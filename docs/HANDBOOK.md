# Project handbook

> The intent, mental model, current state and known traps of the Biotech Catalyst
> Edge Engine, in one place. Coding standards live in `.cursorrules`; this document
> holds the why and the where-we-are.
>
> **Operations log:** every operational change (trading/risk rules, scheduling,
> pipeline, schema, notable UI) is recorded in `docs/OPERATIONS_LOG.md`, newest first.

---

## 1. What this project is

A Bloomberg-terminal-style decision-support engine for trading small-cap biotech
(GBM-focused, broadened to oncology/CNS) around clinical and regulatory catalysts.

The design goal is an objective, formulaic process, the lesson of Kahneman's *Noise*,
rather than gut feel.

**Operating model:** the pipeline ingests data, scores it, and proposes a risk-capped
trade book. A human reviews and executes. No automated broker integration, no
auto-trading. All positions are paper trades.

---

## 2. The core mental model

The money is in the GAP between the engine's grade and what the market has priced in.
These are two distinct quantities, not one subtraction:

1. **Grade (intrinsic quality)**, with no sentiment in it:
   `composite = 0.25*proximity + 0.45*base_rate + 0.30*financial`
   (catalyst nearness, trial odds, cash runway).

2. **Edge gap (the mispricing signal)**:
   `edge_gap = predicted_move - market_implied_move`
   - `edge_gap < 0`: the market pays for a bigger move than justified (overpriced)
   - `edge_gap > 0`: the market underprices the move (underpriced, own it)

The one-line target the design converges on:

```
Trade signal = predicted return - the return the market already priced in
  the prediction = trial odds + financials + other factors
  market's view  = options-implied move + short interest + run-up + valuation
```

Long when the model is more bullish than the crowd, short when more bearish. The gap
is only trustworthy if the grade is accurate, so improving the grade (base_rate
especially) improves everything downstream.

**Current limitation (important):** today's `edge_gap` compares move MAGNITUDE
(`expected_move` vs the options-implied move), not signed direction or return.
Upgrading to a signed-return prediction is the long-term plan (see Roadmap), but it
is blocked on data depth (see section 5).

The scorer and decision logic live in `layers/composite/scorer.py` (`decide_trade`,
`compute_edge_score`). Keep it simple and base-rate-anchored. The *Noise* lesson is
few factors, near-equal weights, no kitchen-sink overfitting.

---

## 3. Tech and infra realities (traps that already bit)

- **Python 3.12+ (3.14 verified), PostgreSQL 16.** Config via `.env`. See
  `requirements.txt` for the pinned library list. Do not add heavy deps
  (sklearn/statsmodels) casually; small models are implemented in pure numpy
  (numpy ships with pandas) to keep the stack lean.
- **The DB sits behind a connection pooler (pgbouncer / Supabase-style).** Confirmed
  via `pg_stat_activity` showing `pgbouncer.get_auth`, `LISTEN "pgrst"`.
- **TRAP 1: per-row inserts are catastrophically slow through the pooler.**
  SQLAlchemy/psycopg2 `executemany` does one network round-trip PER ROW. A few
  thousand `ON CONFLICT` rows takes minutes and looks like a hang. Fix: one bulk
  `INSERT ... VALUES` per chunk via `psycopg2.extras.execute_values`. Applied in
  `scripts/ingest_prices.py` (`_bulk_upsert`). Other ingest/score scripts may have
  the same pattern; fix them the same way when they are slow.
- **TRAP 2: raw-cursor writes need a raw commit.** Writes made via a raw psycopg2
  cursor (`conn.connection.cursor()`) are NOT committed by SQLAlchemy's
  `conn.commit()`; they get rolled back on close. Call `raw.commit()` on the raw
  DBAPI connection. This silently lost a full price backfill once: the script
  reported "31,317 upserted" while only 7,946 persisted. ALWAYS verify writes with
  an independent `COUNT(*)` query; never trust a script's own summary.
- **TRAP 3: yfinance throttling and hangs.** Yahoo rate-limits by IP. `yf_client.py`
  wraps calls with timeouts and uses batched `yf.download` (`fetch_history_batch`).
  Keep download chunks at or below ~30 tickers. Delisted tickers print "possibly
  delisted" and return empty; that is expected, not an error.
