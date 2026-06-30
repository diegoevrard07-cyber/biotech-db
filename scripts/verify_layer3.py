"""Layer 3 verification checks."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_engine
from logger import setup_logger

log = setup_logger("verify_layer3")


def verify_layer3() -> bool:
    failures: list[str] = []
    engine = get_engine()

    with engine.connect() as conn:
        hist = conn.execute(text("SELECT COUNT(*) FROM historical_trials")).scalar() or 0
        if hist < 20000:
            failures.append(f"historical_trials count {hist} < 20000")

        high_pct = conn.execute(
            text(
                """
                SELECT 100.0 * SUM(CASE WHEN primary_outcome_confidence='high' THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0)
                FROM historical_trials
                """
            )
        ).scalar() or 0
        if high_pct < 25:
            failures.append(f"high-confidence extraction {high_pct:.1f}% < 25%")

        any_pct = conn.execute(
            text(
                """
                SELECT 100.0 * SUM(
                    CASE WHEN primary_outcome_met IS NOT NULL THEN 1 ELSE 0 END
                ) / NULLIF(COUNT(*),0)
                FROM historical_trials
                """
            )
        ).scalar() or 0
        if any_pct < 29:
            failures.append(f"any-confidence extraction {any_pct:.1f}% < 29%")

        fda = conn.execute(text("SELECT COUNT(*) FROM fda_approvals")).scalar() or 0
        if fda < 1000:
            failures.append(f"fda_approvals count {fda} < 1000")

        slices = conn.execute(
            text("SELECT COUNT(*) FROM base_rates WHERE source IS NULL OR source = 'computed'")
        ).scalar() or 0
        if slices < 50:
            failures.append(f"computed base_rates slices {slices} < 50")

        priors = conn.execute(
            text("SELECT COUNT(*) FROM base_rates WHERE source = 'industry_prior'")
        ).scalar() or 0
        if priors < 4:
            failures.append(f"industry_prior rows {priors} < 4")

        for phase in ("PHASE1", "PHASE2", "PHASE3", "PHASE4"):
            exists = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM base_rates
                    WHERE phase = :p AND indication_category IS NULL AND sponsor_class IS NULL
                      AND (source IS NULL OR source = 'computed')
                    """
                ),
                {"p": phase},
            ).scalar()
            if not exists:
                failures.append(f"missing phase-only slice for {phase}")

        bad_rates = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM base_rates
                WHERE success_rate < 0 OR success_rate > 1
                """
            )
        ).scalar()
        if bad_rates:
            failures.append(f"success_rate out of range: {bad_rates}")

        bad_ci = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM base_rates
                WHERE ci_low > success_rate OR success_rate > ci_high
                """
            )
        ).scalar()
        if bad_ci:
            failures.append(f"CI ordering violations: {bad_ci}")

        upcoming = conn.execute(
            text("SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE")
        ).scalar() or 0
        mapped = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM catalysts
                WHERE expected_date >= CURRENT_DATE
                  AND (base_rate IS NOT NULL OR base_rate_slice_key = 'unmappable_no_phase')
                """
            )
        ).scalar() or 0
        coverage_pct = 100 * mapped / upcoming if upcoming else 100
        if coverage_pct < 99:
            failures.append(f"catalyst coverage {coverage_pct:.1f}% < 99%")

        missing_source = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM catalysts
                WHERE expected_date >= CURRENT_DATE
                  AND base_rate IS NOT NULL
                  AND base_rate_source IS NULL
                """
            )
        ).scalar() or 0
        if missing_source:
            failures.append(f"{missing_source} mapped catalysts missing base_rate_source")

        print("\n=== Layer 3 Verification ===\n")
        print(f"historical_trials:     {hist}")
        print(f"high-confidence %:     {high_pct:.1f}%")
        print(f"any-confidence %:      {any_pct:.1f}%")
        print(f"fda_approvals:         {fda}")
        print(f"computed slices:       {slices}")
        print(f"industry priors:       {priors}")
        print(f"upcoming catalysts:    {upcoming}")
        print(f"base_rate mapped:      {mapped} ({coverage_pct:.1f}% coverage)")

        print("\nBase rates by phase (top indications):")
        rows = conn.execute(
            text(
                """
                SELECT phase, indication_category, sponsor_class, n_trials, success_rate, confidence_tier
                FROM base_rates
                WHERE phase IN ('PHASE2','PHASE3')
                  AND (source IS NULL OR source = 'computed')
                ORDER BY phase, n_trials DESC
                LIMIT 20
                """
            )
        ).fetchall()
        for r in rows:
            print(f"  {r[0]} | {r[1] or '-'} | {r[2] or '-'} | n={r[3]} rate={float(r[4]):.1%} tier={r[5]}")

        brates = conn.execute(
            text(
                "SELECT base_rate FROM catalysts WHERE expected_date >= CURRENT_DATE AND base_rate IS NOT NULL"
            )
        ).fetchall()
        if brates:
            buckets = Counter()
            for (rate,) in brates:
                r = float(rate)
                if r < 0.2:
                    buckets["0-20%"] += 1
                elif r < 0.4:
                    buckets["20-40%"] += 1
                elif r < 0.6:
                    buckets["40-60%"] += 1
                else:
                    buckets["60%+"] += 1
            print("\nUpcoming catalyst base rate histogram:")
            for k in sorted(buckets.keys()):
                print(f"  {k}: {buckets[k]}")

        sources = conn.execute(
            text(
                """
                SELECT COALESCE(base_rate_source, 'unset'), COUNT(*)
                FROM catalysts
                WHERE expected_date >= CURRENT_DATE AND base_rate IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC
                """
            )
        ).fetchall()
        if sources:
            print("\nbase_rate_source distribution:")
            for src, cnt in sources:
                print(f"  {src}: {cnt}")

        print("\nData Source Limitations:")
        print("  - Any-confidence = trials with primary_outcome_met set (~30% CT.gov ceiling).")
        print("  - High-confidence extraction is bounded by CT.gov structured data (~30% ceiling).")
        print("  - Endpoint-met rate != phase transition rate != approval rate.")
        print("  - PDUFA/adcom rates use industry priors (source=industry_prior), not openFDA CRL data.")

    if failures:
        print(f"\nFAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return False

    print("\nLayer 3 verification passed")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    ok = verify_layer3()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
