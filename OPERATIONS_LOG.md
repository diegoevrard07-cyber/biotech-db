# Operations Log

A running record of operational changes to the GBM Sentiment Arbitrage system so they
can be reviewed and evaluated later. This complements `AGENT_HANDOFF.md` (intent +
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
- **macOS launchd scheduler.** `scripts/setup_launchd_macos.sh` with wake catch-up
  (Brussels: refresh 23:00, autopilot 23:30) and a KeepAlive Streamlit service on port
  8520. (Superseded by GitHub Actions for users who don't want laptop dependency.)
- **Streamlit usability.** Manual "Refresh data" button + 30s cache TTL on portfolio
  data.
