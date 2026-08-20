"""Unit tests for layers/portfolio/projection.py (pure numpy, no DB)."""

from __future__ import annotations

import numpy as np
import pytest

from layers.portfolio import projection as pj


class TestBootstrapPaths:
    def test_shape_and_start(self):
        paths = pj.bootstrap_equity_paths(
            [0.01, -0.01, 0.02, 0.0, -0.02], 1000.0, days=10, n_paths=50
        )
        assert paths.shape == (50, 11)
        assert np.allclose(paths[:, 0], 1000.0)

    def test_deterministic_with_seed(self):
        a = pj.bootstrap_equity_paths(
            [0.01, -0.01, 0.02, 0.0, -0.02], 1000.0, days=5, n_paths=10, seed=3
        )
        b = pj.bootstrap_equity_paths(
            [0.01, -0.01, 0.02, 0.0, -0.02], 1000.0, days=5, n_paths=10, seed=3
        )
        assert np.array_equal(a, b)

    def test_needs_five_returns(self):
        with pytest.raises(ValueError):
            pj.bootstrap_equity_paths([0.01, 0.02], 1000.0)

    def test_all_positive_returns_grow(self):
        paths = pj.bootstrap_equity_paths(
            [0.01, 0.02, 0.015, 0.01, 0.03], 100.0, days=20, n_paths=20
        )
        assert (paths[:, -1] > 100.0).all()


class TestQuantilesAndSummary:
    def setup_method(self):
        self.paths = pj.bootstrap_equity_paths(
            [0.02, -0.02, 0.01, -0.01, 0.0, 0.03, -0.03], 1000.0, days=30, n_paths=500
        )

    def test_quantiles_ordered(self):
        qs = pj.path_quantiles(self.paths)
        assert (qs[5] <= qs[50]).all() and (qs[50] <= qs[95]).all()
        assert len(qs[50]) == self.paths.shape[1]

    def test_summary_fields(self):
        s = pj.projection_summary(self.paths)
        assert s["p05"] <= s["p50"] <= s["p95"]
        assert 0.0 <= s["prob_loss"] <= 1.0
        assert 0.0 <= s["prob_down_10"] <= s["prob_loss"] + 1e-9


class TestScenarios:
    def test_beta_math(self):
        rows = pj.scenario_impacts(beta=0.8, gross_exposure=0.75, equity=10000.0, shocks=(-0.10,))
        assert rows[0]["book_pct"] == pytest.approx(-0.06)
        assert rows[0]["book_usd"] == pytest.approx(-600.0)

    def test_zero_beta_is_flat(self):
        rows = pj.scenario_impacts(beta=0.0, gross_exposure=1.0, equity=5000.0)
        assert all(r["book_pct"] == 0.0 for r in rows)


class TestKelly:
    def test_known_values(self):
        assert pj.kelly_fraction(0.5, 1.0) == pytest.approx(0.0)
        assert pj.kelly_fraction(0.6, 1.0) == pytest.approx(0.2)
        assert pj.kelly_fraction(0.75, 2.0) == pytest.approx(0.625)

    def test_clipped_at_zero(self):
        assert pj.kelly_fraction(0.3, 1.0) == 0.0
        assert pj.kelly_fraction(0.9, 0.0) == 0.0

    def test_surface_shape_and_monotonicity(self):
        p, b, s = pj.kelly_surface()
        assert s.shape == (len(b), len(p))
        # more win probability never lowers kelly
        assert (np.diff(s, axis=1) >= -1e-12).all()
        assert (s >= 0).all()
