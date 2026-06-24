"""
Option-chain math: ATM implied move from a straddle.

Kept pure (operates on DataFrames / plain numbers) so it is unit-testable with
synthetic chains and never touches the network.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _mid(row: pd.Series) -> float | None:
    """Best available option price: bid/ask midpoint, else lastPrice."""
    bid = row.get("bid")
    ask = row.get("ask")
    try:
        bid_f = float(bid) if bid is not None else 0.0
        ask_f = float(ask) if ask is not None else 0.0
    except (TypeError, ValueError):
        bid_f = ask_f = 0.0
    if bid_f > 0 and ask_f > 0:
        return (bid_f + ask_f) / 2.0
    last = row.get("lastPrice")
    try:
        last_f = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        return None
    return last_f if last_f > 0 else None


def _nearest_atm(df: pd.DataFrame, spot: float) -> pd.Series | None:
    if df is None or df.empty or spot is None or spot <= 0:
        return None
    if "strike" not in df.columns:
        return None
    valid = df.dropna(subset=["strike"]).copy()
    if valid.empty:
        return None
    valid["_dist"] = (valid["strike"].astype(float) - spot).abs()
    return valid.sort_values("_dist").iloc[0]


def compute_implied_move(
    calls: pd.DataFrame | None,
    puts: pd.DataFrame | None,
    spot: float | None,
) -> dict[str, Any]:
    """Return {implied_move_pct, atm_iv, atm_strike} for the ATM straddle.

    implied_move_pct = (atm_call_mid + atm_put_mid) / spot  -> the market's
    expected absolute move by expiry. Any missing leg yields None for that field.
    """
    out: dict[str, Any] = {"implied_move_pct": None, "atm_iv": None, "atm_strike": None}
    if spot is None or spot <= 0:
        return out

    call = _nearest_atm(calls, spot) if calls is not None else None
    put = _nearest_atm(puts, spot) if puts is not None else None
    if call is None and put is None:
        return out

    strike = None
    if call is not None:
        strike = float(call["strike"])
    elif put is not None:
        strike = float(put["strike"])
    out["atm_strike"] = strike

    call_mid = _mid(call) if call is not None else None
    put_mid = _mid(put) if put is not None else None
    legs = [m for m in (call_mid, put_mid) if m is not None]
    if legs:
        straddle = sum(legs)
        # If only one leg priced, approximate straddle as 2x that leg.
        if len(legs) == 1:
            straddle *= 2.0
        out["implied_move_pct"] = round(straddle / spot, 6)

    ivs = []
    for leg in (call, put):
        if leg is None:
            continue
        iv = leg.get("impliedVolatility")
        try:
            iv_f = float(iv)
            if iv_f > 0 and not math.isnan(iv_f):
                ivs.append(iv_f)
        except (TypeError, ValueError):
            continue
    if ivs:
        out["atm_iv"] = round(sum(ivs) / len(ivs), 6)

    return out


def pick_expiry(expirations: list[str], target_date: str | None) -> str | None:
    """First expiry on/after target_date; else the last available expiry."""
    if not expirations:
        return None
    exps = sorted(expirations)
    if target_date:
        for e in exps:
            if e >= target_date:
                return e
    return exps[-1]
