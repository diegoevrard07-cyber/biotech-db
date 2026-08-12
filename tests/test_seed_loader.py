"""Tests for company seed CSV loader."""

from pathlib import Path

from layers.layer1.seed_loader import SEED_PATH, load_company_seeds


def test_load_company_seeds_skips_comment_lines():
    df = load_company_seeds()
    assert len(df) > 0
    assert "ticker" in df.columns
    assert "ctgov_sponsor_aliases" in df.columns
    # Comment line must not become a data row
    assert not any(str(v).startswith("# Universe") for v in df["ticker"].astype(str))


def test_seed_file_exists():
    assert SEED_PATH.exists()
