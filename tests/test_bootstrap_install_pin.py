"""tests/test_bootstrap_install_pin.py — hermetic behavioral + static binding for
feat-foundry-bootstrap-install-pin (GP-2.9 item 1, AC-BIP-1..14) AND, since
feat-foundry-installer-unpinning, the AC-IUP-1/2/6 de-tagging of the marketplace REGISTRATION.

WHAT THE PIN IS (read before trusting a green here). A release-tag ref is REPOINTABLE, so the
ref + the catalogue's recorded `sha` name the release the project INTENDED to publish, never an
immutable artifact reference. As of feat-foundry-installer-unpinning, the shell installer's DEFAULT
registration is deliberately TAGLESS (AC-IUP-1) — the ARTIFACT pin (`plugins[].source.sha` in
`.claude-plugin/marketplace.json`, a denied path here) is what actually bounds what code is fetched;
the registration's ref only ever bounded the INDEX, and freezing it is what made
`claude plugin update` re-read the same frozen catalogue forever. This module now proves: an
explicit `--ref` (or `--channel edge`) still composes an exact ref (AC-IUP-2, a regression guard —
this half of AC-BIP-1 is unchanged), the shipped DEFAULT_MARKETPLACE_REF constant is DECOUPLED from
`.claude-plugin/marketplace.json`'s `plugins[foundry].source.ref` and the two are PERMITTED to
differ (AC-IUP-6 — this is a LOCAL, hermetic comparison, never an upstream fact), and the template
clone is anonymous `https://` by default with SSH as an opt-in. It proves nothing about whether the
upstream tag still resolves to that commit — tag protection is an operator-owned dependency, out of
this atom's reach (spec, Dependencies / sequencing).

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

DOC_FILES_EXCLUDED_DIRS = (
    "docs/archive/",
    # atomic specs quote command literals (e.g. `claude plugin marketplace add`) inside AC prose
    # to name a checkpoint's locator/surface text, not to give an adopter a live install
    # instruction — same rationale as CHANGELOG.md's exclusion below (a record of what a checkpoint
    # says, not a documented install path). feat-foundry-bootstrap-cli's own AC-BCL-11 clause is
    # the concrete trip: it names `claude plugin marketplace add` in prose with no version literal,
    # since AC-BCL-11 governs docs/QUICKSTART.md's *ordering*, not a second pinned command.
    "specs/",
)
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
    """SEVERED (feat-foundry-installer-unpinning, AC-IUP-6). This USED to be the AC-BIP-3
    comparison: it read the shipped DEFAULT_MARKETPLACE_REF constant from
    scripts/foundry-bootstrap.sh and `plugins[foundry].source.ref` from
    `.claude-plugin/marketplace.json`, BOTH beneath `root`, and reported a mismatch as drift. That
    coupling is deliberately removed — DEFAULT_MARKETPLACE_REF is now an INDEX-selector default
    only (never composed into the default marketplace-add source, AC-IUP-1), while `source.ref`
    stays the ARTIFACT pin (a denied path, untouched by this atom); the two are PERMITTED to
    differ, and this function now ALWAYS returns None. It still READS both operands beneath
    `root` (rather than becoming a bare no-op), so a genuinely malformed shipped constant or
    manifest still raises loudly through `_script_constant`/`_manifest_ref` — only the
    equality-as-drift verdict is gone. Retained under its original name (rather than deleted) so
    the two controls below keep proving the severance against this SAME production code path, not
    a fresh copy of the old comparison."""
    _script_constant(root, "DEFAULT_MARKETPLACE_REF")
    _manifest_ref(root)
    return None


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


# ───────────────────────────────────────────────────────────────────── AC-IUP-1 ──
# INVERTED (feat-foundry-installer-unpinning). WAS test_marketplace_add_names_an_explicit_ref: the
# DEFAULT registration used to compose an explicit ref unconditionally. It now composes NONE — the
# registration (the INDEX) is tagless; the ARTIFACT stays pinned via the untouched
# `plugins[].source.sha` in `.claude-plugin/marketplace.json`, a denied path here. Name unchanged
# (checkpoints/AC-IUP-7's collected-count floor reference it by node id); polarity reversed.

def test_marketplace_add_names_an_explicit_ref(tmp_path):
    proc = _dry_run(tmp_path / "proj")
    assert proc.returncode == 0, proc.stderr
    m = re.search(r"marketplace add (\S+)", proc.stdout)
    assert m, proc.stdout
    src = m.group(1)
    assert "#" not in src, "the DEFAULT source argument %r still carries a ref (AC-IUP-1)" % src
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    assert src == marketplace, "the tagless default must name the declared marketplace verbatim"


# ───────────────────────────────────────────────────────────────────── AC-IUP-1 ──
# INVERTED (feat-foundry-installer-unpinning). WAS test_default_ref_is_the_pinned_release_tag: the
# default plan used to compose "<marketplace>#<ref>". It now composes the bare marketplace, with no
# "#" anywhere on that line — the resolved ref is still DISCLOSED (the "resolved channel: stable
# (ref vX.Y.Z)" line, AC-BIP-10(a)), just never concatenated into the source argument.

def test_default_ref_is_the_pinned_release_tag(tmp_path):
    proc = _dry_run(tmp_path / "proj")
    assert proc.returncode == 0, proc.stderr
    ref = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    assert ("marketplace add %s" % marketplace) in proc.stdout
    assert not re.search(r"marketplace add %s#" % re.escape(marketplace), proc.stdout), \
        "the default plan must not compose a ref onto the source argument (AC-IUP-1)"
    assert ref in proc.stdout, "the resolved ref must still be DISCLOSED, just not composed in"
    assert re.search(r"channel.*stable", proc.stdout)
    assert "UNSTABLE" not in proc.stdout


# ───────────────────────────────────────────────────────────────────── AC-IUP-6 ──

def test_shipped_pin_matches_marketplace_manifest_ref():
    """UNCHANGED assertion, now VACUOUSLY true by construction (AC-IUP-6): shipped_pin_drift always
    returns None post-severance, so this stays green regardless of whether the shipped constant and
    the manifest ref happen to agree in the real tree. See test_manifest_ref_mutation_convicts_the_
    shipped_pin and test_bootstrap_default_ref_is_decoupled_from_the_manifest_ref for the actual
    anti-vacuity proof of the severance."""
    drift = shipped_pin_drift(REPO_ROOT)
    assert drift is None, "shipped constant drifted from the release manifest: %r" % (drift,)


def test_manifest_ref_mutation_convicts_the_shipped_pin(tmp_path):
    """INVERTED (feat-foundry-installer-unpinning, AC-IUP-6). WAS the mutation negative control
    proving the (now-removed) equality comparison genuinely read both files. The comparison is
    deliberately severed now: a mutated manifest ref must NOT be reported as drift, because the
    index constant and the artifact ref are permitted to differ. Same fixture-building code as
    before (a hard-coded `return None` could not distinguish this from a no-op, which is exactly
    what test_bootstrap_default_ref_is_decoupled_from_the_manifest_ref additionally proves with an
    independently-chosen differing value)."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".claude-plugin").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "foundry-bootstrap.sh", tmp_path / "scripts" / "foundry-bootstrap.sh")
    manifest = json.loads((REPO_ROOT / MANIFEST_RELPATH).read_text(encoding="utf-8"))
    for entry in manifest.get("plugins", []):
        if entry.get("name") == "foundry":
            entry["source"]["ref"] = entry["source"]["ref"] + "-mutated-for-the-negative-control"
    (tmp_path / MANIFEST_RELPATH).write_text(json.dumps(manifest), encoding="utf-8")

    drift = shipped_pin_drift(tmp_path)
    assert drift is None, (
        "AC-IUP-6: the index constant and the manifest's artifact ref are DECOUPLED — a mutated "
        "manifest ref must not be reported as drift: %r" % (drift,)
    )


