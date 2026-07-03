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
- **macOS launchd scheduler.** `scripts/setup_launchd_macos.sh` with wake catch-up
  (Brussels: refresh 23:00, autopilot 23:30) and a KeepAlive Streamlit service on port
  8520. (Superseded by GitHub Actions for users who don't want laptop dependency.)
- **Streamlit usability.** Manual "Refresh data" button + 30s cache TTL on portfolio
  data.
