"""tests/test_floor_hooks.py — feat-foundry-authorization-floor-hooks (AC-FLH-1..11).

Drives `scripts/foundry_floor_hooks.py` (the shared parsing/authorization/evidence helpers) and
both hook entry points (`hooks/foundry-task-created.py`, `hooks/foundry-task-completed.py`) via
direct import (`conftest.load_module`, the sibling suites' own idiom) over throwaway `tmp_path`
fixture trees — never the real `.foundry/` state or the real `~/.claude/tasks/` corpus.

Test names are the acceptance contract's own checkpoint locators (AC-FLH-3/-6/-7/-8/-9/-10) — each
bundles every scenario its AC names into one function, matching the contract's binding exactly.
Review round 1 findings are covered either as an added scenario inside one of those six, or as a
NEW test alongside them (never by renaming/removing a contract-bound test).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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


def _soon(offset_seconds=5):
    """A REAL near-now UTC timestamp (review round 1 item 4 — fixtures must never use a
    perpetually-future stamp like `2099-01-01`, which the shared module now refuses outright)."""
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _write_evidence(project_dir, atom_id, rows, at=None):
    ev_dir = os.path.join(project_dir, ".foundry", "evidence")
    os.makedirs(ev_dir, exist_ok=True)
    doc = {"atom": atom_id, "done_when": rows, "recorded_by": "test-agent", "at": at or _soon()}
    with open(os.path.join(ev_dir, f"{atom_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _met_row(locator, at=None):
    return {"locator": locator, "status": "met", "evidence": "ok", "at": at or _soon()}


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
    # atom reference (not silently ignored) and refused — under review round 1 item 7's unified
    # rule this is now `MalformedAtomSubjectError` (the exact-form regex requires a single slash
    # and slug-only characters), not a bare tuple with an unvalidated id.
    with pytest.raises(ffh.MalformedAtomSubjectError):
        ffh.parse_atom_subject("atom:../../etc/passwd/foo")
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
         "at": _soon()},
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

    # bonus (review round 1 item 4): a far-future timestamp (e.g. 2099) no longer satisfies
    # "newer than the task" forever — the top-level record AND a per-row `at` are both bounded to
    # "not more than 300s past the real current time".
    _write_evidence(project_dir, "c-atom", [
        _met_row("test:tests/test_alpha.py", at="2099-01-01T00:00:00Z"),
        _met_row("test:tests/test_beta.py", at="2099-01-01T00:00:00Z"),
    ], at="2099-01-01T00:00:00Z")
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # a per-row (not top-level) far-future `at`, with a NEAR-now top-level record `at`.
    _write_evidence(project_dir, "c-atom", [
        _met_row("test:tests/test_alpha.py", at="2099-01-01T00:00:00Z"),
        _met_row("test:tests/test_beta.py"),
    ])
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2


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


# ================================================================================================ #
# Review round 1, item 7 — ONE rule for a subject that starts with `atom:`
# ================================================================================================ #


def test_created_refused_malformed_atom_subject_variants(tmp_path):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    _write_release(project_dir, "r-variants", [
        {"id": "atom-a", "charter_ref": ".foundry/releases/r-variants/charters/atom-a.md",
         "depends_on": []},
    ])

    malformed = [
        "Atom:r-variants/atom-a",     # uppercase leading letter
        "ATOM:R-VARIANTS/ATOM-A",     # all-uppercase throughout
        "atom:r-variants/atom-a ",    # trailing space
        " atom:r-variants/atom-a",    # leading space
        "atom:r-variants/atom-a\n",   # trailing newline
        "atom:r-\nvariants/atom-a",   # embedded newline
        "atom:../../etc/foo",         # traversal attempt
    ]
    for subject in malformed:
        with pytest.raises(ffh.MalformedAtomSubjectError):
            ffh.parse_atom_subject(subject)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = created_hook.run({"task_subject": subject}, project_dir=project_dir)
        assert rc == 2, subject
        assert subject.strip("\n") in buf.getvalue() or "atom:" in buf.getvalue(), buf.getvalue()

    # homoglyph-free: a Cyrillic "а" (U+0430) substituted for the Latin "a" in "atom:" does NOT
    # case-fold to the ASCII "atom:" prefix — it is simply a different subject that never even
    # looks like an atom reference, so it exits 0 untouched (no bypass in either direction: it is
    # neither wrongly admitted NOR wrongly treated as a malformed atom attempt).
    homoglyph_subject = "аtom:r-variants/atom-a"
    assert ffh.parse_atom_subject(homoglyph_subject) is None
    assert created_hook.run({"task_subject": homoglyph_subject}, project_dir=project_dir) == 0

    # the exact form itself must still be admitted once actually authorized — proving the strict
    # regex is not accidentally over-tight.
    charter_rel = ".foundry/releases/r-variants/charters/atom-a.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)
    assert created_hook.run({"task_subject": "atom:r-variants/atom-a"}, project_dir=project_dir) == 0


# ================================================================================================ #
# Review round 1, item 1 — unparseable stdin
# ================================================================================================ #


def test_hooks_refuse_or_admit_on_unparseable_stdin(tmp_path):
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = project_dir

    created_cli = os.path.join(REPO_ROOT, "hooks", "foundry-task-created.py")
    completed_cli = os.path.join(REPO_ROOT, "hooks", "foundry-task-completed.py")

    # truncated/invalid JSON that still MENTIONS "atom:" (any case) -> exit 2, fail-closed, naming
    # the parse error.
    for cli, mention in (
        (created_cli, '{"task_subject": "atom:x/y"'),
        (completed_cli, '{"task_subject": "ATOM:X/Y"'),
    ):
        p = subprocess.run([sys.executable, cli], input=mention, capture_output=True, text=True, env=env)
        assert p.returncode == 2, (cli, p.stdout, p.stderr)
        assert "atom:" in p.stderr.lower()

    # invalid JSON with NO "atom:" mention anywhere -> exit 0, untouched.
    for cli in (created_cli, completed_cli):
        p = subprocess.run(
            [sys.executable, cli], input="{completely not json at all",
            capture_output=True, text=True, env=env,
        )
        assert p.returncode == 0, (cli, p.stdout, p.stderr)

    # empty stdin -> exit 0 (no task_subject at all).
    for cli in (created_cli, completed_cli):
        p = subprocess.run([sys.executable, cli], input="", capture_output=True, text=True, env=env)
        assert p.returncode == 0, (cli, p.stdout, p.stderr)


# ================================================================================================ #
# Review round 1, item 3 — hooks.json wiring: every command's script exists / is executable
# ================================================================================================ #


def test_hooks_json_commands_resolve_to_executable_scripts():
    with open(os.path.join(REPO_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        doc = json.load(fh)

    checked = 0
    for _event, groups in doc["hooks"].items():
        for group in groups:
            for h in group.get("hooks", []):
                if h.get("type") != "command":
                    continue
                command = h["command"]
                resolved = command.replace('"${CLAUDE_PLUGIN_ROOT}"', REPO_ROOT)
                tokens = shlex.split(resolved)
                assert tokens, command
                exe_token = tokens[0]
                direct = True
                if exe_token in ("python3", "bash", "sh") and len(tokens) > 1:
                    exe_token = tokens[1]
                    direct = False
                assert os.path.isfile(exe_token), f"{command!r} names a missing script: {exe_token}"
                if direct:
                    assert os.access(exe_token, os.X_OK), (
                        f"{command!r} is invoked directly (exec-bit + shebang dependent — a lost "
                        f"exec bit would exit 126/127, neither of which is exit 2) but "
                        f"{exe_token} is not X_OK"
                    )
                checked += 1
    assert checked >= 10, "sanity: expected to walk many hooks.json command entries"

    # the two enforcement hooks THIS atom ships are direct-invoked, matching every other command
    # entry in the file (review round 1 item 3 considered an explicit python3 prefix; rejected —
    # see the file's own top-level "//" comment for why: scripts/foundry-doctor.py's check_hooks
    # takes a command's first whitespace token as ITS script, with no interpreter-prefix
    # awareness, and is a frozen probe outside this atom's contract scope). The loop above already
    # proves both scripts are X_OK; this proves they are wired at all.
    all_commands = [
        h["command"]
        for groups in doc["hooks"].values()
        for group in groups
        for h in group.get("hooks", [])
        if h.get("type") == "command"
    ]
    assert any(
        c == '"${CLAUDE_PLUGIN_ROOT}"/hooks/foundry-task-created.py' for c in all_commands
    ), all_commands
    assert any(
        c == '"${CLAUDE_PLUGIN_ROOT}"/hooks/foundry-task-completed.py' for c in all_commands
    ), all_commands


# ================================================================================================ #
# Review round 1, item 5 — the st_birthtime platform gate
# ================================================================================================ #


def test_task_created_at_falls_back_without_st_birthtime(tmp_path, monkeypatch):
    tasks_dir = str(tmp_path / "tasks")
    path = _write_task(tasks_dir, "1")
    real_stat = os.stat

    class _NoBirthtimeStat:
        """Wraps a real `os.stat_result`, hiding `st_birthtime` entirely — `hasattr(st,
        "st_birthtime")` must read `False` through this wrapper, exactly like a real Linux
        `stat_result` (review round 1 item 5)."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "st_birthtime":
                raise AttributeError(name)
            return getattr(self._real, name)

    def _stat(p, *a, **k):
        r = real_stat(p, *a, **k)
        if os.path.abspath(str(p)) == os.path.abspath(path):
            return _NoBirthtimeStat(r)
        return r

    monkeypatch.setattr(ffh.os, "stat", _stat)
    dt = ffh.task_created_at("1", tasks_dir=tasks_dir)
    assert isinstance(dt, datetime)
    # falls back to st_mtime, still a sane recent UTC time (not epoch-zero, not raising).
    assert dt > datetime(2020, 1, 1, tzinfo=timezone.utc)
    # AC-RES-1: the returned value names which signal it came from.
    assert dt.source == "mtime"


