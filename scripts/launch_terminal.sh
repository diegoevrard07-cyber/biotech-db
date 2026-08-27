#!/usr/bin/env bash
# Launch the Streamlit Edge Terminal on Python 3.12+ at http://localhost:8321
#
# macOS still ships /usr/bin/python3 as 3.9. uv venvs do not include pip, so
# packages are installed with `uv pip`. If DATABASE_URL is missing or still the
# .env.example placeholder, this starts local Docker Postgres instead of trying
# to resolve aws-0-REGION.
#
#   ./scripts/launch_terminal.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PORT="${STREAMLIT_PORT:-8321}"
LOCAL_URL="postgresql://postgres:postgres@localhost:5432/biotech"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (used to get Python 3.12 and install packages)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Could not install uv. Install Python 3.12 from https://www.python.org/downloads/macos/ and re-run." >&2
  exit 1
fi

echo "Installing Python 3.12 if needed…"
uv python install 3.12

if [[ -x "$PROJ/.venv/bin/python" ]]; then
  if ! "$PROJ/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "Existing .venv is Python < 3.12. Recreating it."
    rm -rf "$PROJ/.venv"
  fi
fi

if [[ ! -x "$PROJ/.venv/bin/python" ]]; then
  uv venv --python 3.12
fi

echo "Installing requirements into .venv…"
uv pip install -r "$PROJ/requirements.txt"

if [[ ! -f "$PROJ/.env" ]]; then
  cp "$PROJ/.env.example" "$PROJ/.env"
  echo "Created .env from .env.example"
fi

# Replace the copied placeholder so libpq never tries to resolve aws-0-REGION.
"$PROJ/.venv/bin/python" - <<'PY'
from pathlib import Path
p = Path(".env")
text = p.read_text(encoding="utf-8")
placeholder = (
    "aws-0-REGION.pooler.supabase.com" in text
    or "postgres.PROJECT_REF:" in text
)
if not placeholder:
    raise SystemExit(0)
local = "postgresql://postgres:postgres@localhost:5432/biotech"
out = []
found = False
for line in text.splitlines():
    if line.startswith("DATABASE_URL="):
        out.append("DATABASE_URL=" + local)
        found = True
    else:
        out.append(line)
if not found:
    out.append("DATABASE_URL=" + local)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("Replaced placeholder DATABASE_URL with local Docker Postgres.")
PY

db_url="$(grep -E '^DATABASE_URL=' "$PROJ/.env" | tail -1 | cut -d= -f2- || true)"
if [[ "$db_url" == *localhost* ]] || [[ "$db_url" == *127.0.0.1* ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "DATABASE_URL points at localhost but docker is not installed." >&2
    echo "Install Docker Desktop, or paste a real Supabase URI into .env." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
      echo "Starting Docker Desktop…"
      open -a Docker
      for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 2
      done
    fi
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running. Start Docker Desktop and re-run." >&2
    exit 1
  fi
  echo "Starting local Postgres (docker compose)…"
  docker compose up -d
  echo "Waiting for Postgres…"
  for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U postgres -d biotech >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  echo "Applying schema…"
  "$PROJ/.venv/bin/python" "$PROJ/scripts/apply_schema.py"
fi

echo "Opening http://localhost:${PORT}"
exec "$PROJ/.venv/bin/python" -m streamlit run "$PROJ/scripts/terminal.py" \
  --server.port "$PORT" \
  --server.headless false
