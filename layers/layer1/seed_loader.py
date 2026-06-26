"""Single source of truth for reading company seed CSV."""

from __future__ import annotations

import pandas as pd

import config

SEED_PATH = config.SEEDS_DIR / "companies.csv"


def load_company_seeds() -> pd.DataFrame:
    """Single source of truth for reading companies.csv."""
    return pd.read_csv(SEED_PATH, comment="#")