def test_task_created_at_source_is_birthtime_when_platform_exposes_it(tmp_path):
    tasks_dir = str(tmp_path / "tasks")
    _write_task(tasks_dir, "1")
    dt = ffh.task_created_at("1", tasks_dir=tasks_dir)
    # This suite runs on whatever platform CI/the operator use — assert only that a platform
    # WITH `st_birthtime` (this repo's own dev/CI hosts, macOS + most modern Linux CI images with
    # a real filesystem, both expose it) reports "birthtime", never silently mislabeling it.
    if hasattr(os.stat(os.path.join(tasks_dir, "1.json")), "st_birthtime"):
        assert dt.source == "birthtime"


def test_completed_hook_names_freshness_caveat_on_mtime_fallback(tmp_path, monkeypatch, capsys):
    """AC-RES-1: on the fallback path (no `st_birthtime`), a refusal for a stale evidence record
    names the caveat text verbatim so a builder debugging a refusal on Linux is not left guessing
    why "creation time" moved."""
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r-caveat/charters/c-atom.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r-caveat", [
        {"id": "c-atom", "charter_ref": charter_rel, "depends_on": []},
    ])
    tasks_dir = str(tmp_path / "tasks")
    path = _write_task(tasks_dir, "1", subject="atom:r-caveat/c-atom")

    real_stat = os.stat

    class _NoBirthtimeStat:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "st_birthtime":
                raise AttributeError(name)
            return getattr(self._real, name)

    def _stat(p, *a, **k):
        r = real_stat(p, *a, **k)
        if os.path.abspath(str(p)) == os.path.abspath(path):
            return _NoBirthtimeStat(r)
        return r

    monkeypatch.setattr(ffh.os, "stat", _stat)

    # a record older than the task's (fallback, mtime-baselined) creation time.
    _write_evidence(project_dir, "c-atom", [
        _met_row("test:tests/test_x.py", at="2000-01-01T00:00:00Z"),
    ], at="2000-01-01T00:00:00Z")

    payload = {"task_subject": "atom:r-caveat/c-atom", "task_id": "1"}
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2
    err = capsys.readouterr().err
    assert "freshness baseline is mtime on this platform" in err, err


