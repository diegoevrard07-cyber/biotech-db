"""
Signed-return regression on the event_returns dataset (roadmap #5).

Question: can leakage-safe, PRE-event information predict the realized abnormal
return around an 8-K? We fit two pure-numpy ridge models on a TEMPORAL split
(train on older events, test on newer ones — no look-ahead):

  1. SIGNED model    -> target = abnormal_return        (can we call direction?)
  2. MAGNITUDE model -> target = |abnormal_return|       (can we call size?)

Features (all computed strictly from price_history BEFORE the filing date):
  run_up 5/10/30/60d, realized vol 30d, log dollar-volume 20d, distance from
  52-week high, log market cap at event (current mcap scaled by price ratio —
  share count is ~stable, so this is leakage-safe for size).

Honest expectation: direction of biotech event reactions is close to a coin
flip (semi-strong efficiency); magnitude (vol clusters) is more predictable and
more useful for SIZING. We report out-of-sample R^2, directional hit-rate, and
standardized coefficients, and compare against the naive "predict the train
mean" baseline. A negative result is a valid, reported outcome — we will NOT
wire a model into the scorer unless it beats the baseline out of sample.

No sklearn (stack discipline): ridge via numpy closed form.
"""

from __future__ import annotations

import argparse
import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sqlalchemy import text

import config
from db import get_connection
from logger import setup_logger

log = setup_logger("returns_regression")

HOLD = 3
MIN_HISTORY = 60          # need >=60 pre-event bars for run_up_60d / vol
FEATURES = [
    "run_up_5d", "run_up_10d", "run_up_30d", "run_up_60d",
    "vol_30d", "log_dollar_vol_20d", "dist_52w_high", "log_mcap",
]


def _load(conn):
    rows = conn.execute(
        text("SELECT company_id, date, close, volume FROM price_history "
             "WHERE company_id IS NOT NULL AND close IS NOT NULL "
             "ORDER BY company_id, date ASC")
    ).all()
    px: dict[int, dict] = {}
    for cid, d, close, vol in rows:
        slot = px.setdefault(cid, {"dates": [], "close": [], "vol": []})
        slot["dates"].append(d)
        slot["close"].append(float(close))
        slot["vol"].append(float(vol) if vol is not None else 0.0)

    mcap = {cid: float(m) for cid, m in conn.execute(
        text("SELECT id, market_cap_usd FROM companies WHERE market_cap_usd IS NOT NULL")
    ).all()}

    events = conn.execute(
        text("SELECT company_id, filing_date, abnormal_return FROM event_returns "
             "WHERE hold_days = :h AND abnormal_return IS NOT NULL "
             "ORDER BY filing_date ASC"),
        {"h": HOLD},
    ).all()
    return px, mcap, events


def _features(px: dict, mcap: dict, cid, filing_date):
    p = px.get(cid)
    if not p:
        return None
    dates, close, vol = p["dates"], p["close"], p["vol"]
    i = bisect.bisect_left(dates, filing_date) - 1   # last bar strictly before filing
    if i < MIN_HISTORY:
        return None
    c0 = close[i]
    if c0 <= 0:
        return None

    def runup(n):
        j = i - n
        return c0 / close[j] - 1.0 if j >= 0 and close[j] > 0 else None

    # realized daily-return vol over last 30 bars
    rets = [close[k] / close[k - 1] - 1.0 for k in range(i - 29, i + 1) if close[k - 1] > 0]
    vol30 = float(np.std(rets)) if len(rets) >= 20 else None

    dvol = np.mean([close[k] * vol[k] for k in range(i - 19, i + 1)])
    win52 = max(close[max(0, i - 251): i + 1])
    dist52 = c0 / win52 - 1.0 if win52 > 0 else None

    m = mcap.get(cid)
    log_mcap = None
    if m and close[-1] > 0:
        log_mcap = float(np.log(max(m * (c0 / close[-1]), 1.0)))

    feats = {
        "run_up_5d": runup(5), "run_up_10d": runup(10),
        "run_up_30d": runup(30), "run_up_60d": runup(60),
        "vol_30d": vol30,
        "log_dollar_vol_20d": float(np.log1p(dvol)) if dvol > 0 else None,
        "dist_52w_high": dist52, "log_mcap": log_mcap,
    }
    if any(v is None for v in feats.values()):
        return None
    return [feats[f] for f in FEATURES]


def _ridge(X, y, lam):
    """Closed-form ridge on standardized X (intercept unpenalized)."""
    n, d = X.shape
    Xa = np.hstack([np.ones((n, 1)), X])
    reg = lam * np.eye(d + 1)
    reg[0, 0] = 0.0
    w = np.linalg.solve(Xa.T @ Xa + reg, Xa.T @ y)
    return w


def _predict(w, X):
    return np.hstack([np.ones((X.shape[0], 1)), X]) @ w


