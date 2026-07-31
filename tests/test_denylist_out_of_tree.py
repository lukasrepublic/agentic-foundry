"""feat-foundry-denylist-out-of-tree — AC-DOT-1..8.

Two coupled defects, one atom:
  1. `.github/actions/leak-gate/denylist.txt` shipped 7 proprietary terms in the clear, in a tree
     that goes public at GP-3. The F3 zero-history reset closes the *history* vector only.
  2. `leak_scan.py` built its finding as `f"NAME-TERM: {full}: {m.group(0)!r}"` and `_cli` printed
     it — so the first gate failure on a public repo republished the term it exists to protect.

Fixing either alone leaves the exposure open, which is why they are one atom.

These tests drive the REAL `scan_tree`, the REAL CLI, and the REAL composite-action script. Fixture
denylists are synthetic throughout — no test here needs the operator's actual terms, and none should
contain them.
"""
import os
import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

LEAK_SCAN_REL = os.path.join(".github", "actions", "leak-gate", "leak_scan.py")
LEAK_SCAN_PATH = os.path.join(REPO_ROOT, LEAK_SCAN_REL)
ACTION_YML = os.path.join(REPO_ROOT, ".github", "actions", "leak-gate", "action.yml")
CI_YML = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
PREPUB = os.path.join(REPO_ROOT, "scripts", "foundry-prepublication-leak-scan.py")

LS = load_module(LEAK_SCAN_REL, "leak_scan_dot")

# Captured at IMPORT time, before conftest's autouse `_clean_env` deletes it. That fixture is right
# — an operator's ambient CLAUDE_PROJECT_DIR must never leak into a hermetic test — but AC-DOT-1(b)
# is the one assertion that legitimately needs the REAL off-tree term set, so it reads this snapshot
# rather than the (correctly) scrubbed environment.
_AMBIENT_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR")
# CI has the terms as a secret, not as a file. Supplying them by content makes AC-DOT-1(b) runnable
# there too, instead of being permanently skipped in the only place that gates a merge.
_AMBIENT_DENYLIST_CONTENT = os.environ.get("FOUNDRY_LEAK_DENYLIST")


def _resolve_real_denylist(tmp_path):
    """The real term set, from whichever source this environment has. Returns a path, or None.

    Order: explicit content (CI secret) -> the operator's private workspace. There is deliberately
    NO in-tree fallback -- a fallback is how an off-tree list silently becomes an in-tree one."""
    if _AMBIENT_DENYLIST_CONTENT:
        p = tmp_path / "real-denylist.txt"
        p.write_text(_AMBIENT_DENYLIST_CONTENT, encoding="utf-8")
        return str(p)
    if _AMBIENT_PROJECT_DIR:
        cand = os.path.join(_AMBIENT_PROJECT_DIR, ".claude", "foundry-leak-denylist.txt")
        if os.path.exists(cand):
            return cand
    return None

# Synthetic terms. `zeta` is deliberately a substring-free, boundary-testable token.
FIXTURE_TERMS = ["alpha", "bravo", "charlie", "zeta"]


def _denylist(tmp_path, terms=FIXTURE_TERMS, name="dl.txt"):
    p = tmp_path / name
    p.write_text("# a comment\n\n" + "\n".join(terms) + "\n", encoding="utf-8")
    return str(p)


