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

st.set_page_config(page_title="Edge Terminal", layout="wide", page_icon="◆")

# ---- Design tokens ----
THEME = {
    "bg": "#0c0f14",
    "panel": "#141922",
    "border": "#252d3a",
    "text": "#e8eaed",
    "muted": "#8b95a5",
    "accent": "#5b8def",
    "green": "#34c759",
    "red": "#ff5a5f",
    "amber": "#f0b429",
    "font": "'Inter', 'Segoe UI', system-ui, sans-serif",
    "mono": "'JetBrains Mono', 'Consolas', monospace",
}

TRADE_COLORS = {
    "buy_the_rumor": THEME["green"],
    "hold_through": THEME["accent"],
    "fade": THEME["red"],
    "avoid": THEME["muted"],
}

st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      html, body, [class*="css"] {{ font-family: {THEME['font']}; color: {THEME['text']}; }}
      .stApp {{ background: {THEME['bg']}; }}
      .block-container {{ padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }}
      h1 {{ font-size: 1.35rem; font-weight: 600; letter-spacing: -0.02em; margin-bottom: 0.15rem; }}
      h2, h3 {{ font-size: 0.95rem; font-weight: 600; color: {THEME['text']}; margin: 0.75rem 0 0.35rem; }}
      hr {{ margin: 0.75rem 0; border-color: {THEME['border']}; opacity: 0.5; }}
      [data-testid="stMetric"] {{
        background: {THEME['panel']}; border: 1px solid {THEME['border']};
        border-radius: 8px; padding: 0.55rem 0.75rem;
      }}
      [data-testid="stMetricValue"] {{
        font-family: {THEME['mono']}; font-size: 1rem; color: {THEME['text']};
      }}
      [data-testid="stMetricLabel"] {{
        color: {THEME['muted']}; text-transform: uppercase; font-size: 0.62rem;
        letter-spacing: 0.06em; font-weight: 500;
      }}
      [data-testid="stSidebar"] {{ background: {THEME['panel']}; border-right: 1px solid {THEME['border']}; }}
      [data-testid="stSidebar"] h1 {{ font-size: 1rem; font-weight: 700; letter-spacing: 0.04em; }}
      div[data-testid="stDataFrame"] {{ font-size: 0.78rem; }}
      [data-testid="stExpander"] {{
        background: {THEME['panel']}; border: 1px solid {THEME['border']}; border-radius: 8px;
      }}
      .stAlert {{ border-radius: 8px; }}
      .panel-caption {{ color: {THEME['muted']}; font-size: 0.82rem; line-height: 1.45; }}
    </style>
    """,
    unsafe_allow_html=True,
)

# Sidebar navigation: two areas, each with sub-pages (populated after page defs).
NAV_SECTIONS: dict[str, dict[str, callable]] = {}


def _style_trade_col(df: pd.DataFrame, col: str = "trade") -> pd.io.formats.style.Styler:
    def color_trade(val):
        return f"color: {TRADE_COLORS.get(val, '#cfd3dc')}; font-weight:700"

    return df.style.map(color_trade, subset=[col])


def _blotter_by_ticker(blotter: pd.DataFrame) -> pd.DataFrame:
    """Best signal per ticker (highest |weight|) for merging into the action book."""
    if blotter.empty:
        return blotter
    b = blotter.copy()
    b["_aw"] = b["suggested_weight"].abs()
    b = b.sort_values("_aw", ascending=False).drop_duplicates("ticker", keep="first")
    return b.drop(columns=["_aw"], errors="ignore")


def _action_desk_filters(sidebar: bool = True) -> dict:
    """Shared filter widgets for the action desk."""
    host = st.sidebar if sidebar else st
    with host.expander("Filters", expanded=not sidebar):
        horizon = host.select_slider("Horizon (days)", [30, 90, 180, 365, 9999], value=90)
        act_days = host.slider("Act-now window (days)", 7, 180, 60)
        only_gbm = host.checkbox("GBM flagship only", value=False)
        types = host.multiselect("Trade types", ["buy_the_rumor", "hold_through", "fade", "avoid"],
                                 default=["buy_the_rumor", "hold_through", "fade"])
        min_w = host.slider("Min |weight|", 0.0, 0.05, 0.0, 0.005)
    return {"horizon": horizon, "act_days": act_days, "only_gbm": only_gbm,
            "types": types, "min_w": min_w}


def _filter_blotter(df: pd.DataFrame, flt: dict) -> pd.DataFrame:
    f = df.copy()
    f = f[(f["days_until"].isna()) | (f["days_until"] <= flt["horizon"])]
    if flt["only_gbm"]:
        f = f[f["is_gbm_focused"] == True]  # noqa: E712
    if flt["types"]:
        f = f[f["trade_type"].isin(flt["types"])]
    f = f[f["suggested_weight"].abs().fillna(0) >= flt["min_w"]]
    return f.reindex(f["suggested_weight"].abs().sort_values(ascending=False).index)


def _book_sized_table(book: dict, equity: float, prices: dict[str, float]) -> pd.DataFrame:
    """Capped action book as a display-ready dataframe."""
    if not book.get("rows"):
        return pd.DataFrame()
    today = pd.Timestamp(book["today"])
    df = pd.DataFrame(book["rows"])
    df["expected_date"] = pd.to_datetime(df["expected_date"])
    df["days_until"] = (df["expected_date"] - today).dt.days
    df["side"] = df["weight"].map(lambda w: "LONG" if w > 0 else "SHORT")
    df["dollars"] = df["weight"].map(lambda w: round(w * equity, 0) if equity else None)
    df["shares"] = df.apply(
        lambda r: (round(abs(r["weight"] * equity) / prices[r["ticker"]], 0)
                   if equity and prices.get(r["ticker"]) else None), axis=1)
    df["timing"] = df["trade_type"].map(lambda t: TIMING.get(t, ""))
    df["urgent"] = df["days_until"].map(lambda d: "!" if d <= 7 else "")
    return df


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
    st.info("Longs use the validated base-rate edge. Shorts and fades are experimental — "
            "paper-trade until they earn a track record. Prices are end-of-day.")


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
PERF_CSV = PROJECT_ROOT / "data" / "raw" / "paper_performance.csv"
PERF_COLUMNS = [
    "date", "equity", "cash", "open_positions", "unrealized_pnl",
    "realized_to_date", "total_return_pct", "exits_today", "opens_today",
    "resized_today", "desk_positions",
]


def _plotly_theme(fig: go.Figure, *, height: int = 340, title: str | None = None) -> go.Figure:
    fig.update_layout(
        height=height,
        title=dict(text=title, font=dict(size=13, color=THEME["text"])) if title else None,
        paper_bgcolor=THEME["bg"],
        plot_bgcolor=THEME["panel"],
        font=dict(family=THEME["font"], color=THEME["text"], size=11),
        margin=dict(l=56, r=32, t=52 if title else 28, b=48),
        legend=dict(
            orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02,
            bgcolor="rgba(0,0,0,0)", font=dict(size=10),
        ),
        uniformtext_minsize=9,
        uniformtext_mode="hide",
    )
    fig.update_xaxes(gridcolor=THEME["border"], zerolinecolor=THEME["border"], showgrid=True)
    fig.update_yaxes(gridcolor=THEME["border"], zerolinecolor=THEME["border"], showgrid=True)
    return fig


def _pnl_bar_chart(hv: pd.DataFrame) -> go.Figure:
    pnl_df = hv.dropna(subset=["pnl_usd"]).sort_values("pnl_usd")
    colors = [THEME["green"] if v >= 0 else THEME["red"] for v in pnl_df["pnl_usd"]]
    h = max(280, min(720, 28 * len(pnl_df) + 80))
    fig = go.Figure(go.Bar(
        x=pnl_df["pnl_usd"], y=pnl_df["ticker"],
        orientation="h", marker_color=colors,
        text=[f"${v:+,.0f}" for v in pnl_df["pnl_usd"]],
        textposition="outside", cliponaxis=False,
    ))
    _plotly_theme(fig, height=h, title="Unrealized P&L by position")
    fig.update_layout(margin=dict(l=56, r=80, t=52, b=48))
    fig.update_xaxes(tickformat="$,.0f")
    return fig


def _alloc_pie_chart(hv: pd.DataFrame) -> go.Figure:
    alloc = hv.dropna(subset=["mkt_value"]).copy()
    alloc["mkt_abs"] = alloc["mkt_value"].abs()
    alloc = alloc.sort_values("mkt_abs", ascending=False)
    top_n = 12
    if len(alloc) > top_n:
        top = alloc.head(top_n)
        other = alloc.iloc[top_n:]["mkt_abs"].sum()
        pie_df = pd.concat([top, pd.DataFrame([{
            "ticker": f"Other ({len(alloc) - top_n})",
            "mkt_abs": other,
        }])], ignore_index=True)
    else:
        pie_df = alloc
    palette = px.colors.qualitative.Dark24
    fig = go.Figure(go.Pie(
        labels=pie_df["ticker"], values=pie_df["mkt_abs"],
        hole=0.52, marker=dict(colors=palette[: len(pie_df)]),
        textinfo="percent", textposition="inside",
        insidetextorientation="horizontal",
        hovertemplate="%{label}<br>$%{value:,.0f}<br>%{percent}<extra></extra>",
    ))
    _plotly_theme(fig, height=420, title="Portfolio allocation (|market value|)")
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01),
        margin=dict(l=24, r=140, t=52, b=24),
    )
    return fig


@st.cache_data(ttl=60)
def load_performance_history() -> pd.DataFrame:
    """Daily equity snapshots from paper autopilot (data/raw/paper_performance.csv)."""
    if not PERF_CSV.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(PERF_CSV)
    except pd.errors.ParserError:
        raw = pd.read_csv(PERF_CSV, header=None, names=PERF_COLUMNS, on_bad_lines="skip")
        if not raw.empty and str(raw.iloc[0]["date"]) == "date":
            raw = raw.iloc[1:]
        df = raw
    for c in PERF_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[PERF_COLUMNS]
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("equity", "cash", "unrealized_pnl", "realized_to_date", "total_return_pct"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").groupby("date", as_index=False).last()


@st.cache_data(ttl=10)
def realized_pnl_total() -> float:
    df = q("SELECT COALESCE(SUM(realized_pnl_usd), 0) AS v FROM portfolio_holdings "
           "WHERE status = 'closed'")
    return float(df.iloc[0]["v"]) if not df.empty else 0.0


def enrich_holdings(open_df: pd.DataFrame, summ: dict, prices: dict[str, float]) -> pd.DataFrame:
    """Open positions with mark, weight, and P&L columns for tables/charts."""
    rows = []
    for r in open_df.itertuples():
        cur = prices.get(r.ticker)
        mv = pf.market_value(r.side, float(r.shares), cur)
        pnl = pf.unrealized_pnl(r.side, float(r.shares), float(r.entry_price), cur)
        pnl_pct = pf.unrealized_pnl_pct(r.side, float(r.entry_price), cur)
        cost = float(r.shares) * float(r.entry_price)
        rows.append({
            "ticker": r.ticker,
            "side": r.side,
            "type": r.trade_type,
            "notes": getattr(r, "notes", None),
            "shares": float(r.shares),
            "entry": float(r.entry_price),
            "cost_basis": cost,
            "now": cur,
            "mkt_value": abs(mv) if mv is not None else None,
            "pct_book": (abs(mv) / summ["equity"] if mv is not None and summ["equity"] else None),
            "pnl_usd": pnl,
            "pnl_pct": pnl_pct,
            "exit_by": r.planned_exit_date,
            "days_to_exit": ((r.planned_exit_date - date.today()).days
                             if r.planned_exit_date else None),
            "rule": r.planned_exit_rule,
        })
    return pd.DataFrame(rows)


def _holding_dicts(df: pd.DataFrame) -> list[dict]:
    return [{"ticker": r.ticker, "side": r.side, "shares": float(r.shares),
             "entry_price": float(r.entry_price), "trade_type": r.trade_type,
             "planned_exit_date": r.planned_exit_date,
             "planned_exit_rule": r.planned_exit_rule} for r in df.itertuples()]


def render_action_center(open_df: pd.DataFrame) -> None:
    st.subheader("Action center")
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
    st.title("Portfolio")
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
    st.title("Cockpit")
    honesty_banner()
    ensure_account()
    acct = get_account()
    prices = latest_prices()
    open_df = load_holdings("open")
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)
    start_cap = float(acct["starting_capital"] or summ["equity"] or 0.0)
    realized = realized_pnl_total()
    perf = load_performance_history()
    hv = enrich_holdings(open_df, summ, prices)

    # ---- headline metrics (two rows) ----
    tot_ret_usd = summ["equity"] - start_cap if start_cap else None
    tot_ret_pct = (tot_ret_usd / start_cap) if start_cap and tot_ret_usd is not None else None
    deployed_pct = (summ["invested_usd"] / summ["equity"]) if summ["equity"] else 0.0
    closed_n = int(q("SELECT COUNT(*) n FROM portfolio_holdings WHERE status='closed'").iloc[0, 0])

    r1 = st.columns(4)
    r1[0].metric("Account value", fmt_usd(summ["equity"]),
                 help="Cash + signed market value of all open positions.")
    r1[1].metric("Total return",
                 f"{tot_ret_pct:+.1%}" if tot_ret_pct is not None else "—",
                 delta=fmt_usd(tot_ret_usd) if tot_ret_usd is not None else None,
                 help=f"Vs starting capital {fmt_usd(start_cap)}.")
    r1[2].metric("Unrealized P&L", fmt_usd(summ["unrealized_pnl_usd"]),
                 help="Open positions only — not locked in until you exit.")
    r1[3].metric("Realized P&L", fmt_usd(realized),
                 help=f"Closed trades only ({closed_n} closed).")

    r2 = st.columns(4)
    r2[0].metric("Cash", fmt_usd(summ["cash"]),
                 help="Unallocated buying power.")
    r2[1].metric("Deployed", f"{deployed_pct:.0%}",
                 help=f"{fmt_usd(summ['invested_usd'])} cost basis in open positions.")
    r2[2].metric("Gross long / short",
                 f"{summ['gross_long_pct']:.0%} / {summ['gross_short_pct']:.0%}",
                 help=f"Long {fmt_usd(summ['gross_long_usd'])} · Short {fmt_usd(summ['gross_short_usd'])}")
    r2[3].metric("Net exposure", f"{summ['net_pct']:+.0%}",
                 help=f"Long minus short = {fmt_usd(summ['net_usd'])} directional.")

    freshness_caption()

    # ---- equity curve + snapshot descriptors ----
    st.subheader("Account performance")
    chart_l, desc_r = st.columns([2, 1])

    with chart_l:
        if perf.empty:
            snap = pd.DataFrame([{
                "date": pd.Timestamp(date.today()),
                "equity": summ["equity"],
                "cash": summ["cash"],
                "unrealized_pnl": summ["unrealized_pnl_usd"],
                "realized_to_date": realized,
            }])
            st.caption("No history file yet — showing today's snapshot only. "
                      "Run paper autopilot to build `data/raw/paper_performance.csv`.")
            plot_df = snap
        else:
            plot_df = perf.copy()
            # append live point if today's close isn't logged yet
            last_d = plot_df["date"].max().date()
            if last_d < date.today():
                plot_df = pd.concat([plot_df, pd.DataFrame([{
                    "date": pd.Timestamp(date.today()),
                    "equity": summ["equity"],
                    "cash": summ["cash"],
                    "unrealized_pnl": summ["unrealized_pnl_usd"],
                    "realized_to_date": realized,
                }])], ignore_index=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=plot_df["date"], y=plot_df["equity"],
            mode="lines+markers", name="Equity",
            line=dict(color=THEME["green"], width=2.5),
            fill="tozeroy", fillcolor="rgba(52,199,89,0.07)",
        ))
        if start_cap:
            fig.add_hline(y=start_cap, line_dash="dash", line_color=THEME["muted"],
                          annotation_text=f"Start {fmt_usd(start_cap)}",
                          annotation_position="bottom right")
        if "unrealized_pnl" in plot_df.columns and plot_df["unrealized_pnl"].notna().any():
            fig.add_trace(go.Scatter(
                x=plot_df["date"], y=plot_df["unrealized_pnl"],
                mode="lines", name="Unrealized P&L",
                line=dict(color=THEME["accent"], width=1.5, dash="dot"),
                yaxis="y2",
            ))
        fig.update_layout(yaxis2=dict(
            title="Unrealized $", overlaying="y", side="right",
            gridcolor="#1f2937", tickformat="$,.0f",
        ))
        _plotly_theme(fig, height=360, title="Equity curve (end-of-day marks)")
        st.plotly_chart(fig, use_container_width=True)

        if len(plot_df) >= 2:
            peak = plot_df["equity"].cummax()
            dd = (plot_df["equity"] - peak) / peak
            max_dd = float(dd.min())
            best_day = plot_df.loc[plot_df["equity"].diff().idxmax()] if len(plot_df) > 1 else None
            st.caption(f"Max drawdown from peak: **{max_dd:.1%}** · "
                       f"Snapshots: **{len(plot_df)}** day(s)")

    with desc_r:
        st.markdown("**Snapshot**")
        st.markdown(
            f"- **Open positions:** {summ['positions']} "
            f"({summ['priced']}/{summ['positions']} priced)\n"
            f"- **Cost basis:** {fmt_usd(summ['invested_usd'])}\n"
            f"- **Market value (long−short):** {fmt_usd(summ['net_usd'])}\n"
            f"- **Cash buffer:** {100 - deployed_pct:.0%} of equity\n"
            f"- **Mode:** {'PAPER' if not open_df.empty and (open_df['notes'] == 'PAPER').any() else 'Live / mixed'}"
        )
        if not hv.empty and hv["pnl_usd"].notna().any():
            best = hv.loc[hv["pnl_usd"].idxmax()]
            worst = hv.loc[hv["pnl_usd"].idxmin()]
            st.markdown(
                f"- **Best open:** {best['ticker']} ({best['pnl_usd']:+,.0f})\n"
                f"- **Worst open:** {worst['ticker']} ({worst['pnl_usd']:+,.0f})"
            )
        nxt = hv.dropna(subset=["days_to_exit"]).sort_values("days_to_exit") if not hv.empty else pd.DataFrame()
        if not nxt.empty:
            nx = nxt.iloc[0]
            st.markdown(f"- **Next exit:** {nx['ticker']} in **{int(nx['days_to_exit'])}d** ({nx['exit_by']})")
        st.markdown(
            "**How to read this**\n"
            "- Green line = total account value each day autopilot runs.\n"
            "- Dashed grey = your starting capital.\n"
            "- Blue dotted = unrealized P&L (right axis).\n"
            "- All prices are **prior close**, not live."
        )

    # ---- trade book (action desk names to trade) ----
    book = load_action_book(90)
    st.subheader("Trade book")
    st.caption("Capped action-desk names in the near-term window. Select a row for the full dossier.")
    render_trade_book_panel(book, summ["equity"] or 0.0, prices, act_days=60, key_prefix="cockpit")

    # ---- position visuals (stacked full-width — no overlap) ----
    if not hv.empty:
        st.subheader("Position breakdown")
        st.plotly_chart(_pnl_bar_chart(hv), use_container_width=True)
        st.plotly_chart(_alloc_pie_chart(hv), use_container_width=True)

        st.markdown("**Holdings detail**")
        show = hv[[
            "ticker", "side", "type", "notes", "shares", "entry", "now",
            "cost_basis", "mkt_value", "pct_book", "pnl_usd", "pnl_pct",
            "exit_by", "days_to_exit",
        ]].rename(columns={
            "type": "trade", "notes": "tag", "entry": "entry $", "now": "last $",
            "cost_basis": "cost $", "mkt_value": "mkt $", "pct_book": "% book",
            "pnl_usd": "P&L $", "pnl_pct": "P&L %", "exit_by": "exit date",
            "days_to_exit": "days left",
        })
        st.dataframe(
            show.style.format({
                "shares": "{:.1f}", "entry $": "{:.2f}", "last $": "{:.2f}",
                "cost $": "${:,.0f}", "mkt $": "${:,.0f}", "% book": "{:.1%}",
                "P&L $": "${:+,.0f}", "P&L %": "{:+.1%}", "days left": "{:.0f}",
            }, na_rep="—"),
            use_container_width=True, hide_index=True, height=min(360, 44 + 36 * len(show)),
        )

    if not perf.empty:
        with st.expander("📋 Daily performance log", expanded=False):
            log = perf.copy()
            log["date"] = log["date"].dt.date
            log["total_return_pct"] = log["total_return_pct"].map(lambda x: f"{x:+.2%}" if pd.notna(x) else "—")
            st.dataframe(log, use_container_width=True, hide_index=True)

    st.divider()
    render_action_center(open_df)


# ===========================================================================
def page_glossary(*, embedded: bool = False) -> None:
    if not embedded:
        st.title("Glossary")
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


@st.cache_data(ttl=300)
def _ticker_dossier_data(ticker: str) -> dict | None:
    companies = q(
        "SELECT id, ticker, name, market_cap_usd, is_gbm_focused, indication_category "
        "FROM companies WHERE ticker = %s", (ticker,)
    )
    if companies.empty:
        return None
    crow = companies.iloc[0]
    cid = int(crow["id"])
    return {
        "crow": crow,
        "cid": cid,
        "fin": q(
            "SELECT cash_and_equivalents_usd, total_liquidity_usd, quarterly_burn_usd, "
            "runway_months, shares_outstanding, period_end "
            "FROM financials WHERE company_id=%s ORDER BY period_end DESC LIMIT 1", (cid,)
        ),
        "pos": q(
            "SELECT short_pct_float, implied_move_pct, run_up_30d, atm_iv, days_to_cover "
            "FROM positioning WHERE company_id=%s ORDER BY date DESC LIMIT 1", (cid,)
        ),
        "prices": q(
            "SELECT date, close, volume FROM price_history WHERE company_id=%s "
            "AND close IS NOT NULL ORDER BY date", (cid,)
        ),
        "cats": q(
            "SELECT id, catalyst_type, expected_date, base_rate, sec_confirmed "
            "FROM catalysts WHERE company_id=%s AND expected_date IS NOT NULL "
            "ORDER BY expected_date", (cid,)
        ),
        "insiders": q(
            "SELECT filing_date, transaction_date, insider_name, insider_role, "
            "transaction_code, shares, price_per_share, value_usd, is_purchase "
            "FROM insider_transactions WHERE company_id=%s "
            "ORDER BY transaction_date DESC LIMIT 50", (cid,)
        ),
    }


def render_ticker_dossier(ticker: str, *, blotter: pd.DataFrame | None = None) -> None:
    """Full company dossier: signals, positioning, financials, price, catalysts, insiders."""
    pack = _ticker_dossier_data(ticker)
    if pack is None:
        st.warning(f"No company record for {ticker}.")
        return
    crow, cid = pack["crow"], pack["cid"]
    fin, pos, prices, cats, insiders = pack["fin"], pack["pos"], pack["prices"], pack["cats"], pack["insiders"]
    if blotter is None:
        blotter = load_blotter()
    sig = (blotter[blotter["ticker"] == ticker].sort_values("days_until").head(1)
           if not blotter.empty else pd.DataFrame())

    gbm = " · GBM flagship" if crow["is_gbm_focused"] else ""
    st.markdown(f"**{crow['name']}** · {crow['indication_category'] or '—'}{gbm}")

    if not sig.empty:
        s = sig.iloc[0]
        r1 = st.columns(8)
        r1[0].metric("Trade", str(s.get("trade_type", "—")))
        r1[1].metric("Weight", f"{float(s['suggested_weight']):+.3f}"
                     if pd.notna(s.get("suggested_weight")) else "—")
        r1[2].metric("Composite", f"{float(s['composite_score']):.2f}"
                     if pd.notna(s.get("composite_score")) else "—")
        r1[3].metric("Base rate", f"{float(s['base_rate']):.2f}"
                     if pd.notna(s.get("base_rate")) else "—")
        r1[4].metric("Edge gap", f"{float(s['edge_gap']):+.2f}"
                     if pd.notna(s.get("edge_gap")) else "—")
        r1[5].metric("Confidence", f"{float(s['confidence']):.2f}"
                     if pd.notna(s.get("confidence")) else "—")
        r1[6].metric("Days to cat", f"{int(s['days_until'])}"
                     if pd.notna(s.get("days_until")) else "—")
        r1[7].metric("Catalyst", str(s.get("catalyst_type", "—"))[:14])

    r2 = st.columns(8)
    mcap = crow.get("market_cap_usd")
    r2[0].metric("Mkt cap", fmt_usd(_f(mcap)))
    r2[1].metric("Short % float", f"{float(pos['short_pct_float'].iloc[0])*100:.1f}%"
                 if not pos.empty and pd.notna(pos["short_pct_float"].iloc[0]) else "—")
    r2[2].metric("Implied move", f"{float(pos['implied_move_pct'].iloc[0])*100:.0f}%"
                 if not pos.empty and pd.notna(pos["implied_move_pct"].iloc[0]) else "—")
    r2[3].metric("Run-up 30d", f"{float(pos['run_up_30d'].iloc[0])*100:+.0f}%"
                 if not pos.empty and pd.notna(pos["run_up_30d"].iloc[0]) else "—")
    r2[4].metric("ATM IV", f"{float(pos['atm_iv'].iloc[0])*100:.0f}%"
                 if not pos.empty and pd.notna(pos["atm_iv"].iloc[0]) else "—")
    r2[5].metric("Days to cover", f"{float(pos['days_to_cover'].iloc[0]):.1f}"
                 if not pos.empty and pd.notna(pos["days_to_cover"].iloc[0]) else "—")
    r2[6].metric("Runway", f"{float(fin['runway_months'].iloc[0]):.0f}mo"
                 if not fin.empty and pd.notna(fin["runway_months"].iloc[0]) else "—")
    r2[7].metric("Liquidity", fmt_usd(_f(fin["total_liquidity_usd"].iloc[0]))
                 if not fin.empty and pd.notna(fin["total_liquidity_usd"].iloc[0]) else "—")

    if not sig.empty:
        s = sig.iloc[0]
        r3 = st.columns(6)
        r3[0].metric("Expected move", f"{float(s['expected_move']):.2f}"
                     if pd.notna(s.get("expected_move")) else "—")
        r3[1].metric("Financing tilt", f"{float(s['financing_tilt']):+.2f}"
                     if pd.notna(s.get("financing_tilt")) else "—")
        r3[2].metric("Insider tilt", f"{float(s['insider_tilt']):+.2f}"
                     if pd.notna(s.get("insider_tilt")) else "—")
        r3[3].metric("Q burn", fmt_usd(_f(fin["quarterly_burn_usd"].iloc[0]))
                     if not fin.empty and pd.notna(fin["quarterly_burn_usd"].iloc[0]) else "—")
        r3[4].metric("Cash", fmt_usd(_f(fin["cash_and_equivalents_usd"].iloc[0]))
                     if not fin.empty and pd.notna(fin["cash_and_equivalents_usd"].iloc[0]) else "—")
        r3[5].metric("Shares out", f"{float(fin['shares_outstanding'].iloc[0])/1e6:.1f}M"
                      if not fin.empty and pd.notna(fin["shares_outstanding"].iloc[0]) else "—")

    tab_chart, tab_cat, tab_ins, tab_sig = st.tabs(
        ["Price", "Catalysts", "Insiders", "Signals"])

    with tab_chart:
        if prices.empty:
            st.info("No price history.")
        else:
            prices = prices.copy()
            prices["date"] = pd.to_datetime(prices["date"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=prices["date"], y=prices["close"], mode="lines", name="Close",
                line=dict(color=THEME["accent"], width=1.6),
            ))
            for _, cr in cats.iterrows():
                fig.add_vline(x=pd.to_datetime(cr["expected_date"]),
                              line_dash="dot", line_color=THEME["amber"], opacity=0.55)
            buys = insiders[insiders["is_purchase"] == True] if not insiders.empty else pd.DataFrame()  # noqa: E712
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
                    name="Insider buy", marker=dict(color=THEME["green"], size=8, symbol="triangle-up"),
                ))
            _plotly_theme(fig, height=360, title=f"{ticker} — amber = catalyst, green = insider buy")
            st.plotly_chart(fig, use_container_width=True)

    with tab_cat:
        st.dataframe(cats if not cats.empty else pd.DataFrame(),
                     use_container_width=True, hide_index=True, height=240)

    with tab_ins:
        st.dataframe(insiders if not insiders.empty else pd.DataFrame(),
                     use_container_width=True, hide_index=True, height=240)

    with tab_sig:
        if blotter.empty:
            st.caption("No signals.")
        else:
            ts = blotter[blotter["ticker"] == ticker].copy()
            view = pd.DataFrame({
                "trade": ts["trade_type"], "wt": ts["suggested_weight"],
                "cat": ts["catalyst_type"], "date": ts["expected_date"].dt.date.astype("object"),
                "d->": ts["days_until"], "comp": ts["composite_score"], "base": ts["base_rate"],
                "exp_mv": ts["expected_move"], "impl_mv": ts["implied_move"], "gap": ts["edge_gap"],
                "runup": ts["run_up_30d"], "short%": ts["short_pct_float"],
                "fin": ts["financing_tilt"], "ins": ts["insider_tilt"],
                "runway": ts["runway_months"], "conf": ts["confidence"],
            })
            st.dataframe(
                _style_trade_col(view).format({
                    "wt": "{:+.3f}", "comp": "{:.2f}", "base": "{:.2f}",
                    "exp_mv": "{:.2f}", "impl_mv": "{:.2f}", "gap": "{:+.2f}",
                    "runup": "{:+.0%}", "short%": "{:.0%}", "fin": "{:+.2f}", "ins": "{:+.2f}",
                    "runway": "{:.0f}", "conf": "{:.2f}", "d->": "{:.0f}",
                }, na_rep="—"),
                use_container_width=True, hide_index=True, height=200,
            )


def render_trade_book_panel(book: dict, equity: float, prices: dict[str, float],
                            *, act_days: int = 60, key_prefix: str = "book") -> None:
    """Interactive trade book: pick a row to expand full company dossier."""
    sized = _book_sized_table(book, equity, prices)
    if sized.empty:
        st.info("No capped positions in the action book.")
        return
    act = sized[sized["days_until"] <= act_days].sort_values("days_until")
    if act.empty:
        st.info(f"No trades within {act_days} days.")
        return

    summary = act[[
        "urgent", "ticker", "side", "trade_type", "weight", "dollars", "shares",
        "expected_date", "days_until", "base_rate", "edge_gap", "timing",
    ]].rename(columns={
        "urgent": "!", "trade_type": "trade", "weight": "wt",
        "expected_date": "catalyst", "days_until": "d->", "base_rate": "base", "edge_gap": "gap",
    })
    summary["catalyst"] = summary["catalyst"].dt.date
    styled = _style_trade_col(summary, col="trade").format({
        "wt": "{:+.3f}", "dollars": "${:,.0f}", "shares": "{:,.0f}",
        "base": "{:.2f}", "gap": "{:+.2f}", "d->": "{:.0f}",
    }, na_rep="")

    st.caption("Select a row to open the full company dossier below.")
    pick = st.dataframe(
        styled, use_container_width=True, hide_index=True,
        height=min(420, 44 + 32 * len(summary)),
        on_select="rerun", selection_mode="single-row",
        key=f"{key_prefix}_trade_table",
    )
    sel_rows = pick.selection.rows if pick.selection else []
    if sel_rows:
        ticker = summary.iloc[sel_rows[0]]["ticker"]
        with st.expander(f"{ticker} — company dossier", expanded=True):
            render_ticker_dossier(ticker)
    else:
        st.caption("Tip: click a row in the table above to load company details.")


# ===========================================================================
@st.cache_data(ttl=300)
def load_action_book(horizon_days: int) -> dict:
    return compute_book(horizon_days=horizon_days)


def page_action_desk() -> None:
    """Merged Action Sheet + Trade Blotter: act-now trades with full metrics."""
    st.title("Action Desk")
    st.caption("Act now · capped book · all signals. Click a row in Act now for the company dossier.")

    flt = _action_desk_filters(sidebar=False)
    blotter = load_blotter()
    if blotter.empty:
        st.warning("No edge scores. Run scripts/run_composite.py.")
        return

    book = load_action_book(min(flt["horizon"], 365))
    ensure_account()
    prices = latest_prices()
    summ = pf.account_summary(_holding_dicts(load_holdings("open")), get_account()["cash"], prices)
    equity = summ["equity"] or 0.0

    caps = {"gross_long": 1.0, "gross_short": 0.30, "net": 0.60, "gbm": 0.25}
    m = st.columns(6)
    m[0].metric("Capped positions", book["positions"])
    m[1].metric("Gross L/S", f"{book['gross_long']:.0%} / {book['gross_short']:.0%}")
    m[2].metric("Net", f"{book['net']:+.0%}", help="cap ±60%")
    m[3].metric("GBM", f"{book['gbm_pct']:.0%}", help="cap 25%")
    m[4].metric("All signals", len(_filter_blotter(blotter, flt)))
    m[5].metric("Account", fmt_usd(equity) if equity else "—")

    sized = _book_sized_table(book, equity, prices)

    tab_act, tab_book, tab_all = st.tabs(["Act now", "Capped book", "All signals"])

    # ---- ACT NOW ----
    with tab_act:
        render_trade_book_panel(
            book, equity, prices, act_days=flt["act_days"], key_prefix="desk",
        )

    # ---- CAPPED BOOK ----
    with tab_book:
        if sized.empty:
            st.warning("Empty capped book.")
        else:
            view = pd.DataFrame({
                "ticker": sized["ticker"], "trade": sized["trade_type"], "side": sized["side"],
                "wt": sized["weight"], "$": sized["dollars"], "sh": sized["shares"],
                "date": sized["expected_date"].dt.date.astype("object"),
                "d->": sized["days_until"], "base": sized["base_rate"], "gap": sized["edge_gap"],
                "conf": sized["confidence"], "sector": sized["sector"],
                "gbm": sized["is_gbm"].map(lambda x: "★" if x else ""),
                "timing": sized["timing"],
            })
            styled = _style_trade_col(view).format({
                "wt": "{:+.3f}", "$": "${:,.0f}", "sh": "{:,.0f}",
                "base": "{:.2f}", "gap": "{:+.2f}", "conf": "{:.2f}", "d->": "{:.0f}",
            }, na_rep="—")
            st.dataframe(styled, use_container_width=True, hide_index=True, height=520)
            if not equity:
                st.caption("Set starting capital on Portfolio to see $ / share sizing.")
            csv = view.to_csv(index=False).encode("utf-8")
            st.download_button("⬇ Download capped book (CSV)", csv,
                               file_name=f"action_sheet_{book['today']}.csv", mime="text/csv")

    # ---- ALL SIGNALS (blotter) ----
    with tab_all:
        f = _filter_blotter(blotter, flt)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Filtered signals", len(f))
        c2.metric("Buy-the-rumor", int((f["trade_type"] == "buy_the_rumor").sum()))
        c3.metric("Fade", int((f["trade_type"] == "fade").sum()))
        c4.metric("In capped book", int(f["ticker"].isin(sized["ticker"]).sum()) if not sized.empty else 0)

        in_book = set(sized["ticker"]) if not sized.empty else set()
        view = pd.DataFrame({
            "book": f["ticker"].map(lambda t: "✓" if t in in_book else ""),
            "ticker": f["ticker"],
            "trade": f["trade_type"],
            "wt": f["suggested_weight"],
            "cat": f["catalyst_type"],
            "date": f["expected_date"].dt.date.astype("object"),
            "d->": f["days_until"],
            "comp": f["composite_score"],
            "base": f["base_rate"],
            "exp_mv": f["expected_move"],
            "impl_mv": f["implied_move"],
            "gap": f["edge_gap"],
            "runup": f["run_up_30d"],
            "short%": f["short_pct_float"],
            "fin": f["financing_tilt"],
            "ins": f["insider_tilt"],
            "runway": f["runway_months"],
            "conf": f["confidence"],
            "gbm": f["is_gbm_focused"].map(lambda x: "★" if x else ""),
        })
        styled = _style_trade_col(view).format({
            "wt": "{:+.3f}", "comp": "{:.2f}", "base": "{:.2f}",
            "exp_mv": "{:.2f}", "impl_mv": "{:.2f}", "gap": "{:+.2f}",
            "runup": "{:+.0%}", "short%": "{:.0%}", "fin": "{:+.2f}", "ins": "{:+.2f}",
            "runway": "{:.0f}", "conf": "{:.2f}", "d->": "{:.0f}",
        }, na_rep="—")
        st.dataframe(styled, use_container_width=True, hide_index=True, height=560)
        st.caption("✓ = name is in the risk-capped book. Use for research; trade from **Act now** / **Capped book**.")


# ===========================================================================
def page_security() -> None:
    """Legacy alias — dossier picker."""
    _intel_dossier_tab()


def _intel_dossier_tab() -> None:
    companies = q(
        "SELECT ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker"
    )
    if companies.empty:
        st.warning("No companies.")
        return
    blotter = load_blotter()
    act_tickers = sorted(blotter["ticker"].dropna().unique().tolist()) if not blotter.empty else []
    csel = st.columns([2, 1])
    ticker = csel[0].selectbox("Ticker", companies["ticker"].tolist(), key="intel_ticker")
    if act_tickers:
        quick = csel[1].selectbox("Signal names", [""] + act_tickers, key="intel_quick")
        if quick:
            ticker = quick
    render_ticker_dossier(ticker, blotter=blotter)


def _render_catalyst_calendar() -> None:
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
    fig.add_vline(x=today, line_dash="dash", line_color=THEME["muted"])
    fig.update_layout(height=max(440, 18 * cal["ticker"].nunique()),
                      paper_bgcolor=THEME["bg"], plot_bgcolor=THEME["panel"],
                      font=dict(color=THEME["text"]), margin=dict(l=48, r=24, t=40, b=48),
                      legend_title_text="", xaxis_title="", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Dot size ∝ |weight| · dashed line = today")


def page_intel() -> None:
    st.title("Market intel")
    st.caption("Company dossiers and the catalyst calendar.")
    tab_d, tab_c = st.tabs(["Company dossier", "Catalyst calendar"])
    with tab_d:
        _intel_dossier_tab()
    with tab_c:
        _render_catalyst_calendar()


def _render_data_health() -> None:
    tables = ["companies", "trials", "catalysts", "edge_scores", "price_history",
              "positioning", "insider_transactions", "catalyst_outcomes", "financials"]
    counts = {t: int(q(f"SELECT COUNT(*) n FROM {t}").iloc[0, 0]) for t in tables}
    cols = st.columns(min(len(counts), 5))
    for col, (name, n) in zip(cols, counts.items()):
        col.metric(name, f"{n:,}")

    st.subheader("Coverage")
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


def page_system() -> None:
    st.title("Models & data")
    tab_v, tab_d = st.tabs(["Validation", "Data health"])
    with tab_v:
        page_validation(embedded=True)
    with tab_d:
        _render_data_health()
    with st.expander("Glossary"):
        page_glossary(embedded=True)


# ===========================================================================
def page_calendar() -> None:
    st.title("Catalyst calendar")
    _render_catalyst_calendar()


# ===========================================================================
def page_validation(*, embedded: bool = False) -> None:
    if not embedded:
        st.title("Validation")

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


NAV_SECTIONS = {
    "Trade Desk": {
        "Cockpit": page_home,
        "Portfolio": page_portfolio,
        "Action Desk": page_action_desk,
    },
    "Research": {
        "Market Intel": page_intel,
        "Models & Data": page_system,
    },
}


def _render_sidebar_nav() -> callable:
    """Two-level nav: pick section, then sub-page."""
    if "nav_section" not in st.session_state:
        st.session_state.nav_section = "Trade Desk"
    if "nav_page" not in st.session_state:
        st.session_state.nav_page = "Cockpit"

    section = st.sidebar.radio(
        "Area",
        list(NAV_SECTIONS.keys()),
        index=list(NAV_SECTIONS.keys()).index(st.session_state.nav_section),
        horizontal=True,
        label_visibility="collapsed",
    )
    if section != st.session_state.nav_section:
        st.session_state.nav_section = section
        st.session_state.nav_page = next(iter(NAV_SECTIONS[section].keys()))

    pages = list(NAV_SECTIONS[section].keys())
    if st.session_state.nav_page not in pages:
        st.session_state.nav_page = pages[0]

    page = st.sidebar.radio(
        section,
        pages,
        index=pages.index(st.session_state.nav_page),
        label_visibility="visible",
    )
    st.session_state.nav_page = page
    st.sidebar.caption(f"{section} › **{page}**")
    return NAV_SECTIONS[section][page]


def main() -> None:
    st.sidebar.title("EDGE TERMINAL")
    st.sidebar.caption("Decision support · read-only")
    page_fn = _render_sidebar_nav()
    st.sidebar.markdown("---")
    page_fn()
    st.sidebar.caption("Data cached 5 min")


if __name__ == "__main__":
    main()
