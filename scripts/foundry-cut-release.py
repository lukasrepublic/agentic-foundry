#!/usr/bin/env python3
"""foundry-cut-release.py — the cut-release micro-workflow's thin enforcing seam (feat-foundry-cut-release).

The FIRST foundry micro-workflow's DETERMINISTIC-SEAM exit gate: verify the ordered cut preconditions →
REFUSE to emit any publish plan unless the EXISTING acceptance gate
(foundry-release-acceptance.run_acceptance) returns `pass` → emit the gotcha-correct publish plan
(annotated tag → re-pin marketplace source.sha to the TAG COMMIT → push main+tag, no force) as DATA.

It reimplements NO gate logic (it calls the existing acceptance verdict), adds NO new floor (the
orthogonal floors — front-authorization, the native merge floor (hooks/foundry-git-discipline.sh plus
the btb-gates checks), the acceptance HARD-STOP — already fired upstream), and NEVER runs `git tag` /
`git push` / any tree mutation. The operator executes the emitted
plan. No version inference (the operator picks the version); no release-time signing (a typed, dormant
seam gated to the first public release, per staged-security-threat-model).
"""
import argparse
import importlib.util
import json
import os
import subprocess
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


class CutReleaseError(Exception):
    pass


def _load_acceptance():
    """Load the sibling foundry-release-acceptance.py (hyphenated filename → by path) for run_acceptance.
    Read-only reuse of the EXISTING gate — this module never modifies it."""
    path = os.path.join(HERE, "foundry-release-acceptance.py")
    if not os.path.isfile(path):
        raise CutReleaseError("foundry-release-acceptance.py absent — cannot reach the acceptance gate")
    spec = importlib.util.spec_from_file_location("foundry_release_acceptance", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                    # fail-closed: an unimportable gate is never a silent pass
        raise CutReleaseError(f"acceptance gate unimportable: {e}")
    return mod


def run_acceptance(*, tree, project_dir=None):
    """Default acceptance_fn: a thin indirection to the EXISTING artifact-sha-bound acceptance gate."""
    return _load_acceptance().run_acceptance(tree=tree, project_dir=project_dir)


def _plugin_dir(tree):
    return os.path.join(tree, ".claude-plugin")


def select_plugin_entry(mdoc):
    """The marketplace.json plugins[] entry for 'foundry' — BY NAME, never by index.

    Shared by read_versions and tag_pin_coherence so the gate cannot end up validating a different
    plugin's pin than the one the cut is bumping. Indexing [0] happens to be right only while the
    manifest stays single-plugin; the moment a second plugin is listed first, an index-based gate
    silently grades the wrong entry and calls an incoherent release coherent."""
    plugs = mdoc.get("plugins")
    if not isinstance(plugs, list) or not plugs:
        raise CutReleaseError("marketplace.json has no plugins[] list")
    named = [p for p in plugs if isinstance(p, dict) and p.get("name") == "foundry"]
    if named:
        return named[0]
    if len(plugs) == 1 and isinstance(plugs[0], dict):
        return plugs[0]                       # single-plugin manifest → unambiguous
    raise CutReleaseError("marketplace.json has no plugins[] entry named 'foundry'")


def read_versions(tree):
    """{plugin, marketplace, marketplace_ref, marketplace_sha} from .claude-plugin/{plugin,marketplace}.json
    (the plugins[] entry named 'foundry'). Raises CutReleaseError on absent/malformed manifests."""
    pj = os.path.join(_plugin_dir(tree), "plugin.json")
    mj = os.path.join(_plugin_dir(tree), "marketplace.json")
    for p in (pj, mj):
        if not os.path.isfile(p):
            raise CutReleaseError(f"manifest absent: {os.path.relpath(p, tree)}")
    try:
        with open(pj, encoding="utf-8") as f:
            pdoc = json.load(f)
        with open(mj, encoding="utf-8") as f:
            mdoc = json.load(f)
    except (ValueError, OSError) as e:
        raise CutReleaseError(f"manifest unreadable: {e}")
    if not isinstance(pdoc, dict) or not isinstance(mdoc, dict):
        raise CutReleaseError("a manifest is not a JSON object")
    entry = select_plugin_entry(mdoc)
    src = entry.get("source")
    if not isinstance(src, dict):
        src = {}
    return {"plugin": pdoc.get("version"), "marketplace": entry.get("version"),
            "marketplace_ref": src.get("ref"), "marketplace_sha": src.get("sha"),
            "repo": src.get("repo")}


def changelog_section(tree, version):
    """The TEXT of the release's `## v<version>` CHANGELOG section — from its heading line up to (but not
    including) the next top-level `## ` heading, or end-of-file. '' when the section is absent. The trace
    source for the ER-reconciliation backstop (ER #134): the release manifest already authored on every cut."""
    path = os.path.join(tree, "CHANGELOG.md")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        body = f.read()
    m = re.search(rf"(?m)^##\s+v{re.escape(version)}(?=[\s—]|$).*", body)
    if not m:
        return ""
    start = m.start()
    nxt = re.search(r"(?m)^##\s+", body[m.end():])
    return body[start:] if nxt is None else body[start:m.end() + nxt.start()]


def traced_ers(section_text):
    """Sorted, de-duplicated ER issue numbers cited in a CHANGELOG section. CONVICT-ONLY on the explicit
    `ER #<digits>` marker (see convict-exonerate-asymmetry): the CHANGELOG references the ER (`ER #159`) and
    its implementing PR (`(#166)`, `PR #94`) in the SAME `#NNN` namespace — matching a bare `#NNN` would
    enumerate PRs and risk a `gh issue close` against a pull request. An unmarked number is not an ER."""
    return sorted({int(n) for n in re.findall(r"\bER #(\d+)", section_text or "")})


def changelog_has_section(tree, version):
    """True iff CHANGELOG.md has a `## v<version>` heading (the cut requires the section to exist)."""
    path = os.path.join(tree, "CHANGELOG.md")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        body = f.read()
    # tail lookahead (not \b) so a 0.7.0 request does NOT false-match `## v0.7.0.1` / `## v0.7.0-rc1`.
    return re.search(rf"(?m)^##\s+v{re.escape(version)}(?=[\s—]|$)", body) is not None


SUITE_CHECK_LABEL = "candidate tree test suite passes"
_SUITE_TIMEOUT_S = 1800


def suite_check(tree, *, runner=None):
    """AC-RSG-1/2: run the CANDIDATE tree's OWN suite and REFUSE on failure. -> (check, ok, detail).

    WHY THIS EXISTS. v0.27.0 shipped with a stale install pin: both manifests were bumped, but
    DEFAULT_MARKETPLACE_REF and the documented install lines still named the previous version, so an adopter
    who installed it and ran bootstrap got the OLD release. The four metadata preconditions above all passed
    and `run_acceptance` (validate --strict + plugin tag --dry-run + DOCTOR-GREEN) contains no pytest, so
    READY was returned TRUTHFULLY while the tree was failing its own suite. The pins are asserted only by
    tests/test_bootstrap_install_pin.py. The gate could not see the thing that broke.

    WHY THE SUITE AND NOT A PIN CHECK. A "is DEFAULT_MARKETPLACE_REF == target?" precondition would catch that
    one defect and would have to enumerate every version-bearing binding to stay correct (there are already
    three). This is the outcome-level control: it asserts all three today and the fourth the day someone
    writes that test, with no change here.

    FAIL-CLOSED. Anything that prevents a real verdict -- pytest absent, no tests/, a usage/internal error,
    nothing collected -- REFUSES, and names its cause distinctly from a test failure. `runner` is injected
    only so the selftest stays hermetic.
    """
    tests_dir = os.path.join(tree, "tests")
    if not os.path.isdir(tests_dir):
        return (SUITE_CHECK_LABEL, False,
                "no tests/ directory in the candidate tree -- cannot verify (fail-closed)")
    if runner is None:
        import subprocess

        def runner(t):
            # The verdict must be a function of the TREE, not of the operator's shell. A stray
            # PYTEST_ADDOPTS="--deselect tests/test_bootstrap_install_pin.py" left over from debugging would
            # otherwise deselect the very test this gate exists to enforce and return rc 0 — reproducing the
            # v0.27.0 incident THROUGH the gate, with no visible signal. PYTHONSAFEPATH stops a candidate
            # tree's own top-level pytest.py from shadowing the real one on sys.path[0].
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")}
            env["PYTHONSAFEPATH"] = "1"
            p = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
                               cwd=t, capture_output=True, text=True, timeout=_SUITE_TIMEOUT_S, env=env)
            return p.returncode, p.stdout, p.stderr
    try:
        rc, out, err = runner(tree)
    except Exception as e:                                     # noqa: BLE001 -- any failure to RUN refuses
        return (SUITE_CHECK_LABEL, False,
                f"could not run the suite: {type(e).__name__}: {e} (fail-closed)")
    if "No module named pytest" in (err or ""):
        return (SUITE_CHECK_LABEL, False, "pytest not importable -- cannot verify (fail-closed)")
    if rc == 0:
        return (SUITE_CHECK_LABEL, True, _suite_tail(out) or "suite passed")
    if rc == 5:
        return (SUITE_CHECK_LABEL, False, "no tests collected -- cannot verify (fail-closed)")
    if rc != 1:
        return (SUITE_CHECK_LABEL, False,
                f"suite could not complete (pytest exit {rc}) -- cannot verify (fail-closed)")
    return (SUITE_CHECK_LABEL, False, "TESTS FAILED -- " + (_failing_summary(out) or _suite_tail(out)
                                                            or "see the suite output"))


