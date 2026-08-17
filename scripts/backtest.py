"""
Phase 8 - Walk-forward event backtest of the trade rules.

For each resolved catalyst, the trade decision is RE-derived as of `--lead-days`
before the event using only data computable at that time:
  - proximity   : as if the decision were made lead_days before the event
  - base_rate   : catalysts.base_rate (historical-ish, stable)
  - run_up_30d  : price return from (event-60d) to (event-30d), known at decision time
Then the realized return is simulated per trade type:
  - buy_the_rumor : enter ~30d pre-event, EXIT ~1d pre-event (sell the news)
  - hold_through  : raw return across the event window (catalyst_outcomes)
  - fade          : RETIRED (former fade setups now avoid; not backtested)
  - avoid         : skipped

Honest caveats (printed): financing/positioning are NOT historical, so they are
omitted from the backtest decision; implied move is unavailable historically.
Results are a directional sanity check, not a promise. Slippage is applied as a
flat per-trade haircut (--slippage).
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from layers.composite.backtest_metrics import summarize
from layers.composite.scorer import (
    BUY_THE_RUMOR,
    HOLD_THROUGH,
    decide_trade,
    score_base_rate,
    score_proximity,
    suggested_weight,
)
from logger import setup_logger

log = setup_logger("backtest")


def _close_before(conn, cid, d):
    return conn.execute(
        text(
            "SELECT close FROM price_history WHERE company_id = :cid AND date <= :d "
            "AND close IS NOT NULL ORDER BY date DESC LIMIT 1"
        ),
        {"cid": cid, "d": d},
    ).scalar()


def _ret(a, b):
    if a is None or b is None:
        return None
    a, b = float(a), float(b)
    return (b / a - 1.0) if a > 0 else None


def backtest(*, lead_days: int = 30, slippage: float = 0.005, csv_path: str | None = None) -> dict:
    """Replay resolved catalysts with as-of decision rules; return summary metrics."""
    trade_returns: list[float] = []
    weighted_returns: list[float] = []
    by_type: dict[str, int] = {}
    rows_out: list[dict] = []

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT o.catalyst_id, o.company_id, o.raw_return, c.expected_date,
                       c.base_rate, co.ticker
                FROM catalyst_outcomes o
                JOIN catalysts c ON c.id = o.catalyst_id
                JOIN companies co ON co.id = o.company_id
                WHERE o.raw_return IS NOT NULL AND c.expected_date IS NOT NULL
                ORDER BY c.expected_date
                """
            )
        ).mappings().all()

        for r in rows:
            ed = r["expected_date"]
            cid = r["company_id"]
            base = score_base_rate(float(r["base_rate"]) if r["base_rate"] is not None else None)

            # Decision as-of lead_days before the event.
            decision_day = ed - timedelta(days=lead_days)
            proximity = score_proximity(ed, today=decision_day)

            # Historical run-up known at decision time: (event-60d) -> (event-30d).
            ru_start = _close_before(conn, cid, ed - timedelta(days=60))
            ru_end = _close_before(conn, cid, ed - timedelta(days=lead_days))
            run_up = _ret(ru_start, ru_end)

            trade_type = decide_trade(
                proximity=proximity, base=base, fin_tilt=0.0,
                run_up_30d=run_up, edge_gap=None,
            )
            if trade_type not in (BUY_THE_RUMOR, HOLD_THROUGH):
                continue  # avoid (incl. former fades) — long-only backtest

            # Realized return per trade type (longs only).
            if trade_type == BUY_THE_RUMOR:
                entry = _close_before(conn, cid, ed - timedelta(days=lead_days))
                exit_ = _close_before(conn, cid, ed - timedelta(days=1))
                raw = _ret(entry, exit_)
            else:  # HOLD_THROUGH
                raw = float(r["raw_return"])

            if raw is None:
                continue

            directional = raw - slippage
            w = suggested_weight(
                trade_type, base=base, proximity=proximity,
                kelly_fraction=config.KELLY_FRACTION, max_weight=config.MAX_SINGLE_NAME_WEIGHT,
            )
            weighted = abs(w) * directional

            trade_returns.append(directional)
            weighted_returns.append(weighted)
            by_type[trade_type] = by_type.get(trade_type, 0) + 1
            rows_out.append(
                {
                    "ticker": r["ticker"], "expected_date": ed.isoformat(),
                    "trade_type": trade_type, "directional_return": round(directional, 4),
                    "weight": w, "weighted_return": round(weighted, 5),
                }
            )

    metrics = summarize(trade_returns, weighted_returns)

    print("\n=== Walk-forward Backtest ===")
    print(f"Lead days: {lead_days}  Slippage/trade: {slippage}")
    print(f"Trades by type: {by_type}")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    if metrics.get("n_trades", 0) == 0:
        print("NOTE: no tradeable resolved catalysts yet. Run ingest_prices.py + "
              "resolve_outcomes.py first; the backtest needs price + outcome history.")

    if csv_path and rows_out:
        import csv
        out = Path(csv_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"Wrote {len(rows_out)} trades to {out}")

    log.info("backtest_complete", lead_days=lead_days, by_type=by_type, **metrics)
    return metrics


def main() -> None:
    """CLI entry: run the walk-forward backtest over resolved catalyst outcomes."""
    parser = argparse.ArgumentParser(description="Walk-forward event backtest")
    parser.add_argument("--lead-days", type=int, default=30)
    parser.add_argument("--slippage", type=float, default=0.005)
    parser.add_argument("--csv", type=str, help="Optional path to write per-trade CSV")
    args = parser.parse_args()
    try:
        config.preflight()
        backtest(lead_days=args.lead_days, slippage=args.slippage, csv_path=args.csv)
    except Exception as exc:  # noqa: BLE001
        log.error("backtest_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
