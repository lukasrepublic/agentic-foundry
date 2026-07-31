"""tests/test_leak_scan_ls_remote_sink.py — behavioral coverage for
feat-foundry-leak-scan-ls-remote-sink (AC-LSH-1..6): the listing sink (`_ls_remote` together with
`build_ls_remote_argv`) is anchored outside the scanned repository and carries the same
per-invocation hardening the sibling probe sink already carries, so the scanned repository's own
`.git/config` cannot direct or execute anything at this sink.

ANTI-VACUOUS-PASS. AC-LSH-3's and AC-LSH-6's "hostile config/env does not execute" checkpoints
bind to an OBSERVABLE SIDE EFFECT (a marker file is or is not created), never an argv-string or
env-dict-membership check alone — an implementation that adds the override string while leaving
the invocation unanchored, or that short-circuits before reaching the transport, cannot fake an
absent file. Each such checkpoint is paired with a POSITIVE CONTROL proving the relevant transport
was actually reached, so an absent marker can never be satisfied by a sink that silently never
tried.

No test here needs network. Local-path liveness uses a throwaway local bare repository. SSH-form
liveness and the SSH-vector checkpoints drive a FAKE `ssh` executable placed first on PATH — the
standard technique git's own test suite uses to exercise the ssh transport hermetically (a wrapper
that either records being invoked, or execs the trailing remote-command string locally, replacing
its own process so the pack-protocol stdio wiring is preserved) — never `GIT_SSH_COMMAND` (this
atom's own AC-LSH-6 asserts that variable is stripped from the sink's environment) and never the
ambient `ssh`.
"""
from __future__ import annotations

import inspect
import os
import stat
import subprocess
import textwrap

import pytest

from conftest import load_module

SCAN = load_module("scripts/foundry-prepublication-leak-scan.py", "foundry_lsh_scan")


# ---------------------------------------------------------------------------------------- helpers
def _git(args, cwd, check=True, env=None):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def _init_repo(path, branch="trunk"):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", branch], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit(path, relname, content, message="commit"):
    p = path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(["add", relname], path)
    _git(["commit", "-q", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path).stdout.strip()


def _write_executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _prepend_path(monkeypatch, bin_dir):
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _poison_repo_config(repo_path):
    """Append a syntactically MALFORMED stanza (an unterminated section header) that makes git
    FAIL LOUDLY -- `fatal: bad config line N`, a genuine parse abort, never merely a warning --
    if this repository's config is ever read as the enclosing/active repository (AC-LSH-1's
    observable read-detector). Appended, never overwritten, so `git init`'s own valid stanzas
    (bare/filemode/etc.) survive and parse cleanly up to this point.

    Deliberately NOT `repositoryformatversion = 99`: verified empirically (2026-07-29) that git
    treats an unsupported repo version as WARN-and-disregard-the-whole-config for a command like
    `ls-remote` (which needs no local repo state) -- the command still succeeds either way, so
    that particular poison cannot distinguish "read" from "not read" here and would pass
    vacuously whether or not discovery actually reached this repository. A syntax error aborts
    unconditionally instead."""
    cfg = repo_path / ".git" / "config"
    with open(cfg, "a", encoding="utf-8") as fh:
        fh.write("\n[core\n")


FAKE_SSH_EXEC_LOCALLY = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Fake ssh for tests -- the standard technique for exercising git's ssh transport without a
    # real network: ignore the host/options git passes ahead of the trailing remote-command
    # string, and exec that command locally, replacing this process so the pack-protocol stdio
    # wiring stays intact.
    import os, sys, shlex
    cmd = sys.argv[-1]
    parts = shlex.split(cmd)
    os.execvp(parts[0], parts)
    """
)


def _fake_ssh_marker_script(marker_path):
    """A fake `ssh` that proves the transport was reached -- creates `marker_path` -- without any
    real network I/O, then fails cleanly (so the caller gets a clean non-zero, never a hang)."""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import pathlib
        pathlib.Path({str(marker_path)!r}).write_text("reached\\n")
        raise SystemExit(1)
        """
    )


