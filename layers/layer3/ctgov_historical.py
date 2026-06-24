"""Historical CT.gov trial fetcher with caching."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import config
from logger import setup_logger

log = setup_logger("ctgov_historical")

CACHE_DIR = config.CACHE_DIR / "ctgov_historical"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
_MIN_INTERVAL = 60.0 / config.CTGOV_MAX_REQUESTS_PER_MIN
_last_request_at = 0.0


class CTGovHistoricalError(Exception):
    pass


def _rate_limit() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def _year_filter(start_year: int, end_year: int) -> str:
    return (
        "AREA[HasResults]true AND AREA[OverallStatus]Completed "
        "AND AREA[StudyType]Interventional "
        f"AND AREA[PrimaryCompletionDate]RANGE[{start_year}-01-01,{end_year}-12-31]"
    )


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((requests.RequestException, CTGovHistoricalError)),
    reraise=True,
)
def _get(params: dict) -> dict:
    _rate_limit()
    resp = requests.get(config.CLINICALTRIALS_API_BASE, params={**params, "format": "json"}, timeout=120)
    if resp.status_code == 429:
        raise CTGovHistoricalError("Rate limited")
    if resp.status_code >= 400:
        raise CTGovHistoricalError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def fetch_year_page(year: int, page_token: str | None = None, page_size: int = 100) -> dict:
    cache_key = f"v2_interventional:year:{year}:token:{page_token or 'start'}:size:{page_size}"
    cache_path = CACHE_DIR / f"{hashlib.sha256(cache_key.encode()).hexdigest()[:16]}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return cached.get("data", cached)

    params: dict[str, Any] = {
        "filter.advanced": _year_filter(year, year),
        "pageSize": page_size,
        "countTotal": "true",
    }
    if page_token:
        params["pageToken"] = page_token
    data = _get(params)
    cache_path.write_text(
        json.dumps({"cached_at": datetime.now(timezone.utc).isoformat(), "data": data}),
        encoding="utf-8",
    )
    return data


def iter_historical_studies(
    start_year: int,
    end_year: int,
    limit: int | None = None,
):
    """Yield study dicts for completed interventional trials with results."""
    count = 0
    for year in range(start_year, end_year + 1):
        page_token = None
        while True:
            data = fetch_year_page(year, page_token)
            studies = data.get("studies") or []
            for study in studies:
                yield study
                count += 1
                if limit and count >= limit:
                    return
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            log.info("historical_page", year=year, fetched=count)
