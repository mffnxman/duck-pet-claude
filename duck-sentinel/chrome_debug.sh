#!/usr/bin/env bash
# Relaunch Chrome with --remote-debugging-port=9222 so browser_watcher can
# attach via Chrome DevTools Protocol.
#
# IMPORTANT: Closes all existing Chrome windows first (CDP port can't bind
# to an already-running Chrome). Your tabs are preserved via the normal
# profile on next restart.

set -u
PORT=9222
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
[ -x "$CHROME" ] || CHROME="/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
[ -x "$CHROME" ] || {
    echo "[chrome_debug] chrome.exe not found in Program Files"
    exit 1
}

PS_EXE="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# Short-circuit if CDP is already available — don't clobber user's session.
if curl -s --max-time 1 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "[chrome_debug] CDP already up on port $PORT — nothing to do"
    curl -s "http://127.0.0.1:$PORT/json/version" | head -c 300
    echo
    exit 0
fi

echo "[chrome_debug] closing existing Chrome windows..."
"$PS_EXE" -Command "Get-Process chrome -ErrorAction SilentlyContinue | \
Stop-Process -Force; 'done'" 2>&1 | tail -2
sleep 2

echo "[chrome_debug] launching Chrome with CDP on port $PORT..."
"$CHROME" --remote-debugging-port=$PORT \
          --remote-allow-origins="http://127.0.0.1:$PORT" \
          >/dev/null 2>&1 &
disown 2>/dev/null

sleep 3
if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null; then
    echo "[chrome_debug] CDP ready at http://127.0.0.1:$PORT"
    echo "[chrome_debug] browser_watcher.py can now attach"
else
    echo "[chrome_debug] WARNING: CDP did not come up. Check Chrome launched ok."
fi
