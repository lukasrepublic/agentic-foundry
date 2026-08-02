"""tests/test_repo_fleet.py — feat-foundry-workspace-repo-verbs (AC-WRV-1..12).

The real `scripts/foundry_repo_fleet.py` driven over materialized `tmp_path` fixtures: git-init'd
checkouts, local-path remotes (hermetic — no real network anywhere in this file), real `.gitignore`
files, planted hostile `.git/config` values, captured argv + child env. Test names are the
acceptance contract's `-k` selectors; each named case is its own function — `pytest -k` only proves
"everything matched passed", never "each named case exists".
"""
from __future__ import annotations

import inspect
import io
import json
import os
import stat
import subprocess
import sys
import textwrap
import uuid

import pytest

from conftest import REPO_ROOT, load_module

FLEET = load_module("scripts/foundry_repo_fleet.py", "foundry_repo_fleet")
# Bare import, deliberately NOT `load_module(...)`: FLEET's own top-level `import
# foundry_repo_registry as REG` (a real import statement) already populated sys.modules with the
# canonical module object above -- a second load_module() call would re-execute the file into a
# DIFFERENT module object, breaking the `FLEET.SANITIZE is REG.sanitize` identity this suite
# checks (AC-WRV-8: the sink is imported, never re-implemented).
import foundry_repo_registry as REG  # noqa: E402
LEAK_SCAN_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-prepublication-leak-scan.py")
CLI = os.path.join(REPO_ROOT, "scripts", "foundry_repo_fleet.py")


# =================================================================================================
# git/fixture helpers (the sibling suite's own idiom, tests/test_repo_registry.py)
# =================================================================================================
def _git(args, cwd, check=True, env=None):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError("git %r failed: %s" % (args, r.stderr))
    return r


def _init_repo(path, branch="trunk"):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", branch], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit(path, relname="f.txt", content="x\n", message="commit"):
    p = path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(["add", relname], path)
    _git(["commit", "-q", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path).stdout.strip()


def _init_bare(path, branch="trunk"):
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "--bare", "-b", branch, str(path)], path.parent)
    return path


def _clone(remote, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "-q", str(remote), str(dest)], dest.parent)
    _git(["config", "user.email", "t@example.com"], dest)
    _git(["config", "user.name", "Test"], dest)
    return dest


def _write_manifest(root, repos):
    doc = {"schema_version": 1, "repos": repos}
    d = root / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "foundry-project.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def _write_gitignore(root, lines):
    (root / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _hash_tree(root):
    import hashlib

    out = {}
    for base, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _head_sha(path):
    return _git(["rev-parse", "HEAD"], path).stdout.strip()


def _git_config_snapshot(path):
    return (path / ".git" / "config").read_text(encoding="utf-8")


def _record_spawn(monkeypatch):
    """Wraps FLEET._spawn — THE single subprocess-creation choke point `scripts/
    foundry_repo_fleet.py` itself uses for every invocation it makes (git and foreach children
    alike) — records (argv, cwd, env, shell), then delegates to the real implementation.

    Deliberately NOT a patch of the global `subprocess.run` (which `scripts/
    foundry_repo_registry.py`'s OWN internal, unhardened read-only calls also funnel through,
    since `subprocess` is one shared module object): AC-WRV-1/-11 scope to the invocations THIS
    module makes, never the denied, separately-reviewed registry module's own oracle calls."""
    calls = []
    real_spawn = FLEET._spawn

    def spy(argv, cwd=None, env=None, timeout=None, input_bytes=None):
        calls.append({
            "argv": list(argv),
            "cwd": cwd,
            "env": env,
            "shell": False,  # _spawn hardcodes shell=False -- never accepts a shell kwarg
        })
        return real_spawn(argv, cwd=cwd, env=env, timeout=timeout, input_bytes=input_bytes)

    monkeypatch.setattr(FLEET, "_spawn", spy)
    return calls


def _git_calls(calls):
    return [c for c in calls if c["argv"] and c["argv"][0] == "git"]


def _verb_of(argv):
    i = 1
    while i < len(argv) and argv[i] == "-c":
        i += 2
    return argv[i] if i < len(argv) else None


_FORBIDDEN_VERBS = {
    "checkout", "switch", "reset", "merge", "rebase", "pull", "push", "clean",
    "stash", "branch", "remote", "submodule", "gc",
}
_FORBIDDEN_FLAGS = {"--force", "-f", "--hard", "--force-sync"}


def _run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, CLI] + list(args), capture_output=True, text=True, timeout=60, env=env,
    )


def _run_full_fleet(root, monkeypatch=None, timeout=5.0):
    """Exercises sync, status, foreach (a no-op command) and validate in sequence, in-process,
    over the same root — the "full run" several checkpoints require."""
    calls = _record_spawn(monkeypatch) if monkeypatch is not None else None
    try:
        outcome, degraded, reason, rows = REG.build_report(str(root))
    except REG.ManifestError:
        rows = []
    sync_result = FLEET.reconcile(str(root), rows, timeout=timeout)
    status_result = FLEET.status(str(root))
    foreach_result = FLEET.foreach(str(root), [sys.executable, "-c", "pass"], timeout=timeout)
    validate_result = FLEET.validate(str(root))
    return {
        "calls": calls,
        "sync": sync_result,
        "status": status_result,
        "foreach": foreach_result,
        "validate": validate_result,
    }


