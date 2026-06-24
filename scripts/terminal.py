"""
GBM/Onc-CNS Edge Engine - Bloomberg-style terminal.

Run with:
    streamlit run scripts/terminal.py

Dark, dense, multi-panel cockpit for the Rung 2 decision-support engine. Read-only,
cached (5 min), no hardcoded credentials (DATABASE_URL from .env).

Panels: Trade Blotter | Security | Catalyst Calendar | Validation | Data Health.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root (layers.*)
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/ (action_sheet)

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import streamlit as st
from dotenv import load_dotenv

from action_sheet import TIMING, compute_book
from layers.portfolio import tracker as pf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "")

st.set_page_config(page_title="EDGE TERMINAL", layout="wide", page_icon="📟")

# ---- Terminal aesthetic ----
st.markdown(
    """
    <style>
      html, body, [class*="css"] { font-family: 'JetBrains Mono','Consolas',monospace; }
      .block-container { padding-top: 1.2rem; padding-bottom: 1rem; max-width: 100%; }
      [data-testid="stMetricValue"] { font-size: 1.25rem; color: #e6e6e6; }
      [data-testid="stMetricLabel"] { opacity: 0.6; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.08em; }
      div[data-testid="stDataFrame"] { font-size: 0.8rem; }
      h1,h2,h3 { letter-spacing: 0.02em; }
      .stApp { background-color: #0b0e11; }
      .amber { color: #f5a623; } .grn { color: #29d391; } .red { color: #ff5c5c; }
    </style>
    """,
    unsafe_allow_html=True,
)

TRADE_COLORS = {
    "buy_the_rumor": "#29d391",
    "hold_through": "#3aa0ff",
    "fade": "#ff5c5c",
    "avoid": "#6b7280",
}


def get_conn():
    if not DATABASE_URL:
        st.error("DATABASE_URL not set in .env")
        st.stop()
    return psycopg2.connect(DATABASE_URL)


@st.cache_data(ttl=300)
def q(sql: str, params: tuple | None = None) -> pd.DataFrame:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if cur.description is None:
                return pd.DataFrame()
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


def fmt_usd(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v/div:.2f}{suf}"
    return f"${v:.0f}"


def _f(v):
    """Decimal/None -> float|None (psycopg2 numerics come back as Decimal)."""
    return None if v is None else float(v)


def exec_write(sql: str, params: tuple | None = None) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


@st.cache_data(ttl=120)
def latest_prices() -> dict[str, float]:
    """ticker -> latest available daily close (end-of-day, not live)."""
    df = q("""
        SELECT DISTINCT ON (ticker) ticker, close
        FROM price_history WHERE close IS NOT NULL
        ORDER BY ticker, date DESC
    """)
    return {r.ticker: float(r.close) for r in df.itertuples()} if not df.empty else {}


def ensure_account() -> None:
    exec_write("INSERT INTO portfolio_account (id, cash_usd) VALUES (1, 0) "
               "ON CONFLICT (id) DO NOTHING")


@st.cache_data(ttl=10)
def get_account() -> dict:
    df = q("SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1")
    if df.empty:
        return {"cash": 0.0, "starting_capital": None}
    return {"cash": _f(df.iloc[0]["cash_usd"]) or 0.0,
            "starting_capital": _f(df.iloc[0]["starting_capital_usd"])}


def set_account(cash: float, starting_capital: float | None) -> None:
    ensure_account()
    exec_write(
        "UPDATE portfolio_account SET cash_usd=%s, starting_capital_usd="
        "COALESCE(%s, starting_capital_usd), updated_at=NOW() WHERE id=1",
        (cash, starting_capital))


@st.cache_data(ttl=10)
def load_holdings(status: str | None = "open") -> pd.DataFrame:
    sql = """
        SELECT h.id, h.ticker, h.company_id, h.catalyst_id, h.side, h.trade_type,
               h.entry_date, h.shares, h.entry_price, h.cost_basis_usd,
               h.planned_exit_rule, h.planned_exit_date, h.status,
               h.exit_date, h.exit_price, h.realized_pnl_usd, h.notes
        FROM portfolio_holdings h
    """
    params = None
    if status:
        sql += " WHERE h.status=%s"
        params = (status,)
    sql += " ORDER BY h.entry_date DESC, h.id DESC"
    return q(sql, params)


def add_holding(*, ticker, company_id, catalyst_id, side, trade_type, entry_date,
                shares, entry_price, planned_exit_rule, planned_exit_date, notes) -> None:
    cost = float(shares) * float(entry_price)
    cash_delta = pf.cash_delta_on_open(side, shares, entry_price)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO portfolio_holdings
                   (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                    shares, entry_price, cost_basis_usd, planned_exit_rule,
                    planned_exit_date, status, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s)""",
                (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                 shares, entry_price, cost, planned_exit_rule, planned_exit_date, notes))
            cur.execute("UPDATE portfolio_account SET cash_usd=cash_usd+%s, updated_at=NOW() "
                        "WHERE id=1", (cash_delta,))
        conn.commit()
    finally:
        conn.close()


def close_holding(hid: int, side: str, shares: float, entry_price: float,
                  exit_price: float, exit_date) -> None:
    realized = pf.realized_pnl(side, shares, entry_price, exit_price)
    cash_delta = pf.cash_delta_on_close(side, shares, exit_price)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE portfolio_holdings SET status='closed', exit_price=%s,
                   exit_date=%s, realized_pnl_usd=%s, updated_at=NOW() WHERE id=%s""",
                (exit_price, exit_date, realized, hid))
            cur.execute("UPDATE portfolio_account SET cash_usd=cash_usd+%s, updated_at=NOW() "
                        "WHERE id=1", (cash_delta,))
        conn.commit()
    finally:
        conn.close()


@st.cache_data(ttl=300)
def data_freshness() -> dict:
    out = {}
    for tbl, col in [("price_history", "fetched_at"), ("positioning", "computed_at"),
                     ("edge_scores", "computed_at")]:
        df = q(f"SELECT MAX({col}) ts FROM {tbl}")
        out[tbl] = df.iloc[0, 0] if not df.empty else None
    return out


def honesty_banner() -> None:
    st.info("**Longs = validated edge** (base-rate model, proven out-of-sample). "
            "**Shorts/fades = experimental** — paper-trade or size small until they earn "
            "a track record. Prices are end-of-day, not live.")


def freshness_caption() -> None:
    fr = data_freshness()
    today = pd.Timestamp(date.today(), tz="UTC")
    bits = []
    for name, ts in fr.items():
        if ts is None:
            bits.append(f"{name}: none")
            continue
        ts = pd.Timestamp(ts)
        age = (today - ts).days if ts.tzinfo else (pd.Timestamp(date.today()) - ts).days
        flag = "🟢" if age <= 2 else ("🟠" if age <= 7 else "🔴")
        bits.append(f"{flag} {name} ({age}d)")
    st.caption("Data freshness — " + " · ".join(bits))


@st.cache_data(ttl=300)
def load_blotter() -> pd.DataFrame:
    df = q(
        """
        SELECT co.ticker, co.name AS company, co.id AS company_id, co.is_gbm_focused,
               co.indication_category, co.market_cap_usd,
               c.id AS catalyst_id, c.catalyst_type, c.expected_date, c.sec_confirmed,
               c.base_rate,
               es.composite_score, es.trade_type, es.suggested_weight, es.edge_gap,
               es.expected_move, es.implied_move, es.financing_tilt, es.insider_tilt,
               es.confidence,
               p.run_up_30d, p.short_pct_float,
               f.runway_months
        FROM edge_scores es
        JOIN catalysts c ON c.id = es.catalyst_id
        JOIN companies co ON co.id = es.company_id
        LEFT JOIN LATERAL (SELECT run_up_30d, short_pct_float FROM positioning
                           WHERE company_id = co.id ORDER BY date DESC LIMIT 1) p ON TRUE
        LEFT JOIN LATERAL (SELECT runway_months FROM financials
                           WHERE company_id = co.id ORDER BY period_end DESC LIMIT 1) f ON TRUE
        """
    )
    if df.empty:
        return df
    df["expected_date"] = pd.to_datetime(df["expected_date"], errors="coerce")
    df["days_until"] = (df["expected_date"] - pd.Timestamp(date.today())).dt.days
    for c in ["composite_score", "suggested_weight", "edge_gap", "expected_move",
              "implied_move", "run_up_30d", "short_pct_float", "runway_months",
              "financing_tilt", "insider_tilt", "base_rate", "confidence"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ===========================================================================
def _holding_dicts(df: pd.DataFrame) -> list[dict]:
    return [{"ticker": r.ticker, "side": r.side, "shares": float(r.shares),
             "entry_price": float(r.entry_price), "trade_type": r.trade_type,
             "planned_exit_date": r.planned_exit_date,
             "planned_exit_rule": r.planned_exit_rule} for r in df.itertuples()]


def render_action_center(open_df: pd.DataFrame) -> None:
    st.subheader("🔔 Action Center — what to do now")
    if open_df.empty:
        st.caption("No open positions. Log a trade below or check the Action Sheet for ideas.")
        return
    alerts = pf.exit_alerts(_holding_dicts(open_df), date.today(), soon_days=7)
    if not alerts:
        st.success("Nothing pressing. No exits due in the next 7 days.")
        return
    for a in alerts:
        when = "TODAY / overdue" if a["days"] <= 0 else f"in {a['days']} day(s) ({a['exit_date']})"
        line = f"**{a['action']} {a['ticker']}** — {when}. {a['reason']}"
        (st.error if a["level"] == "now" else st.warning)(line)


def page_portfolio() -> None:
    st.title("💼 PORTFOLIO")
    honesty_banner()
    ensure_account()
    acct = get_account()
    prices = latest_prices()
    open_df = load_holdings("open")

    # ---- account setup ----
    needs_setup = (acct["starting_capital"] is None and acct["cash"] == 0 and open_df.empty)
    with st.expander("⚙️ Account / cash", expanded=needs_setup):
        if needs_setup:
            st.caption("Set your starting cash to begin. Cash auto-adjusts as you log trades.")
        cash = st.number_input("Cash balance ($)", value=float(acct["cash"]), step=100.0, format="%.2f")
        start_cap = st.number_input("Starting capital ($) — for total-return tracking",
                                    value=float(acct["starting_capital"] or 0.0), step=100.0, format="%.2f")
        if st.button("Save account"):
            set_account(cash, start_cap or None)
            st.cache_data.clear()
            st.rerun()

    # ---- account summary ----
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)
    m = st.columns(6)
    m[0].metric("Account value", fmt_usd(summ["equity"]),
                help="Cash + market value of open positions (long +, short −).")
    m[1].metric("Cash", fmt_usd(summ["cash"]))
    m[2].metric("Unrealized P&L", fmt_usd(summ["unrealized_pnl_usd"]))
    m[3].metric("Gross long", f"{summ['gross_long_pct']:.0%}", help=fmt_usd(summ["gross_long_usd"]))
    m[4].metric("Gross short", f"{summ['gross_short_pct']:.0%}", help=fmt_usd(summ["gross_short_usd"]))
    m[5].metric("Net", f"{summ['net_pct']:+.0%}", help=fmt_usd(summ["net_usd"]))
    if acct["starting_capital"]:
        tot = summ["equity"] - float(acct["starting_capital"])
        st.caption(f"Total return since start: **{fmt_usd(tot)}** "
                   f"({tot/float(acct['starting_capital']):+.1%})")
    freshness_caption()

    st.divider()
    render_action_center(open_df)

    # ---- holdings table ----
    st.divider()
    st.subheader("📂 Open positions")
    if open_df.empty:
        st.caption("None yet.")
    else:
        rows = []
        for r in open_df.itertuples():
            cur = prices.get(r.ticker)
            mv = pf.market_value(r.side, float(r.shares), cur)
            pnl = pf.unrealized_pnl(r.side, float(r.shares), float(r.entry_price), cur)
            pnl_pct = pf.unrealized_pnl_pct(r.side, float(r.entry_price), cur)
            rows.append({
                "ticker": r.ticker, "side": r.side, "type": r.trade_type,
                "shares": float(r.shares), "entry": float(r.entry_price),
                "now": cur, "mkt_value": abs(mv) if mv is not None else None,
                "% book": (abs(mv) / summ["equity"] if mv is not None and summ["equity"] else None),
                "P&L $": pnl, "P&L %": pnl_pct,
                "exit_by": r.planned_exit_date, "rule": r.planned_exit_rule,
            })
        hv = pd.DataFrame(rows)
        sty = hv.style.format({
            "entry": "{:.2f}", "now": "{:.2f}", "mkt_value": "${:,.0f}",
            "% book": "{:.1%}", "P&L $": "${:,.0f}", "P&L %": "{:+.1%}",
            "shares": "{:.0f}"}, na_rep="—")
        st.dataframe(sty, use_container_width=True, hide_index=True, height=280)

    # ---- add trade ----
    st.divider()
    st.subheader("➕ Log a trade")
    companies = q("SELECT id, ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")
    if companies.empty:
        st.caption("No companies in DB.")
    else:
        cc = st.columns([1, 1, 1, 1])
        tk = cc[0].selectbox("Ticker", companies["ticker"].tolist(), key="add_tk")
        cid = int(companies[companies["ticker"] == tk].iloc[0]["id"])
        side = cc[1].selectbox("Side", ["long", "short"], key="add_side")
        ttype = cc[2].selectbox("Trade type", ["buy_the_rumor", "hold_through", "fade", "manual"],
                                key="add_tt")
        size_by = cc[3].radio("Size by", ["shares", "dollars"], horizontal=True, key="add_sizeby")

        cats = q("SELECT id, catalyst_type, expected_date FROM catalysts WHERE company_id=%s "
                 "AND expected_date >= CURRENT_DATE ORDER BY expected_date", (cid,))
        cat_map = {"(none — manual exit)": (None, None)}
        for r in cats.itertuples():
            cat_map[f"{r.catalyst_type} @ {r.expected_date} (#{r.id})"] = (int(r.id), r.expected_date)
        c2 = st.columns([2, 1, 1, 1])
        cat_label = c2[0].selectbox("Link catalyst (sets exit timing)", list(cat_map.keys()),
                                    key="add_cat")
        default_price = float(prices.get(tk, 0.0))
        amount = c2[1].number_input(f"{'Shares' if size_by=='shares' else 'Dollars ($)'}",
                                    min_value=0.0, value=0.0, step=1.0, key="add_amt")
        price = c2[2].number_input("Entry price ($)", min_value=0.0, value=default_price,
                                   step=0.01, format="%.2f", key="add_price")
        entry_dt = c2[3].date_input("Entry date", value=date.today(), key="add_date")
        notes = st.text_input("Notes (optional)", key="add_notes")

        catalyst_id, cat_date = cat_map[cat_label]
        exit_date, exit_rule = pf.planned_exit(ttype, cat_date)
        if exit_date:
            st.caption(f"Planned exit: **{exit_date}** — {exit_rule}")
        if st.button("Add trade", type="primary"):
            if amount <= 0 or price <= 0:
                st.error("Enter a positive size and entry price.")
            else:
                shares = amount if size_by == "shares" else round(amount / price, 4)
                add_holding(ticker=tk, company_id=cid, catalyst_id=catalyst_id, side=side,
                            trade_type=ttype, entry_date=entry_dt, shares=shares,
                            entry_price=price, planned_exit_rule=exit_rule,
                            planned_exit_date=exit_date, notes=notes or None)
                st.cache_data.clear()
                st.success(f"Logged {side} {shares} {tk} @ ${price:.2f}")
                st.rerun()

    # ---- close trade ----
    if not open_df.empty:
        st.divider()
        st.subheader("✅ Close a position")
        lbl_map = {f"{r.ticker} {r.side} {float(r.shares):.0f}@{float(r.entry_price):.2f} (#{r.id})":
                   r for r in open_df.itertuples()}
        cclose = st.columns([2, 1, 1])
        sel = cclose[0].selectbox("Position", list(lbl_map.keys()), key="close_sel")
        row = lbl_map[sel]
        xprice = cclose[1].number_input("Exit price ($)", min_value=0.0,
                                        value=float(prices.get(row.ticker, row.entry_price)),
                                        step=0.01, format="%.2f", key="close_price")
        xdate = cclose[2].date_input("Exit date", value=date.today(), key="close_date")
        rp = pf.realized_pnl(row.side, float(row.shares), float(row.entry_price), xprice)
        st.caption(f"Realized P&L if closed here: **{fmt_usd(rp)}**")
        if st.button("Close position"):
            close_holding(int(row.id), row.side, float(row.shares), float(row.entry_price),
                          xprice, xdate)
            st.cache_data.clear()
            st.success(f"Closed {row.ticker}. Realized {fmt_usd(rp)}")
            st.rerun()


# ===========================================================================
def page_home() -> None:
    st.title("📟 COCKPIT")
    honesty_banner()
    ensure_account()
    acct = get_account()
    prices = latest_prices()
    open_df = load_holdings("open")
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)

    m = st.columns(4)
    m[0].metric("Account value", fmt_usd(summ["equity"]))
    m[1].metric("Open positions", summ["positions"])
    m[2].metric("Unrealized P&L", fmt_usd(summ["unrealized_pnl_usd"]))
    m[3].metric("Net exposure", f"{summ['net_pct']:+.0%}")
    freshness_caption()

    st.divider()
    render_action_center(open_df)

    st.divider()
    st.subheader("🎯 Today's top ideas (capped book)")
    book = load_action_book(365)
    rows = book["rows"][:8]
    if not rows:
        st.caption("No signals. Run scripts/run_composite.py then scripts/action_sheet.py.")
        return
    eq = summ["equity"] or 0.0
    out = []
    for r in rows:
        sized = pf.size_from_weight(r["weight"], eq, prices.get(r["ticker"]))
        out.append({
            "ticker": r["ticker"],
            "action": ("BUY" if r["weight"] > 0 else "SHORT"),
            "type": r["trade_type"],
            "weight": r["weight"],
            "$ target": sized["dollars"] if eq else None,
            "~shares": sized["shares"] if eq else None,
            "catalyst": pd.to_datetime(r["expected_date"]).date(),
            "timing": TIMING.get(r["trade_type"], ""),
        })
    df = pd.DataFrame(out)
    st.dataframe(df.style.format({"weight": "{:+.3f}", "$ target": "${:,.0f}",
                                  "~shares": "{:,.0f}"}, na_rep="—"),
                 use_container_width=True, hide_index=True)
    if not eq:
        st.caption("Set your account value on the Portfolio page to see $ and share sizing.")
    st.caption("These are ideas, not orders. Longs lean on the validated edge; shorts are experimental.")


# ===========================================================================
def page_glossary() -> None:
    st.title("📖 GLOSSARY — plain-language definitions")
    terms = [
        ("Base rate", "The historical success rate for this kind of trial (by phase, disease, "
         "sponsor type). Our most-validated number — it's the statistical 'reality' the crowd's "
         "mood is measured against."),
        ("Edge gap", "Our predicted move minus the move the options market is pricing in. "
         "Positive = market underprices it (lean long). Negative = market overpays (lean fade/short)."),
        ("Composite score / grade", "Overall quality of a setup (0–1): blends how soon the catalyst "
         "is, the base rate, and the company's cash runway. NO market sentiment in it."),
        ("Trade types", "buy_the_rumor = ride into the event, sell BEFORE the result. "
         "hold_through = hold through the result. fade = short an overhyped name. avoid = no trade."),
        ("Suggested / target weight", "Fraction of your book the model would put on this name "
         "(Kelly-fractional, capped). Long is +, short is −."),
        ("Implied move", "How big a move the options market expects around the event (from option "
         "prices). Our read on 'what the crowd has priced in.'"),
        ("Run-up (30d)", "How much the stock already moved in the last 30 days — proxy for how "
         "much hope is already baked in before the event."),
        ("Short % float", "Percent of tradeable shares sold short — a sentiment/positioning gauge."),
        ("Confidence", "How much to trust THIS row (more data + a reliable date = higher)."),
        ("Unrealized P&L", "Paper gain/loss on open positions at the latest close (not yet sold)."),
        ("Gross long / short", "Total size of your long (or short) positions as % of account value."),
        ("Net exposure", "Gross long minus gross short. +60% means net 60% bullish."),
        ("Account value (equity)", "Cash + market value of your positions. Your true net worth in "
         "this account."),
    ]
    for name, desc in terms:
        st.markdown(f"**{name}** — {desc}")


# ===========================================================================
@st.cache_data(ttl=300)
def load_action_book(horizon_days: int) -> dict:
    return compute_book(horizon_days=horizon_days)


def page_action_sheet() -> None:
    st.title("📕 ACTION SHEET")
    st.caption("The risk-capped, executable book (after sector / GBM / gross / net caps). "
               "This is the Trade Blotter's raw signals turned into target weights you can size to.")

    horizon = st.sidebar.select_slider("Horizon (days)", [30, 90, 180, 365], value=365)
    book = load_action_book(horizon)
    rows = book["rows"]
    if not rows:
        st.warning("No sized positions. Run scripts/run_composite.py then scripts/action_sheet.py.")
        return

    caps = {"gross_long": 1.0, "gross_short": 0.30, "net": 0.60, "gbm": 0.25}
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Positions", book["positions"])
    c2.metric("Gross long", f"{book['gross_long']:.1%}", help="cap 100%")
    c3.metric("Gross short", f"{book['gross_short']:.1%}",
              delta="at cap" if book["gross_short"] >= caps["gross_short"] - 1e-3 else None,
              delta_color="off")
    c4.metric("Net", f"{book['net']:+.1%}", help="cap ±60%")
    c5.metric("GBM exposure", f"{book['gbm_pct']:.1%}",
              delta="at cap" if book["gbm_pct"] >= caps["gbm"] - 1e-3 else None,
              delta_color="off")

    today = pd.Timestamp(book["today"])
    prices = latest_prices()
    ensure_account()
    summ = pf.account_summary(_holding_dicts(load_holdings("open")), get_account()["cash"], prices)
    equity = summ["equity"] or 0.0
    df = pd.DataFrame(rows)
    df["expected_date"] = pd.to_datetime(df["expected_date"])
    df["days_until"] = (df["expected_date"] - today).dt.days
    df["timing"] = df["trade_type"].map(lambda t: TIMING.get(t, ""))
    df["side"] = df["weight"].map(lambda w: "LONG" if w > 0 else "SHORT")
    df["dollars"] = df["weight"].map(lambda w: round(w * equity, 0) if equity else None)
    df["shares"] = df.apply(
        lambda r: (round(abs(r["weight"] * equity) / prices[r["ticker"]], 0)
                   if equity and prices.get(r["ticker"]) else None), axis=1)

    view = pd.DataFrame({
        "ticker": df["ticker"],
        "trade": df["trade_type"],
        "side": df["side"],
        "wt": df["weight"],
        "$ target": df["dollars"],
        "~shares": df["shares"],
        "date": df["expected_date"].dt.date.astype("object"),
        "d->": df["days_until"],
        "base": df["base_rate"],
        "gap": df["edge_gap"],
        "conf": df["confidence"],
        "sector": df["sector"],
        "gbm": df["is_gbm"].map(lambda x: "★" if x else ""),
        "timing": df["timing"],
    })

    def color_trade(val):
        return f"color: {TRADE_COLORS.get(val, '#cfd3dc')}; font-weight:700"

    styled = (
        view.style
        .map(color_trade, subset=["trade"])
        .format({"wt": "{:+.3f}", "$ target": "${:,.0f}", "~shares": "{:,.0f}",
                 "base": "{:.2f}", "gap": "{:+.2f}", "conf": "{:.2f}",
                 "d->": "{:.0f}"}, na_rep="—")
    )
    if not equity:
        st.caption("Set your account value on the Portfolio page to see $ and share sizing.")
    st.dataframe(styled, use_container_width=True, height=560, hide_index=True)

    csv = view.to_csv(index=False).encode("utf-8")
    st.download_button("⬇ Download action sheet (CSV)", csv,
                       file_name=f"action_sheet_{book['today']}.csv", mime="text/csv")
    st.caption("wt = capped target weight (long +, short −). gap = model move − implied move. "
               "★ = GBM flagship. Longs lean on the validated base-rate edge; shorts are "
               "unvalidated — size accordingly.")


# ===========================================================================
def page_blotter() -> None:
    st.title("📟 TRADE BLOTTER")
    df = load_blotter()
    if df.empty:
        st.warning("No edge scores. Run scripts/run_composite.py.")
        return

    st.sidebar.subheader("Filters")
    horizon = st.sidebar.select_slider("Horizon (days)", [30, 90, 180, 365, 9999], value=365)
    only_gbm = st.sidebar.checkbox("GBM flagship only", value=False)
    types = sorted(df["trade_type"].dropna().unique().tolist())
    sel = st.sidebar.multiselect("Trade type", types,
                                 default=[t for t in types if t != "avoid"])
    min_w = st.sidebar.slider("Min |suggested weight|", 0.0, 0.05, 0.0, 0.005)

    f = df.copy()
    f = f[(f["days_until"].isna()) | (f["days_until"] <= horizon)]
    if only_gbm:
        f = f[f["is_gbm_focused"] == True]  # noqa: E712
    if sel:
        f = f[f["trade_type"].isin(sel)]
    f = f[f["suggested_weight"].abs().fillna(0) >= min_w]
    f = f.reindex(f["suggested_weight"].abs().sort_values(ascending=False).index)

    longs = f[f["suggested_weight"] > 0]["suggested_weight"].sum()
    shorts = f[f["suggested_weight"] < 0]["suggested_weight"].sum()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Signals", len(f))
    c2.metric("Buy-the-rumor", int((f["trade_type"] == "buy_the_rumor").sum()))
    c3.metric("Fade", int((f["trade_type"] == "fade").sum()))
    c4.metric("Gross long", f"{longs:.1%}")
    c5.metric("Gross short", f"{shorts:.1%}")

    counts = f["trade_type"].value_counts().reset_index()
    counts.columns = ["trade_type", "n"]
    if not counts.empty:
        fig = px.bar(counts, x="n", y="trade_type", orientation="h", color="trade_type",
                     color_discrete_map=TRADE_COLORS)
        fig.update_layout(height=180, showlegend=False, margin=dict(l=10, r=10, t=10, b=10),
                          paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11",
                          font_color="#cfd3dc", yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    view = pd.DataFrame({
        "ticker": f["ticker"],
        "trade": f["trade_type"],
        "wt": f["suggested_weight"],
        "catalyst": f["catalyst_type"],
        "date": f["expected_date"].dt.date.astype("object"),
        "d->": f["days_until"],
        "comp": f["composite_score"],
        "base": f["base_rate"],
        "exp_mv": f["expected_move"],
        "impl_mv": f["implied_move"],
        "gap": f["edge_gap"],
        "runup30": f["run_up_30d"],
        "fin": f["financing_tilt"],
        "ins": f["insider_tilt"],
        "runway": f["runway_months"],
        "gbm": f["is_gbm_focused"].map(lambda x: "★" if x else ""),
    })

    def color_trade(val):
        return f"color: {TRADE_COLORS.get(val, '#cfd3dc')}; font-weight:700"

    styled = (
        view.style
        .map(color_trade, subset=["trade"])
        .format({"wt": "{:+.3f}", "comp": "{:.2f}", "base": "{:.2f}", "exp_mv": "{:.2f}",
                 "impl_mv": "{:.2f}", "gap": "{:+.2f}", "runup30": "{:+.0%}",
                 "fin": "{:+.2f}", "ins": "{:+.2f}", "runway": "{:.0f}", "d->": "{:.0f}"},
                na_rep="—")
    )
    st.dataframe(styled, use_container_width=True, height=560)
    st.caption("wt = signed Kelly-fractional weight (long +, short -). "
               "gap = model expected move - market implied move. ★ = GBM flagship.")


# ===========================================================================
def page_security() -> None:
    st.title("🔬 SECURITY")
    companies = q("SELECT id, ticker, name FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")
    if companies.empty:
        st.warning("No companies.")
        return
    ticker = st.selectbox("Ticker", companies["ticker"].tolist())
    crow = companies[companies["ticker"] == ticker].iloc[0]
    cid = int(crow["id"])

    prices = q(
        "SELECT date, close, volume FROM price_history WHERE company_id=%s "
        "AND close IS NOT NULL ORDER BY date", (cid,)
    )
    pos = q(
        "SELECT short_pct_float, implied_move_pct, run_up_30d, atm_iv, days_to_cover "
        "FROM positioning WHERE company_id=%s ORDER BY date DESC LIMIT 1", (cid,)
    )
    cats = q(
        "SELECT catalyst_type, expected_date FROM catalysts WHERE company_id=%s "
        "AND expected_date IS NOT NULL ORDER BY expected_date", (cid,)
    )
    insiders = q(
        "SELECT filing_date, transaction_date, insider_name, insider_role, "
        "transaction_code, shares, price_per_share, value_usd, is_purchase "
        "FROM insider_transactions WHERE company_id=%s ORDER BY transaction_date DESC LIMIT 50", (cid,)
    )

    m = st.columns(5)
    m[0].metric("Short % float", f"{float(pos['short_pct_float'][0])*100:.1f}%"
                if not pos.empty and pd.notna(pos['short_pct_float'][0]) else "—")
    m[1].metric("Implied move", f"{float(pos['implied_move_pct'][0])*100:.0f}%"
                if not pos.empty and pd.notna(pos['implied_move_pct'][0]) else "—")
    m[2].metric("Run-up 30d", f"{float(pos['run_up_30d'][0])*100:+.0f}%"
                if not pos.empty and pd.notna(pos['run_up_30d'][0]) else "—")
    m[3].metric("ATM IV", f"{float(pos['atm_iv'][0])*100:.0f}%"
                if not pos.empty and pd.notna(pos['atm_iv'][0]) else "—")
    m[4].metric("Days to cover", f"{float(pos['days_to_cover'][0]):.1f}"
                if not pos.empty and pd.notna(pos['days_to_cover'][0]) else "—")

    if prices.empty:
        st.info("No price history. Run scripts/ingest_prices.py.")
    else:
        prices["date"] = pd.to_datetime(prices["date"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=prices["date"], y=prices["close"], mode="lines",
                                 name="close", line=dict(color="#3aa0ff", width=1.4)))
        # catalyst markers
        for _, cr in cats.iterrows():
            d = pd.to_datetime(cr["expected_date"])
            fig.add_vline(x=d, line_dash="dot", line_color="#f5a623", opacity=0.6)
        # insider purchase markers
        buys = insiders[insiders["is_purchase"] == True]  # noqa: E712
        if not buys.empty:
            buys = buys.copy()
            buys["transaction_date"] = pd.to_datetime(buys["transaction_date"])
            merged = pd.merge_asof(
                buys.sort_values("transaction_date"),
                prices.sort_values("date"),
                left_on="transaction_date", right_on="date", direction="nearest",
            )
            fig.add_trace(go.Scatter(
                x=merged["transaction_date"], y=merged["close"], mode="markers",
                name="insider buy", marker=dict(color="#29d391", size=9, symbol="triangle-up")))
        fig.update_layout(height=380, paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11",
                          font_color="#cfd3dc", margin=dict(l=10, r=10, t=30, b=10),
                          title=f"{ticker} close · amber=catalyst · green=insider buy")
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Insider transactions")
        if insiders.empty:
            st.caption("No Form 4 data. Run scripts/ingest_insider.py.")
        else:
            st.dataframe(insiders, use_container_width=True, hide_index=True, height=300)
    with right:
        st.subheader("Catalysts")
        if cats.empty:
            st.caption("No dated catalysts.")
        else:
            st.dataframe(cats, use_container_width=True, hide_index=True, height=300)


# ===========================================================================
def page_calendar() -> None:
    st.title("📅 CATALYST CALENDAR")
    df = load_blotter()
    if df.empty:
        st.warning("No data.")
        return
    cal = df.dropna(subset=["expected_date"]).copy()
    today = pd.Timestamp(date.today())
    cal = cal[(cal["expected_date"] >= today - pd.Timedelta(days=30)) &
              (cal["expected_date"] <= today + pd.Timedelta(days=365))]
    if cal.empty:
        st.info("No catalysts in window.")
        return
    cal["size_val"] = cal["suggested_weight"].abs().fillna(0.005).clip(lower=0.005)
    fig = px.scatter(cal, x="expected_date", y="ticker", size="size_val",
                     color="trade_type", color_discrete_map=TRADE_COLORS, size_max=20,
                     hover_data=["company", "catalyst_type", "composite_score", "suggested_weight"])
    fig.add_vline(x=today, line_dash="dash", line_color="#888")
    fig.update_layout(height=max(420, 16 * cal["ticker"].nunique()),
                      paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11", font_color="#cfd3dc",
                      margin=dict(l=10, r=10, t=30, b=10), legend_title_text="",
                      xaxis_title="", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Dot size ∝ |suggested weight| · dashed line = today")


# ===========================================================================
def page_validation() -> None:
    st.title("✅ VALIDATION")

    # --- Event-study evidence: the REAL returns dataset (8-K reactions) ---
    st.subheader("Event-study evidence — realized returns around 8-K announcements")
    st.caption("Each 8-K is a market-moving filing. We measure the abnormal return "
               "(stock move minus XBI) over a short hold. This is our ground truth for "
               "whether signals predict profit. Build/refresh: python scripts/build_event_returns.py")
    hold = st.radio("Hold window (trading days)", [1, 3, 5], index=1, horizontal=True,
                    key="evt_hold")
    er = q("SELECT abnormal_return, run_up_30d, event_type FROM event_returns "
           "WHERE hold_days = %(h)s AND abnormal_return IS NOT NULL", {"h": int(hold)})
    if er.empty:
        st.info("No event_returns yet. Run: python scripts/build_event_returns.py")
    else:
        er["abnormal_return"] = pd.to_numeric(er["abnormal_return"], errors="coerce")
        er["run_up_30d"] = pd.to_numeric(er["run_up_30d"], errors="coerce")
        ar = er["abnormal_return"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Events", f"{len(ar):,}")
        m2.metric("Median abnormal", f"{ar.median():+.1%}")
        m3.metric("Std (dispersion)", f"{ar.std():.1%}")
        m4.metric("|move| ≥ 25%", f"{(ar.abs() >= 0.25).mean():.0%}")
        st.caption("Takeaway: biotech 8-K reactions are extremely high-variance — that "
                   "dispersion is the opportunity AND the risk. Median near zero means "
                   "most filings are noise; the edge is in selecting which.")

        paired = er.dropna(subset=["run_up_30d"]).copy()
        if len(paired) >= 25:
            paired["run_up_30d"] = paired["run_up_30d"].astype(float)
            paired["q"] = pd.qcut(paired["run_up_30d"], 5,
                                  labels=["Q1 crashed", "Q2", "Q3", "Q4", "Q5 mooned"],
                                  duplicates="drop")
            grp = paired.groupby("q", observed=True)["abnormal_return"].mean().reset_index()
            fig = px.bar(grp, x="q", y="abnormal_return",
                         color="abnormal_return",
                         color_continuous_scale=["#ff5c5c", "#6b7280", "#29d391"],
                         title="Forward abnormal return by PRE-event run-up (the sentiment-gap test)")
            fig.update_layout(height=320, showlegend=False, coloraxis_showscale=False,
                              paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11",
                              font_color="#cfd3dc", yaxis_tickformat=".1%",
                              xaxis_title="", yaxis_title="avg forward abnormal")
            st.plotly_chart(fig, use_container_width=True)
            corr = paired["run_up_30d"].corr(paired["abnormal_return"])
            st.caption(f"Finding: the relationship is a weak **barbell**, not a clean fade "
                       f"(corr={corr:+.3f}). Names that already crashed (Q1) or already "
                       f"mooned (Q5) underperform; the middle drifts up. Pure 'fade the "
                       f"run-up' is weak — only the most extreme run-ups give back, and only "
                       f"slightly. Treat current fade signals with caution.")

        bytype = er.dropna(subset=["event_type"])
        if not bytype.empty:
            tt = (bytype.groupby("event_type")["abnormal_return"]
                  .agg(["mean", "median", "count"]).reset_index()
                  .sort_values("mean"))
            tt["mean"] = (tt["mean"] * 100).round(2).astype(str) + "%"
            tt["median"] = (tt["median"] * 100).round(2).astype(str) + "%"
            st.markdown("**Sanity check — abnormal return by labeled event type:**")
            st.dataframe(tt, use_container_width=True, hide_index=True)
            st.caption("Offerings/license deals skew negative, approvals positive — the "
                       "signs match reality, which validates the abnormal-return math. "
                       "(Labeled subset is small; signs > magnitudes here.)")

    st.divider()
    runs = q("SELECT * FROM calibration_runs ORDER BY run_at DESC LIMIT 1")
    outcomes = q("SELECT outcome_label, COUNT(*) n FROM catalyst_outcomes GROUP BY outcome_label")

    c1, c2, c3 = st.columns(3)
    if runs.empty:
        c1.metric("Brier score", "—")
        c2.metric("Model hit rate", "—")
        c3.metric("Base-rate hit rate", "—")
        st.info("No calibration run yet. Run scripts/calibrate.py after resolve_outcomes.py.")
    else:
        r = runs.iloc[0]
        c1.metric("Brier score", f"{float(r['brier_score']):.3f}" if pd.notna(r['brier_score']) else "—")
        c2.metric("Model hit rate", f"{float(r['model_hit_rate']):.0%}" if pd.notna(r['model_hit_rate']) else "—")
        c3.metric("Base-rate hit rate", f"{float(r['base_rate_hit_rate']):.0%}" if pd.notna(r['base_rate_hit_rate']) else "—")
        rel = r.get("reliability_json")
        if rel:
            try:
                rel_df = pd.DataFrame(rel if isinstance(rel, list) else pd.read_json(rel))
                if not rel_df.empty:
                    fig = px.line(rel_df, x="mean_predicted", y="observed_hit_rate",
                                  markers=True, range_x=[0, 1], range_y=[0, 1])
                    fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1,
                                  line=dict(dash="dash", color="#666"))
                    fig.update_layout(height=340, paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11",
                                      font_color="#cfd3dc", title="Reliability (diagonal = perfect)")
                    st.plotly_chart(fig, use_container_width=True)
            except Exception:
                st.caption("Reliability data unavailable.")

    st.subheader("Resolved outcome distribution")
    if outcomes.empty:
        st.caption("No resolved outcomes. Run scripts/resolve_outcomes.py.")
    else:
        fig = px.bar(outcomes, x="outcome_label", y="n", color="outcome_label",
                     color_discrete_map={"hit": "#29d391", "miss": "#ff5c5c", "ambiguous": "#6b7280"})
        fig.update_layout(height=280, showlegend=False, paper_bgcolor="#0b0e11",
                          plot_bgcolor="#0b0e11", font_color="#cfd3dc")
        st.plotly_chart(fig, use_container_width=True)

    bt = PROJECT_ROOT / "data" / "raw" / "backtest_trades.csv"
    st.subheader("Backtest trades")
    if bt.exists():
        st.dataframe(pd.read_csv(bt), use_container_width=True, hide_index=True, height=300)
    else:
        st.caption("Run: python scripts/backtest.py --csv data/raw/backtest_trades.csv")


# ===========================================================================
def page_health() -> None:
    st.title("🩺 DATA HEALTH")
    tables = ["companies", "trials", "catalysts", "edge_scores", "price_history",
              "positioning", "insider_transactions", "catalyst_outcomes", "financials"]
    counts = {t: int(q(f"SELECT COUNT(*) n FROM {t}").iloc[0, 0]) for t in tables}
    cols = st.columns(len(counts))
    for col, (name, n) in zip(cols, counts.items()):
        col.metric(name, f"{n:,}")

    st.subheader("Signal coverage (in-universe companies with tickers)")
    cov = q(
        """
        SELECT
          (SELECT COUNT(*) FROM companies WHERE ticker IS NOT NULL AND COALESCE(in_universe,TRUE)) AS universe,
          (SELECT COUNT(DISTINCT company_id) FROM price_history WHERE company_id IS NOT NULL) AS with_prices,
          (SELECT COUNT(DISTINCT company_id) FROM positioning) AS with_positioning,
          (SELECT COUNT(DISTINCT company_id) FROM insider_transactions) AS with_insider,
          (SELECT COUNT(*) FROM companies WHERE is_gbm_focused) AS gbm_focused
        """
    )
    if not cov.empty:
        st.dataframe(cov, use_container_width=True, hide_index=True)

    st.subheader("Freshness")
    specs = [("price_history", "fetched_at"), ("positioning", "computed_at"),
             ("insider_transactions", "created_at"), ("edge_scores", "computed_at"),
             ("catalyst_outcomes", "created_at")]
    rows = []
    for tbl, col in specs:
        ts = q(f"SELECT MAX({col}) ts FROM {tbl}").iloc[0, 0]
        rows.append({"table": tbl, "last_updated": ts})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


PAGES = {
    "Home / Cockpit": page_home,
    "Portfolio": page_portfolio,
    "Action Sheet": page_action_sheet,
    "Trade Blotter": page_blotter,
    "Security": page_security,
    "Catalyst Calendar": page_calendar,
    "Validation": page_validation,
    "Glossary": page_glossary,
    "Data Health": page_health,
}


def main() -> None:
    st.sidebar.title("📟 EDGE TERMINAL")
    st.sidebar.caption("Rung 2 · decision support · read-only")
    choice = st.sidebar.radio("Panel", list(PAGES.keys()))
    st.sidebar.markdown("---")
    PAGES[choice]()
    st.sidebar.caption("Data cached 5 min")


if __name__ == "__main__":
    main()
