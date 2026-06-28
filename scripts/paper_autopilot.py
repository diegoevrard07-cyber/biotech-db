"""
Paper-trading autopilot — syncs the PAPER book to the capped Action Desk daily.

Designed for Windows Task Scheduler (weekday evenings). Each run:
  1. Builds the risk-capped action book (same logic as Action Desk / action_sheet.py).
  2. Refreshes prices for held + target tickers.
  3. Closes positions: past exit date, dropped from book, or side/trade flip.
  4. Opens new names and rebalances existing ones toward target weights.
  5. Appends a daily performance snapshot to data/raw/paper_performance.csv.

Only touches notes='PAPER' holdings. Longs AND shorts (fades) follow the action desk.

  python scripts/paper_autopilot.py                 # sync to action desk + snapshot
  python scripts/paper_autopilot.py --dry-run       # show planned trades
  python scripts/paper_autopilot.py --exits-only    # close due exits only, no sync
  python scripts/paper_autopilot.py --horizon-days 90
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
from layers.portfolio import paper_sync as ps
from layers.portfolio import performance_store as perf_store
from layers.portfolio import tracker as pf
from ingest_prices import _rows_from_history
from action_sheet import compute_book
from logger import setup_logger

log = setup_logger("paper_autopilot")

PERF_CSV = config.RAW_DIR / "paper_performance.csv"
PERF_COLUMNS = [
    "date", "equity", "cash", "open_positions", "unrealized_pnl",
    "realized_to_date", "total_return_pct", "exits_today", "opens_today",
    "resized_today", "desk_positions",
]


def _ensure_perf_header() -> None:
    """Upgrade legacy 9-column performance CSV to the current schema."""
    if not PERF_CSV.exists():
        return
    with open(PERF_CSV, encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
    if header == PERF_COLUMNS:
        return
    rows: list[list] = []
    with open(PERF_CSV, encoding="utf-8") as fh:
        reader = csv.reader(fh)
        old_header = next(reader, None)
        if not old_header:
            return
        for row in reader:
            padded = (row + [""] * len(PERF_COLUMNS))[:len(PERF_COLUMNS)]
            rows.append(padded)
    with open(PERF_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PERF_COLUMNS)
        w.writerows(rows)


def _refresh_prices(cur, tickers: list[str]) -> int:
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
        from psycopg2.extras import execute_values
        from ingest_prices import _INSERT_SQL, _VALUES_TEMPLATE
        execute_values(cur, _INSERT_SQL, rows, template=_VALUES_TEMPLATE, page_size=1000)
    return len(rows)


def _latest_closes(cur, tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    cur.execute("""
        SELECT DISTINCT ON (ticker) ticker, close
        FROM price_history WHERE close IS NOT NULL AND ticker = ANY(%s)
        ORDER BY ticker, date DESC
    """, (list(set(tickers)),))
    return {t: float(c) for t, c in cur.fetchall()}


def _load_open_paper(cur) -> list[dict]:
    cur.execute("""
        SELECT id, ticker, company_id, catalyst_id, side, trade_type, entry_date,
               shares, entry_price, cost_basis_usd, planned_exit_date, planned_exit_rule
        FROM portfolio_holdings WHERE status='open' AND notes='PAPER'
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _holding_dicts(holds: list[dict]) -> list[dict]:
    return [{"ticker": h["ticker"], "side": h["side"],
             "shares": float(h["shares"]), "entry_price": float(h["entry_price"])}
            for h in holds]


def _close_position(cur, h: dict, px: float, today: date, reason: str,
                    *, dry_run: bool) -> tuple[float, float]:
    """Close a full position. Returns (cash_delta, realized_pnl)."""
    sh = float(h["shares"])
    ep = float(h["entry_price"])
    rp = pf.realized_pnl(h["side"], sh, ep, px)
    cash_delta = pf.cash_delta_on_close(h["side"], sh, px)
    tag = {"exit_due": "EXIT", "not_in_book": "DROP", "side_flip": "FLIP",
           "trade_change": "RETYPE"}.get(reason, "CLOSE")
    print(f"  {tag} {h['ticker']:<6} {h['side']:<5} {h['trade_type']:<13} "
          f"{sh:.2f} @ {px:.2f}  realized {rp:+,.0f}  ({reason})")
    if not dry_run:
        cur.execute("""
            UPDATE portfolio_holdings SET status='closed', exit_date=%s,
                exit_price=%s, realized_pnl_usd=%s, updated_at=NOW() WHERE id=%s
        """, (today, px, round(rp, 2), h["id"]))
    return cash_delta, rp


