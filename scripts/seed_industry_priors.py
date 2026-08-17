"""
Seed hardcoded industry priors for PDUFA/adcom catalyst types.

These rows use synthetic n=100 for illustrative Wilson-style CIs — they are NOT
derived from our openFDA ingest (which lacks CRL/filing denominators). Sources:
published BIO/Informa/Tufts CSDD approval-rate ranges, conservatively rounded.

Slice keys use a separate namespace (pdufa|..., adcom|...) and do not collide with
computed phase=...|indication=...|sponsor=... keys.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_connection
from logger import setup_logger

log = setup_logger("seed_industry_priors")

PRIORS = [
    {
        "slice_key": "pdufa|novel_nda_bla",
        "n_trials": 100,
        "n_successes": 85,
        "success_rate": 0.85,
        "ci_low": 0.77,
        "ci_high": 0.91,
        "confidence_tier": "high",
        "notes": "P(approval|novel NDA/BLA filing); BIO/Informa ~80-90%",
    },
    {
        "slice_key": "pdufa|snda_efficacy_supplement",
        "n_trials": 100,
        "n_successes": 92,
        "success_rate": 0.92,
        "ci_low": 0.85,
        "ci_high": 0.96,
        "confidence_tier": "high",
        "notes": "P(approval|sNDA efficacy supplement); higher than novel",
    },
    {
        "slice_key": "adcom|positive_vote_to_approval",
        "n_trials": 100,
        "n_successes": 88,
        "success_rate": 0.88,
        "ci_low": 0.81,
        "ci_high": 0.93,
        "confidence_tier": "high",
        "notes": "P(FDA approval | positive AdCom vote)",
    },
    {
        "slice_key": "adcom|any_vote_held",
        "n_trials": 100,
        "n_successes": 75,
        "success_rate": 0.75,
        "ci_low": 0.66,
        "ci_high": 0.83,
        "confidence_tier": "high",
        "notes": "P(FDA approval | any AdCom held); default when vote unknown",
    },
]


def seed(dry_run: bool = False) -> int:
    """Upsert the hand-curated industry prior slices into base_rates."""
    inserted = 0
    with get_connection() as conn:
        for prior in PRIORS:
            if dry_run:
                inserted += 1
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO base_rates (
                        slice_key, phase, indication_category, sponsor_class,
                        n_trials, n_successes, success_rate, ci_low, ci_high,
                        confidence_tier, source, computed_at
                    ) VALUES (
                        :slice_key, NULL, NULL, NULL,
                        :n_trials, :n_successes, :success_rate, :ci_low, :ci_high,
                        :confidence_tier, 'industry_prior', NOW()
                    )
                    ON CONFLICT (slice_key) DO UPDATE SET
                        n_trials = EXCLUDED.n_trials,
                        n_successes = EXCLUDED.n_successes,
                        success_rate = EXCLUDED.success_rate,
                        ci_low = EXCLUDED.ci_low,
                        ci_high = EXCLUDED.ci_high,
                        confidence_tier = EXCLUDED.confidence_tier,
                        source = EXCLUDED.source,
                        computed_at = NOW()
                    """
                ),
                prior,
            )
            inserted += 1
    log.info("industry_priors_seeded", count=inserted)
    print(f"Industry priors upserted: {inserted}")
    return inserted


def main() -> None:
    """CLI entry: seed industry-prior base rates (PDUFA/AdCom benchmarks)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
