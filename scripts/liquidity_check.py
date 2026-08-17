"""
Pre-trade liquidity check for the near-term book.

Small-cap biotech is where fills go to die: wide spreads, thin volume, and a
position that's a big % of average daily volume (ADV) can't be entered or exited
without moving the price against you. This script flags those BEFORE you trade.

For each near-term actionable name it reports:
  - ADV (avg daily $ volume, last 20 bars)  -> how much trades each day
  - range% (avg (high-low)/close, last 20)  -> a cheap spread/cost proxy
  - your position $ at the current sleeve and de-risked weight
  - % of ADV your position would be          -> the key fill-risk number

Flags: ILLIQUID (ADV < $500k), TOO-BIG (>10% of ADV), WIDE (range% > 8%).
Read-only. Rule of thumb: keep a position under ~5-10% of ADV and use LIMIT orders.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from action_sheet import risk_haircut  # reuse the same de-risking
from sqlalchemy import text

import config
from db import get_connection
from logger import setup_logger

log = setup_logger("liquidity_check")

ILLIQUID_ADV = 500_000      # < $500k/day avg -> hard to trade
TOO_BIG_PCT = 0.10          # position > 10% of ADV -> fill risk
WIDE_RANGE = 0.08           # avg daily range > 8% -> wide spread/vol


def _near_term(conn, days: int):
    return conn.execute(text(f"""
        SELECT co.ticker, co.market_cap_usd, c.expected_date, es.trade_type,
               es.suggested_weight
        FROM edge_scores es
        JOIN catalysts c ON c.id = es.catalyst_id
        JOIN companies co ON co.id = es.company_id
        WHERE es.trade_type IS NOT NULL AND es.trade_type <> 'avoid'
          AND es.suggested_weight IS NOT NULL AND es.suggested_weight <> 0
          AND c.expected_date BETWEEN CURRENT_DATE AND CURRENT_DATE + {int(days)}
        ORDER BY c.expected_date ASC
    """)).mappings().all()


def _liquidity(conn, ticker: str) -> dict | None:
    rows = conn.execute(text(
        "SELECT close, high, low, volume FROM price_history "
        "WHERE ticker = :t AND close IS NOT NULL ORDER BY date DESC LIMIT 20"
    ), {"t": ticker}).all()
    if len(rows) < 5:
        return None
    closes = np.array([float(r[0]) for r in rows])
    highs = np.array([float(r[1]) if r[1] is not None else float(r[0]) for r in rows])
    lows = np.array([float(r[2]) if r[2] is not None else float(r[0]) for r in rows])
    vols = np.array([float(r[3]) if r[3] is not None else 0.0 for r in rows])
    adv = float(np.mean(closes * vols))
    rng = float(np.mean((highs - lows) / np.where(closes > 0, closes, np.nan)))
    return {"last": float(closes[0]), "adv": adv, "range_pct": rng,
            "med_vol": float(np.median(vols))}


def run(days: int = 60, sleeve: float | None = None) -> None:
    with get_connection() as conn:
        if sleeve is None:
            sc = conn.execute(text("SELECT starting_capital_usd FROM portfolio_account WHERE id=1")).scalar()
            sleeve = float(sc) if sc else 10_000.0
        names = _near_term(conn, days)
        seen = set()
        out = []
        for r in names:
            t = r["ticker"]
            if t in seen:
                continue
            seen.add(t)
            liq = _liquidity(conn, t)
            if not liq:
                out.append({"ticker": t, "date": r["expected_date"], "trade": r["trade_type"],
                            "skip": "no price"})
                continue
            mult = risk_haircut(float(r["market_cap_usd"]) if r["market_cap_usd"] is not None else None)
            wt = abs(float(r["suggested_weight"])) * mult
            pos = sleeve * wt
            pct_adv = pos / liq["adv"] if liq["adv"] > 0 else float("inf")
            flags = []
            if liq["adv"] < ILLIQUID_ADV:
                flags.append("ILLIQUID")
            if pct_adv > TOO_BIG_PCT:
                flags.append("TOO-BIG")
            if liq["range_pct"] > WIDE_RANGE:
                flags.append("WIDE")
            out.append({"ticker": t, "date": r["expected_date"], "trade": r["trade_type"],
                        "last": liq["last"], "adv": liq["adv"], "range_pct": liq["range_pct"],
                        "pos": pos, "pct_adv": pct_adv, "flags": flags})

    print(f"\n=== PRE-TRADE LIQUIDITY CHECK  (next {days}d, sleeve ${sleeve:,.0f}) ===")
    print(f"{'TICKER':<7}{'DATE':<12}{'TRADE':<14}{'LAST':>8}{'ADV$':>11}{'RANGE%':>8}"
          f"{'POS$':>9}{'%ADV':>7}  FLAGS")
    clean = 0
    for r in out:
        if r.get("skip"):
            print(f"{r['ticker']:<7}{str(r['date']):<12}{r['trade']:<14}{'— ' + r['skip']:>40}")
            continue
        flags = ",".join(r["flags"]) if r["flags"] else "ok"
        if not r["flags"]:
            clean += 1
        print(f"{r['ticker']:<7}{str(r['date']):<12}{r['trade']:<14}{r['last']:>8.2f}"
              f"{r['adv']:>11,.0f}{r['range_pct']:>8.1%}{r['pos']:>9,.0f}"
              f"{r['pct_adv']:>7.1%}  {flags}")
    print(f"\n{clean}/{len([o for o in out if not o.get('skip')])} names trade clean at this sleeve. "
          f"Use LIMIT orders; split TOO-BIG names over multiple days or size down.")
    log.info("liquidity_check_done", names=len(out), clean=clean, sleeve=sleeve)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-trade liquidity / fill-risk check")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--sleeve", type=float, default=None, help="sleeve $ (defaults to account starting capital)")
    args = ap.parse_args()
    try:
        config.preflight()
        run(days=args.days, sleeve=args.sleeve)
    except Exception as exc:  # noqa: BLE001
        log.error("liquidity_check_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
