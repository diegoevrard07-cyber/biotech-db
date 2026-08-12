"""Decision-layer tests: trade_type rules, Kelly sizing bounds, expected move."""

from __future__ import annotations

from datetime import date, timedelta

from layers.composite.scorer import (
    AVOID,
    BUY_THE_RUMOR,
    FADE,
    HOLD_THROUGH,
    ScoreInputs,
    compute_edge_score,
    date_is_reliable,
    decide_trade,
    expected_move,
    financing_tilt,
    kelly_weight,
    suggested_weight,
)


def test_expected_move_peaks_at_uncertainty():
    assert expected_move(0.5) == 0.6
    assert expected_move(0.0) == 0.2
    assert expected_move(1.0) == 0.2
    assert expected_move(None) == 0.6  # None -> 0.5 base


def test_kelly_weight_monotonic_and_floored():
    assert kelly_weight(0.5) == 0.0
    assert kelly_weight(0.4) == 0.0  # floored at 0
    assert kelly_weight(0.75) > kelly_weight(0.6) > 0


def test_financing_tilt_dilution_negative():
    assert financing_tilt(3, 5_000_000, False) <= -0.15
    assert financing_tilt(None, -1, False) == 0.05  # profitable
    assert financing_tilt(36, 5_000_000, False) == 0.0
    assert financing_tilt(36, 5_000_000, True) == -0.10


def test_decide_avoid_on_financing_stressed_hype():
    # Former fade setup — shorts retired → avoid.
    tt = decide_trade(proximity=0.85, base=0.4, fin_tilt=-0.15, run_up_30d=0.8, edge_gap=None)
    assert tt == AVOID


def test_decide_avoid_low_base_high_runup():
    tt = decide_trade(proximity=0.45, base=0.10, fin_tilt=0.0, run_up_30d=1.0, edge_gap=None)
    assert tt == AVOID


def test_decide_buy_the_rumor():
    tt = decide_trade(proximity=1.0, base=0.45, fin_tilt=0.0, run_up_30d=0.1, edge_gap=None)
    assert tt == BUY_THE_RUMOR


def test_decide_hold_through_high_base():
    tt = decide_trade(proximity=0.45, base=0.6, fin_tilt=0.0, run_up_30d=0.0, edge_gap=0.1)
    assert tt == HOLD_THROUGH


def test_decide_avoid_default():
    tt = decide_trade(proximity=0.25, base=0.3, fin_tilt=0.0, run_up_30d=0.0, edge_gap=None)
    assert tt == AVOID


def test_date_is_reliable():
    assert date_is_reliable(True, None, True) is True  # sec_confirmed overrides
    assert date_is_reliable(False, "high", False) is True
    assert date_is_reliable(False, "medium", False) is True
    assert date_is_reliable(False, "low", False) is False
    assert date_is_reliable(False, "high", True) is False  # manual-verification stub


def test_unreliable_date_blocks_buy_the_rumor():
    # Same setup that would be buy_the_rumor, but with an unreliable date.
    tt = decide_trade(
        proximity=1.0, base=0.45, fin_tilt=0.0, run_up_30d=0.1, edge_gap=None, date_reliable=False
    )
    assert tt != BUY_THE_RUMOR


def test_divergence_overpriced_is_avoid():
    # Market prices a much bigger move than the model on weak odds -> avoid (no short).
    tt = decide_trade(proximity=0.45, base=0.45, fin_tilt=0.0, run_up_30d=0.0, edge_gap=-0.15)
    assert tt == AVOID


def test_cheap_optionality_buys_underpriced_move():
    # Market under-prices the move on decent odds -> own the binary.
    tt = decide_trade(proximity=0.45, base=0.5, fin_tilt=0.0, run_up_30d=0.0, edge_gap=0.15)
    assert tt == HOLD_THROUGH


def test_suggested_weight_bounds():
    maxw = 0.05
    for base in (0.0, 0.3, 0.5, 0.7, 1.0):
        for tt in (BUY_THE_RUMOR, FADE, HOLD_THROUGH, AVOID):
            w = suggested_weight(tt, base=base, proximity=1.0, kelly_fraction=0.25, max_weight=maxw)
            assert -maxw <= w <= maxw
    assert (
        suggested_weight(AVOID, base=0.9, proximity=1.0, kelly_fraction=0.25, max_weight=maxw)
        == 0.0
    )
    assert (
        suggested_weight(FADE, base=0.1, proximity=0.5, kelly_fraction=0.25, max_weight=maxw) == 0.0
    )
    assert (
        suggested_weight(
            HOLD_THROUGH, base=0.9, proximity=0.5, kelly_fraction=0.25, max_weight=maxw
        )
        > 0
    )


def test_compute_edge_score_emits_decision_fields():
    res = compute_edge_score(
        ScoreInputs(
            catalyst_id=1,
            company_id=1,
            expected_date=date.today() + timedelta(days=20),
            base_rate=0.6,
            runway_months=24,
            quarterly_burn=3_000_000,
            implied_move=0.4,
            run_up_30d=0.1,
            net_insider_buy_usd=50_000,
        ),
        kelly_fraction=0.25,
        max_weight=0.05,
    )
    for key in (
        "trade_type",
        "expected_move",
        "implied_move",
        "edge_gap",
        "financing_tilt",
        "insider_tilt",
        "suggested_weight",
    ):
        assert key in res
    assert -0.05 <= res["suggested_weight"] <= 0.05
    assert res["edge_gap"] == round(res["expected_move"] - 0.4, 4)
