"""Apply base rates to upcoming catalysts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from layers.layer3.base_rate_lookup import get_base_rate_for_catalyst
from layers.layer3.indication_taxonomy import categorize_indication
from layers.layer3.outcome_extractor import normalize_phase
from layers.layer3.sponsor_classifier import classify_sponsor
from logger import setup_logger

log = setup_logger("apply_base_rates")
UNMAPPED_LOG = config.LOGS_DIR / "unmapped_catalysts.jsonl"


def apply(min_confidence: str = "medium", dry_run: bool = False) -> dict:
    mapped = 0
    unmapped = 0
    unmappable_no_phase = 0

    if not dry_run:
        UNMAPPED_LOG.parent.mkdir(parents=True, exist_ok=True)
        if UNMAPPED_LOG.exists():
            UNMAPPED_LOG.write_text("", encoding="utf-8")

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.id AS catalyst_id, c.catalyst_type, c.expected_date,
                       t.phase, t.indication, t.nct_id,
                       co.name AS company_name, co.primary_indication
                FROM catalysts c
                LEFT JOIN trials t ON c.trial_id = t.id
                LEFT JOIN companies co ON c.company_id = co.id
                WHERE c.expected_date >= CURRENT_DATE
                """
            )
        ).mappings().all()

        for row in rows:
            catalyst_type = row.get("catalyst_type") or ""
            phase = normalize_phase(row.get("phase"))
            conditions = []
            if row.get("indication"):
                conditions = [x.strip() for x in str(row["indication"]).split(",")]
            elif row.get("primary_indication"):
                conditions = [row["primary_indication"]]
            indication = categorize_indication(conditions)
            sponsor_class = classify_sponsor(row.get("company_name") or "", lookup_market_cap=True)

            result = get_base_rate_for_catalyst(
                catalyst_type=catalyst_type,
                phase=phase,
                indication_category=indication,
                sponsor_class=sponsor_class if sponsor_class != "unknown" else None,
                min_confidence=min_confidence,
            )

            if result is None and not phase and catalyst_type == "phase_readout":
                unmappable_no_phase += 1
                if not dry_run:
                    conn.execute(
                        text(
                            """
                            UPDATE catalysts SET
                                base_rate = NULL,
                                base_rate_n = NULL,
                                base_rate_ci_low = NULL,
                                base_rate_ci_high = NULL,
                                base_rate_slice_key = 'unmappable_no_phase',
                                base_rate_source = NULL
                            WHERE id = :catalyst_id
                            """
                        ),
                        {"catalyst_id": row["catalyst_id"]},
                    )
                    with UNMAPPED_LOG.open("a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {
                                    "catalyst_id": row["catalyst_id"],
                                    "nct_id": row.get("nct_id"),
                                    "catalyst_type": catalyst_type,
                                    "phase": phase,
                                    "indication": indication,
                                    "reason": "genuinely_no_phase",
                                }
                            )
                            + "\n"
                        )
                unmapped += 1
                continue

            if result is None:
                unmapped += 1
                if not dry_run:
                    with UNMAPPED_LOG.open("a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {
                                    "catalyst_id": row["catalyst_id"],
                                    "nct_id": row.get("nct_id"),
                                    "catalyst_type": catalyst_type,
                                    "phase": phase,
                                    "indication": indication,
                                    "sponsor_class": sponsor_class,
                                    "reason": "no_slice_meets_min_confidence",
                                }
                            )
                            + "\n"
                        )
                continue

            mapped += 1
            if not dry_run:
                conn.execute(
                    text(
                        """
                        UPDATE catalysts SET
                            base_rate = :base_rate,
                            base_rate_n = :base_rate_n,
                            base_rate_ci_low = :ci_low,
                            base_rate_ci_high = :ci_high,
                            base_rate_slice_key = :slice_key,
                            base_rate_source = :base_rate_source
                        WHERE id = :catalyst_id
                        """
                    ),
                    {
                        "catalyst_id": row["catalyst_id"],
                        "base_rate": result.success_rate,
                        "base_rate_n": result.n_trials,
                        "ci_low": result.ci_low,
                        "ci_high": result.ci_high,
                        "slice_key": result.slice_key,
                        "base_rate_source": result.rate_source,
                    },
                )

    print(f"Catalysts mapped: {mapped}, unmapped: {unmapped} (no_phase: {unmappable_no_phase})")
    log.info(
        "apply_base_rates_complete",
        mapped=mapped,
        unmapped=unmapped,
        unmappable_no_phase=unmappable_no_phase,
    )
    return {"mapped": mapped, "unmapped": unmapped, "unmappable_no_phase": unmappable_no_phase}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-confidence", default="medium")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply(min_confidence=args.min_confidence, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
