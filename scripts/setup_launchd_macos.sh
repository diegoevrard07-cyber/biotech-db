#!/usr/bin/env bash
# Install macOS LaunchAgents for unattended paper trading.
#
# launchd runs missed calendar jobs when the Mac wakes (queued catch-up).
#
#   ./scripts/setup_launchd_macos.sh              # install
#   ./scripts/setup_launchd_macos.sh --dry-run    # preview
#   ./scripts/setup_launchd_macos.sh --remove     # unload + delete
#
# Defaults (override via env):
#   SCHED_TZ=America/New_York   (informational; uses system local time)
#   REFRESH_TIME=18:00          daily data refresh
#   AUTOPILOT_TIME=23:00        weekday paper sync
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"

SCHED_TZ="${SCHED_TZ:-America/New_York}"
REFRESH_TIME="${REFRESH_TIME:-18:00}"
AUTOPILOT_TIME="${AUTOPILOT_TIME:-23:00}"

REFRESH_H="${REFRESH_TIME%%:*}"
REFRESH_M="${REFRESH_TIME##*:}"
AUTO_H="${AUTOPILOT_TIME%%:*}"
AUTO_M="${AUTOPILOT_TIME##*:}"

LABEL_REFRESH="com.biotech-db.daily-refresh"
LABEL_AUTOPILOT="com.biotech-db.paper-autopilot"
AGENTS_DIR="$HOME/Library/LaunchAgents"

DRY_RUN=0
REMOVE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --remove) REMOVE=1 ;;
  esac
done

chmod +x "$PROJ/scripts/run_daily_refresh.sh" "$PROJ/scripts/run_paper_autopilot.sh"

if [[ ! -f "$PROJ/.env" ]]; then
  echo "ERROR: $PROJ/.env not found. Copy .env.example and set DATABASE_URL first." >&2
  exit 1
fi

_unload() {
  local label="$1"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || \
    launchctl unload "$AGENTS_DIR/${label}.plist" 2>/dev/null || true
}

if [[ "$REMOVE" -eq 1 ]]; then
  for label in "$LABEL_REFRESH" "$LABEL_AUTOPILOT"; do
    _unload "$label"
    rm -f "$AGENTS_DIR/${label}.plist"
  done
  echo "Removed launchd agents: $LABEL_REFRESH, $LABEL_AUTOPILOT"
  exit 0
fi

_write_refresh_plist() {
  cat >"$AGENTS_DIR/${LABEL_REFRESH}.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL_REFRESH}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PROJ}/scripts/run_daily_refresh.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJ}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${REFRESH_H}</integer>
    <key>Minute</key>
    <integer>${REFRESH_M}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${PROJ}/data/logs/launchd_refresh.out</string>
  <key>StandardErrorPath</key>
  <string>${PROJ}/data/logs/launchd_refresh.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF
}

_write_autopilot_plist() {
  cat >"$AGENTS_DIR/${LABEL_AUTOPILOT}.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL_AUTOPILOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PROJ}/scripts/run_paper_autopilot.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJ}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>${AUTO_H}</integer><key>Minute</key><integer>${AUTO_M}</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>${AUTO_H}</integer><key>Minute</key><integer>${AUTO_M}</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>${AUTO_H}</integer><key>Minute</key><integer>${AUTO_M}</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>${AUTO_H}</integer><key>Minute</key><integer>${AUTO_M}</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>${AUTO_H}</integer><key>Minute</key><integer>${AUTO_M}</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>${PROJ}/data/logs/launchd_autopilot.out</string>
  <key>StandardErrorPath</key>
  <string>${PROJ}/data/logs/launchd_autopilot.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF
}

echo "Project:  $PROJ"
echo "Platform: macOS launchd (missed jobs run on wake)"
echo "Timezone: system local (set Mac to $SCHED_TZ for ET scheduling)"
echo "Refresh:  daily at $REFRESH_TIME"
echo "Autopilot: weekdays at $AUTOPILOT_TIME"
echo ""

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Would install:"
  echo "  $AGENTS_DIR/${LABEL_REFRESH}.plist"
  echo "  $AGENTS_DIR/${LABEL_AUTOPILOT}.plist"
  exit 0
fi

mkdir -p "$AGENTS_DIR" "$PROJ/data/logs"
_write_refresh_plist
_write_autopilot_plist

for label in "$LABEL_REFRESH" "$LABEL_AUTOPILOT"; do
  _unload "$label"
  launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/${label}.plist"
done

echo "LaunchAgents installed."
echo ""
echo "Verify:"
echo "  launchctl list | grep biotech-db"
echo "  ls -la $AGENTS_DIR/com.biotech-db.*.plist"
echo ""
echo "Manual test:"
echo "  $PROJ/scripts/run_daily_refresh.sh"
echo "  $PROJ/scripts/run_paper_autopilot.sh"
