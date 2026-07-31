"""tests/test_bootstrap_direnv_wiring.py — hermetic behavioral coverage for the direnv
managed-block-writes redesign in scripts/foundry-bootstrap.sh
(feat-foundry-bootstrap-managed-block-writes, AC-BENV-1..11).

THE TWO DEFECTS THIS ATOM CLOSES (both read off the pre-atom shipped script):
  (a) `seed_gh_identity` wrote `$TARGET/.envrc` with a plain `>` redirect, silently destroying any
      pre-existing file (an AWS_PROFILE export, a source_up, a layout python) and revoking the
      adopter's `direnv allow` grant.
  (b) `--operator` was spliced verbatim into that executed file (`export FOUNDRY_OPERATOR="$OPERATOR"`),
      and no code path validated `--operator` at all; separately, the raw `--gh-account` value was
      written to `.claude/gh-identity` BEFORE `normalize_account_slug` ran, so a rejected invocation
      left behind the very marker the shipped gh-account guard keys its block decision on.

THE SHAPE THIS ATOM SHIPS instead: executable logic lives once, at direnv's own designated global-lib
scope (`~/.config/direnv/lib/foundry.sh`, installed byte-identically from `scripts/foundry-direnv-lib.sh`
by a machine-scope step); the per-project payload is INERT DATA (`.claude/gh-identity`,
`.claude/foundry-operator`), read through a redirect into a parser and never sourced or evaluated; and
the adopter's `.envrc` gains exactly one stable line (`use foundry_gh`), appended once with a
lorri/pyenv-style refuse-don't-merge posture — never rewritten again, so `direnv allow` runs once, ever.

Every test drives the REAL shipped script (never a re-implementation of its logic in Python) via
subprocess, against a stubbed `claude` (answers the toolchain-presence probe positively for the
default marketplace, so `project-scaffold`-only invocations pass precondition without a prior
toolchain-install step), a stubbed `gh` (answers `gh api user --jq '...'` per env-var-selected
values, mirroring the sibling identity suites' own convention) and a stubbed `direnv` that records
its own invocation (AC-BENV-5's behavioral half — the shipped script must never actually invoke it).

HERMETICITY, on the same two axes as the sibling identity suites:
  * WRITES — every driven-script invocation redirects HOME and GIT_CONFIG_GLOBAL into a per-test
    tmp_path (which is also what makes the INSTALLED LIB, $HOME/.config/direnv/lib/foundry.sh,
    observable and safe to assert on); the suite can never touch the operator's real global git
    config or the operator's real $HOME/.config/direnv/lib/.
  * READS — every child environment is built from `scrubbed_env()`, which strips every `GH_`- and
    `GITHUB_`-prefixed variable from the test process's own environment first, so an unscrubbed
    ambient token or this repository's own `.envrc` jail can never leak into a driven invocation.

This module is the ONLY place a fixture carries a quote, a backtick, a `$`, a backslash or a
newline (AC-BENV-7's Terminology): the sibling acceptance-contract's locators are a single
quoting-layer `sh -c '...'` grammar with no backslashes, which structurally cannot construct such a
value — only a real subprocess argv list (built in Python, never through a shell) can.

SECURITY-REVIEW FOLLOW-UP (PR #295, mandatory review B1/R1/R3/R5; R2/R4 covered by comments +
one observability test). The write side validates every value BEFORE it writes anything
(validate_arguments), but the shipped lib originally read `.claude/gh-identity` /
`.claude/foundry-operator` back with `tr` alone, which strips whitespace but does not bound the
character set — a hand-edited, corrupted, or symlink-swapped identity file (e.g. one containing
`work/../gh`) could resolve `GH_CONFIG_DIR` to gh's DEFAULT config directory, the exact
cross-account leak this atom exists to prevent. The `test_read_side_*` tests below drive
`use_foundry_gh` directly (installed-lib-plus-hostile-fixture, the same pattern as
`test_identity_data_is_inert`) to prove the read side now re-applies the same allowlist, poisons
`GH_CONFIG_DIR` on any rejection (or a missing/unreadable identity file) rather than falling
through to the default, and never exports a rejected `FOUNDRY_OPERATOR`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "foundry-bootstrap.sh"
LIB_SOURCE = REPO_ROOT / "scripts" / "foundry-direnv-lib.sh"
BASH_BIN = shutil.which("bash") or "/bin/bash"

DEFAULT_MARKETPLACE_SHORT = "agentic-foundry"
DISPATCH_VERB = "use foundry_gh"


# ── stubs ────────────────────────────────────────────────────────────────────────────────────────

# Answers the toolchain-presence probe (`claude plugin list --json`) POSITIVELY for the default
# marketplace, so a `project-scaffold`-only invocation clears its precondition without a prior
# `toolchain-install` step; every other invocation (marketplace add, plugin install) is a no-op.
CLAUDE_STUB = """#!/usr/bin/env python3
import sys

