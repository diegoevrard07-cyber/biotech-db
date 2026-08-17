"""Generate a static one-page research report (print-ready HTML) from live data.

Memo format: top signals, trust metrics, equity curve vs benchmark, catalyst
calendar, and data coverage — queried directly from Postgres at generation time.
Self-contained HTML (inline SVG charts, print CSS, no JS, no external assets),
suitable for printing to PDF or handing across a table.

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

import config
from db import get_connection

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

        # Top of the capped action book (best current ideas, long-only engine).
        out["signals"] = _q(
            cur,
            """
            SELECT co.ticker, c.catalyst_type, c.expected_date,
                   es.trade_type, es.base_rate_score, es.edge_gap,
                   es.suggested_weight, es.composite_score
            FROM edge_scores es
            JOIN companies co ON co.id = es.company_id
            LEFT JOIN catalysts c ON c.id = es.catalyst_id
            WHERE es.trade_type IN ('buy_the_rumor', 'hold_through')
              AND es.suggested_weight > 0
              AND (c.expected_date IS NULL OR c.expected_date >= CURRENT_DATE)
            ORDER BY es.suggested_weight DESC
            LIMIT 12
            """,
        )

        # Model trust: latest calibration run against resolved outcomes.
        out["calibration"] = _q(
            cur,
            """
            SELECT run_at, n_pairs, brier_score, model_hit_rate, base_rate_hit_rate
            FROM calibration_runs ORDER BY run_at DESC LIMIT 1
            """,
        )
        out["outcomes"] = _q(
            cur, "SELECT outcome_label, COUNT(*) FROM catalyst_outcomes GROUP BY 1"
        )

        # Paper track record vs benchmark.
        out["equity"] = _q(
            cur,
            """
            SELECT snapshot_date, equity, benchmark_equity
            FROM portfolio_performance ORDER BY snapshot_date
            """,
        )
        out["account"] = _q(
            cur, "SELECT cash_usd, starting_capital_usd FROM portfolio_account WHERE id=1"
        )
        out["open_positions"] = _q(
            cur,
            """
            SELECT ticker, trade_type, shares, entry_price, planned_exit_date
            FROM portfolio_holdings WHERE status='open' ORDER BY entry_date DESC
            """,
        )

        # Catalyst calendar: next 90 days.
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

        # Coverage / freshness.
        for name, sql in {
            "companies": "SELECT COUNT(*) FROM companies WHERE in_universe",
            "catalysts_upcoming": "SELECT COUNT(*) FROM catalysts WHERE expected_date >= CURRENT_DATE",
            "historical_trials": "SELECT COUNT(*) FROM historical_trials",
            "trials_labeled": "SELECT COUNT(*) FROM historical_trials WHERE primary_outcome_met IS NOT NULL",
            "price_rows": "SELECT COUNT(*) FROM price_history",
            "price_tickers": "SELECT COUNT(DISTINCT ticker) FROM price_history WHERE ticker <> %s",
            "price_latest": "SELECT MAX(date) FROM price_history",
            "edge_scores": "SELECT COUNT(*) FROM edge_scores",
            "scores_latest": "SELECT MAX(computed_at) FROM edge_scores",
        }.items():
            rows = _q(cur, sql, (config.BENCHMARK_TICKER,) if name == "price_tickers" else ())
            out[name] = rows[0][0] if rows else None
        cur.close()
    return out


# ---------------------------------------------------------------------------
# Formatting helpers — consistent: 0.0% / x.x× / $m
# ---------------------------------------------------------------------------


def pct(v, digits: int = 1, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{float(v) * 100:.{digits}f}%"
    return ("+" + s) if sign and float(v) > 0 else s


def usd(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if abs(v) >= 1e6:
        return f"${v / 1e6:.2f}m"
    if abs(v) >= 1e3:
        return f"${v / 1e3:.1f}k"
    return f"${v:.0f}"


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


# ---------------------------------------------------------------------------
# Inline SVG charts (hand-rolled: no JS, prints cleanly)
# ---------------------------------------------------------------------------

INK = "#1a1a1a"
ACCENT = "#0b5cad"
MUTED = "#8a8a8a"
GOOD = "#0a7a3d"
BAD = "#b3261e"


def _line_svg(
    series: list[tuple[str, list[float | None], str]],
    *,
    width: int = 460,
    height: int = 150,
    y_fmt=pct,
) -> str:
    """Two-series line chart as inline SVG. series = [(label, values, color)]."""
    n = max((len(vals) for _, vals, _ in series if vals), default=0)
    if n < 2:
        return ""
    all_vals = [v for _, vals, _ in series for v in vals if v is not None]
    if not all_vals:
        return ""
    lo, hi = min(all_vals), max(all_vals)
    span = (hi - lo) or 1e-9
    pad_l, pad_r, pad_t, pad_b = 44, 8, 10, 18

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
            f'stroke="#e2e2e2" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="8" fill="{MUTED}">{y_fmt(yv)}</text>'
        )
    for label, vals, color in series:
        pts = " ".join(
            f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}" for i, v in enumerate(vals) if v is not None
        )
        if pts:
            parts.append(
                f'<polyline points="{pts}" fill="none" stroke="{color}" ' f'stroke-width="1.4"/>'
            )
        if any(v is not None for v in vals):
            parts.append(
                f'<text x="{width - pad_r}" y="{pad_t + 8 + 10 * series.index((label, vals, color))}" '
                f'text-anchor="end" font-size="8" fill="{color}">{esc(label)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _calendar_svg(rows: list[tuple], *, width: int = 460, height: int = 54) -> str:
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
    for d, ticker, ctype in rows:
        days = (d - today).days
        x = pad_l + days / span * (width - pad_l - pad_r)
        tall = ctype == "pdufa"
        parts.append(
            f'<line x1="{x:.1f}" y1="{height - 16}" x2="{x:.1f}" '
            f'y2="{height - (30 if tall else 24)}" '
            f'stroke="{ACCENT if tall else MUTED}" stroke-width="1.2"/>'
        )
        if labeled < 14 and (days % 4 == 0 or tall):
            parts.append(
                f'<text x="{x:.1f}" y="{height - 36 if tall else height - 32}" '
                f'font-size="7.5" fill="{INK}" text-anchor="middle">{esc(ticker)}</text>'
            )
            labeled += 1
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
# HTML assembly
# ---------------------------------------------------------------------------

CSS = """
@page { size: letter; margin: 11mm; }
* { box-sizing: border-box; }
body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a; background: #fff;
       font-size: 9.5pt; line-height: 1.35; margin: 0; }
