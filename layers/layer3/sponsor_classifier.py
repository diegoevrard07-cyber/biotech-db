"""
Classify trial sponsors by type.

Limitation: historical trials use current sponsor classification as a proxy
for historical market-cap tier. Big-pharma list is curated and stable.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import config
from layers.layer1.seed_loader import load_company_seeds

BIG_PHARMA_PATH = config.SEEDS_DIR / "big_pharma.csv"

ACADEMIC_PATTERN = re.compile(
    r"\b(university|college|institute|hospital|clinic|medical center|medical centre|"
    r"nih|nci|national institutes|academic|foundation|council|ministry of health)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _load_big_pharma_names() -> set[str]:
    names: set[str] = set()
    if not BIG_PHARMA_PATH.exists():
        return names
    import pandas as pd

    df = pd.read_csv(BIG_PHARMA_PATH)
    for _, row in df.iterrows():
        names.add(str(row["name"]).strip().lower())
        aliases = str(row.get("aliases", "") or "")
        for alias in aliases.split("|"):
            alias = alias.strip().lower()
            if alias:
                names.add(alias)
    return names


@lru_cache(maxsize=1)
def _load_company_buckets() -> dict[str, str]:
    """Map normalized company name -> market_cap_bucket from seed CSV."""
    buckets: dict[str, str] = {}
    try:
        df = load_company_seeds()
    except Exception:
        return buckets
    for _, row in df.iterrows():
        name = str(row["name"]).strip().lower()
        bucket = str(row.get("market_cap_bucket", "") or "").lower()
        if name and bucket:
            buckets[name] = bucket
    return buckets


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def classify_sponsor(name: str, lookup_market_cap: bool = False) -> str:
    """Return sponsor_class: big_pharma, mid_cap, small_cap, academic, unknown."""
    if not name or not name.strip():
        return "unknown"

    norm = _normalize(name)
    if ACADEMIC_PATTERN.search(norm):
        return "academic"

    big_pharma = _load_big_pharma_names()
    for bp in big_pharma:
        if bp in norm or norm in bp:
            return "big_pharma"

    if lookup_market_cap:
        buckets = _load_company_buckets()
        for company_name, bucket in buckets.items():
            if company_name in norm or norm in company_name:
                if bucket == "mid":
                    return "mid_cap"
                if bucket in ("small", "micro"):
                    return "small_cap"

    return "unknown"
