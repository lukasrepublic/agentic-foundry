"""tests/test_permissions_policy.py — feat-foundry-authorization-standing-grants-as-policy
(AC-SGP-1..3/7).

Drives `scripts/foundry-permissions-compile.py` both as an imported module (fast, granular unit
coverage of derivation/drift/schema logic) and as the real CLI via subprocess (the argparse
mutual-exclusivity contract, AC-SGP-7, is a property of the entry point itself, not of any
importable function). Every fixture uses a throwaway `tmp_path` workspace — nothing here reads or
writes the real repo tree, the real `.foundry/`, or a real `.claude/settings.json`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

CLI = os.path.join(REPO_ROOT, "scripts", "foundry-permissions-compile.py")

PC = load_module("scripts/foundry-permissions-compile.py", "foundry_permissions_compile")


# --------------------------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------------------------- #


def _write_yaml(root, text):
    d = os.path.join(root, ".foundry")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "permissions.yaml"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_settings(root, allow=None, ask=None, extra_top=None):
    d = os.path.join(root, ".claude")
    os.makedirs(d, exist_ok=True)
    doc = {"permissions": {"allow": allow or [], "ask": ask or []}}
    if extra_top:
        doc.update(extra_top)
    with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _read_settings(root):
    with open(os.path.join(root, ".claude", "settings.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _read_sidecar(root):
    with open(os.path.join(root, ".claude", "foundry-permissions.compiled.json"), encoding="utf-8") as fh:
        return json.load(fh)


_ONE_GRANT_YAML = """\
schema_version: 1
grants:
  - id: self-merge-on-green
    tool: Bash
    pattern: "gh pr merge:*"
    mode: automatic
    preconditions: [ci-green]
    note: standing grant
  - id: destructive-infra
    tool: Bash
    pattern: "tofu apply:*"
    mode: approval_required
"""


def _run_cli(root, *args):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, CLI, "--root", root, *args],
        capture_output=True, text=True, timeout=60, env=env,
    )


# --------------------------------------------------------------------------------------------- #
# AC-SGP-1 — schema / structural validation
# --------------------------------------------------------------------------------------------- #


def test_load_policy_accepts_a_well_formed_file(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    grants = PC.load_policy(root)
    assert len(grants) == 2
    assert {g["id"] for g in grants} == {"self-merge-on-green", "destructive-infra"}


def test_load_policy_rejects_missing_file(tmp_path):
    root = str(tmp_path)
    with pytest.raises(PC.PolicyError, match="missing"):
        PC.load_policy(root)


@pytest.mark.parametrize("bad_yaml,expect", [
    ("schema_version: 2\ngrants: []\n", "schema_version"),
    ("schema_version: 1\ngrants: not-a-list\n", "grants must be a list"),
    ("schema_version: 1\ngrants:\n  - id: a\n    tool: NotATool\n    pattern: x\n    mode: automatic\n",
     "tool"),
    ("schema_version: 1\ngrants:\n  - id: a\n    tool: Bash\n    pattern: x\n    mode: sometimes\n",
     "mode"),
    ("schema_version: 1\ngrants:\n  - id: Not_A_Slug\n    tool: Bash\n    pattern: x\n    mode: automatic\n",
     "id"),
    ("schema_version: 1\ngrants:\n  - id: a\n    tool: Bash\n    pattern: x\n    mode: automatic\n"
     "    extra_field: nope\n", "unknown field"),
])
def test_load_policy_rejects_schema_violations(tmp_path, bad_yaml, expect):
    root = str(tmp_path)
    _write_yaml(root, bad_yaml)
    with pytest.raises(PC.PolicyError, match=expect):
        PC.load_policy(root)


def test_load_policy_rejects_duplicate_ids(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, """\
schema_version: 1
grants:
  - id: dupe
    tool: Bash
    pattern: x
    mode: automatic
  - id: dupe
    tool: Bash
    pattern: y
    mode: automatic
