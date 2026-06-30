# GBM / Onc-CNS Edge Engine

Multi-layer biotech investment research pipeline. Originally GBM-only; broadened
(Rung 2) to small-cap oncology/CNS with GBM kept as a flagged flagship vertical.
Combines clinical catalysts, historical base rates, SEC financials + insider
activity, market price/positioning, labeled outcomes, and a decision layer that
emits an objective trade type and position size. Decision support only — a human
verifies data and places trades (no broker integration).

## Architecture

```
Layer 1 (Catalysts)  → companies, trials, catalysts
Layer 2 (Science)    → council_judgments, trial_scores  [Poe API]
Layer 3 (Base Rates) → historical_trials, base_rates
Layer 4 (Financials) → sec_filings, financials          [SEC EDGAR]
Market data          → price_history, positioning        [yfinance]
Insider              → insider_transactions              [SEC Form 4]
Outcomes             → catalyst_outcomes (labeled returns)
Composite + Decision → edge_scores (trade_type, suggested_weight, edge_gap)
Validation           → calibration_runs + backtest
```

## Rung 2 decision engine

The composite scorer stays simple and base-rate-anchored (few factors, near-equal
weights — the lesson from *Noise*), then a decision layer adds:

- `trade_type`: `buy_the_rumor` (own the pre-catalyst run-up, exit before the
  print), `fade` (short/avoid hyped low-base-rate names with dilution risk),
  `hold_through` (take the binary when odds justify), or `avoid`.
- `expected_move` (model) vs `implied_move` (options market) → `edge_gap`.
- `financing_tilt` (dilution risk) and `insider_tilt` (open-market buying).
- `suggested_weight`: Kelly-fractional, capped at `MAX_SINGLE_NAME_WEIGHT`.

Trust nothing until `scripts/calibrate.py` (Brier/reliability) and
`scripts/backtest.py` (walk-forward) show a positive edge on resolved outcomes.

## Setup

1. Python 3.14+ with virtualenv recommended
2. Copy `.env.example` → `.env` and fill in:
   - `DATABASE_URL` — Supabase Postgres connection string
   - `POE_API_KEY` — Poe API key for Layer 2 council
   - `SEC_USER_AGENT` — Your name + email (SEC requirement)
3. Install dependencies: `pip install -r requirements.txt`
4. Apply schema: `python apply_schema.py`
5. Verify: `python verify.py`

## Run Order

```bash
python scripts/load_companies.py
python scripts/ingest_layer1.py --limit 5   # smoke test
python scripts/ingest_layer1.py             # full ingest
python scripts/verify_layer1.py
python scripts/run_layer1.py
python scripts/run_layer2.py --limit 5   # smoke test first
python scripts/run_layer3.py
python scripts/run_layer4.py          # SEC ingest (requires SEC_USER_AGENT)
# --- Rung 2 ---
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
`--limit` and `--ticker` for smoke tests.

## Terminal dashboard (Rung 2 cockpit)

A Bloomberg-style dark terminal:

```bash
streamlit run scripts/terminal.py
```

Panels: Trade Blotter (ranked signals with trade type + weight), Security
(price/positioning/insider), Catalyst Calendar, Validation (calibration +
backtest), and Data Health. The original `scripts/dashboard.py` still works.

## Scheduling (unattended Rung 2)

Run the fail-soft pipeline daily via Windows Task Scheduler:

```powershell
schtasks /Create /SC DAILY /TN "EdgeEngineRefresh" /ST 18:00 ^
  /TR "cmd /c cd /d C:\Users\Diegos PC\Documents\biotech-db && python scripts\refresh_all.py >> data\logs\refresh.out 2>&1"
```

`refresh_all.py` continues past non-critical ingest failures and exits non-zero
if any stage fails, so the scheduler can surface problems.

## Dashboard

A read-only Streamlit dashboard visualizes the pipeline output:

```bash
streamlit run scripts/dashboard.py
```

Five pages (sidebar): Catalyst Watchlist, Catalyst Detail, Company View, Catalyst
Calendar, Data Health. It reads `DATABASE_URL` from `.env`, caches every query for
5 minutes, and never hardcodes credentials. Dark theme is set in
`.streamlit/config.toml`.

## Data Directories

| Path | Purpose | Gitignored |
|------|---------|------------|
| `data/seeds/` | Curated CSV seed data | No |
| `data/raw/` | Unmatched sponsors, raw exports | Yes |
| `data/cache/` | 24h API response cache | Yes |
| `data/logs/` | JSON structured logs | Yes |

## Database

Schema defined in `schema.sql`. Applied idempotently via `apply_schema.py` using `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.

