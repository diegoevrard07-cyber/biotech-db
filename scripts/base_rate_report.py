"""
Edge-vs-luck report: compare each closed PAPER trade against the base rate the
model predicted for its catalyst.

For every closed long linked to a base-rated catalyst it records the predicted
probability (catalyst base_rate) and the realized result (win = realized P&L > 0),
then buckets predictions to show observed win rate vs mean predicted probability
(a reliability/calibration view) and a Brier score on win/loss outcomes.

Two views:
  * RESOLVED  — trades whose catalyst date has already passed (the bet actually
                played out). This is the meaningful sample.
  * ALL       — every closed trade with a base-rated catalyst (includes names
                closed by rebalancing before the catalyst; use with caution).

  python scripts/base_rate_report.py            # resolved-catalyst trades
  python scripts/base_rate_report.py --all      # include pre-catalyst closes
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection

_BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


def _fetch(cur, *, resolved_only: bool) -> list[dict]:
    sql = """
        SELECT h.ticker, h.trade_type, h.realized_pnl_usd, h.exit_date,
               c.expected_date, c.base_rate
        FROM portfolio_holdings h
        JOIN catalysts c ON c.id = h.catalyst_id
        WHERE h.side = 'long' AND h.status = 'closed' AND h.notes LIKE 'PAPER%%'
          AND c.base_rate IS NOT NULL
    """
    if resolved_only:
        sql += " AND c.expected_date <= %s"
        cur.execute(sql, (date.today(),))
    else:
        cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def run(*, resolved_only: bool = True) -> None:
    """Print win-rate vs predicted base rate, reliability buckets, and Brier score."""
    with get_connection() as conn:
        cur = conn.connection.cursor()
        try:
            trades = _fetch(cur, resolved_only=resolved_only)
        finally:
            cur.close()

    label = "RESOLVED (catalyst date passed)" if resolved_only else "ALL closed base-rated"
    print(f"=== BASE-RATE REPORT — {label} ===")
    if not trades:
        print("  No trades in this view yet.")
        if resolved_only:
            print("  (Catalysts for the current book haven't fired — nothing has truly resolved.)")
        return

    # meaningful realized bets ignore flat (0) trims/dupes
    decided = [t for t in trades if float(t["realized_pnl_usd"] or 0.0) != 0.0]
    n = len(decided)
    wins = sum(1 for t in decided if float(t["realized_pnl_usd"]) > 0)
    mean_base = sum(float(t["base_rate"]) for t in decided) / n if n else 0.0
    hit_rate = wins / n if n else 0.0
    brier = (
        sum(
            (float(t["base_rate"]) - (1.0 if float(t["realized_pnl_usd"]) > 0 else 0.0)) ** 2
            for t in decided
        )
        / n
        if n
        else 0.0
    )

    print(f"  decided trades (non-flat): {n}   flat/zero: {len(trades) - n}")
    print(f"  observed win rate:        {hit_rate:.0%}")
    print(f"  mean predicted base rate: {mean_base:.0%}")
    print(f"  Brier score (win/loss):   {brier:.3f}   (lower is better; 0.25 = coin flip)")

    print("\n  Reliability by predicted base-rate bucket:")
    print(f"    {'bucket':<12}{'n':>4}{'pred':>8}{'won':>8}")
    for lo, hi in _BUCKETS:
        grp = [t for t in decided if lo <= float(t["base_rate"]) < hi]
        if not grp:
            continue
        pred = sum(float(t["base_rate"]) for t in grp) / len(grp)
        won = sum(1 for t in grp if float(t["realized_pnl_usd"]) > 0) / len(grp)
        print(f"    {lo:.1f}-{hi:.1f}{'':<4}{len(grp):>4}{pred:>8.0%}{won:>8.0%}")

    if n < 20:
        print(
            f"\n  ⚠ Sample is tiny (n={n}). Treat as directional only — not a validated edge. "
            "Calibration needs dozens of resolved catalysts."
        )


def main() -> None:
    """CLI entry: edge-vs-luck calibration report on closed paper trades."""
    ap = argparse.ArgumentParser(description="Compare closed trades vs predicted base rates")
    ap.add_argument(
        "--all", action="store_true", help="include trades closed before their catalyst fired"
    )
    args = ap.parse_args()
    config.preflight()
    run(resolved_only=not args.all)


if __name__ == "__main__":
    main()
