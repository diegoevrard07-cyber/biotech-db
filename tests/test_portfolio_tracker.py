"""Tests for the pure portfolio math — focus on the sign conventions that bite."""

from datetime import date

from layers.portfolio import tracker as t


def test_long_pnl_and_value():
    assert t.market_value("long", 100, 12.0) == 1200.0
    assert t.unrealized_pnl("long", 100, 10.0, 12.0) == 200.0
    assert t.unrealized_pnl_pct("long", 10.0, 12.0) == 0.2
    assert t.realized_pnl("long", 100, 10.0, 9.0) == -100.0


def test_short_pnl_and_value():
    # Short is a negative liability; profit when price falls.
    assert t.market_value("short", 100, 12.0) == -1200.0
    assert t.unrealized_pnl("short", 100, 10.0, 8.0) == 200.0
    assert t.unrealized_pnl_pct("short", 10.0, 8.0) == 0.2
    assert t.realized_pnl("short", 100, 10.0, 12.0) == -200.0


def test_cash_flows():
    # Buying a long spends cash; selling returns it.
    assert t.cash_delta_on_open("long", 100, 10.0) == -1000.0
    assert t.cash_delta_on_close("long", 100, 11.0) == 1100.0
    # Shorting receives proceeds; covering spends.
    assert t.cash_delta_on_open("short", 100, 10.0) == 1000.0
    assert t.cash_delta_on_close("short", 100, 8.0) == -800.0


def test_equity_invariant_long():
    # Start 10k cash, buy 100@10 -> cash 9000, holding 100@12 -> equity 10200 = 10k + 200 pnl.
    cash = 10000 + t.cash_delta_on_open("long", 100, 10.0)
    summ = t.account_summary(
        [{"ticker": "ABC", "side": "long", "shares": 100, "entry_price": 10.0}],
        cash, {"ABC": 12.0})
    assert summ["equity"] == 10200.0
    assert summ["unrealized_pnl_usd"] == 200.0
    assert summ["gross_long_usd"] == 1200.0
    assert summ["gross_short_usd"] == 0.0


def test_equity_invariant_short():
    # Start 10k, short 100@10 -> cash 11000, cover value 100@8 -> equity 10200 = 10k + 200 pnl.
    cash = 10000 + t.cash_delta_on_open("short", 100, 10.0)
    summ = t.account_summary(
        [{"ticker": "XYZ", "side": "short", "shares": 100, "entry_price": 10.0}],
        cash, {"XYZ": 8.0})
    assert summ["equity"] == 10200.0
    assert summ["unrealized_pnl_usd"] == 200.0
    assert summ["gross_short_usd"] == 800.0


def test_planned_exit_rules():
    cat = date(2026, 8, 30)
    d, rule = t.planned_exit("buy_the_rumor", cat)
    assert d == date(2026, 8, 29) and "SELL" in rule
    d, _ = t.planned_exit("hold_through", cat)
    assert d == date(2026, 8, 31)
    d, _ = t.planned_exit("fade", cat)
    assert d == date(2026, 8, 31)
    d, rule = t.planned_exit("buy_the_rumor", None)
    assert d is None and "manually" in rule.lower()


def test_exit_alerts_levels():
    today = date(2026, 6, 21)
    holdings = [
        {"ticker": "A", "side": "long", "trade_type": "buy_the_rumor",
         "planned_exit_date": date(2026, 6, 20), "planned_exit_rule": "SELL now"},   # overdue
        {"ticker": "B", "side": "short", "trade_type": "fade",
         "planned_exit_date": date(2026, 6, 25), "planned_exit_rule": "COVER"},       # soon
        {"ticker": "C", "side": "long", "trade_type": "hold_through",
         "planned_exit_date": date(2026, 9, 1), "planned_exit_rule": "later"},        # not yet
    ]
    alerts = t.exit_alerts(holdings, today, soon_days=7)
    assert [a["ticker"] for a in alerts] == ["A", "B"]
    assert alerts[0]["level"] == "now" and alerts[0]["action"] == "SELL"
    assert alerts[1]["level"] == "soon" and alerts[1]["action"] == "COVER"


def test_size_from_weight():
    s = t.size_from_weight(0.05, 100000, 25.0)
    assert s["dollars"] == 5000.0 and s["shares"] == 200.0 and s["side"] == "long"
    s = t.size_from_weight(-0.03, 100000, 10.0)
    assert s["dollars"] == -3000.0 and s["shares"] == 300.0 and s["side"] == "short"
