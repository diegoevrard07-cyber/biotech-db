# Data

This directory holds **seed data** (committed) and **runtime artifacts** (gitignored).
No large or restricted datasets are committed to this repository.

## Committed (small, curated)

| Path | Purpose |
|------|---------|
| `seeds/companies.csv` | Curated universe of ~131 US-listed clinical-stage oncology/CNS companies (ticker, sponsor aliases for ClinicalTrials.gov matching, primary indication). Hand-triaged; see `seeds/triage_log.md`. |
| `seeds/big_pharma.csv` | Large-cap sponsor names + aliases, used to classify trial sponsors (industry vs. academic). |
| `seeds/triage_log.md` | Provenance log: how zero-trial companies were re-aliased or removed. |
| `models/success_model.json` | Trained logistic-regression weights for trial-success prediction. **Research artifact only** — evaluation showed it does not beat the base-rate lookup, so it is not wired into the scorer. |

## Generated at runtime (gitignored, auto-created)

| Path | Contents |
|------|----------|
| `raw/` | CSV exports (action sheets, backtest trades) |
| `cache/` | API response caches (ClinicalTrials.gov, SEC) |
| `logs/` | Structured JSON logs per run |
| `backups/` | Reversible backups written by maintenance scripts |

## Where the real data comes from

The pipeline builds its database from public sources at runtime — nothing needs
to be downloaded manually:

1. **ClinicalTrials.gov API v2** — trials, catalysts, historical outcomes
   (`scripts/ingest_layer1.py`, `scripts/ingest_historical_trials.py`).
2. **SEC EDGAR** — filings, XBRL financials, Form 4 insider transactions
   (`scripts/run_layer4.py`, `scripts/ingest_insider.py`). Requires a descriptive
   `SEC_USER_AGENT` in `.env` (SEC fair-use policy).
3. **yfinance** — daily OHLCV prices, short interest, options-implied moves
   (`scripts/ingest_prices.py`, `scripts/ingest_positioning.py`).

A Postgres database (e.g. a free Supabase project) is required; see the
Quickstart in the root README. After `apply_schema.py` + `load_companies.py`,
the ingestion scripts populate everything else.
