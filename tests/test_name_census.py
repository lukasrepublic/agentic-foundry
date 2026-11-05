"""tests/test_name_census.py -- AC-OM-9 (gv-17): the name census over the programme's change set.

The census finds no workspace, cloud account, repository or person name in any file the change set adds or modifies. It is
NOT a second scanner: it drives the shared module of the existing leak gate (`.github/actions/leak-gate/leak_scan.py`:
`scan_tree`, the loader and the matcher) over a copy of the change set, so it matches terms, paths and structural markers
exactly as the leak gate does, and it prints a term's index, never the term.

Three layers:
  * the CONTROL: a planted synthetic name in `tests/fixtures/census/planted/` MUST be caught and the clean twin MUST pass;
    a census that cannot see the planted name is convicted (the blind-census test).
  * the STRUCTURAL markers over the real change set: always run, need no secret.
  * the TERMS over the real change set: need the real denylist (the CI secret by content, or the operator's private
    workspace). Without it this layer reports NOT-RUN and claims no coverage, as the leak-gate action does on a fork PR.

The change set is everything that differs from the merge-base with `origin/main` (committed, staged, unstaged and
untracked-but-not-ignored), or from `FOUNDRY_CENSUS_BASE` when that names a ref. No resolvable base is a failure, never a pass.
The census is exactly as good as the denylist behind it: a name nobody listed is not found.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT, load_module

leak_scan = load_module(".github/actions/leak-gate/leak_scan.py", "leak_scan_census")

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "census")
# The planted name is synthetic. The denylist that targets it is built in tmp_path, never committed.
PLANTED_TERM = "zorbglatt"

# Captured at import, before conftest's autouse fixture scrubs the environment (same pattern as test_denylist_out_of_tree).
_AMBIENT_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR")
_AMBIENT_DENYLIST_CONTENT = os.environ.get("FOUNDRY_LEAK_DENYLIST")
_AMBIENT_BASE = os.environ.get("FOUNDRY_CENSUS_BASE")


def _git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check)


def census_base():
    """The commit the change set is measured from. Raises (fails the test) when none can be resolved."""
    ref = _AMBIENT_BASE or "origin/main"
    p = _git("merge-base", "HEAD", ref, check=False)
    base = p.stdout.strip()
    assert p.returncode == 0 and base, (
        f"no merge-base between HEAD and {ref!r}: the census has no change set to scan. "
        "CI checks out full history (fetch-depth 0); locally, fetch origin or set FOUNDRY_CENSUS_BASE to a ref")
    return base


def change_set(base):
    """Repo-relative paths added or modified since `base`, including uncommitted and untracked files. Deleted files are not scanned."""
    out = set()
    for args in (("diff", "--name-only", "-z", "--diff-filter=ACMR", base, "HEAD"),
                 ("diff", "--name-only", "-z", "--diff-filter=ACMR", "HEAD"),
                 ("ls-files", "-z", "--others", "--exclude-standard")):
        out.update(x for x in _git(*args).stdout.split("\0") if x)
    return sorted(p for p in out if os.path.isfile(os.path.join(REPO_ROOT, p)) and not os.path.islink(os.path.join(REPO_ROOT, p)))


def materialize(paths, dest):
    """Copy the named repo files into `dest` with their repo-relative paths (so the scan also sees the path text)."""
    for rel in paths:
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(REPO_ROOT, rel), target)
    return str(dest)


def census(root, denylist_path=None, scan=None):
    """Run the shared scanner over `root`. Returns (exit_code, hits)."""
    scan = scan or leak_scan.scan_tree
    if denylist_path is None:
        return scan(root, None, markers_only=True)
    return scan(root, denylist_path)


def assert_caught(code, hits):
    """The control assertion: a planted name must make the census exit non-zero with a NAME-TERM finding."""
    assert code == 1 and any(h.startswith("NAME-TERM") for h in hits), f"the planted name was not caught: {code} {hits}"


def _write_denylist(tmp_path, terms=(PLANTED_TERM,), name="control-terms.txt"):
    p = tmp_path / name
    p.write_text("# synthetic control terms\n" + "\n".join(terms) + "\n", encoding="utf-8")
    return str(p)


def _resolve_real_denylist(tmp_path):
    if _AMBIENT_DENYLIST_CONTENT:
        p = tmp_path / "real-denylist.txt"
        p.write_text(_AMBIENT_DENYLIST_CONTENT, encoding="utf-8")
        return str(p)
    if _AMBIENT_PROJECT_DIR:
        cand = os.path.join(_AMBIENT_PROJECT_DIR, ".claude", "foundry-leak-denylist.txt")
        if os.path.exists(cand):
            return cand
    return None


# ------------------------------------------------------------------------------------------ the control --
def test_a_planted_name_is_caught_and_never_printed(tmp_path):
    root = materialize(["tests/fixtures/census/planted/notes.md"], tmp_path / "planted")
    code, hits = census(root, _write_denylist(tmp_path))
    assert_caught(code, hits)
    assert not any(PLANTED_TERM in h.lower() for h in hits), "a finding must carry the term's index, never the term"


def test_the_clean_twin_passes(tmp_path):
    root = materialize(["tests/fixtures/census/clean/notes.md"], tmp_path / "clean")
    code, hits = census(root, _write_denylist(tmp_path))
    assert code == 0 and hits == []


def test_a_planted_name_in_a_path_is_caught(tmp_path):
    root = tmp_path / "pathplant"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / f"{PLANTED_TERM}-runbook.md").write_text("an innocent body\n", encoding="utf-8")
    code, hits = census(str(root), _write_denylist(tmp_path))
    assert code == 1 and any(h.startswith("PATH-TERM") for h in hits), hits


def test_a_planted_structural_marker_is_caught_without_any_denylist(tmp_path):
    root = tmp_path / "marker"
    root.mkdir()
    (root / "page.md").write_text("see " + "HBK" + "-1 for the history\n", encoding="utf-8")     # assembled: never a literal in the tree
    code, hits = census(str(root), None)
    assert code == 1 and any(h.startswith("MARKER-HBK") for h in hits), hits


def test_a_blind_census_is_convicted(tmp_path):
    """The control must be able to fail: a scanner that reports clean over everything does not pass the planted-name check."""
    def blind(root, denylist_path, markers_only=False):
        return 0, []
    root = materialize(["tests/fixtures/census/planted/notes.md"], tmp_path / "planted")
    code, hits = census(root, _write_denylist(tmp_path), scan=blind)
    assert code == 0                                  # the blind census reports clean over the planted tree ...
    with pytest.raises(AssertionError):
        assert_caught(code, hits)                     # ... and the control assertion rejects it


def test_the_census_scans_a_real_copy_of_the_change_set_not_an_empty_one(tmp_path):
    root = materialize(["tests/fixtures/census/clean/notes.md"], tmp_path / "one")
    shutil.rmtree(os.path.join(root, "tests"))
    code, hits = census(root, None)
    assert code == 1 and any(h.startswith("EMPTY-CORPUS-ERROR") for h in hits), "an empty corpus must refuse, not report clean"


# ----------------------------------------------------------------------------------- the real change set --
def test_the_change_set_is_not_empty():
    paths = change_set(census_base())
    assert paths, "the change set is empty: the census below would be vacuous"
    assert "tests/test_name_census.py" in paths or any(p.startswith(("docs/", "scripts/", "governance/", "tests/")) for p in paths)


def test_no_structural_marker_in_the_change_set(tmp_path):
    root = materialize(change_set(census_base()), tmp_path / "cs")
    code, hits = census(root, None)
    assert code == 0, "\n".join(hits[:20])


def test_no_workspace_account_repository_or_person_name_in_the_change_set(tmp_path):
    real = _resolve_real_denylist(tmp_path)
    if real is None:
        print("AC-OM-9-NAME-CENSUS-NOT-RUN: no off-tree denylist available in this environment")
        pytest.skip("NOT-RUN: no off-tree denylist available (adopter checkout, fork pull request or unset secret)")
    assert leak_scan.load_denylist(real), "the real denylist resolved to zero terms: the census below would be vacuous"
    root = materialize(change_set(census_base()), tmp_path / "cs")
    code, hits = census(root, real)
    assert code == 0, "\n".join(hits[:20])
