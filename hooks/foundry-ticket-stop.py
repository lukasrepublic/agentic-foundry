#!/usr/bin/env python3
"""foundry-ticket-stop — Stop hook: do not stop on reversible work.

Measured three times (July, September, October 2026): the dominant autonomy failure is the agent
ending its turn after a chunk of work it already reasoned through — 69–81% of handoffs were silent
yields, with no question asked. This hook makes the ticket's runnable `Done means` the stop
condition: while the active ticket is open and its command fails or times out, the stop is refused
(exit 2, reason on stderr) with the failing tail, up to STOP_CAP times per ticket; after that the
session may end and the ticket stays open for the next one.

Containment (security review of #270): the ticket is read ONLY from the repository's git dir
(scripts/foundry_ticket_store.py) — a `.claude/foundry-ticket.json` in the working tree, which a fork
branch could commit, is never consulted. The command must match the digest `start` recorded; a
ticket adopted with `--trust-author` is not auto-run here (an explicit `foundry-ticket.py done` runs
it). The timeout is clamped to 300 s; the command's whole process group is killed after every run
and on SIGTERM; output is redacted before it reaches the transcript. Fail-open otherwise (no
ticket, no command, malformed payload admit the stop).
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foundry_ticket_store as store  # noqa: E402

STOP_CAP = 3
try:
    TIMEOUT_S = min(max(int(os.environ.get("FOUNDRY_TICKET_STOP_TIMEOUT", "300")), 1), 300)
except ValueError:
    TIMEOUT_S = 300

_proc = None


def _kill_group():
    if _proc is not None:
        try:
            os.killpg(_proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _on_term(_signum, _frame):
    _kill_group()
    sys.exit(0)


def _load(tf: Path):
    try:
        return json.loads(tf.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run(cmd: str, root: Path):
    """(rc, tail) — rc 124 on timeout; the process group is killed in every case."""
    global _proc
    _proc = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=str(root), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        try:
            out, _ = _proc.communicate(timeout=TIMEOUT_S)
            rc = _proc.returncode
        except subprocess.TimeoutExpired:
            out, rc = "", 124
    finally:
        _kill_group()
        try:
            _proc.communicate(timeout=5)
        except Exception:
            pass
    if rc == 124:
        return 124, "(timed out after %ds)" % TIMEOUT_S
    return rc, store.redact("\n".join((out or "").strip().splitlines()[-15:]))


def main() -> int:
    signal.signal(signal.SIGTERM, _on_term)
    try:
        json.load(sys.stdin)
    except Exception:
        pass  # payload is not needed; the ticket file is the state
    root = store.project_root()
    tf = store.ticket_file(root)
    if not tf or not tf.is_file():
        return 0
    doc = _load(tf)
    if not doc or doc.get("status") != "open":
        return 0
    cmd = (doc.get("done") or "").strip()
    if not cmd:
        return 0
    if store.command_digest(cmd) != doc.get("done_sha256"):
        sys.stderr.write("foundry-ticket-stop: ticket command does not match its recorded digest; not run. "
                         "Re-run `foundry-ticket.py start`.\n")
        return 0
    if doc.get("trusted_by") == "flag":
        sys.stderr.write("foundry-ticket-stop: ticket #%s was adopted with --trust-author; its command is not "
                         "auto-run. Run `foundry-ticket.py done` explicitly.\n" % doc.get("issue"))
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
