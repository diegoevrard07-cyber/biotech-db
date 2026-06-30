"""Ingest FDA drug approvals from openFDA."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm import tqdm

import config
from db import get_connection
from layers.layer3.indication_taxonomy import categorize_indication
from logger import setup_logger

log = setup_logger("ingest_fda_approvals")

OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"
CACHE_DIR = config.CACHE_DIR / "openfda"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60), reraise=True)
def _fetch(params: dict) -> dict:
    time.sleep(0.25)
    resp = requests.get(OPENFDA_URL, params=params, timeout=60)
    if resp.status_code == 429:
        time.sleep(5)
        raise RuntimeError("openFDA rate limited")
    resp.raise_for_status()
    return resp.json()


def _parse_approval(record: dict, start_year: int, end_year: int) -> list[dict]:
    rows: list[dict] = []
    app_num = record.get("application_number")
    sponsor = record.get("sponsor_name", "")
    products = record.get("products") or []
    submissions = record.get("submissions") or []

    for sub in submissions:
        if sub.get("submission_status") != "AP":
            continue
        date_str = sub.get("submission_status_date")
        if not date_str or len(date_str) < 8:
            continue
        year = int(date_str[:4])
        if year < start_year or year > end_year:
            continue
        approval_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        sub_type = sub.get("submission_type", "")
        is_novel = sub_type == "ORIG" and sub.get("submission_number") in ("1", 1, "01")

        for product in products:
            drug_name = product.get("brand_name") or product.get("active_ingredients", [{}])[0].get(
                "name", ""
            )
            for ia in product.get("active_ingredients") or [{}]:
                pass
            indication_text = ""
            for ai in product.get("active_ingredients") or []:
                indication_text = ai.get("name", "") or indication_text

            rows.append(
                {
                    "application_number": f"{app_num}_{sub.get('submission_number')}_{date_str}",
                    "sponsor_name": sponsor,
                    "drug_name": drug_name,
                    "approval_date": approval_date,
                    "indication": indication_text,
                    "indication_category": categorize_indication(
                        [indication_text] if indication_text else []
                    ),
                    "submission_type": sub_type,
                    "is_novel": is_novel,
                    "raw_data": json.dumps(record),
                }
            )
    return rows


def ingest(
    start_year: int = 2010, end_year: int = 2024, limit: int | None = None, dry_run: bool = False
) -> dict:
    """Page through openFDA drugsfda and upsert approvals into fda_approvals."""
    upserted = 0
    skip = 0
    batch_size = 100
    pending: list[dict] = []

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(min=2, max=60),
        retry=retry_if_exception_type(OperationalError),
        reraise=True,
    )
    def _write_batch(rows: list[dict]) -> None:
        with get_connection() as conn:
            for row in rows:
                conn.execute(
                    text("""
                        INSERT INTO fda_approvals (
                            application_number, sponsor_name, drug_name, approval_date,
                            indication, indication_category, submission_type, is_novel, raw_data
                        ) VALUES (
                            :application_number, :sponsor_name, :drug_name, :approval_date,
                            :indication, :indication_category, :submission_type, :is_novel,
                            CAST(:raw_data AS jsonb)
                        )
                        ON CONFLICT (application_number) DO UPDATE SET
                            sponsor_name = EXCLUDED.sponsor_name,
                            drug_name = EXCLUDED.drug_name,
                            approval_date = EXCLUDED.approval_date,
                            indication = EXCLUDED.indication,
                            indication_category = EXCLUDED.indication_category,
                            submission_type = EXCLUDED.submission_type,
                            is_novel = EXCLUDED.is_novel,
                            raw_data = EXCLUDED.raw_data
                        """),
                    row,
                )

    def flush() -> None:
        """Write the pending batch and add it to the upserted count."""
        nonlocal upserted
        if not pending:
            return
        _write_batch(pending)
        upserted += len(pending)
        pending.clear()

    while True:
        params = {"limit": batch_size, "skip": skip}
        data = _fetch(params)
        results = data.get("results") or []
        if not results:
            break

        for record in tqdm(results, desc=f"FDA skip={skip}", leave=False):
            parsed_rows = _parse_approval(record, start_year, end_year)
            for row in parsed_rows:
                if dry_run:
                    upserted += 1
                else:
                    pending.append(row)
                    if len(pending) >= 50:
                        flush()
                if limit and upserted >= limit:
                    if not dry_run:
                        flush()
                    print(f"FDA approvals upserted: {upserted}")
                    return {"upserted": upserted}

        if not dry_run:
            flush()

        skip += batch_size
        total = data.get("meta", {}).get("results", {}).get("total", skip)
        if skip >= total:
            break

    print(f"FDA approvals upserted: {upserted}")
    log.info("fda_ingest_complete", upserted=upserted)
    return {"upserted": upserted}


def main() -> None:
    """CLI entry: ingest FDA approval history from openFDA (Layer 3 base-rate input)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started = datetime.now()
    ingest(args.start_year, args.end_year, args.limit, args.dry_run)
    print(f"Elapsed: {(datetime.now() - started).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
