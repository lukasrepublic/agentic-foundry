#!/usr/bin/env python3
"""foundry_release_plan — plan an epic into increments and batches (dl-04, /foundry:release-plan).

    foundry_release_plan.py plan    <epic> [--from-json <file> | --project <owner>/<number>]
    foundry_release_plan.py publish <epic> [--from-json <file> | --project <owner>/<number>]
                                      [--remote origin] [--dry-run]

PLANS ONLY. It claims nothing and dispatches nothing.

Atoms are read from a corpus: a JSON file (tests), or the epic's GitHub Project through LIVE issue nodes
(`gh api graphql`, never search). Corpus shape:
    {"project_id": "...", "atoms": [{"id", "node_id", "item_id", "blocked_by": [ids], "sub_issues": [ids],
                                     "paths": [...], "labels": [...], "state": "open|closed",
                                     "component": "<optional>", "head": "<optional sha>"}]}
Edges: native issue dependencies (`blocked_by`) plus sub-issue order. The order of an atom's own sub-issues
is a sequence (each is blocked_by the one before it); the sub-issues of an epic issue (a parent that is not
an atom) carry no order. Paths come from each issue body's `## Design` -> `Components:` line. A closed atom
satisfies its dependents. An atom with the `hold` label stays in the plan and leaves the open increment; the label
is only read, never written.

Pipeline: increment planner (foundry_wave_plan) -> merge-plan batching within each increment (foundry_merge_plan) ->
critical path and total float per batch (CPM; weight = `size:` label weight, else 1 per atom) ->
release-plan.yaml, validated against schema/release-plan.schema.json. Same corpus, byte-identical output.

`publish` commits the plan to refs/heads/plan/<epic> by git plumbing (private index, never the work tree)
and sets the Project fields `increment` (Number) and `batch` (Text) on each item, creating them when absent.
Every network call goes through the one function `gh_graphql`.

Exit: 0 ok, 2 refused (one `foundry_release_plan: refused: ...` line on stderr).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_merge_plan as mp  # noqa: E402
import foundry_message_log as fml  # noqa: E402  (the hardened git adapter)
import foundry_wave_plan as wp  # noqa: E402

SCHEMA_PATH = HERE.parent / "schema" / "release-plan.schema.json"
SIZE_WEIGHT = {"xs": 1, "s": 2, "m": 3, "l": 5, "xl": 8}
PROGRAMME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
PLAN_FILE = "release-plan.yaml"
MAX_PAGES = 600            # Project item pages (100 each)
MAX_PATHS_PER_ATOM = 200   # Components paths per atom


class PlanError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# corpus -> atoms
# ---------------------------------------------------------------------------------------------
def components_from_body(body: str) -> list:
    """Paths from the `Components:` line inside the issue body's `## Design` section."""
    in_design = False
    for line in (body or "").splitlines():
        h = re.match(r"^(#+)\s*(.*?)\s*$", line)
        if h:
            in_design = len(h.group(1)) == 2 and h.group(2).strip().casefold() == "design"
            continue
        if in_design:
            m = re.match(r"^\s*(?:[-*]\s*)?\**Components:\**\s*(.*)$", line)
            if m:
                parts = [p.strip().strip("`").strip() for p in m.group(1).split(",")]
                parts = [p for p in parts if p]
                if len(parts) > MAX_PATHS_PER_ATOM:
                    raise PlanError(f"a Components line names {len(parts)} paths; the limit is {MAX_PATHS_PER_ATOM}")
                return parts
    return []


def _weight(atom) -> int:
    for lab in atom.get("labels") or []:
        if lab.startswith("size:") and lab[5:] in SIZE_WEIGHT:
            return SIZE_WEIGHT[lab[5:]]
    return 1