argv = sys.argv[1:]
if len(argv) >= 2 and argv[0] == "plugin" and argv[1] == "list":
    sys.stdout.write('[{"id": "foundry@%s"}]')
    sys.exit(0)
sys.exit(0)
""" % DEFAULT_MARKETPLACE_SHORT

# Answers `gh api user --jq '[(.login // ""), (.name // ""), (.email // "")] | @tsv'` per
# $STUB_GH_TSV / $STUB_GH_EXIT — mirrors tests/test_bootstrap_commit_identity.py's own GH_STUB.
GH_STUB = """#!/usr/bin/env python3
import os
import sys

rc = int(os.environ.get("STUB_GH_EXIT", "0"))
if rc != 0:
    sys.exit(rc)
tsv = os.environ.get("STUB_GH_TSV", "")
sys.stdout.write(tsv)
if tsv and not tsv.endswith("\\n"):
    sys.stdout.write("\\n")
sys.exit(0)
"""

# Records its own invocation to $STUB_DIRENV_RECORD_FILE — used only by
# test_does_not_invoke_direnv (AC-BENV-5's behavioral half: the shipped script must never actually
# invoke `direnv`, so the record file must stay absent after a full run).
DIRENV_STUB = """#!/usr/bin/env python3
import os
import sys

record_path = os.environ.get("STUB_DIRENV_RECORD_FILE")
if record_path:
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write("invoked: %s\\n" % " ".join(sys.argv[1:]))
sys.exit(0)
"""


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def scrubbed_env():
    """A copy of the test process's own environment with every GH_- and GITHUB_-prefixed variable
    removed (mirrors the sibling identity suites' own convention) -- the single helper every child
    environment in this module is built from."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def stub_bin(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_STUB)
    _make_executable(bindir / "gh", GH_STUB)
    _make_executable(bindir / "direnv", DIRENV_STUB)
    return bindir


def _env(home, stub_bin, **extra):
    env = scrubbed_env()
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["PATH"] = "%s:%s" % (stub_bin, env["PATH"])
    # Default gh-probe answer for the default '--gh-account demoacct' used throughout this module
    # (commit-identity resolution needs SOME source when --git-author is not also given); any test
    # exercising a different slug or a hostile probe overrides this explicitly.
    env.setdefault("STUB_GH_TSV", "demoacct\tOcto Cat\tocto@example.com")
    env.update(extra)
    return env


def _run(args, env, timeout=30, stdin=subprocess.DEVNULL):
    cmd = [BASH_BIN, str(BOOTSTRAP)] + list(args)
    return subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdin=stdin,
                           capture_output=True, text=True, timeout=timeout)


def _existing_target(tmp_path, name="project"):
    """A target satisfying obtain_repo's/seed_commit_identity's `.git` DIRECTORY check without a
    genuine git repository (mirrors tests/test_bootstrap_step_split.py's own convention) -- kept
    independent of the git binary's own repo-format concerns."""
    t = tmp_path / name
    (t / ".git").mkdir(parents=True)
    return t


def _seed_operators_registry(target):
    claude_dir = target / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "foundry-operators.json").write_text(
        json.dumps({"operators": {"op_example": {"name": "", "github": "", "added_at": ""}}}, indent=2) + "\n",
        encoding="utf-8")


