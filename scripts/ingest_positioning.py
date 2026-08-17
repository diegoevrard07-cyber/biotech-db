"""
Phase 3 - Ingest objective market positioning ("sentiment") per company.

Captures, as of today, for each in-universe ticker:
  - short_interest, short_pct_float, days_to_cover   (yfinance .info)
  - implied_move_pct, atm_iv, option_expiry          (ATM straddle near next catalyst)
  - run_up_30d                                        (from price_history)
Also backfills companies.market_cap_usd from .info (enables the small-cap gate).

Every leg degrades to NULL on missing data; the row is still written so the
dashboard can show coverage. Idempotent (ON CONFLICT (company_id, date)).
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from layers.marketdata.options import compute_implied_move, pick_expiry
from layers.marketdata.yf_client import (
    fetch_info,
    fetch_option_chain,
    fetch_option_expirations,
)
from logger import setup_logger

log = setup_logger("ingest_positioning")


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _spot_from_info(info: dict) -> float | None:
    for key in ("currentPrice", "regularMarketPrice", "regularMarketPreviousClose", "previousClose"):
        v = _num(info.get(key))
        if v and v > 0:
            return v
    return None


def _run_up_30d(conn, company_id: int) -> float | None:
    rows = conn.execute(
        text(
            """
            SELECT date, close FROM price_history
            WHERE company_id = :cid AND close IS NOT NULL
              AND date >= CURRENT_DATE - INTERVAL '45 days'
            ORDER BY date
            """
        ),
        {"cid": company_id},
    ).fetchall()
    if len(rows) < 2:
        return None
    start_close = float(rows[0][1])
    end_close = float(rows[-1][1])
    if start_close <= 0:
        return None
    return round(end_close / start_close - 1.0, 6)


def _next_catalyst_date(conn, company_id: int) -> str | None:
    r = conn.execute(
        text(
            """
            SELECT MIN(expected_date) FROM catalysts
            WHERE company_id = :cid AND expected_date >= CURRENT_DATE
            """
        ),
        {"cid": company_id},
    ).scalar()
    return r.isoformat() if r else None


_UPSERT = text(
    """
    INSERT INTO positioning (
        company_id, ticker, date, short_interest, short_pct_float, days_to_cover,
        implied_move_pct, atm_iv, option_expiry, run_up_30d, source, computed_at
    ) VALUES (
        :company_id, :ticker, :date, :short_interest, :short_pct_float, :days_to_cover,
        :implied_move_pct, :atm_iv, :option_expiry, :run_up_30d, 'yfinance', NOW()
    )
    ON CONFLICT (company_id, date) DO UPDATE SET
        short_interest = EXCLUDED.short_interest,
        short_pct_float = EXCLUDED.short_pct_float,
        days_to_cover = EXCLUDED.days_to_cover,
        implied_move_pct = EXCLUDED.implied_move_pct,
        atm_iv = EXCLUDED.atm_iv,
        option_expiry = EXCLUDED.option_expiry,
        run_up_30d = EXCLUDED.run_up_30d,
        computed_at = NOW()
    """
)


def ingest(*, dry_run: bool = False, limit: int | None = None, ticker: str | None = None) -> dict:
    """Snapshot short interest, implied move, and 30d run-up per ticker into positioning."""
    today = date.today().isoformat()
    summary = {
        "tickers": 0, "with_short": 0, "with_implied_move": 0,
        "with_runup": 0, "market_caps_set": 0, "errors": [],
    }

    with get_connection() as conn:
        q = """
            SELECT id, ticker FROM companies
            WHERE ticker IS NOT NULL AND COALESCE(in_universe, TRUE) = TRUE
        """
        params: dict = {}
        if ticker:
            q += " AND ticker = :t"
            params["t"] = ticker.upper()
        q += " ORDER BY ticker"
        if limit:
            q += f" LIMIT {int(limit)}"
        companies = conn.execute(text(q), params).mappings().all()

        for co in companies:
            cid, tk = co["id"], co["ticker"]
            summary["tickers"] += 1
            try:
                info = fetch_info(tk)
                short_interest = _num(info.get("sharesShort"))
                short_pct_float = _num(info.get("shortPercentOfFloat"))
                days_to_cover = _num(info.get("shortRatio"))
                market_cap = _num(info.get("marketCap"))
                spot = _spot_from_info(info)
                if spot is None:
                    last = conn.execute(
                        text(
                            "SELECT close FROM price_history WHERE company_id = :cid "
                            "AND close IS NOT NULL ORDER BY date DESC LIMIT 1"
                        ),
                        {"cid": cid},
                    ).scalar()
                    spot = _num(last)

                # Implied move from ATM straddle near the next catalyst.
                implied = {"implied_move_pct": None, "atm_iv": None}
                expiry = None
                if spot:
                    target = _next_catalyst_date(conn, cid)
                    expirations = fetch_option_expirations(tk)
                    expiry = pick_expiry(expirations, target)
                    if expiry:
                        calls, puts = fetch_option_chain(tk, expiry)
                        implied = compute_implied_move(calls, puts, spot)

                run_up = _run_up_30d(conn, cid)

                if short_interest is not None or short_pct_float is not None:
                    summary["with_short"] += 1
                if implied.get("implied_move_pct") is not None:
                    summary["with_implied_move"] += 1
                if run_up is not None:
                    summary["with_runup"] += 1

                if not dry_run:
                    if market_cap is not None:
                        conn.execute(
                            text(
                                "UPDATE companies SET market_cap_usd = :mc, updated_at = NOW() "
                                "WHERE id = :cid"
                            ),
                            {"mc": market_cap, "cid": cid},
                        )
                        summary["market_caps_set"] += 1
                    conn.execute(
                        _UPSERT,
                        {
                            "company_id": cid, "ticker": tk, "date": today,
                            "short_interest": short_interest,
                            "short_pct_float": short_pct_float,
                            "days_to_cover": days_to_cover,
                            "implied_move_pct": implied.get("implied_move_pct"),
                            "atm_iv": implied.get("atm_iv"),
                            "option_expiry": expiry,
                            "run_up_30d": run_up,
                        },
                    )
                print(
                    f"  {tk}: short={short_interest} impl_move={implied.get('implied_move_pct')} "
                    f"runup={run_up} mcap={market_cap}"
                )
            except Exception as exc:  # noqa: BLE001
                log.error("positioning_failed", ticker=tk, error=str(exc))
                summary["errors"].append(f"{tk}: {exc}")

    print("\n=== Positioning Summary ===")
    print(f"Tickers processed:   {summary['tickers']}")
    print(f"With short data:     {summary['with_short']}")
    print(f"With implied move:   {summary['with_implied_move']}")
    print(f"With 30d run-up:     {summary['with_runup']}")
    print(f"Market caps set:     {summary['market_caps_set']}")
    if summary["errors"]:
        print(f"Errors ({len(summary['errors'])}): {summary['errors'][:5]}")
    if dry_run:
        print("(dry run - no rows written)")

    log.info("positioning_complete", **{k: v for k, v in summary.items() if k != "errors"})
    return summary


def main() -> None:
    """CLI entry: ingest positioning/sentiment data from yfinance into positioning."""
    parser = argparse.ArgumentParser(description="Ingest positioning / sentiment (yfinance)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ticker", type=str)
    args = parser.parse_args()
    try:
        config.preflight()
        ingest(dry_run=args.dry_run, limit=args.limit, ticker=args.ticker)
    except Exception as exc:  # noqa: BLE001
        log.error("ingest_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
