"""Edge Terminal — a living sell-side research note for the biotech catalyst engine.

Design rule: a stranger with zero context must be able to read the landing view
like a research note and, within 30 seconds, answer four questions — what does
this system do, what is it recommending right now, does it work (and how would
you know), and how much real data sits behind it. Every element on the page
serves one of those answers.

  Research note          the note itself: masthead, pipeline strip, current
                         signals, evidence, coverage (default view)
  Positions & activity   owner operations: open holdings, closed trades,
                         manual trade entry

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

st.set_page_config(page_title="Edge Engine — Research Note", layout="wide")

# ---------------------------------------------------------------------------
# Design tokens: institutional research note. Warm paper, hairlines, one accent.
# ---------------------------------------------------------------------------

PAPER = "#FAF8F3"
PANEL = "#FFFFFF"
HAIRLINE = "#DDD8CE"
INK = "#17191E"
MUTED = "#6B6560"
FAINT = "#9A938A"
BURGUNDY = "#8A1F2D"  # the single accent: masthead rules, kickers, key figures
GOOD = "#0E7A4E"  # signed/directional values only
BAD = "#B3261E"
SERIF = "'Source Serif 4', Georgia, serif"
SANS = "'Inter', 'IBM Plex Sans', system-ui, sans-serif"
MONO = "'IBM Plex Mono', 'SF Mono', 'Consolas', monospace"

# Validated base-rate model holdout (research result computed offline, not in DB).
# Source: docs/AGENT_HANDOFF.md §5 — temporal holdout run of 2026-06-21.
BASE_RATE_HOLDOUT = {"n": 10127, "brier_skill": 0.098, "auc": 0.676, "date": "21 Jun 2026"}

TRADE_LABELS = {
    "buy_the_rumor": "Buy the rumor",
    "hold_through": "Hold through",
    "avoid": "Avoid",
    "manual": "Manual",
}


def _inject_css() -> None:
    st.markdown(
        f"""
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

          html, body, [class*="css"] {{
            font-family: {SANS}; color: {INK}; font-variant-numeric: tabular-nums;
          }}
          .stApp {{ background: {PAPER}; }}
          header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
          [data-testid="stAppDeployButton"] {{ display: none !important; }}
          .block-container {{ padding: 1.2rem 2rem 2rem; max-width: 1180px; }}
          #MainMenu, footer {{ visibility: hidden; }}
          [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
          [data-testid="collapsedControl"] {{ display: none !important; }}
          [data-testid="stElementContainer"][data-stale="true"] {{ display: none !important; }}

          /* masthead */
          .masthead-title {{ font-family: {SERIF}; font-size: 1.9rem; font-weight: 700;
                             letter-spacing: -0.01em; color: {INK}; line-height: 1.1; }}
          .masthead-sub {{ font-size: .86rem; color: {MUTED}; margin-top: 4px; max-width: 62ch; }}
          .masthead-meta {{ text-align: right; font-family: {MONO}; font-size: .68rem;
                            color: {MUTED}; line-height: 1.7; white-space: nowrap; }}
          .masthead-rule {{ border: none; border-top: 3px solid {BURGUNDY};
                            margin: 10px 0 4px; }}
          .masthead-rule2 {{ border: none; border-top: 1px solid {INK};
                             margin: 0 0 14px; }}

          /* section kickers */
          .kicker {{ font-size: .68rem; font-weight: 600; letter-spacing: .16em;
                     text-transform: uppercase; color: {BURGUNDY};
                     border-bottom: 1px solid {HAIRLINE}; padding-bottom: 4px;
                     margin: 22px 0 8px; }}
          .kicker .q {{ color: {INK}; }}

          /* footnote captions under every chart/table */
          .fnote {{ font-size: .68rem; color: {MUTED}; line-height: 1.55; margin-top: 4px; }}
          .fnote b {{ color: {INK}; font-weight: 600; }}

          /* pipeline strip */
          .pipe {{ display: flex; align-items: stretch; gap: 0; background: {PANEL};
                   border: 1px solid {HAIRLINE}; }}
          .pipe .stage {{ flex: 1; padding: 10px 12px; border-right: 1px solid {HAIRLINE}; }}
          .pipe .stage:last-child {{ border-right: none; }}
          .pipe .stage .t {{ font-size: .6rem; font-weight: 600; letter-spacing: .12em;
                             text-transform: uppercase; color: {MUTED}; }}
          .pipe .stage .n {{ font-family: {MONO}; font-size: 1.05rem; font-weight: 600;
                             color: {INK}; margin-top: 3px; }}
          .pipe .stage .s {{ font-size: .64rem; color: {FAINT}; margin-top: 2px; }}

          /* plain-English signal line */
          .lead-line {{ font-family: {SERIF}; font-size: .95rem; color: {INK};
                        margin: 2px 0 10px; }}
          .lead-line b {{ color: {BURGUNDY}; }}

          /* verdict blocks */
          .verdict {{ font-size: .78rem; color: {INK}; line-height: 1.5; }}
          .verdict .vnum {{ font-family: {MONO}; font-weight: 600; }}

          /* streamlit element restyle: flat, hairline, sharp corners */
          [data-testid="stDataFrame"] {{ border: 1px solid {HAIRLINE}; }}
          .stTabs [data-baseweb="tab-list"] {{ gap: 18px; border-bottom: 1px solid {HAIRLINE}; }}
          .stTabs [data-baseweb="tab"] {{ font-size: .72rem; font-weight: 600;
            letter-spacing: .12em; text-transform: uppercase; color: {MUTED};
            background: none; border: none; padding: 6px 2px; }}
          .stTabs [aria-selected="true"] {{ color: {BURGUNDY};
            border-bottom: 2px solid {BURGUNDY}; }}
          [data-testid="stMetricValue"] {{ font-family: {MONO}; }}
          div[data-testid="stExpander"] {{ border: 1px solid {HAIRLINE};
            border-radius: 0; background: {PANEL}; }}
          .stSelectbox label {{ font-size: .62rem; text-transform: uppercase;
            letter-spacing: .12em; color: {MUTED}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _plotly_note(fig: go.Figure, *, height: int = 240) -> go.Figure:
    """Research-note chart theme: paper background, hairline grids, no legend box."""
    fig.update_layout(
        height=height,
        paper_bgcolor=PANEL,
        plot_bgcolor=PANEL,
        font=dict(family=MONO, color=INK, size=10),
        margin=dict(l=46, r=14, t=10, b=30),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=PANEL, bordercolor=HAIRLINE, font=dict(color=INK, size=10, family=MONO)
        ),
    )
    fig.update_xaxes(gridcolor="#ECE7DC", zerolinecolor="#ECE7DC", linecolor=HAIRLINE)
    fig.update_yaxes(gridcolor="#ECE7DC", zerolinecolor=HAIRLINE, linecolor=HAIRLINE)
    return fig


# ---------------------------------------------------------------------------
# Formatting — consistent everywhere: 0.0% / +x.x pp / $x.xk / 14 Sep 2026
# ---------------------------------------------------------------------------


def f_pct(v, digits: int = 1, sign: bool = False) -> str:
    if v is None or pd.isna(v):
        return "—"
    s = f"{float(v) * 100:.{digits}f}%"
    return ("+" + s) if sign and float(v) > 0 else s


def f_pp(v, digits: int = 1) -> str:
    """Percentage points with explicit sign — the edge-vs-market unit."""
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v) * 100:+.{digits}f} pp"


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