""")
    with pytest.raises(PC.PolicyError, match="duplicate"):
        PC.load_policy(root)


def test_load_policy_rejects_bad_yaml_syntax(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, "schema_version: 1\ngrants: [\n")
    with pytest.raises(PC.PolicyError):
        PC.load_policy(root)


def test_derive_rules_maps_automatic_to_allow_and_approval_required_to_ask():
    grants = [
        {"id": "a", "tool": "Bash", "pattern": "gh pr merge:*", "mode": "automatic"},
        {"id": "b", "tool": "Bash", "pattern": "tofu apply:*", "mode": "approval_required"},
    ]
    derived = PC.derive_rules(grants)
    assert derived == {"allow": ["Bash(gh pr merge:*)"], "ask": ["Bash(tofu apply:*)"]}


# --------------------------------------------------------------------------------------------- #
# AC-SGP-2 — --check
# --------------------------------------------------------------------------------------------- #


def test_check_exits_2_on_missing_policy(tmp_path):
    root = str(tmp_path)
    code, msg = PC.run_check(root)
    assert code == PC.EXIT_MISSING_OR_INVALID == 2
    assert "missing" in msg


def test_check_exits_2_on_invalid_policy(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, "schema_version: 99\ngrants: []\n")
    code, msg = PC.run_check(root)
    assert code == 2
    assert "schema_version" in msg


def test_check_exits_0_when_in_sync(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    write_code, _ = PC.run_write(root)
    assert write_code == 0
    check_code, msg = PC.run_check(root)
    assert check_code == PC.EXIT_OK == 0
    assert msg == "in-sync"


def test_check_reports_drift_exit_3(tmp_path):
    """The named checkpoint test for AC-SGP-2: --check on a policy whose derived rule set
    disagrees with `.claude/settings.json` (never written) exits 3 and names the missing rule."""
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    # settings.json is absent entirely -- every derived rule is "missing".
    code, msg = PC.run_check(root)
    assert code == PC.EXIT_DRIFT == 3
    assert "missing allow rule: 'Bash(gh pr merge:*)'" in msg
    assert "missing ask rule: 'Bash(tofu apply:*)'" in msg


def test_check_reports_drift_when_grant_removed_after_a_write(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    code, _ = PC.run_write(root)
    assert code == 0
    # Now narrow the policy to one grant -- the second rule is still sitting in settings.json,
    # recorded in the sidecar, but no longer derived: an "extra" finding.
    _write_yaml(root, """\
schema_version: 1
grants:
  - id: self-merge-on-green
    tool: Bash
    pattern: "gh pr merge:*"
    mode: automatic
""")
    code, msg = PC.run_check(root)
    assert code == 3
    assert "extra ask rule" in msg
    assert "tofu apply" in msg


def test_check_reports_moved_rule_when_settings_tier_disagrees_with_derivation(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    code, _ = PC.run_write(root)
    assert code == 0
    # Hand-edit settings.json: move the allow rule into ask.
    doc = _read_settings(root)
    doc["permissions"]["allow"].remove("Bash(gh pr merge:*)")
    doc["permissions"]["ask"].append("Bash(gh pr merge:*)")
    with open(os.path.join(root, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    code, msg = PC.run_check(root)
    assert code == 3
    assert "moved rule" in msg


def test_check_never_writes_anything(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    PC.run_check(root)
    assert not os.path.exists(os.path.join(root, ".claude", "settings.json"))
    assert not os.path.exists(os.path.join(root, ".claude", "foundry-permissions.compiled.json"))


# --------------------------------------------------------------------------------------------- #
# AC-SGP-3 — --write
# --------------------------------------------------------------------------------------------- #


def test_write_adds_exactly_the_derived_rules(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    code, _ = PC.run_write(root)
    assert code == 0
    settings = _read_settings(root)
    assert settings["permissions"]["allow"] == ["Bash(gh pr merge:*)"]
    assert settings["permissions"]["ask"] == ["Bash(tofu apply:*)"]
    sidecar = _read_sidecar(root)
    assert sidecar["rules"]["allow"] == ["Bash(gh pr merge:*)"]
    assert sidecar["rules"]["ask"] == ["Bash(tofu apply:*)"]
    assert sidecar["yaml_sha256"] == PC._policy_sha256(root)


def test_write_leaves_every_other_operator_rule_untouched(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    _write_settings(root, allow=["Bash(git status:*)"], ask=["Bash(git push:*)"],
                    extra_top={"enabledPlugins": {"foundry": True}})
    code, _ = PC.run_write(root)
    assert code == 0
    settings = _read_settings(root)
    assert "Bash(git status:*)" in settings["permissions"]["allow"]
    assert "Bash(git push:*)" in settings["permissions"]["ask"]
    assert "Bash(gh pr merge:*)" in settings["permissions"]["allow"]
    assert "Bash(tofu apply:*)" in settings["permissions"]["ask"]
    assert settings["enabledPlugins"] == {"foundry": True}


def test_write_is_idempotent_and_owns_only_its_rules(tmp_path):
    """The named checkpoint test for AC-SGP-3: a second --write with no policy change touches no
    byte, and only rules this compiler itself previously recorded are ever removed."""
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    _write_settings(root, allow=["Bash(git status:*)"], ask=["Bash(git push:*)"])

    code1, _ = PC.run_write(root)
    assert code1 == 0
    settings_path = os.path.join(root, ".claude", "settings.json")
    sidecar_path = os.path.join(root, ".claude", "foundry-permissions.compiled.json")
    settings_bytes_1 = open(settings_path, "rb").read()
    sidecar_bytes_1 = open(sidecar_path, "rb").read()

    code2, _ = PC.run_write(root)
    assert code2 == 0
    settings_bytes_2 = open(settings_path, "rb").read()
    sidecar_bytes_2 = open(sidecar_path, "rb").read()

    assert settings_bytes_1 == settings_bytes_2, "second --write must not change settings.json bytes"
    assert sidecar_bytes_1 == sidecar_bytes_2, "second --write must not change the sidecar's bytes"

    # Narrow the policy to one grant, then --write again: the retired grant's rule (which this
    # compiler itself added) is removed; the operator's own hand-authored rules are NEVER touched.
    _write_yaml(root, """\
