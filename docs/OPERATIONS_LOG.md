# Operations Log

A running record of operational changes to the GBM Sentiment Arbitrage system so they
can be reviewed and evaluated later. This complements `docs/AGENT_HANDOFF.md` (intent +
current state) and `.cursorrules` (coding standards): this file is a chronological
journal of **what changed, when, why, and how to evaluate it.**

## Conventions

- Newest entries at the **top**.
- Each entry: date, a short title, what changed, why, and (where relevant) how to
  verify or roll back.
- Cover anything operational: trading/risk-rule changes, scheduling/automation, data
  pipeline changes, schema migrations, and notable UI changes that affect how decisions
  are read.
- Keep it terse. One entry per logical change. Link the PR/branch when there is one.

---

## 2026-08-17 — Public-release cleanup (GitHub-ready)

**Branch/PR:** `cursor/github-ready-0203`

Repo prepared for public release. No scientific logic, parameters, or results changed.

- **Layout:** root scripts moved into `scripts/` (`apply_schema.py`, `verify.py`,
  `test_connection.py` → `check_db_connection.py`, no longer echoes the DSN host
  with credentials adjacent); ops docs moved to `docs/`.
- **Dead code removed:** 4 unreferenced one-off scripts (`_layer3_report`,
  `_layer3_status`, `_apply_triage`, `_generate_companies_seed`), 4 empty shim
  packages (`layer1_catalysts`, `layer2_science`, `layer3_base_rates`,
  `layer4_financials`), auto-generated Windows wrapper `.ps1` files with personal
  machine paths, and the superseded launchd installers (GH Actions is the venue).
- **Reproducibility:** `pyproject.toml` (pytest/black/isort), expanded `.gitignore`,
  MIT LICENSE, `data/README.md` (data provenance), `certifi` pinned; unused
  `openai`/`pdfplumber`/`lxml` moved to commented optional section.
- **CI:** `.github/workflows/tests.yml` runs pytest on push/PR; DB-backed tests
  auto-skip without `DATABASE_URL` (`tests/conftest.py`).
- **Docs:** README rewritten (results table, architecture, quickstart, limitations);
  personal paths/timezone/machine references sanitized; scheduler TZ default → UTC.
- **Docstrings:** every package `__init__` and public function documented; scorer
  decision thresholds extracted to named, justified constants (values unchanged).

**Verify:** `python -m pytest` → 180 passed, 7 skipped (DB-gated); black/isort clean;
all CLIs compile and `--help` runs.

---

## 2026-07-19 — Kill fades at source + fix long-only books

**Branch/PR:** `cursor/kill-fades-fix-books-0203`

Fades/shorts were still being *scored and shown* even with `LONG_ONLY` on, which kept
polluting the Action Desk blotter and left a path for stale checkouts / leftover DB
rows to mess up the paper book (see 2026-07-14 rogue-shorts incident).

**What changed:**
- **Scorer:** former fade rules in `decide_trade` now return `avoid`. `suggested_weight(fade)`
  is forced to `0` — no negative weights emitted.
- **Action book:** `compute_book` always drops `weight ≤ 0` (not only when `LONG_ONLY`).
- **Paper sync:** refuses negative weights, `trade_type=fade`, and non-long sides.
- **UI:** Action Desk filters / Portfolio add-trade / Strategy page no longer offer or
  document fades as a live trade. Gross-short metrics hidden in long-only mode.
- **Caps:** `MAX_GROSS_SHORT` default → `0`.
- **GH Actions:** `LONG_ONLY=1` + `MAX_GROSS_SHORT=0` pinned; autopilot covers leftovers
  before each sync.
- **One-shot fixer:** `scripts/fix_long_only_book.py` covers open shorts, strips short
  history (via `strip_shorts`), and retires stale `edge_scores` fade rows → avoid/0.

**Live DB fixed (2026-07-19):** Actions run `cover-shorts-now` #29680502632 covered
all 9 open fades (ABOS ADCT KPTI OMER ORIC QURE RGNX SLS WVE), realized −$14,
stripped 68 short history rows, restated 15 performance snapshots, retired 33
stale `edge_scores` fade rows → avoid. Proof: **zero open shorts/fades**.

**Verify:** refresh Edge Terminal Portfolio — no fade rows; open book is long-only.

