# GBM / Onc-CNS Edge Engine

A decision-support pipeline that scores small-cap biotech catalysts (trial readouts,
PDUFA dates, advisory committees) against an **empirical base-rate model** and the
**options market's implied move**, and turns the gap into a risk-capped, long-only
trade book — with a Bloomberg-style terminal UI, paper-trading autopilot, and honest
statistical validation.

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Tests](https://github.com/diegoevrard07-cyber/biotech-db/actions/workflows/tests.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green)

![Edge Terminal cockpit — paper-book equity vs XBI benchmark](docs/img/cockpit.png)

## Overview

Biotech stocks live and die by binary catalysts: a Phase 2 readout or an FDA decision
can move a small-cap 50% overnight. The direction of these events is close to
unforecastable — but the **odds** of success are not unknowable, and the **crowd's
pricing of those odds** is measurable. This project asks a simple question: *can an
objective, formulaic grade of a catalyst — built from historical base rates, trial
phase, and balance-sheet survivability, with no sentiment in it — be systematically
compared against what the options market has priced in, and does the gap carry edge?*

The engine ingests clinical trials (ClinicalTrials.gov), SEC filings and insider
transactions (EDGAR), and market data (prices, short interest, options-implied moves)
into Postgres; scores every upcoming catalyst; and emits a sized, risk-capped action
book. A Streamlit terminal surfaces the book, a paper-trading autopilot executes it
daily on GitHub Actions, and a validation suite (calibration, temporal holdouts,
walk-forward backtests, an event study of 2,028 real 8-K reactions) keeps the system
honest.

**Headline result:** the base-rate model predicts clinical-trial success with real,
out-of-sample skill (Brier skill **+0.098**, AUC **0.676**, n = 10,127 labeled trials,
temporal holdout). Just as important are the validated *negative* results the project
rules out: pre-event price action does **not** predict reaction direction (OOS R² ≈ 0,
47.6% hit rate), and a fancier logistic model does **not** beat the simple base-rate
lookup — findings that directly shaped the final design.

## Key results

| Finding | Evidence |
|---------|----------|
| Trial-success base-rate model has genuine OOS skill | Brier skill **+0.098**, AUC **0.676**, well-calibrated across probability buckets; temporal holdout (train pre-2019 / test post-2019), n = **10,127** labeled trials |
| 8-K catalyst reactions are extreme-variance; edge must come from selection | Event study of **2,028** real 8-Ks (**6,084** event-return rows): median 3-day abnormal return ≈ **−1%**, std ≈ **22%**, ~30% of events move ≥10% |
| Reaction *direction* is not predictable from pre-event technicals | Ridge regression, leakage-safe features: OOS R² **−0.001**, directional hit-rate **47.6%** (below coin flip) |
| Reaction *magnitude* is weakly predictable → used for sizing, not direction | OOS R² **+0.019**; predicted-big events realized **13.9%** avg abs. move vs **7.8%** predicted-small (1.8×, OOS) — wired in as a market-cap risk haircut |
| Simple beats fancy | Pure-numpy logistic regression (AUC 0.655) does **not** beat the base-rate lookup (0.672); the model is kept as a research tool, not wired into the scorer |
| Discipline over narratives | Unvalidated short/fade signals were measured to be the main P&L drag in paper trading and were **retired** — the book is now long-only by construction |

## How it works

```
                        ┌───────────────────── DATA SOURCES ─────────────────────┐
                        │  ClinicalTrials.gov   SEC EDGAR (8-K/XBRL/Form 4)      │
                        │  yfinance (OHLCV, short interest, options-implied move)│
                        └───────────────┬─────────────────────────────────────────┘
                                        │  scripts/ingest_*.py  (idempotent upserts)
                                        ▼
                              PostgreSQL (schema.sql — 22 tables)
                                        │
        ┌───────────────────────────────┼────────────────────────────────────┐
        ▼                               ▼                                    ▼
 layers/layer1   catalysts       layers/layer3   base rates           layers/layer4
 trials, readouts, PDUFA         52k historical trials → empirical    cash runway, burn,
 dates, dedupe                   P(success | phase, indication,       insider flow
                                 sponsor)                             
        └───────────────────────────────┬────────────────────────────────────┘
                                        ▼
                    layers/composite/scorer.py  — the decision layer
                      grade   = 0.25·proximity + 0.45·base_rate + 0.30·financial
                      edge_gap = model expected move − options-implied move
                      → trade_type (buy_the_rumor / hold_through / avoid)
                      → Kelly-fractional weight, market-cap risk haircut
                                        ▼
                    scripts/action_sheet.py — risk-capped long-only book
                      (sector ≤ 40%, GBM cluster ≤ 25%, single name ≤ 5%)
                                        ▼
        ┌───────────────────────────────┴───────────────────────────────┐
        ▼                                                               ▼
 scripts/terminal.py  (Streamlit terminal)        scripts/paper_autopilot.py
 Cockpit · Portfolio · Action Desk ·              daily paper-trading sync with
 Strategy · Market & Models                       stop-loss, drawdown tiers,
                                                  XBI regime filter (GitHub Actions)
                                        ▼
              Validation: calibrate.py (Brier/reliability) · backtest.py
              (walk-forward) · validate_base_rates.py (temporal holdout)
```

## Quickstart

Requires Python 3.12+ and a Postgres database (a free
[Supabase](https://supabase.com) project works).

```bash
git clone https://github.com/diegoevrard07-cyber/biotech-db.git
cd biotech-db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: DATABASE_URL (Postgres), SEC_USER_AGENT ("Name email" — SEC requirement)

python scripts/apply_schema.py         # create tables (idempotent)
python scripts/check_db_connection.py  # smoke-test the connection
python scripts/load_companies.py       # seed the curated company universe
python scripts/refresh_all.py          # full pipeline: ingest → score → validate
                                       # (fail-soft; takes a while on first run)

streamlit run scripts/terminal.py      # the Edge Terminal UI
python -m pytest                       # 180+ tests (DB-backed tests skip w/o DATABASE_URL)
```

Every script supports `--dry-run`; ingestion scripts also take `--limit` / `--ticker`
for smoke tests. All state lives in Postgres, so the pipeline is resumable and the
dashboard reads the same data the autopilot writes.

## Data

All data is pulled from **public sources at runtime** — no datasets are committed
(see `data/README.md`):

- **ClinicalTrials.gov API v2** — trial registry, catalyst dates, historical outcomes
- **SEC EDGAR** — 8-K filings, XBRL company facts (cash/burn), Form 4 insider trades
  (requires a descriptive `SEC_USER_AGENT`; free, no key)
- **yfinance** — daily OHLCV, short interest, options-implied move (unofficial API;
  the client is rate-limited and timeout-wrapped)

The only committed data are small hand-curated seed CSVs (`data/seeds/`: the company
universe and sponsor aliases) and an unused research artifact (`data/models/`).

## Repo structure

```
├── config.py               # every parameter/threshold, env-overridable, documented
├── db.py                   # SQLAlchemy engine + retrying connection helpers
├── logger.py               # structlog JSON logging
├── schema.sql              # Postgres schema — 22 tables, idempotent DDL
├── layers/                 # the pipeline package
│   ├── layer1/             #   catalyst discovery (ClinicalTrials.gov)
│   ├── layer3/             #   historical base rates (52k trials → P(success))
│   ├── layer4/             #   SEC financials, Form 4 insiders, 8-K parsing
│   ├── marketdata/         #   yfinance prices, options-implied move
│   ├── composite/          #   scorer, calibration, backtest metrics, logreg
│   └── portfolio/          #   position/P&L math, risk overlays, paper sync
├── scripts/                # CLI entry points (ingest_*, run_*, refresh_all.py, …)
│   ├── terminal.py         #   Streamlit "Edge Terminal" dashboard
│   ├── refresh_all.py      #   fail-soft orchestrator (the daily pipeline)
│   └── paper_autopilot.py  #   daily paper-trading sync + risk overlays
├── tests/                  # pytest suite — pure logic; DB tests skip w/o DATABASE_URL
├── data/                   # seeds (committed) + runtime artifacts (gitignored)
├── docs/                   # project handbook, operations log, images
└── .github/workflows/      # tests, daily data refresh, paper autopilot
```

## Methods & references

- **Reference-class forecasting / base rates** — Kahneman & Tversky; the scorer is
  deliberately few-factor and near-equal-weight (*Noise*, Kahneman/Sibony/Sunstein 2021).
- **Kelly criterion** (fractional, λ = 0.25, 5% per-name cap) — Kelly (1956).
- **Calibration** — Brier score & reliability buckets vs resolved outcomes (Brier 1950).
- **Models** — L2 logistic regression and ridge regression implemented in pure numpy
  (no sklearn dependency, by design); temporal holdouts throughout to block leakage.
- **Data** — ClinicalTrials.gov API v2; SEC EDGAR (submissions, XBRL companyfacts,
  Form 4); yfinance. Benchmark: XBI (SPDR S&P Biotech ETF).

## Limitations & future work

1. **`edge_gap` compares move *magnitude*, not signed return.** Upgrading to a signed
   mispricing signal needs historical options-implied-move and short-interest depth
   that only accrues going forward.
2. **Event-study window is ~2 years** and the universe is survivorship-trimmed; 8-Ks
   include routine non-catalyst filings (item codes not yet parsed).
3. **The paper-trading record is young and small-sample** — treated as a discipline
   harness, not proof of alpha. Resolved-catalyst calibration is nearly empty so far.
4. **No transaction-cost, borrow, or slippage model**; paper fills at prior close.
5. **Direction of 8-K reactions is not predictable from pre-event technicals**
   (validated negative). Future direction edge must come from fundamentals — trial
   design, biomarker stratification, interim data.
6. **Layer-2 LLM "science council"** (mechanism/design critique via Poe API) is
   scaffolded but not wired into the active pipeline.

## License

MIT — see [LICENSE](LICENSE).

---

*Decision-support research software. Not investment advice. All tracked positions
are paper trades.*
