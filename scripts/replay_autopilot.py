"""
Replay missed paper-autopilot sessions (e.g. after a database outage).

Runs one autopilot cycle per benchmark trading session in [start, end], oldest
first, each with `as_of` set to that session so it sees only prices, peak equity,
and catalyst windows knowable at the time. Signals are whatever `edge_scores`
holds: if the daily refresh was also down, that is the last pre-outage scoring.

Backfill prices for the window first (scripts/ingest_prices.py --since-last).

  python scripts/replay_autopilot.py --start 2026-08-27 --end 2026-10-14
  python scripts/replay_autopilot.py --start 2026-08-27 --end 2026-10-14 --list
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from db import get_connection
from paper_autopilot import run


def trading_sessions(start: date, end: date) -> list[date]:
    """Dates in [start, end] with a stored benchmark close."""
    with get_connection() as conn:
        cur = conn.connection.cursor()
        cur.execute(
            """
            SELECT DISTINCT date FROM price_history
            WHERE ticker = %s AND close IS NOT NULL AND date BETWEEN %s AND %s
            ORDER BY date
            """,
            (config.BENCHMARK_TICKER, start, end),
        )
        return [r[0] for r in cur.fetchall()]


def main() -> None:
    """CLI entry: replay autopilot cycles across a date range."""
    ap = argparse.ArgumentParser(description="Replay missed paper-autopilot sessions")
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--list", action="store_true", help="print the sessions and exit")
    args = ap.parse_args()

    config.preflight()
    sessions = trading_sessions(args.start, args.end)
    print(f"{len(sessions)} sessions: {sessions[0] if sessions else '-'} .. "
          f"{sessions[-1] if sessions else '-'}")
    if args.list or not sessions:
        for d in sessions:
            print(f"  {d}")
        return
    for d in sessions:
        run(as_of=d)


if __name__ == "__main__":
    main()
