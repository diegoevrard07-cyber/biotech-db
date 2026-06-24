"""Train + out-of-sample validate a clinical-trial SUCCESS model (logistic regression).

This is "the regression formula" applied where the data actually supports it: ~15k
historical trials with a known primary-endpoint outcome. It predicts P(trial succeeds)
from phase, indication, sponsor class/track-record, enrollment, and era. The output is
a sharper probability that feeds `base_rate` -> the grade -> the edge gap.

Honest scope: this predicts CLINICAL success, which is a FEATURE of the trade, not the
stock return. The signed-return model is blocked on data depth (see AGENT_HANDOFF.md).

Validation = temporal holdout (train on older trials, test on newer) so we measure
generalization, not memorization. We compare against the base-rate LOOKUP baseline
(phase x indication x sponsor_class group means) to prove the regression earns its keep.

Saves the fitted model to data/models/success_model.json. Pure numpy (no sklearn).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from layers.composite import logreg
from logger import setup_logger

log = setup_logger("train_success_model")

MODEL_PATH = Path(__file__).resolve().parents[1] / "data" / "models" / "success_model.json"
TEST_FRACTION = 0.30  # newest 30% of trials by completion date = out-of-sample test
SPONSOR_SHRINK = 10.0  # pseudo-count toward the global rate for thin sponsors


def _load() -> pd.DataFrame:
    sql = """
        SELECT id, phase, indication_category, sponsor_class, sponsor_name,
               enrollment, primary_completion_date, primary_outcome_met
        FROM historical_trials
        WHERE primary_outcome_met IS NOT NULL
          AND phase IS NOT NULL
          AND primary_completion_date IS NOT NULL
    """
    with get_connection() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    df["y"] = df["primary_outcome_met"].astype(int)
    df["primary_completion_date"] = pd.to_datetime(df["primary_completion_date"])
    return df


def _build_features(df: pd.DataFrame, train_mask: np.ndarray) -> tuple[pd.DataFrame, list[str]]:
    feat = pd.DataFrame(index=df.index)

    # Categoricals -> one-hot (fit over full frame so columns align; values are static).
    for col, prefix, keep in [
        ("phase", "ph", None),
        ("sponsor_class", "sc", None),
        ("indication_category", "ind", 12),
    ]:
        s = df[col].fillna("missing").astype(str)
        if keep is not None:
            top = s[train_mask].value_counts().nlargest(keep).index
            s = s.where(s.isin(top), "other")
        dummies = pd.get_dummies(s, prefix=prefix)
        feat = pd.concat([feat, dummies.astype(float)], axis=1)

    # Enrollment: log1p, impute train median, missing flag.
    enr = pd.to_numeric(df["enrollment"], errors="coerce")
    med = enr[train_mask].median()
    feat["enroll_missing"] = enr.isna().astype(float)
    feat["log_enroll"] = np.log1p(enr.fillna(med).clip(lower=0))

    # Era (completion year), centered.
    yr = df["primary_completion_date"].dt.year.astype(float)
    feat["year"] = yr - yr[train_mask].mean()

    # Sponsor prior-success rate, computed on TRAIN ONLY (no leakage), shrunk to global.
    tr = df[train_mask]
    global_rate = tr["y"].mean()
    grp = tr.groupby("sponsor_name")["y"].agg(["sum", "count"])
    rate = (grp["sum"] + SPONSOR_SHRINK * global_rate) / (grp["count"] + SPONSOR_SHRINK)
    feat["sponsor_rate"] = df["sponsor_name"].map(rate).fillna(global_rate).astype(float)

    return feat, list(feat.columns)


def _baseline_lookup(df: pd.DataFrame, train_mask: np.ndarray) -> np.ndarray:
    """Base-rate LOOKUP: train group means by phase x indication x sponsor_class."""
    tr = df[train_mask]
    g = (df["phase"].astype(str) + "|" + df["indication_category"].astype(str)
         + "|" + df["sponsor_class"].astype(str))
    tr_g = (tr["phase"].astype(str) + "|" + tr["indication_category"].astype(str)
            + "|" + tr["sponsor_class"].astype(str))
    means = tr.assign(_g=tr_g.values).groupby("_g")["y"].mean()
    glob = tr["y"].mean()
    return g.map(means).fillna(glob).to_numpy(dtype=float)


def train(*, store: bool = True, l2: float = 2.0) -> dict:
    df = _load().reset_index(drop=True)
    n = len(df)
    if n < 500:
        raise RuntimeError(f"too few labeled trials ({n}) to train")

    # Temporal split: newest TEST_FRACTION by completion date is the holdout.
    cutoff = df["primary_completion_date"].quantile(1 - TEST_FRACTION)
    train_mask = (df["primary_completion_date"] <= cutoff).to_numpy()
    test_mask = ~train_mask

    feat, cols = _build_features(df, train_mask)
    # Stack: give the regression the base-rate LOOKUP as a feature (train-derived
    # group means), so it can refine the robust lookup with sponsor track record etc.
    # This is the principled way to combine an interaction baseline with extra signals.
    feat["base_lookup"] = _baseline_lookup(df, train_mask)
    cols = list(feat.columns)
    X = feat.to_numpy(dtype=float)
    y = df["y"].to_numpy(dtype=float)

    Xtr, mean, std = logreg.standardize(X[train_mask])
    Xte, _, _ = logreg.standardize(X[test_mask], mean, std)
    ytr, yte = y[train_mask], y[test_mask]

    w = logreg.fit(Xtr, ytr, l2=l2)
    p_test = logreg.predict_proba(w, Xte)

    # Baselines on the same test rows.
    naive_p = np.full(test_mask.sum(), ytr.mean())
    lookup = _baseline_lookup(df, train_mask)[test_mask]

    res = {
        "n_total": n,
        "n_train": int(train_mask.sum()),
        "n_test": int(test_mask.sum()),
        "cutoff_date": str(cutoff.date()),
        "test_actual_rate": round(float(yte.mean()), 4),
        "model": {
            "brier": round(logreg.brier(yte, p_test), 6),
            "auc": round(logreg.auc(yte, p_test), 4),
        },
        "lookup_baseline": {
            "brier": round(logreg.brier(yte, lookup), 6),
            "auc": round(logreg.auc(yte, lookup), 4),
        },
        "naive_brier": round(logreg.brier(yte, naive_p), 6),
    }
    res["model"]["brier_skill"] = round(
        1 - res["model"]["brier"] / res["naive_brier"], 4)
    res["lookup_baseline"]["brier_skill"] = round(
        1 - res["lookup_baseline"]["brier"] / res["naive_brier"], 4)
    res["reliability"] = logreg.reliability(yte, p_test)

    # Coefficient inspection (standardized scale -> comparable importances).
    coefs = sorted(
        [{"feature": c, "weight": round(float(w[i + 1]), 4)} for i, c in enumerate(cols)],
        key=lambda d: abs(d["weight"]), reverse=True,
    )
    res["top_features"] = coefs[:15]

    _print(res, coefs)

    if store:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "features": cols,
            "weights": w.tolist(),
            "mean": mean.tolist(),
            "std": std.tolist(),
            "metrics": {"model": res["model"], "lookup": res["lookup_baseline"]},
            "trained_on": res["n_train"],
            "cutoff_date": res["cutoff_date"],
        }
        MODEL_PATH.write_text(json.dumps(artifact, indent=2))
        print(f"\nSaved model -> {MODEL_PATH}")

    log.info("train_complete", **{k: res[k] for k in ("n_train", "n_test")},
             model_auc=res["model"]["auc"], lookup_auc=res["lookup_baseline"]["auc"])
    return res


def _print(res: dict, coefs: list[dict]) -> None:
    print("\n=== Clinical Success Model — temporal holdout ===")
    print(f"Trials: {res['n_total']}  (train {res['n_train']} <= {res['cutoff_date']} "
          f"< test {res['n_test']})")
    print(f"Test actual success rate: {res['test_actual_rate']}")
    print(f"{'':22}{'Brier':>10}{'BrierSkill':>12}{'AUC':>8}")
    print(f"{'Naive (global mean)':22}{res['naive_brier']:>10.4f}{0.0:>12.4f}{0.5:>8.3f}")
    print(f"{'Base-rate LOOKUP':22}{res['lookup_baseline']['brier']:>10.4f}"
          f"{res['lookup_baseline']['brier_skill']:>12.4f}{res['lookup_baseline']['auc']:>8.3f}")
    print(f"{'Logistic REGRESSION':22}{res['model']['brier']:>10.4f}"
          f"{res['model']['brier_skill']:>12.4f}{res['model']['auc']:>8.3f}")
    lift = res["model"]["auc"] - res["lookup_baseline"]["auc"]
    verdict = ("REGRESSION BEATS lookup" if lift > 0.005
               else "no meaningful lift over lookup" if lift > -0.005
               else "lookup is better — keep base rates")
    print(f"AUC lift vs lookup: {lift:+.4f}  -> {verdict}")
    print("\nReliability (predicted -> observed):")
    for b in res["reliability"]:
        print(f"  {b['bucket']}  n={b['n']:<6} pred={b['pred']:.3f}  obs={b['obs']:.3f}")
    print("\nTop features (|standardized weight|):")
    for c in coefs[:12]:
        print(f"  {c['weight']:+.3f}  {c['feature']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--l2", type=float, default=2.0)
    args = ap.parse_args()
    train(store=not args.no_store, l2=args.l2)


if __name__ == "__main__":
    main()