---

## 2026-07-14 — Rogue shorts incident + risk-hardening package

**Branch/PR:** `cursor/risk-hardening-7e76`

**Incident:** 8 shorts (the old fade book: ABOS QURE RGNX SLS ADCT KPTI ORIC OMER)
were re-opened at 08:26 UTC by an autopilot run OUTSIDE GitHub Actions — a stale
local checkout/env without long-only active fired on machine wake. GH Actions' own
run (00:16 UTC) was clean. Covered all 8 immediately (≈$0 realized). Lesson: GitHub
Actions must be the only execution venue.

**Market context:** XBI −5.4% from its 7/10 high; portfolio −2.5% from peak
($10,943→$10,670) — half the index downside. Same-window: portfolio +6.7% vs XBI +6.5%.

**New protections (all config-gated, in `layers/portfolio/risk.py` + autopilot):**
- **Per-position stop-loss (longs):** close at EOD when marked ≤ −15% from entry
  (`STOP_LOSS_PCT`); stopped names not re-bought the same run. Fills the gap that
  the portfolio breaker alone cannot cover.
- **Graded drawdown de-risk:** tiers −6%→×0.75, −10%→×0.50 (+opens paused),
  −15%→×0.25 (`DRAWDOWN_TIERS`) — acts earlier than the old single −10% cliff.
- **Regime filter:** XBI close below its 20d SMA → all targets ×0.60
  (`REGIME_*`). Full size restored above the SMA.
- **Execution-level long-only guard:** short opens refused at execution regardless
  of upstream targets (defense-in-depth vs stale checkouts/env).
- Strategy page §9 reflects the live overlay config.

**Verify:** 187 tests pass (3 new overlay tests); autopilot `--dry-run` on live DB
shows overlays evaluated and no false triggers (worst name −10% vs −15% stop;
DD −2.5% above first tier).

---

## 2026-07-03 — Fix redesign UI overlap (stale content, chart title, toolbar)