- **TRAP 4: catalyst refresh used DELETE+INSERT and broke on FKs (fixed 2026-06-21).**
  `ingest_layer1._insert_catalysts` deleted all ctgov_v2 catalysts for a company then
  re-inserted. Once `edge_scores`/`portfolio_holdings`/`catalyst_outcomes` referenced
  catalysts, the DELETE raised ForeignKeyViolation, `ingest_layer1` exited 1, and NO
  new catalysts were ingested (silent until checked). Fix: idempotent UPSERT on the
  partial unique index `uq_catalysts_ctgov (trial_id, catalyst_type) WHERE
  source='ctgov_v2'`, keeping catalyst ids STABLE so downstream refs survive. Stale
  catalysts now linger (benign, filtered by date). `refresh_all` runs `apply_schema`
  first so the index is always present. Any new table that FK-references catalysts
  must upsert, never delete-replace.
- **CONCURRENTLY/lock note:** creating indexes while a `refresh_all` is running
  blocks on the refresh's long-lived pooled transactions (even CONCURRENTLY waits).
  Do schema/index changes when no pipeline is running, or let `apply_schema`
  (stage 0) handle them.

---

## 4. Pipeline and run order

Orchestrator: `scripts/refresh_all.py` (fail-soft, runs stages in order). Key stages:

1. `scripts/apply_schema.py`: idempotent schema (`schema.sql`).
2. Ingestion: companies/trials/catalysts/financials, then
   `classify_universe.py` (oncology/CNS categories, GBM flag, small-cap filter),
   `ingest_prices.py` (OHLCV via yfinance), `ingest_positioning.py` (short interest,
   options-implied move, run-up), `ingest_insider.py` (SEC Form 4).
3. Scoring: `run_composite.py` writes `edge_scores` (grade + edge_gap + trade_type +
   weight).
4. Outcomes/validation: `resolve_outcomes.py` (label realized catalysts from prices),
   `calibrate.py` (Brier/reliability vs outcomes), `validate_base_rates.py` (temporal
   holdout of the base-rate model), `backtest.py` (walk-forward of trade rules).
5. Output: `action_sheet.py` (risk-capped book to `data/raw/action_sheet_*.csv`),
   `verify_signals.py` (data-health checks).
6. UI: `scripts/terminal.py` (the Streamlit Edge Terminal). Pages: Cockpit (action
   center + account + top ideas with $ sizing), Portfolio (positions tracker),
   Action Desk, Strategy, Market & Models (dossier, calendar, validation, data
   health, glossary). Run: `streamlit run scripts/terminal.py --server.port 8520`.

**Portfolio tracker** (paper trading; persists to the remote Postgres, not local):

- `DATABASE_URL` typically points at a Supabase cloud pooler. Anything entered in
  the dashboard saves to that cloud DB and persists across restarts and devices. To
  make it local, repoint `DATABASE_URL` at a local Postgres.
- Tables: `portfolio_account` (singleton: cash_usd auto-adjusts on open/close),
  `portfolio_holdings` (one row per position, open/closed, linked to a catalyst for
  exit timing).
- Pure logic: `layers/portfolio/tracker.py` (valuation, P&L, cash flows, exit
  alerts, $-sizing). Sign convention: long = +shares*price asset, short =
  -shares*price liability, so equity = cash + sum of signed market values. Tested in
  `tests/test_portfolio_tracker.py`.
- Prices are end-of-day (latest `price_history` close), not live.
- Exit rules: buy_the_rumor sells ~1 day before the catalyst; hold_through exits
  after it.

**Trading-readiness tools:**

- `scripts/liquidity_check.py`: pre-trade fill-risk for the near-term book: ADV
  ($ volume, 20d), range% (spread/volatility proxy), position $ at the sleeve, and
  % of ADV. Flags ILLIQUID (<$500k), TOO-BIG (>10% ADV), WIDE (range >8%).
- `scripts/seed_paper_trades.py`: seeds PAPER longs (buy_the_rumor/hold_through
  only) from the near-term book into `portfolio_holdings` (notes='PAPER'), sized at
  de-risked weight vs a paper sleeve, exit dates from `tracker.planned_exit`.
  Idempotent; `--reset` wipes PAPER positions and resets cash.

