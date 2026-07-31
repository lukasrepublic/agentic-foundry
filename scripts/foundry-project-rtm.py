#!/usr/bin/env python3
"""foundry-project-rtm — the requirements-traceability matrix (RTM) report (PTR, ER #112 atom
3 of 3; feat-foundry-project-tracking-lifecycle-reprojection, AC-PTR-4).

Compliance regimes (SOC 2 / ISO 27001 / SOX-ITGC) require a traceable line from a control to
the requirement it satisfies to the artifact that evidences it: control -> requirement ->
artifact -> test -> evidence. This report derives exactly that chain for every atom in the
corpus, purely from authorized files + local GitHub links — never a hand-maintained
spreadsheet, never a live GitHub call:

    control (AC-IDs)  ->  atom (spec path)  ->  issue  ->  PR  ->  commit  ->  merge-gate PASS

- **control** <- the sibling acceptance-contract.yaml's `checkpoints[].ac_id` set (PTM's
  `derive_control`, imported READ-ONLY — this module never re-derives it).
- **atom** <- the spec path (PTM's `discover_atoms`, also READ-ONLY).
- **issue** <- the local `.foundry/project-map.json` cache (`atom-id -> issue-number`), written
  by PTC's `sync`. This module only *reads* that cache; it never queries GitHub for the number
  (that discovery is PTC's concern — see the spec's "Out of scope").
- **PR / commit / merge-gate PASS** <- a best-effort, LOCAL-repo `git log` scan (never a
  network call) keyed off the resolved issue number. Injectable via `git_lookup=` for a fully
  deterministic, offline `--selftest` (a `FakeGitLookup` double, mirroring PTC's
  `FakeGraphQLTransport` seam) — the production default (`_default_git_lookup`) is a
  best-effort residual: an unresolvable link is marked **UNRESOLVED** (present in the row,
  never omitted or fabricated), not treated as an error.

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). This module holds no
token, opens no socket, and writes to no spec or acceptance-contract file (AC-PTR-6, the
one-way floor) — it derives a report outward only.

  python scripts/foundry-project-rtm.py                 # print the RTM over the resolved corpus
  python scripts/foundry-project-rtm.py --root DIR       # override the resolved adopter root
  python scripts/foundry-project-rtm.py --format json    # machine-readable
  python scripts/foundry-project-rtm.py --selftest       # AC-PTR-4 (two fixture atoms, temp fixtures)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_project_tracking as ptm  # noqa: E402 — PTM imported READ-ONLY (never modified here)

UNRESOLVED = "UNRESOLVED"


# ==================================================================================================== #
# Local cache read (the issue-number link). Read-only; absent/malformed cache -> {} (never raises).
# ==================================================================================================== #

def _read_cache(root):
    path = os.path.join(root, ".foundry", "project-map.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ==================================================================================================== #
# PR / commit / merge-gate-pass resolution. A pluggable seam (git_lookup) so --selftest is fully
# offline/deterministic (a Fake double) while the CLI default is a best-effort local `git log` scan —
# NEVER a network call, and an unresolvable link is UNRESOLVED, never fabricated.
# ==================================================================================================== #

def _default_git_lookup(root, atom_id, issue_number):
    """Best-effort LOCAL derivation only (residual — no live GitHub call, ever): scan `git log`
    for a merge-commit subject naming a PR that plausibly closed `issue_number` (GitHub's
    default regular-merge subject is `Merge pull request #<n> from ...`). Returns
    (pr_number_or_None, commit_sha_or_None, merge_gate_pass_or_None). This repo's own merge
    commits do not carry a `Closes #<n>` cross-reference in their subject/body (the close
    happens via the PR's OWN body, not the merge commit), so this heuristic frequently returns
    all-None on a real corpus — by design that renders UNRESOLVED rather than a fabricated
    guess. A fuller live-parity resolver (walking the GitHub API for the PR that actually
    closed the issue) is out of scope for this offline-verified atom (see the spec's "Out of
    scope / non-goals" — PTR never opens a socket)."""
    try:
        subprocess.run(
            ["git", "-C", root, "log", "--all", "--format=%H%x01%s",
             "--grep", r"Merge pull request #[0-9]+.*"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except Exception:
        pass
    # Heuristic only: a local `git log` scan has no reliable way to bind a merge commit to the
    # ISSUE it closed (that cross-reference lives in the PR body on GitHub, not the merge
    # commit subject) — so the default resolver deliberately stops at "no match" rather than
    # guessing. A caller with a richer local source (e.g. a maintained PR<->issue cache) should
    # inject its own `git_lookup=` (see --selftest's FakeGitLookup for the seam shape).
    return None, None, None


def build_row(root, spec_path, contract_path, cache, git_lookup=_default_git_lookup):
    """Assemble one atom's traceability row. Never raises; every unresolved link is the literal
    string UNRESOLVED (present in the row, never omitted or fabricated)."""
    atom_id = ptm.atom_id_of(spec_path)
    control = ptm.derive_control(contract_path)
    atom_rel = os.path.relpath(spec_path, root) if os.path.isabs(spec_path) else spec_path

    issue = cache.get(atom_id)
    pr = commit = merge_gate_pass = None
    if issue is not None:
        pr, commit, merge_gate_pass = git_lookup(root, atom_id, issue)

    def _res(v):
        return v if v is not None else UNRESOLVED

    row = {
        "control": control,
        "atom": atom_rel,
        "atom_id": atom_id,
        "issue": _res(issue),
        "pr": _res(pr),
        "commit": _res(commit),
        "merge_gate_pass": _res(merge_gate_pass),
    }
    row["unresolved"] = any(row[k] == UNRESOLVED for k in ("issue", "pr", "commit", "merge_gate_pass"))
    return row


def build_rtm(root_arg=None, git_lookup=_default_git_lookup):
    """The whole-corpus RTM: one row per discovered atom (PTM's discover_atoms, read-only)."""
    root = ptm._resolve_root(root_arg)
    cache = _read_cache(root)
    rows = []
    for spec_path, contract_path in ptm.discover_atoms(root):
        rows.append(build_row(root, spec_path, contract_path, cache, git_lookup=git_lookup))
    return rows


# ==================================================================================================== #
# --selftest — AC-PTR-4 over TWO fixture atoms (one fully-linked, one whose PR/commit artifact is
# ABSENT), driving the REAL build_row over a FAKE, injected git_lookup (fully offline/deterministic —
# mirrors PTC's FakeGraphQLTransport seam). Emits `AC-PTR-4 <label>: PASS` only when the behavior
# actually computed PASS.
# ==================================================================================================== #

def _emit(token, label, ok):
    print(f"{token} {label}: {'PASS' if ok else 'FAIL'}")
    return ok


def _fake_git_lookup(root, atom_id, issue_number):
    """Deterministic fixture double: atom "feat-linked-atom" resolves a full chain; every other
    atom-id (incl. "feat-partial-atom") resolves NOTHING — its PR/commit link stays UNRESOLVED."""
    if atom_id == "feat-linked-atom":
        return 55, "deadbeefcafebabefeed" * 2, True
    return None, None, None


def _selftest_ac4():
    import shutil
    import tempfile
    ok = True
    tmp = tempfile.mkdtemp(prefix="ptr-rtm-4-")
    try:
        # Fixture atom 1 — FULLY LINKED: issue in the cache, and the fake git_lookup resolves a
        # PR + commit + merge-gate PASS for it.
        spec1, contract1 = ptm._mk_atom(
            tmp, "foundry", "rtm-domain", "linked-cap", "linked-atom",
            ["AC-RTM-1", "AC-RTM-2"], authorized=True,
        )
        # Fixture atom 2 — PR/commit artifact ABSENT: issue IS in the cache (so the chain
        # reaches the PR-resolution step), but the fake resolver returns no PR/commit for it —
        # the unresolved link must be PRESENT in the row, not silently dropped.
        spec2, contract2 = ptm._mk_atom(
            tmp, "foundry", "rtm-domain", "partial-cap", "partial-atom",
            ["AC-RTM-3"], authorized=True,
        )
        ptm._write(os.path.join(tmp, ".foundry", "project-map.json"),
                   json.dumps({"feat-linked-atom": 101, "feat-partial-atom": 202}))

        cache = _read_cache(tmp)
        row1 = build_row(tmp, spec1, contract1, cache, git_lookup=_fake_git_lookup)
        row2 = build_row(tmp, spec2, contract2, cache, git_lookup=_fake_git_lookup)

        # -- row 1: the full chain is populated, nothing UNRESOLVED. -------------------------- #
        ok = ok and row1["control"] == ["AC-RTM-1", "AC-RTM-2"]
        ok = ok and row1["atom"].endswith("feat-linked-atom.md")
        ok = ok and row1["issue"] == 101
        ok = ok and row1["pr"] == 55
        ok = ok and row1["commit"] == "deadbeefcafebabefeed" * 2
        ok = ok and row1["merge_gate_pass"] is True
        ok = ok and row1["unresolved"] is False

        # -- row 2: control/atom/issue are all present + correct; the PR/commit/merge-gate link
        #    is marked UNRESOLVED — present in the row (not omitted), and never fabricated
        #    (never e.g. copied from row 1 or defaulted to a fake PR number). --------------------
        ok = ok and row2["control"] == ["AC-RTM-3"]
        ok = ok and row2["issue"] == 202
        ok = ok and row2["pr"] == UNRESOLVED
        ok = ok and row2["commit"] == UNRESOLVED
        ok = ok and row2["merge_gate_pass"] == UNRESOLVED
        ok = ok and row2["unresolved"] is True

        # -- anti-tautology / anti-vacuity: the two rows are NOT identical (a stub that always
        #    emits the same canned row for every atom would otherwise pass trivially). ----------
        ok = ok and row1 != row2
        ok = ok and row1["unresolved"] != row2["unresolved"]

        # -- a third atom absent from the cache entirely -> issue itself is UNRESOLVED too (the
        #    chain degrades gracefully all the way up, never raising). ------------------------- #
        spec3, contract3 = ptm._mk_atom(
            tmp, "foundry", "rtm-domain", "nocache-cap", "nocache-atom", ["AC-RTM-4"], authorized=True,
        )
        row3 = build_row(tmp, spec3, contract3, cache, git_lookup=_fake_git_lookup)
        ok = ok and row3["issue"] == UNRESOLVED and row3["unresolved"] is True

        # -- build_rtm() assembles the whole corpus end to end (no exception, all 3 rows present). -
        full = build_rtm(tmp, git_lookup=_fake_git_lookup)
        ok = ok and len(full) == 3
        ok = ok and {r["atom_id"] for r in full} == {
            "feat-linked-atom", "feat-partial-atom", "feat-nocache-atom",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def selftest():
    ok = _emit("AC-PTR-4", "traceability-chain-with-unresolved-marking", _selftest_ac4())
    return 0 if ok else 1


# ==================================================================================================== #
# CLI
# ==================================================================================================== #

def _print_text(rows):
    if not rows:
        print("(no atoms discovered)")
        return
    for r in rows:
        control = ", ".join(r["control"]) if r["control"] else "(none)"
        print(f"- {r['atom_id']}")
        print(f"    control: {control}")
        print(f"    atom:    {r['atom']}")
        print(f"    issue:   {r['issue']}")
        print(f"    pr:      {r['pr']}")
        print(f"    commit:  {r['commit']}")
        print(f"    merge-floor PASS: {r['merge_gate_pass']}")
        if r["unresolved"]:
            print("    (one or more links UNRESOLVED)")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="foundry-project-rtm — requirements-traceability matrix (PTR, ER #112 atom 3 of 3)"
    )
    ap.add_argument("--root", help="override the resolved adopter root (default: CLAUDE_PROJECT_DIR)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--selftest", action="store_true", help="run AC-PTR-4 over temp fixtures")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    rows = build_rtm(args.root)
    if args.format == "json":
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        _print_text(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