def _planned_atoms(corpus) -> list:
    """The open atoms, with edges to closed or unknown atoms dropped and sub-issue order added."""
    atoms = corpus.get("atoms")
    if not isinstance(atoms, list):
        raise PlanError("corpus needs an `atoms` list")
    by_id = {}
    for a in atoms:
        if not isinstance(a, dict) or not str(a.get("id", "")).strip():
            raise PlanError("every atom needs an id")
        if a["id"] in by_id:
            raise PlanError(f"duplicate atom id {a['id']!r}")
        by_id[a["id"]] = a
    edges = {i: set(a.get("blocked_by") or []) for i, a in by_id.items()}
    for i, a in by_id.items():
        subs = [s for s in (a.get("sub_issues") or []) if s in by_id]
        for prev, nxt in zip(subs, subs[1:]):
            edges[nxt].add(prev)
    open_ids = {i for i, a in by_id.items() if str(a.get("state", "open")).lower() == "open"}
    out = []
    for i in sorted(open_ids):
        a = by_id[i]
        out.append({"id": i, "node_id": a.get("node_id") or i, "item_id": a.get("item_id"),
                    "blocked_by": sorted(edges[i] & open_ids), "paths": sorted(set(a.get("paths") or [])),
                    "labels": sorted(a.get("labels") or []), "component": a.get("component"),
                    "head": a.get("head"), "weight": _weight(a)})
    return out


