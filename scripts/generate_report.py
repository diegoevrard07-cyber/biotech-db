"""Generate a static one-page research note (print-ready HTML) from live data.

Same visual identity as the Edge Terminal dashboard ("institutional research
note"): warm paper, burgundy accent, serif masthead, monospace numerals,
footnote captions with source/as-of/sample-size under every element.

Sections: masthead, pipeline strip, current signals, evidence (calibration +
paper book vs benchmark), catalyst calendar, data coverage. Self-contained HTML
(inline SVG charts, print CSS, no JS, no external assets beyond Google Fonts).

Read-only. No scoring, decision, or ingestion logic lives here — presentation only.

  python scripts/generate_report.py --out docs/report_sample.html
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ (action_sheet)

import pandas as pd
from action_sheet import compute_book

import config
from db import get_connection

# Validated base-rate model holdout (offline research result, not in DB).
# Source: docs/AGENT_HANDOFF.md §5 — temporal holdout run of 2026-06-21.
BASE_RATE_HOLDOUT = {"n": 10127, "brier_skill": 0.098, "auc": 0.676, "date": "21 Jun 2026"}

TRADE_LABELS = {
    "buy_the_rumor": "Buy the rumor",
    "hold_through": "Hold through",
    "avoid": "Avoid",
    "manual": "Manual",
}

# ---------------------------------------------------------------------------
# Queries (read-only; every section tolerates empty results)
# ---------------------------------------------------------------------------


def _q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    """Run a read query on a raw cursor; return rows (empty list on failure)."""
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception:
        return []


def collect() -> dict:
    """Pull every dataset the report needs. Read-only; missing data -> empty."""
    out: dict = {"generated_at": datetime.now(timezone.utc)}
    with get_connection() as conn:
        cur = conn.connection.cursor()

        # The risk-capped book — the same computation the autopilot syncs to.
        try:
            book = compute_book(horizon_days=config.AUTOPILOT_HORIZON_DAYS)
            out["signals"] = [
                (
                    r["ticker"],
                    r["catalyst_type"],
                    r["expected_date"],
                    r["trade_type"],
                    r["base_rate"],
                    r["edge_gap"],
                    r["weight"],
                    r["catalyst_id"],
                )
                for r in book["rows"][:12]
            ]
            out["book_summary"] = {
                "positions": book["positions"],
                "gross_long": book["gross_long"],
                "gbm_pct": book["gbm_pct"],
            }
        except Exception:
            out["signals"] = []
            out["book_summary"] = None

        # Model/market move components for the signal rows (keyed by catalyst).
        out["moves"] = {
            int(r[0]): (
                float(r[1]) if r[1] is not None else None,
                float(r[2]) if r[2] is not None else None,
            )
            for r in _q(cur, "SELECT id, expected_move, implied_move FROM edge_scores")
        }

        out["calibration"] = _q(
            cur,
            "SELECT run_at, n_pairs, brier_score, model_hit_rate, base_rate_hit_rate "
            "FROM calibration_runs ORDER BY run_at DESC LIMIT 1",
        )
        out["calibration_runs"] = (_q(cur, "SELECT COUNT(*) FROM calibration_runs") or [(0,)])[0][0]
        out["outcomes"] = _q(
            cur, "SELECT outcome_label, COUNT(*) FROM catalyst_outcomes GROUP BY 1"
        )

        out["equity"] = _q(
            cur,
            "SELECT snapshot_date, equity, benchmark_equity "
            "FROM portfolio_performance ORDER BY snapshot_date",
        )
        out["account"] = _q(
            cur, "SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1"
        )
        out["open_positions"] = _q(
            cur,
            "SELECT COUNT(*) FROM portfolio_holdings WHERE status='open'",
        )

        out["calendar"] = _q(
            cur,
            """
            SELECT c.expected_date, co.ticker, c.catalyst_type
            FROM catalysts c JOIN companies co ON co.id = c.company_id
            WHERE c.expected_date >= CURRENT_DATE
              AND c.expected_date <= CURRENT_DATE + 90
            ORDER BY c.expected_date
            """,
        )

        for name, sql in {
            "companies": "SELECT COUNT(*) FROM companies WHERE in_universe",
            "catalysts_upcoming": "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE",
            "catalysts_90d": "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE AND expected_date <= CURRENT_DATE + 90",
            "historical_trials": "SELECT COUNT(*) FROM historical_trials",
            "trials_labeled": "SELECT COUNT(*) FROM historical_trials WHERE primary_outcome_met IS NOT NULL",
            "price_rows": "SELECT COUNT(*) FROM price_history",
            "price_tickers": "SELECT COUNT(DISTINCT ticker) FROM price_history WHERE ticker <> %s",
            "price_latest": "SELECT MAX(date) FROM price_history",
            "edge_scores": "SELECT COUNT(*) FROM edge_scores",
            "scores_latest": "SELECT MAX(computed_at) FROM edge_scores",
            "event_returns": "SELECT COUNT(*) FROM event_returns",
        }.items():
            rows = _q(cur, sql, (config.BENCHMARK_TICKER,) if name == "price_tickers" else ())
            out[name] = rows[0][0] if rows else None
        cur.close()
    return out


# ---------------------------------------------------------------------------
# Formatting — consistent: 0.0% / +x.x pp / $x.xk / 14 Sep 2026
# ---------------------------------------------------------------------------


def pct(v, digits: int = 1, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{float(v) * 100:.{digits}f}%"
    return ("+" + s) if sign and float(v) > 0 else s


def pp(v, digits: int = 1) -> str:
    """Percentage points with explicit sign — the edge-vs-market unit."""
    return "—" if v is None else f"{float(v) * 100:+.{digits}f} pp"


def usd(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if abs(v) >= 1e6:
        return f"${v / 1e6:.2f}m"
    if abs(v) >= 1e3:
        return f"${v / 1e3:.1f}k"
    return f"${v:.0f}"


def num(v) -> str:
    """Integer with thousands separators (em dash when missing)."""
    return "—" if v is None else f"{int(v):,}"


def fdate(v) -> str:
    """Dates as '14 Sep 2026'."""
    if v is None:
        return "—"
    return pd.Timestamp(v).strftime("%d %b %Y").lstrip("0")


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


# ---------------------------------------------------------------------------
# Inline SVG charts (hand-rolled: no JS, prints cleanly)
# ---------------------------------------------------------------------------

INK = "#17191E"
PAPER = "#FAF8F3"
PANEL = "#FFFFFF"
HAIRLINE = "#DDD8CE"
MUTED = "#6B6560"
FAINT = "#9A938A"
BURGUNDY = "#8A1F2D"
GOOD = "#0E7A4E"
BAD = "#B3261E"


def _line_svg(
    series: list[tuple[str, list[float | None], str]], *, width: int = 470, height: int = 150
) -> str:
    """Two-series line chart as inline SVG, with direct end-of-line labels."""
    n = max((len(vals) for _, vals, _ in series if vals), default=0)
    if n < 2:
        return ""
    all_vals = [v for _, vals, _ in series for v in vals if v is not None]
    if not all_vals:
        return ""
    lo, hi = min(all_vals), max(all_vals)
    span = (hi - lo) or 1e-9
    pad_l, pad_r, pad_t, pad_b = 46, 66, 10, 18

    def xy(i: int, v: float) -> tuple[float, float]:
        x = pad_l + i * (width - pad_l - pad_r) / (n - 1)
        y = pad_t + (hi - v) / span * (height - pad_t - pad_b)
        return x, y

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" style="width:100%;height:auto">'
    ]
    for frac in (0.0, 0.5, 1.0):
        yv = lo + span * frac
        y = pad_t + (hi - yv) / span * (height - pad_t - pad_b)
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
            f'stroke="#ECE7DC" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="8" fill="{MUTED}">{pct(yv)}</text>'
        )
    for label, vals, color in series:
        pts = " ".join(
            f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}" for i, v in enumerate(vals) if v is not None
        )
        if pts:
            parts.append(
                f'<polyline points="{pts}" fill="none" stroke="{color}" ' f'stroke-width="1.5"/>'
            )
            last = max(i for i, v in enumerate(vals) if v is not None)
            lx, ly = xy(last, vals[last])
            parts.append(
                f'<text x="{lx + 5:.1f}" y="{ly + 3:.1f}" font-size="8" '
                f'fill="{color}">{esc(label)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _calendar_svg(rows: list[tuple], *, width: int = 470, height: int = 54) -> str:
    """Catalyst strip: one tick per event over the next 90 days, labeled."""
    if not rows:
        return ""
    today = date.today()
    span = 90
    pad_l, pad_r = 8, 8
    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto">'
    ]
    parts.append(
        f'<line x1="{pad_l}" y1="{height - 16}" x2="{width - pad_r}" '
        f'y2="{height - 16}" stroke="{INK}" stroke-width="0.8"/>'
    )
    labeled = 0
    labeled_tickers: set[str] = set()
    for d, ticker, ctype in rows:
        days = (d - today).days
        x = pad_l + days / span * (width - pad_l - pad_r)
        tall = ctype == "pdufa"
        parts.append(
            f'<line x1="{x:.1f}" y1="{height - 16}" x2="{x:.1f}" '
            f'y2="{height - (30 if tall else 24)}" '
            f'stroke="{BURGUNDY if tall else FAINT}" stroke-width="1.2"/>'
        )
        # Label each ticker at most once so multi-catalyst names don't repeat.
        if labeled < 14 and (days % 4 == 0 or tall) and ticker not in labeled_tickers:
            parts.append(
                f'<text x="{x:.1f}" y="{height - 36 if tall else height - 32}" '
                f'font-size="7.5" fill="{INK}" text-anchor="middle">{esc(ticker)}</text>'
            )
            labeled += 1
            labeled_tickers.add(ticker)
    for mark in (0, 30, 60, 90):
        x = pad_l + mark / span * (width - pad_l - pad_r)
        lbl = "today" if mark == 0 else f"+{mark}d"
        parts.append(
            f'<text x="{x:.1f}" y="{height - 4}" font-size="7.5" '
            f'fill="{MUTED}" text-anchor="middle">{lbl}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Plain-English signal line (written from the row's own numbers)
# ---------------------------------------------------------------------------


def signal_sentence(ticker: str, ctype: str, edate, ttype: str, base, gap) -> str:
    """One factual sentence about the lead signal, derived only from its fields."""
    days = (edate - date.today()).days if edate else None
    when = f"expected {fdate(edate)} ({days} days)" if edate else "date unconfirmed"
    if ttype == "buy_the_rumor":
        return (
            f"{ticker}: catalyst {when}. Historical success odds {pct(base, 0)}; "
            f"the plan is to ride the pre-event run-up and exit before the result."
        )
    if gap is not None and gap > 0.05:
        return (
            f"{ticker}: catalyst {when}. The model's expected move exceeds the "
            f"options market's by {pp(gap)} — underpriced, so hold through the result."
        )
    return (
        f"{ticker}: catalyst {when}. Historical success odds {pct(base, 0)} with "
        f"acceptable financing; hold through the result."
    )


# ---------------------------------------------------------------------------
# HTML assembly — the note on paper
# ---------------------------------------------------------------------------

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
@page { size: letter; margin: 10mm; }
* { box-sizing: border-box; }
body { font-family: 'Inter', system-ui, sans-serif; color: #17191E;
       background: #FAF8F3; font-size: 9pt; line-height: 1.4; margin: 0; }
.num, td, th, .v { font-family: 'IBM Plex Mono', monospace;
                   font-variant-numeric: tabular-nums; }
header { border-bottom: 2.5px solid #8A1F2D; padding-bottom: 5pt; margin-bottom: 2pt;
         display: flex; justify-content: space-between; align-items: flex-end; }
h1 { font-family: 'Source Serif 4', Georgia, serif; font-size: 15pt; margin: 0;
     letter-spacing: -0.01em; }
.sub { font-size: 7.6pt; color: #6B6560; max-width: 70ch; margin-top: 2pt; }
.meta { font-size: 6.8pt; color: #6B6560; text-align: right; font-family: 'IBM Plex Mono',
        monospace; line-height: 1.6; white-space: nowrap; }
.rule2 { border-top: 0.8pt solid #17191E; margin: 2pt 0 6pt; }
h2 { font-size: 7.2pt; font-weight: 600; text-transform: uppercase;
     letter-spacing: 0.14em; color: #8A1F2D; margin: 8pt 0 3pt;
     border-bottom: 0.5pt solid #DDD8CE; padding-bottom: 2pt; }
h2 .q { color: #17191E; text-transform: none; letter-spacing: 0; font-weight: 500; }
table { border-collapse: collapse; width: 100%; font-size: 7.6pt; background: #fff; }
th { text-transform: uppercase; letter-spacing: 0.06em; font-size: 6.4pt; color: #6B6560;
     border-bottom: 0.8pt solid #17191E; padding: 1.5pt 4pt; text-align: right; }
th:first-child, td:first-child { text-align: left; }
td { border-bottom: 0.4pt solid #ECE7DC; padding: 1.5pt 4pt; text-align: right; }
.pos { color: #0E7A4E; } .neg { color: #B3261E; }
.pipe { display: flex; background: #fff; border: 0.6pt solid #DDD8CE; }
.pipe .stage { flex: 1; padding: 4pt 6pt; border-right: 0.6pt solid #DDD8CE; }
.pipe .stage:last-child { border-right: none; }
.pipe .t { font-size: 5.8pt; font-weight: 600; letter-spacing: 0.1em;
           text-transform: uppercase; color: #6B6560; }
.pipe .n { font-family: 'IBM Plex Mono', monospace; font-size: 9.5pt; font-weight: 600;
           margin-top: 1pt; }
.pipe .s { font-size: 5.8pt; color: #9A938A; }
.cols { display: flex; gap: 10pt; } .cols > div { flex: 1; }
.kv { display: flex; justify-content: space-between; border-bottom: 0.4pt solid #ECE7DC;
      padding: 1.5pt 0; font-size: 7.4pt; background: #fff; }
.kv .k { color: #6B6560; }
.lead { font-family: 'Source Serif 4', Georgia, serif; font-size: 8.6pt; margin: 2pt 0 4pt; }
.lead b { color: #8A1F2D; }
.fnote { font-size: 6.6pt; color: #6B6560; line-height: 1.5; margin-top: 2pt; }
.fnote b { color: #17191E; }
.verdict { font-size: 7.4pt; margin-top: 2pt; }
.verdict .v { font-family: 'IBM Plex Mono', monospace; font-weight: 600; }
footer { border-top: 0.5pt solid #DDD8CE; margin-top: 8pt; padding-top: 3pt;
         font-size: 6.4pt; color: #6B6560; }
"""