# ---------------------------------------------------------------------------------------- fixtures
@pytest.fixture()
def target_bare_remote(tmp_path):
    """A throwaway local bare 'remote' with one commit, reachable via a plain filesystem path."""
    bare = tmp_path / "target-bare.git"
    _git(["init", "-q", "--bare", str(bare)], tmp_path)
    work = tmp_path / "target-work"
    _init_repo(work)
    sha = _commit(work, "a.txt", "one\n", "root")
    _git(["push", "-q", str(bare), "HEAD:refs/heads/trunk"], work)
    return bare, sha


@pytest.fixture()
def scanned_repo(tmp_path):
    """A minimal scanned repository -- distinct from both the anchor and the target -- whose
    config the sink must never read."""
    repo = tmp_path / "scanned"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    return repo


# =================================================================================================
# AC-LSH-1 -- anchored, and discovery TERMINATES there (read-detector, not a cwd/argv assertion)
# =================================================================================================
class TestAnchoredDiscoveryTerminates:
    def test_scanned_repo_config_is_not_parsed(self, monkeypatch, target_bare_remote, scanned_repo):
        """Plant a config that fails loudly if parsed in the SCANNED repository, run the sink
        from inside it (reproducing the exact confirmed-by-execution scenario), and assert the
        sink still succeeds -- which it can only do if that config was never read."""
        bare, sha = target_bare_remote
        _poison_repo_config(scanned_repo)
        monkeypatch.chdir(scanned_repo)
        shas = SCAN._ls_remote(str(bare), heads_and_tags_only=True)
        assert shas == [sha]

    def test_gitdir_planted_above_anchor_is_not_adopted(self, monkeypatch, tmp_path, target_bare_remote):
        """A bare `mkdtemp()` anchor does NOT stop upward discovery on its own (verified
        2026-07-29). Plant a poisoned, valid gitdir in what will become the anchor's PARENT
        directory, force the anchor to be created underneath it, ALSO chdir the process into a
        nested directory under that same poisoned parent (reproducing the pre-fix cwd-discovery
        vector at the same time), and assert the sink still succeeds -- proving discovery
        terminates at the (now `git init --bare`'d) anchor itself rather than walking upward."""
        bare, sha = target_bare_remote
        parent = tmp_path / "poisoned-parent"
        _init_repo(parent)
        _commit(parent, "p.txt", "p\n", "parent-init")
        _poison_repo_config(parent)
        nested_cwd = parent / "nested" / "deeper"
        nested_cwd.mkdir(parents=True)
        monkeypatch.chdir(nested_cwd)

        real_mkdtemp = SCAN.tempfile.mkdtemp

        def spy_mkdtemp(*a, **kw):
            kw["dir"] = str(parent)
            return real_mkdtemp(*a, **kw)

        monkeypatch.setattr(SCAN.tempfile, "mkdtemp", spy_mkdtemp)
        shas = SCAN._ls_remote(str(bare), heads_and_tags_only=True)
        assert shas == [sha]


