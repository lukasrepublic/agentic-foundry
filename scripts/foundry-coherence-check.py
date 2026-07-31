#!/usr/bin/env python3
"""foundry-coherence-check — ADVISORY citation-coherence sweep over the DIRECT-EDIT
WORKSPACE corpus (UL-0021, AC-COH-1..4).

This is an operator/agent advisory sweep, NOT a merge gate. It builds the citation graph
FRESH in-memory on every run (no cache trust — it never reads the committed
.foundry/graph.json), classifies broken citations vs out-of-scope citations, and emits a
single deterministic JSON document.

Exit is tri-state + fail-closed (AC-COH-2):
  0  — ran, zero broken citations
  1  — ran, >=1 broken citation (ADVISORY — see skills/coherence-check/SKILL.md: a 1 must
        NOT be promoted into a blocking gate over the workspace corpus; the merge-gate
        broken-citation check is vacuous under self-hosting, B2-pause rationale)
  2  — operational failure: build_graph raised, the resolved root is missing, or the corpus
        walked to ZERO nodes (an empty/mis-resolved corpus is a HARD error, never a vacuous
        clean 0). Exit 2 stamps provenance.source == "error" with the cause.

See skills/coherence-check/SKILL.md for the venue boundary + the load-bearing 1-vs-2 split.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# CANONICAL_SCOPE lives in THIS consumer (NOT in foundry_graph.py — which keeps a full-root
# None default we must NEVER pass). Overridable via --scope. Sorted, per contract.
CANONICAL_SCOPE = sorted(["docs", "foundry", "specs"])

# GOVERNANCE_SOURCE_DOCS — the DECLARED, NAMED set of foundry governance source docs the
# governance-propagation mode treats as cascade triggers (AC-GOV-1). Repo-relative, sorted,
# canonical-scope-resident. A governance change here should cascade a re-validation of every
# corpus node that cites it. A doc NOT present in the corpus simply yields no worklist for it
# (not an error). Override per-invocation via governance_propagation_worklist(sources=...).
GOVERNANCE_SOURCE_DOCS = sorted([
    "docs/foundry/RESEARCH-FIRST-DISCIPLINE.md",   # the research-first discipline (CLAUDE.md floor)
])


def _resolve_root() -> str:
    """CLAUDE_PROJECT_DIR if set AND not an unexpanded ${-literal, else cwd. No walk-up."""
    pd = os.environ.get("CLAUDE_PROJECT_DIR")
    if pd and not pd.startswith("${"):
        return pd
    return os.getcwd()


def run_coherence_check(root: str, scope: list[str]) -> tuple[dict, int]:
    """Build the graph FRESH and produce (report_dict, exit_code).

    Fail-closed tri-state: missing root OR empty walk OR a raised builder → exit 2 with
    provenance.source == "error"; >=1 broken → exit 1; else exit 0.
    """
    abs_root = os.path.abspath(root)
    scope = sorted(scope)

    # Pre-check to DISTINGUISH "missing root" from "empty walk" in the error field — both
    # still map to exit 2 (AC-COH-2).
    if not os.path.isdir(abs_root):
        report = {
            "provenance": {
                "source": "error",
                "scope": scope,
                "corpus_root": abs_root,
                "node_count": 0,
                "error": f"corpus root is not a directory: {abs_root}",
            },
            "findings": [],
            "out_of_scope": [],
            "counts": {"broken": 0, "out_of_scope": 0},
        }
        return report, 2

    try:
        import foundry_graph as fg
        # NEVER pass build_graph's full-root None default — always the canonical scope.
        graph = fg.build_graph(abs_root, scope)
    except Exception as e:  # operational failure — the builder raised
        report = {
            "provenance": {
                "source": "error",
                "scope": scope,
                "corpus_root": abs_root,
                "node_count": 0,
                "error": f"build_graph raised: {type(e).__name__}: {e}",
            },
            "findings": [],
            "out_of_scope": [],
            "counts": {"broken": 0, "out_of_scope": 0},
        }
        return report, 2

    node_count = graph.get("node_count", 0)
    if node_count == 0:
        # Empty / mis-resolved corpus is a HARD error, never a vacuous clean 0.
        report = {
            "provenance": {
                "source": "error",
                "scope": scope,
                "corpus_root": abs_root,
                "node_count": 0,
                "error": (f"corpus walked to ZERO nodes under scope {scope} at {abs_root} "
                          "(empty or mis-resolved corpus — never a vacuous clean)"),
            },
            "findings": [],
            "out_of_scope": [],
            "counts": {"broken": 0, "out_of_scope": 0},
        }
        return report, 2

    # findings = broken_edges {src, target, resolved, reason}, sorted by (reason, src, target)
    findings = sorted(
        ({"src": e["src"], "target": e["target"], "resolved": e["resolved"], "reason": e["reason"]}
         for e in graph.get("broken_edges", [])),
        key=lambda e: (e["reason"], e["src"], e["target"]),
    )
    # out_of_scope = {src, target, resolved}, sorted by (src, target) — NEVER affects exit.
    oos = sorted(
        ({"src": e["src"], "target": e["target"], "resolved": e["resolved"]}
         for e in graph.get("out_of_scope", [])),
        key=lambda e: (e["src"], e["target"]),
    )

    report = {
        "provenance": {
            "source": "fresh-walk",   # single shape — no fast/stale branch, no is_stale
            "scope": scope,
            "corpus_root": abs_root,
            "node_count": node_count,
        },
        "findings": findings,
        "out_of_scope": oos,
        "counts": {"broken": len(findings), "out_of_scope": len(oos)},
    }
    exit_code = 1 if findings else 0
    return report, exit_code


# --------------------------------------------------------------------------------------- #
# Governance-propagation mode (AC-GOV-1..3) — ADDITIVE, ADVISORY.
#
# A governance-doc change is an *event that cascades* (GitHub Spec Kit `/constitution` prior
# art): when one of the declared GOVERNANCE_SOURCE_DOCS changes, the corpus nodes that CITE it
# are now candidates for drift. This mode reuses the EXISTING citation graph
# (foundry_graph.build_graph — NO hand-rolled parser) and its MATERIALIZED reverse/backlink
# index (the same index that backs the graph_backlinks MCP tool) to emit a deterministic,
# stable-ordered, ADVISORY re-validation worklist mapping each present governance doc → its
# dependent (citing) nodes.
#
# ADVISORY BOUNDARY (load-bearing): this mode adds NO merge authority and is NEVER imported or
# run inside foundry-merge-gate.py. A populated OR empty worklist is an advisory outcome
# (exit 0 — a worklist is never a failure). Only an OPERATIONAL failure (missing root, empty
# walk, or the builder raising) is fail-closed (exit 2, provenance.source == "error"), mirroring
# run_coherence_check's tri-state fail-closed floor. There is no exit 1: the worklist never gates.
# --------------------------------------------------------------------------------------- #

def _governance_error_report(scope: list[str], abs_root: str, msg: str) -> dict:
    return {
        "provenance": {"source": "error", "scope": scope, "corpus_root": abs_root,
                       "node_count": 0, "error": msg},
        "governance_sources": [],
        "present_sources": [],
        "worklist": {},
        "counts": {"sources_declared": 0, "sources_present": 0, "dependents_total": 0},
    }


def governance_propagation_worklist(root: str, sources: list[str] | None = None,
                                    scope: list[str] | None = None) -> dict:
    """ADVISORY governance-propagation worklist (AC-GOV-1/-2).

    Build the citation graph FRESH via the EXISTING foundry_graph.build_graph over the canonical
    scope, then for each DECLARED governance source doc PRESENT in the corpus enumerate the nodes
    that cite it — the MATERIALIZED reverse/backlink index (foundry_graph.backlinks_of) — and
    return a deterministic, stable-ordered re-validation worklist.

    Returns a report dict. provenance.source == "fresh-walk" on success (advisory), or "error"
    on an OPERATIONAL failure (missing root / empty walk / builder raised — fail-closed). A
    governance doc with zero dependents yields an empty list (NOT an error). NEVER gates and is
    NEVER imported/run inside foundry-merge-gate.py.
    """
    sources = sorted(GOVERNANCE_SOURCE_DOCS if sources is None else sources)
    scope = sorted(scope) if scope else CANONICAL_SCOPE
    abs_root = os.path.abspath(root)

    if not os.path.isdir(abs_root):
        return _governance_error_report(scope, abs_root, f"corpus root is not a directory: {abs_root}")

    try:
        import foundry_graph as fg
        # REUSE build_graph — never the full-root None default; always the canonical scope.
        graph = fg.build_graph(abs_root, scope)
    except Exception as e:  # operational failure — the builder raised
        return _governance_error_report(scope, abs_root,
                                        f"build_graph raised: {type(e).__name__}: {e}")

    node_count = graph.get("node_count", 0)
    if node_count == 0:
        return _governance_error_report(
            scope, abs_root,
            f"corpus walked to ZERO nodes under scope {scope} at {abs_root} "
            "(empty or mis-resolved corpus — never a vacuous clean)")

    nodes = graph.get("nodes", {})
    # Normalize declared sources to the repo-relative form build_graph keys nodes/backlinks by.
    norm_sources = sorted(os.path.normpath(s) for s in sources)
    present = [s for s in norm_sources if s in nodes]   # already sorted; absent → no worklist
    worklist = {}
    for s in present:
        # reverse index: nodes that CITE s. Deterministic stable order; empty if zero dependents.
        worklist[s] = sorted(set(fg.backlinks_of(graph, s)))
    dependents_total = sum(len(v) for v in worklist.values())

    return {
        "provenance": {"source": "fresh-walk", "scope": scope,
                       "corpus_root": abs_root, "node_count": node_count},
        "governance_sources": norm_sources,
        "present_sources": present,
        "worklist": worklist,
        "counts": {"sources_declared": len(norm_sources),
                   "sources_present": len(present),
                   "dependents_total": dependents_total},
    }


def run_governance_propagation(root: str, sources: list[str] | None = None,
                               scope: list[str] | None = None) -> tuple[dict, int]:
    """Wrap governance_propagation_worklist with the tri-state fail-closed exit: advisory 0 for
    any worklist (populated OR empty), fail-closed 2 only on an operational failure. NEVER 1."""
    report = governance_propagation_worklist(root, sources, scope)
    code = 2 if report["provenance"]["source"] == "error" else 0
    return report, code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Advisory citation-coherence sweep (always fresh).")
    ap.add_argument("--scope", action="append", default=None,
                    help="override a CANONICAL_SCOPE subdir (repeatable). Default: docs foundry specs.")
    ap.add_argument("--governance-propagation", action="store_true",
                    help="ADVISORY governance-propagation mode: for each declared GOVERNANCE_SOURCE_DOCS "
                         "doc present in the corpus, emit the re-validation worklist of nodes that cite "
                         "it (reusing the citation graph's reverse index). Never gates; exit 0 advisory, "
                         "2 fail-closed on operational failure.")
    args = ap.parse_args(argv)

    root = _resolve_root()
    scope = args.scope if args.scope else CANONICAL_SCOPE
    if args.governance_propagation:
        report, code = run_governance_propagation(root, scope=scope)
        print(json.dumps(report, sort_keys=True))
        return code
    report, code = run_coherence_check(root, scope)
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