def _tree(tmp_path, files, name="tree"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return str(root)


# ------------------------------------------------------------------------------------ AC-DOT-1 --
def test_no_tracked_file_has_denylist_shape():
    """(a) The lazy-rename catch: no TRACKED file is a bare-token list referenced as a denylist."""
    assert not os.path.exists(os.path.join(REPO_ROOT, ".github", "actions", "leak-gate", "denylist.txt")), \
        "the denylist is still in the tree — it must not ship"
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    # Anything still POINTED AT as a denylist by the wiring must not be a tracked in-tree path.
    wiring = "\n".join(open(p, encoding="utf-8").read() for p in (ACTION_YML, CI_YML, LEAK_SCAN_PATH))
    for m in re.finditer(r"[\w./-]*denylist[\w./-]*\.txt", wiring):
        cand = m.group(0).lstrip("./")
        assert cand not in tracked, f"wiring references a TRACKED denylist file: {cand}"


def test_repo_is_clean_against_offtree_real_denylist(tmp_path):
    """(b) THE LOAD-BEARING HALF — the real term set, resolved from OUTSIDE the tree, finds nothing
    anywhere in this repo. This is what proves no term survives at any path under any filename; the
    shape check above only convicts an obvious rename.

    ON SKIPPING, AND WHY THIS EMITS A TOKEN INSTEAD. A skipped test still exits 0, so a checkpoint of
    the form `pytest -k <this> && echo TOKEN` would print its token and go GREEN on a skip — a false
    green on the single assertion that proves no term survives. This test therefore prints an
    explicit verdict token and the checkpoint greps for THAT, so an unrun assertion can never be
    mistaken for a clean one.

    An adopter running this suite has no denylist of ours and legitimately cannot run it; that case
    prints NOT-RUN and is honest about the absence rather than claiming coverage. The real
    publication gate is `scripts/foundry-prepublication-leak-scan.py`, which the operator runs before
    the visibility flip — this is the cheap in-suite echo of it, not a substitute for it.
    """
    real = _resolve_real_denylist(tmp_path)
    if real is None:
        print("DOT-1-CLEAN-NOT-RUN: no off-tree denylist available in this environment")
        pytest.skip("no off-tree denylist available (adopter checkout or unset secret)")
    terms = LS.load_denylist(real)
    assert terms, "the real denylist resolved to zero terms — the scan below would be vacuous"

    # SCOPE IS THE TRACKED SET, NOT THE FILESYSTEM. `scan_tree` walks os.walk, which on a working
    # checkout includes gitignored runtime artifacts under `.foundry/` — those never publish, so
    # convicting them would make this assertion permanently red for reasons unrelated to what ships.
    # What goes public is exactly what git tracks. (This is the same distinction the prepublication
    # scanner draws with its separate tracked-partition scope.)
    matcher = LS.build_matcher(terms)
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True,
                             check=True).stdout.split(b"\0")
    findings = []
    for raw in tracked:
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        full = os.path.join(REPO_ROOT, rel)
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        m = matcher.search(text)
        if m:
            findings.append(LS.format_name_term_finding(
                rel, text.count("\n", 0, m.start()) + 1, LS.match_term_index(m)))
    assert not findings, f"a real term survives in a TRACKED file: {findings}"

    # PATHS ARE PART OF "at any path under any filename" (R4, PR #306 security review). No scope
    # matches terms against path text — `scan_tree` and `history_scope` both match CONTENT only — so
    # a proprietary term living solely in a directory or file NAME, with clean content everywhere,
    # ships silently. Asserted here because AC-DOT-1(b)'s own wording covers it.
    path_findings = [rel for rel in
                     (r.decode("utf-8", "surrogateescape") for r in tracked if r)
                     if matcher.search(rel)]
    assert not path_findings, \
        f"a real term survives in {len(path_findings)} tracked PATH(s) (content was clean)"
    print(f"DOT-1-CLEAN-VERIFIED: {len(terms)} terms, {len(tracked)} tracked files, "
          f"0 content findings, 0 path findings")


# ------------------------------------------------------------------------------------ AC-DOT-2 --
def test_finding_never_carries_matched_text(tmp_path):
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"a.py": "x = 1\ny = bravo\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert exit_code != 0
    hit = next(h for h in hits if h.startswith("NAME-TERM"))
    for term in FIXTURE_TERMS:
        assert term.lower() not in hit.lower(), f"the matched term leaked into the finding: {hit}"
    # and the constructor itself cannot be handed text
    assert "term[" in hit


def test_group0_never_reaches_any_output(tmp_path):
    """Static + dynamic: the module must contain no path that puts `m.group(0)` into a hit."""
    src = open(LEAK_SCAN_PATH, encoding="utf-8").read()
    body = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "group(0)" not in body, "leak_scan.py still references m.group(0) in code"


