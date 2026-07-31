"""tests/test_bootstrap_commit_identity.py — hermetic behavioral coverage for the
jail-scoped commit-identity resolution in scripts/foundry-bootstrap.sh
(feat-foundry-bootstrap-identity-jail-scoped, AC-BIJ-1..12).

Every test drives the REAL shipped script (`resolve_declared_identity` / `seed_commit_identity`)
via subprocess, end-to-end, against a stubbed `gh` (and a no-op `claude`) placed first on PATH.
The `gh` stub records its own COMPLETE `GH_`- and `GITHUB_`-prefixed environment plus argv on
every invocation, so the assertions below observe exactly what the shipped script's child
environment looked like — never a re-implementation of the resolution logic in Python. Recording
both prefixes matters: the script's precedence strip removes five specific token/host variables
(`GH_TOKEN`/`GITHUB_TOKEN`/`GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN`/`GH_HOST`), and a stub
that only recorded `GH_`-prefixed vars would make the `GITHUB_TOKEN` / `GITHUB_ENTERPRISE_TOKEN`
assertions pass unconditionally (unobservable), never actually exercising the strip for those two.

HERMETICITY (AC-BIJ-7) is a safety property here, not hygiene, on two axes:
  * WRITES — the driven script runs `git config --global` and writes
    `~/.config/git/identity-<slug>`, so every test redirects both HOME and GIT_CONFIG_GLOBAL into
    a per-test tmp_path; the suite can never touch the operator's real global git config.
  * READS — this repository's own `.envrc` exports GH_CONFIG_DIR into ANY shell in the tree,
    including one running pytest, and CI's default environment exports `GITHUB_TOKEN`, so an
    unscrubbed harness would silently test the developer's ambient jail or inherit a real token.
    Every child environment is therefore built from `scrubbed_env()`, a copy of the test process's
    own environment with every `GH_`- and `GITHUB_`-prefixed variable removed, before a test adds
    back only the variables it sets deliberately. Test-helper subprocesses that are NOT the driven
    script (the `project` fixture's `git init`, `_read_identity`'s `git config --file`) also run
    under `scrubbed_env()` plus `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at `/dev/null`, so
    `git init`'s own `init.templateDir` honouring can't seed a repo-local `.git/config` (which
    would perturb the `"useConfigOnly" in local_cfg` string check) or read the operator's real
    global config.

RESIDUAL GAPS (documented, not fixed here — out of this atom's scope):
  * the `.login` cross-check in `resolve_declared_identity` pins the declared PRINCIPAL but not
    the HOST: a jail whose `hosts.yml` default resolves to an enterprise instance would resolve
    there, and an enterprise namesake with a matching login would pass the check.
  * proxy / CA-trust variables (`HTTPS_PROXY`, `https_proxy`, `ALL_PROXY`, CA-bundle vars) are not
    stripped from the probe's child environment and could redirect or observe the authenticated
    `gh api user` call; TLS bounds this hard but it is not this script's control.
"""
from __future__ import annotations

import json
import os
import pty
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "foundry-bootstrap.sh"

# The stubbed `gh`: records its own GH_-prefixed environment + argv (one JSON line per
# invocation) to $STUB_RECORD_FILE, then answers per $STUB_GH_EXIT / $STUB_GH_TSV — exactly the
# `gh api user --jq '... | @tsv'` shape the shipped script now depends on.
GH_STUB = """#!/usr/bin/env python3
import json, os, sys

record_path = os.environ.get("STUB_RECORD_FILE")
if record_path:
    env_gh = {k: v for k, v in os.environ.items() if k.startswith(("GH_", "GITHUB_"))}
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "env": env_gh}) + "\\n")

rc = int(os.environ.get("STUB_GH_EXIT", "0"))
if rc != 0:
    sys.exit(rc)

tsv = os.environ.get("STUB_GH_TSV", "")
sys.stdout.write(tsv)
if tsv and not tsv.endswith("\\n"):
    sys.stdout.write("\\n")
sys.exit(0)
"""

