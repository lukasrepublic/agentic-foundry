#!/usr/bin/env python3
"""Derive DRAFT architecture blueprints from an existing codebase (intake existing, between select and baseline).

  foundry_architecture_derive.py [--repo DIR] [--dry-run] [--json]
      [--python-graph FILE | --python-package PKG]      Grimp graph JSON, or run Grimp on PKG
      [--js-graph FILE | --js-source DIR]               `depcruise -T json` output, or run depcruise on DIR
      [--tofu-plan-json FILE | --tofu-plan FILE]        `tofu show -json` output, or run `tofu show -json` on a plan
      [--manifests PATH ...] [--helm-chart DIR] [--kustomize DIR]   rendered manifests, or render them

Extraction uses the ecosystem's own tool only (Grimp, depcruise, tofu, helm, kustomize); this script parses no source
code. A tool that is missing or fails is reported `could-not-look:<tool>` and writes nothing for that ecosystem, never an
empty graph. Java and Nx report the named gap `no-derive:<ecosystem>`.

The one piece of logic that is ours: collapse the module graph to folders, collapse cycles (SCCs), level the condensed
graph topologically and propose the ordered layers with the edge counts that justify them. Everything written is a DRAFT:
the contract (`.importlinter` or `.dependency-cruiser.cjs`), docs/architecture/workspace.dsl (every element tagged `draft`
with a `source` property), retroactive MADR drafts and a confirmation checklist. This script never writes a baseline,
never applies infrastructure and refuses a dirty tracked tree.

Exit: 0 ok, 1 a tool was missing or failed (a reported gap), 2 refused (dirty tree, not a git repo, bad input).
"""
import argparse
import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

DSL_PATH = "docs/architecture/workspace.dsl"
CHECKLIST_PATH = "docs/architecture/DERIVE-CONFIRM.md"
DECISIONS_DIR = "docs/decisions"
ROOT_UNIT = "(root)"

# Only these tool invocations are ever made. `tofu apply` and friends are not in the list, so they cannot be run.
_ALLOWED_TOOLS = {
    "depcruise": {"-T"},
    "tofu": {"show"},
    "helm": {"template"},
    "kustomize": {"build"},
    "git": {"status", "ls-files", "rev-parse"},
}

COMPUTE_TYPES = {
    "aws_instance", "aws_ecs_service", "aws_ecs_task_definition", "aws_lambda_function", "aws_eks_cluster",
    "aws_eks_node_group", "aws_autoscaling_group", "aws_launch_template", "google_compute_instance",
    "google_cloud_run_service", "google_cloud_run_v2_service", "google_container_cluster", "azurerm_linux_virtual_machine",
    "azurerm_kubernetes_cluster", "azurerm_container_app", "kubernetes_deployment", "kubernetes_stateful_set",
}
DATASTORE_TYPES = {
    "aws_db_instance", "aws_rds_cluster", "aws_dynamodb_table", "aws_elasticache_cluster", "aws_elasticache_replication_group",
    "aws_docdb_cluster", "aws_s3_bucket", "google_sql_database_instance", "google_bigquery_dataset", "google_storage_bucket",
    "azurerm_postgresql_flexible_server", "azurerm_mssql_server", "azurerm_cosmosdb_account", "azurerm_storage_account",
}
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
NETWORK_KINDS = {"Service", "Ingress"}


class Refused(Exception):
    pass


class CouldNotLook(Exception):
    def __init__(self, tool, why=""):
        super().__init__(tool)
        self.tool, self.why = tool, why


# ------------------------------------------------------------------------------------------ tools / git ----------

