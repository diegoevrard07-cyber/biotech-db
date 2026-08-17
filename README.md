# GBM / Onc-CNS Edge Engine

**A research pipeline that estimates whether small-cap oncology/CNS biotech stocks are
mispriced ahead of clinical-trial readouts and FDA decisions — and turns that estimate
into sized, typed paper trades.**

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Tests](https://github.com/diegoevrard07-cyber/biotech-db/actions/workflows/tests.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green)

![Edge Terminal — cockpit view: paper-book equity vs the XBI biotech benchmark](docs/img/terminal.png)

![Edge Terminal — the action desk: ranked catalyst trades with type, weight, and timing](docs/img/dashboard.png)

## Why this exists

Small-cap biotech is a market built on binary events. A Phase 2 readout or a PDUFA
decision can move a stock 50% overnight, and the *behavioral* patterns around those
events repeat: prices run up into the print, hype concentrates in names with weak
historical odds, and companies that can't fund themselves to their own catalyst get
diluted at the worst moment. I built this project on a simple thesis: the **direction**
of a trial result is close to unforecastable, but the **odds** are estimable from
history, and the **crowd's pricing of those odds** is measurable. The tradeable
quantity is the gap between the two.

I anchor every score on historical base rates — how often trials like this one, by
phase, disease, and sponsor type, have actually succeeded — and I deliberately keep
the scoring model simple: few factors, near-equal weights. That's the explicit lesson
of Kahneman, Sibony, and Sunstein's *Noise*: in low-signal domains, a simple formula
consistently beats expert judgment and kitchen-sink models alike. This project tested
that directly — a regularized logistic regression with more features did *not* beat
the plain base-rate lookup, so the lookup stayed.

The discipline stance is the point: **trust nothing until calibration (Brier score,
reliability buckets) and walk-forward backtesting show a positive edge on resolved
outcomes.** Every signal here is paper-traded, every assumption is measured, and the
results — including the negative ones — are in the repo.

## What it does

The engine runs a daily pipeline from raw public data to a risk-capped trade book:

1. **Ingest** upcoming catalysts (trial readouts, PDUFA dates, advisory committees)
   from ClinicalTrials.gov; financials, 8-K filings, and Form 4 insider transactions
   from SEC EDGAR; and prices, short interest, and options data via yfinance.
2. **Anchor on base rates** — the empirical success probability of a trial given its
   phase, indication, and sponsor class, mined from ~52,000 historical trials.
3. **Grade each catalyst** with a three-factor composite (catalyst proximity, base
   rate, balance-sheet survivability) — deliberately no sentiment in the grade.
4. **Compare against the market.** The model's *expected move* for the event is set
   against the options market's *implied move* (how big a move traders have priced
   in). The difference is the **edge gap** — the mispricing estimate everything
   hangs on.
5. **Decide and size.** A decision layer emits a trade type — `buy_the_rumor` (ride
   the run-up, exit before the print), `hold_through` (own the binary when odds
   justify it), or `avoid` — and a **Kelly-fractional** position weight (the Kelly
   criterion's optimal bet fraction, taken at quarter-Kelly and capped at 5% per
   name). A fourth type, `fade` (shorting overhyped names), existed and was
   **retired after measurement** — the event study found no reliable edge in it, and
   in paper trading it was the main P&L drag.
6. **Cap the risk** — sector, GBM-cluster, and gross-exposure caps, a market-cap risk
   haircut, and EOD overlays (stop-loss, drawdown tiers, regime filter) that can only
   ever *reduce* exposure.
7. **Execute on paper, daily**, via a GitHub Actions autopilot, and display in a
   Bloomberg-style Streamlit terminal.

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
                    layers/composite/scorer.py  — the decision engine
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

The project started GBM-only and was later broadened to small-cap oncology/CNS with
GBM kept as the flagged flagship vertical (that expansion was internally "Rung 2" —
this README just says "the decision engine" from here).

## Results & validation

