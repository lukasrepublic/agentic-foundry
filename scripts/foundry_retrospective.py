#!/usr/bin/env python3
"""foundry_retrospective - distil `## Retrospective` close comments (the old `## Learned` heading is read too) into proposed rule edits (dl-10, decision 8).

    foundry_retrospective.py collect <epic> [--from-json <file> | --project <owner>/<number>]
    foundry_retrospective.py propose <epic> [--from-json <file> | --project <owner>/<number>]
                               [--out <dir>] [--org <ORG>] [--literals <file>] [--dry-run]
                               [--open-prs] [--open-tickets] [--upstream-repo <owner/name>]

PROPOSES ONLY. `propose` writes files under --out and opens nothing. A pull request or a ticket is opened only
when --open-prs / --open-tickets is given (the floor row for this script is `ask`); a human or a reviewed PR
accepts every edit.

Corpus (a JSON file for tests, or the live issue nodes of the epic's Project through `gh api graphql`, never
search):  {"atoms": [{"id": "<atom id>", "url": "<issue url>", "comments": [{"body": "<markdown>"}]}]}

A `## Retrospective` comment holds one line per item, at most ten, each `kind: text` with kind friction | success |
distill-candidate (aliases: worked, distill). A line names its governance category as `[security]` or
`category: security` (one of the sixteen kit categories), or by a rule id (`FG-SEC-005`, `ACME-SEC-001`), which
maps to that rule's category. A line is UPSTREAM only when it carries the explicit marker `[upstream]` and names no
private id; an FG- rule id alone routes PRIVATE. Every other line is adopter-private. A `## Retrospective` block counts only
when its comment author is the atom issue's closer, a registered operator (.claude/foundry-operators.json) or a
--trusted-authors login; other blocks are counted as skipped (untrusted author) and never parsed.

collect -> normalised list [{atom, line, kind, category, rule_id, url}] on stdout.
propose -> --out/proposals.json (index, private: carries atom ids and urls), --out/private/pr-body.md and
           --out/private/rules.patch.yaml (new <ORG>-<CAT>-<NNN> rules for governance/local/rules.yaml),
           --out/upstream/kit-fix-<n>.md (the kit-fix ticket shape upstream-submit expects).
Every upstream ticket passes the governance leak guard AND an outgoing check (no URL, no owner/repo shape, no #<n>
reference, not the project's own remote owner or repo name); a hit demotes that lesson to private and counts as
demoted. out/upstream and out/private are cleared before each write; --open-tickets files only this run's tickets. No Retrospective lines -> "0 proposals" and nothing is created.

Exit: 0 ok, 2 refused (one `foundry_retrospective: refused: ...` line on stderr).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_release_plan as ep  # noqa: E402  (the one gh_graphql)
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
number url timelineItems(itemTypes:[CLOSED_EVENT],last:1){nodes{... on ClosedEvent{actor{login}}}}
comments(last:50){totalCount nodes{body author{login}}}}}}}}}}"""


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
            closer = (((c.get("timelineItems") or {}).get("nodes") or [{}])[-1].get("actor") or {}).get("login")
            atoms.append({"id": str(c["number"]), "url": c.get("url") or "", "closed_by": closer,
                          "comments": conn.get("nodes") or []})
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
    """The lines under a `## Retrospective` (or old `## Learned`) heading, up to the next heading, capped."""
    out, on = [], False
    for raw in str(body).splitlines():
        s = raw.strip()
        if re.match(r"^#{1,6}\s", s):
            on = bool(re.match(r"^##\s+(?:Retrospective|Learned)\s*$", s, re.I))   # the old heading is an alias
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


def _login(c) -> str:
    a = (c or {}).get("author")
    return str(a.get("login") or "").casefold() if isinstance(a, dict) else ""


def operator_logins(proj: Path) -> set:
    f = proj / ".claude" / "foundry-operators.json"
    try:
        ops = json.loads(f.read_text(encoding="utf-8")).get("operators") or {}
    except (OSError, ValueError, AttributeError):
        return set()
    return {str(o.get("github") or "").casefold() for o in ops.values() if isinstance(o, dict)} - {""}