# =================================================================================================
# AC-LSH-2 -- hardening set from the REAL invocation; anchor via cwd, never an argv token
# =================================================================================================
class TestHardeningSetAtRealCall:
    def test_hardening_set_members_each_present_at_real_call(self, monkeypatch):
        captured = {}

        class FakeCompleted:
            def __init__(self):
                self.returncode = 0
                self.stdout = b""
                self.stderr = b""

        def fake_run(argv, **kwargs):
            if "ls-remote" in argv:
                captured["argv"] = list(argv)
                captured["cwd"] = kwargs.get("cwd")
                captured["env"] = kwargs.get("env")
            return FakeCompleted()

        monkeypatch.setattr(SCAN.subprocess, "run", fake_run)
        SCAN._ls_remote("https://example.invalid/x.git", heads_and_tags_only=True)

        assert "argv" in captured, "the real ls-remote subprocess call was never made"
        argv = captured["argv"]
        for member in (
            "credential.helper=",
            "core.fsmonitor=",
            "core.sshCommand=ssh",
            "protocol.ext.allow=never",
            "protocol.version=0",
        ):
            assert member in argv, f"hardening-set member missing from the real call's argv: {member!r}"

        # The anchor is the invocation's cwd -- not an argv token.
        assert captured["cwd"], "the real call carried no cwd= (anchor) at all"
        assert captured["cwd"] not in argv

    def test_argv_builder_signature_unchanged(self):
        sig = inspect.signature(SCAN.build_ls_remote_argv)
        assert list(sig.parameters.keys()) == ["url", "heads_and_tags_only"]
        # Exactly how the sibling atom's own (denied-to-edit) `--`-placement test calls it.
        url = "https://example.invalid/x.git"
        argv = SCAN.build_ls_remote_argv(url, heads_and_tags_only=True)
        assert argv[0] == "git"
        dash_idx = argv.index("--")
        assert argv[dash_idx + 1 :] == [url]


# =================================================================================================
# AC-LSH-3 -- a hostile core.sshCommand does not execute, AND the ssh transport is genuinely reached
# =================================================================================================
@pytest.fixture()
def ssh_vector_scenario(tmp_path, monkeypatch, target_bare_remote):
    """The SCANNED repository carries a hostile `core.sshCommand` with an observable side effect
    (a marker file). PATH carries a fake `ssh` -- the literal command name the hardening set
    forces via `-c core.sshCommand=ssh` -- that creates a SEPARATE 'reached' marker and fails
    cleanly, proving both that the hostile command never runs AND that the transport was
    genuinely attempted, never silently skipped."""
    bare, _sha = target_bare_remote
    scanned = tmp_path / "scanned"
    _init_repo(scanned)
    _commit(scanned, "f.txt", "x\n", "init")

    hostile_marker = tmp_path / "hostile-marker"
    reached_marker = tmp_path / "reached-marker"
    _git(["config", "core.sshCommand", f"touch {hostile_marker}; exit 1 #"], scanned)
    _git(["remote", "add", "origin", "git@host.example:owner/repo.git"], scanned)

    bin_dir = tmp_path / "bin-ssh-vector"
    bin_dir.mkdir()
    _write_executable(bin_dir / "ssh", _fake_ssh_marker_script(reached_marker))
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.chdir(scanned)

    ssh_url = f"git@fake-ssh-host.invalid:{bare}"
    return hostile_marker, reached_marker, ssh_url


class TestSshCommandVector:
    def test_hostile_ssh_command_does_not_execute(self, ssh_vector_scenario):
        hostile_marker, _reached_marker, ssh_url = ssh_vector_scenario
        SCAN._ls_remote(ssh_url, heads_and_tags_only=True)
        assert not hostile_marker.exists()

    def test_ssh_transport_was_actually_reached(self, ssh_vector_scenario):
        _hostile_marker, reached_marker, ssh_url = ssh_vector_scenario
        SCAN._ls_remote(ssh_url, heads_and_tags_only=True)
        assert reached_marker.exists()


# =================================================================================================
# AC-LSH-4 -- the SCANNED repo's insteadOf does not rewrite at the sink
# =================================================================================================
@pytest.fixture()
def insteadof_scenario(tmp_path):
    real_work = tmp_path / "real-work"
    _init_repo(real_work)
    real_sha = _commit(real_work, "r.txt", "real\n", "real")
    real_bare = tmp_path / "real-bare.git"
    _git(["init", "-q", "--bare", str(real_bare)], tmp_path)
    _git(["push", "-q", str(real_bare), "HEAD:refs/heads/trunk"], real_work)

    attacker_work = tmp_path / "attacker-work"
    _init_repo(attacker_work)
    attacker_sha = _commit(attacker_work, "a.txt", "attacker\n", "attacker")
    attacker_bare = tmp_path / "attacker-bare.git"
    _git(["init", "-q", "--bare", str(attacker_bare)], tmp_path)
    _git(["push", "-q", str(attacker_bare), "HEAD:refs/heads/trunk"], attacker_work)

    scanned = tmp_path / "scanned"
    _init_repo(scanned)
    _commit(scanned, "f.txt", "x\n", "init")
    _git(["config", f"url.{attacker_bare}.insteadOf", str(real_bare)], scanned)

    return scanned, str(real_bare), real_sha, attacker_sha


