"""
GBM Edge Engine — Streamlit dashboard.

Run with:
    streamlit run scripts/dashboard.py

Reads DATABASE_URL from .env (python-dotenv). No credentials are hardcoded.
All DB queries are cached for 5 minutes (@st.cache_data(ttl=300)).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import psycopg2
import streamlit as st
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config / connection
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "")

st.set_page_config(page_title="GBM Edge", layout="wide", page_icon="🧬")

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; padding-bottom: 2rem; }
      [data-testid="stMetricValue"] { font-size: 1.5rem; }
      [data-testid="stMetricLabel"] { opacity: 0.7; }
      div[data-testid="stDataFrame"] { font-size: 0.85rem; }
      h1, h2, h3 { letter-spacing: -0.01em; }
      .stPlotlyChart { border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

CATALYST_EMOJI = {
    "phase_readout": "🧪",
    "pdufa": "📋",
    "advisory_committee": "🏛️",
    "approval": "✅",
    "crl": "⛔",
}

GREEN, YELLOW, RED = "#4ade80", "#fbbf24", "#f87171"


def get_connection():
    """Return a fresh psycopg2 connection from DATABASE_URL."""
    if not DATABASE_URL:
        st.error("DATABASE_URL is not set. Add it to .env in the project root.")
        st.stop()
    return psycopg2.connect(DATABASE_URL)


@st.cache_data(ttl=300)
def run_query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a query and return a DataFrame. Cached for 5 minutes."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if cur.description is None:
                return pd.DataFrame()
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Shared queries
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def load_watchlist() -> pd.DataFrame:
    sql = """
        SELECT
            co.ticker,
            co.name                       AS company_name,
            co.id                         AS company_id,
            c.id                          AS catalyst_id,
            c.catalyst_type,
            c.expected_date,
            c.sec_confirmed,
            c.base_rate                   AS raw_base_rate,
            c.base_rate_n,
            es.composite_score,
            es.catalyst_proximity_score,
            es.science_score,
            es.base_rate_score,
            es.financial_score,
            es.confidence,
            t.nct_id,
            t.phase,
            t.indication,
            t.intervention,
            t.enrollment,
            t.primary_endpoint,
            f.runway_months
        FROM edge_scores es
        JOIN catalysts c  ON c.id = es.catalyst_id
        JOIN companies co ON co.id = es.company_id
        LEFT JOIN trials t ON t.id = c.trial_id
        LEFT JOIN LATERAL (
            SELECT runway_months
            FROM financials f2
            WHERE f2.company_id = co.id
            ORDER BY period_end DESC
            LIMIT 1
        ) f ON TRUE
    """
    df = run_query(sql)
    if df.empty:
        return df
    df["expected_date"] = pd.to_datetime(df["expected_date"], errors="coerce")
    today = pd.Timestamp(date.today())
    df["days_until"] = (df["expected_date"] - today).dt.days
    for col in [
        "composite_score", "catalyst_proximity_score", "science_score",
        "base_rate_score", "financial_score", "confidence", "runway_months",
        "raw_base_rate",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_material_events(ticker: str) -> pd.DataFrame:
    df = run_query(
        """
        SELECT ticker, event_type, event_date, filing_date, confidence,
               drug_name, accession_number
        FROM material_events
        WHERE ticker = %s
        ORDER BY COALESCE(event_date, filing_date) DESC
        """,
        (ticker,),
    )
    if not df.empty:
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
        df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_financials(company_id: int) -> pd.DataFrame:
    df = run_query(
        """
        SELECT period_end, cash_and_equivalents_usd, total_liquidity_usd,
               quarterly_burn_usd, runway_months
        FROM financials
        WHERE company_id = %s
        ORDER BY period_end
        """,
        (company_id,),
    )
    if not df.empty:
        df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce")
        for c in ["cash_and_equivalents_usd", "total_liquidity_usd",
                  "quarterly_burn_usd", "runway_months"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_score_history(catalyst_id: int) -> pd.DataFrame:
    df = run_query(
        """
        SELECT computed_at, composite_score, layer1_score, layer3_score, layer4_score
        FROM score_history
        WHERE catalyst_id = %s
        ORDER BY computed_at
        """,
        (catalyst_id,),
    )
    if not df.empty:
        df["computed_at"] = pd.to_datetime(df["computed_at"], errors="coerce")
        for c in ["composite_score", "layer1_score", "layer3_score", "layer4_score"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_usd(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f}M"
    if abs(value) >= 1e3:
        return f"${value / 1e3:.0f}K"
    return f"${value:.0f}"


def catalyst_label(row) -> str:
    emoji = CATALYST_EMOJI.get(row["catalyst_type"], "•")
    return f"{emoji} {row['catalyst_type']}"


def color_score(val):
    if val is None or pd.isna(val) or val == "—":
        return ""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v < 0.3:
        return f"color: {RED}; font-weight: 600"
    if v < 0.6:
        return f"color: {YELLOW}; font-weight: 600"
    return f"color: {GREEN}; font-weight: 600"


def color_runway(val):
    if val is None or pd.isna(val) or val == "—":
        return ""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v < 6:
        return f"color: {RED}; font-weight: 600"
    if v <= 12:
        return f"color: {YELLOW}; font-weight: 600"
    return f"color: {GREEN}; font-weight: 600"


# ===========================================================================
# SECTION 1 — CATALYST WATCHLIST
# ===========================================================================
def page_watchlist() -> None:
    st.title("🧬 GBM Edge — Catalyst Watchlist")
    df = load_watchlist()

    if df.empty:
        st.warning("No edge scores found. Run `python scripts/run_composite.py` first.")
        return

    # ---- Sidebar filters ----
    st.sidebar.subheader("Filters")
    window = st.sidebar.select_slider(
        "Horizon (days)", options=[30, 90, 180, 365], value=365
    )
    min_score = st.sidebar.slider("Minimum composite score", 0.0, 1.0, 0.0, 0.05)
    types = sorted(df["catalyst_type"].dropna().unique().tolist())
    sel_types = st.sidebar.multiselect("Catalyst type", types, default=types)
    tickers = sorted(df["ticker"].dropna().unique().tolist())
    sel_tickers = st.sidebar.multiselect("Ticker (searchable)", tickers, default=[])
    only_sec = st.sidebar.checkbox("Only SEC-confirmed dates", value=False)
    only_runway = st.sidebar.checkbox("Only full runway data", value=False)

    f = df.copy()
    f = f[(f["days_until"].isna()) | (f["days_until"] <= window)]
    f = f[(f["composite_score"].fillna(0) >= min_score)]
    if sel_types:
        f = f[f["catalyst_type"].isin(sel_types)]
    if sel_tickers:
        f = f[f["ticker"].isin(sel_tickers)]
    if only_sec:
        f = f[f["sec_confirmed"] == True]  # noqa: E712
    if only_runway:
        f = f[f["runway_months"].notna()]

    f = f.sort_values("composite_score", ascending=False, na_position="last")

    # ---- Top metrics ----
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Catalysts (filtered)", len(f))
    c2.metric("Top score", f"{f['composite_score'].max():.2f}" if len(f) else "—")
    c3.metric("Median score", f"{f['composite_score'].median():.2f}" if len(f) else "—")
    c4.metric("SEC-confirmed", int((f["sec_confirmed"] == True).sum()))  # noqa: E712
    c5.metric("Within 90 days", int((f["days_until"] <= 90).sum()))

    st.caption(f"Showing {len(f)} of {len(df)} scored catalysts. Click any column header to sort.")

    # ---- Top catalysts chart ----
    top = f.dropna(subset=["composite_score"]).head(15).copy()
    if not top.empty:
        top["catalyst"] = top.apply(catalyst_label, axis=1)
        figtop = px.bar(
            top.sort_values("composite_score"),
            x="composite_score", y="ticker", orientation="h",
            color="catalyst_type", hover_data=["company_name", "expected_date"],
            range_x=[0, 1],
        )
        figtop.update_layout(
            height=max(280, 22 * len(top)), margin=dict(l=10, r=10, t=30, b=10),
            legend_title_text="", yaxis_title="", xaxis_title="composite score",
            title="Top catalysts by composite score",
        )
        st.plotly_chart(figtop, use_container_width=True)

    # ---- Build display frame ----
    view = pd.DataFrame({
        "ticker": f["ticker"],
        "company": f["company_name"],
        "catalyst": f.apply(catalyst_label, axis=1),
        "expected_date": f["expected_date"].dt.date.astype("object"),
        "days_until": f["days_until"],
        "composite": f["composite_score"],
        "proximity": f["catalyst_proximity_score"],
        "science": f["science_score"],
        "base_rate": f["base_rate_score"],
        "financial": f["financial_score"],
        "runway_mo": f["runway_months"],
        "sec": f["sec_confirmed"].map(lambda x: "✓" if x else "estimate"),
    })

    styled = (
        view.style
        .map(color_score, subset=["composite"])
        .map(color_runway, subset=["runway_mo"])
        .format({
            "composite": "{:.2f}", "proximity": "{:.2f}", "base_rate": "{:.2f}",
            "financial": "{:.2f}", "runway_mo": "{:.1f}", "days_until": "{:.0f}",
        }, na_rep="—")
        .format({"science": lambda v: "—"})
    )
    st.dataframe(styled, use_container_width=True, height=560)


# ===========================================================================
# SECTION 2 — CATALYST DETAIL
# ===========================================================================
def page_detail() -> None:
    st.title("🔬 Catalyst Detail")
    df = load_watchlist()
    if df.empty:
        st.warning("No catalysts available.")
        return

    df = df.sort_values("composite_score", ascending=False, na_position="last")
    options = df["catalyst_id"].tolist()

    def _label(cid: int) -> str:
        r = df[df["catalyst_id"] == cid].iloc[0]
        d = r["expected_date"]
        d_str = d.date().isoformat() if pd.notna(d) else "no date"
        return f"{r['ticker']} — {r['catalyst_type']} — {d_str}"

    cid = st.selectbox("Select catalyst", options, format_func=_label)
    row = df[df["catalyst_id"] == cid].iloc[0]

    left, right = st.columns(2)

    # ---- LEFT ----
    with left:
        st.subheader("Trial")
        if pd.notna(row["nct_id"]):
            st.markdown(
                f"**[{row['nct_id']}](https://clinicaltrials.gov/study/{row['nct_id']})**"
            )
        else:
            st.caption("No linked trial.")
        meta = {
            "Phase": row["phase"] or "—",
            "Indication": row["indication"] or "—",
            "Intervention": row["intervention"] or "—",
            "Enrollment": int(row["enrollment"]) if pd.notna(row["enrollment"]) else "—",
            "Primary endpoint": row["primary_endpoint"] or "—",
        }
        for k, v in meta.items():
            st.markdown(f"**{k}:** {v}")

        st.subheader("Score breakdown")
        breakdown = pd.DataFrame({
            "component": ["proximity", "science", "base_rate", "financial"],
            "score": [
                row["catalyst_proximity_score"],
                row["science_score"],
                row["base_rate_score"],
                row["financial_score"],
            ],
        })
        breakdown["score"] = pd.to_numeric(breakdown["score"], errors="coerce").fillna(0)
        fig = px.bar(
            breakdown, x="score", y="component", orientation="h",
            range_x=[0, 1], color="component",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(showlegend=False, height=240, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        comp = row["composite_score"]
        st.metric("Composite score", f"{comp:.3f}" if pd.notna(comp) else "—")
        st.caption(
            "Science is unavailable (Layer 2 not built); its weight is redistributed "
            "across proximity, base rate, and financial."
        )

    # ---- RIGHT ----
    with right:
        st.subheader("Company snapshot")
        fin = load_financials(int(row["company_id"]))
        mc1, mc2, mc3 = st.columns(3)
        if not fin.empty:
            latest = fin.iloc[-1]
            mc1.metric("Cash", fmt_usd(latest["cash_and_equivalents_usd"]))
            rm = latest["runway_months"]
            mc2.metric("Runway (mo)", f"{rm:.1f}" if pd.notna(rm) else "—")
        else:
            mc1.metric("Cash", "—")
            mc2.metric("Runway (mo)", "—")
        mcap = run_query(
            "SELECT market_cap_usd FROM companies WHERE id = %s",
            (int(row["company_id"]),),
        )
        mcap_val = mcap.iloc[0, 0] if not mcap.empty else None
        mc3.metric("Market cap", fmt_usd(mcap_val))

        st.subheader("Material events")
        ev = load_material_events(row["ticker"])
        if ev.empty:
            st.caption("No SEC material events for this ticker.")
        else:
            ev2 = ev.copy()
            ev2["when"] = ev2["event_date"].fillna(ev2["filing_date"])
            ev2 = ev2.dropna(subset=["when"])
            if not ev2.empty:
                figt = px.scatter(
                    ev2, x="when", y="event_type", color="event_type",
                    hover_data=["drug_name", "confidence", "accession_number"],
                )
                figt.update_traces(marker=dict(size=12))
                figt.update_layout(showlegend=False, height=260,
                                   margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(figt, use_container_width=True)
            else:
                st.caption("Events present but undated.")

        st.subheader("Base rate context")
        br = row["raw_base_rate"]
        n = row["base_rate_n"]
        if pd.notna(br):
            n_str = int(n) if pd.notna(n) else "?"
            st.info(
                f"Historical success for **{row['phase'] or 'phase ?'} / "
                f"{row['indication'] or 'indication ?'}**: "
                f"**{br * 100:.0f}%** (n={n_str})"
            )
        else:
            st.caption("No base rate matched for this catalyst.")

        st.subheader("Score history")
        hist = load_score_history(int(cid))
        if hist.empty or len(hist) < 1:
            st.caption("No score history yet.")
        else:
            figh = px.line(
                hist, x="computed_at",
                y=["composite_score", "layer1_score", "layer3_score", "layer4_score"],
                markers=True,
            )
            figh.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10),
                              legend_title_text="")
            st.plotly_chart(figh, use_container_width=True)


# ===========================================================================
# SECTION 3 — COMPANY VIEW
# ===========================================================================
def page_company() -> None:
    st.title("🏢 Company View")
    df = load_watchlist()
    companies = run_query(
        "SELECT id, ticker, name, market_cap_usd, cik FROM companies "
        "WHERE ticker IS NOT NULL ORDER BY ticker"
    )
    if companies.empty:
        st.warning("No companies found.")
        return

    ticker = st.selectbox("Select ticker", companies["ticker"].tolist())
    crow = companies[companies["ticker"] == ticker].iloc[0]
    company_id = int(crow["id"])

    m1, m2, m3 = st.columns(3)
    m1.metric("Company", crow["name"])
    m2.metric("Market cap", fmt_usd(crow["market_cap_usd"]))
    m3.metric("CIK", crow["cik"] if pd.notna(crow["cik"]) else "— (no SEC link)")

    # ---- Pipeline ----
    st.subheader("Pipeline (trials by phase)")
    trials = run_query(
        """
        SELECT nct_id, phase, status, indication, intervention, enrollment,
               estimated_readout_date, primary_completion_date
        FROM trials
        WHERE company_id = %s
        ORDER BY phase,
                 COALESCE(estimated_readout_date, primary_completion_date) NULLS LAST
        """,
        (company_id,),
    )
    if trials.empty:
        st.caption("No trials linked to this company.")
    else:
        for phase in trials["phase"].fillna("Unknown").unique():
            sub = trials[trials["phase"].fillna("Unknown") == phase]
            with st.expander(f"{phase}  ({len(sub)} trials)", expanded=False):
                st.dataframe(sub, use_container_width=True, hide_index=True)

    # ---- Upcoming catalysts ----
    st.subheader("Upcoming catalysts")
    if not df.empty:
        cdf = df[df["ticker"] == ticker].sort_values("expected_date")
        if cdf.empty:
            st.caption("No scored catalysts for this ticker.")
        else:
            show = cdf[[
                "catalyst_type", "expected_date", "composite_score",
                "base_rate_score", "financial_score", "sec_confirmed",
            ]].copy()
            show["expected_date"] = show["expected_date"].dt.date.astype("object")
            st.dataframe(show, use_container_width=True, hide_index=True)

    # ---- Financials trend ----
    st.subheader("Financials trend")
    fin = load_financials(company_id)
    if fin.empty:
        st.caption("No financials ingested for this company.")
    else:
        melt = fin.melt(
            id_vars="period_end",
            value_vars=["cash_and_equivalents_usd", "quarterly_burn_usd"],
            var_name="metric", value_name="usd",
        )
        figf = px.line(melt, x="period_end", y="usd", color="metric", markers=True)
        figf.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          legend_title_text="")
        st.plotly_chart(figf, use_container_width=True)

    # ---- Material events feed ----
    st.subheader("Material events feed")
    ev = load_material_events(ticker)
    if ev.empty:
        st.caption("No SEC material events.")
    else:
        feed = ev[["event_date", "filing_date", "event_type", "drug_name",
                   "confidence", "accession_number"]].copy()
        feed["event_date"] = feed["event_date"].dt.date.astype("object")
        feed["filing_date"] = feed["filing_date"].dt.date.astype("object")
        st.dataframe(feed, use_container_width=True, hide_index=True)


# ===========================================================================
# SECTION 4 — CATALYST CALENDAR
# ===========================================================================
def page_calendar() -> None:
    st.title("📅 Catalyst Calendar")
    df = load_watchlist()
    if df.empty:
        st.warning("No catalysts available.")
        return

    include_past = st.sidebar.checkbox("Include past 30 days", value=False)
    today = pd.Timestamp(date.today())
    horizon = today + pd.Timedelta(days=365)
    lower = today - pd.Timedelta(days=30) if include_past else today

    cal = df.dropna(subset=["expected_date"]).copy()
    cal = cal[(cal["expected_date"] >= lower) & (cal["expected_date"] <= horizon)]

    if cal.empty:
        st.info("No catalysts in the selected window.")
        return

    cal["size_val"] = cal["composite_score"].fillna(0.1).clip(lower=0.1)
    fig = px.scatter(
        cal,
        x="expected_date",
        y="ticker",
        size="size_val",
        color="catalyst_type",
        size_max=22,
        hover_data={
            "company_name": True, "catalyst_type": True, "phase": True,
            "composite_score": ":.2f", "expected_date": True,
            "size_val": False, "ticker": False,
        },
    )
    fig.update_layout(
        height=max(420, 18 * cal["ticker"].nunique()),
        margin=dict(l=10, r=10, t=30, b=10),
        legend_title_text="Catalyst type",
        xaxis_title="Expected date", yaxis_title="",
    )
    fig.add_vline(x=today, line_dash="dash", line_color="#888")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{len(cal)} catalysts · dot size ∝ composite score · dashed line = today")


# ===========================================================================
# SECTION 5 — DATA HEALTH
# ===========================================================================
@st.cache_data(ttl=300)
def table_counts() -> dict:
    tables = ["companies", "trials", "catalysts", "material_events",
              "financials", "edge_scores"]
    out = {}
    for t in tables:
        r = run_query(f"SELECT COUNT(*) AS n FROM {t}")
        out[t] = int(r.iloc[0, 0]) if not r.empty else 0
    return out


@st.cache_data(ttl=300)
def last_updated() -> pd.DataFrame:
    specs = [
        ("companies", "updated_at"),
        ("trials", "fetched_at"),
        ("catalysts", "created_at"),
        ("material_events", "created_at"),
        ("financials", "computed_at"),
        ("edge_scores", "computed_at"),
        ("sec_filings", "fetched_at"),
        ("score_history", "computed_at"),
    ]
    rows = []
    for tbl, col in specs:
        r = run_query(f"SELECT MAX({col}) AS ts FROM {tbl}")
        ts = r.iloc[0, 0] if not r.empty else None
        rows.append({"table": tbl, "last_updated": ts})
    return pd.DataFrame(rows)


def page_health() -> None:
    st.title("🩺 Data Health")

    counts = table_counts()
    cols = st.columns(len(counts))
    for col, (name, n) in zip(cols, counts.items()):
        col.metric(name, f"{n:,}")

    counts_df = pd.DataFrame(
        {"table": list(counts.keys()), "rows": list(counts.values())}
    )
    figc = px.bar(counts_df, x="table", y="rows", color="table", text="rows")
    figc.update_layout(
        height=300, margin=dict(l=10, r=10, t=30, b=10), showlegend=False,
        yaxis_type="log", title="Row counts by table (log scale)", xaxis_title="",
    )
    st.plotly_chart(figc, use_container_width=True)

    st.subheader("Last updated")
    st.dataframe(last_updated(), use_container_width=True, hide_index=True)

    st.subheader("Layer status")
    layers = pd.DataFrame([
        {"layer": "Layer 1 (Catalysts)", "status": "✅ Production"},
        {"layer": "Layer 2 (AI Council)", "status": "❌ Not built — science_score unavailable"},
        {"layer": "Layer 3 (Base Rates)", "status": "✅ Production"},
        {"layer": "Layer 4 (SEC)", "status": "⚠️ Production but low recall (~5% material event extraction rate)"},
        {"layer": "Composite", "status": "✅ Working with 3 of 4 inputs"},
    ])
    st.dataframe(layers, use_container_width=True, hide_index=True)

    st.subheader("Data quality warnings")

    null_fin = run_query(
        """
        SELECT co.ticker, c.catalyst_type, c.expected_date
        FROM edge_scores es
        JOIN catalysts c ON c.id = es.catalyst_id
        JOIN companies co ON co.id = es.company_id
        WHERE es.financial_score IS NULL
        ORDER BY co.ticker
        """
    )
    with st.expander(f"Catalysts with NULL financial_score ({len(null_fin)})"):
        st.dataframe(null_fin, use_container_width=True, hide_index=True)

    past = run_query(
        """
        SELECT co.ticker, c.catalyst_type, c.expected_date
        FROM catalysts c
        JOIN companies co ON co.id = c.company_id
        WHERE c.expected_date < CURRENT_DATE
        ORDER BY c.expected_date DESC
        """
    )
    with st.expander(f"Catalysts past expected_date, no recorded readout ({len(past)})"):
        st.dataframe(past, use_container_width=True, hide_index=True)

    no_cik = run_query(
        "SELECT ticker, name FROM companies WHERE cik IS NULL AND ticker IS NOT NULL "
        "ORDER BY ticker"
    )
    with st.expander(f"Companies with no CIK — delisted/acquired ({len(no_cik)})"):
        st.dataframe(no_cik, use_container_width=True, hide_index=True)

    stale = run_query(
        """
        SELECT co.ticker, f.period_end, f.computed_at
        FROM financials f
        JOIN companies co ON co.id = f.company_id
        WHERE f.computed_at < NOW() - INTERVAL '90 days'
        ORDER BY f.computed_at
        """
    )
    with st.expander(f"Stale financials (refreshed >90 days ago) ({len(stale)})"):
        if stale.empty:
            st.caption("All financials refreshed within the last 90 days.")
        else:
            st.dataframe(stale, use_container_width=True, hide_index=True)


# ===========================================================================
# Router
# ===========================================================================
PAGES = {
    "Catalyst Watchlist": page_watchlist,
    "Catalyst Detail": page_detail,
    "Company View": page_company,
    "Catalyst Calendar": page_calendar,
    "Data Health": page_health,
}


def main() -> None:
    st.sidebar.title("🧬 GBM Edge")
    choice = st.sidebar.radio("Navigate", list(PAGES.keys()))
    st.sidebar.markdown("---")
    PAGES[choice]()
    st.sidebar.caption("Data cached 5 min · read-only")


if __name__ == "__main__":
    main()