Connection via `DATABASE_URL` in `.env` (Supabase Postgres pooler).

Portfolio data (syncs across machines):

| Table | Purpose |
|-------|---------|
| `portfolio_account` | Cash + starting capital (singleton) |
| `portfolio_holdings` | Every open/closed trade (PAPER + manual) |
| `portfolio_performance` | Daily equity snapshots + XBI benchmark |

Run `python apply_schema.py` after pulling to create new tables. Import local CSV history once with `python scripts/sync_performance_to_db.py`.

## Risk mitigation overlays (paper autopilot)

Two dynamic, end-of-day risk controls run inside `paper_autopilot.py` (all configurable in `.env`, all reduce risk only):

| Overlay | What it does | Key knobs (defaults) |
|---------|--------------|----------------------|
| **Drawdown circuit breaker** | If equity falls > X% below its peak, shrink all targets and pause new opens until it recovers (catches correlated sector selloffs). | `DRAWDOWN_CIRCUIT_PCT=0.10`, `DRAWDOWN_DERISK_FACTOR=0.5` |
| **Partial profit-lock (mean reversion)** | Scale OUT a fraction of a LONG winner when it is both in profit AND stretched above its short-term mean (z-score). Keeps a core into the catalyst; skips names with a catalyst within N days. | `PROFIT_LOCK_GAIN_PCT=0.20`, `PROFIT_LOCK_TRIM_FRACTION=0.25`, `PROFIT_LOCK_ZSCORE=1.5`, `PROFIT_LOCK_MIN_DAYS_TO_CATALYST=3` |

Disable either with `DRAWDOWN_CIRCUIT_ENABLED=0` / `PROFIT_LOCK_ENABLED=0`. Note: EOD-based — they mitigate multi-day slides and over-extension, not single-name overnight gaps (the 5% per-name cap covers that).

## Cloud automation (no laptop required)

Because all state lives in Supabase, the autopilot can run on GitHub Actions instead of a local scheduler — see `.github/workflows/`:

| Workflow | Trigger | Runs |
|----------|---------|------|
| `daily-refresh.yml` | cron daily 22:00 UTC (after US close) | `refresh_all.py` (keeps signals fresh) |
| `paper-autopilot.yml` | **after** the refresh completes, weekdays only | `paper_autopilot.py` (syncs the PAPER book) |

The autopilot is **chained** to the refresh (`workflow_run`), so trades always run on fresh data no matter how long the refresh takes — no fixed-gap race. The refresh runs every day (research data stays current); the autopilot gates itself to weekdays.

**Why once a day, not every few minutes:** every signal here is end-of-day granularity (prices use daily closes; SEC/CT.gov change slowly). Polling more often adds no signal, risks rate-limit bans from yfinance/SEC, and burns Actions minutes. Once daily after the close is optimal.

**Setup (one time):** add repo secrets under *Settings → Secrets and variables → Actions*: `DATABASE_URL` (both jobs) and `SEC_USER_AGENT` (refresh only, for SEC EDGAR). `POE_API_KEY` is **not** needed — the Layer-2 council is scaffolded but not wired into the active pipeline. Both workflows also have a manual *Run workflow* button (`workflow_dispatch`).

Caveats: GitHub Actions cron is best-effort (can be delayed minutes) and scheduled workflows auto-disable after ~60 days of repo inactivity (re-enable in the Actions tab). For a once-daily paper job this is fine. The local launchd/cron setup still works if you prefer running on your Mac — use one or the other to avoid double-trading.

To host the dashboard 24/7, deploy `scripts/terminal.py` to **Streamlit Community Cloud** (free) and add the same secrets there.

## SEC User-Agent (required before Layer 4)

SEC EDGAR requires a descriptive `User-Agent` header with your real contact info. Set in `.env`:

```
SEC_USER_AGENT=Firstname Lastname email@domain.com
```

Layer 2+ council does not need this; Layer 4 `fetch_filings.py` will call `check_sec_user_agent()` and fail fast if unset.

## Python Version Notes

This project runs on **Python 3.14** with version-bumped dependencies where upstream wheels are unavailable:

| Package | Pinned | Original pin | Reason |
|---------|--------|--------------|--------|
| pandas | 2.3.3 | 2.2.3 | cp314 wheel + Streamlit requires pandas<3 |
| pydantic | 2.13.4 | 2.10.3 | pydantic-core needs prebuilt wheel |
| lxml | 6.1.1 | 5.3.0 | No cp314 wheel |
| rapidfuzz | 3.14.5 | 3.10.1 | No cp314 wheel |

**Python 3.12** is the recommended fallback if a future library lacks 3.14 wheels. Do not downgrade automatically — test on 3.12 in a separate venv if needed.
