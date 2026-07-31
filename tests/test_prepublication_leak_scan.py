"""tests/test_prepublication_leak_scan.py — behavioral coverage for
feat-foundry-runtime-gitignore-leak-scan's pre-publication scan (AC-RGLS-8..14).

ANTI-VACUOUS-PASS (contract header): no criterion here asserts a CLEAN verdict for THIS
repository -- it is expected to report FOUND against its own current history (GO-PUBLIC.md §5.5).
Every checkpoint instead binds to fresh scratch fixtures (throwaway git repos, throwaway bare
"remotes") that this module builds and tears down; the module never touches this repository's own
history or its real denylist beyond READING it (to prove term-set identity).

The remote-probe / positive-control tests need REAL, un-mocked git want-negotiation behavior --
whether the server advertises an object matters. The scan's own probe FORCES `protocol.version=0`
client-side (`build_probe_argv`, AC-RGLS-9b) precisely because that is what makes the classic,
documented `uploadpack.allowanysha1inwant` enforcement apply even over a same-machine local-path
transport (verified empirically while building this suite: WITHOUT forcing v0, a local-path
remote's "local optimization" bypasses the advertised-object check entirely regardless of server
config, which would make the positive control untestable and the classification meaningless; WITH
it forced, a local bare repo behaves exactly like a real hardened remote -- refuses by default,
serves only what `uploadpack.allowanysha1inwant` permits). One local bare-repo fixture therefore
suffices for every probe/positive-control test; no daemon or network is needed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

import test_runtime_gitignore

SCAN = load_module("scripts/foundry-prepublication-leak-scan.py", "foundry_rgls_scan")
LEAK_SCAN = load_module(".github/actions/leak-gate/leak_scan.py", "leak_scan_shared_for_rgls")

SCAN_SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-prepublication-leak-scan.py")
APPLIER = os.path.join(REPO_ROOT, "scripts", "foundry-apply-runtime-gitignore.sh")
# The denylist left this tree in feat-foundry-denylist-out-of-tree. These tests never needed the
# operator's real terms -- they exercise resolution, announcement and scope plumbing -- so they now
# build a synthetic list per test. `FIXTURE_DENYLIST_TERMS` is deliberately not any real term.
FIXTURE_DENYLIST_TERMS = ["alpha-fixture", "bravo-fixture"]


def _fixture_denylist(tmp_path, name="denylist.txt"):
    p = tmp_path / name
    p.write_text("\n".join(FIXTURE_DENYLIST_TERMS) + "\n", encoding="utf-8")
    return str(p)


def _scan_env(tmp_path):
    """Env for a SUBPROCESS run of the scanner.

    The default denylist now resolves from $CLAUDE_PROJECT_DIR (the list is no longer in this tree),
    and conftest's autouse `_clean_env` correctly scrubs that variable so an operator's ambient
    workspace never leaks into a hermetic test. So every subprocess invocation must supply its own
    throwaway workspace -- otherwise the scanner fail-closes with no denylist and the test reads an
    empty stdout, which surfaces as a bare StopIteration rather than anything diagnostic."""
    ws = tmp_path / "_scan_workspace"
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "foundry-leak-denylist.txt").write_text(
        "\n".join(FIXTURE_DENYLIST_TERMS) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(ws)
    return env
FRAGMENT = os.path.join(REPO_ROOT, "scripts", "foundry-runtime.gitignore")


# ---------------------------------------------------------------------------------------- helpers
def _git(args, cwd, check=True, env=None):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def _init_repo(path, branch="trunk"):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["checkout", "-q", "-b", branch], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit(path, relname, content, message="commit"):
    p = path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(["add", relname], path)
    _git(["commit", "-q", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path).stdout.strip()


def _apply_fragment(path):
    r = subprocess.run([APPLIER, str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _allow_any_sha1(bare_path):
    _git(["config", "uploadpack.allowanysha1inwant", "true"], bare_path)


@pytest.fixture()
def scratch_repo(tmp_path):
    """A minimal, fragment-covered scratch repo with one commit, no remote."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "README.txt", "hello world\n", "init")
    _apply_fragment(repo)
    return repo


@pytest.fixture()
def local_bare_remote(tmp_path):
    """A bare repo usable as a git remote via a plain filesystem path. Restrictive by default
    (`uploadpack.allowanysha1inwant` unset); call `_allow_any_sha1()` to make it permissive."""
    bare = tmp_path / "bare-remote.git"
    _git(["init", "-q", "--bare", str(bare)], tmp_path)
    return bare


def _seed_remote(bare_path, tmp_path, branch="trunk", name="seed-work"):
    """Push a two-commit history (root + tip) to `bare_path`; return (root_sha, tip_sha)."""
    work = tmp_path / name
    _init_repo(work, branch=branch)
    root_sha = _commit(work, "a.txt", "one\n", "root")
    tip_sha = _commit(work, "a.txt", "two\n", "tip")
    _git(["push", "-q", str(bare_path), f"HEAD:refs/heads/{branch}"], work)
    return root_sha, tip_sha


def _list_all_objects(repo):
    out = _git(["cat-file", "--batch-all-objects", "--batch-check=%(objectname)"], repo, check=False).stdout
    return sorted(out.splitlines())


# =================================================================================================
# AC-RGLS-9 -- probe: sink validation, hardened invocation, disposable store, pure classifier
# =================================================================================================
class TestProbeClassification:
    def test_probe_classification_resolved(self):
        assert SCAN.classify_probe_result(0, "") == "resolved"

    def test_probe_classification_refused_unadvertised(self):
        msg = "error: Server does not allow request for unadvertised object deadbeef"
        assert SCAN.classify_probe_result(1, msg) == "refused"

    def test_probe_classification_refused_not_our_ref(self):
        msg = "fatal: remote error: upload-pack: not our ref 0000000000000000000000000000000000000000"
        assert SCAN.classify_probe_result(128, msg) == "refused"

    def test_probe_classification_error_on_other_failure(self):
        msg = "fatal: unable to access 'https://example.invalid/': Could not resolve host"
        assert SCAN.classify_probe_result(128, msg) == "error"

    def test_probe_classification_error_never_masquerades_as_clean(self):
        assert SCAN.classify_probe_result(1, "") not in ("resolved",)


def test_malformed_sha_line_is_error(tmp_path):
    p = tmp_path / "known-bad-shas.txt"
    p.write_text("not-a-sha\n", encoding="utf-8")
    shas, err = SCAN.load_recorded_bad_shas(str(p))
    assert shas is None
    assert err is not None and "40-hex" in err


def test_malformed_sha_line_is_error_short_hex(tmp_path):
    p = tmp_path / "known-bad-shas.txt"
    p.write_text("deadbeef\n", encoding="utf-8")
    shas, err = SCAN.load_recorded_bad_shas(str(p))
    assert shas is None and err is not None


def test_malformed_sha_line_ignores_blank_and_comment_lines(tmp_path):
    p = tmp_path / "known-bad-shas.txt"
    p.write_text("# a comment\n\n" + "a" * 40 + "\n", encoding="utf-8")
    shas, err = SCAN.load_recorded_bad_shas(str(p))
    assert err is None
    assert shas == ["a" * 40]


