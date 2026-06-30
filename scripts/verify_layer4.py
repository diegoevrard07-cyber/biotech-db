"""Layer 4 verification checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from db import get_engine
from logger import setup_logger

log = setup_logger("verify_layer4")


def verify_layer4() -> bool:
    """Check Layer 4 data quality (CIKs, filings, events, scores); True when all pass."""
    failures: list[str] = []
    engine = get_engine()

    with engine.connect() as conn:
        with_cik = (
            conn.execute(text("SELECT COUNT(*) FROM companies WHERE cik IS NOT NULL")).scalar() or 0
        )
        total = conn.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0
        if with_cik < total * 0.5:
            failures.append(f"companies with CIK {with_cik}/{total} < 50%")

        filings = conn.execute(text("SELECT COUNT(*) FROM sec_filings")).scalar() or 0
        if filings < 1:
            failures.append("sec_filings count < 1")

        events = conn.execute(text("SELECT COUNT(*) FROM material_events")).scalar() or 0
        pdufa_events = conn.execute(text("""
                SELECT COUNT(*) FROM material_events
                WHERE event_type IN ('pdufa_assigned', 'pdufa_delayed', 'adcom_scheduled')
                """)).scalar() or 0
        regulatory_events = conn.execute(text("""
                SELECT COUNT(*) FROM material_events
                WHERE event_type IN (
                    'pdufa_assigned', 'pdufa_delayed', 'adcom_scheduled', 'approval', 'crl'
                )
                """)).scalar() or 0

        financials = conn.execute(text("SELECT COUNT(*) FROM financials")).scalar() or 0
        if financials < 1:
            failures.append("financials count < 1")

        sec_confirmed = (
            conn.execute(text("SELECT COUNT(*) FROM catalysts WHERE sec_confirmed = TRUE")).scalar()
            or 0
        )

        edge = conn.execute(text("SELECT COUNT(*) FROM edge_scores")).scalar() or 0

        print("\n=== Layer 4 Verification ===\n")
        print(f"  companies with CIK:     {with_cik}/{total}")
        print(f"  sec_filings:            {filings}")
        print(
            f"  material_events:        {events} (pdufa/adcom: {pdufa_events}, regulatory: {regulatory_events})"
        )
        print(f"  financials rows:        {financials}")
        print(f"  sec_confirmed catalysts:{sec_confirmed}")
        print(f"  edge_scores:            {edge}")

        if filings < 100 and with_cik >= 50:
            failures.append(f"sec_filings {filings} < 100 (expected ~1500+ after full ingest)")
        if events < 20 and filings >= 100:
            failures.append(f"material_events {events} < 20")
        if regulatory_events < 5 and filings >= 100:
            failures.append(
                f"regulatory material_events {regulatory_events} < 5 " "(pdufa/adcom/approval/crl)"
            )
        if financials < with_cik * 0.5 and with_cik >= 50:
            failures.append(f"financials {financials} < 50% of CIK companies ({with_cik})")
        if edge < 100:
            failures.append(f"edge_scores {edge} < 100")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return False

    print("\nLayer 4 verification passed")
    return True


def main() -> None:
    """CLI entry: verify Layer 4 SEC pipeline output; exit 1 on failures."""
    parser = argparse.ArgumentParser()
    parser.parse_args()
    ok = verify_layer4()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