def run_tool(argv, cwd=None):
    """Run one allowed read-only tool invocation; return stdout or raise CouldNotLook."""
    tool = Path(argv[0]).name
    allowed = _ALLOWED_TOOLS.get(tool)
    if allowed is None or len(argv) < 2 or argv[1] not in allowed:
        raise Refused(f"refused: {' '.join(argv[:2])} is not an allowed extraction command")
    exe = shutil.which(argv[0])
    if exe is None:
        raise CouldNotLook(tool, "not installed")
    try:
        p = subprocess.run([exe] + list(argv[1:]), cwd=cwd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise CouldNotLook(tool, str(e))
    if p.returncode != 0:
        raise CouldNotLook(tool, f"exit {p.returncode}: {p.stderr.strip()[:200]}")
    return p.stdout


def git(repo, *args):
    return run_tool(["git", *args], cwd=str(repo))


def check_tree(repo):
    """Refuse a non-repo or a dirty tracked tree (untracked files do not count)."""
    try:
        git(repo, "rev-parse", "--is-inside-work-tree")
    except CouldNotLook:
        raise Refused(f"refused: {repo} is not a git work tree")
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise Refused("refused: the tracked tree is dirty; commit or stash first")


def tracked_files(repo):
    try:
        return [l for l in git(repo, "ls-files").splitlines() if l]
    except CouldNotLook:
        return []


def import_grimp():
    try:
        return importlib.import_module("grimp")   # optional: the ecosystem tool, not a plugin dependency
    except ImportError:
        return None


# ------------------------------------------------------------------------------------------ extraction ----------

def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"refused: cannot read JSON {path}: {e}")


def python_from_grimp(pkg, repo):
    grimp = import_grimp()
    if grimp is None:
        raise CouldNotLook("grimp", "not importable")
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(Path(repo) / "src"))
    try:
        g = grimp.build_graph(pkg, include_external_packages=True)
    except Exception as e:  # grimp raises several error types
        raise CouldNotLook("grimp", str(e)[:200])
    finally:
        del sys.path[:2]
    edges = []
    for m in sorted(g.modules):
        for d in sorted(g.find_modules_directly_imported_by(m)):
            edges.append({"from": m, "to": d})
    return {"root_package": pkg, "edges": edges}


def normalise_python(doc):
    root = doc.get("root_package")
    if not root or not isinstance(doc.get("edges"), list):
        raise Refused("refused: python graph needs root_package and edges")
    internal, external = {}, {}
    mods = set()
    for e in doc["edges"]:
        a, b = e["from"], e["to"]
        if not (a == root or a.startswith(root + ".")):
            continue
        mods.add(a)
        if b == root or b.startswith(root + "."):
            internal[(a, b)] = internal.get((a, b), 0) + 1
            mods.add(b)
        else:
            external.setdefault(a, set()).add(b.split(".")[0])
    for m in doc.get("modules", []):
        if m == root or m.startswith(root + "."):
            mods.add(m)
    def unit(m):
        parts = m.split(".")
        return f"{root}.{parts[1]}" if len(parts) > 1 else ROOT_UNIT
    return {"ecosystem": "python", "tool": "grimp", "root": root, "modules": sorted(mods),
            "edges": internal, "externals": external, "unit": unit, "source": f"grimp:{root}"}


def js_pkg(name):
    parts = name.split("/")
    return "/".join(parts[:2]) if name.startswith("@") and len(parts) > 1 else parts[0]


def normalise_js(doc, source_dir):
    if not isinstance(doc.get("modules"), list):
        raise Refused("refused: depcruise JSON needs a modules list")
    prefix = (source_dir.rstrip("/") + "/") if source_dir not in ("", ".") else ""
    internal, external, mods = {}, {}, set()
    for m in doc["modules"]:
        src = m.get("source", "")
        if "node_modules/" in src or not src:
            continue
        mods.add(src)
        for d in m.get("dependencies", []):
            types = set(d.get("dependencyTypes", []))
            if types & {"npm", "npm-dev", "npm-optional", "npm-peer", "npm-bundled", "npm-no-pkg", "npm-unknown"}:
                external.setdefault(src, set()).add(js_pkg(d.get("module") or d.get("resolved", "")))
            elif "core" in types or d.get("coreModule"):
                continue
            elif d.get("resolved") and "node_modules/" not in d["resolved"]:
                internal[(src, d["resolved"])] = internal.get((src, d["resolved"]), 0) + 1
                mods.add(d["resolved"])
    def unit(m):
        rel = m[len(prefix):] if prefix and m.startswith(prefix) else m
        parts = rel.split("/")
        return parts[0] if len(parts) > 1 else ROOT_UNIT
    return {"ecosystem": "javascript", "tool": "dependency-cruiser", "root": source_dir or ".", "modules": sorted(mods),
            "edges": internal, "externals": external, "unit": unit, "source": "depcruise:" + (source_dir or ".")}