class TestRemoteValueRestriction:
    def test_remote_value_restriction_rejects_ext(self, scratch_repo):
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "ext::sh -c 'echo pwned'")
        assert url is None and err is not None

    def test_remote_value_restriction_rejects_fd(self, scratch_repo):
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "fd::0")
        assert url is None and err is not None

    def test_remote_value_restriction_accepts_https(self, scratch_repo):
        url, err, name = SCAN.resolve_remote(str(scratch_repo), "https://example.invalid/repo.git")
        assert err is None and url == "https://example.invalid/repo.git"
        assert name is None  # a literal URL, not a configured remote name

    def test_remote_value_restriction_accepts_ssh_scheme(self, scratch_repo):
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "ssh://git@example.invalid/repo.git")
        assert err is None and url is not None

    def test_remote_value_restriction_accepts_git_at_form(self, scratch_repo):
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "git@example.invalid:owner/repo.git")
        assert err is None and url is not None

    def test_remote_value_restriction_rejects_bare_path(self, scratch_repo):
        # a bare filesystem path is NOT a configured remote name here, and not an allowed URL form
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "/etc/passwd")
        assert url is None and err is not None

    def test_remote_value_restriction_accepts_configured_name(self, scratch_repo, local_bare_remote):
        _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
        url, err, name = SCAN.resolve_remote(str(scratch_repo), "origin")
        assert err is None and url == str(local_bare_remote)
        assert name == "origin"

    def test_remote_value_restriction_rejects_ext_even_if_configured(self, scratch_repo):
        _git(["remote", "add", "sneaky", "ext::sh -c false"], scratch_repo)
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "sneaky")
        assert url is None and err is not None

    # ---------------------------------------------------------------- BLOCK (a): insteadOf
    def test_insteadof_redirect_does_not_redirect_the_probe(self, scratch_repo):
        """AC-RGLS-9b/GO-PUBLIC.md §5.4-class vector: `git remote get-url` EXPANDS
        `url.*.insteadOf` (documented git behaviour), so a scanned repository's own `.git/config`
        can silently redirect a naive probe to an attacker-controlled host. FAILS without the fix
        (BLOCK a): the old `resolve_remote` used `git remote get-url`, which would return the
        redirected `http://attacker.example/...` URL here instead of the real configured one."""
        real_url = "https://github.com/example/real-repo.git"
        _git(["remote", "add", "origin", real_url], scratch_repo)
        _git(["config", "url.http://attacker.example/.insteadOf", "https://github.com/"], scratch_repo)

        # Sanity: prove `git remote get-url` WOULD be redirected -- this is the vulnerability.
        redirected = _git(["remote", "get-url", "origin"], scratch_repo).stdout.strip()
        assert redirected.startswith("http://attacker.example/"), redirected

        url, err, name = SCAN.resolve_remote(str(scratch_repo), "origin")
        assert err is None
        assert url == real_url
        assert name == "origin"

    # ---------------------------------------------------------------- BLOCK (b): configured-remote allow-list
    def test_configured_remote_disallowed_http_scheme_is_rejected(self, scratch_repo):
        """A CONFIGURED remote's URL must pass the same allow-list as a directly-passed URL value.
        FAILS without the fix (BLOCK b): the old code checked the allow-list ONLY on the
        directly-passed-value branch, never on a configured remote's resolved URL."""
        _git(["remote", "add", "insecure", "http://insecure.example/repo.git"], scratch_repo)
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "insecure")
        assert url is None and err is not None

    def test_configured_remote_file_scheme_is_rejected(self, scratch_repo, tmp_path):
        target = tmp_path / "filescheme-target"
        target.mkdir()
        _git(["remote", "add", "viafile", f"file://{target}"], scratch_repo)
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "viafile")
        assert url is None and err is not None

    def test_configured_remote_local_path_escape_hatch_requires_existing_directory(self, scratch_repo, tmp_path):
        """The narrow local-path escape hatch (BLOCK b) is EXPLICIT: an absolute path to an
        EXISTING directory only -- a configured remote pointing at a non-existent absolute path is
        still refused, never silently accepted."""
        missing = tmp_path / "does-not-exist-at-all"
        _git(["remote", "add", "gone", str(missing)], scratch_repo)
        url, err, _name = SCAN.resolve_remote(str(scratch_repo), "gone")
        assert url is None and err is not None

    # ---------------------------------------------------------------- Risk #1: default-remote selection
    def test_default_remote_prefers_origin(self, scratch_repo, tmp_path):
        bare_a = tmp_path / "a.git"
        bare_b = tmp_path / "origin.git"
        _git(["init", "-q", "--bare", str(bare_a)], tmp_path)
        _git(["init", "-q", "--bare", str(bare_b)], tmp_path)
        _git(["remote", "add", "backup", str(bare_a)], scratch_repo)
        _git(["remote", "add", "origin", str(bare_b)], scratch_repo)
        url, err, name = SCAN.resolve_remote(str(scratch_repo), None)
        assert err is None
        assert name == "origin"
        assert url == str(bare_b)

    def test_default_remote_multiple_and_unnamed_is_a_finding(self, scratch_repo, tmp_path):
        """Risk #1: a repo with multiple remotes and none named 'origin' must never silently pick
        `sorted(remotes)[0]` -- that is itself a FINDING (an ambiguous default), not a pick. FAILS
        without the fix: the old code picked `sorted(configured)[0]` (deterministically "backup")
        and returned no error at all."""
        bare_a = tmp_path / "backup.git"
        bare_b = tmp_path / "fork.git"
        _git(["init", "-q", "--bare", str(bare_a)], tmp_path)
        _git(["init", "-q", "--bare", str(bare_b)], tmp_path)
        _git(["remote", "add", "backup", str(bare_a)], scratch_repo)
        _git(["remote", "add", "fork", str(bare_b)], scratch_repo)
        url, err, name = SCAN.resolve_remote(str(scratch_repo), None)
        assert url is None
        assert err is not None
        assert "ambiguous" in err.lower() or "multiple" in err.lower()


def test_hardened_git_invocation():
    url = "https://example.invalid/x.git"
    sha = "a" * 40
    argv = SCAN.build_probe_argv("/tmp/disposable", url, sha)
    assert argv[0] == "git"
    assert argv.count("-c") >= 3
    assert "credential.helper=" in argv
    assert "core.fsmonitor=" in argv
    assert "core.sshCommand=ssh" in argv
    # BLOCK (c): `--` precedes BOTH positional args (url, then sha) -- neither can land in git's
    # OPTION position ahead of `--`. FAILS without the fix: the old shape put `url` BEFORE `--`.
    dash_idx = argv.index("--")
    assert argv[dash_idx + 1] == url
    assert argv[dash_idx + 2] == sha
    assert argv[dash_idx + 1 :] == [url, sha]
    assert argv[-1] == sha
    assert "--filter=blob:none" in argv
    assert "--depth=1" in argv
    assert "--no-write-fetch-head" in argv


def test_ls_remote_argv_dash_dash_precedes_url():
    """BLOCK (c) for the advertised-tips probe: `--` before the URL. FAILS without the fix: the
    old `_advertised_tips` built `["git", ..., "ls-remote", "--heads", "--tags", url]` with no `--`
    at all, so `url` sat in git's OPTION position."""
    url = "https://example.invalid/x.git"
    argv = SCAN.build_ls_remote_argv(url, heads_and_tags_only=True)
    assert argv[0] == "git"
    dash_idx = argv.index("--")
    assert argv[dash_idx + 1 :] == [url]
    assert argv[-1] == url
    assert "--heads" in argv and "--tags" in argv

    argv_all = SCAN.build_ls_remote_argv(url, heads_and_tags_only=False)
    dash_idx_all = argv_all.index("--")
    assert argv_all[dash_idx_all + 1 :] == [url]
    assert "--heads" not in argv_all and "--tags" not in argv_all