def f_date(v) -> str:
    """Dates as '14 Sep 2026' throughout the note."""
    if v is None or pd.isna(v):
        return "—"
    return pd.Timestamp(v).strftime("%d %b %Y").lstrip("0")


def f_px(v) -> str:
    return "—" if v is None or pd.isna(v) else f"${float(v):,.2f}"


def _f(v):
    """Decimal/None -> float|None (psycopg2 numerics come back as Decimal)."""
    return None if v is None else float(v)


def _sign_color(v) -> str:
    """Styler callback: institutional green/red strictly by sign."""
    if not isinstance(v, (int, float)) or pd.isna(v):
        return ""
    return f"color: {GOOD}" if v > 0 else (f"color: {BAD}" if v < 0 else "")


# ---------------------------------------------------------------------------
# Data access (read path; cached) — unchanged from the pipeline's perspective
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
               es.confidence, es.catalyst_proximity_score, es.base_rate_score,
               es.financial_score,
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
        "catalyst_proximity_score",
        "base_rate_score",
        "financial_score",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_action_book(horizon_days: int = 365) -> dict:
    """The risk-capped book — the same computation the paper autopilot syncs to."""
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
    """Calibration run history (Brier vs resolved outcomes), oldest to newest."""
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
# Plain-English signal summaries — written from the row's own numbers
# ---------------------------------------------------------------------------