def _suite_tail(out):
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _failing_summary(out):
    """Surface the suite's OWN failing-test names, so the operator can act without re-running by hand.

    Each name is truncated: pytest's -q `FAILED` line appends the short assertion reason, which can carry
    the compared VALUES (an assertion over a token would land verbatim in a verdict an operator might paste
    into an issue). The node id is what makes the failure actionable; the diff is not."""
    names = [ln.strip()[:120] for ln in (out or "").splitlines() if ln.strip().startswith("FAILED")]
    if not names:
        return ""
    shown = "; ".join(names[:5])
    return shown + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")


def tag_pin_coherence(tree, version):
    """(ok, detail) for AC-TPC-1/-2/-4/-5: when tag vTARGET already exists, the marketplace.json
    reachable AT THAT TAG must pin a COMMIT (never an annotated-tag object) that is inside the
    released history and whose plugin.json version is TARGET.

    Adopters install by ref, so the tag's own tree is what a `marketplace add <repo>#vX.Y.Z`
    resolves. The old plan tagged BEFORE re-pinning, so the tag served the PREVIOUS release's sha
    and an install by ref delivered the previous version's code. That shipped twice (v1.0.0 and
    v1.0.1) before being caught. Absent tag => not applicable (a first cut cannot be coherent yet;
    the re-pin has not happened).
    """
    tag = f"v{version}"

    def git(*a):
        """(rc, stdout). rc=-1 means the command could not be RUN at all — never conflated with a
        clean non-zero exit, because 'I could not check' must not read as 'I checked and it is
        absent'. Mirrors suite_check's any-failure-to-RUN-refuses convention."""
        try:
            r = subprocess.run(["git", "-C", tree, *a],
                               capture_output=True, text=True, timeout=30)
        except Exception:
            return -1, ""
        return r.returncode, (r.stdout or "").strip()

    # --- FAIL CLOSED WHEN THE CHECK CANNOT RUN. `git rev-parse --verify refs/tags/X` exits non-zero
    # for "not a git repository", "dubious ownership" (safe.directory — routine in CI containers and
    # any checkout owned by another uid), a corrupt object store and permission errors, all
    # indistinguishably from "the tag is absent". Probing --git-dir first separates them, so a
    # tree the gate cannot inspect REFUSES instead of reporting not-applicable and sailing to READY.
    rc, _ = git("rev-parse", "--git-dir")
    if rc != 0:
        return False, (f"cannot verify the install pin: {tree} is not a readable git repository "
                       f"(git rev-parse --git-dir {'could not run' if rc < 0 else f'exited {rc}'}). "
                       f"The coherence gate refuses rather than reporting not-applicable.")

    rc, _ = git("rev-parse", "--verify", f"refs/tags/{tag}")
    if rc != 0:
        return True, (f"tag {tag} does not exist yet (first cut — not applicable; the emitted plan's "
                      f"order is what carries AC-TPC-3 here, and `--verify-tag` re-checks after tagging)")
    rc, blob = git("show", f"{tag}:.claude-plugin/marketplace.json")
    if rc != 0:
        return False, f"tag {tag} carries no .claude-plugin/marketplace.json"
    try:
        src = select_plugin_entry(json.loads(blob)).get("source") or {}
        sha, ref, repo = src.get("sha", ""), src.get("ref", ""), src.get("repo", "")
    except Exception as e:
        return False, f"marketplace.json at {tag} is unreadable: {type(e).__name__}: {e}"
    if ref != tag:
        return False, f"source.ref at {tag} is {ref!r}, expected {tag!r}"

    # --- source.sha MUST be a full 40-hex object name. git resolves arbitrary revision expressions,
    # so without this an unvalidated value like "main", "HEAD" or "v1.0.0^{commit}" types as a commit
    # and passes every check below — and a BRANCH NAME is a MUTABLE pin, which destroys the exact
    # immutable-artifact property AC-TPC-1 asserts. An abbreviation can also be ambiguous in a larger
    # clone than the one that cut the release.
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        return False, (f"source.sha at {tag} is {sha!r} — not a full 40-character lowercase hex "
                       f"commit id. A ref name or abbreviation is a MUTABLE pin: what an adopter "
                       f"resolves would change as the ref moves.")

    rc, kind = git("cat-file", "-t", sha)
    if rc != 0:
        return False, f"source.sha {sha[:12]}… at {tag} does not resolve in this repo"
    if kind != "commit":
        return False, (f"source.sha at {tag} names a {kind} object, not a commit — use "
                       f"`git rev-parse {tag}^{{commit}}`")

    rc, tagc = git("rev-parse", f"{tag}^{{commit}}")
    if rc != 0:
        return False, f"cannot resolve the commit for tag {tag}"
    rc, parent = git("rev-parse", f"{tag}^{{commit}}~1")
    parent = parent if rc == 0 else ""

    # --- The pin must be the TAG COMMIT ITSELF or its FIRST PARENT — not merely an ancestor.
    # `merge-base --is-ancestor` admits every commit reachable from the tag, which after any merge is
    # every commit of every branch ever merged in. Since the version bump lands at the START of
    # release work, several ancestors carry the target version, so an ancestor-plus-version check
    # would admit a pin naming the version-bump commit and silently omit everything that landed
    # after it — the same "adopter receives the wrong code" outcome, displaced by a few commits.
    # The plan's own shape is exactly two: R2 (tag == pinning commit) or R (its parent).
    if sha not in (tagc, parent):
        rc, anc = git("merge-base", "--is-ancestor", sha, f"{tag}^{{commit}}")
        where = "an ancestor of, but not adjacent to," if anc == 0 and rc == 0 else "NOT in"
        return False, (f"source.sha {sha[:12]}… at {tag} is {where} the released history. The pin "
                       f"MUST name the tag commit ({tagc[:12]}…) or its first parent "
                       f"({parent[:12] + '…' if parent else 'none'}) — the plan's R2/R shape. A pin "
                       f"reaching further back omits everything committed after it.")

    # --- both the PINNED commit and the TAG commit must carry the target version. The pinned commit
    # is what a sha-resolving client installs; the tag commit is what a ref-resolving (or
    # ref-falling-back) client installs. Checking only the former leaves the ref path unasserted.
    for label, commit in (("pinned commit", sha), ("tag commit", tagc)):
        rc, pj = git("show", f"{commit}:.claude-plugin/plugin.json")
        if rc != 0:
            return False, f"the {label} {commit[:12]}… has no .claude-plugin/plugin.json"
        try:
            found = json.loads(pj).get("version")
        except Exception:
            return False, f"plugin.json at the {label} {commit[:12]}… is unreadable"
        if found != version:
            return False, (f"INCOHERENT INSTALL PIN: installing #{tag} resolves the {label} "
                           f"{commit[:12]}…, whose plugin.json says version {found!r} — but this "
                           f"release is {version!r} (tag commit {tagc[:12]}…). An adopter "
                           f"installing by ref would receive {found!r}, not {version!r}. Re-pin "
                           f"BEFORE tagging, then place the tag on the re-pin commit.")

    # --- source.repo cross-check. The gate resolves the sha against the LOCAL repo, which is only
    # meaningful if the local repo IS source.repo. Refuse on a definite mismatch; when origin is
    # unresolvable say so in the detail rather than implying the check ran.
    rc, origin = git("remote", "get-url", "origin")
    note = ""
    if rc == 0 and origin and repo:
        norm = origin.rstrip("/")
        for pre in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
            if norm.startswith(pre):
                norm = norm[len(pre):]
        norm = norm[:-4] if norm.endswith(".git") else norm
        if norm.lower() != str(repo).lower():
            return False, (f"source.repo at {tag} is {repo!r} but origin resolves to {norm!r}. The "
                           f"pin was verified against THIS repo's objects; an install by ref would "
                           f"fetch from {repo!r}, which was not checked.")
    elif repo:
        note = f"; source.repo {repo!r} NOT cross-checked (no resolvable origin remote)"
    return True, (f"install pin at {tag} resolves {sha[:12]}… (version {version}, "
                  f"{'tag commit' if sha == tagc else 'tag parent'}) — coherent{note}")


