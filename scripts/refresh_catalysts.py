"""Re-extract catalysts from trials already in DB (no API calls)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_connection
from layers.layer1.catalyst_extractor import extract_catalysts, merge_funnel_stats, new_funnel_stats
from layers.layer1.ctgov_client import parse_study_record
from layers.layer1.dedupe import dedupe_catalysts
from layers.layer1.funnel_report import print_funnel
from logger import setup_logger

log = setup_logger("refresh_catalysts")


def main() -> None:
    """CLI entry: re-extract and replace ctgov_v2 catalysts from stored trial JSON."""
    total = 0
    funnel = new_funnel_stats()

    with get_connection() as conn:
        companies = conn.execute(text("SELECT id, ticker FROM companies")).mappings().all()
        for co in companies:
            trials = conn.execute(
                text("SELECT id, nct_id, raw_json FROM trials WHERE company_id = :c"),
                {"c": co["id"]},
            ).mappings().all()
            cats: list[dict] = []
            company_funnel = new_funnel_stats()
            for t in trials:
                trial = parse_study_record(t["raw_json"])
                trial["nct_id"] = trial.get("nct_id") or t["nct_id"]
                extracted, fstats = extract_catalysts(trial, co["id"], t["id"])
                cats.extend(extracted)
                company_funnel = merge_funnel_stats(company_funnel, fstats)

            deduped, merges = dedupe_catalysts(cats)
            company_funnel["dropped_dedupe_merge"] += merges
            company_funnel["final_upcoming"] = len(deduped)
            funnel = merge_funnel_stats(funnel, company_funnel)

            conn.execute(
                text("DELETE FROM catalysts WHERE company_id = :c AND source = 'ctgov_v2'"),
                {"c": co["id"]},
            )
            for cat in deduped:
                conn.execute(
                    text(
                        """
                        INSERT INTO catalysts (
                            company_id, trial_id, catalyst_type, expected_date, date_confidence,
                            description, source, source_url, raw_data, requires_manual_verification
                        ) VALUES (
                            :company_id, :trial_id, :catalyst_type, :expected_date, :date_confidence,
                            :description, :source, :source_url, CAST(:raw_data AS jsonb),
                            :requires_manual_verification
                        )
                        """
                    ),
                    {**cat, "raw_data": json.dumps(cat.get("raw_data") or {})},
                )
            total += len(deduped)
            log.info("company_refreshed", ticker=co["ticker"], catalysts=len(deduped), merges=merges)

    funnel["final_upcoming"] = total
    print(f"Catalysts refreshed: {total}")
    print_funnel(funnel)


if __name__ == "__main__":
    main()