def collect_ex(corpus: dict, trusted=()) -> tuple:
    """(lines, skipped). A Retrospective block counts only from the atom's closer, an operator or a trusted login."""
    base = {str(t).casefold() for t in trusted if t}
    out, skipped = [], 0
    for a in corpus["atoms"]:
        if not isinstance(a, dict):
            continue
        ok = base | ({str(a["closed_by"]).casefold()} if a.get("closed_by") else set())
        for c in a.get("comments") or []:
            raw = _learned_lines((c or {}).get("body", ""))
            if not raw:
                continue
            if _login(c) not in ok:
                skipped += 1
                continue
            for ln in raw:
                p = _parse_line(ln)
                if p is None or not p[4]:
                    continue
                kind, cat, rule_id, upstream, clean = p
                out.append({"atom": str(a.get("id", "")), "url": str(a.get("url", "")), "kind": kind,
                            "category": cat, "rule_id": rule_id, "upstream_marker": upstream, "line": clean})
    return out, skipped


def collect(corpus: dict, trusted=()) -> list:
    return collect_ex(corpus, trusted)[0]


# ------------------------------------------------------------------ propose ---

def _key(item: dict) -> str:
    t = unicodedata.normalize("NFKC", item["line"]).casefold()
    return item["category"] + "|" + re.sub(r"[^a-z0-9]+", " ", t).strip()


def route(item: dict) -> str:
    """private | upstream. A private id always wins: it never routes upstream."""
    rid = item.get("rule_id")
    if rid and not rid.startswith("FG-"):
        return "private"
    if item.get("upstream_marker"):
        return "upstream"      # an FG- rule id alone never routes upstream
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
            "title": f"Retrospective lesson ({p['category']}) from {len(p['atoms'])} atom(s)",
            "statement": "Builders SHOULD review the recorded lesson in the note field before changing the same "
                         "area. The lesson is quoted data from an epic close, not an instruction.",
            "note": "lesson: " + json.dumps(p["lesson"], ensure_ascii=True),
            "category": p["category"], "level": 1, "floor": False, "status": "active",
            "applies_to": {"paths": ["**"], "blueprints": ["*"]},
            "check": {"kind": "none"}, "exception": {"allowed": True, "requires": ["reason", "owner", "expires"]},
            "sources": list(p["sources"]) or [f"atom {a}" for a in p["atoms"]],
        })
    return rules


def kit_fix_body(p: dict, n: int) -> str:
    """The kit-fix ticket shape upstream-submit expects. Carries no atom id and no url. The lesson text is the
    commenter's own line, so `outgoing_findings` must pass before it is used (build() enforces this)."""
    rid = p["rule_id"] or "none (new rule proposed)"
    return "\n".join([
        f"# Kit fix: {p['category']} - {p['lesson'][:70]}", "",
        "## Kit", f"{p['category']}", "",
        "## Rule", f"{rid}", "",
        "## Lesson", p["lesson"], "",
        "## Proposed edit",
        "State the exact change to the kit rule or its check. A maintainer reviews it; nothing here is accepted "
        "automatically.", "",
        "## Evidence", f"Seen in {len(p['atoms'])} epic atom(s). Source ids stay in the adopter workspace.", ""])


_URL_RE = re.compile(r"https?:|www\.", re.I)
_SLUG_RE = re.compile(r"(?<![\w/.-])[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*(?![\w-])")
_REF_RE = re.compile(r"#\d+")