def worktree_clean(tree):
    """(ok, detail) — the candidate tree has no uncommitted tracked changes.

    Load-bearing because of the AC-TPC-3 reorder: the tag now lands on R2, a commit made AFTER the
    acceptance verdict. Any modification sitting here at cut time would be committed into R2 by the
    plan's re-pin step and published under the release tag having never been seen by the suite or
    the acceptance gate. Fails CLOSED when git cannot be run, same as tag_pin_coherence."""
    try:
        r = subprocess.run(["git", "-C", tree, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        return False, f"cannot check the working tree: {type(e).__name__}: {e}"
    if r.returncode != 0:
        return False, f"cannot check the working tree: git status exited {r.returncode}"
    dirty = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if dirty:
        return False, ("uncommitted changes present — the re-pin commit (R2) would sweep them into "
                       f"the release tag ungated: {'; '.join(dirty[:5])}"
                       f"{f' (+{len(dirty) - 5} more)' if len(dirty) > 5 else ''}")
    return True, "clean"


def preflight(tree, version, *, suite_runner=None):
    """The ordered cut preconditions as [(check, ok, detail)]. ok iff plugin==marketplace==version ∧
    source.ref==v<version> ∧ CHANGELOG has the `## v<version>` section ∧ the candidate tree's suite passes.
    DOCTOR-GREEN is the acceptance gate's job and is NOT re-checked here (no reimplemented gate logic).

    ORDERING IS LOAD-BEARING (AC-RSG-3): the four cheap metadata checks are evaluated FIRST and, if any
    fails, this returns WITHOUT running the suite -- a typo'd version still refuses in milliseconds instead
    of paying two-to-three minutes to be told what a string compare already knew.
    """
    v = read_versions(tree)
    tag = f"v{version}"
    cl_present = changelog_has_section(tree, version)
    cheap = [
        ("plugin.json version == target",
         v["plugin"] == version, f"plugin.json={v['plugin']!r} target={version!r}"),
        ("marketplace.json version == target (bump BOTH manifests)",
         v["marketplace"] == version, f"marketplace.json={v['marketplace']!r} target={version!r}"),
        ("marketplace source.ref == vTARGET",
         v["marketplace_ref"] == tag, f"source.ref={v['marketplace_ref']!r} expected={tag!r}"),
        ("CHANGELOG.md has the ## vTARGET section",
         cl_present, f"## {tag} section: present={cl_present}"),
        # AC-TPC-9: the tree must be CLEAN. The reorder creates R2 (and places the tag on it) after
        # the acceptance verdict, so anything left modified here would be swept into the release
        # tag without ever having been gated. Checked cheaply, before the suite.
        ("working tree is clean (R2 is created after the gate runs)",) + worktree_clean(tree),
        # AC-TPC-2: what an adopter installing by ref actually resolves. Not applicable until the
        # tag exists, so a first cut passes and a RE-run convicts an incoherent one.
        ("install pin at vTARGET resolves this release",) + tag_pin_coherence(tree, version),
    ]
    if not all(c[1] for c in cheap):
        return cheap
    return cheap + [suite_check(tree, runner=suite_runner)]


def publish_plan(tree, version):
    """The ordered publish plan as DATA (command strings) — NEVER executed.

    ORDER IS LOAD-BEARING (feat-foundry-tag-pin-coherence, AC-TPC-3): RE-PIN FIRST, THEN TAG.
    Adopters install by ref, which resolves marketplace.json AT THE TAG. The previous order tagged
    the release commit and re-pinned afterwards, so the tag's own tree still carried the PREVIOUS
    release's sha and `marketplace add <repo>#vX.Y.Z` delivered the PREVIOUS version's code. That
    shipped twice (v1.0.0, v1.0.1) and was hand-corrected both times by moving the tag.

    A commit cannot contain its own hash, so the pin necessarily names the CONTENT commit (R) while
    the tag sits on the PINNING commit (R2). Both are inside the released history, which is exactly
    what the gate's adjacency check (AC-TPC-5) asserts.

    THE RE-PIN COMMIT IS PATH-SCOPED, AND THE TAG IS RE-VERIFIED BEFORE ANY PUSH. Reordering moves
    the tag off R (the commit the acceptance gate inspected) and onto R2, which is created AFTER the
    verdict. A bare `commit -am` would sweep every modified tracked file into R2 and publish it under
    the release tag having never been gated. Committing ONLY the manifest keeps R2's delta to the one
    line the reorder exists for, and the `--verify-tag` step re-runs the machine check against the
    tag that actually now exists — which is the ONLY point at which the coherence gate is not
    structurally a no-op.
    """
    tag = f"v{version}"
    return [
        f"git -C {tree} status --porcelain                         # MUST be empty: R2 is created after the gate ran",
        f"CONTENT=$(git -C {tree} rev-parse HEAD)                  # the release commit (R) — the code being shipped",
        "# edit .claude-plugin/marketplace.json: set plugins[foundry].source.sha = $CONTENT (and source.ref = "
        f"{tag})",
        f"git -C {tree} commit -m 'release: re-pin marketplace source.sha to the {tag} content commit' "
        f"-- .claude-plugin/marketplace.json  # (R2) PATH-SCOPED — commit ONLY the manifest, or stray "
        f"working-tree edits are swept into the tag ungated",
        f"git -C {tree} tag -a {tag} -m 'agentic-foundry {tag}'    # annotated tag at R2 — the commit that CARRIES the pin",
        f"python3 scripts/foundry-cut-release.py --tree {tree} --version {version} --verify-tag  # MACHINE re-check; must print TAG-PIN-COHERENT",
        f"git -C {tree} push origin main                           # never force-push",
        f"git -C {tree} push origin {tag}                          # if rejected by a parallel push: reconcile by MERGE, never force-push",
    ]


def signing_seam():
    """Typed, DISABLED release-time signing/provenance seam. Sigstore/SLSA is gated to the first PUBLIC
    release (staged-security-threat-model); a private release does not trigger it. No behavior today."""
    return {"status": "not-applicable", "reason": "signing gated to first public release (deferred by design)"}


def er_reconcile_steps(version, ers, repo=None):
    """The ER-reconciliation backstop (ER #134 part 2) as plan DATA. One operator-executed `gh issue close`
    per traced ER, in ascending order, with a release-referencing comment — appended AFTER the push steps so
    an ER only closes once its release is on `main`. Empty list when the release traces to no ER (no spurious
    command). NEVER executed here — the operator runs it, exactly as with the tag/push plan."""
    if not ers:
        return []
    rflag = f" -R {repo}" if repo else ""
    comment = f"Released in v{version} — reconciled at cut by /foundry:cut-release (ER #134 backstop)."
    return [f"gh{rflag} issue close {n} --reason completed --comment '{comment}'"
            f"   # ER-reconciliation backstop (ER #134); no-op if already closed" for n in ers]


def _gh_er_state(ers, repo=None):
    """Default `er_state_fn`: resolve each traced ER's open/closed state via `gh issue view`. Read-only, the
    ONLY network touchpoint, and FAIL-SAFE — returns None (→ `unavailable`, state unknown) on absent `gh`, a
    query error, or a timeout, so an offline cut NEVER reports an ER as closed on unknown state (R3)."""
    import subprocess
    rflag = ["-R", repo] if repo else []
    out = {}
    for n in ers:
        try:
            p = subprocess.run(["gh", *rflag, "issue", "view", str(n), "--json", "state", "-q", ".state"],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if p.returncode != 0:
            return None
        out[n] = p.stdout.strip().lower()
    return out


def cut_release(tree, version, *, acceptance_fn=None, er_state_fn=None, suite_runner=None):
    """The deterministic-seam. preflight → (if ok) acceptance_fn(tree) → (if pass) publish_plan. Returns a
    verdict dict {state ∈ refused|gated|ready, ...}. NEVER runs git tag/push/gh or mutates the tree.
    acceptance_fn and er_state_fn are dependency-injected (defaults to the real gate / gh resolver) so the
    selftest is hermetic. On READY, appends the ER-reconciliation backstop (ER #134): `traced_ers` from the
    CHANGELOG section, `gh issue close` plan steps, and a fail-safe `open_ers` advisory."""
    acceptance_fn = acceptance_fn or run_acceptance
    if er_state_fn is None:
        er_state_fn = _gh_er_state
    pf = preflight(tree, version, suite_runner=suite_runner)
    failed = [c for c in pf if not c[1]]
    if failed:
        return {"state": "refused", "stage": "preflight", "preflight": pf,
                "failures": [f"{c[0]}: {c[2]}" for c in failed], "plan": None}
    verdict = acceptance_fn(tree=tree)
    if verdict.get("verdict") != "pass":
        return {"state": "gated", "stage": "acceptance", "preflight": pf, "acceptance": verdict,
                "failures": list(verdict.get("failures", [])), "plan": None}
    repo = read_versions(tree).get("repo")
    ers = traced_ers(changelog_section(tree, version))
    plan = publish_plan(tree, version) + er_reconcile_steps(version, ers, repo)
    states = er_state_fn(ers, repo=repo) if ers else None     # unknown state ≠ closed (fail-safe, R3)
    er_state_checked = states is not None
    open_ers = sorted(n for n in ers if str(states.get(n, "")).lower() == "open") if er_state_checked else []
    return {"state": "ready", "stage": "publish-plan", "preflight": pf, "acceptance": verdict,
            "signing": signing_seam(), "plan": plan, "traced_ers": ers,
            "open_ers": open_ers, "er_state_checked": er_state_checked, "failures": []}


def _selftest():
    # Defined INSIDE _selftest so it is structurally unreachable from main() — a module-level stub runner
    # is one stray edit away from being wired into the real cut path (which is exactly what happened once).
    _GREEN_SUITE = (lambda _t: (0, "1 passed", ""))
    """Hermetic proof of AC-CREL-1/2/3 over throwaway temp trees + an INJECTED acceptance stub — it never
    invokes the real plugin CLI and never runs git tag/push. Emits the FROZEN tokens."""
    import tempfile
    lines, ok = [], True

    def emit(token, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        lines.append(f"{token}: {'PASS' if passed else 'FAIL'}{(' — ' + detail) if detail else ''}")

    def write_tree(d, *, plugin_v, mp_v, mp_ref, changelog_v):
        pdir = os.path.join(d, ".claude-plugin")
        os.makedirs(pdir, exist_ok=True)
        json.dump({"name": "foundry", "version": plugin_v}, open(os.path.join(pdir, "plugin.json"), "w"))
        json.dump({"name": "agentic-foundry", "plugins": [
            {"name": "foundry", "version": mp_v, "source": {
                "source": "github", "repo": "lukasrepublic/agentic-foundry", "ref": mp_ref, "sha": "0" * 40}}]},
            open(os.path.join(pdir, "marketplace.json"), "w"))
        with open(os.path.join(d, "CHANGELOG.md"), "w") as f:
            f.write(f"# Changelog\n\n## v{changelog_v} — 2026-01-01\n\nstuff\n")
        # A real candidate tree HAS a suite. suite_check's absent-tests/ arm is fail-closed and fires
        # BEFORE the injected runner (deliberately -- a missing suite must never be rescuable by
        # injection), so the fixture needs the directory for _GREEN_SUITE to be reached at all.
        os.makedirs(os.path.join(d, "tests"), exist_ok=True)
        open(os.path.join(d, "tests", ".keep"), "w").close()
        # A real candidate tree is ALSO a committed git checkout. The fixtures used to omit .git
        # entirely, which meant tag_pin_coherence and worktree_clean could not run at all — and while
        # the coherence check still reported "not applicable" that made the selftest a standing proof
        # of READY on a tree the gate was structurally unable to inspect. Both now fail CLOSED on a
        # non-repo, so the fixture must be a real repo or it would be proving the wrong thing.
        for cmd in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "selftest@example.invalid"],
                    ["config", "user.name", "selftest"],
                    ["add", "-A"], ["commit", "-qm", "selftest fixture"]):
            subprocess.run(["git", "-C", d, *cmd], capture_output=True, text=True, timeout=30)

    def snapshot(d):
        out = {}
        for r_, _, files in os.walk(d):
            for fn in files:
                p = os.path.join(r_, fn)
                out[os.path.relpath(p, d)] = open(p, "rb").read()
        return out

    PASS = {"verdict": "pass", "artifact_sha": "sha256:deadbeef", "failures": []}
    FAIL = {"verdict": "fail", "artifact_sha": "sha256:deadbeef", "failures": ["foundry-doctor (candidate tree): RED"]}

    # AC-CREL-1: preflight REFUSES a misordered/incomplete cut and names the failing precondition.
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.6.0", mp_ref="v0.7.0", changelog_v="0.7.0")  # marketplace NOT bumped
        r = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, suite_runner=_GREEN_SUITE)
        mp_refused = r["state"] == "refused" and r["plan"] is None and any("marketplace" in f for f in r["failures"])
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.6.0")  # CHANGELOG section missing
        r = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, suite_runner=_GREEN_SUITE)
        cl_refused = r["state"] == "refused" and r["plan"] is None and any("CHANGELOG" in f for f in r["failures"])
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.6.0", changelog_v="0.7.0")  # source.ref stale
        r = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, suite_runner=_GREEN_SUITE)
        ref_refused = r["state"] == "refused" and any("source.ref" in f for f in r["failures"])
    emit("cut-release-preflight-refuses-misordered",
         mp_refused and cl_refused and ref_refused,
         f"marketplace_not_bumped={mp_refused} changelog_missing={cl_refused} source_ref_stale={ref_refused}")

    # AC-CREL-2: with preconditions OK, the publish plan is GATED on acceptance==pass (HARD-STOP on fail).
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.7.0")
        gated = cut_release(d, "0.7.0", acceptance_fn=lambda **k: FAIL, suite_runner=_GREEN_SUITE)
        ready = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, suite_runner=_GREEN_SUITE)
        gate_holds = (gated["state"] == "gated" and gated["plan"] is None
                      and any("RED" in f for f in gated["failures"])
                      and ready["state"] == "ready" and ready["plan"] is not None)
    emit("cut-release-gates-on-acceptance-pass", gate_holds,
         f"fail->gated_no_plan={gated['state'] == 'gated' and gated['plan'] is None} "
         f"pass->ready_with_plan={ready['state'] == 'ready' and ready['plan'] is not None}")

    # AC-CREL-3: the emitted plan carries the gotcha-correct commands (tag-COMMIT re-pin, no --force) and
    # cut_release has NO side effects (tree byte-unchanged; never created a .git; plan is pure data).
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.7.0")
        before = snapshot(d)
        refs_before = subprocess.run(["git", "-C", d, "show-ref"],
                                     capture_output=True, text=True, timeout=30).stdout
        head_before = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                                     capture_output=True, text=True, timeout=30).stdout
        r = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, suite_runner=_GREEN_SUITE)
        after = snapshot(d)
        plan = r["plan"] or []
        joined = "\n".join(plan)
        # AC-TPC-3: the plan must RE-PIN BEFORE TAGGING. Asserted on the index of the EXECUTABLE
        # STEPS, never on substring positions in the joined text. A substring search finds
        # "source.sha" in the human-readable `# edit …` COMMENT, so swapping the actual `git commit`
        # and `git tag -a` steps — the literal v1.0.0/v1.0.1 defect — still satisfied it. That is a
        # vacuous assertion, and it was here.
        def _step(pred):
            return next((n for n, s in enumerate(plan) if pred(s)), -1)
        repin_at = _step(lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
        tag_at = _step(lambda s: s.startswith("git ") and " tag -a v0.7.0" in s)
        repin_before_tag = 0 <= repin_at < tag_at
        # …and the re-pin commit must be PATH-SCOPED. `commit -am` sweeps every modified tracked file
        # into R2, which the tag then points at — content no gate ever inspected.
        path_scoped = (repin_at >= 0 and "commit -am" not in plan[repin_at]
                       and plan[repin_at].split("#")[0].rstrip().endswith(".claude-plugin/marketplace.json"))
        verify_after_tag = 0 <= tag_at < _step(lambda s: "--verify-tag" in s)
        tag_commit_repin = repin_before_tag and path_scoped and verify_after_tag and "rev-parse HEAD" in joined
        no_force = ("push origin main" in joined and "push origin v0.7.0" in joined and "--force" not in joined)
        # The fixture is now a real repo, so "no side effects" is asserted the way the pytest suite
        # asserts it: refs and HEAD unmoved, tree byte-identical — not "never created a .git".
        rc_refs = subprocess.run(["git", "-C", d, "show-ref"], capture_output=True, text=True, timeout=30)
        rc_head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        no_side_effects = (after == before and all(isinstance(x, str) for x in plan)
                           and rc_refs.stdout == refs_before and rc_head.stdout == head_before)
    # BOTH tokens are emitted. The prior atom's contract (specs/.../release-eng/cut-release,
    # auth_seq=1) asserts the ORIGINAL token by exact string; renaming it would silently stop that
    # authorized checkpoint from matching, and a release-gate assertion that quietly stops asserting
    # is worse than the rename is tidy. The new token names what the check now covers.
    emit("cut-release-emits-tag-commit-repin-plan-no-push",
         tag_commit_repin and no_force and no_side_effects,
         f"tag_commit_repin={tag_commit_repin} no_force_push={no_force} no_side_effects={no_side_effects}")
    emit("cut-release-emits-repin-then-tag-plan-no-push",
         repin_before_tag and path_scoped and verify_after_tag,
         f"repin_step_before_tag_step={repin_before_tag} repin_is_path_scoped={path_scoped} "
         f"verify_tag_after_tag={verify_after_tag}")

    # ER #134 backstop fixtures: a two-section CHANGELOG whose v0.7.0 section cites ER #12 + ER #7 alongside
    # a bare #999 and a PR ref, and a v0.6.0 section citing ER #34 (must NOT leak into the v0.7.0 trace).
    def write_er_changelog(d):
        # NOTE: commits at the end. preflight now requires a CLEAN working tree (the re-pin commit
        # R2 is created after the acceptance verdict, so a dirty tree would ship ungated content
        # under the release tag), and a fixture that edits after write_tree's commit is dirty.
        with open(os.path.join(d, "CHANGELOG.md"), "w") as f:
            f.write("# Changelog\n\n"
                    "## v0.7.0 — 2026-01-01 — the cut\n\n"
                    "Fixed the thing (`cap`, AC-X, ER #12). Also closes ER #7. Landed in PR #500 and #999.\n\n"
                    "## v0.6.0 — 2025-12-01 — prior\n\n"
                    "An older fix (ER #34) that belongs to the previous release.\n")
        subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "-C", d, "commit", "-qm", "er changelog"],
                       capture_output=True, text=True, timeout=30)

    UNAVAILABLE = lambda ers, **k: None                       # fail-safe: gh absent/offline

    # AC-CRER-1: traced_ers derives from the release's OWN section, convict-only on `ER #<digits>`.
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.7.0")
        write_er_changelog(d)
        r = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, er_state_fn=UNAVAILABLE, suite_runner=_GREEN_SUITE)
        traced_ok = r["state"] == "ready" and r["traced_ers"] == [7, 12]  # #999/#500 (bare/PR) & #34 (other section) excluded
    emit("cut-release-traces-ers-from-changelog", traced_ok,
         f"traced_ers={r.get('traced_ers')} (expected [7, 12]; bare #999, PR #500, other-section ER #34 excluded)")

    # AC-CRER-2: the plan appends one gh-issue-close step per traced ER, after push, version in the comment;
    # a no-ER release appends none.
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.7.0")
        write_er_changelog(d)
        r = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, er_state_fn=UNAVAILABLE, suite_runner=_GREEN_SUITE)
        plan = r["plan"] or []
        joined = "\n".join(plan)
        has_closes = "gh" in joined and "issue close 7" in joined and "issue close 12" in joined
        comment_versioned = joined.count("v0.7.0 — reconciled") >= 2
        push_idx = max((i for i, s in enumerate(plan) if "push origin v0.7.0" in s), default=-1)
        close_idx = min((i for i, s in enumerate(plan) if "issue close" in s), default=-1)
        after_push = push_idx >= 0 and close_idx > push_idx
    with tempfile.TemporaryDirectory() as d2:
        write_tree(d2, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.7.0")  # default CHANGELOG: no ER
        r2 = cut_release(d2, "0.7.0", acceptance_fn=lambda **k: PASS, er_state_fn=UNAVAILABLE, suite_runner=_GREEN_SUITE)
        no_er_no_step = r2["traced_ers"] == [] and not any("issue close" in s for s in (r2["plan"] or []))
    emit("cut-release-emits-er-close-plan-steps", has_closes and comment_versioned and after_push and no_er_no_step,
         f"has_closes={has_closes} versioned_comment={comment_versioned} after_push={after_push} no_er_no_step={no_er_no_step}")

    # AC-CRER-3: the open-ER advisory is fail-safe and non-gating — a stubbed state resolver flags OPEN ERs
    # without changing state; an unavailable resolver reports nothing closed.
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, plugin_v="0.7.0", mp_v="0.7.0", mp_ref="v0.7.0", changelog_v="0.7.0")
        write_er_changelog(d)
        STUB = lambda ers, **k: {7: "open", 12: "closed"}
        rc = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, er_state_fn=STUB, suite_runner=_GREEN_SUITE)
        checked_ok = rc["state"] == "ready" and rc["er_state_checked"] is True and rc["open_ers"] == [7]
        ru = cut_release(d, "0.7.0", acceptance_fn=lambda **k: PASS, er_state_fn=UNAVAILABLE, suite_runner=_GREEN_SUITE)
        unavail_ok = ru["er_state_checked"] is False and ru["open_ers"] == [] and ru["state"] == "ready"
    emit("cut-release-open-ers-advisory-failsafe", checked_ok and unavail_ok,
         f"checked(open_ers=[7],non_gating)={checked_ok} unavailable(nothing_closed)={unavail_ok}")

    print("foundry-cut-release self-test:")
    for ln in lines:
        print("  " + ln)
    print("CUT-RELEASE-SELFTEST-GREEN" if ok else "CUT-RELEASE-SELFTEST-RED")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="cut-release micro-workflow — verify the ordered preconditions, "
                                             "gate on the existing acceptance verdict, emit the publish plan "
                                             "(NEVER pushes; the operator executes the plan).")
    ap.add_argument("--tree", help="path to the candidate plugin tree (a committed checkout)")
    ap.add_argument("--version", help="the release version being cut (the operator picks it; no inference)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--verify-tag", action="store_true",
                    help="re-run ONLY the install-pin coherence check against an EXISTING tag "
                         "vTARGET, after tagging and BEFORE pushing. This is the step at which the "
                         "check stops being a no-op: during a first cut the tag does not exist yet, "
                         "so cut-release can only assert the PLAN's order, not the artifact.")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.tree or not args.version:
        ap.error("--tree and --version are required (unless --selftest)")
    if args.verify_tag:
        # A dedicated, callable post-tag verifier. Without it AC-TPC-2 has no caller on the cut that
        # creates the tag, and the only defense against recurrence is the operator following the
        # plan's order by hand — which is exactly the procedural failure this atom exists to end.
        ok, detail = tag_pin_coherence(os.path.abspath(args.tree), args.version)
        print(f"{'TAG-PIN-COHERENT' if ok else 'TAG-PIN-INCOHERENT'}: {detail}")
        return 0 if ok else 2
    try:
        # NO suite_runner here, EVER. The CLI must use the real subprocess runner; injecting a stub on this
        # path would make every cut report a fabricated green suite — strictly worse than the v0.27.0 hole
        # this atom closes, because v0.27.0 at least made no claim about tests. Guarded by
        # test_cli_path_never_injects_a_suite_runner and by _GREEN_SUITE being local to _selftest.
        r = cut_release(os.path.abspath(args.tree), args.version)
    except CutReleaseError as e:
        print(f"cut-release error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r, indent=2))
        return 0 if r["state"] == "ready" else 2
    print(f"cut-release: state={r['state'].upper()}  version={args.version}")
    for (name, okc, detail) in r["preflight"]:
        print(f"  [{'ok ' if okc else 'XX '}] {name}: {detail}")
    if r["state"] == "refused":
        print("\nREFUSED (preflight) — fix the failing precondition(s) above in the frozen order, then re-run.")
    elif r["state"] == "gated":
        print("\nHARD-STOP — the acceptance gate verdict is FAIL; nothing tagged/pushed. Failures:")
        for f in r["failures"]:
            print(f"  - {f}")
    else:
        print(f"\nacceptance: PASS ({(r['acceptance'].get('artifact_sha') or '')[:16]})  signing: {r['signing']['status']}")
        ers = r.get("traced_ers") or []
        if ers:
            print(f"traced ERs (from CHANGELOG ## v{args.version}): {ers}")
            if r.get("er_state_checked") and r.get("open_ers"):
                print(f"  ⚠️  ADVISORY — traced ERs still OPEN at cut: {r['open_ers']} — the plan's "
                      "`gh issue close` steps will reconcile them (ER #134 backstop).")
            elif not r.get("er_state_checked"):
                print("  (ER open/closed state not checked — gh unavailable/offline; the reconciliation "
                      "plan steps are still emitted below.)")
        print("PUBLISH PLAN — cut-release NEVER runs these; the operator executes them:")
        for step in r["plan"]:
            print(f"  $ {step}")
    return 0 if r["state"] == "ready" else 2


if __name__ == "__main__":
    sys.exit(main())