def _installed_lib(home):
    return home / ".config" / "direnv" / "lib" / "foundry.sh"


# ── write-set / snapshot helpers (Terminology: the write-set) ──────────────────────────────────

def _snapshot_paths(root):
    """path(posix, relative) -> ('SYMLINK:<target>' | sha256) for every FILE/symlink under root.
    Plain directories are not tracked as distinct write-set members: the acceptance contract's own
    enumeration (Terminology / AC-BENV-10) names concrete files only, and a directory's mere
    existence is an incidental byproduct of `mkdir -p` preceding one of those file writes, never
    independently informative."""
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = "SYMLINK:" + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _ignored(relpath):
    """The write-set's three deliberate blind spots (Terminology)."""
    if relpath == ".git/index":
        return True
    if relpath == ".git/logs" or relpath.startswith(".git/logs/"):
        return True
    if relpath.endswith(".lock"):
        return True
    return False


def _write_set(before, after):
    changed = {}
    for k in set(before) | set(after):
        if _ignored(k):
            continue
        if before.get(k) != after.get(k):
            changed[k] = (before.get(k), after.get(k))
    return changed


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-1 ══

def test_installs_lib_byte_identical(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    proc = _run(["project-scaffold", str(target), "--existing"], _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr

    installed = _installed_lib(home)
    assert installed.exists()
    assert installed.read_bytes() == LIB_SOURCE.read_bytes()


def test_installed_lib_mode_preserved(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    lib_dir = home / ".config" / "direnv" / "lib"
    lib_dir.mkdir(parents=True)
    installed = lib_dir / "foundry.sh"
    installed.write_text("# pre-existing stub, not the shipped content\n", encoding="utf-8")
    installed.chmod(0o640)

    proc = _run(["project-scaffold", str(target), "--existing"], _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr

    mode = stat.S_IMODE(installed.stat().st_mode)
    assert mode == 0o640, oct(mode)
    # and the install still happened -- mode preservation must not mean "skipped the write".
    assert installed.read_bytes() == LIB_SOURCE.read_bytes()


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-2 ══

WIRING_COMMENT_NEEDLE = "foundry"  # the stanza names the factory; exact wording is the script's own


@pytest.mark.parametrize("case", ["absent", "conforming", "foreign"])
def test_wiring_stanza_appended_once(tmp_path, home, stub_bin, case):
    target = _existing_target(tmp_path)
    envrc = target / ".envrc"
    foreign_lines = ["export AWS_PROFILE=work", "source_up_if_exists", "layout python"]

    if case == "conforming":
        # A prior run's own converged output -- the shape a re-run must leave untouched.
        first = _run(["project-scaffold", str(target), "--existing", "--gh-account", "demoacct"],
                     _env(home, stub_bin))
        assert first.returncode == 0, first.stderr
        pre_content = envrc.read_text(encoding="utf-8")
        pre_mtime = envrc.stat().st_mtime_ns
    elif case == "foreign":
        envrc.write_text("\n".join(foreign_lines) + "\n", encoding="utf-8")

    proc = _run(["project-scaffold", str(target), "--existing", "--gh-account", "demoacct"],
                _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr

    content = envrc.read_text(encoding="utf-8")
    lines = content.splitlines()
    conforming_count = sum(1 for l in lines if l.strip() == DISPATCH_VERB)
    assert conforming_count == 1, content

    if case == "foreign":
        assert lines[:3] == foreign_lines, content
        assert lines[-1] == DISPATCH_VERB
    elif case == "conforming":
        assert content == pre_content
        assert envrc.stat().st_mtime_ns == pre_mtime, "a conforming .envrc must not be rewritten at all"
    else:
        assert content == envrc.read_text(encoding="utf-8")
        assert lines == [l for l in lines]  # absent-state file contains exactly the stanza
        assert len(lines) == 2
        assert lines[1] == DISPATCH_VERB


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-3 ══

@pytest.mark.parametrize("case", ["ambiguous_reference", "symlinked_envrc", "symlinked_lib"])
def test_refuses_ambiguous_or_symlinked(tmp_path, home, stub_bin, case):
    target = _existing_target(tmp_path)
    args = ["project-scaffold", str(target), "--existing"]

    if case == "ambiguous_reference":
        envrc = target / ".envrc"
        before = "# use foundry_gh (disabled manually, do not re-add)\nexport AWS_PROFILE=work\n"
        envrc.write_text(before, encoding="utf-8")
        args += ["--gh-account", "demoacct"]
        proc = _run(args, _env(home, stub_bin))
        assert proc.returncode != 0
        assert DISPATCH_VERB in proc.stderr, proc.stderr
        assert envrc.read_text(encoding="utf-8") == before

    elif case == "symlinked_envrc":
        referent = tmp_path / "external-envrc"
        referent.write_text("export SOMETHING=1\n", encoding="utf-8")
        envrc = target / ".envrc"
        envrc.symlink_to(referent)
        args += ["--gh-account", "demoacct"]
        proc = _run(args, _env(home, stub_bin))
        assert proc.returncode != 0
        assert DISPATCH_VERB in proc.stderr, proc.stderr
        assert envrc.is_symlink()
        assert os.readlink(envrc) == str(referent)
        assert referent.read_text(encoding="utf-8") == "export SOMETHING=1\n"

    else:  # symlinked_lib
        lib_dir = home / ".config" / "direnv" / "lib"
        lib_dir.mkdir(parents=True)
        referent = tmp_path / "external-lib.sh"
        referent.write_text("# not the shipped lib\n", encoding="utf-8")
        installed = lib_dir / "foundry.sh"
        installed.symlink_to(referent)
        proc = _run(args, _env(home, stub_bin))
        assert proc.returncode != 0
        assert DISPATCH_VERB in proc.stderr, proc.stderr
        assert installed.is_symlink()
        assert os.readlink(installed) == str(referent)
        assert referent.read_text(encoding="utf-8") == "# not the shipped lib\n"


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-4 ══

def test_identity_data_is_inert(tmp_path, home, stub_bin):
    """AC-BENV-4's inertness fixture, adapted for the B1 security-review remediation (PR #295):
    the criterion's letter ("the literal text appears in the resulting variable's value") was
    written against the PRE-remediation design, where `tr` alone bounded the value and ANY string
    -- hostile or not -- flowed straight into GH_CONFIG_DIR. Post-remediation, a `$(...)`-bearing
    value is exactly what the new read-side allowlist REJECTS (poisoning GH_CONFIG_DIR instead of
    exporting it) -- a STRICTLY STRONGER outcome than harmless passthrough, but one where the
    literal text no longer lands in $GH_CONFIG_DIR itself. The same two properties the criterion
    cares about are still proven here, from the same fixture: (a) the command's side effect does
    NOT occur (the sentinel file is never created -- non-execution, unconditionally true regardless
    of accept/reject), and (b) the literal payload text still demonstrably survives as an INERT
    STRING all the way through this dispatch -- reaching the rejection diagnostic verbatim rather
    than being interpreted as a command anywhere along the way."""
    target = _existing_target(tmp_path)
    proc = _run(["project-scaffold", str(target), "--existing", "--gh-account", "demoacct"],
                _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr

    sentinel = tmp_path / "PWNED"
    marker = target / ".claude" / "gh-identity"
    payload = "$(touch %s)demoacct" % sentinel
    marker.write_text(payload + "\n", encoding="utf-8")

    result = _use_foundry_gh(target, home)
    assert result.returncode == 0, result.stderr
    assert not sentinel.exists(), "the $(...) command-substitution form EXECUTED -- inertness violated"

    values = _parse_use_foundry_gh_output(result.stdout)
    assert values.get("GH_CONFIG_DIR") != str(home / ".config" / "gh"), "fell back to the default account"
    assert "$(touch" in result.stderr, (
        "the literal payload text did not survive, as an inert string, through to the rejection "
        "diagnostic -- it should never be interpreted, only carried and eventually reported"
    )


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-5 ══

def test_does_not_invoke_direnv(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    _seed_operators_registry(target)
    record_file = tmp_path / "direnv-record.jsonl"
    env = _env(home, stub_bin, STUB_DIRENV_RECORD_FILE=str(record_file))
    proc = _run(["project-scaffold", str(target), "--existing",
                 "--operator", "op_demo", "--gh-account", "demoacct",
                 "--git-author", "Dev Tester <dev@example.com>"], env)
    assert proc.returncode == 0, proc.stderr
    assert not record_file.exists(), "the shipped script invoked `direnv`"


def test_reports_direnv_allow_only_when_envrc_changed(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    env = _env(home, stub_bin)
    args = ["project-scaffold", str(target), "--existing", "--gh-account", "demoacct"]

    proc1 = _run(args, env)
    assert proc1.returncode == 0, proc1.stderr
    assert "direnv allow" in proc1.stdout, proc1.stdout
    assert str(target) in proc1.stdout

    proc2 = _run(args, env)
    assert proc2.returncode == 0, proc2.stderr
    assert "direnv allow" not in proc2.stdout, proc2.stdout
    assert "no re-authorization" in proc2.stdout, proc2.stdout


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-6 ══

def test_validates_arguments_before_any_step(tmp_path, home, stub_bin):
    target = tmp_path / "nonexistent-target"
    before_home = _snapshot_paths(home)
    proc = _run(["project-scaffold", str(target), "--existing", "--gh-account", "not a valid slug!"],
                _env(home, stub_bin))
    assert proc.returncode != 0
    assert "--gh-account" in proc.stderr, proc.stderr
    assert not target.exists()
    assert _snapshot_paths(home) == before_home


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-7 ══

HOSTILE_OPERATOR_VALUES = {
    "double_quote": 'op"evil',
    "single_quote": "op'evil",
    "command_substitution": "op$(touch pwned)",
    "backtick": "op`touch pwned`",
    "newline": "op\nevil",
}


@pytest.mark.parametrize("case", sorted(HOSTILE_OPERATOR_VALUES))
def test_operator_value_cannot_inject(tmp_path, home, stub_bin, case):
    target = _existing_target(tmp_path)
    value = HOSTILE_OPERATOR_VALUES[case]
    before_home = _snapshot_paths(home)
    before_target = _snapshot_paths(target)

    proc = _run(["project-scaffold", str(target), "--existing", "--operator", value],
                _env(home, stub_bin))
    assert proc.returncode != 0
    assert "--operator" in proc.stderr, proc.stderr
    assert _write_set(before_home, _snapshot_paths(home)) == {}
    assert _write_set(before_target, _snapshot_paths(target)) == {}


def test_accepted_operator_value_yields_parsable(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    _seed_operators_registry(target)
    proc = _run(["project-scaffold", str(target), "--existing",
                 "--operator", "op.demo-1", "--gh-account", "demoacct"], _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr

    for p in (_installed_lib(home), target / ".envrc"):
        r = subprocess.run([BASH_BIN, "-n", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, (p, r.stderr)


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-8 ══

REJECTED_CASES = {
    "gh_account": ["--gh-account", "bad slug!"],
    "git_author": ["--git-author", "NoBracketsHere"],
    "operator": ["--operator", "bad operator!"],
}


@pytest.mark.parametrize("case", sorted(REJECTED_CASES))
def test_rejected_argument_writes_nothing(tmp_path, home, stub_bin, case):
    target = _existing_target(tmp_path)
    _seed_operators_registry(target)
    before_home = _snapshot_paths(home)
    before_target = _snapshot_paths(target)

    proc = _run(["project-scaffold", str(target), "--existing"] + REJECTED_CASES[case],
                _env(home, stub_bin))
    assert proc.returncode != 0
    assert _write_set(before_home, _snapshot_paths(home)) == {}
    assert _write_set(before_target, _snapshot_paths(target)) == {}


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-9 ══

def test_marker_carries_validated_slug(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    raw = "  Demo Acct  "  # surrounding AND embedded whitespace
    expected_slug = "DemoAcct"  # normalize_account_slug strips ALL whitespace, keeps case
    env = _env(home, stub_bin, STUB_GH_TSV="%s\tOcto Cat\tocto@example.com" % expected_slug)

    proc = _run(["project-scaffold", str(target), "--existing", "--gh-account", raw], env)
    assert proc.returncode == 0, proc.stderr

    marker = target / ".claude" / "gh-identity"
    assert marker.read_bytes() == (expected_slug + "\n").encode("utf-8")

    inc = home / ".config" / "git" / ("identity-%s" % expected_slug)
    assert inc.exists()

    expected_gh_config_dir = "%s/.config/gh-%s" % (home, expected_slug)
    # byte-agreement with the installed lib's OWN derivation of GH_CONFIG_DIR from the SAME marker.
    script = 'cd "$1" && . "$2" && use_foundry_gh && printf "%s" "$GH_CONFIG_DIR"'
    result = subprocess.run(
        [BASH_BIN, "-c", script, "_", str(target), str(_installed_lib(home))],
        env=dict(scrubbed_env(), HOME=str(home)), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected_gh_config_dir


# ══════════════════════════════════════════════════════════════════════════════ AC-BENV-10 ══

SHAPES = {
    # (i) all three supplied
    "all_three": (
        ["--operator", "op_demo", "--gh-account", "demoacct", "--git-author", "Dev Tester <dev@example.com>"],
        {".gitignore", ".foundry/leak-scan/known-bad-shas.txt", ".claude/foundry-operators.json",
         ".claude/gh-identity", ".claude/foundry-operator", ".envrc", ".git/config"},
    ),
    # (ii) none of the three
    "none": ([], {".gitignore", ".foundry/leak-scan/known-bad-shas.txt"}),
    # (iii) --gh-account supplied, --operator absent
    "gh_account_only": (
        ["--gh-account", "demoacct"],
        {".gitignore", ".foundry/leak-scan/known-bad-shas.txt",
         ".claude/gh-identity", ".envrc", ".git/config"},
    ),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_write_set_matches_enumeration(tmp_path, home, stub_bin, shape):
    args, expected = SHAPES[shape]
    target = _existing_target(tmp_path)
    _seed_operators_registry(target)
    before = _snapshot_paths(target)

    env = _env(home, stub_bin, STUB_GH_TSV="demoacct\tOcto Cat\tocto@example.com")
    proc = _run(["project-scaffold", str(target), "--existing"] + args, env)
    assert proc.returncode == 0, proc.stderr

    changed = _write_set(before, _snapshot_paths(target))
    assert set(changed) == expected, changed


def test_second_identical_run_is_byte_identical(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    _seed_operators_registry(target)
    args = ["project-scaffold", str(target), "--existing",
            "--operator", "op_demo", "--gh-account", "demoacct",
            "--git-author", "Dev Tester <dev@example.com>"]
    env = _env(home, stub_bin)

    proc1 = _run(args, env)
    assert proc1.returncode == 0, proc1.stderr
    after1 = _snapshot_paths(target)
    envrc = target / ".envrc"
    envrc_mtime_after_run1 = envrc.stat().st_mtime_ns

    time.sleep(0.01)  # so a real rewrite-to-identical-bytes would still show a distinct mtime
    proc2 = _run(args, env)
    assert proc2.returncode == 0, proc2.stderr
    after2 = _snapshot_paths(target)

    assert after1 == after2
    assert envrc.stat().st_mtime_ns == envrc_mtime_after_run1, ".envrc was written on the re-run"


def test_operator_record_preserved_on_rerun(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    _seed_operators_registry(target)
    args = ["project-scaffold", str(target), "--existing", "--operator", "op_demo"]
    env = _env(home, stub_bin)

    proc1 = _run(args, env)
    assert proc1.returncode == 0, proc1.stderr

    reg = target / ".claude" / "foundry-operators.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    data["operators"]["op_demo"]["github"] = "octocat"
    data["operators"]["op_demo"]["added_at"] = "2026-01-01"
    reg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    proc2 = _run(args, env)
    assert proc2.returncode == 0, proc2.stderr

    data2 = json.loads(reg.read_text(encoding="utf-8"))
    assert data2["operators"]["op_demo"]["github"] == "octocat"
    assert data2["operators"]["op_demo"]["added_at"] == "2026-01-01"


# ══════════════════════════════════ security-review follow-up (PR #295) ══════════════════════════
# B1 (Block) + R1/R5 (Risk): read-side validation in use_foundry_gh. R3 (Risk): mode-clamp on
# install. R2 (Risk, cheap mitigation): the append-skip is now observable. R4 is documented only
# (a code comment at the check + this PR's residuals list), not tested here.

def _install_lib_for_read_tests(home):
    """Places the shipped lib at the path use_foundry_gh's caller (install_direnv_lib) would --
    without running a full scaffold -- so the read-side tests below can drive the dispatch function
    directly against a hand-crafted hostile fixture."""
    lib_dir = home / ".config" / "direnv" / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    installed = lib_dir / "foundry.sh"
    installed.write_bytes(LIB_SOURCE.read_bytes())
    return installed


def _seed_read_side_fixture(tmp_path, home, gh_identity_content=None, operator_content=None,
                             name="read-side-project"):
    target = _existing_target(tmp_path, name=name)
    _install_lib_for_read_tests(home)
    claude_dir = target / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    if gh_identity_content is not None:
        (claude_dir / "gh-identity").write_text(gh_identity_content, encoding="utf-8")
    if operator_content is not None:
        (claude_dir / "foundry-operator").write_text(operator_content, encoding="utf-8")
    return target


def _use_foundry_gh(target, home):
    """Sources the INSTALLED lib with `target` as cwd, calls use_foundry_gh, and reports the two
    resulting variables (FOUNDRY_OPERATOR reported as the literal string '<unset>' when the
    function declined to export it) plus stderr -- the read-side security-review regression
    harness, mirroring test_identity_data_is_inert's own sourcing pattern."""
    installed = _installed_lib(home)
    script = (
        'cd "$1" && . "$2" && use_foundry_gh && '
        'printf "GH_CONFIG_DIR=%s\\nFOUNDRY_OPERATOR=%s\\n" "$GH_CONFIG_DIR" "${FOUNDRY_OPERATOR-<unset>}"'
    )
    return subprocess.run(
        [BASH_BIN, "-c", script, "_", str(target), str(installed)],
        env=dict(scrubbed_env(), HOME=str(home)), capture_output=True, text=True, timeout=15,
    )


def _parse_use_foundry_gh_output(stdout):
    values = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            values[k] = v
    return values


# ── B1 (Block): a traversal-bearing gh-identity value must NOT resolve to gh's default config ──

def test_read_side_traversal_slug_is_poisoned(tmp_path, home):
    target = _seed_read_side_fixture(tmp_path, home, gh_identity_content="work/../gh\n")
    result = _use_foundry_gh(target, home)
    assert result.returncode == 0, result.stderr

    values = _parse_use_foundry_gh_output(result.stdout)
    default_gh_config_dir = str(home / ".config" / "gh")
    gh_config_dir = values.get("GH_CONFIG_DIR", "")
    assert gh_config_dir, "GH_CONFIG_DIR must ALWAYS be exported, never left unset, on a rejection"
    assert gh_config_dir != default_gh_config_dir, (
        "a traversal-bearing slug resolved to gh's DEFAULT config directory -- the exact "
        "cross-account leak this atom exists to prevent"
    )
    assert "gh-identity" in result.stderr, result.stderr


def test_read_side_hostile_slug_values_are_poisoned(tmp_path, home):
    default_gh_config_dir = str(home / ".config" / "gh")
    for i, content in enumerate(("work/../gh\n", "/etc/passwd\n", "\n", "..\n")):
        target = _seed_read_side_fixture(tmp_path, home, gh_identity_content=content,
                                          name="read-side-project-%d" % i)
        result = _use_foundry_gh(target, home)
        assert result.returncode == 0, (content, result.stderr)
        values = _parse_use_foundry_gh_output(result.stdout)
        gh_config_dir = values.get("GH_CONFIG_DIR", "")
        assert gh_config_dir, (content, "GH_CONFIG_DIR left unset")
        assert gh_config_dir != default_gh_config_dir, (content, gh_config_dir)


# ── R1 (Risk): a missing/unreadable gh-identity file must ALSO fail closed (poison), not fall
# open to the default account. .claude/foundry-operator, by contrast, just stays unexported.

def test_read_side_missing_identity_file_is_poisoned(tmp_path, home):
    target = _seed_read_side_fixture(tmp_path, home)  # no gh-identity file at all
    result = _use_foundry_gh(target, home)
    assert result.returncode == 0, result.stderr

    values = _parse_use_foundry_gh_output(result.stdout)
    default_gh_config_dir = str(home / ".config" / "gh")
    gh_config_dir = values.get("GH_CONFIG_DIR", "")
    assert gh_config_dir, "GH_CONFIG_DIR must ALWAYS be exported, even with no identity file at all"
    assert gh_config_dir != default_gh_config_dir
    assert "gh-identity" in result.stderr, result.stderr
    assert values.get("FOUNDRY_OPERATOR") == "<unset>", "no operator file -> simply not exported"


# ── R5 (Risk): a hostile .claude/foundry-operator value must not be exported (no poison needed
# for FOUNDRY_OPERATOR -- an unexported value leaves downstream registry resolution fail-closed).

def test_read_side_hostile_operator_value_is_not_exported(tmp_path, home):
    target = _seed_read_side_fixture(
        tmp_path, home, gh_identity_content="demoacct\n", operator_content="op$(touch pwned)\n")
    result = _use_foundry_gh(target, home)
    assert result.returncode == 0, result.stderr

    values = _parse_use_foundry_gh_output(result.stdout)
    assert values.get("FOUNDRY_OPERATOR") == "<unset>", values
    assert not (target / "pwned").exists(), "the $(...) form executed -- inertness violated"
    assert "foundry-operator" in result.stderr, result.stderr


# ── R3 (Risk): mode preservation on install must clamp a planted permissive mode, never carry a
# world-writable bit (or setuid/setgid/sticky) across under the guise of "preserving" it.

def test_installed_lib_mode_is_clamped_when_planted_permissive(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    lib_dir = home / ".config" / "direnv" / "lib"
    lib_dir.mkdir(parents=True)
    installed = lib_dir / "foundry.sh"
    installed.write_text("# planted, world-writable stand-in\n", encoding="utf-8")
    installed.chmod(0o666)

    proc = _run(["project-scaffold", str(target), "--existing"], _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr

    mode = stat.S_IMODE(installed.stat().st_mode)
    assert mode <= 0o755, oct(mode)
    assert not (mode & 0o022), "group/other WRITE bit survived the 'preserve mode' install: %s" % oct(mode)
    assert installed.read_bytes() == LIB_SOURCE.read_bytes()


def test_installed_lib_mode_0640_still_preserved_exactly(tmp_path, home, stub_bin):
    """The clamp must be a no-op for a mode already within the 0755 envelope -- the reviewer's own
    compatibility bar for the pre-existing AC-BENV-1 mode-preservation checkpoint."""
    target = _existing_target(tmp_path)
    lib_dir = home / ".config" / "direnv" / "lib"
    lib_dir.mkdir(parents=True)
    installed = lib_dir / "foundry.sh"
    installed.write_text("# pre-existing stub\n", encoding="utf-8")
    installed.chmod(0o640)

    proc = _run(["project-scaffold", str(target), "--existing"], _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE(installed.stat().st_mode) == 0o640


# ── R2 (Risk, cheap mitigation): a skipped append (already-conforming .envrc) now names the exact
# line it found, so a wrongful skip is at least observable in the run's own output.

def test_wiring_stanza_skip_reports_line_number(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    envrc = target / ".envrc"
    envrc.write_text("export FOO=1\n%s\n" % DISPATCH_VERB, encoding="utf-8")

    proc = _run(["project-scaffold", str(target), "--existing", "--gh-account", "demoacct"],
                _env(home, stub_bin))
    assert proc.returncode == 0, proc.stderr
    assert "line 2" in proc.stdout, proc.stdout
    assert "not appending" in proc.stdout, proc.stdout
