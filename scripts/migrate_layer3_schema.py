"""One-time migration: replace legacy historical_trials/base_rates with Layer 3 schema."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_connection
from logger import setup_logger

log = setup_logger("migrate_layer3_schema")


def migrate() -> None:
    """Drop and recreate historical_trials and base_rates with the Layer 3 schema."""
    with get_connection() as conn:
        conn.execute(text("DROP TABLE IF EXISTS base_rates CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS historical_trials CASCADE"))
        conn.execute(
            text(
                """
                CREATE TABLE historical_trials (
                    id SERIAL PRIMARY KEY,
                    nct_id TEXT UNIQUE NOT NULL,
                    phase TEXT,
                    conditions JSONB,
                    indication_category TEXT,
                    sponsor_name TEXT,
                    sponsor_class TEXT,
                    primary_completion_date DATE,
                    enrollment INTEGER,
                    primary_outcome_met BOOLEAN,
                    primary_outcome_confidence TEXT,
                    extraction_method TEXT,
                    raw_results JSONB,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE base_rates (
                    id SERIAL PRIMARY KEY,
                    slice_key TEXT UNIQUE NOT NULL,
                    phase TEXT,
                    indication_category TEXT,
                    sponsor_class TEXT,
                    n_trials INTEGER NOT NULL,
                    n_successes INTEGER NOT NULL,
                    success_rate NUMERIC(5,4),
                    ci_low NUMERIC(5,4),
                    ci_high NUMERIC(5,4),
                    confidence_tier TEXT,
                    computed_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_hist_phase_indication ON historical_trials(phase, indication_category)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_hist_sponsor_class ON historical_trials(sponsor_class)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_base_rates_lookup ON base_rates(phase, indication_category, sponsor_class)"))
    log.info("layer3_schema_migrated")
    print("Layer 3 schema migration complete")


if __name__ == "__main__":
    migrate()
