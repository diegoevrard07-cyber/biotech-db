"""
Phase 7 - Calibration: is the edge real?

Joins edge_scores predictions to resolved catalyst_outcomes and reports:
  - Brier score of base_rate as a probability of a favorable (hit) outcome
  - reliability table (predicted bucket vs observed hit rate)
  - model hit rate (favorable trade types) vs the naive base-rate hit rate

Ambiguous outcomes are excluded from the binary scoring. Persists a snapshot to
calibration_runs unless --no-store. Read-only on outcomes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from layers.composite.calibration import brier_score, hit_rate, reliability_table
from logger import setup_logger

log = setup_logger("calibrate")

FAVORABLE_TRADES = ("buy_the_rumor", "hold_through")


def calibrate(*, store: bool = True) -> dict:
    """Score predictions against resolved outcomes (Brier, reliability, lift); snapshot to DB."""
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT es.base_rate_score, es.composite_score, es.trade_type,
                       o.outcome_label
                FROM catalyst_outcomes o
                JOIN edge_scores es ON es.catalyst_id = o.catalyst_id
                WHERE o.outcome_label IN ('hit', 'miss')
                """
            )
        ).mappings().all()

    pairs: list[tuple[float, int]] = []
    favorable_actuals: list[int] = []
    all_actuals: list[int] = []
    for r in rows:
        actual = 1 if r["outcome_label"] == "hit" else 0
        all_actuals.append(actual)
        if r["base_rate_score"] is not None:
            pairs.append((float(r["base_rate_score"]), actual))
        if r["trade_type"] in FAVORABLE_TRADES:
            favorable_actuals.append(actual)

    brier = brier_score(pairs)
    table = reliability_table(pairs, n_buckets=5)
    base_hr = hit_rate(all_actuals)
    model_hr = hit_rate(favorable_actuals)

    print("\n=== Calibration ===")
    print(f"Resolved hit/miss pairs:        {len(all_actuals)}")
    print(f"Pairs with base-rate prediction: {len(pairs)}")
    print(f"Brier score (base_rate):         {brier}")
    print(f"Naive base-rate hit rate:        {base_hr}")
    print(f"Model hit rate (favorable):      {model_hr}  (n={len(favorable_actuals)})")
    if model_hr is not None and base_hr is not None:
        lift = round(model_hr - base_hr, 4)
        print(f"Model lift over naive:           {lift:+}")
    print("Reliability table:")
    for b in table:
        print(f"  {b['bucket']}  n={b['n']:<4} pred={b['mean_predicted']:.3f} "
              f"observed={b['observed_hit_rate']:.3f}")
    if not all_actuals:
        print("NOTE: no resolved hit/miss outcomes yet - run resolve_outcomes.py after "
              "price ingestion. Calibration needs labeled history.")

    if store and all_actuals:
        with get_connection() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO calibration_runs (
                        n_pairs, brier_score, model_hit_rate, base_rate_hit_rate,
                        reliability_json, notes
                    ) VALUES (
                        :n, :brier, :model_hr, :base_hr, CAST(:rel AS jsonb), :notes
                    )
                    """
                ),
                {
                    "n": len(pairs), "brier": brier, "model_hr": model_hr,
                    "base_hr": base_hr, "rel": json.dumps(table),
                    "notes": f"favorable={FAVORABLE_TRADES}",
                },
            )
        print("Stored snapshot to calibration_runs.")

    log.info("calibrate_complete", n=len(all_actuals), brier=brier,
             base_hr=base_hr, model_hr=model_hr)
    return {"n": len(all_actuals), "brier": brier, "base_hr": base_hr, "model_hr": model_hr}


def main() -> None:
    """CLI entry: calibrate edge_scores against catalyst_outcomes (Phase 7)."""
    parser = argparse.ArgumentParser(description="Calibrate predictions vs outcomes")
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args()
    try:
        config.preflight()
        calibrate(store=not args.no_store)
    except Exception as exc:  # noqa: BLE001
        log.error("calibrate_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
