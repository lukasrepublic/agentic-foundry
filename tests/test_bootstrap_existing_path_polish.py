"""tests/test_bootstrap_existing_path_polish.py — hermetic behavioral coverage for
feat-foundry-bootstrap-existing-path-polish (AC-BEPP-1a..1d, AC-BEPP-2, AC-BEPP-4, AC-BEPP-5).

Every test drives the REAL shipped script (scripts/foundry-bootstrap.sh, never a re-implementation
of its logic in Python) via subprocess, with the `project-scaffold` step selector EXPLICIT (never
the combined form, whose toolchain-install step performs real writes before any check this atom
adds ever runs — the spec's v1.1 SCOPE note). A stubbed `claude` (positive plugin-inventory answer,
so `require_toolchain_affirmed`'s probe affirms with no network) and a harmless no-op `gh` sit first
on PATH; every child environment redirects `HOME`/`GIT_CONFIG_GLOBAL` into a per-test scratch
directory (mirrors the sibling bootstrap suites' own convention — this module can never touch the
operator's real global git config or the real `~/.config/direnv/lib/foundry.sh`).

AC-BEPP-2's own normative verification premise (the reason this criterion cannot pass vacuously): a
SCRATCH `$HOME` the test creates and controls, in which the machine-scope direnv-lib path carries NO
`foundry.sh` BEFORE the invocation starts — never the ambient `$HOME`, where an already-installed,
idempotently-rewritten lib would make a genuine write indistinguishable from no write at all. Every
write-set assertion below asserts absence *before* as well as *after* the driven refusal, and
`test_write_set_probe_detects_a_planted_write` is the positive control proving the same snapshot
comparison actually reports a difference when a file is planted under either observed root — without
it, a snapshot helper that silently observed nothing would make the sweep green forever.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "foundry-bootstrap.sh"
BASH_BIN = shutil.which("bash") or "/bin/bash"

# A `claude` stub that answers the toolchain-presence probe (`plugin list --json`) with a positive
# match for the default marketplace, so `require_toolchain_affirmed` affirms with no network and no
# real `claude` CLI needed. Every other invocation (there should be none — the refusals this module
# proves all land before `toolchain_install_step` would ever run in the combined form, and this
# module never drives the combined form) exits 0 harmlessly.
CLAUDE_STUB = """#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if len(argv) >= 2 and argv[0] == "plugin" and argv[1] == "list":
    sys.stdout.write('[{"id": "foundry@agentic-foundry"}]')
    sys.exit(0)
sys.exit(0)
"""

# Present on PATH (preflight_scaffold's `command -v gh` only fires with --gh-account, which this
# module never passes) but never meant to be invoked.
GH_NOOP_STUB = """#!/usr/bin/env python3
import sys
sys.exit(0)
"""

# security review R1: emulates `git clone <url> <dest>` by materializing <dest>/.git/ plus a
# HOSTILE .claude/foundry-operators.json -- a SYMLINK to $HOSTILE_SYMLINK_TARGET, exactly what a
# malicious --template could ship. This drives the CLONE path (--existing OFF), which
# preflight_scaffold's [ -L ] check never reaches (that check is scoped to --existing, since the
# clone path cannot check a registry that does not exist until the clone completes) -- so this is
# the harness for seed_operator's OWN symlink backstop, not preflight_scaffold's. Every other git
# subcommand exits 0 harmlessly (none is expected: this module never passes --gh-account, so
# seed_commit_identity's real `git config` calls never fire).
GIT_CLONE_SYMLINK_STUB = """#!/usr/bin/env python3
import os
import sys

argv = sys.argv[1:]
if argv and argv[0] == "clone":
    positional = []
    skip_next = False
    for tok in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok == "-c":
            skip_next = True
            continue
        positional.append(tok)
    dest = positional[-1]
    os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
    claude_dir = os.path.join(dest, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    os.symlink(os.environ["HOSTILE_SYMLINK_TARGET"], os.path.join(claude_dir, "foundry-operators.json"))
    sys.exit(0)
sys.exit(0)
"""


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _scrubbed_env():
    """Mirrors the sibling bootstrap suites' own convention: strip every GH_-/GITHUB_-prefixed
    variable from the test process's own environment first, so an unscrubbed ambient token can
    never leak into a driven invocation."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}


@pytest.fixture()
def scratch_home(tmp_path):
    """The scratch home (Terminology, AC-BEPP-2's verification premise): a directory this fixture
    creates and controls, exported as $HOME for the invocation under test, in which the
    machine-scope direnv-lib path carries NO foundry.sh at the moment the invocation starts."""
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def stub_bin(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_STUB)
    _make_executable(bindir / "gh", GH_NOOP_STUB)
    return bindir


def _env(home, bindir, **extra):
    env = _scrubbed_env()
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["PATH"] = "%s:%s" % (bindir, env["PATH"])
    env.update(extra)
    return env


def _run(args, env, timeout=30):
    cmd = [BASH_BIN, str(BOOTSTRAP)] + list(args)
    return subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)


