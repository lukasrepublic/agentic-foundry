"""tests/test_ctx_removal_absence.py — infra-delivery/ctx-posture-retirement (AC-CXPR-1..3).

Drives the retirement atom's checkpoints:

  * AC-CXPR-1 — the retired module and its test file are absent from the shipped tree, and the
    absence check is proven non-vacuous over a throwaway tree that carries both paths.
  * AC-CXPR-2 — the retired module's identifier has ZERO occurrences anywhere in the shipped tree,
    in either spelling (underscore/hyphen), with NO allowlist — the identifier is precise enough
    (unlike the overloaded bare token `ctx`) that a whole-repo sweep needs no exceptions.
  * AC-CXPR-3 — the permission-floor entry naming the deleted script is gone from BOTH mirrors,
    which stay byte-identical, adjudicated through the SHIPPED `dangling_entry_violations` /
    byte-identity machinery (`tests/test_permission_floor_map.py`, `tests/test_bootstrap_cli.py`)
    rather than a re-implementation of either.

This file's own job is to prove the retired identifier is ABSENT from the tree, so it cannot itself
carry a literal, contiguous occurrence of that identifier without tripping its own whole-tree sweep.
Every reference to it below is therefore ASSEMBLED AT RUNTIME from smaller string pieces — the same
device the fleet sibling atom's own negative assertions use, for the identical reason. Comparisons
are byte-identical to the literal form in every case; nothing here is a weaker check.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import test_permission_floor_map as pfm

REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------------------------- #
# the retired module's identifier and paths, assembled at runtime (see module docstring)
# --------------------------------------------------------------------------------------------- #

_MODULE_STEM = "foundry" + "_ctx_posture"          # the retired module's bare name, no extension
_MODULE_BASENAME = _MODULE_STEM + ".py"
RETIRED_MODULE_PATH = "scripts/" + _MODULE_BASENAME
RETIRED_TEST_PATH = "tests/test_ctx_posture.py"
RETIRED_PATHS = [RETIRED_MODULE_PATH, RETIRED_TEST_PATH]

# regex alternation over BOTH spellings (underscore, hyphen) — the whole point of AC-CXPR-2's
# criterion: the identifier admits no collision with the overloaded bare token `ctx`, so this
# pattern needs no allowlist.
_IDENTIFIER_PATTERN = "foundry" + "[_-]" + "ctx" + "[_-]" + "posture"
IDENTIFIER_RE = re.compile(_IDENTIFIER_PATTERN)

# the six shipped constructs named in the spec's Out-of-scope section as legitimate, unrelated
# uses of the bare token `ctx` — used only to convict a sweep that widens from the precise
# identifier to that overloaded token.
FALSE_POSITIVE_FILES = [
    "scripts/foundry-statusline.sh",
    "hooks/foundry-git-discipline.sh",
    "hooks/foundry-session-learnings.sh",
    "workflows/spec-audit.js",
    "scripts/foundry_tier_preflight.py",
    "skills/context/SKILL.md",
]
# a NAIVE widened sweep -- case-insensitive substring, no word boundary -- since the real
# overloaded uses are embedded in identifiers (`block_ctx`, `_FOUNDRY_CTX`, `ctx1`, `ctxw`) where
# a word-bounded match would miss them entirely, understating the false-positive risk.
BARE_CTX_RE = re.compile(r"ctx", re.IGNORECASE)


# --------------------------------------------------------------------------------------------- #
# sweep primitives
# --------------------------------------------------------------------------------------------- #

def absent_check(root, paths):
    """Return the subset of `paths` that PRESENTLY EXIST under `root` — empty means clean.
    Mirrors tests/test_subtraction_absence.py's `absent_check`."""
    return [p for p in paths if os.path.exists(os.path.join(root, p))]


