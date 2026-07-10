#!/usr/bin/env bash
# Keep Streamlit terminal running (used by launchd KeepAlive agent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

LOGDIR="$PROJ/data/logs"
mkdir -p "$LOGDIR"
PORT="${STREAMLIT_PORT:-8520}"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$PROJ/.venv/bin/python" ]]; then
    PYTHON="$PROJ/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi

exec "$PYTHON" -m streamlit run "$PROJ/scripts/terminal.py" \
  --server.port "$PORT" \
  --server.headless true \
  >>"$LOGDIR/streamlit.out" 2>>"$LOGDIR/streamlit.err"
