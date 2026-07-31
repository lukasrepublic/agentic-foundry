"""foundry_graph — citation-graph builder with MATERIALIZED backlinks (§19 + delta-1).

Plain-text files are the source of truth; the graph is a derived, reproducible cache.
delta-1 (the §21.7 hard prereq) = materialized backlinks: an O(1) "what cites X" reverse
index, so foundry-graph-mcp's graph_backlinks tool is O(1), not a full scan.

Generic + de-jargoned (§19.6): citation keywords are generic (Doc/Atom/...), not
project-specific. Edges come from markdown links + `[Keyword: target]` citation forms.
Stdlib-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

# Generic citation keywords (§19.6 — no project jargon). Legacy adopter/Feature kept
# for adopter back-compat during migration.
_CITE_RE = re.compile(r"\[(?:Doc|Atom|adopter|Feature|Foundation|Source|memory):\s*([^\]\|]+?)\]")
_MDLINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+\.md)(?:#[^)]*)?\)")

# Graph schema version (UL-0017 / feat-foundry-graph-coherence-substrate). v1 adds
# broken_edges + out_of_scope + build_scope + builder_version. A reader that finds NO
# schema_version must treat the graph as pre-substrate (re-walk; an absent broken_edges
# key is "unknown", NEVER "clean").
SCHEMA_VERSION = 1

# Bumped whenever the resolver / citation-regex changes in a way that could re-classify an
# edge. Folded into the staleness comparison so a builder-version skew reads STALE.
BUILDER_VERSION = "graph-builder-v1"


def _norm(root: str, path: str) -> str:
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root))


def _resolve(root: str, src_rel: str, target: str) -> str | None:
    """Resolve a citation target to a node (repo-relative .md path) or None.

    Retained for back-compat; build_graph uses _resolve_target (which distinguishes the
    failure reasons _resolve collapses into None — AC-GCOH-1)."""
    resolved, _reason = _resolve_target(root, src_rel, target)
    return resolved


def _resolve_target(root: str, src_rel: str, target: str) -> tuple[str | None, str]:
    """Classify a citation target against the actual resolver branches (AC-GCOH-1).

    Returns (resolved_rel_or_None, reason):
      - ("<rel>", "ok")            an existing .md file was found (may still be out of the
                                   built scope — build_graph decides edge vs out_of_scope)
      - (None, "malformed")        empty / unparseable target
      - (None, "target_missing")   non-empty target, but no existing .md file matches
    """
    stripped = target.strip()
    stripped = stripped.split("#", 1)[0].split()[0] if stripped else stripped
    if not stripped:
        return None, "malformed"
    # Try repo-root-relative, then relative to the citing file's dir.
    cands = [os.path.join(root, stripped),
             os.path.join(root, os.path.dirname(src_rel), stripped)]
    for c in cands:
        if os.path.isfile(c) and c.endswith(".md"):
            return _norm(root, c), "ok"
    return None, "target_missing"


def build_graph(root: str, subdirs: list[str] | None = None) -> dict:
    """Scan markdown under root (optionally limited to subdirs) → graph with nodes,
    forward edges, and a MATERIALIZED backlinks reverse index."""
    root = os.path.abspath(root)
    files = []
    walk_roots = [os.path.join(root, d) for d in subdirs] if subdirs else [root]
    for wr in walk_roots:
        for dirpath, dirnames, filenames in os.walk(wr):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".worktrees", "__pycache__")]
            for fn in filenames:
                if fn.endswith(".md"):
                    files.append(os.path.join(dirpath, fn))

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    backlinks: dict[str, list[str]] = {}
    broken_edges: list[dict] = []          # AC-GCOH-1: was silently dropped
    out_of_scope: list[dict] = []          # resolves to an existing file outside the scope — NOT broken
    sig = hashlib.sha256()

    for f in sorted(files):
        rel = _norm(root, f)
        raw = open(f, "rb").read()
        nodes[rel] = {"content_sha256": hashlib.sha256(raw).hexdigest()}
        sig.update(rel.encode()); sig.update(nodes[rel]["content_sha256"].encode())

    for f in sorted(files):
        rel = _norm(root, f)
        text = open(f, encoding="utf-8", errors="replace").read()
        seen: set = set()              # resolved-edge dedup, keyed (rel, dst)
        broken_seen: set = set()       # broken/out-of-scope dedup, keyed (rel, raw target)
        for m in list(_CITE_RE.finditer(text)) + list(_MDLINK_RE.finditer(text)):
            raw_target = m.group(1).strip()
            dst, reason = _resolve_target(root, rel, m.group(1))
            if reason == "ok":
                if dst == rel:
                    continue                              # self-citation: intentional non-edge
                if dst in nodes:
                    if (rel, dst) not in seen:            # duplicate edge: intentional non-edge
                        seen.add((rel, dst))
                        edges.append({"src": rel, "dst": dst})
                        backlinks.setdefault(dst, []).append(rel)   # MATERIALIZED reverse index
                else:
                    # resolves to a real .md OUTSIDE the built scope → valid, just unindexed
                    if (rel, raw_target) not in broken_seen:
                        broken_seen.add((rel, raw_target))
                        out_of_scope.append({"src": rel, "target": raw_target, "resolved": dst})
            else:
                # malformed / target_missing → genuinely broken (was silently dropped)
                if (rel, raw_target) not in broken_seen:
                    broken_seen.add((rel, raw_target))
                    broken_edges.append({"src": rel, "target": raw_target, "resolved": None, "reason": reason})

    broken_edges.sort(key=lambda e: (e["src"], e["target"]))   # reproducible order
    out_of_scope.sort(key=lambda e: (e["src"], e["target"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "build_scope": sorted(subdirs) if subdirs else [],   # [] = full-root build
        "source_signature": sig.hexdigest(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "broken_edge_count": len(broken_edges),
        "nodes": nodes,
        "edges": edges,
        "backlinks": backlinks,
        "broken_edges": broken_edges,
        "out_of_scope": out_of_scope,
    }


def write_graph(graph: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(graph, fh, sort_keys=True, indent=0, separators=(",", ":"))
    os.replace(tmp, out_path)


def is_stale(root: str, graph_path: str) -> tuple[bool, str]:
    """AC-GCOH-2: scope-pinned, content-based staleness gate. Recompute the signature over
    the committed graph's RECORDED build_scope (NOT the full-root default — a scope mismatch
    must not masquerade as content drift) and compare signature + builder_version.

    Returns (stale, reason). reason ∈ {current, absent, unreadable, pre-substrate,
    builder-version, content-drift}. Fail-closed: anything but an exact match is STALE."""
    if not os.path.isfile(graph_path):
        return True, "absent"
    try:
        g = json.load(open(graph_path))
    except Exception:
        return True, "unreadable"
    if not all(k in g for k in ("source_signature", "schema_version", "build_scope")):
        return True, "pre-substrate"          # predates the substrate → re-walk, record provenance
    if g.get("builder_version") != BUILDER_VERSION:
        return True, "builder-version"
    scope = g.get("build_scope") or None        # [] = full-root build
    fresh = build_graph(root, scope)
    if fresh["source_signature"] != g["source_signature"]:
        return True, "content-drift"
    return False, "current"


# O(1) query helpers (consumed by foundry-graph-mcp).
def backlinks_of(graph: dict, node: str) -> list[str]:
    return graph.get("backlinks", {}).get(node, [])


def neighbors_of(graph: dict, node: str) -> list[str]:
    return [e["dst"] for e in graph.get("edges", []) if e["src"] == node]
