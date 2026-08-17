"""Tests for long-only mode in the capped book (action_sheet.size_book).

When config.LONG_ONLY is on, shorts are dropped upstream in compute_book, and
size_book must not throttle a pure-long book below the gross-long cap (so freed
short capital can be redeployed into longs).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import action_sheet as a

import config


def _rows():
    return [
        {"ticker": "AAA", "name": "A", "company_id": 1, "catalyst_id": 1,
         "is_gbm": False, "sector": "onc", "market_cap_usd": 2e9,
         "trade_type": "hold_through", "catalyst_type": "phase_readout",
         "expected_date": None, "weight": 0.40, "base_rate": 0.6,
         "edge_gap": 0.1, "confidence": 0.8},
        {"ticker": "BBB", "name": "B", "company_id": 2, "catalyst_id": 2,
         "is_gbm": False, "sector": "cns", "market_cap_usd": 2e9,
         "trade_type": "hold_through", "catalyst_type": "phase_readout",
         "expected_date": None, "weight": 0.40, "base_rate": 0.6,
         "edge_gap": 0.1, "confidence": 0.8},
    ]


def test_long_only_skips_net_throttle(monkeypatch):
    monkeypatch.setattr(config, "LONG_ONLY", True)
    rows, summary = a.size_book(_rows())
    # Two 0.40 longs = 0.80 gross long. Net cap is 0.60, but long-only must skip it,
    # so the book stays at 0.80 (bounded by MAX_GROSS_LONG, not MAX_NET).
    assert summary["gross_short"] == 0
    assert round(summary["gross_long"], 2) == 0.80
    assert round(summary["net"], 2) == 0.80


def test_longshort_still_enforces_net(monkeypatch):
    monkeypatch.setattr(config, "LONG_ONLY", False)
    rows, summary = a.size_book(_rows())
    # No shorts here, so with the net cap active the long book is throttled to 0.60.
    assert round(summary["net"], 2) <= config.MAX_NET + 1e-6
