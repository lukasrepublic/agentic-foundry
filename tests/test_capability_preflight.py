"""tests/test_capability_preflight.py — feat-foundry-authorization-capability-preflight-at-dispatch
(AC-CPD-1..9).

Drives `scripts/foundry-capability-preflight.py` both as an imported module (fast, granular
coverage of `rule_covers`/`preflight`/the two input loaders) and as the real CLI via subprocess
(the argparse mutual-exclusivity contract and the real process exit codes, mirroring
`tests/test_permissions_policy.py`'s own idiom). Every fixture uses a throwaway `tmp_path`
workspace plus an isolated `home` dir — nothing here reads or writes the real repo tree, the real
`.foundry/`, or the real `~/.claude/settings.json`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

CLI = os.path.join(REPO_ROOT, "scripts", "foundry-capability-preflight.py")

CPF = load_module("scripts/foundry-capability-preflight.py", "foundry_capability_preflight")
CONTRACT_SCHEMA_PATH = os.path.join(REPO_ROOT, "schema", "acceptance-contract.schema.json")
BLOCKER_SCHEMA_PATH = os.path.join(REPO_ROOT, "schema", "blocker.schema.json")

contract_mod = load_module("scripts/foundry_contract.py", "foundry_contract")
blocker_mod = load_module("scripts/foundry_blocker_check.py", "foundry_blocker_check")


# --------------------------------------------------------------------------------------------- #
# helpers — `root` is always the WORKSPACE dir passed as --root/project_dir; `home` is always the
# isolated user-scope dir passed as --home. Neither ever nests the other.
# --------------------------------------------------------------------------------------------- #


def _workspace_root(tmp_path):
    return str(tmp_path / "workspace")


def _home_root(tmp_path):
    return str(tmp_path / "home")


def _write_settings(root, allow=None, ask=None, deny=None):
    base = os.path.join(root, ".claude")
    os.makedirs(base, exist_ok=True)
    doc = {"permissions": {"allow": allow or [], "ask": ask or [], "deny": deny or []}}
    with open(os.path.join(base, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _write_local_settings(root, allow=None, ask=None, deny=None):
    base = os.path.join(root, ".claude")
    os.makedirs(base, exist_ok=True)
    doc = {"permissions": {"allow": allow or [], "ask": ask or [], "deny": deny or []}}
    with open(os.path.join(base, "settings.local.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _write_home_settings(home, allow=None, ask=None, deny=None):
    base = os.path.join(home, ".claude")
    os.makedirs(base, exist_ok=True)
    doc = {"permissions": {"allow": allow or [], "ask": ask or [], "deny": deny or []}}
    with open(os.path.join(base, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _write_permissions_yaml(root, text):
    d = os.path.join(root, ".foundry")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "permissions.yaml"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_contract(path, requires_capabilities):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {
        "spec_ref": "foundry/test/fixtures/golden-spec.md",
        "spec_sha256": "deadbeef" * 8,
        "scope": {"allowed_paths": ["apps/api/src/**"]},
        "checkpoints": [
            {"ac_id": "AC-X-1", "surface": "file:apps/api/src/x.py", "locator": "x",
             "expect": {"op": "matches", "value": "x", "baseline": "pre-change"}},
        ],
        "requires_capabilities": requires_capabilities,
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh)


def _write_charter(path, capabilities=None, no_section=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# A charter\n", "\n", "## Goal\n", "\n", "do the thing\n", "\n"]
    if not no_section:
        lines += ["## Requires capabilities\n", "\n"]
        for cap in (capabilities or []):
            lines.append(f"- `{cap}`\n")
        lines.append("\n")
    lines += ["## Scope (write boundary)\n", "\n", "- some/path/**\n"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def _run_cli(root, home, *args):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, CLI, "--root", root, "--home", home, *args],
        capture_output=True, text=True, timeout=60, env=env,
    )


# --------------------------------------------------------------------------------------------- #
# AC-CPD-2 — coverage: the PLATFORM's own token-boundary prefix matching, never by substring, and
# NEVER the permission floor's interpreter-word-as-blanket fold (auth_seq 2)
# --------------------------------------------------------------------------------------------- #


def test_coverage_uses_the_floor_covers_rule_never_substring():
    # Bash: a `git push:*` allow covers a longer command under the SAME reach prefix...
    assert CPF.rule_covers("Bash(git push:*)", "Bash(git push origin main:*)")
    # ...but never merely because the capability's text CONTAINS the allow rule's reach elsewhere
    # (the reach must be a PREFIX, not a substring anywhere in the string).
    assert not CPF.rule_covers("Bash(git push:*)", "Bash(echo git push:*)")

    # THE DEFECT PR #176 SECURITY REVIEW CAUGHT: the permission floor's `canonicalize` drops a
    # leading interpreter word (`python3`, `bash`, `sh`) as a "blanket reach" fold for its OWN
    # doctor-floor comparison — routing Bash coverage through it here made `Bash(python3:*)` read
    # as covering EVERY Bash capability. The platform's own matching does not fold interpreter
    # words: `Bash(python3:*)`'s reach is the literal text `python3`, nothing more.
    assert not CPF.rule_covers("Bash(python3:*)", "Bash(gh pr merge:*)")
    assert not CPF.rule_covers("Bash(bash:*)", "Bash(rm -rf /:*)")
    # sanity: it still covers a capability that genuinely starts with that literal prefix.
    assert CPF.rule_covers("Bash(python3:*)", "Bash(python3 scripts/foo.py:*)")

    # non-Bash: same tool + glob-prefix pattern covers a capability whose pattern starts with the
    # literal prefix...
    assert CPF.rule_covers("Edit(scripts/foo*)", "Edit(scripts/foobar.py)")
    # ...but a capability that merely CONTAINS "scripts/foo" elsewhere in its own path is not
    # covered — prefix, never substring.
    assert not CPF.rule_covers("Edit(scripts/foo*)", "Edit(other/scripts/foobar.py)")

    # exact-pattern non-Bash match (no glob marker at all) still covers.
    assert CPF.rule_covers("Read(docs/README.md)", "Read(docs/README.md)")
    assert not CPF.rule_covers("Read(docs/README.md)", "Read(docs/README2.md)")

    # a bare (no-parens) native tool rule only covers the identical bare tool.
    assert CPF.rule_covers("CronCreate", "CronCreate")
    assert not CPF.rule_covers("CronCreate", "CronDelete")

    # tool mismatch never covers, regardless of pattern shape.
    assert not CPF.rule_covers("Bash(git push:*)", "Edit(git push)")
    assert not CPF.rule_covers("Edit(scripts/**)", "Bash(scripts/foo.py:*)")

    # the four token-boundary marker forms AC-CPD-2 names explicitly.
    assert CPF.rule_covers("Bash(npm run test *)", "Bash(npm run test unit:*)")
    assert CPF.rule_covers("Edit(scripts/**)", "Edit(scripts/sub/foo.py)")


# --------------------------------------------------------------------------------------------- #
# AC-CPD-6 — the three exits: ok(0) / missing(3) / unreadable(2)
# --------------------------------------------------------------------------------------------- #


def test_exit_codes_ok_missing_unreadable(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    contract_path = os.path.join(root, "contract.yaml")

    # ok: the one declared capability is covered by an allow rule.
    _write_settings(root, allow=["Bash(git status:*)"])
    _write_contract(contract_path, ["Bash(git status:*)"])
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 0, r.stdout + r.stderr
    verdict = json.loads(r.stdout)
    assert verdict["status"] == "ok"
    assert verdict["missing"] == []

    # v1.18.0 (AC-V118A-5): a declared capability with no covering allow is ADVISORY — listed under
    # `classifier`, status stays ok, exit 0, never `missing` with a rule to add.
    _write_contract(contract_path, ["Bash(git status:*)", "Bash(gh pr merge:*)"])
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 0, r.stdout + r.stderr
    verdict = json.loads(r.stdout)
    assert verdict["status"] == "ok"
    assert verdict["missing"] == []
    assert [c["capability"] for c in verdict["classifier"]] == ["Bash(gh pr merge:*)"]
    assert "rule_to_add" not in verdict["classifier"][0]

    # missing: only a DENY refusing a declared capability blocks (exit 3).
    _write_settings(root, allow=["Bash(git status:*)"], deny=["Bash(gh pr merge:*)"])
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 3, r.stdout + r.stderr
    verdict = json.loads(r.stdout)
    assert verdict["status"] == "missing"
    assert [m["capability"] for m in verdict["missing"]] == ["Bash(gh pr merge:*)"]
    assert verdict["missing"][0]["where"] == "denied"
    assert verdict["classifier"] == []

    # unreadable: a malformed settings.json (invalid JSON) is a fail-closed input error.
    settings_path = os.path.join(root, ".claude", "settings.json")
    with open(settings_path, "w", encoding="utf-8") as fh:
        fh.write("not valid json {{{")
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "settings.json" in err["error"]

    # unreadable: a missing contract file names itself.
    _write_settings(root, allow=[])
    r = _run_cli(root, home, "--contract", os.path.join(root, "does-not-exist.yaml"))
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "does-not-exist.yaml" in err["error"]


def test_exit_2_on_malformed_permissions_yaml(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    _write_settings(root, allow=[])
    contract_path = os.path.join(root, "contract.yaml")
    _write_contract(contract_path, ["Bash(git status:*)"])
    _write_permissions_yaml(root, "schema_version: 99\ngrants: []\n")
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "permissions.yaml" in err["error"]


def test_no_declared_capabilities_is_trivially_ok(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    contract_path = os.path.join(root, "contract.yaml")
    _write_contract(contract_path, [])
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "ok"


# --------------------------------------------------------------------------------------------- #
# AC-CPD-1 (auth_seq 2) — --contract/--charter confined under the project dir, a regular file,
# at most _MAX_FILE_BYTES
# --------------------------------------------------------------------------------------------- #


def test_contract_path_escaping_the_project_dir_is_refused(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    os.makedirs(root, exist_ok=True)
    # a sibling of `root`, i.e. genuinely outside it -- written with a REAL absolute path (not a
    # literal ".." token) so this exercises path resolution, not string matching.
    outside = str(tmp_path / "outside-contract.yaml")
    _write_contract(outside, ["Bash(git status:*)"])
    r = _run_cli(root, home, "--contract", outside)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "containment" in err["error"]

    # the same escape, spelled with a literal `..` traversal relative to root.
    _write_contract(os.path.join(root, "..", "sibling-contract.yaml"), ["Bash(git status:*)"])
    r = _run_cli(root, home, "--contract", os.path.join(root, "..", "sibling-contract.yaml"))
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "containment" in err["error"]


def test_contract_path_must_be_a_regular_file(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    a_directory = os.path.join(root, "not-a-file")
    os.makedirs(a_directory, exist_ok=True)
    r = _run_cli(root, home, "--contract", a_directory)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "regular file" in err["error"]


def test_contract_path_over_size_cap_is_refused(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    oversized = os.path.join(root, "huge-contract.yaml")
    os.makedirs(root, exist_ok=True)
    with open(oversized, "wb") as fh:
        fh.write(b"#" + b"x" * (CPF._MAX_FILE_BYTES + 1))
    r = _run_cli(root, home, "--contract", oversized)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "1 MiB" in err["error"]


def test_charter_path_is_confined_the_same_way(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    os.makedirs(root, exist_ok=True)
    outside = str(tmp_path / "outside-charter.md")
    _write_charter(outside, capabilities=["Bash(git status:*)"])
    r = _run_cli(root, home, "--charter", outside)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "containment" in err["error"]


# --------------------------------------------------------------------------------------------- #
# AC-CPD-2 (auth_seq 2) — a control character, newline, backtick, `$(`, or `;` in a capability or
# rule pattern is refused (exit 2), never compared or echoed
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("hostile", [
    "Bash(git status; rm -rf /:*)",
    "Bash(git status && echo `whoami`:*)",
    "Bash(git status $(whoami):*)",
    "Bash(git sta\ntus:*)",
])
def test_forbidden_characters_in_a_declared_capability_are_refused(tmp_path, hostile):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    contract_path = os.path.join(root, "contract.yaml")
    _write_settings(root, allow=[])
    _write_contract(contract_path, [hostile])
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "refused" in err["error"]


def test_sanitize_strips_a_control_character_from_the_refusal_message():
    """The render floor (`foundry_permission_floor.sanitize`, AC-CPD-2's "every string echoed
    passes _sanitize") strips a raw control character even from the error text this CLI builds
    itself -- unit-level, not scraped from a subprocess's stdout."""
    hostile = "Bash(git sta\x07tus:*)"  # BEL, a control character
    assert "\x07" not in CPF._sanitize(hostile)


def test_forbidden_characters_in_an_effective_allow_rule_are_refused(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    contract_path = os.path.join(root, "contract.yaml")
    _write_contract(contract_path, ["Bash(git status:*)"])
    _write_settings(root, allow=["Bash(git status; rm -rf /:*)"])
    r = _run_cli(root, home, "--contract", contract_path)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)
    assert "refused" in err["error"]


def test_every_string_echoed_into_the_verdict_is_sanitized(tmp_path):
    """A capability legitimately valid on its own (no forbidden character) still passes through
    `foundry_permission_floor.sanitize` before landing in the verdict — defense in depth, not a
    substitute for the refusal above."""
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    cap = "Bash(gh pr merge:*)"
    verdict = CPF.preflight([cap], root, home=home)
    assert verdict["classifier"][0]["capability"] == CPF._sanitize(cap)
    _write_settings(root, deny=[cap])
    verdict = CPF.preflight([cap], root, home=home)
    assert verdict["missing"][0]["capability"] == CPF._sanitize(cap)
    assert verdict["missing"][0]["rule_to_add"] == CPF._sanitize(cap)


# --------------------------------------------------------------------------------------------- #
# AC-CPD-7 — ask never grants; deny subtracts an otherwise-covering allow (Bash + non-Bash),
# EITHER direction (deny covers the capability, OR the capability covers a narrower deny)
# --------------------------------------------------------------------------------------------- #


def test_ask_does_not_grant_and_deny_subtracts(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)

    # Bash: an `ask` rule alone never grants — the capability reads `classifier` (advisory).
    _write_settings(root, ask=["Bash(gh pr merge:*)"])
    verdict = CPF.preflight(["Bash(gh pr merge:*)"], root, home=home)
    assert verdict["status"] == "ok"
    assert verdict["missing"] == []
    assert verdict["classifier"][0]["capability"] == "Bash(gh pr merge:*)"

    # Bash: an otherwise-covering allow is subtracted by a deny on the same rule (deny covers the
    # capability) — reported `where: "denied"`.
    _write_settings(root, allow=["Bash(gh pr merge:*)"], deny=["Bash(gh pr merge:*)"])
    verdict = CPF.preflight(["Bash(gh pr merge:*)"], root, home=home)
    assert verdict["status"] == "missing"
    assert verdict["missing"][0]["where"] == "denied"

    # Bash: a NARROWER deny that lies WITHIN a broader requested capability also subtracts (the
    # operator carved a sub-case out on purpose) — the AC-CPD-2 auth_seq 3 amendment's own example.
    _write_settings(root, allow=["Bash(git push:*)"], deny=["Bash(git push --force:*)"])
    verdict = CPF.preflight(["Bash(git push:*)"], root, home=home)
    assert verdict["status"] == "missing"
    assert verdict["missing"][0]["where"] == "denied"

    # sanity: a deny that is neither broader-nor-equal NOR narrower-nested does NOT subtract.
    _write_settings(root, allow=["Bash(git push:*)"], deny=["Bash(git pull:*)"])
    verdict = CPF.preflight(["Bash(git push:*)"], root, home=home)
    assert verdict["status"] == "ok"

    # sanity: the same allow WITHOUT the deny is granted.
    _write_settings(root, allow=["Bash(gh pr merge:*)"])
    verdict = CPF.preflight(["Bash(gh pr merge:*)"], root, home=home)
    assert verdict["status"] == "ok"

    # non-Bash: an `ask` rule alone never grants — advisory `classifier`, not `missing`.
    _write_settings(root, ask=["Edit(scripts/**)"])
    verdict = CPF.preflight(["Edit(scripts/foo.py)"], root, home=home)
    assert verdict["status"] == "ok"
    assert verdict["missing"] == []
    assert [c["capability"] for c in verdict["classifier"]] == ["Edit(scripts/foo.py)"]

    # non-Bash: a deny subtracts an otherwise-covering allow (deny covers the capability).
    _write_settings(root, allow=["Edit(scripts/**)"], deny=["Edit(scripts/**)"])
    verdict = CPF.preflight(["Edit(scripts/foo.py)"], root, home=home)
    assert verdict["status"] == "missing"
    assert verdict["missing"][0]["where"] == "denied"

    # non-Bash: a narrower deny nested inside a broader requested capability also subtracts.
    _write_settings(root, allow=["Edit(scripts/**)"], deny=["Edit(scripts/secrets.py)"])
    verdict = CPF.preflight(["Edit(scripts/**)"], root, home=home)
    assert verdict["status"] == "missing"
    assert verdict["missing"][0]["where"] == "denied"

    # sanity: the same allow WITHOUT the deny is granted.
    _write_settings(root, allow=["Edit(scripts/**)"])
    verdict = CPF.preflight(["Edit(scripts/foo.py)"], root, home=home)
    assert verdict["status"] == "ok"


# --------------------------------------------------------------------------------------------- #
# AC-CPD-1 — automatic grants + preconditions_unverified; the three effective settings sources
# --------------------------------------------------------------------------------------------- #


def test_automatic_grant_covers_and_lists_its_preconditions(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    _write_permissions_yaml(root, (
        "schema_version: 1\n"
        "grants:\n"
        "  - id: self-merge-on-green\n"
        "    tool: Bash\n"
        "    pattern: \"gh pr merge:*\"\n"
        "    mode: automatic\n"
        "    preconditions: [ci-green]\n"
    ))
    verdict = CPF.preflight(["Bash(gh pr merge:*)"], root, home=home)
    assert verdict["status"] == "ok"
    assert verdict["preconditions_unverified"] == [
        {"capability": "Bash(gh pr merge:*)", "grant_id": "self-merge-on-green",
         "preconditions": ["ci-green"]},
    ]


def test_automatic_grant_without_preconditions_covers_silently(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    _write_permissions_yaml(root, (
        "schema_version: 1\n"
        "grants:\n"
        "  - id: read-only-report\n"
        "    tool: Bash\n"
        "    pattern: \"git status:*\"\n"
        "    mode: automatic\n"
    ))
    verdict = CPF.preflight(["Bash(git status:*)"], root, home=home)
    assert verdict["status"] == "ok"
    assert verdict["preconditions_unverified"] == []


def test_approval_required_grant_does_not_count_as_granted(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    _write_permissions_yaml(root, (
        "schema_version: 1\n"
        "grants:\n"
        "  - id: destructive-infra\n"
        "    tool: Bash\n"
        "    pattern: \"tofu apply:*\"\n"
        "    mode: approval_required\n"
    ))
    verdict = CPF.preflight(["Bash(tofu apply:*)"], root, home=home)
    # not granted -> advisory `classifier`, never a blocker (v1.18.0, AC-V118A-5)
    assert verdict["status"] == "ok"
    assert verdict["missing"] == []
    assert [c["capability"] for c in verdict["classifier"]] == ["Bash(tofu apply:*)"]


def test_missing_permissions_yaml_is_not_an_error(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    verdict = CPF.preflight(["Bash(git status:*)"], root, home=home)
    # nothing grants it, but no crash/exception -- and not-granted is advisory
    assert verdict["status"] == "ok"
    assert [c["capability"] for c in verdict["classifier"]] == ["Bash(git status:*)"]


def test_user_scope_settings_contribute_a_grant(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    _write_home_settings(home, allow=["Bash(git status:*)"])
    verdict = CPF.preflight(["Bash(git status:*)"], root, home=home)
    assert verdict["status"] == "ok"


def test_settings_local_json_contributes_a_grant(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    _write_local_settings(root, allow=["Bash(git status:*)"])
    verdict = CPF.preflight(["Bash(git status:*)"], root, home=home)
    assert verdict["status"] == "ok"


# --------------------------------------------------------------------------------------------- #
# AC-CPD-8 — the adopter-A allowlist fixture names each missing rule exactly
# --------------------------------------------------------------------------------------------- #


ADOPTER_A_SETTINGS = os.path.join(REPO_ROOT, "tests", "fixtures", "preflight", "settings-adopter-a.json")


def test_adopter_a_allowlist_names_each_not_pre_granted_capability(tmp_path):
    with open(ADOPTER_A_SETTINGS, encoding="utf-8") as fh:
        adopter_doc = json.load(fh)
    assert "allow" in adopter_doc["permissions"]

    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    d = os.path.join(root, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(adopter_doc, fh)

    required = ["Bash(gh pr merge:*)", "CronCreate"]
    verdict = CPF.preflight(required, root, home=home)
    # v1.18.0 (AC-V118A-5): named exactly, under the advisory `classifier` key, never `missing`
    assert verdict["status"] == "ok"
    assert verdict["missing"] == []
    assert {c["capability"] for c in verdict["classifier"]} == set(required)
    for c in verdict["classifier"]:
        assert "rule_to_add" not in c

    # sanity: a capability the adopter DID grant is not reported at all.
    verdict2 = CPF.preflight(["Bash(git status:*)"], root, home=home)
    assert verdict2["status"] == "ok"
    assert verdict2["classifier"] == []


# --------------------------------------------------------------------------------------------- #
# AC-CPD-5 — operator-approval is byte-identical across all four sites
# --------------------------------------------------------------------------------------------- #


def test_operator_approval_member_is_byte_identical_across_four_sites():
    with open(CONTRACT_SCHEMA_PATH, encoding="utf-8") as fh:
        contract_schema = json.load(fh)
    with open(BLOCKER_SCHEMA_PATH, encoding="utf-8") as fh:
        blocker_schema = json.load(fh)

    escalate_when_enum = set(contract_schema["properties"]["escalate_when"]["items"]["enum"])
    why_operator_enum = set(blocker_schema["properties"]["why_operator"]["enum"])
    dwe_set = contract_mod._DWE_ESCALATE_SET
    why_operator_set = blocker_mod._WHY_OPERATOR_SET

    for label, s in (
        ("schema/acceptance-contract.schema.json escalate_when", escalate_when_enum),
        ("scripts/foundry_contract.py _DWE_ESCALATE_SET", dwe_set),
        ("schema/blocker.schema.json why_operator", why_operator_enum),
        ("scripts/foundry_blocker_check.py _WHY_OPERATOR_SET", why_operator_set),
    ):
        assert "operator-approval" in s, f"{label} is missing operator-approval: {sorted(s)}"

    # byte-identical: all four are the SAME set of members, not merely each containing the one.
    assert escalate_when_enum == dwe_set == why_operator_enum == why_operator_set


# --------------------------------------------------------------------------------------------- #
# AC-CPD-9 — both schemas + both hand-rolled floors accept operator-approval, refuse an unknown
# --------------------------------------------------------------------------------------------- #


def test_both_schemas_and_floors_accept_operator_approval_and_refuse_unknown():
    jsonschema = pytest.importorskip("jsonschema")

    with open(CONTRACT_SCHEMA_PATH, encoding="utf-8") as fh:
        contract_schema = json.load(fh)
    contract_item_schema = contract_schema["properties"]["escalate_when"]["items"]
    jsonschema.validate("operator-approval", contract_item_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("not-a-real-member", contract_item_schema)

    with open(BLOCKER_SCHEMA_PATH, encoding="utf-8") as fh:
        blocker_schema = json.load(fh)
    why_operator_schema = blocker_schema["properties"]["why_operator"]
    jsonschema.validate("operator-approval", why_operator_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("not-a-real-member", why_operator_schema)

    # the hand-rolled floors (UL-0011 pattern: run regardless of jsonschema availability)
    assert "operator-approval" in contract_mod._DWE_ESCALATE_SET
    assert "not-a-real-member" not in contract_mod._DWE_ESCALATE_SET
    assert "operator-approval" in blocker_mod._WHY_OPERATOR_SET
    assert "not-a-real-member" not in blocker_mod._WHY_OPERATOR_SET

    # end-to-end through the real structural validators.
    doc = {
        "spec_ref": "foundry/test/fixtures/golden-spec.md",
        "spec_sha256": "deadbeef" * 8,
        "scope": {"allowed_paths": ["apps/api/src/**"]},
        "checkpoints": [
            {"ac_id": "AC-X-1", "surface": "file:apps/api/src/x.py", "locator": "x",
             "expect": {"op": "matches", "value": "x", "baseline": "pre-change"}},
        ],
        "escalate_when": ["operator-approval"],
    }
    ok, errors, _ = contract_mod.validate_contract_bytes(yaml.safe_dump(doc).encode("utf-8"))
    assert ok is True, errors

    candidate = {
        "claim": "the operator has not granted a capability this atom's contract declares",
        "evidence": ["cli:foundry-capability-preflight.py --contract x.yaml exited 3"],
        "attempted": ["ran the preflight", "checked .claude/settings.json"],
        "why_operator": "operator-approval",
    }
    assert blocker_mod.validate_candidate(candidate) == []


# --------------------------------------------------------------------------------------------- #
# --charter — "## Requires capabilities" parsing
# --------------------------------------------------------------------------------------------- #


def test_charter_without_requires_capabilities_section_is_trivially_ok(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    charter_path = os.path.join(root, "charter.md")
    _write_charter(charter_path, no_section=True)
    r = _run_cli(root, home, "--charter", charter_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "ok"


def test_charter_requires_capabilities_section_is_read(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    charter_path = os.path.join(root, "charter.md")
    _write_charter(charter_path, capabilities=["Bash(gh pr merge:*)", "CronCreate"])
    r = _run_cli(root, home, "--charter", charter_path)
    # both declared capabilities are read (and are advisory: nothing pre-grants them)
    assert r.returncode == 0, r.stdout + r.stderr
    verdict = json.loads(r.stdout)
    assert {c["capability"] for c in verdict["classifier"]} == {"Bash(gh pr merge:*)", "CronCreate"}
    # a deny on one of them makes the charter a blocker
    _write_settings(root, deny=["CronCreate"])
    r = _run_cli(root, home, "--charter", charter_path)
    assert r.returncode == 3, r.stdout + r.stderr
    verdict = json.loads(r.stdout)
    assert [m["capability"] for m in verdict["missing"]] == ["CronCreate"]
    assert [c["capability"] for c in verdict["classifier"]] == ["Bash(gh pr merge:*)"]


def test_charter_missing_file_is_unreadable(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    r = _run_cli(root, home, "--charter", os.path.join(root, "no-such-charter.md"))
    assert r.returncode == 2, r.stdout + r.stderr


# --------------------------------------------------------------------------------------------- #
# the CLI's own --contract/--charter mutual exclusivity
# --------------------------------------------------------------------------------------------- #


def test_cli_contract_and_charter_are_mutually_exclusive(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    r = _run_cli(root, home, "--contract", "x.yaml", "--charter", "y.md")
    assert r.returncode not in (0,), r.stdout + r.stderr
    assert "not allowed" in (r.stdout + r.stderr).lower()


def test_cli_requires_one_of_contract_or_charter(tmp_path):
    root = _workspace_root(tmp_path)
    home = _home_root(tmp_path)
    r = _run_cli(root, home)
    assert r.returncode not in (0,)


# --------------------------------------------------------------------------------------------- #
# AC-CPD-4 — foundry-doctor.py wires the preflight as its permissions-policy advisory line
# --------------------------------------------------------------------------------------------- #


def test_doctor_permissions_policy_never_reddens_on_a_malformed_active_release(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor_capability_preflight_test")
    project_dir = str(tmp_path / "proj")
    releases_dir = os.path.join(project_dir, ".foundry", "releases", "r1")
    os.makedirs(releases_dir, exist_ok=True)
    with open(os.path.join(releases_dir, "release.yaml"), "w", encoding="utf-8") as fh:
        fh.write("id: r1\ndescription: not a valid release (missing atoms)\nstate: active\n")
    ok, detail = doctor.check_permissions_policy(plugin_root=REPO_ROOT, project_dir=project_dir)
    assert ok is not False
    assert "preflight" in detail


def test_doctor_permissions_policy_reports_ok_with_zero_atoms(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor_capability_preflight_test2")
    project_dir = str(tmp_path / "proj")
    ok, detail = doctor.check_permissions_policy(plugin_root=REPO_ROOT, project_dir=project_dir)
    assert ok is True
    # AC-CPD-4: the R1 drift state rides the SAME line -- no .foundry/permissions.yaml here, so
    # the policy half reads `absent`, exactly like the pre-existing R1 probe did on its own line.
    # permissions-scaffold (ER #215, AC-PSC-4): an absent policy names its remedy in the same line
    assert detail.startswith("preflight over 0 active atom(s): 0 denied; policy absent (.foundry/permissions.yaml vs .claude/settings.json) — seed it"), detail


def test_doctor_permissions_policy_counts_missing_rules_from_an_active_release(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor_capability_preflight_test3")
    project_dir = str(tmp_path / "proj")
    release_dir = os.path.join(project_dir, ".foundry", "releases", "r1")
    os.makedirs(release_dir, exist_ok=True)
    contract_path = os.path.join(project_dir, "specs", "atom-a", "acceptance-contract.yaml")
    _write_contract(contract_path, ["Bash(gh pr merge:*)"])
    with open(os.path.join(release_dir, "release.yaml"), "w", encoding="utf-8") as fh:
        fh.write(
            "id: r1\n"
            "description: one atom, one missing capability\n"
            "state: active\n"
            "atoms:\n"
            "  - id: atom-a\n"
            "    spec_ref: specs/atom-a/spec.md\n"
            "    contract_ref: specs/atom-a/acceptance-contract.yaml\n"
            "    depends_on: []\n"
        )
    ok, detail = doctor.check_permissions_policy(plugin_root=REPO_ROOT, project_dir=project_dir)
    # v1.18.0 (AC-V118A-5): not pre-granted is advisory information, never a blocker — the line
    # stays ok (the absent policy is informational too)
    assert detail.startswith("preflight over 1 active atom(s): 0 denied, 1 not pre-granted; policy absent"), detail


def test_doctor_permissions_policy_keeps_the_r1_drift_state_on_the_same_line(tmp_path, monkeypatch):
    """AC-CPD-4 (auth_seq 2): the R1 drift comparison (`foundry-permissions-compile.py --check`)
    is NOT dropped -- it rides the same `permissions-policy` line the preflight status does."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor_capability_preflight_test4")
    project_dir = str(tmp_path / "proj")
    os.makedirs(project_dir, exist_ok=True)
    _write_permissions_yaml(project_dir, (
        "schema_version: 1\n"
        "grants:\n"
        "  - id: self-merge-on-green\n"
        "    tool: Bash\n"
        "    pattern: \"gh pr merge:*\"\n"
        "    mode: automatic\n"
    ))
    # drift: the derived allow rule is not (yet) in .claude/settings.json -- one finding (v1.18.0:
    # the self-guard deny pair is retired, so it is no longer derived).
    ok, detail = doctor.check_permissions_policy(plugin_root=REPO_ROOT, project_dir=project_dir)
    assert ok is doctor.ADVISORY
    assert detail == "preflight over 0 active atom(s): 0 denied; policy drift (1) (.foundry/permissions.yaml vs .claude/settings.json)"

    # reconcile it, via the real compiler this time -- --check now reports in-sync.
    pc = load_module("scripts/foundry-permissions-compile.py", "foundry_permissions_compile_for_doctor_test")
    code, _msg = pc.run_write(project_dir)
    assert code == 0
    ok, detail = doctor.check_permissions_policy(plugin_root=REPO_ROOT, project_dir=project_dir)
    assert ok is True
    assert detail == "preflight over 0 active atom(s): 0 denied; policy in-sync (.foundry/permissions.yaml vs .claude/settings.json)"