def _open_position(cur, tgt: dict, today: date, *, dry_run: bool) -> float:
    """Open a new position. Returns cash_delta (negative for long spend)."""
    sh = float(tgt["target_shares"])
    px = float(tgt["price"])
    cost = round(sh * px, 2)
    cash_delta = pf.cash_delta_on_open(tgt["side"], sh, px)
    print(f"  OPEN {tgt['ticker']:<6} {tgt['side']:<5} {tgt['trade_type']:<13} "
          f"{sh:.2f} @ {px:.2f}  ${abs(cash_delta):,.0f}  wt {tgt['weight']:+.3f}  "
          f"exit {tgt['planned_exit_date']}")
    if not dry_run:
        cur.execute("""
            INSERT INTO portfolio_holdings
                (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                 shares, entry_price, cost_basis_usd, planned_exit_rule,
                 planned_exit_date, status, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open','PAPER')
        """, (tgt["ticker"], tgt["company_id"], tgt["catalyst_id"], tgt["side"],
              tgt["trade_type"], today, sh, px, cost,
              tgt["planned_exit_rule"], tgt["planned_exit_date"]))
    return cash_delta


def _resize_position(cur, h: dict, tgt: dict, today: date, *,
                     dry_run: bool) -> tuple[float, float]:
    """Resize toward target shares. Returns (cash_delta, realized_pnl)."""
    cur_sh = float(h["shares"])
    tgt_sh = float(tgt["target_shares"])
    px = float(tgt["price"])
    ep = float(h["entry_price"])
    rp = 0.0
    cash_delta = 0.0

    if tgt_sh > cur_sh:
        add = round(tgt_sh - cur_sh, 2)
        cash_delta = pf.cash_delta_on_open(h["side"], add, px)
        new_ep = (cur_sh * ep + add * px) / tgt_sh
        new_cost = round(tgt_sh * new_ep, 2)
        print(f"  ADD  {h['ticker']:<6} +{add:.2f} -> {tgt_sh:.2f} @ {px:.2f}  "
              f"wt {tgt['weight']:+.3f}")
        if not dry_run:
            cur.execute("""
                UPDATE portfolio_holdings SET shares=%s, entry_price=%s, cost_basis_usd=%s,
                    catalyst_id=%s, trade_type=%s, planned_exit_rule=%s,
                    planned_exit_date=%s, updated_at=NOW() WHERE id=%s
            """, (tgt_sh, round(new_ep, 4), new_cost, tgt["catalyst_id"], tgt["trade_type"],
                  tgt["planned_exit_rule"], tgt["planned_exit_date"], h["id"]))
        return cash_delta, rp

    trim = round(cur_sh - tgt_sh, 2)
    rp = pf.realized_pnl(h["side"], trim, ep, px)
    cash_delta = pf.cash_delta_on_close(h["side"], trim, px)
    new_cost = round(tgt_sh * ep, 2)
    print(f"  TRIM {h['ticker']:<6} -{trim:.2f} -> {tgt_sh:.2f} @ {px:.2f}  "
          f"realized {rp:+,.0f}  wt {tgt['weight']:+.3f}")
    if not dry_run:
        cur.execute("""
            INSERT INTO portfolio_holdings
                (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                 shares, entry_price, cost_basis_usd, exit_date, exit_price,
                 realized_pnl_usd, status, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'closed','PAPER_TRIM')
        """, (h["ticker"], h.get("company_id"), h.get("catalyst_id"), h["side"],
              h.get("trade_type"), h.get("entry_date") or today, trim, ep,
              round(trim * ep, 2), today, px, round(rp, 2)))
        cur.execute("""
            UPDATE portfolio_holdings SET shares=%s, cost_basis_usd=%s,
                catalyst_id=%s, trade_type=%s, planned_exit_rule=%s,
                planned_exit_date=%s, updated_at=NOW() WHERE id=%s
        """, (tgt_sh, new_cost, tgt["catalyst_id"], tgt["trade_type"],
              tgt["planned_exit_rule"], tgt["planned_exit_date"], h["id"]))
    return cash_delta, rp


def _update_metadata(cur, h: dict, tgt: dict, *, dry_run: bool) -> None:
    """Refresh exit dates / catalyst when size is already on target."""
    if (h.get("planned_exit_date") == tgt["planned_exit_date"]
            and h.get("trade_type") == tgt["trade_type"]
            and h.get("catalyst_id") == tgt["catalyst_id"]):
        return
    if not dry_run:
        cur.execute("""
            UPDATE portfolio_holdings SET catalyst_id=%s, trade_type=%s,
                planned_exit_rule=%s, planned_exit_date=%s, updated_at=NOW()
            WHERE id=%s
        """, (tgt["catalyst_id"], tgt["trade_type"], tgt["planned_exit_rule"],
              tgt["planned_exit_date"], h["id"]))


