"""ClinicalTrials.gov API v2 client with caching and rate limiting."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from rapidfuzz import fuzz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import config
from logger import setup_logger

log = setup_logger("ctgov_client")

READOUT_BUFFER_DAYS = 90
_MIN_INTERVAL = 60.0 / config.CTGOV_MAX_REQUESTS_PER_MIN
_last_request_at = 0.0


class CTGovError(Exception):
    """Raised when ClinicalTrials.gov returns an HTTP error or rate-limits the client."""

    pass


def _rate_limit() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def _cache_path(key: str) -> Path:
    safe = hashlib.sha256(key.encode()).hexdigest()[:16]
    return config.CTGOV_CACHE_DIR / f"{safe}.json"


def _read_cache(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(payload.get("cached_at", ""))
        if datetime.now(timezone.utc) - cached_at > timedelta(days=config.CTGOV_CACHE_TTL_DAYS):
            return None
        return payload.get("data")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log.warning("cache_read_failed", key=key, error=str(exc))
        return None


def _write_cache(key: str, data: Any) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cached_at": datetime.now(timezone.utc).isoformat(), "data": data}
    path.write_text(json.dumps(payload), encoding="utf-8")


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((requests.RequestException, CTGovError)),
    reraise=True,
)
def _get(url: str, params: dict | None = None) -> dict:
    _rate_limit()
    resp = requests.get(url, params=params, timeout=60)
    if resp.status_code == 429:
        log.warning("ctgov_rate_limited", url=url)
        raise CTGovError("Rate limited by ClinicalTrials.gov")
    if resp.status_code >= 400:
        log.error("ctgov_http_error", status=resp.status_code, url=url, body=resp.text[:500])
        raise CTGovError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _parse_date_struct(struct: dict | None) -> str | None:
    if not struct:
        return None
    return struct.get("date")


def parse_study_record(study: dict) -> dict:
    """Parse a CT.gov v2 study JSON into a flat record."""
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    design = ps.get("designModule", {})
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {})
    conditions = ps.get("conditionsModule", {})
    arms = ps.get("armsInterventionsModule", {})
    outcomes = ps.get("outcomesModule", {})
    desc = ps.get("descriptionModule", {})

    phases = design.get("phases", [])
    phase = "/".join(phases) if phases else None

    interventions = arms.get("interventions", [])
    intervention_names = [i.get("name", "") for i in interventions if i.get("name")]
    lead_intervention = intervention_names[0] if intervention_names else None

    primary_outcomes = outcomes.get("primaryOutcomes", [])
    primary_endpoint = None
    if primary_outcomes:
        primary_endpoint = primary_outcomes[0].get("measure")

    design_info = design.get("designInfo", {})
    allocation = design_info.get("allocation", "")
    is_randomized = allocation.upper() == "RANDOMIZED" if allocation else None
    has_control = any(
        arm.get("type", "").upper() in ("PLACEBO_COMPARATOR", "ACTIVE_COMPARATOR", "SHAM_COMPARATOR")
        for arm in arms.get("armGroups", [])
    )

    lead_sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "")
    collaborators = [c.get("name", "") for c in sponsor_mod.get("collaborators", [])]

    locations = []
    for loc in study.get("protocolSection", {}).get("contactsLocationsModule", {}).get("locations", []):
        locations.append(
            {
                "facility": loc.get("facility"),
                "city": loc.get("city"),
                "state": loc.get("state"),
                "country": loc.get("country"),
            }
        )

    brief_summary = desc.get("briefSummary", "") or ""
    detailed = desc.get("detailedDescription", "") or ""

    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle") or ident.get("officialTitle"),
        "phase": phase,
        "status": status.get("overallStatus"),
        "sponsor": lead_sponsor,
        "collaborators": collaborators,
        "lead_intervention": lead_intervention,
        "interventions": intervention_names,
        "conditions": conditions.get("conditions", []),
        "primary_endpoint": primary_endpoint,
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "start_date": _parse_date_struct(status.get("startDateStruct")),
        "primary_completion_date": _parse_date_struct(status.get("primaryCompletionDateStruct")),
        "completion_date": _parse_date_struct(status.get("completionDateStruct")),
        "is_randomized": is_randomized,
        "has_control_arm": has_control,
        "locations": locations,
        "brief_summary": brief_summary,
        "detailed_description": detailed,
        "raw_json": study,
    }


def _normalize_org(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _sponsor_matches(
    study: dict,
    reference_names: list[str],
    ticker: str | None,
    threshold: int,
) -> bool:
    ps = study.get("protocolSection", {})
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {})
    sponsor_names = [sponsor_mod.get("leadSponsor", {}).get("name", "")]
    sponsor_names.extend(c.get("name", "") for c in sponsor_mod.get("collaborators", []))
    sponsor_names = [n for n in sponsor_names if n]

    refs = [r for r in reference_names if r]
    for name in sponsor_names:
        name_lower = name.lower()
        name_norm = _normalize_org(name)
        for ref in refs:
            ref_lower = ref.lower()
            ref_norm = _normalize_org(ref)
            if name_lower == ref_lower:
                return True
            if ref_norm and name_norm and (ref_norm in name_norm or name_norm in ref_norm):
                if min(len(ref_norm), len(name_norm)) >= 4:
                    return True
            if fuzz.token_set_ratio(name_lower, ref_lower) >= threshold:
                return True
        if ticker and ticker.lower() in name_lower:
            return True
    return False


def _sponsor_query_variants(company_name: str) -> list[str]:
    variants = [company_name]
    for suffix in (
        " Therapeutics",
        " Pharmaceuticals",
        " Pharmaceutical",
        " Pharma",
        " Biopharmaceuticals",
        " Biopharma",
        " Inc.",
        " Inc",
        " SA",
        " Corporation",
        " Corp.",
        " Ltd.",
        " Limited",
    ):
        if company_name.endswith(suffix):
            variants.append(company_name[: -len(suffix)].strip())
    return list(dict.fromkeys(v for v in variants if v))


def _fetch_sponsor_page(query: str, page_token: str | None = None) -> dict:
    params: dict[str, Any] = {"query.spons": query, "pageSize": 100, "format": "json"}
    if page_token:
        params["pageToken"] = page_token
    return _get(config.CLINICALTRIALS_API_BASE, params)


def _search_queries(
    company_name: str,
    sponsor_aliases: list[str] | None,
) -> list[tuple[str, str]]:
    """Return ordered (query_label, search_string) pairs."""
    ordered: list[tuple[str, str]] = []
    for variant in _sponsor_query_variants(company_name):
        ordered.append(("name", variant))
    for alias in sponsor_aliases or []:
        alias = alias.strip()
        if not alias:
            continue
        for variant in _sponsor_query_variants(alias):
            ordered.append((f"alias:{alias}", variant))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for label, q in ordered:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique.append((label, q))
    return unique


def search_by_sponsor(
    company_name: str,
    ticker: str | None = None,
    sponsor_aliases: list[str] | None = None,
) -> tuple[list[dict], str]:
    """
    Search CT.gov for studies by company name, then aliases, with rapidfuzz on results.
    Returns (studies, match_strategy).
    """
    alias_key = "|".join(sponsor_aliases or [])
    cache_key = f"sponsor:v2:{company_name}:{ticker or ''}:{alias_key}"
    cached = _read_cache(cache_key)
    if cached is not None:
        log.info("cache_hit", key=cache_key)
        if isinstance(cached, list):
            return cached, "cache"
        return cached.get("studies", []), cached.get("match_strategy", "cache")

    seen_ncts: set[str] = set()
    all_studies: list[dict] = []
    match_strategy = "none"
    count_before = 0
    reference_names = [company_name] + list(sponsor_aliases or [])

    for label, query in _search_queries(company_name, sponsor_aliases):
        page_token = None
        while True:
            data = _fetch_sponsor_page(query, page_token)
            for study in data.get("studies", []):
                if _sponsor_matches(study, reference_names, ticker, config.SPONSOR_FUZZY_THRESHOLD):
                    parsed = parse_study_record(study)
                    nct = parsed.get("nct_id")
                    if nct and nct not in seen_ncts:
                        seen_ncts.add(nct)
                        all_studies.append(parsed)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if len(all_studies) > count_before and match_strategy == "none":
            match_strategy = label
            count_before = len(all_studies)

    payload = {"studies": all_studies, "match_strategy": match_strategy}
    _write_cache(cache_key, payload)
    log.info(
        "sponsor_search_complete",
        sponsor=company_name,
        ticker=ticker,
        trials=len(all_studies),
        match_strategy=match_strategy,
    )
    return all_studies, match_strategy


def get_study(nct_id: str) -> dict:
    """Fetch full study record by NCT ID."""
    cache_key = f"nct:{nct_id}"
    nct_path = config.CTGOV_CACHE_DIR / f"{nct_id}.json"
    if nct_path.exists():
        try:
            payload = json.loads(nct_path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(payload.get("cached_at", ""))
            if datetime.now(timezone.utc) - cached_at <= timedelta(days=config.CTGOV_CACHE_TTL_DAYS):
                return parse_study_record(payload.get("data", {}))
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    cached = _read_cache(cache_key)
    if cached is not None:
        return parse_study_record(cached)

    url = f"{config.CLINICALTRIALS_API_BASE}/{nct_id}"
    data = _get(url, {"format": "json"})
    _write_cache(cache_key, data)
    nct_path.write_text(
        json.dumps({"cached_at": datetime.now(timezone.utc).isoformat(), "data": data}),
        encoding="utf-8",
    )
    return parse_study_record(data)