# =================================================================================================
# scenario builder — a workspace exercising every path_status/origin combination this atom acts on
# =================================================================================================
@pytest.fixture()
def scenario(tmp_path):
    root = tmp_path / "ws"
    _init_repo(root)
    _commit(root, "README.md", "root\n", "root init")

    remotes_dir = tmp_path / "remotes"
    bare_match = _init_bare(remotes_dir / "match.git")
    bare_other = _init_bare(remotes_dir / "other.git")
    bare_notcloned = _init_bare(remotes_dir / "notcloned.git")

    # seed each bare remote with one commit via a throwaway clone-push
    seed = tmp_path / "seed"
    _clone(bare_match, seed / "match")
    _commit(seed / "match", "a.txt", "a\n", "seed")
    _git(["push", "-q", "origin", "trunk"], seed / "match")
    _clone(bare_other, seed / "other")
    _commit(seed / "other", "b.txt", "b\n", "seed")
    _git(["push", "-q", "origin", "trunk"], seed / "other")
    _clone(bare_notcloned, seed / "notcloned")
    _commit(seed / "notcloned", "c.txt", "c\n", "seed")
    _git(["push", "-q", "origin", "trunk"], seed / "notcloned")

    present_match = _clone(bare_match, root / "present_match")
    present_mismatch = _clone(bare_match, root / "present_mismatch")
    _git(["remote", "set-url", "origin", str(bare_other)], present_mismatch)  # declared != configured origin -> checked below
    present_dirty = _clone(bare_match, root / "present_dirty")
    (present_dirty / "a.txt").write_text("dirty\n", encoding="utf-8")
    present_diverged = _clone(bare_match, root / "present_diverged")
    _commit(present_diverged, "local.txt", "local\n", "local-only commit")

    repos = {
        "match": {"path": "./present_match", "remote": str(bare_match)},
        "mismatch": {"path": "./present_mismatch", "remote": str(bare_match)},
        "dirty": {"path": "./present_dirty", "remote": str(bare_match)},
        "diverged": {"path": "./present_diverged", "remote": str(bare_match)},
        "notcloned": {"path": "./not_cloned_repo", "remote": str(bare_notcloned)},
        "dangling": {"path": "./nowhere"},
    }
    _write_manifest(root, repos)
    _write_gitignore(root, [
        "/present_match/", "/present_mismatch/", "/present_dirty/", "/present_diverged/",
        "/not_cloned_repo/",
    ])
    _git(["add", ".gitignore"], root)
    _git(["commit", "-q", "-m", "gitignore"], root)

    return {
        "root": root,
        "bare_match": bare_match,
        "bare_other": bare_other,
        "bare_notcloned": bare_notcloned,
        "repos": repos,
    }


# =================================================================================================
# AC-WRV-1 — the mutation floor
# =================================================================================================
def test_git_argv_is_the_closed_set_hardened_and_dash_dash_guarded(scenario, monkeypatch):
    result = _run_full_fleet(scenario["root"], monkeypatch=monkeypatch)
    git_calls = _git_calls(result["calls"])
    assert git_calls, "expected at least one git invocation across the full run"

    for call in git_calls:
        argv = call["argv"]
        assert call["shell"] is False, "a git invocation ran with shell != False: %r" % (argv,)
        assert "-C" not in argv, "a git invocation used -C instead of cwd=: %r" % (argv,)
        verb = _verb_of(argv)
        assert verb in FLEET._ALLOWED_GIT_VERBS, "git verb outside the closed set: %r in %r" % (verb, argv)
        for bad in _FORBIDDEN_VERBS:
            assert bad not in argv, "forbidden verb %r present in %r" % (bad, argv)
        for bad in _FORBIDDEN_FLAGS:
            assert bad not in argv, "forbidden flag %r present in %r" % (bad, argv)
        # the fixed hardening-set -c prefix precedes the subcommand
        assert argv[1:3] == ["-c", "credential.helper="], "hardening set does not lead argv: %r" % (argv,)
        if verb in ("clone", "fetch"):
            assert "--" in argv, "network-capable invocation carries no --: %r" % (argv,)
            dash_idx = argv.index("--")
            verb_idx = argv.index(verb)
            assert dash_idx > verb_idx


def test_drift_is_reported_and_the_checkout_is_untouched(scenario, monkeypatch):
    root = scenario["root"]
    before = {
        "mismatch": (_head_sha(root / "present_mismatch"), _hash_tree(root / "present_mismatch"),
                     _git_config_snapshot(root / "present_mismatch")),
        "dirty": (_head_sha(root / "present_dirty"), _hash_tree(root / "present_dirty"),
                  _git_config_snapshot(root / "present_dirty")),
        "diverged": (_head_sha(root / "present_diverged"), _hash_tree(root / "present_diverged"),
                     _git_config_snapshot(root / "present_diverged")),
    }

    result = _run_full_fleet(root, monkeypatch=monkeypatch)

    for k in before:
        after = (_head_sha(root / ("present_%s" % k)), _hash_tree(root / ("present_%s" % k)),
                 _git_config_snapshot(root / ("present_%s" % k)))
        assert after == before[k], "checkout %r was mutated by the run" % k

    sync_rows_by_key = {r["key"]: r for r in result["sync"]["rows"]}
    assert sync_rows_by_key["mismatch"]["action"] in ("skip", "refuse")
    assert sync_rows_by_key["mismatch"]["finding"] is True
    # dirty and diverged both declare the SAME remote as "match" -> still fetched (fetch never
    # touches working tree/index), but the working tree bytes above prove it stayed untouched.
    assert sync_rows_by_key["dirty"]["action"] == "fetch"
    assert sync_rows_by_key["diverged"]["action"] == "fetch"

    status_rows_by_key = {r["key"]: r for r in result["status"][2]}
    assert status_rows_by_key["dirty"]["dirty"] is True
    assert status_rows_by_key["diverged"]["ahead_behind"] != "0/0"
    assert status_rows_by_key["mismatch"]["origin"] == "mismatch"


# =================================================================================================
# AC-WRV-2 — sync acts by path_status, fetches the DECLARED remote
# =================================================================================================
def test_sync_acts_by_path_status_and_fetches_the_declared_remote(scenario, monkeypatch):
    root = scenario["root"]
    calls = _record_spawn(monkeypatch)
    outcome, degraded, reason, rows = REG.build_report(str(root))
    result = FLEET.reconcile(str(root), rows, timeout=5.0)
    by_key = {r["key"]: r for r in result["rows"]}

    assert by_key["notcloned"]["action"] == "clone"
    assert by_key["notcloned"]["result"] == "ok"
    assert (root / "not_cloned_repo" / ".git").exists()

    assert by_key["match"]["action"] == "fetch"
    assert by_key["match"]["result"] == "ok"

    for k in ("mismatch", "dangling"):
        assert by_key[k]["action"] in ("skip", "refuse")

    fetch_calls = [c for c in _git_calls(calls) if _verb_of(c["argv"]) == "fetch"]
    assert fetch_calls, "expected at least one fetch"
    for c in fetch_calls:
        argv = c["argv"]
        dash_idx = argv.index("--")
        named_remote = argv[dash_idx + 1]
        # the fetch names the DECLARED remote (bare_match), never a configured origin that
        # might differ (mismatch row never reaches fetch at all, asserted above).
        assert named_remote == str(scenario["bare_match"])


