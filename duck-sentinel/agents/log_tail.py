"""
Log tail agent — watches a log file for pattern matches.

Mandate shape:
    {
        "path": "~/path/to/app.log",
        "patterns": ["ERROR", "timeout", "connection refused"],
        "urgent_patterns": ["ERROR", "CRITICAL"]
    }

Only new lines (appended after agent spawn) are matched — we don't
re-scan history. Each match becomes a finding; urgent patterns wake
the duck brain via the bus.
"""
import os
import re
import time

from base import Agent


def main():
    agent = Agent.from_argv()
    path = os.path.expanduser(agent.mandate.get("path", ""))
    patterns = [re.compile(p, re.IGNORECASE) for p in agent.mandate.get("patterns", [])]
    urgent = [re.compile(p, re.IGNORECASE) for p in agent.mandate.get("urgent_patterns", [])]

    if not path or not patterns:
        agent.log("mandate requires 'path' and at least one pattern; exiting")
        return

    agent.log(f"tailing {path} for {len(patterns)} patterns")

    # Start at end of file — only surface NEW activity
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    while not agent.should_stop():
        try:
            cur_size = os.path.getsize(path)
            if cur_size < size:
                # Rotated / truncated — start over from beginning
                size = 0
            if cur_size > size:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(size)
                    new_chunk = f.read()
                size = cur_size
                for line in new_chunk.splitlines():
                    for pat in patterns:
                        if pat.search(line):
                            is_urgent = any(u.search(line) for u in urgent)
                            agent.finding({
                                "path": path,
                                "pattern": pat.pattern,
                                "line": line[:500],
                            }, urgent=is_urgent)
                            agent.log(f"match ({pat.pattern}): {line[:120]}")
                            break  # one finding per line
        except FileNotFoundError:
            agent.log(f"file gone: {path}")
        except Exception as e:
            agent.log(f"error: {e}")

        # Responsive sleep
        slept = 0.0
        while slept < agent.interval_sec and not agent.should_stop():
            time.sleep(1)
            slept += 1

    agent.finalize()


if __name__ == "__main__":
    main()
