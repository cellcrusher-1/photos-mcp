#!/bin/bash
# Install (or update) the photos connector on this Mac, for the Claude desktop app
# and Claude Code. Run from the repo folder:  ./install.sh
#
# What it does:
#   1. Installs a Python 3.12.13 used ONLY by this connector, in ~/.photos-mcp/python
#      (so Full Disk Access granted to it covers nothing else).
#   2. Makes ~/.photos-mcp/venv with the exact versions in pyproject.toml.
#   3. Points the Claude desktop app and Claude Code (all projects) at it, and removes
#      the old third-party "osxphotos" connector.
#   4. Prints the one program to add to Full Disk Access and opens that settings page.
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
BASE="$HOME/.photos-mcp"
PYVER="3.12.13"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || { echo "uv is not installed. See README step 1." >&2; exit 1; }

echo "→ Dedicated Python $PYVER in $BASE/python"
export UV_PYTHON_INSTALL_DIR="$BASE/python"
uv python install "$PYVER" --quiet
PY="$(uv python find "$PYVER" --managed-python)"
REALPY="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$PY" 2>/dev/null || readlink -f "$PY")"
case "$REALPY" in "$BASE"/*) ;; *) echo "Expected Python under $BASE, got $REALPY. Stopping." >&2; exit 1;; esac

echo "→ Connector in $BASE/venv"
uv venv "$BASE/venv" --python "$PY" --quiet --allow-existing
uv pip install --python "$BASE/venv/bin/python" --quiet --reinstall-package photos-mcp "$REPO"
CMD="$BASE/venv/bin/photos-mcp"
[ -x "$CMD" ] || { echo "Install failed: $CMD missing" >&2; exit 1; }

echo "→ Claude desktop app settings"
"$BASE/venv/bin/python" - "$CMD" <<'EOF'
import json, pathlib, sys
p = pathlib.Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
c = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
s = c.setdefault("mcpServers", {})
removed = s.pop("osxphotos", None) is not None
s["photos"] = {"command": sys.argv[1], "args": []}
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(c, indent=2))
print("   photos connector added" + ("; old osxphotos connector removed" if removed else ""))
EOF

if command -v claude >/dev/null; then
  echo "→ Claude Code (user scope: every project)"
  claude mcp remove osxphotos -s user >/dev/null 2>&1 || true
  claude mcp remove photos -s user >/dev/null 2>&1 || true
  claude mcp add --scope user photos -- "$CMD" >/dev/null && echo "   added"
else
  echo "→ Claude Code not found on PATH; skipped (run ./install.sh again after installing it)"
fi

echo
echo "Last step: give Full Disk Access to this one program (and nothing else):"
echo
echo "   $REALPY"
echo
printf '%s' "$REALPY" | pbcopy 2>/dev/null && echo "(It's on your clipboard: in Full Disk Access click +, press ⌘⇧G, paste, Open.)"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
echo "Then quit Claude (right-click its Dock icon → Quit) and reopen it."