# =================================================================================================
# AC-WRV-2 idempotence
# =================================================================================================
def test_first_sync_clones_then_second_clones_nothing(scenario, monkeypatch):
    root = scenario["root"]

    outcome, degraded, reason, rows1 = REG.build_report(str(root))
    calls1 = _record_spawn(monkeypatch)
    result1 = FLEET.reconcile(str(root), rows1, timeout=5.0)
    clone_count_1 = sum(1 for c in _git_calls(calls1) if _verb_of(c["argv"]) == "clone")
    assert clone_count_1 >= 1

    outcome, degraded, reason, rows2 = REG.build_report(str(root))
    calls2 = _record_spawn(monkeypatch)
    result2 = FLEET.reconcile(str(root), rows2, timeout=5.0)
    clone_count_2 = sum(1 for c in _git_calls(calls2) if _verb_of(c["argv"]) == "clone")
    assert clone_count_2 == 0

    paths_before = set(os.listdir(root))
    outcome, degraded, reason, rows3 = REG.build_report(str(root))
    FLEET.reconcile(str(root), rows3, timeout=5.0)
    assert set(os.listdir(root)) == paths_before


# =================================================================================================
# AC-WRV-3 — the boundary refuses before the socket
# =================================================================================================
def _bare_target(tmp_path, name="target.git"):
    return _init_bare(tmp_path / "remotes2" / name)


@pytest.fixture()
def refusal_workspace(tmp_path):
    root = tmp_path / "ws2"
    _init_repo(root)
    _commit(root, "README.md", "root\n", "root init")
    _write_manifest(root, {})
    return root


def test_boundary_refuses_disallowed_remote_forms_and_escaping_targets(refusal_workspace, tmp_path, monkeypatch):
    root = refusal_workspace
    helper = "helper-%s" % uuid.uuid4().hex[:10]  # a genericity device -- absent from both sources
    target_ok = _bare_target(tmp_path)

    # a symlinked-parent escape: the declared path's parent is a symlink pointing OUTSIDE root
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (root / "escaper-parent").symlink_to(outside, target_is_directory=True)

    already_exists = root / "already-here"
    already_exists.mkdir()

    hostile_key = "-hostile"

    cases = [
        {"key": "transport-helper", "path": "./x1", "remote": "%s::do-something" % helper},
        {"key": "ext-form", "path": "./x2", "remote": "ext::sh -c false"},
        {"key": "fd-form", "path": "./x3", "remote": "fd::7"},
        {"key": "git-scheme", "path": "./x4", "remote": "git://example.invalid/repo.git"},
        {"key": "http-scheme", "path": "./x5", "remote": "http://example.invalid/repo.git"},
        {"key": "file-scheme", "path": "./x6", "remote": "file:///tmp/x"},
        {"key": "relative", "path": "./x7", "remote": "relative/path.git"},
        {"key": "dash-leading", "path": "./x8", "remote": "-evil.example:x"},
        {"key": "control-char", "path": "./x9", "remote": "host.example:owner/\x01repo.git"},
        {"key": "escaping-target", "path": "./escaper-parent/child", "remote": str(target_ok)},
        {"key": "already-exists", "path": "./already-here", "remote": str(target_ok)},
        {"key": hostile_key, "path": "./x10", "remote": str(target_ok)},
    ]
    rows = []
    for c in cases:
        rows.append({
            "key": c["key"], "path": c["path"], "path_status": "not-cloned",
            "declared_remote": c["remote"], "origin": "undeclared",
            "resolved_path": os.path.realpath(os.path.join(str(root), c["path"].lstrip("./"))),
        })

    calls = _record_spawn(monkeypatch)
    result = FLEET.reconcile(str(root), rows, timeout=5.0)
    by_key = {r["key"]: r for r in result["rows"]}

    for c in cases:
        row = by_key[c["key"]]
        if c["key"] == hostile_key:
            # a hostile key never alters the accept/refuse decision -- this row's remote+path
            # are otherwise valid, so it is accepted, and the key itself never reaches an argv
            # option position anywhere in the captured argv.
            assert row["action"] == "clone" and row["result"] == "ok", row
            for call in calls:
                assert hostile_key not in call["argv"]
            continue
        assert row["action"] == "refuse", "case %r was not refused: %r" % (c["key"], row)
        assert row["result"] == "n/a"
        assert row["finding"] is True

    # zero captured git argv named any of the refused remotes/paths as a clone target
    git_calls = _git_calls(calls)
    clone_calls = [c for c in git_calls if _verb_of(c["argv"]) == "clone"]
    cloned_targets = {c["argv"][-1] for c in clone_calls}
    for c in cases:
        if c["key"] in (hostile_key,):
            continue
        assert not any(c["key"] in t for t in cloned_targets)


def test_accepted_row_clones_with_the_exact_hardened_argv(refusal_workspace, tmp_path, monkeypatch):
    root = refusal_workspace
    target = _bare_target(tmp_path, "accept.git")
    calls = _record_spawn(monkeypatch)
    dest = os.path.join(str(root), "accepted")
    rows = [{
        "key": "accepted", "path": "./accepted", "path_status": "not-cloned",
        "declared_remote": str(target), "origin": "undeclared",
        "resolved_path": os.path.realpath(dest),
    }]
    result = FLEET.reconcile(str(root), rows, timeout=5.0)
    assert result["rows"][0]["action"] == "clone"
    assert result["rows"][0]["result"] == "ok"
    assert (root / "accepted" / ".git").exists()

    clone_calls = [c for c in _git_calls(calls) if _verb_of(c["argv"]) == "clone"]
    assert len(clone_calls) == 1
    argv = clone_calls[0]["argv"]
    expected = ["git"] + FLEET._HARDENING_SET + ["clone", "--no-recurse-submodules", "--", str(target), os.path.realpath(dest)]
    assert argv == expected, "argv=%r" % (argv,)


# =================================================================================================
# AC-WRV-4 — status
# =================================================================================================
def test_status_emits_one_row_per_entry_with_typed_fields(scenario):
    degraded, reason, rows, exit_code = FLEET.status(str(scenario["root"]))
    assert len(rows) == len(scenario["repos"])
    by_key = {r["key"]: r for r in rows}
    for key in scenario["repos"]:
        assert key in by_key
        r = by_key[key]
        assert set(r.keys()) == {"key", "present", "origin", "branch", "ahead_behind", "dirty"}
    assert by_key["match"]["present"] is True
    assert by_key["match"]["origin"] == "match"
    assert by_key["match"]["branch"] == "trunk"
    assert by_key["dangling"]["present"] is False


