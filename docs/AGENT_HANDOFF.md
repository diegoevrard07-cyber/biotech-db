# PROJECT HANDBOOK — GBM / Onc-CNS Edge Engine

> Single source of truth for *intent, mental model, current state, and known traps*.
> The `.cursorrules` file holds hard coding standards; this document holds the *why*
> and the *where we are*.
>
> **Operations log:** every operational change (trading/risk rules, scheduling,
> pipeline, schema, notable UI) is recorded in `docs/OPERATIONS_LOG.md`, newest first.

---

## 1. What this project is (the goal)

A **Bloomberg-terminal-style decision-support engine** for trading small-cap
biotech (GBM-focused, broadened to oncology/CNS) around clinical/regulatory catalysts.

The design goal is an *objective, formulaic* process — the lesson of Kahneman's
*Noise* — rather than gut feel.

**Operating model = "Rung 2":** the pipeline ingests data, scores it, and proposes a
risk-capped trade book. A human reviews and executes. **No automated broker
integration. No auto-trading.** All positions are paper trades.

---

## 2. The core mental model (READ THIS — it's the whole thesis)

You make money on the **GAP between your grade and what the market has priced in.**
It is two distinct quantities, NOT one subtraction:

1. **Grade (intrinsic quality)** — no sentiment in it:
   `composite = 0.25·proximity + 0.45·base_rate + 0.30·financial`
   (catalyst nearness, trial odds, cash runway).

2. **Edge gap (the mispricing / money signal)**:
   `edge_gap = your_predicted_move − market_implied_move`
   - `edge_gap < 0` → market pays for a BIGGER move than justified → **overpriced → FADE/short**
   - `edge_gap > 0` → market UNDERPRICES the move → **underpriced → BUY/own it**

The one-line target the owner wants to converge on:
```
Trade signal = your predicted return − the return the market already priced in
  your prediction  = trial odds + financials + other factors
  market's view    = options-implied move + short interest + run-up + valuation
```

**Direction = long when you're more bullish than the crowd, short when more bearish.**
The gap is only trustworthy if the grade is accurate, so improving the grade
(base_rate especially) improves everything downstream.

**Current limitation (important):** today's `edge_gap` compares *move MAGNITUDE*
(`expected_move` vs options-implied move), not *signed direction/return*. Upgrading to
a signed-return prediction is the long-term plan (see Roadmap), but it is blocked on
data depth (see §5).

The scorer/decision logic lives in `layers/composite/scorer.py` (`decide_trade`,
`compute_edge_score`). Keep it **simple and base-rate-anchored** — the *Noise* lesson is
few factors, near-equal weights, no kitchen-sink overfitting.

---

## 3. Tech / infra realities (traps that already bit us)

- **Python 3.14, PostgreSQL 16.** Config via `.env`. See `.cursorrules` for the full
  library list. **Do not add heavy deps (sklearn/statsmodels) without asking** — we
  implement small models in pure numpy (numpy ships with pandas) to respect the stack.
- **The DB sits behind a connection pooler (pgbouncer / Supabase-style),** despite
  `.cursorrules` describing a bare local postgres. Confirmed via `pg_stat_activity`
  showing `pgbouncer.get_auth`, `LISTEN "pgrst"`.
- **TRAP #1 — per-row inserts are catastrophically slow through the pooler.**
  SQLAlchemy/psycopg2 `executemany` does one network round-trip PER ROW. A few
  thousand `ON CONFLICT` rows takes *minutes* and looks like a hang. **Fix: one bulk
  `INSERT ... VALUES` per chunk via `psycopg2.extras.execute_values`.** Applied in
  `scripts/ingest_prices.py` (`_bulk_upsert`). Other ingest/score scripts likely have
  the same pattern — fix them the same way when they're slow.
- **TRAP #2 — raw-cursor writes need a raw commit.** If you write via a raw psycopg2
  cursor (`conn.connection.cursor()`), SQLAlchemy's `conn.commit()` does NOT commit it;
  it gets rolled back on close. **You must call `raw.commit()`** (the raw DBAPI
  connection). This silently lost a full price backfill once — the script reported
  "31,317 upserted" while only 7,946 persisted. ALWAYS verify writes with an
  independent `COUNT(*)` query, never trust a script's own summary (per `.cursorrules`
  verification requirement).
- **TRAP #3 — yfinance throttling / hangs.** Yahoo rate-limits by IP. `yf_client.py`
  wraps calls with timeouts and uses batched `yf.download` (`fetch_history_batch`).
  Keep download chunks ≤ ~30 tickers. Delisted tickers print "possibly delisted" and
  return empty — that's expected, not an error.