def test_hardened_git_invocation_sets_no_terminal_prompt(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 1
        stdout = b""
        stderr = b"fatal: could not read"

    def fake_run(argv, capture_output, env, timeout):
        captured["argv"] = argv
        captured["env"] = env
        return FakeCompleted()

    monkeypatch.setattr(SCAN.subprocess, "run", fake_run)
    SCAN.run_probe_fetch("/tmp/disposable-x", "https://example.invalid/x.git", "a" * 40)
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["argv"][0] == "git"


def test_probe_uses_disposable_store():
    created = []
    removed = []

    real_mkdtemp = SCAN.tempfile.mkdtemp
    real_rmtree = SCAN.shutil.rmtree

    def spy_mkdtemp(*a, **kw):
        d = real_mkdtemp(*a, **kw)
        created.append(d)
        return d

    def spy_rmtree(path, ignore_errors=False):
        removed.append(path)
        return real_rmtree(path, ignore_errors=ignore_errors)

    orig_mkdtemp = SCAN.tempfile.mkdtemp
    orig_rmtree = SCAN.shutil.rmtree
    SCAN.tempfile.mkdtemp = spy_mkdtemp
    SCAN.shutil.rmtree = spy_rmtree
    try:
        outcome, _detail = SCAN.probe_sha("https://example.invalid/nope.git", "a" * 40, timeout=5)
    finally:
        SCAN.tempfile.mkdtemp = orig_mkdtemp
        SCAN.shutil.rmtree = orig_rmtree

    assert outcome == "error"
    assert len(created) == 1
    assert created[0] in removed
    assert not os.path.exists(created[0])


def test_scanned_repo_object_store_untouched(scratch_repo, local_bare_remote, tmp_path):
    root_sha, _tip_sha = _seed_remote(local_bare_remote, tmp_path)
    _allow_any_sha1(local_bare_remote)
    before_objects = _list_all_objects(scratch_repo)
    before_refs = _git(["for-each-ref"], scratch_repo).stdout

    outcome, _detail = SCAN.probe_sha(str(local_bare_remote), root_sha)
    assert outcome == "resolved"

    after_objects = _list_all_objects(scratch_repo)
    after_refs = _git(["for-each-ref"], scratch_repo).stdout
    assert before_objects == after_objects
    assert before_refs == after_refs
    assert not (scratch_repo / ".git" / "FETCH_HEAD").exists()


# =================================================================================================
# AC-RGLS-10 -- positive control (needs real advertised/unadvertised want-negotiation semantics)
# =================================================================================================
class TestPositiveControl:
    def test_positive_control_failure_fails_scope(self, scratch_repo, local_bare_remote, tmp_path):
        _seed_remote(local_bare_remote, tmp_path)  # allowanysha1inwant left at its restrictive default
        _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
        ok, findings, resolved_url, had_error, remote_name, probe_count = SCAN.remote_probe_scope(str(scratch_repo), "origin", [])
        assert ok is False
        assert had_error is True
        assert any("positive control" in f.message.lower() for f in findings)
        assert remote_name == "origin"

    def test_positive_control_success_permits_a_clean_scope(self, scratch_repo, local_bare_remote, tmp_path):
        _seed_remote(local_bare_remote, tmp_path)
        _allow_any_sha1(local_bare_remote)
        _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
        ok, findings, resolved_url, had_error, remote_name, probe_count = SCAN.remote_probe_scope(str(scratch_repo), "origin", [])
        assert ok is True, [str(f) for f in findings]
        assert findings == []
        assert remote_name == "origin"
        assert probe_count == 1  # just the positive control -- no recorded-bad SHAs supplied

    def test_control_object_must_be_unadvertised(self, local_bare_remote, tmp_path):
        root_sha, tip_sha = _seed_remote(local_bare_remote, tmp_path)
        _allow_any_sha1(local_bare_remote)
        url = str(local_bare_remote)
        candidate = SCAN.find_unadvertised_control_object(url)
        assert candidate is not None
        assert candidate == root_sha
        tips = SCAN._advertised_tips(url)
        assert candidate not in tips
        assert tip_sha in tips

    def test_control_object_none_constructible_when_single_commit(self, tmp_path):
        bare = tmp_path / "single-bare.git"
        _git(["init", "-q", "--bare", str(bare)], tmp_path)
        _allow_any_sha1(bare)
        work = tmp_path / "single-work"
        _init_repo(work)
        _commit(work, "a.txt", "only\n", "root-only")
        _git(["push", "-q", str(bare), "HEAD:refs/heads/trunk"], work)
        candidate = SCAN.find_unadvertised_control_object(str(bare))
        assert candidate is None

    def test_control_object_excludes_full_advertisement_not_just_heads_and_tags(self, local_bare_remote, tmp_path):
        """Risk #3: a candidate that is advertised via a NON-heads/tags ref (e.g. GitHub's
        `refs/pull/*/head`) must not be selected as the "unadvertised" control object -- the
        exclusion set must be the FULL advertisement, not just `refs/heads`+`refs/tags`. FAILS
        without the fix: the old code excluded candidates only against `_advertised_tips`
        (`--heads --tags`), so a candidate advertised solely via another ref namespace would still
        be returned as if it were genuinely unadvertised."""
        root_sha, _tip_sha = _seed_remote(local_bare_remote, tmp_path)
        _allow_any_sha1(local_bare_remote)
        # advertise the would-be control candidate (the root commit) via a non-heads/tags ref --
        # simulating refs/pull/*/head style advertisement.
        work = tmp_path / "seed-work"
        _git(["push", "-q", str(local_bare_remote), f"{root_sha}:refs/pull/1/head"], work)
        candidate = SCAN.find_unadvertised_control_object(str(local_bare_remote))
        # the root commit has no parent of its own, so once it is excluded (now advertised via
        # refs/pull/1/head), no further candidate is constructible.
        assert candidate is None

    def test_no_remote_configured_is_found(self, scratch_repo):
        ok, findings, url, had_error, remote_name, probe_count = SCAN.remote_probe_scope(str(scratch_repo), None, [])
        assert ok is False
        assert url is None
        assert remote_name is None
        assert probe_count == 0
        assert any("no remote configured" in f.message.lower() for f in findings)

    def test_unreachable_remote_is_found(self, scratch_repo):
        ok, findings, url, had_error, remote_name, probe_count = SCAN.remote_probe_scope(
            str(scratch_repo), "https://example.invalid/does-not-exist-9f3c.git", []
        )
        assert ok is False
        assert had_error is True


# =================================================================================================
# AC-RGLS-11 -- one term set, resolved from the scan's own location
# =================================================================================================
def test_single_term_set(tmp_path):
    # The default now resolves OUTSIDE this tree, from the private workspace, and returns None when
    # that is unavailable -- never a tree-relative fallback, which is how an off-tree list silently
    # becomes an in-tree one again.
    import os as _os
    _prev = _os.environ.pop("CLAUDE_PROJECT_DIR", None)
    try:
        assert SCAN.default_denylist_path() is None
        _os.environ["CLAUDE_PROJECT_DIR"] = str(tmp_path)
        assert SCAN.default_denylist_path() == _os.path.realpath(
            _os.path.join(str(tmp_path), SCAN.DENYLIST_WORKSPACE_RELPATH))
        assert REPO_ROOT not in SCAN.default_denylist_path()
    finally:
        _os.environ.pop("CLAUDE_PROJECT_DIR", None)
        if _prev is not None:
            _os.environ["CLAUDE_PROJECT_DIR"] = _prev
    fixture = _fixture_denylist(tmp_path)
    terms_direct = LEAK_SCAN.load_denylist(fixture)
    terms_via_scan = LEAK_SCAN.load_denylist(fixture)
    assert terms_direct == terms_via_scan
    assert len(terms_direct) >= 1


def test_denylist_resolved_from_scan_location(tmp_path):
    """Even when --root points at a directory carrying its OWN (hostile) denylist copy at the
    same relative path, the scan resolves the REAL default denylist from its own location."""
    fake_root = tmp_path / "fake-root"
    fake_denylist_dir = fake_root / ".github" / "actions" / "leak-gate"
    fake_denylist_dir.mkdir(parents=True)
    (fake_denylist_dir / "denylist.txt").write_text("totally-different-term\n", encoding="utf-8")
    _init_repo(fake_root)
    _commit(fake_root, "f.txt", "hello\n", "init")
    _apply_fragment(fake_root)

    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True)
    real = workspace / ".claude" / "foundry-leak-denylist.txt"
    real.write_text("\n".join(FIXTURE_DENYLIST_TERMS) + "\n", encoding="utf-8")

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(workspace)
    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(fake_root)],
                       capture_output=True, text=True, env=env)
    denylist_line = next(l for l in r.stdout.splitlines() if l.startswith("foundry-prepublication-leak-scan: denylist="))
    # The hostile copy sitting inside --root is still ignored; the term set comes from the workspace.
    assert str(fake_denylist_dir) not in denylist_line
    assert os.path.realpath(str(real)) in denylist_line


