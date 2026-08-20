# Biotech Catalyst Edge Engine

A research pipeline and paper-trading system for small-cap biotech catalysts.
It estimates the probability that a clinical trial succeeds, compares the expected
stock move against what the options market has priced in, and turns the gap into a
sized, risk-capped paper portfolio. The whole loop runs unattended once a day on
GitHub Actions and reports into a Streamlit terminal.

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Tests](https://github.com/diegoevrard07-cyber/biotech-db/actions/workflows/tests.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green)

![Portfolio: equity vs XBI, allocation, live KPIs](docs/img/terminal.png)

![Research / Risk: Monte Carlo fan and index-shock scenarios](docs/img/risk-lab.png)

![Research / Risk: 3D catalyst landscape and Kelly surface](docs/img/dashboard.png)

## The idea

Small-cap biotech trades on binary events. A Phase 2 readout or an FDA decision can
move a stock 50% in a day, and the behavior around these events repeats: prices run
up into the news, hype concentrates in trials with weak historical odds, and companies
that cannot fund themselves to their own catalyst get diluted at the worst moment.

The starting thesis is that the direction of a single trial result is close to
unforecastable, but the odds are estimable from history, and the market's pricing of
those odds is measurable. The tradeable quantity is the gap between the two.

Every score is therefore anchored on base rates: how often trials like this one, by
phase, indication and sponsor type, have actually succeeded, computed from 52,000
historical trials. The scoring model is kept simple on purpose, a few factors with
near-equal weights. That choice was tested rather than assumed: a regularized
logistic regression with more features failed to beat the plain base-rate lookup out
of sample, so the lookup stayed.

Nothing is trusted until calibration (Brier score, reliability buckets) and
walk-forward backtesting show a positive edge on resolved outcomes. Every signal is
paper-traded, and the results, including the negative ones, are in this repo.

## How it works

The daily pipeline goes from raw public data to a risk-capped trade book:

1. **Ingest.** Upcoming catalysts (trial readouts, PDUFA dates, advisory committees)
   from ClinicalTrials.gov; financials, 8-K filings and Form 4 insider transactions
   from SEC EDGAR; prices, short interest and options data via yfinance.
2. **Anchor on base rates.** The empirical success probability of each trial given
   its phase, indication and sponsor class.
3. **Grade each catalyst** with a three-factor composite: catalyst proximity, base
   rate, balance-sheet survivability. No sentiment goes into the grade.
4. **Compare against the market.** The model's expected move for the event is set
   against the options-implied move. The difference is the edge gap, the mispricing
   estimate everything hangs on.
5. **Decide and size.** A decision layer emits a trade type (`buy_the_rumor`: ride
   the run-up and exit before the print, `hold_through`: own the binary when the odds
   justify it, or `avoid`) and a position weight from the Kelly criterion, taken at
   quarter-Kelly and capped at 5% per name. A fourth type, `fade` (shorting overhyped
   names), was retired after measurement: the event study found no reliable edge in
   it, and in paper trading it was the main drag on P&L.
6. **Cap the risk.** Sector, GBM-cluster and gross-exposure caps, a market-cap risk
   haircut, and end-of-day overlays (stop-loss, drawdown tiers, regime filter) that
   can only ever reduce exposure.
7. **Execute on paper, daily,** via a GitHub Actions autopilot, and display
   everything in a Bloomberg-style Streamlit terminal.

```
                        ┌───────────────────── DATA SOURCES ─────────────────────┐
                        │  ClinicalTrials.gov   SEC EDGAR (8-K/XBRL/Form 4)      │
                        │  yfinance (OHLCV, short interest, options-implied move)│
                        └───────────────┬────────────────────────────────────────┘
                                        │  scripts/ingest_*.py  (idempotent upserts)
                                        ▼
                              PostgreSQL (schema.sql, 22 tables)
                                        │
        ┌───────────────────────────────┼────────────────────────────────────┐
        ▼                               ▼                                    ▼
 layers/layer1   catalysts       layers/layer3   base rates           layers/layer4
 trials, readouts, PDUFA         52k historical trials to empirical   cash runway, burn,
 dates, dedupe                   P(success | phase, indication,       insider flow
                                 sponsor)
        └───────────────────────────────┬────────────────────────────────────┘
                                        ▼
                    layers/composite/scorer.py   (the decision engine)
                      grade    = 0.25*proximity + 0.45*base_rate + 0.30*financial
                      edge_gap = model expected move - options-implied move
                      -> trade_type (buy_the_rumor / hold_through / avoid)
                      -> Kelly-fractional weight, market-cap risk haircut
                                        ▼
                    scripts/action_sheet.py   (risk-capped long-only book)
                      sector <= 40%, GBM cluster <= 25%, single name <= 5%
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

## Results

Numbers come from the committed project logs and are dated where it matters. The
scientific prediction is validated; the trading record is young and small, and is
reported that way.

**Validated: the trial-success model.** On a temporal holdout (train pre-2019, test
post-2019, n = 10,127 labeled trials) the base-rate model scores a Brier skill of
+0.098 over the prior with AUC 0.676, and is well calibrated across all probability
buckets. This predicts clinical trial success, a feature rather than a stock return,
and it is the load-bearing input to everything downstream.

**Measured: what catalyst reactions look like.** An event study of 2,028 real SEC
8-K filings (6,084 event-return rows against the XBI biotech benchmark, as of
2026-06-21) shows a median 3-day abnormal return of about -1% with a standard
deviation near 22%, and roughly 30% of events moving 10% or more. Most 8-Ks are
noise, so any edge has to come from selection, not participation.

**Negative results, kept on purpose:**

- Reaction direction is not predictable from pre-event price action. A ridge
  regression on leakage-safe technical features scored an out-of-sample R² of
  -0.001 with a 47.6% hit rate, below a coin flip. No price-based direction signal
  is wired into the scorer.
- Reaction magnitude is weakly predictable (out-of-sample R² +0.019; events the
  model called big realized an average absolute move of 13.9% vs 7.8% for the rest).
  This is used only for sizing, via the small-cap risk haircut, never for direction.
- A pure-numpy logistic regression (AUC 0.655) did not beat the base-rate lookup
  (0.672 in the same head-to-head run). The simple model won and the fancier one is
  kept as a research tool.

**Early: the paper-trading record.** The live paper book is weeks old with a small
sample. As of 2026-07-03: 39 closed longs at +3.7% on cost with a 79% win rate,
while lagging buy-and-hold XBI over the same strong stretch, and resolved-catalyst
calibration is nearly empty (n of 1 to 3). The shorts book was measured to be the
main drag and was cut; the long book leans on the validated base-rate edge. This is
ongoing validation, not a claim of alpha. The point of the repo is the process that
will prove or disprove it.

Scale as of 2026-06-21: 131 companies, 450 tracked catalysts, 52,341 historical
trials, ~142k daily price rows across 115 tickers, 6,822 insider transactions, and
180+ automated tests.

## Design decisions

- **Simple, near-equal weights, on evidence rather than taste.** In a domain this
  noisy, extra parameters fit noise. The hypothesis from Kahneman, Sibony and
  Sunstein's *Noise* (simple formulas beat expert judgment in low-signal domains)
  was tested directly: the logistic model lost to the lookup, so the lookup stayed.
- The cadence is once daily, end of day. Every signal is end-of-day granularity
  (daily closes; SEC and ClinicalTrials.gov change slowly), so polling more often
  adds no signal, risks rate-limit bans, and burns CI minutes.
- **Risk overlays only ever reduce risk.** Stop-loss, drawdown tiers, regime filter,
  profit-lock and the market-cap haircut can shrink or block a position, never
  enlarge one. There is no code path that adds risk dynamically.
- The human stays in the loop. This is decision support: the engine proposes a
  sized book, a human reviews it. There is no broker integration and no auto-trading;
  the autopilot only ever touches paper positions.
- **All state lives in Postgres,** so the full loop (ingestion, scoring, paper
  trading, dashboard) runs serverless on GitHub Actions and Streamlit Cloud, and the
  UI reads exactly what the autopilot writes.
- Models are pure numpy, no sklearn: a few hundred auditable lines beat a black-box
  dependency for a few thousand rows.

## Quickstart

Requires Python 3.12+ and a Postgres database: a free [Supabase](https://supabase.com)
project, or local Docker (`docker compose up -d`, then
`DATABASE_URL=postgresql://postgres:postgres@localhost:5432/biotech`).

```bash
git clone https://github.com/diegoevrard07-cyber/biotech-db.git
cd biotech-db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: DATABASE_URL (Postgres) and SEC_USER_AGENT ("Name email", an SEC requirement)

python scripts/apply_schema.py     # create tables (idempotent)
python scripts/refresh_all.py      # full pipeline: ingest, score, validate (fail-soft)
streamlit run scripts/terminal.py  # the Edge Terminal
```

`python -m pytest` runs the 180+ test suite (DB-backed tests skip automatically
without `DATABASE_URL`). The full script-by-script run order and per-script flags
are in [docs/PIPELINE.md](docs/PIPELINE.md).

## Repository layout

```
├── config.py               # every parameter/threshold, env-overridable, documented
├── schema.sql              # Postgres schema, 22 tables, idempotent DDL
├── layers/                 # the pipeline package
│   ├── layer1/             #   catalyst discovery (ClinicalTrials.gov)
│   ├── layer3/             #   historical base rates (52k trials to P(success))
│   ├── layer4/             #   SEC financials, Form 4 insiders, 8-K parsing
│   ├── marketdata/         #   yfinance prices, options-implied move
│   ├── composite/          #   scorer, calibration, backtest metrics, logreg
│   └── portfolio/          #   position/P&L math, risk overlays, paper sync
├── scripts/                # CLI entry points (terminal.py, refresh_all.py, ...)
├── tests/                  # pytest suite; DB tests skip without DATABASE_URL
├── data/                   # seeds (committed) + runtime artifacts (gitignored)
└── docs/                   # pipeline, operations, data reference, handbook, ops log
```

## Methods and references

- **Reference-class forecasting / base rates:** Kahneman and Tversky; *Noise*
  (Kahneman, Sibony, Sunstein, 2021) for the few-factors, equal-weights discipline.
- **Kelly criterion:** Kelly (1956); fractional (0.25x), capped per name.
- **Calibration:** Brier (1950); Brier score and reliability buckets against
  resolved outcomes.
- **Models:** L2 logistic regression and ridge regression in pure numpy, with
  temporal holdouts throughout to block leakage.
- **Data:** ClinicalTrials.gov API v2; SEC EDGAR (submissions, XBRL company facts,
  Form 4); yfinance. Benchmark: XBI (SPDR S&P Biotech ETF).

## Limitations and next steps

1. **End-of-day granularity** cannot catch intraday or overnight single-name gaps
   beyond the 5% per-name cap. Next: intraday risk checks, which need a live feed.
2. **The resolved-catalyst sample is tiny** (calibration nearly empty). It accrues
   automatically as the forward book resolves; `calibrate.py` re-runs monthly.
3. **`edge_gap` compares move magnitude, not signed return.** Next: accumulate
   implied-move snapshots to build a signed mispricing signal.
4. **Survivorship and lookahead risk.** The universe is today's listed names; the
   guards are temporal holdouts and as-of decision rules in the backtest. Next: a
   point-in-time universe that includes delistings.
5. **Paper trading only,** with no transaction-cost, borrow or slippage model. Next:
   a cost model before any real-money consideration.
6. **The event-study window is about two years** and 8-Ks include routine
   non-catalyst filings. Next: parse 8-K item codes to drop the routine ones.

## Disclaimer and license

Personal research project. Decision support only; all tracked positions are paper
trades. Not financial advice. MIT licensed, see [LICENSE](LICENSE).

---

*More detail: [docs/PIPELINE.md](docs/PIPELINE.md) (full run order) ·
[docs/OPERATIONS.md](docs/OPERATIONS.md) (scheduling, hosting, risk overlays) ·
[docs/DATA.md](docs/DATA.md) (data sources, schema) ·
[docs/HANDBOOK.md](docs/HANDBOOK.md) (project handbook) ·
[docs/OPERATIONS_LOG.md](docs/OPERATIONS_LOG.md) (change journal)*
