"""tests/test_upstream_submit_identity.py — the derivation executable for
feat-foundry-upstream-submit-identity.md (AC-URIDENT-1..9, issue #21 item b).

New in this atom (does not exist on the merge base, which already carries the sibling atom's
tests/test_upstream_submit.py). Drives `scripts/foundry-upstream-submit.py` directly, importing it
exactly like every other converted `tests/test_*.py` module does (`conftest.load_module`). Every
fixture is hermetic: identity files are written into a temp project root, and `gh` is always the
module's own `_RecordingGhRunner` double — no test spawns a real `gh` process or touches the
network (the guard-agreement fixture spawns `bash`, not `gh`, and makes no network call either).

One function per criterion, named so the acceptance-contract's `-k` selectors bind to it:
`ac1_malformed_identity_refused`, `ac2_declared_identity`, `ac2_ambient_tokens_removed`,
`ac2_guard_fixture_agrees`, `ac3_printed_command`, `ac4_undeclared_identity`,
`ac5_no_env_values_printed`, `ac5_tempfile_removed`, `ac6_selftest_hermetic`,
`ac7_one_test_function`, `ac7_sibling_module_green`, `ac8_skill_documents`, `ac9_changelog_records`.

Why the sibling module (`tests/test_upstream_submit.py`) is not edited: it is DENIED to this atom
and pins nothing about the printed command's quoting or prefix, so this atom introduces the
`GH_CONFIG_DIR` prefix and `shlex.quote` rendering without touching it — `ac7_sibling_module_green`
below proves it still passes, unmodified, as its own subprocess run.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

from conftest import REPO_ROOT, load_module

m = load_module("scripts/foundry-upstream-submit.py", "foundry_upstream_submit")

SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-upstream-submit.py")
SIBLING_TEST_PATH = os.path.join(REPO_ROOT, "tests", "test_upstream_submit.py")
GUARD_FIXTURE_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "gh-account-guard.pinned.sh")


def _declare_identity(root, handle_text):
    claude_dir = root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "gh-identity").write_text(handle_text, encoding="utf-8")


# =============================================================== AC-URIDENT-1 — malformed refuses

def test_ac1_malformed_identity_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "../evil;rm -rf ~")  # non-empty, fails the handle grammar
    dbl = m._RecordingGhRunner()
    raised = False
    exc = None
    try:
        m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                  extra_tokens=[], runner=dbl, project_dir=str(root))
    except m.MalformedIdentityError as e:
        raised, exc = True, e
    assert raised, "malformed identity did not raise"
    assert dbl.calls == []  # zero gh invocations of any kind
    idfile = str(root / ".claude" / "gh-identity")
    assert exc.identity_file == idfile
    diag = m._format_identity_refusal(exc.identity_file)
    assert "evil" not in diag  # the offending string never appears in a rendered diagnostic
    assert "../evil;rm -rf ~" not in diag
    assert idfile in diag
    assert m._HANDLE_RE.pattern in diag  # the required grammar is named

    # AC-URIDENT-1's exit status contract, proven end-to-end (real subprocess, no gh on PATH).
    candidate = tmp_path / "er.md"
    candidate.write_text(m._GOOD_CANDIDATE, encoding="utf-8")
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    proc = subprocess.run(
        [sys.executable, SCRIPT_PATH, "--candidate", str(candidate), "--repo", "o/r", "--create"],
        capture_output=True, text=True, env=env, cwd=str(root),
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "evil" not in proc.stdout and "evil" not in proc.stderr
    assert idfile in proc.stderr


# =============================================================== AC-URIDENT-2 — child env

def test_ac2_declared_identity(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "  octocat\n")  # whitespace around/within is deleted, not part of it
    dbl = m._RecordingGhRunner()
    rep = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=dbl, project_dir=str(root))
    assert not rep["refused"]
    assert rep["handle"] == "octocat"
    expect_dir = m.isolated_config_dir("octocat")
    assert len(dbl.envs) == 2  # label-ensure + issue-create
    for env in dbl.envs:
        assert env is not None
        assert env.get("GH_CONFIG_DIR") == expect_dir


def test_ac2_ambient_tokens_removed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octocat")
    dirty_env = dict(os.environ)
    for var in m._AMBIENT_STRIP_VARS:
        dirty_env[var] = "SENTINEL-" + var
    dbl = m._RecordingGhRunner()
    rep = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=dbl, project_dir=str(root), base_env=dirty_env)
    assert not rep["refused"]
    assert len(dbl.envs) == 2
    for env in dbl.envs:
        for var in m._AMBIENT_STRIP_VARS:
            assert var not in env, f"{var} leaked into the child env: {env.get(var)!r}"
        assert env.get("GH_CONFIG_DIR") == m.isolated_config_dir("octocat")


def test_ac2_guard_fixture_agrees(tmp_path):
    """Mechanical agreement with the REAL shipped guard (a pinned byte copy), driven over its own
    stdin protocol — not against this spec's prose restatement of it."""
    assert os.path.isfile(GUARD_FIXTURE_PATH)
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octocat")
    handle = m.resolve_identity(str(root))
    assert handle == "octocat"
    env = m.gh_env(handle, dict(os.environ))
    cmd = m.build_issue_command("o/r", "an issue title", "/tmp/body.md")
    rendered_argv = m.render_command(cmd, None)  # bare argv text, as a Bash tool_input.command
    payload = json.dumps({"tool_input": {"command": rendered_argv}})

    admitted = subprocess.run(["bash", GUARD_FIXTURE_PATH], input=payload, capture_output=True,
                               text=True, env=env, cwd=str(root))
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr

    env_no_config_dir = dict(env)
    env_no_config_dir.pop("GH_CONFIG_DIR", None)
    blocked = subprocess.run(["bash", GUARD_FIXTURE_PATH], input=payload, capture_output=True,
                              text=True, env=env_no_config_dir, cwd=str(root))
    assert blocked.returncode != 0, blocked.stdout + blocked.stderr