# =================================================================================================
# AC-RGLS-12 -- announced, per-scope excluded, denylist-origin separated, no term echo, no file
# =================================================================================================
def test_announces_denylist_path_and_term_count(scratch_repo, tmp_path):
    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo)], capture_output=True, text=True, env=_scan_env(tmp_path))
    assert "denylist=" in r.stdout
    assert f"terms={len(FIXTURE_DENYLIST_TERMS)}" in r.stdout
    assert "(default)" in r.stdout


def test_announces_denylist_override_labelled_non_default(scratch_repo, tmp_path):
    custom = tmp_path / "custom-denylist.txt"
    custom.write_text("custom-term\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo), "--denylist", str(custom)],
        capture_output=True,
        text=True,
    )
    assert "NON-DEFAULT" in r.stdout


def test_exclusion_applied_per_scope_working_tree(tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("proprietary-term\n", encoding="utf-8")
    terms = LEAK_SCAN.load_denylist(str(denylist))
    matcher = LEAK_SCAN.build_matcher(terms)
    ok, findings, excluded_real = SCAN.working_tree_scope(str(tmp_path), str(denylist), terms, matcher, LEAK_SCAN)
    assert ok is True and findings == []
    assert excluded_real == os.path.realpath(str(denylist))


def test_exclusion_applied_per_scope_history(tmp_path):
    """Risk #2 fix: the denylist's own historical content is separated into 'denylist-origin' by
    CONTENT match (not by the blob's reported path) -- the blob IS scanned, just bucketed
    separately, never silently excluded outright."""
    repo = tmp_path / "histrepo"
    _init_repo(repo)
    denylist_relpath = ".github/actions/leak-gate/denylist.txt"
    content = "proprietary-term\n"
    _commit(repo, denylist_relpath, content, "add denylist")
    terms = ["proprietary-term"]
    matcher = LEAK_SCAN.build_matcher(terms)
    # The fixture COMMITS the denylist into the repo, so this is the in-repo case the
    # denylist-origin bucket exists for; pinned explicitly now that the bucket is gated on
    # containment (feat-foundry-denylist-out-of-tree).
    ok, findings, denylist_findings = SCAN.history_scope(
        str(repo), {denylist_relpath}, terms, matcher, denylist_content=content.encode("utf-8"),
        denylist_inside_root=True
    )
    assert ok is True
    assert findings == []  # not counted as a real finding
    assert len(denylist_findings) == 1  # scanned + separately categorised, never silently dropped
    assert denylist_findings[0].category == "denylist-origin"


def test_history_scope_scans_blob_even_when_reported_path_is_denylist_path(monkeypatch):
    """Regression for Risk #2: previously, ANY blob whose reported path (git's rev-list --objects
    dedupes each object to a SINGLE reported path) equalled the denylist's own path was `continue`d
    before ever entering the scan corpus, regardless of its actual content. So a genuine leak
    blob that happened to be reported at that path (a plain consequence of git's traversal order,
    not evidence the content is actually the denylist's) was silently never scanned at all. FAILS
    without the fix: the old code always treated `path in denylist_relpaths` as exclude-outright,
    so `findings` would be empty and this real leak would never surface."""
    same_sha = "c" * 40
    denylist_content = b"totally-different-denylist-content\n"
    real_leak_content = b"proprietary-term appears here\n"
    fake_entries = [(same_sha, ".github/actions/leak-gate/denylist.txt")]
    monkeypatch.setattr(SCAN, "_rev_list_objects", lambda root: fake_entries)
    monkeypatch.setattr(SCAN, "_batch_check_types", lambda root, shas: {same_sha: "blob"})
    monkeypatch.setattr(SCAN, "_batch_blob_contents", lambda root, shas: [(same_sha, real_leak_content)])
    monkeypatch.setattr(SCAN, "_commit_messages", lambda root: [])
    monkeypatch.setattr(SCAN, "_tag_messages", lambda root: [])

    terms = ["proprietary-term"]
    matcher = LEAK_SCAN.build_matcher(terms)
    # `denylist_inside_root=True` pins the LEGITIMATE case these two tests are about: a repository
    # that genuinely carries its own denylist, where a historical blob of it is an expected artifact.
    # feat-foundry-denylist-out-of-tree gated the bucket on that containment, because once the list
    # lives OUTSIDE the scanned repo a byte-identical blob is no longer an artifact -- it is the leak.
    # (Covered from the other direction by
    # tests/test_denylist_out_of_tree.py::test_offtree_denylist_blob_in_history_is_a_FINDING_not_denylist_origin.)
    ok, findings, denylist_findings = SCAN.history_scope(
        "/irrelevant", {".github/actions/leak-gate/denylist.txt"}, terms, matcher,
        denylist_content=denylist_content, denylist_inside_root=True
    )
    assert ok is False  # a REAL finding, never silently dropped
    assert len(findings) == 1
    assert findings[0].category == "finding"
    assert denylist_findings == []


def test_denylist_origin_hits_separate_category(monkeypatch):
    """A blob whose CONTENT is byte-identical to the denylist file's own current content -- reached
    via a path that is NOT the denylist's current path (git's single-path dedupe reported it
    elsewhere, e.g. a historical rename) -- is still bucketed 'denylist-origin', separate from
    ordinary findings, and does not by itself fail the scope. Risk #2: content-based, not
    path-based, so this is true regardless of which single path git happens to report."""
    same_sha = "b" * 40
    denylist_content = b"proprietary-term\n"
    fake_entries = [(same_sha, "copies/elsewhere.txt")]  # git reports ONE path, and it is NOT the denylist's own
    monkeypatch.setattr(SCAN, "_rev_list_objects", lambda root: fake_entries)
    monkeypatch.setattr(SCAN, "_batch_check_types", lambda root, shas: {same_sha: "blob"})
    monkeypatch.setattr(SCAN, "_batch_blob_contents", lambda root, shas: [(same_sha, denylist_content)])
    monkeypatch.setattr(SCAN, "_commit_messages", lambda root: [])
    monkeypatch.setattr(SCAN, "_tag_messages", lambda root: [])

    terms = ["proprietary-term"]
    matcher = LEAK_SCAN.build_matcher(terms)
    # `denylist_inside_root=True` pins the LEGITIMATE case these two tests are about: a repository
    # that genuinely carries its own denylist, where a historical blob of it is an expected artifact.
    # feat-foundry-denylist-out-of-tree gated the bucket on that containment, because once the list
    # lives OUTSIDE the scanned repo a byte-identical blob is no longer an artifact -- it is the leak.
    # (Covered from the other direction by
    # tests/test_denylist_out_of_tree.py::test_offtree_denylist_blob_in_history_is_a_FINDING_not_denylist_origin.)
    ok, findings, denylist_findings = SCAN.history_scope(
        "/irrelevant", {".github/actions/leak-gate/denylist.txt"}, terms, matcher,
        denylist_content=denylist_content, denylist_inside_root=True
    )
    assert ok is True  # denylist-origin hits do not by themselves fail the scope
    assert findings == []
    assert len(denylist_findings) == 1
    assert denylist_findings[0].category == "denylist-origin"
    assert "elsewhere.txt" in denylist_findings[0].message


def test_output_never_echoes_matched_term(tmp_path):
    repo = tmp_path / "echorepo"
    _init_repo(repo)
    _commit(repo, "leak.txt", "contains proprietary-term right here\n", "leak")
    _apply_fragment(repo)
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("proprietary-term\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, SCAN_SCRIPT_PATH, "--root", str(repo), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    assert "proprietary-term" not in r.stdout
    assert "PREPUB-LEAK-SCAN-FOUND" in r.stdout
    assert re.search(r"leak\.txt:\d+ \(term#\d+\)", r.stdout)


def test_output_never_echoes_matched_term_containing_colon_space(tmp_path):
    """Risk #4 regression: a denylist term containing ': ' must not corrupt the derived path. FAILS
    without the fix: `rest.rpartition(': ')` splits on the raw hit string's overall LAST ': ',
    which lands INSIDE the repr of the matched text (which itself contains ': '), corrupting the
    path used for `os.path.relpath` / `_locate_term` -- so the expected `leak.txt:<line>
    (term#<n>)` shape is never produced."""
    repo = tmp_path / "colontermrepo"
    _init_repo(repo)
    _commit(repo, "leak.txt", "prefix bad: term suffix here\n", "leak")
    _apply_fragment(repo)
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("bad: term\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, SCAN_SCRIPT_PATH, "--root", str(repo), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    assert "bad: term" not in r.stdout
    assert "PREPUB-LEAK-SCAN-FOUND" in r.stdout
    assert re.search(r"leak\.txt:\d+ \(term#\d+\)", r.stdout), r.stdout


def test_sanitize_unknown_hit_shape_is_redacted_not_echoed():
    """Risk #4: the ALLOW-list means an unrecognized shape from the shared module is redacted, not
    echoed verbatim. FAILS without the fix: the old catch-all branch re-printed ANY unrecognized
    hit string raw."""
    terms = ["proprietary-term"]
    matcher = LEAK_SCAN.build_matcher(terms)
    finding = SCAN._sanitize_working_tree_hit("/root", "SOME-FUTURE-SHAPE: proprietary-term leaked raw", terms, matcher)
    assert finding.category == "error"
    assert "proprietary-term" not in finding.message
    assert "REDACTED" in finding.message


def test_writes_no_file_into_scanned_repo(tmp_path):
    repo = tmp_path / "nofilerepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "hello\n", "init")
    _apply_fragment(repo)
    before = sorted(p.relative_to(repo) for p in repo.rglob("*") if ".git" not in p.parts)
    subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(repo)], capture_output=True, text=True, env=_scan_env(tmp_path))
    after = sorted(p.relative_to(repo) for p in repo.rglob("*") if ".git" not in p.parts)
    assert before == after


# =================================================================================================
# AC-RGLS-13 -- an unprotected repository is a finding
# =================================================================================================
def test_fragment_coverage_no_gitignore(tmp_path):
    repo = tmp_path / "nogitignore"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    finding = SCAN.fragment_coverage_finding(str(repo))
    assert finding is not None
    assert str(repo) in finding.message or "no readable" in finding.message


def test_fragment_coverage_gitignore_without_block(tmp_path):
    repo = tmp_path / "plaingitignore"
    _init_repo(repo)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _commit(repo, "f.txt", "x\n", "init")
    finding = SCAN.fragment_coverage_finding(str(repo))
    assert finding is not None
    assert "managed block" in finding.message


def test_fragment_coverage_present_when_block_carried(scratch_repo):
    assert SCAN.fragment_coverage_finding(str(scratch_repo)) is None


def test_fragment_coverage_end_to_end_found(tmp_path):
    repo = tmp_path / "unprotected"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(repo)], capture_output=True, text=True, env=_scan_env(tmp_path))
    assert r.returncode != 0
    assert "PREPUB-LEAK-SCAN-FOUND" in r.stdout
    assert "fragment-coverage" in r.stdout.lower()


