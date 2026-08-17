"""
Paper-trading autopilot — syncs the PAPER book to the capped Action Desk daily.

Designed for Windows Task Scheduler (weekday evenings). Each run:
  1. Builds the risk-capped action book (same logic as Action Desk / action_sheet.py).
  2. Refreshes prices for held + target tickers.
  3. Closes positions: past exit date, dropped from book, or side/trade flip.
  4. Opens new names and rebalances existing ones toward target weights.
  5. Appends a daily performance snapshot to data/raw/paper_performance.csv.

Only touches notes='PAPER' holdings. When config.LONG_ONLY is set (default), the
capped book contains no shorts, no short target is ever executed (execution-level
guard), and any open shorts fall out of the book and are covered on the next sync.

Risk overlays (see config): per-position stop-loss on longs, graded drawdown
de-risking with open-pause, and an XBI-SMA regime filter that shrinks targets in
a down-tape.

  python scripts/paper_autopilot.py                 # sync to action desk + snapshot
  python scripts/paper_autopilot.py --dry-run       # show planned trades
  python scripts/paper_autopilot.py --exits-only    # close due exits only, no sync
  python scripts/paper_autopilot.py --cover-shorts  # cover all open PAPER shorts now
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

from action_sheet import compute_book
from ingest_prices import _rows_from_history

import config
from db import get_connection
from layers.marketdata.yf_client import fetch_history_batch
from layers.portfolio import paper_sync as ps
from layers.portfolio import performance_store as perf_store
from layers.portfolio import risk
from layers.portfolio import tracker as pf
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
        from ingest_prices import _INSERT_SQL, _VALUES_TEMPLATE
        from psycopg2.extras import execute_values
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
           "trade_change": "RETYPE", "long_only_cover": "COVER",
           "stop_loss": "STOP"}.get(reason, "CLOSE")
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


def _trailing_closes(cur, ticker: str, days: int) -> list[float]:
    """Most-recent `days` daily closes for a ticker (newest first)."""
    cur.execute(
        """
        SELECT close FROM price_history
        WHERE ticker = %s AND close IS NOT NULL
        ORDER BY date DESC LIMIT %s
        """,
        (ticker, days),
    )
    return [float(r[0]) for r in cur.fetchall()]


def _peak_equity(cur, current_equity: float) -> float:
    """Highest recorded equity (Supabase history), never below today's value."""
    cur.execute("SELECT MAX(equity) FROM portfolio_performance")
    row = cur.fetchone()
    peak = float(row[0]) if row and row[0] is not None else 0.0
    return max(peak, float(current_equity))


def _profit_lock_trim(cur, h: dict, px: float, today: date, fraction: float,
                      *, dry_run: bool) -> tuple[float, float, float]:
    """Scale OUT `fraction` of a winning long. Returns (cash_delta, realized, new_shares)."""
    cur_sh = float(h["shares"])
    trim = round(cur_sh * fraction, 2)
    remaining = round(cur_sh - trim, 2)
    if trim <= 0 or remaining <= 0:
        return 0.0, 0.0, cur_sh
    ep = float(h["entry_price"])
    rp = pf.realized_pnl(h["side"], trim, ep, px)
    cash_delta = pf.cash_delta_on_close(h["side"], trim, px)
    gain_pct = (px / ep - 1) if ep else 0.0
    print(f"  LOCK {h['ticker']:<6} -{trim:.2f} -> {remaining:.2f} @ {px:.2f}  "
          f"realized {rp:+,.0f}  (+{gain_pct:.0%}, mean-revert trim)")
    if not dry_run:
        cur.execute("""
            INSERT INTO portfolio_holdings
                (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                 shares, entry_price, cost_basis_usd, exit_date, exit_price,
                 realized_pnl_usd, status, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'closed','PAPER_LOCK')
        """, (h["ticker"], h.get("company_id"), h.get("catalyst_id"), h["side"],
              h.get("trade_type"), h.get("entry_date") or today, trim, ep,
              round(trim * ep, 2), today, px, round(rp, 2)))
        cur.execute("""
            UPDATE portfolio_holdings SET shares=%s, cost_basis_usd=%s, updated_at=NOW()
            WHERE id=%s
        """, (remaining, round(remaining * ep, 2), h["id"]))
    return cash_delta, rp, remaining


