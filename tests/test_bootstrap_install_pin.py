"""tests/test_bootstrap_install_pin.py — hermetic behavioral + static binding for
feat-foundry-bootstrap-install-pin (GP-2.9 item 1, AC-BIP-1..14).

WHAT THE PIN IS (read before trusting a green here). A release-tag ref is REPOINTABLE, so the
ref + the catalogue's recorded `sha` name the release the project INTENDED to publish, never an
immutable artifact reference. This module proves three things and no more: the script NAMES an
exact ref on every plugin-install invocation (dry-run AND real), the shipped constant AGREES with
`.claude-plugin/marketplace.json` (a LOCAL, hermetic comparison — never an upstream fact), and the
template clone is anonymous `https://` by default with SSH as an opt-in. It proves nothing about
whether the upstream tag still resolves to that commit — tag protection is an operator-owned
dependency, out of this atom's reach (spec, Dependencies / sequencing).

HERMETICITY. Every test is network-free. The dry-run path invokes neither `git` nor `claude` (the
shipped `run()` helper only prints a plan under `--dry-run`), so most of this module needs neither
on PATH. AC-BIP-14 alone drives the REAL (non-`--dry-run`) path, against a git repository created
with `git init` and a RECORDING `claude` stub placed first on PATH — never the network, never a
real marketplace/plugin.

NO RELEASE-TAG LITERAL. AC-BIP-3's liveness property requires both operands of the drift
comparison to be read from the shipped files at run time, parameterized by a repository root
(`shipped_pin_drift`); this module carries no hard-coded ref value anywhere, including in the
tests that assert *shape* (a regex, not a literal) or that compose an out-of-band `--ref v9.9.9`
override (a value chosen to differ from whatever the shipped constant currently is).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "foundry-bootstrap.sh"
MANIFEST_RELPATH = ".claude-plugin/marketplace.json"

DOC_FILES_EXCLUDED_DIRS = ("docs/archive/",)
DOC_FILES_EXCLUDED_NAMES = ("CHANGELOG.md",)

MARKETPLACE_ADD_RE = re.compile(r"claude plugin marketplace add")


# ─────────────────────────────────────────────────────────────── shared helpers ──

def _scrubbed_env():
    """A copy of the test process's own environment with every GH_-/GITHUB_-prefixed variable
    removed, matching the sibling bootstrap suites' convention (this repo's own `.envrc` and CI's
    default environment both export tokens/paths under those prefixes that no test here should
    observe or depend on)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _script_constant(root: Path, name: str) -> str:
    """Reads a `NAME="value"` shell constant out of the shipped bootstrap script, beneath `root`.
    Used for every operand this module needs (the pinned ref, the edge ref, the default
    marketplace) so nothing here hard-codes a value the script itself owns."""
    text = (root / "scripts" / "foundry-bootstrap.sh").read_text(encoding="utf-8")
    m = re.search(r'^%s="([^"]*)"' % re.escape(name), text, re.MULTILINE)
    assert m, "%s not found in scripts/foundry-bootstrap.sh" % name
    return m.group(1)


def _manifest_ref(root: Path) -> str:
    data = json.loads((root / MANIFEST_RELPATH).read_text(encoding="utf-8"))
    for entry in data.get("plugins", []):
        if entry.get("name") == "foundry":
            return entry["source"]["ref"]
    raise AssertionError("no plugins[] entry named 'foundry' in %s" % MANIFEST_RELPATH)


def shipped_pin_drift(root: Path):
    """The AC-BIP-3 comparison, parameterized by a repository root (Design/notes, item 2): reads
    the shipped DEFAULT_MARKETPLACE_REF constant from scripts/foundry-bootstrap.sh and the
    plugins[foundry].source.ref from .claude-plugin/marketplace.json, BOTH beneath `root`, at call
    time. Returns None when they agree; otherwise a (constant, manifest_ref) pair describing the
    drift. Neither operand is a literal in this module — a hard-coded string comparison cannot
    satisfy this function's contract, which is what the negative control below convicts."""
    constant = _script_constant(root, "DEFAULT_MARKETPLACE_REF")
    manifest_ref = _manifest_ref(root)
    if constant == manifest_ref:
        return None
    return (constant, manifest_ref)