class TestInsteadOfNotRewrittenAtSink:
    def test_scanned_repo_insteadof_does_not_rewrite_at_sink(self, monkeypatch, insteadof_scenario):
        scanned, real_url, real_sha, attacker_sha = insteadof_scenario
        monkeypatch.chdir(scanned)
        shas = SCAN._ls_remote(real_url, heads_and_tags_only=True)
        assert shas == [real_sha]
        assert attacker_sha not in shas


# =================================================================================================
# AC-LSH-6 -- the sink environment is built by SUBTRACTION
# =================================================================================================
_SINK_ENV_REMOVED_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
)


class TestSinkEnvironmentBySubtraction:
    @pytest.mark.parametrize("var", _SINK_ENV_REMOVED_VARS)
    def test_sink_environment_removes_each_git_redirect_var(self, monkeypatch, var):
        monkeypatch.setenv(var, "poison-value")
        env = SCAN._ls_remote_sink_env()
        assert var not in env, f"{var} was not removed from the sink environment"

    def test_ambient_git_dir_does_not_defeat_the_anchor(self, monkeypatch, tmp_path, insteadof_scenario):
        """GIT_DIR IS the repository regardless of anchor/cwd/-C (verified 2026-07-29): set it to
        point at a repository carrying an `insteadOf` rewrite and assert the sink still contacts
        the URL it was given, unrewritten -- which it can only do if GIT_DIR was removed."""
        scanned, real_url, real_sha, attacker_sha = insteadof_scenario
        monkeypatch.setenv("GIT_DIR", str(scanned / ".git"))
        shas = SCAN._ls_remote(real_url, heads_and_tags_only=True)
        assert shas == [real_sha]
        assert attacker_sha not in shas

    def test_ambient_git_ssh_command_does_not_execute(self, monkeypatch, tmp_path, target_bare_remote):
        """GIT_SSH_COMMAND OUTRANKS `-c core.sshCommand=ssh` (verified 2026-07-29 by execution):
        set it to a command with an observable side effect and assert that side effect is
        absent."""
        bare, _sha = target_bare_remote
        marker = tmp_path / "env-ssh-command-marker"
        monkeypatch.setenv("GIT_SSH_COMMAND", f"touch {marker}; exit 1 #")
        ssh_url = f"git@fake-ssh-host.invalid:{bare}"
        SCAN._ls_remote(ssh_url, heads_and_tags_only=True)
        assert not marker.exists()


# =================================================================================================
# AC-LSH-5 -- the sink still WORKS on BOTH transports (the counterweight to "safe because dead")
# =================================================================================================
class TestSinkStillWorks:
    def test_sink_still_lists_advertised_refs_local(self, target_bare_remote):
        bare, sha = target_bare_remote
        shas = SCAN._ls_remote(str(bare), heads_and_tags_only=True)
        assert shas == [sha]

    def test_sink_still_lists_advertised_refs_ssh_form(self, monkeypatch, tmp_path, target_bare_remote):
        bare, sha = target_bare_remote
        bin_dir = tmp_path / "bin-live-ssh"
        bin_dir.mkdir()
        _write_executable(bin_dir / "ssh", FAKE_SSH_EXEC_LOCALLY)
        _prepend_path(monkeypatch, bin_dir)
        ssh_url = f"git@fake-ssh-host.invalid:{bare}"
        shas = SCAN._ls_remote(ssh_url, heads_and_tags_only=True)
        assert shas == [sha]
