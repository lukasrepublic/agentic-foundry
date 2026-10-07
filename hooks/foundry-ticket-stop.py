#!/usr/bin/env python3
"""foundry-ticket-stop — Stop hook: do not stop on reversible work.

Measured three times (July, September, October 2026): the dominant autonomy failure is the agent
ending its turn after a chunk of work it already reasoned through — 69–81% of handoffs were silent
yields, with no question asked. This hook makes the ticket's runnable `Done means` the stop
condition: while the active ticket (`.claude/foundry-ticket.json`) is open and its command fails
or times out, the stop is refused (exit 2, reason on stderr) with the failing tail, up to STOP_CAP
times per ticket; after that the session may end and the ticket stays open for the next one.

Fail-open: no ticket, no Done-means command, or a malformed payload admits the stop. A timeout
counts as a refusal (so a hanging check cannot cost TIMEOUT_S on every later stop forever) and the
command's whole process group is killed. The hook never runs anything the ticket did not declare;
`foundry-ticket.py start` only adopts a command from an issue author with write access.
hooks.json gives this entry a timeout larger than TIMEOUT_S.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STOP_CAP = 3
try:
    TIMEOUT_S = int(os.environ.get("FOUNDRY_TICKET_STOP_TIMEOUT", "300"))
except ValueError:
    TIMEOUT_S = 300


def _root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return Path.cwd().resolve()


def _load(tf: Path):
    try:
        return json.loads(tf.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run(cmd: str, root: Path):
    """(rc, tail) — rc 124 on timeout; the process group is killed so no grandchild is orphaned."""
    proc = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=str(root), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except Exception:
            pass
        out, _ = proc.communicate()
        return 124, "(timed out after %ds)\n%s" % (TIMEOUT_S, (out or "")[-800:])
    return proc.returncode, "\n".join((out or "").strip().splitlines()[-15:])


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        pass  # payload is not needed; the ticket file is the state
    root = _root()
    tf = root / ".claude" / "foundry-ticket.json"
    if not tf.is_file():
        return 0
    doc = _load(tf)
    if not doc or doc.get("status") != "open":
        return 0
    cmd = (doc.get("done") or "").strip()
    if not cmd:
        return 0
    try:
        blocks = int(doc.get("stop_blocks") or 0)
    except (TypeError, ValueError):
        blocks = 0
    if blocks >= STOP_CAP:
        return 0
    try:
        rc, tail = _run(cmd, root)
    except OSError as e:
        rc, tail = 127, str(e)
    # re-read: an `allow` made while the command ran must not be lost
    doc = _load(tf) or doc
    if rc == 0:
        doc["status"] = "done"
        doc["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tf.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return 0
    doc["stop_blocks"] = blocks + 1
    tf.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    reason = ("foundry-ticket-stop: ticket #%s is not done (Done means exited %d; refusal %d/%d). "
              "Do not stop on reversible work — fix it and run the check again. If a step genuinely "
              "needs the operator, say exactly which one and why.\n%s"
              % (doc.get("issue"), rc, blocks + 1, STOP_CAP, tail))
    sys.stderr.write(reason + "\n")
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    return 2


if __name__ == "__main__":
    sys.exit(main())
