"""
Ingest SEC XBRL companyfacts into financials table (cash, burn, runway).

Usage:
  python scripts/ingest_financials.py --limit 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from layers.layer4.sec_client import fetch_companyfacts
from layers.layer4.xbrl_extractor import (
    compute_burn_rate,
    compute_runway,
    extract_quarterly_concept,
)
from logger import setup_logger

log = setup_logger("ingest_financials")

CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsAndShortTermInvestments",
    "Cash",
]
STI_CONCEPTS = ["MarketableSecuritiesCurrent", "ShortTermInvestments"]
OCF_CONCEPT = "NetCashProvidedByUsedInOperatingActivities"
OPEX_CONCEPTS = ["ResearchAndDevelopmentExpense", "OperatingExpenses"]


def _latest_value(facts: dict, concepts: list[str]):
    for concept in concepts:
        quarters = extract_quarterly_concept(facts, concept)
        if quarters:
            return concept, sorted(quarters, key=lambda q: q.period_end)[-1]
    return None, None


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


def _latest_filing_id(conn, company_id: int) -> int | None:
    row = conn.execute(
        text("""
            SELECT id FROM sec_filings
            WHERE company_id = :cid
            ORDER BY filing_date DESC NULLS LAST, fetched_at DESC
            LIMIT 1
            """),
        {"cid": company_id},
    ).scalar()
    return int(row) if row else None


def ingest_financials(
    *,
    dry_run: bool = False,
    ticker: str | None = None,
    limit: int | None = None,
) -> dict:
    """Pull XBRL companyfacts per company; upsert cash, burn, and runway into financials."""
    stats = {"companies": 0, "written": 0, "skipped": 0, "errors": 0}

    with get_connection() as conn:
        companies = _load_companies(conn, ticker=ticker, limit=limit)

    for co in companies:
        stats["companies"] += 1
        cik = co["cik"]
        company_id = co["id"]
        ticker_u = co["ticker"]

        try:
            facts = fetch_companyfacts(cik)
        except Exception as exc:
            stats["errors"] += 1
            log.warning("companyfacts_failed", ticker=ticker_u, error=str(exc))
            continue

        if not facts.get("facts"):
            stats["skipped"] += 1
            continue

        _, cash_q = _latest_value(facts, CASH_CONCEPTS)
        _, sti_q = _latest_value(facts, STI_CONCEPTS)
        ocf_q_list = extract_quarterly_concept(facts, OCF_CONCEPT)
        _, opex_q = _latest_value(facts, OPEX_CONCEPTS)

        if not cash_q and not ocf_q_list:
            stats["skipped"] += 1
            continue

        period_end = cash_q.period_end if cash_q else ocf_q_list[-1].period_end
        cash = cash_q.value if cash_q else None
        sti = sti_q.value if sti_q else None
        has_liquidity_data = cash_q is not None or sti_q is not None
        total_liq = (cash or 0.0) + (sti or 0.0) if has_liquidity_data else None

        ocf_recent = sorted(ocf_q_list, key=lambda q: q.period_end)[-4:]
        quarterly_burn = compute_burn_rate(ocf_recent)
        if has_liquidity_data and total_liq is not None:
            runway = compute_runway(total_liq, quarterly_burn)
            runway_months = runway.get("runway_months")
            if runway_months == float("inf"):
                runway_months = None
        else:
            runway_months = None

        opex = opex_q.value if opex_q else None

        if dry_run:
            print(
                f"DRY RUN {ticker_u}: period={period_end} cash={cash} burn={quarterly_burn} "
                f"runway={runway_months}"
            )
            stats["written"] += 1
            continue

        with get_connection() as conn:
            filing_id = _latest_filing_id(conn, company_id)
            conn.execute(
                text("""
                    INSERT INTO financials (
                        company_id, filing_id, period_end,
                        cash_and_equivalents_usd, short_term_investments_usd,
                        total_liquidity_usd, quarterly_opex_usd, quarterly_burn_usd,
                        runway_months, computed_at
                    ) VALUES (
                        :company_id, :filing_id, :period_end,
                        :cash, :sti, :total_liq, :opex, :burn, :runway, NOW()
                    )
                    ON CONFLICT (company_id, period_end) DO UPDATE SET
                        filing_id = EXCLUDED.filing_id,
                        cash_and_equivalents_usd = EXCLUDED.cash_and_equivalents_usd,
                        short_term_investments_usd = EXCLUDED.short_term_investments_usd,
                        total_liquidity_usd = EXCLUDED.total_liquidity_usd,
                        quarterly_opex_usd = EXCLUDED.quarterly_opex_usd,
                        quarterly_burn_usd = EXCLUDED.quarterly_burn_usd,
                        runway_months = EXCLUDED.runway_months,
                        computed_at = NOW()
                    """),
                {
                    "company_id": company_id,
                    "filing_id": filing_id,
                    "period_end": period_end,
                    "cash": cash,
                    "sti": sti,
                    "total_liq": total_liq,
                    "opex": opex,
                    "burn": quarterly_burn,
                    "runway": runway_months,
                },
            )
        stats["written"] += 1
        log.info(
            "financials_written",
            ticker=ticker_u,
            period_end=str(period_end),
            burn=quarterly_burn,
            runway_months=runway_months,
        )

    log.info("ingest_financials_complete", **stats)
    print(
        f"Financials: companies={stats['companies']}, written={stats['written']}, "
        f"skipped={stats['skipped']}, errors={stats['errors']}"
    )
    return stats


def main() -> None:
    """CLI entry: ingest SEC XBRL financials (cash/burn/runway) for the universe."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ticker")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    ingest_financials(dry_run=args.dry_run, ticker=args.ticker, limit=args.limit)


if __name__ == "__main__":
    main()
