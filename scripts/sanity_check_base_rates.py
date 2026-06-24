"""
Sanity check base rates against industry benchmarks.

Important distinctions:
- Endpoint-met != phase advance != approval. These benchmarks measure endpoint-met rate
  (primary endpoint met on CT.gov posted results), not BIO/Informa phase-transition rates.
- CT.gov reporting bias: trials with positive structured results post more often, inflating
  positive rates by roughly 5-10pp vs real-world transition statistics.
- Industry "phase success" figures (BIO, Informa, Tufts CSDD) typically refer to phase
  transition (e.g. P2->P3), which is stricter than endpoint-met at readout.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_engine

BENCHMARKS = [
    ("Phase 1 endpoint met", "PHASE1", None, None, 0.50, 0.70),
    ("Phase 2 endpoint met", "PHASE2", None, None, 0.38, 0.55),
    ("Phase 3 endpoint met", "PHASE3", None, None, 0.55, 0.72),
    ("Oncology Phase 2", "PHASE2", "oncology_solid", None, 0.25, 0.45),
    ("Rare disease Phase 3", "PHASE3", "rare_genetic", None, 0.55, 0.80),
]


def _classify(rate: float, lo: float, hi: float) -> str:
    if rate < 0 or rate > 1:
        return "FAIL"
    if lo <= rate <= hi:
        return "OK"
    if rate < lo - 0.15 or rate > hi + 0.15:
        return "FAIL"
    if (lo - rate > 0 and rate <= 0) or (rate - hi > 0 and rate >= 1):
        return "FAIL"
    if rate < lo - 0.10 or rate > hi + 0.10:
        return "FAIL"
    return "INFO"


def main() -> None:
    engine = get_engine()
    print("\n=== Base Rate Sanity Check ===\n")
    print(f"{'Slice':<45} {'Actual':>8} {'Expected':>12} {'Flag':>6}")
    print("-" * 75)
    failures = 0
    infos = 0

    with engine.connect() as conn:
        for label, phase, ind, sponsor, lo, hi in BENCHMARKS:
            clauses = ["phase = :phase", "indication_category IS NULL", "sponsor_class IS NULL"]
            params: dict = {"phase": phase}
            if ind:
                clauses = ["phase = :phase", "indication_category = :ind", "sponsor_class IS NULL"]
                params["ind"] = ind
            row = conn.execute(
                text(
                    f"""
                    SELECT success_rate, n_trials FROM base_rates
                    WHERE {' AND '.join(clauses)}
                      AND (source IS NULL OR source = 'computed')
                    LIMIT 1
                    """
                ),
                params,
            ).first()
            if not row:
                print(f"{label:<45} {'N/A':>8} {lo:.0%}-{hi:.0%} {'MISS':>6}")
                continue
            rate = float(row[0])
            flag = _classify(rate, lo, hi)
            if flag == "FAIL":
                failures += 1
            elif flag == "INFO":
                infos += 1
            print(f"{label:<45} {rate:>7.1%} {lo:.0%}-{hi:.0%} {flag:>6} (n={row[1]})")

    if failures:
        print(f"\n{failures} benchmark(s) deviate >15pp or sign-flipped")
        sys.exit(1)
    if infos:
        print(f"\nSanity check passed with {infos} informational deviation(s) (within 10-15pp)")
    else:
        print("\nSanity check passed (all within expected ranges)")


if __name__ == "__main__":
    main()