- **TRAP #4 — catalyst refresh used DELETE+INSERT and broke on FKs (FIXED 2026-06-21).**
  `ingest_layer1._insert_catalysts` deleted all ctgov_v2 catalysts for a company then
  re-inserted. Once `edge_scores`/`portfolio_holdings`/`catalyst_outcomes` referenced
  catalysts, the DELETE raised ForeignKeyViolation → ingest_layer1 exited 1 → NO new
  catalysts ingested (silent until you check). Fix: idempotent UPSERT on the partial
  unique index `uq_catalysts_ctgov (trial_id, catalyst_type) WHERE source='ctgov_v2'`,
  keeping catalyst ids STABLE so downstream refs survive. Stale catalysts now linger
  (benign, filtered by date). `refresh_all` runs `apply_schema` first so the index is
  always present. If you add another table that FK-references catalysts, do NOT reintroduce
  a delete-replace; upsert.
- **CONCURRENTLY/lock note:** creating indexes while a `refresh_all` is running blocks on
  the refresh's long-lived pooled transactions (even CONCURRENTLY waits). Do schema/index
  changes when no pipeline is running, or let `apply_schema` (stage 0) handle them.

---

## 4. Pipeline / run order

Orchestrator: `scripts/refresh_all.py` (fail-soft, runs stages in order). Key stages:

1. `apply_schema.py` — idempotent schema (`schema.sql`).
2. Ingestion: companies/trials/catalysts/financials (existing), then
   `classify_universe.py` (oncology/CNS categories, GBM flag, small-cap filter),
   `ingest_prices.py` (OHLCV via yfinance), `ingest_positioning.py` (short interest,
   options-implied move, run-up), `ingest_insider.py` (SEC Form 4).
3. Scoring: `run_composite.py` → `edge_scores` (grade + edge_gap + trade_type + weight).
4. Outcomes/validation: `resolve_outcomes.py` (label realized catalysts from prices),
   `calibrate.py` (Brier/reliability vs outcomes), `validate_base_rates.py` (temporal
   holdout of the base-rate model), `backtest.py` (walk-forward of trade rules).
5. Output: `action_sheet.py` (risk-capped long/short book → `data/raw/action_sheet_*.csv`),
   `verify_signals.py` (data-health checks).
6. UI: `scripts/terminal.py` (Streamlit "EDGE TERMINAL" dashboard).
   Pages: **Home/Cockpit** (action center + account + top ideas with $ sizing),
   **Portfolio** (real positions tracker), Action Sheet, Trade Blotter, Security,
   Catalyst Calendar, Validation, **Glossary** (plain-language defs), Data Health.
   Run: `streamlit run scripts/terminal.py --server.port 8520`.

PORTFOLIO TRACKER (paper trading — persists to REMOTE Supabase, NOT local):
- IMPORTANT: `DATABASE_URL` points to a Supabase cloud Postgres pooler, despite
  `.cursorrules` describing a local postgres. Anything entered in the dashboard saves
  to that cloud DB and persists across restarts/devices. It is NOT private-to-machine.
  To make it truly local, repoint `DATABASE_URL` at a local Postgres.
- Tables: `portfolio_account` (singleton: cash_usd auto-adjusts on open/close),
  `portfolio_holdings` (one row per position, open/closed, linked to a catalyst for
  exit timing).
- Pure logic: `layers/portfolio/tracker.py` (valuation, P&L, cash flows, exit alerts,
  $-sizing). Sign convention: long = +shares*price asset, short = −shares*price
  liability, so equity = cash + Σ signed market values. Tested in
  `tests/test_portfolio_tracker.py`.
- Prices are END-OF-DAY (latest `price_history` close), not live.
- Exit rules: buy_the_rumor → sell ~1d BEFORE catalyst; hold_through → exit after;
  fade → cover after.

TRADING-READINESS TOOLS (added 2026-06-21):
- `scripts/liquidity_check.py` — pre-trade fill-risk for the near-term book: ADV ($ vol,
  20d), range% (spread/vol proxy), position $ at the sleeve, and % of ADV. Flags ILLIQUID
  (<$500k), TOO-BIG (>10% ADV), WIDE (range >8%). At a $10k sleeve nothing is fill-
  constrained; the live flags are volatility (ADCT/BHVN WIDE) and SAGE = no price (acquired).
- `scripts/seed_paper_trades.py` — seeds PAPER longs (buy_the_rumor/hold_through only;
  fades excluded) from the near-term book into `portfolio_holdings` (notes='PAPER'), sized
  at de-risked weight vs a paper sleeve, exit dates from `tracker.planned_exit`. Idempotent;
  `--reset` wipes PAPER + resets cash. CURRENT PAPER STATE: sleeve $10k, 7 open longs
  (ADCT/IMVT/MIST/OCUL/BHVN/CLDX/BNTX), ~$2.7k deployed, cash $7.3k. SAGE skipped (no price).
  This is the owner practicing the workflow before real money — NOT real positions.

