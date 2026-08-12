"""Basic database connectivity tests."""

import config
from db import get_engine


def test_database_url_set():
    assert config.DATABASE_URL, "DATABASE_URL must be set"


def test_engine_connects():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        assert result.scalar() == 1