def signal_sentence(r: pd.Series) -> str:
    """One factual sentence about a signal, derived only from its own fields."""
    days = r.get("days_until")
    when = (
        f"expected {f_date(r.get('expected_date'))} ({int(days)} days)"
        if pd.notna(days)
        else "date unconfirmed"
    )
    base = _f(r.get("base_rate"))
    gap = _f(r.get("edge_gap"))
    ttype = r.get("trade_type")
    if ttype == "buy_the_rumor":
        return (
            f"Catalyst {when}. Historical success odds {f_pct(base, 0)}; "
            f"the plan is to ride the pre-event run-up and exit before the result."
        )
    if gap is not None and gap > 0.05:
        return (
            f"Catalyst {when}. The model's expected move exceeds the options "
            f"market's by {f_pp(gap)} — underpriced, so hold through the result."
        )
    return (
        f"Catalyst {when}. Historical success odds {f_pct(base, 0)} with "
        f"acceptable financing; hold through the result."
    )


# ---------------------------------------------------------------------------
# The note
# ---------------------------------------------------------------------------


def _masthead() -> None:
    """Masthead: serif title, plain-English subtitle, as-of/freshness, burgundy rule."""
    px_date = q("SELECT MAX(date) AS d FROM price_history").iloc[0]["d"]
    sc_date = q("SELECT MAX(computed_at) AS d FROM edge_scores").iloc[0]["d"]
    left, right = st.columns([7, 3])
    with left:
        st.markdown(
            "<div class='masthead-title'>Biotech Catalyst Edge Engine</div>", unsafe_allow_html=True
        )
        st.markdown(
            "<div class='masthead-sub'>Systematic screening of binary biotech "
            "catalysts — trial readouts and FDA decisions — scoring each event's "
            "model-derived odds against the move the options market has priced in, "
            "and paper-trading the gap. Decision support; no real money.</div>",
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f"<div class='masthead-meta'>as of {f_date(date.today())}<br>"
            f"prices through {f_date(px_date)}<br>"
            f"scores computed {f_date(sc_date)}</div>",
            unsafe_allow_html=True,
        )
    st.markdown("<hr class='masthead-rule'><hr class='masthead-rule2'>", unsafe_allow_html=True)


def _pipeline_strip(blotter: pd.DataFrame, perf: pd.DataFrame) -> None:
    """Orientation device: the pipeline flow with live counts under each stage."""
    cov = load_coverage()["counts"]
    upcoming = q("SELECT COUNT(*) AS n FROM catalysts WHERE expected_date >= CURRENT_DATE").iloc[0][
        "n"
    ]
    soon = q(
        "SELECT COUNT(*) AS n FROM catalysts WHERE expected_date >= CURRENT_DATE "
        "AND expected_date <= CURRENT_DATE + 90"
    ).iloc[0]["n"]
    outcomes = cov.get("catalyst_outcomes", 0)
    cal = load_calibration()
    latest_cal = cal.iloc[-1] if not cal.empty else None
    brier = _f(latest_cal["brier_score"]) if latest_cal is not None else None
    n_pairs = int(latest_cal["n_pairs"]) if latest_cal is not None else 0
    tot = _f(perf.iloc[-1]["total_return_pct"]) if not perf.empty else None
    xbi = _f(perf.iloc[-1]["xbi_return_pct"]) if not perf.empty else None

    stages = [
        ("Universe", f_int(cov.get("companies")), "small-cap oncology/CNS companies"),
        ("Catalysts tracked", f_int(upcoming), f"{f_int(soon)} dated within 90 days"),
        ("Scored signals", f_int(cov.get("edge_scores")), "every catalyst graded daily"),
        ("Resolved outcomes", f_int(outcomes), "labeled hits/misses so far"),
        (
            "Calibration",
            f"{brier:.3f}" if brier is not None else "—",
            f"Brier score, n={n_pairs} (small)",
        ),
        ("Paper book", f_pct(tot, 1, True), f"vs XBI {f_pct(xbi, 1, True)}"),
    ]
    cells = "".join(
        f"<div class='stage'><div class='t'>{t}</div><div class='n'>{n}</div>"
        f"<div class='s'>{s}</div></div>"
        for t, n, s in stages
    )
    st.markdown(f"<div class='pipe'>{cells}</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='fnote'>The pipeline, left to right: a fixed universe of companies → "
        "their dated clinical/FDA events → each event scored → events that have already "
        "happened get labeled → those labels test the model → the paper portfolio's "
        "record against XBI, the biotech index benchmark.</div>",
        unsafe_allow_html=True,
    )


