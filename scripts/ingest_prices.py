"""
Phase 2 - Ingest daily OHLCV price history via yfinance.

Backfills price_history for every in-universe company with a ticker, plus the
benchmark ETF (company_id NULL) used for abnormal-return calculations later.

Idempotent (ON CONFLICT (ticker, date)). Per-ticker failures are logged and
skipped so one delisted name never aborts the batch. Supports --dry-run,
--limit, --ticker, --no-benchmark, --lookback-days.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import text

import config
from db import get_connection, get_engine
from layers.marketdata.yf_client import fetch_history_batch
from logger import setup_logger

log = setup_logger("ingest_prices")

# One bulk INSERT ... VALUES per chunk via psycopg2 execute_values. Per-row
# executemany round-trips through the connection pooler (one network hop each),
# which makes a few thousand ON CONFLICT rows take minutes; this collapses an
# entire chunk into a single statement.
_INSERT_SQL = """
    INSERT INTO price_history (
        company_id, ticker, date, open, high, low, close, adj_close, volume, source, fetched_at
    ) VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        company_id = COALESCE(EXCLUDED.company_id, price_history.company_id),
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        adj_close = EXCLUDED.adj_close,
        volume = EXCLUDED.volume,
        source = EXCLUDED.source,
        fetched_at = NOW()
"""

_VALUES_TEMPLATE = (
    "(%(company_id)s, %(ticker)s, %(date)s, %(open)s, %(high)s, %(low)s, "
    "%(close)s, %(adj_close)s, %(volume)s, %(source)s, NOW())"
)


def _bulk_upsert(conn, rows: list[dict]) -> None:
    """Single-statement upsert of many price rows through the raw psycopg2 cursor.

    Commits on the raw DBAPI connection: writes issued via a raw cursor are NOT
    part of the transaction SQLAlchemy's conn.commit() tracks, so committing at
    the SQLAlchemy level would leave them to be rolled back on close.
    """
    if not rows:
        return
    raw = conn.connection  # SQLAlchemy -> DBAPI (psycopg2) connection
    cur = raw.cursor()
    try:
        execute_values(cur, _INSERT_SQL, rows, template=_VALUES_TEMPLATE, page_size=1000)
        raw.commit()
    finally:
        cur.close()


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _rows_from_history(df: pd.DataFrame, *, company_id: int | None, ticker: str) -> list[dict]:
    rows: list[dict] = []
    for idx, r in df.iterrows():
        try:
            d = idx.date()
        except AttributeError:
            d = pd.to_datetime(idx).date()
        vol = _num(r.get("Volume"))
        rows.append(
            {
                "company_id": company_id,
                "ticker": ticker,
                "date": d.isoformat(),
                "open": _num(r.get("Open")),
                "high": _num(r.get("High")),
                "low": _num(r.get("Low")),
                "close": _num(r.get("Close")),
                "adj_close": _num(r.get("Adj Close")),
                "volume": int(vol) if vol is not None else None,
                "source": "yfinance",
            }
        )
    return rows


CHUNK_SIZE = 30


def _global_start(conn, *, lookback_days: int, since_last: bool) -> str:
    """Batch start date. Incremental mode starts just after the latest stored bar."""
    full_start = (date.today() - timedelta(days=lookback_days))
    if not since_last:
        return full_start.isoformat()
    last = conn.execute(text("SELECT MAX(date) FROM price_history")).scalar()
    if not last:
        return full_start.isoformat()
    # small overlap buffer; ON CONFLICT makes re-fetch idempotent
    inc_start = last - timedelta(days=5)
    return max(inc_start, full_start).isoformat()


def ingest(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    ticker: str | None = None,
    benchmark: bool = True,
    lookback_days: int | None = None,
    since_last: bool = False,
) -> dict:
    lookback_days = lookback_days or config.PRICE_LOOKBACK_DAYS
    summary = {"tickers": 0, "rows": 0, "empty": [], "errors": []}

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
        companies = [dict(r) for r in conn.execute(text(q), params).mappings().all()]
        start = _global_start(conn, lookback_days=lookback_days, since_last=since_last)

    print(f"Batch start date: {start} ({'incremental' if since_last else 'full'})", flush=True)

    cmap: dict[str, int | None] = {}
    order: list[str] = []
    if benchmark and not ticker:
        cmap[config.BENCHMARK_TICKER] = None
        order.append(config.BENCHMARK_TICKER)
    for co in companies:
        cmap[co["ticker"]] = co["id"]
        order.append(co["ticker"])

    chunks = [order[i:i + CHUNK_SIZE] for i in range(0, len(order), CHUNK_SIZE)]

    conn = get_engine().connect()
    try:
        for ci, chunk in enumerate(chunks, 1):
            try:
                data = fetch_history_batch(chunk, start=start)
            except Exception as exc:  # noqa: BLE001
                log.error("price_batch_failed", chunk=ci, error=str(exc))
                summary["errors"].append(f"chunk{ci}: {exc}")
                continue

            chunk_rows = 0
            batch: list[dict] = []
            for tk in chunk:
                summary["tickers"] += 1
                df = data.get(tk)
                if df is None or df.empty:
                    summary["empty"].append(tk)
                    continue
                rows = _rows_from_history(df, company_id=cmap.get(tk), ticker=tk)
                batch.extend(rows)
                chunk_rows += len(rows)
                summary["rows"] += len(rows)
            if not dry_run:
                _bulk_upsert(conn, batch)
            print(f"  chunk {ci}/{len(chunks)} ({len(chunk)} tickers): {chunk_rows} rows, "
                  f"{len(data)} with data", flush=True)
    finally:
        conn.close()

    print("\n=== Price Ingestion Summary ===")
    print(f"Tickers processed: {summary['tickers']}")
    print(f"Rows upserted:     {summary['rows']}")
    if summary["empty"]:
        print(f"No data ({len(summary['empty'])}): {', '.join(summary['empty'][:30])}")
    if summary["errors"]:
        print(f"Errors ({len(summary['errors'])}): {summary['errors'][:5]}")
    if dry_run:
        print("(dry run - no rows written)")

    log.info("price_ingest_complete", tickers=summary["tickers"], rows=summary["rows"],
             empty=len(summary["empty"]), errors=len(summary["errors"]))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest daily price history (yfinance)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ticker", type=str)
    parser.add_argument("--no-benchmark", action="store_true")
    parser.add_argument("--lookback-days", type=int)
    parser.add_argument("--since-last", action="store_true",
                        help="Incremental: only fetch bars newer than what is stored")
    args = parser.parse_args()
    try:
        config.preflight()
        summary = ingest(
            dry_run=args.dry_run, limit=args.limit, ticker=args.ticker,
            benchmark=not args.no_benchmark, lookback_days=args.lookback_days,
            since_last=args.since_last,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("ingest_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)

    # Hard-fail only if literally nothing came back.
    if summary["tickers"] > 0 and summary["rows"] == 0 and not args.dry_run:
        print("ERROR: no price rows ingested for any ticker")
        sys.exit(1)


if __name__ == "__main__":
    main()