# ------------------------------------------------------------------------------------ AC-DOT-3 --
def test_term_index_resolvable_count_printed_list_not(tmp_path):
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"a.py": "q = charlie\n"})
    r = subprocess.run([sys.executable, LEAK_SCAN_PATH, "--root", root, "--denylist", dl],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "term[2]" in out, f"index must identify charlie as term 2: {out}"
    assert f"terms={len(FIXTURE_TERMS)}" in out, "the term COUNT must be printed to make an index interpretable"
    for term in FIXTURE_TERMS:
        assert term.lower() not in out.lower(), f"the CLI printed a term: {out}"


# ------------------------------------------------------------------------------------ AC-DOT-4 --
def test_action_materializes_outside_root_and_masks():
    y = open(ACTION_YML, encoding="utf-8").read()
    assert "inputs:" in y and "denylist:" in y, "the action must take the denylist as an input"
    assert "RUNNER_TEMP" in y, "the denylist must be materialized outside github.workspace"
    assert "::add-mask::" in y, "each term must be registered for log masking"
    assert "trap cleanup EXIT" in y, "the materialized file must be removed even on failure"
    # the materialized path must NOT be under the scanned workspace
    assert "github.workspace }}/foundry-denylist" not in y


def test_ci_supplies_secret_and_no_pull_request_target():
    y = open(CI_YML, encoding="utf-8").read()
    assert "secrets.FOUNDRY_LEAK_DENYLIST" in y, "ci.yml must supply the denylist from a secret"
    assert "is-fork-pr:" in y, "ci.yml must pass the fork signal explicitly"
    assert "head.repo.fork" in y, "the fork signal must come from the event payload"
    code = "\n".join(ln.split("#", 1)[0] for ln in y.splitlines())
    assert "pull_request_target" not in code, \
        "pull_request_target runs with secrets against untrusted code — rejected by design"


# ------------------------------------------------------------------------------------ AC-DOT-5 --
def test_fork_pr_degrades_visibly(tmp_path):
    """Markers-only mode reports the term scan as NOT RUN — never as clean."""
    root = _tree(tmp_path, {"a.py": "nothing interesting here\n"})
    r = subprocess.run([sys.executable, LEAK_SCAN_PATH, "--root", root, "--markers-only"],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "LEAK-SCAN-DEGRADED" in out, f"a degraded run must say so: {out}"
    assert "LEAK-SCAN-CLEAN" not in out, "a degraded run must NOT emit a clean term-scan verdict"


def test_empty_input_on_nonfork_event_refuses():
    """THE FAIL-OPEN GUARD. Keying 'degrade' on an empty denylist alone would silently downgrade
    every push to main to markers-only the moment the secret is deleted or renamed, while still
    reporting success — reintroducing this atom's own defect one layer down.

    Asserted on the action's control flow: the degrade branch must be guarded by the FORK signal,
    and an empty input on any other event must exit non-zero.
    """
    y = open(ACTION_YML, encoding="utf-8").read()
    # the degrade must be reachable only under the fork condition
    assert 'FOUNDRY_IS_FORK_PR}" = "true"' in y, "the degrade must be gated on the fork signal"
    assert "--markers-only" in y
    # and the non-fork empty-input path must refuse
    refuse_region = y.split('FOUNDRY_IS_FORK_PR}" = "true"', 1)[1]
    assert "exit 1" in refuse_region, "an empty input on a non-fork event must REFUSE"
    assert "REFUSED" in refuse_region

    # dynamic half: the module refuses when given no denylist and no explicit degrade
    r = subprocess.run([sys.executable, LEAK_SCAN_PATH, "--root", REPO_ROOT],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode != 0, "no denylist and no --markers-only must refuse"
    assert "DENYLIST-ERROR" in (r.stdout + r.stderr)


# ------------------------------------------------------------------------------------ AC-DOT-6 --
def test_prepublication_resolves_offtree_and_tracked_set_reconciled():
    src = open(PREPUB, encoding="utf-8").read()
    assert "default_denylist_path" in src
    assert "CLAUDE_PROJECT_DIR" in src, "the default must resolve from the workspace, not the tree"
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert 'os.path.join(_SCRIPTS_PARENT, ".github", "actions", "leak-gate", "denylist.txt")' not in code, \
        "the scanner still resolves a denylist INSIDE the tree"
    # fail-closed with neither source available
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    r = subprocess.run([sys.executable, PREPUB, "--root", REPO_ROOT],
                       capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode != 0, "no denylist source must REFUSE, never scan against an empty term set"


def test_prepublication_parses_the_new_hit_shape(tmp_path):
    """The integration seam that actually broke during this build: the scanner PARSES the shared
    module's hit strings. The old parser required the hit to end in a quote (the `repr()` tail), so
    against the new shape it returned None for every hit and degraded each real finding to
    `REDACTED` — fail-closed, but with the path, line and index the operator needs stripped out."""
    SCAN = load_module("scripts/foundry-prepublication-leak-scan.py", "prepub_dot")
    parsed = SCAN._split_name_term_rest("/x/y/a.py:42: term[3]")
    assert parsed == ("/x/y/a.py", 42, 3), parsed
    # a path containing a colon still resolves (the tail is anchored to the end)
    assert SCAN._split_name_term_rest("/x/we:ird/a.py:7: term[0]") == ("/x/we:ird/a.py", 7, 0)
    # an unrecognized shape still redacts rather than echoing
    assert SCAN._split_name_term_rest("garbage") is None


# ------------------------------------------------------------------------------------ AC-DOT-7 --
def test_docstring_names_only_live_consumers():
    doc = open(LEAK_SCAN_PATH, encoding="utf-8").read().split('"""')[1]
    assert "foundry_checks/reference-agents.py" not in doc or "no longer exist" in doc
    assert "foundry-prepublication-leak-scan.py" in doc, "the live consumer must be named"
    assert not os.path.isdir(os.path.join(REPO_ROOT, "scripts", "foundry_checks")), \
        "fixture assumption broken: scripts/foundry_checks/ exists again"


# ------------------------------------------------------------------------------------ AC-DOT-8 --
def test_antitaut_planted_term_still_detected(tmp_path):
    """THE POSITIVE CONTROL. Without it, every no-echo assertion is satisfied by a gate that simply
    stopped matching anything."""
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"a.py": "value = alpha\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert exit_code != 0, "a planted term was NOT detected — the gate is broken, not quiet"
    assert any(h.startswith("NAME-TERM") for h in hits)


def test_antitaut_finding_has_line_and_index_no_term(tmp_path):
    dl = _denylist(tmp_path)
    root = _tree(tmp_path, {"deep/b.py": "one\ntwo\nthree\nk = zeta\n"})
    _c, hits = LS.scan_tree(root, dl)
    hit = next(h for h in hits if h.startswith("NAME-TERM"))
    assert ":4:" in hit, f"wrong line number: {hit}"
    assert "term[3]" in hit, f"wrong term index: {hit}"
    # the no-substring assertion applies with the PATH removed: a fixture path may legitimately
    # contain term-like characters, and asserting over the whole finding would convict the path.
    tail = hit.split(root, 1)[-1]
    term = "zeta"
    for n in range(3, len(term) + 1):
        for i in range(len(term) - n + 1):
            assert term[i:i + n] not in tail.lower(), f"a >=3-char substring of the term leaked: {hit}"


def test_antitaut_finding_shape_is_term_independent(tmp_path):
    """Two DIFFERENT terms must yield findings identical in STRUCTURE — differing only in the path,
    line and index VALUES. Equal total length is deliberately NOT asserted: term[3] and term[11]
    legitimately differ in digit count, so the property is structural identity, not byte-length."""
    dl = _denylist(tmp_path)
    r1 = _tree(tmp_path, {"f.py": "a = alpha\n"}, name="t1")
    r2 = _tree(tmp_path, {"f.py": "a = bravo\n"}, name="t2")
    h1 = next(h for h in LS.scan_tree(r1, dl)[1] if h.startswith("NAME-TERM"))
    h2 = next(h for h in LS.scan_tree(r2, dl)[1] if h.startswith("NAME-TERM"))
    shape = re.compile(r"^NAME-TERM: (?P<p>.+):(?P<l>\d+): term\[(?P<i>\d+)\]$")
    m1, m2 = shape.match(h1), shape.match(h2)
    assert m1 and m2, (h1, h2)
    assert m1.group("l") == m2.group("l") == "1"
    assert {m1.group("i"), m2.group("i")} == {"0", "1"}, "indices must distinguish the two terms"
    # identical once the varying values are blanked
    assert shape.sub("X", h1) == shape.sub("X", h2)


def test_antitaut_absent_denylist_refuses_distinctly(tmp_path):
    root = _tree(tmp_path, {"a.py": "harmless\n"})
    # missing
    c1, h1 = LS.scan_tree(root, str(tmp_path / "nope.txt"))
    assert c1 != 0 and any(h.startswith("DENYLIST-ERROR") for h in h1)
    assert not any(h.startswith("NAME-TERM") for h in h1), "a denylist error must not look like a term hit"
    # zero-term
    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n\n", encoding="utf-8")
    c2, h2 = LS.scan_tree(root, str(empty))
    assert c2 != 0 and any("zero terms" in h for h in h2)


def test_antitaut_degraded_finds_marker_never_clean(tmp_path):
    """The degraded path must still catch what needs no secret, and must never report clean."""
    root = _tree(tmp_path, {"a.py": "id: " + "HBK" + "-7\n"})
    r = subprocess.run([sys.executable, LEAK_SCAN_PATH, "--root", root, "--markers-only"],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0, f"a planted structural marker must still be caught when degraded: {out}"
    assert "MARKER-HBK" in out
    assert "LEAK-SCAN-DEGRADED" in out
    assert "LEAK-SCAN-CLEAN" not in out


def test_antitaut_exclusion_is_realpath_not_blanket_path(tmp_path):
    """Exclusion must be by realpath of the MATERIALIZED denylist, not a blanket rule on the old
    in-tree location. A term planted at the would-be denylist path must still be FOUND."""
    dl = _denylist(tmp_path)  # lives OUTSIDE the scanned root
    root = _tree(tmp_path, {".github/actions/leak-gate/denylist.txt": "alpha\n"})
    exit_code, hits = LS.scan_tree(root, dl)
    assert exit_code != 0, "a term at the old in-tree denylist path was skipped by a blanket rule"
    assert any(h.startswith("NAME-TERM") and "leak-gate/denylist.txt" in h for h in hits), hits


def test_antitaut_the_real_denylist_file_is_still_self_excluded(tmp_path):
    """The positive control for the exclusion: the denylist actually in force is NOT scanned as a
    file (it would self-match every term). Only the file at its realpath is excluded."""
    dl = _denylist(tmp_path, name="inside.txt")
    root = str(tmp_path)  # scan the dir CONTAINING the denylist
    exit_code, hits = LS.scan_tree(root, dl)
    name_hits = [h for h in hits if h.startswith("NAME-TERM") and "inside.txt" in h]
    assert not name_hits, f"the denylist in force must be excluded from its own scan: {name_hits}"


# --------------------------------------------------- security review, PR #306 (Blocks B1 + B2) --
def test_offtree_denylist_blob_in_history_is_a_FINDING_not_denylist_origin(tmp_path):
    """B2 — THE FALSE GREEN THAT WOULD HAVE SHIPPED THE LEAK.

    `history_scope` buckets a blob byte-identical to the denylist as `denylist-origin` and excludes
    it from the verdict. That was correct while the denylist was a TRACKED FILE of the scanned repo —
    its own historical blobs were an expected artifact. This atom inverted the premise: the list is
    no longer in the tree, so such a blob IS the leak.

    Left ungated it blinded the gate to exactly this atom's headline defect, because the natural
    migration copies the in-tree file to the workspace byte-for-byte. Verified live on this repo
    before the fix: the retired in-tree blob and the workspace copy shared SHA fdd065c2, so the
    retired denylist was excluded from the verdict and the scan reported CLEAN with all 7 terms one
    `git cat-file` away from any visitor.
    """
    SCAN = load_module("scripts/foundry-prepublication-leak-scan.py", "prepub_b2")
    terms = ["alpha-fixture", "bravo-fixture"]
    content = ("\n".join(terms) + "\n").encode("utf-8")
    matcher = LS.build_matcher(terms)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    # the historical in-tree denylist, then its removal — the real migration shape
    (repo / "denylist.txt").write_bytes(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add"], cwd=repo, check=True)
    (repo / "denylist.txt").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "remove"], cwd=repo, check=True)

    # THE FIX: the denylist now lives OUTSIDE the scanned root.
    ok, findings, origin = SCAN.history_scope(
        str(repo), set(), terms, matcher, denylist_content=content, denylist_inside_root=False)
    assert ok is False, "a historical blob of the off-tree denylist must FAIL the history scope"
    assert findings and not origin, \
        "it must be a hard FINDING, not bucketed as an expected denylist-origin artifact"

    # THE NEGATIVE CONTROL: while the denylist genuinely lives in the repo, the bucket still
    # applies — otherwise this test would pass for a build that simply deleted the bucket.
    ok2, findings2, origin2 = SCAN.history_scope(
        str(repo), set(), terms, matcher, denylist_content=content, denylist_inside_root=True)
    assert ok2 is True and origin2 and not findings2, \
        "the in-repo case must still bucket as denylist-origin — the gate is on containment, not a deletion"


def test_every_early_exit_emits_the_verdict_sentinel_on_stdout(tmp_path):
    """B1 — a refusal that speaks only to stderr reads as 'no FOUND token' to a stdout-keyed
    consumer, which is indistinguishable from not-found. This atom added a third early exit
    (unresolvable denylist) and must not be the one that reopens Risk #8."""
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    r = subprocess.run([sys.executable, PREPUB, "--root", REPO_ROOT],
                       capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode != 0
    assert "PREPUB-LEAK-SCAN-FOUND" in r.stdout, \
        f"the refusal must emit the verdict sentinel on STDOUT, not only stderr: {r.stdout!r}"
    assert "PREPUB-LEAK-SCAN-CLEAN" not in r.stdout


def _run_action_script(tmp_path, *, denylist, is_fork, event_name):
    """Execute the composite action's ACTUAL shell body (R6).

    The fail-open guard is the single most load-bearing control in this atom and was previously
    asserted only by substring-matching the YAML — which passes for any file where `exit 1` appears
    anywhere after a marker, including a reordering that makes it unreachable. This extracts the
    real `run:` block and runs it under bash.
    """
    import yaml as _yaml
    doc = _yaml.safe_load(open(ACTION_YML, encoding="utf-8"))
    body = doc["runs"]["steps"][0]["run"]
    # The action interpolates ${{ github.workspace }}; substitute a real root for execution.
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    (root / "a.py").write_text("harmless\n", encoding="utf-8")
    body = body.replace("${{ github.workspace }}", str(root))
    env = dict(os.environ)
    env.update({
        "FOUNDRY_DENYLIST": denylist,
        "FOUNDRY_IS_FORK_PR": is_fork,
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_ACTION_PATH": os.path.dirname(ACTION_YML),
        "RUNNER_TEMP": str(tmp_path / "runner_temp"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    })
    (tmp_path / "runner_temp").mkdir(exist_ok=True)
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, env=env, timeout=300)


def test_action_shell_refuses_empty_denylist_on_push(tmp_path):
    """THE FAIL-OPEN GUARD, EXECUTED. A deleted or renamed secret on a push to main must REFUSE."""
    r = _run_action_script(tmp_path, denylist="", is_fork="false", event_name="push")
    assert r.returncode != 0, f"an empty denylist on a push must refuse: {r.stdout}{r.stderr}"
    assert "REFUSED" in (r.stdout + r.stderr)
    assert "DEGRADED" not in (r.stdout + r.stderr)


def test_action_shell_refuses_fork_flag_on_non_pull_request(tmp_path):
    """R1 — `is-fork-pr` is caller-asserted, so a workflow that hardcodes it true (or an expression
    that turns truthy on push after a refactor) must not be able to skip the term scan on a push."""
    r = _run_action_script(tmp_path, denylist="", is_fork="true", event_name="push")
    assert r.returncode != 0, f"a fork flag on a push event must NOT degrade: {r.stdout}{r.stderr}"
    assert "REFUSED" in (r.stdout + r.stderr)


def test_action_shell_degrades_only_on_a_real_fork_pull_request(tmp_path):
    """The positive control: the degrade path IS reachable where it legitimately applies. Without
    this, the two refusals above are satisfied by an action that refuses unconditionally."""
    r = _run_action_script(tmp_path, denylist="", is_fork="true", event_name="pull_request")
    out = r.stdout + r.stderr
    assert "DEGRADED" in out, f"a genuine fork PR must degrade, not refuse: {out}"
    assert "LEAK-SCAN-CLEAN" not in out, "a degraded run must never emit a clean term-scan verdict"


def test_action_shell_scans_and_cleans_up_when_the_secret_is_present(tmp_path):
    """End-to-end positive control: a real denylist runs a real scan, and the materialized file is
    removed and never lands inside the scanned root."""
    r = _run_action_script(tmp_path, denylist="alpha\nbravo\n", is_fork="false", event_name="push")
    out = r.stdout + r.stderr
    assert "::add-mask::alpha" in out, "each term must be registered for masking"
    assert "LEAK-SCAN-CLEAN" in out, out
    assert r.returncode == 0, out
    leftovers = list((tmp_path / "runner_temp").glob("foundry-denylist.*"))
    assert not leftovers, f"the materialized denylist was not cleaned up: {leftovers}"
    assert not list((tmp_path / "ws").rglob("foundry-denylist.*")), \
        "the denylist was materialized INSIDE the scanned root"
