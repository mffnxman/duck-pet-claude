#!/usr/bin/env bash
# Stop all duck-sentinel workers.
PS_EXE="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
for script in sentinel.py triage.py duck_brain.py browser_watcher.py \
              ask_listener.py duck_session.py duck_proactive.py; do
    "$PS_EXE" -Command "Get-CimInstance Win32_Process | \
Where-Object { \$_.CommandLine -like '*${script}*' -and \$_.Name -eq 'python.exe' } | \
ForEach-Object { Stop-Process -Id \$_.ProcessId -Force; \
'stopped ${script} ' + \$_.ProcessId }" 2>/dev/null
done
