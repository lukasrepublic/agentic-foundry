#!/usr/bin/env python3
"""foundry-permissions-compile — compiles `.foundry/permissions.yaml` into the native
`Tool(pattern)` permission rules Claude Code already enforces
(feat-foundry-authorization-standing-grants-as-policy, AC-SGP-1..4/7).

The operator's standing grants ("proceed on green CI", "drive this forward", ...) live today in
transcripts and memory files, so every session re-asks. This atom gives them ONE file the operator
edits: each grant is `automatic` (compiles to one `permissions.allow` rule) or `approval_required`
(compiles to NO settings rule — v1.18.0, AC-V118A-3). Grants only ever WIDEN: an `ask` rule outranks
`allow` and auto mode in Claude Code, so compiling `approval_required` to `ask` turned a grant into a
prompt the operator never asked for (measured on an adopter workspace, 2026-09-25). An
`approval_required` grant is documentation for the agent's own loop — the command keeps the
session's normal permission mode. The two self-guard deny rules this compiler used to place on the
policy file (AC-SGP-4) are retired (operator decision 2026-09-25): `--write` removes them and every
`ask` rule it previously compiled. The compiler writes
NO policy engine of our own — it derives the native rule set and reconciles it into
`.claude/settings.json`, recording exactly what it added in a sidecar
(`.claude/foundry-permissions.compiled.json`) so a later `--write` can remove exactly what it
previously added and nothing the operator wrote by hand (AC-SGP-3).

Two mutually exclusive modes (AC-SGP-7), modelled on `foundry-config.py`'s `--root` override
convention:

  foundry-permissions-compile.py --check [--root PATH]   # read-only; exit 0/2/3, never writes
  foundry-permissions-compile.py --write [--root PATH]   # reconciles .claude/settings.json + sidecar

Exit codes (AC-SGP-2):
  0  --check: the derived rule set agrees with the sidecar AND `.claude/settings.json`.
  2  a missing or schema-invalid `.foundry/permissions.yaml` (both modes) -- the schema error is
     named on stderr/stdout.
  3  --check only: drift -- every missing, extra, or moved rule is named.

YAML parsing uses PyYAML, the same required dependency every other gate script in this repo
already carries (`scripts/foundry_contract.py`, `scripts/foundry-doctor.py`) -- AC-SGP-7's
"stdlib-only" is read the same way `foundry_contract.py`'s own docstring reads it: no NEW
third-party dependency is introduced, and JSON-Schema validation
(`schema/permissions.schema.json`) is OPPORTUNISTIC (skipped, not silently trusted, when
`jsonschema` is absent) with a hand-rolled structural floor underneath it that always runs --
the identical pattern `foundry_contract.py` uses for `acceptance-contract.yaml`.

Reuses `scripts.foundry_permission_floor.load_settings_file` for a bounded, exception-tolerant
read of `.claude/settings.json` -- never re-implements that parsing. Rule *canonicalisation*
(the ask/allow-direction fold `foundry_permission_floor.canonicalize` performs for the doctor's
Bash-script floor) does not apply here: every rule this compiler ever writes or compares is one it
derived itself, verbatim `<tool>(<pattern>)`, so comparison is exact-string membership -- folding
would only reintroduce the re-implementation this atom is told not to do.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_permission_floor as _pf  # noqa: E402  (load_settings_file reuse, AC-SGP-2)

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("foundry-permissions-compile: PyYAML is required (pip install pyyaml)\n")
    raise

SCHEMA_VERSION = 1
TOOLS = ("Bash", "Edit", "Write", "Read", "WebFetch", "Agent")
MODES = ("automatic", "approval_required")
PRECONDITIONS = (
    "ci-green",
    "security-reviewed-label",
    "spec-authorized",
    "charter-committed",
    "worktree-clean",
    "branch-up-to-date",
)
_ID_RE = __import__("re").compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_BLANKET_PATTERN_RE = __import__("re").compile(r"^[\s*:?/.]*$")

POLICY_REL = os.path.join(".foundry", "permissions.yaml")
SETTINGS_REL = os.path.join(".claude", "settings.json")
SIDECAR_REL = os.path.join(".claude", "foundry-permissions.compiled.json")
_MAX_FILE_BYTES = 1024 * 1024

# Exit codes (AC-SGP-2)
EXIT_OK = 0
EXIT_MISSING_OR_INVALID = 2
EXIT_DRIFT = 3


class PolicyError(Exception):
    """A missing or schema-invalid permissions.yaml (AC-SGP-2 exit 2)."""


# --------------------------------------------------------------------------------------------- #
# load + validate (hand-rolled floor always runs; jsonschema opportunistic on top, AC-SGP-1)
# --------------------------------------------------------------------------------------------- #


def _jsonschema_available():
    try:
        import jsonschema  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def _schema_path():
    return os.path.join(HERE, "..", "schema", "permissions.schema.json")


def _jsonschema_check(data):
    """Opportunistic. Returns [] when jsonschema is unavailable (the hand-rolled floor below
    still runs) -- same degrade-loudly pattern as foundry_contract.py's _jsonschema_check."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    try:
        with open(_schema_path(), encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:  # type: ignore
        return [f"schema: {e.message} (at {'/'.join(str(p) for p in e.absolute_path)})"]
    except Exception as e:  # pragma: no cover
        return [f"schema: could not validate ({e})"]
    return []


def _structural_check(data):
    """Hand-rolled floor -- runs regardless of jsonschema availability (UL-0011 pattern)."""
    errors = []
    if not isinstance(data, dict):
        return ["permissions.yaml root must be a mapping"]
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}, got {data.get('schema_version')!r}")
    grants = data.get("grants")
    if not isinstance(grants, list):
        errors.append("grants must be a list")
        grants = []
    seen_ids = set()
    for i, g in enumerate(grants):
        if not isinstance(g, dict):
            errors.append(f"grants[{i}] must be a mapping")
            continue
        allowed_keys = {"id", "tool", "pattern", "mode", "preconditions", "note"}
        extra = set(g.keys()) - allowed_keys
        if extra:
            errors.append(f"grants[{i}] has unknown field(s): {sorted(extra)}")
        gid = g.get("id")
        if not isinstance(gid, str) or not gid or not _ID_RE.match(gid):
            errors.append(f"grants[{i}].id must be a non-empty slug, got {gid!r}")
        elif gid in seen_ids:
            errors.append(f"duplicate grant id: {gid!r}")
        else:
            seen_ids.add(gid)
        tool = g.get("tool")
        if tool not in TOOLS:
            errors.append(f"grants[{i}].tool must be one of {TOOLS}, got {tool!r}")
        pattern = g.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"grants[{i}].pattern must be a non-empty string, got {pattern!r}")
        elif _BLANKET_PATTERN_RE.match(pattern):
            # PR #165 security review: a wildcard-only body compiles to a blanket rule (`Bash(:*)`,
            # `Edit(**)`) -- breadth is an operator decision, but a blanket is never a "grant".
            errors.append(f"grants[{i}].pattern {pattern!r} is a blanket wildcard -- name the command/path")
        mode = g.get("mode")
        if mode not in MODES:
            errors.append(f"grants[{i}].mode must be one of {MODES}, got {mode!r}")
        pre = g.get("preconditions", [])
        if "preconditions" in g:
            if not isinstance(pre, list) or any(p not in PRECONDITIONS for p in pre):
                errors.append(f"grants[{i}].preconditions must be a subset of {PRECONDITIONS}")
        if "note" in g and not isinstance(g.get("note"), str):
            errors.append(f"grants[{i}].note must be a string")
    return errors


