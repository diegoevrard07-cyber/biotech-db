"""Apply schema.sql to Supabase Postgres (idempotent)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

import config
from db import get_connection, get_engine
from logger import setup_logger

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

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
    "price_history",
    "positioning",
    "insider_transactions",
    "catalyst_outcomes",
    "calibration_runs",
    "portfolio_account",
    "portfolio_holdings",
    "portfolio_performance",
    "event_returns",
]


def apply_schema(dry_run: bool = False) -> list[str]:
    """Execute schema.sql statements. Returns list of tables verified."""
    log = setup_logger("apply_schema")

    if not config.DATABASE_URL:
        log.error("missing_database_url")
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    # Strip line comments before splitting — a leading file comment must not swallow the first CREATE.
    cleaned_lines = [line.split("--")[0] for line in sql.splitlines()]
    cleaned_sql = "\n".join(line for line in cleaned_lines if line.strip())
    statements = [s.strip() for s in cleaned_sql.split(";") if s.strip()]

    log.info("schema_load", path=str(SCHEMA_PATH), statement_count=len(statements))

    if dry_run:
        print(f"DRY RUN: would execute {len(statements)} SQL statements from {SCHEMA_PATH.name}")
        for table in EXPECTED_TABLES:
            print(f"  - CREATE TABLE IF NOT EXISTS {table}")
        return EXPECTED_TABLES

    with get_connection() as conn:
        for i, stmt in enumerate(statements, 1):
            try:
                conn.execute(text(stmt))
                log.info("statement_executed", index=i)
            except Exception as exc:
                log.error("statement_failed", index=i, error=str(exc))
                print(f"ERROR on statement {i}: {exc}")
                sys.exit(1)

    # Verify tables exist
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
        )
        existing = {row[0] for row in result}

    missing = [t for t in EXPECTED_TABLES if t not in existing]
    if missing:
        log.error("tables_missing", missing=missing)
        print(f"ERROR: missing tables after apply: {missing}")
        sys.exit(1)

    log.info("schema_applied", tables=sorted(existing & set(EXPECTED_TABLES)))
    print(f"SUCCESS: {len(EXPECTED_TABLES)} tables verified in public schema")
    for t in EXPECTED_TABLES:
        print(f"  OK {t}")
    return EXPECTED_TABLES


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply database schema (idempotent)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without DB writes")
    args = parser.parse_args()
    apply_schema(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
