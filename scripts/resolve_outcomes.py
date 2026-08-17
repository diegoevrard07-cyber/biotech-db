"""
Phase 5 - Resolve catalyst outcomes into labeled returns (the durable asset).

For each catalyst whose expected_date has passed and that has surrounding price
history, compute the pre/post move over EVENT_WINDOW_DAYS, the benchmark move,
the abnormal return, and a reaction-based hit/miss/ambiguous label. Idempotent
(ON CONFLICT (catalyst_id)).

Honest caveat: the label reflects the market's reaction, not verified trial
success. source = 'price_reaction'.
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
from layers.composite.outcomes import compute_outcome
from logger import setup_logger

log = setup_logger("resolve_outcomes")


def _close_on_or_before(conn, company_id: int | None, ticker: str | None, d) -> float | None:
    if company_id is not None:
        row = conn.execute(
            text(
                "SELECT close FROM price_history WHERE company_id = :cid AND date <= :d "
                "AND close IS NOT NULL ORDER BY date DESC LIMIT 1"
            ),
            {"cid": company_id, "d": d},
        ).scalar()
    else:
        row = conn.execute(
            text(
                "SELECT close FROM price_history WHERE ticker = :t AND company_id IS NULL "
                "AND date <= :d AND close IS NOT NULL ORDER BY date DESC LIMIT 1"
            ),
            {"t": ticker, "d": d},
        ).scalar()
    return float(row) if row is not None else None


def _close_on_or_after(conn, company_id: int | None, ticker: str | None, d) -> float | None:
    if company_id is not None:
        row = conn.execute(
            text(
                "SELECT close FROM price_history WHERE company_id = :cid AND date >= :d "
                "AND close IS NOT NULL ORDER BY date ASC LIMIT 1"
            ),
            {"cid": company_id, "d": d},
        ).scalar()
    else:
        row = conn.execute(
            text(
                "SELECT close FROM price_history WHERE ticker = :t AND company_id IS NULL "
                "AND date >= :d AND close IS NOT NULL ORDER BY date ASC LIMIT 1"
            ),
            {"t": ticker, "d": d},
        ).scalar()
    return float(row) if row is not None else None


_UPSERT = text("""
    INSERT INTO catalyst_outcomes (
        catalyst_id, company_id, resolved_date, outcome_label, pre_event_price,
        post_event_price, raw_return, benchmark_return, abnormal_return,
        event_window_days, source, notes, created_at
    ) VALUES (
        :catalyst_id, :company_id, :resolved_date, :outcome_label, :pre, :post,
        :raw_return, :benchmark_return, :abnormal_return, :win, 'price_reaction', :notes, NOW()
    )
    ON CONFLICT (catalyst_id) DO UPDATE SET
        resolved_date = EXCLUDED.resolved_date,
        outcome_label = EXCLUDED.outcome_label,
        pre_event_price = EXCLUDED.pre_event_price,
        post_event_price = EXCLUDED.post_event_price,
        raw_return = EXCLUDED.raw_return,
        benchmark_return = EXCLUDED.benchmark_return,
        abnormal_return = EXCLUDED.abnormal_return,
        event_window_days = EXCLUDED.event_window_days,
        notes = EXCLUDED.notes
    """)


def resolve(*, dry_run: bool = False, limit: int | None = None) -> dict:
    """Label past catalysts hit/miss/ambiguous from price reaction; upsert catalyst_outcomes."""
    win = config.EVENT_WINDOW_DAYS
    threshold = config.OUTCOME_MOVE_THRESHOLD
    bench = config.BENCHMARK_TICKER
    summary = {"candidates": 0, "resolved": 0, "no_price": 0, "labels": {}}

    with get_connection() as conn:
        q = """
            SELECT c.id AS catalyst_id, c.company_id, c.expected_date, co.ticker
            FROM catalysts c
            JOIN companies co ON co.id = c.company_id
            WHERE c.expected_date IS NOT NULL AND c.expected_date < CURRENT_DATE
            ORDER BY c.expected_date DESC
        """
        if limit:
            q += f" LIMIT {int(limit)}"
        rows = conn.execute(text(q)).mappings().all()

        for r in rows:
            summary["candidates"] += 1
            ed = r["expected_date"]
            cid = r["company_id"]
            pre_date = ed - timedelta(days=win)
            post_date = ed + timedelta(days=win)

            pre = _close_on_or_before(conn, cid, None, pre_date)
            post = _close_on_or_after(conn, cid, None, post_date)
            if pre is None or post is None:
                summary["no_price"] += 1
                continue

            b_pre = _close_on_or_before(conn, None, bench, pre_date)
            b_post = _close_on_or_after(conn, None, bench, post_date)

            out = compute_outcome(pre, post, b_pre, b_post, threshold=threshold)
            label = out["outcome_label"]
            summary["labels"][label] = summary["labels"].get(label, 0) + 1
            summary["resolved"] += 1

            if dry_run:
                continue
            conn.execute(
                _UPSERT,
                {
                    "catalyst_id": r["catalyst_id"],
                    "company_id": cid,
                    "resolved_date": post_date.isoformat(),
                    "outcome_label": label,
                    "pre": pre,
                    "post": post,
                    "raw_return": out["raw_return"],
                    "benchmark_return": out["benchmark_return"],
                    "abnormal_return": out["abnormal_return"],
                    "win": win,
                    "notes": f"window=+/-{win}d threshold={threshold}",
                },
            )

    print("\n=== Outcome Resolution ===")
    print(f"Past catalysts:     {summary['candidates']}")
    print(f"Resolved:           {summary['resolved']}")
    print(f"Skipped (no price): {summary['no_price']}")
    print(f"Label distribution: {summary['labels']}")
    if dry_run:
        print("(dry run - no rows written)")

    log.info(
        "resolve_complete",
        **{k: v for k, v in summary.items() if k != "labels"},
        labels=summary["labels"],
    )
    return summary


def main() -> None:
    """CLI entry: resolve past catalyst outcomes from price history."""
    parser = argparse.ArgumentParser(description="Resolve catalyst outcomes from price history")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        config.preflight()
        resolve(dry_run=args.dry_run, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        log.error("resolve_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
