"""Unit tests for config.normalize_database_url."""

from __future__ import annotations

import pytest

from config import normalize_database_url


def test_empty():
    assert normalize_database_url(None) == ""
    assert normalize_database_url("") == ""
    assert normalize_database_url("   ") == ""


def test_strips_quotes_and_prefix():
    raw = 'DATABASE_URL="postgresql://u:p@host:6543/postgres"'
    assert normalize_database_url(raw) == "postgresql://u:p@host:6543/postgres"


def test_strips_https_after_at():
    raw = "postgresql://u:p@https://host.example.com:6543/postgres"
    assert normalize_database_url(raw) == "postgresql://u:p@host.example.com:6543/postgres"


def test_rejects_https_dashboard_link():
    with pytest.raises(RuntimeError, match="web link"):
        normalize_database_url("https://xxxxx.supabase.co")


def test_rejects_non_postgres_scheme():
    with pytest.raises(RuntimeError, match="must start with postgresql"):
        normalize_database_url("mysql://u:p@host/db")


def test_accepts_postgres_scheme():
    assert normalize_database_url("postgres://u:p@host:5432/db") == "postgres://u:p@host:5432/db"


def test_supabase_forces_ssl_and_session_port():
    raw = "postgresql://u:p@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"
    out = normalize_database_url(raw)
    assert ":5432/" in out
    assert "sslmode=require" in out
    assert ":6543/" not in out


def test_supabase_keeps_existing_sslmode():
    raw = (
        "postgresql://u:p@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres" "?sslmode=require"
    )
    assert normalize_database_url(raw) == raw
