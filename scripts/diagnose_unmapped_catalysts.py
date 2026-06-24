"""Diagnose catalysts missing base rates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_connection, get_engine
from layers.layer1.ctgov_client import get_study
from layers.layer3.outcome_extractor import normalize_phase
from logger import setup_logger

log = setup_logger("diagnose_unmapped_catalysts")


def _classify(api_phase_str: str | None, db_phase: str | None, parsed_phase: str | None) -> str:
    if not api_phase_str:
        return "genuinely_no_phase"
    parts = [p for p in api_phase_str.split("/") if p]
    if len(parts) > 1:
        return "multi_phase"
    if api_phase_str and not parsed_phase:
        return "parser_dropped"
    if api_phase_str and db_phase is None:
        return "parser_dropped"
    return "other"


def diagnose(fix_trials: bool = False) -> list[dict]:
    engine = get_engine()
    results: list[dict] = []

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.id, c.catalyst_type, c.expected_date, c.base_rate_slice_key,
                       t.nct_id, t.phase AS db_phase, t.title,
                       co.name AS sponsor
                FROM catalysts c
                LEFT JOIN trials t ON c.trial_id = t.id
                LEFT JOIN companies co ON c.company_id = co.id
                WHERE c.expected_date >= CURRENT_DATE
                  AND (c.base_rate IS NULL OR c.base_rate_slice_key = 'unmappable_no_phase')
                ORDER BY c.id
                """
            )
        ).mappings().all()

    print(f"\n=== Unmapped catalyst diagnosis ({len(rows)} rows) ===\n")

    for row in rows:
        nct_id = row.get("nct_id")
        api_phase_str = None
        parsed = None
        if nct_id:
            try:
                record = get_study(nct_id)
                api_phase_str = record.get("phase")
                parsed = normalize_phase(api_phase_str)
            except Exception as exc:
                log.warning("ctgov_fetch_failed", nct_id=nct_id, error=str(exc))

        cause = _classify(api_phase_str, row.get("db_phase"), parsed)
        if fix_trials and cause == "parser_dropped" and api_phase_str and nct_id:
            with get_connection() as conn:
                conn.execute(
                    text("UPDATE trials SET phase = :phase WHERE nct_id = :nct_id"),
                    {"phase": api_phase_str, "nct_id": nct_id},
                )
            print(f"  FIXED trial phase -> {api_phase_str}")

        entry = {
            "catalyst_id": row["id"],
            "nct_id": nct_id,
            "catalyst_type": row["catalyst_type"],
            "expected_date": str(row["expected_date"]),
            "sponsor": row.get("sponsor"),
            "db_phase": row.get("db_phase"),
            "api_phases": api_phase_str.split("/") if api_phase_str else [],
            "parsed_phase": parsed,
            "cause": cause,
        }
        results.append(entry)

        print(f"Catalyst {row['id']} | {nct_id} | type={row['catalyst_type']}")
        print(f"  expected_date: {row['expected_date']} | sponsor: {row.get('sponsor')}")
        print(f"  db phase:      {row.get('db_phase')}")
        print(f"  API phases:    {entry['api_phases']}")
        print(f"  parsed phase:  {parsed}")
        print(f"  cause:         {cause}\n")

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix-trials", action="store_true", help="Update trials.phase when API has phase but DB is null")
    args = parser.parse_args()
    diagnose(fix_trials=args.fix_trials)


if __name__ == "__main__":
    main()
