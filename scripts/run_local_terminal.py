#!/usr/bin/env python3
"""Start a local Postgres (no Docker) and open the Streamlit terminal on port 8321.

Uses the pip-installable ``pgserver`` binaries so a Mac without Docker Desktop
can still run the UI. The server is kept alive for as long as Streamlit runs.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

PORT = os.getenv("STREAMLIT_PORT", "8321")
PGDATA = PROJ / "data" / "pgdata"


def _write_database_url(uri: str) -> None:
    env_path = PROJ / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("DATABASE_URL="):
            out.append("DATABASE_URL=" + uri)
            found = True
        else:
            out.append(line)
    if not found:
        out.append("DATABASE_URL=" + uri)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _import_pgserver():
    try:
        return importlib.import_module("pgserver")
    except ImportError:
        print("Installing pgserver (embedded Postgres, no Docker)…")
        uv = shutil.which("uv")
        if uv:
            subprocess.check_call([uv, "pip", "install", "pgserver"])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pgserver"])
        return importlib.import_module("pgserver")


def main() -> int:
    pgserver = _import_pgserver()
    PGDATA.mkdir(parents=True, exist_ok=True)
    print(f"Starting local Postgres in {PGDATA}…")
    server = pgserver.get_server(str(PGDATA))
    uri = server.get_uri()
    _write_database_url(uri)
    os.environ["DATABASE_URL"] = uri
    print("DATABASE_URL -> embedded Postgres (no Docker)")

    print("Applying schema…")
    subprocess.check_call([sys.executable, str(PROJ / "scripts" / "apply_schema.py")])

    print(f"Opening http://localhost:{PORT}")
    try:
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(PROJ / "scripts" / "terminal.py"),
                "--server.port",
                PORT,
                "--server.headless",
                "false",
            ]
        )
    finally:
        # Hold the handle until Streamlit exits so the server is not GC'd.
        del server


if __name__ == "__main__":
    raise SystemExit(main())
