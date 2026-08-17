"""
Reconcile catalyst dates with SEC-confirmed events from material_events table.

RULES:
1. Only overwrite when SEC event confidence is 'high' (default --min-confidence).
2. Preserve original date in expected_date_original on first overwrite.
3. Append every change to expected_date_history.
4. Never overwrite a more-recent sec_confirmed source with an older filing.
5. Match by ticker, mapped event type, drug name (fuzzy 90+), date ±90 days.

Usage:
  python scripts/reconcile_pdufa_dates.py --dry-run --limit 20
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from layers.layer4.pdufa_reconciliation import (
    CatalystRow,
    MaterialEvent,
    catalyst_type_for_event,
    fuzzy_match,
    reconcile_catalyst,
)
from logger import setup_logger

log = setup_logger("reconcile_pdufa_dates")


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def load_catalysts(conn, *, ticker: str | None = None) -> list[CatalystRow]:
    """Load SEC-relevant catalysts (pdufa/adcom/approval/crl) as CatalystRow records."""
    sql = """
        SELECT c.id, co.ticker, c.catalyst_type, c.expected_date,
               COALESCE(c.raw_data->>'drug_name', c.description) AS drug_name,
               COALESCE(c.sec_confirmed, FALSE) AS sec_confirmed,
               c.sec_source_accession, c.expected_date_original,
               COALESCE(c.expected_date_history, '[]'::jsonb) AS expected_date_history
        FROM catalysts c
        JOIN companies co ON co.id = c.company_id
        WHERE c.catalyst_type IN ('pdufa', 'advisory_committee', 'approval', 'crl')
    """
    params: dict = {}
    if ticker:
        sql += " AND co.ticker = :ticker"
        params["ticker"] = ticker.upper()
    rows = conn.execute(text(sql), params).mappings().all()
    return [
        CatalystRow(
            id=r["id"],
            ticker=r["ticker"],
            catalyst_type=r["catalyst_type"],
            expected_date=_parse_date(r["expected_date"]),
            drug_name=r.get("drug_name"),
            sec_confirmed=bool(r["sec_confirmed"]),
            sec_source_accession=r.get("sec_source_accession"),
            expected_date_original=_parse_date(r.get("expected_date_original")),
            expected_date_history=list(r["expected_date_history"] or []),
        )
        for r in rows
    ]


def load_material_events(conn, *, since: date | None = None) -> list[MaterialEvent]:
    """Load material_events (optionally filed since a date) as MaterialEvent records."""
    sql = """
        SELECT ticker, event_type, event_date, confidence, accession_number,
               filing_date, drug_name, extracted_data
        FROM material_events
        WHERE 1=1
    """
    params: dict = {}
    if since:
        sql += " AND filing_date >= :since"
        params["since"] = since
    rows = conn.execute(text(sql), params).mappings().all()
    return [
        MaterialEvent(
            ticker=r["ticker"],
            event_type=r["event_type"],
            event_date=_parse_date(r["event_date"]),
            confidence=r["confidence"],
            accession_number=r["accession_number"],
            filed_date=_parse_date(r["filing_date"]) or date.today(),
            drug_name=r.get("drug_name"),
            extracted_data=dict(r.get("extracted_data") or {}),
        )
        for r in rows
    ]


def load_filing_dates(conn) -> dict[str, date]:
    """Map accession_number -> filing_date from sec_filings (recency guard input)."""
    rows = (
        conn.execute(
            text(
                "SELECT accession_number, filing_date FROM sec_filings WHERE filing_date IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    out: dict[str, date] = {}
    for r in rows:
        if r["accession_number"] and r["filing_date"]:
            out[r["accession_number"]] = _parse_date(r["filing_date"])
    return out


def apply_update(conn, catalyst_id: int, updates: dict, history_entry: dict) -> None:
    """Write one reconciled date change, preserving the original and appending history."""
    conn.execute(
        text("""
            UPDATE catalysts
            SET expected_date = COALESCE(:expected_date, expected_date),
                sec_confirmed = :sec_confirmed,
                sec_source_accession = :sec_source_accession,
                expected_date_original = COALESCE(:expected_date_original, expected_date_original),
                expected_date_history = COALESCE(expected_date_history, '[]'::jsonb) || CAST(:history AS jsonb)
            WHERE id = :id
            """),
        {
            "id": catalyst_id,
            "expected_date": updates.get("expected_date"),
            "sec_confirmed": updates.get("sec_confirmed", True),
            "sec_source_accession": updates.get("sec_source_accession"),
            "expected_date_original": updates.get("expected_date_original"),
            "history": json.dumps([history_entry]),
        },
    )


def create_catalysts_from_events(
    conn,
    events: list[MaterialEvent],
    *,
    min_confidence: str = "high",
    dry_run: bool = True,
) -> int:
    """Insert SEC-sourced pdufa/adcom catalysts when no close match exists."""
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    min_rank = confidence_rank.get(min_confidence, 3)

    existing = conn.execute(text("""
            SELECT co.ticker, c.catalyst_type, c.expected_date,
                   COALESCE(c.raw_data->>'drug_name', c.description) AS drug_name
            FROM catalysts c
            JOIN companies co ON co.id = c.company_id
            WHERE c.catalyst_type IN ('pdufa', 'advisory_committee')
            """)).mappings().all()

    created = 0
    for ev in events:
        cat_type = catalyst_type_for_event(ev.event_type)
        if not cat_type or confidence_rank.get(ev.confidence, 0) < min_rank:
            continue
        if not ev.event_date:
            continue

        duplicate = False
        for row in existing:
            if row["ticker"].upper() != ev.ticker.upper():
                continue
            if row["catalyst_type"] != cat_type:
                continue
            if row["expected_date"] and abs((row["expected_date"] - ev.event_date).days) <= 90:
                duplicate = True
                break
            if row.get("drug_name") and ev.drug_name:
                if fuzzy_match(row["drug_name"], ev.drug_name):
                    duplicate = True
                    break
        if duplicate:
            continue

        company = conn.execute(
            text("SELECT id FROM companies WHERE ticker = :t"),
            {"t": ev.ticker.upper()},
        ).scalar()
        if not company:
            continue

        description = f"SEC {ev.event_type}"
        if ev.drug_name:
            description += f" — {ev.drug_name}"

        if dry_run:
            print(
                f"would create catalyst: {ev.ticker} {cat_type} {ev.event_date} ({ev.accession_number})"
            )
            created += 1
            continue

        conn.execute(
            text("""
                INSERT INTO catalysts (
                    company_id, catalyst_type, expected_date, date_confidence,
                    description, source, source_url, sec_confirmed, sec_source_accession,
                    raw_data, requires_manual_verification
                ) VALUES (
                    :company_id, :catalyst_type, :expected_date, :date_confidence,
                    :description, 'sec_8k', :source_url, TRUE, :accession,
                    CAST(:raw_data AS jsonb), FALSE
                )
                """),
            {
                "company_id": company,
                "catalyst_type": cat_type,
                "expected_date": ev.event_date,
                "date_confidence": ev.confidence,
                "description": description,
                "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ev.ticker}",
                "accession": ev.accession_number,
                "raw_data": json.dumps({"drug_name": ev.drug_name, "event_type": ev.event_type}),
            },
        )
        existing.append(
            {
                "ticker": ev.ticker,
                "catalyst_type": cat_type,
                "expected_date": ev.event_date,
                "drug_name": ev.drug_name,
            }
        )
        created += 1

    return created


def run(
    *,
    dry_run: bool = True,
    ticker: str | None = None,
    since: date | None = None,
    min_confidence: str = "high",
    limit: int | None = None,
    create_missing: bool = False,
) -> dict:
    """Match SEC events to catalysts and overwrite dates per the reconciliation rules."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = (
        Path(__file__).resolve().parents[1] / "data" / "logs" / f"pdufa_reconciliation_{ts}.jsonl"
    )

    stats = {"updated": 0, "unchanged": 0, "proposed": 0}
    proposals: list[dict] = []

    with get_connection() as conn:
        catalysts = load_catalysts(conn, ticker=ticker)
        events = load_material_events(conn, since=since)
        filing_dates = load_filing_dates(conn)

        if create_missing:
            created = create_catalysts_from_events(
                conn, events, min_confidence=min_confidence, dry_run=dry_run
            )
            stats["created"] = created
            if not dry_run and created:
                catalysts = load_catalysts(conn, ticker=ticker)
        else:
            stats["created"] = 0

        events_by_ticker: dict[str, list[MaterialEvent]] = {}
        for ev in events:
            events_by_ticker.setdefault(ev.ticker.upper(), []).append(ev)

        log_file = None if dry_run else log_path.open("w", encoding="utf-8")
        try:
            for cat in catalysts:
                candidates = events_by_ticker.get(cat.ticker.upper(), [])
                updates, history = reconcile_catalyst(
                    cat,
                    candidates,
                    filing_dates=filing_dates,
                    min_confidence=min_confidence,
                )
                if not updates:
                    stats["unchanged"] += 1
                    continue

                proposal = {
                    "catalyst_id": cat.id,
                    "ticker": cat.ticker,
                    "catalyst_type": cat.catalyst_type,
                    "old_date": cat.expected_date.isoformat() if cat.expected_date else None,
                    "new_date": (
                        updates["expected_date"].isoformat()
                        if updates.get("expected_date")
                        else None
                    ),
                    "accession": updates.get("sec_source_accession"),
                    "reasoning": history,
                }
                proposals.append(proposal)

                if dry_run:
                    stats["proposed"] += 1
                    continue

                apply_update(conn, cat.id, updates, history)
                if log_file:
                    log_file.write(json.dumps(proposal) + "\n")
                stats["updated"] += 1
        finally:
            if log_file:
                log_file.close()

    if dry_run:
        for p in proposals[: limit or 20]:
            print(json.dumps(p, indent=2))
    else:
        log.info("reconciliation_complete", **stats, log_file=str(log_path))

    stats["proposals"] = len(proposals)
    return stats


def main() -> None:
    """CLI entry: reconcile catalyst dates against SEC-confirmed material events."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print proposed changes without writing (default)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply updates to catalysts and write jsonl log",
    )
    parser.add_argument("--ticker")
    parser.add_argument("--since", help="YYYY-MM-DD")
    parser.add_argument("--min-confidence", default="high")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create new pdufa/adcom catalysts from unmatched SEC events",
    )
    args = parser.parse_args()

    since = date.fromisoformat(args.since) if args.since else None
    stats = run(
        dry_run=not args.write,
        ticker=args.ticker,
        since=since,
        min_confidence=args.min_confidence,
        limit=args.limit,
        create_missing=args.create_missing,
    )
    print(
        f"Summary: proposed={stats.get('proposed', 0)} updated={stats['updated']} "
        f"created={stats.get('created', 0)} unchanged={stats['unchanged']}"
    )


if __name__ == "__main__":
    main()
