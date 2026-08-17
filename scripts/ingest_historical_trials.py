"""Ingest completed CT.gov trials with results into historical_trials."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from tqdm import tqdm

import config
from db import get_connection
from layers.layer3.ctgov_historical import CTGovHistoricalError, iter_historical_studies
from layers.layer3.indication_taxonomy import categorize_indication
from layers.layer3.outcome_extractor import ExtractionStats, extract_outcome, normalize_phase
from layers.layer3.sponsor_classifier import classify_sponsor
from logger import setup_logger

log = setup_logger("ingest_historical_trials")


def _parse_db_date(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if len(v) == 4:
        return f"{v}-01-01"
    if len(v) == 7:
        return f"{v}-01"
    return v[:10]


def _parse_study(study: dict) -> dict:
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    design = ps.get("designModule", {})
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {})
    conditions = ps.get("conditionsModule", {}).get("conditions", []) or []

    sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "")
    phase_raw = "/".join(design.get("phases") or [])
    phase = normalize_phase(phase_raw)
    outcome = extract_outcome(study)

    return {
        "nct_id": ident.get("nctId"),
        "phase": phase,
        "conditions": json.dumps(conditions),
        "indication_category": categorize_indication(conditions),
        "sponsor_name": sponsor,
        "sponsor_class": classify_sponsor(sponsor, lookup_market_cap=True),
        "primary_completion_date": _parse_db_date(
            status.get("primaryCompletionDateStruct", {}).get("date")
        ),
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "primary_outcome_met": outcome.primary_outcome_met,
        "primary_outcome_confidence": outcome.primary_outcome_confidence,
        "extraction_method": outcome.extraction_method,
        "raw_results": json.dumps(study.get("resultsSection") or {}),
        "source": "ctgov",
    }


def ingest(
    start_year: int = 2010,
    end_year: int = 2024,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Stream completed CT.gov studies and upsert labeled outcomes into historical_trials."""
    stats = ExtractionStats()
    upserted = 0
    errors = 0
    pending: list[dict] = []

    def flush() -> None:
        """Upsert the pending batch of parsed trial rows."""
        nonlocal upserted
        if not pending:
            return
        with get_connection() as conn:
            for row in pending:
                conn.execute(
                    text(
                        """
                        INSERT INTO historical_trials (
                            nct_id, phase, conditions, indication_category, sponsor_name,
                            sponsor_class, primary_completion_date, enrollment,
                            primary_outcome_met, primary_outcome_confidence,
                            extraction_method, raw_results, source
                        ) VALUES (
                            :nct_id, :phase, CAST(:conditions AS jsonb), :indication_category,
                            :sponsor_name, :sponsor_class, :primary_completion_date, :enrollment,
                            :primary_outcome_met, :primary_outcome_confidence,
                            :extraction_method, CAST(:raw_results AS jsonb), :source
                        )
                        ON CONFLICT (nct_id) DO UPDATE SET
                            phase = EXCLUDED.phase,
                            conditions = EXCLUDED.conditions,
                            indication_category = EXCLUDED.indication_category,
                            sponsor_name = EXCLUDED.sponsor_name,
                            sponsor_class = EXCLUDED.sponsor_class,
                            primary_completion_date = EXCLUDED.primary_completion_date,
                            enrollment = EXCLUDED.enrollment,
                            primary_outcome_met = EXCLUDED.primary_outcome_met,
                            primary_outcome_confidence = EXCLUDED.primary_outcome_confidence,
                            extraction_method = EXCLUDED.extraction_method,
                            raw_results = EXCLUDED.raw_results,
                            source = EXCLUDED.source
                        """
                    ),
                    row,
                )
                upserted += 1
        pending.clear()

    iterator = iter_historical_studies(start_year, end_year, limit=limit)
    for study in tqdm(iterator, desc="Historical trials"):
        try:
            row = _parse_study(study)
            if not row.get("nct_id"):
                continue
            stats.record(row["primary_outcome_confidence"])
            if dry_run:
                upserted += 1
                continue
            pending.append(row)
            if len(pending) >= 100:
                flush()
        except Exception as exc:
            errors += 1
            log.error("parse_failed", error=str(exc))
    if not dry_run:
        flush()

    summary = {"upserted": upserted, "errors": errors, **stats.as_dict()}
    print(f"\nHistorical trials upserted: {upserted}")
    print(f"Extraction stats: {stats.as_dict()}")
    log.info("ingest_historical_complete", **summary)
    return summary


def main() -> None:
    """CLI entry: ingest historical trial outcomes from CT.gov (Layer 3 training data)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    started = datetime.now()
    try:
        summary = ingest(
            start_year=args.start_year,
            end_year=args.end_year,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except CTGovHistoricalError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print(f"Elapsed: {(datetime.now() - started).total_seconds():.1f}s")

    if summary["upserted"] == 0 and not args.dry_run:
        sys.exit(1)


if __name__ == "__main__":
    main()