def _dry_run(target: Path, extra_args=None):
    env = _scrubbed_env()
    env["HOME"] = str(target.parent / ("home-%s" % target.name))
    args = ["bash", str(BOOTSTRAP), str(target), "--dry-run", *(extra_args or [])]
    return subprocess.run(args, env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)


def _documented_install_instructions(root: Path):
    """Every occurrence of `claude plugin marketplace add` in a shipped Markdown file of the repo
    beneath `root`, excluding docs/archive/** (historical release notes) and CHANGELOG.md (a
    changelog entry records what that release said) — Terminology, "a documented install
    instruction". Scans the whole tree rather than a fixed file list so the non-empty lower bound
    (AC-BIP-13) is genuine: a documented install path deleted or moved elsewhere is still found."""
    instructions = []
    for path in sorted(root.rglob("*.md")):
        if any(part == ".git" for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel in DOC_FILES_EXCLUDED_NAMES:
            continue
        if any(rel.startswith(prefix) for prefix in DOC_FILES_EXCLUDED_DIRS):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if MARKETPLACE_ADD_RE.search(line):
                instructions.append((rel, line))
    return instructions


CLAUDE_RECORDING_STUB = """#!/usr/bin/env python3
import os
import sys

record_path = os.environ.get("CLAUDE_CALLS")
if record_path:
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(0)
"""


def _helper_env(tmp_path):
    """The env every non-driven-script test helper subprocess (git init) runs under: scrubbed
    (GH_-/GITHUB_-prefixed vars removed) AND with HOME/GIT_CONFIG_GLOBAL redirected into the
    per-test tmp_path, so `git init`'s own honouring of the operator's REAL global config (in
    particular `init.templateDir`) can never leak into a fixture-created repo — mirrors the
    sibling bootstrap suites' `_helper_env()` convention (tests/test_bootstrap_commit_identity.py)."""
    env = _scrubbed_env()
    env["HOME"] = str(tmp_path / "helper-home")
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "helper-home" / ".gitconfig")
    return env


@pytest.fixture()
def existing_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_helper_env(tmp_path))
    return repo


def _real_run(target: Path, extra_args, home: Path, calls_file: Path, bindir: Path):
    env = _scrubbed_env()
    env["HOME"] = str(home)
    env["PATH"] = "%s:%s" % (bindir, env["PATH"])
    env["CLAUDE_CALLS"] = str(calls_file)
    args = ["bash", str(BOOTSTRAP), str(target), "--existing", *extra_args]
    return subprocess.run(args, env=env, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)


