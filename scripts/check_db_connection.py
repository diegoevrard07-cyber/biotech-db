"""Smoke-test the DATABASE_URL connection and print the Postgres version.

Not part of the pipeline — a setup diagnostic to confirm credentials and
network reachability before running ingestion. Exits non-zero on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def main() -> int:
    """Connect via DATABASE_URL and report the server version. Returns exit code."""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    # Re-read after load_dotenv so a just-edited .env is picked up.
    try:
        db_url = config.normalize_database_url(__import__("os").getenv("DATABASE_URL", ""))
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    if not db_url:
        print("FAIL: DATABASE_URL not found in .env")
        print("Expected one line like:")
        print(
            "  DATABASE_URL=postgresql://postgres.REF:PASSWORD@"
            "aws-0-REGION.pooler.supabase.com:6543/postgres"
        )
        return 1

    # Never print credentials — show only the host part after '@'.
    host = db_url.split("@", 1)[1] if "@" in db_url else "<unparseable>"
    print(f"Connecting to: {host}")

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT version();")
        version = cur.fetchone()[0]
        print("SUCCESS")
        print(f"Postgres version: {version}")
        cur.close()
        conn.close()
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        err = str(exc).lower()
        if "client encoding" in err or "ssl" in err:
            print(
                "HINT: for Supabase + this app use Session pooler port 5432 and sslmode=require, e.g.\n"
                "  ...@aws-1-REGION.pooler.supabase.com:5432/postgres?sslmode=require\n"
                "(not transaction pooler port 6543)."
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