# =============================================================== AC-URIDENT-3 — printed command

def test_ac3_printed_command(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octo-cat")
    tricky = m._GOOD_CANDIDATE.replace(
        "deploy-status false-greens a never-rolled commit",
        "deploy `whoami` && echo pwned; $(id) 'quoted'")
    dbl = m._RecordingGhRunner()
    rep_dry = m._submit(tricky, repo="o/r", adopter="x", foundry_version="0", create=False,
                         extra_tokens=[], runner=dbl, project_dir=str(root))
    rep_create = m._submit(tricky, repo="o/r", adopter="x", foundry_version="0", create=True,
                            extra_tokens=[], runner=dbl, project_dir=str(root))
    expect_dir = m.isolated_config_dir("octo-cat")
    prefix = f"GH_CONFIG_DIR={shlex.quote(expect_dir)}"

    for rep in (rep_dry, rep_create):
        rendered = rep["rendered_command"]
        assert rendered.startswith(prefix + " ")
        body = rendered[len(prefix) + 1:]
        # round-tripping the rendered text through shlex.split reconstructs the EXACT argv —
        # proof of paste-safety, not merely presence of quote characters.
        assert shlex.split(body) == rep["issue_command"]


# =============================================================== AC-URIDENT-4 — undeclared mode

def test_ac4_undeclared_identity(tmp_path):
    root = tmp_path / "project"
    root.mkdir()  # no .claude/gh-identity at all
    dirty_env = dict(os.environ)
    dirty_env["GH_TOKEN"] = "SENTINEL-GH_TOKEN"
    dirty_env["GH_CONFIG_DIR"] = "/some/preexisting/config/dir"
    dbl = m._RecordingGhRunner()
    rep = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=dbl, project_dir=str(root), base_env=dirty_env)
    assert not rep["refused"]
    assert rep["handle"] is None
    assert len(dbl.envs) == 2
    for env in dbl.envs:
        assert env == dirty_env  # passed through UNMODIFIED: nothing set, nothing removed
    assert rep["rendered_command"].startswith("gh ")
    assert "GH_CONFIG_DIR=" not in rep["rendered_command"]


