#!/usr/bin/env bash
# Launch the Streamlit Edge Terminal on Python 3.12+ at http://localhost:8321
#
# Never uses macOS /usr/bin/python3 (still 3.9). Never waits on Docker Desktop.
# If Docker is already running, uses docker compose. Otherwise starts an
# embedded Postgres via pgserver (pip-installable binaries).
#
#   ./scripts/launch_terminal.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PORT="${STREAMLIT_PORT:-8321}"

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

# If .env already points at a real database (e.g. Supabase), use it as-is and do
# not start a local one. Only placeholder / localhost URLs fall through.
existing_url="$(grep -E '^DATABASE_URL=' "$PROJ/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
case "$existing_url" in
  ""|*aws-0-REGION*|*PROJECT_REF*|*localhost*|*127.0.0.1*)
    : ;;  # placeholder or local: set up a local database below
  postgresql://*|postgres://*)
    echo "Using DATABASE_URL from .env"
    echo "Opening http://localhost:${PORT}"
    exec "$PROJ/.venv/bin/python" -m streamlit run "$PROJ/scripts/terminal.py" \
      --server.port "$PORT" \
      --server.headless false
    ;;
esac

if docker info >/dev/null 2>&1; then
  echo "Docker is running, using docker compose Postgres."
  if [[ ! -f "$PROJ/.env" ]]; then
    cp "$PROJ/.env.example" "$PROJ/.env"
  fi
  "$PROJ/.venv/bin/python" - <<'PY'
from pathlib import Path
p = Path(".env")
text = p.read_text(encoding="utf-8") if p.exists() else ""
local = "postgresql://postgres:postgres@localhost:5432/biotech"
out, found = [], False
for line in text.splitlines():
    if line.startswith("DATABASE_URL="):
        out.append("DATABASE_URL=" + local)
        found = True
    else:
        out.append(line)
if not found:
    out.append("DATABASE_URL=" + local)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  docker compose up -d
  for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U postgres -d biotech >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  "$PROJ/.venv/bin/python" "$PROJ/scripts/apply_schema.py"
  echo "Opening http://localhost:${PORT}"
  exec "$PROJ/.venv/bin/python" -m streamlit run "$PROJ/scripts/terminal.py" \
    --server.port "$PORT" \
    --server.headless false
fi

echo "No Docker daemon, using embedded Postgres (pgserver)."
uv pip install pgserver
export STREAMLIT_PORT="$PORT"
exec "$PROJ/.venv/bin/python" "$PROJ/scripts/run_local_terminal.py"
