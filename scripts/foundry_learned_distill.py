#!/usr/bin/env python3
"""foundry_learned_distill - distil `## Learned` close comments into proposed rule edits (dl-10, decision 8).

    foundry_learned_distill.py collect <programme> [--from-json <file> | --project <owner>/<number>]
    foundry_learned_distill.py propose <programme> [--from-json <file> | --project <owner>/<number>]
                               [--out <dir>] [--org <ORG>] [--literals <file>] [--dry-run]
                               [--open-prs] [--open-tickets] [--upstream-repo <owner/name>]

PROPOSES ONLY. `propose` writes files under --out and opens nothing. A pull request or a ticket is opened only
when --open-prs / --open-tickets is given (the floor row for this script is `ask`); a human or a reviewed PR
accepts every edit.

Corpus (a JSON file for tests, or the live issue nodes of the programme's Project through `gh api graphql`, never
search):  {"atoms": [{"id": "<atom id>", "url": "<issue url>", "comments": [{"body": "<markdown>"}]}]}

A `## Learned` comment holds one line per item, at most ten, each `kind: text` with kind friction | success |
distill-candidate (aliases: worked, distill). A line names its governance category as `[security]` or
`category: security` (one of the sixteen kit categories), or by a rule id (`FG-SEC-005`, `ACME-SEC-001`), which
maps to that rule's category. A line is UPSTREAM when it names an FG- rule id or carries the marker `[upstream]`,
and never when it names a private id; every other line is adopter-private.

collect -> normalised list [{atom, line, kind, category, rule_id, url}] on stdout.
propose -> --out/proposals.json (index, private: carries atom ids and urls), --out/private/pr-body.md and
           --out/private/rules.patch.yaml (new <ORG>-<CAT>-<NNN> rules for governance/local/rules.yaml),
           --out/upstream/kit-fix-<n>.md (the kit-fix ticket shape upstream-submit expects).
Every upstream ticket passes the governance leak guard; a finding demotes that lesson to private and never
reaches the upstream output. No Learned lines -> "0 proposals" and nothing is created.

Exit: 0 ok, 2 refused (one `foundry_learned_distill: refused: ...` line on stderr).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_execution_plan as ep  # noqa: E402  (the one gh_graphql)
import foundry_governance as gov  # noqa: E402  (rule schema check + leak guard)

CATEGORIES = ("product", "architecture", "stack", "security", "contracts", "quality", "delivery", "operations",
              "cost", "observability", "data", "compliance", "ux", "exceptions", "factory", "conduct")
# The id abbreviation each category carries in rule ids (read from the shipped kits; `factory` has no kit).
ABBREV = {"product": "PRD", "architecture": "ARCH", "stack": "STK", "security": "SEC", "contracts": "API",
          "quality": "QA", "delivery": "DLV", "operations": "OPS", "cost": "COST", "observability": "OBS",
          "data": "DATA", "compliance": "LIC", "ux": "UX", "exceptions": "EXC", "factory": "FAC", "conduct": "CND"}
BY_ABBREV = {v: k for k, v in ABBREV.items()}
KINDS = {"friction": "friction", "success": "success", "worked": "success", "distill-candidate": "distill-candidate",
         "distill": "distill-candidate"}
PROPOSE_KINDS = {"friction", "distill-candidate"}
PROGRAMME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
RULE_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15})-([A-Z]{2,5})-([0-9]{3})\b")
MAX_LINES_PER_COMMENT = 10
MAX_LINE_CHARS = 600
MAX_PAGES = 200
LOCAL_RULES = "governance/local/rules.yaml"


class DistillError(Exception):
    pass


# ------------------------------------------------------------------ collect ---

_Q = """query($owner:String!,$number:Int!,$after:String){repositoryOwner(login:$owner){... on ProjectV2Owner{
projectV2(number:$number){items(first:50,after:$after){pageInfo{hasNextPage endCursor} nodes{content{... on Issue{
number url comments(last:50){totalCount nodes{body}}}}}}}}}}"""


def fetch_corpus(owner: str, number: int) -> dict:
    atoms, after, seen = [], None, set()
    for _ in range(MAX_PAGES):
        v = {"owner": owner, "number": number}
        if after:
            v["after"] = after
        try:
            data = ep.gh_graphql(_Q, v)
        except ep.PlanError as exc:
            raise DistillError(str(exc))
        proj = (data.get("repositoryOwner") or {}).get("projectV2")
        if not proj:
            raise DistillError(f"no Project {owner}/{number}")
        for node in proj["items"]["nodes"]:
            c = node.get("content") or {}
            if "number" not in c:
                continue
            conn = c.get("comments") or {}
            if (conn.get("totalCount") or 0) > len(conn.get("nodes") or []):
                raise DistillError(f"issue {c['number']}: comments truncated; refusing a partial read")
            atoms.append({"id": str(c["number"]), "url": c.get("url") or "", "comments": conn.get("nodes") or []})
        info = proj["items"]["pageInfo"]
        if not info["hasNextPage"]:
            return {"atoms": atoms}
        after = info["endCursor"]
        if not after or after in seen:
            raise DistillError("Project paging repeated a cursor; refusing")
        seen.add(after)
    raise DistillError("Project has too many pages; refusing")


def load_corpus(args) -> dict:
    if args.from_json:
        try:
            doc = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DistillError(f"corpus unreadable: {type(exc).__name__}")
        if not isinstance(doc, dict) or not isinstance(doc.get("atoms"), list):
            raise DistillError("corpus must be an object with an `atoms` list")
        return doc
    if args.project:
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*)/([0-9]+)", args.project)
        if not m:
            raise DistillError("--project must be <owner>/<number>")
        return fetch_corpus(m.group(1), int(m.group(2)))
    raise DistillError("name a corpus: --from-json <file> or --project <owner>/<number>")


def _learned_lines(body: str) -> list:
    """The lines under a `## Learned` heading, up to the next heading, capped."""
    out, on = [], False
    for raw in str(body).splitlines():
        s = raw.strip()
        if re.match(r"^#{1,6}\s", s):
            on = bool(re.match(r"^##\s+Learned\s*$", s, re.I))
            continue
        if on and s:
            out.append(s)
    return out[:MAX_LINES_PER_COMMENT]


def _parse_line(text: str):
    """(kind, category, rule_id, upstream_marker, clean text) or None when the line has no known kind."""
    s = re.sub(r"^[-*+]\s+", "", text).strip()
    m = re.match(r"^\**\s*([A-Za-z-]+)\s*\**\s*:\s*(.*)$", s)
    if not m or m.group(1).lower() not in KINDS:
        return None
    kind, rest = KINDS[m.group(1).lower()], m.group(2).strip()
    upstream = bool(re.search(r"\[upstream\]", rest, re.I))
    cat = None
    for tag in re.findall(r"\[([A-Za-z-]+)\]", rest):
        if tag.lower() in CATEGORIES:
            cat = tag.lower()
    m2 = re.match(r"^category\s*:\s*([A-Za-z-]+)\b\s*[-:;,]?\s*(.*)$", rest, re.I)
    if m2 and m2.group(1).lower() in CATEGORIES:
        cat, rest = m2.group(1).lower(), m2.group(2)
    rule = RULE_ID_RE.search(rest)
    rule_id = rule.group(0) if rule else None
    if rule and rule.group(2) in BY_ABBREV:
        cat = BY_ABBREV[rule.group(2)]
    clean = re.sub(r"\[(?:upstream|" + "|".join(CATEGORIES) + r")\]", "", rest, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip()[:MAX_LINE_CHARS]
    return kind, cat, rule_id, upstream, clean


def collect(corpus: dict) -> list:
    out = []
    for a in corpus["atoms"]:
        if not isinstance(a, dict):
            continue
        for c in a.get("comments") or []:
            for ln in _learned_lines((c or {}).get("body", "")):
                p = _parse_line(ln)
                if p is None or not p[4]:
                    continue
                kind, cat, rule_id, upstream, clean = p
                out.append({"atom": str(a.get("id", "")), "url": str(a.get("url", "")), "kind": kind,
                            "category": cat, "rule_id": rule_id, "upstream_marker": upstream, "line": clean})
    return out


# ------------------------------------------------------------------ propose ---

def _key(item: dict) -> str:
    t = unicodedata.normalize("NFKC", item["line"]).casefold()
    return item["category"] + "|" + re.sub(r"[^a-z0-9]+", " ", t).strip()


def route(item: dict) -> str:
    """private | upstream. A private id always wins: it never routes upstream."""
    rid = item.get("rule_id")
    if rid and not rid.startswith("FG-"):
        return "private"
    if rid or item.get("upstream_marker"):
        return "upstream"
    return "private"


def group(lines: list) -> tuple:
    """(proposals, unrouted). One proposal per distinct lesson; lines with no category are unrouted."""
    props, unrouted = {}, []
    for it in lines:
        if it["kind"] not in PROPOSE_KINDS:
            continue
        if not it["category"]:
            unrouted.append(it)
            continue
        k = _key(it)
        p = props.setdefault(k, {"category": it["category"], "lesson": it["line"], "route": "private",
                                 "rule_id": None, "atoms": [], "sources": [], "_routes": set()})
        p["_routes"].add(route(it))
        p["rule_id"] = p["rule_id"] or it.get("rule_id")
        if it["atom"] not in p["atoms"]:
            p["atoms"].append(it["atom"])
        if it["url"] and it["url"] not in p["sources"]:
            p["sources"].append(it["url"])
    out = []
    for p in props.values():
        routes = p.pop("_routes")
        p["route"] = "private" if "private" in routes else "upstream"
        out.append(p)
    out.sort(key=lambda p: (p["category"], p["lesson"]))
    return out, unrouted


def _infer_org(local_rules: list) -> str:
    for r in local_rules:
        m = RULE_ID_RE.fullmatch(str((r or {}).get("id", "")))
        if m and m.group(1) != "FG":
            return m.group(1)
    return "LOC"


def private_rules(props: list, local_rules: list, org: str) -> list:
    used = {}
    for r in local_rules:
        m = RULE_ID_RE.fullmatch(str((r or {}).get("id", "")))
        if m:
            used[(m.group(1), m.group(2))] = max(used.get((m.group(1), m.group(2)), 0), int(m.group(3)))
    rules = []
    for p in props:
        ab = ABBREV[p["category"]]
        n = used.get((org, ab), 0) + 1
        used[(org, ab)] = n
        rules.append({
            "id": f"{org}-{ab}-{n:03d}",
            "title": p["lesson"][:80],
            "statement": f"Builders SHOULD apply this lesson from a programme close: {p['lesson']}",
            "category": p["category"], "level": 1, "floor": False, "status": "active",
            "applies_to": {"paths": ["**"], "blueprints": ["*"]},
            "check": {"kind": "none"}, "exception": {"allowed": True, "requires": ["reason", "owner", "expires"]},
            "sources": list(p["sources"]) or [f"atom {a}" for a in p["atoms"]],
        })
    return rules


def kit_fix_body(p: dict, n: int) -> str:
    """The kit-fix ticket shape upstream-submit expects. Carries no atom id, no url, no adopter text."""
    rid = p["rule_id"] or "none (new rule proposed)"
    return "\n".join([
        f"# Kit fix: {p['category']} - {p['lesson'][:70]}", "",
        "## Kit", f"{p['category']}", "",
        "## Rule", f"{rid}", "",
        "## Lesson", p["lesson"], "",
        "## Proposed edit",
        "State the exact change to the kit rule or its check. A maintainer reviews it; nothing here is accepted "
        "automatically.", "",
        "## Evidence", f"Seen in {len(p['atoms'])} programme atom(s). Source ids stay in the adopter workspace.", ""])


def validate_rules(rules: list) -> list:
    errs = []
    for r in rules:
        errs += gov.rule_errors(r, upstream=False)
    return errs


def build(lines: list, local_rules: list, org: str, literals) -> dict:
    props, unrouted = group(lines)
    upstream, private, demoted = [], [], []
    for p in props:
        if p["route"] == "upstream":
            body = kit_fix_body(p, len(upstream) + 1)
            found = gov.leak_findings({"kit-fix.md": body}, lock=None, literals=literals)
            if not found:
                upstream.append({**p, "body": body})
                continue
            p["route"] = "private"
            p["demoted"] = True
            demoted.append(p["lesson"])
        private.append(p)
    rules = private_rules(private, local_rules, org)
    for p, r in zip(private, rules):
        p["rule_id_new"] = r["id"]
    return {"upstream": upstream, "private": private, "rules": rules, "unrouted": unrouted, "demoted": len(demoted)}


def pr_body(programme: str, private: list, rules: list) -> str:
    out = [f"# Learned: proposed edits to governance/local for programme {programme}", "",
           "Proposed by the learned distill. Review each rule; accept, edit or drop it. Nothing here is in force "
           "until this pull request merges.", ""]
    for p, r in zip(private, rules):
        out += [f"## {r['id']} ({p['category']})", "", p["lesson"], "",
                "Sources: " + ", ".join(p["sources"] or [f"atom {a}" for a in p["atoms"]]), ""]
    return "\n".join(out)


# ------------------------------------------------------------------ outputs ---

def write_outputs(out: Path, programme: str, res: dict) -> list:
    files = {}
    if res["private"]:
        files["private/pr-body.md"] = pr_body(programme, res["private"], res["rules"])
        files["private/rules.patch.yaml"] = yaml.safe_dump({"rules": res["rules"]}, sort_keys=False)
    for i, u in enumerate(res["upstream"], 1):
        files[f"upstream/kit-fix-{i}.md"] = u["body"]
    index = {"programme": programme,
             "private": [{k: p[k] for k in ("category", "lesson", "atoms", "sources", "rule_id_new")}
                         for p in res["private"]],
             "upstream": [{k: p[k] for k in ("category", "lesson", "atoms", "rule_id")} for p in res["upstream"]],
             "unrouted": [{"atom": i["atom"], "line": i["line"]} for i in res["unrouted"]]}
    files["proposals.json"] = json.dumps(index, indent=2, sort_keys=True) + "\n"
    for rel, text in files.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return sorted(files)


def _run(argv: list) -> str:
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise DistillError(f"{argv[0]} {argv[1]} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def open_prs(programme: str, out: Path, proj: Path) -> str:
    branch = f"learned/{programme}"
    target = proj / LOCAL_RULES
    patch = yaml.safe_load((out / "private/rules.patch.yaml").read_text(encoding="utf-8"))["rules"]
    doc = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}) if target.is_file() else {}
    doc["rules"] = list(doc.get("rules") or []) + patch
    _run(["git", "-C", str(proj), "switch", "-c", branch])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    _run(["git", "-C", str(proj), "add", "--", LOCAL_RULES])
    _run(["git", "-C", str(proj), "commit", "-m", f"learned: proposed rules from programme {programme}"])
    _run(["git", "-C", str(proj), "push", "-u", "origin", branch])
    return _run(["gh", "pr", "create", "--draft", "--head", branch, "--title",
                 f"learned: proposed rules from programme {programme}",
                 "--body-file", str(out / "private/pr-body.md")])


def open_tickets(out: Path, repo: str) -> list:
    urls = []
    for f in sorted((out / "upstream").glob("kit-fix-*.md")):
        title = f.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        urls.append(_run(["gh", "issue", "create", "--repo", repo, "--title", title, "--body-file", str(f)]))
    return urls


# ---------------------------------------------------------------------- cli ---

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_learned_distill.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for name in ("collect", "propose"):
        sp = sub.add_parser(name)
        sp.add_argument("programme")
        sp.add_argument("--from-json")
        sp.add_argument("--project")
        if name == "propose":
            sp.add_argument("--out")
            sp.add_argument("--org")
            sp.add_argument("--literals")
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--open-prs", action="store_true")
            sp.add_argument("--open-tickets", action="store_true")
            sp.add_argument("--upstream-repo", default="lukasrepublic/agentic-foundry")
            sp.add_argument("--project-dir", default=".")
    args = ap.parse_args(argv)
    try:
        if not PROGRAMME_RE.fullmatch(args.programme):
            raise DistillError("programme name has characters outside [A-Za-z0-9._-]")
        lines = collect(load_corpus(args))
        if args.verb == "collect":
            print(json.dumps(lines, indent=2, sort_keys=True))
            return 0
        proj = Path(args.project_dir)
        local = gov.load_local(proj)["rules"] if (proj / LOCAL_RULES).is_file() else []
        org = args.org or _infer_org(local)
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", org):
            raise DistillError("--org must match [A-Z][A-Z0-9]{1,15}")
        res = build(lines, local, org, gov.read_literals(args.literals))
        errs = validate_rules(res["rules"])
        if errs:
            raise DistillError("generated rules fail the rule schema: " + "; ".join(errs[:3]))
        n = len(res["private"]) + len(res["upstream"])
        print(f"{n} proposals ({len(res['private'])} private, {len(res['upstream'])} upstream, "
              f"{len(res['unrouted'])} unrouted, {res['demoted']} demoted by the leak guard)")
        if n == 0 or args.dry_run or not args.out:
            if n and not args.out:
                print("no --out given: nothing written")
            return 0
        out = Path(args.out)
        for f in write_outputs(out, args.programme, res):
            print(f"wrote {out / f}")
        if args.open_prs and res["private"]:
            print("pr " + open_prs(args.programme, out, proj))
        if args.open_tickets:
            for u in open_tickets(out, args.upstream_repo):
                print("ticket " + u)
        return 0
    except (DistillError, gov.Refused) as exc:
        print(f"foundry_learned_distill: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
