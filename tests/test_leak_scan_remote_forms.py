"""tests/test_leak_scan_remote_forms.py — behavioral coverage for
feat-foundry-leak-scan-scp-remote-form (AC-SCP-1..4/6).

This atom widens the leak scan's remote allow-list by one documented git form (the scp-like
`[user@]host:path` syntax) and generalises the two-name `ext::`/`fd::` transport-helper exclusion
into a rule -- nothing else about the authorizing atom's hardening changes. AC-SCP-5 (that the
authorizing atom's `tests/test_prepublication_leak_scan.py` still passes in full) is asserted by
running THAT file directly; it is not duplicated here, and this module never edits it.

GENERICITY DEVICE (AC-SCP-1 / AC-SCP-2): every value the shipped implementation must accept or
reject "by rule, not by enumeration" is built at test-collection/run time from a fresh random
label (`uuid.uuid4()`), never as a fixed string literal baked into this file. A hardcoded
accept-list of the spec's own three scp-like examples, or a hardcoded reject-list of two/four
transport-helper names, would satisfy neither generation device and is deliberately not what this
file does.

No test here needs real network: the two "unreachable" AC-SCP-6 checkpoints either drive
`remote_probe_scope` directly against a configured remote pointing at `*.invalid` (an RFC 2606
reserved TLD that never resolves -- the exact convention `test_prepublication_leak_scan.py`
already relies on for its own `test_unreachable_remote_is_found`) or exercise the CLI over the
same fixture; nothing here waits on or requires a live remote to answer.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

from conftest import REPO_ROOT, load_module

SCAN = load_module("scripts/foundry-prepublication-leak-scan.py", "foundry_rgls_scan_scp")

SCAN_SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-prepublication-leak-scan.py")

# The denylist left this tree in feat-foundry-denylist-out-of-tree, so the scanner's default term set
# now resolves from $CLAUDE_PROJECT_DIR -- which conftest's autouse `_clean_env` correctly scrubs so
# an operator's ambient workspace cannot leak into a hermetic test. A subprocess run therefore needs
# its own throwaway workspace, or the scanner fail-closes with no denylist and the assertions below
# read an empty stdout. These tests are about REMOTE-FORM handling; the term set is incidental.
_FIXTURE_TERMS = ["alpha-fixture", "bravo-fixture"]


def _scan_env(tmp_path):
    ws = tmp_path / "_scan_workspace"
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "foundry-leak-denylist.txt").write_text(
        "\n".join(_FIXTURE_TERMS) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(ws)
    return env


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


@pytest.fixture()
def repo(tmp_path):
    """A minimal scratch repo, no remote, no fragment applied -- these tests never depend on
    fragment-coverage; they exercise remote-value resolution and rendering only."""
    r = tmp_path / "repo"
    _init_repo(r)
    _commit(r, "README.txt", "hello world\n", "init")
    return r


def _fresh_label(prefix):
    """A label absent from this file's own source: derived at run time from `uuid.uuid4()`, never
    a string literal written anywhere in this module."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _generate_scp_like_values(n=6):
    """`n` scp-like `[user@]host:path` values, none of them a fixed literal: every host label,
    user label and path segment is derived from a fresh `uuid4` at call time. Alternates the
    `user@host:path` and bare `host:path` forms."""
    values = []
    for i in range(n):
        host = _fresh_label("gen-host-") + (".example" if i % 3 == 0 else "")
        path = f"{_fresh_label('owner-')}/{_fresh_label('repo-')}.git"
        if i % 2 == 0:
            user = _fresh_label("user-")
            values.append(f"{user}@{host}:{path}")
        else:
            values.append(f"{host}:{path}")
    return values


def _naive_colon_before_slash_shape(value):
    """A deliberately NAIVE reimplementation of "no '/' before the first ':'" -- used only to
    PROVE, independently of the shipped implementation, that a given value satisfies the scp-like
    shape in isolation (the ordering trap AC-SCP-2/AC-SCP-3 exist to guard against). This is not
    the implementation under test."""
    if ":" not in value:
        return False
    colon_idx = value.index(":")
    slash_idx = value.find("/")
    return slash_idx == -1 or colon_idx < slash_idx


# =================================================================================================
# AC-SCP-1 -- the scp-like form is accepted, by RULE (generated inputs), both entry paths
# =================================================================================================
def test_accepts_generated_scp_like_forms():
    generated = _generate_scp_like_values()
    assert len(generated) >= 4
    for value in generated:
        assert SCAN.url_is_allowed_form(value) is True, value
        # both directly-passed-URL and configured-remote entry paths share the same predicate
        url, err, _name = SCAN.resolve_remote(REPO_ROOT, value)
        assert err is None, err
        assert url == value