# The stubbed `claude`: a pure no-op so `install_plugin` (marketplace add / plugin install)
# succeeds without any network access.
CLAUDE_STUB = """#!/usr/bin/env python3
import sys
sys.exit(0)
"""

SENTINELS = {
    "GH_TOKEN": "sentinel-token-aaa",
    "GITHUB_TOKEN": "sentinel-token-bbb",
    "GH_ENTERPRISE_TOKEN": "sentinel-token-ccc",
    "GITHUB_ENTERPRISE_TOKEN": "sentinel-token-ddd",
    "GH_HOST": "sentinel-host.example.invalid",
}


def scrubbed_env():
    """A copy of the test process's own environment with every GH_- and GITHUB_-prefixed variable
    removed (AC-BIJ-7) — the single helper every child environment in this module is built from.
    GITHUB_ is scrubbed too because GITHUB_TOKEN is GitHub Actions' own default env var: an
    unscrubbed harness running in CI would inherit a real token into every test's parent
    environment, silently widening what the (also GITHUB_-recording, see GH_STUB) probe-env
    assertions could observe."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def stub_bin(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "gh", GH_STUB)
    _make_executable(bindir / "claude", CLAUDE_STUB)
    return bindir


def _helper_env():
    """The env every non-driven-script test helper subprocess (git init, git config --file) runs
    under: scrubbed (AC-BIJ-7) AND with GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM pointed at /dev/null,
    so `git init`'s own honouring of the operator's real `init.templateDir` can't seed a
    repo-local `.git/config` (which would perturb the `_artifacts_absent` "useConfigOnly" in
    local_cfg string check) and `git config --file` can't fall back to reading real ambient
    config."""
    env = scrubbed_env()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


@pytest.fixture()
def project(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_helper_env(), check=True)
    return proj


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def _base_env(stub_bin, home, record_file, **extra):
    env = scrubbed_env()
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["PATH"] = "%s:%s" % (stub_bin, env["PATH"])
    env["STUB_RECORD_FILE"] = str(record_file)
    env.update(extra)
    return env


def _read_records(record_file):
    if not record_file.exists():
        return []
    return [json.loads(line) for line in record_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run(project, stub_bin, home, record_file, args, extra_env=None, stdin=subprocess.DEVNULL):
    env = _base_env(stub_bin, home, record_file, **(extra_env or {}))
    cmd = ["bash", str(BOOTSTRAP), str(project), "--existing", *args]
    return subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdin=stdin,
                           capture_output=True, text=True, timeout=30)


def _include_file(home, slug):
    return home / ".config" / "git" / ("identity-%s" % slug)


def _global_config(home):
    return home / ".gitconfig"


def _artifacts_absent(home, project, slug):
    """The three commit-identity write artifacts AC-BIJ-3/AC-BIJ-10/AC-BIJ-11 forbid on a
    fail-closed / discarded-probe path: the per-account include file, the global includeIf entry,
    and repo-local useConfigOnly. The jail CONFIG directory itself is tolerated (a `gh` invocation
    may materialize it as a startup side effect)."""
    inc = _include_file(home, slug)
    glob = _global_config(home)
    include_present = glob.exists() and "includeIf" in glob.read_text(encoding="utf-8")
    local_cfg = project / ".git" / "config"
    use_config_only = local_cfg.exists() and "useConfigOnly" in local_cfg.read_text(encoding="utf-8")
    return (not inc.exists()) and (not include_present) and (not use_config_only)


def _read_identity(home, slug):
    inc = _include_file(home, slug)
    env = _helper_env()
    name = subprocess.run(["git", "config", "--file", str(inc), "user.name"],
                           env=env, capture_output=True, text=True, check=True).stdout.rstrip("\n")
    email = subprocess.run(["git", "config", "--file", str(inc), "user.email"],
                            env=env, capture_output=True, text=True, check=True).stdout.rstrip("\n")
    return name, email


# ─────────────────────────────────────────────────────────────── AC-BIJ-1 ──

def test_probe_scoped_to_declared_jail(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": "%s\tOcto Cat\tocto@example.com" % slug})
    assert proc.returncode == 0, proc.stderr

    records = _read_records(record_file)
    assert records, "the stubbed gh was never invoked"
    expected_dir = "%s/.config/gh-%s" % (home, slug)
    # every recorded invocation carried GH_CONFIG_DIR, and it was the DECLARED jail — never unset,
    # never the ambient value (there is none in a scrubbed harness to begin with).
    assert all("GH_CONFIG_DIR" in r["env"] for r in records)
    assert all(r["env"].get("GH_CONFIG_DIR") == expected_dir for r in records)


# ─────────────────────────────────────────────────────────────── AC-BIJ-2 ──

def test_login_match_adopts_probe_values_byte_identical(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    name = "O'Cat  Ünïcode Name"
    email = "octo+test@example.com"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": "%s\t%s\t%s" % (slug, name, email)})
    assert proc.returncode == 0, proc.stderr
    got_name, got_email = _read_identity(home, slug)
    assert got_name == name
    assert got_email == email


def test_login_match_is_case_insensitive(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": "DemoAcCT\tOcto Cat\tocto@example.com"})
    assert proc.returncode == 0, proc.stderr
    inc = _include_file(home, slug)
    assert inc.exists()


# ─────────────────────────────────────────────────────────────── AC-BIJ-3 ──

def test_unauthenticated_jail_fails_closed_no_writes(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_EXIT": "1"})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)


def test_probe_result_discarded_on_empty_email(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": "%s\tOcto Cat\t" % slug})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)


# ─────────────────────────────────────────────────────────────── AC-BIJ-6 ──

def test_malformed_git_author_fails_closed(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file,
                ["--gh-account", slug, "--git-author", "NoBracketsHere"])
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)
    assert not _read_records(record_file), "gh must never be probed once --git-author is malformed"


def test_no_gh_account_wires_no_commit_identity(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    proc = _run(project, stub_bin, home, record_file, [])
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".config" / "git").exists()
    glob = _global_config(home)
    assert not glob.exists() or "includeIf" not in glob.read_text(encoding="utf-8")
    local_cfg = project / ".git" / "config"
    assert not local_cfg.exists() or "useConfigOnly" not in local_cfg.read_text(encoding="utf-8")


def test_matching_login_with_invalid_email_fails_closed(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": "%s\tOcto Cat\tnoatsign-example" % slug})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)


# ─────────────────────────────────────────────────────────────── AC-BIJ-8 ──

def test_probe_env_has_no_ambient_token_or_host(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env=dict(SENTINELS, STUB_GH_TSV="%s\tOcto Cat\tocto@example.com" % slug))
    assert proc.returncode == 0, proc.stderr

    records = _read_records(record_file)
    assert records
    for record in records:
        for var in SENTINELS:
            assert var not in record["env"], "%s leaked into the probe environment" % var

    inc = _include_file(home, slug)
    written = inc.read_text(encoding="utf-8")
    for sentinel_value in SENTINELS.values():
        assert sentinel_value not in written


# ─────────────────────────────────────────────────────────────── AC-BIJ-9 ──

def test_jail_path_derives_from_validated_slug(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    raw = "  Demo Acct  "  # surrounding AND embedded whitespace
    expected_slug = "DemoAcct"  # normalize_account_slug strips ALL whitespace, keeps case
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", raw],
                extra_env={"STUB_GH_TSV": "demoacct\tOcto Cat\tocto@example.com"})
    assert proc.returncode == 0, proc.stderr

    records = _read_records(record_file)
    assert records
    expected_dir = "%s/.config/gh-%s" % (home, expected_slug)
    assert records[0]["env"].get("GH_CONFIG_DIR") == expected_dir


# ────────────────────────────────────────────────────────────── AC-BIJ-10 ──

def test_multiline_probe_field_discarded_in_full(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    attacker_token = "pwned@attacker.example"
    # the CLI's own @tsv framing escapes an embedded real newline as a literal two-char "\n" —
    # simulate exactly that shape: one physical TSV line whose NAME field carries the escape.
    tsv = "%s\tFirst\\nSecond %s\tocto@example.com" % (slug, attacker_token)
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": tsv})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)

    for root in (home, project):
        for path in root.rglob("*"):
            if path.is_file():
                assert attacker_token.encode() not in path.read_bytes(), \
                    "attacker token leaked into %s" % path


# The test above simulates the ESCAPED shape (`gh api user --jq '... | @tsv'` turns a real
# embedded newline into the literal two characters `\n`), which the broken-but-harmless
# `case "$name$email" in *'\n'*|*'\t'*)` string check catches. It never exercises the two
# structural guards that sit BEFORE that check on the raw `$probe`:
#   * the one-line guard   — `[ "$(printf '%s\n' "$probe" | wc -l)" -eq 1 ]`
#   * the tab-count guard  — `[ "$(printf '%s' "$probe" | tr -cd '\t' | wc -c)" -eq 2 ]`
# The three tests below drive the script with a GENUINE (real, non-escaped) newline and with a
# genuinely wrong tab count, so those two guards are actually reached.
#
# CONVICTION NOTE (evidence-quality, read before trusting the "convicts" label): the one-line
# guard and the tab-count guard are ANDed in the SAME `if`, and everything downstream (the exact
# `cut -f1` login-match, and the fact that `cut -fN` returns the WHOLE line for any N on a line
# with fewer tabs than needed) makes the one-line guard PROVABLY REDUNDANT given its neighbour:
# under the tab-count==2 precondition, a full valid record already consumes the entire 2-tab
# budget, so any further physical line is forced to 0 tabs — and cut's own "no delimiter -> pass
# the whole line through, for -f1 AND -f2 AND -f3 alike" behaviour then contaminates login (not
# just name/email) on that extra line, so the exact-string login-match rejects it independently,
# with the IDENTICAL generic "could not resolve a commit identity" message either way. This was
# verified empirically (both with and without the one-line clause, byte-identical stdout/stderr/
# exit code/artifact-state) — see the framework-engineer report for the worked payloads. The test
# below is retained as real regression coverage for the raw-newline shape (closing the "zero
# coverage" gap), but it does NOT independently convict the one-line guard's removal; only the
# combination of it + the tab-count guard + the login-match is what's load-bearing here.
def test_multiline_probe_field_discarded_genuine_newline(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    attacker_token = "pwned@attacker.example"
    # a REAL (not escaped) newline splitting an otherwise-valid record across two physical lines —
    # the shape a compromised/non-jq `gh` (or a future `--jq` rewrite that stops @tsv-escaping)
    # could actually emit, as opposed to the CLI's own @tsv-escaped form covered above.
    tsv = "%s\tOcto Cat\tocto@example.com\n%s" % (slug, attacker_token)
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": tsv})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)

    for root in (home, project):
        for path in root.rglob("*"):
            if path.is_file():
                assert attacker_token.encode() not in path.read_bytes(), \
                    "attacker token leaked into %s" % path


# CONVICTS: the tab-count guard (`tr -cd '\t' | wc -c -eq 2`), specifically the too-FEW-fields
# direction. A single physical line (the one-line guard is satisfied either way) with only 1 tab
# is missing the email field entirely.
#
# EVIDENCE-QUALITY NOTE: on this specific short payload, removing the tab-count guard alone does
# NOT flip this test (verified empirically) — `cut -f3` of a 2-field line returns empty either
# way, so the function's own end-of-function `[ -n "$email" ] || return 1` independently fails
# closed with the SAME generic caller message. It is retained as direct coverage of the "missing
# field" shape (paired with the 3-tab/extra-field test below, which DOES convict) and to document
# this redundancy precisely rather than imply a false conviction.
def test_probe_tab_count_rejects_missing_field(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    tsv = "%s\tOcto Cat" % slug  # 1 tab: login + name only, no email field
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": tsv})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)


# CONVICTS: the tab-count guard, too-MANY-fields direction. A single physical line with 3 tabs
# (login/name/email PLUS an extra field) still has a login that matches and `cut -f1`/`-f2`/`-f3`
# still extract the correct name/email — `cut` silently ignores the 4th field, so WITHOUT the
# tab-count guard this payload is wrongly ACCEPTED (rc 0, artifacts written) despite carrying an
# unexpected extra field the script never asked for. Verified empirically: removing the guard
# flips this test from rc!=0/no-artifacts to rc==0/artifacts-written (see report).
def test_probe_tab_count_rejects_extra_field(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    tsv = "%s\tOcto Cat\tocto@example.com\tunexpected-4th-field" % slug  # 3 tabs
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": tsv})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)


# CONVICTS: the caller's two-line post-condition in `seed_commit_identity`
# (`[ "$(printf '%s\n' "$id" | wc -l)" -eq 2 ] || die "malformed resolved identity ..."`), which
# guards against the `sed -n 1p` / `sed -n 2p` field-shift when the resolved name/email carries a
# REAL embedded newline. The gh-probe path can never reach this check with a corrupted-but-
# non-empty email (see the note on the tests above), but the --git-author path can: a `Name`
# containing a genuine newline is untouched by the (escaped-only) literal `\n`/`\t` case check,
# so `resolve_declared_identity` returns a 3-physical-line "<name>\n<email>" blob. WITH the guard,
# this dies with the SPECIFIC message "malformed resolved identity" BEFORE the name/email are ever
# split. WITHOUT the guard (verified empirically), `sed -n 1p`/`sed -n 2p` field-shift kicks in:
# name becomes only "First" and email becomes "Second" (the injected second physical line, NOT
# the real email) — validate_identity_value then rejects "Second" for lacking '@', producing a
# DIFFERENT, distinguishing message ("commit-identity email lacks '@': Second"). This test asserts
# on the "malformed resolved identity" message specifically, so it flips (message goes missing,
# replaced by the different one) when the guard is removed.
def test_git_author_embedded_newline_hits_two_line_postcondition(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    author = "First\nSecond <dev@example.com>"  # a REAL newline inside the declared name
    proc = _run(project, stub_bin, home, record_file,
                ["--gh-account", slug, "--git-author", author])
    assert proc.returncode != 0
    assert "malformed resolved identity" in proc.stderr, proc.stderr
    assert _artifacts_absent(home, project, slug)
    assert not _read_records(record_file), "gh must never be probed when --git-author is set"


# ────────────────────────────────────────────────────────────── AC-BIJ-11 ──

def test_login_mismatch_is_not_adopted(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    proc = _run(project, stub_bin, home, record_file, ["--gh-account", slug],
                extra_env={"STUB_GH_TSV": "someoneelse\tOcto Cat\tocto@example.com"})
    assert proc.returncode != 0
    assert _artifacts_absent(home, project, slug)


def test_login_mismatch_prompts_for_both_fields(project, stub_bin, home, tmp_path):
    record_file = tmp_path / "gh-record.jsonl"
    slug = "demoacct"
    env = _base_env(stub_bin, home, record_file,
                     STUB_GH_TSV="someoneelse\tWrong Name\twrong@example.com")
    typed_name = "Typed Operator"
    typed_email = "typed@example.com"

    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["bash", str(BOOTSTRAP), str(project), "--existing", "--gh-account", slug],
            env=env, cwd=str(REPO_ROOT),
            stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        os.write(master_fd, ("%s\n%s\n" % (typed_name, typed_email)).encode())
        _, stderr = proc.communicate(timeout=30)
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        os.close(master_fd)

    stderr_text = stderr.decode("utf-8", "replace")
    assert "commit-identity name for account" in stderr_text
    assert "commit-identity email for account" in stderr_text
    assert proc.returncode == 0, stderr_text

    got_name, got_email = _read_identity(home, slug)
    assert got_name == typed_name
    assert got_email == typed_email
