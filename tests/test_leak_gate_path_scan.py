"""feat-foundry-leak-gate-path-scan — AC-LPS-1..7.

R4 from PR #306's security review: no scope matched terms against PATH text, so a proprietary
term living solely in a file or directory NAME shipped silently — and every finding echoed its
path verbatim, so the first marker/read-error inside a term-bearing path would republish the
term in a world-readable Actions log. Detection + span-exact redaction, one atom.

Synthetic terms throughout; these tests never need (and must never contain) real terms.
"""
import os
import re
import subprocess
import sys

from conftest import REPO_ROOT, load_module

LS = load_module(os.path.join(".github", "actions", "leak-gate", "leak_scan.py"), "leak_scan_lps")
SCAN = load_module("scripts/foundry-prepublication-leak-scan.py", "prepub_lps")

TERMS = ["alpha", "bravo", "charlie"]


def _denylist(tmp_path):
    p = tmp_path / "dl.txt"
    p.write_text("\n".join(TERMS) + "\n", encoding="utf-8")
    return str(p)


def _tree(tmp_path, files, name="tree"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return str(root)


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)


# ------------------------------------------------------------------------------------ AC-LPS-1 --
def test_path_term_in_filename_detected(tmp_path):
    """A term-bearing FILENAME with clean content is a finding. Content scanning alone is blind
    to it — that blindness is the R4 defect."""
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"docs/alpha-integration/notes.md": "perfectly clean content\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert exit_code != 0, "a term in a path must convict"
    pt = [h for h in hits if h.startswith("PATH-TERM")]
    assert pt, f"no PATH-TERM finding: {hits}"
    assert "term[0]" in pt[0]


def test_path_matching_is_root_relative_not_absolute(tmp_path):
    """The ABSOLUTE prefix (an operator's home dir, a CI workspace path) must never fire a term —
    only the corpus-relative path is scanned text."""
    dl = _denylist(tmp_path)
    # plant the term in the PREFIX (the tmp dir name), keep the tree itself clean
    prefix = tmp_path / "bravo-workspace"
    prefix.mkdir()
    root = _tree(prefix, {"src/clean.py": "clean\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert not [h for h in hits if h.startswith("PATH-TERM")], \
        f"the absolute prefix fired a path term: {hits}"


# ------------------------------------------------------------------------------------ AC-LPS-2 --
def test_single_redactor_all_finding_classes(tmp_path):
    """Every finding class that carries a path routes through redact_path — asserted statically
    (no f-string embeds a raw `full`/`rel` in a hit) and dynamically (marker + read-error paths
    come back redacted)."""
    src = open(os.path.join(REPO_ROOT, ".github", "actions", "leak-gate", "leak_scan.py"),
               encoding="utf-8").read()
    body = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    for raw in ('{full}', '{rel}'):
        assert raw not in body, f"a finding embeds an unredacted path variable {raw}"

    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"pkg/charlie-sdk/readme.md": "id " + "HBK" + "-9\n"})
    _c, hits = LS.scan_tree(root, dl)
    marker = next(h for h in hits if h.startswith("MARKER-HBK"))
    assert "charlie" not in marker.lower(), f"marker finding leaked the term via its path: {marker}"
    assert "term[2]" in marker


def test_redact_path_passthrough_without_matcher():
    """Markers-only mode has no terms to withhold; the path passes through unchanged."""
    assert LS.redact_path("/a/b/c.md", None) == "/a/b/c.md"


# ------------------------------------------------------------------------------------ AC-LPS-3 --
def _history_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    return repo


