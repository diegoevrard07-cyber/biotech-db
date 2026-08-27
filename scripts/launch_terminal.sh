#!/usr/bin/env bash
# Launch the Streamlit Edge Terminal on Python 3.12+.
#
# macOS still ships /usr/bin/python3 as 3.9. The pinned deps (numpy 2.4.4, pandas
# 2.3.3, …) have no 3.9 wheels, so `python3 -m venv` on a Mac fails. This script
# never uses that interpreter: it prefers an already-installed 3.12+, and if
# none exists it installs a project-local copy via uv (no Homebrew required).
#
#   ./scripts/launch_terminal.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

py_ok() {
  local bin="$1"
  [[ -x "$bin" ]] || return 1
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null
}

find_python() {
  local c
  for c in \
    python3.14 python3.13 python3.12 \
    /opt/homebrew/opt/python@3.14/bin/python3.14 \
    /opt/homebrew/opt/python@3.13/bin/python3.13 \
    /opt/homebrew/opt/python@3.12/bin/python3.12 \
    /usr/local/opt/python@3.14/bin/python3.14 \
    /usr/local/opt/python@3.13/bin/python3.13 \
    /usr/local/opt/python@3.12/bin/python3.12 \
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
  do
    if command -v "$c" >/dev/null 2>&1; then
      c="$(command -v "$c")"
    fi
    if py_ok "$c"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  echo "No Python 3.12+ on PATH. Installing uv (downloads CPython 3.12 for this project)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1
}

PYTHON="$(find_python || true)"

if [[ -z "$PYTHON" ]]; then
  ensure_uv || {
    echo "Could not install uv. Install Python 3.12 from https://www.python.org/downloads/macos/ and re-run." >&2
    exit 1
  }
  echo "Installing Python 3.12 via uv…"
  uv python install 3.12
  PYTHON="$(uv python find 3.12)"
fi

echo "Using $($PYTHON -c 'import sys; print(sys.executable, sys.version.split()[0])')"

if [[ -x "$PROJ/.venv/bin/python" ]]; then
  if ! py_ok "$PROJ/.venv/bin/python"; then
    echo "Existing .venv is Python < 3.12 (typical macOS default). Recreating it."
    rm -rf "$PROJ/.venv"
  fi
fi

if [[ ! -x "$PROJ/.venv/bin/python" ]]; then
  "$PYTHON" -m venv "$PROJ/.venv"
fi

VENV_PY="$PROJ/.venv/bin/python"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r "$PROJ/requirements.txt"

if [[ ! -f "$PROJ/.env" ]]; then
  cp "$PROJ/.env.example" "$PROJ/.env"
  echo "Created .env from .env.example — set DATABASE_URL in it if you want live paper-book numbers."
fi

if ! grep -qE '^DATABASE_URL=postgresql://' "$PROJ/.env" 2>/dev/null; then
  echo "Warning: DATABASE_URL is not set in .env. The UI will start, but charts will be empty."
fi

exec "$VENV_PY" -m streamlit run "$PROJ/scripts/terminal.py"