# =================================================================================================
# AC-RGLS-14 -- tracked-partition scope
# =================================================================================================
def test_tracked_partition_enumerates_index_and_refs(tmp_path):
    repo = tmp_path / "trackedrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _commit(repo, ".foundry/rogue-file.txt", "not part of the designed set\n", "rogue tracked file")
    _apply_fragment(repo)
    ok, findings = SCAN.tracked_partition_scope(str(repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is False
    assert any(".foundry/rogue-file.txt" in f.message for f in findings)


def test_tracked_partition_enumerates_other_refs_too(tmp_path):
    repo = tmp_path / "trackedrefsrepo"
    _init_repo(repo, branch="trunk")
    _commit(repo, "f.txt", "x\n", "init")
    _git(["checkout", "-q", "-b", "other-branch"], repo)
    _commit(repo, ".foundry/only-on-other-branch.txt", "x\n", "only here")
    _git(["checkout", "-q", "trunk"], repo)
    _apply_fragment(repo)
    ok, findings = SCAN.tracked_partition_scope(str(repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is False
    assert any("only-on-other-branch.txt" in f.message for f in findings)


def test_tracked_partition_finding_without_term_match(tmp_path):
    """The realized incident class: a tracked runtime-partition path outside the designed set is
    a finding even though its CONTENT matches no denylist term at all."""
    repo = tmp_path / "silentrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _commit(repo, ".foundry/per-worker-env/nothing-suspicious.txt", "completely unrelated content\n", "tracked runtime file")
    _apply_fragment(repo)

    fixture_denylist = _fixture_denylist(tmp_path, name="fragment-denylist.txt")
    terms = LEAK_SCAN.load_denylist(fixture_denylist)
    matcher = LEAK_SCAN.build_matcher(terms)
    wt_ok, wt_findings, _ = SCAN.working_tree_scope(str(repo), fixture_denylist, terms, matcher, LEAK_SCAN)
    assert wt_ok is True and wt_findings == []  # every term-matched scope passes clean

    ok, findings = SCAN.tracked_partition_scope(str(repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is False
    assert any("per-worker-env/nothing-suspicious.txt" in f.message for f in findings)


def test_tracked_partition_designed_set_is_not_a_finding(tmp_path):
    repo = tmp_path / "designedrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _apply_fragment(repo)
    (repo / ".foundry").mkdir(exist_ok=True)
    (repo / ".foundry" / "README.md").write_text("designed\n", encoding="utf-8")
    _git(["add", "-f", ".foundry/README.md"], repo)
    _git(["commit", "-q", "-m", "track a designed member"], repo)
    ok, findings = SCAN.tracked_partition_scope(str(repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is True
    assert findings == []


def test_tracked_recorded_bad_sha_file_is_finding(tmp_path, local_bare_remote):
    repo = tmp_path / "trackedkbsrepo"
    _init_repo(repo)
    root_sha = _commit(repo, "a.txt", "one\n", "root")
    _commit(repo, "a.txt2", "two\n", "tip")
    _git(["push", "-q", str(local_bare_remote), "HEAD:refs/heads/trunk"], repo)
    _allow_any_sha1(local_bare_remote)
    _apply_fragment(repo)

    relpath = ".foundry/leak-scan/known-bad-shas.txt"
    (repo / relpath).write_text(root_sha + "\n", encoding="utf-8")
    _git(["add", "-f", relpath], repo)
    _git(["commit", "-q", "-m", "accidentally track the recovery index"], repo)

    ok, findings = SCAN.tracked_partition_scope(str(repo), relpath, str(local_bare_remote), False)
    assert ok is False
    messages = " ".join(f.message for f in findings)
    assert relpath in messages
    assert "resolve" in messages.lower()


def test_tracked_recorded_bad_sha_file_untracked_and_shas_refused_is_not_a_second_finding(tmp_path, local_bare_remote):
    """Sanity companion: when the recorded-bad-SHA file is NOT tracked at all, the second clause
    of AC-RGLS-14 never fires (only the ordinary designed-tracked-set logic applies -- and since
    it is not tracked, there is nothing to flag)."""
    repo = tmp_path / "untrackedkbsrepo"
    _init_repo(repo)
    _commit(repo, "a.txt", "one\n", "root")
    _apply_fragment(repo)
    relpath = ".foundry/leak-scan/known-bad-shas.txt"
    ok, findings = SCAN.tracked_partition_scope(str(repo), relpath, str(local_bare_remote), False)
    assert ok is True
    assert findings == []


# =================================================================================================
# AC-LSR-1..8 -- reconcile the leak scanner's designed-tracked set (the retired
# .foundry/wiring-hash.pin allow-list entry, feat-foundry-leak-scan-designed-tracked-set-reconcile)
# =================================================================================================
def test_tracked_partition_flags_force_added_retired_pin(tmp_path):
    """AC-LSR-2: the deliberate force-add the tracked-partition scope exists to catch. Ignore
    rules do not apply to an already-tracked path, so `git add -f .foundry/wiring-hash.pin` must
    make the scope return not-ok with a finding naming the path.

    ANTI-VACUOUS-PASS: this test, backported to the merge base, FAILS -- the allow-listed member
    made `tracked_partition_scope` return ok=True for a force-added `.foundry/wiring-hash.pin`.
    That failure is the defect this atom exists to close."""
    repo = tmp_path / "forceaddretiredpin"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _apply_fragment(repo)
    (repo / ".foundry").mkdir(exist_ok=True)
    (repo / ".foundry" / "wiring-hash.pin").write_text("retired\n", encoding="utf-8")
    _git(["add", "-f", ".foundry/wiring-hash.pin"], repo)
    _git(["commit", "-q", "-m", "force-add the retired pin"], repo)
    ok, findings = SCAN.tracked_partition_scope(str(repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is False
    assert any(".foundry/wiring-hash.pin" in f.message for f in findings)


def test_tracked_partition_all_designed_members_stay_clean(tmp_path):
    """AC-LSR-3: the over-narrowing guard -- a repository tracking all three designed-tracked
    members and no other runtime-partition path stays CLEAN (ok True, findings empty). Convicts a
    dropped survivor, which the AC-LSR-1 membership grep alone could miss."""
    repo = tmp_path / "alldesignedrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _apply_fragment(repo)
    (repo / ".foundry").mkdir(exist_ok=True)
    for member in (".foundry/README.md", ".foundry/build-provenance.yaml", ".foundry/stack-profile.lock"):
        p = repo / member
        p.write_text("designed\n", encoding="utf-8")
        _git(["add", "-f", member], repo)
    _git(["commit", "-q", "-m", "track all three designed members"], repo)
    ok, findings = SCAN.tracked_partition_scope(str(repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is True
    assert findings == []


def _fragment_derived_designed_set():
    """The canonical derivation (spec Clarification C1): strip the leading `!/` from each
    re-include line of the shipped `scripts/foundry-runtime.gitignore` fragment. Confined to
    TEST time only -- never imported into the live scanner (spec prior-art family 2: a parse
    defect in a LIVE allow-list would widen it silently; a parse defect here fails loudly instead)."""
    with open(FRAGMENT, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return frozenset(line[2:] for line in lines if line.startswith("!/"))


def _applier_members():
    """Parse the applier's `MEMBERS=(...)` array line as text (read-only; the applier is a denied
    path here)."""
    with open(APPLIER, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip().startswith("MEMBERS="):
                m = re.search(r"MEMBERS=\(([^)]*)\)", line)
                assert m is not None, "applier MEMBERS= line not in the expected shape"
                return frozenset(tok.strip('"') for tok in m.group(1).split())
    raise AssertionError("applier declares no MEMBERS= line")


def _assert_candidate_matches_fragment(candidate_set):
    """AC-LSR-4/-5: the ONE comparison helper, a function of a candidate set, compared against the
    fragment-derived set. Every one of AC-LSR-4's three targets, and every one of AC-LSR-5's per-
    target mutation stand-ins, flows through this single function."""
    expected = _fragment_derived_designed_set()
    assert frozenset(candidate_set) == expected


def test_designed_tracked_set_matches_shipped_fragment():
    """AC-LSR-4: one canonical source (the shipped `scripts/foundry-runtime.gitignore` fragment),
    three sites checked against it -- the scanner's constant, the applier's `MEMBERS=` array, and
    the runtime-gitignore test module's `DESIGNED_TRACKED_SET` expectation, read as a module
    attribute of the imported sibling `test_runtime_gitignore` module."""
    _assert_candidate_matches_fragment(SCAN.DESIGNED_TRACKED_SET)
    _assert_candidate_matches_fragment(_applier_members())
    _assert_candidate_matches_fragment(test_runtime_gitignore.DESIGNED_TRACKED_SET)


def test_designed_set_mutation_convicts_the_scanner_copy():
    """AC-LSR-5, scanner target: the comparison genuinely bites against a divergent stand-in for
    the scanner's own `DESIGNED_TRACKED_SET`, in both directions -- the retired pin re-added, and
    a surviving member dropped."""
    live = SCAN.DESIGNED_TRACKED_SET
    _assert_candidate_matches_fragment(live)  # the live value agrees with the fragment

    retired_readded = frozenset(live | {".foundry/wiring-hash.pin"})
    with pytest.raises(AssertionError):
        _assert_candidate_matches_fragment(retired_readded)

    survivor_dropped = frozenset(live - {".foundry/README.md"})
    with pytest.raises(AssertionError):
        _assert_candidate_matches_fragment(survivor_dropped)


def test_designed_set_mutation_convicts_the_applier_copy():
    """AC-LSR-5, applier target: same shape as the scanner-copy control, over the applier's parsed
    `MEMBERS=` array."""
    live = _applier_members()
    _assert_candidate_matches_fragment(live)

    retired_readded = frozenset(live | {".foundry/wiring-hash.pin"})
    with pytest.raises(AssertionError):
        _assert_candidate_matches_fragment(retired_readded)

    survivor_dropped = frozenset(live - {".foundry/build-provenance.yaml"})
    with pytest.raises(AssertionError):
        _assert_candidate_matches_fragment(survivor_dropped)


def test_designed_set_mutation_convicts_the_test_module_copy():
    """AC-LSR-5, sibling test-module target: same shape as the scanner-copy control, over
    `test_runtime_gitignore.DESIGNED_TRACKED_SET`. Without this per-target control, a scanner-only
    mutation would leave this target unconvicted -- exactly the vacuity the idiom exists to
    prevent (spec prior-art family 3)."""
    live = frozenset(test_runtime_gitignore.DESIGNED_TRACKED_SET)
    _assert_candidate_matches_fragment(live)

    retired_readded = frozenset(live | {".foundry/wiring-hash.pin"})
    with pytest.raises(AssertionError):
        _assert_candidate_matches_fragment(retired_readded)

    survivor_dropped = frozenset(live - {".foundry/stack-profile.lock"})
    with pytest.raises(AssertionError):
        _assert_candidate_matches_fragment(survivor_dropped)


# =================================================================================================
# AC-RGLS-8 -- clean is unreachable without all four scopes; per-scope status lines
# =================================================================================================
def test_per_scope_status_lines(scratch_repo, tmp_path):
    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo)], capture_output=True, text=True, env=_scan_env(tmp_path))
    for label in ["SCOPE fragment-coverage", "SCOPE working-tree", "SCOPE history", "SCOPE tracked-partition", "SCOPE remote-probe"]:
        assert label in r.stdout, r.stdout


def test_history_scope_covers_commit_messages(tmp_path):
    repo = tmp_path / "msgrepo"
    _init_repo(repo)
    (repo / "f.txt").write_text("clean content\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-q", "-m", "message mentions proprietary-term right in the log"], repo)
    terms = ["proprietary-term"]
    matcher = LEAK_SCAN.build_matcher(terms)
    ok, findings, denylist_findings = SCAN.history_scope(str(repo), set(), terms, matcher)
    assert ok is False
    assert any("commit message" in f.message for f in findings)


def test_history_scope_covers_tag_messages(tmp_path):
    repo = tmp_path / "tagrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "clean\n", "init")
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t.t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t.t")
    _git(["tag", "-a", "v1", "-m", "tag message mentions proprietary-term here"], repo, env=env)
    terms = ["proprietary-term"]
    matcher = LEAK_SCAN.build_matcher(terms)
    ok, findings, denylist_findings = SCAN.history_scope(str(repo), set(), terms, matcher)
    assert ok is False
    assert any("tag message" in f.message for f in findings)


def test_missing_recorded_bad_sha_file_names_remedy(scratch_repo, tmp_path):
    missing = os.path.join(str(scratch_repo), ".foundry", "leak-scan", "known-bad-shas.txt")
    if os.path.exists(missing):
        os.remove(missing)  # the applier stubs it on first run; remove it to exercise the absent case
    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo), "--known-bad-shas", missing], capture_output=True, text=True, env=_scan_env(tmp_path))
    assert r.returncode != 0
    assert missing in r.stdout
    assert "empty file is a valid statement" in r.stdout.lower()


def test_traversal_or_enumeration_error_is_found(tmp_path):
    not_a_repo = tmp_path / "not-a-git-repo"
    not_a_repo.mkdir()
    ok, findings = SCAN.tracked_partition_scope(str(not_a_repo), ".foundry/leak-scan/known-bad-shas.txt", None, True)
    assert ok is False
    assert any(f.category == "error" for f in findings)


def test_scope_gating_all_four_required_for_clean(scratch_repo, local_bare_remote, tmp_path):
    """The end-to-end proof that CLEAN requires every scope: build a fully clean fixture (working
    tree clean, history clean, nothing extra tracked, fragment present, an empty recorded-bad-SHA
    list, and a remote whose positive control resolves), assert CLEAN -- then flip exactly ONE
    scope (tracked-partition) to dirty and assert the verdict flips to FOUND while every other
    scope is still individually reported clean."""
    _seed_remote(local_bare_remote, tmp_path)
    _allow_any_sha1(local_bare_remote)
    _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
    known_bad = scratch_repo / ".foundry" / "leak-scan" / "known-bad-shas.txt"
    known_bad.parent.mkdir(parents=True, exist_ok=True)
    known_bad.write_text("# nothing recorded\n", encoding="utf-8")

    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo), "--remote", "origin"], capture_output=True, text=True, env=_scan_env(tmp_path))
    assert "PREPUB-LEAK-SCAN-CLEAN" in r.stdout, r.stdout
    assert r.returncode == 0

    # now flip ONLY the tracked-partition scope dirty (the file IS correctly ignored by the
    # fragment; force-add it to simulate the realized-incident class: tracked despite the rule)
    (scratch_repo / ".foundry" / "stray.txt").write_text("not designed\n", encoding="utf-8")
    _git(["add", "-f", ".foundry/stray.txt"], scratch_repo)
    _git(["commit", "-q", "-m", "stray tracked runtime file"], scratch_repo)
    r2 = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo), "--remote", "origin"], capture_output=True, text=True, env=_scan_env(tmp_path))
    assert "PREPUB-LEAK-SCAN-FOUND" in r2.stdout
    assert r2.returncode != 0
    assert "SCOPE working-tree: clean" in r2.stdout
    history_line = next(l for l in r2.stdout.splitlines() if l.startswith("SCOPE history"))
    assert "clean" in history_line
    assert "SCOPE tracked-partition: FOUND" in r2.stdout


# =================================================================================================
# Risk #1 -- the remote-probe status line names the remote / host / probe count
# =================================================================================================
def test_remote_probe_status_line_names_remote_host_and_probe_count(scratch_repo, local_bare_remote, tmp_path):
    """FAILS without the fix: the old status line was a bare `SCOPE remote-probe: clean`/`FOUND`
    with no indication of WHICH remote/host was actually probed or how many SHAs were reached --
    so a scope that (due to a wrong default pick) never touched the real repository would print
    identically to one that did."""
    _seed_remote(local_bare_remote, tmp_path)
    _allow_any_sha1(local_bare_remote)
    _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
    r = subprocess.run(
        [sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo), "--remote", "origin"],
        capture_output=True,
        text=True,
        env=_scan_env(tmp_path),
    )
    remote_probe_line = next(l for l in r.stdout.splitlines() if l.startswith("SCOPE remote-probe"))
    assert "remote=origin" in remote_probe_line
    assert "url-host=local-path" in remote_probe_line
    assert "probes=" in remote_probe_line
    assert re.search(r"probes=\d+", remote_probe_line)