def test_history_only_path_convicted_redacted(tmp_path):
    """A term-bearing path that exists ONLY in history (added then deleted) is one
    `git log --raw` away on a public repo — the history scope must convict it, redacted."""
    repo = _history_repo(tmp_path)
    bad = repo / "vendors" / "bravo-adapter"
    bad.mkdir(parents=True)
    (bad / "shim.py").write_text("clean content\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "add"], repo)
    subprocess.run(["git", "rm", "-rq", "vendors"], cwd=repo, check=True, capture_output=True)
    _git(["commit", "-qm", "remove"], repo)
    assert not (repo / "vendors").exists(), "fixture: the path must be history-only"

    matcher = LS.build_matcher(TERMS)
    ok, findings, _origin = SCAN.history_scope(str(repo), set(), TERMS, matcher)
    assert ok is False, "a history-only term path must FAIL the scope"
    path_hits = [f for f in findings if "historical PATH" in f.message]
    assert path_hits, f"no historical-path finding: {[f.message for f in findings]}"
    joined = "\n".join(f.message for f in findings)
    assert "bravo" not in joined.lower(), f"the term leaked through a history finding: {joined}"
    assert "term[1]" in joined


def test_history_blob_finding_path_is_redacted(tmp_path):
    """The existing blob-content findings report `(historical path …)` — that field must
    redact too, or a term-bearing path leaks the moment any blob under it matches content."""
    repo = _history_repo(tmp_path)
    d = repo / "alpha-docs"
    d.mkdir()
    (d / "note.md").write_text("mentions bravo here\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "add"], repo)

    matcher = LS.build_matcher(TERMS)
    ok, findings, _ = SCAN.history_scope(str(repo), set(), TERMS, matcher)
    assert ok is False
    blob = next(f for f in findings if f.message.startswith("blob "))
    assert "alpha" not in blob.message.lower(), f"blob finding leaked the path term: {blob.message}"
    assert "term[0]" in blob.message


# ------------------------------------------------------------------------------------ AC-LPS-4 --
def test_gate_enforces_what_the_selfscan_asserts(tmp_path):
    """The tracked-set clean test asserts zero path findings; the GATE must convict what that
    assertion checks for — proven on a fixture planting a term filename in an otherwise-clean
    tree, via the real scan_tree (the same callable CI's composite action drives)."""
    dl = _denylist(tmp_path)
    clean = _tree(tmp_path, {"src/ok.py": "fine\n"}, name="clean")
    assert LS.scan_tree(clean, dl)[0] == 0, "fixture: the clean tree must pass"
    planted = _tree(tmp_path, {"src/ok.py": "fine\n", "src/alpha_client.py": "fine\n"}, name="planted")
    exit_code, hits = LS.scan_tree(planted, dl)
    assert exit_code != 0 and any(h.startswith("PATH-TERM") for h in hits)


# ------------------------------------------------------------------------------------ AC-LPS-5 --
def test_script_output_names_merge_floor():
    """F2 (first-run audit): the three USER-OUTPUT sites say merge floor. Asserted on the
    emitted strings, not on comments."""
    contract = open(os.path.join(REPO_ROOT, "scripts", "foundry_contract.py"), encoding="utf-8").read()
    assert "is enforced at the merge floor, " in contract, "the warn must name the merge floor"
    assert "is enforced at the merge gate (0d), " not in contract, "the retired-gate warn survives"

    rtm = open(os.path.join(REPO_ROOT, "scripts", "foundry-project-rtm.py"), encoding="utf-8").read()
    assert 'merge-floor PASS: {r[\'merge_gate_pass\']}' in rtm

    fleet = open(os.path.join(REPO_ROOT, "scripts", "foundry-fleet-session-machinery.py"), encoding="utf-8").read()
    assert 'f"corpus-authorized + merge-floor {pr_state}"' in fleet


# ------------------------------------------------------------------------------------ AC-LPS-6 --
def test_narration_gate_covers_nonmarkdown(tmp_path):
    """The widened corpus actually bites: the gate walked with a planted .json offender must
    convict it. Runs the REAL test function over a materialized mini-tree."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "docs_claims_lps", os.path.join(REPO_ROOT, "tests", "test_docs_claims.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert ".json" in mod._NARRATION_EXTS and ".sh" in mod._NARRATION_EXTS and ".yml" in mod._NARRATION_EXTS

    # dynamic: point the module's walk at a planted tree and expect conviction. The corpus
    # is the TRACKED set (R5), so the planted tree must be a git repo with the file tracked.
    planted = _tree(tmp_path, {"cfg/settings.json": '{"//": "kept from the subtraction era"}\n'})
    _git(["init", "-q"], planted)
    _git(["config", "user.email", "t@t"], planted)
    _git(["config", "user.name", "t"], planted)
    _git(["add", "-A"], planted)
    real_root = mod.REPO_ROOT
    try:
        mod.REPO_ROOT = planted
        try:
            mod.test_no_journey_narration_in_shipped_docs()
            convicted = False
        except AssertionError:
            convicted = True
    finally:
        mod.REPO_ROOT = real_root
    assert convicted, "a planted narration marker in a .json file was not convicted"


# ------------------------------------------------------------------------------------ AC-LPS-7 --
def test_antitaut_span_exact_segments_intact(tmp_path):
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"docs/alpha-guide/setup.md": "clean\n"})
    _c, hits = LS.scan_tree(root, dl)
    pt = next(h for h in hits if h.startswith("PATH-TERM"))
    assert "docs/term[0]-guide/setup.md" in pt, f"segments must survive redaction: {pt}"
    term = "alpha"
    for n in range(3, len(term) + 1):
        for i in range(len(term) - n + 1):
            assert term[i:i + n] not in pt.lower(), f"term substring leaked: {pt}"


def test_antitaut_two_terms_two_indices(tmp_path):
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"alpha/bravo.md": "clean\n"})
    _c, hits = LS.scan_tree(root, dl)
    pt = next(h for h in hits if h.startswith("PATH-TERM"))
    assert "term[0]/term[1].md" in pt, f"each term must redact to its OWN index: {pt}"


def test_antitaut_boundary_holds_clean_stays_clean(tmp_path):
    """Identifier-boundary semantics hold for paths: a term embedded in a longer identifier
    does not fire; a genuinely clean tree yields zero PATH-TERM findings and exit 0."""
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"src/alphabet_soup.py": "clean\n", "lib/subravo.md": "clean\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert not [h for h in hits if h.startswith("PATH-TERM")], hits
    assert exit_code == 0, f"a clean tree must stay clean: {hits}"


def test_antitaut_marker_in_term_path_redacted(tmp_path):
    """THE R4 DISCLOSURE CASE, verbatim: an unrelated structural marker inside a term-bearing
    path must report the marker without republishing the term through the path field."""
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"docs/integrations/charlie/notes.md": "ref " + "HBK" + "-3\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert exit_code != 0
    marker = next(h for h in hits if h.startswith("MARKER-HBK"))
    assert "charlie" not in marker.lower(), f"R4 reproduced — the marker leaked the term: {marker}"
    assert "term[2]" in marker, f"the redacted path must keep locating power: {marker}"
