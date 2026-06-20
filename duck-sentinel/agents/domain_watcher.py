"""
Domain watcher agent — monitors a URL for meaningful changes.

Mandate shape:
    {
        "url": "https://example.com/api",
        "interest": "4xx/5xx status, content diff, cert change"
    }

Findings fire on: status change, significant content hash change, new
security headers, or HTTP error surges. Pings the bus → triage → brain.
"""
import hashlib
import sys
import time

import requests

from base import Agent


def _content_hash(r: requests.Response) -> str:
    # Hash first 10KB of body; ignore dynamic noise like CSRF tokens by
    # collapsing long digit/hex runs.
    text = r.text[:10000]
    # Very crude normalization
    import re
    text = re.sub(r"[a-f0-9]{16,}", "X", text)
    text = re.sub(r"\d{10,}", "N", text)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def main():
    agent = Agent.from_argv()
    url = agent.mandate.get("url")
    if not url:
        agent.log("no url in mandate; exiting")
        return
    agent.log(f"watching {url} every {agent.interval_sec}s")

    last_status = None
    last_hash = None
    last_headers = {}

    while not agent.should_stop():
        try:
            r = requests.get(url, timeout=10, allow_redirects=True,
                             headers={"User-Agent": "duck-sentinel/1.0"})
            status = r.status_code
            h = _content_hash(r)
            secure_headers = {
                k: v for k, v in r.headers.items()
                if k.lower() in ("content-security-policy", "strict-transport-security",
                                 "x-frame-options", "x-content-type-options",
                                 "server", "access-control-allow-origin")
            }

            # First observation — set baseline silently
            if last_status is None:
                last_status = status
                last_hash = h
                last_headers = secure_headers
                agent.log(f"baseline: status={status}, hash={h[:10]}")
            else:
                changes = []
                if status != last_status:
                    changes.append(f"status {last_status} → {status}")
                if h != last_hash:
                    changes.append("content diff")
                header_diff = {
                    k: (last_headers.get(k), v)
                    for k, v in secure_headers.items()
                    if last_headers.get(k) != v
                }
                for k, (old, new) in header_diff.items():
                    changes.append(f"header `{k}` changed")

                if changes:
                    urgent = (status >= 400 and last_status < 400) or status >= 500
                    agent.finding({
                        "url": url,
                        "changes": changes,
                        "status": status,
                        "prev_status": last_status,
                        "header_changes": header_diff,
                    }, urgent=urgent)
                    agent.log(f"CHANGE: {', '.join(changes)}")
                last_status = status
                last_hash = h
                last_headers = secure_headers
        except requests.RequestException as e:
            # Connection errors are themselves signal (site down)
            agent.log(f"request error: {e}")
            if last_status is not None and last_status < 400:
                agent.finding({
                    "url": url,
                    "changes": [f"unreachable: {type(e).__name__}"],
                    "status": None,
                }, urgent=True)
            last_status = -1
        except Exception as e:
            agent.log(f"unexpected error: {e}")

        # Sleep in small slices so kill.flag is responsive
        slept = 0.0
        while slept < agent.interval_sec and not agent.should_stop():
            time.sleep(1)
            slept += 1

    agent.finalize()


if __name__ == "__main__":
    main()