def _profit_lock_pass(cur, holds: list[dict], closes: dict, targets: dict,
                      today: date, *, dry_run: bool) -> tuple[float, float, int]:
    """Partial mean-reversion profit-take on extended long winners.

    Returns (cash_delta_total, realized_total, count). Adjusts `targets` so the
    normal sync does not immediately re-add a name we just locked.
    """
    if not config.PROFIT_LOCK_ENABLED:
        return 0.0, 0.0, 0
    cash_delta_total = 0.0
    realized_total = 0.0
    locked = 0
    for h in holds:
        if h["side"] != pf.LONG:
            continue
        px = closes.get(h["ticker"])
        ep = float(h["entry_price"])
        if px is None or ep <= 0:
            continue
        if (px / ep - 1) < config.PROFIT_LOCK_GAIN_PCT:
            continue
        ped = h.get("planned_exit_date")
        if ped is not None and (ped - today).days <= config.PROFIT_LOCK_MIN_DAYS_TO_CATALYST:
            continue  # let imminent catalysts play out
        hist = _trailing_closes(cur, h["ticker"], config.PROFIT_LOCK_LOOKBACK_DAYS)
        z = pf.zscore(px, hist)
        if z is None or z < config.PROFIT_LOCK_ZSCORE:
            continue  # not stretched above its mean -> nothing to revert
        cd, rp, new_sh = _profit_lock_trim(
            cur, h, px, today, config.PROFIT_LOCK_TRIM_FRACTION, dry_run=dry_run)
        if new_sh < float(h["shares"]):
            cash_delta_total += cd
            realized_total += rp
            locked += 1
            h["shares"] = new_sh
            if h["ticker"] in targets:
                targets[h["ticker"]]["target_shares"] = new_sh  # block re-add this run
    return cash_delta_total, realized_total, locked


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
    closed = opened = resized = locked = 0
    derisk = False
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

            # Graded drawdown de-risk: scale targets progressively as drawdown
            # deepens; pause new opens once the scale hits the pause threshold.
            dd_scale = 1.0
            if config.DRAWDOWN_CIRCUIT_ENABLED:
                peak = _peak_equity(cur, equity)
                dd_scale = risk.drawdown_scale(equity, peak, config.DRAWDOWN_TIERS)
                if dd_scale < 1.0:
                    dd = (equity / peak - 1) if peak else 0.0
                    print(f"  [DRAWDOWN] equity ${equity:,.0f} is {dd:+.1%} vs peak "
                          f"${peak:,.0f} -> targets x{dd_scale}"
                          + ("  (opens paused)" if dd_scale <= config.DRAWDOWN_OPEN_PAUSE_SCALE else ""))
            derisk = dd_scale <= config.DRAWDOWN_OPEN_PAUSE_SCALE

            # Market-regime filter: benchmark below its SMA -> lighter gross.
            rg_scale = 1.0
            if config.REGIME_FILTER_ENABLED:
                xbi_hist = list(reversed(_trailing_closes(
                    cur, config.BENCHMARK_TICKER, config.REGIME_SMA_DAYS)))
                rg_scale = risk.regime_scale(xbi_hist, config.REGIME_SMA_DAYS,
                                             config.REGIME_DERISK_FACTOR)
                if rg_scale < 1.0:
                    print(f"  [REGIME] {config.BENCHMARK_TICKER} below its "
                          f"{config.REGIME_SMA_DAYS}d SMA -> targets x{rg_scale}")

            scale = dd_scale * rg_scale

            print(f"\n=== PAPER AUTOPILOT  {today} ===")
            print(f"Action desk: {book['positions']} targets  "
                  f"(L {book['gross_long']:.0%} / S {book['gross_short']:.0%}  "
                  f"net {book['net']:+.0%})  horizon {horizon}d")
            print(f"Price rows refreshed: {n_px}   open positions: {len(holds)}   "
                  f"equity ${equity:,.0f}")

            targets = ps.build_targets(book_rows, equity, closes) if sync_book else {}

            # --- 1) CLOSES (stop-loss first, then book sync) ---
            still_open: list[dict] = []
            for h in holds:
                px_now = closes.get(h["ticker"])
                if (config.STOP_LOSS_ENABLED
                        and risk.stop_loss_hit(h["side"], float(h["entry_price"]),
                                               px_now, config.STOP_LOSS_PCT)):
                    reason = "stop_loss"
                elif sync_book:
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
                if reason == "stop_loss":
                    targets.pop(h["ticker"], None)  # do not re-buy a stopped name today
            holds = still_open

            # Recompute equity after closes for sizing new opens.
            if sync_book and targets:
                equity = pf.account_summary(_holding_dicts(holds), cash, closes)["equity"]
                kept = set(targets.keys())
                targets = {t: v for t, v in ps.build_targets(book_rows, equity, closes).items()
                           if t in kept}

                # Risk overlays: shrink all targets by the combined scale.
                if scale < 1.0:
                    for t in targets:
                        targets[t]["target_shares"] = round(
                            targets[t]["target_shares"] * scale, 2)
                        targets[t]["target_dollars"] = round(
                            targets[t]["target_dollars"] * scale, 2)

                # --- 1b) PARTIAL PROFIT-LOCK (mean reversion, longs only) ---
                cd, rp, locked = _profit_lock_pass(
                    cur, holds, closes, targets, today, dry_run=dry_run)
                cash += cd
                realized_today += rp

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
                        if derisk:
                            continue  # circuit breaker: no new opens while de-risked
                        if config.LONG_ONLY and tgt["side"] != pf.LONG:
                            # Execution-level guard: NEVER open a short in long-only
                            # mode, no matter what upstream produced the target.
                            print(f"  [refuse open] {t}: short target blocked (LONG_ONLY)")
                            continue
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
            print(f"\n  Closed: {closed}  Opened: {opened}  Resized: {resized}  "
                  f"Profit-locked: {locked}{'  [DE-RISKED]' if derisk else ''}")
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
             profit_locked=locked, derisked=derisk,
             desk_positions=book["positions"], equity=round(summary["equity"], 2),
             realized_total=round(realized_total, 2))


