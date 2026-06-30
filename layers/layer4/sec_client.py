"""
Rate-limited SEC EDGAR HTTP client.

Layer 4 main ingest: submissions, companyfacts, and primary 8-K document fetch.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from functools import lru_cache
from pathlib import Path

import certifi
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import config

_MIN_INTERVAL = 1.0 / config.SEC_MAX_REQUESTS_PER_SEC
_last_request_at = 0.0

CATALYST_EVENT_TYPES = frozenset(
    {
        "pdufa_assigned",
        "pdufa_delayed",
        "adcom_scheduled",
        "approval",
        "crl",
        "offering",
        "license_deal",
    }
)


def _headers(*, accept: str = "application/json") -> dict[str, str]:
    config.check_sec_user_agent()
    return {"User-Agent": config.SEC_USER_AGENT, "Accept": accept}


def _throttle() -> None:
    global _last_request_at
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=15),
    retry=retry_if_exception_type((requests.RequestException, requests.HTTPError)),
    reraise=True,
)
def sec_get(url: str, *, as_json: bool = True) -> requests.Response:
    """GET an EDGAR URL with throttling, User-Agent header, and retry on failure."""
    _throttle()
    resp = requests.get(
        url,
        headers=_headers(accept="application/json" if as_json else "text/html,*/*"),
        verify=certifi.where(),
        timeout=90,
    )
    if resp.status_code == 404:
        return resp
    resp.raise_for_status()
    return resp


def format_cik(cik: str | int) -> str:
    """Normalize a CIK to the 10-digit zero-padded form EDGAR URLs require."""
    return str(int(cik)).zfill(10)


@lru_cache(maxsize=1)
def fetch_company_ticker_map() -> dict[str, str]:
    """Return ticker -> zero-padded CIK."""
    resp = sec_get(config.SEC_COMPANY_TICKERS_URL)
    resp.raise_for_status()
    data = resp.json()
    out: dict[str, str] = {}
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str") or entry.get("cik")
        if ticker and cik:
            out[ticker] = format_cik(cik)
    return out


def fetch_submissions(cik: str) -> dict:
    """Fetch a company's submissions JSON (filing history); {} when CIK is unknown."""
    url = config.SEC_SUBMISSIONS_URL.format(cik=format_cik(cik))
    resp = sec_get(url)
    if resp.status_code == 404:
        return {}
    return resp.json()


def fetch_companyfacts(cik: str) -> dict:
    """Fetch a company's XBRL companyfacts JSON (financials); {} when CIK is unknown."""
    url = config.SEC_XBRL_FACTS_URL.format(cik=format_cik(cik))
    resp = sec_get(url)
    if resp.status_code == 404:
        return {}
    return resp.json()


def resolve_primary_doc(cik: str, adsh: str) -> tuple[str, str] | None:
    """Return (primary_filename, filing_date) for accession."""
    cik_int = str(int(cik))
    acc_nodash = adsh.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
    try:
        resp = sec_get(index_url)
        if resp.status_code == 404:
            return None
        data = resp.json()
    except Exception:
        return None

    items = data.get("directory", {}).get("item", [])
    if isinstance(items, dict):
        items = [items]

    filed = data.get("filingDate") or data.get("filedDate") or ""

    names: list[str] = []
    for item in items:
        name = item.get("name", "")
        lower = name.lower()
        if not name.endswith((".htm", ".html")):
            continue
        if "index" in lower or lower.endswith(".txt"):
            continue
        names.append(name)

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


def download_filing_html(cik: str, adsh: str, filename: str) -> str:
    """Download a filing's primary document HTML for material-event parsing."""
    cik_int = str(int(cik))
    acc_nodash = adsh.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{filename}"
    resp = sec_get(url, as_json=False)
    resp.raise_for_status()
    return resp.text


def primary_doc_url(cik: str, adsh: str, filename: str) -> str:
    """Build the canonical EDGAR URL for a filing's primary document."""
    cik_int = str(int(cik))
    acc_nodash = adsh.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{filename}"


def iter_recent_filings(
    submissions: dict,
    *,
    forms: frozenset[str] | None = None,
    since: date | None = None,
) -> list[dict]:
    """Yield recent filing metadata from submissions JSON (newest first)."""
    forms = forms or frozenset({"8-K"})
    recent = submissions.get("filings", {}).get("recent", {})
    accs = recent.get("accessionNumber") or []
    form_list = recent.get("form") or []
    dates = recent.get("filingDate") or []
    primary_docs = recent.get("primaryDocument") or []

    results: list[dict] = []
    for i, form in enumerate(form_list):
        if form not in forms:
            continue
        adsh = accs[i] if i < len(accs) else None
        if not adsh:
            continue
        filed_str = dates[i] if i < len(dates) else None
        filed = date.fromisoformat(filed_str) if filed_str else None
        if since and filed and filed < since:
            continue
        results.append(
            {
                "accession_number": adsh,
                "form": form,
                "filing_date": filed_str,
                "primary_document": primary_docs[i] if i < len(primary_docs) else None,
            }
        )
    return results


def cache_path(name: str) -> Path:
    """Return a path under the SEC cache directory, creating it if needed."""
    path = config.CACHE_DIR / "sec" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_json_cache(path: Path, *, max_age_hours: int | None = None) -> dict | None:
    """Load a cached JSON file; None when missing or older than max_age_hours."""
    if not path.exists():
        return None
    if max_age_hours is not None:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if age_h > max_age_hours:
            return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_cache(path: Path, data: dict) -> None:
    """Write a dict to a JSON cache file (e.g. submissions or companyfacts)."""
    path.write_text(json.dumps(data), encoding="utf-8")
