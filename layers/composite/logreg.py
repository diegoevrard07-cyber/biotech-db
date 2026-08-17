"""Pure-numpy regularized logistic regression + ranking metrics.

Deliberately dependency-light: numpy ships with pandas (already in the stack), so
we avoid pulling scikit-learn/statsmodels. This is a small, transparent L2-penalized
logistic regression fit by gradient descent on standardized features. Good enough for
a few thousand rows and a few dozen features, and easy to audit (the *Noise* lesson:
prefer a simple, inspectable model over a kitchen-sink black box).
"""

from __future__ import annotations

import numpy as np


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Clip to avoid overflow warnings on extreme logits.
    z = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


def standardize(
    X: np.ndarray,
    mean: np.ndarray | None = None,
    std: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z-score columns. Fit stats on train, reuse them on test (pass mean/std)."""
    if mean is None:
        mean = X.mean(axis=0)
    if std is None:
        std = X.std(axis=0)
        std = np.where(std < 1e-12, 1.0, std)
    return (X - mean) / std, mean, std


def fit(
    X: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = 1.0,
    lr: float = 0.2,
    n_iter: int = 5000,
) -> np.ndarray:
    """Return weight vector w (length d+1; w[0] is the bias). X must be standardized."""
    n, d = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(d + 1)
    for _ in range(n_iter):
        p = _sigmoid(Xb @ w)
        grad = Xb.T @ (p - y) / n
        reg = (l2 / n) * w
        reg[0] = 0.0  # don't penalize the bias
        w -= lr * (grad + reg)
    return w


def predict_proba(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    """X must be standardized with the SAME mean/std used at fit time."""
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])
    return _sigmoid(Xb @ w)


def brier(y: np.ndarray, p: np.ndarray) -> float:
    """Mean squared error of predicted probabilities vs realized binary outcomes."""
    return float(np.mean((p - y) ** 2))


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank-based ROC AUC (Mann-Whitney). Returns 0.5 if degenerate."""
    y = np.asarray(y)
    pos = p[y == 1]
    neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1)
    # Average ranks for ties.
    _, inv, counts = np.unique(p, return_inverse=True, return_counts=True)
    cum = np.cumsum(counts)
    start = cum - counts
    avg_rank_by_group = (start + cum + 1) / 2.0
    ranks = avg_rank_by_group[inv]
    sum_pos = ranks[y == 1].sum()
    n_pos, n_neg = len(pos), len(neg)
    return float((sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def reliability(y: np.ndarray, p: np.ndarray, *, n_buckets: int = 10) -> list[dict]:
    """Calibration table: mean predicted probability vs observed hit rate per bucket."""
    out = []
    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    for i in range(n_buckets):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi) if i < n_buckets - 1 else (p >= lo) & (p <= hi)
        if mask.sum() == 0:
            continue
        out.append({
            "bucket": f"{lo:.2f}-{hi:.2f}",
            "n": int(mask.sum()),
            "pred": round(float(p[mask].mean()), 3),
            "obs": round(float(y[mask].mean()), 3),
        })
    return out
