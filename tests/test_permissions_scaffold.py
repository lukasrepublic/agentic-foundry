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
    """hotfix-v1.17.3 (AC-PSC-3 restored): a fresh seed is IN-SYNC on the create path — the two
    self-guard deny rules (`Edit`/`Write` on the policy file) ride with the floor, so nobody has to
    remember the compiler; `--check` exits 0 and the doctor says `policy in-sync` immediately."""
    home, target, proc = _scaffold(tmp_path)
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Edit(.foundry/permissions.yaml)" in settings["permissions"]["deny"]
    assert "Write(.foundry/permissions.yaml)" in settings["permissions"]["deny"]
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target), HOME=str(home))
    check = subprocess.run([sys.executable, COMPILE, "--check", "--root", str(target)],
                           capture_output=True, text=True, env=env, cwd=str(target))
    assert check.returncode == 0, check.stdout + check.stderr
    doc = subprocess.run([sys.executable, DOCTOR], capture_output=True, text=True, env=env, cwd=str(target))
    assert "policy in-sync" in doc.stdout, doc.stdout


def test_existing_reconcile_floor_converges_the_self_guard_pair(tmp_path):
    """An older workspace (policy seeded, deny pair absent) gains the pair on `--existing
    --reconcile-floor`, reported on one row; a second run says already present."""
    home, target, _ = _scaffold(tmp_path)
    sp = target / ".claude" / "settings.json"
    settings = json.loads(sp.read_text(encoding="utf-8"))
    settings["permissions"]["deny"] = [r for r in settings["permissions"]["deny"] if "permissions.yaml" not in r]
    sp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    again = run_cli(["--dir", str(target), "--existing", "--reconcile-floor", "--yes"], home=home, input_text="")
    assert again.returncode in (0, 2), again.stdout + again.stderr
    assert "[permissions] self-guard deny rules added (2)" in again.stdout, again.stdout
    settings = json.loads(sp.read_text(encoding="utf-8"))
    assert "Write(.foundry/permissions.yaml)" in settings["permissions"]["deny"]
    once_more = run_cli(["--dir", str(target), "--existing", "--reconcile-floor", "--yes"], home=home, input_text="")
    assert "[permissions] self-guard deny rules already present" in once_more.stdout, once_more.stdout


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