# ================================================================================================ #
# Review round 1, item 6 — cwd realpath + session_id validation
# ================================================================================================ #


def test_created_refused_invalid_cwd_and_session_id(tmp_path):
    # payload cwd that does not resolve to an existing directory -> exit 2.
    ghost = str(tmp_path / "does-not-exist-at-all")
    assert created_hook.run({"task_subject": "atom:r/x", "cwd": ghost}) == 2

    # an unsafe session_id used by the completed hook's tasks-dir resolution -> exit 2.
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r-sid/charters/c.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r-sid", [{"id": "c", "charter_ref": charter_rel, "depends_on": []}])

    for bad_sid in ("../etc", "/etc/passwd", "a/b", "..", "a b"):
        payload = {"task_subject": "atom:r-sid/c", "task_id": "1", "session_id": bad_sid}
        assert completed_hook.run(payload, project_dir=project_dir) == 2, bad_sid

    # a SAFE session_id with no matching tasks dir anywhere is still a clean (not crashing) exit 2.
    payload = {"task_subject": "atom:r-sid/c", "task_id": "1", "session_id": "safe-session-id"}
    assert completed_hook.run(payload, project_dir=project_dir) == 2


# ================================================================================================ #
# Review round 1, item 8 — duplicate locator: last row wins
# ================================================================================================ #


