"""
Fetch SEC 8-K filings, parse material events, upsert sec_filings + material_events.

Usage:
  python scripts/fetch_filings.py --limit 5          # smoke test
  python scripts/fetch_filings.py --since 2023-01-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from layers.layer4.eight_k_parser import parse_8k
from layers.layer4.sec_client import (
    CATALYST_EVENT_TYPES,
    download_filing_html,
    fetch_submissions,
    iter_recent_filings,
    primary_doc_url,
    resolve_primary_doc,
)
from logger import setup_logger

log = setup_logger("fetch_filings")

DEFAULT_SINCE = date.today() - timedelta(days=730)


def _load_companies(conn, *, ticker: str | None = None, limit: int | None = None):
    sql = """
        SELECT id, ticker, cik FROM companies
        WHERE cik IS NOT NULL AND ticker IS NOT NULL
    """
    params: dict = {}
    if ticker:
        sql += " AND ticker = :ticker"
        params["ticker"] = ticker.upper()
    sql += " ORDER BY ticker"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(text(sql), params).mappings().all()


def _known_accessions(conn) -> set[str]:
    rows = conn.execute(text("SELECT accession_number FROM sec_filings")).fetchall()
    return {r[0] for r in rows}


def _upsert_filing(
    conn,
    *,
    company_id: int,
    accession: str,
    filing_type: str,
    filing_date: date | None,
    url: str,
    raw_json: dict,
) -> int:
    row = conn.execute(
        text(
            """
            INSERT INTO sec_filings (
                company_id, accession_number, filing_type, filing_date, url, raw_json
            ) VALUES (
                :company_id, :accession, :filing_type, :filing_date, :url, CAST(:raw_json AS jsonb)
            )
            ON CONFLICT (accession_number) DO UPDATE SET
                filing_date = EXCLUDED.filing_date,
                url = EXCLUDED.url,
                raw_json = EXCLUDED.raw_json,
                fetched_at = NOW()
            RETURNING id
            """
        ),
        {
            "company_id": company_id,
            "accession": accession,
            "filing_type": filing_type,
            "filing_date": filing_date,
            "url": url,
            "raw_json": json.dumps(raw_json),
        },
    ).scalar_one()
    return int(row)


def _upsert_material_event(
    conn,
    *,
    company_id: int,
    ticker: str,
    accession: str,
    filing_date: date | None,
    event,
) -> None:
    extracted = {
        "raw_excerpt": event.raw_excerpt,
        "item_number": event.item_number,
        "indication": event.indication,
    }
    conn.execute(
        text(
            """
            INSERT INTO material_events (
                company_id, ticker, accession_number, filing_date,
                event_type, event_date, confidence, drug_name, extracted_data
            ) VALUES (
                :company_id, :ticker, :accession, :filing_date,
                :event_type, :event_date, :confidence, :drug_name, CAST(:extracted AS jsonb)
            )
            ON CONFLICT (accession_number, event_type, event_date) DO UPDATE SET
                confidence = EXCLUDED.confidence,
                drug_name = EXCLUDED.drug_name,
                extracted_data = EXCLUDED.extracted_data,
                filing_date = EXCLUDED.filing_date
            """
        ),
        {
            "company_id": company_id,
            "ticker": ticker,
            "accession": accession,
            "filing_date": filing_date,
            "event_type": event.event_type,
            "event_date": event.event_date,
            "confidence": event.confidence,
            "drug_name": event.drug_name,
            "extracted": json.dumps(extracted),
        },
    )


def fetch_filings(
    *,
    dry_run: bool = False,
    since: date | None = None,
    ticker: str | None = None,
    limit: int | None = None,
    max_filings_per_company: int = 20,
    reparse: bool = False,
) -> dict:
    since = since or DEFAULT_SINCE
    stats = {
        "companies": 0,
        "filings_fetched": 0,
        "filings_skipped": 0,
        "events_written": 0,
        "errors": 0,
    }

    with get_connection() as conn:
        companies = _load_companies(conn, ticker=ticker, limit=limit)

    for co in companies:
        stats["companies"] += 1
        cik = co["cik"]
        ticker_u = co["ticker"]
        company_id = co["id"]

        try:
            submissions = fetch_submissions(cik)
        except Exception as exc:
            stats["errors"] += 1
            log.warning("submissions_failed", ticker=ticker_u, error=str(exc))
            continue

        filings = iter_recent_filings(submissions, forms=frozenset({"8-K"}), since=since)
        filings = filings[:max_filings_per_company]

        with get_connection() as conn:
            known = set() if reparse else _known_accessions(conn)

            for filing in filings:
                accession = filing["accession_number"]
                if accession in known and not reparse:
                    stats["filings_skipped"] += 1
                    continue

                filed = (
                    date.fromisoformat(filing["filing_date"])
                    if filing.get("filing_date")
                    else None
                )

                resolved = resolve_primary_doc(cik, accession)
                if not resolved:
                    stats["errors"] += 1
                    log.warning("no_primary_doc", ticker=ticker_u, accession=accession)
                    continue

                filename, _ = resolved
                url = primary_doc_url(cik, accession, filename)

                if dry_run:
                    print(f"DRY RUN: {ticker_u} {accession} {filed}")
                    stats["filings_fetched"] += 1
                    continue

                try:
                    html = download_filing_html(cik, accession, filename)
                except Exception as exc:
                    stats["errors"] += 1
                    log.warning("download_failed", ticker=ticker_u, accession=accession, error=str(exc))
                    continue

                events = parse_8k(html)
                catalyst_events = [e for e in events if e.event_type in CATALYST_EVENT_TYPES]

                filing_id = _upsert_filing(
                    conn,
                    company_id=company_id,
                    accession=accession,
                    filing_type="8-K",
                    filing_date=filed,
                    url=url,
                    raw_json={"items": filing, "events_found": len(events)},
                )

                for ev in events:
                    _upsert_material_event(
                        conn,
                        company_id=company_id,
                        ticker=ticker_u,
                        accession=accession,
                        filing_date=filed,
                        event=ev,
                    )
                    stats["events_written"] += 1

                known.add(accession)
                stats["filings_fetched"] += 1
                log.info(
                    "filing_ingested",
                    ticker=ticker_u,
                    accession=accession,
                    catalyst_events=len(catalyst_events),
                    filing_id=filing_id,
                )

    log.info("fetch_filings_complete", **stats)
    print(
        f"Filings: companies={stats['companies']}, fetched={stats['filings_fetched']}, "
        f"skipped={stats['filings_skipped']}, events={stats['events_written']}, "
        f"errors={stats['errors']}"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", help="YYYY-MM-DD")
    parser.add_argument("--ticker")
    parser.add_argument("--limit", type=int, help="Limit companies processed")
    parser.add_argument("--max-filings", type=int, default=20)
    parser.add_argument("--reparse", action="store_true", help="Re-fetch known accessions")
    args = parser.parse_args()

    since = date.fromisoformat(args.since) if args.since else None
    fetch_filings(
        dry_run=args.dry_run,
        since=since,
        ticker=args.ticker,
        limit=args.limit,
        max_filings_per_company=args.max_filings,
        reparse=args.reparse,
    )


if __name__ == "__main__":
    main()