def _lib_path(home: Path) -> Path:
    """The machine-scope direnv-lib path (Terminology, the observed write scope's second root)."""
    return home / ".config" / "direnv" / "lib" / "foundry.sh"


def _existing_target(tmp_path, name="target"):
    """A target directory satisfying obtain_repo's/preflight_scaffold's `.git` DIRECTORY check
    without needing a genuine git repository (mirrors tests/test_bootstrap_step_split.py's and
    tests/test_bootstrap_direnv_wiring.py's own convention)."""
    t = tmp_path / name
    (t / ".git").mkdir(parents=True)
    return t


def _tree_snapshot(root: Path):
    """Recursive path listing + per-file content digest, sorted — the byte-identity evidence used
    throughout this module (mirrors the sibling suites' own `_tree_snapshot` helper)."""
    if not root.exists():
        return None
    items = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            items.append((rel, "SYMLINK:" + os.readlink(p)))
        elif p.is_dir():
            items.append((rel, "DIR"))
        elif p.is_file():
            items.append((rel, hashlib.sha256(p.read_bytes()).hexdigest()))
    return items


# ══════════════════════════════════════════════════════════════════════════ AC-BEPP-1a ══

def test_refusal_is_upfront_and_actionable(tmp_path, scratch_home, stub_bin):
    """The hand-tested case: no registry at all. Exit 5, the absent registry path named, and at
    least one remedy the adopter can act on — refusing BEFORE install_direnv_lib's machine-scope
    write (proven directly here by asserting the lib path stays absent; the general invariant over
    all six shapes is AC-BEPP-2's own sweep below)."""
    target = _existing_target(tmp_path)
    reg = target / ".claude" / "foundry-operators.json"

    proc = _run(["project-scaffold", str(target), "--existing", "--operator", "op_demo"],
                _env(scratch_home, stub_bin))

    assert proc.returncode == 5, proc.stderr
    assert str(reg) in proc.stderr, proc.stderr
    assert "/foundry:init" in proc.stderr, proc.stderr  # the remedy
    assert not _lib_path(scratch_home).exists()


# ══════════════════════════════════════════════════════════════════════════ AC-BEPP-1b ══
# Hostile or malformed registry CONTENT, three shapes — each must be caught by the guarded parse
# (exit 5, the path and the defect named, NO parser stack trace) rather than an existence-only `-f`
# test that lets the content through to seed_operator's json.load, which crashes AFTER the writes.

def test_registry_empty_file_refuses(tmp_path, scratch_home, stub_bin):
    """(i) an EMPTY file — the commonest corruption shape (an interrupted write, a bare `touch`)."""
    target = _existing_target(tmp_path)
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    reg = claude_dir / "foundry-operators.json"
    reg.write_text("", encoding="utf-8")

    proc = _run(["project-scaffold", str(target), "--existing", "--operator", "op_demo"],
                _env(scratch_home, stub_bin))

    assert proc.returncode == 5, proc.stderr
    assert str(reg) in proc.stderr, proc.stderr
    assert "empty" in proc.stderr.lower(), proc.stderr
    assert "traceback" not in proc.stderr.lower(), proc.stderr


def test_registry_invalid_json_refuses(tmp_path, scratch_home, stub_bin):
    """(ii) content that is not valid JSON at all — the crafted/corrupt-content case."""
    target = _existing_target(tmp_path)
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    reg = claude_dir / "foundry-operators.json"
    reg.write_text("not json{{{", encoding="utf-8")

    proc = _run(["project-scaffold", str(target), "--existing", "--operator", "op_demo"],
                _env(scratch_home, stub_bin))

    assert proc.returncode == 5, proc.stderr
    assert str(reg) in proc.stderr, proc.stderr
    assert "json" in proc.stderr.lower(), proc.stderr
    assert "traceback" not in proc.stderr.lower(), proc.stderr


def test_registry_top_level_array_refuses(tmp_path, scratch_home, stub_bin):
    """(iii) VALID JSON whose top level is not an object (an array) — the case a naive
    `json.load`-only guard still lets through: it parses, then `d.setdefault` raises on the list."""
    target = _existing_target(tmp_path)
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    reg = claude_dir / "foundry-operators.json"
    reg.write_text("[1, 2, 3]", encoding="utf-8")

    proc = _run(["project-scaffold", str(target), "--existing", "--operator", "op_demo"],
                _env(scratch_home, stub_bin))

    assert proc.returncode == 5, proc.stderr
    assert str(reg) in proc.stderr, proc.stderr
    assert "object" in proc.stderr.lower(), proc.stderr
    assert "traceback" not in proc.stderr.lower(), proc.stderr