def test_completed_duplicate_locator_last_row_wins(tmp_path):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r-dup/charters/c.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_dup.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r-dup", [{"id": "c", "charter_ref": charter_rel, "depends_on": []}])
    tasks_dir = str(tmp_path / "tasks")
    _write_task(tasks_dir, "1", subject="atom:r-dup/c")
    payload = {"task_subject": "atom:r-dup/c", "task_id": "1"}

    # a stale `met` row followed by a LATER `unmet` row for the SAME locator -> refused.
    _write_evidence(project_dir, "c", [
        _met_row("test:tests/test_dup.py"),
        {"locator": "test:tests/test_dup.py", "status": "unmet", "evidence": "still red",
         "at": _soon()},
    ])
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 2

    # the reverse: an early `unmet` row followed by a LATER `met` row -> admitted.
    _write_evidence(project_dir, "c", [
        {"locator": "test:tests/test_dup.py", "status": "unmet", "evidence": "was red",
         "at": _soon()},
        _met_row("test:tests/test_dup.py"),
    ])
    assert completed_hook.run(payload, project_dir=project_dir, tasks_dir=tasks_dir) == 0


# ================================================================================================ #
# Review round 1, item 9 — two tasks-dir candidate shapes, tried in order
# ================================================================================================ #


def test_completed_tasks_dir_candidates_tried_in_order(tmp_path, monkeypatch):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r-cand/charters/c.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r-cand", [{"id": "c", "charter_ref": charter_rel, "depends_on": []}])
    _write_evidence(project_dir, "c", [_met_row("test:tests/test_x.py")])

    session_id = "deadbeef-full-uuid-not-truncated"
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    full_dir = str(home / ".claude" / "tasks" / session_id)
    truncated_dir = str(home / ".claude" / "tasks" / f"session-{session_id[:8]}")

    payload = {"task_subject": "atom:r-cand/c", "task_id": "1", "session_id": session_id}

    # neither candidate exists yet -> exit 2, naming BOTH paths tried.
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        assert completed_hook.run(payload, project_dir=project_dir) == 2
    assert os.path.join(full_dir, "1.json") in buf.getvalue()
    assert os.path.join(truncated_dir, "1.json") in buf.getvalue()

    # only the TRUNCATED (`session-<8>`) shape exists -> admitted via the second candidate.
    _write_task(truncated_dir, "1", subject="atom:r-cand/c")
    assert completed_hook.run(payload, project_dir=project_dir) == 0

    # the FULL session_id form also works on its own (a fresh session_id/task, no truncated dir).
    session_id_2 = "another-full-session-uuid"
    full_dir_2 = str(home / ".claude" / "tasks" / session_id_2)
    _write_task(full_dir_2, "2", subject="atom:r-cand/c")
    payload_2 = {"task_subject": "atom:r-cand/c", "task_id": "2", "session_id": session_id_2}
    assert completed_hook.run(payload_2, project_dir=project_dir) == 0

    # an explicit --tasks-dir override still takes priority (tried before either candidate).
    explicit_dir = str(tmp_path / "explicit-tasks-dir")
    _write_task(explicit_dir, "3", subject="atom:r-cand/c")
    payload_3 = {"task_subject": "atom:r-cand/c", "task_id": "3", "session_id": "irrelevant-id"}
    assert completed_hook.run(payload_3, project_dir=project_dir, tasks_dir=explicit_dir) == 0


