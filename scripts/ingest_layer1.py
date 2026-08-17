"""
TODO LAYER 4: PDUFA and advisory_committee catalysts may still include CT.gov keyword
stubs until SEC 8-K ingest + reconciliation runs (scripts/run_layer4.py).
After Layer 4, pdufa+adcom counts should jump from ~9 to 30-50.
If they don't, Layer 4 has a bug.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from tqdm import tqdm

import config
from db import get_connection
from layers.layer1.catalyst_extractor import extract_catalysts, merge_funnel_stats, new_funnel_stats
from layers.layer1.ctgov_client import CTGovError, search_by_sponsor
from layers.layer1.dedupe import dedupe_catalysts
from layers.layer1.funnel_report import print_funnel
from logger import setup_logger

log = setup_logger("ingest_layer1")


def _parse_db_date(value: str | None):
    """Normalize CT.gov date strings to ISO dates for Postgres DATE columns."""
    if not value:
        return None
    v = value.strip()
    if len(v) == 4:
        return f"{v}-01-01"
    if len(v) == 7:
        return f"{v}-01"
    return v[:10]


def _parse_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in raw.split("|") if a.strip()]


def _upsert_trial(conn, company_id: int, trial: dict) -> int:
    params = {
        "nct_id": trial["nct_id"],
        "company_id": company_id,
        "title": trial.get("title"),
        "phase": trial.get("phase"),
        "status": trial.get("status"),
        "indication": ", ".join(trial.get("conditions") or []),
        "intervention": trial.get("lead_intervention"),
        "primary_endpoint": trial.get("primary_endpoint"),
        "enrollment": trial.get("enrollment"),
        "start_date": _parse_db_date(trial.get("start_date")),
        "primary_completion_date": _parse_db_date(trial.get("primary_completion_date")),
        "estimated_readout_date": None,
        "is_randomized": trial.get("is_randomized"),
        "has_control_arm": trial.get("has_control_arm"),
        "raw_json": json.dumps(trial.get("raw_json") or {}),
    }
    row = conn.execute(
        text(
            """
            INSERT INTO trials (
                nct_id, company_id, title, phase, status, indication, intervention,
                primary_endpoint, enrollment, start_date, primary_completion_date,
                estimated_readout_date, is_randomized, has_control_arm, raw_json, fetched_at
            ) VALUES (
                :nct_id, :company_id, :title, :phase, :status, :indication, :intervention,
                :primary_endpoint, :enrollment, :start_date, :primary_completion_date,
                :estimated_readout_date, :is_randomized, :has_control_arm, CAST(:raw_json AS jsonb), NOW()
            )
            ON CONFLICT (nct_id) DO UPDATE SET
                company_id = EXCLUDED.company_id,
                title = EXCLUDED.title,
                phase = EXCLUDED.phase,
                status = EXCLUDED.status,
                indication = EXCLUDED.indication,
                intervention = EXCLUDED.intervention,
                primary_endpoint = EXCLUDED.primary_endpoint,
                enrollment = EXCLUDED.enrollment,
                start_date = EXCLUDED.start_date,
                primary_completion_date = EXCLUDED.primary_completion_date,
                is_randomized = EXCLUDED.is_randomized,
                has_control_arm = EXCLUDED.has_control_arm,
                raw_json = EXCLUDED.raw_json,
                fetched_at = NOW()
            RETURNING id
            """
        ),
        params,
    ).scalar()
    return int(row)


def _insert_catalysts(conn, company_id: int, catalysts: list[dict]) -> int:
    # UPSERT (not DELETE+INSERT): a ctgov catalyst is keyed by (trial_id, catalyst_type)
    # via the partial unique index uq_catalysts_ctgov. This keeps catalyst ids STABLE so
    # edge_scores / portfolio_holdings / catalyst_outcomes that reference them survive a
    # re-ingest (the old delete-replace hit a FK violation once those tables were populated).
    # Stale catalysts (trials no longer returned) are left in place; they're harmless and
    # filtered downstream by date/status.
    count = 0
    for cat in catalysts:
        conn.execute(
            text(
                """
                INSERT INTO catalysts (
                    company_id, trial_id, catalyst_type, expected_date, date_confidence,
                    description, source, source_url, raw_data, requires_manual_verification
                ) VALUES (
                    :company_id, :trial_id, :catalyst_type, :expected_date, :date_confidence,
                    :description, :source, :source_url, CAST(:raw_data AS jsonb), :requires_manual_verification
                )
                ON CONFLICT (trial_id, catalyst_type) WHERE source = 'ctgov_v2' AND trial_id IS NOT NULL
                DO UPDATE SET
                    company_id = EXCLUDED.company_id,
                    expected_date = EXCLUDED.expected_date,
                    date_confidence = EXCLUDED.date_confidence,
                    description = EXCLUDED.description,
                    source_url = EXCLUDED.source_url,
                    raw_data = EXCLUDED.raw_data,
                    requires_manual_verification = EXCLUDED.requires_manual_verification
                """
            ),
            {
                "company_id": cat["company_id"],
                "trial_id": cat.get("trial_id"),
                "catalyst_type": cat["catalyst_type"],
                "expected_date": cat["expected_date"],
                "date_confidence": cat["date_confidence"],
                "description": cat["description"],
                "source": cat["source"],
                "source_url": cat.get("source_url"),
                "raw_data": json.dumps(cat.get("raw_data") or {}),
                "requires_manual_verification": cat.get("requires_manual_verification", False),
            },
        )
        count += 1
    return count


def ingest(
    dry_run: bool = False,
    ticker: str | None = None,
    limit: int | None = None,
) -> dict:
    """Fetch CT.gov trials per company, extract + dedupe catalysts, upsert both tables."""
    summary = {
        "companies_processed": 0,
        "trials_ingested": 0,
        "catalysts_extracted": 0,
        "duplicates_merged": 0,
        "errors": [],
        "zero_trial_companies": [],
        "alias_fixes": [],
    }
    funnel = new_funnel_stats()

    with get_connection() as conn:
        query = """
            SELECT id, ticker, name, ctgov_sponsor_aliases
            FROM companies ORDER BY ticker
        """
        params: dict = {}
        if ticker:
            query = query.replace("ORDER BY", "WHERE ticker = :ticker ORDER BY")
            params["ticker"] = ticker.upper()
        if limit:
            query += f" LIMIT {int(limit)}"
        companies = conn.execute(text(query), params).mappings().all()

    if not companies:
        print("ERROR: no companies found — run load_companies.py first")
        sys.exit(1)

    for company in tqdm(companies, desc="Ingesting Layer 1"):
        cid = company["id"]
        name = company["name"]
        tick = company["ticker"]
        aliases = _parse_aliases(company.get("ctgov_sponsor_aliases"))
        summary["companies_processed"] += 1

        try:
            trials, match_strategy = search_by_sponsor(name, ticker=tick, sponsor_aliases=aliases)
        except CTGovError as exc:
            log.error("ctgov_failed", ticker=tick, error=str(exc))
            summary["errors"].append(f"{tick}: {exc}")
            continue
        except Exception as exc:
            log.error("fetch_failed", ticker=tick, error=str(exc))
            summary["errors"].append(f"{tick}: {exc}")
            continue

        if match_strategy.startswith("alias:"):
            summary["alias_fixes"].append(f"{tick}:{match_strategy}")
            log.info("alias_match_success", ticker=tick, strategy=match_strategy, trials=len(trials))

        if not trials:
            summary["zero_trial_companies"].append(tick)
            log.info("no_trials", ticker=tick, name=name, strategy=match_strategy)
            continue

        log.info("sponsor_match", ticker=tick, strategy=match_strategy, trials=len(trials))

        company_catalysts: list[dict] = []
        company_funnel = new_funnel_stats()

        if dry_run:
            summary["trials_ingested"] += len(trials)
            for trial in trials:
                cats, fstats = extract_catalysts(trial, cid, trial_id=None)
                company_catalysts.extend(cats)
                company_funnel = merge_funnel_stats(company_funnel, fstats)
        else:
            with get_connection() as conn:
                for trial in trials:
                    trial_id = _upsert_trial(conn, cid, trial)
                    summary["trials_ingested"] += 1
                    cats, fstats = extract_catalysts(trial, cid, trial_id)
                    company_catalysts.extend(cats)
                    company_funnel = merge_funnel_stats(company_funnel, fstats)

        deduped, merges = dedupe_catalysts(company_catalysts)
        company_funnel["dropped_dedupe_merge"] += merges
        company_funnel["final_upcoming"] = len(deduped)
        funnel = merge_funnel_stats(funnel, company_funnel)

        summary["duplicates_merged"] += merges
        summary["catalysts_extracted"] += len(deduped)

        if not dry_run:
            with get_connection() as conn:
                _insert_catalysts(conn, cid, deduped)

    funnel["final_upcoming"] = summary["catalysts_extracted"]

    print("\n=== Layer 1 Ingestion Summary ===")
    print(f"Companies processed: {summary['companies_processed']}")
    print(f"Trials ingested:     {summary['trials_ingested']}")
    print(f"Catalysts extracted: {summary['catalysts_extracted']}")
    print(f"Duplicates merged:   {summary['duplicates_merged']}")
    if summary["alias_fixes"]:
        print(f"Alias match wins ({len(summary['alias_fixes'])}): {summary['alias_fixes'][:10]}")
    if summary["zero_trial_companies"]:
        print(
            f"Zero-trial companies ({len(summary['zero_trial_companies'])}): "
            f"{', '.join(summary['zero_trial_companies'])}"
        )
    if summary["errors"]:
        print(f"Errors ({len(summary['errors'])}): {summary['errors'][:5]}")

    print_funnel(funnel)

    log.info("ingest_complete", funnel=funnel, **{k: v for k, v in summary.items() if k != "errors"})
    summary["funnel"] = funnel
    return summary


def main() -> None:
    """CLI entry: Layer 1 ingestion — CT.gov trials and upcoming catalysts into Postgres."""
    parser = argparse.ArgumentParser(description="Ingest Layer 1 trials and catalysts")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ticker", type=str, help="Process single ticker")
    parser.add_argument("--limit", type=int, help="Process first N companies")
    args = parser.parse_args()

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    started = datetime.now()
    try:
        summary = ingest(dry_run=args.dry_run, ticker=args.ticker, limit=args.limit)
    except Exception as exc:
        log.error("ingest_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)

    elapsed = (datetime.now() - started).total_seconds()
    print(f"Elapsed: {elapsed:.1f}s")

    if summary["errors"] and len(summary["errors"]) == summary["companies_processed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
