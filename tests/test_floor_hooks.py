"""tests/test_floor_hooks.py — feat-foundry-authorization-floor-hooks (AC-FLH-1..11).

Drives `scripts/foundry_floor_hooks.py` (the shared parsing/authorization/evidence helpers) and
both hook entry points (`hooks/foundry-task-created.py`, `hooks/foundry-task-completed.py`) via
direct import (`conftest.load_module`, the sibling suites' own idiom) over throwaway `tmp_path`
fixture trees — never the real `.foundry/` state or the real `~/.claude/tasks/` corpus.

Test names are the acceptance contract's own checkpoint locators (AC-FLH-3/-6/-7/-8/-9/-10) — each
bundles every scenario its AC names into one function, matching the contract's binding exactly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

ffh = load_module("scripts/foundry_floor_hooks.py", "foundry_floor_hooks")
fa = load_module("scripts/foundry_authz.py", "foundry_authz")
fc = load_module("scripts/foundry_contract.py", "foundry_contract")

# `hooks/` never carried a `.py` file before this atom, so `hooks/__pycache__` never existed
# either — tests/test_permission_floor_map.py's `commanded_basenames` walks EVERY file under
# hooks/** as prose (AC-PFM-2's not_invoked truth-check) and does not expect a binary bytecode
# cache to land there. Importing the two hooks with `sys.dont_write_bytecode` suppressed avoids
# ever creating one, rather than teaching that sibling suite about bytecode caches (out of this
# atom's contract scope).
_prev_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    created_hook = load_module("hooks/foundry-task-created.py", "foundry_task_created")
    completed_hook = load_module("hooks/foundry-task-completed.py", "foundry_task_completed")
finally:
    sys.dont_write_bytecode = _prev_dont_write_bytecode


# ================================================================================================ #
# fixture helpers
# ================================================================================================ #


def _git(project_dir, *args):
    return subprocess.run(["git", "-C", project_dir, *args], capture_output=True, text=True)


def _init_repo(project_dir):
    """A real git repo with ONE commit (a bare `git log` on a zero-commit repo exits 128 "does not
    have any commits yet" — not the clean exit-0/empty-stdout an uncommitted PATH gets once the
    repo has history), so `_charter_committed`'s `git log -1 -- <path>` behaves exactly as it would
    in a real workspace for both a committed and an uncommitted charter."""
    os.makedirs(project_dir, exist_ok=True)
    _git(project_dir, "init", "-q")
    _git(project_dir, "config", "user.email", "floor-hooks-test@example.com")
    _git(project_dir, "config", "user.name", "Floor Hooks Test")
    readme = os.path.join(project_dir, "README.md")
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write("fixture\n")
    r = _git(project_dir, "add", "README.md")
    assert r.returncode == 0, r.stderr
    r = _git(project_dir, "commit", "-q", "-m", "init")
    assert r.returncode == 0, r.stderr


def _commit(project_dir, relpath):
    r = _git(project_dir, "add", relpath)
    assert r.returncode == 0, r.stderr
    r = _git(project_dir, "commit", "-q", "-m", f"add {relpath}")
    assert r.returncode == 0, r.stderr


def _write_release(project_dir, release_id, atoms, state="active"):
    rel_dir = os.path.join(project_dir, ".foundry", "releases", release_id)
    os.makedirs(rel_dir, exist_ok=True)
    doc = {"id": release_id, "description": "fixture release", "state": state, "atoms": atoms}
    with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)


def _write_charter(project_dir, rel_charter_path, done_when, escalate_when=None):
    path = os.path.join(project_dir, rel_charter_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# Fixture charter\n", "\n", "## Done when\n"]
    lines += [f"- {b}\n" for b in done_when]
    if escalate_when:
        lines += ["\n", "## Escalate when\n"] + [f"- {b}\n" for b in escalate_when]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    return path


def _write_factory_atom(project_dir, rel_dir, ac_id, done_when=None):
    spec_dir = os.path.join(project_dir, rel_dir)
    os.makedirs(spec_dir, exist_ok=True)
    spec_path = os.path.join(spec_dir, "feat-fixture.md")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Fixture feature\n\n<!-- normative -->\n"
            f"- **{ac_id}** (Requirement): the fixture behaves.\n<!-- /normative -->\n"
        )
    contract_path = os.path.join(spec_dir, "acceptance-contract.yaml")
    spec_ref = os.path.relpath(spec_path, project_dir)
    contract_ref = os.path.relpath(contract_path, project_dir)
    doc = {
        "spec_ref": spec_ref,
        "spec_sha256": fc.spec_sha256(spec_path),
        "scope": {"allowed_paths": ["dummy/**"]},
        "checkpoints": [
            {"ac_id": ac_id, "surface": "file:dummy.md", "locator": "x",
             "expect": {"op": "matches", "value": "x", "baseline": "pre-change"}},
        ],
    }
    if done_when:
        doc["done_when"] = list(done_when)
    with open(contract_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
    return spec_ref, contract_ref, spec_path, contract_path


def _authorize(spec_path, contract_path):
    fa.authorize(spec_path, contract_path, operator_id="op_test", merge_autonomy_mode="lean",
                 authorized_at="2026-09-19T00:00:00Z")


def _write_task(tasks_dir, task_id, subject="atom:fixture/x"):
    os.makedirs(tasks_dir, exist_ok=True)
    path = os.path.join(tasks_dir, f"{task_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {"id": task_id, "subject": subject, "description": "", "activeForm": "Working",
             "status": "in_progress", "blocks": [], "blockedBy": []},
            fh,
        )
    return path


def _write_evidence(project_dir, atom_id, rows, at="2099-01-01T00:00:00Z"):
    ev_dir = os.path.join(project_dir, ".foundry", "evidence")
    os.makedirs(ev_dir, exist_ok=True)
    doc = {"atom": atom_id, "done_when": rows, "recorded_by": "test-agent", "at": at}
    with open(os.path.join(ev_dir, f"{atom_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _met_row(locator, at="2099-01-01T00:00:00Z"):
    return {"locator": locator, "status": "met", "evidence": "ok", "at": at}


# ================================================================================================ #
# AC-FLH-3 — never a locator, never a write, no subprocess beyond the one named one
# ================================================================================================ #


def test_hooks_never_write_and_never_execute_locators(tmp_path):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r-write/charters/c-atom.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r-write", [
        {"id": "c-atom", "charter_ref": charter_rel, "depends_on": []},
    ])
    tasks_dir = str(tmp_path / "tasks")
    _write_task(tasks_dir, "1", subject="atom:r-write/c-atom")
    _write_evidence(project_dir, "c-atom", [_met_row("test:tests/test_x.py")])

    before = _tree_snapshot(str(tmp_path))

    write_events = []
    popen_events = []

    def _hook(event, args):
        if event == "open" and len(args) >= 2:
            mode = args[1]
            path_arg = args[0]
            if isinstance(mode, str) and any(ch in mode for ch in ("w", "a", "x", "+")):
                if str(path_arg).startswith(str(tmp_path)):
                    write_events.append((event, args))
        elif event in ("os.rename", "os.remove", "os.mkdir", "os.rmdir",
                       "shutil.copyfile", "shutil.move", "shutil.rmtree"):
            if args and str(args[0]).startswith(str(tmp_path)):
                write_events.append((event, args))
        elif event == "subprocess.Popen":
            popen_events.append(args)

    sys.addaudithook(_hook)

    rc1 = created_hook.run({"task_subject": "atom:r-write/c-atom"}, project_dir=project_dir)
    rc2 = completed_hook.run(
        {"task_subject": "atom:r-write/c-atom", "task_id": "1"},
        project_dir=project_dir, tasks_dir=tasks_dir,
    )

    after = _tree_snapshot(str(tmp_path))

    assert rc1 == 0 and rc2 == 0
    assert write_events == [], f"unexpected write event(s): {write_events}"
    assert before == after, "the tree changed even though no write event fired"

    # AC-FLH-3: the ONE allowed subprocess, and nothing else — TaskCreated's charter-committed
    # check runs it once; TaskCompleted (which never checks authorization) runs no subprocess.
    expected = ["git", "-C", project_dir, "log", "-1", "--format=%H", "--", charter_rel]
    for call_args in popen_events:
        argv = list(call_args[1])
        assert argv == expected, f"unexpected subprocess call: {argv}"
    assert len(popen_events) == 1, f"expected exactly one subprocess call, got {popen_events}"


def _tree_snapshot(root):
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.sep + ".git" in dirpath + os.sep:
            continue
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            st = os.stat(path)
            snap[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
    return snap


# ================================================================================================ #
# AC-FLH-6 — TaskCreated refuses an unauthorized factory atom and an uncommitted charter
# ================================================================================================ #


def test_created_refused_unauthorized_factory_atom_and_uncommitted_charter(tmp_path):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)

    # factory atom: contract validates but carries NO `authorized:` trailer (CONTRACT_FROZEN, not
    # AUTHORIZED) — never frozen via _authorize().
    spec_ref, contract_ref, _spec_path, _contract_path = _write_factory_atom(
        project_dir, ".foundry/releases/r6/specs/f-atom", "AC-F6-1"
    )
    charter_rel = ".foundry/releases/r6/charters/c-atom.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    # charter file exists on disk but is deliberately NEVER committed.

    _write_release(project_dir, "r6", [
        {"id": "f-atom", "spec_ref": spec_ref, "contract_ref": contract_ref, "depends_on": []},
        {"id": "c-atom", "charter_ref": charter_rel, "depends_on": []},
    ])

    rc_f = created_hook.run({"task_subject": "atom:r6/f-atom"}, project_dir=project_dir)
    rc_c = created_hook.run({"task_subject": "atom:r6/c-atom"}, project_dir=project_dir)

    assert rc_f == 2
    assert rc_c == 2

    # capture stderr for each, run again capturing output via subprocess-free re-run + capsys is
    # awkward for a direct call, so re-derive the message the same way the hook does.
    import io
    import contextlib

    buf_f = io.StringIO()
    with contextlib.redirect_stderr(buf_f):
        assert created_hook.run({"task_subject": "atom:r6/f-atom"}, project_dir=project_dir) == 2
    assert "/foundry:authorize" in buf_f.getvalue()

    buf_c = io.StringIO()
    with contextlib.redirect_stderr(buf_c):
        assert created_hook.run({"task_subject": "atom:r6/c-atom"}, project_dir=project_dir) == 2
    assert "commit the charter" in buf_c.getvalue()


# ================================================================================================ #
# AC-FLH-7 — TaskCreated admits authorized shapes, exits 0 untouched on a non-atom subject
# ================================================================================================ #


def test_created_admitted_authorized_shapes_and_non_atom_untouched(tmp_path):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)

    spec_ref, contract_ref, spec_path, contract_path = _write_factory_atom(
        project_dir, ".foundry/releases/r7/specs/f-atom", "AC-F7-1"
    )
    _authorize(spec_path, contract_path)

    charter_rel = ".foundry/releases/r7/charters/c-atom.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)

    _write_release(project_dir, "r7", [
        {"id": "f-atom", "spec_ref": spec_ref, "contract_ref": contract_ref, "depends_on": []},
        {"id": "c-atom", "charter_ref": charter_rel, "depends_on": []},
    ])

    assert created_hook.run({"task_subject": "atom:r7/f-atom"}, project_dir=project_dir) == 0
    assert created_hook.run({"task_subject": "atom:r7/c-atom"}, project_dir=project_dir) == 0

    # any other subject exits 0 untouched — even a nonexistent project dir is never resolved.
    ghost_dir = str(tmp_path / "does-not-exist")
    assert created_hook.run({"task_subject": "fix the flaky test"}, project_dir=ghost_dir) == 0
    assert created_hook.run({"description": "no task_subject at all"}, project_dir=ghost_dir) == 0

    # exercise the real stdin/argv CLI wiring (AC-FLH-4's "read their payload from stdin") once,
    # over the same non-atom subject, via a real subprocess.
    cli = os.path.join(REPO_ROOT, "hooks", "foundry-task-created.py")
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = ghost_dir
    proc = subprocess.run(
        [sys.executable, cli],
        input=json.dumps({"task_subject": "fix the flaky test", "cwd": ghost_dir}),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr


# ================================================================================================ #
# AC-FLH-8 — TaskCreated refuses an unresolvable release, a non-slug id, and a loader crash
# ================================================================================================ #


def test_created_refused_unresolvable_release_nonslug_id_and_loader_crash(tmp_path, monkeypatch):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    _write_release(project_dir, "r8", [
        {"id": "f-atom", "charter_ref": ".foundry/releases/r8/charters/c.md", "depends_on": []},
    ])

    # unresolvable release: no release.yaml at all for this id.
    assert created_hook.run({"task_subject": "atom:no-such-release/foo"}, project_dir=project_dir) == 2

    # unresolvable atom: the release exists, the atom id does not.
    assert created_hook.run({"task_subject": "atom:r8/no-such-atom"}, project_dir=project_dir) == 2

    # non-slug id: extra '/' / '..' inside the release-id portion is recognized as an ATTEMPTED
    # atom reference (not silently ignored) and refused, never silently passed through as
    # "any other subject".
    parsed = ffh.parse_atom_subject("atom:../../etc/passwd/foo")
    assert parsed == ("../../etc/passwd", "foo")
    assert created_hook.run({"task_subject": "atom:../../etc/passwd/foo"}, project_dir=project_dir) == 2

    # loader crash: an internal error injected into foundry_release.load_release itself.
    def _boom(*_a, **_k):
        raise RuntimeError("injected loader crash")

    monkeypatch.setattr(ffh.fr, "load_release", _boom)
    assert created_hook.run({"task_subject": "atom:r8/f-atom"}, project_dir=project_dir) == 2


# ================================================================================================ #
# AC-FLH-9 — TaskCompleted refuses missing/older/unmet/absent-locator/task-miss
# ================================================================================================ #


def test_completed_refused_missing_older_unmet_absent_locator_and_task_miss(tmp_path):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r9/charters/c-atom.md"
    _write_charter(project_dir, charter_rel, done_when=[
        "test:tests/test_alpha.py", "test:tests/test_beta.py",
    ])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r9", [
        {"id": "c-atom", "charter_ref": charter_rel, "depends_on": []},
    ])
    tasks_dir = str(tmp_path / "tasks")
    _write_task(tasks_dir, "1", subject="atom:r9/c-atom")

    payload = {"task_subject": "atom:r9/c-atom", "task_id": "1"}

    # (a) missing record — no evidence file at all.
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # (b) record older than the task — top-level `at` predates the (real, ~2026) task file.
    _write_evidence(
        project_dir, "c-atom",
        [_met_row("test:tests/test_alpha.py", at="2000-01-01T00:00:00Z"),
         _met_row("test:tests/test_beta.py", at="2000-01-01T00:00:00Z")],
        at="2000-01-01T00:00:00Z",
    )
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # (c) an unmet locator.
    _write_evidence(project_dir, "c-atom", [
        _met_row("test:tests/test_alpha.py"),
        {"locator": "test:tests/test_beta.py", "status": "unmet", "evidence": "still red",
         "at": "2099-01-01T00:00:00Z"},
    ])
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # (d) a locator absent from the record entirely.
    _write_evidence(project_dir, "c-atom", [_met_row("test:tests/test_alpha.py")])
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # (e) a task id absent from the tasks dir.
    _write_evidence(project_dir, "c-atom", [
        _met_row("test:tests/test_alpha.py"), _met_row("test:tests/test_beta.py"),
    ])
    missing_task_payload = {"task_subject": "atom:r9/c-atom", "task_id": "does-not-exist"}
    assert completed_hook.run(missing_task_payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # bonus (AC-FLH-2, not its own named checkpoint): an atom with no done_when declared at all.
    no_dw_charter = ".foundry/releases/r9/charters/no-dw.md"
    path = os.path.join(project_dir, no_dw_charter)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# No done-when section\n")
    _commit(project_dir, no_dw_charter)
    doc_path = os.path.join(project_dir, ".foundry", "releases", "r9", "release.yaml")
    with open(doc_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    doc["atoms"].append({"id": "no-dw-atom", "charter_ref": no_dw_charter, "depends_on": []})
    with open(doc_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
    _write_task(tasks_dir, "2", subject="atom:r9/no-dw-atom")
    no_dw_payload = {"task_subject": "atom:r9/no-dw-atom", "task_id": "2"}
    assert completed_hook.run(no_dw_payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2


# ================================================================================================ #
# AC-FLH-10 — TaskCompleted admits a newer, complete record; a crash in the evidence reader refuses
# ================================================================================================ #


def test_completed_admitted_newer_complete_record_and_crash_refuses(tmp_path, monkeypatch):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r10/charters/c-atom.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_only.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r10", [
        {"id": "c-atom", "charter_ref": charter_rel, "depends_on": []},
    ])
    tasks_dir = str(tmp_path / "tasks")
    _write_task(tasks_dir, "1", subject="atom:r10/c-atom")
    _write_evidence(project_dir, "c-atom", [_met_row("test:tests/test_only.py")])

    payload = {"task_subject": "atom:r10/c-atom", "task_id": "1"}
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 0

    def _boom(*_a, **_k):
        raise RuntimeError("injected evidence-reader crash")

    monkeypatch.setattr(ffh, "read_evidence_record", _boom)
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2
