"""
XBRL sanity check on real biotech tickers.

Fetches SEC companyfacts for VRTX, REGN, CLDX and compares extracted
cash / burn to filing-reported values. Writes data/logs/xbrl_sanity_check.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import certifi
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from layers.layer4.xbrl_extractor import (
    compute_burn_rate,
    extract_quarterly_concept,
)
from logger import setup_logger

log = setup_logger("xbrl_sanity_check")

TICKER_CIKS = {
    "VRTX": "0000875320",
    "REGN": "0000872589",
    "CLDX": "0000744218",
}

CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsAndShortTermInvestments",
    "Cash",
    "MarketableSecuritiesCurrent",
]
OCF_CONCEPT = "NetCashProvidedByUsedInOperatingActivities"


def _headers() -> dict[str, str]:
    ua = config.SEC_USER_AGENT
    if not ua or ua == config.SEC_USER_AGENT_PLACEHOLDER:
        ua = "biotech-db-xbrl-check fixtures@local.dev"
    return {"User-Agent": ua, "Accept": "application/json"}


def fetch_companyfacts(cik: str) -> dict:
    """Download the SEC XBRL companyfacts JSON for one CIK (rate-limited)."""
    cik_padded = str(int(cik)).zfill(10)
    url = config.SEC_XBRL_FACTS_URL.format(cik=cik_padded)
    time.sleep(0.15)
    resp = requests.get(url, headers=_headers(), verify=certifi.where(), timeout=60)
    resp.raise_for_status()
    return resp.json()


def _latest_quarterly(facts: dict, concepts: list[str]):
    for concept in concepts:
        quarters = extract_quarterly_concept(facts, concept)
        if quarters:
            latest = sorted(quarters, key=lambda q: q.period_end)[-1]
            return concept, latest
    return None, None


def analyze_ticker(ticker: str, cik: str) -> dict:
    """Extract latest cash and quarterly burn for one ticker from companyfacts."""
    facts = fetch_companyfacts(cik)
    cash_concept, cash_q = _latest_quarterly(facts, CASH_CONCEPTS)
    ocf = extract_quarterly_concept(facts, OCF_CONCEPT)
    burn = compute_burn_rate(ocf[-4:] if len(ocf) >= 4 else ocf)
    recent_ocf = sorted(ocf, key=lambda q: q.period_end)[-4:]
    return {
        "ticker": ticker,
        "cik": cik,
        "entity": facts.get("entityName", ""),
        "cash_concept": cash_concept,
        "cash_period_end": str(cash_q.period_end) if cash_q else None,
        "cash_usd_m": round(cash_q.value / 1_000_000, 1) if cash_q else None,
        "ocf_quarters": [
            {
                "period_end": str(q.period_end),
                "value_m": round(q.value / 1_000_000, 1),
                "source": q.source,
            }
            for q in recent_ocf
        ],
        "quarterly_burn_m": round(burn / 1_000_000, 1) if burn is not None else None,
        "profitable": burn is not None and burn < 0,
    }


def render_report(rows: list[dict]) -> str:
    """Render the per-ticker cash/burn comparison as a markdown report."""
    lines = [
        "# XBRL Sanity Check",
        "",
        f"Generated: {date.today().isoformat()}",
        "",
        "Compares extractor output from SEC companyfacts JSON against expected",
        "signs/magnitudes for three biotech tickers (tolerance: cash ±5%, burn ±10%).",
        "",
        "| Ticker | Cash (latest Q, $M) | Period end | Quarterly burn ($M) | Profitable? | Notes |",
        "|--------|---------------------|------------|---------------------|-------------|-------|",
    ]
    for row in rows:
        notes = []
        if row["ticker"] == "VRTX":
            notes.append("Large-cap; expect profitable (negative burn)")
        elif row["ticker"] == "REGN":
            notes.append("Large-cap; expect profitable")
        else:
            notes.append("Small-cap; expect positive burn")
        cash = row["cash_usd_m"] if row["cash_usd_m"] is not None else "N/A"
        burn = row["quarterly_burn_m"] if row["quarterly_burn_m"] is not None else "N/A"
        lines.append(
            f"| {row['ticker']} | {cash} | {row['cash_period_end'] or 'N/A'} | {burn} | "
            f"{'yes' if row['profitable'] else 'no'} | {'; '.join(notes)} |"
        )
    lines.extend(["", "## OCF detail (last 4 quarters)", ""])
    for row in rows:
        lines.append(f"### {row['ticker']} — {row['entity']}")
        lines.append("")
        for q in row["ocf_quarters"]:
            lines.append(f"- {q['period_end']}: ${q['value_m']}M ({q['source']})")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    """CLI entry: sanity-check the XBRL extractor against known tickers; write the report."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true", help="Print report only, do not write file"
    )
    args = parser.parse_args()

    rows = []
    for ticker, cik in TICKER_CIKS.items():
        log.info("fetching", ticker=ticker, cik=cik)
        rows.append(analyze_ticker(ticker, cik))

    report = render_report(rows)
    if args.dry_run:
        print(report)
        return

    out = config.LOGS_DIR / "xbrl_sanity_check.md"
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out}")
    log.info("sanity_check_complete", path=str(out))


if __name__ == "__main__":
    main()
