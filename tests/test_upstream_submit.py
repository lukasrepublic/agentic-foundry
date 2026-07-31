"""tests/test_upstream_submit.py — the derivation executable for
feat-foundry-upstream-submit-first-use.md (AC-URFIRST-1..9).

New in this atom (does not exist on the merge base). Drives `scripts/foundry-upstream-submit.py`
directly, importing it exactly like every other converted `tests/test_*.py` module does
(`conftest.load_module`). Every fixture is hermetic: `gh` is replaced by the module's own
`_RecordingGhRunner` double (or, for the two subprocess-level checks, by manipulating `PATH`/a
fake local `gh` shim so no real `gh` and no network is ever reached) — no test spawns a real `gh`
process or touches the network.

One function per criterion, named so the acceptance-contract's `-k` selectors bind to it:
`ac1_ensure_precedes_create`, `ac1_ensure_is_idempotent`, `ac2_ensure_failure_degrades`,
`ac3_gh_failure_typed`, `ac3_no_traceback`, `ac4_leak_blocks`, `ac5_request_only`,
`ac5_single_spawn_site`, `ac6_selftest_hermetic`, `ac7_one_test_function`, `ac8_skill_documents`,
`ac9_changelog_records`.

`test_sec_*` functions (added under the floor-#3 security-review remediation, additive — the
frozen AC-bound names above are unchanged) regression-cover the review's findings: the
structurally-inert PUT self-scan clause, the whole-line `selfscan:exclude` escape hatch, spawn-
pattern-denylist gaps (including alias/rebind forms), the denylist-only runner-seam envelope, the
swallowed issue URL, and the dropped first-failure diagnostic on a double `gh` failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types

from conftest import REPO_ROOT, load_module

m = load_module("scripts/foundry-upstream-submit.py", "foundry_upstream_submit")

SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-upstream-submit.py")


def _args(candidate_path, *, repo="o/r", adopter="x", foundry_version="0", create=True):
    return types.SimpleNamespace(candidate=str(candidate_path), repo=repo, adopter=adopter,
                                  foundry_version=foundry_version, create=create)


# =============================================================== AC-URFIRST-1 — ensure/idempotent

def test_ac1_ensure_precedes_create():
    """Probe-then-create: when the probe reports the label ABSENT (a 404-shaped failure), the
    ensure runs the create call before issue-create — three calls, in order: probe, create,
    issue-create."""
    dbl = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),
    })
    result = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                        create=True, extra_tokens=[], runner=dbl)
    assert not result["refused"]
    assert len(dbl.calls) == 3, dbl.calls
    assert dbl.calls[0] == m.build_label_probe_command("o/r"), dbl.calls[0]
    assert dbl.calls[1][:3] == ["gh", "label", "create"], dbl.calls[1]
    assert dbl.calls[2][:3] == ["gh", "issue", "create"], dbl.calls[2]
    # the probe and create calls both name the repo and the shipped label
    assert "o/r" in " ".join(dbl.calls[0]) and "enhancement-request" in " ".join(dbl.calls[0])
    assert "o/r" in dbl.calls[1] and "enhancement-request" in dbl.calls[1]
    assert not result["label_degraded"]


def test_ac1_ensure_is_idempotent():
    """Probe-then-create is a pure create-or-noop: when the probe reports the label PRESENT
    (default clean success), the ensure makes NO create call at all — zero writes on the
    already-exists path. Two consecutive submits both succeed and neither ever calls
    `build_label_command`."""
    dbl = m._RecordingGhRunner()  # every call defaults to a clean success == "label present"
    r1 = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                    create=True, extra_tokens=[], runner=dbl)
    r2 = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                    create=True, extra_tokens=[], runner=dbl)
    assert not r1["label_degraded"] and not r2["label_degraded"]
    assert len(dbl.calls) == 4  # probe + issue-create, twice — ZERO `gh label create` calls
    assert dbl.calls[0] == m.build_label_probe_command("o/r")
    assert dbl.calls[1][:3] == ["gh", "issue", "create"]
    # the second consecutive ensure is argv-identical to the first — the same probe, not a
    # duplicate write (AC-URFIRST-1's no-duplicate-label / zero-write-on-already-exists clause).
    assert dbl.calls[0] == dbl.calls[2] == m.build_label_probe_command("o/r")
    for call in dbl.calls:
        assert call[:3] != ["gh", "label", "create"], f"unexpected write call: {call}"


# =============================================================== AC-URFIRST-2 — degrade

def test_ac2_ensure_failure_degrades(tmp_path, capsys):
    """The ensure fails only when BOTH the probe and the follow-on create fail (probe-then-create:
    a probe failure alone just means "absent", not "cannot ensure")."""
    candidate = tmp_path / "er.md"
    candidate.write_text(m._GOOD_CANDIDATE, encoding="utf-8")
    dbl = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 403: must have admin rights", spawned=True),
    })
    rc = m._run_live(_args(candidate, repo="o/r"), runner=dbl)
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert len(dbl.calls) == 3
    assert dbl.calls[0] == m.build_label_probe_command("o/r")
    assert dbl.calls[1][:3] == ["gh", "label", "create"]
    assert "--label" not in dbl.calls[2]  # filed WITHOUT --label
    assert m._DEGRADE_TOKEN in captured.err
    assert "enhancement-request" in captured.err
    assert "o/r" in captured.err
    assert "403" in captured.err  # the create failure's captured stderr is named (the more
    # diagnostic of the two — see _ensure_label's detail precedence)


# =============================================================== AC-URFIRST-3 — typed exit 3

def test_ac3_gh_failure_typed(tmp_path, capsys):
    candidate = tmp_path / "er.md"
    candidate.write_text(m._GOOD_CANDIDATE, encoding="utf-8")
    dbl = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 422: validation failed", spawned=True),
    })
    rc = m._run_live(_args(candidate, repo="o/r"), runner=dbl)
    captured = capsys.readouterr()
    assert rc == 3
    assert "gh issue create" in captured.err
    assert "422" in captured.err
    assert "remedy" in captured.err.lower()
    assert "Traceback" not in captured.err and "Traceback" not in captured.out

    # gh absent/not executable is the SAME typed disposition, not a different one.
    dbl2 = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: m.GhResult(returncode=127, stdout="", stderr="[Errno 2] No such file: 'gh'", spawned=False),
    })
    rc2 = m._run_live(_args(candidate, repo="o/r"), runner=dbl2)
    captured2 = capsys.readouterr()
    assert rc2 == 3
    assert "Traceback" not in captured2.err and "Traceback" not in captured2.out


def test_ac3_no_traceback(tmp_path):
    """Real end-to-end proof (no double): run the shipped module as a subprocess with a `gh`-free
    PATH (the absent case) and with a fake failing `gh` shim on PATH (the non-zero-exit case).
    Neither ever spawns a real `gh` or touches the network."""
    candidate = tmp_path / "er.md"
    candidate.write_text(m._GOOD_CANDIDATE, encoding="utf-8")

    def _run(env_path):
        env = dict(os.environ)
        env["PATH"] = env_path
        env.pop("CLAUDE_PROJECT_DIR", None)
        return subprocess.run(
            [sys.executable, SCRIPT_PATH, "--candidate", str(candidate), "--repo", "o/r", "--create"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
        )

    # (a) gh absent from PATH entirely.
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    proc_absent = _run(str(empty_bin))
    assert proc_absent.returncode == 3, proc_absent.stdout + proc_absent.stderr
    assert "Traceback" not in proc_absent.stdout and "Traceback" not in proc_absent.stderr

    # (b) a fake `gh` on PATH: the label-probe "succeeds" (label already present, so the ensure
    # makes no create call at all), issue-create exits non-zero.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "api "*) exit 0 ;;\n'
        '  "issue create") echo "HTTP 422: validation failed" 1>&2; exit 1 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    proc_fail = _run(str(fake_bin))
    assert proc_fail.returncode == 3, proc_fail.stdout + proc_fail.stderr
    assert "Traceback" not in proc_fail.stdout and "Traceback" not in proc_fail.stderr
    assert "422" in proc_fail.stderr


# =============================================================== AC-URFIRST-4 — leak blocks gh

def test_ac4_leak_blocks():
    dbl = m._RecordingGhRunner()
    result = m._submit(m.leaky_floor_candidate(), repo="o/r", adopter="x", foundry_version="0",
                        create=True, extra_tokens=[], runner=dbl)
    assert result["refused"] is True
    assert result["issue_command"] is None
    assert dbl.calls == []  # zero gh invocations of ANY kind, label-ensure included


# =============================================================== AC-URFIRST-5 — envelope + seam

def test_ac5_request_only():
    scan = m._selfscan_ac5()
    assert scan["request_only"] is True, scan["forbidden_present"]
    assert scan["forbidden_present"] == []
    # the built commands themselves are issue-create / label-create / the read-only label-GET
    # probe only — never a create-or-update `--force` (probe-then-create is a pure create-or-noop).
    issue_cmd = m.build_issue_command("o/r", "t", "/tmp/b")
    label_cmd = m.build_label_command("o/r")
    probe_cmd = m.build_label_probe_command("o/r")
    assert issue_cmd[:3] == ["gh", "issue", "create"]
    assert label_cmd[:3] == ["gh", "label", "create"]
    assert "--force" not in label_cmd
    assert probe_cmd == ["gh", "api", "repos/o/r/labels/enhancement-request"]
    assert m._is_allowed_label_probe(probe_cmd) is True
    for cmd in (issue_cmd, label_cmd, probe_cmd):
        joined = " ".join(cmd)
        assert "pr create" not in joined and "pr merge" not in joined
        assert "push" not in joined
        assert " PUT" not in joined and "-X PUT" not in joined and "--method PUT" not in joined


def test_ac5_single_spawn_site():
    scan = m._selfscan_ac5()
    assert scan["spawn_count"] == 1, scan
    assert scan["spawn_in_seam"] == 1, scan
    assert scan["single_spawn_site"] is True


# ================================================ security-review remediation (post-build review)
# The atom's floor-#3 security review found one BLOCK (the PUT clause was structurally inert) and
# several risks (a whole-line `selfscan:exclude` escape hatch, spawn-pattern gaps, a denylist-only
# envelope, a swallowed issue URL, and a lost first-failure diagnostic). Each fix gets its own
# regression test here so none of these can silently regress again.

def test_sec_put_clause_fires_on_synthetic_string():
    """The BLOCK: `_FORBIDDEN_CONTRIB_VERBS` carries two upper-cased PUT entries that a
    lower-cased scan text could never match. Prove the case-insensitive clause actually fires,
    using a runtime-assembled string (never a contiguous literal in THIS test file either, so the
    test module doesn't trip a real CI leak-style grep on the exact phrase)."""
    put_probe = "adopter code ran: gh api " + "-X" + " " + "PUT" + " repos/o/r/labels/x"
    hits = m._forbidden_verbs_present(put_probe)
    assert hits, "the upper-cased PUT entries never fired against a synthetic PUT string"
    assert any("put" in h.lower() for h in hits)

    method_probe = "adopter code ran: gh api " + "--method" + " " + "PUT" + " repos/o/r/labels/x"
    hits2 = m._forbidden_verbs_present(method_probe)
    assert hits2, "the --method PUT entry never fired against a synthetic PUT string"


def test_sec_verb_exclude_marker_scoped_to_verb_list_only():
    """The RISK: the OLD `selfscan:exclude` marker stripped ANY line carrying it — including a
    real spawn call disguised as `subprocess.run(...)  # selfscan:exclude`. The new marker
    (`_VERB_LIST_MARKER`) must be scoped to the `_FORBIDDEN_CONTRIB_VERBS` definition ONLY: a
    synthetic source snippet containing a real spawn call annotated with the marker must still be
    counted by the spawn scan."""
    synthetic_source = (
        "import subprocess\n"
        "def f():\n"
        "    subprocess.run(['ls'])  # " + m._VERB_LIST_MARKER + "\n"
    )
    count, hits = m._count_spawn_calls(synthetic_source)
    assert count == 1, f"a spawn call marked with the verb-list marker vanished from the count: {hits}"


def test_sec_spawn_pattern_gaps_now_detected():
    """The RISK: several spawn shapes previously left `spawn_count` at the old total — each of
    these must now be detected on its own (one synthetic module per shape, isolated)."""
    shapes = {
        "check_call": "import subprocess\nsubprocess.check_call(['x'])\n",
        "os.popen": "import os\nos.popen('x')\n",
        "os.posix_spawn": "import os\nos.posix_spawn('/bin/ls', ['ls'], {})\n",
        "os.fork": "import os\nos.fork()\n",
        "pty.spawn": "import pty\npty.spawn(['x'])\n",
        "aliased import": "from subprocess import run\nrun(['x'])\n",
        "aliased-as import": "from subprocess import run as r\nr(['x'])\n",
        "module alias": "import subprocess as sp\nsp.run(['x'])\n",
        "space before paren": "import subprocess\nsubprocess.run (['x'])\n",
    }
    for label, src in shapes.items():
        count, hits = m._count_spawn_calls(src)
        assert count == 1, f"spawn shape not detected: {label!r} -> count={count} hits={hits}"


def test_sec_argv_allowlist_refuses_out_of_envelope(monkeypatch):
    """The RISK: the envelope used to be a pure denylist. `_real_gh_runner` must refuse (not
    spawn) any argv whose first three tokens are not exactly `gh issue create` / `gh label
    create` — proven here by patching real `subprocess.run` to explode if ever reached, then
    handing the seam an out-of-envelope (PUT-shaped) argv."""
    import subprocess as real_subprocess

    def _boom(*_a, **_k):
        raise AssertionError("out-of-envelope argv reached real subprocess.run")

    monkeypatch.setattr(real_subprocess, "run", _boom)
    result = m._real_gh_runner(["gh", "api", "-X", "PUT", "repos/o/r/labels/x"])
    assert result.spawned is False
    assert result.returncode != 0

    # a --method PUT spelling is refused identically (not just the -X spelling).
    result2 = m._real_gh_runner(["gh", "api", "--method", "PUT", "repos/o/r/labels/x"])
    assert result2.spawned is False
    assert result2.returncode != 0

    # a `gh api` call against some OTHER endpoint (not the exact labels-GET path) is refused too —
    # the widening is scoped to exactly one endpoint shape, not `gh api` in general.
    result3 = m._real_gh_runner(["gh", "api", "repos/o/r/issues"])
    assert result3.spawned is False
    assert result3.returncode != 0

    # an in-envelope prefix is unaffected by the allowlist itself (still reaches the real spawn
    # attempt, which the patched `subprocess.run` raises on to prove it was actually reached).
    import pytest as _pytest
    with _pytest.raises(AssertionError):
        m._real_gh_runner(["gh", "issue", "create", "--repo", "o/r"])

    # the ONE admitted `gh api` shape — the read-only label-GET probe — IS let through to the real
    # spawn attempt (same proof-by-explosion as above).
    with _pytest.raises(AssertionError):
        m._real_gh_runner(m.build_label_probe_command("o/r"))


def test_sec_issue_url_printed_on_success(tmp_path, capsys):
    """The REGRESSION this atom introduced: the merge base streamed `gh`'s stdout (the created
    issue's URL); this atom's `capture_output=True` + a print that named only argv/repo silently
    dropped it. The created issue's URL must be printed on the success path."""
    candidate = tmp_path / "er.md"
    candidate.write_text(m._GOOD_CANDIDATE, encoding="utf-8")
    dbl = m._RecordingGhRunner(responses={
        1: m.GhResult(returncode=0, stdout="https://github.com/o/r/issues/42\n", stderr="",
                      spawned=True),
    })
    rc = m._run_live(_args(candidate, repo="o/r"), runner=dbl)
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "https://github.com/o/r/issues/42" in captured.out


def test_sec_both_failures_fold_ensure_stderr_into_exit3(tmp_path, capsys):
    """The diagnostic-improvement finding: when BOTH the label-ensure and the issue-create fail,
    the ensure's own captured stderr (often the more diagnostic cause, e.g. a 403 permissions
    error vs. a downstream 404) must be folded into the exit-3 diagnostic, not silently dropped
    behind the create failure."""
    candidate = tmp_path / "er.md"
    candidate.write_text(m._GOOD_CANDIDATE, encoding="utf-8")
    dbl = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),  # probe: absent
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 403: must have admin rights",
                      spawned=True),  # create: fails too -> ensure fails (degraded)
        2: m.GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),  # issue-create fails
    })
    rc = m._run_live(_args(candidate, repo="o/r"), runner=dbl)
    captured = capsys.readouterr()
    assert rc == 3
    assert "404" in captured.err          # the issue-create failure that triggered exit 3
    assert "403" in captured.err          # the ensure failure, folded in — was previously dropped
    assert "admin rights" in captured.err