# ══════════════════════════════════════════════════════════════════════════ AC-BEPP-1c ══

def test_registry_symlink_refuses(tmp_path, scratch_home, stub_bin):
    """A symlinked registry path — the same refusal convention install_direnv_lib and
    append_wiring_stanza already apply to a symlinked install target / .envrc."""
    target = _existing_target(tmp_path)
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("{}", encoding="utf-8")
    reg = claude_dir / "foundry-operators.json"
    reg.symlink_to(elsewhere)

    proc = _run(["project-scaffold", str(target), "--existing", "--operator", "op_demo"],
                _env(scratch_home, stub_bin))

    assert proc.returncode == 5, proc.stderr
    assert str(reg) in proc.stderr, proc.stderr
    assert "symlink" in proc.stderr.lower(), proc.stderr


def test_registry_symlink_on_clone_path_seed_operator_backstop_refuses(tmp_path, scratch_home):
    """security review R1: preflight_scaffold's [ -L ] check is scoped to --existing (the clone
    path cannot check a registry that does not exist until the clone completes) — so a hostile
    --template that ships .claude/foundry-operators.json AS A SYMLINK reaches seed_operator
    unrefused unless seed_operator carries its OWN symlink backstop. This drives the CLONE path
    (--existing OFF) via a git stub that materializes exactly that hostile shape, and asserts BOTH
    that the run refuses (exit 5, naming the path and the reason) AND that nothing was written
    through the link: the symlink itself must survive unreplaced, and the file it points at must be
    byte-identical to what it was before the run — [ -f ] follows symlinks, so a regression here
    would silently succeed and overwrite whatever the link resolves to."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_STUB)
    _make_executable(bindir / "gh", GH_NOOP_STUB)
    _make_executable(bindir / "git", GIT_CLONE_SYMLINK_STUB)

    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text('{"planted": "do-not-overwrite"}', encoding="utf-8")
    before_elsewhere = elsewhere.read_bytes()

    target = tmp_path / "cloned-target"
    env = _env(scratch_home, bindir, HOSTILE_SYMLINK_TARGET=str(elsewhere))

    proc = _run(["project-scaffold", str(target), "--operator", "op_demo"], env)

    assert proc.returncode == 5, proc.stderr
    assert "symlink" in proc.stderr.lower(), proc.stderr

    reg = target / ".claude" / "foundry-operators.json"
    assert reg.is_symlink(), "seed_operator must refuse the symlink, never replace it with a regular file"
    assert elsewhere.read_bytes() == before_elsewhere, "seed_operator must not write through the symlink"


# ══════════════════════════════════════════════════════════════════════════ AC-BEPP-1d ══

def test_existing_non_git_refuses_upfront(tmp_path, scratch_home, stub_bin):
    """`--existing` against a directory that is NOT a git repo: exit 4 UNCHANGED, the same message
    text obtain_repo already emits for this condition today, and (unlike today) no machine-scope
    write happens first."""
    target = tmp_path / "not-a-repo"
    target.mkdir()

    proc = _run(["project-scaffold", str(target), "--existing"], _env(scratch_home, stub_bin))

    assert proc.returncode == 4, proc.stderr
    assert proc.stderr == "foundry-bootstrap: --existing given but %s is not a git repo\n" % target
    assert not _lib_path(scratch_home).exists()


# ══════════════════════════════════════════════════════════════════════════ AC-BEPP-2 ══
# Every one of the six pre-write refusal shapes leaves the observed write scope byte-identical.

def _setup_refusal_shape(tmp_path, shape):
    """Materializes the target (+ registry content, where relevant) for one of AC-BEPP-2's six
    pinned pre-write refusal shapes. Returns (target, extra_cli_args, extra_watch_path).
    extra_watch_path is None for every shape except symlinked_registry, where it is the file the
    symlink POINTS AT — deliberately OUTSIDE both roots of the observed write scope (security
    review R3): a regression that wrote THROUGH the link would land there, not under $TARGET or the
    machine-scope lib path, so the sweep must watch it explicitly rather than rely on the two
    observed-scope snapshots noticing."""
    if shape == "non_git_target":
        target = tmp_path / "target"
        target.mkdir()
        return target, ["--existing"], None

    target = _existing_target(tmp_path)
    if shape == "absent_registry":
        return target, ["--existing", "--operator", "op_demo"], None

    claude_dir = target / ".claude"
    claude_dir.mkdir()
    reg = claude_dir / "foundry-operators.json"
    extra_watch = None
    if shape == "empty_file":
        reg.write_text("", encoding="utf-8")
    elif shape == "invalid_json":
        reg.write_text("not json{{{", encoding="utf-8")
    elif shape == "top_level_array":
        reg.write_text("[1, 2, 3]", encoding="utf-8")
    elif shape == "symlinked_registry":
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text("{}", encoding="utf-8")
        reg.symlink_to(elsewhere)
        extra_watch = elsewhere
    else:
        raise AssertionError("unknown refusal shape: %r" % shape)
    return target, ["--existing", "--operator", "op_demo"], extra_watch


# The count is PINNED at six (spec AC-BEPP-2) so a dropped case fails the checkpoint instead of
# shrinking it silently: absent registry, empty file, invalid JSON, top-level array, symlinked
# registry, non-git target.
WRITE_SET_SWEEP_SHAPES = [
    "absent_registry",
    "empty_file",
    "invalid_json",
    "top_level_array",
    "symlinked_registry",
    "non_git_target",
]


@pytest.mark.parametrize("shape", WRITE_SET_SWEEP_SHAPES)
def test_write_set_is_empty(tmp_path, shape):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_STUB)
    _make_executable(bindir / "gh", GH_NOOP_STUB)

    target, extra_args, extra_watch = _setup_refusal_shape(tmp_path, shape)
    lib_path = _lib_path(home)

    # AC-BEPP-2's normative verification premise: the scratch home starts with foundry.sh ABSENT.
    assert not lib_path.exists(), "fixture defect: scratch HOME must start with foundry.sh absent"

    before_target = _tree_snapshot(target)
    # security review R3: for symlinked_registry, also watch the file the link points AT -- it sits
    # OUTSIDE both roots of the observed write scope, so a regression that wrote through the link
    # would be invisible to before_target/lib_path alone.
    before_extra = extra_watch.read_bytes() if extra_watch is not None else None

    proc = _run(["project-scaffold", str(target)] + extra_args, _env(home, bindir))
    assert proc.returncode != 0, (shape, proc.stdout, proc.stderr)

    after_target = _tree_snapshot(target)
    assert after_target == before_target, (shape, "the target tree changed on a refusal")
    assert not lib_path.exists(), (shape, "the machine-scope direnv lib is no longer absent")
    if extra_watch is not None:
        after_extra = extra_watch.read_bytes()
        assert after_extra == before_extra, (
            shape, "the symlink target outside the observed write scope was written through"
        )


def test_write_set_probe_detects_a_planted_write(tmp_path):
    """The positive control on the probe itself (AC-BEPP-2's verification premise, machine-checkable
    half): plants a file under EACH root of the observed write scope and asserts the SAME
    before/after comparison `test_write_set_is_empty` uses actually reports a difference. Without
    this, a snapshot helper that silently observed nothing would make the sweep green forever."""
    home = tmp_path / "home"
    home.mkdir()
    target = _existing_target(tmp_path)
    lib_path = _lib_path(home)

    assert not lib_path.exists()
    before_target = _tree_snapshot(target)

    # Plant under root (i): the target tree.
    (target / "planted-evidence.txt").write_text("a write landed here\n", encoding="utf-8")
    after_target_planted = _tree_snapshot(target)
    assert after_target_planted != before_target, "the target-tree snapshot never changes"

    # Plant under root (ii): the machine-scope direnv-lib path.
    lib_path.parent.mkdir(parents=True, exist_ok=True)
    lib_path.write_text("# planted\n", encoding="utf-8")
    assert lib_path.exists(), "the machine-scope lib-path probe never changes"


# ══════════════════════════════════════════════════════════════════════════ AC-BEPP-5 ══
# AC-BEPP-4's shape (the die helper prints ONLY its message argument; the exit code no longer joins
# the text) pinned by a shipped test, at two call sites carrying different exit codes.

def test_die_message_shape(tmp_path):
    target = tmp_path / "w"

    proc1 = subprocess.run([BASH_BIN, str(BOOTSTRAP), str(target), "--bogus"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=15)
    assert proc1.returncode == 2
    assert proc1.stderr == "foundry-bootstrap: unknown option: --bogus\n"

    proc2 = subprocess.run([BASH_BIN, str(BOOTSTRAP), str(target), "--ref", "bad..ref"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=15)
    assert proc2.returncode == 6
    assert proc2.stderr.count("\n") == 1
    assert proc2.stderr.endswith("bad..ref\n")
    assert not proc2.stderr.rstrip("\n").endswith(" 6"), (
        "the exit code must not be joined into the printed message: %r" % proc2.stderr
    )
