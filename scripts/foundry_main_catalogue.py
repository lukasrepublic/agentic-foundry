#!/usr/bin/env python3
"""foundry_main_catalogue.py — the default-branch catalogue coherence check
(feat-foundry-main-catalogue-coherence).

Today the blob an adopter resolves is the release TAG's `.claude-plugin/marketplace.json`, and
that blob is graded at cut time by `tag_pin_coherence` (`scripts/foundry-cut-release.py:306`).
Sibling work drops the `#vX.Y.Z` from this plugin's marketplace registration, which moves
resolution from the tag's blob to the DEFAULT BRANCH's blob — a blob graded, until this module,
by nothing.

`main_catalogue_coherence(tree, default_branch=...)` reads the default branch's committed
`.claude-plugin/marketplace.json` for the `plugins[]` entry named `foundry` and REFUSES unless:

  (a) `source.sha` is a full 40-character lowercase hex object id — AC-MCC-2;
  (b) that id resolves to a git COMMIT object — never an annotated-tag object — AC-MCC-2;
  (c) that id is an ANCESTOR of the default branch (`git merge-base --is-ancestor`) — AC-MCC-3;
  (d) the `.claude-plugin/plugin.json` committed AT THAT SHA declares a `version` equal to
      the catalogue's own advertised `version` — AC-MCC-1.

Ancestry, not adjacency: `tag_pin_coherence` rejects mere ancestry for a TAG because a tag's
history has exactly two admissible shapes (the tag commit or its first parent). The default
branch accumulates every merge and has no such shape, so ancestry is the correct and only
available reachability predicate here — deliberate divergence, not a copy error (see the spec's
"Why ancestry here and adjacency there").

Fail-closed: anything that prevents a real verdict — an unreadable git directory, a default
branch that does not resolve, a missing or malformed manifest, a `git` invocation that could not
be run at all — REFUSES, never reports not-applicable. Mirrors
`scripts/foundry-cut-release.py:341-345`.

Threat model: trusted operator; a READ-ONLY detective control. This module performs no
mutation and no network access (AC-MCC-6) — it only ever runs `git rev-parse`, `git cat-file -t`,
`git show` and `git merge-base --is-ancestor` against the local object store, and grades a claim
already committed, not authenticity.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

_HEX40_RE = re.compile(r"[0-9a-f]{40}")


def _git(tree, *args):
    """(rc, stdout) for a read-only `git -C tree <args>`. rc=-1 means the command could not be RUN
    at all — never conflated with a clean non-zero exit, so "I could not check" never reads as
    "I checked and it is absent"."""
    try:
        r = subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True, timeout=30)
    except Exception:
        return -1, ""
    return r.returncode, (r.stdout or "").strip()


def select_plugin_entry(mdoc):
    """The marketplace.json `plugins[]` entry for `foundry` — BY NAME, never by index (mirrors
    `scripts/foundry-cut-release.py:select_plugin_entry`, duplicated here rather than imported so
    this module has no in-tree dependency beyond the stdlib)."""
    plugs = mdoc.get("plugins")
    if not isinstance(plugs, list) or not plugs:
        raise ValueError("marketplace.json has no plugins[] list")
    named = [p for p in plugs if isinstance(p, dict) and p.get("name") == "foundry"]
    if named:
        return named[0]
    if len(plugs) == 1 and isinstance(plugs[0], dict):
        return plugs[0]                       # single-plugin manifest → unambiguous
    raise ValueError("marketplace.json has no plugins[] entry named 'foundry'")


def main_catalogue_coherence(tree, *, default_branch="main"):
    """(ok, detail) for AC-MCC-1/-2/-3/-4: the default branch's committed
    `.claude-plugin/marketplace.json` (the `foundry` entry) must pin a commit that is reachable
    from the default branch and whose committed `.claude-plugin/plugin.json` carries the same
    `version` the catalogue advertises.

    Read-only: every git invocation below is `rev-parse`, `cat-file -t`, `show` or
    `merge-base --is-ancestor` — none writes a ref, an object, the index, or the working tree, and
    none touches the network (AC-MCC-6).
    """
    # --- FAIL CLOSED WHEN THE CHECK CANNOT RUN. Mirrors foundry-cut-release.py:333-339: probing
    # --git-dir first separates "not a readable git repository" from a genuine incoherence verdict,
    # so a tree this check cannot inspect REFUSES rather than sailing to a false PASS.
    rc, _ = _git(tree, "rev-parse", "--git-dir")
    if rc != 0:
        return False, (f"cannot verify the default-branch catalogue: {tree} is not a readable git "
                       f"repository (git rev-parse --git-dir "
                       f"{'could not run' if rc < 0 else f'exited {rc}'}). The coherence check "
                       f"refuses rather than reporting not-applicable.")

    branch_ref = f"refs/heads/{default_branch}"
    rc, _ = _git(tree, "rev-parse", "--verify", branch_ref)
    if rc != 0:
        return False, (f"cannot verify the default-branch catalogue: {default_branch!r} does not "
                       f"resolve in {tree} (git rev-parse --verify "
                       f"{'could not run' if rc < 0 else f'exited {rc}'}). The coherence check "
                       f"refuses rather than reporting not-applicable.")

    rc, blob = _git(tree, "show", f"{branch_ref}:.claude-plugin/marketplace.json")
    if rc != 0:
        return False, f"{default_branch} carries no committed .claude-plugin/marketplace.json"
    try:
        entry = select_plugin_entry(json.loads(blob))
    except Exception as e:
        return False, (f"marketplace.json on {default_branch} is unreadable: "
                       f"{type(e).__name__}: {e}")

    version = entry.get("version")
    if not version:
        return False, f"marketplace.json on {default_branch} has no version for the 'foundry' entry"
    source = entry.get("source") or {}
    sha = source.get("sha", "")

    # --- AC-MCC-2(a): source.sha MUST be a full 40-hex object name. A ref name or abbreviation is a
    # MUTABLE pin — what an adopter resolves would change as the ref moves — and would otherwise
    # type as a commit and sail through every check below.
    if not _HEX40_RE.fullmatch(sha or ""):
        return False, (f"source.sha on {default_branch} is {sha!r} — not a full 40-character "
                       f"lowercase hex commit id. A ref name or abbreviation is a MUTABLE pin: what "
                       f"an adopter resolves would change as the ref moves.")

    # --- AC-MCC-2(b): the id must resolve, and to a COMMIT object — never an annotated tag object,
    # which `cat-file -t` reports distinctly.
    rc, kind = _git(tree, "cat-file", "-t", sha)
    if rc != 0:
        return False, f"source.sha {sha[:12]}… on {default_branch} does not resolve in this repo"
    if kind != "commit":
        return False, (f"source.sha on {default_branch} names a {kind!r} object, not a commit — "
                       f"use `git rev-parse {sha}^{{commit}}`")

    # --- AC-MCC-3: the id must be an ancestor of the default branch. Deliberately ancestry, not
    # adjacency — see the module docstring's "Ancestry, not adjacency".
    #
    # `git merge-base --is-ancestor` documents exactly THREE outcomes: exit 0 (true), exit 1 (false
    # — a clean, genuine negative), or a HIGHER exit code / could-not-run (a real git error, e.g. an
    # object the command could not resolve). Both non-zero cases refuse either way, so this was
    # already fail-closed; the split below exists only so the MESSAGE never tells an operator
    # "not an ancestor" for a run where git itself failed to answer the question.
    rc, _ = _git(tree, "merge-base", "--is-ancestor", sha, branch_ref)
    if rc == 1:
        return False, (f"source.sha {sha[:12]}… is NOT an ancestor of {default_branch} — an "
                       f"adopter resolving {default_branch} and installing by this sha would fetch "
                       f"a commit outside the default branch's history.")
    if rc != 0:
        return False, (f"cannot verify ancestry of source.sha {sha[:12]}… against {default_branch}: "
                       f"git merge-base --is-ancestor "
                       f"{'could not run' if rc < 0 else f'exited {rc}'} (expected 0=ancestor or "
                       f"1=not-an-ancestor). The coherence check refuses rather than reporting "
                       f"not-applicable.")

    # --- AC-MCC-1: the committed plugin.json AT THAT SHA must carry the SAME version the
    # catalogue advertises. This is the R→R2 window defect: R legitimately advertises the new
    # version while source.sha still names the previous release's commit.
    rc, pj = _git(tree, "show", f"{sha}:.claude-plugin/plugin.json")
    if rc != 0:
        return False, f"the pinned commit {sha[:12]}… has no .claude-plugin/plugin.json"
    try:
        pinned_version = json.loads(pj).get("version")
    except Exception:
        return False, f"plugin.json at the pinned commit {sha[:12]}… is unreadable"
    if pinned_version != version:
        return False, (f"INCOHERENT DEFAULT-BRANCH CATALOGUE: {default_branch} advertises version "
                       f"{version!r} but source.sha {sha[:12]}… pins a commit whose plugin.json "
                       f"declares version {pinned_version!r}. An adopter resolving {default_branch} "
                       f"and installing by this sha would receive {pinned_version!r}, not "
                       f"{version!r}.")

    return True, (f"default-branch catalogue on {default_branch} pins {sha[:12]}… "
                  f"(version {version}) — coherent")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Read-only, offline coherence check over the default branch's committed "
                    ".claude-plugin/marketplace.json for the 'foundry' plugins[] entry "
                    "(feat-foundry-main-catalogue-coherence, AC-MCC-1..4/6).")
    ap.add_argument("--tree", default=".", help="path to the git repository (default: cwd)")
    ap.add_argument("--default-branch", default="main",
                    help="the default branch to grade (default: main)")
    args = ap.parse_args(argv)

    ok, detail = main_catalogue_coherence(os.path.abspath(args.tree),
                                          default_branch=args.default_branch)
    print(("MCC-PASS: " if ok else "MCC-FAIL: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
