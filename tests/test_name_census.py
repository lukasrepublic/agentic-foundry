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

The change set depends on the event. A pull request: everything that differs from the merge-base with the base ref
(`GITHUB_BASE_REF`, else `origin/main`), plus uncommitted and untracked-but-not-ignored files. A push: `HEAD~1..HEAD`, or the
push's `before` sha when the event payload gives one. `FOUNDRY_CENSUS_BASE` names an explicit base ref and wins. An EMPTY change
set passes with a printed note; a PR whose base cannot be resolved is a failure (no change set is knowable).
The term layer FAILS where it cannot run, except on a fork pull request, where secrets do not resolve and it reports NOT-RUN,
exactly as the leak-gate action does. Outside CI (no `GITHUB_ACTIONS`) a missing denylist is NOT-RUN, so a contributor's
checkout is not red for want of a private list.
The census is exactly as good as the denylist behind it: a name nobody listed is not found.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile

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
_EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
_EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH", "")
_BASE_REF = os.environ.get("GITHUB_BASE_REF", "")
_IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"
_SHA = re.compile(r"[0-9a-f]{40}")


def _git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check)


def _event():
    try:
        with open(_EVENT_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge_base(ref):
    assert ref and not ref.startswith("-"), f"unusable base ref {ref!r}"
    p = _git("merge-base", "--end-of-options", "HEAD", ref, check=False)
    base = p.stdout.strip()
    assert p.returncode == 0 and base, (
        f"no merge-base between HEAD and {ref!r}: the census has no change set to scan. "
        "CI checks out full history (fetch-depth 0); locally, fetch origin or set FOUNDRY_CENSUS_BASE to a ref")
    return base


def census_base(event_name=None, event=None, explicit=None, base_ref=None):
    """The commit the change set is measured from, or None when there is nothing to measure from (a first commit)."""
    event_name = _EVENT_NAME if event_name is None else event_name
    explicit = _AMBIENT_BASE if explicit is None else explicit
    if explicit:
        return _merge_base(explicit)
    if event_name == "push":
        before = (event if event is not None else _event()).get("before") or ""
        if _SHA.fullmatch(before) and _git("cat-file", "-e", before + "^{commit}", check=False).returncode == 0:
            return before
        p = _git("rev-parse", "--verify", "--quiet", "HEAD~1", check=False)
        return p.stdout.strip() or None
    base_ref = _BASE_REF if base_ref is None else base_ref
    return _merge_base(f"origin/{base_ref}" if base_ref else "origin/main")


def change_set(base):
    """Repo-relative paths added or modified since `base`, including uncommitted and untracked files. Deleted files are not scanned."""
    out = set()
    queries = [("diff", "--name-only", "-z", "--diff-filter=ACMR", "HEAD"),
               ("ls-files", "-z", "--others", "--exclude-standard")]
    if base:
        queries.insert(0, ("diff", "--name-only", "-z", "--diff-filter=ACMR", "--end-of-options", base, "HEAD"))
    for args in queries:
        out.update(x for x in _git(*args).stdout.split("\0") if x)
    return sorted(p for p in out if os.path.isfile(os.path.join(REPO_ROOT, p)) and not os.path.islink(os.path.join(REPO_ROOT, p)))


def _is_fork_pr():
    return _EVENT_NAME == "pull_request" and bool(((_event().get("pull_request") or {}).get("head") or {}).get("repo", {}).get("fork"))


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


@contextlib.contextmanager
def real_denylist():
    """Yield the real denylist path, or None. A secret supplied by content is written to a private temp directory OUTSIDE
    pytest's tmp dirs and removed in a `finally`, so no clear-text copy survives the test."""
    if _AMBIENT_DENYLIST_CONTENT:
        d = tempfile.mkdtemp(prefix="foundry-census-")
        try:
            os.chmod(d, 0o700)
            path = os.path.join(d, "denylist")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_AMBIENT_DENYLIST_CONTENT)
            yield path
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return
    if _AMBIENT_PROJECT_DIR:
        cand = os.path.join(_AMBIENT_PROJECT_DIR, ".claude", "foundry-leak-denylist.txt")
        if os.path.exists(cand):
            yield cand
            return
    yield None


