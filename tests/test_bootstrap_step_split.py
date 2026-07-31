"""tests/test_bootstrap_step_split.py — hermetic behavioral coverage for the toolchain-install /
project-scaffold split in scripts/foundry-bootstrap.sh
(feat-foundry-bootstrap-toolchain-scaffold-split, AC-BTSS-1..12).

Every test drives the REAL shipped script (never a re-implementation of its logic in Python) via
subprocess, against a stubbed `claude` (and, where needed, a no-op `gh`/passthrough-or-clone-
emulating `git`) placed first on PATH. The `claude` stub records every invocation's argv (one JSON
line per call) to $STUB_RECORD_FILE and answers the `plugin list --json` toolchain-presence probe
per a handful of env-var-selected modes (ok / exit_nonzero / empty / garbage / sleep), mirroring
the shape `claude plugin list --json` actually returns (an array of `{"id": "<name>@<marketplace-
short-name>"}` objects — verified against the real installed CLI while writing the probe, per the
spec's Clarifications).

HERMETICITY (AC-BTSS-11) is a safety property here, not hygiene: the driven script runs
`git config --global` (inside `seed_commit_identity`, fired only by `--gh-account`), so every test
redirects both HOME and GIT_CONFIG_GLOBAL into a per-test tmp_path — this suite can never touch the
operator's real global git config. Every child environment is built from `scrubbed_env()`, which
strips every `GH_`/`GITHUB_`-prefixed variable from the test process's own environment first, so an
unscrubbed ambient token can never leak into a driven invocation (mirrors the two frozen identity
suites' own convention).

Why most scaffold-step tests use `--existing` over a plain directory (not a real `git init`): the
shipped `obtain_repo`/`seed_commit_identity` only require `$TARGET/.git` to be a DIRECTORY — they
never validate that it is a genuine git repository — so a bare `mkdir .git` is sufficient and keeps
these tests independent of the git binary's own repo-format concerns. The three AC-BTSS-5 tests that
must exercise the NON-`--existing` (fresh clone) path use a dedicated `git` stub
(`GIT_STUB_CLONE_OR_REAL`) that emulates `git clone` (materializing a target directory plus a seed
`.claude/foundry-operators.json`, mirroring a real template clone) and passes every OTHER git
subcommand through to the real git binary (resolved once, before any PATH override) — so
`seed_commit_identity`'s real `git config` calls still behave exactly as shipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "foundry-bootstrap.sh"
BASH_BIN = shutil.which("bash") or "/bin/bash"
REAL_GIT = shutil.which("git")

DEFAULT_MARKETPLACE = "lukasrepublic/agentic-foundry"
DEFAULT_MARKETPLACE_SHORT = DEFAULT_MARKETPLACE.rsplit("/", 1)[-1]

# The stubbed `claude`: records every invocation's argv (+ whether $STUB_TARGET_CHECK exists at
# call time, when set — the AC-BTSS-5 ordering evidence) to $STUB_RECORD_FILE, then answers
# `plugin marketplace add` / `plugin install` via $STUB_INSTALL_EXIT (default 0) and
# `plugin list --json` (the toolchain-presence probe) per $STUB_LIST_MODE.
CLAUDE_STUB = """#!/usr/bin/env python3
import json
import os
import sys
import time

argv = sys.argv[1:]
record_path = os.environ.get("STUB_RECORD_FILE")
if record_path:
    entry = {"argv": argv}
    check_path = os.environ.get("STUB_TARGET_CHECK")
    if check_path:
        entry["target_exists"] = os.path.exists(check_path)
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\\n")

is_list = len(argv) >= 2 and argv[0] == "plugin" and argv[1] == "list"
if is_list:
    mode = os.environ.get("STUB_LIST_MODE", "ok")
    if mode == "sleep":
        time.sleep(float(os.environ.get("STUB_LIST_SLEEP", "30")))
        mode = "ok"
    if mode == "exit_nonzero":
        sys.exit(int(os.environ.get("STUB_LIST_EXIT", "1")))
    if mode == "empty":
        sys.exit(0)
    if mode == "garbage":
        sys.stdout.write("not-json-garbage {{{")
        sys.exit(0)
    sys.stdout.write(os.environ.get("STUB_LIST_JSON", "[]"))
    sys.exit(0)

