# Operations

How the pipeline runs unattended. **Use exactly one execution venue**: running both
cloud and local schedulers double-trades the paper book (this actually happened once;
see `docs/OPERATIONS_LOG.md`, 2026-07-14).

## Cloud automation (GitHub Actions, the supported venue)

Because all state lives in Postgres, the autopilot runs serverless on GitHub Actions,
no laptop required. See `.github/workflows/`:

| Workflow | Trigger | Runs |
|----------|---------|------|
| `daily-refresh.yml` | cron daily 22:00 UTC (after US close) | `refresh_all.py` (keeps signals fresh) |
| `paper-autopilot.yml` | **after** the refresh completes, weekdays only | `paper_autopilot.py` (syncs the PAPER book) |
| `cover-shorts-now.yml` | push / manual | one-shot long-only repair (cover + strip + retire fades) |
| `tests.yml` | push / PR | pytest |

The autopilot is **chained** to the refresh (`workflow_run`), so trades always run on
fresh data no matter how long the refresh takes, with no fixed-gap race. The refresh runs
every day (research data stays current); the autopilot gates itself to weekdays.

**Why once a day, not every few minutes:** every signal here is end-of-day granularity
(prices use daily closes; SEC/CT.gov change slowly). Polling more often adds no signal,
risks rate-limit bans from yfinance/SEC, and burns Actions minutes. Once daily after
the close is optimal.

**Setup (one time):** add repo secrets under *Settings → Secrets and variables →
Actions*: `DATABASE_URL` (both jobs) and `SEC_USER_AGENT` (refresh only, for SEC
EDGAR). `POE_API_KEY` is **not** needed: the Layer-2 council is scaffolded but not
wired into the active pipeline. All workflows also have a manual *Run workflow* button
(`workflow_dispatch`).

Caveats: GitHub Actions cron is best-effort (can be delayed minutes) and scheduled
workflows auto-disable after ~60 days of repo inactivity (re-enable in the Actions
tab). For a once-daily paper job this is fine.

## Local scheduling (optional alternative)

Linux cron installer:

```bash
./scripts/setup_scheduler.sh            # install (defaults: refresh 23:00, autopilot 23:30 UTC)
./scripts/setup_scheduler.sh --dry-run  # preview
./scripts/setup_scheduler.sh --remove   # uninstall
```

Windows Task Scheduler (run from an elevated PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1
```

which registers `EdgeEngineRefresh` (daily) and `BiotechPaperAutopilot` (weekdays),
equivalent to:

```powershell
schtasks /Create /SC DAILY /TN "EdgeEngineRefresh" /ST 18:00 ^
  /TR "cmd /c cd /d C:\path\to\biotech-db && python scripts\refresh_all.py >> data\logs\refresh.out 2>&1"
```

`refresh_all.py` continues past non-critical ingest failures and exits non-zero if any
stage fails, so the scheduler can surface problems.

## Dashboard hosting

To host the terminal 24/7, deploy `scripts/terminal.py` to **Streamlit Community
Cloud** (free) and add the same secrets there. Locally:

```bash
streamlit run scripts/terminal.py            # Edge Terminal (current UI)
```

The legacy `scripts/dashboard.py` (the original five-page read-only dashboard) was
removed in the 2026-08 cleanup (superseded by the terminal); recoverable from git
history if ever needed.

## Risk mitigation overlays (paper autopilot)

Two dynamic, end-of-day risk controls run inside `paper_autopilot.py` (all
configurable in `.env`, all reduce risk only):

| Overlay | What it does | Key knobs (defaults) |
|---------|--------------|----------------------|
| **Drawdown circuit breaker** | If equity falls > X% below its peak, shrink all targets and pause new opens until it recovers (catches correlated sector selloffs). | `DRAWDOWN_CIRCUIT_PCT=0.10`, `DRAWDOWN_DERISK_FACTOR=0.5` |
| **Partial profit-lock (mean reversion)** | Scale OUT a fraction of a LONG winner when it is both in profit AND stretched above its short-term mean (z-score). Keeps a core into the catalyst; skips names with a catalyst within N days. | `PROFIT_LOCK_GAIN_PCT=0.20`, `PROFIT_LOCK_TRIM_FRACTION=0.25`, `PROFIT_LOCK_ZSCORE=1.5`, `PROFIT_LOCK_MIN_DAYS_TO_CATALYST=3` |

Additional overlays added 2026-07-14 (see `docs/OPERATIONS_LOG.md`): per-position
stop-loss (`STOP_LOSS_PCT=0.15`), graded drawdown tiers (`DRAWDOWN_TIERS`), and an
XBI regime filter (`REGIME_SMA_DAYS=20`, `REGIME_DERISK_FACTOR=0.60`).

Disable any of them with `*_ENABLED=0`. Note: these are EOD-based; they mitigate multi-day
slides and over-extension, not single-name overnight gaps (the 5% per-name cap covers
that).