# =============================================================== AC-URIDENT-5 — hygiene

def test_ac5_no_env_values_printed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octocat")
    dirty_env = dict(os.environ)
    for var in m._AMBIENT_STRIP_VARS:
        dirty_env[var] = "SENTINEL-" + var

    # probe-then-create: BOTH the probe (index 0) and the create (index 1) must fail to degrade.
    dbl_degrade = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 403: must have admin rights", spawned=True),
    })
    rep_degrade = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                             create=True, extra_tokens=[], runner=dbl_degrade, project_dir=str(root),
                             base_env=dirty_env)
    assert rep_degrade["label_degraded"]
    warning = m._format_degrade_warning(m._LABEL_NAME, "o/r", rep_degrade.get("degrade_detail", ""))

    dbl_fail = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 422: validation failed", spawned=True),
    })
    rep_fail = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                          create=True, extra_tokens=[], runner=dbl_fail, project_dir=str(root),
                          base_env=dirty_env)
    gh_diag = m._format_gh_failure(rep_fail.get("rendered_command") or "", rep_fail.get("gh_stderr", ""))

    _declare_identity(root, "../malformed;x")
    diag_malformed = None
    try:
        m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                  extra_tokens=[], runner=m._RecordingGhRunner(), project_dir=str(root),
                  base_env=dirty_env)
    except m.MalformedIdentityError as e:
        diag_malformed = m._format_identity_refusal(e.identity_file)
    assert diag_malformed is not None

    for var in m._AMBIENT_STRIP_VARS:
        sentinel = "SENTINEL-" + var
        assert sentinel not in warning
        assert sentinel not in gh_diag
        assert sentinel not in diag_malformed
    # GH_CONFIG_DIR's VALUE is the one permitted disclosure (it is a path the adopter must act on,
    # not a credential) — this asserts the hygiene rule is "no OTHER env value", not "no values".
    assert m.isolated_config_dir("octocat") not in warning  # not composed into THIS diagnostic
    assert "gh issue create" in gh_diag  # the diagnostic still carries the useful argv


def test_ac5_tempfile_removed(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octocat")
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    import tempfile as tempfile_mod
    monkeypatch.setattr(tempfile_mod, "tempdir", str(scratch))

    # success path
    dbl_ok = m._RecordingGhRunner()
    rep_ok = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                        extra_tokens=[], runner=dbl_ok, project_dir=str(root))
    assert not rep_ok["refused"]
    assert os.listdir(scratch) == []

    # label-degrade path — probe-then-create: BOTH the probe (index 0) and the create (index 1)
    # must fail for the ensure to degrade.
    dbl_degrade = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=1, stdout="", stderr="HTTP 404", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 403", spawned=True),
    })
    rep_degrade = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                             create=True, extra_tokens=[], runner=dbl_degrade, project_dir=str(root))
    assert rep_degrade["label_degraded"]
    assert os.listdir(scratch) == []

    # gh-failure path
    dbl_fail = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 422", spawned=True),
    })
    rep_fail = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                          create=True, extra_tokens=[], runner=dbl_fail, project_dir=str(root))
    assert rep_fail.get("gh_failed")
    assert os.listdir(scratch) == []

    # the refusal path (AC-URIDENT-1) never creates a tempfile at all -- still nothing to leak.
    _declare_identity(root, "../bad;x")
    try:
        m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                  extra_tokens=[], runner=m._RecordingGhRunner(), project_dir=str(root))
    except m.MalformedIdentityError:
        pass
    assert os.listdir(scratch) == []


# =============================================================== AC-URIDENT-6 — hermetic selftest

