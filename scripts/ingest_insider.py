"""
Phase 4 - Ingest SEC Form 4 insider transactions.

For each in-universe company with a CIK, scan recent Form 4 filings, parse the
ownership XML, and upsert individual transactions. Open-market purchases (code P)
are the empirically useful bullish signal consumed later by the decision scorer.

Reuses the throttled/retrying sec_client. Per-company failures are logged and
skipped. Idempotent. Supports --dry-run, --limit, --ticker, --lookback-days.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection, get_engine
from layers.layer4 import sec_client
from layers.layer4.form4_parser import parse_form4
from logger import setup_logger

log = setup_logger("ingest_insider")

DEFAULT_LOOKBACK_DAYS = 730


def _find_form4_xml(cik: str, adsh: str) -> str | None:
    """Locate the raw ownership XML inside a filing (skip the xsl rendering)."""
    cik_int = str(int(cik))
    acc = adsh.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/index.json"
    try:
        resp = sec_client.sec_get(url)
        if resp.status_code == 404:
            return None
        items = resp.json().get("directory", {}).get("item", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("index_fetch_failed", cik=cik, adsh=adsh, error=str(exc))
        return None
    if isinstance(items, dict):
        items = [items]
    xmls = [it.get("name", "") for it in items if it.get("name", "").lower().endswith(".xml")]
    for name in xmls:
        if not name.lower().startswith("xsl"):
            return name
    return xmls[0] if xmls else None


_UPSERT = text(
    """
    INSERT INTO insider_transactions (
        company_id, cik, accession_number, filing_date, transaction_date,
        insider_name, insider_role, transaction_code, shares, price_per_share,
        value_usd, is_purchase, source, created_at
    ) VALUES (
        :company_id, :cik, :accession_number, :filing_date, :transaction_date,
        :insider_name, :insider_role, :transaction_code, :shares, :price_per_share,
        :value_usd, :is_purchase, 'sec_form4', NOW()
    )
    ON CONFLICT (accession_number, insider_name, transaction_date, transaction_code, shares)
    DO UPDATE SET
        price_per_share = EXCLUDED.price_per_share,
        value_usd = EXCLUDED.value_usd,
        is_purchase = EXCLUDED.is_purchase,
        insider_role = EXCLUDED.insider_role
    """
)


def ingest(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    ticker: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict:
    since = date.today() - timedelta(days=lookback_days)
    summary = {"companies": 0, "filings": 0, "transactions": 0, "purchases": 0, "errors": []}

    with get_connection() as conn:
        q = """
            SELECT id, ticker, cik FROM companies
            WHERE ticker IS NOT NULL AND cik IS NOT NULL
              AND COALESCE(in_universe, TRUE) = TRUE
        """
        params: dict = {}
        if ticker:
            q += " AND ticker = :t"
            params["t"] = ticker.upper()
        q += " ORDER BY ticker"
        if limit:
            q += f" LIMIT {int(limit)}"
        companies = [dict(r) for r in conn.execute(text(q), params).mappings().all()]

    # One pooled connection, commit PER COMPANY: visible progress + isolation.
    conn = get_engine().connect()
    try:
        for co in companies:
            cid, tk, cik = co["id"], co["ticker"], co["cik"]
            summary["companies"] += 1
            try:
                subs = sec_client.fetch_submissions(cik)
                if not subs:
                    continue
                filings = sec_client.iter_recent_filings(
                    subs, forms=frozenset({"4"}), since=since
                )
                for f in filings:
                    adsh = f["accession_number"]
                    xml_name = _find_form4_xml(cik, adsh)
                    if not xml_name:
                        continue
                    try:
                        xml_text = sec_client.download_filing_html(cik, adsh, xml_name)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("form4_download_failed", ticker=tk, adsh=adsh, error=str(exc))
                        continue
                    parsed = parse_form4(xml_text)
                    summary["filings"] += 1
                    for t in parsed["transactions"]:
                        if t.get("shares") is None or not t.get("transaction_date"):
                            continue
                        summary["transactions"] += 1
                        if t.get("is_purchase"):
                            summary["purchases"] += 1
                        if dry_run:
                            continue
                        conn.execute(
                            _UPSERT,
                            {
                                "company_id": cid,
                                "cik": cik,
                                "accession_number": adsh,
                                "filing_date": f.get("filing_date"),
                                "transaction_date": t.get("transaction_date"),
                                "insider_name": t.get("insider_name") or "UNKNOWN",
                                "insider_role": t.get("insider_role"),
                                "transaction_code": t.get("transaction_code") or "?",
                                "shares": t.get("shares"),
                                "price_per_share": t.get("price_per_share"),
                                "value_usd": t.get("value_usd"),
                                "is_purchase": bool(t.get("is_purchase")),
                            },
                        )
                if not dry_run:
                    conn.commit()
                print(f"  {tk}: {len(filings)} form4 filings", flush=True)
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                log.error("insider_failed", ticker=tk, error=str(exc))
                summary["errors"].append(f"{tk}: {exc}")
    finally:
        conn.close()

    print("\n=== Insider Ingestion Summary ===")
    print(f"Companies scanned:  {summary['companies']}")
    print(f"Form 4 filings:     {summary['filings']}")
    print(f"Transactions:       {summary['transactions']}")
    print(f"Open-market buys:   {summary['purchases']}")
    if summary["errors"]:
        print(f"Errors ({len(summary['errors'])}): {summary['errors'][:5]}")
    if dry_run:
        print("(dry run - no rows written)")

    log.info("insider_complete", **{k: v for k, v in summary.items() if k != "errors"})
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEC Form 4 insider transactions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ticker", type=str)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args()
    try:
        config.preflight(require_sec=True)
        ingest(
            dry_run=args.dry_run, limit=args.limit, ticker=args.ticker,
            lookback_days=args.lookback_days,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("ingest_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
