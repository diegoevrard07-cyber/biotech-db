"""
Quantitative risk/reward report for the paper book, vs XBI.

Three tiers of evidence, increasing in reliability:
  1) Live paper equity curve: daily Sharpe/vol/drawdown/beta (usually tiny sample).
  2) Closed-trade distribution: expectancy, win rate, payoff, per-trade Sharpe, Kelly.
  3) XBI reference: long-run annualized return / vol / Sharpe / max drawdown.

Sharpe uses rf=0 (paper book, short horizon). Annualized with 252 trading days.
Everything is flagged when the sample is too small to trust. The math lives in
layers/portfolio/stats.py, shared with the Streamlit terminal.

  python scripts/risk_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection
from layers.portfolio import stats


def run() -> None:
    """Print risk/reward stats: live equity curve, closed-trade distribution, XBI yardstick."""
    with get_connection() as conn:
        cur = conn.connection.cursor()
        try:
            cur.execute(
                "SELECT snapshot_date, equity, xbi_close FROM portfolio_performance "
                "WHERE equity IS NOT NULL ORDER BY snapshot_date"
            )
            snaps = cur.fetchall()
            cur.execute(
                "SELECT realized_pnl_usd, cost_basis_usd FROM portfolio_holdings "
                "WHERE side='long' AND status='closed' AND notes LIKE 'PAPER%%' "
                "AND cost_basis_usd > 0"
            )
            trades = cur.fetchall()
            cur.execute(
                "SELECT close FROM price_history WHERE ticker=%s AND close IS NOT NULL "
                "ORDER BY date",
                (config.BENCHMARK_TICKER,),
            )
            xbi_hist = [float(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    print("=" * 64)
    print("RISK / REWARD REPORT  (rf=0, annualized x sqrt(252))")
    print("=" * 64)

    # ---- 1) live equity curve ----
    print("\n[1] LIVE PAPER EQUITY CURVE")
    equity = [float(r[1]) for r in snaps]
    bench = [float(r[2]) if r[2] is not None else None for r in snaps]
    curve = stats.equity_curve_stats(equity, benchmark=bench)
    if curve:
        print(f"    snapshots: {curve['n']}   period return: {curve['period_return']:+.2%}")
        if curve["sharpe"] is not None:
            print(f"    ann. vol: {curve['ann_vol']:.1%}   Sharpe: {curve['sharpe']:.2f}")
        else:
            print("    Sharpe: n/a")
        print(f"    max drawdown: {curve['max_drawdown']:.2%}")
        if curve["beta"] is not None:
            print(f"    beta vs XBI: {curve['beta']:+.2f}  (n={curve['beta_days']} days)")
        print(f"    ⚠ n={curve['n']}: statistically meaningless; ignore the magnitudes.")
    else:
        print("    not enough snapshots yet.")

    # ---- 2) closed-trade distribution ----
    print("\n[2] CLOSED-TRADE DISTRIBUTION (realized longs)")
    dist = stats.closed_trade_stats([float(p) / float(cb) for p, cb in trades])
    if dist:
        print(
            f"    trades: {dist['n']}   expectancy: {dist['expectancy']:+.2%}/trade   "
            f"sd: {dist['sd']:.2%}"
        )
        print(
            f"    win rate: {dist['win_rate']:.0%}   avg win: {dist['avg_win']:+.2%}   "
            f"avg loss: {dist['avg_loss']:+.2%}   payoff: {dist['payoff']:.2f}"
        )
        if dist["per_trade_sharpe"] is not None:
            print(f"    per-trade Sharpe (mean/sd): {dist['per_trade_sharpe']:.2f}")
        print(f"    implied full-Kelly fraction: {dist['kelly']:.0%}")
        print(
            "    ⚠ young + upward-biased: profit-lock/rebalance book small wins & "
            "cut winners; losers may still be open (survivorship)."
        )
    else:
        print("    no closed trades.")

    # ---- 3) XBI reference ----
    print("\n[3] XBI REFERENCE (long-run yardstick)")
    ref = stats.benchmark_reference_stats(xbi_hist)
    if ref:
        print(f"    history: {ref['n']} days")
        print(
            f"    ann. return: {ref['ann_return']:+.1%}   ann. vol: {ref['ann_vol']:.1%}   "
            f"Sharpe: {ref['sharpe']:.2f}   max DD: {ref['max_drawdown']:.1%}"
        )
    else:
        print("    insufficient XBI history.")

    print("\n" + "=" * 64)
    print("Reliable read needs months + dozens of RESOLVED catalysts. Until then,")
    print("XBI's Sharpe ~0.27 / vol ~32% / maxDD ~-55% is the risk bar to beat.")


def main() -> None:
    """CLI entry: print the paper book's risk/reward report."""
    config.preflight()
    run()


if __name__ == "__main__":
    main()