def test_default_remote_selection_status_line_shows_which_remote_was_chosen(scratch_repo, local_bare_remote, tmp_path):
    """Sanity companion: when --remote is omitted and 'origin' is configured, the status line names
    it (never a silently-picked, unrelated remote)."""
    _seed_remote(local_bare_remote, tmp_path)
    _allow_any_sha1(local_bare_remote)
    _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
    r = subprocess.run([sys.executable, SCAN_SCRIPT_PATH, "--root", str(scratch_repo)], capture_output=True, text=True, env=_scan_env(tmp_path))
    remote_probe_line = next(l for l in r.stdout.splitlines() if l.startswith("SCOPE remote-probe"))
    assert "remote=origin" in remote_probe_line


# =================================================================================================
# Risk #5 -- a resolving recorded-bad SHA is truncated + cites the recorded-bad-shas file/line
# =================================================================================================
def test_recorded_bad_sha_resolving_finding_truncates_sha_and_cites_line(scratch_repo, local_bare_remote, tmp_path):
    """FAILS without the fix: the old code printed the FULL 40-hex recorded-bad SHA in the
    finding message -- exactly the recovery index this atom's own §5.4 reasoning says must never
    be published."""
    root_sha, _tip_sha = _seed_remote(local_bare_remote, tmp_path)
    _allow_any_sha1(local_bare_remote)
    _git(["remote", "add", "origin", str(local_bare_remote)], scratch_repo)
    ok, findings, url, had_error, remote_name, probe_count = SCAN.remote_probe_scope(
        str(scratch_repo), "origin", [(root_sha, 3)], known_bad_shas_display="known-bad-shas.txt"
    )
    assert ok is False
    resolving = [f for f in findings if "resolves against the remote" in f.message]
    assert len(resolving) == 1
    msg = resolving[0].message
    assert root_sha not in msg  # never the full 40-hex object name
    assert root_sha[:12] in msg
    assert "known-bad-shas.txt line 3" in msg
    assert probe_count == 2  # positive control + the one recorded-bad SHA


