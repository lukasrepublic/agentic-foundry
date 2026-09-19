#!/usr/bin/env python3
"""foundry_message_kind — the outgoing cross-session message lint (fleet-is-listagents, AC-FIL-3).

Cross-session reach is native (`ListAgents`, or a hook/Bash child posting into this session's own
inbox socket) and carries no constraint on the TEXT that crosses it — a command inside a message
never runs, and a message is never consent (see `docs/how-to/deck-and-containers.md`, quoting the
primary doc). What was missing was a shape for the text itself: an outgoing message could be
anything, so a reader had to parse full prose before knowing what it was even claiming. This lint
fixes a small, closed vocabulary onto the FIRST LINE:

    FINDING | NEEDS-INTERFACE | CHALLENGE | HANDOFF

`CLAIMED`/`DONE` are never messages — the native task list already carries task status, so a
message reporting only that duplicates it and is not one of the four kinds above.

`FINDING` and `CHALLENGE` SHALL carry at least one evidence line: an explicit `evidence:` line, a
URL, a repo-relative path (optionally with a `:line` suffix), or a captured command-output/prompt
line (`$ ...` / `> ...`). An assertion with nothing behind it is prose, not a finding.

`HANDOFF` SHALL carry a fenced ```json block matching `schema/blocker.schema.json`'s `handoff`
shape (`cwd`, `command`, `why`, `expect`, no unknown fields, `command` free of chaining/
substitution/top-level `set -e`/`trap`/`exec`). The shape check is REUSED BY IMPORT from
`foundry_blocker_check.py` (`_handoff_errors`) — never re-copied — so the deck's blocker lint and
this message lint cannot drift on what a safe handoff command looks like.

A fifth kind, `TICK`, was added by the `routine-wake` atom (autonomy-continuation R3): a Routine's
entire job is one message waking a named deck session, and that message needs no evidence — it
carries only a programme identifier and a stamp. `TICK`'s first line SHALL be exactly `TICK
<programme> <UTC-stamp>`, where `<programme>` is a `[a-z0-9-]+` slug and `<UTC-stamp>` has the
shape `YYYY-MM-DDTHH:MM:SSZ` (`datetime.strftime("%Y-%m-%dT%H:%M:%SZ")`, the same shape
`foundry_command_deck_watch._stamp` already renders elsewhere) — anything else on that line is
invalid, named by the `tick-shape` rule.

Usage:
    foundry_message_kind.py --in <path-to-text-file-or-'-'-for-stdin>

Output (exit 0, valid): one JSON object on stdout — {"valid": true, "kind": "<KIND>"}.
Output (exit 3, invalid): one JSON object on stdout naming the failing rule —
    {"valid": false, "kind": "<KIND-or-null>", "rule": "<rule-name>", "reason": "<detail>"}.
Malformed (exit 2): the --in source could not be read, or yielded no content at all — the reason
goes to stderr and nothing is printed to stdout (mirrors `foundry_blocker_check.py`'s own
malformed-input handling, AC-BRE-2).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

import foundry_blocker_check as _bc  # noqa: E402  (reuse _handoff_errors — never re-copy)

KINDS = ("FINDING", "NEEDS-INTERFACE", "CHALLENGE", "HANDOFF", "TICK")
_EVIDENCE_KINDS = ("FINDING", "CHALLENGE")

_FIRST_LINE_RE = re.compile(r"^(FINDING|NEEDS-INTERFACE|CHALLENGE|HANDOFF|TICK)(?::|\s|$)")
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# AC-RWK-1/routine-wake: TICK's first line is exactly `TICK <programme> <UTC-stamp>` — a
# [a-z0-9-]+ slug then a stamp shaped `YYYY-MM-DDTHH:MM:SSZ`. Nothing else on that line is valid.
_TICK_LINE_RE = re.compile(r"^TICK ([a-z0-9-]+) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$")

# An "evidence line" (AC-FIL-3): an explicit `evidence:` prefix, a URL, a repo-relative path with
# an extension (optionally `:line`), or a captured command/prompt line.
_EVIDENCE_PREFIX_RE = re.compile(r"(?i)\bevidence:\s*\S")
_URL_RE = re.compile(r"https?://\S+")
_PATH_RE = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\.[A-Za-z0-9]{1,10}(?::\d+)?\b")
_CMD_OUTPUT_RE = re.compile(r"^\s*[$>]\s+\S")


class MessageKindError(Exception):
    """Malformed --in input (AC-FIL-3): unreadable source, or no content at all."""


def _has_evidence_line(text: str) -> bool:
    # The kind line itself never counts: a claim that merely names a path or URL is still prose.
    for line in text.split("\n")[1:]:
        if (_EVIDENCE_PREFIX_RE.search(line) or _URL_RE.search(line)
                or _PATH_RE.search(line) or _CMD_OUTPUT_RE.match(line)):
            return True
    return False


def _handoff_json_block_errors(text: str):
    """Returns (rule, reason) on failure, or None when the HANDOFF's fenced json block is
    present, parseable, and passes the reused `_handoff_errors` shape floor."""
    m = _JSON_FENCE_RE.search(text)
    if not m:
        return ("handoff-json-block-missing",
                "HANDOFF must carry a fenced ```json block matching "
                "schema/blocker.schema.json's handoff shape")
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return ("handoff-json-not-parseable", f"the fenced json block is not valid JSON: {e}")
    errs = _bc._handoff_errors(parsed)
    if errs:
        return ("handoff-shape", "; ".join(errs))
    return None


def lint(text: str) -> dict:
    """Pure: text -> verdict dict. {"valid": True, "kind": ...} or
    {"valid": False, "kind": ..., "rule": ..., "reason": ...}."""
    first_line = text.split("\n", 1)[0].strip()
    m = _FIRST_LINE_RE.match(first_line)
    if not m:
        return {
            "valid": False,
            "kind": None,
            "rule": "first-line-kind",
            "reason": (
                f"first line does not start with one of {list(KINDS)!r}: {first_line[:80]!r}"
            ),
        }
    kind = m.group(1)

    if kind == "HANDOFF":
        failure = _handoff_json_block_errors(text)
        if failure:
            rule, reason = failure
            return {"valid": False, "kind": kind, "rule": rule, "reason": reason}
    elif kind == "TICK":
        if not _TICK_LINE_RE.match(first_line):
            return {
                "valid": False,
                "kind": kind,
                "rule": "tick-shape",
                "reason": (
                    "TICK's first line must be exactly 'TICK <programme> <UTC-stamp>' with a "
                    f"[a-z0-9-]+ programme and a YYYY-MM-DDTHH:MM:SSZ stamp: {first_line[:80]!r}"
                ),
            }
    elif kind in _EVIDENCE_KINDS:
        if not _has_evidence_line(text):
            return {
                "valid": False,
                "kind": kind,
                "rule": "evidence-required",
                "reason": (
                    f"{kind} requires >=1 evidence line (an `evidence:` line, a URL, a path, "
                    "or captured command output)"
                ),
            }
    # NEEDS-INTERFACE carries no further structural requirement beyond the kind line itself.

    return {"valid": True, "kind": kind}


def _load_input(path: str) -> str:
    if path == "-":
        try:
            raw = sys.stdin.read()
        except UnicodeDecodeError as e:
            raise MessageKindError(f"stdin is not UTF-8: {e}") from e
    else:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            raise MessageKindError(f"cannot read {path!r}: {e}") from e
    if raw.strip() == "":
        raise MessageKindError(f"{path!r} yields no content")
    return raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="lint an outgoing cross-session message's kind")
    ap.add_argument("--in", dest="in_path", required=True,
                     help="path to the message text, or '-' for stdin")
    args = ap.parse_args(argv)

    try:
        text = _load_input(args.in_path)
    except MessageKindError as e:
        print(f"foundry_message_kind: REFUSED — {e}", file=sys.stderr)
        return 2

    verdict = lint(text)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["valid"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