Numbers below are from the committed project logs; samples are dated where it matters.
The short version: the *scientific* prediction is validated; the *trading* record is
young and honestly small.

**Validated — trial-success base-rate model** (temporal holdout: train pre-2019, test
post-2019, n = **10,127** labeled trials): Brier skill **+0.098** over the base-rate
prior, AUC **0.676**, well-calibrated across all probability buckets. This predicts
*clinical trial success* — a feature, not a stock return — and it's the load-bearing
input to everything downstream.

**Measured — what catalyst reactions look like** (event study of **2,028** real SEC
8-Ks, 6,084 event-return rows vs the XBI biotech benchmark, as of 2026-06-21): median
3-day abnormal return ≈ **−1%**, std ≈ **22%**, ~30% of events move ≥10%. Most 8-Ks
are noise — the edge has to come from *selection*, not participation.

**Validated negatives (the useful kind):**

- Reaction *direction* is not predictable from pre-event price action: ridge
  regression on leakage-safe technical features, OOS R² **−0.001**, hit rate **47.6%**
  — below a coin flip. No price-based direction signal is wired in.
- Reaction *magnitude* is weakly predictable (OOS R² **+0.019**; predicted-big events
  realized **13.9%** vs **7.8%** average absolute move, 1.8×). Used for *sizing*
  (small-cap risk haircut), never direction.
- A pure-numpy logistic regression (AUC 0.655) did not beat the base-rate lookup
  (0.672). The simple model won; the fancier one is kept as a research tool.

**Honestly early — the paper-trading record.** The live paper book is weeks old with
a tiny sample: as of 2026-07-03, 39 closed longs at +3.7% on cost with a 79% win rate,
but lagging buy-and-hold XBI over the same hot window, and resolved-catalyst
calibration is nearly empty (n ≈ 1–3). The shorts/fades book was measured to be the
main drag and was killed; the long book leans on the validated base-rate edge. This is
ongoing validation, not a claim of alpha — the point of the repo is the *process* that
will prove or disprove it.

Universe scale (as of 2026-06-21): 131 companies · 450 tracked catalysts · 52,341
historical trials · ~142k daily price rows across 115 tickers · 6,822 insider
transactions · 180+ automated tests.

## Design decisions I'd defend in an interview

- **Simple, near-equal weights — on evidence, not taste.** In a domain this noisy,
  extra parameters fit noise. The *Noise* hypothesis was tested: the logistic model
  lost to the lookup, so the lookup stayed.
- **Once-daily, end-of-day cadence is optimal here.** Every signal is EOD granularity
  (daily closes; SEC/CT.gov change slowly). Polling more often adds no signal, risks
  yfinance/SEC rate-limit bans, and burns CI minutes.
- **Risk overlays only ever reduce risk.** Stop-loss, drawdown tiers, regime filter,
  profit-lock, and the market-cap haircut can shrink or block a position — never
  enlarge one. There is no code path that adds risk dynamically.
- **The human stays in the loop.** This is decision support: the engine proposes a
  sized book, a human reviews and executes. No broker integration, no auto-trading —
  the autopilot only ever touches paper positions.
- **All state lives in Postgres**, so the whole loop — ingestion, scoring, paper
  trading, dashboard — runs serverless on GitHub Actions and Streamlit Cloud, and the
  UI reads exactly what the autopilot writes.
- **Models are pure numpy, no sklearn.** A few hundred lines I can audit line-by-line
  beat a black-box dependency for a few thousand rows.

## Quickstart

