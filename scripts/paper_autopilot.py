"""
Paper-trading autopilot — runs the paper book WITHOUT a human (or an AI) babysitting.

Designed to be triggered by Windows Task Scheduler once per trading day. Each run:
  1. Refreshes recent prices for the tickers we hold (+ near-term candidates).
  2. Auto-EXECUTES exit rules: any open PAPER position whose planned_exit_date has
     arrived is closed at the latest close (realized P&L booked, cash returned).
  3. (optional) Opens new near-term PAPER longs to keep the book working.
  4. Appends a daily performance snapshot to data/raw/paper_performance.csv.

Fail-soft: if the price fetch fails (yfinance throttle), it still books exits on
the last known close and still snapshots — it never hard-crashes the schedule.
Only touches PAPER holdings (notes='PAPER'); real positions are never modified.

  python scripts/paper_autopilot.py              # one cycle (exits + new + snapshot)
  python scripts/paper_autopilot.py --no-open    # manage existing only, no new entries
  python scripts/paper_autopilot.py --dry-run    # show what it WOULD do
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from db import get_connection
from layers.marketdata.yf_client import fetch_history_batch
from layers.portfolio import tracker as pf
from ingest_prices import _bulk_upsert, _rows_from_history
from action_sheet import risk_haircut
from logger import setup_logger

log = setup_logger("paper_autopilot")

PERF_CSV = config.RAW_DIR / "paper_performance.csv"
LONG_TYPES = ("buy_the_rumor", "hold_through")
OPEN_WINDOW_DAYS = 60


def _refresh_prices(cur, tickers: list[str]) -> int:
    """Best-effort recent-price refresh for the given tickers. Returns rows upserted."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return 0
    cur.execute("SELECT ticker, id FROM companies WHERE ticker = ANY(%s)", (tickers,))
    cmap = {t: cid for t, cid in cur.fetchall()}
    start = (date.today() - timedelta(days=10)).isoformat()
    try:
        data = fetch_history_batch(tickers, start=start)
    except Exception as exc:  # noqa: BLE001
        log.error("autopilot_price_fetch_failed", error=str(exc))
        print(f"  price refresh failed (continuing on last known prices): {exc}")
        return 0
    rows: list[dict] = []
    for t in tickers:
        df = data.get(t)
        if df is None or df.empty:
            continue
        rows.extend(_rows_from_history(df, company_id=cmap.get(t), ticker=t))
    if rows:
        _bulk_upsert_via_cursor(cur, rows)
    return len(rows)


def _bulk_upsert_via_cursor(cur, rows: list[dict]) -> None:
    """Reuse ingest_prices' INSERT but on our existing cursor (no extra commit here)."""
    from psycopg2.extras import execute_values
    from ingest_prices import _INSERT_SQL, _VALUES_TEMPLATE
    execute_values(cur, _INSERT_SQL, rows, template=_VALUES_TEMPLATE, page_size=1000)