PAPER AUTOPILOT (unattended daily run, added 2026-06-21):
- `scripts/paper_autopilot.py` — one daily cycle: refresh prices for held/candidate
  tickers, auto-EXECUTE exits (close PAPER positions whose planned_exit_date arrived at
  the latest close, book realized P&L, return cash), optionally OPEN new near-term longs,
  then append a snapshot to `data/raw/paper_performance.csv`. Fail-soft on price-fetch.
  Only touches notes='PAPER'. Flags: `--dry-run`, `--no-open`.
- Scheduling: GitHub Actions (`.github/workflows/paper-autopilot.yml`) runs it on
  weekdays after the data refresh. It writes to the same remote Supabase tables, so
  results show in the dashboard Portfolio page.

Run a single script directly (e.g. `python scripts/ingest_prices.py --lookback-days 400`).
Most support `--dry-run`, `--limit`, `--ticker`.

---

## 5. Current state (update this as you work) — as of 2026-06-21

**Data:**
- Prices: 115 tickers / ~142k rows (5-year history, 2021-06 → 2026-06). 17 universe tickers delisted (no data).
- Positioning: 113 short interest, 101 implied move, 112 run-up, 114 market caps.
- Insider: 110 companies, 6,822 Form 4 transactions.
- Catalysts: 450 (444 upcoming, 77 in next 180d, only 6 in the past → forward-looking DB).
- `catalyst_outcomes`: ~2 resolved (both ambiguous) → forward catalysts not yet resolved.
- `event_returns`: **6,084 rows** (2,028 8-K events × {1,3,5}d holds) → FIRST real signed
  abnormal-return ground truth. Built by `scripts/build_event_returns.py`. See findings below.
- `historical_trials`: 52,341 rows; **10,127 with usable `primary_outcome_met` labels.**

**Validated edge (the proven part):** base-rate model, temporal holdout
(train pre-2019 / test post-2019, n=10,127): **Brier skill +0.098, AUC 0.676, well
calibrated** across all probability buckets. This is real but modest. It predicts
*clinical trial success*, which is a FEATURE — not stock returns.

**Live book:** long-only. `action_sheet.py` drops every short/fade; scorer maps former
fade setups to `avoid`. Caps: gross long ≤100%, gross short = 0, GBM ≤25%.

**Honest risk:** the LONGS lean on the validated base-rate edge. Fades/shorts are
**retired** (unvalidated; were the main drag). Run `scripts/fix_long_only_book.py`
on the live DB if any leftover shorts or stale fade scores remain.

**Returns dataset — PARTIALLY UNBLOCKED (2026-06-21):** the earlier blocker
("no announcement dates") was wrong. `sec_filings` holds **2,028 8-K filings across 106
companies (2024-06 → 2026-06)**, all inside the 5-yr price window. 8-K dates ARE the dates
the market reacted. `scripts/build_event_returns.py` now builds `event_returns`
(6,084 rows = 2,028 events × hold {1,3,5}d) measuring realized ABNORMAL returns
(stock − XBI) + 30d pre-event run-up. This is our first REAL signed-return ground truth.
Remaining gaps: 8-Ks include routine non-catalyst noise; window is ~2yr not 5; still
survivorship-trimmed; can't reconstruct historical implied-move/short-interest so
edge_gap itself isn't backtestable yet.

**Event-study findings (objective, hold=3d, n=2,028):**
- Reactions are EXTREME variance: std ≈ 22%, ~30% of events move ≥10%, ~8% move ≥25%.
  Median ≈ −1% → most 8-Ks are noise; the edge is selection, not participation.
- "Fade the run-up" is WEAK: corr(run-up, forward abnormal) ≈ −0.04. It's a barbell —
  names that already crashed (Q1) OR already mooned (Q5) underperform; the middle drifts
  up (+2–3%). Only the most extreme run-ups give back, and only ~1%. → the scorer's
  `fade` signals are NOT strongly supported by realized data; keep them paper/half-size.
- Sign sanity holds: offerings/license deals skew negative, approvals positive →
  validates the abnormal-return math.

---

## 6. Roadmap (priority order)

1. **[DONE 2026-06-21] Fixed `run_composite.py`** per-row pooler insert → bulk `execute_values` (160s → 5.5s, verified persisted).
2. **[DONE 2026-06-21] Extended price history to 5 years** (115 tickers, 142k rows, 2021-06 → 2026-06, verified).
3. **[DONE 2026-06-21 — NEGATIVE RESULT] Clinical-success regression.** Built pure-numpy
   logistic regression (`layers/composite/logreg.py`) + trainer (`scripts/train_success_model.py`).
   Temporal holdout (n=10,127). **Finding: the regression does NOT beat the base-rate LOOKUP**
   (regression AUC 0.655, stacked 0.671 vs lookup 0.672; lookup also better calibrated /
   higher Brier skill 0.086 vs 0.058–0.068). Conclusion: **keep the base-rate lookup; do
   NOT wire the model into the scorer** (it would hurt). The trainer is kept as a research
   tool — re-run as new features/data arrive. More accuracy needs NEW FEATURES (trial design,
   biomarker stratification, interim data), not a fancier model. `data/models/success_model.json`
   is written but unused.