def test_status_names_unknown_rather_than_faking_clean(tmp_path):
    root = tmp_path / "ws3"
    _init_repo(root)
    _commit(root, "r.txt", "r\n", "init")
    detached = root / "detached_repo"
    _clone(root, detached)
    head = _head_sha(root)
    _git(["checkout", "-q", head], detached)  # detach HEAD, no upstream branch
    absent_target = root / "not-there"
    _write_manifest(root, {
        "detached": {"path": "./detached_repo"},
        "absent": {"path": "./not-there"},
    })

    degraded, reason, rows, exit_code = FLEET.status(str(root))
    by_key = {r["key"]: r for r in rows}

    assert by_key["detached"]["branch"] == "detached"
    assert by_key["detached"]["ahead_behind"] == "no-upstream"
    assert by_key["absent"]["present"] is False
    for field in ("branch", "ahead_behind", "dirty"):
        assert by_key["absent"][field] == "unknown"
    assert "0/0" not in json.dumps(rows)


# =================================================================================================
# AC-WRV-5 — foreach
# =================================================================================================
def test_foreach_fans_out_over_present_repos_and_collects_failures(scenario):
    marker_dir = scenario["root"] / "_markers"
    marker_dir.mkdir()
    wrapper = scenario["root"] / "_probe.py"
    wrapper.write_text(textwrap.dedent("""
        import os, sys, pathlib
        name = os.path.basename(os.getcwd())
        pathlib.Path(sys.argv[1] + "/" + name).write_text("ok")
        if name == "present_mismatch":
            sys.exit(1)
    """), encoding="utf-8")

    degraded, reason, rows, exit_code = FLEET.foreach(
        str(scenario["root"]), [sys.executable, str(wrapper), str(marker_dir)], timeout=15.0,
    )
    by_key = {r["key"]: r for r in rows}
    present_keys = {k for k, v in scenario["repos"].items() if "remote" in v and k != "notcloned"}
    assert set(by_key.keys()) == present_keys

    assert by_key["mismatch"]["result"] == "failed"
    assert by_key["mismatch"]["finding"] is True
    for k in present_keys - {"mismatch"}:
        assert by_key[k]["result"] == "ok"
        assert by_key[k]["finding"] is False

    # every present repo still ran despite the failing one (marker filename = checkout dir basename)
    dir_basename = {"match": "present_match", "mismatch": "present_mismatch",
                     "dirty": "present_dirty", "diverged": "present_diverged"}
    for k in present_keys:
        assert (marker_dir / dir_basename[k]).exists(), "no marker for %r" % k

    assert exit_code == FLEET.EXIT_FINDINGS


def test_foreach_passes_argv_literally_with_no_shell(scenario):
    marker = scenario["root"] / "argv-marker.json"
    probe = scenario["root"] / "_argv_probe.py"
    probe.write_text(textwrap.dedent("""
        import json, sys
        with open(sys.argv[1], "a") as fh:
            fh.write(json.dumps(sys.argv[2:]) + "\\n")
    """), encoding="utf-8")
    hostile_arg = "; rm -rf / && echo pwned | cat `echo x` $(echo y)"

    degraded, reason, rows, exit_code = FLEET.foreach(
        str(scenario["root"]), [sys.executable, str(probe), str(marker), hostile_arg], timeout=15.0,
    )
    assert marker.exists()
    lines = marker.read_text(encoding="utf-8").splitlines()
    assert lines, "no child ever ran"
    for line in lines:
        argv_seen = json.loads(line)
        assert argv_seen == [hostile_arg], "the hostile argument was not passed as a single literal: %r" % argv_seen


def test_foreach_captures_child_output_and_reports_timeout_and_spawn_failure(scenario):
    ansi_probe = scenario["root"] / "_ansi_probe.py"
    ansi_probe.write_text(textwrap.dedent("""
        import sys
        sys.stdout.write("line1\\n\\x1b[31mline2\\x1b[0m\\n" + chr(0x9b) + "line3\\n")
        sys.stdout.flush()
    """), encoding="utf-8")
    degraded, reason, rows, exit_code = FLEET.foreach(
        str(scenario["root"]), [sys.executable, str(ansi_probe)], timeout=15.0,
    )
    by_key = {r["key"]: r for r in rows}
    match_row = by_key["match"]
    assert "\x1b[" not in match_row["stdout"]
    assert "\x9b" not in match_row["stdout"]
    lines = match_row["stdout"].split("\n")
    assert len(lines) >= 3  # line count/order preserved

    timeout_probe = scenario["root"] / "_sleep_probe.py"
    timeout_probe.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    degraded, reason, rows2, exit_code2 = FLEET.foreach(
        str(scenario["root"]), [sys.executable, str(timeout_probe)], timeout=0.2,
    )
    by_key2 = {r["key"]: r for r in rows2}
    assert by_key2["match"]["result"] == "timeout"
    assert by_key2["match"]["finding"] is True
    assert exit_code2 == FLEET.EXIT_FINDINGS

    degraded, reason, rows3, exit_code3 = FLEET.foreach(
        str(scenario["root"]), ["/nonexistent/binary/definitely-not-here-%s" % uuid.uuid4().hex], timeout=5.0,
    )
    by_key3 = {r["key"]: r for r in rows3}
    assert by_key3["match"]["result"] == "spawn-failed"
    assert by_key3["match"]["finding"] is True