def _updater_cmd():
    """The updater pinned to the version this plugin ships with (ER #228: a bare `npx <pkg>` can run
    a stale cached copy). Falls back to `@latest`, which still forces a registry check."""
    try:
        pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli-update", "package.json")
        with open(pkg, encoding="utf-8") as fh:
            v = json.load(fh).get("version")
        if isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+", v):
            return f"npx update-agentic-workspace@{v}"
    except (OSError, ValueError):
        pass
    return "npx update-agentic-workspace@latest"


def _with_remedy(msg):
    """permissions-scaffold (ER #215, AC-PSC-4): an absent policy names its remedy at the CLI
    boundary, so the exception text foundry-capability-preflight.py classifies on stays exact."""
    if msg.rstrip().endswith("is missing"):
        return msg + (f" — seed it: `{_updater_cmd()}` writes a starter, or copy "
                      "context/permissions-template.yaml from the plugin")
    return msg


def load_policy(project_dir):
    """Returns the parsed+validated grants list. Raises PolicyError (AC-SGP-2 exit 2) on any
    missing-file or schema/structural problem, naming the error."""
    path = os.path.join(project_dir, POLICY_REL)
    if not os.path.isfile(path):
        # The exact "... is missing" suffix is load-bearing: foundry-capability-preflight.py
        # classifies an absent policy by it (`str(e).rstrip().endswith("is missing")`). The remedy
        # (ER #215, AC-PSC-4) is appended at the CLI boundary in `_check`, not here.
        raise PolicyError(f"{POLICY_REL} is missing")
    try:
        st = os.stat(path)
        if st.st_size > _MAX_FILE_BYTES:
            raise PolicyError(f"{POLICY_REL} exceeds 1 MiB")
        with open(path, "rb") as fh:
            raw = fh.read()
        data = yaml.safe_load(raw)
    except PolicyError:
        raise
    except Exception as e:
        raise PolicyError(f"{POLICY_REL} YAML parse error: {e}") from e

    errors = _jsonschema_check(data)
    errors += _structural_check(data)
    if errors:
        raise PolicyError(f"{POLICY_REL} schema error(s): " + "; ".join(errors))
    return data.get("grants", [])