def test_ac6_selftest_hermetic(monkeypatch, capsys):
    """If `--selftest` ever escapes an injected double, the REAL runner's one spawn call site
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
        assert f"AC-URIDENT-{i} " in out, out
    for i in range(1, 6):
        assert f"AC-URFIRST-{i} " in out, out
    assert "UPSTREAM-SUBMIT-SELFTEST-GREEN" in out


# =============================================================== AC-URIDENT-7 — suite binding

def test_ac7_one_test_function():
    """Runs the module's --selftest as a subprocess (the contract's binding requirement) —
    proving the shipped, unmodified file is GREEN, with every AC-URIDENT and AC-URFIRST line."""
    proc = subprocess.run([sys.executable, SCRIPT_PATH, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UPSTREAM-SUBMIT-SELFTEST-GREEN" in proc.stdout
    for i in range(1, 6):
        assert f"AC-URIDENT-{i} " in proc.stdout


def test_ac7_sibling_module_green():
    """The sibling atom's module (denied to this atom, never edited here) must still pass, run as
    its own subprocess — proving the shipped file, not merely an in-process import."""
    assert os.path.isfile(SIBLING_TEST_PATH)
    proc = subprocess.run([sys.executable, "-m", "pytest", SIBLING_TEST_PATH, "-q"],
                           capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# =============================================================== AC-URIDENT-8 — skill doc

def test_ac8_skill_documents():
    skill_path = os.path.join(REPO_ROOT, "skills", "upstream-submit", "SKILL.md")
    with open(skill_path, encoding="utf-8") as fh:
        text = fh.read()
    assert "GH_CONFIG_DIR" in text
    assert ".claude/gh-identity" in text
    lower = text.lower()
    assert "bare" in lower or "no prefix" in lower or "no env" in lower
    assert "exit" in lower and "4" in text


# =============================================================== AC-URIDENT-9 — changelog

def test_ac9_changelog_records():
    changelog_path = os.path.join(REPO_ROOT, "CHANGELOG.md")
    with open(changelog_path, encoding="utf-8") as fh:
        text = fh.read()
    # Retargeted in the pre-v1 content pass: the changelog is a v1.0.0 baseline, so the
    # assertion binds the CAPABILITY's documented properties, not a per-atom release entry
    # or a pre-v1 PR number (which a zero-history public tree cannot resolve).
    assert "upstream-submit" in text
    lower = text.lower()
    assert "gh_config_dir" in lower
    assert "token" in lower
    assert "refus" in lower or "exit 4" in lower or "exit `4`" in lower


# ================================================ security-review remediation (post-build review)
# A floor-#3 security review returned NO Block and five Risks against this atom. Each fix gets its
# own regression test here so none of these can silently regress again (additive — the frozen
# AC-bound test names above are unchanged).

def test_sec_exit3_diagnostic_shell_quoted(tmp_path):
    """The RISK: `_format_gh_failure` used to print an UNQUOTED `' '.join(argv)` — a THIRD render
    point that, unlike the dry-run line and the `--create` echo, was never routed through
    `render_command`'s `shlex.quote`. Adopter-authored title text (this fixture's tricky title,
    the same shape AC-URIDENT-3 uses) must round-trip through the exit-3 diagnostic exactly like it
    does through the success-path rendering — proof of paste-safety, not merely quote characters
    somewhere in the text."""
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octo-cat")
    tricky = m._GOOD_CANDIDATE.replace(
        "deploy-status false-greens a never-rolled commit",
        "deploy `whoami` && echo pwned; $(id)")
    dbl = m._RecordingGhRunner(responses={
        0: m.GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: m.GhResult(returncode=1, stdout="", stderr="HTTP 422: validation failed", spawned=True),
    })
    rep = m._submit(tricky, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=dbl, project_dir=str(root))
    assert rep.get("gh_failed") is True
    diag = m._format_gh_failure(rep.get("rendered_command") or "", rep.get("gh_stderr", ""))
    expect_dir = m.isolated_config_dir("octo-cat")
    prefix = f"GH_CONFIG_DIR={shlex.quote(expect_dir)}"
    command_line = next(ln for ln in diag.splitlines() if ln.strip().startswith("command"))
    body = command_line.split(":", 1)[1].strip()
    assert body.startswith(prefix + " ")
    # round-trips through shlex.split back to the EXACT argv the verb built.
    assert shlex.split(body[len(prefix) + 1:]) == rep["issue_command"]


def test_sec_runner_env_composed_at_every_internal_call_site(tmp_path, monkeypatch):
    """CRITICAL COMPOSITION POINT (rebase-time resolution, gp25-upstream-submit-identity onto
    gp25-upstream-submit-first-use): this atom's own design originally called for `env` to be a
    REQUIRED (no-default) argument on the runner seam, so a call site that forgot to pass it would
    fail loudly (`TypeError`) instead of silently inheriting the ambient environment. That design is
    INCOMPATIBLE with the sibling `feat-foundry-upstream-submit-first-use` atom's own FROZEN
    regression test (`tests/test_upstream_submit.py::test_sec_argv_allowlist_refuses_out_of_envelope`,
    denied to this atom, never edited here), which calls `_real_gh_runner(cmd)` with exactly ONE
    argument — it predates this feature entirely and is entitled to. `env` therefore carries an
    `= None` default on BOTH seam implementations (an omitted `env` is accepted, not a `TypeError`),
    so the byte-frozen sibling module keeps passing unmodified (see `test_ac7_sibling_module_green`).
    The property the no-default design was actually protecting — this atom's OWN internal call
    sites never silently omitting the composed environment — is preserved by code discipline instead
    of the type signature: `_submit`'s two `run(...)` calls and `_ensure_label`'s two `run(...)`
    calls always pass `env` explicitly, proven here directly against the real seam (not the double),
    with real `subprocess.run` patched to explode if reached, so an out-of-envelope argv omitting
    `env` is refused identically to one that supplies it — the default changes nothing about the
    envelope check, which runs BEFORE `env` is ever inspected."""
    import subprocess as real_subprocess

    def _boom(*_a, **_k):
        raise AssertionError("reached real subprocess.run")

    monkeypatch.setattr(real_subprocess, "run", _boom)
    # an omitted `env` no longer raises TypeError — it is accepted, identically to an explicit None.
    result_omitted = m._real_gh_runner(["gh", "api", "-X", "PUT", "repos/o/r/labels/x"])
    result_explicit_none = m._real_gh_runner(["gh", "api", "-X", "PUT", "repos/o/r/labels/x"], None)
    assert result_omitted.spawned is False and result_explicit_none.spawned is False
    assert result_omitted.returncode == result_explicit_none.returncode

    # the double mirrors the same signature (omission accepted, not a TypeError).
    dbl = m._RecordingGhRunner()
    result_dbl_omitted = dbl(["gh", "issue", "create"])
    assert result_dbl_omitted.spawned is True
    assert dbl.envs == [None]  # the double records the omission honestly, as env=None

    # ...but every REAL call this module's own code makes always composes and passes env, whether
    # or not an identity is declared — proven from captured evidence, never from the signature.
    no_identity_root = tmp_path / "no-identity-project"
    no_identity_root.mkdir()  # no .claude/gh-identity — the undeclared-identity path
    dbl2 = m._RecordingGhRunner()
    rep = m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=dbl2, project_dir=str(no_identity_root))
    assert not rep["refused"]
    assert len(dbl2.envs) == 2 and all(e is not None for e in dbl2.envs)


def test_sec_undecodable_identity_refuses(tmp_path):
    """The RISK: a PRESENT, non-empty, non-UTF-8 `.claude/gh-identity` used to map to `return None`
    (ambient) via the old `except (OSError, UnicodeDecodeError): return None` — the SAME
    disposition as a file that does not exist at all. A present-but-unhonourable declaration must
    refuse (exit 4 / `MalformedIdentityError`), the same disposition as a grammar failure — never a
    silent fall back to ambient. Only an ABSENT/unreadable file (`OSError`) means "no identity"."""
    root = tmp_path / "project"
    root.mkdir()
    claude_dir = root / ".claude"
    claude_dir.mkdir()
    (claude_dir / "gh-identity").write_bytes(b"\xff\xfe\x00bad-utf8-not-a-handle")
    import pytest
    with pytest.raises(m.MalformedIdentityError):
        m.resolve_identity(str(root))

    dbl = m._RecordingGhRunner()
    with pytest.raises(m.MalformedIdentityError):
        m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                  extra_tokens=[], runner=dbl, project_dir=str(root))
    assert dbl.calls == []  # zero gh invocations — same fail-closed disposition as AC-URIDENT-1


def test_sec_ascii_only_whitespace_normalization(tmp_path):
    """The RISK: the shipped guard normalizes with `tr -d '[:space:]'` (ASCII-only, POSIX
    `[:space:]` under the C locale both guard variants run in); the verb used to normalize with
    Python's Unicode-aware `\\s+`. A handle carrying a non-ASCII whitespace character (NBSP,
    U+00A0) would normalize to a VALID handle in the verb but survive untouched in the guard —
    pinning a DIFFERENT account's jail than the guard independently derives from the same file. The
    verb now uses the identical ASCII-only class, so this handle correctly fails the grammar (the
    NBSP survives the strip and is not alphanumeric/hyphen) instead of silently normalizing away."""
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octo cat")  # NBSP embedded — invisible in most editors/diffs
    import pytest
    with pytest.raises(m.MalformedIdentityError):
        m.resolve_identity(str(root))


def test_sec_handle_grammar_fullmatch_not_match():
    """The RISK: `_HANDLE_RE.match` matches even when the subject carries a trailing newline
    (`$` matches just before a trailing `\\n` without `re.MULTILINE`) — the grammar was
    self-sufficient only because the whitespace strip already removed any such newline FIRST, an
    invisible coupling to call order. Prove the regex itself is airtight independent of that
    ordering: `fullmatch` (what `resolve_identity` now uses) rejects a trailing newline that
    `match` would have let through."""
    assert m._HANDLE_RE.match("octocat\n") is not None    # the OLD check would have admitted this
    assert m._HANDLE_RE.fullmatch("octocat\n") is None     # the NEW check correctly refuses it


def test_sec_tempfile_removed_on_write_failure(tmp_path, monkeypatch):
    """The RISK: `NamedTemporaryFile(delete=False)` creates the file on disk BEFORE the `with`
    block's body runs; `tf_path = tf.name` used to be assigned AFTER `tf.write(body)` — if the
    write raised, `tf_path` stayed `None` and the `finally` skipped `os.unlink`, leaking the
    already-created tempfile (holding the rendered, possibly-sensitive ER body). Prove a write
    failure still leaves the tempfile cleaned up: the file created by a monkeypatched,
    raising-on-write wrapper must not survive the (propagated) exception."""
    root = tmp_path / "project"
    root.mkdir()
    _declare_identity(root, "octocat")
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    import tempfile as tempfile_mod
    monkeypatch.setattr(tempfile_mod, "tempdir", str(scratch))

    real_ntf = tempfile_mod.NamedTemporaryFile
    created: dict[str, str] = {}

    class _BoomOnWrite:
        def __init__(self, real):
            self._real = real
            self.name = real.name
            created["path"] = real.name

        def write(self, _data):
            raise OSError("simulated disk-full during body write")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._real.__exit__(*exc)
            return False

    def _patched(*a, **k):
        return _BoomOnWrite(real_ntf(*a, **k))

    monkeypatch.setattr(tempfile_mod, "NamedTemporaryFile", _patched)

    import pytest
    dbl = m._RecordingGhRunner()
    with pytest.raises(OSError):
        m._submit(m._GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                  extra_tokens=[], runner=dbl, project_dir=str(root))
    assert created.get("path"), "the wrapper never recorded a created tempfile path"
    assert not os.path.exists(created["path"]), "tempfile leaked after a failed write"