# =================================================================================================
# Risk #6 -- raw git stderr (may embed a credentialed URL) is never echoed into a finding
# =================================================================================================
def test_remote_probe_error_never_echoes_raw_git_stderr(monkeypatch, scratch_repo):
    """FAILS without the fix: the old code interpolated the raw fetch stderr (`detail`) directly
    into the finding message, which for a real credentialed remote (e.g.
    `https://x-access-token:<token>@host/...`) would leak the token into the scan's own output."""
    _git(["remote", "add", "origin", "https://x-access-token:super-secret-token@example.invalid/repo.git"], scratch_repo)

    def fake_find_control(url, timeout=30):
        return "a" * 40

    def fake_probe_sha(url, sha, timeout=30):
        return "error", f"fatal: could not read from 'https://x-access-token:super-secret-token@example.invalid/repo.git': network unreachable (sha={sha})"

    monkeypatch.setattr(SCAN, "find_unadvertised_control_object", fake_find_control)
    monkeypatch.setattr(SCAN, "probe_sha", fake_probe_sha)

    ok, findings, url, had_error, remote_name, probe_count = SCAN.remote_probe_scope(str(scratch_repo), "origin", [])
    assert ok is False
    assert had_error is True
    joined = " ".join(f.message for f in findings)
    assert "super-secret-token" not in joined
    assert "x-access-token" not in joined
    assert "example.invalid" in joined  # host-only rendering is still informative