# ---------------------------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------------------------
def build_plan(epic: str, corpus: dict) -> dict:
    atoms = _planned_atoms(corpus)
    by_id = {a["id"]: a for a in atoms}
    increments = wp.plan_waves(atoms)

    increment_batches = []          # [[{id, atoms, paths, weight}]]
    batch_of = {}
    for n, ids in enumerate(increments, 1):
        paths = {i: by_id[i]["paths"] for i in ids}
        ready = [{"id": i, "component": by_id[i]["component"]} for i in ids]
        clusters = mp.plan(ready, paths, None, ())["clusters"]
        batches = []
        for c in sorted(clusters, key=lambda c: sorted(c["items"])[0]):
            members = sorted(c["items"])
            b = {"id": f"w{n}-{members[0]}", "atoms": members,
                 "paths": sorted({p for i in members for p in by_id[i]["paths"]}),
                 "weight": sum(by_id[i]["weight"] for i in members)}
            for i in members:
                batch_of[i] = b["id"]
            batches.append(b)
        increment_batches.append(batches)

    preds = {b["id"]: set() for w in increment_batches for b in w}
    for a in atoms:
        for d in a["blocked_by"]:
            if batch_of[d] == batch_of[a["id"]]:
                raise PlanError(f"atoms {d!r} and {a['id']!r} share a batch but one blocks the other")
            preds[batch_of[a["id"]]].add(batch_of[d])
    # Overlap-induced ordering: an atom sits after every earlier-increment atom whose paths overlap its own, so that
    # atom is a predecessor too (total float must never be optimistic about work that cannot run in parallel).
    increment_of_atom = {i: n for n, ids in enumerate(increments) for i in ids}
    for x in atoms:
        for y in atoms:
            if increment_of_atom[y["id"]] < increment_of_atom[x["id"]] and any(
                    wp.paths_overlap(p, q) for p in x["paths"] for q in y["paths"]):
                preds[batch_of[x["id"]]].add(batch_of[y["id"]])
    flat = [b for w in increment_batches for b in w]
    increment_index = {b["id"]: n for n, w in enumerate(increment_batches) for b in w}
    for bid, ps in preds.items():
        if any(increment_index[p] >= increment_index[bid] for p in ps):
            raise PlanError(f"batch {bid!r} sits in an increment with, or before, a batch it depends on")

    # critical path method over the batch graph (flat is in increment order = topological order)
    es, ef = {}, {}
    for b in flat:
        es[b["id"]] = max((ef[p] for p in preds[b["id"]]), default=0)
        ef[b["id"]] = es[b["id"]] + b["weight"]
    end = max(ef.values(), default=0)
    succs = {bid: set() for bid in preds}
    for bid, ps in preds.items():
        for p in ps:
            succs[p].add(bid)
    lf, ls = {}, {}
    for b in reversed(flat):
        lf[b["id"]] = min((ls[s] for s in succs[b["id"]]), default=end)
        ls[b["id"]] = lf[b["id"]] - b["weight"]
    critical = []
    if flat:
        cur = sorted((b["id"] for b in flat if ef[b["id"]] == end))[0]
        while True:
            critical.append(cur)
            tight = sorted(p for p in preds[cur] if ef[p] == es[cur])
            if not tight:
                break
            cur = tight[0]
        critical.reverse()

    held = {a["id"] for a in atoms if "hold" in a["labels"]}
    out_increments, open_increment, wip_limit = [], None, 0
    for n, batches in enumerate(increment_batches, 1):
        out_increments.append({"n": n, "batches": [
            {"id": b["id"], "atoms": b["atoms"], "paths": b["paths"], "total_float": ls[b["id"]] - es[b["id"]]}
            for b in batches]})
    # A batch is startable only when it holds no held atom and has no predecessor at all: every atom in the plan is
    # open (not merged), so a batch behind any open or held batch must wait (a hold propagates to its dependents).
    startable = [b for b in flat if not held & set(b["atoms"]) and not preds[b["id"]]]
    if startable:
        open_increment = min(increment_index[b["id"]] for b in startable) + 1
        wip_limit = len(startable)

    canon = json.dumps([{k: a[k] for k in ("id", "node_id", "blocked_by", "paths", "labels", "component", "weight")}
                        for a in atoms], sort_keys=True, separators=(",", ":"))
    gen = {"node_ids": sorted(a["node_id"] for a in atoms),
           "corpus_sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest()}
    heads = {a["id"]: a["head"] for a in atoms if a["head"]}
    if heads:
        gen["heads"] = heads
    plan = {"epic": epic, "generated_from": gen, "increments": out_increments, "critical_path": critical,
            "held": sorted(held), "open_increment": open_increment, "wip_limit": wip_limit}
    validate(plan)
    return plan


def validate(plan: dict) -> None:
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(plan, schema)
    except jsonschema.ValidationError as e:
        raise PlanError(f"plan fails schema/release-plan.schema.json: {e.message}")


def render(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False, default_flow_style=False, allow_unicode=True, width=10_000)


# ---------------------------------------------------------------------------------------------
# GitHub: ONE network function
# ---------------------------------------------------------------------------------------------
def gh_graphql(query: str, variables: dict | None = None) -> dict:
    """The only network call in this file. Sends a real JSON body (`gh api graphql --input -`) so every variable,
    including a ProjectV2FieldValue object, keeps its type. Returns the GraphQL `data` object."""
    body = json.dumps({"query": query, "variables": variables or {}}, sort_keys=True)
    try:
        r = subprocess.run(["gh", "api", "graphql", "--input", "-"], input=body, capture_output=True, text=True,
                           timeout=120)
    except subprocess.TimeoutExpired:
        raise PlanError("gh api graphql timed out")
    if r.returncode != 0:
        raise PlanError(f"gh api graphql failed: {fml.redact(r.stderr.strip())[:300]}")
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        raise PlanError("gh api graphql returned invalid JSON")
    if not isinstance(doc, dict) or doc.get("errors") or not isinstance(doc.get("data"), dict):
        raise PlanError(f"graphql errors or no data: {fml.redact(json.dumps(doc.get('errors') if isinstance(doc, dict) else doc))[:300]}")
    return doc["data"]


_ITEMS_Q = """query($owner:String!,$number:Int!,$after:String){repositoryOwner(login:$owner){... on ProjectV2Owner{
projectV2(number:$number){id fields(first:50){totalCount pageInfo{hasNextPage} nodes{... on ProjectV2FieldCommon{id name dataType}}}
items(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{id content{... on Issue{id number state body
repository{nameWithOwner}
labels(first:50){totalCount pageInfo{hasNextPage} nodes{name}}
blockedBy(first:50){totalCount pageInfo{hasNextPage} nodes{id}}
subIssues(first:100){totalCount pageInfo{hasNextPage} nodes{id}}}}}}}}}}"""


def _complete(conn, what: str) -> list:
    """The nodes of a GraphQL connection, refusing when the connection is truncated or its shape is unknown."""
    if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
        raise PlanError(f"malformed {what} connection")
    info = conn.get("pageInfo")
    if not isinstance(info, dict) or info.get("hasNextPage") is not False:
        raise PlanError(f"{what} is truncated (more than one page); refusing to plan from a partial read")
    total = conn.get("totalCount")
    if total is not None and total > len(conn["nodes"]):
        raise PlanError(f"{what} is truncated ({len(conn['nodes'])} of {total}); refusing to plan from a partial read")
    return conn["nodes"]


def _project_pages(owner: str, number: int):
    after, items, seen = None, [], set()
    for _ in range(MAX_PAGES):
        v = {"owner": owner, "number": number}
        if after:
            v["after"] = after
        data = gh_graphql(_ITEMS_Q, v)
        proj = ((data.get("repositoryOwner") or {}).get("projectV2"))
        if not proj:
            raise PlanError(f"no Project {owner}/{number}")
        pid = proj["id"]
        fields = _complete(proj["fields"], "Project fields")
        items += proj["items"]["nodes"]
        info = proj["items"]["pageInfo"]
        if not info["hasNextPage"]:
            return pid, fields, items
        after = info["endCursor"]
        if not after or after in seen:
            raise PlanError("Project paging repeated a cursor; refusing")
        seen.add(after)
    raise PlanError(f"Project has more than {MAX_PAGES} pages of items; refusing")


def fetch_corpus(owner: str, number: int) -> dict:
    pid, fields, items = _project_pages(owner, number)
    issues = [(it["id"], it["content"]) for it in items if (it.get("content") or {}).get("id")]
    labels_of = {c["id"]: [l["name"] for l in _complete(c["labels"], f"labels of {c['id']}")] for _, c in issues}
    issues = [(i, c) for i, c in issues if "epic" not in labels_of[c["id"]]]
    key = {c["id"]: f"{c['repository']['nameWithOwner']}#{c['number']}" for _, c in issues}
    atoms = []
    for item_id, c in issues:
        atoms.append({"id": key[c["id"]], "node_id": c["id"], "item_id": item_id,
                      "blocked_by": sorted(key[b["id"]] for b in _complete(c["blockedBy"], f"blockedBy of {key[c['id']]}")
                                           if b["id"] in key),
                      "sub_issues": [key[s["id"]] for s in _complete(c["subIssues"], f"subIssues of {key[c['id']]}")
                                     if s["id"] in key],
                      "paths": components_from_body(c.get("body") or ""),
                      "labels": labels_of[c["id"]],
                      "state": str(c["state"]).lower()})
    return {"project_id": pid,
            "fields": [{"id": f["id"], "name": f["name"], "dataType": f.get("dataType")} for f in fields if f.get("id")],
            "atoms": atoms}


_CREATE_Q = """mutation($project:ID!,$name:String!,$type:ProjectV2CustomFieldType!){createProjectV2Field(input:{
projectId:$project,dataType:$type,name:$name}){projectV2Field{... on ProjectV2FieldCommon{id name}}}}"""
_SET_Q = """mutation($project:ID!,$item:ID!,$field:ID!,$value:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(
input:{projectId:$project,itemId:$item,fieldId:$field,value:$value}){projectV2Item{id}}}"""
_CLEAR_Q = """mutation($project:ID!,$item:ID!,$field:ID!){clearProjectV2ItemFieldValue(
input:{projectId:$project,itemId:$item,fieldId:$field}){projectV2Item{id}}}"""
_FIELD_TYPES = (("increment", "NUMBER"), ("batch", "TEXT"))


def project(plan: dict, corpus: dict) -> int:
    """Set Number field `increment` and Text field `batch` on each planned item, and clear them on closed atoms.
    Refuses a pre-existing field of the wrong type. Returns the count of planned items set."""
    pid = corpus.get("project_id")
    if not pid:
        raise PlanError("no Project to project onto (corpus has no project_id)")
    existing = {f["name"]: f for f in corpus.get("fields") or []}
    for name, dtype in _FIELD_TYPES:
        got = existing.get(name)
        if got is not None and got.get("dataType") != dtype:
            raise PlanError(f"Project field {name!r} exists with type {got.get('dataType')}, not {dtype}; refusing to use it")
    fields = {n: f["id"] for n, f in existing.items()}
    made = set()
    for name, dtype in _FIELD_TYPES:
        if name not in fields:
            res = gh_graphql(_CREATE_Q, {"project": pid, "name": name, "type": dtype})
            fields[name] = res["createProjectV2Field"]["projectV2Field"]["id"]
            made.add(name)
    item_of = {a["id"]: a.get("item_id") for a in corpus["atoms"]}
    count = 0
    for w in plan["increments"]:
        for b in w["batches"]:
            for aid in b["atoms"]:
                item = item_of.get(aid)
                if not item:
                    continue
                for fname, val in (("increment", {"number": w["n"]}), ("batch", {"text": b["id"]})):
                    gh_graphql(_SET_Q, {"project": pid, "item": item, "field": fields[fname], "value": val})
                count += 1
    planned = {aid for w in plan["increments"] for b in w["batches"] for aid in b["atoms"]}
    for a in corpus["atoms"]:
        if a["id"] not in planned and a.get("item_id") and str(a.get("state", "open")).lower() != "open":
            for fname, _ in _FIELD_TYPES:
                if fname not in made:
                    gh_graphql(_CLEAR_Q, {"project": pid, "item": a["item_id"], "field": fields[fname]})
    return count


# ---------------------------------------------------------------------------------------------
# the planner ref
# ---------------------------------------------------------------------------------------------
def _ok(ad, repo, args, what, env=None, input_text=None):
    r = ad.git(args, repo, env=env, input_text=input_text)
    if r.returncode != 0:
        raise PlanError(f"{what} failed: {fml.redact((r.stderr or r.stdout).strip())[:300]}")
    return r.stdout


def publish_ref(repo, remote: str, epic: str, text: str, before_push=None) -> str:
    """Commit `text` as release-plan.yaml onto refs/heads/plan/<epic> and push (never forced). Returns the
    tip sha; an unchanged plan makes no new commit. `before_push()` runs after the commit is built and before the
    push (and before returning for an unchanged plan), so a failure there leaves the ref untouched. Refuses when the
    existing tip holds any path other than release-plan.yaml."""
    if not PROGRAMME_RE.match(epic or "") or epic.endswith((".lock", ".")) or ".." in epic:
        raise PlanError(f"epic name {epic!r} is not a plain ref-safe name")
    if not fml.valid_remote(remote):
        raise PlanError(f"remote {remote!r} is not a plain remote name or URL")
    ad, ref = fml.Adapter(), f"refs/heads/plan/{epic}"
    mine = f"refs/foundry/plan-fetched-{os.getpid()}"
    try:
        tip = None
        f = ad.git(["fetch", "--no-tags", "--quiet", fml.EOO, remote, f"+{ref}:{mine}"], repo)
        if f.returncode == 0:
            tip = _ok(ad, repo, ["rev-parse", "--verify", mine], "rev-parse").strip()
        elif "couldn't find remote ref" not in (f.stderr or ""):
            raise PlanError(f"fetch of {ref} failed: {fml.redact((f.stderr or f.stdout).strip())[:300]}")
        if tip:
            names = [n for n in _ok(ad, repo, ["ls-tree", "-r", "--name-only", tip], "ls-tree").splitlines() if n]
            if names != [PLAN_FILE]:
                raise PlanError(f"{ref} holds paths other than {PLAN_FILE} ({sorted(set(names) - {PLAN_FILE})[:5]}); "
                                "refusing to publish over a ref this tool does not own")
        with tempfile.TemporaryDirectory(prefix="foundry-plan-") as td:
            env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
            env.update(fml._identity_env(ad, repo))
            _ok(ad, repo, ["read-tree", "--empty"], "read-tree", env=env)
            sha = _ok(ad, repo, ["hash-object", "-w", "--stdin"], "hash-object", env=env, input_text=text).strip()
            _ok(ad, repo, ["update-index", "--add", "--cacheinfo", f"100644,{sha},{PLAN_FILE}"], "update-index", env=env)
            tree = _ok(ad, repo, ["write-tree"], "write-tree", env=env).strip()
            unchanged = bool(tip) and _ok(ad, repo, ["rev-parse", f"{tip}^{{tree}}"], "rev-parse tree").strip() == tree
            commit = tip
            if not unchanged:
                args = ["-c", "commit.gpgsign=false", "commit-tree", tree, "-m", f"release-plan: {epic}"]
                if tip:
                    args += ["-p", tip]
                commit = _ok(ad, repo, args, "commit-tree", env=env).strip()
        if before_push:
            before_push()
        if unchanged:
            return tip
        p = ad.git(["push", "--porcelain", fml.EOO, remote, f"{commit}:{ref}"], repo)
        if p.returncode != 0:
            raise PlanError(f"push of {ref} refused: {fml.redact((p.stderr or p.stdout).strip())[:300]}")
        return commit
    finally:
        ad.git(["update-ref", "-d", mine], repo)


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _load(args) -> dict:
    if args.from_json:
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        target = (root / args.from_json).resolve()
        if target != root and root not in target.parents:
            raise PlanError(f"--from-json {args.from_json!r} is outside the project root")
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise PlanError(f"cannot read corpus {args.from_json!r}: {e}")
    if args.project:
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9-]*)/(\d+)$", args.project)
        if not m:
            raise PlanError("--project must be <owner>/<number>")
        return fetch_corpus(m.group(1), int(m.group(2)))
    raise PlanError("give --from-json <file> or --project <owner>/<number>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_release_plan.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for name in ("plan", "publish"):
        p = sub.add_parser(name)
        p.add_argument("epic")
        p.add_argument("--from-json")
        p.add_argument("--project")
        if name == "publish":
            p.add_argument("--remote", default="origin")
            p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        if not PROGRAMME_RE.match(a.epic):
            raise PlanError(f"epic name {a.epic!r} is not a plain name")
        corpus = _load(a)
        plan = build_plan(a.epic, corpus)
        text = render(plan)
        if a.verb == "plan" or a.dry_run:
            sys.stdout.write(text)
            return 0
        counted = []
        # Only an explicit --project may mutate a Project: a corpus file never carries that authority.
        has_project = bool(a.project) and bool(corpus.get("project_id"))
        sha = publish_ref(os.getcwd(), a.remote, a.epic, text,
                          before_push=(lambda: counted.append(project(plan, corpus))) if has_project else None)
        msg = f"published plan/{a.epic} at {sha}; "
        msg += f"{counted[0]} Project item(s) set" if counted else "Project fields skipped (no --project)"
        print(msg)
        return 0
    except (PlanError, wp.WavePlanError, mp.Refused, KeyError, TypeError, AttributeError, ValueError,
            subprocess.TimeoutExpired, OSError) as e:
        print(f"foundry_release_plan: refused: {fml.redact(str(e)).splitlines()[0] if str(e) else type(e).__name__}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
