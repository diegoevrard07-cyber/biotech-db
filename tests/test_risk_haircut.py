"""Tests for the market-cap risk haircut (action_sheet.risk_haircut / apply_risk_haircut).

The haircut encodes the only validated finding from returns_regression.py: event
MAGNITUDE is predictable and small market cap is the dominant driver, so tiny-caps
get sized down. Invariant we care about most: the haircut can NEVER increase a
position, only shrink it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config
import action_sheet as a


def test_tiers_monotonic_nondecreasing_with_size():
    nano = a.risk_haircut(50_000_000)
    micro = a.risk_haircut(200_000_000)
    small = a.risk_haircut(500_000_000)
    big = a.risk_haircut(5_000_000_000)
    assert nano <= micro <= small <= big
    assert nano == 0.50
    assert big == 1.0


def test_unknown_marketcap_is_conservative():
    assert a.risk_haircut(None) == config.RISK_HAIRCUT_UNKNOWN
    assert a.risk_haircut(0) == config.RISK_HAIRCUT_UNKNOWN
    assert a.risk_haircut(-5) == config.RISK_HAIRCUT_UNKNOWN


def test_multiplier_always_in_unit_interval():
    for mc in [None, 0, 1, 1e6, 1e8, 3e8, 1e9, 1e12]:
        m = a.risk_haircut(mc)
        assert 0.0 < m <= 1.0


def test_apply_haircut_only_shrinks_and_records():
    rows = [
        {"ticker": "TINY", "weight": 0.20, "market_cap_usd": 50_000_000},   # nano
        {"ticker": "BIG", "weight": -0.10, "market_cap_usd": 5_000_000_000}, # large, short
        {"ticker": "UNK", "weight": 0.08, "market_cap_usd": None},
    ]
    a.apply_risk_haircut(rows)
    by = {r["ticker"]: r for r in rows}
    # never grows |weight|
    for r in rows:
        assert abs(r["weight"]) <= abs(r["raw_weight"]) + 1e-9
    # nano halved, large untouched, sign preserved on the short
    assert by["TINY"]["weight"] == round(0.20 * 0.50, 4)
    assert by["BIG"]["weight"] == round(-0.10 * 1.0, 4)
    assert by["BIG"]["weight"] < 0
    assert by["UNK"]["weight"] == round(0.08 * config.RISK_HAIRCUT_UNKNOWN, 4)
    assert by["TINY"]["risk_mult"] == 0.50