def test_bootstrap_default_ref_is_decoupled_from_the_manifest_ref(tmp_path):
    """NEW (feat-foundry-installer-unpinning, AC-IUP-6), anti-vacuity: builds a temporary tree in
    which the shipped index constant and the manifest's artifact ref carry DIFFERENT values —
    chosen independently of test_manifest_ref_mutation_convicts_the_shipped_pin's suffix-mutation
    approach, so this is not a re-run of the identically-shaped fixture — and asserts no drift is
    reported. A tree that still enforced equality (i.e. main before this atom) would fail this."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".claude-plugin").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "foundry-bootstrap.sh", tmp_path / "scripts" / "foundry-bootstrap.sh")
    manifest = json.loads((REPO_ROOT / MANIFEST_RELPATH).read_text(encoding="utf-8"))
    constant = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    differing_ref = "v0.0.1-deliberately-different"
    if differing_ref == constant:
        differing_ref = "v0.0.2-deliberately-different"
    for entry in manifest.get("plugins", []):
        if entry.get("name") == "foundry":
            entry["source"]["ref"] = differing_ref
    (tmp_path / MANIFEST_RELPATH).write_text(json.dumps(manifest), encoding="utf-8")

    assert constant != differing_ref  # sanity: this really is a difference, not a no-op fixture
    drift = shipped_pin_drift(tmp_path)
    assert drift is None, "AC-IUP-6: differing index/artifact refs must not be reported as drift: %r" % (drift,)


def test_shipped_pin_is_a_semver_release_tag():
    """AC-IUP-9's machine check binding the RETAINED index constant (the constant no longer tracks
    the manifest, AC-IUP-6, so it cannot silently rot to garbage undetected — its own shape is
    still validated here)."""
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


# ──────────────────────────────────────────────────────────────────── AC-IUP-1 ──
# INVERTED (feat-foundry-installer-unpinning). WAS test_dry_run_discloses_the_resolved_pin's (c):
# the disclosed pin used to be COMPOSED onto the source argument ("<marketplace>#<ref>"). It is
# still disclosed (b) — just never composed (c inverted).

def test_dry_run_discloses_the_resolved_pin(tmp_path):
    target = tmp_path / "proj"
    proc = _dry_run(target)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    ref = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE_REF")
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")

    assert re.search(r"channel.*stable", out)                       # (a)
    assert ref in out                                                 # (b) still disclosed
    assert ("%s#%s" % (marketplace, ref)) not in out                  # (c) INVERTED: never composed
    assert ("marketplace add %s" % marketplace) in out                # (c2) the bare, tagless source
    assert "https://github.com/" in out                                # (d)
    assert not target.exists()                                         # (e) no fs change
    # (e) no claude invocation: the process ran with no `claude` on the (scrubbed, real) PATH and
    # still exited 0 with a full plan — a real invocation attempt would have failed loudly instead.


# ──────────────────────────────────────────────────────────────────── AC-IUP-1 ──
# INVERTED (feat-foundry-installer-unpinning). WAS test_selftest_asserts_the_pinned_plan: the
# shipped selftest's own "pinned marketplace ref default names an explicit semver release tag"
# check is renamed to describe the tagless default (scripts/foundry-bootstrap.sh's selftest()).

def test_selftest_asserts_the_pinned_plan():
    proc = subprocess.run(["bash", str(BOOTSTRAP), "--selftest"], env=_scrubbed_env(),
                           cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BOOTSTRAP-SELFTEST-GREEN" in proc.stdout
    assert re.search(r"\[ok\].*default.*tagless", proc.stdout)
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
# INVERTED by feat-foundry-install-line-unpinning (AC-ILU-3). WHAT CHANGED AND WHY: a documented
# `claude plugin marketplace add <marketplace>#vX.Y.Z` line pins the marketplace REGISTRATION
# (the INDEX), not merely the artifact -- and that registration lands verbatim in an adopter's
# `settings.json`. Every subsequent `claude plugin update` then re-reads the catalogue AT THAT
# FROZEN REF forever, truthfully reporting "already at the latest version (X)" even after a new
# tag publishes. `plugins[].source.sha` (untouched by this atom) remains the real artifact pin;
# see the spec's "Two pins were conflated" section. This function's NAME is unchanged (checkpoints
# reference it by node id); its polarity is reversed, not deleted -- a re-introduced version
# literal on a documented install line still fails CI, it just fails the OPPOSITE assertion.

def _version_pinned_offenders(instructions, marketplace):
    """(rel, line) pairs among `instructions` whose marketplace-add source names `marketplace`
    (THIS repo's own marketplace slug, e.g. "lukasrepublic/agentic-foundry") with a version
    literal (`#...`) attached to it -- the pinned-registration defect AC-ILU-1/2/3 forbid. A
    source naming some OTHER marketplace (an adopter's own worked example, a different project)
    is not this repository's registration and is never convicted here.

    Security-review FIX 3(b): the source argument is matched with an OPTIONAL non-whitespace
    prefix ahead of the bare `owner/repo` slug, so a pin re-introduced in the full URL form
    (`marketplace add https://github.com/lukasrepublic/agentic-foundry#v1.6.0`) is convicted too
    -- the original anchor matched only the bare-slug spelling and silently missed that one."""
    pin_re = re.compile(r"marketplace add \S*%s#\S+" % re.escape(marketplace))
    return [(rel, line) for rel, line in instructions if pin_re.search(line)]


def test_documented_install_commands_name_the_shipped_pin():
    """INVERTED (feat-foundry-install-line-unpinning, AC-ILU-3): no documented marketplace-add
    source naming THIS repo's own marketplace may carry a version literal. See the module-level
    comment above this function for why.

    Security-review FIX 3(a): a NON-VACUITY FLOOR is restored underneath the inversion. The
    pre-inversion check's `assert pins` failed CLOSED the moment its regex stopped matching
    anything real; a bare `assert not offenders` on its own would instead go GREEN the same way
    -- a drifted/broken `_version_pinned_offenders` pattern would silently stop convicting
    anything and this test would report success for the wrong reason. So each scanned doc must
    still carry at least one recognized marketplace-add instruction (proving the SCANNER still
    finds real install lines), independently of whether any of them is pinned."""
    instructions = _documented_install_instructions(REPO_ROOT)
    assert instructions, "no documented install instructions found (must be non-empty)"
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")

    scanned_rels = {rel for rel, _ in instructions}
    for expected_rel in ("README.md", "docs/QUICKSTART.md", "docs/troubleshooting.md",
                         "docs/how-to/adopt-on-an-existing-codebase.md",
                         "skills/cut-release/SKILL.md"):
        assert expected_rel in scanned_rels, (
            "%s carries no recognized marketplace-add install instruction -- either the doc lost "
            "its install line or the scanner's regex stopped matching real lines (both must fail "
            "this test, not pass it silently)" % expected_rel
        )

    offenders = _version_pinned_offenders(instructions, marketplace)
    assert not offenders, (
        "documented install line(s) still pin this repo's marketplace REGISTRATION to a version "
        "literal (the exact defect AC-ILU-1/2/3 remove -- see spec "
        "'install-line-unpinning'): %r" % (offenders,)
    )


# ──────────────────────────────────────────────────────────────────── AC-ILU-4 ──
# THE ANTI-VACUITY ROW. Proves the inverted check above discriminates a pinned documented install
# line from a tagless one, rather than being green for free because no shipped doc happens to
# carry a pin today.

def test_injected_version_literal_convicts_the_documented_install_check(tmp_path):
    """Copies a documented install line's SHAPE into a THROWAWAY file under tmp_path (never a real
    shipped doc), injects a version literal onto its marketplace-add source, and asserts
    `_version_pinned_offenders` convicts it -- while a tagless sibling line, written into the same
    throwaway tree, is NOT convicted. Both assertions must hold for the inversion to be trusted:
    without the first, the check could be vacuously green; without the second, it could be
    unconditionally red.

    Security-review FIX 3(b): also covers the FULL URL spelling of the source
    (`https://github.com/<marketplace>#<ref>`), which the bare-slug-anchored pattern used to miss
    entirely -- convicted here, or the broadened pattern above would be unverified."""
    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    tagless_line = "claude plugin marketplace add %s" % marketplace
    pinned_line = tagless_line + "#v9.9.9-injected"
    pinned_url_line = "claude plugin marketplace add https://github.com/%s#v9.9.9-injected" % marketplace

    (tmp_path / "pinned.md").write_text("```bash\n%s\n```\n" % pinned_line, encoding="utf-8")
    (tmp_path / "clean.md").write_text("```bash\n%s\n```\n" % tagless_line, encoding="utf-8")
    (tmp_path / "pinned-url.md").write_text("```bash\n%s\n```\n" % pinned_url_line, encoding="utf-8")

    instructions = _documented_install_instructions(tmp_path)
    assert len(instructions) == 3, instructions

    offending_files = {rel for rel, _ in _version_pinned_offenders(instructions, marketplace)}
    assert "pinned.md" in offending_files, \
        "an injected version literal was NOT convicted by the inverted check"
    assert "pinned-url.md" in offending_files, (
        "an injected version literal spelled as a full URL source "
        "(https://github.com/<marketplace>#<ref>) was NOT convicted"
    )
    assert "clean.md" not in offending_files, \
        "a tagless sibling line was wrongly convicted -- the check is unconditionally red"


# ──────────────────────────────────────────────────────────────────── AC-IUP-1 ──
# INVERTED (feat-foundry-installer-unpinning). WAS test_real_path_records_the_pinned_marketplace_add:
# the REAL (non-dry-run) path used to record "plugin marketplace add <marketplace>#<ref>". It now
# records the bare, tagless source on the default (stable) channel.

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
    expected = "plugin marketplace add %s" % marketplace
    assert expected in calls, calls
    assert not any(c.startswith(expected + "#") for c in calls), \
        "the default (stable) real invocation must not carry a ref: %r" % (calls,)


# ──────────────────────────────────────────────────────────────────── AC-IUP-4 ──
# NEW (feat-foundry-installer-unpinning). VERIFIED (spec Clarifications): `claude plugin
# marketplace add` has no --autoUpdate flag (--help lists only --scope/--sparse), and a real
# CLAUDE_CONFIG_DIR-isolated run wrote {"source": {"source": "github", "repo": ...}} with NO
# autoUpdate key at all -- an absence the shell installer cannot avoid (it has no write of its own
# into that scope; see AC-BTSS-1's test_toolchain_step_needs_only_the_claude_cli, which proves
# toolchain-install needs NOTHING but the `claude` binary reachable -- a constraint an extra
# settings-file write would break). AC-IUP-5 therefore widens the pinned-state predicate to accept
# `autoUpdate !== true` (never merely `=== false`), so the exact shape a real `marketplace add`
# produces classifies IDENTICALLY to an explicit `autoUpdate: false` for the allow-tier gate. Proven
# end-to-end here: no installer call ever carries an autoUpdate=true literal, and the VERIFIED real
# shape is fed through the SAME classifyPin the npx CLI's own reconcile path uses.

def test_bootstrap_registers_with_auto_update_false(tmp_path, existing_repo):
    bindir = _recording_claude_stub(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    calls_file = tmp_path / "calls.log"

    proc = _real_run(existing_repo, [], home, calls_file, bindir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    calls = _read_calls(calls_file)
    assert calls, "the recording claude stub was never invoked (absent/empty recording is a FAILURE)"
    assert not any("autoUpdate" in c or "true" in c.lower() for c in calls), (
        "no installer invocation may carry an autoUpdate=true literal: %r" % (calls,)
    )

    marketplace = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    name = marketplace.rsplit("/", 1)[-1]
    verified_real_entry = {"source": {"source": "github", "repo": marketplace}}  # no autoUpdate key
    js = (
        "import { classifyPin } from './src/floorReconcile.mjs';\n"
        "const settingsObj = { extraKnownMarketplaces: { %s: %s } };\n"
        "const pins = { marketplace_name: %s, marketplace_repo: %s, plugin_version: '9.9.9' };\n"
        "console.log(JSON.stringify(classifyPin(settingsObj, pins)));\n"
    ) % (json.dumps(name), json.dumps(verified_real_entry), json.dumps(name), json.dumps(marketplace))
    node_proc = subprocess.run(
        ["node", "--input-type=module", "-e", js],
        cwd=str(REPO_ROOT / "cli"), capture_output=True, text=True, timeout=15,
    )
    assert node_proc.returncode == 0, node_proc.stderr
    result = json.loads(node_proc.stdout)
    assert result["state"] == "pinned", (
        "AC-IUP-4/5: the shell-registered shape (no ref, no autoUpdate key) must classify pinned: %r" % result
    )


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


# ── the equality the tightened pinned-state predicate made load-bearing ────────────────────────
# PR #132 security review, Risk 2. cli/src/floorReconcile.mjs's classifyPin now requires the
# adopter's registered source.repo to EQUAL cli/package.json's foundry.marketplace_repo before a
# tagless entry can classify `pinned`. The shell installer registers scripts/foundry-bootstrap.sh's
# DEFAULT_MARKETPLACE. Nothing bound those two values, and they are edited in different files by
# different atoms -- so a rename or a case change in either would make EVERY shell-installed
# adopter's reconcile classify `unpinned` and silently withhold all 42 allow rules, which is the
# exact failure AC-IUP-5 exists to prevent. Bound here, once, so the divergence is loud.
def test_shell_default_marketplace_equals_the_cli_pin_block_repo():
    """foundry-bootstrap.sh's DEFAULT_MARKETPLACE == cli/package.json foundry.marketplace_repo."""
    shell_value = _script_constant(REPO_ROOT, "DEFAULT_MARKETPLACE")
    with open(os.path.join(REPO_ROOT, "cli", "package.json"), encoding="utf-8") as fh:
        pin_block = json.load(fh)["foundry"]
    assert shell_value, "DEFAULT_MARKETPLACE did not resolve -- the comparison would be vacuous"
    assert pin_block.get("marketplace_repo"), "cli/package.json foundry.marketplace_repo is missing"
    assert shell_value == pin_block["marketplace_repo"], (
        "the shell installer registers %r while the CLI pin block declares %r -- a tagless entry "
        "written by one will classify UNPINNED against the other and silently lose the allow tier"
        % (shell_value, pin_block["marketplace_repo"])
    )
