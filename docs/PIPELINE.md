# Pipeline reference

The full script-by-script run order. Most readers want the
[README quickstart](../README.md#quickstart) instead; this is the complete version.

## Prerequisites

Copy `.env.example` → `.env` and fill in:

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | yes | Postgres connection string (Supabase pooler or local) |
| `SEC_USER_AGENT` | yes, before Layer 4 | SEC EDGAR requires a descriptive UA: `"Firstname Lastname email@domain.com"`. `fetch_filings.py` calls `check_sec_user_agent()` and fails fast if unset or still the placeholder. |
| `POE_API_KEY` | no | Only for the Layer-2 LLM science council, which is scaffolded but **not wired** into the active pipeline. Omit it. |

Setup:

```bash
pip install -r requirements.txt
python scripts/apply_schema.py        # idempotent schema (schema.sql)
python scripts/check_db_connection.py # smoke-test credentials/network
```

## Run order

```bash
python scripts/load_companies.py
python scripts/ingest_layer1.py --limit 5   # smoke test
python scripts/ingest_layer1.py             # full ingest
python scripts/verify_layer1.py
python scripts/run_layer1.py
python scripts/run_layer3.py
python scripts/run_layer4.py          # SEC ingest (requires SEC_USER_AGENT)
# --- expanded universe (oncology/CNS, GBM flagship) ---
python scripts/classify_universe.py   # tag is_gbm_focused / indication_category / in_universe
python scripts/ingest_prices.py --lookback-days 400   # yfinance OHLCV + XBI benchmark
python scripts/ingest_positioning.py  # short interest, implied move, IV, run-up
python scripts/ingest_insider.py      # SEC Form 4 (requires SEC_USER_AGENT)
python scripts/resolve_outcomes.py    # label past catalysts from price reaction
python scripts/run_composite.py       # composite + decision layer
python scripts/calibrate.py           # Brier / reliability vs outcomes
python scripts/backtest.py --csv data/raw/backtest_trades.csv
python scripts/verify_signals.py      # signal coverage / freshness
# Or all at once (fail-soft orchestrator):
python scripts/refresh_all.py
```

All scripts support `--dry-run` (no DB writes). Ingestion scripts also support
`--limit` and `--ticker` for smoke tests. `refresh_all.py` continues past
non-critical ingest failures and exits non-zero if any stage fails, so a scheduler
can surface problems. `load_companies.py` is a one-time seed per database, not a
refresh stage.

## Verification scripts

| Script | Checks |
|--------|--------|
| `scripts/verify.py` | tables exist, row counts, orphan FKs, sample rows |
| `scripts/verify_layer1.py` | catalyst extraction coverage |
| `scripts/verify_layer3.py` | base-rate slices and labels |
| `scripts/verify_layer4.py` | SEC filings/financials integrity |
| `scripts/verify_signals.py` | signal coverage / freshness |

## Architecture (layer map)

```
Layer 1 (Catalysts)  → companies, trials, catalysts
Layer 2 (Science)    → council_judgments, trial_scores  [Poe API; scaffolded, not wired]
Layer 3 (Base Rates) → historical_trials, base_rates
Layer 4 (Financials) → sec_filings, financials          [SEC EDGAR]
Market data          → price_history, positioning        [yfinance]
Insider              → insider_transactions              [SEC Form 4]
Outcomes             → catalyst_outcomes (labeled returns)
Composite + Decision → edge_scores (trade_type, suggested_weight, edge_gap)
Validation           → calibration_runs + backtest
```
