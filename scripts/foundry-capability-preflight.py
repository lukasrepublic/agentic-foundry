#!/usr/bin/env python3
"""foundry-capability-preflight — checks, at arm/dispatch time, that every capability an atom's
contract (or a charter) declares is actually granted before the build starts
(feat-foundry-authorization-capability-preflight-at-dispatch, AC-CPD-1..9).

Measured: one adopter's sessions hit 44 permission-classifier blocks on verbs the operator had
already granted (including the operator's own `CronCreate`), another 192, a third 0 — same
operator, different `settings.json`. Every one surfaced hundreds of tool calls into a drive. This
CLI reads the declared `requires_capabilities` (R1's `standing-grants-as-policy` field, each a
native rule string `Tool(pattern)`) and checks each one is covered by an `allow` rule in the
effective settings, or an `automatic` grant in `.foundry/permissions.yaml` — fails fast, naming the
exact missing rule. It NEVER edits settings or the policy (read-only, the same posture
`foundry-permissions-compile.py --check` and `foundry_permission_floor.py` already carry).

Two mutually exclusive input modes:

  foundry-capability-preflight.py --contract <path-to-acceptance-contract.yaml>
  foundry-capability-preflight.py --charter <path-to-a-charter.md>            # "## Requires capabilities"

`--contract` reads the frozen contract's `requires_capabilities` list (schema/acceptance-
contract.schema.json, feat-foundry-contract-done-when-escalate-when). `--charter` reads a
charter-lane atom's own `## Requires capabilities` markdown section (a bullet list of the same
native rule strings) — ABSENT is not an error: a charter with no such section declares nothing to
preflight and reads as trivially `ok`. Both paths (and every per-atom ref the doctor resolves the
same way) must be CONFINED under the project dir (no `..` escape), a regular file, at most
`_MAX_FILE_BYTES` (AC-CPD-1, auth_seq 2).

Coverage (AC-CPD-2, auth_seq 2 — the PLATFORM's own matching, not the permission-floor's Bash-only
canonicalization): a declared capability counts as granted only when an `allow` rule (from
`.claude/settings.json`, `.claude/settings.local.json`, or the user-scope `~/.claude/settings.json`,
in that effective order) or an `automatic` grant (`.foundry/permissions.yaml`) COVERS it: the
rule's tool is equal AND its pattern is equal or is a PREFIX AT A TOKEN BOUNDARY — the rule's
pattern ends `<prefix>:*`, `<prefix> *`, `<prefix>*`, or `<prefix>**`, and the capability's pattern
starts with that literal `<prefix>` — for EVERY tool alike, Bash included. This deliberately does
NOT route through `foundry_permission_floor.covers`/`canonicalize`: that module folds a leading
interpreter word (`python3`, `bash`, `sh`) out of a Bash rule's reach for the doctor's own
permission-FLOOR comparison, which would make `Bash(python3:*)` read as covering every Bash
capability here — a real defect a security review caught (PR #176). `foundry_permission_floor` is
still reused for `load_settings_file` and the render-floor `sanitize` helper, never for coverage.

An `ask` rule never grants. A `deny` rule subtracts an otherwise-covering `allow`/`automatic` grant
in EITHER direction: the deny covers the capability (broad-or-equal), OR the capability covers the
deny (a narrower deny nested inside a broader requested capability, e.g. deny
`Bash(git push --force:*)` under capability `Bash(git push:*)` — the operator carved a sub-case out
on purpose, so the broad grant is not clean). Either direction reports `missing` with
`where: "denied"`.

A capability or rule pattern containing a control character (including newline), a backtick, `$(`,
or `;` is refused as an unreadable input (exit 2) rather than silently compared or echoed — and
every string this CLI DOES echo into the verdict (`capability`, `rule_to_add`, `grant_id`, each
precondition) is passed through `foundry_permission_floor.sanitize` first, so a render-hostile
byte sequence that slips past that refusal still cannot reach a terminal/handoff unmangled.

Exit codes (AC-CPD-1): 0 nothing missing; 3 something missing (verdict still printed); 2 an
unreadable input (a missing/malformed/out-of-bounds contract or charter, a malformed settings
file, `.foundry/permissions.yaml`, or a forbidden-character rule/capability string), naming it on
stdout as a JSON error object.

Out of scope (spec `## Out of scope / non-goals`): editing settings.json/permissions.yaml (the
preflight reports; the operator or `--write` acts); MCP tool rules and non-Bash tools beyond the
six the policy schema names; enforcing a grant's `preconditions` (surfaced as
`preconditions_unverified`, still the agent's job to verify by command before relying on the
grant — R3/R4).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_permission_floor as _pf  # noqa: E402  (load_settings_file + sanitize reuse)

try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("foundry-capability-preflight: PyYAML is required (pip install pyyaml)\n")
    raise

EXIT_OK = 0
EXIT_MISSING = 3
EXIT_UNREADABLE = 2

_MAX_FILE_BYTES = 1024 * 1024

# The three effective settings sources, in the order AC-CPD-1 fixes: the workspace's own two
# settings files, then the user-scope one. Read via the SAME loader `foundry_permission_floor`
# already uses (never re-implemented) — missing is fine, a malformed one is fail-closed (exit 2).
WORKSPACE_SETTINGS_LABEL = os.path.join(".claude", "settings.json")
WORKSPACE_LOCAL_SETTINGS_LABEL = os.path.join(".claude", "settings.local.json")
USER_SETTINGS_LABEL = "~/.claude/settings.json"

# AC-CPD-2: a `missing` entry caused by a deny (either direction) is a distinct `where`, so the
# operator/skill can tell "never granted" from "explicitly denied" without re-running the check.
WHERE_DENIED = "denied"

_RULE_RE = re.compile(r"^([A-Za-z0-9_-]+)\((.*)\)$", re.DOTALL)
_BARE_RULE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_CHARTER_SECTION_RE = re.compile(
    r"^##\s+Requires capabilities\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)
_CHARTER_BULLET_RE = re.compile(r"^[ \t]*[-*]\s+`?([A-Za-z0-9_-]+\([^)]*\)|[A-Za-z0-9_-]+)`?\s*$",
                                 re.MULTILINE)

# AC-CPD-2: refused outright, on a capability OR any rule pattern compared against one — a
# control character (0x00-0x1f, 0x7f — this range already covers newline/carriage-return), a
# backtick, a `$(` command-substitution opener, or a `;` chain separator.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_FORBIDDEN_SUBSTRINGS = ("`", "$(", ";")


class PreflightInputError(Exception):
    """AC-CPD-1: an unreadable/malformed/out-of-bounds input (contract, charter, a settings file,
    the permissions policy, or a forbidden-character rule/capability string) -- exit 2, naming it."""


# --------------------------------------------------------------------------------------------- #
# the render floor (AC-CPD-2's sanitise-on-echo requirement)
# --------------------------------------------------------------------------------------------- #


def _has_forbidden_chars(s):
    """AC-CPD-2: True for anything not safe to compare/echo -- a control character (newline
    included), a backtick, `$(`, or `;`."""
    if not isinstance(s, str):
        return True
    if _CONTROL_CHAR_RE.search(s):
        return True
    return any(tok in s for tok in _FORBIDDEN_SUBSTRINGS)


def _sanitize(s, cap=None):
    """Every string this CLI echoes into the verdict passes the floor's own render floor
    (`foundry_permission_floor.sanitize`) -- defense in depth alongside the refusal above, not a
    substitute for it. `cap` defaults to the floor's own per-LINE cap (200) for a single verdict
    field; a composed, multi-part CLI error message passes an explicit, larger `cap` so the render
    floor (control/zero-width stripping) never ALSO truncates the message mid-word."""
    if cap is None:
        return _pf.sanitize(s)
    return _pf.sanitize(s, cap=cap)


def _reject_forbidden(s, source):
    if _has_forbidden_chars(s):
        raise PreflightInputError(
            f"{source}: {_sanitize(s)!r} contains a control character, newline, backtick, '$(' "
            "or ';' -- refused"
        )


# --------------------------------------------------------------------------------------------- #
# rule parsing + coverage (AC-CPD-2)
# --------------------------------------------------------------------------------------------- #


def parse_rule(rule):
    """`Tool(pattern)` -> (tool, pattern); a bare `Tool` (no parens, e.g. `CronCreate`) ->
    (tool, None). Returns None for anything else."""
    if not isinstance(rule, str):
        return None
    m = _RULE_RE.match(rule)
    if m:
        return m.group(1), m.group(2)
    if _BARE_RULE_RE.match(rule):
        return rule, None
    return None


def _strip_prefix_marker(pattern):
    """AC-CPD-2: strip ONE of the platform's four token-boundary prefix markers from the end of
    `pattern` -- `:*`, `**`, ` *` (checked as their own two-character marker, longest first) or a
    bare trailing `*` -- returning (reach, is_prefix). `is_prefix` is False when none apply (an
    exact-match-only pattern)."""
    for marker in (":*", "**", " *"):
        if pattern.endswith(marker):
            return pattern[: -len(marker)], True
    if pattern.endswith("*"):
        return pattern[:-1], True
    return pattern, False


def _platform_covers(candidate_rule, capability_rule):
    """AC-CPD-2's coverage relation, used identically for every tool including Bash: same tool AND
    (pattern equal, OR the candidate's pattern is a prefix-at-a-token-boundary whose literal
    `<prefix>` the capability's pattern starts with). Never by substring, and never the permission
    floor's interpreter-word-as-blanket fold — `Bash(python3:*)`'s reach is the literal text
    `python3`, nothing more."""
    a = parse_rule(candidate_rule)
    b = parse_rule(capability_rule)
    if a is None or b is None:
        return False
    a_tool, a_pattern = a
    b_tool, b_pattern = b
    if a_tool != b_tool:
        return False
    if a_pattern is None or b_pattern is None:
        return a_pattern == b_pattern
    if a_pattern == b_pattern:
        return True
    reach, is_prefix = _strip_prefix_marker(a_pattern)
    if not is_prefix:
        return False
    return b_pattern.startswith(reach)


def rule_covers(candidate_rule, capability_rule, home=None):
    """Does `candidate_rule` (an allow/automatic/deny rule) cover `capability_rule` (a declared
    `requires_capabilities` entry)? `home` is accepted for call-site back-compat but unused —
    AC-CPD-2's platform matching is a pure string relation, no `~`/plugin-cache folding."""
    del home
    return _platform_covers(candidate_rule, capability_rule)


def _deny_subtracts(deny_rule, capability_rule):
    """AC-CPD-2: a deny subtracts an otherwise-covering allow/automatic grant when it covers the
    capability (broad-or-equal) OR lies WITHIN it (the capability covers the deny — a narrower
    deny carved out of a broader requested capability, e.g. deny `Bash(git push --force:*)` under
    capability `Bash(git push:*)`)."""
    return _platform_covers(deny_rule, capability_rule) or _platform_covers(capability_rule, deny_rule)


# --------------------------------------------------------------------------------------------- #
# path confinement (AC-CPD-1, auth_seq 2)
# --------------------------------------------------------------------------------------------- #


def _confine_path(path, project_dir, source_label):
    """AC-CPD-1: `path` (relative paths resolved against `project_dir`; an absolute path is
    accepted only if it already resolves inside it) must be CONFINED under `project_dir` (no `..`
    escape), a regular file, at most `_MAX_FILE_BYTES`. Returns the resolved real path. Raises
    PreflightInputError naming `path`, never a raw traceback, on any violation."""
    if not isinstance(path, str) or not path.strip():
        raise PreflightInputError(f"{source_label}: path must be a non-empty string")
    real_project = os.path.realpath(project_dir)
    candidate = path if os.path.isabs(path) else os.path.join(project_dir, path)
    real_candidate = os.path.realpath(candidate)
    if real_candidate != real_project and not real_candidate.startswith(real_project + os.sep):
        raise PreflightInputError(f"{path}: resolved path escapes the project dir (containment)")
    if not os.path.exists(real_candidate):
        raise PreflightInputError(f"{path} is missing")
    st = os.stat(real_candidate)
    if not stat.S_ISREG(st.st_mode):
        raise PreflightInputError(f"{path} is not a regular file")
    if st.st_size > _MAX_FILE_BYTES:
        raise PreflightInputError(f"{path} exceeds 1 MiB")
    return real_candidate


# --------------------------------------------------------------------------------------------- #
# input loaders — --contract / --charter (AC-CPD-1)
# --------------------------------------------------------------------------------------------- #


def _validate_capability_strings(items, source):
    for i, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise PreflightInputError(
                f"{source}: requires_capabilities[{i}] must be a non-empty string, got {item!r}"
            )
        _reject_forbidden(item, f"{source}: requires_capabilities[{i}]")
        if parse_rule(item) is None:
            raise PreflightInputError(
                f"{source}: requires_capabilities[{i}] {item!r} is not a native `Tool(pattern)` "
                "(or bare `Tool`) rule string"
            )


def load_contract_capabilities(path, project_dir):
    """Reads a frozen acceptance-contract.yaml's `requires_capabilities` list (schema/acceptance-
    contract.schema.json). Absent -> [] (nothing declared, back-compat). `path` is confined under
    `project_dir` first (AC-CPD-1)."""
    real_path = _confine_path(path, project_dir, "contract")
    try:
        with open(real_path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        raise PreflightInputError(f"{path} is unreadable: {e}") from e
    try:
        data = yaml.safe_load(raw)
    except Exception as e:
        raise PreflightInputError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PreflightInputError(f"{path}: contract root must be a mapping")
    rc = data.get("requires_capabilities", [])
    if not isinstance(rc, list):
        raise PreflightInputError(f"{path}: requires_capabilities must be a list")
    _validate_capability_strings(rc, path)
    return list(rc)


def load_charter_capabilities(path, project_dir):
    """Reads a charter's `## Requires capabilities` markdown section (a bullet list of native
    rule strings). Absent section is NOT an error for the file itself -- but a missing/out-of-
    bounds file is (AC-CPD-1's "unreadable input"). `path` is confined under `project_dir` first."""
    real_path = _confine_path(path, project_dir, "charter")
    try:
        with open(real_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        raise PreflightInputError(f"{path} is unreadable: {e}") from e
    m = _CHARTER_SECTION_RE.search(text)
    if not m:
        return []
    items = _CHARTER_BULLET_RE.findall(m.group(1))
    _validate_capability_strings(items, path)
    return items


# --------------------------------------------------------------------------------------------- #
# effective settings + automatic grants (AC-CPD-1)
# --------------------------------------------------------------------------------------------- #


def _load_compile_module():
    """Lazy-imports `foundry-permissions-compile.py` (hyphenated filename) for `load_policy` /
    `PolicyError` reuse -- never re-implemented (mirrors the doctor's own lazy-import pattern for
    a hyphenated sibling script)."""
    path = os.path.join(HERE, "foundry-permissions-compile.py")
    spec = importlib.util.spec_from_file_location("foundry_permissions_compile_preflight", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _effective_allow_and_deny(project_dir, home=None):
    """[(rule, where), ...] for allow and deny, unioned across the three effective settings
    sources in AC-CPD-1's fixed order. `ask` rules are never collected (AC-CPD-2: ask never
    grants). Raises PreflightInputError naming the file on an unreadable/malformed source, or a
    forbidden-character rule."""
    home = home or os.path.expanduser("~")
    sources = (
        (os.path.join(project_dir, ".claude", "settings.json"), WORKSPACE_SETTINGS_LABEL),
        (os.path.join(project_dir, ".claude", "settings.local.json"), WORKSPACE_LOCAL_SETTINGS_LABEL),
        (os.path.join(home, ".claude", "settings.json"), USER_SETTINGS_LABEL),
    )
    allow, deny = [], []
    for path, label in sources:
        result = _pf.load_settings_file(path)
        if result["status"] == "unreadable":
            raise PreflightInputError(f"{label} is unreadable/invalid")
        if result["status"] == "absent":
            continue
        for rule in result["rules"]["allow"]:
            _reject_forbidden(rule, f"{label} (allow)")
            allow.append((rule, label))
        for rule in result["rules"]["deny"]:
            _reject_forbidden(rule, f"{label} (deny)")
            deny.append((rule, label))
    return allow, deny


def _automatic_grants(project_dir):
    """[{"rule", "grant_id", "preconditions"}, ...] for every `automatic` grant in
    `.foundry/permissions.yaml`. A missing policy file is NOT an error (not every atom needs one);
    a present-but-malformed one is, and so is a forbidden-character rule (AC-CPD-1/-2)."""
    pc = _load_compile_module()
    try:
        grants = pc.load_policy(project_dir)
    except pc.PolicyError as e:
        if str(e).rstrip().endswith("is missing"):
            return []
        raise PreflightInputError(str(e)) from e
    out = []
    for g in grants:
        if g.get("mode") != "automatic":
            continue
        rule = f"{g['tool']}({g['pattern']})"
        _reject_forbidden(rule, f".foundry/permissions.yaml grant {g.get('id')!r}")
        out.append({
            "rule": rule,
            "grant_id": g["id"],
            "preconditions": list(g.get("preconditions", [])),
        })
    return out


# --------------------------------------------------------------------------------------------- #
# the verdict (AC-CPD-1, AC-CPD-2)
# --------------------------------------------------------------------------------------------- #


def preflight(capabilities, project_dir, home=None):
    """The whole check. Returns the AC-CPD-1 verdict dict:
    {"status": "ok"|"missing", "missing": [...], "preconditions_unverified": [...]}. Every string
    placed in the returned dict has passed `_sanitize` (AC-CPD-2)."""
    allow, deny = _effective_allow_and_deny(project_dir, home=home)
    automatic = _automatic_grants(project_dir)

    missing = []
    preconditions_unverified = []

    for cap in capabilities:
        granting = [rule for rule, _where in allow if rule_covers(rule, cap)]
        granting += [g["rule"] for g in automatic if rule_covers(g["rule"], cap)]

        denied_by = [rule for rule, _where in deny if _deny_subtracts(rule, cap)]
        if denied_by:
            missing.append({
                "capability": _sanitize(cap),
                "rule_to_add": _sanitize(cap),
                "where": WHERE_DENIED,
            })
            continue

        if not granting:
            missing.append({
                "capability": _sanitize(cap),
                "rule_to_add": _sanitize(cap),
                "where": _sanitize(WORKSPACE_SETTINGS_LABEL),
            })
            continue

        for g in automatic:
            if g["preconditions"] and rule_covers(g["rule"], cap):
                preconditions_unverified.append({
                    "capability": _sanitize(cap),
                    "grant_id": _sanitize(g["grant_id"]),
                    "preconditions": [_sanitize(p) for p in g["preconditions"]],
                })

    status = "missing" if missing else "ok"
    return {"status": status, "missing": missing, "preconditions_unverified": preconditions_unverified}


# --------------------------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------------------------- #


def _project_dir(root):
    return root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="foundry-capability-preflight — check an atom's declared requires_capabilities "
                    "are granted before dispatch"
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--contract", default=None, help="path to a frozen acceptance-contract.yaml")
    src.add_argument("--charter", default=None,
                     help="path to a charter .md (reads its ## Requires capabilities section)")
    ap.add_argument("--root", default=None, help="project dir override (default: $CLAUDE_PROJECT_DIR or cwd)")
    ap.add_argument("--home", default=None, help="home dir override (test-only; default: os.path.expanduser('~'))")
    args = ap.parse_args(argv)

    project_dir = _project_dir(args.root)

    try:
        if args.contract:
            capabilities = load_contract_capabilities(args.contract, project_dir)
        else:
            capabilities = load_charter_capabilities(args.charter, project_dir)
        verdict = preflight(capabilities, project_dir, home=args.home)
    except PreflightInputError as e:
        print(json.dumps({"status": "error", "error": _sanitize(str(e), cap=2000)}, indent=2))
        return EXIT_UNREADABLE

    print(json.dumps(verdict, indent=2))
    return EXIT_OK if verdict["status"] == "ok" else EXIT_MISSING


if __name__ == "__main__":
    sys.exit(main())
