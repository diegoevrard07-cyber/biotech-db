"""Generate Layer 3 final report data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from db import get_engine

engine = get_engine()
with engine.connect() as conn:
    print("=== Phase 2/3 base rates by indication ===")
    rows = conn.execute(
        text(
            """
            SELECT phase, COALESCE(indication_category,'(all)'), n_trials,
                   ROUND(success_rate::numeric, 4), confidence_tier
            FROM base_rates
            WHERE phase IN ('PHASE2','PHASE3') AND sponsor_class IS NULL
            ORDER BY phase, n_trials DESC
            """
        )
    ).fetchall()
    for r in rows:
        print(f"  {r[0]} | {r[1]} | n={r[2]} rate={float(r[3]):.1%} tier={r[4]}")

    print("\n=== Base rate slices by confidence tier ===")
    tiers = conn.execute(
        text("SELECT confidence_tier, COUNT(*) FROM base_rates GROUP BY 1 ORDER BY 2 DESC")
    ).fetchall()
    print(tiers)

    print("\n=== Catalyst base rate distribution ===")
    hist = conn.execute(
        text(
            """
            SELECT
              CASE
                WHEN base_rate < 0.2 THEN '0-20%'
                WHEN base_rate < 0.4 THEN '20-40%'
                WHEN base_rate < 0.6 THEN '40-60%'
                ELSE '60%+'
              END AS bucket,
              COUNT(*)
            FROM catalysts WHERE expected_date >= CURRENT_DATE AND base_rate IS NOT NULL
            GROUP BY 1 ORDER BY 1
            """
        )
    ).fetchall()
    print(hist)

    unmapped = conn.execute(
        text(
            "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE AND base_rate IS NULL"
        )
    ).scalar()
    print(f"\nUnmapped upcoming catalysts: {unmapped}")
