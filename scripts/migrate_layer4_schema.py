"""Idempotent Layer 4 schema migration (catalysts provenance + material_events)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from logger import setup_logger

log = setup_logger("migrate_layer4_schema")

MIGRATION_SQL = """
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS sec_confirmed BOOLEAN DEFAULT FALSE;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS sec_source_accession TEXT;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS expected_date_original DATE;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS expected_date_history JSONB DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS material_events (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    ticker TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    filing_date DATE,
    event_type TEXT NOT NULL,
    event_date DATE,
    confidence TEXT NOT NULL,
    drug_name TEXT,
    extracted_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (accession_number, event_type, event_date)
);

CREATE INDEX IF NOT EXISTS idx_material_events_ticker ON material_events(ticker);
CREATE INDEX IF NOT EXISTS idx_material_events_type ON material_events(event_type);
CREATE INDEX IF NOT EXISTS idx_material_events_filed ON material_events(filing_date);
"""


def migrate(*, dry_run: bool = False) -> None:
    statements = [s.strip() for s in MIGRATION_SQL.split(";") if s.strip()]
    if dry_run:
        for stmt in statements:
            print(stmt + ";")
        return

    with get_connection() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
            log.info("executed", sql=stmt[:80])
    log.info("migration_complete")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
