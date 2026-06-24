"""
Lever 2 (moneymaxxing) - Daily sized ACTION SHEET.

Turns raw per-name edge scores into an executable, risk-capped book:
  1. one best signal per ticker (avoids double-counting a name's catalysts)
  2. sector caps (per indication_category) and a GBM-correlation cap
  3. gross-long / gross-short / net exposure caps
  4. concrete action + timing per trade type

Output: a dated table (OPEN/HOLD/EXIT timing, target weight) + portfolio summary,
written to data/raw/action_sheet_<date>.csv. Read-only on the DB.

It does NOT know your current positions, so "target weight" is the book it would
hold today; reconcile against what you already own when you trade.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import config
from db import get_connection
from logger import setup_logger

log = setup_logger("action_sheet")

TIMING = {
    "buy_the_rumor": "ENTER now; EXIT ~1 trading day BEFORE the print (sell the news)",
    "hold_through": "ENTER; HOLD through the readout; exit after",
    "fade": "SHORT now; COVER after the print",
}


def risk_haircut(market_cap_usd: float | None) -> float:
    """Position-size multiplier in (0, 1]. Smaller cap -> smaller size (bigger
    realized event moves => more ruin risk). Grounded in returns_regression.py:
    magnitude is predictable, direction is not, and small mcap is the top driver.
    Pure / unit-tested."""
    if not config.RISK_HAIRCUT_ENABLED:
        return 1.0
    if market_cap_usd is None or market_cap_usd <= 0:
        return config.RISK_HAIRCUT_UNKNOWN
    for ceiling, mult in config.RISK_HAIRCUT_TIERS:
        if market_cap_usd < ceiling:
            return mult
    return 1.0


def apply_risk_haircut(rows: list[dict]) -> None:
    """Shrink each weight by its market-cap risk multiplier (in place). Records
    raw_weight + risk_mult for transparency. Only ever reduces |weight|."""
    for r in rows:
        mult = risk_haircut(r.get("market_cap_usd"))
        r["raw_weight"] = r["weight"]
        r["risk_mult"] = mult
        r["weight"] = round(r["weight"] * mult, 4)


def _scale_group(rows: list[dict], key_fn, cap: float) -> None:
    """Scale signed weights within each group so sum(|w|) <= cap. In place."""
    groups: dict = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    for members in groups.values():
        gross = sum(abs(r["weight"]) for r in members)
        if gross > cap and gross > 0:
            f = cap / gross
            for r in members:
                r["weight"] = round(r["weight"] * f, 4)


def _scale_side(rows: list[dict], positive: bool, cap: float) -> None:
    side = [r for r in rows if (r["weight"] > 0) == positive and r["weight"] != 0]
    gross = sum(abs(r["weight"]) for r in side)
    if gross > cap and gross > 0:
        f = cap / gross
        for r in side:
            r["weight"] = round(r["weight"] * f, 4)


_SIGNAL_SQL = """
    SELECT co.ticker, co.name, co.is_gbm_focused, co.market_cap_usd,
           COALESCE(co.indication_category, 'other') AS sector,
           c.catalyst_type, c.expected_date,
           es.trade_type, es.suggested_weight, es.composite_score,
           es.base_rate_score, es.edge_gap, es.confidence
    FROM edge_scores es
    JOIN catalysts c ON c.id = es.catalyst_id
    JOIN companies co ON co.id = es.company_id
    WHERE es.trade_type IS NOT NULL
      AND es.trade_type <> 'avoid'
      AND es.suggested_weight IS NOT NULL
      AND es.suggested_weight <> 0
      AND c.expected_date IS NOT NULL
      AND c.expected_date >= CURRENT_DATE
      AND c.expected_date <= CURRENT_DATE + (:h || ' days')::interval
