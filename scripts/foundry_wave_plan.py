#!/usr/bin/env python3
"""foundry_wave_plan — deterministic wave planning (dl-04; ported from the 1.x foundry-wave-plan.py).

Input: atoms [{id, blocked_by[], paths[]}]. Output: waves, a list of sorted id lists, such that
  1. an atom sits in a wave STRICTLY AFTER every wave holding one of its `blocked_by` atoms (dependency
     closure), and
  2. no two atoms in one wave have overlapping declared `paths` (path-overlap split).

Pure and deterministic: the same atoms give the same waves (Kahn order with an ascending-id tiebreak; no clock,
no randomness). A dependency cycle REFUSES (WavePlanError) and names every atom in the cycle; an atom that only
sits downstream of a cycle is not named. Never a partial plan.

Path overlap is conservative (it never under-splits). Each path is normalised, then cut to a literal directory
prefix: a trailing `/**` or `/*` is dropped; any other glob character cuts the path at its first segment. Two
paths overlap when the prefixes are equal or one is a directory prefix of the other. A path whose prefix is empty
(`""`, `/`, `.`, a leading glob such as `*.py` or `**`) or that holds a `..` segment can reach anywhere, so it
overlaps EVERY path.

    foundry_wave_plan.py <atoms.json>     # atoms JSON (a list, or {"atoms": [...]}) -> waves JSON on stdout
"""
from __future__ import annotations

import heapq
import json
import posixpath
import sys


class WavePlanError(Exception):
    """Fail-closed: a malformed atom list or a dependency cycle."""


_GLOB_META = frozenset("*?[]")


def _has_glob_meta(segment):
    return any(c in _GLOB_META for c in segment)


def _normalize_path(p):
    return posixpath.normpath(p).lstrip("/")


def _literal_overlap_prefix(p):
    norm = _normalize_path(p)
    for suffix in ("/**", "/*"):
        if norm.endswith(suffix):
            candidate = norm[: -len(suffix)]
            if not _has_glob_meta(candidate):
                return candidate
            norm = candidate
            break
    segments = norm.split("/") if norm else []
    for i, seg in enumerate(segments):
        if _has_glob_meta(seg):
            return "/".join(segments[:i])
    return norm


def _reaches_everything(prefix):
    return prefix in ("", ".") or ".." in prefix.split("/")


def paths_overlap(a, b):
    na, nb = _literal_overlap_prefix(a), _literal_overlap_prefix(b)
    if _reaches_everything(na) or _reaches_everything(nb):
        return True
    if na == nb:
        return True
    return na.startswith(nb + "/") or nb.startswith(na + "/")


def _atoms_overlap(paths_a, paths_b):
    return any(paths_overlap(a, b) for a in paths_a for b in paths_b)


def _sccs(ids, deps):
    """Tarjan, iterative. Returns the strongly connected components of the graph id -> deps[id]."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(ids):
        if root in index:
            continue
        work = [(root, iter(sorted(deps[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(deps[nxt]))))
                    advanced = True
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                out.append(sorted(comp))
    return out


def plan_waves(atoms):
    """atoms: [{id, blocked_by[], paths[]}] -> waves (list of sorted id lists, index 0 = first wave)."""
    by_id = {}
    for a in atoms:
        aid = a.get("id") if isinstance(a, dict) else None
        if not isinstance(aid, str) or not aid:
            raise WavePlanError("every atom needs a string id")
        if aid in by_id:
            raise WavePlanError(f"duplicate atom id {aid!r}")
        by_id[aid] = a
    deps = {}
    for aid, a in by_id.items():
        bb = list(a.get("blocked_by") or [])
        for d in bb:
            if d not in by_id:
                raise WavePlanError(f"atom {aid!r} is blocked_by unknown atom {d!r}")
        deps[aid] = set(bb)

    # Kahn, ascending-id tiebreak
    dependents = {aid: [] for aid in by_id}
    for aid, ds in deps.items():
        for d in ds:
            dependents[d].append(aid)
    remaining = {aid: len(ds) for aid, ds in deps.items()}
    heap = [aid for aid, n in remaining.items() if n == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        aid = heapq.heappop(heap)
        order.append(aid)
        for nxt in dependents[aid]:
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                heapq.heappush(heap, nxt)
    if len(order) != len(by_id):
        stuck = {aid for aid in by_id if aid not in set(order)}
        cyc = sorted({x for comp in _sccs(stuck, {k: deps[k] & stuck for k in stuck})
                      if len(comp) > 1 or comp[0] in deps[comp[0]] for x in comp})
        raise WavePlanError("dependency cycle among atoms: " + ", ".join(cyc))

    wave_of, members = {}, []
    for aid in order:
        paths = list(by_id[aid].get("paths") or [])
        w = max((wave_of[d] + 1 for d in deps[aid]), default=0)
        while True:
            if w >= len(members):
                members.append([])
            if not any(_atoms_overlap(paths, list(by_id[s].get("paths") or [])) for s in members[w]):
                break
            w += 1
        members[w].append(aid)
        wave_of[aid] = w
    return [sorted(m) for m in members]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: foundry_wave_plan.py <atoms.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as f:
            doc = json.load(f)
        atoms = doc["atoms"] if isinstance(doc, dict) else doc
        print(json.dumps({"waves": plan_waves(atoms)}, indent=2, sort_keys=True))
    except (OSError, ValueError, KeyError, WavePlanError) as e:
        print(f"foundry_wave_plan: refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