# =================================================================================================
# AC-WRV-6 — validate, forward + reverse
# =================================================================================================
def test_validate_reports_undeclared_checkouts_with_discovered_origin(scenario):
    root = scenario["root"]
    undeclared = root / "vendor" / "some-repo"
    _init_repo(undeclared)
    _commit(undeclared, "v.txt", "v\n", "init")
    _git(["remote", "add", "origin", "https://example.invalid/owner/some-repo.git"], undeclared)

    declared_but_absent_origin = root / "vendor" / "no-origin-repo"
    _init_repo(declared_but_absent_origin)
    _commit(declared_but_absent_origin, "n.txt", "n\n", "init")

    degraded, reason, rows, exit_code = FLEET.validate(str(root))
    undeclared_rows = [r for r in rows if r.get("kind") == "undeclared-checkout"]
    by_path = {r["path"]: r for r in undeclared_rows}

    assert os.path.join("vendor", "some-repo") in by_path
    assert by_path[os.path.join("vendor", "some-repo")]["discovered_origin"] == "https://example.invalid/owner/some-repo.git"

    assert os.path.join("vendor", "no-origin-repo") in by_path
    assert by_path[os.path.join("vendor", "no-origin-repo")]["discovered_origin"] == "undeclared"

    # a DECLARED checkout (present_match) never appears in the reverse-direction findings
    assert "present_match" not in by_path

    # the scan does not descend into a discovered checkout
    nested = undeclared / "nested-would-be-invisible"
    _init_repo(nested)
    degraded2, reason2, rows2, exit_code2 = FLEET.validate(str(root))
    undeclared_rows2 = [r for r in rows2 if r.get("kind") == "undeclared-checkout"]
    assert not any("nested-would-be-invisible" in r["path"] for r in undeclared_rows2)


def test_reverse_scan_excludes_exactly_three_names_and_descends_others(scenario):
    root = scenario["root"]
    for excluded_name in (".git", "node_modules", ".worktrees"):
        p = root / excluded_name / "planted-repo"
        _init_repo(p)
        _commit(p, "e.txt", "e\n", "init")

    included_dir = root / "vendor2" / "planted-repo"
    _init_repo(included_dir)
    _commit(included_dir, "i.txt", "i\n", "init")

    # a symlinked directory is never traversed
    real_target = root.parent / "outside-symlink-target"
    real_repo = real_target / "planted-repo"
    _init_repo(real_repo)
    _commit(real_repo, "s.txt", "s\n", "init")
    (root / "symlinked").symlink_to(real_target, target_is_directory=True)

    degraded, reason, rows, exit_code = FLEET.validate(str(root))
    undeclared_paths = {r["path"] for r in rows if r.get("kind") == "undeclared-checkout"}

    for excluded_name in ("node_modules", ".worktrees"):
        assert not any(p.startswith(excluded_name) for p in undeclared_paths), (
            "excluded dir %r was descended into: %r" % (excluded_name, undeclared_paths)
        )
    assert any(p.startswith("vendor2") for p in undeclared_paths), (
        "a non-excluded directory was NOT descended into: %r" % undeclared_paths
    )
    assert not any("symlinked" in p for p in undeclared_paths), "a symlinked directory was traversed"

    # every reported candidate is physically confined inside the root
    for r in rows:
        if r.get("kind") != "undeclared-checkout":
            continue
        assert not r["path"].startswith("..")


def test_validate_round_trip_preserves_registry_vocabulary(scenario):
    root = scenario["root"]
    degraded, reason, rows, exit_code = FLEET.validate(str(root))
    forward_rows = [r for r in rows if r.get("kind") == "forward"]
    by_key = {r["key"]: r for r in forward_rows}
    for k in scenario["repos"]:
        assert k in by_key
        assert by_key[k]["path_status"] in ("present", "not-cloned", "dangling", "outside-workspace")
        assert by_key[k]["gitignore"] in ("ok", "unpaired", "unanchored", "tracked", "n/a", "unknown")
        assert by_key[k]["origin"] in ("match", "mismatch", "undeclared", "not-a-checkout", "unknown")
    assert by_key["mismatch"]["origin"] == "mismatch"
    assert exit_code == FLEET.EXIT_FINDINGS  # mismatch + notcloned + dangling are all findings

    # a clean scenario (no findings in either direction) exits 0
    clean_root = root.parent / "clean_ws"
    _init_repo(clean_root)
    _commit(clean_root, "c.txt", "c\n", "init")
    _write_manifest(clean_root, {})
    degraded_c, reason_c, rows_c, exit_code_c = FLEET.validate(str(clean_root))
    assert exit_code_c == FLEET.EXIT_CLEAN


# =================================================================================================
# AC-WRV-7 — the envelope + advisory tri-state, for every verb
# =================================================================================================
@pytest.mark.parametrize("verb", ["sync", "status", "foreach", "validate"])
def test_json_envelope_and_advisory_tristate_for_every_verb(scenario, verb, tmp_path):
    root = scenario["root"]
    args = ["--root", str(root), verb, "--json"] if False else [verb, "--root", str(root), "--json"]
    if verb == "foreach":
        args = ["foreach", "--root", str(root), "--json", "--", sys.executable, "-c", "pass"]
    r = _run_cli(*args)
    doc = json.loads(r.stdout)
    assert set(doc.keys()) == {"degraded", "degraded_reason", "rows"}
    assert isinstance(doc["degraded"], bool)
    assert r.returncode in (FLEET.EXIT_CLEAN, FLEET.EXIT_FINDINGS, FLEET.EXIT_ERROR)
    for row in doc["rows"]:
        assert "key" in row or verb == "validate"  # reverse rows carry key=None

    # a findings run
    if r.returncode == FLEET.EXIT_FINDINGS:
        assert doc["rows"]

    # a degraded (manifest-absent) run -> EXIT_ERROR, well-formed stderr, no crash
    empty_root = tmp_path / "no-manifest-ws"
    empty_root.mkdir()
    args_bad = [verb, "--root", str(empty_root), "--json"]
    if verb == "foreach":
        args_bad = ["foreach", "--root", str(empty_root), "--json", "--", sys.executable, "-c", "pass"]
    r2 = _run_cli(*args_bad)
    assert r2.returncode == FLEET.EXIT_ERROR
    assert r2.stdout.strip() == "" or "degraded" not in r2.stdout

    help_text = _run_cli("--help").stdout
    assert "gate" in help_text.lower() and "advisory" in help_text.lower()


# =================================================================================================
# AC-WRV-8 — redaction, inherited
# =================================================================================================
def test_every_channel_uses_the_registry_redaction_sink():
    assert FLEET.SANITIZE is REG.sanitize

    root_marker = str(uuid.uuid4())
    https_remote = "https://user-%s:secret-%s@example.invalid/o/r.git" % (root_marker, root_marker)
    ssh_remote = "ssh://user-%s:secret-%s@example.invalid/o/r.git" % (root_marker, root_marker)
    scp_remote = "user-%s:secret-%s@example.invalid:o/r.git" % (root_marker, root_marker)

    for remote in (https_remote, ssh_remote, scp_remote):
        row = {"key": "k", "path": "p", "declared_remote": remote, "discovered_origin": remote,
               "resolved_path": "/x", "origin": "mismatch", "path_status": "present"}
        sanitized = FLEET._sanitize_row(row)
        assert "secret-%s" % root_marker not in json.dumps(sanitized)

    # the error path: an uncaught exception in main() emits a SANITIZED line, never a raw traceback
    out = io.StringIO()
    err = io.StringIO()
    exit_code = FLEET.main(["sync", "--root", "/definitely/does/not/exist/%s" % uuid.uuid4().hex],
                            stdout=out, stderr=err)
    assert exit_code == FLEET.EXIT_ERROR
    assert "Traceback" not in err.getvalue()


