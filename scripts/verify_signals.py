"""
Phase 10 - Data-health checks for the Rung 2 signal tables.

Reports coverage, freshness, and orphans for price_history, positioning,
insider_transactions, catalyst_outcomes, and edge_scores decision fields.
Read-only. Exits non-zero only on a structural problem (no edge scores at all).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from logger import setup_logger

log = setup_logger("verify_signals")


def _scalar(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def verify() -> dict:
    out: dict = {}
    with get_connection() as conn:
        universe = _scalar(
            conn,
            "SELECT COUNT(*) FROM companies WHERE ticker IS NOT NULL "
            "AND COALESCE(in_universe, TRUE)",
        )
        out["universe"] = universe
        out["gbm_focused"] = _scalar(conn, "SELECT COUNT(*) FROM companies WHERE is_gbm_focused")

        with_prices = _scalar(
            conn, "SELECT COUNT(DISTINCT company_id) FROM price_history WHERE company_id IS NOT NULL"
        )
        with_pos = _scalar(conn, "SELECT COUNT(DISTINCT company_id) FROM positioning")
        with_ins = _scalar(conn, "SELECT COUNT(DISTINCT company_id) FROM insider_transactions")
        out["price_rows"] = _scalar(conn, "SELECT COUNT(*) FROM price_history")
        out["positioning_rows"] = _scalar(conn, "SELECT COUNT(*) FROM positioning")
        out["insider_rows"] = _scalar(conn, "SELECT COUNT(*) FROM insider_transactions")
        out["outcomes"] = _scalar(conn, "SELECT COUNT(*) FROM catalyst_outcomes")
        out["edge_scores"] = _scalar(conn, "SELECT COUNT(*) FROM edge_scores")
        out["edge_with_trade_type"] = _scalar(
            conn, "SELECT COUNT(*) FROM edge_scores WHERE trade_type IS NOT NULL"
        )
        out["edge_with_implied_move"] = _scalar(
            conn, "SELECT COUNT(*) FROM edge_scores WHERE implied_move IS NOT NULL"
        )

        def pct(n: int) -> str:
            return f"{(100*n/universe):.0f}%" if universe else "n/a"

        print("\n=== Signal Health ===")
        print(f"In-universe tickers:        {universe}")
        print(f"GBM flagship:               {out['gbm_focused']}")
        print(f"Price coverage:             {with_prices}/{universe} ({pct(with_prices)})  rows={out['price_rows']}")
        print(f"Positioning coverage:       {with_pos}/{universe} ({pct(with_pos)})  rows={out['positioning_rows']}")
        print(f"Insider coverage:           {with_ins}/{universe} ({pct(with_ins)})  rows={out['insider_rows']}")
        print(f"Resolved outcomes:          {out['outcomes']}")
        print(f"Edge scores:                {out['edge_scores']} (trade_type={out['edge_with_trade_type']}, "
              f"implied_move={out['edge_with_implied_move']})")

        print("\nTrade-type distribution:")
        for r in conn.execute(text(
            "SELECT trade_type, COUNT(*) n FROM edge_scores WHERE trade_type IS NOT NULL "
            "GROUP BY trade_type ORDER BY n DESC"
        )):
            print(f"  {r[0]:<16} {r[1]}")

        print("\nFreshness (max timestamp):")
        for tbl, col in [("price_history", "fetched_at"), ("positioning", "computed_at"),
                         ("insider_transactions", "created_at"), ("edge_scores", "computed_at"),
                         ("catalyst_outcomes", "created_at")]:
            ts = conn.execute(text(f"SELECT MAX({col}) FROM {tbl}")).scalar()
            print(f"  {tbl:<22} {ts}")

        # Warnings
        no_price = _scalar(
            conn,
            "SELECT COUNT(*) FROM companies co WHERE co.ticker IS NOT NULL "
            "AND COALESCE(co.in_universe, TRUE) AND NOT EXISTS "
            "(SELECT 1 FROM price_history p WHERE p.company_id = co.id)",
        )
        if no_price:
            print(f"\nWARNING: {no_price} in-universe tickers have no price history "
                  "(likely delisted/acquired).")

    log.info("verify_signals_complete", **out)
    if out["edge_scores"] == 0:
        print("\nERROR: no edge scores present.")
        sys.exit(1)
    return out


def main() -> None:
    try:
        config.preflight()
        verify()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("verify_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