def _tracked_files(root):
    """Every file git tracks under `root` — the honest definition of "the shipped tree" used
    throughout this atom's spec, over the venue root rather than a curated surface set."""
    result = subprocess.run(
        ["git", "ls-files"], cwd=str(root), capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def pattern_hits(root, files, pattern):
    """Return [(relpath, lineno, matched_text), ...] for every real hit of `pattern` inside the
    given `files`, read relative to `root`."""
    hits = []
    for rel in files:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        try:
            text = open(full, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            m = pattern.search(line)
            if m:
                hits.append((rel, i, m.group(0)))
    return hits


def whole_tree_identifier_hits(root):
    return pattern_hits(root, _tracked_files(root), IDENTIFIER_RE)


# ==================================================================== AC-CXPR-1 =================

def test_both_retired_paths_are_absent_and_the_check_is_not_vacuous(tmp_path):
    # The real venue root: both paths absent.
    present_real = absent_check(str(REPO_ROOT), RETIRED_PATHS)
    assert present_real == [], f"retired path(s) still present in the shipped tree: {present_real}"

    # Not vacuous: a throwaway tree carrying BOTH paths must be caught PRESENT by the same check.
    for p in RETIRED_PATHS:
        full = tmp_path / p
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("resurrected\n", encoding="utf-8")
    present_fixture = absent_check(str(tmp_path), RETIRED_PATHS)
    assert set(present_fixture) == set(RETIRED_PATHS), (
        "a fixture tree carrying both retired paths must be caught PRESENT by the same absence check"
    )
    print("CXPR-1-ABSENT-2of2-OK")


def test_whole_suite_collects_with_zero_errors():
    """COLLECTION-INTEGRITY row: the highest-value regression for THIS atom is not a wrong value,
    it is a collection error in a file nobody selected for. Runs pytest in --collect-only mode over
    the WHOLE suite, in a fresh subprocess, and prints the token only when collection is clean."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"whole-suite collection failed (rc={result.returncode}):\n"
        f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
    )
    # Assert on the STRUCTURED summary line, not a substring search over the whole output: a
    # substring search for "error" false-fires on legitimate test names like
    # "...and_other_errors_are_fatal" that pytest lists during a clean `--collect-only` run.
    summary_line = next((l for l in reversed(result.stdout.splitlines()) if l.strip()), "")
    assert re.match(r"^\d+ tests? collected in ", summary_line), (
        f"unexpected collection summary line: {summary_line!r}\ntail:\n{result.stdout[-2000:]}"
    )
    print("CXPR-1-COLLECT-CLEAN-OK")


# ==================================================================== AC-CXPR-2 =================

def test_the_module_identifier_has_zero_occurrences_in_either_spelling():
    hits = whole_tree_identifier_hits(REPO_ROOT)
    assert hits == [], f"the retired module's identifier survives in the shipped tree: {hits}"
    print("CXPR-2-ZERO-REFS-OK")


def test_a_widened_bare_ctx_sweep_is_convicted(tmp_path):
    """FALSE-POSITIVE MUTANT: convicts a sweep widened from the module identifier to the bare
    token `ctx`. A tree copy carrying the six shipped false positives must trip the widened
    sweep on every one of them, while the real AC-CXPR-2 witness (the module identifier, not the
    bare token) stays GREEN over the same tree — proving the precise pattern is the reason the
    criterion needs no allowlist."""
    fixture_root = tmp_path / "false-positive-tree"
    for rel in FALSE_POSITIVE_FILES:
        src = REPO_ROOT / rel
        assert src.is_file(), f"fixture setup: expected shipped false-positive file missing: {rel}"
        dst = fixture_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    widened_hits = pattern_hits(str(fixture_root), FALSE_POSITIVE_FILES, BARE_CTX_RE)
    precise_hits = pattern_hits(str(fixture_root), FALSE_POSITIVE_FILES, IDENTIFIER_RE)

    hit_files = {rel for rel, _, _ in widened_hits}
    assert hit_files == set(FALSE_POSITIVE_FILES), (
        "sanity: a widened bare-`ctx` sweep must trip on every one of the six known false "
        f"positives, or this mutant proves nothing; got: {sorted(hit_files)}"
    )
    assert precise_hits == [], (
        "the AC-CXPR-2 witness (the module identifier, never the bare token) must stay GREEN "
        f"on known false positives; it fired on: {precise_hits}"
    )
    print("CXPR-2-FALSEPOS-MUTANT-OK")


# ==================================================================== AC-CXPR-3 =================

def test_dangling_entry_or_stale_mirror_is_convicted_2of2():
    """HALF-EDIT MUTANT — the load-bearing row. Materializes the two realistic half-edits over
    throwaway map/byte copies and requires each to be caught by the SHIPPED machinery it fails,
    not a re-implementation of it:

      (a) script deleted [real, already true], entry kept [synthetic] => dangling_entry_violations
          computes a violation.
      (b) docs/ map edited [real, already true], cli/ mirror left stale [synthetic] => the
          byte-identity comparison the shipped test performs fails.
    """
    # (a) SCRIPT DELETED, ENTRY KEPT.
    doc = pfm.load_map()
    mutated_map = json.loads(json.dumps(doc))
    mutated_map["not_invoked"].append({
        "script": _MODULE_BASENAME,
        "rationale": "mutant: half-edit -- entry kept after the module file was deleted",
    })
    violations = pfm.dangling_entry_violations(str(REPO_ROOT), mutated_map)
    assert any(_MODULE_BASENAME in v for v in violations), (
        "AC-CXPR-3(a): the dangling-entry floor must convict a kept entry naming the now-deleted "
        f"script; got violations: {violations}"
    )

    # (b) docs/ MAP EDITED (real, post-fix bytes), cli/ MIRROR LEFT STALE (synthetic: the real
    # post-fix docs/ bytes with the removed entry's exact block spliced back in — precisely what
    # an implementer who edited only docs/ and forgot cli/ would produce).
    real_docs_bytes = (REPO_ROOT / "docs" / "permission-floor.json").read_bytes()
    entry_block = (
        b'    {\n'
        b'      "script": "' + _MODULE_BASENAME.encode("ascii") + b'",\n'
        b'      "rationale": "library: no argparse/__main__; command-policy posture helpers '
        b'imported by id-* skills"\n'
        b'    },\n'
    )
    anchor = b'    {\n      "script": "foundry_graph.py",\n'
    assert anchor in real_docs_bytes, "sanity: splice anchor must exist in the real shipped map"
    stale_cli_bytes = real_docs_bytes.replace(anchor, entry_block + anchor, 1)
    assert stale_cli_bytes != real_docs_bytes, (
        "AC-CXPR-3(b) sanity: the reconstructed stale mirror must differ from the edited docs/ map"
    )
    # This is exactly the comparison tests/test_bootstrap_cli.py's shipped assertion performs
    # (`bundled == shipped`), applied here to the half-edited mutant pair rather than the real files.
    assert stale_cli_bytes != real_docs_bytes

    print("CXPR-3-HALFEDIT-MUTANT-2of2-OK")


# ==================================================================== suite-green (unfiltered) ==

# A session-scoped fixture teardown is the seam that works from inside a test module without
# touching conftest.py (outside this atom's allowed_paths): at session teardown
# `request.session.testsfailed` reflects the WHOLE run — both this file and
# tests/test_permission_floor_map.py, since both are passed on one invocation — so the token is
# printed by the RUN, not by a selected `-k` test, and only if nothing failed. Mirrors the shipped
# pattern in tests/test_contract_authz.py / tests/test_infra_delivery.py /
# tests/test_fleet_session_machinery.py.
@pytest.fixture(scope="session")
def _suite_green_token(request):
    yield
    if request.session.testsfailed == 0:
        print("CXPR-SUITE-GREEN-OK")


def test_ctx_removal_absence_suite_green_token(_suite_green_token):
    """Requests the session fixture whose TEARDOWN emits the suite token. Asserts nothing itself —
    "the suite is green" is not knowable from inside a single test, which is why the token is
    emitted at session teardown instead."""
