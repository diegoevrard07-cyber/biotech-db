"""Compute base_rates slices from historical_trials and fda_approvals."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from layers.layer3.compute_utils import build_slice_key, confidence_tier, wilson_ci
from logger import setup_logger

log = setup_logger("compute_base_rates")

PHASES = ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]


def _write_slice(conn, phase, indication, sponsor, n, successes) -> bool:
    if n < 5:
        return False
    rate = successes / n
    ci_low, ci_high = wilson_ci(successes, n)
    slice_key = build_slice_key(
        phase=phase,
        indication=indication,
        sponsor=sponsor,
    )
    tier = confidence_tier(n)
    conn.execute(
        text(
            """
            INSERT INTO base_rates (
                slice_key, phase, indication_category, sponsor_class,
                n_trials, n_successes, success_rate, ci_low, ci_high, confidence_tier, source, computed_at
            ) VALUES (
                :slice_key, :phase, :indication_category, :sponsor_class,
                :n_trials, :n_successes, :success_rate, :ci_low, :ci_high, :confidence_tier, 'computed', NOW()
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
            WHERE base_rates.source IS NULL OR base_rates.source = 'computed'
            """
        ),
        {
            "slice_key": slice_key,
            "phase": phase,
            "indication_category": indication,
            "sponsor_class": sponsor,
            "n_trials": n,
            "n_successes": successes,
            "success_rate": round(rate, 4),
            "ci_low": round(ci_low, 4),
            "ci_high": round(ci_high, 4),
            "confidence_tier": tier,
        },
    )
    return True


def compute(dry_run: bool = False) -> dict:
    written = 0
    with get_connection() as conn:
        if not dry_run:
            conn.execute(
                text(
                    "DELETE FROM base_rates WHERE source IS NULL OR source = 'computed'"
                )
            )

        # Trials with known outcomes only
        rows = conn.execute(
            text(
                """
                SELECT phase, indication_category, sponsor_class,
                       COUNT(*) AS n,
                       SUM(CASE WHEN primary_outcome_met THEN 1 ELSE 0 END) AS successes
                FROM historical_trials
                WHERE primary_outcome_met IS NOT NULL AND phase IS NOT NULL
                GROUP BY phase, indication_category, sponsor_class
                """
            )
        ).mappings().all()

        slices_to_write: list[tuple] = []

        # Aggregate for broader slices
        agg: dict[tuple, list[int]] = {}
        for r in rows:
            key_full = (r["phase"], r["indication_category"], r["sponsor_class"])
            agg[key_full] = [r["n"], r["successes"]]

        # phase only
        phase_agg: dict[str, list[int]] = {}
        phase_ind_agg: dict[tuple, list[int]] = {}
        phase_sponsor_agg: dict[tuple, list[int]] = {}
        ind_agg: dict[str, list[int]] = {}

        for (phase, ind, sponsor), (n, succ) in agg.items():
            phase_agg[phase] = [phase_agg.get(phase, [0, 0])[0] + n, phase_agg.get(phase, [0, 0])[1] + succ]
            phase_ind_agg[(phase, ind)] = [
                phase_ind_agg.get((phase, ind), [0, 0])[0] + n,
                phase_ind_agg.get((phase, ind), [0, 0])[1] + succ,
            ]
            phase_sponsor_agg[(phase, sponsor)] = [
                phase_sponsor_agg.get((phase, sponsor), [0, 0])[0] + n,
                phase_sponsor_agg.get((phase, sponsor), [0, 0])[1] + succ,
            ]
            ind_agg[ind] = [ind_agg.get(ind, [0, 0])[0] + n, ind_agg.get(ind, [0, 0])[1] + succ]

        for phase, ind, sponsor in agg:
            slices_to_write.append((phase, ind, sponsor, agg[(phase, ind, sponsor)]))
        for phase, ind in phase_ind_agg:
            slices_to_write.append((phase, ind, None, phase_ind_agg[(phase, ind)]))
        for phase, sponsor in phase_sponsor_agg:
            slices_to_write.append((phase, None, sponsor, phase_sponsor_agg[(phase, sponsor)]))
        for phase in phase_agg:
            slices_to_write.append((phase, None, None, phase_agg[phase]))
        for ind in ind_agg:
            slices_to_write.append((None, ind, None, ind_agg[ind]))

        # PDUFA proxy from FDA novel approvals by indication
        fda_rows = conn.execute(
            text(
                """
                SELECT indication_category, COUNT(*) AS n,
                       SUM(CASE WHEN is_novel THEN 1 ELSE 0 END) AS successes
                FROM fda_approvals
                GROUP BY indication_category
                """
            )
        ).mappings().all()
        fda_total_n = 0
        fda_total_succ = 0
        for r in fda_rows:
            fda_total_n += r["n"]
            fda_total_succ += r["successes"]
            slices_to_write.append(("PDUFA", r["indication_category"], None, [r["n"], r["successes"]]))
        if fda_total_n:
            slices_to_write.append(("PDUFA", None, None, [fda_total_n, fda_total_succ]))

        if dry_run:
            written = sum(1 for _, _, _, (n, _) in slices_to_write if n >= 5)
        else:
            for phase, ind, sponsor, (n, succ) in slices_to_write:
                if _write_slice(conn, phase, ind, sponsor, n, succ):
                    written += 1

    print(f"Base rate slices written: {written}")
    log.info("compute_base_rates_complete", written=written)
    return {"written": written}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not config.DATABASE_URL:
        sys.exit(1)
    started = datetime.now()
    compute(dry_run=args.dry_run)
    print(f"Elapsed: {(datetime.now() - started).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