**Paper autopilot** (unattended daily run):

- `scripts/paper_autopilot.py`: one daily cycle. Refresh prices for held and
  candidate tickers, execute exits (close PAPER positions whose planned_exit_date
  arrived, book realized P&L, return cash), optionally open new near-term longs,
  then append a snapshot to `data/raw/paper_performance.csv`. Fail-soft on price
  fetch. Only touches notes='PAPER'. Flags: `--dry-run`, `--no-open`.
- Scheduling: GitHub Actions (`.github/workflows/paper-autopilot.yml`) runs it on
  weekdays after the data refresh, writing to the same remote tables the dashboard
  reads.

Run any script directly (e.g. `python scripts/ingest_prices.py --lookback-days 400`).
Most support `--dry-run`, `--limit`, `--ticker`.

---

## 5. Current state, as of 2026-06-21

**Data:**

- Prices: 115 tickers / ~142k rows (5-year history, 2021-06 to 2026-06). 17 universe
  tickers delisted (no data).
- Positioning: 113 short interest, 101 implied move, 112 run-up, 114 market caps.
- Insider: 110 companies, 6,822 Form 4 transactions.
- Catalysts: 450 (444 upcoming, 77 in the next 180d, only 6 in the past, so the DB
  is forward-looking).
- `catalyst_outcomes`: ~2 resolved (both ambiguous); forward catalysts not yet
  resolved.
- `event_returns`: 6,084 rows (2,028 8-K events x {1,3,5}d holds), the first real
  signed abnormal-return ground truth. Built by `scripts/build_event_returns.py`.
- `historical_trials`: 52,341 rows; 10,127 with usable `primary_outcome_met` labels.

**Validated edge (the proven part):** the base-rate model on a temporal holdout
(train pre-2019 / test post-2019, n=10,127): Brier skill +0.098, AUC 0.676, well
calibrated across all probability buckets. Real but modest, and it predicts clinical
trial success, which is a feature, not stock returns.

**Live book:** long-only. `action_sheet.py` drops every short/fade; the scorer maps
former fade setups to `avoid`. Caps: gross long <= 100%, gross short = 0, GBM <= 25%.

**Honest risk:** the longs lean on the validated base-rate edge. Fades/shorts are
retired (unvalidated; they were the main drag). Run `scripts/fix_long_only_book.py`
on the live DB if any leftover shorts or stale fade scores remain.

