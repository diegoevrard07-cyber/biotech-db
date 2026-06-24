"""
Lever 1 (moneymaxxing gate) - Validate the base-rate model OUT OF SAMPLE.

Base rates are computed from historical_trials, so testing on the same rows is
circular. This does a temporal holdout: train slice success rates on OLDER trials,
predict on NEWER trials, and measure calibration (Brier), discrimination (AUC),
and reliability vs a naive "always predict the global mean" baseline.

If the model beats the baseline and the reliability curve tracks the diagonal,
the base-rate signal generalizes and is safe to size on. If not, fix it before
risking capital. Read-only except an optional calibration_runs snapshot.
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
from layers.composite.calibration import brier_score, reliability_table
from logger import setup_logger

log = setup_logger("validate_base_rates")

MIN_SLICE_N = 10


def _auc(pairs: list[tuple[float, int]]) -> float | None:
    """Probability a random hit is ranked above a random miss (Mann-Whitney)."""
    pos = [p for p, a in pairs if a == 1]
    neg = [p for p, a in pairs if a == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for pp in pos:
        for nn in neg:
            if pp > nn:
                wins += 1
            elif pp == nn:
                ties += 1
    return round((wins + 0.5 * ties) / (len(pos) * len(neg)), 4)


def _slice_keys(phase, indication, sponsor):
    """Finest-to-coarsest backoff keys for a trial."""
    return [
        ("p+i+s", (phase, indication, sponsor)),
        ("p+i", (phase, indication, None)),
        ("p", (phase, None, None)),
    ]


def validate(*, cutoff_quantile: float = 0.7, store: bool = True) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT phase, indication_category, sponsor_class,
                       primary_completion_date AS pcd,
                       primary_outcome_met AS met
                FROM historical_trials
                WHERE primary_outcome_met IS NOT NULL
                  AND primary_completion_date IS NOT NULL
                  AND phase IS NOT NULL
                ORDER BY primary_completion_date
                """
            )
        ).mappings().all()

    data = [
        (r["phase"], r["indication_category"], r["sponsor_class"], r["pcd"],
         1 if r["met"] else 0)
        for r in rows
    ]
    if len(data) < 200:
        print(f"Only {len(data)} labeled trials - too few to validate.")
        return {"n": len(data)}

    cut_idx = int(len(data) * cutoff_quantile)
    cutoff_date = data[cut_idx][3]
    train = [d for d in data if d[3] < cutoff_date]
    test = [d for d in data if d[3] >= cutoff_date]
    if not train or not test:
        print("Temporal split produced an empty side.")
        return {"n": len(data)}

    # Build slice success rates on TRAIN at every backoff granularity.
    from collections import defaultdict
    agg: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])  # key -> [successes, n]
    global_succ = sum(d[4] for d in train)
    global_n = len(train)
    global_rate = global_succ / global_n
    for phase, ind, spon, _pcd, met in train:
        for _label, key in _slice_keys(phase, ind, spon):
            agg[key][0] += met
            agg[key][1] += 1

    def predict(phase, ind, spon) -> float:
        for _label, key in _slice_keys(phase, ind, spon):
            s, n = agg[key]
            if n >= MIN_SLICE_N:
                return s / n
        return global_rate

    model_pairs: list[tuple[float, int]] = []
    naive_pairs: list[tuple[float, int]] = []
    for phase, ind, spon, _pcd, met in test:
        model_pairs.append((predict(phase, ind, spon), met))
        naive_pairs.append((global_rate, met))

    model_brier = brier_score(model_pairs)
    naive_brier = brier_score(naive_pairs)
    auc = _auc(model_pairs)
    table = reliability_table(model_pairs, n_buckets=10)
    test_rate = sum(a for _, a in model_pairs) / len(model_pairs)
    skill = None
    if model_brier is not None and naive_brier not in (None, 0):
        skill = round(1 - model_brier / naive_brier, 4)  # Brier skill score

    print("\n=== Base-rate temporal validation ===")
    print(f"Labeled trials:     {len(data)}  (train {len(train)} < {cutoff_date} <= test {len(test)})")
    print(f"Train global rate:  {global_rate:.3f}   Test actual rate: {test_rate:.3f}")
    print(f"Model Brier:        {model_brier}")
    print(f"Naive Brier:        {naive_brier}   (always predict global mean)")
    print(f"Brier skill score:  {skill}   (>0 means model beats naive)")
    print(f"AUC:                {auc}   (0.5 = no discrimination, >0.6 useful)")
    print("Reliability (predicted -> observed):")
    for b in table:
        print(f"  {b['bucket']}  n={b['n']:<5} pred={b['mean_predicted']:.3f} "
              f"obs={b['observed_hit_rate']:.3f}")

    verdict = "INCONCLUSIVE"
    if auc is not None and skill is not None:
        if auc >= 0.6 and skill > 0:
            verdict = "EDGE: base rates generalize out-of-sample"
        elif auc >= 0.55 and skill > 0:
            verdict = "WEAK EDGE: some generalization"
        else:
            verdict = "NO EDGE: base rates do not beat the naive baseline out-of-sample"
    print(f"\nVERDICT: {verdict}")

    if store:
        with get_connection() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO calibration_runs (
                        n_pairs, brier_score, model_hit_rate, base_rate_hit_rate,
                        reliability_json, notes
                    ) VALUES (:n, :brier, :mhr, :bhr, CAST(:rel AS jsonb), :notes)
                    """
                ),
                {
                    "n": len(test), "brier": model_brier,
                    "mhr": auc, "bhr": round(global_rate, 4),
                    "rel": json.dumps(table),
                    "notes": f"base_rate_temporal_holdout cutoff={cutoff_date} "
                             f"skill={skill} auc={auc} verdict={verdict}",
                },
            )
        print("Stored snapshot to calibration_runs.")

    log.info("validate_complete", n=len(data), model_brier=model_brier,
             naive_brier=naive_brier, skill=skill, auc=auc, verdict=verdict)
    return {"model_brier": model_brier, "naive_brier": naive_brier,
            "skill": skill, "auc": auc, "verdict": verdict}


def main() -> None:
    parser = argparse.ArgumentParser(description="Out-of-sample base-rate validation")
    parser.add_argument("--cutoff-quantile", type=float, default=0.7)
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args()
    try:
        config.preflight()
        validate(cutoff_quantile=args.cutoff_quantile, store=not args.no_store)
    except Exception as exc:  # noqa: BLE001
        log.error("validate_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