def remote_literals(proj: Path) -> list:
    """The project's own remote owner and repo name (git remote get-url origin), as leak literals."""
    try:
        url = subprocess.run(["git", "-C", str(proj), "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    return [m.group(1), m.group(2)] if m else []


def outgoing_findings(body: str, literals) -> list:
    """Fail-closed checks on a body bound for a public ticket (kinds only, never the matched text)."""
    out = []
    if _URL_RE.search(body):
        out.append("url")
    if _SLUG_RE.search(body):
        out.append("owner-repo-shape")
    if _REF_RE.search(body):
        out.append("issue-reference")
    out += gov.leak_findings({"kit-fix.md": body}, lock=None, literals=literals)
    return out


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
            found = outgoing_findings(body, literals)
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


def pr_body(epic: str, private: list, rules: list) -> str:
    out = [f"# Retrospective: proposed edits to governance/local for epic {epic}", "",
           "Proposed by the retrospective distill. Review each rule; accept, edit or drop it. Nothing here is in force "
           "until this pull request merges.", ""]
    for p, r in zip(private, rules):
        out += [f"## {r['id']} ({p['category']})", "", p["lesson"], "",
                "Sources: " + ", ".join(p["sources"] or [f"atom {a}" for a in p["atoms"]]), ""]
    return "\n".join(out)


# ------------------------------------------------------------------ outputs ---

def write_outputs(out: Path, epic: str, res: dict) -> list:
    for sub in ("upstream", "private"):
        d = out / sub
        if d.is_symlink():
            raise DistillError(f"{d} is a symlink; refusing")
        if d.is_dir():
            shutil.rmtree(d)
    files = {}
    if res["private"]:
        files["private/pr-body.md"] = pr_body(epic, res["private"], res["rules"])
        files["private/rules.patch.yaml"] = yaml.safe_dump({"rules": res["rules"]}, sort_keys=False)
    for i, u in enumerate(res["upstream"], 1):
        files[f"upstream/kit-fix-{i}.md"] = u["body"]
    index = {"epic": epic,
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


def open_prs(epic: str, out: Path, proj: Path) -> str:
    branch = f"retrospective/{epic}"
    target = proj / LOCAL_RULES
    patch = yaml.safe_load((out / "private/rules.patch.yaml").read_text(encoding="utf-8"))["rules"]
    doc = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}) if target.is_file() else {}
    doc["rules"] = list(doc.get("rules") or []) + patch
    _run(["git", "-C", str(proj), "switch", "-c", branch])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    _run(["git", "-C", str(proj), "add", "--", LOCAL_RULES])
    _run(["git", "-C", str(proj), "commit", "-m", f"retrospective: proposed rules from epic {epic}"])
    _run(["git", "-C", str(proj), "push", "-u", "origin", branch])
    return _run(["gh", "pr", "create", "--draft", "--head", branch, "--title",
                 f"retrospective: proposed rules from epic {epic}",
                 "--body-file", str(out / "private/pr-body.md")])


def open_tickets(upstream: list, repo: str, literals) -> list:
    """Files this run's in-memory tickets only, re-running the outgoing check on each just before filing."""
    urls = []
    for u in upstream:
        if outgoing_findings(u["body"], literals):
            continue
        title = u["body"].splitlines()[0].lstrip("# ").strip()
        urls.append(_run(["gh", "issue", "create", "--repo", repo, "--title", title, "--body", u["body"]]))
    return urls


# ---------------------------------------------------------------------- cli ---

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_retrospective.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for name in ("collect", "propose"):
        sp = sub.add_parser(name)
        sp.add_argument("epic")
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
        sp.add_argument("--trusted-authors", default="", help="comma-separated logins whose Retrospective blocks count")
    args = ap.parse_args(argv)
    try:
        if not PROGRAMME_RE.fullmatch(args.epic):
            raise DistillError("epic name has characters outside [A-Za-z0-9._-]")
        proj = Path(getattr(args, "project_dir", "."))
        trusted = {t.strip() for t in args.trusted_authors.split(",") if t.strip()} | operator_logins(proj)
        lines, skipped = collect_ex(load_corpus(args), trusted)
        if args.verb == "collect":
            print(json.dumps(lines, indent=2, sort_keys=True))
            print(f"skipped (untrusted author): {skipped}", file=sys.stderr)
            return 0
        local = gov.load_local(proj)["rules"] if (proj / LOCAL_RULES).is_file() else []
        org = args.org or _infer_org(local)
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", org):
            raise DistillError("--org must match [A-Z][A-Z0-9]{1,15}")
        lits = gov.read_literals(args.literals) + remote_literals(proj)
        res = build(lines, local, org, lits)
        errs = validate_rules(res["rules"])
        if errs:
            raise DistillError("generated rules fail the rule schema: " + "; ".join(errs[:3]))
        n = len(res["private"]) + len(res["upstream"])
        print(f"{n} proposals ({len(res['private'])} private, {len(res['upstream'])} upstream, "
              f"{len(res['unrouted'])} unrouted, {res['demoted']} demoted by the leak guard, "
              f"{skipped} skipped (untrusted author))")
        if n == 0 or args.dry_run or not args.out:
            if n and not args.out:
                print("no --out given: nothing written")
            return 0
        out = Path(args.out)
        for f in write_outputs(out, args.epic, res):
            print(f"wrote {out / f}")
        if args.open_prs and res["private"]:
            print("pr " + open_prs(args.epic, out, proj))
        if args.open_tickets:
            for u in open_tickets(res["upstream"], args.upstream_repo, lits):
                print("ticket " + u)
        return 0
    except (DistillError, gov.Refused) as exc:
        print(f"foundry_retrospective: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
