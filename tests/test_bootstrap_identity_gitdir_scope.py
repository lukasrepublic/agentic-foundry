"""tests/test_bootstrap_identity_gitdir_scope.py — hermetic behavioral coverage for THE NARROW
`includeIf` BINDING + THE BROAD-RULE MIGRATION in `seed_commit_identity`
(scripts/foundry-bootstrap.sh), feat-foundry-commit-identity-gitdir-scope, AC-CIDW-1..9.

THE DEFECT THIS ATOM FIXES: the shipped `seed_commit_identity` wrote ONE global `includeIf`
whose pattern was the canonical project path with a trailing `/` — git appends `**` to a
`gitdir:` pattern ending in `/`, so the rule matched the gitdir of EVERY repository nested
beneath the project, not just the project's own repository (and its linked worktrees). This
atom narrows the binding to exactly two entries (`<canon>.git` and `<canon>.git/`) and migrates
an already-bootstrapped machine off the superseded broad rule.

ANTI-VACUOUS-PASS (mirrors the acceptance contract's own device list):

  1. EVERY behavioural checkpoint asserts OBSERVED GIT RESOLUTION AGAINST A SENTINEL, never a
     string match and never a negative assertion. Measured: `git config user.email` in a nested
     repo with no global `[user]` returns EMPTY at exit 1, which satisfies "is not the declared
     value" — so a negative assertion would go GREEN for an implementation that wrote no binding
     at all, wrote a broken one, or corrupted the global config. Every resolution checkpoint here
     therefore asserts EQUALITY against a value that SHOULD resolve: `DECLARED_EMAIL` for the
     project + its worktree, `SENTINEL_EMAIL` for a nested independent repo. The sentinel is
     seeded into the GLOBAL config for every resolution test, which also proves `includeIf` wins:
     `includeIf` is applied at the point it appears in the file, so a bare global `[user]` block
     could otherwise (in a broken implementation) resolve instead of the include.

  2. THE THREE-WAY GLUE. No single `gitdir:` pattern passes project + worktree + nested-exclusion
     simultaneously (measured in the spec's own table) — AC-CIDW-2 (project), AC-CIDW-3
     (worktree) and AC-CIDW-4 (nested) mutually convict any one-pattern implementation.

  3. MIGRATION IS EXERCISED ON A DIRTY, PREFIX-COLLIDING, MULTI-ACCOUNT MACHINE
     (`test_prefix_colliding_multi_account_machine_survives_migration`, AC-CIDW-7). An unscoped
     removal (e.g. sweeping every `includeIf.gitdir` key, or a prefix/`grep -F` match) satisfies
     AC-CIDW-5 in isolation while destroying every OTHER identity on the machine — measured on a
     fixture mirroring the operator's real one. Byte-exact keying is what prevents that.

  4. NO PII IN THE AC-CIDW-8 REPORT. The "other broad rules" warning is verified never to contain
     a resolved `user.email` — including from an include file that DOES carry one, so a naive
     "resolve every candidate to build a nicer message" implementation would be caught.

Every test drives the REAL shipped script end-to-end via subprocess (never a reimplementation of
its git-config logic in Python), against a stubbed `gh`/`claude` on PATH (no network) and
`--git-author "Name <email>"` (bypasses the gh probe entirely — that resolution path is the
sibling suite's concern, `tests/test_bootstrap_commit_identity.py`, denied to this atom).

HERMETICITY, on the same two axes as the sibling suite:
  * WRITES — every driven-script invocation redirects HOME + GIT_CONFIG_GLOBAL into a per-test
    tmp_path; the suite can never touch the operator's real global git config.
  * READS — every child environment (driven script AND every test-helper subprocess: git init,
    git worktree add, the fixture-seeding `git config --file` calls, the resolution-reading
    `git config`) is built from `scrubbed_env()` (strips `GH_`/`GITHUB_*`, matching the sibling
    suite) and points `GIT_CONFIG_SYSTEM` at `/dev/null`; helper subprocesses that must NOT see
    the fixture's global config (git init, git worktree add, the `_canon()` path probe) also
    point `GIT_CONFIG_GLOBAL` at `/dev/null`, so `git init`'s own `init.templateDir` honouring
    (or any ambient identity) can never perturb a fixture or a resolution assertion.
"""
from __future__ import annotations