sys.exit(int(os.environ.get("STUB_INSTALL_EXIT", "0")))
"""

# A harmless `gh`: present on PATH (so preflight's `command -v gh` succeeds when --gh-account is
# given) but never meant to be invoked in these tests -- every test that wires commit-identity
# passes --git-author, which bypasses the gh probe entirely (resolve_declared_identity).
GH_NOOP_STUB = """#!/usr/bin/env python3
import sys
sys.exit(0)
"""

# Used only by the hermeticity tests: ANY invocation records (to the SAME record file `claude`
# writes to, distinguished by "cmd") and exits non-zero, so the hermeticity assertion is simply
# "the record file was never created".
CLAUDE_OR_GH_FAIL_STUB = """#!/usr/bin/env python3
import json
import os
import sys

record_path = os.environ.get("STUB_RECORD_FILE")
if record_path:
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"cmd": os.path.basename(sys.argv[0]), "argv": sys.argv[1:]}) + "\\n")
sys.exit(1)
"""

# Used only by the hermeticity tests: records argv, always exits 0 -- lets the assertion be "every
# recorded git invocation is a read-only config --get-family read" instead of "git was never
# called" (the shipped dry-run path legitimately runs `git config --global --get-all`).
GIT_STUB_RECORD = """#!/usr/bin/env python3
import json
import os
import sys

record_path = os.environ.get("STUB_GIT_RECORD_FILE")
if record_path:
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
sys.exit(0)
"""

# Used only by the three AC-BTSS-5 fresh-clone tests: emulates `git clone <url> <dest>` (creates
# <dest>/.git/ plus a seed .claude/foundry-operators.json, mirroring a real template clone closely
# enough for seed_operator to succeed) and passes every OTHER git subcommand through to the REAL
# git binary (resolved before any PATH override, via $STUB_REAL_GIT) -- so seed_commit_identity's
# real `git config` calls still behave exactly as shipped.
GIT_STUB_CLONE_OR_REAL = """#!/usr/bin/env python3
import json
import os
import subprocess
import sys

argv = sys.argv[1:]
record_path = os.environ.get("STUB_GIT_RECORD_FILE")
if record_path:
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv}) + "\\n")

if argv and argv[0] == "clone":
    # feat-foundry-bootstrap-install-pin (GP-2.9 item 1, merged after this atom): the shipped
    # clone now appends trailing `-c <key>=<value>` pairs after the destination (anonymous-https
    # credential-helper/askpass disabling), so the destination is no longer reliably argv[-1] --
    # it is the LAST positional argument once every "-c VALUE" pair is filtered out.
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
    with open(os.path.join(claude_dir, "foundry-operators.json"), "w", encoding="utf-8") as fh:
        fh.write('{"operators": {"op_example": {"name": "", "github": "", "added_at": ""}}}\\n')
    sys.exit(0)

real_git = os.environ.get("STUB_REAL_GIT")
if not real_git:
    sys.exit(1)
