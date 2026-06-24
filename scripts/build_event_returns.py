"""
Build the REAL returns-validation dataset from 8-K filing dates.

Each 8-K in `sec_filings` is a material-event announcement date. For every one
that falls inside our price history we measure the realized ABNORMAL return
(stock move minus XBI benchmark) over short hold windows, plus the pre-event
30-day run-up. The result (`event_returns`) is ground truth we can finally use
to test whether our signals actually predict realized profit:

  - Distribution of biotech 8-K reactions (how big, how often big).
  - "Fade the run-up": do names that ran up hard BEFORE an event give back
    return AFTER it? (the core sentiment-gap thesis).
  - Sanity: offerings should be negative, approvals positive (validates the
    abnormal-return math against known-sign events).

Honest caveats:
  - 8-Ks include routine filings (earnings, governance), so the full set is
    diluted with non-catalyst noise. We report both the full set and the
    labeled-event subset (joined from material_events).
  - This is REACTION, not trade P&L: no slippage, no borrow cost, assumes you
    could enter at the prior close. Treat magnitudes as upper bounds.
  - Prices are survivorship-trimmed (delisted names dropped) -> real-world
    downside is worse than shown.

Idempotent: ON CONFLICT (accession_number, hold_days). In-memory price lookups
(one query for all prices) avoid the per-row pooler round-trip trap.
"""

from __future__ import annotations

import argparse
import bisect
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import execute_values
from sqlalchemy import text

import config
from db import get_connection
from layers.composite.outcomes import compute_outcome
from logger import setup_logger

log = setup_logger("build_event_returns")

HOLD_WINDOWS = [1, 3, 5]   # trading days held after the event day
RUNUP_LOOKBACK = 30        # trading days before the event for the run-up

_INSERT_SQL = """
    INSERT INTO event_returns (
        company_id, ticker, accession_number, filing_date, filing_type,
        event_type, hold_days, pre_price, post_price, raw_return,
        benchmark_return, abnormal_return, run_up_30d, created_at
    ) VALUES %s
    ON CONFLICT (accession_number, hold_days) DO UPDATE SET
        event_type = EXCLUDED.event_type,
        pre_price = EXCLUDED.pre_price,
        post_price = EXCLUDED.post_price,
        raw_return = EXCLUDED.raw_return,
        benchmark_return = EXCLUDED.benchmark_return,
        abnormal_return = EXCLUDED.abnormal_return,
        run_up_30d = EXCLUDED.run_up_30d,
        created_at = NOW()
"""
_VALUES_TEMPLATE = (
    "(%(company_id)s, %(ticker)s, %(accession_number)s, %(filing_date)s, "
    "%(filing_type)s, %(event_type)s, %(hold_days)s, %(pre_price)s, "
    "%(post_price)s, %(raw_return)s, %(benchmark_return)s, %(abnormal_return)s, "
    "%(run_up_30d)s, NOW())"
)


def _load_prices(conn) -> tuple[dict[int, tuple[list, list]], tuple[list, list]]:
    """Return ({company_id: (dates, closes)}, (bench_dates, bench_closes)), all sorted."""
    bench_ticker = config.BENCHMARK_TICKER
    rows = conn.execute(
        text(
            "SELECT company_id, ticker, date, close FROM price_history "
            "WHERE close IS NOT NULL ORDER BY company_id NULLS LAST, date ASC"
        )
    ).all()
    by_company: dict[int, tuple[list, list]] = {}
    bench: tuple[list, list] = ([], [])
    for company_id, ticker, d, close in rows:
        if company_id is None:
            if ticker == bench_ticker:
                bench[0].append(d)
                bench[1].append(float(close))
            continue
        slot = by_company.setdefault(company_id, ([], []))
        slot[0].append(d)
        slot[1].append(float(close))
    return by_company, bench


def _on_or_before(dates: list, closes: list, target) -> float | None:
    """Close on the latest trading day <= target."""
    idx = bisect.bisect_right(dates, target) - 1
    return closes[idx] if idx >= 0 else None


def _load_event_types(conn) -> dict[str, str]:
    """Map accession_number -> event_type from material_events (labeled subset)."""
    out: dict[str, str] = {}
    for acc, etype in conn.execute(
        text("SELECT accession_number, event_type FROM material_events "
             "WHERE accession_number IS NOT NULL AND event_type IS NOT NULL")
    ).all():
        out[acc] = etype
    return out