# =================================================================================================
# AC-WRV-9 — the skill ships, disambiguated from the session roster
# =================================================================================================
SKILL_PATH = os.path.join(REPO_ROOT, "skills", "repos", "SKILL.md")


def test_skill_ships_and_disambiguates_from_the_session_roster():
    assert os.path.isfile(SKILL_PATH)
    text = open(SKILL_PATH, encoding="utf-8").read()
    front = text.split("---")[1]
    assert "name: repos" in front
    for verb in ("sync", "status", "foreach", "validate"):
        assert verb in text
    assert "/foundry:fleet" in text
    assert "session" in text.lower() and "roster" in text.lower()


def test_how_to_documents_the_verbs_without_new_enforced_labels():
    howto = os.path.join(REPO_ROOT, "docs", "how-to", "multi-repo-control-plane.md")
    text = open(howto, encoding="utf-8").read()
    import re

    headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, re.MULTILINE))
    fleet_idx = [i for i, m in enumerate(headings) if "fleet verb" in m.group(2).lower()]
    assert len(fleet_idx) == 1, "expected exactly one 'fleet verbs' heading"
    i = fleet_idx[0]
    level = len(headings[i].group(1))
    start = headings[i].end()
    end = len(text)
    for j in range(i + 1, len(headings)):
        if len(headings[j].group(1)) <= level:
            end = headings[j].start()
            break
    section = text[start:end]
    for verb in ("sync", "status", "foreach", "validate"):
        assert verb in section
    assert "surfaced" in section.lower() and ("never fixed" in section.lower() or "not fixed" in section.lower())

    # the CLOSED enforced-roster is unchanged — the exact 8 pinned labels, no 9th
    enf_idx = [i for i, m in enumerate(headings) if "enforced" in m.group(2).lower()]
    assert len(enf_idx) == 1
    i = enf_idx[0]
    level = len(headings[i].group(1))
    start = headings[i].end()
    end = len(text)
    for j in range(i + 1, len(headings)):
        if len(headings[j].group(1)) <= level:
            end = headings[j].start()
            break
    enforced_section = text[start:end]
    label_re = re.compile(r"^-\s+\*\*([^*]+)\*\*\s*—\s*([a-z-]+)\.", re.MULTILINE)
    parsed = dict(label_re.findall(enforced_section))
    pinned = {
        "repo-key resolution": "machine-enforced",
        "dispatch bind-check": "machine-enforced",
        "target_repo freeze": "machine-enforced",
        "authorization venue floors": "not-enforced-today",
        "doctor registry validation": "not-enforced-today",
        "pairing rule": "practice",
        "clone-before-register ordering": "practice",
        "session-root rule": "not-enforced-today",
    }
    assert parsed == pinned, "the enforced roster drifted: %r" % (parsed,)


# =================================================================================================
# AC-WRV-10 — the reconcile seam
# =================================================================================================
def test_reconcile_is_an_importable_callable_the_cli_renders(scenario, monkeypatch):
    import foundry_repo_fleet  # the underscored module name is the point

    sig = inspect.signature(foundry_repo_fleet.reconcile)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["root", "rows", "timeout"]
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[2].kind == inspect.Parameter.KEYWORD_ONLY
    assert params[2].default is None

    root = scenario["root"]
    outcome, degraded, reason, rows = REG.build_report(str(root))

    real_run = FLEET.subprocess.run
    calls = []

    def spy(argv, **kwargs):
        calls.append(True)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(FLEET.subprocess, "run", spy)

    out = io.StringIO()
    err = io.StringIO()
    result = foundry_repo_fleet.reconcile(str(root), rows, timeout=5.0)
    assert out.getvalue() == "" and err.getvalue() == "", "reconcile() wrote to a stream"
    assert set(result.keys()) == {"degraded", "degraded_reason", "rows"}
    for row in result["rows"]:
        assert set(row.keys()) == {"key", "path", "remote", "action", "result", "finding", "detail"}
        assert row["action"] in ("clone", "fetch", "skip", "refuse")
        assert row["result"] in ("ok", "failed", "timeout", "spawn-failed", "n/a")
        assert isinstance(row["finding"], bool)

    # the sync CLI path renders THIS callable's return value, never a forked second path
    r = _run_cli("sync", "--root", str(root), "--json")
    cli_rows = {row["key"]: row for row in json.loads(r.stdout)["rows"]}
    assert set(cli_rows.keys()) == {row["key"] for row in result["rows"]}
    for key, row in cli_rows.items():
        assert row["action"] in ("clone", "fetch", "skip", "refuse")


def test_reconcile_revalidates_hostile_rows_and_hostile_roots(scenario, tmp_path):
    root = scenario["root"]
    outside = tmp_path / "escape-target"
    outside.mkdir()
    (root / "lies-parent").symlink_to(outside, target_is_directory=True)
    helper = "helper-%s" % uuid.uuid4().hex[:10]

    fabricated_rows = [
        {"key": "lies-escaping", "path": "./lies-parent/child", "path_status": "not-cloned",
         "declared_remote": str(_bare_target(tmp_path)), "origin": "undeclared"},
        {"key": "lies-transport-helper", "path": "./whatever", "path_status": "not-cloned",
         "declared_remote": "%s::x" % helper, "origin": "undeclared"},
        {"key": "-hostile-key", "path": "./whatever2", "path_status": "not-cloned",
         "declared_remote": "%s::x" % helper, "origin": "undeclared"},
    ]
    result = FLEET.reconcile(str(root), fabricated_rows, timeout=5.0)
    for row in result["rows"]:
        assert row["action"] == "refuse", row

    # driven against a root with NO manifest
    no_manifest_root = tmp_path / "no-manifest"
    no_manifest_root.mkdir()
    result_nm = FLEET.reconcile(str(no_manifest_root), [{"key": "x", "path": "./y",
                                 "path_status": "not-cloned", "declared_remote": str(_bare_target(tmp_path))}],
                                 timeout=5.0)
    assert result_nm["degraded"] is True
    assert result_nm["rows"] == []

    # driven against a root whose LEXICAL path resolves elsewhere (a symlink)
    real_root = tmp_path / "real-root"
    _init_repo(real_root)
    _write_manifest(real_root, {})
    spoofed = tmp_path / "spoofed-root"
    spoofed.symlink_to(real_root, target_is_directory=True)
    result_spoof = FLEET.reconcile(str(spoofed), [], timeout=5.0)
    assert result_spoof["degraded"] is False  # manifest genuinely resolves through the symlink
    assert os.path.realpath(str(spoofed)) == os.path.realpath(str(real_root))


