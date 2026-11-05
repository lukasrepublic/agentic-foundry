#!/usr/bin/env python3
"""foundry_blocker_check — "needs the operator" is a claim that needs an attempt (operating-model rules
B3, B4, C1; AC-OM-8). Re-extracted from the 1.18.5 lint and extended.

A deck tick's Blockers section used to be free prose, so anything could be reported as a blocker and the
operator became the inbox. This CLI is the lint a tick runs BEFORE it reports: it reads a JSON list of
candidate blockers (schema/blocker.schema.json) and partitions them into `blockers` and `next_tasks`
(each demotion carries its reason). A candidate is a blocker only when it carries:

  claim, evidence[] (>=1), attempted[] (>=1, each {command, outcome}), control (the call that would have
  failed differently), why_operator in {external-provisioning, credential-step, no-consensus-after-research,
  priced-ask}; handoff {cwd, command, why, expect} for credential-step / external-provisioning / priced-ask;
  priced_ask {purpose, cost_breakdown[>=1 {item, monthly_cost, currency}]} for priced-ask; optionally
  `questions` {capability, authority, judgement} (the three separate questions of rule B4).

Demotions to next_tasks (AC-OM-8): no `attempted`, no `control`, a credential-step that never tried the
credential a running job already holds (only an `attempted` entry with used_running_job_credential: true counts;
free text is never read), and any other schema/shape failure (a chained handoff command, a missing field).
REFUSED (exit 2, nothing on stdout): input that is not a JSON array, and a priced-ask whose cost_breakdown is
missing or empty (a priced ask with no price is malformed, not merely incomplete).

The JSON-Schema check runs when `jsonschema` is importable; a hand-rolled structural floor ALWAYS runs, so
the lint never degrades to a no-op, and it is the one that NAMES the offending token. No network.

Usage:
    foundry_blocker_check.py --in <path-to-json-file-or-'-'-for-stdin>
Output (exit 0): {"blockers": [...], "next_tasks": [{"candidate": {...}, "reason": "..."}]}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys

_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = os.path.join(os.path.dirname(_HERE_DIR), "schema", "blocker.schema.json")

# The closed set of reasons only the operator can act (rules B3/B4/C1). Mirrors schema/blocker.schema.json.
_WHY_OPERATOR_SET = {"external-provisioning", "credential-step", "no-consensus-after-research", "priced-ask"}

_REQUIRED_FIELDS = ("claim", "evidence", "attempted", "control", "why_operator", "questions")
_ALLOWED_FIELDS = _REQUIRED_FIELDS + ("handoff", "priced_ask", "options", "recommendation", "one_word_ask")
_QUESTION_FIELDS = ("capability", "authority", "judgement")

# AC-OHS-2: these two why_operator members name a step the AGENT structurally cannot take itself
# -- a credential the agent does not hold, or provisioning outside its reach -- so a candidate
# carrying one MUST hand the operator the runnable command as data, not leave it to be inferred
# from `claim`/`evidence` prose.
_WHY_OPERATOR_REQUIRES_HANDOFF = {"credential-step", "external-provisioning", "priced-ask"}

_HANDOFF_REQUIRED_FIELDS = ("cwd", "command", "why", "expect")
# AC-OHS-1: ONE bare command. These two regexes are the SINGLE definition: schema/blocker.schema.json carries
# the same strings as `not.pattern` (a test asserts byte equality), and the floor below applies them directly,
# so the verdict never depends on whether `jsonschema` is installed.
#   chain/redirect/substitution: & (also &&) ; | < (also <( ) > (also >( and >>) CR LF $( and backtick
HANDOFF_CHAIN_PATTERN = r"[&;|<>\r\n`]|\$\("
#   top-level shell-altering words, after optional NAME=value assignments and command/builtin prefixes:
#   trap, exec, exit, set -e / -ex / -o errexit
HANDOFF_WORD_PATTERN = (r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:(?:command|builtin)\s+)*"
                        r"(?:trap|exec|exit|set\s+(?:-[A-Za-z]*e[A-Za-z]*|-[A-Za-z]*o\s+errexit))(?:\s|$)")
_HANDOFF_CHAIN_RE = re.compile(HANDOFF_CHAIN_PATTERN)
_HANDOFF_WORD_RE = re.compile(HANDOFF_WORD_PATTERN)


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
            m = _HANDOFF_CHAIN_RE.search(command)
            if m:
                tok = {"\n": "newline", "\r": "carriage return"}.get(m.group(0), m.group(0))
                errs.append(f"handoff.command contains {tok!r} — one bare command only: no chaining, "
                            "background, redirection, process or command substitution")
            m = _HANDOFF_WORD_RE.search(command)
            if m:
                errs.append(f"handoff.command starts with {m.group(0).strip()!r} — top-level trap/exec/exit/"
                            "set -e/set -o errexit is refused (it could alter or exit the operator's own shell)")

    if "why" in handoff and not (isinstance(handoff.get("why"), str) and handoff.get("why")):
        errs.append("handoff.why must be a non-empty string")

    if "expect" in handoff and not (isinstance(handoff.get("expect"), str) and handoff.get("expect")):
        errs.append("handoff.expect must be a non-empty string")

    return errs


def _tried_running_job_credential(attempted) -> bool:
    """Only the explicit flag counts. Free text is not read: "never tried the job token" must not pass."""
    return any(isinstance(a, dict) and a.get("used_running_job_credential") is True for a in attempted)


def _cost_breakdown_empty(candidate) -> bool:
    pa = candidate.get("priced_ask")
    cb = pa.get("cost_breakdown") if isinstance(pa, dict) else None
    return not (isinstance(cb, list) and len(cb) >= 1)


def _priced_ask_errors(pa) -> list[str]:
    if not isinstance(pa, dict):
        return ["priced_ask must be an object"]
    errs: list[str] = []
    extra = sorted(set(pa) - {"purpose", "cost_breakdown"})
    if extra:
        errs.append(f"priced_ask has unknown field(s) {extra!r}")
    if not (isinstance(pa.get("purpose"), str) and pa.get("purpose").strip()):
        errs.append("priced_ask.purpose must be a non-empty string")
    cb = pa.get("cost_breakdown")
    if not isinstance(cb, list) or not cb:
        errs.append("priced_ask.cost_breakdown must be a non-empty list")
        return errs
    for i, row in enumerate(cb):
        if not isinstance(row, dict):
            errs.append(f"priced_ask.cost_breakdown[{i}] must be an object")
            continue
        if set(row) != {"item", "monthly_cost", "currency"}:
            errs.append(f"priced_ask.cost_breakdown[{i}] must have exactly item, monthly_cost, currency")
        if not (isinstance(row.get("item"), str) and row.get("item")):
            errs.append(f"priced_ask.cost_breakdown[{i}].item must be a non-empty string")
        mc = row.get("monthly_cost")
        if isinstance(mc, bool) or not isinstance(mc, (int, float)) or mc < 0:
            errs.append(f"priced_ask.cost_breakdown[{i}].monthly_cost must be a number >= 0")
        if not (isinstance(row.get("currency"), str) and row.get("currency")):
            errs.append(f"priced_ask.cost_breakdown[{i}].currency must be a non-empty string")
    return errs


class BlockerCheckError(Exception):
    """Refused input: not valid JSON, not a JSON array, or a priced-ask with no cost breakdown (exit 2)."""


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

    ev = candidate.get("evidence")
    if "evidence" in candidate:
        if not isinstance(ev, list) or len(ev) < 1:
            errs.append("evidence must be a non-empty list")
        else:
            for i, item in enumerate(ev):
                if not isinstance(item, str) or not item:
                    errs.append(f"evidence[{i}] must be a non-empty string")

    if "attempted" in candidate:
        att = candidate.get("attempted")
        if not isinstance(att, list) or len(att) < 1:
            errs.append("no attempted: attempted must be a non-empty list of {command, outcome} "
                        "(a 'needs the operator' claim needs an attempt)")
        else:
            for i, item in enumerate(att):
                if not isinstance(item, dict):
                    errs.append(f"attempted[{i}] must be an object with command and outcome")
                    continue
                extra = sorted(set(item) - {"command", "outcome", "used_running_job_credential"})
                if extra:
                    errs.append(f"attempted[{i}] has unknown field(s) {extra!r}")
                for f in ("command", "outcome"):
                    if not (isinstance(item.get(f), str) and item.get(f)):
                        errs.append(f"attempted[{i}].{f} must be a non-empty string")
                if "used_running_job_credential" in item and not isinstance(item["used_running_job_credential"], bool):
                    errs.append(f"attempted[{i}].used_running_job_credential must be a boolean")

    if "control" in candidate:
        ctl = candidate.get("control")
        if not (isinstance(ctl, str) and ctl.strip()):
            errs.append("no control: control must be a non-empty string naming the call that would have "
                        "failed differently")

    if "questions" in candidate:
        q = candidate.get("questions")  # presence is enforced by _REQUIRED_FIELDS
        if not isinstance(q, dict):
            errs.append("questions must be an object")
        else:
            extra = sorted(set(q) - set(_QUESTION_FIELDS))
            if extra:
                errs.append(f"questions has unknown field(s) {extra!r}")
            for f in _QUESTION_FIELDS:
                if not (isinstance(q.get(f), str) and q.get(f).strip()):
                    errs.append(f"questions.{f} must be a non-empty string")

    for f in ("recommendation", "one_word_ask"):
        if f in candidate and not (isinstance(candidate[f], str) and candidate[f].strip()):
            errs.append(f"{f} must be a non-empty string")
    if "options" in candidate and not (isinstance(candidate["options"], list) and len(candidate["options"]) >= 2
                                       and all(isinstance(o, str) and o for o in candidate["options"])):
        errs.append("options must be a list of at least 2 non-empty strings")

    if "priced_ask" in candidate:
        errs.extend(_priced_ask_errors(candidate.get("priced_ask")))

    if "why_operator" in candidate:
        wo = candidate.get("why_operator")
        if not isinstance(wo, str) or wo not in _WHY_OPERATOR_SET:
            errs.append(
                f"why_operator {wo!r} not in the closed escalate_when set {sorted(_WHY_OPERATOR_SET)}"
            )
        # AC-OHS-2: credential-step / external-provisioning REQUIRE a handoff.
        elif wo == "no-consensus-after-research":
            opts = candidate.get("options")
            if not (isinstance(opts, list) and len(opts) >= 2):
                errs.append("why_operator 'no-consensus-after-research' requires options (at least 2)")
            for f in ("recommendation", "one_word_ask"):
                if f not in candidate:
                    errs.append(f"why_operator 'no-consensus-after-research' requires {f}")
        elif wo == "priced-ask" and "priced_ask" not in candidate:
            errs.append("why_operator 'priced-ask' requires priced_ask {purpose, cost_breakdown}")
        elif wo in _WHY_OPERATOR_REQUIRES_HANDOFF and "handoff" not in candidate:
            errs.append(
                f"why_operator {wo!r} requires handoff (the operator needs cwd/command/why/expect, "
                "not prose, to act on a credential or provisioning step)"
            )

    if candidate.get("why_operator") == "credential-step" and isinstance(candidate.get("attempted"), list) \
            and candidate["attempted"] and not _tried_running_job_credential(candidate["attempted"]):
        errs.append("credential-step without an attempt that used the credential a running job already holds "
                    "(set attempted[].used_running_job_credential to true on the attempt that used it)")

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


# S5 rule 8: a deck stall class. It is not an operator-only claim, so it has its own shape: {stall_class, evidence}
# with `evidence` = the last `deck.stall_after_ticks` cost-log lines (default 3), all counted (no ci-pending tag),
# each with N > 0 and K = 0. A valid one is reported as a blocker; an invalid one is demoted with its reason.
_STALL_CLASSES = ("cost-without-progress",)
_COST_LOG_LINE = re.compile(
    r"^\S+ agents started (\d+) \| longest agent (\d+) calls \| items closed (\d+)( ci-pending)?$")


def _stall_errors(candidate) -> list[str]:
    errs: list[str] = []
    unknown = sorted(set(candidate) - {"stall_class", "evidence", "claim"})
    if unknown:
        errs.append(f"unknown field(s) {unknown!r} on a stall candidate")
    ev = candidate.get("evidence")
    if not isinstance(ev, list) or len(ev) < 3:
        return errs + ["stall evidence must be a list of at least 3 cost-log lines"]
    for i, line in enumerate(ev):
        m = _COST_LOG_LINE.match(line) if isinstance(line, str) else None
        if not m:
            errs.append(f"evidence[{i}] is not a cost-log line")
        elif m.group(4):
            errs.append(f"evidence[{i}] is ci-pending and does not count")
        elif int(m.group(1)) == 0 or int(m.group(3)) != 0:
            errs.append(f"evidence[{i}] does not have N > 0 and K = 0")
    return errs


def partition(candidates: list) -> dict:
    """AC-BRE-2/AC-OM-8: partition a list of candidate blockers into `blockers` (schema-valid, reported
    unchanged) and `next_tasks` (invalid, each paired with the reason it was demoted)."""
    refused: list[str] = []
    verdicts: list[list[str]] = []
    for i, c in enumerate(candidates):
        if isinstance(c, dict) and "stall_class" in c:
            if c.get("stall_class") in _STALL_CLASSES:
                verdicts.append(_stall_errors(c))
            else:
                verdicts.append([f"unknown stall_class {c.get('stall_class')!r}"])
            continue
        if isinstance(c, dict) and c.get("why_operator") == "priced-ask" and _cost_breakdown_empty(c):
            refused.append(f"index {i}: priced-ask with an empty or missing priced_ask.cost_breakdown "
                           "(a priced ask needs its price; rule C1)")
            verdicts.append([])
            continue
        try:
            verdicts.append(validate_candidate(c))
        except Exception as e:  # noqa: BLE001 — one malformed candidate must not take the batch down unannounced
            refused.append(f"index {i}: cannot be validated ({type(e).__name__}: {e})")
            verdicts.append([])
    if refused:
        raise BlockerCheckError("batch refused, every malformed candidate listed: " + "; ".join(refused))
    blockers = []
    next_tasks = []
    for candidate, errors in zip(candidates, verdicts):
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

    try:
        verdict = partition(candidates)
    except BlockerCheckError as e:
        print(f"foundry_blocker_check: REFUSED — {e}", file=sys.stderr)
        return 2
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
