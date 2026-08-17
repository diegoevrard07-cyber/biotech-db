"""Layer 1 verification checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_engine
from layers.layer1.seed_loader import load_company_seeds
from logger import setup_logger

log = setup_logger("verify_layer1")
ALLOWED_TYPES = config.ALLOWED_CATALYST_TYPES


def verify_layer1() -> bool:
    """Check Layer 1 data quality (companies/trials/catalysts); True when all pass."""
    failures: list[str] = []
    engine = get_engine()

    seed_count = 0
    try:
        seed_count = len(load_company_seeds())
    except FileNotFoundError:
        seed_count = 0

    with engine.connect() as conn:
        db_companies = conn.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0
        if seed_count and db_companies != seed_count:
            failures.append(
                f"companies count mismatch: CSV={seed_count}, DB={db_companies} "
                "(document diff if intentional)"
            )

        trial_companies = conn.execute(
            text("SELECT COUNT(DISTINCT company_id) FROM trials WHERE company_id IS NOT NULL")
        ).scalar() or 0
        pct = (trial_companies / db_companies * 100) if db_companies else 0
        if db_companies and pct < 80:
            failures.append(
                f"trials coverage low: {pct:.1f}% of companies have trials (need >=80%)"
            )

        # Catalysts per company with trials
        missing_cats = conn.execute(
            text(
                """
                SELECT c.ticker FROM companies c
                JOIN trials t ON t.company_id = c.id
                LEFT JOIN catalysts cat ON cat.company_id = c.id
                WHERE (t.phase ILIKE '%PHASE2%' OR t.phase ILIKE '%PHASE3%')
                  AND t.primary_completion_date IS NOT NULL
                  AND (t.primary_completion_date + INTERVAL '90 days') >= CURRENT_DATE
                GROUP BY c.id, c.ticker
                HAVING COUNT(DISTINCT cat.id) = 0
                """
            )
        ).fetchall()
        if missing_cats:
            failures.append(
                f"{len(missing_cats)} companies with upcoming Phase 2/3 readouts but zero catalysts: "
                f"{[r[0] for r in missing_cats[:10]]}"
            )

        orphan_checks = [
            ("catalysts", "company_id", "companies"),
            ("catalysts", "trial_id", "trials"),
            ("trials", "company_id", "companies"),
        ]
        for child, fk, parent in orphan_checks:
            orphans = conn.execute(
                text(
                    f"""
                    SELECT COUNT(*) FROM {child} c
                    LEFT JOIN {parent} p ON c.{fk} = p.id
                    WHERE c.{fk} IS NOT NULL AND p.id IS NULL
                    """
                )
            ).scalar() or 0
            if orphans > 0:
                failures.append(f"Orphan FK: {child}.{fk} ({orphans})")

        stale = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM catalysts
                WHERE expected_date < CURRENT_DATE - INTERVAL '30 days'
                """
            )
        ).scalar() or 0
        total_cats = conn.execute(text("SELECT COUNT(*) FROM catalysts")).scalar() or 0
        if total_cats and stale / total_cats > 0.2:
            failures.append(
                f"Too many stale catalysts: {stale}/{total_cats} ({100*stale/total_cats:.0f}%) >30 days past"
            )

        bad_types = conn.execute(
            text(
                """
                SELECT DISTINCT catalyst_type FROM catalysts
                WHERE catalyst_type IS NOT NULL
                """
            )
        ).fetchall()
        for (ctype,) in bad_types:
            if ctype not in ALLOWED_TYPES:
                failures.append(f"Invalid catalyst_type: {ctype}")

        by_type = conn.execute(
            text(
                """
                SELECT catalyst_type, COUNT(*) FROM catalysts
                GROUP BY catalyst_type ORDER BY COUNT(*) DESC
                """
            )
        ).fetchall()

        manual = conn.execute(
            text("SELECT COUNT(*) FROM catalysts WHERE requires_manual_verification = TRUE")
        ).scalar() or 0
        manual_pct = (manual / total_cats * 100) if total_cats else 0
        if total_cats and manual_pct > 80:
            failures.append(f"Catalyst validation failure: {manual_pct:.0f}% require manual verification")

        top = conn.execute(
            text(
                """
                SELECT c.ticker, COUNT(cat.id) AS n
                FROM companies c
                LEFT JOIN catalysts cat ON cat.company_id = c.id
                GROUP BY c.ticker ORDER BY n DESC LIMIT 10
                """
            )
        ).fetchall()

    print("\n=== Layer 1 Verification ===\n")
    print(f"Seed CSV companies:     {seed_count}")
    print(f"DB companies:           {db_companies}")
    print(f"Companies with trials:  {trial_companies} ({pct:.1f}%)")
    with engine.connect() as conn:
        total_trials = conn.execute(text("SELECT COUNT(*) FROM trials")).scalar()
        print(f"Total trials:           {total_trials}")
        print(f"Total catalysts:        {total_cats}")
        print(f"Manual verification:    {manual} ({manual_pct:.1f}%)")
        print("\nCatalysts by type:")
        for ctype, n in by_type:
            print(f"  {ctype}: {n}")
        print("\nTop 10 companies by catalyst count:")
        for tick, n in top:
            print(f"  {tick}: {n}")

    if failures:
        print(f"\n❌ FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        log.warning("verify_layer1_failed", failures=failures)
        return False

    print("\n✅ Layer 1 verification passed")
    log.info("verify_layer1_passed")
    return True


def main() -> None:
    """CLI entry: verify Layer 1 ingestion output; exit 1 on failures."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print("DRY RUN: verify_layer1 performs read-only checks")
    ok = verify_layer1()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
