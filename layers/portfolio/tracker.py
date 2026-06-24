"""Pure portfolio math for the tracker: valuation, P&L, cash flows, exit timing.

No DB, no I/O — every function takes plain values so it is trivially testable and
the dashboard can reuse it. Sign convention: a LONG is a positive asset
(+shares*price); a SHORT is a negative liability (-shares*price). This makes
account equity a single clean sum: equity = cash + sum(signed market values).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

LONG = "long"
SHORT = "short"

# Plain-language exit instruction per trade type.
EXIT_RULES = {
    "buy_the_rumor": "SELL ~1 trading day BEFORE the catalyst (sell the rumor, never hold the print)",
    "hold_through": "EXIT shortly AFTER the readout (you held through it on purpose)",
    "fade": "COVER the short AFTER the print",
}


def planned_exit(trade_type: str | None, catalyst_date: date | None,
                 *, lead_days: int = 1) -> tuple[date | None, str]:
    """Return (exit_date, plain-language rule) given the trade type and catalyst date."""
    tt = (trade_type or "").lower()
    rule = EXIT_RULES.get(tt, "Review around the catalyst date.")
    if catalyst_date is None:
        return None, "No linked catalyst — set an exit date manually."
    if tt == "buy_the_rumor":
        return catalyst_date - timedelta(days=lead_days), rule
    if tt in ("hold_through", "fade"):
        return catalyst_date + timedelta(days=1), rule
    return catalyst_date, rule


def market_value(side: str, shares: float, price: float | None) -> float | None:
    """Signed market value: + for long (asset), - for short (liability)."""
    if price is None:
        return None
    sign = 1.0 if side == LONG else -1.0
    return sign * float(shares) * float(price)


def unrealized_pnl(side: str, shares: float, entry_price: float,
                   current_price: float | None) -> float | None:
    if current_price is None:
        return None
    sh, e, c = float(shares), float(entry_price), float(current_price)
    return sh * (c - e) if side == LONG else sh * (e - c)


def unrealized_pnl_pct(side: str, entry_price: float,
                       current_price: float | None) -> float | None:
    if current_price is None or not entry_price:
        return None
    e, c = float(entry_price), float(current_price)
    return (c - e) / e if side == LONG else (e - c) / e


def realized_pnl(side: str, shares: float, entry_price: float, exit_price: float) -> float:
    sh, e, x = float(shares), float(entry_price), float(exit_price)
    return sh * (x - e) if side == LONG else sh * (e - x)


def cash_delta_on_open(side: str, shares: float, price: float) -> float:
    """Buying a long spends cash (-); opening a short receives proceeds (+)."""
    amt = float(shares) * float(price)
    return -amt if side == LONG else amt


def cash_delta_on_close(side: str, shares: float, price: float) -> float:
    """Selling a long returns cash (+); covering a short spends cash (-)."""
    amt = float(shares) * float(price)
    return amt if side == LONG else -amt


def shares_from_dollars(dollars: float, price: float | None) -> float | None:
    if not price:
        return None
    return float(dollars) / float(price)


def size_from_weight(weight: float, equity: float,
                     price: float | None) -> dict[str, Any]:
    """Translate a target portfolio weight (signed fraction) into $ and shares."""
    dollars = float(weight) * float(equity)
    return {
        "dollars": round(dollars, 2),
        "shares": (round(abs(dollars) / float(price), 2) if price else None),
        "side": LONG if weight >= 0 else SHORT,
    }


def account_summary(holdings: list[dict], cash: float,
                    price_map: dict[str, float]) -> dict[str, Any]:
    """Aggregate open holdings + cash into account-level numbers.

    Each holding dict needs: ticker, side, shares, entry_price.
    price_map maps ticker -> current price (missing -> treated as 0 value).
    """
    gross_long = 0.0
    gross_short = 0.0
    upnl = 0.0
    invested = 0.0
    priced = 0
    for h in holdings:
        cur = price_map.get(h["ticker"])
        if cur is not None:
            priced += 1
        mv = market_value(h["side"], h["shares"], cur) or 0.0
        if h["side"] == LONG:
            gross_long += mv
        else:
            gross_short += -mv  # store as positive magnitude
        pnl = unrealized_pnl(h["side"], h["shares"], h["entry_price"], cur)
        upnl += pnl or 0.0
        invested += float(h["shares"]) * float(h["entry_price"])

    equity = cash + gross_long - gross_short
    return {
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "gross_long_usd": round(gross_long, 2),
        "gross_short_usd": round(gross_short, 2),
        "net_usd": round(gross_long - gross_short, 2),
        "invested_usd": round(invested, 2),
        "unrealized_pnl_usd": round(upnl, 2),
        "gross_long_pct": (gross_long / equity if equity else 0.0),
        "gross_short_pct": (gross_short / equity if equity else 0.0),
        "net_pct": ((gross_long - gross_short) / equity if equity else 0.0),
        "positions": len(holdings),
        "priced": priced,
    }


def exit_alerts(open_holdings: list[dict], today: date,
                *, soon_days: int = 7) -> list[dict]:
    """Pressing actions: positions whose planned exit is overdue or within soon_days.

    Each holding needs: ticker, side, trade_type, planned_exit_date (date|None),
    planned_exit_rule (str|None).
    """
    out = []
    for h in open_holdings:
        ped = h.get("planned_exit_date")
        if ped is None:
            continue
        days = (ped - today).days
        if days <= 0:
            level = "now"
        elif days <= soon_days:
            level = "soon"
        else:
            continue
        action = "SELL" if h.get("side") == LONG else "COVER"
        out.append({
            "ticker": h["ticker"],
            "action": action,
            "level": level,
            "days": days,
            "exit_date": ped,
            "reason": h.get("planned_exit_rule") or EXIT_RULES.get(
                (h.get("trade_type") or "").lower(), "Review around the catalyst date."),
        })
    out.sort(key=lambda a: a["days"])
    return out