# ================================================================================================ #
# Review round 1, item 10 — the charter git-subprocess failure branch
# ================================================================================================ #


def test_created_charter_git_failure_distinct_from_uncommitted(tmp_path, monkeypatch):
    project_dir = str(tmp_path / "project")
    _init_repo(project_dir)
    charter_rel = ".foundry/releases/r-gitfail/charters/c.md"
    _write_charter(project_dir, charter_rel, done_when=["test:tests/test_x.py"])
    _commit(project_dir, charter_rel)
    _write_release(project_dir, "r-gitfail", [
        {"id": "c", "charter_ref": charter_rel, "depends_on": []},
    ])
    payload = {"task_subject": "atom:r-gitfail/c"}

    # (a) the subprocess itself fails to even run (e.g. no git binary) -> OSError.
    def _raise_oserror(*_a, **_k):
        raise OSError("git binary not found")

    monkeypatch.setattr(ffh.subprocess, "run", _raise_oserror)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        assert created_hook.run(payload, project_dir=project_dir) == 2
    assert "commit the charter" not in buf.getvalue(), (
        "a git-subprocess failure must be distinct from the ordinary "
        "'not authorized (commit the charter)' refusal"
    )
    assert "git log" in buf.getvalue()

    # (b) the subprocess runs but git itself exits non-zero (e.g. 128, "not a git repository").
    class _Result:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(ffh.subprocess, "run", lambda *_a, **_k: _Result())
    buf2 = io.StringIO()
    with contextlib.redirect_stderr(buf2):
        assert created_hook.run(payload, project_dir=project_dir) == 2
    assert "commit the charter" not in buf2.getvalue()
    assert "128" in buf2.getvalue() or "not a git repository" in buf2.getvalue()


# ------------------------------------------------------------------------------------------ #
# security review round 2 (PR #190): the four residual fail-open shapes
# ------------------------------------------------------------------------------------------ #

def _run_hook_raw(hook_name, raw_bytes, tmp_path):
    cli = os.path.join(REPO_ROOT, "hooks", hook_name)
    return subprocess.run([sys.executable, cli], input=raw_bytes, capture_output=True,
                          cwd=str(tmp_path), timeout=30)


@pytest.mark.parametrize("hook_name", ["foundry-task-created.py", "foundry-task-completed.py"])
def test_round2_wrong_shape_json_naming_an_atom_refuses(hook_name, tmp_path):
    for raw in (b'["atom:r/x"]', b'"atom:r/x"', b'{"task_subject": ["atom:r/x"]}'):
        p = _run_hook_raw(hook_name, raw, tmp_path)
        assert p.returncode == 2, (raw, p.stderr)
        assert b"REFUSED" in p.stderr
    p = _run_hook_raw(hook_name, b'["not an atom"]', tmp_path)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("hook_name", ["foundry-task-created.py", "foundry-task-completed.py"])
def test_round2_non_utf8_stdin_never_exits_1(hook_name, tmp_path):
    p = _run_hook_raw(hook_name, b'{"task_subject": "caf\xe9 task"}', tmp_path)
    assert p.returncode in (0, 2), p.stderr
    p = _run_hook_raw(hook_name, b'{"task_subject": "atom:r/x\xff"}', tmp_path)
    assert p.returncode == 2, p.stderr


def test_round2_evidence_record_must_name_the_same_atom(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".foundry" / "evidence").mkdir(parents=True)
    rec = {"atom": "other-atom", "done_when": [], "recorded_by": "x", "at": "2026-09-19T00:00:00Z"}
    (proj / ".foundry" / "evidence" / "my-atom.json").write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(ffh.FloorHookError, match="names atom"):
        ffh.read_evidence_record("my-atom", str(proj))
