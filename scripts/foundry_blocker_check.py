#!/usr/bin/env python3
"""foundry_blocker_check — a blocker without evidence is a Next Task (feat-foundry-blocker-
requires-evidence, AC-BRE-2). Extended by feat-foundry-operator-handoff-schema (AC-OHS-1, AC-OHS-2)
to constrain the optional `handoff` field: anything the operator must run is handed over as DATA
(`cwd`, `command`, `why`, `expect`), never as prose to parse, and `why_operator` values that name a
credential/provisioning step REQUIRE one.

A deck tick's Blockers section used to be free prose, so anything could be reported as a blocker
and the operator became the inbox. This CLI is the lint a tick runs BEFORE it reports: it reads a
JSON list of candidate blockers (schema/blocker.schema.json, AC-BRE-1 — `claim`, `evidence[]`,
`attempted[]`, `why_operator` drawn from the SAME closed `escalate_when` set
`schema/acceptance-contract.schema.json` already fixes, optional but constrained `handoff`) and
partitions them into `blockers` (schema-valid) and `next_tasks` (everything else, each carrying WHY
it was demoted). The tick reports only the `blockers` partition under its Blockers section; the
rest goes under Next Tasks (skills/command-deck/tick-prompt.template.md §5c).

Validation mirrors foundry_audit_ledger.py's v2 write-boundary pattern: a real JSON-Schema check
against schema/blocker.schema.json when `jsonschema` is importable, PLUS a hand-rolled structural
floor that ALWAYS runs — so the check never silently degrades to a no-op when the optional
dependency is absent (mirrors foundry_contract.py's UL-0011 pattern). The hand-rolled floor is also
the one that NAMES the offending token in a refused `handoff.command` (AC-OHS-1) — the JSON-Schema
`pattern` checks alone can only accept/reject, not explain.

Usage:
    foundry_blocker_check.py --in <path-to-json-file-or-'-'-for-stdin>

Input: a JSON array of candidate blocker objects (possibly empty).
Output (exit 0): one JSON object on stdout —
    {"blockers": [<valid candidates, unchanged>],
     "next_tasks": [{"candidate": <original object>, "reason": "<why demoted>"}, ...]}
Malformed input (not valid JSON, or not a JSON array) exits 2 with the reason on stderr; nothing
is printed to stdout in that case.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = os.path.join(os.path.dirname(_HERE_DIR), "schema", "blocker.schema.json")

# The SAME closed set schema/acceptance-contract.schema.json's `escalate_when` enum fixes
# (feat-foundry-contract-done-when-escalate-when, AC-DWE-1) — not redefined independently here,
# restated only as the literal values so this module has no import-time dependency on that one.
_WHY_OPERATOR_SET = {
    "external-provisioning", "credential-step", "no-consensus-after-research",
    "security-widening", "irreversible-action",
}

_REQUIRED_FIELDS = ("claim", "evidence", "attempted", "why_operator")
_ALLOWED_FIELDS = _REQUIRED_FIELDS + ("handoff",)

# AC-OHS-2: these two why_operator members name a step the AGENT structurally cannot take itself
# -- a credential the agent does not hold, or provisioning outside its reach -- so a candidate
# carrying one MUST hand the operator the runnable command as data, not leave it to be inferred
# from `claim`/`evidence` prose.
_WHY_OPERATOR_REQUIRES_HANDOFF = {"credential-step", "external-provisioning"}

_HANDOFF_REQUIRED_FIELDS = ("cwd", "command", "why", "expect")
# AC-OHS-1: a bare command is refused if it chains -- these are checked as plain substrings so the
# offending token can be named verbatim in the reason, regardless of where in the command it sits.
_HANDOFF_CHAIN_TOKENS = (("&&", "&&"), (";", ";"), ("|", "|"), ("\n", "newline"), ("\r", "carriage return"))
# Refused only at the TOP LEVEL (as the command's own leading word or flag), never as a substring
# of a longer word (e.g. "trap_handler.sh" or "execute.sh" are fine) -- shlex tokenization is what
# makes that distinction possible.
_HANDOFF_TOP_LEVEL_REFUSED_WORDS = {"trap", "exec"}


def _handoff_errors(handoff) -> list[str]:
    """Hand-rolled structural floor for `handoff` (AC-OHS-1) -- ALWAYS runs, mirrors
    `_structural_errors`'s pattern, and is the one that names the offending token in a refused
    `command` (the JSON-Schema `pattern` checks in schema/blocker.schema.json can only
    accept/reject, not explain)."""
    if not isinstance(handoff, dict):
        return ["handoff must be a JSON object"]

    errs: list[str] = []

    unknown = sorted(set(handoff.keys()) - set(_HANDOFF_REQUIRED_FIELDS))
    if unknown:
        errs.append(f"handoff has unknown field(s) {unknown!r} (additionalProperties: false)")

    for field in _HANDOFF_REQUIRED_FIELDS:
        if field not in handoff:
            errs.append(f"handoff missing required field {field!r}")

    if "cwd" in handoff:
        cwd = handoff.get("cwd")
        if not (isinstance(cwd, str) and cwd and (cwd == "~" or cwd.startswith("~/") or cwd.startswith("/"))):
            errs.append(
                f"handoff.cwd {cwd!r} must be an absolute path (starts with '/') or a "
                "~-relative path (starts with '~'), never a bare relative path"
            )

    if "command" in handoff:
        command = handoff.get("command")
        if not (isinstance(command, str) and command.strip()):
            errs.append("handoff.command must be a non-empty string")
        else:
            for token, name in _HANDOFF_CHAIN_TOKENS:
                if token in command:
                    errs.append(
                        f"handoff.command contains {name!r} — one bare command only, no chaining"
                    )
            try:
                words = shlex.split(command)
            except ValueError as e:
                errs.append(f"handoff.command is not shell-tokenizable: {e}")
                words = []
            if words:
                if words[0] in _HANDOFF_TOP_LEVEL_REFUSED_WORDS:
                    errs.append(
                        f"handoff.command starts with {words[0]!r} — top-level {words[0]!r} is "
                        "refused (it could alter or exit the operator's own shell)"
                    )
                if words[0] == "set":
                    e_flags = [w for w in words[1:] if w.startswith("-") and "e" in w]
                    if e_flags:
                        errs.append(
                            f"handoff.command starts with 'set {e_flags[0]}' — top-level 'set -e' "
                            "is refused (it could exit the operator's own shell)"
                        )

    if "why" in handoff and not (isinstance(handoff.get("why"), str) and handoff.get("why")):
        errs.append("handoff.why must be a non-empty string")

    if "expect" in handoff and not (isinstance(handoff.get("expect"), str) and handoff.get("expect")):
        errs.append("handoff.expect must be a non-empty string")

    return errs


class BlockerCheckError(Exception):
    """Malformed --in input (AC-BRE-2): not valid JSON, or not a JSON array."""


def load_schema() -> dict:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _structural_errors(candidate) -> list[str]:
    """Hand-rolled structural floor for one candidate blocker — ALWAYS runs (in addition to the
    real JSON-Schema check when `jsonschema` is importable), so a missing optional dependency
    never silently widens what counts as a valid blocker."""
    if not isinstance(candidate, dict):
        return ["candidate is not a JSON object"]

    errs: list[str] = []

    unknown = sorted(set(candidate.keys()) - set(_ALLOWED_FIELDS))
    if unknown:
        errs.append(f"unknown field(s) {unknown!r} (additionalProperties: false)")

    for field in _REQUIRED_FIELDS:
        if field not in candidate:
            errs.append(f"missing required field {field!r}")

    claim = candidate.get("claim")
    if "claim" in candidate and not (isinstance(claim, str) and claim):
        errs.append("claim must be a non-empty string")

    for field in ("evidence", "attempted"):
        if field not in candidate:
            continue
        val = candidate.get(field)
        if not isinstance(val, list) or len(val) < 1:
            errs.append(f"{field} must be a non-empty list")
        else:
            for i, item in enumerate(val):
                if not isinstance(item, str) or not item:
                    errs.append(f"{field}[{i}] must be a non-empty string")

    if "why_operator" in candidate:
        wo = candidate.get("why_operator")
        if wo not in _WHY_OPERATOR_SET:
            errs.append(
                f"why_operator {wo!r} not in the closed escalate_when set {sorted(_WHY_OPERATOR_SET)}"
            )
        # AC-OHS-2: credential-step / external-provisioning REQUIRE a handoff.
        elif wo in _WHY_OPERATOR_REQUIRES_HANDOFF and "handoff" not in candidate:
            errs.append(
                f"why_operator {wo!r} requires handoff (the operator needs cwd/command/why/expect, "
                "not prose, to act on a credential or provisioning step)"
            )

    if "handoff" in candidate:
        errs.extend(_handoff_errors(candidate.get("handoff")))

    return errs


def validate_candidate(candidate) -> list[str]:
    """Validate one candidate blocker. Uses `jsonschema` against `schema/blocker.schema.json`
    when the optional dependency is importable, PLUS the hand-rolled `_structural_errors` floor
    — which always runs regardless (never a silent no-op degrade). Returns a list of error
    strings; `[]` means valid."""
    errors: list[str] = []
    try:
        import jsonschema  # type: ignore
    except ImportError:
        jsonschema = None  # type: ignore

    if jsonschema is not None:
        try:
            jsonschema.validate(candidate, load_schema())
        except jsonschema.ValidationError as e:  # type: ignore[attr-defined]
            errors.append(f"schema: {e.message}")
        except OSError as e:
            errors.append(f"schema file unreadable: {e}")

    errors.extend(_structural_errors(candidate))
    return errors


def partition(candidates: list) -> dict:
    """AC-BRE-2: partition a list of candidate blockers into `blockers` (schema-valid, reported
    unchanged) and `next_tasks` (invalid, each paired with the reason it was demoted)."""
    blockers = []
    next_tasks = []
    for candidate in candidates:
        errors = validate_candidate(candidate)
        if errors:
            next_tasks.append({"candidate": candidate, "reason": "; ".join(errors)})
        else:
            blockers.append(candidate)
    return {"blockers": blockers, "next_tasks": next_tasks}


def _load_input(path: str) -> list:
    if path == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as e:
            raise BlockerCheckError(f"cannot read {path!r}: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BlockerCheckError(f"{path!r} is not valid JSON: {e}") from e

    if not isinstance(data, list):
        raise BlockerCheckError(f"{path!r} must contain a JSON array, got {type(data).__name__}")

    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="partition candidate blockers into blockers/next_tasks")
    ap.add_argument("--in", dest="in_path", required=True,
                     help="path to a JSON array of candidate blockers, or '-' for stdin")
    args = ap.parse_args(argv)

    try:
        candidates = _load_input(args.in_path)
    except BlockerCheckError as e:
        print(f"foundry_blocker_check: REFUSED — {e}", file=sys.stderr)
        return 2

    verdict = partition(candidates)
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
