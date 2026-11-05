"""gv-16 / operating-model rule G3: commit before you authorize.

`foundry-authorize.py` refuses (`commit-before-authorize`) while the spec or the contract has uncommitted changes
(staged, unstaged or untracked); the freeze binds the SHA-256 of the committed text. A tree that is not a git repository
cannot be checked: that degrade is disclosed, never silent. Drives the real script in a throw-away git repo.
"""
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_authorize_no_audit_precondition import _run, _write_fixture


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@e.invalid", *a],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def fx():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "ws"
        spec, contract = _write_fixture(root)
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        git(root, "add", ".")
        git(root, "commit", "-qm", "init")
        yield root, spec, contract


def test_a_clean_committed_spec_and_contract_are_admitted(fx):
    root, spec, contract = fx
    p = _run(root, spec, contract, [], yes=False)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "commit-before-authorize" not in p.stdout + p.stderr


def test_a_dirty_spec_is_refused_in_a_dry_run_and_with_yes(fx):
    root, spec, contract = fx
    spec.write_text(spec.read_text() + "\n- **AC-ADP-2**: a new clause.\n")
    for yes in (False, True):
        p = _run(root, spec, contract, [], yes=yes)
        assert p.returncode == 1, p.stdout + p.stderr
        assert "commit-before-authorize" in p.stderr and "specs/adap-probe.md" in p.stderr
    audit = root / ".foundry" / "security-audit.jsonl"
    assert not audit.exists() or "authorize-complete" not in audit.read_text()


def test_a_dirty_contract_is_refused_and_the_freeze_writes_nothing(fx):
    root, spec, contract = fx
    before = contract.read_text()
    contract.write_text(before + "# edited\n")
    p = _run(root, spec, contract, [], yes=True)
    assert p.returncode == 1 and "commit-before-authorize" in p.stderr and "adap-probe.yaml" in p.stderr
    assert contract.read_text() == before + "# edited\n"            # untouched: no freeze block was written


def test_a_staged_but_uncommitted_change_and_an_untracked_spec_are_refused(fx):
    root, spec, contract = fx
    spec.write_text(spec.read_text() + "\nmore\n")
    git(root, "add", str(spec))
    assert "commit-before-authorize" in _run(root, spec, contract, [], yes=False).stderr
    git(root, "commit", "-qm", "spec")
    assert _run(root, spec, contract, [], yes=False).returncode == 0                       # control: committed again
    fresh = spec.with_name("fresh.md")
    fresh.write_text(spec.read_text())
    p = _run(root, fresh, contract, [], yes=False)
    assert p.returncode == 1 and "commit-before-authorize" in p.stderr and "fresh.md" in p.stderr


def test_other_dirty_files_do_not_block(fx):
    root, spec, contract = fx
    (root / "src").mkdir()
    (root / "src" / "unrelated.py").write_text("x = 1\n")
    p = _run(root, spec, contract, [], yes=False)
    assert p.returncode == 0, p.stdout + p.stderr


def test_a_tree_that_is_not_a_repository_degrades_with_a_disclosed_warning():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "ws"
        spec, contract = _write_fixture(root)
        p = _run(root, spec, contract, [], yes=False)
        assert p.returncode == 0, p.stdout + p.stderr
        assert "commit-before-authorize check degraded" in p.stdout