4. **[DONE 2026-06-21] Wired `action_sheet` into the Streamlit terminal.** Refactored
   `action_sheet.py` to expose `compute_book()` / `size_book()` (pure, reusable). Added an
   "Action Sheet" page (the landing page) in `scripts/terminal.py` showing the risk-capped
   book + portfolio caps + CSV download. Verified serving at localhost:8520.
4b. **[DONE 2026-06-21] Portfolio tracker + clean cockpit.** Added `portfolio_account`/
    `portfolio_holdings` tables, `layers/portfolio/tracker.py` (pure, tested), and the
    Home/Cockpit + Portfolio + Glossary pages. Action Center surfaces pressing exits;
    holdings show $/% / P&L; recommendations translate to $ and shares at account size.
    Cash auto-tracked. All 9 pages render clean (AppTest), 173 tests pass.

4c. **[DONE 2026-06-21] Event-return validation set + dashboard.** Discovered `sec_filings`
    already has 2,028 8-K announcement dates in-window. Built `event_returns`
    (`scripts/build_event_returns.py`, wired into `refresh_all.py`) and an Event-study
    section on the Validation page (return distribution, run-up-quintile chart, by-event-type
    sanity). Findings above. Also corrected the docs: the DB is REMOTE Supabase, not local.

5. **[DONE 2026-06-21 — MIXED RESULT] Signed-return regression on `event_returns`.**
   `scripts/returns_regression.py`: pure-numpy ridge, temporal split (train 1,419 events
   <2026-01-09 / test 609 >=), leakage-safe price-only features (run-up 5/10/30/60d,
   realized vol 30d, log dollar-vol, dist-from-52w-high, log mcap-at-event).
   - **DIRECTION: NOT predictable.** OOS R^2 −0.001 (worse than the mean baseline),
     directional hit-rate **47.6%** (below coin flip), corr +0.02. Pre-event technicals do
     NOT call the sign of biotech 8-K reactions (semi-strong efficiency). **Do NOT wire a
     price-based direction signal into the scorer — it would hurt.** Direction edge must
     come from FUNDAMENTALS (the validated base-rate clinical model), not chart features.
   - **MAGNITUDE: weakly predictable & useful.** OOS R^2 +0.019 (beats baseline),
     corr +0.15. Predicted-big events realized **13.9%** avg |move| vs **7.8%** for
     predicted-small (1.8x, OOS). Driver: small mcap + run-up.      Useful for SIZING / buying
     optionality, NOT as a standalone alpha.
   - **[WIRED 2026-06-21] Risk haircut in sizing.** `action_sheet.risk_haircut()` /
     `apply_risk_haircut()` shrink positions by market-cap tier (nano ×0.5, micro/unknown
     ×0.7, small ×0.85, ≥$1B ×1.0) BEFORE portfolio caps. Encodes the magnitude finding
     (small mcap = violent) as DE-RISKING only — it can never enlarge a position. Config:
     `RISK_HAIRCUT_*` (toggle via `RISK_HAIRCUT_ENABLED=0`). Tested (`test_risk_haircut.py`).
     Effect on current book: gross long 86.9%→82.6%, short 30%→27.7% (per-name bigger:
     tiny-caps 5%→3.5%). Owner context: satellite sleeve + discipline-tool use.
   - Remaining levers to improve: parse 8-K ITEM CODES (not stored; needs EDGAR re-fetch)
     to drop routine filings; add options skew / short-interest-change once historical
     snapshots accrue; non-linear model only AFTER better features (linear isn't the limit).
6. **[partially unblocked] Backfill `press_releases`/`documents`** — `sec_filings` 8-K dates
   already give announcement anchors (see #4c). Press releases would still add same-day
   intraday granularity and non-8-K catalysts (e.g., conference data) but are no longer the
   blocker for a returns dataset.
7. **[backlog] Apply the same bulk-`execute_values` fix** to any other ingest/score script
   that is slow (same pooler round-trip pattern): check `ingest_positioning`, `ingest_insider`,
   `resolve_outcomes`, `calibrate`.

---

## 7. Working agreements

- Speed + correctness, with radical transparency: report when something failed
  silently, when a shortcut was taken, an assumption made, or verification skipped.
- After any change: run the affected scripts, report what ACTUALLY happened (row
  counts, sample data, query results as proof), and flag silent failures / fallbacks.
- Scope discipline: GBM/onco-CNS only. Filter at ingestion. Don't over-engineer.
