"""Shared pytest configuration.

Tests that need a live Postgres database are skipped automatically when
DATABASE_URL is not set (e.g. CI runners, fresh clones), so the pure-logic
suite stays green everywhere.
"""

from __future__ import annotations

import os

import pytest


def _db_available() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _db_available():
        return
    skip_no_db = pytest.mark.skip(reason="DATABASE_URL not set — skipping DB-backed test")
    for item in items:
        if _is_db_test(item):
            item.add_marker(skip_no_db)


def _is_db_test(item: pytest.Item) -> bool:
    path = str(item.fspath)
    # Test modules that open real database connections at call time.
    db_modules = ("test_db.py", "test_industry_priors.py")
    return any(path.endswith(name) for name in db_modules)
