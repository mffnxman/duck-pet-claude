#!/usr/bin/env bash
# duck.sh — one command to rule them all.
#
#   duck.sh                     # status (default)
#   duck.sh start               # launch full stack: sentinel + pet + status panel
#   duck.sh stop                # kill everything (sentinel + pet + status)
#   duck.sh restart             # stop then start
#   duck.sh status              # show all worker health
#   duck.sh ask "your question" # drop into ask.txt → duck bubble answer
#   duck.sh trends [refresh|builder|creator]  # Duck Radar: trending-AI digest
#   duck.sh logs                # tail all logs together
#   duck.sh watchers            # list running sub-agents
#   duck.sh forget              # nuke persistent session id (fresh memory)

set -u
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PET_DIR="$HOME/tools/claude-pet"
PS_EXE="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

all_scripts=(
    sentinel.py triage.py browser_watcher.py
    duck_session.py duck_proactive.py
    ask_listener.py duck_brain.py
    pet.py duck_status.py
)

stop_all() {
    echo "[duck] stopping everything..."
    for s in "${all_scripts[@]}"; do
        "$PS_EXE" -NoProfile -Command "Get-CimInstance Win32_Process | \
Where-Object { \$_.CommandLine -like '*${s}*' -and \
(\$_.Name -eq 'python.exe' -or \$_.Name -eq 'pythonw.exe') } | \
ForEach-Object { Stop-Process -Id \$_.ProcessId -Force; \
'  stopped ${s} ' + \$_.ProcessId }" 2>/dev/null
    done
}

start_sentinel() {
    local s="$1"
    local log="/tmp/${s%.py}.log"
    PYTHONIOENCODING=utf-8 nohup python -u "$SELF_DIR/$s" > "$log" 2>&1 &
    echo "  $s pid=$! log=$log"
}

start_all() {
    echo "[duck] launching sentinel stack..."
    cd "$SELF_DIR"
    start_sentinel "sentinel.py"
    start_sentinel "triage.py"
    start_sentinel "browser_watcher.py"
    start_sentinel "duck_session.py"
    echo "  (warming claude session — 8s)"
    sleep 8
    start_sentinel "ask_listener.py"
    start_sentinel "duck_brain.py"
    start_sentinel "duck_proactive.py"

    echo "[duck] launching pet duck..."
    cd "$PET_DIR"
    PYTHONIOENCODING=utf-8 nohup pythonw pet.py > /tmp/pet.log 2>&1 &
    echo "  pet.py pid=$!"

    echo "[duck] launching status panel..."
    cd "$SELF_DIR"
    PYTHONIOENCODING=utf-8 nohup pythonw duck_status.py > /tmp/duck_status.log 2>&1 &
    echo "  duck_status.py pid=$!"

    sleep 2
    show_status
}

show_status() {
    echo
    echo "[duck] status:"
    "$PS_EXE" -NoProfile -Command "Get-CimInstance Win32_Process | \
Where-Object { (\$_.CommandLine -like '*sentinel.py*' -or \
\$_.CommandLine -like '*triage.py*' -or \
\$_.CommandLine -like '*browser_watcher.py*' -or \
\$_.CommandLine -like '*duck_session.py*' -or \
\$_.CommandLine -like '*duck_proactive.py*' -or \
\$_.CommandLine -like '*ask_listener.py*' -or \
\$_.CommandLine -like '*duck_brain.py*' -or \
\$_.CommandLine -like '*pet.py*' -or \
\$_.CommandLine -like '*duck_status.py*') -and \
(\$_.Name -eq 'python.exe' -or \$_.Name -eq 'pythonw.exe') } | \
Select-Object ProcessId,@{N='Worker';E={ \
if (\$_.CommandLine -like '*sentinel.py*'){'sentinel'} \
elseif (\$_.CommandLine -like '*triage.py*'){'triage'} \
elseif (\$_.CommandLine -like '*browser_watcher.py*'){'browser'} \
elseif (\$_.CommandLine -like '*duck_session.py*'){'session'} \
elseif (\$_.CommandLine -like '*duck_proactive.py*'){'proactive'} \
elseif (\$_.CommandLine -like '*ask_listener.py*'){'ask'} \
elseif (\$_.CommandLine -like '*duck_brain.py*'){'brain'} \
elseif (\$_.CommandLine -like '*pet.py*'){'pet'} \
else {'status-panel'} }} | Format-Table -AutoSize" 2>/dev/null
    echo "inbox: ~/Downloads/duck_scratchpad/inbox.md"
}

ask_duck() {
    local q="$*"
    if [ -z "$q" ]; then
        echo "usage: duck.sh ask \"your question\""
        exit 1
    fi
    mkdir -p "$HOME/Downloads/duck_scratchpad"
    echo "$q" > "$HOME/Downloads/duck_scratchpad/ask.txt"
    echo "[duck] dropped question — watch for the bubble."
}

trends_duck() {
    local arg="${1:-}"
    local flags=""
    case "$arg" in
        refresh) flags="--refresh" ;;
        builder) flags="--lane builder" ;;
        creator) flags="--lane creator" ;;
        "")      flags="" ;;
        *) echo "usage: duck.sh trends [refresh|builder|creator]"; return 1 ;;
    esac
    echo "[duck] pulling AI radar..."
    cd "$SELF_DIR"
    PYTHONIOENCODING=utf-8 python "$SELF_DIR/trend_digest.py" --cli $flags
    echo
    echo "[duck] full board: ~/Downloads/duck_scratchpad/trends.md"
}

show_logs() {
    exec tail -f /tmp/sentinel.log /tmp/triage.log /tmp/browser_watcher.log \
                 /tmp/duck_session.log /tmp/ask_listener.log \
                 /tmp/duck_brain.log /tmp/duck_proactive.log /tmp/pet.log
}

list_watchers() {
    python "$SELF_DIR/agent_manager.py" list
}

forget_memory() {
    local sid_file="$HOME/Downloads/duck_scratchpad/duck_session_id.txt"
    if [ -f "$sid_file" ]; then
        rm "$sid_file"
        echo "[duck] session id cleared — next start creates a fresh conversation"
        echo "       (persona memory from ~/.claude/.../memory/ still loads)"
    else
        echo "[duck] no session id to clear"
    fi
}

cmd="${1:-status}"
shift 2>/dev/null || true

case "$cmd" in
    start)    start_all ;;
    stop)     stop_all ;;
    restart)  stop_all; sleep 1; start_all ;;
    status|"") show_status ;;
    ask)      ask_duck "$@" ;;
    trends)   trends_duck "$@" ;;
    logs)     show_logs ;;
    watchers) list_watchers ;;
    forget)   forget_memory ;;
    *)
        echo "usage: duck.sh {start|stop|restart|status|ask|trends|logs|watchers|forget}"
        exit 2
        ;;
esac