def _load_open_shorts_and_fades(cur) -> list[dict]:
    """Every open short OR fade — not limited to notes='PAPER'.

    The Portfolio UI shows all open holdings; leftover fades from rogue runs
    must be coverable even if notes differ.
    """
    cur.execute("""
        SELECT id, ticker, company_id, catalyst_id, side, trade_type, entry_date,
               shares, entry_price, cost_basis_usd, planned_exit_date, planned_exit_rule,
               notes
        FROM portfolio_holdings
        WHERE status='open' AND (side='short' OR trade_type='fade')
        ORDER BY ticker, id
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def cover_shorts(*, dry_run: bool = False) -> None:
    """Close every open short/fade at last close and free the cash. One-shot.

    Use to flatten shorts immediately (e.g. when switching to long-only) rather than
    waiting for the daily sync to drop them as not_in_book. Catches side='short'
    OR trade_type='fade' across all notes (not just PAPER).
    """
    today = date.today()
    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            cur.execute("INSERT INTO portfolio_account (id, cash_usd) VALUES (1, 0) "
                        "ON CONFLICT (id) DO NOTHING")
            cur.execute("SELECT cash_usd FROM portfolio_account WHERE id=1")
            cash = float((cur.fetchone() or (0.0,))[0] or 0.0)

            shorts = _load_open_shorts_and_fades(cur)
            print(f"\n=== COVER SHORTS  {today} ===")
            if not shorts:
                print("  No open shorts/fades.")
                return
            tickers = [h["ticker"] for h in shorts]
            n_px = 0 if dry_run else _refresh_prices(cur, tickers)
            closes = _latest_closes(cur, tickers)
            print(f"  Open shorts/fades: {len(shorts)}   price rows refreshed: {n_px}")
            for h in shorts:
                print(f"    {h['ticker']:<6} side={h['side']:<5} type={h['trade_type']:<13} "
                      f"notes={h.get('notes')!r} shares={float(h['shares']):.2f}")

            covered = 0
            realized_today = 0.0
            for h in shorts:
                # Fade rows that somehow have side≠short: force short semantics
                # so cover P&L/cash and strip_shorts both see them as shorts.
                if h["side"] != pf.SHORT:
                    print(f"  [coerce] {h['ticker']}: {h['side']}/{h['trade_type']} → short")
                    if not dry_run:
                        cur.execute(
                            "UPDATE portfolio_holdings SET side=%s, updated_at=NOW() WHERE id=%s",
                            (pf.SHORT, h["id"]),
                        )
                    h = dict(h)
                    h["side"] = pf.SHORT
                px = closes.get(h["ticker"])
                if px is None:
                    print(f"  [skip] {h['ticker']}: no price")
                    continue
                cd, rp = _close_position(cur, h, px, today, "long_only_cover", dry_run=dry_run)
                cash += cd
                realized_today += rp
                covered += 1

            print(f"\n  Covered: {covered}/{len(shorts)}   realized {realized_today:+,.0f}   "
                  f"cash -> ${cash:,.0f}")
            if not dry_run:
                cur.execute("UPDATE portfolio_account SET cash_usd=%s, updated_at=NOW() WHERE id=1",
                            (round(cash, 2),))
                raw.commit()
                print("  Committed.")
            else:
                print("  (dry run — nothing written)")
        finally:
            cur.close()
    log.info("cover_shorts_done", covered=covered if shorts else 0,
             realized=round(realized_today, 2) if shorts else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper autopilot — sync to capped action desk")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exits-only", action="store_true",
                    help="close due exits only; do not sync to action desk")
    ap.add_argument("--no-open", action="store_true",
                    help="alias for --exits-only (legacy)")
    ap.add_argument("--cover-shorts", action="store_true",
                    help="cover all open PAPER shorts now, then exit")
    ap.add_argument("--horizon-days", type=int, default=None,
                    help=f"action desk horizon (default {config.AUTOPILOT_HORIZON_DAYS})")
    args = ap.parse_args()
    exits_only = args.exits_only or args.no_open
    try:
        config.preflight()
        if args.cover_shorts:
            cover_shorts(dry_run=args.dry_run)
            return
        run(dry_run=args.dry_run, sync_book=not exits_only,
            horizon_days=args.horizon_days)
    except Exception as exc:  # noqa: BLE001
        log.error("autopilot_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