# ------------------------------------------------------------------------------------------ layering -------------

def tarjan(nodes, adj):
    index, low, onstack, stack, out, counter = {}, {}, set(), [], [], [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstack.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstack.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(sorted(comp))

    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    for n in sorted(nodes):
        if n not in index:
            visit(n)
    return out


def propose_layers(ext):
    """Folder-collapse, collapse SCCs, level topologically. Layers are listed top (depends on the rest) first."""
    unit = ext["unit"]
    units = sorted({unit(m) for m in ext["modules"]} - {ROOT_UNIT})
    counts = {}
    for (a, b), n in ext["edges"].items():
        ua, ub = unit(a), unit(b)
        if ROOT_UNIT in (ua, ub) or ua == ub:
            continue
        counts[(ua, ub)] = counts.get((ua, ub), 0) + n
    adj = {u: set() for u in units}
    for (a, b) in counts:
        adj[a].add(b)
    sccs = tarjan(units, adj)
    comp_of = {u: i for i, c in enumerate(sccs) for u in c}
    cadj = {i: set() for i in range(len(sccs))}
    for (a, b) in counts:
        if comp_of[a] != comp_of[b]:
            cadj[comp_of[a]].add(comp_of[b])
    level = {}

    def lv(i):
        if i not in level:
            level[i] = 0 if not cadj[i] else 1 + max(lv(j) for j in cadj[i])
        return level[i]

    for i in cadj:
        lv(i)
    by_level = {}
    for i, c in enumerate(sccs):
        by_level.setdefault(level[i], []).append(c)
    layers = []
    for L in sorted(by_level, reverse=True):
        groups = sorted(by_level[L])
        members = sorted(u for g in groups for u in g)
        below = sum(n for (a, b), n in counts.items() if a in members and comp_of[b] != comp_of[a])
        layers.append({"level": L, "units": members, "groups": groups, "edges_to_lower_layers": below})
    cycles = []
    for c in sccs:
        if len(c) > 1:
            n = sum(v for (a, b), v in counts.items() if a in c and b in c)
            cycles.append({"units": c, "internal_edges": n})
    cycles.sort(key=lambda c: c["units"])
    layer_of = {u: i for i, l in enumerate(layers) for u in l["units"]}
    return {"layers": layers, "cycles": cycles, "unit_edges": [{"from": a, "to": b, "count": n} for (a, b), n in sorted(counts.items())],
            "layer_of": layer_of, "units": units}


def layer_externals(ext, plan):
    """Observed external packages per layer index."""
    out = {i: set() for i in range(len(plan["layers"]))}
    for m, pk in ext["externals"].items():
        u = ext["unit"](m)
        if u in plan["layer_of"]:
            out[plan["layer_of"][u]] |= set(pk)
    return {i: sorted(v) for i, v in out.items()}


# ------------------------------------------------------------------------------------------ infra -----------------

def tofu_resources(doc):
    root = (doc.get("planned_values") or doc.get("values") or {}).get("root_module") or {}
    out = []

    def walk(mod):
        for r in mod.get("resources", []):
            out.append({"address": r.get("address"), "type": r.get("type"), "name": r.get("name")})
        for c in mod.get("child_modules", []):
            walk(c)

    walk(root)
    return sorted((r for r in out if r["address"] and r["type"]), key=lambda r: r["address"])


def read_manifests(paths):
    docs = []
    files = []
    for p in paths:
        p = Path(p)
        files += sorted(q for q in p.rglob("*") if q.suffix in (".yaml", ".yml", ".json")) if p.is_dir() else [p]
    for f in files:
        try:
            for d in yaml.safe_load_all(f.read_text(encoding="utf-8")):
                if isinstance(d, dict) and d.get("kind") and d.get("metadata"):
                    docs.append((str(f), d))
        except (OSError, yaml.YAMLError) as e:
            raise Refused(f"refused: cannot read manifest {f}: {e}")
    return docs


def manifest_nodes(docs):
    nodes = []
    for f, d in docs:
        kind, name = d["kind"], d["metadata"].get("name")
        ns = d["metadata"].get("namespace", "default")
        if not name or kind not in WORKLOAD_KINDS | NETWORK_KINDS:
            continue
        nodes.append({"kind": kind, "name": name, "ns": ns, "workload": kind in WORKLOAD_KINDS, "file": f})
    return sorted(nodes, key=lambda n: (n["ns"], n["kind"], n["name"]))


def find_deployables(repo, files):
    out = []
    for f in files:
        base = Path(f).name
        if base == "Dockerfile" or base.startswith("Dockerfile.") or base.endswith(".Dockerfile"):
            out.append({"name": (Path(f).parent.name or "root") + "-image", "technology": "Docker", "source": f})
        elif re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", base):
            try:
                svcs = (yaml.safe_load((Path(repo) / f).read_text(encoding="utf-8")) or {}).get("services") or {}
            except (OSError, yaml.YAMLError):
                svcs = {}
            for s in sorted(svcs):
                out.append({"name": s, "technology": "Docker Compose", "source": f"{f}#services.{s}"})
        elif base == "Chart.yaml":
            try:
                n = (yaml.safe_load((Path(repo) / f).read_text(encoding="utf-8")) or {}).get("name")
            except (OSError, yaml.YAMLError):
                n = None
            if n:
                out.append({"name": str(n), "technology": "Helm", "source": f})
    return sorted(out, key=lambda d: (d["name"], d["source"]))


# ------------------------------------------------------------------------------------------ writers ---------------

def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def ident(prefix, name, used):
    base = re.sub(r"[^A-Za-z0-9_]", "_", f"{prefix}_{name}").strip("_") or prefix
    i, cand = 1, base
    while cand in used:
        i += 1
        cand = f"{base}_{i}"
    used.add(cand)
    return cand


def render_importlinter(ext, plan, ext_by_layer):
    root = ext["root"]
    lines = ["# DRAFT import-linter contract derived by foundry_architecture_derive.py. Nothing enforces it until a human",
             "# confirms the layer order and names. Layers are listed top (may import the rest) to bottom.",
             "# `a : b` marks units of one layer that may import each other (a detected cycle or equal-level siblings).",
             "[importlinter]", f"root_package = {root}", "include_external_packages = True", "",
             "[importlinter:contract:layers]", "name = Layers and allowed direction (DRAFT)", "type = layers", "layers ="]
    for layer in plan["layers"]:
        lines.append("    " + " : ".join(layer["units"]))
    lines += ["", "# Observed external packages per layer (allowed-externals draft; confirm before enforcing):"]
    for i, layer in enumerate(plan["layers"]):
        lines.append(f"#   {' : '.join(layer['units'])}: {', '.join(ext_by_layer[i]) or '(none observed)'}")
    return "\n".join(lines) + "\n"


def render_depcruise(ext, plan, ext_by_layer):
    prefix = (ext["root"].rstrip("/") + "/") if ext["root"] not in ("", ".") else ""
    rules = ['    { name: "no-circular", severity: "error", from: {}, to: { circular: true } },']
    layers = plan["layers"]
    for i, layer in enumerate(layers):
        higher = [u for l in layers[:i] for u in l["units"]]
        frm = "|".join(re.escape(u) for u in layer["units"])
        if higher:
            to = "|".join(re.escape(u) for u in higher)
            rules.append(f'    {{ name: "layer-{i + 1}-not-to-higher", severity: "error", '
                         f'from: {{ path: "^{prefix}({frm})/" }}, to: {{ path: "^{prefix}({to})/" }} }},')
        allowed = ext_by_layer[i]
        pat = "|".join(re.escape(p) for p in allowed) if allowed else None
        to_ext = '{ dependencyTypes: ["npm"]' + (f', pathNot: "node_modules/({pat})(/|$)"' if pat else "") + " }"
        rules.append(f'    {{ name: "layer-{i + 1}-external-allowlist", severity: "error", '
                     f'from: {{ path: "^{prefix}({frm})/" }}, to: {to_ext} }},')
    return ("// DRAFT dependency-cruiser rules derived by foundry_architecture_derive.py. Not enforced until a human confirms\n"
            "// the layer order and the allowed external packages per layer. Layers are listed top to bottom in rule order.\n"
            "module.exports = {\n  forbidden: [\n" + "\n".join(rules) + "\n  ],\n  options: { doNotFollow: { path: \"node_modules\" } },\n};\n")


def element(kind, name, source, indent, extra=None, children=None, ident_=None):
    pad = "    " * indent
    head = f"{pad}{ident_ + ' = ' if ident_ else ''}{kind} {q(name)}" + "".join(f" {q(x)}" for x in (extra or []))
    body = [f"{pad}    tags {q('draft')}", f"{pad}    properties {{", f"{pad}        {q('source')} {q(source)}", f"{pad}    }}"]
    return [head + " {"] + body + (children or []) + [f"{pad}}}"]


def render_dsl(repo_name, codebases, deployables, nodes, tofu, tofu_src):
    used = set()
    sysid = ident("sys", repo_name, used)
    model = []
    cont = []
    for ext, plan in codebases:
        cid = ident("code", ext["root"], used)
        comps = []
        for layer in plan["layers"]:
            for u in layer["units"]:
                comps += element("component", u, f"{ext['source']}#{u}", 4, [f"layer {plan['layers'].index(layer) + 1}"],
                                 ident_=ident("comp", u, used))
        cont += element("container", ext["root"], ext["source"], 3, [ext["tool"] + " code base"], comps, ident_=cid)
    for d in deployables:
        cont += element("container", d["name"], d["source"], 3, [d["technology"]], ident_=ident("dep", d["name"], used))
    model += element("softwareSystem", repo_name, "derive:repo-root", 2, ident_=sysid, children=cont)
    envs = []
    dn = []
    for r in tofu:
        kind = "deploymentNode"
        dn += element(kind, r["address"], f"{tofu_src}#{r['address']}", 3, [r["type"]], ident_=ident("tf", r["address"], used))
    for n in nodes:
        dn += element("deploymentNode", f"{n['ns']}/{n['kind']}/{n['name']}", f"{n['file']}#{n['kind']}/{n['name']}", 3,
                      [n["kind"]], ident_=ident("k8s", f"{n['ns']}_{n['kind']}_{n['name']}", used))
    if dn:
        envs += element("deploymentEnvironment", "Live", "derive:infra-extraction", 2, children=dn)
    views = ["    views {", f"        systemContext {sysid} {{", "            include *", "            autolayout lr", "        }",
             f"        container {sysid} {{", "            include *", "            autolayout lr", "        }"]
    if dn:
        views += [f"        deployment {sysid} \"Live\" {{", "            include *", "            autolayout lr", "        }"]
    views += ["    }"]
    out = [f"workspace {q(repo_name + ' (derived draft)')} {q('DRAFT: derived by foundry_architecture_derive.py; a human confirms')} {{",
           "    !identifiers hierarchical", "    properties {", f"        {q('model-status')} {q('draft')}", "    }",
           "    model {"] + model + envs + ["    }"] + views + ["}"]
    return "\n".join(out) + "\n"


def next_adr_number(repo):
    d = Path(repo) / DECISIONS_DIR
    nums = [int(m.group(1)) for p in d.glob("*.md") if (m := re.match(r"^(\d{4})-", p.name))] if d.is_dir() else []
    return max(nums, default=0) + 1


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def adr_text(title, confidence, evidence, context, check_id="FG-ARCH-001"):
    fm = {"status": "proposed", "date": datetime.date.today().isoformat(), "retroactive": True, "confidence": confidence,
          "evidence": evidence, "decision-makers": [], "superseded-by": "", "confirmation": {"check_id": check_id}}
    return ("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n" f"# {title}\n\n"
            "> DRAFT, reconstructed from evidence by foundry_architecture_derive.py. A human states the decision and the rationale.\n\n"
            f"## Context and problem statement\n\n{context}\n\n"
            "## Decision outcome\n\nTODO(human): state the decision.\n\n"
            "### Rationale\n\nTODO(human)\n\n### Confirmation\n\n"
            f"Compliance is checked by `{check_id}` once the human confirms this record.\n")


def build_adrs(repo, codebases, tofu, tofu_src):
    adrs = []
    for ext, plan in codebases:
        if len(plan["layers"]) < 2:
            continue
        ev = [f"{ext['source']}: {len(plan['layers'])} layers, {sum(e['count'] for e in plan['unit_edges'])} unit edges, "
              f"{len(plan['cycles'])} cycle(s)"]
        ev += [f"edges {e['from']} -> {e['to']}: {e['count']}" for e in plan["unit_edges"][:12]]
        order = " > ".join(" : ".join(l["units"]) for l in plan["layers"])
        adrs.append((f"Layered dependency direction in the {ext['ecosystem']} code base",
                     "medium" if not plan["cycles"] else "low", ev,
                     f"The observed {ext['ecosystem']} imports follow this top-to-bottom order: {order}.", "FG-ARCH-001"))
    for r in tofu:
        if r["type"] in DATASTORE_TYPES:
            adrs.append((f"Datastore {r['address']} ({r['type']})", "high", [f"{tofu_src}#{r['address']}"],
                         f"The infrastructure plan declares `{r['address']}` of type `{r['type']}`.", "none"))
    n = next_adr_number(repo)
    existing = {re.sub(r"^\d{4}-", "", p.stem) for p in (Path(repo) / DECISIONS_DIR).glob("*.md")} if (Path(repo) / DECISIONS_DIR).is_dir() else set()
    out = []
    for title, conf, ev, ctx, check in adrs:
        s = slug(title)
        if s in existing:
            continue
        text = adr_text(title, conf, ev, ctx, check)
        if check == "none":
            text = text.replace("check_id: none", "check_id: none\n  reason: no automated check yet; the human names one when confirming")
        out.append((f"{DECISIONS_DIR}/{n:04d}-{s}.md", text))
        n += 1
    return out


def render_checklist(codebases, report, adr_paths, has_infra):
    L = ["# Architecture derive: what a human must confirm", "",
         "Everything derived is a DRAFT. Merging the PR that carries it is the confirmation; the baseline is generated by the",
         "`baseline` step only after that. Nothing here is enforced yet.", ""]
    for ext, plan in codebases:
        L += [f"## {ext['ecosystem']} ({ext['tool']})", "", "- [ ] Layer order and layer names (top first):"]
        L += [f"  - {i + 1}. {', '.join(l['units'])} ({l['edges_to_lower_layers']} edges to lower layers)" for i, l in enumerate(plan["layers"])]
        L += ["- [ ] Module-to-layer mapping (each folder unit sits in the layer above)",
              "- [ ] Allowed external packages per layer (observed imports; see the contract file)"]
        L += [f"- [ ] Accepted cycle (to be frozen by the baseline step): {', '.join(c['units'])} ({c['internal_edges']} edges)" for c in plan["cycles"]]
        L.append("")
    L += ["## Model", "", "- [ ] Container boundaries and external systems (not extractable; add only with a `source`)"]
    if has_infra:
        L.append("- [ ] Deployment nodes from the infrastructure extraction")
    L.append("- [ ] Replace each `draft` tag, set `model-status` to `confirmed` in docs/architecture/workspace.dsl")
    L += [f"- [ ] Decision and rationale of {p}" for p in adr_paths]
    L += ["", "## Gaps reported", ""] + ([f"- {g}" for g in report["gaps"]] or ["- none"])
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------ main ----------------

def detect(repo, files):
    names = {Path(f).name for f in files}
    eco = {}
    if "nx.json" in names:
        eco["nx"] = "no-derive:nx"
    if names & {"pom.xml", "build.gradle", "build.gradle.kts"}:
        eco["java"] = "no-derive:java"
    return eco


def autodetect_python_pkg(repo, files):
    cands = {}
    for f in files:
        parts = Path(f).parts
        if f.endswith(".py") and len(parts) >= 2:
            top = parts[1] if parts[0] == "src" and len(parts) > 2 else parts[0]
            if len(parts) > (2 if parts[0] == "src" else 1) and top not in ("tests", "test", "docs", "scripts", "src"):
                cands[top] = cands.get(top, 0) + 1
    ok = [c for c in cands if (Path(repo) / c / "__init__.py").exists() or (Path(repo) / "src" / c / "__init__.py").exists()]
    return sorted(ok, key=lambda c: (-cands[c], c))


def derive(a):
    repo = Path(a.repo).resolve()
    check_tree(repo)
    files = tracked_files(repo)
    gaps, codebases, notes = [], [], []
    for eco, msg in detect(repo, files).items():
        gaps.append(msg)

    # python
    try:
        if a.python_graph:
            codebases.append(normalise_python(load_json(a.python_graph)))
        else:
            pkg = a.python_package
            if not pkg and any(Path(f).name in ("pyproject.toml", "setup.py", "setup.cfg") for f in files):
                cands = autodetect_python_pkg(repo, files)
                pkg = cands[0] if cands else None
                notes += [f"python: other packages not derived: {', '.join(cands[1:])}"] if len(cands) > 1 else []
            if pkg:
                codebases.append(normalise_python(python_from_grimp(pkg, repo)))
    except CouldNotLook as e:
        gaps.append(f"could-not-look:{e.tool}" + (f" ({e.why})" if e.why else ""))

    # js / ts
    try:
        if a.js_graph:
            codebases.append(normalise_js(load_json(a.js_graph), a.js_source or "src"))
        elif a.js_source or "package.json" in {Path(f).name for f in files} and not detect(repo, files).get("nx"):
            src = a.js_source or ("src" if (repo / "src").is_dir() else ".")
            out = run_tool(["depcruise", "-T", "json", "--no-config", src], cwd=str(repo))
            try:
                codebases.append(normalise_js(json.loads(out), src))
            except ValueError:
                raise CouldNotLook("depcruise", "output is not JSON")
    except CouldNotLook as e:
        gaps.append(f"could-not-look:{e.tool}" + (f" ({e.why})" if e.why else ""))

    # infra
    tofu, tofu_src, docs = [], "", []
    try:
        if a.tofu_plan_json:
            tofu, tofu_src = tofu_resources(load_json(a.tofu_plan_json)), str(a.tofu_plan_json)
        elif a.tofu_plan:
            tofu = tofu_resources(json.loads(run_tool(["tofu", "show", "-json", str(a.tofu_plan)], cwd=str(repo))))
            tofu_src = str(a.tofu_plan)
    except CouldNotLook as e:
        gaps.append(f"could-not-look:{e.tool}" + (f" ({e.why})" if e.why else ""))
    except ValueError:
        gaps.append("could-not-look:tofu (output is not JSON)")
    try:
        if a.manifests:
            docs += read_manifests(a.manifests)
        if a.helm_chart:
            rendered = run_tool(["helm", "template", str(a.helm_chart)], cwd=str(repo))
            docs += [(f"helm:{a.helm_chart}", d) for d in yaml.safe_load_all(rendered) if isinstance(d, dict) and d.get("kind")]
        if a.kustomize:
            rendered = run_tool(["kustomize", "build", str(a.kustomize)], cwd=str(repo))
            docs += [(f"kustomize:{a.kustomize}", d) for d in yaml.safe_load_all(rendered) if isinstance(d, dict) and d.get("kind")]
    except CouldNotLook as e:
        gaps.append(f"could-not-look:{e.tool}" + (f" ({e.why})" if e.why else ""))
    nodes = manifest_nodes(docs)

    cb = []
    for ext in codebases:
        plan = propose_layers(ext)
        if plan["layers"]:
            cb.append((ext, plan))
    deployables = find_deployables(repo, files)

    report = {"gaps": gaps, "notes": notes, "ecosystems": [], "deployment_nodes": len(tofu) + len(nodes)}
    for ext, plan in cb:
        report["ecosystems"].append({"ecosystem": ext["ecosystem"], "tool": ext["tool"], "layers": [l["units"] for l in plan["layers"]],
                                     "layer_detail": plan["layers"], "cycles": plan["cycles"], "unit_edges": plan["unit_edges"],
                                     "externals_per_layer": layer_externals(ext, plan)})
    writes = {}
    adrs = []
    for ext, plan in cb:
        ebl = layer_externals(ext, plan)
        if ext["ecosystem"] == "python":
            writes[".importlinter"] = render_importlinter(ext, plan, ebl)
        else:
            writes[".dependency-cruiser.cjs"] = render_depcruise(ext, plan, ebl)
    if cb or tofu or nodes:
        writes[DSL_PATH] = render_dsl(repo.name, cb, deployables, nodes, tofu, tofu_src)
        adrs = build_adrs(repo, cb, tofu, tofu_src)
        for p, t in adrs:
            writes[p] = t
        writes[CHECKLIST_PATH] = render_checklist(cb, report, [p for p, _ in adrs], bool(tofu or nodes))
    # never overwrite an existing file: write the draft beside it
    final = {}
    for rel, text in writes.items():
        target = repo / rel
        final[rel + ".derived" if target.exists() else rel] = text
    report["writes"] = sorted(final)
    return repo, report, final


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--python-graph")
    ap.add_argument("--python-package")
    ap.add_argument("--js-graph")
    ap.add_argument("--js-source")
    ap.add_argument("--tofu-plan-json")
    ap.add_argument("--tofu-plan")
    ap.add_argument("--manifests", nargs="*")
    ap.add_argument("--helm-chart")
    ap.add_argument("--kustomize")
    a = ap.parse_args(argv)
    try:
        repo, report, final = derive(a)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    if not a.dry_run:
        for rel, text in final.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    if a.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(("derive-architecture (dry-run):" if a.dry_run else "derive-architecture:"))
        for e in report["ecosystems"]:
            print(f"  {e['ecosystem']} layers (top first): " + " > ".join(" : ".join(l) for l in e["layers"]))
            for c in e["cycles"]:
                print(f"  cycle finding: {', '.join(c['units'])} ({c['internal_edges']} edges); kept in one layer")
        for g in report["gaps"]:
            print(f"  gap: {g}")
        for n in report["notes"]:
            print(f"  note: {n}")
        for rel in report["writes"]:
            print(f"  {'would write' if a.dry_run else 'wrote'}: {rel}")
        print("  every output is a DRAFT; no baseline is written; a human confirms (see " + CHECKLIST_PATH + ")")
    return 1 if any(g.startswith("could-not-look") for g in report["gaps"]) else 0


if __name__ == "__main__":
    sys.exit(main())
