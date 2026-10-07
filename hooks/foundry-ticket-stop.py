#!/usr/bin/env python3
"""foundry-ticket-stop — Stop hook: do not stop on reversible work.

Measured three times (July, September, October 2026): the dominant autonomy failure is the agent
ending its turn after a chunk of work it already reasoned through — 69–81% of handoffs were silent
yields, with no question asked. This hook makes the ticket's runnable `Done means` the stop
condition: while the active ticket (`.claude/foundry-ticket.json`) is open and its command fails,
the stop is refused (exit 2) with the failing tail as the reason, up to STOP_CAP times per ticket;
after that the session may end and the ticket stays open for the next one.

Fail-open: no ticket, no Done-means command, a command that times out (default 300 s), or a
malformed payload all admit the stop. The hook never runs anything the ticket did not declare.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STOP_CAP = 3
TIMEOUT_S = int(os.environ.get("FOUNDRY_TICKET_STOP_TIMEOUT", "300"))


def _root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return Path.cwd().resolve()


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        pass  # payload is not needed; the ticket file is the state
    root = _root()
    tf = root / ".claude" / "foundry-ticket.json"
    if not tf.is_file():
        return 0
    try:
        doc = json.loads(tf.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if doc.get("status") != "open":
        return 0
    cmd = (doc.get("done") or "").strip()
    if not cmd:
        return 0
    blocks = int(doc.get("stop_blocks") or 0)
    if blocks >= STOP_CAP:
        return 0
    try:
        out = subprocess.run(["/bin/bash", "-c", cmd], cwd=str(root), capture_output=True,
                             text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 0
    if out.returncode == 0:
        doc["status"] = "done"
        doc["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tf.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return 0
    doc["stop_blocks"] = blocks + 1
    tf.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    tail = "\n".join((out.stdout + out.stderr).strip().splitlines()[-15:])
    reason = ("foundry-ticket-stop: ticket #%s is not done (Done means exited %d; refusal %d/%d). "
              "Do not stop on reversible work — fix it and run the check again. If a step genuinely "
              "needs the operator, say exactly which one and why.\n%s"
              % (doc.get("issue"), out.returncode, blocks + 1, STOP_CAP, tail))
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    return 2


if __name__ == "__main__":
    sys.exit(main())