def _signals_section(blotter: pd.DataFrame, book: dict, equity: float) -> None:
    """CURRENT SIGNALS: the ranked book in human terms, plus per-name drill-down."""
    st.markdown(
        "<div class='kicker'>Current signals — <span class='q'>what the model "
        "is recommending today</span></div>",
        unsafe_allow_html=True,
    )
    rows = book["rows"]
    if not rows:
        st.caption("No signal passed the decision rules today.")
        return

    extra = blotter.set_index("catalyst_id") if not blotter.empty else pd.DataFrame()
    table = []
    for r in rows:
        b = extra.loc[r["catalyst_id"]] if r["catalyst_id"] in extra.index else None
        table.append(
            {
                "Ticker": r["ticker"],
                "Catalyst": (r["catalyst_type"] or "").replace("_", " "),
                "Date": f_date(r["expected_date"]),
                "Model prob.": _f(r["base_rate"]),
                "Model move": _f(b["expected_move"]) if b is not None else None,
                "Market-implied": _f(b["implied_move"]) if b is not None else None,
                "Edge vs market": _f(r["edge_gap"]),
                "Action": TRADE_LABELS.get(r["trade_type"], r["trade_type"]),
                "Weight": r["weight"],
                "$ size": round(r["weight"] * equity, 0) if equity else None,
            }
        )
    df = pd.DataFrame(table)

    top = df.iloc[0]
    top_row = blotter[blotter["ticker"] == top["Ticker"]].sort_values("days_until").iloc[0]
    st.markdown(
        f"<div class='lead-line'>Lead idea: <b>{top['Ticker']}</b> — "
        f"{signal_sentence(top_row)}</div>",
        unsafe_allow_html=True,
    )

    styled = df.style.map(_sign_color, subset=["Edge vs market"]).format(
        {
            "Model prob.": "{:.0%}",
            "Model move": "{:.0%}",
            "Market-implied": "{:.0%}",
            "Edge vs market": "{:+.1%}",
            "Weight": "{:.1%}",
            "$ size": "${:,.0f}",
        },
        na_rep="—",
    )
    st.dataframe(
        styled, use_container_width=True, hide_index=True, height=min(460, 38 + 35 * len(df))
    )
    st.markdown(
        "<div class='fnote'><b>How to read this:</b> <b>Model prob.</b> is the historical "
        "success rate of comparable trials. <b>Model move</b> is the size of move the model "
        "expects around the event; <b>Market-implied</b> is the move options traders have "
        "priced in. <b>Edge vs market</b> is the difference, in percentage points — positive "
        "means the market underprices the event. <b>Weight</b> is the suggested share of the "
        "paper portfolio (Kelly-fractional, capped at 5% per name). "
        f"Book: {book['positions']} positions · gross {f_pct(book['gross_long'], 0)} of "
        f"{f_pct(config.MAX_GROSS_LONG, 0)} cap · GBM cluster {f_pct(book['gbm_pct'], 0)} of "
        f"{f_pct(config.MAX_GBM_WEIGHT, 0)} cap. Source: edge_scores, recomputed daily.</div>",
        unsafe_allow_html=True,
    )

    # -- per-name drill-down: the component scores behind the number --------
    tickers = df["Ticker"].tolist()
    pick = st.selectbox("Inspect a name — the components behind its score", tickers)
    if pick:
        s = blotter[blotter["ticker"] == pick].sort_values("days_until").iloc[0]
        c1, c2 = st.columns(2)
        with c1:
            comp = pd.DataFrame(
                {
                    "Component": [
                        "Catalyst proximity (timing)",
                        "Base rate (historical odds)",
                        "Financial survivability",
                        "Composite grade",
                        "Confidence",
                    ],
                    "Value": [
                        _f(s["catalyst_proximity_score"]),
                        _f(s["base_rate_score"]),
                        _f(s["financial_score"]),
                        _f(s["composite_score"]),
                        _f(s["confidence"]),
                    ],
                }
            )
            st.dataframe(
                comp.style.format({"Value": "{:.2f}"}, na_rep="—"),
                use_container_width=True,
                hide_index=True,
            )
        with c2:
            ctx = pd.DataFrame(
                {
                    "Context": [
                        "30-day run-up",
                        "Short interest (% float)",
                        "Cash runway",
                        "Financing tilt",
                        "Insider tilt",
                    ],
                    "Value": [
                        f_pct(_f(s["run_up_30d"]), 1, True),
                        f_pct(_f(s["short_pct_float"]), 1),
                        (
                            f"{_f(s['runway_months']):.0f} months"
                            if pd.notna(s["runway_months"])
                            else "—"
                        ),
                        f"{_f(s['financing_tilt']):+.2f}" if pd.notna(s["financing_tilt"]) else "—",
                        f"{_f(s['insider_tilt']):+.2f}" if pd.notna(s["insider_tilt"]) else "—",
                    ],
                }
            )
            st.dataframe(ctx, use_container_width=True, hide_index=True)
        st.markdown(
            "<div class='fnote'>The grade blends timing, historical odds, and balance-sheet "
            "strength (near-equal weights, by design). Run-up measures how much hope is "
            "already priced in; financing tilt marks dilution risk; insider tilt flags "
            "open-market buying by executives.</div>",
            unsafe_allow_html=True,
        )