**Returns dataset, partially unblocked (2026-06-21):** the earlier blocker ("no
announcement dates") was wrong. `sec_filings` holds 2,028 8-K filings across 106
companies (2024-06 to 2026-06), all inside the 5-year price window. 8-K dates ARE
the dates the market reacted. `scripts/build_event_returns.py` builds
`event_returns` (6,084 rows = 2,028 events x hold {1,3,5}d) measuring realized
abnormal returns (stock minus XBI) plus 30d pre-event run-up. Remaining gaps: 8-Ks
include routine non-catalyst noise; the window is ~2 years, not 5; still
survivorship-trimmed; historical implied-move/short-interest cannot be
reconstructed, so edge_gap itself is not backtestable yet.

**Event-study findings (hold=3d, n=2,028):**

- Reactions have extreme variance: std ~22%, ~30% of events move >=10%, ~8% move
  >=25%. Median ~-1%, so most 8-Ks are noise; the edge is selection, not
  participation.
- "Fade the run-up" is weak: corr(run-up, forward abnormal) ~ -0.04. It is a
  barbell: names that already crashed (Q1) or already mooned (Q5) underperform; the
  middle drifts up (+2 to 3%). Only the most extreme run-ups give back, and only
  ~1%. The scorer's former `fade` signals are not strongly supported by realized
  data, which is why they were retired.
- Sign sanity holds: offerings and license deals skew negative, approvals positive,
  which validates the abnormal-return math.

---

## 6. Roadmap (priority order)

1. **[DONE 2026-06-21] Fixed `run_composite.py`** per-row pooler insert to bulk
   `execute_values` (160s to 5.5s, verified persisted).
2. **[DONE 2026-06-21] Extended price history to 5 years** (115 tickers, 142k rows,
   2021-06 to 2026-06, verified).
3. **[DONE 2026-06-21, NEGATIVE RESULT] Clinical-success regression.** Pure-numpy
   logistic regression (`layers/composite/logreg.py`) plus trainer
   (`scripts/train_success_model.py`). Temporal holdout (n=10,127). Finding: the
   regression does NOT beat the base-rate lookup (regression AUC 0.655, stacked
   0.671 vs lookup 0.672; the lookup is also better calibrated, Brier skill 0.086 vs
   0.058-0.068). Conclusion: keep the base-rate lookup; do not wire the model into
   the scorer (it would hurt). The trainer is kept as a research tool. More accuracy
   needs NEW FEATURES (trial design, biomarker stratification, interim data), not a
   fancier model. `data/models/success_model.json` is written but unused.
4. **[DONE 2026-06-21] Wired `action_sheet` into the terminal.** Refactored to
   expose `compute_book()` / `size_book()` (pure, reusable); added the Action Desk
   page showing the risk-capped book, portfolio caps, and CSV download.
5. **[DONE 2026-06-21] Portfolio tracker and cockpit.** `portfolio_account` /
   `portfolio_holdings` tables, `layers/portfolio/tracker.py` (pure, tested), and
   the Cockpit, Portfolio and Glossary pages. Cash auto-tracked.
6. **[DONE 2026-06-21] Event-return validation set and dashboard.** Built
   `event_returns` (wired into `refresh_all.py`) plus an event-study section on the
   Validation page (return distribution, run-up-quintile chart, by-event-type
   sanity). Findings in section 5.
7. **[DONE 2026-06-21, MIXED RESULT] Signed-return regression on `event_returns`.**
   `scripts/returns_regression.py`: pure-numpy ridge, temporal split (train 1,419
   events before 2026-01-09 / test 609 after), leakage-safe price-only features
   (run-up 5/10/30/60d, realized vol 30d, log dollar-vol, dist-from-52w-high, log
   mcap-at-event).
   - **Direction: not predictable.** OOS R² -0.001 (worse than the mean baseline),
     directional hit-rate 47.6% (below a coin flip), corr +0.02. Pre-event
     technicals do not call the sign of biotech 8-K reactions (consistent with
     semi-strong efficiency). A price-based direction signal is NOT wired into the
     scorer; direction edge must come from fundamentals (the validated base-rate
     clinical model), not chart features.
   - **Magnitude: weakly predictable and useful.** OOS R² +0.019, corr +0.15.
     Predicted-big events realized 13.9% average absolute move vs 7.8% for
     predicted-small (1.8x, out of sample). Driver: small market cap plus run-up.
     Useful for sizing, not as standalone alpha.
   - **[WIRED 2026-06-21] Risk haircut in sizing.** `action_sheet.risk_haircut()` /
     `apply_risk_haircut()` shrink positions by market-cap tier (nano x0.5,
     micro/unknown x0.7, small x0.85, >=$1B x1.0) BEFORE portfolio caps. Encodes the
     magnitude finding as de-risking only; it can never enlarge a position. Config:
     `RISK_HAIRCUT_*` (toggle via `RISK_HAIRCUT_ENABLED=0`). Tested
     (`test_risk_haircut.py`).
   - Remaining levers: parse 8-K ITEM CODES (not stored; needs an EDGAR re-fetch) to
     drop routine filings; add options skew / short-interest change once historical
     snapshots accrue; a non-linear model only AFTER better features (linear is not
     the limit).
8. **[partially unblocked] Backfill `press_releases`/`documents`.** `sec_filings`
   8-K dates already give announcement anchors (see item 6). Press releases would
   still add same-day intraday granularity and non-8-K catalysts (e.g. conference
   data) but are no longer the blocker for a returns dataset.
9. **[backlog] Apply the bulk-`execute_values` fix** to any other ingest/score
   script that is slow (same pooler round-trip pattern): check `ingest_positioning`,
   `ingest_insider`, `resolve_outcomes`, `calibrate`.

---

## 7. Working principles

- Speed plus correctness, with radical transparency: silent failures, shortcuts,
  assumptions and skipped verification get reported, not hidden.
- After any change: run the affected scripts and record what actually happened (row
  counts, sample data, query results as proof), and flag silent failures and
  fallback paths.
- Scope discipline: oncology/CNS only, filtered at ingestion. Simple scripts over
  frameworks.
