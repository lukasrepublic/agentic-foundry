"""feat-foundry-control-plane-preflight (v1.2) — AC-CPP-1..9.

Drives the REAL doctor callables (`scripts/foundry-doctor.py`) and the real
`scripts/foundry_control_plane.py` CLI over materialized fixture trees (`tmp_path`) — never a
standalone selftest. Test names are the contract's `-k` selectors; each named case is its OWN
test function, because `pytest -k` proves only "everything matched passed", never "each named
case exists" (`assert-on-structure-not-substrings` / anti-tautology discipline).

THE LOAD-BEARING CONSTRAINT IS "NO FALSE POSITIVES" (AC-CPP-6): these checks run at every session
start. `legitimate_layouts_stay_green` drives all six negative controls through the SAME
`check_control_plane` / `find_ancestor_control_plane` callables the RED tests use, so a check that
convicts a correctly-rooted plane is caught here, not discovered live.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REPO_ROOT, load_module

cp = load_module("scripts/foundry_control_plane.py", "foundry_control_plane")
doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor")

CLI = os.path.join(REPO_ROOT, "scripts", "foundry_control_plane.py")


# --------------------------------------------------------------------------------------- #
# fixture helpers
# --------------------------------------------------------------------------------------- #
def _write_manifest(dirpath, doc):
    d = os.path.join(dirpath, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "foundry-project.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)


def _write_malformed_manifest(dirpath):
    d = os.path.join(dirpath, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "foundry-project.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ")


def _mkdirs(*parts):
    p = os.path.join(*parts)
    os.makedirs(p, exist_ok=True)
    return p


def _run_cli(start_dir, *extra_args, env_overrides=None):
    e = dict(os.environ)
    e.pop("CLAUDE_PROJECT_DIR", None)
    if env_overrides:
        e.update(env_overrides)
    return subprocess.run(
        [sys.executable, CLI, start_dir, *extra_args],
        capture_output=True, text=True, timeout=30, env=e,
    )


def _run_doctor(project_dir, *extra_args, plugin_root=None):
    e = dict(os.environ)
    e["CLAUDE_PROJECT_DIR"] = project_dir
    e["CLAUDE_PLUGIN_ROOT"] = plugin_root or REPO_ROOT
    return subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py"), *extra_args],
        capture_output=True, text=True, timeout=30, env=e,
    )


def _seed_minimal_operator_registry(project_dir):
    """So a doctor subprocess run against a throwaway project_dir only ever goes RED because of
    the control-plane check under test — never because the operator-registry probe (an unrelated,
    pre-existing check) also has something to say about an empty fixture tree."""
    d = os.path.join(project_dir, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "foundry-operators.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "operators": {
            "op_test": {"name": "T", "github": "t", "added_at": "2026-01-01"}}}, f)


# ============================================================================================ #
# AC-CPP-1 — dangling repos{} path
# ============================================================================================ #
def test_dangling_repo_path_is_red(tmp_path):
    # Each case below lives in its OWN sibling directory under `tmp_path` — never nested inside
    # one another — so the ancestor-plane walk (a DIFFERENT check, AC-CPP-2/-3) never sees one
    # case's manifest while evaluating another's.
    project = _mkdirs(str(tmp_path), "project-with-ghost")
    _write_manifest(project, {
        "schema_version": 1,
        "repos": {"ghost": {"path": "does-not-exist-anywhere"}},
    })
    ok, detail = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is False
    assert "ghost" in detail
    assert "does-not-exist-anywhere" in detail

    # AC-CPP-1's own "ok" cases: no repos{} at all, and repos{} with only the workspace
    # self-entry (path ".") — both must stay green.
    empty = _mkdirs(str(tmp_path), "empty-manifest")
    _write_manifest(empty, {"schema_version": 1})
    ok2, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=empty)
    assert ok2 is True

    ws_only = _mkdirs(str(tmp_path), "workspace-only")
    _write_manifest(ws_only, {"schema_version": 1, "repos": {"workspace": {"path": "."}}})
    ok3, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=ws_only)
    assert ok3 is True


# ============================================================================================ #
# AC-CPP-2 — session rooted IN a hosted repo
# ============================================================================================ #
def test_session_rooted_in_hosted_repo_is_red(tmp_path):
    plane = _mkdirs(str(tmp_path), "plane")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})

    finding = cp.find_ancestor_control_plane(hosted)
    assert finding is not None
    assert finding["kind"] == "hosted"
    assert os.path.realpath(finding["ancestor"]) == os.path.realpath(plane)
    assert finding["key"] == "hosted"

    ok, detail = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=hosted)
    assert ok is False
    assert os.path.realpath(plane) in detail
    assert "hosted" in detail
    assert "start the session at" in detail


# ============================================================================================ #
# AC-CPP-3 — session rooted BELOW a control plane, not itself a named hosted repo
# ============================================================================================ #
def test_session_below_control_plane_is_red(tmp_path):
    plane = _mkdirs(str(tmp_path), "plane2")
    scratch = _mkdirs(plane, "scratch", "deeper", "still")
    _write_manifest(plane, {"schema_version": 1, "repos": {}})

    finding = cp.find_ancestor_control_plane(scratch)
    assert finding is not None
    assert finding["kind"] == "subdir"
    assert os.path.realpath(finding["ancestor"]) == os.path.realpath(plane)

    ok, detail = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=scratch)
    assert ok is False
    assert os.path.realpath(plane) in detail
    assert "subdirectory of the control plane" in detail


# ============================================================================================ #
# AC-CPP-3b — co-occurrence yields exactly ONE finding, AC-CPP-2's
# ============================================================================================ #
def test_co_occurrence_reports_one_finding(tmp_path):
    plane = _mkdirs(str(tmp_path), "plane3")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})

    # start_dir == the hosted repo's own root: BOTH AC-CPP-2 (named hosted repo) and AC-CPP-3
    # (a strict subdirectory of the plane) hold simultaneously for this exact input.
    finding = cp.find_ancestor_control_plane(hosted)
    assert finding["kind"] == "hosted"  # the more specific finding wins, not "subdir"

    ok, detail = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=hosted)
    assert ok is False
    # Exactly one control-plane finding — one "remedy:" clause, not two concatenated ones.
    assert detail.count("remedy") == 1
    assert "subdirectory of the control plane" not in detail


# ============================================================================================ #
# AC-CPP-4 — the scripted CLI: invocable standalone, exits non-zero, names the plane + remedy
# ============================================================================================ #
def test_preflight_cli_exits_nonzero_and_names_plane(tmp_path):
    plane = _mkdirs(str(tmp_path), "plane4")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})

    r = _run_cli(hosted)
    assert r.returncode != 0, r.stdout + r.stderr
    assert os.path.realpath(plane) in r.stdout
    assert "remedy" in r.stdout.lower() or "start the session at" in r.stdout


# ============================================================================================ #
# AC-CPP-4 — the override flag exits 0 but STILL reports the finding (no silent bypass)
# ============================================================================================ #
def test_override_exits_zero_but_still_reports(tmp_path):
    plane = _mkdirs(str(tmp_path), "plane5")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})

    r = _run_cli(hosted, "--override")
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.path.realpath(plane) in r.stdout
    assert "override" in r.stdout.lower()

    # And WITHOUT --override, the same fixture is refused (proves --override is what flips it).
    r2 = _run_cli(hosted)
    assert r2.returncode != 0


# ============================================================================================ #
# AC-CPP-4b — init's SKILL.md names the preflight BEFORE the first write step
# ============================================================================================ #
def test_init_skill_names_preflight_before_first_write():
    path = os.path.join(REPO_ROOT, "skills", "init", "SKILL.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()

    preflight_idx = text.find("foundry_control_plane.py")
    assert preflight_idx != -1, "SKILL.md does not name the scripted preflight at all"

    # The first step that WRITES a file is "Create `.claude/foundry-operators.json`" (the
    # operator-registry step) — the earliest concrete file-write instruction in the procedure.
    first_write_idx = text.find("Create `.claude/foundry-operators.json`")
    assert first_write_idx != -1, "SKILL.md's first write step marker not found"

    assert preflight_idx < first_write_idx, (
        "the control-plane preflight must be named BEFORE the first write step, "
        f"got preflight@{preflight_idx} write@{first_write_idx}"
    )

    # The instruction to stop on a non-zero exit must live in the SAME step (between the
    # preflight mention and the next numbered step after it).
    next_step_idx = text.find("\n2. **", preflight_idx)
    assert next_step_idx != -1
    step_text = text[preflight_idx:next_step_idx].lower()
    assert "stop" in step_text and "non-zero" in step_text


# ============================================================================================ #
# AC-CPP-5 — the walk crosses ancestor .git boundaries (both the plane's own and the hosted
# repo's own) to find the plane
# ============================================================================================ #
def test_walk_crosses_git_boundary_to_find_plane(tmp_path):
    plane = _mkdirs(str(tmp_path), "cross-plane")
    _mkdirs(plane, ".git")  # the CONTROL PLANE also carries its own .git (it is a repo too)
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})

    hosted = _mkdirs(plane, "hosted")
    _mkdirs(hosted, ".git")  # the HOSTED repo carries ITS OWN .git — a `git`-style walk that
    # stopped at the nearest repository root would halt right here and never see the plane.

    deep = _mkdirs(hosted, "src", "pkg")

    finding = cp.find_ancestor_control_plane(deep)
    assert finding is not None
    assert os.path.realpath(finding["ancestor"]) == os.path.realpath(plane)


# ============================================================================================ #
# AC-CPP-5 — the walk does NOT stop at a filesystem-mount boundary between the hosted repo and
# the plane (the bind-mounted-container layout this product's own docs promote). A REAL mount
# cannot be created without root in this sandbox, so this asserts the property that actually
# carries the guarantee: nothing in the walk consults device/mount identity at all — it is a
# plain directory-name walk that cannot distinguish a mount boundary from an ordinary directory
# boundary, so it cannot special-case (stop at) one. Mirrors AC-CPP-9's own case-fold residual
# note: asserted by fixture on infrastructure that cannot materialize the real-world condition.
# ============================================================================================ #
def test_walk_does_not_stop_at_mount_boundary(tmp_path):
    plane = _mkdirs(str(tmp_path), "mount-plane")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})
    # "hosted" simulates a separately-bind-mounted volume: nested several levels below the
    # plane, with no special marker distinguishing it from an ordinary subdirectory — exactly
    # what a mount point looks like to a walk that reads directory names only.
    hosted = _mkdirs(plane, "hosted")
    deep = _mkdirs(hosted, "var", "lib", "app")

    finding = cp.find_ancestor_control_plane(deep)
    assert finding is not None
    assert os.path.realpath(finding["ancestor"]) == os.path.realpath(plane)

    import inspect
    src = inspect.getsource(cp)
    assert "st_dev" not in src and "stat(" not in src, (
        "the walk must not consult device/mount identity — doing so risks stopping at a mount "
        "boundary, exactly the defect AC-CPP-5 was rewritten (v1.2) to remove"
    )


# ============================================================================================ #
# AC-CPP-5 — bounded (32 levels), symlink-safe, malformed-manifest tolerant
# ============================================================================================ #
def test_walk_is_bounded_and_safe(tmp_path):
    # Each sub-scenario below lives under its OWN isolated top-level directory so none of their
    # planted manifests leak into another sub-scenario's ancestor walk.

    # -- bounded at exactly 32 levels --
    bound_root = _mkdirs(str(tmp_path), "bound-scenario")
    chain = bound_root
    for i in range(33):
        chain = _mkdirs(chain, f"L{i}")
    start = chain  # 33 ancestor levels exist ABOVE bound_root's own L0 child

    _write_manifest(bound_root, {"schema_version": 1, "repos": {}})
    # bound_root is the 33rd ancestor of `start` (L0..L32 are 33 levels; start is inside L32, so
    # its parent chain up through L0 is 32 hops, and one more hop reaches bound_root itself — the
    # 33rd ancestor, out of the 32-level bound).
    assert cp.find_ancestor_control_plane(start, max_levels=32) is None
    # Raising the bound by one finds it — proves the miss above is the BOUND, not a bug.
    assert cp.find_ancestor_control_plane(start, max_levels=34) is not None

    # -- malformed ancestor manifest is skipped, not raised, and the walk continues upward --
    malformed_scenario = _mkdirs(str(tmp_path), "malformed-scenario")
    outer = _mkdirs(malformed_scenario, "outer")
    _write_manifest(outer, {"schema_version": 1, "repos": {}})  # the REAL plane
    garbage = _mkdirs(outer, "garbage")
    _write_malformed_manifest(garbage)  # an ancestor with UNPARSEABLE JSON, nearer than `outer`
    leaf = _mkdirs(garbage, "leaf")

    finding = cp.find_ancestor_control_plane(leaf)  # must not raise
    assert finding is not None
    assert os.path.realpath(finding["ancestor"]) == os.path.realpath(outer)

    # -- does not follow a symlink out of the starting tree: realpath resolution happens ONCE,
    # up front, so a symlink that LOOKS (by its unresolved path) like it sits under a plane must
    # not be convicted via that unresolved path if its REAL target sits elsewhere with no plane
    # above it.
    symlink_scenario = _mkdirs(str(tmp_path), "symlink-scenario")
    real_target = _mkdirs(symlink_scenario, "real-elsewhere", "target")
    decoy_root = _mkdirs(symlink_scenario, "decoy-root")
    _write_manifest(decoy_root, {"schema_version": 1, "repos": {}})  # a plane, but a DECOY
    link_path = os.path.join(decoy_root, "link")
    os.symlink(real_target, link_path)

    finding2 = cp.find_ancestor_control_plane(link_path)
    assert finding2 is None, (
        "the walk must resolve the symlink FIRST and walk the REAL target's ancestry, not the "
        "decoy plane implied by the symlink's own (unresolved) parent directory"
    )


# ============================================================================================ #
# AC-CPP-6 — six legitimate layouts stay green (no false positives)
# ============================================================================================ #
def test_legitimate_layouts_stay_green(tmp_path):
    # (a) a correctly-rooted control-plane session — project dir == the plane root.
    plane_root = _mkdirs(str(tmp_path), "a-plane-root")
    _write_manifest(plane_root, {"schema_version": 1, "repos": {"app": {"path": "app"}}})
    _mkdirs(plane_root, "app")
    ok, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=plane_root)
    assert ok is True
    assert cp.find_ancestor_control_plane(plane_root) is None

    # (b) a single-repo adopter with only the `workspace` self-entry.
    single = _mkdirs(str(tmp_path), "b-single-repo")
    _write_manifest(single, {"schema_version": 1, "repos": {"workspace": {"path": "."}}})
    ok, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=single)
    assert ok is True

    # (c) a standalone repo with no ancestor manifest anywhere.
    standalone = _mkdirs(str(tmp_path), "c-standalone", "nested", "deep")
    ok, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=standalone)
    assert ok is True
    assert cp.find_ancestor_control_plane(standalone) is None

    # (d) a git WORKTREE whose `.git` is a FILE, not a directory — even when nested below an
    # ancestor plane that would otherwise convict it (this factory's own worker-dispatch
    # machinery creates exactly this shape; AC-CPP-6(d) must hold unconditionally).
    plane_d = _mkdirs(str(tmp_path), "d-plane")
    _write_manifest(plane_d, {"schema_version": 1, "repos": {}})
    worktree = _mkdirs(plane_d, "worker", "wt")
    with open(os.path.join(worktree, ".git"), "w", encoding="utf-8") as f:
        f.write("gitdir: /somewhere/else/.git/worktrees/wt\n")
    assert cp.find_ancestor_control_plane(worktree) is None
    ok, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=worktree)
    assert ok is True

    # (e) a path reached through a symlink that resolves OUTSIDE any control plane.
    real_e = _mkdirs(str(tmp_path), "e-real-target")
    link_e = os.path.join(str(tmp_path), "e-symlink")
    os.symlink(real_e, link_e)
    assert cp.find_ancestor_control_plane(link_e) is None
    ok, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=link_e)
    assert ok is True

    # (f) a CI checkout at an arbitrary path with no ancestor manifest.
    ci_checkout = _mkdirs(str(tmp_path), "f-runner-work", "repo-under-test", "checkout")
    ok, _ = doctor.check_control_plane(plugin_root=REPO_ROOT, project_dir=ci_checkout)
    assert ok is True


# ============================================================================================ #
# AC-CPP-9 — normalization CONVICTS: trailing slash / redundant separator / dot segment / a
# case-fold variant are the SAME path and must still go RED, never slip through as a non-match
# ============================================================================================ #
def test_normalized_path_variants_still_convict(tmp_path, monkeypatch):
    plane = _mkdirs(str(tmp_path), "norm-plane")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})

    # trailing slash
    assert cp.find_ancestor_control_plane(hosted + os.sep)["kind"] == "hosted"
    # redundant separator
    assert cp.find_ancestor_control_plane(
        os.path.join(plane, "hosted" + os.sep + os.sep)
    )["kind"] == "hosted"
    # a redundant `.` segment
    assert cp.find_ancestor_control_plane(
        os.path.join(plane, ".", "hosted")
    )["kind"] == "hosted"

    # the shared helper does the equating — never a hand-rolled per-call-site comparison
    assert cp.paths_equal(hosted, hosted + os.sep + os.sep + ".")

    # case-fold on a case-insensitive filesystem: this CI runs on a case-SENSITIVE filesystem
    # (the residual this AC's own text discloses), so we exercise the helper's case-fold BRANCH
    # directly via `os.path.normcase` — the same seam a genuinely case-insensitive OS uses — to
    # prove the shared helper is what carries that behavior, not a hand-rolled comparison that
    # would silently omit it.
    assert cp.paths_equal("/A/B/C", "/a/b/c") is False  # real behavior on THIS (case-sensitive) fs
    monkeypatch.setattr(os.path, "normcase", lambda p: p.lower())
    assert cp.paths_equal("/A/B/C", "/a/b/c") is True  # the SAME helper, case-insensitive branch


# ============================================================================================ #
# AC-CPP-7 — --session-start keeps its fail-open contract even with a control-plane finding
# ============================================================================================ #
def test_session_start_fails_open(tmp_path):
    plane = _mkdirs(str(tmp_path), "so-plane")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})
    _seed_minimal_operator_registry(hosted)

    r = _run_doctor(hosted, "--session-start")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING" in r.stderr
    assert "control-plane" in r.stderr


# ============================================================================================ #
# AC-CPP-7 — the fail-open WARNING carries the offending key/ancestor + the one-line remedy
# ============================================================================================ #
def test_session_start_warning_is_actionable(tmp_path):
    plane = _mkdirs(str(tmp_path), "wa-plane")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})
    _seed_minimal_operator_registry(hosted)

    r = _run_doctor(hosted, "--session-start")
    assert r.returncode == 0
    assert os.path.realpath(plane) in r.stderr
    assert "start the session at" in r.stderr


# ============================================================================================ #
# AC-CPP-7 — the operator-invoked form (no --session-start) exits NON-ZERO on a finding
# ============================================================================================ #
def test_operator_invoked_exits_nonzero(tmp_path):
    plane = _mkdirs(str(tmp_path), "op-plane")
    hosted = _mkdirs(plane, "hosted")
    _write_manifest(plane, {"schema_version": 1, "repos": {"hosted": {"path": "hosted"}}})
    _seed_minimal_operator_registry(hosted)

    r = _run_doctor(hosted)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "DOCTOR-RED" in r.stdout
    assert "control-plane" in r.stdout


# ============================================================================================ #
# AC-CPP-8 — the shipped docs state the residual NARROWLY (not "undetectable in general")
# ============================================================================================ #
def test_residual_is_declared_narrowly():
    path = os.path.join(REPO_ROOT, "docs", "how-to", "multi-repo-control-plane.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()

    assert "per-project" in text.lower()
    assert "user-wide" in text.lower()
    assert "unreachable only" in text.lower() or "unreachable **only**" in text

    # The narrow residual replaces the old, over-broad claim — it must be gone.
    assert "no preflight check currently catches it" not in text

    # And it must NOT claim the mistake is undetectable in general.
    assert "undetectable" not in text.lower() or "not a general claim" in text.lower()
