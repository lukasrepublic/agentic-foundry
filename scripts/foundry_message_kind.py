#!/usr/bin/env python3
"""foundry_message_kind -- the closed-vocabulary lint for one shared-message-log record (gv-09).

Re-extracted from the 1.18.5 first-line lint and moved from "the kind is the first line of free text" to
"the kind is a field of a structured record". The closed set is:

    FINDING | NEEDS-INTERFACE | CHALLENGE | HANDOFF | TICK | PROD-BEFORE | PROD-AFTER | STOP

  * FINDING / CHALLENGE  carry >=1 evidence line in the body: an `evidence:` line, a URL, a repo-relative
                         path (optionally `:line`), or a captured `$ ...` / `> ...` line.
  * HANDOFF              carries a fenced ```json block with exactly cwd/command/why/expect; the command is
                         one bare command (no chaining, substitution, top-level `exec`/`trap`/`set -e`).
  * TICK                 body's first line is exactly `<programme> <UTC-stamp>` (`[a-z0-9-]+`, `YYYY-MM-DDTHH:MM:SSZ`).
  * PROD-BEFORE / PROD-AFTER / STOP   carry a `corr` (the id that joins a production step's before/after/stop)
                         and a non-empty body; a STOP's body is its reason.
  * NEEDS-INTERFACE      no further structural requirement.

`lint(record) -> list[str]` returns the named errors ([] = valid). `strict_body=False` skips the
evidence/handoff/tick body rules (used only by the one-time issue import, whose source text predates them).

CLI:  foundry_message_kind.py --in <record.json | ->      exit 0 valid / 3 invalid (errors on stdout) / 2 unreadable.
No network. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys

KINDS = ("FINDING", "NEEDS-INTERFACE", "CHALLENGE", "HANDOFF", "TICK", "PROD-BEFORE", "PROD-AFTER", "STOP")
_EVIDENCE_KINDS = ("FINDING", "CHALLENGE")
_CORR_KINDS = ("PROD-BEFORE", "PROD-AFTER", "STOP")
VERSION = 1
MAX_BODY_BYTES = 16 * 1024

_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_TICK_LINE_RE = re.compile(r"^([a-z0-9-]+) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$")
_EVIDENCE_PREFIX_RE = re.compile(r"(?i)\bevidence:\s*\S")
_URL_RE = re.compile(r"https?://\S+")
_PATH_RE = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\.(?=[0-9]*[A-Za-z])[A-Za-z0-9]{1,10}(?::\d+)?\b")
_CMD_OUTPUT_RE = re.compile(r"^\s*[$>]\s+\S")

_HANDOFF_FIELDS = ("cwd", "command", "why", "expect")
_CHAIN_TOKENS = (("&&", "&&"), (";", ";"), ("|", "|"), ("\n", "newline"), ("\r", "carriage return"),
                 ("$(", "$( command substitution"), ("`", "backtick command substitution"),
                 (">", "redirection"), ("<", "redirection"), ("&", "background/chaining &"))
_REFUSED_TOP_WORDS = {"trap", "exec"}


def _has_evidence_line(body: str) -> bool:
    return any(_EVIDENCE_PREFIX_RE.search(ln) or _URL_RE.search(ln) or _PATH_RE.search(ln)
               or _CMD_OUTPUT_RE.match(ln) for ln in body.split("\n"))


def _handoff_errors(handoff) -> list:
    if not isinstance(handoff, dict):
        return ["handoff must be a JSON object"]
    errs = []
    unknown = sorted(set(handoff) - set(_HANDOFF_FIELDS))
    if unknown:
        errs.append(f"handoff has unknown field(s) {unknown!r}")
    for f in _HANDOFF_FIELDS:
        if f not in handoff:
            errs.append(f"handoff missing required field {f!r}")
    if "cwd" in handoff:
        cwd = handoff["cwd"]
        if not (isinstance(cwd, str) and cwd and (cwd == "~" or cwd.startswith("~/") or cwd.startswith("/"))):
            errs.append(f"handoff.cwd {cwd!r} must be absolute or ~-relative")
    if "command" in handoff:
        cmd = handoff["command"]
        if not (isinstance(cmd, str) and cmd.strip()):
            errs.append("handoff.command must be a non-empty string")
        else:
            for tok, name in _CHAIN_TOKENS:
                if tok in cmd:
                    errs.append(f"handoff.command contains {name!r} -- one bare command only")
            try:
                words = shlex.split(cmd)
            except ValueError as e:
                errs.append(f"handoff.command is not shell-tokenizable: {e}")
                words = []
            if words and words[0] in _REFUSED_TOP_WORDS:
                errs.append(f"handoff.command starts with {words[0]!r}, which is refused")
            if words and words[0] == "set" and any(w.startswith("-") and "e" in w for w in words[1:]):
                errs.append("handoff.command starts with 'set -e', which is refused")
    for f in ("why", "expect"):
        if f in handoff and not (isinstance(handoff[f], str) and handoff[f]):
            errs.append(f"handoff.{f} must be a non-empty string")
    return errs


def _body_errors(kind: str, body: str) -> list:
    if kind in _EVIDENCE_KINDS and not _has_evidence_line(body):
        return [f"evidence-required: {kind} needs >=1 evidence line (an `evidence:` line, a URL, a path, "
                "or captured command output)"]
    if kind == "HANDOFF":
        m = _JSON_FENCE_RE.search(body)
        if not m:
            return ["handoff-json-block-missing: HANDOFF needs a fenced ```json block (cwd, command, why, expect)"]
        try:
            parsed = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            return [f"handoff-json-not-parseable: {e}"]
        errs = _handoff_errors(parsed)
        return [f"handoff-shape: {e}" for e in errs]
    if kind == "TICK":
        first = body.split("\n", 1)[0].strip()
        if not _TICK_LINE_RE.match(first):
            return ["tick-shape: a TICK body's first line must be '<programme> <YYYY-MM-DDTHH:MM:SSZ>' "
                    f"with a [a-z0-9-]+ programme: {first[:80]!r}"]
    return []


def lint(record, strict_body: bool = True) -> list:
    """Pure: record dict -> list of named errors ([] = valid)."""
    if not isinstance(record, dict):
        return ["record-shape: a record must be a JSON object"]
    errs = []
    if record.get("v") != VERSION:
        errs.append(f"version: v must be {VERSION}")
    if not (isinstance(record.get("id"), str) and _ULID_RE.match(record["id"])):
        errs.append("id: must be a 26-character ULID")
    if not (isinstance(record.get("time"), str) and _RFC3339_UTC_RE.match(record["time"])):
        errs.append("time: must be an RFC 3339 UTC timestamp ending in Z")
    for f in ("from", "to"):
        if not (isinstance(record.get(f), str) and record[f].strip()):
            errs.append(f"{f}: must be a non-empty string")
    kind = record.get("kind")
    if kind not in KINDS:
        errs.append(f"kind: {kind!r} is outside the closed set {list(KINDS)!r}")
    refs = record.get("refs")
    if not (isinstance(refs, list) and all(isinstance(r, str) and r for r in refs)):
        errs.append("refs: must be a list of non-empty strings")
    corr = record.get("corr")
    if corr is not None and not (isinstance(corr, str) and corr.strip()):
        errs.append("corr: must be a non-empty string when present")
    body = record.get("body")
    if not isinstance(body, str):
        errs.append("body: must be a string")
        return errs
    if strict_body and len(body.encode("utf-8")) > MAX_BODY_BYTES:
        errs.append(f"body-too-large: a body is capped at {MAX_BODY_BYTES} bytes")
    if kind in _CORR_KINDS:
        if not corr:
            errs.append(f"corr-required: {kind} needs a corr id joining its before/after/stop")
        if not body.strip():
            errs.append(f"body-required: {kind} needs a non-empty body")
    if kind in KINDS and strict_body:
        errs.extend(_body_errors(kind, body))
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="lint one shared-message-log record")
    ap.add_argument("--in", dest="in_path", required=True, help="path to the record JSON, or '-' for stdin")
    args = ap.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.in_path == "-" else open(args.in_path, encoding="utf-8").read()
        record = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"foundry_message_kind: REFUSED -- cannot read a record from {args.in_path!r}: {e}", file=sys.stderr)
        return 2
    errs = lint(record)
    print(json.dumps({"valid": not errs, "kind": record.get("kind") if isinstance(record, dict) else None,
                      "errors": errs}, indent=2))
    return 0 if not errs else 3


if __name__ == "__main__":
    raise SystemExit(main())
