#!/bin/bash
# Run the kitchen screen photo copy every night at 3:07 AM (launchd, your user only).
# Run once, after the main ./install.sh and kitchen_sync.py --setup:
#   ~/photos-mcp/extras/kitchen_sync/install.sh
# Undo:  ~/photos-mcp/extras/kitchen_sync/install.sh --remove
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
LABEL="dev.cellcrusher.kitchen-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$HOME/.photos-mcp/venv/bin/python"
LOG="$HOME/Library/Logs/kitchen-sync.log"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$PLIST"
  echo "Nightly kitchen photo copy removed. Photos already on the family page stay until you delete them there."
  exit 0
fi
[ -x "$PY" ] || { echo "Run the main ./install.sh first ($PY is missing)." >&2; exit 1; }
[ -f "$HOME/.photos-mcp/kitchen-sync.json" ] || { echo "Run kitchen_sync.py --setup first." >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$HERE/kitchen_sync.py</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>7</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Set: a fresh random set of photos goes to the kitchen screen every night at 3:07 AM. Log: $LOG"
echo "Run it now to test:  launchctl kickstart gui/$(id -u)/$LABEL"
