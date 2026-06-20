"""
Agent manager — CLI for spawning / listing / killing duck sub-agents.

This is the primitive the duck uses (via Bash tool) to delegate long
tasks. Usage:

    python agent_manager.py spawn domain_watcher \
        --mandate '{"url":"https://example.com","interest":"4xx surges"}' \
        --interval 60 --desc "watch example.com for errors"

    python agent_manager.py list
    python agent_manager.py kill <agent_id>
    python agent_manager.py findings <agent_id>
    python agent_manager.py reap            # clean up dead agents

Agent types live at agents/<type>.py and must call Agent.from_argv().
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.join(_THIS_DIR, "agents")
AGENT_TYPES_DIR = AGENTS_DIR


def _list_agent_types() -> list[str]:
    if not os.path.isdir(AGENT_TYPES_DIR):
        return []
    return sorted(
        f[:-3] for f in os.listdir(AGENT_TYPES_DIR)
        if f.endswith(".py") and f not in ("base.py", "__init__.py")
    )


def _agent_dir(agent_id: str) -> str:
    return os.path.join(AGENTS_DIR, agent_id)


def _read_pid(agent_id: str) -> int | None:
    p = os.path.join(_agent_dir(agent_id), "pid.txt")
    try:
        with open(p, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        # Fallback — signal 0 check
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def cmd_spawn(args):
    if args.type not in _list_agent_types():
        print(f"unknown agent type '{args.type}'. "
              f"available: {_list_agent_types()}", file=sys.stderr)
        sys.exit(2)
    try:
        mandate = json.loads(args.mandate)
    except json.JSONDecodeError as e:
        print(f"--mandate must be JSON: {e}", file=sys.stderr)
        sys.exit(2)

    agent_id = f"{args.type}-{uuid.uuid4().hex[:8]}"
    adir = _agent_dir(agent_id)
    os.makedirs(adir, exist_ok=True)

    meta = {
        "id": agent_id,
        "type": args.type,
        "mandate": mandate,
        "interval_sec": args.interval,
        "description": args.desc or f"{args.type} agent",
        "spawned_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(adir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    script = os.path.join(AGENT_TYPES_DIR, f"{args.type}.py")
    # CREATE_NO_WINDOW on Windows so agents don't flash a console
    creationflags = 0x08000000 if os.name == "nt" else 0
    log_path = os.path.join(adir, "stdout.log")
    with open(log_path, "a", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            [sys.executable, script, agent_id],
            stdout=logf, stderr=subprocess.STDOUT,
            cwd=AGENT_TYPES_DIR,
            creationflags=creationflags,
        )
    # Best-effort: wait briefly to confirm it didn't immediately die
    time.sleep(0.5)
    print(json.dumps({
        "id": agent_id,
        "type": args.type,
        "pid": proc.pid,
        "dir": adir,
        "description": meta["description"],
    }, indent=2))


def cmd_list(args):
    rows = []
    if not os.path.isdir(AGENTS_DIR):
        print(json.dumps([]))
        return
    for name in os.listdir(AGENTS_DIR):
        adir = os.path.join(AGENTS_DIR, name)
        meta_path = os.path.join(adir, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        pid = _read_pid(name)
        running = _is_running(pid)
        findings_count = 0
        fpath = os.path.join(adir, "findings.jsonl")
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    findings_count = sum(1 for _ in f)
            except OSError:
                pass
        rows.append({
            "id": meta.get("id", name),
            "type": meta.get("type"),
            "description": meta.get("description"),
            "spawned_at": meta.get("spawned_at"),
            "pid": pid,
            "running": running,
            "findings": findings_count,
        })
    print(json.dumps(rows, indent=2))


def cmd_kill(args):
    adir = _agent_dir(args.id)
    if not os.path.isdir(adir):
        print(f"no such agent: {args.id}", file=sys.stderr)
        sys.exit(1)
    # Graceful first
    flag = os.path.join(adir, "kill.flag")
    with open(flag, "w") as f:
        f.write(str(time.time()))
    print(f"kill flag set for {args.id}. waiting up to {args.wait}s for clean exit…")
    pid = _read_pid(args.id)
    deadline = time.time() + args.wait
    while time.time() < deadline and _is_running(pid):
        time.sleep(1)
    if pid and _is_running(pid):
        print("still alive — force terminating")
        if os.name == "nt":
            subprocess.run(
                [r"C:\Windows\System32\taskkill.exe", "/PID", str(pid), "/F"],
                capture_output=True,
            )
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    # Cleanup
    try:
        os.remove(flag)
    except OSError:
        pass
    print(f"killed {args.id}")


def cmd_findings(args):
    fpath = os.path.join(_agent_dir(args.id), "findings.jsonl")
    if not os.path.isfile(fpath):
        print("[]")
        return
    with open(fpath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    last_n = lines[-args.last:] if args.last > 0 else lines
    out = []
    for ln in last_n:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    print(json.dumps(out, indent=2))


def cmd_reap(args):
    """Remove dirs for agents that are no longer running and have old logs."""
    reaped = 0
    now = time.time()
    for name in os.listdir(AGENTS_DIR):
        adir = os.path.join(AGENTS_DIR, name)
        meta_path = os.path.join(adir, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        pid = _read_pid(name)
        if _is_running(pid):
            continue
        # Stale — check if spawn time is older than --older-than hours
        try:
            mtime = os.path.getmtime(meta_path)
        except OSError:
            continue
        age_h = (now - mtime) / 3600
        if age_h >= args.older_than:
            shutil.rmtree(adir, ignore_errors=True)
            reaped += 1
    print(f"reaped {reaped} dead agents older than {args.older_than}h")


def main():
    p = argparse.ArgumentParser(description="duck-sentinel agent manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spawn", help="spawn a new agent")
    sp.add_argument("type", help=f"agent type (one of {_list_agent_types()})")
    sp.add_argument("--mandate", required=True, help="JSON mandate")
    sp.add_argument("--interval", type=int, default=60,
                    help="tick interval in seconds (default 60)")
    sp.add_argument("--desc", default="", help="human-readable description")
    sp.set_defaults(func=cmd_spawn)

    sl = sub.add_parser("list", help="list agents")
    sl.set_defaults(func=cmd_list)

    sk = sub.add_parser("kill", help="kill an agent by id")
    sk.add_argument("id")
    sk.add_argument("--wait", type=int, default=5, help="seconds before SIGKILL")
    sk.set_defaults(func=cmd_kill)

    sf = sub.add_parser("findings", help="show agent findings")
    sf.add_argument("id")
    sf.add_argument("--last", type=int, default=20)
    sf.set_defaults(func=cmd_findings)

    sr = sub.add_parser("reap", help="remove dead agent dirs")
    sr.add_argument("--older-than", type=float, default=24,
                    help="only reap dirs older than N hours (default 24)")
    sr.set_defaults(func=cmd_reap)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