def test_accepts_scp_like_configured_remote(repo):
    """The SAME allow-list, exercised via the configured-remote entry path (`resolve_remote` /
    `_resolve_configured_remote_url`), with a freshly-generated scp-like value -- not the
    directly-passed-URL branch AC-SCP-1's other checkpoint covers."""
    for value in _generate_scp_like_values(3):
        remote_name = _fresh_label("remote-")
        _git(["remote", "add", remote_name, value], repo)
        url, err, name = SCAN.resolve_remote(str(repo), remote_name)
        assert err is None, err
        assert url == value
        assert name == remote_name
        _git(["remote", "remove", remote_name], repo)


def test_rejects_non_ascii_homoglyph_host():
    """AC-SCP-1: the authority-segment pattern is ASCII-anchored (`re.ASCII`, explicit A-Za-z0-9
    class) -- a Unicode-aware class (Python `\\w` without `re.ASCII`) would admit a homoglyph host
    that renders indistinguishably in the status line. Build a host that is byte-for-byte distinct
    from its ASCII look-alike by substituting a Cyrillic 'а' (U+0430) for the Latin 'a', with a
    fresh random suffix so the literal string itself is not what carries the assertion."""
    suffix = _fresh_label("")
    ascii_host = f"gith{suffix}b.example"
    homoglyph_host = ascii_host.replace("a", "а", 1) if "a" in ascii_host else f"gаth{suffix}.example"
    assert homoglyph_host != ascii_host
    value = f"{homoglyph_host}:owner/{suffix}.git"
    assert SCAN.url_is_allowed_form(value) is False


# =================================================================================================
# AC-SCP-2 -- transport-helper syntax excluded generically, FIRST, unsatisfiable by enumeration
# =================================================================================================
def test_rejects_generated_transport_helper():
    """A helper name generated at test time and absent from the implementation's source -- no
    enumeration of helper-name string literals (a four-name tuple included) can pre-satisfy this,
    because the shipped predicate contains no name list at all."""
    for _ in range(3):
        helper = _fresh_label("helper-")
        assert helper not in ("ext", "fd")  # sanity: genuinely not one of the two named forms
        value_no_slash = f"{helper}::do-something-arbitrary"
        value_with_slash_after = f"{helper}::do/something/arbitrary"
        assert SCAN.url_is_allowed_form(value_no_slash) is False
        assert SCAN.url_is_allowed_form(value_with_slash_after) is False
        # and via the configured-remote path too
        assert SCAN._is_transport_helper_syntax(value_no_slash) is True
        assert SCAN._is_transport_helper_syntax(value_with_slash_after) is True


def test_rejects_generated_transport_helper_via_configured_remote(repo):
    helper = _fresh_label("helper-")
    remote_name = _fresh_label("remote-")
    _git(["remote", "add", remote_name, f"{helper}::sh -c false"], repo)
    url, err, _name = SCAN.resolve_remote(str(repo), remote_name)
    assert url is None
    assert err is not None


def test_rejects_ext_despite_scp_like_shape():
    """The first ordering trap, made a checkpoint: `ext::sh -c whoami` has no '/' before its
    first ':', so it satisfies the scp-like shape in isolation -- verified independently via the
    naive shape check below. The shipped predicate must still reject it because the
    transport-helper exclusion runs FIRST."""
    value = "ext::sh -c whoami"
    assert _naive_colon_before_slash_shape(value) is True  # satisfies the scp-like shape alone
    assert SCAN._is_transport_helper_syntax(value) is True
    assert SCAN.url_is_allowed_form(value) is False
    # fd:: -- the sibling named-in-the-authorizing-spec helper -- stays rejected too
    assert SCAN.url_is_allowed_form("fd::7") is False


# =================================================================================================
# AC-SCP-3 -- scheme dispatch precedes the scp-like rule; prior rejections stay rejected
# =================================================================================================
def test_scheme_dispatch_precedes_scp_rule():
    """The second ordering trap: every `<scheme>://` URL also satisfies the scp-like shape in
    isolation (`file:///tmp/x` has no '/' before its ':', "host part" `file`) -- proven
    independently below. The shipped predicate must decide these by the pre-existing scheme
    allow-list only, never by the scp-like rule."""
    assert _naive_colon_before_slash_shape("file:///tmp/x") is True  # satisfies the shape alone

    assert SCAN.url_is_allowed_form("file:///tmp/x") is False
    assert SCAN.url_is_allowed_form("http://example.invalid/repo.git") is False
    assert SCAN.url_is_allowed_form("git://example.invalid/repo.git") is False
    assert SCAN.url_is_allowed_form("https://example.invalid/repo.git") is True
    assert SCAN.url_is_allowed_form("ssh://git@example.invalid/repo.git") is True


