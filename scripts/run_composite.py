"""Compute and upsert composite edge_scores for upcoming catalysts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from psycopg2.extras import execute_values
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db import get_connection
from layers.composite.scorer import ScoreInputs, compute_edge_score
from logger import setup_logger

log = setup_logger("run_composite")


def _f(value) -> float | None:
    return float(value) if value is not None else None


# Bulk upsert via execute_values: per-row executemany round-trips through the
# connection pooler one statement at a time (minutes for a few hundred rows).
# Commit on the RAW connection — SQLAlchemy's conn.commit() does not cover raw
# cursor writes and would let them roll back on close. (See AGENT_HANDOFF.md.)
_EDGE_SQL = """
    INSERT INTO edge_scores (
        company_id, catalyst_id,
        catalyst_proximity_score, science_score, base_rate_score,
        financial_score, composite_score, confidence, weights_json,
        trade_type, expected_move, implied_move, edge_gap,
        financing_tilt, insider_tilt, suggested_weight,
        rationale, computed_at
    ) VALUES %s
    ON CONFLICT (catalyst_id) DO UPDATE SET
        catalyst_proximity_score = EXCLUDED.catalyst_proximity_score,
        science_score = EXCLUDED.science_score,
        base_rate_score = EXCLUDED.base_rate_score,
        financial_score = EXCLUDED.financial_score,
        composite_score = EXCLUDED.composite_score,
        confidence = EXCLUDED.confidence,
        weights_json = EXCLUDED.weights_json,
        trade_type = EXCLUDED.trade_type,
        expected_move = EXCLUDED.expected_move,
        implied_move = EXCLUDED.implied_move,
        edge_gap = EXCLUDED.edge_gap,
        financing_tilt = EXCLUDED.financing_tilt,
        insider_tilt = EXCLUDED.insider_tilt,
        suggested_weight = EXCLUDED.suggested_weight,
        rationale = EXCLUDED.rationale,
        computed_at = NOW()
"""
_EDGE_TEMPLATE = (
    "(%(company_id)s, %(catalyst_id)s, %(proximity)s, %(science)s, %(base_rate)s, "
    "%(financial)s, %(composite)s, %(confidence)s, CAST(%(weights)s AS jsonb), "
    "%(trade_type)s, %(expected_move)s, %(implied_move)s, %(edge_gap)s, "
    "%(financing_tilt)s, %(insider_tilt)s, %(suggested_weight)s, %(rationale)s, NOW())"
)
_HIST_SQL = """
    INSERT INTO score_history (
        catalyst_id, composite_score,
        layer1_score, layer3_score, layer4_score,
        layer_breakdown, computed_at
    ) VALUES %s