sys.exit(subprocess.call([real_git] + argv))
"""


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def scrubbed_env():
    """A copy of the test process's own environment with every GH_- and GITHUB_-prefixed variable
    removed (mirrors the frozen identity suites' own convention) -- the single helper every child
    environment in this module is built from."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def stub_bin(tmp_path):
    """claude (recording + configurable) + a harmless no-op gh. The default PATH for most tests:
    prepended before the REAL system PATH, so real `git`/`python3` remain reachable for
    obtain_repo's `--existing` path and seed_commit_identity's real git-config writes."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_STUB)
    _make_executable(bindir / "gh", GH_NOOP_STUB)
    return bindir


@pytest.fixture()
def clone_stub_bin(tmp_path):
    bindir = tmp_path / "clonebin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_STUB)
    _make_executable(bindir / "gh", GH_NOOP_STUB)
    _make_executable(bindir / "git", GIT_STUB_CLONE_OR_REAL)
    return bindir


@pytest.fixture()
def hermetic_stub_bin(tmp_path):
    bindir = tmp_path / "hermeticbin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_OR_GH_FAIL_STUB)
    _make_executable(bindir / "gh", CLAUDE_OR_GH_FAIL_STUB)
    _make_executable(bindir / "git", GIT_STUB_RECORD)
    return bindir


def _env(home, stub_bin, **extra):
    env = scrubbed_env()
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["PATH"] = "%s:%s" % (stub_bin, env["PATH"])
    env.update(extra)
    return env


def _run(args, env, cwd=None, timeout=30, stdin=subprocess.DEVNULL):
    cmd = [BASH_BIN, str(BOOTSTRAP)] + list(args)
    return subprocess.run(cmd, env=env, cwd=str(cwd) if cwd else str(REPO_ROOT), stdin=stdin,
                           capture_output=True, text=True, timeout=timeout)


def _read_records(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _tree_snapshot(root: Path):
    """Recursive path listing + per-file content digest, sorted -- the byte-identity evidence used
    throughout this module."""
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


def _existing_target(tmp_path, name="existing-target"):
    """A target directory that satisfies obtain_repo's/seed_commit_identity's `.git` DIRECTORY
    check without needing a genuine git repository (see module docstring)."""
    t = tmp_path / name
    (t / ".git").mkdir(parents=True)
    return t


def _list_json(entries):
    return json.dumps(entries)


def _positive_entry(short=DEFAULT_MARKETPLACE_SHORT):
    return {"id": "foundry@%s" % short}


def _name_only_entry():
    return {"id": "foundry"}


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-1 ══

def test_toolchain_step_alone_runs_both_install_operations_and_nothing_else(tmp_path, home, stub_bin):
    # NOTE (feat-foundry-bootstrap-install-pin, GP-2.9 item 1, merged after this atom): the
    # plugin-install step now names an explicit ref on every `marketplace add` invocation — the
    # pinned source argument is `<marketplace>#<resolved ref>`, never the bare marketplace value.
    # The expected ref is read from the shipped constant (never a literal here) so this assertion
    # tracks whichever ref the release process has pinned, rather than freezing one.
    ref_match = re.search(r'^DEFAULT_MARKETPLACE_REF="([^"]*)"', BOOTSTRAP.read_text(encoding="utf-8"), re.MULTILINE)
    assert ref_match, "DEFAULT_MARKETPLACE_REF not found in scripts/foundry-bootstrap.sh"
    pinned_ref = ref_match.group(1)

    record_file = tmp_path / "claude-record.jsonl"
    marketplace = "acme-org/nonstandard-marketplace"
    env = _env(home, stub_bin, STUB_RECORD_FILE=str(record_file))
    proc = _run(["toolchain-install", "--marketplace", marketplace], env)
    assert proc.returncode == 0, proc.stderr

    records = _read_records(record_file)
    pinned_marketplace = "%s#%s" % (marketplace, pinned_ref)
    marketplace_adds = [r for r in records if r["argv"] == ["plugin", "marketplace", "add", pinned_marketplace]]
    installs = [r for r in records if r["argv"] == ["plugin", "install", "foundry@nonstandard-marketplace"]]
    assert len(marketplace_adds) == 1, records
    assert len(installs) == 1, records
    # a hard-coded default marketplace could never produce this suffix
    assert installs[0]["argv"][1] == "install"
    assert "foundry@nonstandard-marketplace" in installs[0]["argv"]
    assert len(records) == 2, "no other claude invocation should have been recorded: %r" % records


def test_toolchain_step_requires_no_target_argument(tmp_path, home, stub_bin):
    record_file = tmp_path / "claude-record.jsonl"
    env = _env(home, stub_bin, STUB_RECORD_FILE=str(record_file))
    proc = _run(["toolchain-install"], env)
    assert proc.returncode == 0, proc.stderr  # in particular NOT the usage exit 2


def test_toolchain_step_needs_only_the_claude_cli(tmp_path, home, stub_bin):
    record_file = tmp_path / "claude-record.jsonl"
    env = _env(home, stub_bin, STUB_RECORD_FILE=str(record_file))
    # PATH carries ONLY the claude stub (plus python3's own directory, needed to exec the STUB
    # ITSELF via its `#!/usr/bin/env python3` shebang -- not something foundry-bootstrap.sh calls)
    # -- no git, no gh, nothing else.
    claude_only = tmp_path / "claude-only-bin"
    claude_only.mkdir()
    _make_executable(claude_only / "claude", CLAUDE_STUB)
    python3_dir = os.path.dirname(shutil.which("python3"))
    env["PATH"] = "%s:%s" % (claude_only, python3_dir)
    proc = _run(["toolchain-install"], env)
    assert proc.returncode == 0, proc.stderr
    assert len(_read_records(record_file)) == 2


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-2 ══

def test_toolchain_step_modifies_no_project_path_and_exits_zero(tmp_path, home, stub_bin):
    pristine = tmp_path / "pristine-cwd"
    pristine.mkdir()
    (pristine / "keep.txt").write_text("untouched\n", encoding="utf-8")
    target_shaped = tmp_path / "target-shaped-sibling"
    target_shaped.mkdir()
    (target_shaped / "other.txt").write_text("also untouched\n", encoding="utf-8")

    before_pristine = _tree_snapshot(pristine)
    before_sibling = _tree_snapshot(target_shaped)

    env = _env(home, stub_bin, STUB_RECORD_FILE=str(tmp_path / "claude-record.jsonl"))
    proc = _run(["toolchain-install"], env, cwd=pristine)

    assert proc.returncode == 0, proc.stderr  # matters: an immediate error also leaves trees unchanged
    assert _tree_snapshot(pristine) == before_pristine
    assert _tree_snapshot(target_shaped) == before_sibling


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-3 ══

def test_scaffold_step_alone_performs_project_writes(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    record_file = tmp_path / "claude-record.jsonl"
    env = _env(home, stub_bin,
               STUB_RECORD_FILE=str(record_file),
               STUB_LIST_MODE="ok",
               STUB_LIST_JSON=_list_json([_positive_entry()]))
    proc = _run(["project-scaffold", str(target), "--existing",
                 "--gh-account", "demoacct", "--git-author", "Dev Tester <dev@example.com>"], env)
    assert proc.returncode == 0, proc.stderr

    assert (target / ".claude" / "gh-identity").read_text(encoding="utf-8").strip() == "demoacct"
    assert (target / ".envrc").exists()
    inc = home / ".config" / "git" / "identity-demoacct"
    assert inc.exists()
    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert "FOUNDRY-RUNTIME-GITIGNORE-BEGIN" in gitignore


def test_scaffold_step_alone_records_no_install_operation(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    record_file = tmp_path / "claude-record.jsonl"
    env = _env(home, stub_bin,
               STUB_RECORD_FILE=str(record_file),
               STUB_LIST_MODE="ok",
               STUB_LIST_JSON=_list_json([_positive_entry()]))
    proc = _run(["project-scaffold", str(target), "--existing"], env)
    assert proc.returncode == 0, proc.stderr

    records = _read_records(record_file)
    assert not any(r["argv"][:3] == ["plugin", "marketplace", "add"] for r in records), records
    assert not any(r["argv"][:2] == ["plugin", "install"] for r in records), records


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-4a ══

def test_scaffold_refuses_before_any_write_when_toolchain_absent(tmp_path, home, stub_bin):
    target = tmp_path / "nonexistent-target"
    env = _env(home, stub_bin, STUB_LIST_MODE="exit_nonzero")
    proc = _run(["project-scaffold", str(target)], env)
    assert proc.returncode != 0
    assert not target.exists()


def test_scaffold_refuses_on_existing_target_without_touching_it(tmp_path, home, stub_bin):
    target = tmp_path / "existing-repo"
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    (target / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    before = _tree_snapshot(target)

    env = _env(home, stub_bin, STUB_LIST_MODE="exit_nonzero")
    proc = _run(["project-scaffold", str(target), "--existing"], env)
    assert proc.returncode != 0
    assert _tree_snapshot(target) == before
    assert not (target / ".claude").exists()
    assert not (target / ".envrc").exists()


def test_scaffold_refusal_message_names_the_toolchain_step(tmp_path, home, stub_bin):
    target = tmp_path / "nonexistent-target"
    env = _env(home, stub_bin, STUB_LIST_MODE="exit_nonzero")
    proc = _run(["project-scaffold", str(target)], env)
    assert proc.returncode != 0
    assert "toolchain-install" in proc.stderr, proc.stderr


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-4b ══

@pytest.mark.parametrize("case,list_mode,list_json,list_exit", [
    ("non_zero_exit", "exit_nonzero", "", "1"),
    ("empty_output", "empty", "", "0"),
    ("unparseable_output", "garbage", "", "0"),
])
def test_indeterminate_probe_answer_is_treated_as_absent(tmp_path, home, stub_bin, case, list_mode, list_json, list_exit):
    target = tmp_path / ("nonexistent-target-%s" % case)
    env = _env(home, stub_bin, STUB_LIST_MODE=list_mode, STUB_LIST_JSON=list_json, STUB_LIST_EXIT=list_exit)
    proc = _run(["project-scaffold", str(target)], env)
    assert proc.returncode != 0, "case=%s should refuse" % case
    assert not target.exists()


def test_hanging_probe_times_out_and_is_treated_as_absent(tmp_path, home, stub_bin):
    target = tmp_path / "nonexistent-target"
    env = _env(home, stub_bin, STUB_LIST_MODE="sleep", STUB_LIST_SLEEP="30")
    start = time.monotonic()
    proc = _run(["project-scaffold", str(target)], env, timeout=25)
    elapsed = time.monotonic() - start
    assert proc.returncode != 0
    assert not target.exists()
    assert elapsed < 20, "the probe's <=15s bound was not honoured (took %.1fs)" % elapsed


def test_a_foreign_marketplace_foundry_plugin_does_not_affirm(tmp_path, home, stub_bin):
    target = tmp_path / "nonexistent-target"
    env = _env(home, stub_bin, STUB_LIST_MODE="ok",
               STUB_LIST_JSON=_list_json([_positive_entry(short="some-other-marketplace")]))
    proc = _run(["project-scaffold", str(target)], env)
    assert proc.returncode != 0
    assert not target.exists()


def test_affirmation_does_not_survive_across_invocations(tmp_path, home, stub_bin):
    # Run 1: a successful toolchain-only invocation (source (A), established in THAT process).
    env1 = _env(home, stub_bin, STUB_RECORD_FILE=str(tmp_path / "claude-record-1.jsonl"))
    proc1 = _run(["toolchain-install"], env1)
    assert proc1.returncode == 0, proc1.stderr

    # Run 2: a SEPARATE, later scaffold-only invocation whose stub reports absent.
    target = tmp_path / "nonexistent-target"
    env2 = _env(home, stub_bin, STUB_LIST_MODE="exit_nonzero")
    proc2 = _run(["project-scaffold", str(target)], env2)
    assert proc2.returncode != 0
    assert not target.exists()


def test_no_environment_bypass_affirms_the_toolchain(tmp_path, home, stub_bin):
    target = tmp_path / "nonexistent-target"
    env = _env(home, stub_bin, STUB_LIST_MODE="exit_nonzero",
               FOUNDRY_TOOLCHAIN_PRESENT="1", FOUNDRY_SKIP_TOOLCHAIN_CHECK="1", FOUNDRY_ASSUME_TOOLCHAIN="1")
    proc = _run(["project-scaffold", str(target)], env)
    assert proc.returncode != 0
    assert not target.exists()


def test_name_only_inventory_affirms_but_warns(tmp_path, home, stub_bin):
    target = _existing_target(tmp_path)
    env = _env(home, stub_bin, STUB_LIST_MODE="ok", STUB_LIST_JSON=_list_json([_name_only_entry()]))
    proc = _run(["project-scaffold", str(target), "--existing"], env)
    assert proc.returncode == 0, proc.stderr
    assert DEFAULT_MARKETPLACE in proc.stderr
    assert "name-only" in proc.stderr or "name only" in proc.stderr.lower()
    assert "FOUNDRY-RUNTIME-GITIGNORE-BEGIN" in (target / ".gitignore").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-5 ══

def test_combined_form_runs_toolchain_then_scaffold(tmp_path, home, clone_stub_bin):
    target = tmp_path / "fresh-target"
    record_file = tmp_path / "claude-record.jsonl"
    env = _env(home, clone_stub_bin,
               STUB_RECORD_FILE=str(record_file),
               STUB_TARGET_CHECK=str(target),
               STUB_REAL_GIT=REAL_GIT)
    proc = _run([str(target), "--marketplace", DEFAULT_MARKETPLACE], env)
    assert proc.returncode == 0, proc.stderr

    records = _read_records(record_file)
    installs = [r for r in records if r["argv"][:2] == ["plugin", "marketplace"] or r["argv"][:2] == ["plugin", "install"]]
    assert len(installs) == 2, records
    for r in installs:
        assert r.get("target_exists") is False, "the target existed before install completed: %r" % r
    assert target.exists()  # the clone (via the stub) DID eventually happen


def test_combined_form_failed_install_leaves_no_project_dir(tmp_path, home, clone_stub_bin):
    target = tmp_path / "fresh-target"
    env = _env(home, clone_stub_bin, STUB_INSTALL_EXIT="7", STUB_REAL_GIT=REAL_GIT)
    proc = _run([str(target)], env)
    assert proc.returncode != 0
    assert not target.exists()


def test_combined_form_end_state_equals_the_two_step_sequence(tmp_path, home, clone_stub_bin):
    home_a = tmp_path / "home-a"; home_a.mkdir()
    home_b = tmp_path / "home-b"; home_b.mkdir()
    target_a = tmp_path / "target-combined"
    target_b = tmp_path / "target-two-step"

    common_opts = ["--operator", "op_demo", "--gh-account", "demoacct",
                   "--git-author", "Combo Tester <combo@example.com>"]

    env_a = _env(home_a, clone_stub_bin, STUB_REAL_GIT=REAL_GIT)
    proc_a = _run([str(target_a)] + common_opts, env_a)
    assert proc_a.returncode == 0, proc_a.stderr

    env_b1 = _env(home_b, clone_stub_bin, STUB_REAL_GIT=REAL_GIT)
    proc_b1 = _run(["toolchain-install"], env_b1)
    assert proc_b1.returncode == 0, proc_b1.stderr
    # A SEPARATE process: source (A) does not cross invocations (AC-BTSS-4b(f)), so this second
    # step must affirm independently, via a positive probe answer (source (B)).
    env_b2 = _env(home_b, clone_stub_bin, STUB_REAL_GIT=REAL_GIT,
                  STUB_LIST_MODE="ok", STUB_LIST_JSON=_list_json([_positive_entry()]))
    proc_b2 = _run(["project-scaffold", str(target_b)] + common_opts, env_b2)
    assert proc_b2.returncode == 0, proc_b2.stderr

    def end_state(target):
        items = []
        for p in sorted(target.rglob("*")):
            rel = p.relative_to(target)
            if rel.parts and rel.parts[0] == ".git":
                continue  # excluded: .git/ internals are not reproducible across two clones
            if p.is_dir():
                items.append((str(rel), "DIR"))
            elif p.is_file():
                items.append((str(rel), hashlib.sha256(p.read_bytes()).hexdigest()))
        return items

    def use_config_only(target):
        r = subprocess.run(["git", "config", "--file", str(target / ".git" / "config"), "user.useConfigOnly"],
                            capture_output=True, text=True)
        return r.stdout.strip()

    assert end_state(target_a) == end_state(target_b)
    assert use_config_only(target_a) == use_config_only(target_b) == "true"


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-6 ══

def test_selftest_invokes_neither_claude_nor_gh(tmp_path, home, hermetic_stub_bin):
    record_file = tmp_path / "fail-record.jsonl"
    env = _env(home, hermetic_stub_bin, STUB_RECORD_FILE=str(record_file))
    proc = _run(["--selftest"], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BOOTSTRAP-SELFTEST-GREEN" in proc.stdout
    assert not record_file.exists(), _read_records(record_file)


def test_dry_run_of_each_step_and_the_combined_form_invokes_neither_claude_nor_gh(tmp_path, home, hermetic_stub_bin):
    record_file = tmp_path / "fail-record.jsonl"
    scenarios = [
        ["toolchain-install", "--dry-run"],
        ["project-scaffold", str(tmp_path / "t-scaffold"), "--dry-run"],
        [str(tmp_path / "t-combined"), "--dry-run"],
    ]
    for args in scenarios:
        env = _env(home, hermetic_stub_bin, STUB_RECORD_FILE=str(record_file))
        proc = _run(args, env)
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
        assert not record_file.exists(), (args, _read_records(record_file))


def test_hermetic_runs_write_nothing_outside_their_own_temp_dir(tmp_path, home, hermetic_stub_bin):
    git_record = tmp_path / "git-record.jsonl"

    def snapshot_pair(target_parent):
        return (_tree_snapshot(home), _tree_snapshot(target_parent))

    # (1) --selftest: no target concept; only $HOME's byte-identity is meaningful.
    before_home = _tree_snapshot(home)
    env = _env(home, hermetic_stub_bin, STUB_GIT_RECORD_FILE=str(git_record))
    proc = _run(["--selftest"], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _tree_snapshot(home) == before_home

    # (2) toolchain-install --dry-run: no target concept either.
    before_home = _tree_snapshot(home)
    env = _env(home, hermetic_stub_bin, STUB_GIT_RECORD_FILE=str(git_record))
    proc = _run(["toolchain-install", "--dry-run"], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _tree_snapshot(home) == before_home

    # (3) project-scaffold --dry-run against an EXISTING target (so seed_commit_identity's
    #     dry-run branch actually reaches its real, read-only `git config --get-all` call) and (4)
    #     the combined form's --dry-run against a second existing target. Each target's parent is
    #     its OWN dedicated directory (never tmp_path itself, which also holds this test's own
    #     bookkeeping -- home/, hermeticbin/, the git-record file -- that legitimately keeps
    #     changing as a side effect of the driven invocation's OWN read-only git calls being
    #     recorded there; a target-parent scoped to just the target keeps the assertion honest).
    for label, args_fn in [
        ("scaffold", lambda t: ["project-scaffold", str(t), "--dry-run", "--gh-account", "demoacct"]),
        ("combined", lambda t: [str(t), "--dry-run", "--gh-account", "demoacct"]),
    ]:
        parent = tmp_path / ("target-parent-%s" % label)
        parent.mkdir()
        target = _existing_target(parent, name="target")
        before = snapshot_pair(parent)
        env = _env(home, hermetic_stub_bin, STUB_GIT_RECORD_FILE=str(git_record))
        proc = _run(args_fn(target), env)
        assert proc.returncode == 0, (label, proc.stdout, proc.stderr)
        assert snapshot_pair(parent) == before, label

    # Every git invocation recorded across ALL of the above is a read-only config --get-family read.
    for rec in _read_records(git_record):
        argv = rec["argv"]
        assert argv and argv[0] == "config", argv
        assert any(a.startswith("--get") for a in argv), argv


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-7 ══

def test_dry_run_of_each_step_writes_nothing(tmp_path, home, stub_bin):
    scaffold_target = tmp_path / "t-scaffold-dry"
    combined_target = tmp_path / "t-combined-dry"
    scenarios = [
        (["toolchain-install", "--dry-run"], None),
        (["project-scaffold", str(scaffold_target), "--dry-run"], scaffold_target),
        ([str(combined_target), "--dry-run"], combined_target),
    ]
    for args, target in scenarios:
        env = _env(home, stub_bin, STUB_RECORD_FILE=str(tmp_path / "claude-record.jsonl"))
        proc = _run(args, env)
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
        if target is not None:
            assert not target.exists(), args


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-8 ══

def test_toolchain_only_completion_message_names_the_scaffold_step(tmp_path, home, stub_bin):
    env = _env(home, stub_bin, STUB_RECORD_FILE=str(tmp_path / "claude-record.jsonl"))
    proc = _run(["toolchain-install"], env)
    assert proc.returncode == 0, proc.stderr
    assert "project-scaffold" in proc.stdout
    assert "cd " not in proc.stdout


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-9 ══

def _accepted_long_options():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("parse_args() {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    body = "\n".join(lines[start:end + 1])
    return sorted(set(re.findall(r"--[A-Za-z][A-Za-z-]*(?=[)|])", body)))


def test_help_lists_every_accepted_option(tmp_path, home, stub_bin):
    options = _accepted_long_options()
    assert options, "option discovery collapsed"
    env = _env(home, stub_bin)
    proc = _run(["--help"], env)
    assert proc.returncode == 0, proc.stderr
    for opt in options:
        assert opt in proc.stdout, "help text is missing %s" % opt


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-11 ══
# (the module-wide floor is exercised by every test above; nothing additional to add here.)


# ══════════════════════════════════════════════════════════════════════════ AC-BTSS-12 ══

def test_scaffold_plan_applies_the_runtime_gitignore_before_any_other_write(tmp_path, home, stub_bin):
    target = tmp_path / "t-order"
    env = _env(home, stub_bin, STUB_RECORD_FILE=str(tmp_path / "claude-record.jsonl"))
    proc = _run(["project-scaffold", str(target), "--dry-run", "--operator", "op_demo", "--gh-account", "demoacct"], env)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()

    def first_index(needle):
        for i, l in enumerate(lines):
            if needle in l:
                return i
        return None

    gitignore_line = first_index("foundry-apply-runtime-gitignore.sh")
    operator_line = first_index("seed operator")
    gh_identity_line = first_index("gh-identity")
    use_config_only_line = first_index("useConfigOnly")

    assert gitignore_line is not None
    assert operator_line is not None
    assert gh_identity_line is not None
    assert use_config_only_line is not None
    assert gitignore_line < operator_line
    assert gitignore_line < gh_identity_line
    assert gitignore_line < use_config_only_line


# ════════════════════════════ security-review follow-up (PR #294, R1/R3/R8a/R8b) ══
# Cheap, in-scope fixes from the mandatory security review, each with its own regression test.
# R2 (marketplace-basename scoping), R4 (no install-partial rollback) and R5 (unpinned install) are
# disclosed residuals (see CHANGELOG.md), not fixed in this atom. R6/R7 are pre-existing,
# byte-identical to main, and owned by the sibling `bootstrap-managed-block-writes` atom.

@pytest.mark.parametrize("step", ["toolchain-install", "project-scaffold"])
def test_step_name_after_an_option_is_refused_not_adopted_as_target(tmp_path, home, stub_bin, step):
    """R1: the step selector is recognized ONLY as the leading token. An option-first invocation
    naming the step name anywhere else must refuse -- not silently adopt the literal step-name
    string as <target-dir> and run the (wrong) combined form into a directory named after it."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    literal_target = cwd / step
    env = _env(home, stub_bin, STUB_RECORD_FILE=str(tmp_path / "claude-record.jsonl"))
    proc = _run(["--marketplace", "acme-org/x", step], env, cwd=cwd)
    assert proc.returncode != 0
    assert not literal_target.exists(), "the step name must never be adopted as a target directory"
    assert step in proc.stderr
    assert "must come first" in proc.stderr


def test_foreign_marketplace_precedence_over_name_only_in_mixed_inventory(tmp_path, home, stub_bin):
    """R3: a mixed inventory carrying BOTH a foreign-marketplace foundry entry AND a name-only
    foundry entry (no exact match for the declared marketplace) must refuse -- foreign-marketplace
    detection must outrank the name-only hedge, never affirm through it."""
    target = tmp_path / "nonexistent-target"
    mixed = _list_json([_positive_entry(short="some-other-marketplace"), _name_only_entry()])
    env = _env(home, stub_bin, STUB_LIST_MODE="ok", STUB_LIST_JSON=mixed)
    proc = _run(["project-scaffold", str(target)], env)
    assert proc.returncode != 0
    assert not target.exists()


def test_usage_refuses_loudly_when_its_sentinel_is_missing(tmp_path):
    """R8a: usage()'s `sed -n '2,/pat/p'` has no upper bound if the BOOTSTRAP-USAGE-END sentinel is
    absent -- a stray-deleted/renamed sentinel must fail closed (a clear internal-error refusal),
    never silently dump the whole script (function bodies included) as --help output."""
    scratch = tmp_path / "scratch-scripts"
    scratch.mkdir()
    mutant = scratch / "foundry-bootstrap.sh"
    lines = BOOTSTRAP.read_text(encoding="utf-8").splitlines(keepends=True)
    # Delete ONLY the header's own sentinel LINE (not every textual mention of the token -- the
    # guard's own grep pattern and die message also name it, and a blanket string-replace would
    # silently rewrite those too, defeating the mutation).
    header_line_idx = next(i for i, l in enumerate(lines) if l.rstrip("\n") == "# BOOTSTRAP-USAGE-END")
    del lines[header_line_idx]
    mutant.write_text("".join(lines), encoding="utf-8")
    mutant.chmod(mutant.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    proc = subprocess.run([BASH_BIN, str(mutant), "--help"], capture_output=True, text=True, timeout=15)
    assert proc.returncode != 0
    assert "sentinel" in proc.stderr.lower()
    assert "toolchain_install_step()" not in proc.stdout, (
        "a fail-open sentinel guard would have dumped the whole script as help text"
    )


def test_selftest_check_seven_guards_its_command_substitution_with_set_plus_e():
    """R8b: `out="$(...)"; rc=$?` with no `set +e` wrapper means that, under this script's own
    `set -euo pipefail`, a non-zero exit from the command substitution aborts the WHOLE selftest
    before `rc=$?` is ever reached -- the `[ $rc -eq 0 ]` conjunct reading $rc could then never
    observe a failure. Structural regression guard: the same set +e / set -e wrapper the sibling
    checks (missing-target, unknown-option) already use must bracket check 7's substitution too."""
    text = BOOTSTRAP.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines()
                if "toolchain-install --dry-run --marketplace demo/nonstandard-marketplace" in l)
    stripped = line.strip()
    assert stripped.startswith("set +e;"), line
    assert stripped.endswith("set -e"), line
    assert "rc=$?" in stripped