# --------------------------------------------------------------------------------------------- #
# derivation (AC-SGP-2)
# --------------------------------------------------------------------------------------------- #


# RETIRED in v1.18.0 (operator decision 2026-09-25): the two self-guard deny rules an earlier
# `--write` placed on the policy file. Kept as a constant only so `--write` can take them back (a deny
# rule is otherwise never removed: an operator's own deny is sacrosanct, PR #165).
POLICY_SELF_DENY_RULES = (
    "Edit(.foundry/permissions.yaml)",
    "Write(.foundry/permissions.yaml)",
)


def derive_rules(grants):
    """Every `automatic` grant -> one allow rule; an `approval_required` grant -> NOTHING (v1.18.0,
    AC-V118A-3: a grant never adds a prompt). No deny rules. Sorted so the derived set is
    deterministic regardless of grant-declaration order -- required for --write's idempotency."""
    allow = [f"{g['tool']}({g['pattern']})" for g in grants if g["mode"] == "automatic"]
    return {"allow": sorted(set(allow)), "ask": [], "deny": []}


# --------------------------------------------------------------------------------------------- #
# settings.json read/write
# --------------------------------------------------------------------------------------------- #


def _read_settings_doc(path):
    """Full settings.json document, read defensively. {} (absent doc) if the file does not
    exist; raises PolicyError on an unreadable/malformed file (both modes fail closed rather
    than silently clobbering an operator-authored settings.json)."""
    if not os.path.isfile(path):
        return {}
    try:
        st = os.stat(path)
        if st.st_size > _MAX_FILE_BYTES:
            raise PolicyError(f"{SETTINGS_REL} exceeds 1 MiB")
        with open(path, "rb") as fh:
            raw = fh.read()
        doc = json.loads(raw.decode("utf-8"))
    except PolicyError:
        raise
    except Exception as e:
        raise PolicyError(f"{SETTINGS_REL} unreadable/invalid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise PolicyError(f"{SETTINGS_REL} root must be a JSON object")
    return doc


def _effective_settings_rules(project_dir):
    """{"allow": set(...), "ask": set(...), "deny": set(...)} from `.claude/settings.json` ONLY
    (not settings.local.json -- AC-SGP-2 names settings.json specifically; the doctor's own
    permission-floor probe is the one that additionally watches the local-persist path)."""
    result = _pf.load_settings_file(os.path.join(project_dir, SETTINGS_REL))
    if result["status"] != "ok":
        return {"allow": set(), "ask": set(), "deny": set()}
    rules = result["rules"]
    return {
        "allow": set(rules.get("allow", [])),
        "ask": set(rules.get("ask", [])),
        "deny": set(rules.get("deny", [])),
    }


def _read_sidecar(project_dir):
    path = os.path.join(project_dir, SIDECAR_REL)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        rules = doc.get("rules", {})
        return {
            "yaml_sha256": doc.get("yaml_sha256"),
            "allow": list(rules.get("allow", [])),
            "ask": list(rules.get("ask", [])),
            "deny": list(rules.get("deny", [])),
        }
    except Exception as e:
        # PR #165 security review: an unreadable sidecar used to read as "nothing owned", which let a
        # retired grant's allow rule survive and --check report in-sync. Fail closed instead.
        raise PolicyError(f"{SIDECAR_REL} is unreadable ({e}); repair or delete it before compiling")


def _policy_sha256(project_dir):
    path = os.path.join(project_dir, POLICY_REL)
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------------------------------------- #
# --check (AC-SGP-2)
# --------------------------------------------------------------------------------------------- #


_ASK_ALLOW_TIERS = ("allow", "ask")
_ALL_TIERS = ("allow", "ask", "deny")


def compute_drift(derived, settings_rules, sidecar):
    """Returns a list of human-readable drift findings; [] means agreement. Three classes:
      - missing <tier> rule R           : derived, not present in settings.json under <tier>
      - moved rule R: expected <tier>, found <other> : an allow/ask rule, present under the
                                            WRONG one of the other two ask/allow tiers (AC-SGP-4's
                                            deny rules have no allow/ask counterpart to move
                                            between, so `deny` is checked for presence only)
      - extra <tier> rule R (...)        : previously compiled by us (sidecar), no longer
                                            derived, but still sitting in settings.json
    """
    findings = []
    for tier in _ALL_TIERS:
        for rule in derived[tier]:
            if rule in settings_rules[tier]:
                continue
            moved_to = None
            if tier in _ASK_ALLOW_TIERS:
                other = "ask" if tier == "allow" else "allow"
                if rule in settings_rules[other]:
                    moved_to = other
            if moved_to:
                findings.append(f"moved rule: {rule!r} expected {tier} but found {moved_to}")
            else:
                findings.append(f"missing {tier} rule: {rule!r}")
    if sidecar is not None:
        derived_all = set(derived["allow"]) | set(derived["ask"]) | set(derived["deny"])
        for tier in _ALL_TIERS:
            for rule in sidecar.get(tier, []):
                if rule in derived_all:
                    continue
                if rule in settings_rules[tier]:
                    findings.append(
                        f"extra {tier} rule (previously compiled, no longer derived): {rule!r}"
                    )
    # v1.18.0: the retired self-guard pair still in settings.json is drift `--write` takes back
    for rule in POLICY_SELF_DENY_RULES:
        if rule in settings_rules["deny"]:
            f = f"extra deny rule (previously compiled, no longer derived): {rule!r}"
            if f not in findings:
                findings.append(f)
    return sorted(findings)


def run_check(project_dir):
    """Returns (exit_code, message). Never writes."""
    try:
        grants = load_policy(project_dir)
    except PolicyError as e:
        return EXIT_MISSING_OR_INVALID, _with_remedy(str(e))
    derived = derive_rules(grants)
    settings_rules = _effective_settings_rules(project_dir)
    try:
        sidecar = _read_sidecar(project_dir)
    except PolicyError as e:
        return EXIT_MISSING_OR_INVALID, str(e)
    findings = compute_drift(derived, settings_rules, sidecar)
    if findings:
        return EXIT_DRIFT, "drift:\n  " + "\n  ".join(findings)
    return EXIT_OK, "in-sync"


# --------------------------------------------------------------------------------------------- #
# --write (AC-SGP-3)
# --------------------------------------------------------------------------------------------- #


def run_write(project_dir):
    """Returns (exit_code, message). Reconciles `.claude/settings.json` and the sidecar."""
    try:
        grants = load_policy(project_dir)
    except PolicyError as e:
        return EXIT_MISSING_OR_INVALID, _with_remedy(str(e))
    derived = derive_rules(grants)

    settings_path = os.path.join(project_dir, SETTINGS_REL)
    try:
        doc = _read_settings_doc(settings_path)
    except PolicyError as e:
        return EXIT_MISSING_OR_INVALID, str(e)

    try:
        prev_sidecar = _read_sidecar(project_dir)
    except PolicyError as e:
        return EXIT_MISSING_OR_INVALID, str(e)
    owned_before = {
        tier: set(prev_sidecar[tier]) if prev_sidecar else set()
        for tier in _ALL_TIERS
    }

    perms = doc.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
    owned_after = {}
    for tier in _ALL_TIERS:
        existing = perms.get(tier, [])
        if not isinstance(existing, list):
            existing = []
        to_remove = owned_before[tier] - set(derived[tier])
        if tier == "deny":
            # PR #165 security review: an operator's own deny rule is never removed, whatever the
            # sidecar claims -- only the compiler's two retired self-protection rules are taken back,
            # owned or not (v1.18.0: an earlier updater also placed them).
            to_remove = set(POLICY_SELF_DENY_RULES)
        kept = [r for r in existing if r not in to_remove]
        to_add = [r for r in derived[tier] if r not in kept]
        perms[tier] = kept + to_add
        # Ownership = what this compiler ADDED (now or earlier), never a pre-existing operator rule
        # that merely coincides with a derived one (PR #165 code review).
        owned_after[tier] = sorted((owned_before[tier] & set(derived[tier])) | set(to_add))
    doc["permissions"] = perms

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    _atomic_write_json(settings_path, doc)

    sidecar_doc = {
        "yaml_sha256": _policy_sha256(project_dir),
        "rules": {tier: owned_after[tier] for tier in _ALL_TIERS},
    }
    sidecar_path = os.path.join(project_dir, SIDECAR_REL)
    os.makedirs(os.path.dirname(sidecar_path), exist_ok=True)
    _atomic_write_json(sidecar_path, sidecar_doc)

    return EXIT_OK, (
        f"wrote {len(derived['allow'])} allow rule(s), {len(derived['ask'])} ask rule(s), "
        f"{len(derived['deny'])} deny rule(s) to {SETTINGS_REL}; sidecar recorded at {SIDECAR_REL}"
    )


def _atomic_write_json(path, doc):
    """Deterministic (sorted top-level insertion is NOT reordered -- Python dicts preserve
    insertion order, and this module always builds `doc` the same way for the same inputs, so
    two consecutive writes with no policy change produce byte-identical output -- AC-SGP-3's
    idempotency)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------------------------- #


def _project_dir(root):
    return root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="foundry-permissions-compile — compile .foundry/permissions.yaml into "
                    "native Claude Code permission rules"
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only drift check (exit 0/2/3)")
    mode.add_argument("--write", action="store_true", help="reconcile .claude/settings.json + sidecar")
    ap.add_argument("--root", default=None, help="project dir override (default: $CLAUDE_PROJECT_DIR or cwd)")
    args = ap.parse_args(argv)

    project_dir = _project_dir(args.root)

    if args.check:
        code, message = run_check(project_dir)
    else:
        code, message = run_write(project_dir)

    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
