"""Quick Layer 3 ingest status."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from db import get_engine

engine = get_engine()
with engine.connect() as conn:
    h = conn.execute(text("SELECT COUNT(*) FROM historical_trials")).scalar()
    f = conn.execute(text("SELECT COUNT(*) FROM fda_approvals")).scalar()
    conf = conn.execute(
        text(
            """
            SELECT primary_outcome_confidence, COUNT(*)
            FROM historical_trials
            GROUP BY primary_outcome_confidence
            ORDER BY 2 DESC
            """
        )
    ).fetchall()
    phase = conn.execute(
        text(
            """
            SELECT phase, COUNT(*) FROM historical_trials
            WHERE phase IS NOT NULL GROUP BY phase ORDER BY 2 DESC
            """
        )
    ).fetchall()
print(f"historical_trials: {h}")
print(f"fda_approvals: {f}")
print("confidence:", conf)
print("phase:", phase)