def test_slash_before_colon_is_a_local_path():
    """Independently of scheme dispatch: a bare relative/absolute path containing a colon AFTER a
    slash is a local path, not scp-like, and stays rejected by the allow-list (the narrow
    local-path escape hatch is a SEPARATE, configured-remote-only mechanism, exercised by the
    sibling suite -- not what `url_is_allowed_form` itself grants)."""
    for value in ("./rel:path", "sub/dir:colon", "/abs/path:with:colons"):
        assert SCAN._is_scp_like_shape(value) is False, value
        assert SCAN.url_is_allowed_form(value) is False, value


def test_dash_leading_scp_shaped_is_rejected():
    """A value that is otherwise fully scp-like (no transport-helper syntax, no scheme, no slash
    before its colon) but whose authority segment begins with '-' stays rejected -- the AC-SCP-1
    pattern's anchored first character enforces this, even though the value's colon/slash shape
    alone would qualify as scp-like."""
    value = "-evil.example:x"
    assert SCAN._is_scp_like_shape(value) is True  # shape-only: it IS scp-like in shape
    assert SCAN.url_is_allowed_form(value) is False  # but the authority pattern rejects it


# =================================================================================================
# AC-SCP-4 -- rendering: alias labelled, scheme-URL host rendering NOT regressed
# =================================================================================================
def test_renders_scp_like_host_as_unresolved_alias():
    suffix = _fresh_label("")
    host = f"personal-alias-{suffix}"
    path = f"owner-{suffix}/repo-{suffix}.git"

    rendered = SCAN.redact_url_to_host(f"{host}:{path}")
    assert rendered != "?"
    assert rendered != "unrecognized-url-form"
    assert host in rendered
    assert path not in rendered  # never carrying the path
    assert rendered != host  # rendered DISTINGUISHABLY as an unresolved alias, not a bare host

    user = f"deploy-{suffix}"
    rendered_with_user = SCAN.redact_url_to_host(f"{user}@{host}:{path}")
    assert user not in rendered_with_user  # never carrying any user@ prefix
    assert host in rendered_with_user
    assert rendered_with_user != host


def test_scheme_url_host_rendering_unchanged():
    """FAILS if an scp-like branch is placed ahead of the scheme branches: `https://h/p` would
    then render as host `https`, destroying the host signal for forms already accepted before
    this atom."""
    assert SCAN.redact_url_to_host("https://h/p") == "h"
    assert SCAN.redact_url_to_host("https://user@h/p") == "h"
    assert SCAN.redact_url_to_host("ssh://git@example.invalid/repo.git") == "example.invalid"
    assert SCAN.redact_url_to_host("/abs/local/path") == "local-path"


# =================================================================================================
# AC-SCP-6 -- the newly-admitted class stays fail-closed
# =================================================================================================
def test_unreachable_scp_like_remote_is_found_not_clean(repo):
    remote_name = _fresh_label("remote-")
    # example.invalid is RFC 2606-reserved -- guaranteed never to resolve, the same convention
    # test_prepublication_leak_scan.py's own test_unreachable_remote_is_found already relies on.
    scp_value = f"deploy@example.invalid:{_fresh_label('owner-')}/repo.git"
    _git(["remote", "add", remote_name, scp_value], repo)

    # sanity: the value is genuinely admitted by the allow-list (this is the newly-widened class,
    # not a value that was already rejected before this atom).
    assert SCAN.url_is_allowed_form(scp_value) is True

    ok, findings, url, had_error, resolved_name, probe_count = SCAN.remote_probe_scope(
        str(repo), remote_name, []
    )
    assert url == scp_value
    assert resolved_name == remote_name
    assert ok is False  # never clean
    assert had_error is True
    assert probe_count == 0  # the probe never got as far as a single fetch
    assert any(f.category == "error" for f in findings)


def test_unreachable_scp_like_remote_is_not_skipped(repo, tmp_path):
    """End-to-end: the scope reports FOUND with probes=0 -- never silently omitted, never a
    disguised skip. An implementation that special-cased an unreachable scp-like remote into a
    skip would make THIS specific repository's scope report clean (or absent) while destroying the
    control; it must instead show up exactly like every other unreachable-remote FOUND."""
    remote_name = _fresh_label("remote-")
    scp_value = f"deploy@example.invalid:{_fresh_label('owner-')}/repo.git"
    _git(["remote", "add", remote_name, scp_value], repo)

    r = subprocess.run(
        [sys.executable, SCAN_SCRIPT_PATH, "--root", str(repo), "--remote", remote_name],
        capture_output=True,
        text=True,
        env=_scan_env(tmp_path),
    )
    assert "PREPUB-LEAK-SCAN-CLEAN" not in r.stdout
    assert "PREPUB-LEAK-SCAN-FOUND" in r.stdout
    remote_probe_line = next(l for l in r.stdout.splitlines() if l.startswith("SCOPE remote-probe"))
    assert "FOUND" in remote_probe_line
    assert "probes=0" in remote_probe_line
    assert f"remote={remote_name}" in remote_probe_line
