"""SQLAlchemy database engine and connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from tenacity import retry, stop_after_attempt, wait_exponential

import config

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return a singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL not set in environment")
        _engine = create_engine(
            config.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Context manager yielding a connection; commits on success, rolls back on error."""
    engine = get_engine()
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def execute_with_retry(conn: Connection, sql: str, params: dict | None = None):
    """Execute SQL with retry for transient connection failures."""
    return conn.execute(text(sql), params or {})