def run(*, dry_run: bool = False, sync_book: bool = True,
        horizon_days: int | None = None) -> None:
    today = date.today()
    horizon = horizon_days or config.AUTOPILOT_HORIZON_DAYS
    tol = config.AUTOPILOT_REBALANCE_PCT
    closed = opened = resized = 0
    realized_today = 0.0
    summary: dict = {"equity": 0.0, "positions": 0, "unrealized_pnl_usd": 0.0}

    book = compute_book(horizon_days=horizon)
    book_rows = book["rows"]

    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            cur.execute("INSERT INTO portfolio_account (id, cash_usd) VALUES (1, 0) "
                        "ON CONFLICT (id) DO NOTHING")
            cur.execute("SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1")
            arow = cur.fetchone() or (0.0, None)
            cash = float(arow[0] or 0.0)
            sleeve = float(arow[1] or 0.0)

            holds = _load_open_paper(cur)
            target_tickers = [r["ticker"] for r in book_rows]
            want = sorted({h["ticker"] for h in holds} | set(target_tickers)
                          | {config.BENCHMARK_TICKER})
            n_px = 0 if dry_run else _refresh_prices(cur, want)
            closes = _latest_closes(cur, want or ["XBI"])

            equity = pf.account_summary(_holding_dicts(holds), cash, closes)["equity"]
            if sleeve <= 0 and equity > 0:
                sleeve = equity

            print(f"\n=== PAPER AUTOPILOT  {today} ===")
            print(f"Action desk: {book['positions']} targets  "
                  f"(L {book['gross_long']:.0%} / S {book['gross_short']:.0%}  "
                  f"net {book['net']:+.0%})  horizon {horizon}d")
            print(f"Price rows refreshed: {n_px}   open positions: {len(holds)}   "
                  f"equity ${equity:,.0f}")

            targets = ps.build_targets(book_rows, equity, closes) if sync_book else {}

            # --- 1) CLOSES ---
            still_open: list[dict] = []
            for h in holds:
                if sync_book:
                    reason = ps.close_reason(h, targets.get(h["ticker"]), today)
                else:
                    ped = h.get("planned_exit_date")
                    reason = "exit_due" if ped is not None and ped <= today else None
                if reason is None:
                    still_open.append(h)
                    continue
                px = closes.get(h["ticker"])
                if px is None:
                    print(f"  [skip close] {h['ticker']}: no price")
                    still_open.append(h)
                    continue
                cd, rp = _close_position(cur, h, px, today, reason, dry_run=dry_run)
                cash += cd
                realized_today += rp
                closed += 1
            holds = still_open

            # Recompute equity after closes for sizing new opens.
            if sync_book and targets:
                equity = pf.account_summary(_holding_dicts(holds), cash, closes)["equity"]
                targets = ps.build_targets(book_rows, equity, closes)
                held = {h["ticker"]: h for h in holds}

                # --- 2) TRIMS (free cash before adds) ---
                for t in sorted(targets, key=lambda x: -abs(targets[x]["weight"])):
                    h = held.get(t)
                    if not h or not ps.needs_resize(h, targets[t], tol):
                        continue
                    if float(targets[t]["target_shares"]) >= float(h["shares"]):
                        continue
                    px = closes.get(t)
                    if px is None:
                        continue
                    cd, rp = _resize_position(cur, h, targets[t], today, dry_run=dry_run)
                    cash += cd
                    realized_today += rp
                    resized += 1
                    h["shares"] = targets[t]["target_shares"]

                # --- 3) OPENS + ADDS ---
                held = {h["ticker"]: h for h in holds}

                for t in sorted(targets, key=lambda x: -abs(targets[x]["weight"])):
                    tgt = targets[t]
                    h = held.get(t)
                    px = closes.get(t)
                    if px is None:
                        continue
                    if h is None:
                        cd = pf.cash_delta_on_open(tgt["side"], tgt["target_shares"], px)
                        if tgt["side"] == pf.LONG and -cd > cash + 1e-6:
                            print(f"  [skip open] {t}: need ${-cd:,.0f}, cash ${cash:,.0f}")
                            continue
                        cash += _open_position(cur, tgt, today, dry_run=dry_run)
                        opened += 1
                        if not dry_run:
                            held[t] = {"ticker": t}
                    elif ps.needs_resize(h, tgt, tol):
                        if float(tgt["target_shares"]) > float(h["shares"]):
                            add_cd = pf.cash_delta_on_open(h["side"],
                                float(tgt["target_shares"]) - float(h["shares"]), px)
                            if h["side"] == pf.LONG and -add_cd > cash + 1e-6:
                                print(f"  [skip add] {t}: need ${-add_cd:,.0f}, cash ${cash:,.0f}")
                                continue
                            cd, rp = _resize_position(cur, h, tgt, today, dry_run=dry_run)
                            cash += cd
                            realized_today += rp
                            resized += 1
                    else:
                        _update_metadata(cur, h, tgt, dry_run=dry_run)

            # --- SNAPSHOT ---
            open_after = _load_open_paper(cur) if not dry_run else holds
            summary = pf.account_summary(_holding_dicts(open_after), cash, closes)

            cur.execute("SELECT COALESCE(SUM(realized_pnl_usd),0) FROM portfolio_holdings "
                        "WHERE notes LIKE 'PAPER%' AND status='closed'")
            realized_total = float(cur.fetchone()[0] or 0.0)

            if not dry_run:
                cur.execute("UPDATE portfolio_account SET cash_usd=%s, updated_at=NOW() WHERE id=1",
                            (round(cash, 2),))
                raw.commit()
                open_after = _load_open_paper(cur)
                summary = pf.account_summary(_holding_dicts(open_after), cash, closes)
                cur.execute("SELECT COALESCE(SUM(realized_pnl_usd),0) FROM portfolio_holdings "
                            "WHERE notes LIKE 'PAPER%' AND status='closed'")
                realized_total = float(cur.fetchone()[0] or 0.0)

            ret_pct = (summary["equity"] - sleeve) / sleeve if sleeve else 0.0
            print(f"\n  Closed: {closed}  Opened: {opened}  Resized: {resized}")
            print(f"  Cash ${cash:,.0f} | Equity ${summary['equity']:,.0f} | "
                  f"Unrealized {summary['unrealized_pnl_usd']:+,.0f} | "
                  f"Realized-to-date {realized_total:+,.0f} | "
                  f"Total return {ret_pct:+.1%} vs ${sleeve:,.0f} sleeve")

            if not dry_run:
                PERF_CSV.parent.mkdir(parents=True, exist_ok=True)
                _ensure_perf_header()
                new_file = not PERF_CSV.exists() or PERF_CSV.stat().st_size == 0
                with open(PERF_CSV, "a", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    if new_file:
                        w.writerow(PERF_COLUMNS)
                    w.writerow([today, round(summary["equity"], 2), round(cash, 2),
                                summary["positions"], summary["unrealized_pnl_usd"],
                                round(realized_total, 2), round(ret_pct, 4),
                                closed, opened, resized, book["positions"]])
                print(f"  Snapshot appended -> {PERF_CSV}")

                xbi_px = closes.get(config.BENCHMARK_TICKER)
                track_start = perf_store.tracking_start_date(cur)
                xbi_ret, bench_eq = perf_store.benchmark_fields(
                    cur,
                    xbi_close=xbi_px,
                    starting_capital=sleeve if sleeve > 0 else None,
                    track_start=track_start,
                )
                perf_store.upsert_snapshot(cur, {
                    "snapshot_date": today,
                    "equity": round(summary["equity"], 2),
                    "cash": round(cash, 2),
                    "open_positions": summary["positions"],
                    "unrealized_pnl": summary["unrealized_pnl_usd"],
                    "realized_to_date": round(realized_total, 2),
                    "total_return_pct": round(ret_pct, 4),
                    "exits_today": closed,
                    "opens_today": opened,
                    "resized_today": resized,
                    "desk_positions": book["positions"],
                    "xbi_close": xbi_px,
                    "xbi_return_pct": xbi_ret,
                    "benchmark_equity": bench_eq,
                })
                raw.commit()
                print("  Snapshot upserted -> portfolio_performance (Supabase)")
            else:
                print("  (dry run — nothing written)")
        finally:
            cur.close()

    log.info("autopilot_done", closed=closed, opened=opened, resized=resized,
             desk_positions=book["positions"], equity=round(summary["equity"], 2),
             realized_total=round(realized_total, 2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper autopilot — sync to capped action desk")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exits-only", action="store_true",
                    help="close due exits only; do not sync to action desk")
    ap.add_argument("--no-open", action="store_true",
                    help="alias for --exits-only (legacy)")
    ap.add_argument("--horizon-days", type=int, default=None,
                    help=f"action desk horizon (default {config.AUTOPILOT_HORIZON_DAYS})")
    args = ap.parse_args()
    exits_only = args.exits_only or args.no_open
    try:
        config.preflight()
        run(dry_run=args.dry_run, sync_book=not exits_only,
            horizon_days=args.horizon_days)
    except Exception as exc:  # noqa: BLE001
        log.error("autopilot_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
