# Data & database reference

## Data sources

All data is pulled from **public sources at runtime**; no restricted datasets are
committed (see `data/README.md` for the committed seeds):

- **ClinicalTrials.gov API v2**: trial registry, catalyst dates, historical outcomes
- **SEC EDGAR**: 8-K filings, XBRL company facts (cash/burn), Form 4 insider trades
  (requires a descriptive `SEC_USER_AGENT`; free, no key)
- **yfinance**: daily OHLCV, short interest, options-implied move (unofficial API;
  the client is rate-limited and timeout-wrapped)

## Data directories

| Path | Purpose | Gitignored |
|------|---------|------------|
| `data/seeds/` | Curated CSV seed data | No |
| `data/models/` | Research artifacts (unused success model) | No |
| `data/raw/` | Unmatched sponsors, raw exports | Yes |
| `data/cache/` | 24h API response cache | Yes |
| `data/logs/` | JSON structured logs | Yes |
| `data/backups/` | Reversible maintenance-script backups | Yes |

## Database

Schema defined in `schema.sql` (22 tables). Applied idempotently via
`python scripts/apply_schema.py` using `CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS`. Run it after pulling to create new tables.

Connection via `DATABASE_URL` in `.env` (Supabase Postgres pooler, or local Postgres
via `docker compose up -d`).

Portfolio data (syncs across machines):

| Table | Purpose |
|-------|---------|
| `portfolio_account` | Cash + starting capital (singleton) |
| `portfolio_holdings` | Every open/closed trade (PAPER + manual) |
| `portfolio_performance` | Daily equity snapshots + XBI benchmark |

Import local CSV history once with `python scripts/sync_performance_to_db.py`.

## Python version notes

The project is developed and tested on **Python 3.12** (CI) and also runs on
**Python 3.14**, with version-bumped dependencies where upstream wheels were
unavailable at the time:

| Package | Pinned | Original pin | Reason |
|---------|--------|--------------|--------|
| pandas | 2.3.3 | 2.2.3 | cp314 wheel + Streamlit requires pandas<3 |
| pydantic | 2.13.4 | 2.10.3 | pydantic-core needs prebuilt wheel |
| lxml | 6.1.1 | 5.3.0 | No cp314 wheel (now optional, not imported) |
| rapidfuzz | 3.14.5 | 3.10.1 | No cp314 wheel |

Python 3.12 is the recommended baseline if a future library lacks 3.14 wheels.