def _scan_change_set(tmp_path, denylist):
    base = census_base()
    paths = change_set(base)
    if not paths:
        print("AC-OM-9: the change set is empty (nothing to scan): pass")
        return 0, []
    return census(materialize(paths, tmp_path / "cs"), denylist)


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
def test_an_empty_change_set_passes_with_a_note(tmp_path, capsys, monkeypatch):
    """A push whose HEAD equals its base has nothing to scan: pass, say so, never fail."""
    monkeypatch.setitem(globals(), "change_set", lambda base: [])
    assert _scan_change_set(tmp_path, None) == (0, [])
    assert "change set is empty" in capsys.readouterr().out
    head = _git("rev-parse", "HEAD").stdout.strip()
    assert _git("diff", "--name-only", "--end-of-options", head, "HEAD").stdout == ""      # HEAD against itself: nothing


def test_the_base_follows_the_event():
    head = _git("rev-parse", "HEAD").stdout.strip()
    parent = _git("rev-parse", "HEAD~1").stdout.strip()
    sha = "a" * 40
    assert census_base("push", {"before": parent}, explicit="") == parent                      # the push's own before sha
    assert census_base("push", {"before": sha}, explicit="") == parent                         # unknown before: HEAD~1
    assert census_base("push", {"before": "0" * 40}, explicit="") == parent                    # a new branch's null before
    assert census_base("pull_request", {}, explicit="", base_ref="main") == _merge_base("origin/main")
    assert census_base("pull_request", {}, explicit=head, base_ref="main") == head             # an explicit base wins
    with pytest.raises(AssertionError):
        census_base("pull_request", {}, explicit="", base_ref="no-such-branch-here")          # an unresolvable PR base fails
    with pytest.raises(AssertionError):
        census_base("pull_request", {}, explicit="--output=x", base_ref="main")                # an option is not a ref


def test_no_structural_marker_in_the_change_set(tmp_path):
    code, hits = _scan_change_set(tmp_path, None)
    assert code == 0, "\n".join(hits[:20])


def term_layer(tmp_path, in_ci, fork):
    with real_denylist() as real:
        if real is None:
            if in_ci and not fork:
                pytest.fail("AC-OM-9: no denylist resolved on a non-fork event: the term layer cannot run. "
                            "The repository secret FOUNDRY_LEAK_DENYLIST is unset or misconfigured (the leak gate refuses the same way)")
            print("AC-OM-9-NAME-CENSUS-NOT-RUN: no denylist available (fork pull request or a checkout outside CI)")
            pytest.skip("NOT-RUN: no denylist available (fork pull request, or a checkout outside CI)")
        assert leak_scan.load_denylist(real), "the real denylist resolved to zero terms: the census below would be vacuous"
        code, hits = _scan_change_set(tmp_path, real)
        assert code == 0, "\n".join(hits[:20])


def test_no_workspace_account_repository_or_person_name_in_the_change_set(tmp_path):
    term_layer(tmp_path, _IN_CI, _is_fork_pr())


def test_the_term_layer_fails_where_it_cannot_run_except_on_a_fork_pr(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "_AMBIENT_DENYLIST_CONTENT", None)
    monkeypatch.setitem(globals(), "_AMBIENT_PROJECT_DIR", None)
    with pytest.raises(pytest.fail.Exception):
        term_layer(tmp_path, in_ci=True, fork=False)                 # a non-fork CI event with no secret: RED
    with pytest.raises(pytest.skip.Exception):
        term_layer(tmp_path, in_ci=True, fork=True)                  # a fork PR: NOT-RUN
    with pytest.raises(pytest.skip.Exception):
        term_layer(tmp_path, in_ci=False, fork=False)                # a contributor checkout: NOT-RUN


def test_the_secret_copy_does_not_survive(monkeypatch):
    monkeypatch.setitem(globals(), "_AMBIENT_DENYLIST_CONTENT", "synthetic-term\n")
    with real_denylist() as path:
        assert os.path.isfile(path) and (os.stat(path).st_mode & 0o077) == 0
        parent = os.path.dirname(path)
    assert not os.path.exists(parent)
