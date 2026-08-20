"""Post-phase health check for the edge engine database."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root (config, db)

from sqlalchemy import text

import config
from db import get_engine
from logger import setup_logger

EXPECTED_TABLES = [
    "companies",
    "trials",
    "catalysts",
    "council_judgments",
    "trial_scores",
    "historical_trials",
    "base_rates",
    "sec_filings",
    "financials",
    "edge_scores",
    "score_history",
    "fda_approvals",
    "material_events",
]

FK_CHECKS = [
    ("trials", "company_id", "companies", "id"),
    ("catalysts", "company_id", "companies", "id"),
    ("catalysts", "trial_id", "trials", "id"),
    ("council_judgments", "trial_id", "trials", "id"),
    ("trial_scores", "trial_id", "trials", "id"),
    ("sec_filings", "company_id", "companies", "id"),
    ("financials", "company_id", "companies", "id"),
    ("financials", "filing_id", "sec_filings", "id"),
    ("edge_scores", "company_id", "companies", "id"),
    ("edge_scores", "catalyst_id", "catalysts", "id"),
    ("score_history", "catalyst_id", "catalysts", "id"),
]

SAMPLE_TABLES = [
    "companies",
    "trials",
    "catalysts",
    "trial_scores",
    "base_rates",
    "financials",
    "edge_scores",
]


def verify(phase: str = "0", expect_rows: dict[str, int] | None = None) -> bool:
    """Run health checks. Returns True if all pass."""
    log = setup_logger("verify")
    expect_rows = expect_rows or {}
    anomalies: list[str] = []

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        return False

    try:
        config.check_sec_user_agent()
    except RuntimeError as exc:
        print(f"WARNING: {exc}")
        print("  (Required before Layer 4 SEC EDGAR ingestion)\n")

    engine = get_engine()
    print(f"\n=== VERIFY (Phase {phase}) ===\n")

    with engine.connect() as conn:
        # 1. Tables exist
        result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """))
        existing = {row[0] for row in result}
        missing = [t for t in EXPECTED_TABLES if t not in existing]
        if missing:
            anomalies.append(f"Missing tables: {missing}")
        else:
            print(f"Tables: all {len(EXPECTED_TABLES)} present")

        # 2. Row counts
        print("\nRow counts:")
        counts: dict[str, int] = {}
        for table in EXPECTED_TABLES:
            if table not in existing:
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
            counts[table] = count
            min_expected = expect_rows.get(table, 0)
            flag = ""
            if count < min_expected:
                anomalies.append(f"{table}: expected >={min_expected} rows, got {count}")
                flag = " ⚠"
            print(f"  {table}: {count}{flag}")

        # 3. Orphan FKs
        print("\nOrphan FK checks:")
        for child, fk_col, parent, pk_col in FK_CHECKS:
            if child not in existing or parent not in existing:
                continue
            orphans = conn.execute(text(f"""
                    SELECT COUNT(*) FROM {child} c
                    LEFT JOIN {parent} p ON c.{fk_col} = p.{pk_col}
                    WHERE c.{fk_col} IS NOT NULL AND p.{pk_col} IS NULL
                    """)).scalar() or 0
            status = "OK" if orphans == 0 else f"FAIL ({orphans})"
            print(f"  {child}.{fk_col} -> {parent}: {status}")
            if orphans > 0:
                anomalies.append(f"Orphan FKs: {child}.{fk_col} ({orphans})")

        # 4. updated_at freshness (companies only — not all tables have it)
        if "companies" in existing:
            row = conn.execute(text("SELECT MAX(updated_at) FROM companies")).scalar()
            if row:
                print(f"\ncompanies.updated_at (latest): {row}")

        # 5. Sample rows
        print("\nSample rows (top 5 per main table):")
        for table in SAMPLE_TABLES:
            if table not in existing or counts.get(table, 0) == 0:
                print(f"  {table}: (empty)")
                continue
            rows = conn.execute(text(f"SELECT * FROM {table} LIMIT 5")).mappings().all()
            print(f"  {table}:")
            for r in rows:
                # Mask any URL-like values
                summary = {
                    k: (v if k not in ("raw_json", "raw_response", "weights_json") else "<json>")
                    for k, v in dict(r).items()
                }
                print(f"    {summary}")

    print(f"\nChecked at: {datetime.now(timezone.utc).isoformat()}")

    if anomalies:
        print(f"\nVERIFY FAILED — {len(anomalies)} anomaly(ies):")
        for a in anomalies:
            print(f"  - {a}")
        log.warning("verify_failed", anomalies=anomalies)
        return False

    print("\nVERIFY PASSED")
    log.info("verify_passed", phase=phase)
    return True


def main() -> None:
    """CLI entry: run the read-only DB health check; exit 1 on anomalies."""
    parser = argparse.ArgumentParser(description="Database health check")
    parser.add_argument("--phase", default="0", help="Phase label for reporting")
    parser.add_argument("--dry-run", action="store_true", help="No-op flag for consistency")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN: verify.py performs read-only checks only")
        return

    ok = verify(phase=args.phase)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
