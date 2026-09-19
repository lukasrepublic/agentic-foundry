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
preflight and reads as trivially `ok`.

Coverage (AC-CPD-2): a declared capability counts as granted only when an `allow` rule (from
`.claude/settings.json`, `.claude/settings.local.json`, or the user-scope `~/.claude/settings.json`,
in that effective order) or an `automatic` grant (`.foundry/permissions.yaml`) COVERS it:
  - for a `Bash(...)` capability: `scripts.foundry_permission_floor.covers` (the floor's own
    canonical/prefix rule — interpreter-word drop, ~/`$HOME` fold, plugin-cache-version fold, then
    prefix-reach comparison);
  - for every other tool: the rule's tool is equal AND its pattern is equal, or the rule's pattern
    is a glob prefix (`<prefix>*` or `<prefix>**`) whose literal prefix the capability's pattern
    starts with — NEVER by substring.
An `ask` rule never grants. A `deny` rule that covers the same capability (same coverage relation,
either direction) subtracts an otherwise-covering `allow`/`automatic` grant.

Exit codes (AC-CPD-1): 0 nothing missing; 3 something missing (verdict still printed); 2 an
unreadable input (a missing/malformed contract, charter, settings file, or `.foundry/
permissions.yaml`), naming it on stdout as a JSON error object.

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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_permission_floor as _pf  # noqa: E402  (load_settings_file + covers reuse, AC-CPD-2)

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

_RULE_RE = re.compile(r"^([A-Za-z0-9_-]+)\((.*)\)$", re.DOTALL)
_BARE_RULE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_CHARTER_SECTION_RE = re.compile(
    r"^##\s+Requires capabilities\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)
_CHARTER_BULLET_RE = re.compile(r"^[ \t]*[-*]\s+`?([A-Za-z0-9_-]+\([^)]*\)|[A-Za-z0-9_-]+)`?\s*$",
                                 re.MULTILINE)


class PreflightInputError(Exception):
    """AC-CPD-1: an unreadable/malformed input (contract, charter, a settings file, or the
    permissions policy) -- exit 2, naming it."""


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


def _non_bash_covers(candidate_rule, capability_rule):
    """AC-CPD-2 non-Bash coverage: same tool AND (pattern equal, OR the candidate's pattern is a
    glob prefix `<prefix>*`/`<prefix>**` and the capability's pattern starts with that literal
    prefix). Never by substring. Also handles the bare-tool shape (`CronCreate`, pattern None):
    only an exact bare-tool match covers a bare-tool capability."""
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
    if a_pattern.endswith("**"):
        return b_pattern.startswith(a_pattern[:-2])
    if a_pattern.endswith("*"):
        return b_pattern.startswith(a_pattern[:-1])
    return False


def rule_covers(candidate_rule, capability_rule, home=None):
    """Does `candidate_rule` (an allow/automatic/deny rule) cover `capability_rule` (a declared
    `requires_capabilities` entry)? `Bash(...)` capabilities route through
    `foundry_permission_floor.covers` (the floor's own canonical/prefix rule, AC-CPD-2); every
    other tool routes through `_non_bash_covers`. Used symmetrically for allow/automatic coverage
    AND for deny subtraction (AC-CPD-2's "a deny on the same rule wins")."""
    parsed_cap = parse_rule(capability_rule)
    if parsed_cap is None:
        return False
    cap_tool, _cap_pattern = parsed_cap
    if cap_tool == "Bash":
        return bool(_pf.covers(candidate_rule, capability_rule, home=home))
    return _non_bash_covers(candidate_rule, capability_rule)


# --------------------------------------------------------------------------------------------- #
# input loaders — --contract / --charter (AC-CPD-1)
# --------------------------------------------------------------------------------------------- #


def _validate_capability_strings(items, source):
    for i, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise PreflightInputError(
                f"{source}: requires_capabilities[{i}] must be a non-empty string, got {item!r}"
            )
        if parse_rule(item) is None:
            raise PreflightInputError(
                f"{source}: requires_capabilities[{i}] {item!r} is not a native `Tool(pattern)` "
                "(or bare `Tool`) rule string"
            )


def load_contract_capabilities(path):
    """Reads a frozen acceptance-contract.yaml's `requires_capabilities` list (schema/acceptance-
    contract.schema.json). Absent -> [] (nothing declared, back-compat)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise PreflightInputError(f"{path} is missing")
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


def load_charter_capabilities(path):
    """Reads a charter's `## Requires capabilities` markdown section (a bullet list of native
    rule strings). Absent section, or an absent file's section-worth of nothing, is NOT an error
    for the file itself -- but a missing file is (AC-CPD-1's "unreadable input")."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise PreflightInputError(f"{path} is missing")
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
    grants). Raises PreflightInputError naming the file on an unreadable/malformed source."""
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
            allow.append((rule, label))
        for rule in result["rules"]["deny"]:
            deny.append((rule, label))
    return allow, deny


def _automatic_grants(project_dir):
    """[{"rule", "grant_id", "preconditions"}, ...] for every `automatic` grant in
    `.foundry/permissions.yaml`. A missing policy file is NOT an error (not every atom needs one);
    a present-but-malformed one is (AC-CPD-1)."""
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
        out.append({
            "rule": f"{g['tool']}({g['pattern']})",
            "grant_id": g["id"],
            "preconditions": list(g.get("preconditions", [])),
        })
    return out


# --------------------------------------------------------------------------------------------- #
# the verdict (AC-CPD-1, AC-CPD-2)
# --------------------------------------------------------------------------------------------- #


def preflight(capabilities, project_dir, home=None):
    """The whole check. Returns the AC-CPD-1 verdict dict:
    {"status": "ok"|"missing", "missing": [...], "preconditions_unverified": [...]}."""
    allow, deny = _effective_allow_and_deny(project_dir, home=home)
    automatic = _automatic_grants(project_dir)

    missing = []
    preconditions_unverified = []

    for cap in capabilities:
        granting = [rule for rule, _where in allow if rule_covers(rule, cap, home=home)]
        granting += [g["rule"] for g in automatic if rule_covers(g["rule"], cap, home=home)]

        if not granting:
            missing.append({
                "capability": cap,
                "rule_to_add": cap,
                "where": WORKSPACE_SETTINGS_LABEL,
            })
            continue

        denied_by = [rule for rule, _where in deny if rule_covers(rule, cap, home=home)]
        if denied_by:
            missing.append({
                "capability": cap,
                "rule_to_add": cap,
                "where": WORKSPACE_SETTINGS_LABEL,
            })
            continue

        for g in automatic:
            if g["preconditions"] and rule_covers(g["rule"], cap, home=home):
                preconditions_unverified.append({
                    "capability": cap,
                    "grant_id": g["grant_id"],
                    "preconditions": list(g["preconditions"]),
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
            capabilities = load_contract_capabilities(args.contract)
        else:
            capabilities = load_charter_capabilities(args.charter)
        verdict = preflight(capabilities, project_dir, home=args.home)
    except PreflightInputError as e:
        print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return EXIT_UNREADABLE

    print(json.dumps(verdict, indent=2))
    return EXIT_OK if verdict["status"] == "ok" else EXIT_MISSING


if __name__ == "__main__":
    sys.exit(main())