.num, td, th { font-family: 'SF Mono', 'Consolas', monospace; font-variant-numeric: tabular-nums; }
header { border-bottom: 2px solid #1a1a1a; padding-bottom: 4pt; margin-bottom: 6pt;
         display: flex; justify-content: space-between; align-items: baseline; }
h1 { font-size: 13pt; margin: 0; letter-spacing: 0.02em; }
h2 { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.12em; color: #555;
     margin: 7pt 0 2pt; border-bottom: 0.5pt solid #bbb; padding-bottom: 1pt; }
.meta { font-size: 7.5pt; color: #666; }
table { border-collapse: collapse; width: 100%; font-size: 8pt; }
th { text-transform: uppercase; letter-spacing: 0.06em; font-size: 6.8pt; color: #555;
     border-bottom: 0.8pt solid #1a1a1a; padding: 1.5pt 4pt; text-align: right; }
th:first-child, td:first-child { text-align: left; }
td { border-bottom: 0.4pt solid #ddd; padding: 1.5pt 4pt; text-align: right; }
tr.pos td.ret { color: #0a7a3d; } tr.neg td.ret { color: #b3261e; }
.cols { display: flex; gap: 12pt; } .cols > div { flex: 1; }
.kpi { display: flex; justify-content: space-between; border-bottom: 0.4pt solid #ddd;
       padding: 1.5pt 0; font-size: 8pt; }
.kpi b { font-weight: normal; font-family: 'SF Mono', monospace; }
.note { font-size: 7.3pt; color: #666; margin-top: 3pt; }
footer { border-top: 0.5pt solid #bbb; margin-top: 7pt; padding-top: 3pt;
         font-size: 7pt; color: #666; }
"""


def build_html(d: dict) -> str:
    """Assemble the one-page memo HTML from collected data."""
    gen = d["generated_at"].strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>Edge Engine research memo — {gen}</title><style>{CSS}</style></head><body>",
        f"<header><h1>Biotech Catalyst Edge Engine — research memo</h1>"
        f"<span class='meta'>generated {gen} · paper trading · not investment advice</span></header>",
    ]

    # -- Top signals -------------------------------------------------------
    parts.append("<h2>Top signals (risk-capped book)</h2>")
    if d["signals"]:
        parts.append(
            "<table><tr><th>Ticker</th><th>Catalyst</th><th>Date</th><th>Type</th>"
            "<th>Base rate</th><th>Edge gap</th><th>Weight</th></tr>"
        )
        for ticker, ctype, edate, ttype, base, gap, weight, _comp in d["signals"]:
            gap_cls = "pos" if (gap or 0) >= 0 else "neg"
            parts.append(
                f"<tr class='{gap_cls}'><td>{esc(ticker)}</td><td>{esc(ctype)}</td>"
                f"<td class='num'>{esc(edate)}</td><td>{esc(ttype)}</td>"
                f"<td class='num'>{pct(base, 0)}</td>"
                f"<td class='num ret'>{pct(gap, 1, sign=True) if gap is not None else '—'}</td>"
                f"<td class='num'>{pct(weight, 1)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='note'>No active long signals in the scored universe.</p>")

    # -- Trust + account ----------------------------------------------------
    cal = d["calibration"][0] if d["calibration"] else None
    outcomes = {lbl: n for lbl, n in d["outcomes"]} if d["outcomes"] else {}
    n_outcomes = sum(outcomes.values())
    parts.append("<div class='cols'><div>")
    parts.append("<h2>Should you trust it (validation)</h2>")
    if cal:
        run_at, n_pairs, brier, model_hr, base_hr = cal
        parts.append(
            f"<div class='kpi'>Calibration vs resolved outcomes <b>n = {n_pairs}</b></div>"
            f"<div class='kpi'>Brier score (lower = better) <b>{float(brier):.3f}</b></div>"
            f"<div class='kpi'>Model hit rate vs base-rate hit rate <b>{pct(model_hr, 0)} vs {pct(base_hr, 0)}</b></div>"
            f"<div class='note'>Run {esc(str(run_at)[:10])}. Small samples are shown as-is — "
            "this is ongoing validation, not a claim of alpha.</div>"
        )
    else:
        parts.append(
            "<p class='note'>No calibration runs yet — outcomes accrue as "
            "forward catalysts resolve.</p>"
        )
    parts.append(f"<div class='kpi'>Resolved catalyst outcomes labeled <b>{n_outcomes}</b></div>")
    parts.append("</div><div>")
    parts.append("<h2>Paper account vs benchmark</h2>")
    if d["account"]:
        cash, start_cap = d["account"][0]
        eq_rows = d["equity"]
        latest_eq = float(eq_rows[-1][1]) if eq_rows else None
        bench_latest = float(eq_rows[-1][2]) if eq_rows and eq_rows[-1][2] else None
        ret = (latest_eq / float(start_cap) - 1) if latest_eq and start_cap else None
        bret = (bench_latest / float(start_cap) - 1) if bench_latest and start_cap else None
        parts.append(
            f"<div class='kpi'>Equity <b>{usd(latest_eq)}</b></div>"
            f"<div class='kpi'>Cash <b>{usd(cash)}</b></div>"
            f"<div class='kpi'>Open positions <b>{len(d['open_positions'])}</b></div>"
            f"<div class='kpi'>Total return <b>{pct(ret, 1, sign=True)}</b></div>"
            f"<div class='kpi'>XBI benchmark return <b>{pct(bret, 1, sign=True)}</b></div>"
        )
    else:
        parts.append("<p class='note'>Paper account not initialized.</p>")
    parts.append("</div></div>")

    # -- Equity curve -------------------------------------------------------
    if len(d["equity"]) >= 2:
        dates = [r[0] for r in d["equity"]]
        eq = [float(r[1]) for r in d["equity"]]
        bench = [float(r[2]) if r[2] is not None else None for r in d["equity"]]
        start = eq[0] or 1.0
        eq_n = [v / start - 1 for v in eq]
        b_n = [(v / start - 1) if v is not None else None for v in bench]
        svg = _line_svg([("paper book", eq_n, ACCENT), ("XBI", b_n, MUTED)])
        if svg:
            parts.append(
                "<h2>Paper equity vs XBI (cumulative return since " f"{dates[0]})</h2>{svg}"
            )

    # -- Calendar -----------------------------------------------------------
    if d["calendar"]:
        parts.append(
            f"<h2>Catalyst calendar — next 90 days ({len(d['calendar'])} dated, "
            f"tall ticks = PDUFA)</h2>{_calendar_svg(d['calendar'])}"
        )

    # -- Coverage -----------------------------------------------------------
    latest_px = d.get("price_latest")
    scores_at = str(d.get("scores_latest") or "")[:10]
    parts.append(
        "<h2>Data coverage & freshness</h2>"
        "<div class='cols'><div>"
        f"<div class='kpi'>Universe companies <b>{d.get('companies')}</b></div>"
        f"<div class='kpi'>Upcoming catalysts <b>{d.get('catalysts_upcoming')}</b></div>"
        f"<div class='kpi'>Historical trials mined <b>{d.get('historical_trials')}</b></div>"
        f"<div class='kpi'>…with success labels <b>{d.get('trials_labeled')}</b></div>"
        "</div><div>"
        f"<div class='kpi'>Price rows (tickers) <b>{d.get('price_rows')} ({d.get('price_tickers')})</b></div>"
        f"<div class='kpi'>Prices current through <b>{esc(latest_px)}</b></div>"
        f"<div class='kpi'>Scored catalysts <b>{d.get('edge_scores')}</b></div>"
        f"<div class='kpi'>Scores computed <b>{esc(scores_at)}</b></div>"
        "</div></div>"
    )

    parts.append(
        "<footer>Generated by scripts/generate_report.py from the live research database. "
        "Decision support only; all positions are paper trades. Not investment advice.</footer>"
        "</body></html>"
    )
    return "".join(parts)


def main() -> None:
    """CLI entry: query the DB and write the self-contained HTML report."""
    ap = argparse.ArgumentParser(description="Generate the one-page research report")
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
        f"equity_points={len(data['equity'])} outcomes={sum(n for _, n in data['outcomes']) if data['outcomes'] else 0}"
    )


if __name__ == "__main__":
    main()