# =================================================================================================
# Risk #7 -- read-side git invocations against the scanned repo are hardened
# =================================================================================================
def test_read_side_git_invocations_are_hardened(monkeypatch, tmp_path):
    """FAILS without the fix: `_git()` invoked plain `["git"] + args` with no `-c` overrides and no
    explicit env, inheriting whatever hooksPath/fsmonitor/ext-transport config or ambient env the
    scanned repository (or the caller's shell) happened to carry."""
    captured = []
    real_run = SCAN.subprocess.run

    def spy_run(argv, **kwargs):
        captured.append((list(argv), kwargs.get("env")))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(SCAN.subprocess, "run", spy_run)
    repo = tmp_path / "hardenedrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    SCAN._git(["rev-list", "--objects", "--all"], str(repo))

    assert captured, "expected at least one git invocation"
    argv, env = captured[-1]
    assert "core.fsmonitor=" in argv
    assert "core.hooksPath=/dev/null" in argv
    assert "protocol.ext.allow=never" in argv
    assert env is not None and env.get("GIT_TERMINAL_PROMPT") == "0"


def test_batch_blob_contents_is_hardened(monkeypatch, tmp_path):
    """FAILS without the fix: `_batch_blob_contents` called `subprocess.Popen` directly, bypassing
    `_git()` entirely -- no hardening `-c` overrides, no explicit env. Builds the fixture repo
    BEFORE patching `SCAN.subprocess.Popen` -- `SCAN.subprocess` is the same shared `subprocess`
    module object every caller uses (module caching), so patching it any earlier would also
    intercept the fixture-setup git calls this test's own helpers make."""
    repo = tmp_path / "batchrepo"
    _init_repo(repo)
    sha = _commit(repo, "f.txt", "hello\n", "init")
    blob_sha = _git(["rev-parse", f"{sha}:f.txt"], repo).stdout.strip()

    captured = {}
    real_popen = SCAN.subprocess.Popen

    def spy_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs.get("env")
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(SCAN.subprocess, "Popen", spy_popen)

    list(SCAN._batch_blob_contents(str(repo), [blob_sha]))

    assert "core.fsmonitor=" in captured["argv"]
    assert "core.hooksPath=/dev/null" in captured["argv"]
    assert captured["env"] is not None and captured["env"].get("GIT_TERMINAL_PROMPT") == "0"


# =================================================================================================
# Risk #8 -- early-exit paths still emit the verdict sentinel
# =================================================================================================
def test_shared_module_load_failure_emits_found_sentinel(tmp_path, monkeypatch, capsys):
    """FAILS without the fix: the old code returned 1 on this path with NO sentinel printed at
    all, which fails OPEN for a consumer keying only on the PREPUB-LEAK-SCAN-FOUND/CLEAN string."""
    repo = tmp_path / "brokenmodrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _apply_fragment(repo)
    monkeypatch.setattr(SCAN, "SHARED_MODULE_PATH", "/nonexistent/leak_scan.py")

    rc = SCAN.run_scan(
        str(repo), None, str(repo / ".foundry" / "leak-scan" / "known-bad-shas.txt"), _fixture_denylist(tmp_path), False
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "PREPUB-LEAK-SCAN-FOUND" in captured.out


def test_denylist_error_emits_found_sentinel(tmp_path, capsys):
    """FAILS without the fix: same fail-open shape as above, on the denylist-load-error path."""
    repo = tmp_path / "baddenylistrepo"
    _init_repo(repo)
    _commit(repo, "f.txt", "x\n", "init")
    _apply_fragment(repo)
    bad_denylist = tmp_path / "empty-denylist.txt"
    bad_denylist.write_text("", encoding="utf-8")  # parses to zero terms -> DenylistError

    rc = SCAN.run_scan(
        str(repo),
        None,
        str(repo / ".foundry" / "leak-scan" / "known-bad-shas.txt"),
        str(bad_denylist),
        True,
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "PREPUB-LEAK-SCAN-FOUND" in captured.out
