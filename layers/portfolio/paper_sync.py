"""Pure helpers: reconcile open PAPER holdings against the capped action desk."""

from __future__ import annotations

from datetime import date
from typing import Any

from layers.portfolio import tracker as pf


def build_targets(
    book_rows: list[dict],
    equity: float,
    prices: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Map ticker -> target position sized from capped book weights."""
    if equity <= 0:
        return {}
    targets: dict[str, dict[str, Any]] = {}
    for r in book_rows:
        t = r["ticker"]
        px = prices.get(t)
        if not px:
            continue
        weight = float(r["weight"])
        if weight <= 0:
            continue  # never target a short/fade
        if r.get("trade_type") == "fade":
            continue
        sized = pf.size_from_weight(weight, equity, px)
        shares = sized["shares"]
        if not shares or shares <= 0:
            continue
        if sized["side"] != pf.LONG:
            continue  # defense-in-depth: refuse short targets
        ped, rule = pf.planned_exit(r["trade_type"], r["expected_date"])
        targets[t] = {
            "ticker": t,
            "company_id": r.get("company_id"),
            "catalyst_id": r.get("catalyst_id"),
            "trade_type": r["trade_type"],
            "side": sized["side"],
            "weight": weight,
            "target_shares": shares,
            "target_dollars": sized["dollars"],
            "price": px,
            "planned_exit_date": ped,
            "planned_exit_rule": rule,
            "expected_date": r["expected_date"],
        }
    return targets


def close_reason(holding: dict, target: dict | None, today: date) -> str | None:
    """Why a position should be closed, or None if it can stay."""
    ped = holding.get("planned_exit_date")
    if ped is not None and ped <= today:
        return "exit_due"
    if target is None:
        return "not_in_book"
    if holding.get("side") != target["side"]:
        return "side_flip"
    if holding.get("trade_type") != target.get("trade_type"):
        return "trade_change"
    return None


def needs_resize(holding: dict, target: dict, tolerance_pct: float) -> bool:
    """True when current shares diverge from target by more than tolerance_pct."""
    cur = float(holding["shares"])
    tgt = float(target["target_shares"])
    if tgt <= 0:
        return False
    return abs(cur - tgt) / tgt > tolerance_pct
