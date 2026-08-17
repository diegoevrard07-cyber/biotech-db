"""Edge Terminal — data-first research dashboard for the biotech catalyst engine.

Design rule: every pixel shows a number, a comparison, or a label. No decorative
panels, no filler, no empty states dressed up as features. Three dense views:

  Now          What is the model telling me to do, and should I trust it?
               (capped action book + validation stats + exits due, one screen)
  Track record Is the paper book working? (equity vs XBI, holdings, closed trades)
  Evidence     Why believe the model? (event study, calibration, data coverage)

Run with:
    streamlit run scripts/terminal.py
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root (layers.*)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ (action_sheet)

import pandas as pd
import plotly.graph_objects as go
import psycopg2
import streamlit as st
from action_sheet import compute_book
from dotenv import load_dotenv

import config
from layers.portfolio import tracker as pf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "")

st.set_page_config(page_title="Edge Terminal", layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------------------------
# Style: dark, thin rules, tabular numerals, red/green only for direction.
# ---------------------------------------------------------------------------

INK = "#d6dae2"  # primary text
MUTED = "#7d8695"  # labels
FAINT = "#4a5462"  # hairlines
ACCENT = "#4c9aff"  # single emphasis color
GOOD = "#2fbf8f"  # positive direction only
BAD = "#e5534b"  # negative direction only
MONO = "'JetBrains Mono', 'SF Mono', 'Consolas', monospace"

# Validated base-rate model holdout (research result, not in DB).
# Source: docs/AGENT_HANDOFF.md §5, temporal holdout run of 2026-06-21.
BASE_RATE_HOLDOUT = {"n": 10127, "brier_skill": 0.098, "auc": 0.676, "date": "2026-06-21"}


def _inject_css() -> None:
    st.markdown(
        f"""
        <style>
          html, body, [class*="css"] {{
            font-family: {MONO}; color: {INK}; font-variant-numeric: tabular-nums;
          }}
          .stApp {{ background: #0b0e12; }}
          header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
          [data-testid="stAppDeployButton"] {{ display: none !important; }}
          .block-container {{ padding: 1.4rem 1.2rem 1.5rem; max-width: 1560px; }}
          #MainMenu, footer {{ visibility: hidden; }}
          [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
          [data-testid="collapsedControl"] {{ display: none !important; }}
          [data-testid="stElementContainer"][data-stale="true"] {{ display: none !important; }}

          h1 {{ font-size: 1.0rem; font-weight: 600; letter-spacing: .12em;
                text-transform: uppercase; margin: 0; }}
          h2, h3 {{ font-size: .72rem; font-weight: 600; color: {MUTED};
                    text-transform: uppercase; letter-spacing: .12em;
                    margin: .9rem 0 .25rem; }}
          hr {{ margin: .55rem 0; border-color: {FAINT}; opacity: .5; }}

          /* flat metric strip: label over number, hairline separators, no cards */
          .strip {{ display: flex; flex-wrap: wrap; gap: 0; border-top: 1px solid {FAINT};
                    border-bottom: 1px solid {FAINT}; padding: 6px 0; margin: 6px 0; }}
          .strip .cell {{ padding: 0 14px; border-right: 1px solid {FAINT}; }}
          .strip .cell:last-child {{ border-right: none; }}
          .strip .k {{ color: {MUTED}; font-size: .6rem; text-transform: uppercase;
                       letter-spacing: .1em; }}
          .strip .v {{ font-size: .95rem; font-weight: 600; }}
          .up {{ color: {GOOD}; }} .down {{ color: {BAD}; }} .flat {{ color: {MUTED}; }}

          /* definition rows for trust / coverage blocks */
          .kv {{ display: flex; justify-content: space-between; padding: 2px 0;
                 border-bottom: 1px solid #171d26; font-size: .78rem; }}
          .kv .k {{ color: {MUTED}; }} .kv .v {{ font-weight: 600; }}

          [data-testid="stDataFrame"] {{ border: 1px solid #171d26; }}
          [data-testid="stMetric"] {{ background: none; border: none; padding: 0; }}
          [data-testid="stMetricValue"] {{ font-family: {MONO}; font-size: .95rem; }}
          [data-testid="stMetricLabel"] {{ color: {MUTED}; font-size: .62rem;
                                           text-transform: uppercase; letter-spacing: .1em; }}
          .stSelectbox label, .stSlider label, .stMultiSelect label {{
            font-size: .62rem; text-transform: uppercase; letter-spacing: .1em;
            color: {MUTED}; }}
          div[role="tablist"] {{ gap: 2px; }}
          div[role="tablist"] button {{ font-size: .72rem; text-transform: uppercase;
            letter-spacing: .1em; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _plotly_theme(fig: go.Figure, *, height: int = 260) -> go.Figure:
    """Minimal chart theme: hairline grids, no frame, tabular numerals."""
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=MONO, color=INK, size=10),
        margin=dict(l=48, r=12, t=8, b=32),
        showlegend=True,
        legend=dict(orientation="h", y=1.08, x=0, font=dict(size=9, color=MUTED)),
        hoverlabel=dict(bgcolor="#171d26", bordercolor=FAINT, font=dict(color=INK, size=10)),
    )
    fig.update_xaxes(gridcolor="#171d26", zerolinecolor="#171d26", linecolor=FAINT)
    fig.update_yaxes(gridcolor="#171d26", zerolinecolor=FAINT, linecolor=FAINT)
    return fig


# ---------------------------------------------------------------------------
# Formatting — consistent everywhere: 0.0% / +x.x% / $x.xk / em dash for missing
# ---------------------------------------------------------------------------


def f_pct(v, digits: int = 1, sign: bool = False) -> str:
    if v is None or pd.isna(v):
        return "—"
    s = f"{float(v) * 100:.{digits}f}%"
    return ("+" + s) if sign and float(v) > 0 else s


def f_usd(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"${v / div:.2f}{suf}"
    return f"${v:.0f}"


def f_int(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{int(v):,}"


def f_px(v) -> str:
    return "—" if v is None or pd.isna(v) else f"${float(v):,.2f}"


def _f(v):
    """Decimal/None -> float|None (psycopg2 numerics come back as Decimal)."""
    return None if v is None else float(v)


def _dir_cls(v) -> str:
    """CSS class by direction — the only place red/green is assigned."""
    if v is None or pd.isna(v):
        return "flat"
    return "up" if float(v) > 0 else ("down" if float(v) < 0 else "flat")


# ---------------------------------------------------------------------------
# Data access (read path; cached)
# ---------------------------------------------------------------------------


def get_conn():
    """Return a fresh psycopg2 connection from DATABASE_URL (stops the app if unset)."""
    if not DATABASE_URL:
        st.error("DATABASE_URL not set in .env")
        st.stop()
    return psycopg2.connect(DATABASE_URL)


@st.cache_data(ttl=300)
def q(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a read query and return a DataFrame. Cached for 5 minutes."""
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


def exec_write(sql: str, params: tuple | None = None) -> None:
    """Execute a single write statement against the DB and commit."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


@st.cache_data(ttl=30)
def latest_prices() -> dict[str, float]:
    """ticker -> latest available daily close (end-of-day, not live)."""
    df = q("""
        SELECT DISTINCT ON (ticker) ticker, close
        FROM price_history WHERE close IS NOT NULL
        ORDER BY ticker, date DESC
    """)
    return {r.ticker: float(r.close) for r in df.itertuples()} if not df.empty else {}


def ensure_account() -> None:
    """Insert the singleton portfolio_account row (id=1) if missing."""
    exec_write(
        "INSERT INTO portfolio_account (id, cash_usd) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"
    )


@st.cache_data(ttl=30)
def get_account() -> dict:
    """Return {cash, starting_capital} for the singleton paper account."""
    df = q("SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1")
    if df.empty:
        return {"cash": 0.0, "starting_capital": None}
    return {
        "cash": _f(df.iloc[0]["cash_usd"]) or 0.0,
        "starting_capital": _f(df.iloc[0]["starting_capital_usd"]),
    }


@st.cache_data(ttl=30)
def load_holdings(status: str | None = "open") -> pd.DataFrame:
    """Load portfolio holdings (default: open only), newest entry first."""
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


def add_holding(
    *,
    ticker,
    company_id,
    catalyst_id,
    side,
    trade_type,
    entry_date,
    shares,
    entry_price,
    planned_exit_rule,
    planned_exit_date,
    notes,
) -> None:
    """Insert an open holding and apply its cash impact to the account."""
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
                (
                    ticker,
                    company_id,
                    catalyst_id,
                    side,
                    trade_type,
                    entry_date,
                    shares,
                    entry_price,
                    cost,
                    planned_exit_rule,
                    planned_exit_date,
                    notes,
                ),
            )
            cur.execute(
                "UPDATE portfolio_account SET cash_usd=cash_usd+%s, updated_at=NOW() WHERE id=1",
                (cash_delta,),
            )
        conn.commit()
    finally:
        conn.close()


def close_holding(
    hid: int, side: str, shares: float, entry_price: float, exit_price: float, exit_date
) -> None:
    """Close a holding at exit_price, booking realized P&L and the cash delta."""
    realized = pf.realized_pnl(side, shares, entry_price, exit_price)
    cash_delta = pf.cash_delta_on_close(side, shares, exit_price)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE portfolio_holdings SET status='closed', exit_price=%s,
                   exit_date=%s, realized_pnl_usd=%s, updated_at=NOW() WHERE id=%s""",
                (exit_price, exit_date, realized, hid),
            )
            cur.execute(
                "UPDATE portfolio_account SET cash_usd=cash_usd+%s, updated_at=NOW() WHERE id=1",
                (cash_delta,),
            )
        conn.commit()
    finally:
        conn.close()


@st.cache_data(ttl=300)
def load_blotter() -> pd.DataFrame:
    """Signal blotter: edge_scores joined to companies, catalysts, positioning, financials."""
    df = q("""
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
        """)
    if df.empty:
        return df
    df["expected_date"] = pd.to_datetime(df["expected_date"], errors="coerce")
    df["days_until"] = (df["expected_date"] - pd.Timestamp(date.today())).dt.days
    for c in [
        "composite_score",
        "suggested_weight",
        "edge_gap",
        "expected_move",
        "implied_move",
        "run_up_30d",
        "short_pct_float",
        "runway_months",
        "financing_tilt",
        "insider_tilt",
        "base_rate",
        "confidence",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_action_book(horizon_days: int = 365) -> dict:
    """The risk-capped book — same computation the autopilot syncs to."""
    return compute_book(horizon_days=horizon_days)


@st.cache_data(ttl=300)
def load_performance() -> pd.DataFrame:
    """Daily equity snapshots with the stored XBI benchmark series."""
    df = q("""
        SELECT snapshot_date, equity, cash, benchmark_equity, xbi_return_pct,
               total_return_pct, open_positions, realized_to_date
        FROM portfolio_performance ORDER BY snapshot_date
    """)
    if df.empty:
        return df
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    for c in [
        "equity",
        "cash",
        "benchmark_equity",
        "xbi_return_pct",
        "total_return_pct",
        "realized_to_date",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_event_returns() -> pd.DataFrame:
    """Realized abnormal returns around historical 8-K events (the evidence base)."""
    df = q("""
        SELECT ticker, filing_date, event_type, hold_days, abnormal_return, run_up_30d
        FROM event_returns WHERE abnormal_return IS NOT NULL
    """)
    for c in ["abnormal_return", "run_up_30d"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_calibration() -> pd.DataFrame:
    """Calibration run history (Brier vs resolved outcomes), newest last."""
    df = q("""
        SELECT run_at, n_pairs, brier_score, model_hit_rate, base_rate_hit_rate
        FROM calibration_runs ORDER BY run_at
    """)
    for c in ["brier_score", "model_hit_rate", "base_rate_hit_rate"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_coverage() -> dict:
    """Row counts and freshness per pipeline table — what data actually exists."""
    counts = {}
    for tbl in [
        "companies",
        "trials",
        "catalysts",
        "edge_scores",
        "price_history",
        "positioning",
        "insider_transactions",
        "sec_filings",
        "event_returns",
        "historical_trials",
        "catalyst_outcomes",
        "base_rates",
    ]:
        df = q(f"SELECT COUNT(*) AS n FROM {tbl}")  # noqa: S608 — fixed table names
        counts[tbl] = int(df.iloc[0]["n"]) if not df.empty else 0
    fresh = {}
    for tbl, col in [
        ("price_history", "date"),
        ("positioning", "date"),
        ("edge_scores", "computed_at"),
        ("insider_transactions", "filing_date"),
        ("sec_filings", "filing_date"),
    ]:
        df = q(f"SELECT MAX({col}) AS ts FROM {tbl}")  # noqa: S608
        fresh[tbl] = df.iloc[0]["ts"] if not df.empty else None
    return {"counts": counts, "freshness": fresh}


def _holding_dicts(df: pd.DataFrame) -> list[dict]:
    """Convert a holdings DataFrame to the plain dicts the pure tracker math expects."""
    return [
        {
            "ticker": r.ticker,
            "side": r.side,
            "shares": float(r.shares),
            "entry_price": float(r.entry_price),
            "trade_type": r.trade_type,
            "planned_exit_date": r.planned_exit_date,
            "planned_exit_rule": r.planned_exit_rule,
        }
        for r in df.itertuples()
    ]


def _purge_open_shorts_once() -> None:
    """Long-only safety: cover leftover open shorts/fades once per session."""
    if not config.LONG_ONLY or st.session_state.get("_shorts_purged"):
        return
    check = q(
        "SELECT COUNT(*) AS n FROM portfolio_holdings "
        "WHERE status='open' AND (side='short' OR trade_type='fade')"
    )
    n = int(check.iloc[0]["n"]) if not check.empty else 0
    st.session_state["_shorts_purged"] = True
    if n <= 0:
        return
    st.warning(f"Long-only: covering {n} leftover short/fade position(s) now…")
    try:
        from paper_autopilot import cover_shorts
        from strip_shorts import run as strip_shorts_run

        cover_shorts(dry_run=False)
        strip_shorts_run(dry_run=False)
        st.cache_data.clear()
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to cover shorts: {exc}")


# ---------------------------------------------------------------------------
# Small renderers
# ---------------------------------------------------------------------------


def strip(cells: list[tuple[str, str]]) -> None:
    """Render a flat label-over-value metric strip (no cards, hairline separators)."""
    html_cells = "".join(
        f"<div class='cell'><div class='k'>{k}</div><div class='v'>{v}</div></div>"
        for k, v in cells
    )
    st.markdown(f"<div class='strip'>{html_cells}</div>", unsafe_allow_html=True)


def kv(rows: list[tuple[str, str]]) -> None:
    """Render label/value rows for trust and coverage blocks."""
    body = "".join(
        f"<div class='kv'><span class='k'>{k}</span><span class='v'>{v}</span></div>"
        for k, v in rows
    )
    st.markdown(body, unsafe_allow_html=True)


def _color_signed(v) -> str:
    """Styler callback: red/green strictly by sign."""
    return (
        f"color: {GOOD}"
        if isinstance(v, (int, float)) and v > 0
        else (f"color: {BAD}" if isinstance(v, (int, float)) and v < 0 else "")
    )


# ---------------------------------------------------------------------------
# Page: Now — the book, the trust metrics, the exits. One screen.
# ---------------------------------------------------------------------------


def page_now() -> None:
    """Landing view: what to do right now, and whether the model is trustworthy."""
    _purge_open_shorts_once()
    ensure_account()
    acct = get_account()
    prices = latest_prices()
    open_df = load_holdings("open")
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)
    perf = load_performance()
    blotter = load_blotter()
    book = load_action_book(365)

    # -- header strip: account + freshness, all comparisons inline ----------
    xbi_ret = _f(perf.iloc[-1]["xbi_return_pct"]) if not perf.empty else None
    tot_ret = _f(perf.iloc[-1]["total_return_pct"]) if not perf.empty else None
    px_date = q("SELECT MAX(date) AS d FROM price_history").iloc[0]["d"]
    sc_date = q("SELECT MAX(computed_at) AS d FROM edge_scores").iloc[0]["d"]
    st.markdown("# EDGE TERMINAL")
    strip(
        [
            ("equity", f_usd(summ["equity"])),
            ("cash", f_usd(summ["cash"])),
            ("open", str(summ["positions"])),
            ("gross long", f_pct(summ["gross_long_pct"], 0)),
            ("book vs XBI", f"{f_pct(tot_ret, 1, True)} / {f_pct(xbi_ret, 1, True)}"),
            ("prices", str(px_date or "—")),
            ("scores", str(sc_date)[:10] if sc_date is not None else "—"),
        ]
    )

    left, right = st.columns([7, 3], gap="medium")

    with left:
        st.markdown("## Action book — risk-capped, long-only")
        rows = book["rows"]
        if not rows:
            st.caption("Book is empty: no signal passed the decision rules today.")
        else:
            extra = blotter.set_index("catalyst_id") if not blotter.empty else pd.DataFrame()
            table = []
            for r in rows:
                b = extra.loc[r["catalyst_id"]] if r["catalyst_id"] in extra.index else None
                table.append(
                    {
                        "ticker": r["ticker"],
                        "catalyst": r["catalyst_type"],
                        "date": r["expected_date"],
                        "d→": (r["expected_date"] - book["today"]).days,
                        "type": r["trade_type"],
                        "base": _f(r["base_rate"]),
                        "expΔ": _f(b["expected_move"]) if b is not None else None,
                        "implΔ": _f(b["implied_move"]) if b is not None else None,
                        "gap": _f(r["edge_gap"]),
                        "wt": r["weight"],
                        "$": round(r["weight"] * summ["equity"], 0) if summ["equity"] else None,
                    }
                )
            df = pd.DataFrame(table)
            styled = df.style.map(_color_signed, subset=["gap"]).format(
                {
                    "base": "{:.0%}",
                    "expΔ": "{:.0%}",
                    "implΔ": "{:.0%}",
                    "gap": "{:+.1%}",
                    "wt": "{:.1%}",
                    "$": "${:,.0f}",
                },
                na_rep="—",
            )
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
                height=min(560, 38 + 35 * len(df)),
            )
            st.caption(
                f"{book['positions']} positions · gross {f_pct(book['gross_long'], 0)} of "
                f"{f_pct(config.MAX_GROSS_LONG, 0)} cap · GBM {f_pct(book['gbm_pct'], 0)} of "
                f"{f_pct(config.MAX_GBM_WEIGHT, 0)} cap · horizon 365d"
            )

        # -- catalyst calendar: next 90 days, dense -------------------------
        st.markdown("## Catalyst calendar — next 90 days")
        cal = (
            blotter[(blotter["days_until"] >= 0) & (blotter["days_until"] <= 90)].sort_values(
                "days_until"
            )
            if not blotter.empty
            else pd.DataFrame()
        )
        if cal.empty:
            st.caption("No dated catalysts in the next 90 days.")
        else:
            cal_view = pd.DataFrame(
                {
                    "date": cal["expected_date"].dt.date,
                    "ticker": cal["ticker"],
                    "catalyst": cal["catalyst_type"],
                    "type": cal["trade_type"],
                    "base": cal["base_rate"],
                    "gap": cal["edge_gap"],
                }
            )
            st.dataframe(
                cal_view.style.map(_color_signed, subset=["gap"]).format(
                    {"base": "{:.0%}", "gap": "{:+.1%}"}, na_rep="—"
                ),
                use_container_width=True,
                hide_index=True,
                height=min(320, 38 + 35 * len(cal_view)),
            )

    with right:
        st.markdown("## Should you trust it")
        cal_hist = load_calibration()
        latest_cal = cal_hist.iloc[-1] if not cal_hist.empty else None
        outcomes = q("SELECT outcome_label, COUNT(*) AS n FROM catalyst_outcomes GROUP BY 1")
        oc = (
            {r.outcome_label: int(r.n) for r in outcomes.itertuples()} if not outcomes.empty else {}
        )
        trust_rows = [
            (
                "base-rate model holdout",
                f"Brier +{BASE_RATE_HOLDOUT['brier_skill']:.3f} · AUC {BASE_RATE_HOLDOUT['auc']:.3f}",
            ),
            (
                "holdout sample",
                f"n = {BASE_RATE_HOLDOUT['n']:,} (as of {BASE_RATE_HOLDOUT['date']})",
            ),
            (
                "resolved outcomes",
                f"{sum(oc.values())} ({oc.get('hit', 0)} hit / "
                f"{oc.get('miss', 0)} miss / {oc.get('ambiguous', 0)} ambig)",
            ),
        ]
        if latest_cal is not None:
            trust_rows += [
                ("calibration n", f_int(latest_cal["n_pairs"])),
                (
                    "calibration Brier",
                    (
                        f"{float(latest_cal['brier_score']):.3f}"
                        if pd.notna(latest_cal["brier_score"])
                        else "—"
                    ),
                ),
            ]
        trust_rows.append(("signals scored", f_int(len(blotter))))
        kv(trust_rows)
        st.caption(
            "Calibration vs resolved outcomes is nearly empty — ongoing "
            "validation, not proof of alpha."
        )

        st.markdown("## Exits due")
        alerts = pf.exit_alerts(_holding_dicts(open_df), date.today(), soon_days=7)
        if not alerts:
            st.caption("None overdue or due within 7 days.")
        else:
            kv(
                [
                    (
                        f"{a['ticker']} · {a['action']}",
                        f"{a['days']}d" if a["days"] > 0 else "overdue",
                    )
                    for a in alerts
                ]
            )

        st.markdown("## Universe")
        cov = load_coverage()["counts"]
        kv(
            [
                ("companies", f_int(cov.get("companies"))),
                (
                    "upcoming catalysts",
                    f_int(
                        q(
                            "SELECT COUNT(*) AS n FROM catalysts "
                            "WHERE expected_date >= CURRENT_DATE"
                        ).iloc[0]["n"]
                    ),
                ),
                ("historical trials", f_int(cov.get("historical_trials"))),
                ("8-K events studied", f_int(cov.get("event_returns"))),
            ]
        )

        # -- ticker drill-down ----------------------------------------------
        st.markdown("## Ticker")
        tickers = sorted(blotter["ticker"].unique()) if not blotter.empty else []
        if tickers:
            pick = st.selectbox("ticker", tickers, index=0, label_visibility="collapsed")
            _dossier_compact(pick, blotter)


def _dossier_compact(ticker: str, blotter: pd.DataFrame) -> None:
    """Dense per-ticker drill-down: latest signal, price path, catalysts, insiders."""
    sig = blotter[blotter["ticker"] == ticker].sort_values("days_until")
    if not sig.empty:
        s = sig.iloc[0]
        kv(
            [
                ("type / weight", f"{s['trade_type']} · {f_pct(_f(s['suggested_weight']), 1)}"),
                (
                    "base rate vs gap",
                    f"{f_pct(_f(s['base_rate']), 0)} · {f_pct(_f(s['edge_gap']), 1, True)}",
                ),
                ("run-up 30d", f_pct(_f(s["run_up_30d"]), 1, True)),
                ("short % float", f_pct(_f(s["short_pct_float"]), 1)),
                (
                    "runway",
                    f"{_f(s['runway_months']):.0f} mo" if pd.notna(s["runway_months"]) else "—",
                ),
            ]
        )
    px = q(
        "SELECT date, close FROM price_history WHERE ticker=%s AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 120",
        (ticker,),
    )
    if not px.empty:
        px = px.iloc[::-1]
        fig = go.Figure(
            go.Scatter(
                x=pd.to_datetime(px["date"]),
                y=px["close"].astype(float),
                mode="lines",
                line=dict(color=ACCENT, width=1.2),
                showlegend=False,
            )
        )
        _plotly_theme(fig, height=140)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    ins = q(
        "SELECT transaction_date, insider_name, value_usd, is_purchase "
        "FROM insider_transactions i JOIN companies co ON co.id = i.company_id "
        "WHERE co.ticker=%s ORDER BY transaction_date DESC LIMIT 5",
        (ticker,),
    )
    if not ins.empty:
        kv(
            [
                (
                    f"{'buy' if r.is_purchase else 'sell'} · {str(r.transaction_date)}",
                    f_usd(_f(r.value_usd)),
                )
                for r in ins.itertuples()
            ]
        )


# ---------------------------------------------------------------------------
# Page: Track record — is the paper book working?
# ---------------------------------------------------------------------------


def _risk_stats(perf: pd.DataFrame) -> dict:
    """Daily-snapshot risk stats: total/XBI return, alpha, max DD, vol, Sharpe."""
    if perf.empty or len(perf) < 2:
        return {}
    eq = perf["equity"].astype(float)
    rets = eq.pct_change().dropna()
    out = {
        "days": len(perf),
        "total": _f(perf.iloc[-1]["total_return_pct"]),
        "xbi": _f(perf.iloc[-1]["xbi_return_pct"]),
        "max_dd": float((eq / eq.cummax() - 1).min()),
    }
    if out["total"] is not None and out["xbi"] is not None:
        out["alpha"] = out["total"] - out["xbi"]
    if len(rets) > 1 and rets.std() > 0:
        out["vol"] = float(rets.std() * (252**0.5))
        out["sharpe"] = float(rets.mean() / rets.std() * (252**0.5))
    return out


def page_track_record() -> None:
    """Paper-book performance vs benchmark, open holdings, closed trades."""
    perf = load_performance()
    acct = get_account()
    prices = latest_prices()
    open_df = load_holdings("open")
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)
    stats = _risk_stats(perf)

    st.markdown("# TRACK RECORD")
    if stats:
        strip(
            [
                ("total return", f_pct(stats.get("total"), 1, True)),
                ("XBI same window", f_pct(stats.get("xbi"), 1, True)),
                ("alpha", f_pct(stats.get("alpha"), 1, True)),
                ("max drawdown", f_pct(stats.get("max_dd"), 1)),
                ("ann. vol", f_pct(stats.get("vol"), 1)),
                (
                    "Sharpe (rf=0)",
                    f"{stats['sharpe']:.2f}" if stats.get("sharpe") is not None else "—",
                ),
                ("days", str(stats.get("days", 0))),
            ]
        )
    else:
        st.caption("Track record starts when the first daily snapshot lands.")

    if not perf.empty:
        fig = go.Figure()
        fig.add_scatter(
            x=perf["snapshot_date"],
            y=perf["equity"],
            mode="lines",
            name="paper book",
            line=dict(color=ACCENT, width=1.4),
        )
        if perf["benchmark_equity"].notna().any():
            fig.add_scatter(
                x=perf["snapshot_date"],
                y=perf["benchmark_equity"],
                mode="lines",
                name="XBI",
                line=dict(color=MUTED, width=1.1, dash="dot"),
            )
        _plotly_theme(fig, height=280)
        fig.update_yaxes(tickformat="$,.0f")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # -- open holdings ------------------------------------------------------
    st.markdown("## Open positions")
    if open_df.empty:
        st.caption("None.")
    else:
        rows = []
        for r in open_df.itertuples():
            cur = prices.get(r.ticker)
            pnl = pf.unrealized_pnl(r.side, float(r.shares), float(r.entry_price), cur)
            pnl_pct = pf.unrealized_pnl_pct(r.side, float(r.entry_price), cur)
            mv = pf.market_value(r.side, float(r.shares), cur)
            rows.append(
                {
                    "ticker": r.ticker,
                    "type": r.trade_type,
                    "entry": r.entry_date,
                    "shares": float(r.shares),
                    "entry $": float(r.entry_price),
                    "last $": cur,
                    "wt": (abs(mv) / summ["equity"] if mv and summ["equity"] else None),
                    "P&L $": pnl,
                    "P&L %": pnl_pct,
                    "exit by": r.planned_exit_date,
                }
            )
        hv = pd.DataFrame(rows).sort_values("P&L $", ascending=False)
        st.dataframe(
            hv.style.map(_color_signed, subset=["P&L $", "P&L %"]).format(
                {
                    "shares": "{:,.1f}",
                    "entry $": "${:,.2f}",
                    "last $": "${:,.2f}",
                    "wt": "{:.1%}",
                    "P&L $": "${:+,.0f}",
                    "P&L %": "{:+.1%}",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
            height=min(480, 38 + 35 * len(hv)),
        )
        upnl = summ["unrealized_pnl_usd"]
        st.caption(
            f"unrealized {f_usd(upnl)} · cash {f_usd(summ['cash'])} · "
            f"invested {f_usd(summ['invested_usd'])}"
        )

    # -- closed trades ------------------------------------------------------
    st.markdown("## Closed trades")
    closed = load_holdings("closed")
    if closed.empty:
        st.caption("None yet.")
    else:
        cv = pd.DataFrame(
            {
                "ticker": closed["ticker"],
                "type": closed["trade_type"],
                "entry": closed["entry_date"],
                "exit": closed["exit_date"],
                "entry $": pd.to_numeric(closed["entry_price"]),
                "exit $": pd.to_numeric(closed["exit_price"]),
                "realized $": pd.to_numeric(closed["realized_pnl_usd"]),
            }
        ).sort_values("exit", ascending=False)
        st.dataframe(
            cv.style.map(_color_signed, subset=["realized $"]).format(
                {"entry $": "${:,.2f}", "exit $": "${:,.2f}", "realized $": "${:+,.0f}"}, na_rep="—"
            ),
            use_container_width=True,
            hide_index=True,
            height=min(360, 38 + 35 * len(cv)),
        )
        realized = float(cv["realized $"].sum())
        wins = int((cv["realized $"] > 0).sum())
        st.caption(
            f"realized {f_usd(realized)} across {len(cv)} trades · " f"win rate {wins}/{len(cv)}"
        )

    # -- manual entry (functional, collapsed) -------------------------------
    with st.expander("Log / close a trade"):
        _trade_forms(open_df, prices)


def _trade_forms(open_df: pd.DataFrame, prices: dict[str, float]) -> None:
    """Manual paper-trade entry and close forms (the dashboard's only write path)."""
    companies = q("SELECT id, ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")
    if companies.empty:
        st.caption("No companies in DB.")
        return
    cc = st.columns([1, 1, 1, 1])
    tk = cc[0].selectbox("Ticker", companies["ticker"].tolist(), key="add_tk")
    cid = int(companies[companies["ticker"] == tk].iloc[0]["id"])
    if config.LONG_ONLY:
        side = "long"
        ttype = cc[1].selectbox("Type", ["buy_the_rumor", "hold_through", "manual"], key="add_tt")
    else:
        side = cc[1].selectbox("Side", ["long", "short"], key="add_side")
        ttype = cc[2].selectbox(
            "Type", ["buy_the_rumor", "hold_through", "fade", "manual"], key="add_tt"
        )
    size_by = cc[3].radio("Size by", ["shares", "dollars"], horizontal=True, key="add_sizeby")
    cats = q(
        "SELECT id, catalyst_type, expected_date FROM catalysts WHERE company_id=%s "
        "AND expected_date >= CURRENT_DATE ORDER BY expected_date",
        (cid,),
    )
    cat_map = {"(none — manual exit)": (None, None)}
    for r in cats.itertuples():
        cat_map[f"{r.catalyst_type} @ {r.expected_date} (#{r.id})"] = (int(r.id), r.expected_date)
    c2 = st.columns([2, 1, 1, 1])
    cat_label = c2[0].selectbox("Link catalyst", list(cat_map.keys()), key="add_cat")
    amount = c2[1].number_input(
        "Shares" if size_by == "shares" else "Dollars",
        min_value=0.0,
        value=0.0,
        step=1.0,
        key="add_amt",
    )
    price = c2[2].number_input(
        "Entry price", min_value=0.0, value=float(prices.get(tk, 0.0)), step=0.01, key="add_price"
    )
    entry_dt = c2[3].date_input("Entry date", value=date.today(), key="add_date")
    catalyst_id, cat_date = cat_map[cat_label]
    exit_date, exit_rule = pf.planned_exit(ttype, cat_date)
    if st.button("Add trade", type="primary"):
        if amount <= 0 or price <= 0:
            st.error("Enter a positive size and entry price.")
        else:
            shares = amount if size_by == "shares" else round(amount / price, 4)
            add_holding(
                ticker=tk,
                company_id=cid,
                catalyst_id=catalyst_id,
                side=side,
                trade_type=ttype,
                entry_date=entry_dt,
                shares=shares,
                entry_price=price,
                planned_exit_rule=exit_rule,
                planned_exit_date=exit_date,
                notes="manual",
            )
            st.cache_data.clear()
            st.rerun()

    if not open_df.empty:
        lbl_map = {
            f"{r.ticker} {r.side} {float(r.shares):.0f}@{float(r.entry_price):.2f} "
            f"(#{r.id})": int(r.id)
            for r in open_df.itertuples()
        }
        c3 = st.columns([2, 1, 1])
        pick = c3[0].selectbox("Open position", list(lbl_map.keys()), key="close_pick")
        xprice = c3[1].number_input(
            "Exit price",
            min_value=0.0,
            value=float(prices.get(pick.split(" ")[0], 0.0)),
            step=0.01,
            key="close_px",
        )
        xdate = c3[2].date_input("Exit date", value=date.today(), key="close_date")
        if st.button("Close position"):
            row = open_df[open_df["id"] == lbl_map[pick]].iloc[0]
            close_holding(
                int(row["id"]),
                row["side"],
                float(row["shares"]),
                float(row["entry_price"]),
                xprice,
                xdate,
            )
            st.cache_data.clear()
            st.rerun()


# ---------------------------------------------------------------------------
# Page: Evidence — why believe the model?
# ---------------------------------------------------------------------------


def page_evidence() -> None:
    """Validation evidence: event study, calibration history, data coverage."""
    st.markdown("# EVIDENCE")

    ev = load_event_returns()
    st.markdown("## Event study — realized abnormal returns around 8-K catalysts")
    if ev.empty:
        st.caption("No event returns built yet (scripts/build_event_returns.py).")
    else:
        rows = []
        for hold, grp in ev.groupby("hold_days"):
            abn = grp["abnormal_return"].dropna()
            rows.append(
                {
                    "hold": f"{int(hold)}d",
                    "n": len(abn),
                    "mean": abn.mean(),
                    "median": abn.median(),
                    "std": abn.std(),
                    "|move| ≥ 10%": (abn.abs() >= 0.10).mean(),
                    "corr(run-up, fwd)": (
                        grp[["run_up_30d", "abnormal_return"]].dropna().corr().iloc[0, 1]
                        if len(grp.dropna(subset=["run_up_30d", "abnormal_return"])) > 2
                        else None
                    ),
                }
            )
        st.dataframe(
            pd.DataFrame(rows).style.format(
                {
                    "mean": "{:+.1%}",
                    "median": "{:+.1%}",
                    "std": "{:.1%}",
                    "|move| ≥ 10%": "{:.0%}",
                    "corr(run-up, fwd)": "{:+.3f}",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Direction is a coin flip (median ≈ 0, run-up correlation ≈ 0); "
            "magnitude is the exploitable quantity — it drives the sizing haircut."
        )

        h3 = ev[ev["hold_days"] == 3]["abnormal_return"].dropna()
        if len(h3) > 10:
            fig = go.Figure(
                go.Histogram(x=h3, nbinsx=60, marker_color=ACCENT, opacity=0.85, showlegend=False)
            )
            _plotly_theme(fig, height=180)
            fig.update_xaxes(tickformat=".0%", title="3-day abnormal return")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # -- calibration history -------------------------------------------------
    st.markdown("## Calibration vs resolved outcomes")
    cal = load_calibration()
    if cal.empty:
        st.caption("No calibration runs yet.")
    else:
        cv = cal.copy()
        cv["run_at"] = pd.to_datetime(cv["run_at"]).dt.date
        st.dataframe(
            cv.tail(12)
            .iloc[::-1]
            .style.format(
                {
                    "n_pairs": "{:.0f}",
                    "brier_score": "{:.3f}",
                    "model_hit_rate": "{:.0%}",
                    "base_rate_hit_rate": "{:.0%}",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "n_pairs is tiny because few forward catalysts have resolved — "
            "this table is the honest state of validation, updated daily."
        )

    # -- base-rate model ------------------------------------------------------
    st.markdown("## Base-rate model (the validated input)")
    kv(
        [
            (
                "temporal holdout",
                f"Brier skill +{BASE_RATE_HOLDOUT['brier_skill']:.3f} · "
                f"AUC {BASE_RATE_HOLDOUT['auc']:.3f}",
            ),
            ("holdout sample", f"n = {BASE_RATE_HOLDOUT['n']:,} labeled trials"),
            ("as of", BASE_RATE_HOLDOUT["date"]),
            ("historical trials mined", f_int(load_coverage()["counts"].get("historical_trials"))),
            ("base-rate slices", f_int(load_coverage()["counts"].get("base_rates"))),
        ]
    )

    # -- coverage -------------------------------------------------------------
    st.markdown("## Data coverage & freshness")
    cov = load_coverage()
    rows = []
    for tbl, n in cov["counts"].items():
        ts = cov["freshness"].get(tbl)
        rows.append({"table": tbl, "rows": n, "latest": str(ts)[:10] if ts is not None else "—"})
    st.dataframe(
        pd.DataFrame(rows).style.format({"rows": "{:,}"}), use_container_width=True, hide_index=True
    )


# ---------------------------------------------------------------------------
# Nav + main
# ---------------------------------------------------------------------------

PAGES = {"Now": page_now, "Track record": page_track_record, "Evidence": page_evidence}


def main() -> None:
    """Render the selected page (top tab nav; no sidebar)."""
    _inject_css()
    page = st.radio("page", list(PAGES.keys()), horizontal=True, label_visibility="collapsed")
    PAGES[page]()


main()
