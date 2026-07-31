"""tests/test_subtraction_absence.py — Blocks 2 + 3 of the subtraction-contract-widening atom
(AC-SCW-6..9, feat-foundry-subtraction-contract-widening.md): the widened deleted-module
absence/reference sweep.

The frozen token set, surface set, absence enumeration, and approved-exceptions allowlist are
NOT inlined here — they live in the single fixture
tests/fixtures/subtraction/deleted-module-sweep.yaml, mirrored verbatim from the sibling
acceptance-contract.yaml preamble, so the widened locators are hash-frozen with the contract.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "subtraction" / "deleted-module-sweep.yaml"


def _load_fixture():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


FIXTURE = _load_fixture()


# ------------------------------------------------------------------------ sweep primitives -----

def absent_check(root, absent_paths):
    """Return the subset of `absent_paths` that PRESENTLY EXIST under `root` — empty means the
    sweep is clean. `root` is a str/Path; `absent_paths` are repo-relative."""
    return [p for p in absent_paths if os.path.exists(os.path.join(root, p))]


def surface_files(root, surface_set):
    files = []
    for pat in surface_set:
        files.extend(sorted(glob.glob(os.path.join(root, pat))))
    return files


def sweep_occurrences(root, token_set, surface_set):
    """Return {(relpath, token_pattern): [(lineno, matched_text), ...]} for every real hit of a
    frozen token pattern inside a frozen surface-set file, under `root`."""
    out = {}
    for f in surface_files(root, surface_set):
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(f, root)
        for tok in token_set:
            rx = re.compile(tok)
            hits = [(i, m.group(0)) for i, line in enumerate(text.split("\n"), 1)
                    for m in [rx.search(line)] if m]
            if hits:
                out[(rel, tok)] = hits
    return out


def allowlist_well_formed_errors(root, allowlist, token_set):
    """Return a list of human-readable errors for every malformed allowlist entry: path does not
    resolve in `root`, token not a member of `token_set`, or reason empty/whitespace-only."""
    errors = []
    tokset = set(token_set)
    for e in allowlist:
        path, token, reason = e.get("path"), e.get("token"), e.get("reason")
        if not path or not os.path.isfile(os.path.join(root, path)):
            errors.append(f"entry path does not resolve in the candidate tree: {e!r}")
        if token not in tokset:
            errors.append(f"entry token is not a member of the frozen token set: {e!r}")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"entry reason is empty/whitespace: {e!r}")
    return errors


def uncovered_occurrences(occurrences, allowlist):
    """Occurrences (by (path, token) pair) with no matching allowlist entry."""
    allow_pairs = {(e.get("path"), e.get("token")) for e in allowlist}
    return {k: v for k, v in occurrences.items() if k not in allow_pairs}


# ==================================================================== AC-SCW-6 ==================

def test_absent_enumeration_includes_branch_protection_applier(tmp_path):
    applier = "scripts/foundry-branch-protection.sh"
    assert applier in FIXTURE["absent_paths"], (
        "the frozen absence enumeration must include the deleted branch-protection applier"
    )

    # The real tree: the applier stays absent, and the sweep over the real tree reports it clean.
    assert absent_check(REPO_ROOT, [applier]) == []

    # Negative-path proof: a candidate tree in which the path EXISTS must fail the sweep.
    (tmp_path / "scripts").mkdir()
    (tmp_path / applier).write_text("#!/usr/bin/env bash\necho resurrected\n", encoding="utf-8")
    present = absent_check(tmp_path, [applier])
    assert present == [applier], "a tree with the deleted applier present must fail the sweep"


# ==================================================================== AC-SCW-7 ==================

# Independently-authored copy of the FROZEN SWEEP DEFINITION from the sibling
# acceptance-contract.yaml preamble (transcribed by hand here, not read off the fixture file) —
# this is the cross-check that catches fixture drift, per the criterion's own text: "the fixture
# sets must equal the preamble sets".
_EXPECTED_TOKEN_SET = [
    'foundry[_-]merge[_-]gate', 'foundry[_-]ci[_-]gate', 'foundry[_-]walk[_-]evidence',
    'foundry[_-]ui[_-]walk', 'foundry[_-]provenance', 'foundry-emit-provenance',
    'foundry[_-]wiring[_-]hash', 'foundry[_-]wiring[_-]heal', 'foundry[_-]authz[_-]light',
    'foundry-authorize-light', 'foundry[_-]authz[_-]drift', 'foundry[_-]admission',
    'foundry[_-]acceptance[_-]flag', 'foundry[_-]released[_-]gate',
    'foundry[_-]operator[_-]interaction', 'foundry[_-]adi', 'foundry[_-]ihdi', 'foundry[_-]debit',
    'foundry[_-]run[_-]ledger', 'foundry[_-]drift[_-]sweep', 'foundry-spawn-worker',
    'foundry-fanout', 'foundry-branch-protection', 'branch-protection\\.json', 'merge-gate-hook',
    'pr-create-provenance', 'foundry-merge-gate-status', 'skills/authorize-light',
    'skills/emit-provenance',
]
_EXPECTED_SURFACE_SET = [
    'scripts/*.py', 'scripts/*.sh', 'scripts/foundry_checks/*.py', 'hooks/*.sh',
    'hooks/hooks.json', 'workflows/*.js', '.github/workflows/*.yml', 'skills/*/SKILL.md',
    'agents/*.md',
]


def test_widened_sweep_is_clean_over_frozen_token_and_surface_sets():
    assert FIXTURE["token_set"] == _EXPECTED_TOKEN_SET, "fixture token_set drifted from the frozen preamble"
    assert FIXTURE["surface_set"] == _EXPECTED_SURFACE_SET, "fixture surface_set drifted from the frozen preamble"
    assert "skills/*/SKILL.md" in FIXTURE["surface_set"]
    assert "agents/*.md" in FIXTURE["surface_set"]

    occ = sweep_occurrences(REPO_ROOT, FIXTURE["token_set"], FIXTURE["surface_set"])
    uncovered = uncovered_occurrences(occ, FIXTURE["allowlist"])
    assert uncovered == {}, f"widened sweep found un-allowlisted occurrences: {sorted(uncovered)}"


# ==================================================================== AC-SCW-8 ==================

def test_compact_reinject_has_no_run_ledger_reference():
    hook_path = REPO_ROOT / "hooks" / "foundry-compact-reinject.sh"
    text = hook_path.read_text(encoding="utf-8")

    # Reference-absence ONLY (AC-SCW-8, narrowed per the spec's own Clarification): the deleted
    # module, its bounded-subprocess helper, and its cap constant must not appear.
    assert not re.search(r"foundry[_-]run[_-]ledger", text), "compact-reinject still names the deleted run-ledger module"
    assert "_bounded_ledger_summary" not in text, "the bounded-subprocess helper is still present"
    assert "LEDGER_CAP_SECONDS" not in text, "the ledger cap constant is still present"
    assert "FOUNDRY_COMPACT_LEDGER_CAP_SECONDS" not in text, "the ledger cap env override is still present"

    # And the widened sweep, run against the candidate tree, reports THIS FILE specifically
    # clean (no un-allowlisted occurrence for it — it was deliberately NOT allowlisted; AC-SCW-8
    # removes the reference outright rather than tolerating it).
    occ = sweep_occurrences(REPO_ROOT, FIXTURE["token_set"], FIXTURE["surface_set"])
    file_hits = {k: v for k, v in occ.items() if k[0] == "hooks/foundry-compact-reinject.sh"}
    assert file_hits == {}, f"the widened sweep still finds a hit in compact-reinject.sh: {file_hits}"
    assert not any(e.get("path") == "hooks/foundry-compact-reinject.sh" for e in FIXTURE["allowlist"]), (
        "compact-reinject.sh must not be allowlisted — the reference is removed, not tolerated"
    )


# ==================================================================== AC-SCW-9 ==================

@pytest.mark.parametrize("row", ["coverage", "well-formedness", "injected-occurrence-fails"])
def test_allowlist_governs_every_tolerated_occurrence_and_is_well_formed(row):
    if row == "coverage":
        # (i) every occurrence the widened sweep finds over the REAL tree has a matching entry.
        occ = sweep_occurrences(REPO_ROOT, FIXTURE["token_set"], FIXTURE["surface_set"])
        uncovered = uncovered_occurrences(occ, FIXTURE["allowlist"])
        assert uncovered == {}, f"un-allowlisted occurrence(s): {sorted(uncovered)}"
        return

    if row == "well-formedness":
        # (ii) every real entry is well-formed: path resolves, token in the frozen set, reason
        # non-empty — plus the three malformed-entry negative paths, over throwaway copies so
        # the real fixture is never mutated: a bad path, a bad token, and an empty reason must
        # each be caught.
        errors = allowlist_well_formed_errors(REPO_ROOT, FIXTURE["allowlist"], FIXTURE["token_set"])
        assert errors == [], f"malformed allowlist entries: {errors}"

        bad_path = [{"path": "scripts/does-not-exist.py", "token": FIXTURE["token_set"][0], "reason": "x"}]
        assert allowlist_well_formed_errors(REPO_ROOT, bad_path, FIXTURE["token_set"]) != []
        bad_token = [{"path": "scripts/foundry_contract.py", "token": "not-a-frozen-token", "reason": "x"}]
        assert allowlist_well_formed_errors(REPO_ROOT, bad_token, FIXTURE["token_set"]) != []
        bad_reason = [{"path": "scripts/foundry_contract.py", "token": FIXTURE["token_set"][0], "reason": "   "}]
        assert allowlist_well_formed_errors(REPO_ROOT, bad_reason, FIXTURE["token_set"]) != []
        return

    # row == "injected-occurrence-fails": an injected, un-allowlisted occurrence fails the
    # sweep — over a throwaway candidate tree copy (never the real repo), by planting a fresh
    # deleted-module mention in a surface file with NO allowlist entry covering it.
    candidate = _make_candidate_tree(tmp_root=_tmp_candidate_root())
    try:
        target = os.path.join(candidate, "scripts", "foundry_grounding_conformance.py")
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n# freshly injected, un-allowlisted mention: foundry_ihdi\n")
        occ2 = sweep_occurrences(candidate, FIXTURE["token_set"], FIXTURE["surface_set"])
        uncovered2 = uncovered_occurrences(occ2, FIXTURE["allowlist"])
        assert ("scripts/foundry_grounding_conformance.py", "foundry[_-]ihdi") in uncovered2, (
            "an injected, un-allowlisted deleted-module mention must fail the sweep"
        )
    finally:
        shutil.rmtree(candidate, ignore_errors=True)


def _tmp_candidate_root():
    import tempfile
    return tempfile.mkdtemp(prefix="foundry-scw-candidate-")


def _make_candidate_tree(tmp_root):
    """A throwaway copy of just the surface-set directories the sweep needs, so the injected-hit
    negative path (AC-SCW-9 row iii) never touches the real repo tree."""
    for d in ("scripts", "hooks", "workflows", ".github/workflows", "skills", "agents"):
        src = REPO_ROOT / d
        if src.is_dir():
            shutil.copytree(src, os.path.join(tmp_root, d))
    return tmp_root
