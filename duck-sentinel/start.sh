#!/usr/bin/env bash
# Launch all three duck-sentinel workers: sentinel, triage, duck_brain.
# Each writes its log to /tmp/<name>.log. Re-running this script will kill
# existing workers first so you always have exactly one of each.

set -u
cd "$(dirname "$0")"

PS_EXE="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

stop_worker() {
    local script="$1"
    "$PS_EXE" -Command "Get-CimInstance Win32_Process | \
Where-Object { \$_.CommandLine -like '*${script}*' -and \$_.Name -eq 'python.exe' } | \
ForEach-Object { Stop-Process -Id \$_.ProcessId -Force; \
'stopped ${script} ' + \$_.ProcessId }" 2>/dev/null
}

start_worker() {
    local script="$1"
    local log="/tmp/${script%.py}.log"
    PYTHONIOENCODING=utf-8 nohup python -u "$script" > "$log" 2>&1 &
    echo "  ${script} pid=$! log=$log"
}

echo "[duck] stopping any running workers..."
for s in sentinel.py triage.py duck_brain.py browser_watcher.py \
         ask_listener.py duck_session.py duck_proactive.py; do
    stop_worker "$s"
done

sleep 1

echo "[duck] launching workers..."
start_worker "sentinel.py"
start_worker "triage.py"
start_worker "browser_watcher.py"
# duck_session first — brain+ask_listener+proactive depend on its HTTP port.
start_worker "duck_session.py"
echo "  (waiting 8s for duck_session to warm up claude -p)"
sleep 8
start_worker "ask_listener.py"
start_worker "duck_brain.py"
start_worker "duck_proactive.py"

sleep 2
echo
echo "[duck] status:"
"$PS_EXE" -Command "Get-CimInstance Win32_Process | \
Where-Object { (\$_.CommandLine -like '*sentinel.py*' -or \
\$_.CommandLine -like '*triage.py*' -or \
\$_.CommandLine -like '*browser_watcher.py*' -or \
\$_.CommandLine -like '*ask_listener.py*' -or \
\$_.CommandLine -like '*duck_session.py*' -or \
\$_.CommandLine -like '*duck_proactive.py*' -or \
\$_.CommandLine -like '*duck_brain.py*') -and \$_.Name -eq 'python.exe' } | \
Select-Object ProcessId,@{N='Script';E={ \
if (\$_.CommandLine -like '*sentinel.py*') {'sentinel'} \
elseif (\$_.CommandLine -like '*triage.py*') {'triage'} \
elseif (\$_.CommandLine -like '*browser_watcher.py*') {'browser'} \
elseif (\$_.CommandLine -like '*ask_listener.py*') {'ask'} \
elseif (\$_.CommandLine -like '*duck_session.py*') {'session'} \
elseif (\$_.CommandLine -like '*duck_proactive.py*') {'proactive'} \
else {'duck_brain'} }} | Format-Table -AutoSize" 2>/dev/null

echo
echo "[duck] tail logs with:  tail -f /tmp/sentinel.log /tmp/triage.log /tmp/duck_brain.log"
echo "[duck] inbox at:         ~/Downloads/duck_scratchpad/inbox.md"
