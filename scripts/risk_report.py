"""
Quantitative risk/reward report for the paper book, vs XBI.

Three tiers of evidence, increasing in reliability:
  1) Live paper equity curve  — daily Sharpe/vol/drawdown/beta (usually tiny sample).
  2) Closed-trade distribution — expectancy, win rate, payoff, per-trade Sharpe, Kelly.
  3) XBI reference            — long-run annualized return / vol / Sharpe / max drawdown.

Sharpe uses rf=0 (paper book, short horizon). Annualized with 252 trading days.
Everything is flagged when the sample is too small to trust.

  python scripts/risk_report.py
"""

from __future__ import annotations

import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection

ANN = math.sqrt(252)


def _returns(series: list[float]) -> list[float]:
    return [series[i] / series[i - 1] - 1 for i in range(1, len(series)) if series[i - 1]]


def _max_dd(series: list[float]) -> float:
    peak, mdd = series[0], 0.0
    for p in series:
        peak = max(peak, p)
        if peak:
            mdd = min(mdd, p / peak - 1)
    return mdd


def _sharpe(rets: list[float]) -> float | None:
    if len(rets) < 2:
        return None
    sd = st.pstdev(rets)
    if sd <= 0:
        return None
    return (sum(rets) / len(rets)) / sd * ANN


def run() -> None:
    with get_connection() as conn:
        cur = conn.connection.cursor()
        try:
            cur.execute("SELECT snapshot_date, equity, xbi_close FROM portfolio_performance "
                        "WHERE equity IS NOT NULL ORDER BY snapshot_date")
            snaps = cur.fetchall()
            cur.execute("SELECT realized_pnl_usd, cost_basis_usd FROM portfolio_holdings "
                        "WHERE side='long' AND status='closed' AND notes LIKE 'PAPER%%' "
                        "AND cost_basis_usd > 0")
            trades = cur.fetchall()
            cur.execute("SELECT close FROM price_history WHERE ticker=%s AND close IS NOT NULL "
                        "ORDER BY date", (config.BENCHMARK_TICKER,))
            xbi_hist = [float(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    print("=" * 64)
    print("RISK / REWARD REPORT  (rf=0, annualized x sqrt(252))")
    print("=" * 64)

    # ---- 1) live equity curve ----
    print("\n[1] LIVE PAPER EQUITY CURVE")
    eq = [float(r[1]) for r in snaps]
    if len(eq) >= 3:
        pr = _returns(eq)
        vol = st.pstdev(pr) * ANN if len(pr) >= 2 else float("nan")
        shp = _sharpe(pr)
        print(f"    snapshots: {len(eq)}   period return: {eq[-1]/eq[0]-1:+.2%}")
        print(f"    ann. vol: {vol:.1%}   Sharpe: {shp:.2f}" if shp else "    Sharpe: n/a")
        print(f"    max drawdown: {_max_dd(eq):.2%}")
        # beta vs XBI on overlapping days
        pair = [(float(a[1]), float(a[2])) for a in snaps if a[2] is not None]
        if len(pair) >= 3:
            pe = _returns([p[0] for p in pair])
            xe = _returns([p[1] for p in pair])
            n = min(len(pe), len(xe))
            if n >= 2 and st.pstdev(xe[:n]) > 0:
                mean_p = sum(pe[:n]) / n
                mean_x = sum(xe[:n]) / n
                cov = sum((pe[i] - mean_p) * (xe[i] - mean_x) for i in range(n)) / n
                beta = cov / st.pvariance(xe[:n])
                print(f"    beta vs XBI: {beta:+.2f}  (n={n} days)")
        print(f"    ⚠ n={len(eq)} — statistically meaningless; ignore the magnitudes.")
    else:
        print("    not enough snapshots yet.")

    # ---- 2) closed-trade distribution ----
    print("\n[2] CLOSED-TRADE DISTRIBUTION (realized longs)")
    if trades:
        rets = [float(p) / float(cb) for p, cb in trades]
        n = len(rets)
        m, sd = sum(rets) / n, st.pstdev(rets)
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r < 0]
        wr = len(wins) / n
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        payoff = (aw / abs(al)) if al else float("inf")
        kelly = wr - (1 - wr) / payoff if payoff not in (0, float("inf")) else float("nan")
        print(f"    trades: {n}   expectancy: {m:+.2%}/trade   sd: {sd:.2%}")
        print(f"    win rate: {wr:.0%}   avg win: {aw:+.2%}   avg loss: {al:+.2%}   payoff: {payoff:.2f}")
        print(f"    per-trade Sharpe (mean/sd): {m/sd:.2f}" if sd else "")
        print(f"    implied full-Kelly fraction: {kelly:.0%}")
        print(f"    ⚠ young + upward-biased: profit-lock/rebalance book small wins & "
              "cut winners; losers may still be open (survivorship).")
    else:
        print("    no closed trades.")

    # ---- 3) XBI reference ----
    print("\n[3] XBI REFERENCE (long-run yardstick)")
    if len(xbi_hist) > 60:
        xr = _returns(xbi_hist)
        m = sum(xr) / len(xr)
        vol = st.pstdev(xr) * ANN
        print(f"    history: {len(xbi_hist)} days")
        print(f"    ann. return: {(1+m)**252-1:+.1%}   ann. vol: {vol:.1%}   "
              f"Sharpe: {m/st.pstdev(xr)*ANN:.2f}   max DD: {_max_dd(xbi_hist):.1%}")
    else:
        print("    insufficient XBI history.")

    print("\n" + "=" * 64)
    print("Reliable read needs months + dozens of RESOLVED catalysts. Until then,")
    print("XBI's Sharpe ~0.27 / vol ~32% / maxDD ~-55% is the risk bar to beat.")


def main() -> None:
    config.preflight()
    run()


if __name__ == "__main__":
    main()