def _read_calls(calls_file: Path):
    if not calls_file.exists():
        return []
    return [line for line in calls_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _recording_claude_stub(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "claude", CLAUDE_RECORDING_STUB)
    return bindir


# ───────────────────────────────────────────────────────────────────── AC-BIP-1 ──

def test_marketplace_add_names_an_explicit_ref(tmp_path):
    proc = _dry_run(tmp_path / "proj")
    assert proc.returncode == 0, proc.stderr
    m = re.search(r"marketplace add (\S+)", proc.stdout)
    assert m, proc.stdout
    src = m.group(1)
    assert "#" in src, "pinned source argument %r carries no ref" % src
    _, _, ref = src.partition("#")
    assert re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", ref) and ".." not in ref, \
        "resolved ref %r is outside the accepted ref grammar" % ref


# ───────────────────────────────────────────────────────────────────── AC-BIP-2 ──

def test_default_ref_is_the_pinned_release_tag(tmp_path):
    proc = _dry_run(tmp_path / "proj")
    assert proc.returncode == 0, proc.stderr
    ref = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    assert ("marketplace add %s#%s" % (marketplace, ref)) in proc.stdout
    assert re.search(r"channel.*stable", proc.stdout)
    assert "UNSTABLE" not in proc.stdout


# ───────────────────────────────────────────────────────────────────── AC-BIP-3 ──

def test_shipped_pin_matches_marketplace_manifest_ref():
    drift = shipped_pin_drift(REPO_ROOT)
    assert drift is None, "shipped constant drifted from the release manifest: %r" % (drift,)


def test_manifest_ref_mutation_convicts_the_shipped_pin(tmp_path):
    """The mutation negative control (Design/notes, item 2): copies the two files this atom binds
    into a temporary root, rewrites the manifest's ref, and asserts the comparison reports drift.
    A hard-coded string comparison cannot pass this — it would report agreement regardless of what
    the temporary manifest says."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".claude-plugin").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "foundry-bootstrap.sh", tmp_path / "scripts" / "foundry-bootstrap.sh")
    manifest = json.loads((REPO_ROOT / MANIFEST_RELPATH).read_text(encoding="utf-8"))
    for entry in manifest.get("plugins", []):
        if entry.get("name") == "foundry":
            entry["source"]["ref"] = entry["source"]["ref"] + "-mutated-for-the-negative-control"
    (tmp_path / MANIFEST_RELPATH).write_text(json.dumps(manifest), encoding="utf-8")

    drift = shipped_pin_drift(tmp_path)
    assert drift is not None, "the mutated manifest ref was not detected as drift"
    constant, manifest_ref = drift
    assert constant != manifest_ref
    assert manifest_ref.endswith("-mutated-for-the-negative-control")


def test_shipped_pin_is_a_semver_release_tag():
    constant = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    assert re.match(r"^v[0-9]+[.][0-9]+[.][0-9]+$", constant), \
        "shipped pinned default ref %r is not a v<major>.<minor>.<patch> tag" % constant


# ───────────────────────────────────────────────────────────────────── AC-BIP-4 ──

def test_explicit_ref_flag_overrides_the_channel_default(tmp_path):
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    proc = _dry_run(tmp_path / "proj", ["--ref", "v9.9.9-explicit", "--channel", "edge"])
    assert proc.returncode == 0, proc.stderr
    assert ("marketplace add %s#v9.9.9-explicit" % marketplace) in proc.stdout
    assert re.search(r"channel.*explicit", proc.stdout)
    # an explicit --ref does not select the edge channel's UNSTABLE warning, even though
    # --channel edge was also supplied in this invocation.
    assert "UNSTABLE" not in proc.stdout


# ───────────────────────────────────────────────────────────────────── AC-BIP-5 ──

def test_unusable_pin_selector_fails_closed(tmp_path):
    cases = [
        ["--ref", "bad ref"],
        ["--ref", "..bad"],
        ["--ref", "#bad"],
        ["--ref", "v0.26.1\nanything"],  # security-review Risk 1: a newline-embedded ref must be
                                          # refused, not slipped past the line-anchored grep by its
                                          # first line alone (see is_accepted_ref's newline guard).
        ["--channel", "Stable"],
        ["--channel", "STABLE"],
        ["--channel", "Edge"],
        ["--marketplace", "acme/x#evil"],
    ]
    for i, args in enumerate(cases):
        target = tmp_path / ("proj-%d" % i)
        env = _scrubbed_env()
        env["HOME"] = str(tmp_path / ("home-%d" % i))
        proc = subprocess.run(["bash", str(BOOTSTRAP), str(target), *args],
                               env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
        assert proc.returncode != 0, "case %r unexpectedly succeeded: %s" % (args, proc.stdout)
        assert not target.exists(), "case %r left a target directory behind" % (args,)


def test_newline_embedded_ref_is_refused_with_no_claude_call(tmp_path, existing_repo):
    """The real-path half of the newline-embedded-ref regression (security-review Risk 1):
    exits non-zero AND records no claude invocation at all — a forged extra plan/disclosure line
    is meaningless if the script never even reaches the point of composing one, but this proves
    the fail-closed path holds on the path adopters actually run, not only under --dry-run."""
    bindir = _recording_claude_stub(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    calls_file = tmp_path / "calls.log"

    proc = _real_run(existing_repo, ["--ref", "v0.26.1\nanything"], home, calls_file, bindir)
    assert proc.returncode != 0

    calls = _read_calls(calls_file)
    assert not calls, "a newline-embedded ref must record no claude invocation: %r" % (calls,)


# ───────────────────────────────────────────────────────────────────── AC-BIP-6 ──

def test_edge_channel_is_opt_in_and_warns_unstable(tmp_path):
    edge_ref = _script_constant(REPO_ROOT, "EDGE_MARKETPLACE_REF")
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")

    edge_proc = _dry_run(tmp_path / "edge", ["--channel", "edge"])
    assert edge_proc.returncode == 0, edge_proc.stderr
    assert ("marketplace add %s#%s" % (marketplace, edge_ref)) in edge_proc.stdout
    assert "UNSTABLE" in edge_proc.stderr
    assert edge_ref in edge_proc.stderr
    assert re.search(r"channel.*edge", edge_proc.stdout)

    stable_proc = _dry_run(tmp_path / "stable")
    assert stable_proc.returncode == 0, stable_proc.stderr
    assert "UNSTABLE" not in stable_proc.stderr


# ───────────────────────────────────────────────────────────────────── AC-BIP-7 ──

def test_plugin_install_target_carries_no_ref(tmp_path):
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    name = marketplace.rsplit("/", 1)[-1]
    proc = _dry_run(tmp_path / "proj", ["--channel", "edge"])
    assert proc.returncode == 0, proc.stderr
    m = re.search(r"plugin install (\S+)", proc.stdout)
    assert m, proc.stdout
    install_target = m.group(1)
    assert install_target == "foundry@%s" % name
    assert "#" not in install_target


# ───────────────────────────────────────────────────────────────────── AC-BIP-8 ──

def test_template_clone_defaults_to_anonymous_https(tmp_path):
    proc = _dry_run(tmp_path / "proj")
    assert proc.returncode == 0, proc.stderr
    assert "https://github.com/" in proc.stdout
    assert "GIT_TERMINAL_PROMPT=0" in proc.stdout
    assert "git@github.com:" not in proc.stdout
    assert not (tmp_path / "proj").exists()

    # security-review Risk 2: GIT_TERMINAL_PROMPT=0 bounds only git's OWN terminal prompt — a
    # configured credential helper (osxkeychain/libsecret/store/gh's own helper) would otherwise
    # still silently attach the adopter's ambient GitHub token, and GIT_ASKPASS/core.askPass could
    # still pop a blocking prompt. The composed clone argv must disable both classes for THIS
    # invocation, so "anonymous https" is an enforced claim, not merely an unauthenticated-looking
    # transport scheme.
    assert "-c credential.helper=" in proc.stdout
    assert "-c core.askPass=" in proc.stdout
    # both -c overrides must land on the SAME clone plan line as the URL, and must not disturb the
    # frozen AC-BIP-8 checkpoint locator's "git clone https://github.com/" literal adjacency.
    clone_line = next(line for line in proc.stdout.splitlines() if "git clone" in line)
    assert "https://github.com/" in clone_line
    assert "-c credential.helper=" in clone_line
    assert "-c core.askPass=" in clone_line
    assert re.search(r"git clone https://github\.com/", clone_line), clone_line


# ───────────────────────────────────────────────────────────────────── AC-BIP-9 ──

def test_template_ssh_flag_selects_the_ssh_form(tmp_path):
    proc = _dry_run(tmp_path / "proj", ["--template-ssh"])
    assert proc.returncode == 0, proc.stderr
    assert "git@github.com:" in proc.stdout
    assert "https://github.com/" not in proc.stdout


# ──────────────────────────────────────────────────────────────────── AC-BIP-10 ──

def test_dry_run_discloses_the_resolved_pin(tmp_path):
    target = tmp_path / "proj"
    proc = _dry_run(target)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    ref = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")

    assert re.search(r"channel.*stable", out)                       # (a)
    assert ref in out                                                 # (b)
    assert ("%s#%s" % (marketplace, ref)) in out                      # (c)
    assert "https://github.com/" in out                                # (d)
    assert not target.exists()                                         # (e) no fs change
    # (e) no claude invocation: the process ran with no `claude` on the (scrubbed, real) PATH and
    # still exited 0 with a full plan — a real invocation attempt would have failed loudly instead.


# ──────────────────────────────────────────────────────────────────── AC-BIP-11 ──

def test_selftest_asserts_the_pinned_plan():
    proc = subprocess.run(["bash", str(BOOTSTRAP), "--selftest"], env=_scrubbed_env(),
                           cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BOOTSTRAP-SELFTEST-GREEN" in proc.stdout
    assert re.search(r"\[ok\].*pinned marketplace ref", proc.stdout)
    assert re.search(r"\[ok\].*edge channel", proc.stdout)
    assert re.search(r"\[ok\].*https template clone", proc.stdout)


# ──────────────────────────────────────────────────────────────────── AC-BIP-12 ──

def test_help_documents_every_option():
    proc = subprocess.run(["bash", str(BOOTSTRAP), "--help"], env=_scrubbed_env(),
                           cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for token in ("--ref", "--channel", "--template-ssh"):
        assert token in out, "%s missing from --help output" % token
    assert re.search(r"pinned.*release tag", out, re.IGNORECASE)
    assert re.search(r"edge", out, re.IGNORECASE)
    assert re.search(r"unstable", out, re.IGNORECASE)
    assert re.search(r"unpinned", out, re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────── AC-BIP-13 ──

def test_documented_install_commands_name_the_shipped_pin():
    instructions = _documented_install_instructions(REPO_ROOT)
    assert instructions, "no documented install instructions found (must be non-empty)"
    ref = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    for rel, line in instructions:
        assert re.search(r"#%s\b" % re.escape(ref), line), \
            "%s: %r does not name the shipped pinned ref %s" % (rel, line, ref)


# ──────────────────────────────────────────────────────────────────── AC-BIP-14 ──

def test_real_path_records_the_pinned_marketplace_add(tmp_path, existing_repo):
    bindir = _recording_claude_stub(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    calls_file = tmp_path / "calls.log"

    proc = _real_run(existing_repo, [], home, calls_file, bindir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    calls = _read_calls(calls_file)
    assert calls, "the recording claude stub was never invoked (absent/empty recording is a FAILURE)"

    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    ref = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    expected = "plugin marketplace add %s#%s" % (marketplace, ref)
    assert expected in calls, calls


def test_real_path_edge_warns_unstable(tmp_path, existing_repo):
    bindir = _recording_claude_stub(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    calls_file = tmp_path / "calls.log"

    proc = _real_run(existing_repo, ["--channel", "edge"], home, calls_file, bindir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNSTABLE" in proc.stderr

    calls = _read_calls(calls_file)
    assert calls, "the recording claude stub was never invoked (absent/empty recording is a FAILURE)"

    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    edge_ref = _script_constant(REPO_ROOT, "EDGE_MARKETPLACE_REF")
    expected = "plugin marketplace add %s#%s" % (marketplace, edge_ref)
    assert expected in calls, calls


def test_real_path_fail_closed_records_no_claude_call(tmp_path, existing_repo):
    bindir = _recording_claude_stub(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    calls_file = tmp_path / "calls.log"

    proc = _real_run(existing_repo, ["--channel", "BOGUS"], home, calls_file, bindir)
    assert proc.returncode != 0

    calls = _read_calls(calls_file)
    assert not calls, "an unusable pin selector must record no claude invocation: %r" % (calls,)