def build_html(d: dict) -> str:
    """Assemble the one-page research note HTML from collected data."""
    gen = d["generated_at"].strftime("%d %b %Y %H:%M UTC")
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>Biotech Catalyst Edge Engine — research note — {gen}</title>",
        f"<style>{CSS}</style></head><body>",
    ]

    # -- masthead ------------------------------------------------------------
    parts.append(
        f"<header><div><h1>Biotech Catalyst Edge Engine</h1>"
        f"<div class='sub'>Systematic screening of binary biotech catalysts — trial "
        f"readouts and FDA decisions — scoring each event's model-derived odds against "
        f"the move the options market has priced in, and paper-trading the gap. "
        f"Decision support; no real money.</div></div>"
        f"<div class='meta'>generated {gen}<br>prices through "
        f"{fdate(d.get('price_latest'))}<br>scores computed "
        f"{fdate(d.get('scores_latest'))}</div></header><div class='rule2'></div>"
    )

    # -- pipeline strip -------------------------------------------------------
    cal = d["calibration"][0] if d["calibration"] else None
    brier = float(cal[2]) if cal and cal[2] is not None else None
    n_pairs = int(cal[1]) if cal else 0
    eq_rows = d["equity"]
    start_cap = float(d["account"][0][1]) if d["account"] and d["account"][0][1] else None
    latest_eq = float(eq_rows[-1][1]) if eq_rows else None
    bench_latest = float(eq_rows[-1][2]) if eq_rows and eq_rows[-1][2] else None
    tot = (latest_eq / start_cap - 1) if latest_eq and start_cap else None
    xbi = (bench_latest / start_cap - 1) if bench_latest and start_cap else None
    n_outcomes = sum(n for _, n in d["outcomes"]) if d["outcomes"] else 0

    stages = [
        ("Universe", num(d.get("companies")), "small-cap oncology/CNS"),
        (
            "Catalysts",
            num(d.get("catalysts_upcoming")),
            f"{num(d.get('catalysts_90d'))} within 90 days",
        ),
        ("Scored", num(d.get("edge_scores")), "graded daily"),
        ("Resolved", num(n_outcomes), "labeled outcomes so far"),
        (
            "Calibration",
            f"{brier:.3f}" if brier is not None else "—",
            f"Brier, n={n_pairs} (small)",
        ),
        ("Paper book", pct(tot, 1, True), f"vs XBI {pct(xbi, 1, True)}"),
    ]
    parts.append(
        "<div class='pipe'>"
        + "".join(
            f"<div class='stage'><div class='t'>{t}</div><div class='n'>{n}</div>"
            f"<div class='s'>{s}</div></div>"
            for t, n, s in stages
        )
        + "</div>"
        "<div class='fnote'>The pipeline, left to right: a fixed universe of companies → "
        "their dated clinical/FDA events → each event scored → resolved events labeled → "
        "labels test the model → the paper portfolio's record against XBI, the biotech "
        "index benchmark.</div>"
    )

    # -- current signals -------------------------------------------------------
    bs = d.get("book_summary")
    book_line = (
        f" — {bs['positions']} positions, gross {pct(bs['gross_long'], 0)}, "
        f"GBM cluster {pct(bs['gbm_pct'], 0)}"
        if bs
        else ""
    )
    parts.append(
        f"<h2>Current signals <span class='q'>— what the model recommends "
        f"today{book_line}</span></h2>"
    )
    if d["signals"]:
        first = d["signals"][0]
        parts.append(
            f"<div class='lead'>Lead idea: <b>{esc(first[0])}</b> — "
            f"{esc(signal_sentence(first[0], first[1], first[2], first[3], first[4], first[5]))}</div>"
        )
        parts.append(
            "<table><tr><th>Ticker</th><th>Catalyst</th><th>Date</th>"
            "<th>Model prob.</th><th>Model move</th><th>Market-implied</th>"
            "<th>Edge vs market</th><th>Action</th><th>Weight</th></tr>"
        )
        for ticker, ctype, edate, ttype, base, gap, weight, cat_id in d["signals"]:
            exp_mv, imp_mv = d["moves"].get(cat_id, (None, None))
            gap_cls = "pos" if (gap or 0) >= 0 else "neg"
            parts.append(
                f"<tr><td>{esc(ticker)}</td><td>{esc((ctype or '').replace('_', ' '))}</td>"
                f"<td class='num'>{fdate(edate)}</td>"
                f"<td class='num'>{pct(base, 0)}</td>"
                f"<td class='num'>{pct(exp_mv, 0)}</td>"
                f"<td class='num'>{pct(imp_mv, 0)}</td>"
                f"<td class='num {gap_cls}'>{pp(gap)}</td>"
                f"<td>{esc(TRADE_LABELS.get(ttype, ttype))}</td>"
                f"<td class='num'>{pct(weight, 1)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='fnote'>No active long signals in the scored universe.</p>")
    parts.append(
        "<div class='fnote'><b>How to read this:</b> <b>Model prob.</b> is the historical "
        "success rate of comparable trials. <b>Model move</b> is the move the model expects "
        "around the event; <b>Market-implied</b> is the move options traders have priced "
        "in. <b>Edge vs market</b> is the difference, in percentage points — positive means "
        "the market underprices the event. <b>Weight</b> is the suggested share of the "
        "paper portfolio (Kelly-fractional, capped at 5% per name). Source: edge_scores, "
        "recomputed daily.</div>"
    )

    # -- evidence ---------------------------------------------------------------
    parts.append("<h2>Evidence <span class='q'>— does it work, and how would you know</span></h2>")
    parts.append("<div class='cols'><div>")
    if cal:
        parts.append(
            f"<div class='kv'><span class='k'>Brier score (lower = better; 0.25 = coin flip)</span>"
            f"<span class='v'>{brier:.3f}</span></div>"
            f"<div class='kv'><span class='k'>Resolved outcomes in the test</span>"
            f"<span class='v'>{n_pairs}</span></div>"
            f"<div class='kv'><span class='k'>Calibration runs to date</span>"
            f"<span class='v'>{num(d.get('calibration_runs'))}</span></div>"
            f"<div class='verdict'>Brier <span class='v'>{brier:.3f}</span> on "
            f"<span class='v'>n={n_pairs}</span> resolved events — far too few to judge. "
            f"Printed as-is; the number updates as dated catalysts resolve.</div>"
        )
    else:
        parts.append("<div class='fnote'>No calibration runs yet.</div>")
    parts.append(
        f"<div class='fnote'>The validated foundation sits one layer down: the "
        f"trial-success base-rate model scores Brier skill <b>+{BASE_RATE_HOLDOUT['brier_skill']:.3f}</b>, "
        f"AUC <b>{BASE_RATE_HOLDOUT['auc']:.3f}</b> on <b>n={BASE_RATE_HOLDOUT['n']:,}</b> "
        f"held-out trials (temporal split, as of {BASE_RATE_HOLDOUT['date']}).</div>"
    )
    parts.append("</div><div>")
    if len(d["equity"]) >= 2:
        start = float(d["equity"][0][1]) or 1.0
        eq_n = [float(r[1]) / start - 1 for r in d["equity"]]
        b_n = [(float(r[2]) / start - 1) if r[2] is not None else None for r in d["equity"]]
        svg = _line_svg([("paper book", eq_n, BURGUNDY), ("XBI", b_n, FAINT)])
        if svg:
            parts.append(svg)
        days = len(d["equity"])
        ahead = tot is not None and xbi is not None and tot > xbi
        parts.append(
            f"<div class='verdict'><span class='v'>{pct(tot, 1, True)}</span> vs XBI "
            f"<span class='v'>{pct(xbi, 1, True)}</span> over <span class='v'>{days}</span> "
            f"trading days — {'ahead of' if ahead else 'behind'} the benchmark; too short "
            f"a window to conclude either way.</div>"
            f"<div class='fnote'>Source: portfolio_performance daily snapshots; XBI "
            f"normalized to the same start. Paper fills at prior close; no transaction "
            f"costs modeled.</div>"
        )
    else:
        parts.append(
            "<div class='fnote'>Track record starts when the first daily " "snapshot lands.</div>"
        )
    parts.append("</div></div>")

    # -- calendar ----------------------------------------------------------------
    if d["calendar"]:
        parts.append(
            f"<h2>Catalyst calendar <span class='q'>— next 90 days "
            f"({len(d['calendar'])} dated; tall ticks = PDUFA, the FDA decision deadline)</span></h2>"
            f"{_calendar_svg(d['calendar'])}"
        )

    # -- coverage ------------------------------------------------------------------
    parts.append(
        "<h2>Coverage &amp; data health <span class='q'>— how much real data is behind "
        "this</span></h2>"
        "<div class='cols'><div>"
        f"<div class='kv'><span class='k'>Historical trials mined</span>"
        f"<span class='v'>{num(d.get('historical_trials'))}</span></div>"
        f"<div class='kv'><span class='k'>…with success labels</span>"
        f"<span class='v'>{num(d.get('trials_labeled'))}</span></div>"
        f"<div class='kv'><span class='k'>8-K event returns studied</span>"
        f"<span class='v'>{num(d.get('event_returns'))}</span></div>"
        "</div><div>"
        f"<div class='kv'><span class='k'>Price rows (tickers)</span>"
        f"<span class='v'>{num(d.get('price_rows'))} ({num(d.get('price_tickers'))})</span></div>"
        f"<div class='kv'><span class='k'>Prices current through</span>"
        f"<span class='v'>{fdate(d.get('price_latest'))}</span></div>"
        f"<div class='kv'><span class='k'>Signals scored</span>"
        f"<span class='v'>{num(d.get('edge_scores'))}</span></div>"
        "</div></div>"
        "<div class='fnote'>All sources are public: ClinicalTrials.gov, SEC EDGAR, and "
        "end-of-day market data. The pipeline refreshes daily; stale feeds would show "
        "their age here.</div>"
    )

    parts.append(
        "<footer>Generated by scripts/generate_report.py from the live research database. "
        "Personal research project. Decision support only; all positions are paper trades. "
        "Not investment advice.</footer></body></html>"
    )
    return "".join(parts)


def main() -> None:
    """CLI entry: query the DB and write the self-contained HTML report."""
    ap = argparse.ArgumentParser(description="Generate the one-page research note")
    ap.add_argument(
        "--out",
        default="docs/report_sample.html",
        help="Output HTML path (default: docs/report_sample.html)",
    )
    args = ap.parse_args()
    config.preflight()
    data = collect()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(data), encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    print(
        f"  signals={len(data['signals'])} calendar={len(data['calendar'])} "
        f"equity_points={len(data['equity'])} "
        f"outcomes={sum(n for _, n in data['outcomes']) if data['outcomes'] else 0}"
    )


if __name__ == "__main__":
    main()
