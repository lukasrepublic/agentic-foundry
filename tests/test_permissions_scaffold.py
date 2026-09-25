"""permissions-scaffold (ER #215, AC-PSC-1..4) — the starter policy, end to end through the real CLI.

The scaffold writes `.foundry/permissions.yaml` once (a SEED); a second run reports it `kept`
whatever the operator did to it and never touches it; the template validates against the schema;
a freshly scaffolded workspace compiles in-sync with zero grants and the doctor says so; an absent
policy names its remedy in the doctor line and in the compiler's own message.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from test_bootstrap_cli import run_cli  # noqa: E402  (the same throwaway-HOME runner every CLI test uses)

TEMPLATE = os.path.join(REPO, "cli", "templates", "permissions.yaml.tmpl")
CONTEXT_COPY = os.path.join(REPO, "context", "permissions-template.yaml")
SCHEMA = os.path.join(REPO, "schema", "permissions.schema.json")
COMPILE = os.path.join(REPO, "scripts", "foundry-permissions-compile.py")
DOCTOR = os.path.join(REPO, "scripts", "foundry-doctor.py")


def _scaffold(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    proc = run_cli(["--dir", str(target), "--yes"], home=home)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return home, target, proc


def test_template_and_context_copy_are_byte_identical():
    with open(TEMPLATE, "rb") as a, open(CONTEXT_COPY, "rb") as b:
        assert a.read() == b.read()


def test_template_validates_against_the_schema():
    import yaml
    with open(TEMPLATE, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert doc == {"schema_version": 1, "grants": []}
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA, encoding="utf-8") as fh:
        jsonschema.validate(doc, json.load(fh))


def test_scaffold_seeds_the_policy_then_keeps_it(tmp_path):
    home, target, proc = _scaffold(tmp_path)
    policy = target / ".foundry" / "permissions.yaml"
    assert "[create] .foundry/permissions.yaml" in proc.stdout
    with open(TEMPLATE, "rb") as fh:
        assert policy.read_bytes() == fh.read()

    # the operator edits it — a real grant — and a reconcile run reports `kept`, exit 0, bytes intact
    edited = policy.read_text(encoding="utf-8").replace(
        "grants: []",
        "grants:\n  - id: merge-atom-pr-when-green\n    tool: Bash\n    pattern: \"gh pr merge:*\"\n"
        "    mode: automatic\n    preconditions: [ci-green]\n")
    policy.write_text(edited, encoding="utf-8")
    before = policy.stat()
    again = run_cli(["--dir", str(target), "--existing", "--yes"], home=home, input_text="")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "[kept] .foundry/permissions.yaml" in again.stdout
    assert "[drifted] .foundry/permissions.yaml" not in again.stdout
    assert policy.read_text(encoding="utf-8") == edited
    assert policy.stat().st_mtime_ns == before.st_mtime_ns

    # dry-run over the same tree prints the same row and writes nothing
    dry = run_cli(["--dir", str(target), "--existing", "--yes", "--dry-run"], home=home, input_text="")
    assert "[kept] .foundry/permissions.yaml" in dry.stdout
    assert policy.stat().st_mtime_ns == before.st_mtime_ns


def test_fresh_scaffold_compiles_in_sync_and_the_doctor_agrees(tmp_path):
    """AC-PSC-3, v1.18.0 (AC-V118A-4): a fresh seed is IN-SYNC on the create path with NO self-guard
    deny pair written (it is retired) — `--check` exits 0 and the doctor says `policy in-sync`."""
    home, target, proc = _scaffold(tmp_path)
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Edit(.foundry/permissions.yaml)" not in settings["permissions"]["deny"]
    assert "Write(.foundry/permissions.yaml)" not in settings["permissions"]["deny"]
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target), HOME=str(home))
    check = subprocess.run([sys.executable, COMPILE, "--check", "--root", str(target)],
                           capture_output=True, text=True, env=env, cwd=str(target))
    assert check.returncode == 0, check.stdout + check.stderr
    doc = subprocess.run([sys.executable, DOCTOR], capture_output=True, text=True, env=env, cwd=str(target))
    assert "policy in-sync (.foundry/permissions.yaml vs .claude/settings.json)" in doc.stdout, doc.stdout


def test_existing_reconcile_floor_retires_the_self_guard_pair(tmp_path):
    """v1.18.0 (AC-V118A-4): an older workspace carrying the self-guard pair has it RETIRED on
    `--existing --reconcile-floor`, each rule named with its file and tier, next to an operator deny
    rule that is never touched; a second run is silent about it and the compiler reads in-sync."""
    home, target, _ = _scaffold(tmp_path)
    sp = target / ".claude" / "settings.json"
    settings = json.loads(sp.read_text(encoding="utf-8"))
    settings["permissions"]["deny"] = settings["permissions"]["deny"] + [
        "Edit(.foundry/permissions.yaml)", "Bash(rm -rf:*)", "Write(.foundry/permissions.yaml)"]
    sp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    again = run_cli(["--dir", str(target), "--existing", "--reconcile-floor", "--yes"], home=home, input_text="")
    assert again.returncode in (0, 2), again.stdout + again.stderr
    assert "[retired] .claude/settings.json deny: Edit(.foundry/permissions.yaml)" in again.stdout, again.stdout
    assert "[retired] .claude/settings.json deny: Write(.foundry/permissions.yaml)" in again.stdout, again.stdout
    assert "self-guard deny rules added" not in again.stdout
    settings = json.loads(sp.read_text(encoding="utf-8"))
    assert "Edit(.foundry/permissions.yaml)" not in settings["permissions"]["deny"]
    assert "Write(.foundry/permissions.yaml)" not in settings["permissions"]["deny"]
    assert "Bash(rm -rf:*)" in settings["permissions"]["deny"], "an operator deny rule was removed"
    once_more = run_cli(["--dir", str(target), "--existing", "--reconcile-floor", "--yes"], home=home, input_text="")
    assert "permissions.yaml" not in "".join(l for l in once_more.stdout.splitlines(True) if "[retired]" in l), once_more.stdout
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target), HOME=str(home))
    check = subprocess.run([sys.executable, COMPILE, "--check", "--root", str(target)],
                           capture_output=True, text=True, env=env, cwd=str(target))
    assert check.returncode == 0, check.stdout + check.stderr


def test_absent_policy_names_the_remedy(tmp_path):
    home, target, _ = _scaffold(tmp_path)
    (target / ".foundry" / "permissions.yaml").unlink()
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target), HOME=str(home))
    check = subprocess.run([sys.executable, COMPILE, "--check", "--root", str(target)],
                           capture_output=True, text=True, env=env, cwd=str(target))
    assert check.returncode == 2
    assert "npx update-agentic-workspace" in check.stdout + check.stderr
    doc = subprocess.run([sys.executable, DOCTOR], capture_output=True, text=True, env=env, cwd=str(target))
    assert "policy absent" in doc.stdout and "npx update-agentic-workspace" in doc.stdout, doc.stdout


def test_retired_self_guard_pair_is_the_same_text_in_the_compiler_and_the_cli():
    """PR #233 review Risk 5, carried to v1.18.0: the retired pair is named in the compiler (Python,
    `POLICY_SELF_DENY_RULES`, which `--write` takes back) and in the CLI (node,
    `RETIRED_FLOOR_LITERALS.deny`, which the updater takes back); a parity test stops them drifting
    apart. The old CLI module that WROTE the pair is gone."""
    import re
    assert not os.path.exists(os.path.join(REPO, "cli", "src", "selfGuardDeny.mjs"))
    node_pair = subprocess.run(["node", "-e", "import('./cli/src/floorReconcile.mjs').then(m=>console.log(JSON.stringify(m.RETIRED_FLOOR_LITERALS.deny.filter(r=>r.includes('permissions.yaml')))))"],
                               capture_output=True, text=True, cwd=REPO)
    assert node_pair.returncode == 0, node_pair.stderr
    py = open(COMPILE, encoding="utf-8").read()
    py_pair = re.findall(r'"((?:Edit|Write)\(\.foundry/permissions\.yaml\))"', py)
    assert sorted(json.loads(node_pair.stdout)) == sorted(py_pair) == ["Edit(.foundry/permissions.yaml)", "Write(.foundry/permissions.yaml)"]