def _r2(y, p):
    ss_res = float(np.sum((y - p) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def run(lam: float = 10.0, test_frac: float = 0.30) -> dict:
    with get_connection() as conn:
        px, mcap, events = _load(conn)

    X, y, dts = [], [], []
    for cid, fd, abn in events:
        row = _features(px, mcap, cid, fd)
        if row is None:
            continue
        X.append(row)
        y.append(float(abn))
        dts.append(fd)
    X = np.array(X, float)
    y = np.array(y, float)
    n = len(y)
    if n < 100:
        print(f"Only {n} usable events — too few. Aborting.")
        return {"n": n}

    # temporal split (events already ordered by filing_date)
    cut = int(n * (1 - test_frac))
    Xtr, Xte = X[:cut], X[cut:]
    ytr, yte = y[:cut], y[cut:]

    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1.0
    Ztr, Zte = (Xtr - mu) / sd, (Xte - mu) / sd

    print("\n" + "=" * 64)
    print(f"SIGNED-RETURN REGRESSION  (hold={HOLD}d, n={n}, "
          f"train={cut} <{dts[cut]}  test={n-cut} >={dts[cut]})")
    print("=" * 64)

    # ---- baseline: predict train mean ----
    base_pred = np.full_like(yte, ytr.mean())
    base_r2 = _r2(yte, base_pred)

    # ---- 1) SIGNED model ----
    w = _ridge(Ztr, ytr, lam)
    pte = _predict(w, Zte)
    r2 = _r2(yte, pte)
    # directional accuracy (only where the model takes a non-trivial view)
    dir_acc = float(np.mean(np.sign(pte) == np.sign(yte)))
    corr = float(np.corrcoef(pte, yte)[0, 1]) if np.std(pte) > 0 else float("nan")

    print("\n[1] DIRECTION (target = signed abnormal return)")
    print(f"    OOS R^2 (model):     {r2:+.4f}")
    print(f"    OOS R^2 (baseline):  {base_r2:+.4f}  (predict train mean)")
    print(f"    Directional hit-rate: {dir_acc:.1%}  (50% = coin flip)")
    print(f"    corr(pred, actual):   {corr:+.3f}")

    # ---- 2) MAGNITUDE model ----
    ytr_m, yte_m = np.abs(ytr), np.abs(yte)
    mu2, sd2 = Xtr.mean(0), Xtr.std(0)
    sd2[sd2 == 0] = 1.0
    Ztr2, Zte2 = (Xtr - mu2) / sd2, (Xte - mu2) / sd2
    wm = _ridge(Ztr2, ytr_m, lam)
    ptem = _predict(wm, Zte2)
    r2m = _r2(yte_m, ptem)
    base_r2m = _r2(yte_m, np.full_like(yte_m, ytr_m.mean()))
    corrm = float(np.corrcoef(ptem, yte_m)[0, 1]) if np.std(ptem) > 0 else float("nan")

    print("\n[2] SIZE (target = |abnormal return|, useful for position sizing)")
    print(f"    OOS R^2 (model):     {r2m:+.4f}")
    print(f"    OOS R^2 (baseline):  {base_r2m:+.4f}")
    print(f"    corr(pred, actual):   {corrm:+.3f}")
    # does predicted-size rank actual size? top vs bottom tercile realized move
    order = np.argsort(ptem)
    t = len(order) // 3
    lo_move = float(np.mean(yte_m[order[:t]]))
    hi_move = float(np.mean(yte_m[order[-t:]]))
    print(f"    realized |move|: bottom-tercile {lo_move:.1%}  vs  top-tercile {hi_move:.1%} "
          f"(by predicted size)")

    print("\n[3] Standardized feature weights (sign model | size model):")
    for k, f in enumerate(FEATURES):
        print(f"    {f:<20} {w[k+1]:+.4f}   |   {wm[k+1]:+.4f}")

    # ---- verdict ----
    print("\n" + "-" * 64)
    sign_useful = (r2 > base_r2 + 1e-4) and (dir_acc > 0.52)
    size_useful = (r2m > base_r2m + 0.01) and (hi_move > lo_move * 1.25)
    print(f"VERDICT: direction predictable? {'YES (weak)' if sign_useful else 'NO'}    "
          f"size predictable? {'YES' if size_useful else 'NO'}")
    if size_useful and not sign_useful:
        print("=> Use the SIZE model for risk/sizing & option selection, NOT for direction.")
    if not sign_useful and not size_useful:
        print("=> Neither beats baseline OOS. Do NOT wire into the scorer. Need better features.")
    print("-" * 64)

    log.info("returns_regression_done", n=n, r2_sign=r2, dir_acc=dir_acc,
             r2_size=r2m, hi_move=hi_move, lo_move=lo_move)
    return {"n": n, "r2_sign": r2, "dir_acc": dir_acc, "r2_size": r2m,
            "hi_move": hi_move, "lo_move": lo_move}


def main() -> None:
    ap = argparse.ArgumentParser(description="Signed/magnitude returns regression on event_returns")
    ap.add_argument("--lam", type=float, default=10.0, help="ridge L2 strength")
    ap.add_argument("--test-frac", type=float, default=0.30)
    args = ap.parse_args()
    try:
        config.preflight()
        run(lam=args.lam, test_frac=args.test_frac)
    except Exception as exc:  # noqa: BLE001
        log.error("returns_regression_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