# =============================================================== AC-URFIRST-6 — hermetic selftest

def test_ac6_selftest_hermetic(monkeypatch, capsys):
    """If `--selftest` ever escapes the injected double, the REAL runner's one spawn call site
    calls `subprocess.run` — patch that out so any such escape raises loudly instead of silently
    reaching the network."""
    import subprocess as real_subprocess

    def _boom(*_a, **_k):
        raise AssertionError("selftest spawned a REAL subprocess — hermeticity violated")

    monkeypatch.setattr(real_subprocess, "run", _boom)
    rc = m._selftest()
    out = capsys.readouterr().out
    assert rc == 0, out
    for i in range(1, 6):
        assert f"AC-URFIRST-{i} " in out, out
    for j in range(1, 5):
        assert f"AC-URSUBMIT-{j} " in out, out
    assert "UPSTREAM-SUBMIT-SELFTEST-GREEN" in out


# =============================================================== AC-URFIRST-7 — suite binding

def test_ac7_one_test_function():
    """Runs the module's --selftest as a subprocess (the contract's binding requirement) —
    proving the shipped, unmodified file (not just the in-process import) is GREEN."""
    proc = subprocess.run([sys.executable, SCRIPT_PATH, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UPSTREAM-SUBMIT-SELFTEST-GREEN" in proc.stdout


# =============================================================== AC-URFIRST-8 — skill doc

def test_ac8_skill_documents():
    skill_path = os.path.join(REPO_ROOT, "skills", "upstream-submit", "SKILL.md")
    with open(skill_path, encoding="utf-8") as fh:
        text = fh.read()
    lower = text.lower()
    assert "unlabelled" in lower or "unlabeled" in lower
    assert "warning" in lower
    # exit statuses named with their meaning
    assert "exit" in lower
    assert "2" in text and ("leak" in lower or "refus" in lower)
    assert "3" in text and "gh" in lower


# =============================================================== AC-URFIRST-9 — changelog

def test_ac9_changelog_records():
    changelog_path = os.path.join(REPO_ROOT, "CHANGELOG.md")
    with open(changelog_path, encoding="utf-8") as fh:
        text = fh.read()
    # Retargeted in the pre-v1 content pass: the changelog is a v1.0.0 baseline, so the
    # assertion binds the CAPABILITY's documented properties, not a per-atom release entry
    # or a pre-v1 PR number (which a zero-history public tree cannot resolve).
    assert "upstream-submit" in text
    lower = text.lower()
    assert "label-ensure" in lower or "label ensure" in lower
    assert "degrad" in lower
    assert "exit" in lower
