"""Load curated company universe from data/seeds/companies.csv."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import text

from db import get_connection
from layers.layer1.seed_loader import load_company_seeds
from logger import setup_logger

log = setup_logger("load_companies")


def _cell(row, col: str) -> str | None:
    val = row.get(col)
    if pd.isna(val) or str(val).strip() == "":
        return None
    return str(val).strip()


def load_companies(dry_run: bool = False) -> dict[str, int]:
    """Upsert the seed CSV into companies and delete rows no longer in the seed."""
    df = load_company_seeds()
    required = {
        "ticker",
        "name",
        "exchange",
        "market_cap_bucket",
        "primary_indication",
        "ctgov_sponsor_aliases",
        "notes",
    }
    missing = required - set(df.columns)
    if missing:
        print(f"ERROR: companies.csv missing columns: {missing}")
        sys.exit(1)

    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "removed": 0}
    seed_tickers = {str(t).strip().upper() for t in df["ticker"]}

    if dry_run:
        print(f"DRY RUN: would upsert {len(df)} companies")
        return stats

    with get_connection() as conn:
        existing_tickers = {
            row[0]
            for row in conn.execute(
                text("SELECT ticker FROM companies WHERE ticker IS NOT NULL")
            ).fetchall()
        }
        orphan_tickers = existing_tickers - seed_tickers
        if orphan_tickers:
            tickers_list = list(orphan_tickers)
            conn.execute(
                text("""
                    DELETE FROM catalysts WHERE company_id IN (
                        SELECT id FROM companies WHERE ticker = ANY(:tickers)
                    )
                    """),
                {"tickers": tickers_list},
            )
            conn.execute(
                text("""
                    DELETE FROM trials WHERE company_id IN (
                        SELECT id FROM companies WHERE ticker = ANY(:tickers)
                    )
                    """),
                {"tickers": tickers_list},
            )
            conn.execute(
                text("DELETE FROM companies WHERE ticker = ANY(:tickers)"),
                {"tickers": tickers_list},
            )
            stats["removed"] = len(orphan_tickers)
            log.info("removed_orphan_companies", tickers=sorted(orphan_tickers))

        for _, row in df.iterrows():
            ticker = str(row["ticker"]).strip().upper()
            existing = (
                conn.execute(
                    text("""
                    SELECT id, name, exchange, market_cap_bucket, primary_indication,
                           ctgov_sponsor_aliases, notes
                    FROM companies WHERE ticker = :t
                    """),
                    {"t": ticker},
                )
                .mappings()
                .first()
            )

            params = {
                "ticker": ticker,
                "name": _cell(row, "name"),
                "exchange": _cell(row, "exchange"),
                "market_cap_bucket": _cell(row, "market_cap_bucket"),
                "primary_indication": _cell(row, "primary_indication"),
                "ctgov_sponsor_aliases": _cell(row, "ctgov_sponsor_aliases"),
                "notes": _cell(row, "notes"),
            }

            if existing is None:
                conn.execute(
                    text("""
                        INSERT INTO companies (
                            ticker, name, exchange, market_cap_bucket, primary_indication,
                            ctgov_sponsor_aliases, notes
                        ) VALUES (
                            :ticker, :name, :exchange, :market_cap_bucket, :primary_indication,
                            :ctgov_sponsor_aliases, :notes
                        )
                        """),
                    params,
                )
                stats["inserted"] += 1
            else:
                compare_keys = (
                    "name",
                    "exchange",
                    "market_cap_bucket",
                    "primary_indication",
                    "ctgov_sponsor_aliases",
                    "notes",
                )
                changed = any(
                    str(existing.get(k) or "") != str(params.get(k) or "") for k in compare_keys
                )
                if changed:
                    conn.execute(
                        text("""
                            UPDATE companies SET
                                name = :name, exchange = :exchange,
                                market_cap_bucket = :market_cap_bucket,
                                primary_indication = :primary_indication,
                                ctgov_sponsor_aliases = :ctgov_sponsor_aliases,
                                notes = :notes, updated_at = NOW()
                            WHERE ticker = :ticker
                            """),
                        params,
                    )
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1

    log.info("companies_loaded", **stats)
    print(
        f"Companies: inserted={stats['inserted']}, updated={stats['updated']}, "
        f"unchanged={stats['unchanged']}, removed={stats['removed']}"
    )
    return stats


def main() -> None:
    """CLI entry: load the company universe seed CSV into the companies table."""
    parser = argparse.ArgumentParser(description="Load companies from seed CSV")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        load_companies(dry_run=args.dry_run)
    except Exception as exc:
        log.error("load_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
