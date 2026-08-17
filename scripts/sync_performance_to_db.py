"""
Import local paper_performance.csv into portfolio_performance (Supabase).

One-time / idempotent sync so history merges across machines.

  python scripts/sync_performance_to_db.py
  python scripts/sync_performance_to_db.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection
from layers.portfolio import performance_store as perf_store

PERF_CSV = config.RAW_DIR / "paper_performance.csv"
PERF_COLUMNS = [
    "date",
    "equity",
    "cash",
    "open_positions",
    "unrealized_pnl",
    "realized_to_date",
    "total_return_pct",
    "exits_today",
    "opens_today",
    "resized_today",
    "desk_positions",
]


def main() -> None:
    """CLI entry: import the local paper_performance CSV into portfolio_performance."""
    ap = argparse.ArgumentParser(description="Sync local performance CSV to Supabase")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    config.preflight()

    if not PERF_CSV.exists():
        print(f"No local CSV at {PERF_CSV} — nothing to import.")
        return

    rows: list[dict] = []
    with open(PERF_CSV, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            if not raw.get("date") or raw.get("date") == "date":
                continue
            rows.append(raw)

    if not rows:
        print("CSV is empty.")
        return

    print(f"Found {len(rows)} row(s) in {PERF_CSV}")

    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            track_start = perf_store.tracking_start_date(cur)
            cur.execute("SELECT starting_capital_usd FROM portfolio_account WHERE id=1")
            acct = cur.fetchone()
            start_cap = float(acct[0]) if acct and acct[0] else None

            n = 0
            for raw_row in rows:
                snap_date = raw_row["date"]
                xbi_close = None
                cur.execute(
                    "SELECT close FROM price_history WHERE ticker=%s AND date=%s",
                    (config.BENCHMARK_TICKER, snap_date),
                )
                px = cur.fetchone()
                if px and px[0] is not None:
                    xbi_close = float(px[0])

                xbi_ret, bench_eq = perf_store.benchmark_fields(
                    cur,
                    xbi_close=xbi_close,
                    starting_capital=start_cap,
                    track_start=track_start,
                )

                payload = {
                    "snapshot_date": snap_date,
                    "equity": float(raw_row.get("equity") or 0),
                    "cash": float(raw_row["cash"]) if raw_row.get("cash") else None,
                    "open_positions": (
                        int(raw_row["open_positions"]) if raw_row.get("open_positions") else None
                    ),
                    "unrealized_pnl": (
                        float(raw_row["unrealized_pnl"]) if raw_row.get("unrealized_pnl") else None
                    ),
                    "realized_to_date": (
                        float(raw_row["realized_to_date"])
                        if raw_row.get("realized_to_date")
                        else None
                    ),
                    "total_return_pct": (
                        float(raw_row["total_return_pct"])
                        if raw_row.get("total_return_pct")
                        else None
                    ),
                    "exits_today": int(raw_row.get("exits_today") or 0),
                    "opens_today": int(raw_row.get("opens_today") or 0),
                    "resized_today": int(raw_row.get("resized_today") or 0),
                    "desk_positions": (
                        int(raw_row["desk_positions"]) if raw_row.get("desk_positions") else None
                    ),
                    "xbi_close": xbi_close,
                    "xbi_return_pct": xbi_ret,
                    "benchmark_equity": bench_eq,
                }
                if args.dry_run:
                    print(f"  would upsert {snap_date} equity={payload['equity']}")
                else:
                    perf_store.upsert_snapshot(cur, payload)
                n += 1
            if not args.dry_run:
                raw.commit()
            print(
                f"{'Would sync' if args.dry_run else 'Synced'} {n} snapshot(s) to portfolio_performance."
            )
        finally:
            cur.close()


if __name__ == "__main__":
    main()