import os
import platform
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "foundry-bootstrap.sh"

NOOP_STUB = """#!/usr/bin/env python3
import sys
sys.exit(0)
"""

SLUG = "demoacct"
DECLARED_NAME = "Declared Operator"
DECLARED_EMAIL = "declared@example.com"
GIT_AUTHOR = "%s <%s>" % (DECLARED_NAME, DECLARED_EMAIL)
SENTINEL_NAME = "Sentinel Operator"
SENTINEL_EMAIL = "sentinel@example.invalid"

# The platform condition prefix (Terminology): gitdir/i: on Darwin, gitdir: otherwise — mirrors
# the shipped script's own `case "$(uname -s)" in Darwin) ... esac`.
PLATFORM_PREFIX = "gitdir/i:" if platform.system() == "Darwin" else "gitdir:"


def scrubbed_env():
    """A copy of the test process's own environment with every GH_- and GITHUB_-prefixed
    variable removed — the single helper every child environment in this module is built from
    (mirrors tests/test_bootstrap_commit_identity.py::scrubbed_env)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}


def _iso_env():
    """Env for a test-helper subprocess that must be fully isolated from BOTH the fixture's
    global config and any ambient one: git init, git worktree add, the canonical-path probe."""
    env = scrubbed_env()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def stub_bin(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "gh", NOOP_STUB)
    _make_executable(bindir / "claude", NOOP_STUB)
    return bindir


@pytest.fixture()
def project(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_iso_env(), check=True)
    return proj


def _canon(path) -> str:
    """The canonical project path (Terminology): symlink-resolved absolute, trailing `/` —
    byte-identical to the shipped script's own `cd "$TARGET" && pwd -P`, computed the same way
    (via bash) rather than reimplemented in Python."""
    out = subprocess.run(["bash", "-c", 'cd "$1" && pwd -P', "_", str(path)],
                          env=_iso_env(), capture_output=True, text=True, check=True)
    return out.stdout.strip() + "/"


def _inc_path(home, slug=SLUG) -> str:
    return str(home / ".config" / "git" / ("identity-%s" % slug))


def _broad_key(canon, prefix=PLATFORM_PREFIX) -> str:
    return "includeif.%s%s.path" % (prefix, canon)


def _narrow_keys(canon, prefix=PLATFORM_PREFIX):
    return ("includeif.%s%s.git.path" % (prefix, canon), "includeif.%s%s.git/.path" % (prefix, canon))


def _seed_global(home, key, value, add=False):
    """Seed a raw key/value into home/.gitconfig via git's OWN writer (never in-place text
    editing), exactly the mechanism `seed_commit_identity` itself uses. `key` may be a full
    dotted `section.subsection-with-embedded-dots.variable` string — git's CLI parses the FIRST
    token as section, the LAST as variable, and everything between (dots included) as the
    subsection; this is exactly how the shipped script writes its own includeIf entries."""
    home.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env()
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    args = ["git", "config", "--file", str(home / ".gitconfig")]
    if add:
        args.append("--add")
    args += [key, value]
    subprocess.run(args, env=env, check=True)


def _seed_sentinel(home):
    _seed_global(home, "user.name", SENTINEL_NAME)
    _seed_global(home, "user.email", SENTINEL_EMAIL)


def _get_all(home, key):
    """None if the key is entirely absent; else the list of its values, in file order."""
    env = scrubbed_env()
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    proc = subprocess.run(["git", "config", "--file", str(home / ".gitconfig"), "--get-all", key],
                           env=env, capture_output=True, text=True)
    if proc.returncode == 1:
        return None
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def _resolved(repo_dir, home, key) -> str:
    """`git config <key>` resolved INSIDE repo_dir, under the fixture's real global config —
    empty string if nothing resolves (exit 1)."""
    env = scrubbed_env()
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    proc = subprocess.run(["git", "config", key], cwd=str(repo_dir), env=env,
                           capture_output=True, text=True)
    return proc.stdout.rstrip("\n")


def _driven_env(stub_bin, home, extra=None):
    env = scrubbed_env()
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["PATH"] = "%s:%s" % (stub_bin, env["PATH"])
    if extra:
        env.update(extra)
    return env


def _run(target, stub_bin, home, args, extra_env=None):
    env = _driven_env(stub_bin, home, extra_env)
    cmd = ["bash", str(BOOTSTRAP), str(target), "--existing", *args]
    return subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)


def _wire(project, stub_bin, home, extra_env=None):
    return _run(project, stub_bin, home, ["--gh-account", SLUG, "--git-author", GIT_AUTHOR], extra_env)


def _add_worktree(project, wt_path, branch="wt-branch"):
    # An initial commit is needed before `git worktree add -b` can branch off HEAD. `-c user.*`
    # is command-line-scoped ONLY (never persisted to project/.git/config, which would otherwise
    # permanently outrank includeIf for every later resolution check in this repo).
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Setup", "-c", "user.email=setup@example.com",
                     "commit", "--allow-empty", "-q", "-m", "init"], env=_iso_env(), check=True)
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-q", str(wt_path), "-b", branch],
                    env=_iso_env(), check=True)


def _init_nested(project, name="nested"):
    nested = project / name
    nested.mkdir()
    subprocess.run(["git", "init", "-q", str(nested)], env=_iso_env(), check=True)
    return nested


def _no_includeif_artifacts(home, slug=SLUG) -> bool:
    inc = Path(_inc_path(home, slug))
    cfg = home / ".gitconfig"
    include_present = cfg.exists() and "includeIf" in cfg.read_text(encoding="utf-8")
    return (not inc.exists()) and (not include_present)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-1 ══

def test_writes_two_narrow_gitdir_entries(project, stub_bin, home):
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    canon = _canon(project)
    narrow1, narrow2 = _narrow_keys(canon)
    assert _get_all(home, narrow1) == [_inc_path(home)]
    assert _get_all(home, narrow2) == [_inc_path(home)]


def test_never_writes_the_broad_rule(project, stub_bin, home):
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    canon = _canon(project)
    assert _get_all(home, _broad_key(canon)) is None


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-2 ══

def test_project_repo_resolves_declared_over_sentinel(project, stub_bin, home):
    _seed_sentinel(home)
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    assert _resolved(project, home, "user.email") == DECLARED_EMAIL
    assert _resolved(project, home, "user.name") == DECLARED_NAME


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-3 ══

def test_linked_worktree_resolves_declared_over_sentinel(project, stub_bin, home, tmp_path):
    _seed_sentinel(home)
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    wt = tmp_path / "wt"
    _add_worktree(project, wt)
    assert _resolved(wt, home, "user.email") == DECLARED_EMAIL


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-4 ══

def test_nested_repo_resolves_the_sentinel(project, stub_bin, home):
    """A nested independent repository PRE-EXISTING before wiring."""
    _seed_sentinel(home)
    nested = _init_nested(project, "nested-before")
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    assert _resolved(nested, home, "user.email") == SENTINEL_EMAIL


def test_nested_repo_created_after_wiring_resolves_the_sentinel(project, stub_bin, home):
    """A nested independent repository created STRICTLY AFTER wiring — the common fresh-clone
    case an adopter hits when they clone a client/employer repo beneath the project post-setup."""
    _seed_sentinel(home)
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    nested = _init_nested(project, "nested-after")
    assert _resolved(nested, home, "user.email") == SENTINEL_EMAIL


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-5 ══

def test_migrates_away_the_superseded_broad_rule(project, stub_bin, home):
    canon = _canon(project)
    _seed_global(home, _broad_key(canon), "/tmp/pre-existing-inc")

    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr
    assert "migrated away the superseded broad commit-identity rule" in proc.stdout

    assert _get_all(home, _broad_key(canon)) is None
    narrow1, narrow2 = _narrow_keys(canon)
    assert _get_all(home, narrow1) == [_inc_path(home)]
    assert _get_all(home, narrow2) == [_inc_path(home)]


def test_migration_is_keyed_byte_exact_not_prefix(tmp_path, stub_bin, home):
    """`<canon>` is a strict string PREFIX of sibling project names `<canon>2` / `<canon>-other`
    (Terminology: byte-exact subsection equality). A prefix/grep -F removal would wrongly sweep
    these up; byte-exact keying must not."""
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_iso_env(), check=True)
    sib2 = tmp_path / "proj2"
    sib2.mkdir()
    sibo = tmp_path / "proj-other"
    sibo.mkdir()

    canon = _canon(proj)
    canon2 = _canon(sib2)
    canono = _canon(sibo)
    _seed_global(home, _broad_key(canon), "/tmp/target-inc")
    _seed_global(home, _broad_key(canon2), "/tmp/sibling2-inc")
    _seed_global(home, _broad_key(canono), "/tmp/siblingother-inc")

    proc = _wire(proj, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    assert _get_all(home, _broad_key(canon)) is None
    assert _get_all(home, _broad_key(canon2)) == ["/tmp/sibling2-inc"]
    assert _get_all(home, _broad_key(canono)) == ["/tmp/siblingother-inc"]


def test_migration_enumeration_is_case_correct(project, stub_bin, home):
    """git LOWERCASES section+key names on read (`--get-regexp` prints `includeif...`, never
    `includeIf...`), so a comparison against the literal `includeIf.` prefix silently no-ops. Seed
    the broad rule using the EXACT capitalization the (pre-this-atom) shipped script itself wrote
    (`includeIf`, matching `git config --global "includeIf.${match}.path" ...`) — the raw on-disk
    file therefore literally starts `[includeIf `, mixed-case, exactly as an already-bootstrapped
    machine has it today."""
    canon = _canon(project)
    _seed_global(home, "includeIf.%s%s.path" % (PLATFORM_PREFIX, canon), "/tmp/pre-existing-inc")

    raw_before = (home / ".gitconfig").read_text(encoding="utf-8")
    assert '[includeIf "' in raw_before, "fixture must literally carry the mixed-case section header"

    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr
    assert _get_all(home, _broad_key(canon)) is None


def test_migration_handles_whitespace_path_via_nul_enumeration(tmp_path, stub_bin, home):
    """A canonical project path containing a SPACE must not be word-split into the wrong key —
    the reason enumeration is NUL-delimited (`--get-regexp -z --name-only`)."""
    proj = tmp_path / "proj with space"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_iso_env(), check=True)

    canon = _canon(proj)
    assert " " in canon
    _seed_global(home, _broad_key(canon), "/tmp/space-inc")

    proc = _wire(proj, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    assert _get_all(home, _broad_key(canon)) is None
    narrow1, narrow2 = _narrow_keys(canon)
    assert _get_all(home, narrow1) == [_inc_path(home)]
    assert _get_all(home, narrow2) == [_inc_path(home)]
    # no truncated/word-split remnant of the space-containing pattern was left behind: the ONLY
    # includeIf subsections present are the two narrow entries for the full (space-containing)
    # canonical path — nothing derived from a word-split fragment of it. Enumerated the same
    # NUL-delimited way the implementation does (a plain-whitespace split would itself be wrong
    # here, given the key contains a literal space).
    env = scrubbed_env()
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    raw = subprocess.run(
        ["git", "config", "--file", str(home / ".gitconfig"), "--get-regexp", "-z", "--name-only", "^includeif\\."],
        env=env, capture_output=True, check=True,
    ).stdout
    listing = [k.decode("utf-8") for k in raw.split(b"\0") if k]
    assert sorted(listing) == sorted([narrow1, narrow2])


def test_absent_key_is_success_and_other_errors_are_fatal(project, stub_bin, home):
    # (a) clean machine, no broad rule at all: --unset-all exits 5 ("key absent") -> SUCCESS.
    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr
    canon = _canon(project)
    narrow1, narrow2 = _narrow_keys(canon)
    assert _get_all(home, narrow1) == [_inc_path(home)]

    # (b) a genuinely malformed global config: every non-5 error from git config is FATAL, never
    # swallowed by a blanket `|| true`. A malformed-config parse error (git exit 128) is used
    # because it reproduces regardless of the driving user's uid (a read-only *file* is not
    # actually enough to make `git config` fail: git rewrites via a lockfile + rename, which only
    # needs write permission on the *directory*).
    home2 = home.parent / "home2"
    home2.mkdir()
    (home2 / ".gitconfig").write_text('[bad section header without closing\n\tpath = foo\n', encoding="utf-8")
    proj2 = home.parent / "proj2"
    proj2.mkdir()
    subprocess.run(["git", "init", "-q", str(proj2)], env=_iso_env(), check=True)

    proc2 = _wire(proj2, stub_bin, home2)
    assert proc2.returncode != 0
    assert not (home2 / ".config" / "git" / ("identity-%s" % SLUG)).exists()


def test_post_removal_reread_refuses_a_surviving_broad_rule(project, stub_bin, home):
    """A DUPLICATE (multi-valued) broad-rule entry — as `--add` would leave from a corrupted
    earlier run — is the shape that distinguishes `--unset-all` from a naive `--unset`: `--unset`
    on a multi-valued key ALSO exits 5 ("has multiple values"), the SAME code AC-CIDW-5(c) treats
    as success, and would leave the rule live with no error. The post-removal re-read is the
    backstop for exactly that shape; this proves it converges to fully removed, not merely
    reduced."""
    canon = _canon(project)
    key = _broad_key(canon)
    _seed_global(home, key, "/tmp/dup-inc-1")
    _seed_global(home, key, "/tmp/dup-inc-2", add=True)
    assert _get_all(home, key) == ["/tmp/dup-inc-1", "/tmp/dup-inc-2"]

    proc = _wire(project, stub_bin, home)
    assert proc.returncode == 0, proc.stderr
    assert _get_all(home, key) is None


def test_dry_run_discloses_the_removal(project, stub_bin, home):
    canon = _canon(project)
    _seed_global(home, _broad_key(canon), "/tmp/pre-existing-inc")
    before = (home / ".gitconfig").read_text(encoding="utf-8")

    proc = _run(project, stub_bin, home,
                ["--gh-account", SLUG, "--git-author", GIT_AUTHOR, "--dry-run"])
    assert proc.returncode == 0, proc.stderr
    assert "remove the superseded broad includeIf rule" in proc.stdout
    assert canon in proc.stdout

    after = (home / ".gitconfig").read_text(encoding="utf-8")
    assert after == before, "dry-run must make NO changes"


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-6 ══

def test_rerun_converges_without_duplicates(project, stub_bin, home):
    proc1 = _wire(project, stub_bin, home)
    assert proc1.returncode == 0, proc1.stderr
    proc2 = _wire(project, stub_bin, home)
    assert proc2.returncode == 0, proc2.stderr

    canon = _canon(project)
    narrow1, narrow2 = _narrow_keys(canon)
    assert _get_all(home, narrow1) == [_inc_path(home)]
    assert _get_all(home, narrow2) == [_inc_path(home)]


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-7 ══

def test_prefix_colliding_multi_account_machine_survives_migration(tmp_path, stub_bin, home):
    """THE fixture that protects the operator's other identities: ONE dirty, multi-account,
    prefix-colliding machine, ONE run, and every pre-run line this atom does not document as
    added/removed SHALL be byte-identical afterwards. Deliberately not a whole-file digest (the
    file legitimately changes) — a per-line survivor check instead."""
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_iso_env(), check=True)
    sib2 = tmp_path / "proj2"
    sib2.mkdir()
    sibo = tmp_path / "proj-other"
    sibo.mkdir()

    canon = _canon(proj)
    canon2 = _canon(sib2)
    canono = _canon(sibo)
    unrelated_canon = "/home/operator/work/project-b-handbook/"

    _seed_global(home, _broad_key(canon), "/tmp/target-inc")
    _seed_global(home, _broad_key(canon2), "/tmp/sibling2-inc")
    _seed_global(home, _broad_key(canono), "/tmp/siblingother-inc")
    _seed_global(home, _broad_key(unrelated_canon), "/tmp/unrelated-account-inc")

    pre_lines = (home / ".gitconfig").read_text(encoding="utf-8").splitlines()
    # `_seed_global`/`_broad_key` write the section header via git's CLI dotted-key form using
    # the literal lowercase "includeif" this module standardizes on, so that is the case git
    # stores (and preserves) in the raw file — matching the header text exactly.
    documented_removed = {
        '[includeif "%s%s"]' % (PLATFORM_PREFIX, canon),
        "\tpath = /tmp/target-inc",
    }
    survivors_expected = [l for l in pre_lines if l not in documented_removed]
    assert len(survivors_expected) == len(pre_lines) - 2, "fixture sanity: exactly 2 lines documented removed"

    proc = _wire(proj, stub_bin, home)
    assert proc.returncode == 0, proc.stderr

    post_lines = (home / ".gitconfig").read_text(encoding="utf-8").splitlines()

    # every survivor line is present, byte-identical, and git's own rewrite (verified: --unset-all
    # + a fresh `git config --global` append) preserves the ORIGINAL relative order of untouched
    # sections, with new content appended at the end — so this is a prefix, not just a subset.
    assert post_lines[: len(survivors_expected)] == survivors_expected

    # and the two sibling + one unrelated broad rules are still independently resolvable, unchanged.
    assert _get_all(home, _broad_key(canon2)) == ["/tmp/sibling2-inc"]
    assert _get_all(home, _broad_key(canono)) == ["/tmp/siblingother-inc"]
    assert _get_all(home, _broad_key(unrelated_canon)) == ["/tmp/unrelated-account-inc"]

    # the target's own broad rule is gone, replaced by the narrow binding.
    assert _get_all(home, _broad_key(canon)) is None
    narrow1, narrow2 = _narrow_keys(canon)
    assert _get_all(home, narrow1) == [_inc_path(home)]
    assert _get_all(home, narrow2) == [_inc_path(home)]


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-8 ══

def test_other_broad_rules_are_reported(tmp_path, stub_bin, home):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_iso_env(), check=True)
    other = tmp_path / "project-b-handbook"
    other.mkdir()

    other_canon = _canon(other)
    _seed_global(home, _broad_key(other_canon), "/tmp/other-account-inc")

    proc = _wire(proj, stub_bin, home)
    assert proc.returncode == 0, proc.stderr
    assert "WARNING" in proc.stderr
    assert (PLATFORM_PREFIX + other_canon) in proc.stderr
    assert "re-run" in proc.stderr

    # never modified
    assert _get_all(home, _broad_key(other_canon)) == ["/tmp/other-account-inc"]


def test_warning_never_prints_a_resolved_email(tmp_path, stub_bin, home):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], env=_iso_env(), check=True)
    other = tmp_path / "project-c-handbook"
    other.mkdir()

    other_canon = _canon(other)
    other_inc = home / "other-account-identity"
    leaked_email = "other-account-do-not-leak@example.com"
    other_inc.parent.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env()
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    subprocess.run(["git", "config", "--file", str(other_inc), "user.name", "Other Account"], env=env, check=True)
    subprocess.run(["git", "config", "--file", str(other_inc), "user.email", leaked_email], env=env, check=True)
    _seed_global(home, _broad_key(other_canon), str(other_inc))

    proc = _wire(proj, stub_bin, home)
    assert proc.returncode == 0, proc.stderr
    assert leaked_email not in proc.stdout
    assert leaked_email not in proc.stderr


# ══════════════════════════════════════════════════════════════════════════════════ AC-CIDW-9 ══

def test_refuses_when_gitdir_is_not_a_directory(tmp_path, stub_bin, home):
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], env=_iso_env(), check=True)
    wt = tmp_path / "wt-as-target"
    _add_worktree(main, wt)
    assert (wt / ".git").is_file(), "fixture sanity: a linked worktree's .git is a FILE"

    proc = _wire(wt, stub_bin, home)
    assert proc.returncode != 0
    assert _no_includeif_artifacts(home)
    local_cfg = wt / ".git"  # a file, not a repo-local config dir — useConfigOnly was never set
    assert local_cfg.is_file()
