"""
Fetch real 8-K HTML fixtures from SEC EDGAR for parser test corpus.

Usage:
  python scripts/fetch_eight_k_fixtures.py [--category pdufa_assigned] [--limit 4]

Requires SEC_USER_AGENT in .env (or uses check bypass for fixture generation only).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import certifi
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from logger import setup_logger

log = setup_logger("fetch_eight_k_fixtures")

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eight_k"

SEARCH_QUERIES: dict[str, list[str]] = {
    "pdufa_assigned": [
        '"PDUFA date"',
        '"Prescription Drug User Fee Act" date',
        "PDUFA action date",
    ],
    "crl": ['"complete response letter"', '"received a CRL"'],
    "approval": [
        "FDA approved ANNOV",
        "announced FDA approval of",
        "received FDA approval for",
        "FDA has approved KALYDECO",
    ],
    "adcom_scheduled": ['"Advisory Committee"', '"advisory committee meeting"'],
    "offering": ['"public offering"', '"underwritten offering"'],
    "license_deal": ['"license agreement"', '"collaboration agreement"'],
    "negative": [
        '"PDUFA date" may be extended',
        "partner FDA approved",
        "previously received a complete response letter",
        "incorporated by reference" "shelf registration",
        "expanded access program FDA",
        "clinical hold was lifted",
    ],
}

NEGATIVE_META = [
    {
        "prefix": "competitor_pdufa",
        "forbidden": "pdufa_assigned",
        "notes": "Forward-looking PDUFA risk, not assignment",
    },
    {
        "prefix": "partner_approval",
        "forbidden": "approval",
        "notes": "Partner drug approved; filer is not the asset owner",
    },
    {
        "prefix": "historical_crl",
        "forbidden": "crl",
        "notes": "Past CRL referenced in narrative, not new receipt",
    },
    {
        "prefix": "routine_underwriting",
        "forbidden": "offering",
        "notes": "Shelf/incorporation by reference, not active offering",
    },
    {
        "prefix": "expanded_access",
        "forbidden": "approval",
        "notes": "Expanded access mention, not approval milestone",
    },
    {
        "prefix": "lifted_clinical_hold",
        "forbidden": "crl",
        "notes": "Clinical hold lifted — opposite of CRL",
    },
]


def _headers() -> dict[str, str]:
    ua = config.SEC_USER_AGENT
    if not ua or ua == config.SEC_USER_AGENT_PLACEHOLDER:
        ua = "biotech-db-fixture-fetcher fixtures@local.dev"
    return {"User-Agent": ua, "Accept": "application/json, text/html"}


def _sec_get(url: str, *, as_json: bool = True) -> requests.Response:
    time.sleep(0.12)
    resp = requests.get(url, headers=_headers(), verify=certifi.where(), timeout=60)
    resp.raise_for_status()
    return resp


def search_filings(query: str, *, size: int = 20) -> list[dict]:
    """Full-text search EDGAR for 8-K filings matching the query; return hit sources."""
    params = {
        "q": query,
        "forms": "8-K",
        "from": 0,
        "size": size,
        "dateRange": "custom",
        "startdt": "2020-01-01",
        "enddt": "2026-12-31",
    }
    resp = _sec_get(
        "https://efts.sec.gov/LATEST/search-index?"
        + "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items()),
        as_json=True,
    )
    hits = resp.json().get("hits", {}).get("hits", [])
    return [h.get("_source", {}) for h in hits]


def _parse_ticker(display_name: str) -> str | None:
    m = re.search(r"\(([A-Z]{1,5})\)", display_name or "")
    return m.group(1) if m else None


def resolve_primary_doc(cik: str, adsh: str) -> tuple[str, str] | None:
    """Return (primary_filename, filing_date) for accession."""
    cik_int = str(int(cik))
    acc_nodash = adsh.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
    try:
        resp = _sec_get(index_url)
        data = resp.json()
    except Exception:
        return None

    items = data.get("directory", {}).get("item", [])
    if isinstance(items, dict):
        items = [items]

    filed = data.get("filingDate") or data.get("filedDate") or ""

    def _candidates() -> list[str]:
        names: list[str] = []
        for item in items:
            name = item.get("name", "")
            lower = name.lower()
            if not name.endswith((".htm", ".html")):
                continue
            if "index" in lower or lower.endswith(".txt"):
                continue
            names.append(name)
        return names

    names = _candidates()
    primary = None
    for name in names:
        if re.search(r"8k\.htm", name, re.I):
            primary = name
            break
    if not primary:
        for name in names:
            if "ex-" not in name.lower() and "ex99" not in name.lower():
                primary = name
                break
    if not primary and names:
        primary = names[0]
    if not primary:
        return None
    return primary, filed


def download_primary_html(cik: str, adsh: str, filename: str) -> str:
    """Download the primary document HTML for one accession from EDGAR."""
    cik_int = str(int(cik))
    acc_nodash = adsh.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{filename}"
    resp = _sec_get(url, as_json=False)
    return resp.text


CATEGORY_ANCHORS: dict[str, list[str]] = {
    "pdufa_assigned": ["pdufa", "prescription drug user fee act"],
    "crl": ["complete response letter", " crl "],
    "approval": ["fda approved", "received approval", "marketing approval"],
    "adcom_scheduled": ["advisory committee", "adcom"],
    "offering": ["public offering", "underwritten offering", "offering of"],
    "license_deal": ["license agreement", "collaboration agreement", "licensing agreement"],
    "negative": [
        "pdufa",
        "complete response",
        "clinical hold",
        "expanded access",
        "incorporated by reference",
    ],
}


def smart_truncate_html(html: str, category: str, max_bytes: int = 5120) -> str:
    """Keep cover page + disclosure paragraph containing category anchor."""
    if len(html.encode("utf-8")) <= max_bytes:
        return html

    lower = html.lower()
    anchor_pos = len(html) // 3  # default: after cover
    for kw in CATEGORY_ANCHORS.get(category, []):
        pos = lower.find(kw)
        if pos >= 0:
            anchor_pos = pos
            break

    # Cover: first ~1800 bytes; disclosure window around anchor
    cover_end = min(1800, anchor_pos)
    window_start = max(0, anchor_pos - 600)
    window_end = min(len(html), anchor_pos + 2800)
    excerpt = html[:cover_end] + "\n<!-- ... truncated ... -->\n" + html[window_start:window_end]
    if len(excerpt.encode("utf-8")) > max_bytes:
        enc = excerpt.encode("utf-8")[:max_bytes]
        return enc.decode("utf-8", errors="ignore")
    return excerpt


def fetch_negative(limit_per_query: int = 1) -> list[dict]:
    """Fetch near-miss 8-Ks that mention trigger words but are NOT the event (parser
    must reject these)."""
    results: list[dict] = []
    seen: set[str] = set()
    for meta, query in zip(NEGATIVE_META, SEARCH_QUERIES["negative"]):
        try:
            raw_hits = search_filings(query, size=20)
        except Exception as exc:
            log.warning("negative_search_failed", query=query, error=str(exc))
            continue
        for hit in raw_hits:
            adsh = hit.get("adsh")
            ciks = hit.get("ciks") or []
            if not adsh or not ciks or adsh in seen:
                continue
            if hit.get("file_type", "").upper().startswith("EX-"):
                continue
            seen.add(adsh)
            cik = ciks[0]
            resolved = resolve_primary_doc(cik, adsh)
            if not resolved:
                continue
            filename, filed = resolved
            try:
                html = download_primary_html(cik, adsh, filename)
            except Exception:
                continue
            display = (hit.get("display_names") or [""])[0]
            ticker = _parse_ticker(display) or "UNK"
            acc_clean = adsh.replace("-", "")
            results.append(
                {
                    "category": "negative",
                    "negative_prefix": meta["prefix"],
                    "forbidden_event_type": meta["forbidden"],
                    "notes": meta["notes"],
                    "ticker": ticker,
                    "accession": adsh,
                    "filed_date": hit.get("file_date") or filed,
                    "items": hit.get("items") or [],
                    "primary_doc_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{filename}",
                    "html": smart_truncate_html(html, "negative"),
                    "display_name": display,
                }
            )
            break
    return results


def fetch_category(category: str, limit: int = 4) -> list[dict]:
    """Fetch up to `limit` real 8-K fixture records for one event category."""
    queries = SEARCH_QUERIES.get(category, [])
    seen_adsh: set[str] = set()
    results: list[dict] = []

    for query in queries:
        if len(results) >= limit:
            break
        try:
            hits = search_filings(query, size=30)
        except Exception as exc:
            log.warning("search_failed", category=category, query=query, error=str(exc))
            continue

        for hit in hits:
            if len(results) >= limit:
                break
            adsh = hit.get("adsh")
            ciks = hit.get("ciks") or []
            if not adsh or not ciks or adsh in seen_adsh:
                continue
            # Prefer primary 8-K hits, skip exhibit-only if possible
            if hit.get("file_type", "").upper().startswith("EX-"):
                continue
            seen_adsh.add(adsh)
            cik = ciks[0]
            resolved = resolve_primary_doc(cik, adsh)
            if not resolved:
                log.warning("no_primary_doc", adsh=adsh)
                continue
            filename, filed = resolved
            try:
                html = download_primary_html(cik, adsh, filename)
            except Exception as exc:
                log.warning("download_failed", adsh=adsh, error=str(exc))
                continue

            display = (hit.get("display_names") or [""])[0]
            ticker = _parse_ticker(display) or "UNK"
            items = hit.get("items") or []
            acc_clean = adsh.replace("-", "")
            primary_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{filename}"
            )

            results.append(
                {
                    "category": category,
                    "ticker": ticker,
                    "accession": adsh,
                    "filed_date": hit.get("file_date") or filed,
                    "items": items,
                    "primary_doc_url": primary_url,
                    "html": smart_truncate_html(html, category),
                    "display_name": display,
                }
            )
            log.info("fetched", category=category, ticker=ticker, accession=adsh)

    return results


def save_fixture(record: dict, *, negative: bool = False) -> Path:
    """Write the fixture .html plus a starter .expected.json under tests/fixtures/eight_k."""
    category = record["category"]
    acc_slug = record["accession"].replace("-", "")
    if negative:
        subdir = FIXTURES_ROOT / "negative"
        prefix = record.get("negative_prefix", "negative")
        base = f"{prefix}_{record['ticker']}_{acc_slug}"
    else:
        subdir = FIXTURES_ROOT / category
        base = f"{record['ticker']}_{acc_slug}"
    subdir.mkdir(parents=True, exist_ok=True)

    html_path = subdir / f"{base}.html"
    html_path.write_text(record["html"], encoding="utf-8")

    expected = {
        "should_match": not negative,
        "event_type": record.get("forbidden_event_type", category) if negative else category,
        "event_date": record.get("event_date"),
        "drug_name": record.get("drug_name"),
        "indication": record.get("indication"),
        "confidence": record.get("confidence", "high" if not negative else None),
        "items": record.get("items") or [],
        "filing_metadata": {
            "ticker": record["ticker"],
            "accession": record["accession"],
            "filed_date": record.get("filed_date"),
            "primary_doc_url": record.get("primary_doc_url"),
        },
        "notes": record.get("notes", ""),
    }
    if negative:
        expected["confidence"] = None
        expected.pop("event_date", None)

    json_path = subdir / f"{base}.expected.json"
    json_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return html_path


def main() -> None:
    """CLI entry: fetch 8-K fixture corpus from EDGAR and write tests/fixtures/eight_k."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", action="append", help="Category to fetch")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    categories = args.category or list(SEARCH_QUERIES.keys())
    inventory: list[dict] = []

    for cat in categories:
        if cat == "negative":
            records = fetch_negative()
        else:
            records = fetch_category(cat, limit=args.limit)
        print(f"{cat}: fetched {len(records)} filings")
        for rec in records:
            inventory.append({"category": cat, **{k: v for k, v in rec.items() if k != "html"}})
            if not args.list_only and rec.get("html"):
                path = save_fixture(rec, negative=(cat == "negative"))
                print(f"  saved {path.name}")

    inv_path = FIXTURES_ROOT / "inventory.json"
    inv_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"Inventory written to {inv_path}")


if __name__ == "__main__":
    main()
