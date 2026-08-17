"""
One-shot: make the live paper book match the retired-fade / long-only strategy.

Steps (idempotent):
  1. Cover every open PAPER short at last close.
  2. Strip all short holdings from portfolio history + restate performance
     (delegates to strip_shorts.run).
  3. Retire stale fade scores in edge_scores → trade_type='avoid',
     suggested_weight=0 (so Action Desk "All signals" stops showing shorts).

  python scripts/fix_long_only_book.py --dry-run
  python scripts/fix_long_only_book.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from paper_autopilot import cover_shorts
from strip_shorts import run as strip_shorts_run

import config
from db import get_connection
from logger import setup_logger

log = setup_logger("fix_long_only_book")


def retire_fade_scores(*, dry_run: bool = False) -> int:
    """Zero out every edge_scores row still labeled fade."""
    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM edge_scores WHERE trade_type = 'fade'")
            n = int(cur.fetchone()[0] or 0)
            print(f"\n=== RETIRE FADE SCORES ===")
            print(f"  fade rows in edge_scores: {n}")
            if n == 0:
                print("  Nothing to retire.")
                return 0
            if dry_run:
                print("  (dry run — nothing written)")
                return n
            cur.execute("""
                UPDATE edge_scores
                SET trade_type = 'avoid',
                    suggested_weight = 0,
                    computed_at = NOW()
                WHERE trade_type = 'fade'
            """)
            raw.commit()
            print(f"  Updated {n} row(s) → avoid / weight 0. Committed.")
            return n
        finally:
            cur.close()


def run(*, dry_run: bool = False) -> None:
    """Cover open paper shorts, strip short history, and retire fade scores (one-shot)."""
    print(f"=== FIX LONG-ONLY BOOK  dry_run={dry_run} ===")
    cover_shorts(dry_run=dry_run)
    strip_shorts_run(dry_run=dry_run)
    n = retire_fade_scores(dry_run=dry_run)
    log.info("fix_long_only_book_done", dry_run=dry_run, fades_retired=n)
    print("\nDone. Re-run action_sheet / terminal to confirm zero shorts.")


def main() -> None:
    """CLI entry: migrate the paper book to the retired-fade / long-only strategy."""
    ap = argparse.ArgumentParser(description="Cover shorts, strip history, retire fade scores")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    config.preflight()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