**Branch/PR:** `cursor/dashboard-redesign-7e76` (#9)

Diagnosed with a headless-Chrome (Playwright) screenshot pass against the live app:
- **Stale-content overlap** (the "unreadable" report): on a page switch, ~37 elements
  from the previous page lingered at 0.68 opacity while the query-heavy new page loaded,
  ghosting old text over new. Fixed by hiding stale elements during rerun:
  `[data-testid="stElementContainer"][data-stale="true"] {{ display:none }}`.
- **Stray "undefined"** on the balance chart = empty Plotly `Title()` with `text=None`;
  `_plotly_theme` now always sets `text=title or ""`.
- **Streamlit toolbar** (Deploy/RUNNING) crowded the top-right nav → bumped block-container
  top padding to 2.6rem and hid the Deploy button.

Verified via before/after screenshots of all five pages + a mid-switch capture (no ghosting).

---

## 2026-07-03 — Cockpit redesign (Projection-Finance-style UI)

**Branch/PR:** `cursor/dashboard-redesign-7e76`

Full visual overhaul of the Cockpit page + global theme, modeled on the reference
screenshot. Data/logic layer untouched (same DB functions).

- **New design system** (`_inject_css`): deep-navy gradient background, rounded gradient
  cards, pill segmented-controls, restyled metrics/tabs/tables/buttons/sidebar, custom
  scrollbars, hidden default Streamlit chrome. New palette (accent #6c8cff, green
  #2fd39a, red #f76a83, purple #8b7bff).
- **Rebuilt Cockpit** (`page_home`): page header with PAPER·mode badge + info chips;
  5 KPI stat cards (start, now, total return, alpha vs XBI, drawdown risk); "Current
  balance" card with metric selector + timeframe segmented control (1W…ALL) and a
  soft-filled area chart + XBI overlay; twin allocation donuts (start → now) with a
  color legend; **kept** the Position breakdown (P&L bar) and Portfolio allocation
  (donut) charts per owner request, restyled; a tabbed Actions card (Open / Trade book /
  Closed) with ticker search and green/red P&L.
- Helpers added: `_stat_card`, `render_kpi_row`, `render_page_header`, `_bucket_donut`,
  `_alloc_legend_html`, `_cockpit_balance_chart`, `_timeframe_control`, `_rgba`.
- Restyled shared `_plotly_theme` + kept charts to transparent card backgrounds; fixed
  stale `THEME["panel"]` / hardcoded `#0b0e11` refs in calendar/validation charts.

**Verify:** 184 tests pass; headless `AppTest` render of Cockpit / Portfolio / Action
Desk / Market & Models / Strategy against the live DB → no exceptions.

---

## 2026-07-03 — Risk/reward report tool

**Branch/PR:** `cursor/risk-report-7e76`

- **`scripts/risk_report.py`**: reproducible risk/reward evaluation in three tiers —
  (1) live paper equity curve (ann. vol, Sharpe, max DD, beta vs XBI), (2) closed-trade
  distribution (expectancy, win rate, payoff, per-trade Sharpe, implied Kelly), (3) XBI
  long-run reference (ann. return/vol/Sharpe/max DD). rf=0, annualized ×√252; flags small
  samples.
- **Snapshot of current output (all young/biased, not a verdict):**
  - XBI reference (5y): +9.2%/yr, vol 32.2%, **Sharpe 0.27**, max DD −54.7%.
  - Closed longs (n=39): expectancy +6.2%/trade, win 79%, payoff 1.19, per-trade
    Sharpe 0.96 — but upward-biased (profit-lock books small wins/cuts winners; open losers
    excluded).
  - Live equity (n=5): meaningless magnitudes; beta vs XBI ~0.67 on 3 overlapping days.
  - Population event dispersion (2,113 8-K events, 3d hold): mean +1.0%, sd 22% → per-bet
    edge is tiny; portfolio edge must come from selection + diversification across events.

---

## 2026-07-03 — Fix XBI benchmark base (was overstating XBI by ~4pp)

**Branch/PR:** `cursor/fix-xbi-benchmark-base-7e76`

- **Bug:** the tracking start is the first `entry_date` (2026-06-21), a **weekend/holiday**
  with no XBI close. `performance_store._xbi_base_close` picked the last close ON/BEFORE
  (6/18 = $140.72) while the dashboard's `terminal._benchmark_base_close` picked the first
  close ON/AFTER (6/22 = $145.86). Same window, two different bases → the stored metric
  showed **XBI +14.0%** while the chart showed **+10.0%**. The 6/18 base anchors XBI three
  trading days before any position existed and flatters the benchmark.
- **Fix:** `_xbi_base_close` now prefers the first close ON/AFTER the start (matches the
  dashboard), falling back to before only if none exists. Restated the stored snapshots.
- **Corrected numbers:** XBI **+10.0%** (base $145.86). Portfolio +6.9% (post short-strip)
  → **alpha ≈ −3.1%**, not −7.2%. Still lagging, but roughly half the gap the bug implied.

**Verify:** `performance_store._xbi_base_close` returns $145.86 for track_start 2026-06-21;
latest snapshot `xbi_return_pct = 0.1001`.

---

## 2026-07-03 — Strip all historical shorts + performance analysis tools

**Branch/PR:** `cursor/strip-shorts-perf-analysis-7e76`

- **Stripped every short the paper book ever did** (`scripts/strip_shorts.py`, reversible).
  21 short rows (13 full + 8 trim closes; net realized **−$172.56**), all already closed.
  The script backs up rows to `data/backups/shorts_backup_<ts>.json` and a
  `shorts_removed_backup` table, deletes the shorts, reverses their net cash impact
  (`cash += $172.56` → $2,062.25, since a round-tripped short's net cash = its realized
  P&L), and restates the 5 `portfolio_performance` snapshots to a long-only basis.
  Result: realized **+$14.73 → +$187.29**, total return **+5.16% → +6.9%**, alpha vs XBI
  **−8.9% → ~−7.2%** (improved but XBI gap not closed — the lag is the long book vs a hot
  tape, not the shorts).
- **`scripts/base_rate_report.py`**: edge-vs-luck report — compares each closed trade's
  realized win/loss against the model's predicted catalyst base rate; reliability buckets
  + Brier score. `--all` includes pre-catalyst closes; default shows only resolved
  catalysts.

**Evaluation snapshot (all tiny-sample, ~2 weeks):**
- Realized longs: 39 closed, deployed ~$5.1k, **+$187.29 (+3.66% on cost, avg 5.8-day hold)**;
  vs XBI **+14%** over the window — realized trading has captured far less than buy-and-hold XBI.
- Calibration: **only 1 resolved catalyst pair** (Brier 0.41, n=1) — statistically empty.
- Base-rate report (resolved): n=3 decided, 67% win vs 79% predicted (Brier 0.106); high
  base-rate names won, the coin-flip lost — directionally sensible, not validated.

**Reversibility:** restore from the JSON backup or `shorts_removed_backup` table.

---

## 2026-07-01 — Long-only mode (drop shorts/fades)

**Branch/PR:** `cursor/long-only-mode-7e76`

Shorts/fades were never actually disabled — earlier discussion left `AUTOPILOT_LONG_ONLY`
unimplemented, so the autopilot kept opening `fade` shorts (8 open, the main P&L drag).
This makes long-only real:

- **`config.LONG_ONLY`** (env `LONG_ONLY`, default **ON**). Set `LONG_ONLY=0` to re-enable
  shorts.
- **`action_sheet.compute_book`**: when long-only, negative-weight (short/fade) signals are
  dropped before sizing — the capped book, Action Desk trade book, and paper autopilot all
  become long-only. Existing open shorts fall out of the book and get covered on the next
  sync (`not_in_book`).
- **`action_sheet.size_book`**: in long-only the net-exposure throttle is skipped (net =
  gross long), so deployment is governed by the gross-long cap (`MAX_GROSS_LONG`, 100%)
  instead of the `MAX_NET` 60% cap — freed short capital is redeployed into longs rather
  than sitting idle.
- **`paper_autopilot.py --cover-shorts`**: one-shot command that covers all open PAPER
  shorts at last close and frees the cash (used to flatten immediately rather than waiting
  for the daily sync).
- **Strategy page** reflects the live mode (LONG-ONLY / LONG-SHORT) and the cap change.

**One-time action:** ran `--cover-shorts` on the live DB to flatten the 8 open shorts
(net realized ≈ −$130, locking in drag that was already unrealized).

**Note (risk posture):** long-only now allows up to 100% long deployment (was ~90% net with
shorts). Lower `MAX_GROSS_LONG` to hold a larger cash buffer if desired.

**Verify:** `python -m pytest` → 184 passed (2 new long-only tests); `--cover-shorts --dry-run`
previewed all 8 covers before writing.

---

## 2026-06-30 — Strip explanatory fluff from the terminal (keep operational text)

**Branch/PR:** `cursor/remove-cockpit-honesty-banner-7e76` (#4)

Removed hand-holding captions, tips, and pure-prose metric tooltips while keeping
operational text (run commands, empty-state facts, data-bearing tooltips, legends,
freshness, drawdown, computed stats):

- **Cockpit/Portfolio:** dropped "Log a trade below / check the Action Sheet" and
  "Nothing pressing" → terse empty states; removed the "Set your starting cash…"
  setup caption; removed prose `help=` tooltips that carried no data (Account value,
  Unrealized P&L, Cash, XBI price, XBI return, Alpha). Kept tooltips that show dollar
  amounts/counts/caps.
- **Trade book / Action Desk:** removed "Capped action-desk names… Select a row…",
  "Select a row to open the full company dossier", and "Act now · capped book · all
  signals. Click a row…" tips. Trimmed the capped-book legend to "✓ = in the
  risk-capped book." Sizing note made declarative.
- **Validation:** removed the "Takeaway…", "Finding: weak barbell…", and
  "Offerings/license deals skew negative…" commentary essays; kept the computed
  `corr(...)` stat, metrics, and the build command. Trimmed chart/section titles.
- **Sidebar footer:** "Cache 30s · click Refresh after autopilot runs" → "Cache 30s".

**Why:** owner designed the system and does not need explanations; maximize operational
signal, drop fluff.

**Verify:** `python -m pytest` → 182 passed; `pyflakes scripts/terminal.py` → clean;
headless `AppTest` render of all five pages against the live DB → no exceptions.

---

## 2026-06-30 — Terminal restructure: Strategy tab, merged Research, dead-code cleanup

**Branch/PR:** `cursor/remove-cockpit-honesty-banner-7e76` (#4)

- **Merged Research surface.** Collapsed the two Research nav pages ("Market Intel" and
  "Models & Data") into a single `page_research` ("Market & Models") with tabs: Company
  dossier, Catalyst calendar, Validation, Data health, Glossary. Nav is now
  `Trade Desk: Cockpit · Portfolio · Action Desk` and `Research: Strategy · Market & Models`.
- **New Strategy tab** (`page_strategy`): a quant-grade spec of the engine — thesis,
  universe/inputs, composite grade formula, edge-gap definition, trade-type decision
  rules, sizing (fractional Kelly + market-cap risk-haircut tiers), portfolio caps, exit
  timing, risk overlays, validation/calibration, cadence, and known limitations. Reads
  parameters live from `config` + `layers.composite.scorer` so it cannot drift from the
  running system. No advisory/hint copy.
- **Dead code removed** from `scripts/terminal.py`: unused pages `page_security`,
  `page_calendar`, `page_health` (the last duplicated `_render_data_health`); unused
  `timedelta` import; unused locals `best_day`, `cid`, `caps`. Fixed the stale module
  docstring.
- **Repo-wide lint cleanup:** removed unused imports / dead locals flagged by pyflakes
  across `scripts/` and `layers/` (verify_layer3/4, fetch_eight_k_fixtures,
  seed_paper_trades, ingest_fda_approvals, dashboard, backtest, ingest_financials,
  catalyst_extractor, dedupe, indication_taxonomy, ctgov_historical, sponsor_classifier).

**Why:** declutter navigation, give a single authoritative strategy reference, and bring
the codebase to a clean health baseline.

**Verify:** `python -m pytest` → 182 passed; `python -m pyflakes scripts/ layers/` →
clean; headless `AppTest` render of Cockpit/Portfolio/Action Desk/Market & Models/Strategy
against the live DB → no exceptions.

---

## 2026-06-30 — UI cleanup: remove advisory copy from Edge Terminal

**Branch/PR:** `cursor/remove-cockpit-honesty-banner-7e76` (#4)

- Removed the "honesty banner" (*"Longs use the validated base-rate edge…"*) from every
  page (Cockpit + Portfolio); deleted the `honesty_banner()` function.
- Removed the "How to read this" chart legend help text under the Cockpit performance
  chart.
- Removed the trade-book tip caption (*"Tip: click a row in the table above…"*).
- Removed parenthetical hints from exit-rule / timing text, e.g. *(you held through it
  on purpose)*, *(sell the rumor, never hold the print)*, *(sell the news)*. Added
  `tracker.format_exit_rule()` so hints are also stripped from rules already stored in
  the DB at display time.
- Removed the sidebar caption *"Decision support · read-only"*.

**Why:** owner preference — declutter the terminal; the advisory copy is no longer
wanted on screen.

**Verify:** `python -m pytest tests/test_portfolio_tracker.py` passes; reload Streamlit
and confirm the strings are gone from Cockpit, Portfolio, and the sidebar.

---

## Earlier (pre-log) — backfilled summary

These shipped to `main` before this log existed; recorded here for completeness.

- **Cloud automation (GitHub Actions).** `daily-refresh.yml` (cron, runs
  `refresh_all.py`) and `paper-autopilot.yml` (chained via `workflow_run`, weekday-gated)
  so the pipeline no longer depends on the laptop being on. Requires repository secrets
  `DATABASE_URL` and `SEC_USER_AGENT`.
- **Risk mitigation in paper autopilot.** Drawdown circuit breaker
  (`DRAWDOWN_CIRCUIT_PCT=0.10`, de-risk ×0.5, pause new opens) and a partial
  mean-reversion profit-lock on longs (+20% gain & z-score ≥ 1.5 → trim 25%). Knobs in
  `config.py`; logic in `scripts/paper_autopilot.py` + `layers/portfolio/tracker.py`.
- **XBI benchmark.** Dedicated "Benchmark · XBI" strip and chart overlay in the Cockpit;
  `portfolio_performance` table for daily equity/XBI snapshots; autopilot writes to
  Supabase + local CSV.
- **Local schedulers (superseded).** An earlier launchd/Task-Scheduler setup ran the
  refresh + autopilot from a laptop. Superseded by GitHub Actions — a single cloud
  execution venue avoids stale-checkout incidents.
- **Streamlit usability.** Manual "Refresh data" button + 30s cache TTL on portfolio
  data.
