"""Read-only inventory of the live database: what data actually exists right now.

Used to ground presentation decisions (dashboard, report, README) in real counts
rather than schema potential. Prints a human-readable table and a JSON blob.
Makes no writes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection

QUERIES: list[tuple[str, str]] = [
    ("companies_total", "SELECT COUNT(*) FROM companies"),
    ("companies_in_universe", "SELECT COUNT(*) FROM companies WHERE in_universe"),
    ("companies_gbm", "SELECT COUNT(*) FROM companies WHERE is_gbm_focused"),
    ("trials", "SELECT COUNT(*) FROM trials"),
    ("catalysts_total", "SELECT COUNT(*) FROM catalysts"),
    ("catalysts_upcoming", "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE"),
    (
        "catalysts_next_90d",
        "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE AND expected_date <= CURRENT_DATE + 90",
    ),
    (
        "catalysts_next_180d",
        "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE AND expected_date <= CURRENT_DATE + 180",
    ),
    (
        "catalysts_by_type",
        "SELECT catalyst_type, COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE GROUP BY 1 ORDER BY 2 DESC",
    ),
    ("edge_scores_total", "SELECT COUNT(*) FROM edge_scores"),
    (
        "edge_scores_by_type",
        "SELECT trade_type, COUNT(*) FROM edge_scores GROUP BY 1 ORDER BY 2 DESC",
    ),
    ("edge_scores_latest", "SELECT MAX(computed_at) FROM edge_scores"),
    ("edge_scores_with_implied", "SELECT COUNT(*) FROM edge_scores WHERE implied_move IS NOT NULL"),
    (
        "edge_scores_with_base_rate",
        "SELECT COUNT(*) FROM edge_scores WHERE base_rate_score IS NOT NULL",
    ),
    ("catalyst_outcomes", "SELECT COUNT(*) FROM catalyst_outcomes"),
    (
        "outcomes_by_label",
        "SELECT outcome_label, COUNT(*) FROM catalyst_outcomes GROUP BY 1 ORDER BY 2 DESC",
    ),
    ("calibration_runs", "SELECT COUNT(*) FROM calibration_runs"),
    (
        "calibration_latest",
        "SELECT run_at, n_pairs, brier_score, model_hit_rate, base_rate_hit_rate FROM calibration_runs ORDER BY run_at DESC LIMIT 1",
    ),
    ("event_returns", "SELECT COUNT(*) FROM event_returns"),
    ("event_returns_events", "SELECT COUNT(DISTINCT (company_id, event_date)) FROM event_returns"),
    ("price_history_rows", "SELECT COUNT(*) FROM price_history"),
    ("price_history_tickers", "SELECT COUNT(DISTINCT ticker) FROM price_history"),
    ("price_history_latest", "SELECT MAX(date) FROM price_history"),
    ("positioning_rows", "SELECT COUNT(*) FROM positioning"),
    ("positioning_latest", "SELECT MAX(date) FROM positioning"),
    ("insider_transactions", "SELECT COUNT(*) FROM insider_transactions"),
    ("historical_trials", "SELECT COUNT(*) FROM historical_trials"),
    (
        "historical_trials_labeled",
        "SELECT COUNT(*) FROM historical_trials WHERE primary_outcome_met IS NOT NULL",
    ),
    ("base_rates", "SELECT COUNT(*) FROM base_rates"),
    ("sec_filings", "SELECT COUNT(*) FROM sec_filings"),
    ("sec_filings_8k", "SELECT COUNT(*) FROM sec_filings WHERE form_type = '8-K'"),
    ("portfolio_open", "SELECT COUNT(*) FROM portfolio_holdings WHERE status = 'open'"),
    (
        "portfolio_open_by_type",
        "SELECT side, trade_type, COUNT(*) FROM portfolio_holdings WHERE status='open' GROUP BY 1,2 ORDER BY 1,2",
    ),
    ("portfolio_closed", "SELECT COUNT(*) FROM portfolio_holdings WHERE status = 'closed'"),
    ("portfolio_cash", "SELECT cash_usd FROM portfolio_account WHERE id = 1"),
    (
        "portfolio_starting_capital",
        "SELECT starting_capital_usd FROM portfolio_account WHERE id = 1",
    ),
    ("performance_snapshots", "SELECT COUNT(*) FROM portfolio_performance"),
    (
        "performance_latest",
        "SELECT snapshot_date, equity, total_return_pct, xbi_return_pct FROM portfolio_performance ORDER BY snapshot_date DESC LIMIT 1",
    ),
    (
        "performance_first",
        "SELECT snapshot_date, equity FROM portfolio_performance ORDER BY snapshot_date ASC LIMIT 1",
    ),
]


def run() -> dict:
    """Execute every inventory query and return {name: value}. Read-only."""
    out: dict = {}
    with get_connection() as conn:
        raw = conn.connection.cursor()
        for name, sql in QUERIES:
            try:
                raw.execute(sql)
                rows = raw.fetchall()
                if len(rows) == 1 and len(rows[0]) == 1:
                    out[name] = rows[0][0]
                elif len(rows) == 1:
                    out[name] = list(rows[0])
                else:
                    out[name] = [list(r) for r in rows]
            except Exception as exc:  # noqa: BLE001 — report missing tables, don't die
                out[name] = f"ERROR: {exc}"
    return out


def main() -> None:
    """Print the inventory as aligned text plus a machine-readable JSON block."""
    config.preflight()
    inv = run()
    print("\n=== LIVE DB INVENTORY ===")
    for name, value in inv.items():
        print(f"  {name:32} {value}")
    print("\n=== JSON ===")
    print(json.dumps(inv, default=str, indent=2))


if __name__ == "__main__":
    main()