def build(*, dry_run: bool = False) -> dict:
    summary = {"filings": 0, "computed": 0, "no_price": 0, "rows": 0}
    with get_connection() as conn:
        prices, bench = _load_prices(conn)
        bench_dates, bench_closes = bench
        event_types = _load_event_types(conn)

        filings = conn.execute(
            text(
                "SELECT f.company_id, c.ticker, f.accession_number, f.filing_date, f.filing_type "
                "FROM sec_filings f JOIN companies c ON c.id = f.company_id "
                "WHERE f.filing_date IS NOT NULL AND c.ticker IS NOT NULL "
                "ORDER BY f.filing_date ASC"
            )
        ).mappings().all()

        out_rows: list[dict] = []
        for f in filings:
            summary["filings"] += 1
            cid = f["company_id"]
            pdata = prices.get(cid)
            if not pdata:
                summary["no_price"] += 1
                continue
            dates, closes = pdata
            fd = f["filing_date"]

            pre_idx = bisect.bisect_left(dates, fd) - 1      # last close strictly before filing
            event_idx = bisect.bisect_left(dates, fd)        # first trading day on/after filing
            if pre_idx < 0 or event_idx >= len(dates):
                summary["no_price"] += 1
                continue
            pre_close = closes[pre_idx]
            pre_date = dates[pre_idx]

            run_up = None
            ru_idx = pre_idx - RUNUP_LOOKBACK
            if ru_idx >= 0 and closes[ru_idx] > 0:
                run_up = round(pre_close / closes[ru_idx] - 1.0, 6)

            b_pre = _on_or_before(bench_dates, bench_closes, pre_date)
            computed_any = False
            for hold in HOLD_WINDOWS:
                exit_idx = min(event_idx + hold - 1, len(dates) - 1)
                post_close = closes[exit_idx]
                post_date = dates[exit_idx]
                b_post = _on_or_before(bench_dates, bench_closes, post_date)

                out = compute_outcome(pre_close, post_close, b_pre, b_post,
                                      threshold=config.OUTCOME_MOVE_THRESHOLD)
                out_rows.append({
                    "company_id": cid,
                    "ticker": f["ticker"],
                    "accession_number": f["accession_number"],
                    "filing_date": fd.isoformat(),
                    "filing_type": f["filing_type"],
                    "event_type": event_types.get(f["accession_number"]),
                    "hold_days": hold,
                    "pre_price": pre_close,
                    "post_price": post_close,
                    "raw_return": out["raw_return"],
                    "benchmark_return": out["benchmark_return"],
                    "abnormal_return": out["abnormal_return"],
                    "run_up_30d": run_up,
                })
                computed_any = True
            if computed_any:
                summary["computed"] += 1

        summary["rows"] = len(out_rows)
        if not dry_run and out_rows:
            raw = conn.connection
            cur = raw.cursor()
            try:
                execute_values(cur, _INSERT_SQL, out_rows,
                               template=_VALUES_TEMPLATE, page_size=1000)
                raw.commit()
            finally:
                cur.close()

    print("\n=== Build Event Returns ===")
    print(f"8-K filings scanned:    {summary['filings']}")
    print(f"Events with prices:     {summary['computed']}")
    print(f"Skipped (no price):     {summary['no_price']}")
    print(f"Rows written:           {summary['rows']} ({'dry run' if dry_run else 'persisted'})")
    log.info("build_event_returns_complete", **summary)
    return summary


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    i = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[i]


def analyze(hold: int = 3) -> None:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                "SELECT abnormal_return, run_up_30d, event_type FROM event_returns "
                "WHERE hold_days = :h AND abnormal_return IS NOT NULL"
            ),
            {"h": hold},
        ).all()
    abn = [float(r[0]) for r in rows]
    if not abn:
        print("No event_returns rows to analyze. Run the build first.")
        return

    print(f"\n=== Event-Return Validation (hold = {hold} trading days) ===")
    print(f"N events: {len(abn)}")
    print(f"Mean abnormal:   {statistics.mean(abn):+.2%}")
    print(f"Median abnormal: {statistics.median(abn):+.2%}")
    print(f"Stdev abnormal:  {statistics.pstdev(abn):.2%}")
    print(f"P10 / P90:       {_pct(abn,0.10):+.2%} / {_pct(abn,0.90):+.2%}")
    big10 = sum(1 for a in abn if abs(a) >= 0.10) / len(abn)
    big25 = sum(1 for a in abn if abs(a) >= 0.25) / len(abn)
    print(f"|move| >= 10%:   {big10:.1%}   |move| >= 25%: {big25:.1%}")

    # --- Fade-the-run-up test: does pre-event run-up predict post-event return? ---
    paired = [(float(ru), float(a)) for a, ru, _ in rows if ru is not None]
    if len(paired) >= 25:
        paired.sort(key=lambda x: x[0])
        n = len(paired)
        q = n // 5
        print("\n  Fade-the-run-up (quintiles by 30d pre-event run-up):")
        print("  run-up bucket        avg pre-runup   avg FORWARD abnormal   n")
        labels = ["Q1 lowest", "Q2", "Q3", "Q4", "Q5 highest"]
        for i, lab in enumerate(labels):
            lo = i * q
            hi = (i + 1) * q if i < 4 else n
            chunk = paired[lo:hi]
            ru_avg = statistics.mean(c[0] for c in chunk)
            fwd_avg = statistics.mean(c[1] for c in chunk)
            print(f"  {lab:<18}   {ru_avg:+7.1%}        {fwd_avg:+7.2%}            {len(chunk)}")
        # simple Pearson correlation
        xs = [p[0] for p in paired]
        ys = [p[1] for p in paired]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in paired)
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        corr = cov / (vx ** 0.5 * vy ** 0.5) if vx > 0 and vy > 0 else float("nan")
        print(f"\n  corr(run-up, forward abnormal) = {corr:+.3f}  "
              f"(negative => fade works)")

    # --- Sanity / signal by labeled event type ---
    by_type: dict[str, list[float]] = {}
    for a, _, et in rows:
        if et:
            by_type.setdefault(et, []).append(float(a))
    if by_type:
        print("\n  Abnormal return by labeled event type (sanity check):")
        for et, vals in sorted(by_type.items(), key=lambda kv: statistics.mean(kv[1])):
            print(f"  {et:<16} mean {statistics.mean(vals):+7.2%}   median "
                  f"{statistics.median(vals):+7.2%}   n={len(vals)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build + analyze event-return validation set")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--hold", type=int, default=3)
    args = parser.parse_args()
    try:
        config.preflight()
        if not args.analyze_only:
            build(dry_run=args.dry_run)
        analyze(hold=args.hold)
    except Exception as exc:  # noqa: BLE001
        log.error("build_event_returns_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
