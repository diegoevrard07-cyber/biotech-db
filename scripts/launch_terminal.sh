#!/usr/bin/env bash
# Launch the Streamlit Edge Terminal on Python 3.12+.
#
# macOS still ships /usr/bin/python3 as 3.9. The pinned deps have no 3.9 wheels.
# uv venvs also do not include pip, so this script installs with `uv pip` and
# never calls `python -m pip`.
#
#   ./scripts/launch_terminal.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

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
  echo "Created .env from .env.example — set DATABASE_URL in it if you want live paper-book numbers."
fi

if ! grep -qE '^DATABASE_URL=postgresql://' "$PROJ/.env" 2>/dev/null; then
  echo "Warning: DATABASE_URL is not set in .env. The UI will start, but charts will be empty."
fi

exec "$PROJ/.venv/bin/python" -m streamlit run "$PROJ/scripts/terminal.py"
