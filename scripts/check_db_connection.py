"""Smoke-test the DATABASE_URL connection and print the Postgres version.

Not part of the pipeline — a setup diagnostic to confirm credentials and
network reachability before running ingestion. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys

import psycopg2
from dotenv import load_dotenv


def main() -> int:
    """Connect via DATABASE_URL and report the server version. Returns exit code."""
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("FAIL: DATABASE_URL not found in .env")
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
        return 1


if __name__ == "__main__":
    sys.exit(main())