Requires Python 3.12+ and a Postgres database — a free [Supabase](https://supabase.com)
project, or local Docker (`docker compose up -d`, then
`DATABASE_URL=postgresql://postgres:postgres@localhost:5432/biotech`).

```bash
git clone https://github.com/diegoevrard07-cyber/biotech-db.git
cd biotech-db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: DATABASE_URL (Postgres) and SEC_USER_AGENT ("Name email" — SEC requirement)
# POE_API_KEY is optional — the Layer-2 science council is scaffolded, not wired in

python scripts/apply_schema.py     # create tables (idempotent)
python scripts/refresh_all.py      # full pipeline: ingest → score → validate (fail-soft)
streamlit run scripts/terminal.py  # the Edge Terminal
```

`python -m pytest` runs the 180+ test suite (DB-backed tests skip automatically
without `DATABASE_URL`). The full script-by-script run order, per-script flags, and
verify scripts are in [docs/PIPELINE.md](docs/PIPELINE.md).

## Repository layout

```
├── config.py               # every parameter/threshold, env-overridable, documented
├── schema.sql              # Postgres schema — 22 tables, idempotent DDL
├── layers/                 # the pipeline package
│   ├── layer1/             #   catalyst discovery (ClinicalTrials.gov)
│   ├── layer3/             #   historical base rates (52k trials → P(success))
│   ├── layer4/             #   SEC financials, Form 4 insiders, 8-K parsing
│   ├── marketdata/         #   yfinance prices, options-implied move
│   ├── composite/          #   scorer, calibration, backtest metrics, logreg
│   └── portfolio/          #   position/P&L math, risk overlays, paper sync
├── scripts/                # CLI entry points (terminal.py, refresh_all.py, …)
├── tests/                  # pytest suite — pure logic; DB tests skip w/o DATABASE_URL
├── data/                   # seeds (committed) + runtime artifacts (gitignored)
└── docs/                   # PIPELINE.md · OPERATIONS.md · DATA.md · handbook · ops log
```

## Methods & references

- **Reference-class forecasting / base rates** — Kahneman & Tversky; *Noise*
  (Kahneman, Sibony, Sunstein 2021) for the few-factors, equal-weights discipline.
- **Kelly criterion** — Kelly (1956); fractional (λ = 0.25), capped per name.
- **Calibration** — Brier (1950); Brier score + reliability buckets vs resolved outcomes.
- **Models** — L2 logistic regression and ridge regression in pure numpy; temporal
  holdouts throughout to block leakage.
- **Data** — ClinicalTrials.gov API v2; SEC EDGAR (submissions, XBRL companyfacts,
  Form 4); yfinance. Benchmark: XBI (SPDR S&P Biotech ETF).

## Limitations & roadmap

1. **EOD granularity** can't catch intraday/overnight single-name gaps beyond the 5%
   per-name cap → next: intraday risk checks need a live data feed.
2. **Resolved-catalyst sample is tiny** (calibration nearly empty) → accrues
   automatically as the forward book resolves; re-run `calibrate.py` monthly.
3. **`edge_gap` compares move magnitude, not signed return** → next: accumulate
   implied-move snapshots to build a signed mispricing signal.
4. **Survivorship/lookahead risk** — the universe is today's listed names; guards are
   temporal holdouts and as-of decision rules in the backtest → next: point-in-time
   universe including delistings.
5. **Layer-2 science council** (LLM mechanism/design critique via Poe) is scaffolded
   but not wired in → next: connect it to the scorer's `science_score` slot.
6. **Paper trading only** — no transaction-cost, borrow, or slippage model → next:
   a cost model before any real-money consideration.
7. **Event-study window is ~2 years** and 8-Ks include routine non-catalyst noise →
   next: parse 8-K item codes to drop routine filings.

## Disclaimer & license

Personal research project. Decision support only; all tracked positions are paper
trades. Not financial advice. MIT — see [LICENSE](LICENSE).

---

*More detail: [docs/PIPELINE.md](docs/PIPELINE.md) (full run order) ·
[docs/OPERATIONS.md](docs/OPERATIONS.md) (scheduling, hosting, risk overlays) ·
[docs/DATA.md](docs/DATA.md) (data sources, schema, Python notes) ·
[docs/AGENT_HANDOFF.md](docs/AGENT_HANDOFF.md) (project handbook) ·
[docs/OPERATIONS_LOG.md](docs/OPERATIONS_LOG.md) (change journal)*