"""
_HIST_TEMPLATE = (
    "(%(catalyst_id)s, %(composite)s, %(proximity)s, %(base_rate)s, "
    "%(financial)s, CAST(%(breakdown)s AS jsonb), NOW())"
)


def run_composite(*, dry_run: bool = False, limit: int | None = None) -> dict:
    stats = {"scored": 0, "skipped": 0, "trade_types": {}}

    sql = """
        SELECT c.id AS catalyst_id, c.company_id, c.expected_date, c.base_rate,
               COALESCE(c.sec_confirmed, FALSE) AS sec_confirmed,
               c.date_confidence,
               COALESCE(c.requires_manual_verification, FALSE) AS requires_manual_verification,
               f.runway_months, f.quarterly_burn_usd AS quarterly_burn,
               p.implied_move_pct, p.run_up_30d, p.short_pct_float,
               ins.net_buy_usd,
               COALESCE(off.cnt, 0) > 0 AS recent_offering
        FROM catalysts c
        LEFT JOIN LATERAL (
            SELECT runway_months, quarterly_burn_usd
            FROM financials
            WHERE company_id = c.company_id
            ORDER BY period_end DESC
            LIMIT 1
        ) f ON TRUE
        LEFT JOIN LATERAL (
            SELECT implied_move_pct, run_up_30d, short_pct_float
            FROM positioning
            WHERE company_id = c.company_id
            ORDER BY date DESC
            LIMIT 1
        ) p ON TRUE
        LEFT JOIN LATERAL (
            SELECT SUM(CASE WHEN is_purchase THEN value_usd ELSE -value_usd END) AS net_buy_usd
            FROM insider_transactions
            WHERE company_id = c.company_id
              AND filing_date >= CURRENT_DATE - INTERVAL '180 days'
        ) ins ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS cnt
            FROM material_events
            WHERE company_id = c.company_id
              AND event_type = 'offering'
              AND filing_date >= CURRENT_DATE - INTERVAL '180 days'
        ) off ON TRUE
        WHERE c.expected_date IS NOT NULL
           OR c.catalyst_type IN ('pdufa', 'advisory_committee', 'phase_readout')
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    edge_rows: list[dict] = []
    hist_rows: list[dict] = []

    with get_connection() as conn:
        rows = conn.execute(text(sql)).mappings().all()

        for row in rows:
            inputs = ScoreInputs(
                catalyst_id=row["catalyst_id"],
                company_id=row["company_id"],
                expected_date=row["expected_date"],
                base_rate=_f(row["base_rate"]),
                runway_months=_f(row["runway_months"]),
                quarterly_burn=_f(row["quarterly_burn"]),
                sec_confirmed=bool(row["sec_confirmed"]),
                implied_move=_f(row["implied_move_pct"]),
                run_up_30d=_f(row["run_up_30d"]),
                short_pct_float=_f(row["short_pct_float"]),
                net_insider_buy_usd=_f(row["net_buy_usd"]),
                recent_offering=bool(row["recent_offering"]),
                date_confidence=row["date_confidence"],
                requires_manual_verification=bool(row["requires_manual_verification"]),
            )
            scores = compute_edge_score(
                inputs,
                kelly_fraction=config.KELLY_FRACTION,
                max_weight=config.MAX_SINGLE_NAME_WEIGHT,
            )
            tt = scores["trade_type"]
            stats["trade_types"][tt] = stats["trade_types"].get(tt, 0) + 1

            if dry_run:
                print(f"DRY RUN catalyst {inputs.catalyst_id}: composite={scores['composite_score']}")
                stats["scored"] += 1
                continue

            edge_rows.append({
                "company_id": inputs.company_id,
                "catalyst_id": inputs.catalyst_id,
                "proximity": scores["catalyst_proximity_score"],
                "science": scores["science_score"],
                "base_rate": scores["base_rate_score"],
                "financial": scores["financial_score"],
                "composite": scores["composite_score"],
                "confidence": scores["confidence"],
                "weights": json.dumps(scores["weights_json"]),
                "trade_type": scores["trade_type"],
                "expected_move": scores["expected_move"],
                "implied_move": scores["implied_move"],
                "edge_gap": scores["edge_gap"],
                "financing_tilt": scores["financing_tilt"],
                "insider_tilt": scores["insider_tilt"],
                "suggested_weight": scores["suggested_weight"],
                "rationale": scores["rationale"],
            })
            hist_rows.append({
                "catalyst_id": inputs.catalyst_id,
                "composite": scores["composite_score"],
                "proximity": scores["catalyst_proximity_score"],
                "base_rate": scores["base_rate_score"],
                "financial": scores["financial_score"],
                "breakdown": json.dumps(scores),
            })
            stats["scored"] += 1

        if not dry_run and edge_rows:
            raw = conn.connection
            cur = raw.cursor()
            try:
                execute_values(cur, _EDGE_SQL, edge_rows, template=_EDGE_TEMPLATE, page_size=500)
                execute_values(cur, _HIST_SQL, hist_rows, template=_HIST_TEMPLATE, page_size=500)
                raw.commit()
            finally:
                cur.close()

    log.info("run_composite_complete", scored=stats["scored"], skipped=stats["skipped"],
             trade_types=stats["trade_types"])
    print(f"Composite: scored={stats['scored']}, skipped={stats['skipped']}")
    print(f"Trade types: {stats['trade_types']}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run_composite(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
