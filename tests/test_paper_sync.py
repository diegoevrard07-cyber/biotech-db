"""Tests for action-desk paper sync helpers."""

from datetime import date

from layers.portfolio import paper_sync as ps


def test_build_targets_long_only_skips_shorts():
    rows = [
        {
            "ticker": "AAA",
            "weight": 0.05,
            "trade_type": "buy_the_rumor",
            "expected_date": date(2026, 8, 1),
            "company_id": 1,
            "catalyst_id": 10,
        },
        {
            "ticker": "BBB",
            "weight": -0.03,
            "trade_type": "fade",
            "expected_date": date(2026, 9, 1),
            "company_id": 2,
            "catalyst_id": 20,
        },
        {
            "ticker": "CCC",
            "weight": 0.04,
            "trade_type": "fade",  # stale fade label
            "expected_date": date(2026, 9, 1),
            "company_id": 3,
            "catalyst_id": 30,
        },
    ]
    targets = ps.build_targets(rows, equity=10_000, prices={"AAA": 10.0, "BBB": 20.0, "CCC": 10.0})
    assert targets["AAA"]["side"] == "long"
    assert targets["AAA"]["target_shares"] == 50.0
    assert "BBB" not in targets  # negative weight refused
    assert "CCC" not in targets  # fade trade_type refused


def test_close_reasons():
    today = date(2026, 6, 24)
    h = {
        "ticker": "X",
        "side": "long",
        "trade_type": "buy_the_rumor",
        "planned_exit_date": date(2026, 6, 20),
    }
    assert ps.close_reason(h, {"side": "long", "trade_type": "buy_the_rumor"}, today) == "exit_due"

    h2 = {
        "ticker": "Y",
        "side": "long",
        "trade_type": "buy_the_rumor",
        "planned_exit_date": date(2026, 7, 1),
    }
    assert ps.close_reason(h2, None, today) == "not_in_book"
    assert ps.close_reason(h2, {"side": "short", "trade_type": "fade"}, today) == "side_flip"


def test_needs_resize():
    h = {"shares": 100}
    tgt = {"target_shares": 110}
    assert ps.needs_resize(h, tgt, 0.05) is True
    assert ps.needs_resize(h, {"target_shares": 105}, 0.10) is False
