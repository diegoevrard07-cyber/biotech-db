"""
Strip every SHORT trade the paper book has ever done, so performance reflects the
long-only strategy that is now in effect.

What it does (all shorts are expected to be closed already):
  1. Backs up every side='short' holding to data/backups/shorts_backup_<ts>.json
     AND to a shorts_removed_backup table (reversible).
  2. Deletes those rows from portfolio_holdings.
  3. Reverses their net cash impact on the account. A fully round-tripped short's
     net cash flow equals its realized P&L (open receives proceeds, close spends
     them), so removing all shorts means cash -= net_short_realized (i.e. cash rises
     by the short book's net loss). Keeps equity = cash + Σ market value consistent.
  4. Rewrites portfolio_performance snapshots to a long-only basis: realized_to_date
     recomputed from long closes only, and equity shifted by the short realized booked
     up to each snapshot date. (Intra-window unrealized marks on days a short was still
     open are not restated — a second-order effect; the latest snapshot, with no open
     shorts, is exact.)

Idempotent: if there are no short rows, it does nothing.

  python scripts/strip_shorts.py --dry-run     # show what would change
  python scripts/strip_shorts.py               # apply (writes a backup first)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection

BACKUP_DIR = config.DATA_DIR / "backups"


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def run(*, dry_run: bool = False) -> None:
    """Delete short holdings (with backup), reverse their cash impact, restate snapshots."""
    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            cur.execute("SELECT * FROM portfolio_holdings WHERE side='short'")
            cols = [d[0] for d in cur.description]
            shorts = [dict(zip(cols, r)) for r in cur.fetchall()]

            if not shorts:
                print("No short holdings found — nothing to strip.")
                return

            open_shorts = [h for h in shorts if h["status"] == "open"]
            net_short_realized = sum(float(h["realized_pnl_usd"] or 0.0) for h in shorts)

            cur.execute("SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1")
            arow = cur.fetchone() or (Decimal(0), None)
            cash = float(arow[0] or 0.0)
            start_cap = float(arow[1] or 0.0)
            new_cash = cash - net_short_realized  # remove shorts' net cash contribution

            print(f"=== STRIP SHORTS  {date.today()} ===")
            print(f"  short rows: {len(shorts)}  (open: {len(open_shorts)})")
            print(f"  net short realized P&L: {net_short_realized:+,.2f}")
            print(f"  cash: ${cash:,.2f} -> ${new_cash:,.2f}  (reversing short cash impact)")
            if open_shorts:
                print(
                    f"  WARNING: {len(open_shorts)} short(s) still OPEN — their market "
                    "value is dropped; cover them first for an exact reversal."
                )

            # ---- recompute long-only performance snapshots ----
            cur.execute(
                "SELECT snapshot_date, equity, realized_to_date FROM portfolio_performance "
                "ORDER BY snapshot_date"
            )
            snaps = cur.fetchall()
            snap_updates = []
            for snap_date, equity, realized_td in snaps:
                cur.execute(
                    "SELECT COALESCE(SUM(realized_pnl_usd),0) FROM portfolio_holdings "
                    "WHERE side='long' AND status='closed' AND notes LIKE 'PAPER%%' "
                    "AND exit_date <= %s",
                    (snap_date,),
                )
                long_realized = float(cur.fetchone()[0] or 0.0)
                cur.execute(
                    "SELECT COALESCE(SUM(realized_pnl_usd),0) FROM portfolio_holdings "
                    "WHERE side='short' AND exit_date <= %s",
                    (snap_date,),
                )
                short_realized_td = float(cur.fetchone()[0] or 0.0)
                new_equity = float(equity or 0.0) - short_realized_td
                new_ret = (new_equity - start_cap) / start_cap if start_cap else None
                snap_updates.append(
                    {
                        "date": snap_date,
                        "old_equity": float(equity or 0.0),
                        "new_equity": round(new_equity, 2),
                        "old_realized": float(realized_td or 0.0),
                        "new_realized": round(long_realized, 2),
                        "new_return": round(new_ret, 4) if new_ret is not None else None,
                    }
                )

            print(f"  performance snapshots to restate: {len(snap_updates)}")
            for s in snap_updates:
                print(
                    f"    {s['date']}: equity ${s['old_equity']:,.0f}->${s['new_equity']:,.0f}  "
                    f"realized {s['old_realized']:+,.0f}->{s['new_realized']:+,.0f}"
                )

            if dry_run:
                print("\n  (dry run — nothing written)")
                return

            # ---- backup ----
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = BACKUP_DIR / f"shorts_backup_{ts}.json"
            payload = {
                "created_at": ts,
                "net_short_realized": net_short_realized,
                "cash_before": cash,
                "cash_after": new_cash,
                "holdings": [{k: _jsonable(v) for k, v in h.items()} for h in shorts],
                "snapshots_before": [
                    {"date": str(d), "equity": float(e or 0), "realized_to_date": float(r or 0)}
                    for d, e, r in snaps
                ],
            }
            backup_file.write_text(json.dumps(payload, indent=2))
            print(f"\n  backup written -> {backup_file}")

            # DB-side backup table (reversible without the file)
            cur.execute(
                "CREATE TABLE IF NOT EXISTS shorts_removed_backup (LIKE portfolio_holdings)"
            )
            cur.execute(
                "ALTER TABLE shorts_removed_backup ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ DEFAULT NOW()"
            )
            ids = [h["id"] for h in shorts]
            cur.execute(
                """
                INSERT INTO shorts_removed_backup
                SELECT *, NOW() FROM portfolio_holdings WHERE id = ANY(%s)
            """,
                (ids,),
            )

            # ---- delete shorts + reverse cash ----
            cur.execute("DELETE FROM portfolio_holdings WHERE id = ANY(%s)", (ids,))
            cur.execute(
                "UPDATE portfolio_account SET cash_usd=%s, updated_at=NOW() WHERE id=1",
                (round(new_cash, 2),),
            )

            # ---- restate snapshots ----
            for s in snap_updates:
                cur.execute(
                    """
                    UPDATE portfolio_performance
                    SET equity=%s, realized_to_date=%s, total_return_pct=%s, updated_at=NOW()
                    WHERE snapshot_date=%s
                """,
                    (s["new_equity"], s["new_realized"], s["new_return"], s["date"]),
                )

            raw.commit()
            print(
                f"\n  Deleted {len(ids)} short rows, reversed cash, restated "
                f"{len(snap_updates)} snapshot(s). Committed."
            )
        finally:
            cur.close()


def main() -> None:
    """CLI entry: strip all short trades from the paper book (long-only migration)."""
    ap = argparse.ArgumentParser(description="Strip all short trades from the paper book")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    config.preflight()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