"""


def _best_per_ticker(raw) -> list[dict]:
    """Best single signal per ticker: highest |weight|, then nearest date."""
    best: dict[str, dict] = {}
    for r in raw:
        w = float(r["suggested_weight"])
        rec = {
            "ticker": r["ticker"], "name": r["name"],
            "is_gbm": bool(r["is_gbm_focused"]), "sector": r["sector"],
            "market_cap_usd": float(r["market_cap_usd"]) if r["market_cap_usd"] is not None else None,
            "trade_type": r["trade_type"], "catalyst_type": r["catalyst_type"],
            "expected_date": r["expected_date"], "weight": round(w, 4),
            "base_rate": float(r["base_rate_score"]) if r["base_rate_score"] is not None else None,
            "edge_gap": float(r["edge_gap"]) if r["edge_gap"] is not None else None,
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
        }
        cur = best.get(r["ticker"])
        if cur is None or abs(rec["weight"]) > abs(cur["weight"]) or (
            abs(rec["weight"]) == abs(cur["weight"]) and rec["expected_date"] < cur["expected_date"]
        ):
            best[r["ticker"]] = rec
    return list(best.values())


def size_book(rows: list[dict]) -> tuple[list[dict], dict]:
    """Apply risk caps (sector -> GBM -> gross sides -> net) in place; return
    (sized_rows, portfolio_summary). Pure: no DB, no printing."""
    _scale_group(rows, lambda r: r["sector"], config.MAX_SECTOR_WEIGHT)
    gbm_rows = [r for r in rows if r["is_gbm"]]
    gbm_gross = sum(abs(r["weight"]) for r in gbm_rows)
    if gbm_gross > config.MAX_GBM_WEIGHT and gbm_gross > 0:
        f = config.MAX_GBM_WEIGHT / gbm_gross
        for r in gbm_rows:
            r["weight"] = round(r["weight"] * f, 4)
    _scale_side(rows, positive=True, cap=config.MAX_GROSS_LONG)
    _scale_side(rows, positive=False, cap=config.MAX_GROSS_SHORT)

    # Enforce net exposure: scale the dominant side down to meet the net cap.
    gl = sum(r["weight"] for r in rows if r["weight"] > 0)
    gs = -sum(r["weight"] for r in rows if r["weight"] < 0)
    net0 = gl - gs
    if net0 > config.MAX_NET and gl > 0:
        target_long = config.MAX_NET + gs
        f = max(0.0, target_long / gl)
        for r in rows:
            if r["weight"] > 0:
                r["weight"] = round(r["weight"] * f, 4)
    elif net0 < -config.MAX_NET and gs > 0:
        target_short = config.MAX_NET + gl
        f = max(0.0, target_short / gs)
        for r in rows:
            if r["weight"] < 0:
                r["weight"] = round(r["weight"] * f, 4)

    rows = [r for r in rows if abs(r["weight"]) >= 0.001]
    rows.sort(key=lambda r: abs(r["weight"]), reverse=True)

    gross_long = sum(r["weight"] for r in rows if r["weight"] > 0)
    gross_short = -sum(r["weight"] for r in rows if r["weight"] < 0)
    summary = {
        "positions": len(rows),
        "gross_long": gross_long,
        "gross_short": gross_short,
        "net": gross_long - gross_short,
        "gbm_pct": sum(abs(r["weight"]) for r in rows if r["is_gbm"]),
    }
    return rows, summary


def compute_book(*, horizon_days: int = 365) -> dict:
    """Query + size the book. Returns {rows, today, horizon_days, **summary}.
    This is the reusable entrypoint (CLI and dashboard both call it)."""
    today = date.today()
    with get_connection() as conn:
        raw = conn.execute(text(_SIGNAL_SQL), {"h": horizon_days}).mappings().all()
    picks = _best_per_ticker(raw)
    apply_risk_haircut(picks)   # de-risk tiny-caps BEFORE applying portfolio caps
    rows, summary = size_book(picks)
    return {"rows": rows, "today": today, "horizon_days": horizon_days, **summary}


def build(*, horizon_days: int = 365, csv_out: str | None = None) -> dict:
    book = compute_book(horizon_days=horizon_days)
    rows = book["rows"]
    today = book["today"]
    gross_long, gross_short = book["gross_long"], book["gross_short"]
    net, gbm_pct = book["net"], book["gbm_pct"]

    print(f"\n=== ACTION SHEET  {today} (horizon {horizon_days}d) ===")
    print(f"{'TICKER':<7}{'TRADE':<15}{'WT':>8}  {'DATE':<11}{'D->':>4}  {'BASE':>5}  TIMING")
    for r in rows:
        d_until = (r["expected_date"] - today).days
        urgent = "!" if d_until <= config.URGENT_DAYS else " "
        base = f"{r['base_rate']:.2f}" if r["base_rate"] is not None else "  - "
        print(f"{r['ticker']:<7}{r['trade_type']:<15}{r['weight']:>+8.3f}  "
              f"{r['expected_date']} {d_until:>3}{urgent} {base:>5}  "
              f"{TIMING.get(r['trade_type'], '')}")

    print("\n--- Portfolio ---")
    print(f"Positions:    {len(rows)}")
    print(f"Gross long:   {gross_long:.1%}  (cap {config.MAX_GROSS_LONG:.0%})")
    print(f"Gross short:  {gross_short:.1%}  (cap {config.MAX_GROSS_SHORT:.0%})")
    print(f"Net:          {net:+.1%}  (cap +/-{config.MAX_NET:.0%})")
    print(f"GBM exposure: {gbm_pct:.1%}  (cap {config.MAX_GBM_WEIGHT:.0%})")
    if abs(net) > config.MAX_NET + 1e-4:
        print(f"WARNING: net exposure {net:+.1%} exceeds +/-{config.MAX_NET:.0%} - trim the dominant side.")

    out_path = csv_out or str(config.RAW_DIR / f"action_sheet_{today}.csv")
    if rows:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["ticker", "name", "trade_type", "weight", "raw_weight",
                             "risk_mult", "expected_date", "days_until", "base_rate",
                             "edge_gap", "sector", "is_gbm", "catalyst_type", "timing"])
            for r in rows:
                writer.writerow([
                    r["ticker"], r["name"], r["trade_type"], r["weight"],
                    r.get("raw_weight"), r.get("risk_mult"),
                    r["expected_date"], (r["expected_date"] - today).days,
                    r["base_rate"], r["edge_gap"], r["sector"], r["is_gbm"],
                    r["catalyst_type"], TIMING.get(r["trade_type"], ""),
                ])
        print(f"\nWrote {len(rows)} positions to {out_path}")

    log.info("action_sheet_complete", positions=len(rows), gross_long=round(gross_long, 4),
             gross_short=round(gross_short, 4), net=round(net, 4), gbm=round(gbm_pct, 4))
    return {"positions": len(rows), "gross_long": gross_long, "gross_short": gross_short, "net": net}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the daily sized action sheet")
    parser.add_argument("--horizon-days", type=int, default=365)
    parser.add_argument("--csv", type=str)
    args = parser.parse_args()
    try:
        config.preflight()
        build(horizon_days=args.horizon_days, csv_out=args.csv)
    except Exception as exc:  # noqa: BLE001
        log.error("action_sheet_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