def _evidence_section(perf: pd.DataFrame) -> None:
    """DOES IT WORK?: calibration and paper-book vs benchmark, with honest verdicts."""
    st.markdown(
        "<div class='kicker'>Evidence — <span class='q'>does it work, and how "
        "would you know</span></div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("**Calibration — are the model's probabilities accurate?**")
        cal = load_calibration()
        latest = cal.iloc[-1] if not cal.empty else None
        if latest is None:
            st.caption("No calibration runs yet.")
        else:
            brier = _f(latest["brier_score"])
            n_pairs = int(latest["n_pairs"])
            metrics = pd.DataFrame(
                {
                    "Measure": [
                        "Brier score (probability accuracy; lower = better)",
                        "Resolved outcomes in the test",
                        "Calibration runs to date",
                    ],
                    "Value": [
                        f"{brier:.3f}" if brier is not None else "—",
                        f_int(n_pairs),
                        f_int(len(cal)),
                    ],
                }
            )
            st.dataframe(metrics, use_container_width=True, hide_index=True)
            st.markdown(
                f"<div class='verdict'>Brier <span class='vnum'>{brier:.3f}</span> on "
                f"<span class='vnum'>n={n_pairs}</span> resolved events — far too few to "
                f"judge. This updates automatically as dated catalysts resolve; the number "
                f"is printed as-is rather than hidden.</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f"<div class='fnote'>The validated foundation sits one layer down: the "
            f"trial-success base-rate model scores Brier skill <b>+{BASE_RATE_HOLDOUT['brier_skill']:.3f}</b>, "
            f"AUC <b>{BASE_RATE_HOLDOUT['auc']:.3f}</b> on <b>n={BASE_RATE_HOLDOUT['n']:,}</b> "
            f"held-out trials (temporal split, as of {BASE_RATE_HOLDOUT['date']}). "
            f"A Brier score measures probability accuracy — 0.25 is the coin-flip "
            f"benchmark; skill above zero beats the naive rate.</div>",
            unsafe_allow_html=True,
        )

    with right:
        st.markdown("**Track record — the paper portfolio vs the benchmark**")
        if perf.empty or len(perf) < 2:
            st.caption("Track record starts when the first daily snapshot lands.")
        else:
            fig = go.Figure()
            fig.add_scatter(
                x=perf["snapshot_date"],
                y=perf["equity"],
                mode="lines",
                line=dict(color=BURGUNDY, width=1.6),
            )
            if perf["benchmark_equity"].notna().any():
                fig.add_scatter(
                    x=perf["snapshot_date"],
                    y=perf["benchmark_equity"],
                    mode="lines",
                    line=dict(color=FAINT, width=1.2, dash="dot"),
                )
            # direct labeling at line ends instead of a legend
            fig.add_annotation(
                x=perf["snapshot_date"].iloc[-1],
                y=perf["equity"].iloc[-1],
                text="paper book",
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(size=9, color=BURGUNDY, family=MONO),
            )
            if perf["benchmark_equity"].notna().any():
                fig.add_annotation(
                    x=perf["snapshot_date"].iloc[-1],
                    y=perf["benchmark_equity"].dropna().iloc[-1],
                    text="XBI",
                    showarrow=False,
                    xanchor="left",
                    yanchor="top",
                    font=dict(size=9, color=MUTED, family=MONO),
                )
            _plotly_note(fig, height=230)
            fig.update_yaxes(tickformat="$,.0f")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            tot = _f(perf.iloc[-1]["total_return_pct"])
            xbi = _f(perf.iloc[-1]["xbi_return_pct"])
            days = len(perf)
            ahead = tot is not None and xbi is not None and tot > xbi
            st.markdown(
                f"<div class='verdict'><span class='vnum'>{f_pct(tot, 1, True)}</span> vs "
                f"XBI <span class='vnum'>{f_pct(xbi, 1, True)}</span> over "
                f"<span class='vnum'>{days}</span> trading days — "
                f"{'ahead of' if ahead else 'behind'} the benchmark; too short a window "
                f"to conclude either way.</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div class='fnote'>Source: portfolio_performance daily snapshots; "
                "benchmark is XBI (SPDR biotech ETF) normalized to the same start. "
                "Paper fills at prior close; no transaction costs modeled.</div>",
                unsafe_allow_html=True,
            )

    # -- event study: what history says about these events -------------------
    ev = load_event_returns()
    if not ev.empty:
        h3 = ev[ev["hold_days"] == 3]["abnormal_return"].dropna()
        n_events = ev.groupby(["ticker", "filing_date"]).ngroups
        med = h3.median() if len(h3) else None
        sd = h3.std() if len(h3) else None
        big = (h3.abs() >= 0.10).mean() if len(h3) else None
        st.markdown(
            f"<div class='fnote' style='margin-top:10px'><b>What history says:</b> across "
            f"<b>{f_int(n_events)}</b> past biotech 8-K events, the median 3-day abnormal "
            f"move is <b>{f_pct(med, 1)}</b> with a typical spread of <b>±{f_pct(sd, 0)}</b>; "
            f"<b>{f_pct(big, 0)}</b> of events move 10% or more. Direction is close to a coin "
            f"flip — the edge must come from selecting which events to trade, not predicting "
            f"outcomes. Source: event_returns (stock minus XBI over the window).</div>",
            unsafe_allow_html=True,
        )


def _coverage_section() -> None:
    """COVERAGE & DATA HEALTH: how much real data sits behind the note."""
    st.markdown(
        "<div class='kicker'>Coverage &amp; data health — <span class='q'>how much "
        "real data is behind this</span></div>",
        unsafe_allow_html=True,
    )
    cov = load_coverage()
    c = cov["counts"]
    fr = cov["freshness"]
    left, right = st.columns(2, gap="large")
    with left:
        tbl = pd.DataFrame(
            {
                "Dataset": [
                    "Historical trials mined",
                    "…with success labels",
                    "Base-rate slices",
                    "8-K events studied",
                    "SEC filings parsed",
                    "Insider transactions",
                ],
                "Rows": [
                    c.get("historical_trials"),
                    None,
                    c.get("base_rates"),
                    c.get("event_returns"),
                    c.get("sec_filings"),
                    c.get("insider_transactions"),
                ],
            }
        )
        labeled = q(
            "SELECT COUNT(*) AS n FROM historical_trials " "WHERE primary_outcome_met IS NOT NULL"
        ).iloc[0]["n"]
        tbl.loc[tbl["Dataset"] == "…with success labels", "Rows"] = int(labeled)
        st.dataframe(
            tbl.style.format({"Rows": "{:,}"}, na_rep="—"),
            use_container_width=True,
            hide_index=True,
        )
    with right:
        fresh_rows = []
        for label, tbl_name in [
            ("Prices", "price_history"),
            ("Positioning", "positioning"),
            ("Signals", "edge_scores"),
            ("Insider filings", "insider_transactions"),
            ("SEC filings", "sec_filings"),
        ]:
            ts = fr.get(tbl_name)
            fresh_rows.append({"Feed": label, "Latest data": f_date(ts)})
        st.dataframe(pd.DataFrame(fresh_rows), use_container_width=True, hide_index=True)
    st.markdown(
        "<div class='fnote'>All sources are public: ClinicalTrials.gov, SEC EDGAR, and "
        "end-of-day market data. The pipeline refreshes daily; stale feeds would show "
        "their age here.</div>",
        unsafe_allow_html=True,
    )


def page_note() -> None:
    """The landing view: a complete research note, top to bottom."""
    _purge_open_shorts_once()
    ensure_account()
    acct = get_account()
    blotter = load_blotter()
    book = load_action_book(365)
    perf = load_performance()
    open_df = load_holdings("open")
    prices = latest_prices()
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)

    _masthead()
    _pipeline_strip(blotter, perf)
    _signals_section(blotter, book, summ["equity"])
    _evidence_section(perf)
    _coverage_section()

    st.markdown(
        "<div class='fnote' style='margin-top:18px; border-top: 1px solid #DDD8CE; "
        "padding-top: 8px'>Personal research project. Decision support only; every "
        "position shown is a paper trade. Not investment advice.</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Positions & activity (owner operations)
# ---------------------------------------------------------------------------


def page_positions() -> None:
    """Owner operations: open holdings, closed trades, manual entry."""
    acct = get_account()
    prices = latest_prices()
    open_df = load_holdings("open")
    summ = pf.account_summary(_holding_dicts(open_df), acct["cash"], prices)

    st.markdown("<div class='kicker'>Open positions</div>", unsafe_allow_html=True)
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
                    "Ticker": r.ticker,
                    "Type": TRADE_LABELS.get(r.trade_type, r.trade_type),
                    "Entered": f_date(r.entry_date),
                    "Shares": float(r.shares),
                    "Entry": float(r.entry_price),
                    "Last": cur,
                    "Weight": (abs(mv) / summ["equity"] if mv and summ["equity"] else None),
                    "P&L $": pnl,
                    "P&L %": pnl_pct,
                    "Exit by": f_date(r.planned_exit_date),
                }
            )
        hv = pd.DataFrame(rows).sort_values("P&L $", ascending=False)
        st.dataframe(
            hv.style.map(_sign_color, subset=["P&L $", "P&L %"]).format(
                {
                    "Shares": "{:,.1f}",
                    "Entry": "${:,.2f}",
                    "Last": "${:,.2f}",
                    "Weight": "{:.1%}",
                    "P&L $": "${:+,.0f}",
                    "P&L %": "{:+.1%}",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
            height=min(480, 38 + 35 * len(hv)),
        )
        st.markdown(
            f"<div class='fnote'>Unrealized <b>{f_usd(summ['unrealized_pnl_usd'])}</b> · "
            f"cash <b>{f_usd(summ['cash'])}</b> · invested <b>{f_usd(summ['invested_usd'])}</b>. "
            f"Marks are prior close, end-of-day.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='kicker'>Closed trades</div>", unsafe_allow_html=True)
    closed = load_holdings("closed")
    if closed.empty:
        st.caption("None yet.")
    else:
        cv = pd.DataFrame(
            {
                "Ticker": closed["ticker"],
                "Type": closed["trade_type"].map(lambda t: TRADE_LABELS.get(t, t)),
                "Entry": closed["entry_date"].map(f_date),
                "Exit": closed["exit_date"].map(f_date),
                "Entry $": pd.to_numeric(closed["entry_price"]),
                "Exit $": pd.to_numeric(closed["exit_price"]),
                "Realized $": pd.to_numeric(closed["realized_pnl_usd"]),
            }
        ).sort_values("Exit", ascending=False)
        st.dataframe(
            cv.style.map(_sign_color, subset=["Realized $"]).format(
                {"Entry $": "${:,.2f}", "Exit $": "${:,.2f}", "Realized $": "${:+,.0f}"}, na_rep="—"
            ),
            use_container_width=True,
            hide_index=True,
            height=min(360, 38 + 35 * len(cv)),
        )
        realized = float(cv["Realized $"].sum())
        wins = int((cv["Realized $"] > 0).sum())
        st.markdown(
            f"<div class='fnote'>Realized <b>{f_usd(realized)}</b> across {len(cv)} "
            f"trades · win rate {wins}/{len(cv)}.</div>",
            unsafe_allow_html=True,
        )

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
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Render the research note (default) and the positions appendix."""
    _inject_css()
    tab_note, tab_positions = st.tabs(["Research note", "Positions & activity"])
    with tab_note:
        page_note()
    with tab_positions:
        page_positions()


main()