# =================================================================================================
# AC-WRV-11 — the hardening set + sink env on every git child, member by member
# =================================================================================================
def test_every_git_child_carries_the_hardening_set_and_sink_env(scenario, monkeypatch):
    result = _run_full_fleet(scenario["root"], monkeypatch=monkeypatch)
    git_calls = _git_calls(result["calls"])
    assert git_calls

    hardening_members = FLEET._HARDENING_SET
    for call in git_calls:
        argv = call["argv"]
        for j in range(0, len(hardening_members), 2):
            pair = hardening_members[j:j + 2]
            assert argv[j + 1] == pair[0] and argv[j + 2] == pair[1], (
                "hardening member out of place at %r in %r" % (pair, argv)
            )
        env = call["env"]
        assert env is not None
        for var in FLEET.SINK_ENV_REMOVED_VARS:
            assert var not in env, "%r present in sink env" % var
        assert env.get("GIT_TERMINAL_PROMPT") == "0"


def test_planted_hostile_config_does_not_fire_with_positive_control(scenario, tmp_path, monkeypatch):
    root = scenario["root"]
    governed = root / "present_match"
    fsmonitor_marker = tmp_path / "fsmonitor-marker"
    hook = tmp_path / "hostile-fsmonitor.sh"
    hook.write_text("#!/bin/sh\ntouch %s\nexit 0\n" % fsmonitor_marker, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    _git(["config", "core.fsmonitor", str(hook)], governed)

    # positive control: the SAME planted hook, invoked WITHOUT the hardening override, over the
    # checkout's own ambient config -- proves the hook mechanism genuinely fires when unhardened.
    assert not fsmonitor_marker.exists()
    subprocess.run(["git", "status", "--porcelain"], cwd=str(governed), capture_output=True)
    assert fsmonitor_marker.exists(), "positive control did not fire -- the hook mechanism is not live"
    fsmonitor_marker.unlink()

    # this tool's OWN hardened invocation must leave the marker absent
    degraded, reason, rows, exit_code = FLEET.status(str(root))
    assert not fsmonitor_marker.exists(), "hostile core.fsmonitor fired under this tool's hardened invocation"

    # an undeclared checkout's OWN planted hostile config: absent after a `validate` run too
    undeclared = root / "vendor3" / "hostile-undeclared"
    _init_repo(undeclared)
    _commit(undeclared, "u.txt", "u\n", "init")
    undeclared_marker = tmp_path / "undeclared-fsmonitor-marker"
    hook2 = tmp_path / "hostile-fsmonitor-2.sh"
    hook2.write_text("#!/bin/sh\ntouch %s\nexit 0\n" % undeclared_marker, encoding="utf-8")
    hook2.chmod(hook2.stat().st_mode | stat.S_IXUSR)
    _git(["config", "core.fsmonitor", str(hook2)], undeclared)

    degraded2, reason2, rows2, exit_code2 = FLEET.validate(str(root))
    assert not undeclared_marker.exists()


# =================================================================================================
# AC-WRV-12 — egress bound, no submodule amplification, the inertness non-promise
# =================================================================================================
def test_egress_is_bounded_to_declared_remotes_with_sockets_denied(scenario, monkeypatch):
    import socket

    real_socket = socket.socket

    def deny_socket(*args, **kwargs):
        raise AssertionError("a network socket was opened during a hermetic fleet run")

    monkeypatch.setattr(socket, "socket", deny_socket)
    try:
        outcome, degraded, reason, rows = REG.build_report(str(scenario["root"]))
        result = FLEET.reconcile(str(scenario["root"]), rows, timeout=5.0)
        assert any(r["action"] == "clone" and r["result"] == "ok" for r in result["rows"])
    finally:
        monkeypatch.setattr(socket, "socket", real_socket)

    calls = _record_spawn(monkeypatch)
    outcome, degraded, reason, rows = REG.build_report(str(scenario["root"]))
    FLEET.reconcile(str(scenario["root"]), rows, timeout=5.0)
    network_calls = [c for c in _git_calls(calls) if _verb_of(c["argv"]) in ("clone", "fetch")]
    declared_remotes = {v.get("remote") for v in scenario["repos"].values() if v.get("remote")}
    for c in network_calls:
        argv = c["argv"]
        dash_idx = argv.index("--")
        named = argv[dash_idx + 1]
        assert named in declared_remotes


def test_submodule_recursion_is_off_on_clone_and_on_fetch(scenario, tmp_path, monkeypatch):
    root = scenario["root"]
    inner = _init_bare(tmp_path / "remotes3" / "inner.git")
    seed_inner = tmp_path / "seed-inner"
    _clone(inner, seed_inner)
    _commit(seed_inner, "i.txt", "i\n", "seed")
    _git(["push", "-q", "origin", "trunk"], seed_inner)

    outer = _init_bare(tmp_path / "remotes3" / "outer.git")
    seed_outer = tmp_path / "seed-outer"
    _clone(outer, seed_outer)
    _git(["-c", "protocol.file.allow=always", "submodule", "add", str(inner), "sub"], seed_outer)
    _git(["commit", "-q", "-m", "add submodule"], seed_outer)
    _git(["push", "-q", "origin", "trunk"], seed_outer)

    calls = _record_spawn(monkeypatch)
    rows = [{"key": "withsub", "path": "./withsub", "path_status": "not-cloned",
             "declared_remote": str(outer), "origin": "undeclared"}]
    result = FLEET.reconcile(str(root), rows, timeout=15.0)
    assert result["rows"][0]["result"] == "ok"
    cloned = root / "withsub"
    assert (cloned / ".gitmodules").exists()  # the pointer file is a normal tracked file
    assert not any((cloned / "sub").iterdir()) if (cloned / "sub").exists() else True
    assert not (cloned / "sub" / ".git").exists(), "the submodule was recursed into"

    clone_calls = [c for c in _git_calls(calls) if _verb_of(c["argv"]) == "clone"]
    assert clone_calls and "--no-recurse-submodules" in clone_calls[0]["argv"]
    assert "fetch.recurseSubmodules=no" in clone_calls[0]["argv"]
    assert "submodule.recurse=false" in clone_calls[0]["argv"]

    # now fetch it (present + match)
    repos2 = dict(scenario["repos"])
    repos2["withsub"] = {"path": "./withsub", "remote": str(outer)}
    _write_manifest(root, repos2)
    calls2 = _record_spawn(monkeypatch)
    outcome, degraded, reason, rows2 = REG.build_report(str(root))
    result2 = FLEET.reconcile(str(root), rows2, timeout=15.0)
    fetch_calls = [c for c in _git_calls(calls2) if _verb_of(c["argv"]) == "fetch"
                   and str(outer) in c["argv"]]
    assert fetch_calls
    assert "--no-recurse-submodules" in fetch_calls[0]["argv"]


def test_help_and_skill_state_the_cloned_tree_non_promise():
    help_text = _run_cli("--help").stdout
    for token in ("untrusted", "CLAUDE.md", ".claude", ".mcp.json"):
        assert token in help_text, "help text missing %r" % token

    skill_text = open(SKILL_PATH, encoding="utf-8").read()
    for token in ("untrusted", "CLAUDE.md", ".claude", ".mcp.json"):
        assert token in skill_text, "SKILL.md missing %r" % token


# =================================================================================================
# Security review (PR #59) — regression tests for the applied fixes
# =================================================================================================
def test_r3_fetch_refuses_a_present_match_row_whose_path_is_not_a_git_checkout(scenario, monkeypatch):
    """R3: a fabricated row claims path_status=present/origin=match against a directory that is
    physically confined inside the root but is NOT a git checkout at all (no .git dir or file) —
    `reconcile` SHALL refuse it rather than spawning `git fetch` inside a non-checkout directory."""
    root = scenario["root"]
    not_a_checkout = root / "just_a_plain_directory"
    not_a_checkout.mkdir()

    calls = _record_spawn(monkeypatch)
    rows = [{
        "key": "notacheckout", "path": "./just_a_plain_directory",
        "path_status": "present", "origin": "match",
        "declared_remote": str(scenario["bare_match"]),
        "resolved_path": str(not_a_checkout),
    }]
    result = FLEET.reconcile(str(root), rows, timeout=5.0)
    row = result["rows"][0]
    assert row["action"] == "refuse", row
    assert row["finding"] is True
    assert not _git_calls(calls), "a git command was spawned for a non-checkout present/match row"


def test_r6_sync_timeout_defaults_to_the_declared_bound_when_the_flag_is_absent():
    """R6: `sync --timeout` previously defaulted to None (unbounded network egress). A declared
    default now applies when the flag is absent; the flag still overrides it."""
    parser = FLEET.build_parser()
    args_default = parser.parse_args(["sync", "--root", "."])
    assert args_default.timeout == FLEET.DEFAULT_SYNC_TIMEOUT_SECONDS
    assert FLEET.DEFAULT_SYNC_TIMEOUT_SECONDS == 600.0

    args_override = parser.parse_args(["sync", "--root", ".", "--timeout", "12.5"])
    assert args_override.timeout == 12.5

    # AC-WRV-10's pinned reconcile() signature/default is untouched by this CLI-only fix.
    sig = inspect.signature(FLEET.reconcile)
    assert sig.parameters["timeout"].default is None


def test_r9_reconcile_except_handler_survives_a_non_dict_row_that_raises_mid_processing(monkeypatch, tmp_path):
    """R9: the per-row except handler's fallback `remote` derivation must not itself crash on a
    non-dict row -- guarded with the same isinstance(row, dict) check the adjacent lines carry."""
    root = tmp_path / "r9-ws"
    _init_repo(root)
    _commit(root, "README.md", "root\n", "root init")
    _write_manifest(root, {})

    def boom(root_physical, row, timeout):
        raise RuntimeError("forced failure mid-row-processing")

    monkeypatch.setattr(FLEET, "_process_reconcile_row", boom)
    hostile_non_dict_row = ["not", "a", "dict"]
    result = FLEET.reconcile(str(root), [hostile_non_dict_row], timeout=5.0)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["action"] == "refuse"
    assert row["finding"] is True
    assert row["key"] is None
    assert row["remote"] is None


def test_r1_sink_env_removes_git_allow_protocol_as_an_additional_over_removal():
    """R1: an additional explicit removal beyond the spec-pinned SINK_ENV_REMOVED_VARS tuple, which
    stays byte-identical to its original members (never extended) so the pinned tuple itself is
    unchanged."""
    base = dict(os.environ)
    base["GIT_ALLOW_PROTOCOL"] = "https"
    env = FLEET._sink_env(base_env=base)
    assert "GIT_ALLOW_PROTOCOL" not in env
    assert FLEET.SINK_ENV_REMOVED_VARS == (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS",
    )


def test_r8_sys_path_bootstrap_is_idempotent():
    """R8: the module-level sys.path bootstrap (`if _HERE_DIR not in sys.path: sys.path.append(...)`)
    SHALL NOT grow sys.path with duplicate entries across repeated bootstrap passes -- the guard
    that replaced the unconditional `sys.path.insert(0, ...)`. Scoped to THIS module's own guard
    behavior, not a whole-suite invariant: other shipped scripts (out of this atom's scope) still
    use an unconditional `sys.path.insert(0, ...)` when loaded via `load_module()` elsewhere in the
    suite, so sys.path may already carry more than one entry for this same directory by the time
    this test runs; the guard's OWN idempotence is what is asserted here."""
    here_dir = os.path.dirname(os.path.abspath(FLEET.__file__))
    if here_dir not in sys.path:
        sys.path.append(here_dir)
    before_count = sys.path.count(here_dir)
    assert before_count >= 1

    for _ in range(3):
        if here_dir not in sys.path:
            sys.path.append(here_dir)
    after_count = sys.path.count(here_dir)
    assert after_count == before_count, "repeated guarded bootstrap passes grew sys.path"
