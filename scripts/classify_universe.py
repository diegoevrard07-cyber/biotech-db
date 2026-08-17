"""
Phase 1 - Classify the broadened universe.

For every company, derive from its trials:
  - indication_category  (dominant category across trials)
  - is_gbm_focused       (any trial matches a GBM marker -> flagship vertical)
  - in_universe          (market_cap_usd <= SMALL_CAP_CEILING_USD; NULL = kept TRUE)

Ingestion is already sponsor-driven and indication-agnostic, so this script does
not re-fetch anything. It only tags the existing rows so the GBM flagship and the
wider small-cap oncology/CNS book are both queryable. Idempotent; safe to re-run.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from layers.layer3.indication_taxonomy import (
    categorize_indication,
    is_gbm_focused,
    is_in_scope,
)
from logger import setup_logger

log = setup_logger("classify_universe")


def _split_conditions(indication: str | None) -> list[str]:
    if not indication:
        return []
    return [part.strip() for part in indication.split(",") if part.strip()]


def classify(*, dry_run: bool = False) -> dict:
    """Tag every company with indication_category, is_gbm_focused, and in_universe."""
    summary = {
        "companies": 0,
        "gbm_focused": 0,
        "in_scope_onc_cns": 0,
        "out_of_universe_marketcap": 0,
        "no_trials": 0,
        "by_category": Counter(),
    }

    with get_connection() as conn:
        companies = conn.execute(text("""
                SELECT id, ticker, name, market_cap_usd
                FROM companies
                ORDER BY ticker
                """)).mappings().all()

        for co in companies:
            summary["companies"] += 1
            cid = co["id"]

            trials = conn.execute(
                text("SELECT indication FROM trials WHERE company_id = :cid"),
                {"cid": cid},
            ).fetchall()

            if not trials:
                summary["no_trials"] += 1

            cat_votes: Counter = Counter()
            gbm = False
            for (indication,) in trials:
                conditions = _split_conditions(indication)
                if not conditions:
                    continue
                cat_votes[categorize_indication(conditions)] += 1
                if is_gbm_focused(conditions):
                    gbm = True

            # Dominant category; fall back to existing primary_indication-ish 'other'.
            dominant = cat_votes.most_common(1)[0][0] if cat_votes else "other"
            summary["by_category"][dominant] += 1
            if gbm:
                summary["gbm_focused"] += 1
            if is_in_scope(dominant):
                summary["in_scope_onc_cns"] += 1

            mcap = co["market_cap_usd"]
            in_universe = True
            if mcap is not None and float(mcap) > config.SMALL_CAP_CEILING_USD:
                in_universe = False
                summary["out_of_universe_marketcap"] += 1

            if dry_run:
                continue

            conn.execute(
                text("""
                    UPDATE companies
                    SET indication_category = :cat,
                        is_gbm_focused = :gbm,
                        in_universe = :inu,
                        updated_at = NOW()
                    WHERE id = :cid
                    """),
                {"cat": dominant, "gbm": gbm, "inu": in_universe, "cid": cid},
            )

    print("\n=== Universe Classification ===")
    print(f"Companies classified:        {summary['companies']}")
    print(f"GBM-focused (flagship):      {summary['gbm_focused']}")
    print(f"Oncology/CNS in-scope:       {summary['in_scope_onc_cns']}")
    print(f"Out-of-universe (mkt cap):   {summary['out_of_universe_marketcap']}")
    print(f"Companies with no trials:    {summary['no_trials']}")
    print("Top indication categories:")
    for cat, n in summary["by_category"].most_common(10):
        print(f"  {cat:<26} {n}")
    if dry_run:
        print("(dry run - no rows written)")

    log.info(
        "classify_complete",
        companies=summary["companies"],
        gbm_focused=summary["gbm_focused"],
        in_scope=summary["in_scope_onc_cns"],
        out_of_universe=summary["out_of_universe_marketcap"],
    )
    return summary


def main() -> None:
    """CLI entry: classify the company universe from its trials (Phase 1)."""
    parser = argparse.ArgumentParser(description="Classify the broadened universe")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        config.preflight()
        classify(dry_run=args.dry_run)
    except Exception as exc:
        log.error("classify_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
