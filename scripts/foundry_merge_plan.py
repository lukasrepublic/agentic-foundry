#!/usr/bin/env python3
"""foundry_merge_plan — the merge plan every tick (operating-model rule A1, FG-DLV-005).

    foundry_merge_plan.py --ready <json|file> --paths <json|file>
                          [--open-prs <json|file>] [--catalogue <glob> ...]

  --ready      the ready items: a list of ids, or of objects {"id": ..., "component": "<optional>"}.
  --paths      {"<id>": ["changed/path", ...]} — the files each ready item changes.
  --open-prs   optional: [{"number": 12, "files": [...], "component": "<optional>", "queued": false}].
  --catalogue  a shared-catalogue glob (repeatable). Default: the catalogue list in this file plus one glob
               per line of `.foundry/catalogue-paths` when that file exists.

Each argument is JSON text or the path of a JSON file INSIDE the project root ($CLAUDE_PROJECT_DIR, else the
cwd); a path outside it is refused. Components are labels the caller supplies; nothing here derives them.

What it does, in order:
  1. Cluster ready items that overlap: they name one file, or one explicit component. A shared catalogue is
     just a shared file, so two items that touch it can never land in two clusters.
  2. For each cluster, look for an open PR for the same area. An open PR that is NOT queued takes the
     cluster's work (`add-to-pr`): no sibling PR. An open PR that IS queued cannot take a push
     (`wait-for-queued`): the cluster waits for it to land or be dequeued, then opens its own PR.
     No overlap: `new-pr`.
  3. Order the clusters: largest first, a cluster that changes a shared catalogue before one that does not,
     then by first id, so a shared catalogue changes in one PR per round.

Output: ONE report line, then the plan as JSON. Exit 0 plan printed, 2 refused (one `refused: ...` line).
Reads no network and writes nothing. Items with no entry in `--paths` cannot be clustered honestly: each is its
own cluster, flagged `unmapped`, and the report line says so.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path

# Files many tickets edit at once; a change here that lands in two PRs in one round makes the second conflict.
DEFAULT_CATALOGUE = (
    ".claude-plugin/marketplace.json", ".claude-plugin/plugin.json", "hooks/hooks.json",
    "docs/permission-floor.json", "cli/permission-floor.json", "docs/standing-versions.md",
    "docs/standing-versions.lock.yaml", "CHANGELOG.md", "governance/**/rules.yaml",
)
CATALOGUE_FILE = Path(".foundry") / "catalogue-paths"


class Refused(Exception):
    pass


def _json_arg(text: str, name: str):
    raw = text.strip()
    if raw[:1] not in ("[", "{"):
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        target = (root / text).resolve()
        if target != root and root not in target.parents:
            raise Refused(f"{name}: {text!r} is outside the project root; pass inline JSON or a file inside it")
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise Refused(f"{name}: not JSON and not a readable file ({exc.strerror})")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise Refused(f"{name}: invalid JSON ({exc})")


def _norm(p: str) -> str:
    p = str(p).strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _items(ready) -> list:
    if not isinstance(ready, list):
        raise Refused("--ready: expected a JSON list")
    out, seen = [], set()
    for e in ready:
        item = {"id": e} if isinstance(e, (str, int)) else e
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise Refused("--ready: every item needs an id")
        iid = str(item["id"])
        if iid in seen:
            raise Refused(f"--ready: duplicate item id {iid!r}")
        seen.add(iid)
        out.append({"id": iid, "component": str(item["component"]) if item.get("component") else None})
    return out


def _is_catalogue(path: str, globs) -> bool:
    for g in globs:
        if g.endswith("/**") and path.startswith(g[:-2]):
            return True
        if g.startswith("**/"):
            parts = path.split("/")
            if any(fnmatch.fnmatchcase("/".join(parts[k:]), g[3:]) for k in range(len(parts))):
                return True
        elif fnmatch.fnmatchcase(path, g):
            return True
    return False


def catalogue_globs(project: Path, extra=None) -> list:
    """The shared-catalogue globs: `extra` when given, else the defaults plus `.foundry/catalogue-paths`."""
    if extra:
        return list(extra)
    out = list(DEFAULT_CATALOGUE)
    f = Path(project) / CATALOGUE_FILE
    if f.is_file():
        out += [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    return out


def plan(ready, paths, open_prs=None, catalogue=DEFAULT_CATALOGUE) -> dict:
    items = _items(ready)
    if not isinstance(paths, dict):
        raise Refused("--paths: expected a JSON object {id: [paths]}")
    files, unmapped = {}, []
    for it in items:
        got = paths.get(it["id"])
        if not isinstance(got, list) or not got or not all(isinstance(x, str) and x.strip() for x in got):
            unmapped.append(it["id"])
            files[it["id"]] = set()
        else:
            files[it["id"]] = {_norm(x) for x in got}
    prs = []
    for pr in (open_prs or []):
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            raise Refused("--open-prs: every PR needs an integer number")
        prs.append({"number": pr["number"], "files": {_norm(x) for x in pr.get("files", [])},
                    "component": pr.get("component"), "queued": bool(pr.get("queued"))})

    # 1. union-find over shared file or shared component
    parent = {it["id"]: it["id"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            ia, ib = items[a], items[b]
            if (files[ia["id"]] & files[ib["id"]]) or (ia["component"] and ia["component"] == ib["component"]):
                parent[find(ia["id"])] = find(ib["id"])
    groups = {}
    for it in items:
        groups.setdefault(find(it["id"]), []).append(it)

    clusters = []
    for members in groups.values():
        ids = [m["id"] for m in members]
        cfiles = set().union(*(files[i] for i in ids))
        comps = {m["component"] for m in members if m["component"]}
        shared = sorted(f for f in cfiles if _is_catalogue(f, catalogue))
        # 2. which open PR rides the cluster
        hit = [p for p in prs if (p["files"] & cfiles) or (p["component"] and p["component"] in comps)]
        open_ok = sorted((p for p in hit if not p["queued"]), key=lambda p: p["number"])
        queued = sorted((p for p in hit if p["queued"]), key=lambda p: p["number"])
        if open_ok:
            action, pr, waits = "add-to-pr", open_ok[0]["number"], None
        elif queued:
            action, pr, waits = "wait-for-queued", None, queued[0]["number"]
        else:
            action, pr, waits = "new-pr", None, None
        clusters.append({"items": sorted(ids), "files": sorted(cfiles), "catalogue": shared, "action": action,
                         "pr": pr, "waits_for": waits, "unmapped": any(i in unmapped for i in ids)})

    # 3. largest first; a catalogue-changing cluster before a plain one; then first id
    clusters.sort(key=lambda c: (-len(c["items"]), 0 if c["catalogue"] else 1, c["items"][0]))
    for n, c in enumerate(clusters, 1):
        c["order"] = n
    cat_owner = {}
    for c in clusters:
        for f in c["catalogue"]:
            cat_owner.setdefault(f, []).append(c["order"])
    assert all(len(v) == 1 for v in cat_owner.values()), "a catalogue file landed in two clusters"
    return {"ready": len(items), "clusters": clusters, "unmapped": sorted(unmapped),
            "catalogue_once": {f: v[0] for f, v in sorted(cat_owner.items())}}


def report_line(p: dict) -> str:
    cl = p["clusters"]
    merged = sum(1 for c in cl if len(c["items"]) > 1)
    bits = [f"merge-plan: {p['ready']} ready -> {len(cl)} cluster(s)"]
    if merged:
        bits.append("; " + ", ".join(
            "{" + "+".join(c["items"]) + "} share " + (", ".join(c["catalogue"] or c["files"][:2]) or "a component")
            for c in cl if len(c["items"]) > 1))
    ride = [f"#{c['pr']}" for c in cl if c["action"] == "add-to-pr"]
    wait = [f"#{c['waits_for']}" for c in cl if c["action"] == "wait-for-queued"]
    if ride:
        bits.append("; adds to open PR " + ", ".join(ride))
    if wait:
        bits.append("; waits for queued PR " + ", ".join(wait))
    if p["catalogue_once"]:
        bits.append("; catalogue " + ", ".join(f"{f} once (cluster {n})" for f, n in p["catalogue_once"].items()))
    if p["unmapped"]:
        bits.append("; NO PATHS for " + ", ".join(p["unmapped"]))
    return "".join(bits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_merge_plan.py", description=__doc__.split("\n")[0])
    ap.add_argument("--ready", required=True)
    ap.add_argument("--paths", required=True)
    ap.add_argument("--open-prs", default=None)
    ap.add_argument("--catalogue", action="append", default=None)
    a = ap.parse_args(argv)
    try:
        cat = catalogue_globs(Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()), a.catalogue)
        out = plan(_json_arg(a.ready, "--ready"), _json_arg(a.paths, "--paths"),
                   _json_arg(a.open_prs, "--open-prs") if a.open_prs else None, cat)
    except Refused as exc:
        print(f"refused: {exc}")
        return 2
    print(report_line(out))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
