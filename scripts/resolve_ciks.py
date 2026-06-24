"""Resolve and store SEC CIKs for seeded companies."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from layers.layer4.sec_client import fetch_company_ticker_map, format_cik
from logger import setup_logger

log = setup_logger("resolve_ciks")


def resolve_ciks(*, dry_run: bool = False) -> dict[str, int]:
    ticker_map = fetch_company_ticker_map()
    stats = {"resolved": 0, "missing": 0, "unchanged": 0}

    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT id, ticker, cik FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")
        ).mappings().all()

        for row in rows:
            ticker = str(row["ticker"]).upper()
            cik = ticker_map.get(ticker)
            if not cik:
                stats["missing"] += 1
                log.warning("cik_not_found", ticker=ticker)
                continue

            existing = row.get("cik")
            if existing and format_cik(existing) == format_cik(cik):
                stats["unchanged"] += 1
                continue

            stats["resolved"] += 1
            if dry_run:
                print(f"would set {ticker} -> {format_cik(cik)}")
                continue

            conn.execute(
                text("UPDATE companies SET cik = :cik, updated_at = NOW() WHERE id = :id"),
                {"cik": format_cik(cik), "id": row["id"]},
            )

    log.info("resolve_ciks_complete", **stats)
    print(
        f"CIKs: resolved={stats['resolved']}, unchanged={stats['unchanged']}, "
        f"missing={stats['missing']}"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    resolve_ciks(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