def _latest_closes(cur, tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    cur.execute("""
        SELECT DISTINCT ON (ticker) ticker, close
        FROM price_history WHERE close IS NOT NULL AND ticker = ANY(%s)
        ORDER BY ticker, date DESC
    """, (list(set(tickers)),))
    return {t: float(c) for t, c in cur.fetchall()}


def run(*, dry_run: bool = False, open_new: bool = True) -> None:
    today = date.today()
    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            # --- load open paper book ---
            cur.execute("""
                SELECT id, ticker, company_id, side, trade_type, shares, entry_price,
                       planned_exit_date
                FROM portfolio_holdings WHERE status='open' AND notes='PAPER'
            """)
            cols = [d[0] for d in cur.description]
            holds = [dict(zip(cols, r)) for r in cur.fetchall()]
            held_tickers = [h["ticker"] for h in holds]

            # --- candidate near-term longs (for optional new entries) ---
            cand = []
            if open_new:
                cur.execute(f"""
                    SELECT co.ticker, co.id AS company_id, c.id AS catalyst_id,
                           c.expected_date, es.trade_type, es.suggested_weight, co.market_cap_usd
                    FROM edge_scores es
                    JOIN catalysts c ON c.id = es.catalyst_id
                    JOIN companies co ON co.id = es.company_id
                    WHERE es.trade_type = ANY(%s)
                      AND es.suggested_weight IS NOT NULL AND es.suggested_weight > 0
                      AND c.expected_date BETWEEN CURRENT_DATE AND CURRENT_DATE + {OPEN_WINDOW_DAYS}
                    ORDER BY c.expected_date ASC
                """, (list(LONG_TYPES),))
                ccols = [d[0] for d in cur.description]
                cand = [dict(zip(ccols, r)) for r in cur.fetchall()]

            # --- price refresh (best effort) ---
            want = held_tickers + [c["ticker"] for c in cand]
            n_px = 0 if dry_run else _refresh_prices(cur, want)
            closes = _latest_closes(cur, want or ["XBI"])
            print(f"\n=== PAPER AUTOPILOT  {today} ===")
            print(f"Price rows refreshed: {n_px}   open positions: {len(holds)}")

            # --- account cash ---
            cur.execute("SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1")
            arow = cur.fetchone() or (0.0, None)
            cash = float(arow[0] or 0.0)
            sleeve = float(arow[1] or cash)

            # --- 1) EXECUTE DUE EXITS ---
            realized_today = 0.0
            closed = 0
            for h in holds:
                ped = h["planned_exit_date"]
                if ped is None or ped > today:
                    continue
                px = closes.get(h["ticker"])
                if px is None:
                    print(f"  [skip exit] {h['ticker']}: no price")
                    continue
                rp = pf.realized_pnl(h["side"], h["shares"], h["entry_price"], px)
                cash_delta = pf.cash_delta_on_close(h["side"], h["shares"], px)
                print(f"  EXIT {h['ticker']:<6} {h['trade_type']:<13} @ {px:.2f}  "
                      f"realized {rp:+,.0f}  (due {ped})")
                if not dry_run:
                    cur.execute("""
                        UPDATE portfolio_holdings SET status='closed', exit_date=%s,
                            exit_price=%s, realized_pnl_usd=%s, updated_at=NOW() WHERE id=%s
                    """, (today, px, round(rp, 2), h["id"]))
                cash += cash_delta
                realized_today += rp
                closed += 1
                held_tickers.remove(h["ticker"])

            # --- 2) OPEN NEW (optional) ---
            opened = 0
            if open_new:
                seen = set(held_tickers)
                for c in cand:
                    t = c["ticker"]
                    if t in seen:
                        continue
                    px = closes.get(t)
                    if not px:
                        continue
                    seen.add(t)
                    mult = risk_haircut(float(c["market_cap_usd"]) if c["market_cap_usd"] is not None else None)
                    dollars = sleeve * float(c["suggested_weight"]) * mult
                    shares = round(dollars / px, 2)
                    cost = round(shares * px, 2)
                    if shares <= 0 or cost > cash:
                        continue
                    ped, rule = pf.planned_exit(c["trade_type"], c["expected_date"])
                    print(f"  OPEN {t:<6} {c['trade_type']:<13} {shares:.2f} @ {px:.2f}  "
                          f"cost {cost:,.0f}  exit {ped}")
                    if not dry_run:
                        cur.execute("""
                            INSERT INTO portfolio_holdings
                                (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                                 shares, entry_price, cost_basis_usd, planned_exit_rule,
                                 planned_exit_date, status, notes)
                            VALUES (%s,%s,%s,'long',%s,%s,%s,%s,%s,%s,%s,'open','PAPER')
                        """, (t, c["company_id"], c["catalyst_id"], c["trade_type"], today,
                              shares, px, cost, rule, ped))
                    cash -= cost
                    opened += 1

            # --- 3) MARK TO MARKET + SNAPSHOT ---
            cur.execute("""
                SELECT ticker, side, shares, entry_price
                FROM portfolio_holdings WHERE status='open' AND notes='PAPER'
            """)
            open_after = [{"ticker": t, "side": s, "shares": float(sh), "entry_price": float(e)}
                          for t, s, sh, e in cur.fetchall()]
            # include freshly-opened (not yet committed if dry_run)
            summary = pf.account_summary(open_after, cash, closes)

            cur.execute("SELECT COALESCE(SUM(realized_pnl_usd),0) FROM portfolio_holdings "
                        "WHERE notes='PAPER' AND status='closed'")
            realized_total = float(cur.fetchone()[0] or 0.0)

            if not dry_run:
                cur.execute("UPDATE portfolio_account SET cash_usd=%s, updated_at=NOW() WHERE id=1",
                            (round(cash, 2),))
                raw.commit()

            equity = summary["equity"]
            ret_pct = (equity - sleeve) / sleeve if sleeve else 0.0
            print(f"\n  Exits: {closed}  New: {opened}  Open now: {summary['positions']}")
            print(f"  Cash ${cash:,.0f} | Equity ${equity:,.0f} | "
                  f"Unrealized {summary['unrealized_pnl_usd']:+,.0f} | "
                  f"Realized-to-date {realized_total:+,.0f} | "
                  f"Total return {ret_pct:+.1%} vs ${sleeve:,.0f} sleeve")

            if not dry_run:
                PERF_CSV.parent.mkdir(parents=True, exist_ok=True)
                new_file = not PERF_CSV.exists()
                with open(PERF_CSV, "a", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    if new_file:
                        w.writerow(["date", "equity", "cash", "open_positions", "unrealized_pnl",
                                    "realized_to_date", "total_return_pct", "exits_today", "opens_today"])
                    w.writerow([today, round(equity, 2), round(cash, 2), summary["positions"],
                                summary["unrealized_pnl_usd"], round(realized_total, 2),
                                round(ret_pct, 4), closed, opened])
                print(f"  Snapshot appended -> {PERF_CSV}")
            else:
                print("  (dry run — nothing written)")
        finally:
            cur.close()

    log.info("autopilot_done", exits=closed, opens=opened, equity=round(summary["equity"], 2),
             realized_total=round(realized_total, 2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper-trading autopilot (one daily cycle)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-open", action="store_true", help="manage existing only; do not open new")
    args = ap.parse_args()
    try:
        config.preflight()
        run(dry_run=args.dry_run, open_new=not args.no_open)
    except Exception as exc:  # noqa: BLE001
        log.error("autopilot_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
