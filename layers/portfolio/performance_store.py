"""Persist portfolio performance snapshots to Supabase (cross-device sync)."""

from __future__ import annotations

from datetime import date
from typing import Any

import config

_UPSERT_SQL = """
    INSERT INTO portfolio_performance (
        snapshot_date, equity, cash, open_positions, unrealized_pnl,
        realized_to_date, total_return_pct, exits_today, opens_today,
        resized_today, desk_positions, xbi_close, xbi_return_pct, benchmark_equity
    ) VALUES (
        %(snapshot_date)s, %(equity)s, %(cash)s, %(open_positions)s, %(unrealized_pnl)s,
        %(realized_to_date)s, %(total_return_pct)s, %(exits_today)s, %(opens_today)s,
        %(resized_today)s, %(desk_positions)s, %(xbi_close)s, %(xbi_return_pct)s,
        %(benchmark_equity)s
    )
    ON CONFLICT (snapshot_date) DO UPDATE SET
        equity = EXCLUDED.equity,
        cash = EXCLUDED.cash,
        open_positions = EXCLUDED.open_positions,
        unrealized_pnl = EXCLUDED.unrealized_pnl,
        realized_to_date = EXCLUDED.realized_to_date,
        total_return_pct = EXCLUDED.total_return_pct,
        exits_today = EXCLUDED.exits_today,
        opens_today = EXCLUDED.opens_today,
        resized_today = EXCLUDED.resized_today,
        desk_positions = EXCLUDED.desk_positions,
        xbi_close = EXCLUDED.xbi_close,
        xbi_return_pct = EXCLUDED.xbi_return_pct,
        benchmark_equity = EXCLUDED.benchmark_equity,
        updated_at = NOW()
"""


def _xbi_base_close(cur, track_start: date) -> float | None:
    """Benchmark base = first close ON/AFTER the tracking start (the first day you
    could actually have deployed into XBI), falling back to the last close before it
    only if none exists after. Must match terminal._benchmark_base_close so the stored
    metric and the dashboard chart agree — otherwise a weekend/holiday start date makes
    them pick different bases (e.g. 6/18 vs 6/22) and report different XBI returns."""
    cur.execute(
        """
        SELECT close FROM price_history
        WHERE ticker = %s AND close IS NOT NULL AND date >= %s
        ORDER BY date ASC LIMIT 1
        """,
        (config.BENCHMARK_TICKER, track_start),
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    cur.execute(
        """
        SELECT close FROM price_history
        WHERE ticker = %s AND close IS NOT NULL AND date <= %s
        ORDER BY date DESC LIMIT 1
        """,
        (config.BENCHMARK_TICKER, track_start),
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def tracking_start_date(cur) -> date | None:
    cur.execute("SELECT MIN(entry_date) FROM portfolio_holdings")
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    cur.execute("SELECT MIN(snapshot_date) FROM portfolio_performance")
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def benchmark_fields(
    cur,
    *,
    xbi_close: float | None,
    starting_capital: float | None,
    track_start: date | None,
) -> tuple[float | None, float | None]:
    """Return (xbi_return_pct, benchmark_equity) normalized to starting capital."""
    if xbi_close is None or not starting_capital or starting_capital <= 0 or not track_start:
        return None, None
    base = _xbi_base_close(cur, track_start)
    if base is None or base <= 0:
        return None, None
    xbi_ret = (xbi_close / base) - 1.0
    bench_equity = starting_capital * (xbi_close / base)
    return round(xbi_ret, 6), round(bench_equity, 2)


def upsert_snapshot(cur, row: dict[str, Any]) -> None:
    cur.execute(_UPSERT_SQL, row)


def load_history(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT snapshot_date, equity, cash, open_positions, unrealized_pnl,
               realized_to_date, total_return_pct, exits_today, opens_today,
               resized_today, desk_positions, xbi_close, xbi_return_pct, benchmark_equity
        FROM portfolio_performance
        ORDER BY snapshot_date
        """
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