schema_version: 1
grants:
  - id: self-merge-on-green
    tool: Bash
    pattern: "gh pr merge:*"
    mode: automatic
""")
    code3, _ = PC.run_write(root)
    assert code3 == 0
    settings = _read_settings(root)
    assert "Bash(tofu apply:*)" not in settings["permissions"]["ask"], \
        "a retired compiler-owned rule must be removed on --write"
    assert "Bash(git status:*)" in settings["permissions"]["allow"], \
        "an operator-authored allow rule must never be removed by --write"
    assert "Bash(git push:*)" in settings["permissions"]["ask"], \
        "an operator-authored ask rule must never be removed by --write"

    check_code, check_msg = PC.run_check(root)
    assert check_code == 0, check_msg


def test_write_exits_2_on_missing_policy(tmp_path):
    root = str(tmp_path)
    code, msg = PC.run_write(root)
    assert code == 2
    assert "missing" in msg
    assert not os.path.exists(os.path.join(root, ".claude", "settings.json"))


def test_write_refuses_a_malformed_settings_json_without_clobbering_it(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    d = os.path.join(root, ".claude")
    os.makedirs(d, exist_ok=True)
    settings_path = os.path.join(d, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as fh:
        fh.write("not valid json {{{")
    code, msg = PC.run_write(root)
    assert code == 2
    assert open(settings_path, encoding="utf-8").read() == "not valid json {{{"


# --------------------------------------------------------------------------------------------- #
# AC-SGP-7 — the CLI's own --check/--write mutual exclusivity
# --------------------------------------------------------------------------------------------- #


def test_cli_check_and_write_are_mutually_exclusive(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    r = _run_cli(root, "--check", "--write")
    assert r.returncode not in (0,), r.stdout + r.stderr
    assert "not allowed" in (r.stdout + r.stderr).lower()


def test_cli_requires_one_of_check_or_write(tmp_path):
    root = str(tmp_path)
    r = _run_cli(root)
    assert r.returncode not in (0,)


def test_cli_check_real_process_exit_codes(tmp_path):
    root = str(tmp_path)
    _write_yaml(root, _ONE_GRANT_YAML)
    r = _run_cli(root, "--check")
    assert r.returncode == 3, r.stdout + r.stderr
    r = _run_cli(root, "--write")
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run_cli(root, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_check_real_process_exit_2_on_missing_policy(tmp_path):
    root = str(tmp_path)
    r = _run_cli(root, "--check")
    assert r.returncode == 2, r.stdout + r.stderr
