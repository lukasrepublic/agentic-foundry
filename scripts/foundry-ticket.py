#!/usr/bin/env python3
"""foundry-ticket — the unit of work is a GitHub issue with a runnable "Done means".

    foundry-ticket.py start <issue|url> [--repo OWNER/NAME] [--trust-author]
    foundry-ticket.py allow <path> [<path>...]        permit writing a paper path for this ticket
    foundry-ticket.py done                            run the ticket's Done-means command
    foundry-ticket.py status                          print the active ticket
    foundry-ticket.py clear                           forget the active ticket
    foundry-ticket.py next [--json]                   the operator's next item, read from GitHub (gn-08)
    foundry-ticket.py claim <issue> [--repo R] [--allow-exception "<reason>"]   record `start` (never self-assigns)
    foundry-ticket.py reconcile [<issue>] [--json]    stop_dispatch per started issue, from current GitHub state
    foundry-ticket.py lead acquire|renew|release|check [--stale-after 10m]      local duplicate-lead lease

The active ticket lives in the repository's GIT DIR (per worktree; never checked out, never committed).
Two hooks read it: `hooks/foundry-paper-guard.py` (a write to specs/, .foundry/, docs/, status-reports/,
charters/ is refused unless the ticket allows the path) and `hooks/foundry-ticket-stop.py` (the session
may not stop while the Done-means command fails). That is the whole governance model: one ticket, one
runnable check, the platform's branch protection + CI as the gate.

Issue body conventions (both optional, both plain Markdown):

    ## Done means
    ```
    python3 -m pytest tests/ -q
    ```
    ## Paper allowed
    CHANGELOG.md, docs/how-to/x.md

Only the FENCED block under `## Done means` is ever executed, and only after `start` confirmed the
issue's author has write access to the issue's own repository. `--trust-author` overrides that for a
command the OPERATOR has read; it is not silently allowed by the plugin's permission hook, and neither
is `done` (both execute input). Only stdlib. `gh` must be authenticated for `start`.
"""
from __future__ import annotations

import argparse
import calendar
import getpass
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_deck_events as deck  # noqa: E402
import foundry_ticket_store as store  # noqa: E402

STOP_CAP = 3  # how many times the Stop hook may refuse before it lets the session end
TRUSTED_PERMISSIONS = {"admin", "maintain", "write"}
project_root = store.project_root


def ticket_path(root: Path | None = None) -> Path | None:
    return store.ticket_file(root)


def load(root: Path | None = None) -> dict | None:
    p = ticket_path(root)
    if not p or not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(doc: dict, root: Path | None = None) -> Path:
    p = ticket_path(root)
    if not p:
        sys.exit("foundry-ticket: not a git repository — a ticket needs one (it lives in the git dir)")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return p


def norm_path(entry: str, root: Path | None = None) -> str:
    """Root-relative POSIX form of a paper-allowed entry: strips `./`, a trailing ` — note`,
    backticks/bullets, and resolves an absolute path under the root. Returns "" when unusable."""
    s = entry.strip().strip("-*`• ").strip()
    for sep in (" — ", " - ", " –"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    s = s.strip("`").strip()
    if not s or s.startswith("#"):
        return ""
    p = Path(s).expanduser()
    if p.is_absolute():
        try:
            p = p.resolve().relative_to((root or project_root()))
        except Exception:
            return ""
    parts = [x for x in p.as_posix().split("/") if x not in ("", ".")]
    if ".." in parts:
        return ""
    return "/".join(parts)


_DONE_RE = re.compile(r"^##+[ \t]*done[ \t]+means\b.*?$", re.I | re.M)
_PAPER_RE = re.compile(r"^##+[ \t]*paper[ \t]+allowed\b.*?$", re.I | re.M)
_FENCE_RE = re.compile(r"^(```+|~~~+)[^\n]*\n(.*?)^\1[ \t]*$", re.S | re.M)
_HEADING_RE = re.compile(r"^##+[ \t]", re.M)
_ISSUE_URL_RE = re.compile(r"https?://[^/]+/([^/]+/[^/]+)/issues/\d+")


def parse_body(body: str) -> tuple[str, list[str]]:
    """Return (done_cmd, paper_allowed). Only a fenced block directly under `## Done means` counts
    as the command; an unfenced section yields "" (nothing is ever executed from prose)."""
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    done = ""
    m = _DONE_RE.search(body)
    if m:
        rest = body[m.end():]
        fm = _FENCE_RE.search(rest)
        nxt = _HEADING_RE.search(rest)
        if fm and (nxt is None or fm.start() < nxt.start()):
            done = fm.group(2).strip()
    paper: list[str] = []
    pm = _PAPER_RE.search(body)
    if pm:
        rest = body[pm.end():]
        nxt = _HEADING_RE.search(rest)
        sec = rest[: nxt.start()] if nxt else rest
        for raw in re.split(r"[,\n]", sec):
            item = norm_path(raw)
            if item and item not in paper:
                paper.append(item)
    return done, paper


def _gh_json(args: list[str]) -> dict | None:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def repo_of_issue(issue: dict, explicit: str | None) -> str | None:
    """OWNER/NAME of the issue itself (from its URL) — the permission check must run against the
    repository the issue lives in, never the local checkout (security review of #270, R5)."""
    m = _ISSUE_URL_RE.match(issue.get("url") or "")
    if m:
        return m.group(1)
    return explicit


def author_trusted(repo: str | None, login: str, gh_json=_gh_json) -> tuple[bool, str]:
    """True when `login` has write/maintain/admin on `repo` (or is the authenticated user)."""
    if not login:
        return False, "issue author unknown"
    me = gh_json(["api", "user"]) or {}
    if me.get("login") and me["login"] == login:
        return True, "issue author is the authenticated user"
    if not repo:
        return False, "the issue's repository could not be determined"
    perm = gh_json(["api", f"repos/{repo}/collaborators/{login}/permission"]) or {}
    level = perm.get("permission") or ""
    if level in TRUSTED_PERMISSIONS:
        return True, f"issue author {login} has {level} access on {repo}"
    return False, f"issue author {login} has {level or 'no'} access on {repo}"


def cmd_start(args: list[str]) -> int:
    if not args:
        sys.exit("usage: foundry-ticket.py start <issue|url> [--repo OWNER/NAME] [--trust-author]")
    number = args[0]
    repo = None
    if "--repo" in args:
        i = args.index("--repo")
        if i + 1 >= len(args):
            sys.exit("foundry-ticket: --repo needs OWNER/NAME")
        repo = args[i + 1]
    trust = "--trust-author" in args
    cmd = ["issue", "view", str(number), "--json", "number,title,body,url,author,state"]
    if repo:
        cmd += ["--repo", repo]
    issue = _gh_json(cmd)
    if issue is None:
        sys.exit("foundry-ticket: gh issue view failed (is gh authenticated, and is the issue number right?)")
    done, paper = parse_body(issue.get("body") or "")
    login = ((issue.get("author") or {}).get("login")) or ""
    issue_repo = repo_of_issue(issue, repo)
    trusted_by = "author"
    if done and not trust:
        ok, why = author_trusted(issue_repo, login)
        if not ok:
            sys.exit("foundry-ticket: the Done-means command in this issue was not adopted — " + why +
                     ". The Stop hook would run it unattended, so a command from outside the repository's "
                     "write team needs the OPERATOR to read it and start the ticket themselves. Nothing was recorded.")
    elif done and trust:
        trusted_by = "flag"
    doc = {
        "issue": issue["number"],
        "url": issue.get("url", ""),
        "repo": issue_repo or "",
        "title": issue.get("title", ""),
        "author": login,
        "state": issue.get("state", ""),
        "done": done,
        "done_sha256": store.command_digest(done),
        "trusted_by": trusted_by,
        "paper_allowed": paper,
        "status": "open",
        "stop_blocks": 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = save(doc)
    print(f"ticket #{doc['issue']} active: {doc['title']}")
    print(f"  done means : {store.redact(done) or '(none declared — add a fenced block under `## Done means`)'}")
    print(f"  paper      : {', '.join(paper) if paper else '(none — paper writes are refused)'}")
    print(f"  recorded   : {p}")
    return 0


def cmd_allow(args: list[str]) -> int:
    root = project_root()
    doc = load(root)
    if not doc:
        sys.exit("foundry-ticket: no active ticket; run `foundry-ticket.py start <issue>` first")
    if not args:
        sys.exit("usage: foundry-ticket.py allow <path> [<path>...]")
    for a in args:
        n = norm_path(a, root)
        if not n:
            sys.exit(f"foundry-ticket: {a!r} is not a path under the project root")
        if n not in doc["paper_allowed"]:
            doc["paper_allowed"].append(n)
    save(doc, root)
    print("paper allowed for #%s: %s" % (doc["issue"], ", ".join(doc["paper_allowed"])))
    return 0


def run_done(doc: dict, root: Path, timeout: int = 1800) -> tuple[int, str]:
    cmd = (doc.get("done") or "").strip()
    if not cmd:
        return 0, "no Done-means command declared"
    if store.command_digest(cmd) != doc.get("done_sha256"):
        return 125, "the recorded Done-means command does not match its digest; re-run `start`"
    proc = None
    try:
        proc = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            out, rc = "", 124
    except OSError as e:
        return 127, str(e)
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # nothing the check started outlives it
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
    if rc == 124:
        return 124, f"timed out after {timeout}s"
    tail = (out or "").strip().splitlines()[-20:]
    return rc, store.redact("\n".join(tail))


def cmd_done(_args: list[str]) -> int:
    root = project_root()
    doc = load(root)
    if not doc:
        sys.exit("foundry-ticket: no active ticket")
    rc, tail = run_done(doc, root)
    if rc == 0:
        doc = load(root) or doc
        doc["status"] = "done"
        doc["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save(doc, root)
        print(f"ticket #{doc['issue']}: Done means PASSED")
        return 0
    print(f"ticket #{doc['issue']}: Done means FAILED (rc={rc})\n{tail}")
    return 1


def cmd_status(_args: list[str]) -> int:
    doc = load()
    if not doc:
        print("no active ticket")
        return 1
    print(json.dumps(doc, indent=2))
    return 0


def cmd_clear(_args: list[str]) -> int:
    p = ticket_path()
    if p and p.is_file():
        p.unlink()
        print("ticket cleared")
    return 0


# ====================================================================================================
# gn-08 — the owner-aware queue: read / next / claim / reconcile, and the local lead lease.
#
# GitHub is the authority; the local store is an execution cache. Nothing below ever selects work, or
# judges a claim authorized, from a local record. Every `gh` call goes through ONE injectable runner
# `run(args, stdin=None) -> str` (raises GhError); tests pass a stub, so nothing touches the network.
# ====================================================================================================

DEFAULT_WIP_LIMIT = 2
DEFAULT_STALE_AFTER = 600          # seconds; >= 3x the deck cadence
STARTED_STATUSES = ("in progress", "review")
SKIP_REASONS = ("unassigned", "other-owner", "backlog", "blocked", "no-acceptance", "no-repo-key")
STOP_REASONS = ("reassigned", "cancelled", "duplicate", "withdrawn", "acceptance_changed")
_PRIORITY_WORDS = {"critical": 0, "urgent": 0, "blocker": 0, "high": 1, "medium": 2, "normal": 2, "low": 3}
_ACCEPT_RE = re.compile(r"^##+[ \t]*acceptance[ \t]+criteria\b.*?$", re.I | re.M)
_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S", re.M)
_REF_RE = re.compile(r"^(?:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#)?(\d+)$")

_ISSUE_FIELDS = """number title body url state stateReason author{login}
assignees(first:30){totalCount nodes{login}} labels(first:60){totalCount nodes{name}}
blockedBy(first:50){totalCount nodes{number state repository{nameWithOwner}}}
blocking(first:50){totalCount nodes{number state repository{nameWithOwner}}}
parent{number repository{nameWithOwner}}
closedByPullRequestsReferences(first:20){nodes{number state url}}
projectItems(first:20){totalCount nodes{project{number}
status:fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}
priority:fieldValueByName(name:"Priority"){... on ProjectV2ItemFieldSingleSelectValue{name}}}}"""
_LIST_QUERY = ("query($owner:String!,$repo:String!,$after:String){repository(owner:$owner,name:$repo){"
               "issues(first:50,states:OPEN,after:$after){pageInfo{hasNextPage endCursor} nodes{"
               + _ISSUE_FIELDS + "}}}}")
_ONE_QUERY = ("query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){"
              "issue(number:$number){" + _ISSUE_FIELDS + "}}}")
_PRIORITY_QUERY = ("query($owner:String!,$number:Int!){repositoryOwner(login:$owner){... on ProjectV2Owner{"
                   "projectV2(number:$number){field(name:\"Priority\"){... on ProjectV2SingleSelectField{"
                   "options{name}}}}}}}")


class GhError(RuntimeError):
    """A gh/GraphQL call failed, timed out, or returned an incomplete answer."""


class QueueUnavailable(RuntimeError):
    """The queue (or one issue) cannot be read completely; nothing may be selected or authorized."""


def default_run(args: list[str], stdin: str | None = None) -> str:
    try:
        r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise GhError("gh timed out after 120s")
    except OSError as e:
        raise GhError(f"gh could not be run: {e}")
    if r.returncode != 0:
        raise GhError(store.redact(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}"))
    return r.stdout


def _graphql(run, query: str, **variables) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is None:
            continue
        args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    try:
        doc = json.loads(run(args))
    except json.JSONDecodeError as e:
        raise GhError(f"unreadable GraphQL answer: {e}")
    if not isinstance(doc, dict) or doc.get("errors") or not isinstance(doc.get("data"), dict):
        raise GhError("GraphQL error: " + store.redact(json.dumps((doc or {}).get("errors") if isinstance(doc, dict) else doc)[:300]))
    return doc["data"]


def _norm_status(s) -> str:
    return re.sub(r"[\s_-]+", " ", str(s or "")).strip().lower()


def load_config(root: Path) -> dict:
    """`github_scope` + `repos{}` from <root>/.claude/foundry-project.json → {scope, repos, owner, project_number}."""
    p = root / ".claude" / "foundry-project.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise QueueUnavailable(f"project config unreadable ({p}): {e}")
    scope = doc.get("github_scope") or {}
    repos: dict[str, str] = {}
    for key, val in (doc.get("repos") or {}).items():
        slug = val if isinstance(val, str) else ((val or {}).get("repo") or (val or {}).get("slug") or "")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug or ""):
            repos[key] = slug
    if not repos:
        raise QueueUnavailable("no repos{} with OWNER/NAME entries in .claude/foundry-project.json")
    owner = scope.get("owner") or next(iter(repos.values())).split("/")[0]
    pn = scope.get("project_number")
    return {"scope": scope, "repos": repos, "owner": owner,
            "project_number": int(pn) if isinstance(pn, (int, str)) and str(pn).isdigit() else None}


def operator_login(run) -> str:
    try:
        login = (json.loads(run(["api", "user"])) or {}).get("login") or ""
    except (GhError, ValueError) as e:
        raise QueueUnavailable(f"the authenticated login could not be read: {e}")
    if not login:
        raise QueueUnavailable("the authenticated login could not be read (gh api user returned none)")
    return login


def has_acceptance(body: str) -> bool:
    """`## Acceptance criteria` with >=1 list item, or a non-empty `## Done means` section."""
    body = (body or "").replace("\r\n", "\n")
    m = _ACCEPT_RE.search(body)
    if m:
        rest = body[m.end():]
        nxt = _HEADING_RE.search(rest)
        if _ITEM_RE.search(rest[: nxt.start()] if nxt else rest):
            return True
    d = _DONE_RE.search(body)
    if d:
        rest = body[d.end():]
        nxt = _HEADING_RE.search(rest)
        return bool((rest[: nxt.start()] if nxt else rest).strip())
    return False


def _nodes(node: dict, key: str) -> list:
    block = node.get(key) or {}
    items = block.get("nodes") or []
    total = block.get("totalCount")
    if isinstance(total, int) and total > len(items):
        raise GhError(f"incomplete read: {key} has {total} entries, {len(items)} returned")
    return [x for x in items if x]


def normalize(node: dict, repo: str, project_number: int | None) -> dict:
    """GraphQL issue node → the owner-aware record. Every field the ticket reader returned before is kept."""
    body = node.get("body") or ""
    labels = [x["name"] for x in _nodes(node, "labels") if x.get("name")]
    status, source, prio = None, "label", None
    if project_number is not None:
        source = "project"
        for it in _nodes(node, "projectItems"):
            if (it.get("project") or {}).get("number") == project_number:
                status = _norm_status((it.get("status") or {}).get("name")) or None
                prio = (it.get("priority") or {}).get("name")
                break
    else:
        for lb in labels:
            if lb.lower().startswith("status:"):
                status = _norm_status(lb.split(":", 1)[1])
                break
    if prio is None and project_number is None:
        for lb in labels:
            if lb.lower().startswith("priority:"):
                prio = lb.split(":", 1)[1].strip()
                break
    def refs(key):
        return [{"number": b.get("number"), "repo": (b.get("repository") or {}).get("nameWithOwner"),
                 "state": (b.get("state") or "").upper()} for b in _nodes(node, key)]
    par = node.get("parent")
    return {
        "number": node.get("number"), "title": node.get("title") or "", "body": store.redact(body),
        "body_sha256": store.command_digest(body), "url": node.get("url") or "",
        "author": (node.get("author") or {}).get("login") or "", "state": (node.get("state") or "").upper(),
        "state_reason": (node.get("stateReason") or "").upper() or None, "repo": repo,
        "assignees": [a["login"] for a in _nodes(node, "assignees") if a.get("login")],
        "labels": labels, "status": status, "status_source": source, "priority": prio,
        "blocked_by": refs("blockedBy"), "blocking": refs("blocking"),
        "parent": ({"number": par.get("number"), "repo": (par.get("repository") or {}).get("nameWithOwner")}
                   if par else None),
        "linked_prs": [{"number": x.get("number"), "state": (x.get("state") or "").upper(), "url": x.get("url")}
                       for x in _nodes(node, "closedByPullRequestsReferences")],
        "_has_acceptance": has_acceptance(body),
    }


def parse_ref(arg: str, default_repo: str | None, repos: dict | None = None) -> tuple[str, int]:
    m = _ISSUE_URL_RE.match(arg or "")
    if m:
        return m.group(1), int(arg.rstrip("/").rsplit("/", 1)[1])
    m = _REF_RE.match(arg or "")
    if not m:
        raise ValueError(f"{arg!r} is not an issue number, OWNER/NAME#N, or an issue URL")
    repo = m.group(1) or default_repo
    if not repo and repos and len(set(repos.values())) == 1:
        repo = next(iter(repos.values()))
    if not repo:
        raise ValueError("which repository? pass --repo OWNER/NAME (or OWNER/NAME#N)")
    return repo, int(m.group(2))


def read_issue(run, repo: str, number: int, project_number: int | None = None, trust_author: bool = False) -> dict:
    """Read one issue completely. Returns the pre-gn-08 fields (number,title,body,url,author,state) plus
    assignees, labels, status+status_source, blocked_by, blocking, parent, linked_prs — and the parsed
    `done` command, adopted only under the same author-trust rule `start` applies (else "" + a reason)."""
    owner, name = repo.split("/", 1)
    try:
        data = _graphql(run, _ONE_QUERY, owner=owner, repo=name, number=number)
        node = data["repository"]["issue"]
        if not node:
            raise GhError(f"{repo}#{number} not found")
        rec = normalize(node, repo, project_number)
    except (KeyError, TypeError) as e:
        raise GhError(f"unexpected GraphQL shape: {e}")
    raw_body = node.get("body") or ""
    done, paper = parse_body(raw_body)
    rec["done"], rec["done_trusted"], rec["done_reason"] = "", False, ""
    if done:
        def gh_json(a):
            try:
                return json.loads(run(a))
            except (GhError, ValueError):
                return None
        ok, why = (True, "operator passed --trust-author") if trust_author else author_trusted(repo, rec["author"], gh_json)
        rec["done_trusted"], rec["done_reason"] = ok, why
        if ok:
            rec["done"] = store.redact(done)
    rec["done_sha256"] = store.command_digest(done)
    rec["paper_allowed"] = paper
    return rec


def read_queue(run, cfg: dict) -> list[dict]:
    """Every open issue in every in-scope repo, completely or not at all (any failed page → QueueUnavailable)."""
    out = []
    try:
        for repo in sorted(set(cfg["repos"].values())):
            owner, name = repo.split("/", 1)
            after = None
            while True:
                data = _graphql(run, _LIST_QUERY, owner=owner, repo=name, after=after)
                block = data["repository"]["issues"]
                out += [normalize(n, repo, cfg["project_number"]) for n in block["nodes"] if n]
                if not block["pageInfo"]["hasNextPage"]:
                    break
                after = block["pageInfo"]["endCursor"]
    except (GhError, KeyError, TypeError) as e:
        raise QueueUnavailable(str(e))
    return out


def priority_order(run, cfg: dict) -> list[str] | None:
    """Project `Priority` option names, highest first (None when no project is configured)."""
    if cfg["project_number"] is None:
        return None
    try:
        data = _graphql(run, _PRIORITY_QUERY, owner=cfg["owner"], number=cfg["project_number"])
        opts = data["repositoryOwner"]["projectV2"]["field"]["options"]
        return [_norm_status(o["name"]) for o in opts]
    except (GhError, KeyError, TypeError) as e:
        raise QueueUnavailable(f"the Project's Priority field is unreachable: {e}")


def priority_rank(issue: dict, order: list[str] | None) -> int:
    p = _norm_status(issue.get("priority"))
    if not p:
        return 1000
    if order is not None:
        return order.index(p) if p in order else 999
    if p in _PRIORITY_WORDS:
        return _PRIORITY_WORDS[p]
    m = re.fullmatch(r"p?(\d+)", p)
    return int(m.group(1)) if m else 999


def _repo_key(issue: dict, cfg: dict) -> str | None:
    for lb in issue["labels"]:
        if lb.lower().startswith("repo:"):
            key = lb.split(":", 1)[1].strip()
            if key in cfg["repos"]:
                return key
    return None


def _open_blockers(issue: dict) -> list[dict]:
    return [b for b in issue["blocked_by"] if b["state"] == "OPEN"]


def load_claims(root: Path) -> dict:
    p = store.claims_file(root)
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_claim(root: Path, key: str, rec: dict) -> Path:
    p = store.claims_file(root)
    if not p:
        raise GhError("not a git repository — the claim record lives in the git dir")
    claims = load_claims(root)
    claims[key] = rec
    store.atomic_write_json(p, claims)
    return p


def judge(issue: dict, login: str, claim: dict | None, withdrawn_by: tuple | None = None) -> dict:
    """The reconcile verdict from CURRENT GitHub state alone. A local claim supplies only the baseline body
    hash to compare against — never an authorization. `withdrawn_by`: (True, actor) when history shows a
    return to Backlog after start."""
    key = f"{issue['repo']}#{issue['number']}"
    base = {"issue": key, "number": issue["number"], "repo": issue["repo"]}
    if issue["state"] == "CLOSED":
        reason = {"NOT_PLANNED": "cancelled", "DUPLICATE": "duplicate"}.get(issue.get("state_reason") or "", "closed")
        return {**base, "stop_dispatch": True, "reason": reason}
    if login not in issue["assignees"]:
        return {**base, "stop_dispatch": True, "reason": "reassigned", "assignees": issue["assignees"]}
    if issue["status"] == "backlog":
        if withdrawn_by and withdrawn_by[0]:
            return {**base, "stop_dispatch": True, "reason": "withdrawn", "actor": withdrawn_by[1]}
        return {**base, "stop_dispatch": True, "reason": "not_started"}
    if claim and claim.get("body_sha256") and claim["body_sha256"] != issue["body_sha256"]:
        return {**base, "stop_dispatch": True, "reason": "acceptance_changed"}
    return {**base, "stop_dispatch": False, "reason": "authorized"}


class _Adapter(deck.GhAdapter):
    """gn-04's adapter, routed through the ONE injectable runner."""

    def __init__(self, run):
        self._runner = run

    def _run(self, args, stdin=None):
        try:
            return self._runner(args, stdin)
        except GhError as e:
            raise RuntimeError(str(e))


def _ready_at(run, repo: str, number: int, now: float) -> str:
    h = deck.fetch_history(f"{repo}#{number}", _iso(now), _Adapter(run))
    ts = [e.at for e in h.events if e.event == "ready" and not e.invalid]
    return min(ts) if ts else "9999-12-31T00:00:00Z"


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def select_next(run, root: Path, now: float) -> dict:
    cfg = load_config(root)
    login = operator_login(run)
    issues = read_queue(run, cfg)
    order = priority_order(run, cfg)
    claims = load_claims(root)
    skipped, resume, ready = [], [], []

    def skip(i, reason):
        skipped.append({"issue": f"{i['repo']}#{i['number']}", "number": i["number"], "repo": i["repo"], "reason": reason})

    for i in issues:
        if i["state"] != "OPEN":
            continue
        if not i["assignees"]:
            skip(i, "unassigned"); continue
        if login not in i["assignees"]:
            skip(i, "other-owner"); continue
        if i["status"] in STARTED_STATUSES:
            v = judge(i, login, claims.get(f"{i['repo']}#{i['number']}"))
            if v["stop_dispatch"]:
                skip(i, "stop-dispatch:" + v["reason"])
            else:
                resume.append(i)
            continue
        if i["status"] != "ready":
            skip(i, "backlog"); continue
        if _open_blockers(i):
            skip(i, "blocked"); continue
        if not i["_has_acceptance"]:
            skip(i, "no-acceptance"); continue
        if _repo_key(i, cfg) is None:
            skip(i, "no-repo-key"); continue
        ready.append(i)

    def sortkey(i):
        return (priority_rank(i, order), i["number"], i["repo"])

    mode, pick = "none", None
    if resume:
        mode, pick = "resume", sorted(resume, key=sortkey)[0]
    elif ready:
        ranked = sorted(ready, key=sortkey)
        top = [i for i in ranked if priority_rank(i, order) == priority_rank(ranked[0], order)]
        if len(top) > 1:   # oldest first Ready (gn-04 history), then lowest number
            try:
                stamped = sorted(top, key=lambda i: (_ready_at(run, i["repo"], i["number"], now), i["number"], i["repo"]))
            except (RuntimeError, ValueError, KeyError) as e:
                raise QueueUnavailable(f"tie-break history unreadable: {e}")
            ranked = stamped
        mode, pick = "ready", ranked[0]
    for i in issues:
        i.pop("_has_acceptance", None)
    sel = None
    if pick is not None:
        pick = dict(pick)
        pick["repo_key"] = _repo_key(pick, cfg)
        pick.pop("_has_acceptance", None)
        sel = pick
    return {"queue": "available", "operator": login, "mode": mode, "selected": sel, "skipped": skipped,
            "wip": wip_count(issues, login, claims)}


def wip_count(issues: list[dict], login: str, claims: dict, exclude: str | None = None) -> int:
    n = 0
    for i in issues:
        key = f"{i['repo']}#{i['number']}"
        if i["state"] != "OPEN" or login not in i["assignees"] or key == exclude:
            continue
        if i["status"] in STARTED_STATUSES or i["status"] == "blocked" or (_open_blockers(i) and key in claims):
            n += 1
    return n


def cmd_next(args: list[str], run, now: float) -> int:
    ap = argparse.ArgumentParser(prog="foundry-ticket.py next",
                                 description="Select the operator's next work item from GitHub (never from a local record).")
    ap.add_argument("--json", action="store_true", help="print the full result, including every skipped candidate and its reason")
    a = ap.parse_args(args)
    root = project_root()
    try:
        res = select_next(run, root, now)
    except QueueUnavailable as e:
        out = {"queue": "unavailable", "cause": store.redact(str(e)), "selected": None}
        print(json.dumps(out, indent=2) if a.json else f"queue: unavailable — {out['cause']}")
        return 3
    if a.json:
        print(json.dumps(res, indent=2))
    elif res["selected"]:
        s = res["selected"]
        print(f"next ({res['mode']}): {s['repo']}#{s['number']} — {s['title']}  [status: {s['status']}, repo key: {s['repo_key']}]")
    else:
        print("next: nothing eligible (assigned to you, Ready, unblocked, with acceptance criteria and a repo key)")
        for sk in res["skipped"]:
            print(f"  skipped {sk['issue']}: {sk['reason']}")
    return 0


def cmd_claim(args: list[str], run, now: float) -> int:
    ap = argparse.ArgumentParser(prog="foundry-ticket.py claim",
                                 description="Record `start` for an issue assigned to you (never self-assigns).")
    ap.add_argument("issue")
    ap.add_argument("--repo")
    ap.add_argument("--limit", type=int, default=DEFAULT_WIP_LIMIT)
    ap.add_argument("--allow-exception", metavar="REASON", default=None)
    a = ap.parse_args(args)
    root = project_root()
    try:
        cfg = load_config(root)
        repo, number = parse_ref(a.issue, a.repo, cfg["repos"])
        login = operator_login(run)
        issues = read_queue(run, cfg)
    except (QueueUnavailable, ValueError) as e:
        print(f"claim refused: {store.redact(str(e))}", file=sys.stderr)
        return 2
    target = next((i for i in issues if i["repo"] == repo and i["number"] == number), None)
    if target is None:
        print(f"claim refused: {repo}#{number} is not an open issue in scope", file=sys.stderr)
        return 2
    if not target["assignees"]:
        print(f"claim refused: {repo}#{number} is unassigned (claim never self-assigns)", file=sys.stderr)
        return 2
    if login not in target["assignees"]:
        print(f"claim refused: {repo}#{number} is assigned to {', '.join(target['assignees'])}, not {login}", file=sys.stderr)
        return 2
    key = f"{repo}#{number}"
    claims = load_claims(root)
    wip = wip_count(issues, login, claims, exclude=key)
    reason = (a.allow_exception or "").strip() or None
    if wip >= a.limit and not reason:
        print(f"claim refused: {login} already has {wip} started, unfinished issue(s) (limit {a.limit}); "
              f"pass --allow-exception \"<reason>\" to record a visible exception", file=sys.stderr)
        return 2
    payload = {"by": login, "target": key,
               "note": f"start recorded by {login} for {key}" + (f" (WIP exception: {reason})" if wip >= a.limit and reason else "")}
    if wip >= a.limit and reason:
        payload["exception"] = reason
    try:
        ev = deck.emit(key, "start", payload, adapter=_Adapter(run))
    except (ValueError, RuntimeError) as e:
        print(f"claim refused: recording `start` failed: {store.redact(str(e))}", file=sys.stderr)
        return 2
    rec = {"issue": key, "number": number, "repo": repo, "operator": login, "body_sha256": target["body_sha256"],
           "claimed_at": _iso(now), "start_idempotency_key": ev.idempotency_key,
           "exception": reason if wip >= a.limit and reason else None}
    path = save_claim(root, key, rec)
    print(f"claimed {key} for {login} (wip {wip + 1}/{a.limit}{', EXCEPTION: ' + reason if rec['exception'] else ''}); recorded {path}")
    return 0


def reconcile_one(run, cfg: dict, login: str, repo: str, number: int, claim: dict | None, now: float) -> dict:
    key = f"{repo}#{number}"
    try:
        issue = read_issue(run, repo, number, cfg["project_number"])
        wd = None
        if issue["state"] == "OPEN" and issue["status"] == "backlog":
            h = deck.fetch_history(key, _iso(now), _Adapter(run))
            w = [e for e in h.events if e.event == "withdrawn"]
            wd = (True, w[-1].by) if w else (False, None)
        return judge(issue, login, claim, wd)
    except (GhError, RuntimeError, ValueError, KeyError) as e:
        return {"issue": key, "number": number, "repo": repo, "stop_dispatch": True, "reason": "unverified",
                "cause": store.redact(str(e))}


def cmd_reconcile(args: list[str], run, now: float) -> int:
    ap = argparse.ArgumentParser(prog="foundry-ticket.py reconcile",
                                 description="Report stop_dispatch per started issue, judged from current GitHub state only.")
    ap.add_argument("issue", nargs="?")
    ap.add_argument("--repo")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(args)
    root = project_root()
    claims = load_claims(root)
    results, cause = [], None
    cfg = login = None
    try:
        cfg = load_config(root)
        login = operator_login(run)
    except QueueUnavailable as e:
        cause = str(e)
    targets: list[tuple[str, int]] = []
    try:
        if a.issue:
            targets = [parse_ref(a.issue, a.repo, cfg["repos"] if cfg else None)]
        else:
            targets = [(c["repo"], c["number"]) for c in claims.values() if c.get("repo") and c.get("number")]
            if cfg and login:
                try:
                    for i in read_queue(run, cfg):
                        if i["state"] == "OPEN" and login in i["assignees"] and i["status"] in STARTED_STATUSES:
                            t = (i["repo"], i["number"])
                            if t not in targets:
                                targets.append(t)
                except QueueUnavailable as e:
                    cause = str(e)
    except ValueError as e:
        print(f"reconcile: {e}", file=sys.stderr)
        return 2
    for repo, number in targets:
        claim = claims.get(f"{repo}#{number}")
        if cfg is None or login is None:
            results.append({"issue": f"{repo}#{number}", "number": number, "repo": repo, "stop_dispatch": True,
                            "reason": "unverified", "cause": store.redact(cause or "")})
        else:
            results.append(reconcile_one(run, cfg, login, repo, number, claim, now))
    out = {"results": results, "complete": cause is None}
    if cause:
        out["cause"] = store.redact(cause)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        for r in results:
            print(f"{r['issue']}: stop_dispatch={str(r['stop_dispatch']).lower()} reason={r['reason']}"
                  + (f" actor={r['actor']}" if r.get("actor") else ""))
        if cause:
            print(f"reconcile: the queue could not be enumerated completely — {store.redact(cause)}")
        if not results and not cause:
            print("reconcile: no started issues")
    return 3 if (cause or any(r["reason"] == "unverified" for r in results)) else 0


# ---- the local lead lease -----------------------------------------------------------------------------

LOCAL_ONLY = ("this check is local to this machine: it cannot see a lead running elsewhere. Inspect the "
              "cross-machine signals instead — the issue's assignee, any linked draft PR, and the issue's `start` record.")


def _parse_duration(s: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", str(s).strip())
    if not m:
        raise ValueError(f"bad duration {s!r} (use e.g. 600, 90s, 10m, 1h)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def _read_lease(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _epoch(iso) -> float:
    try:
        return calendar.timegm(time.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0.0


def cmd_lead(args: list[str], now: float) -> int:
    ap = argparse.ArgumentParser(prog="foundry-ticket.py lead",
                                 description="The local duplicate-lead lease (.foundry/runtime/lead.json).")
    ap.add_argument("action", choices=("acquire", "renew", "release", "check"))
    ap.add_argument("--stale-after", default=str(DEFAULT_STALE_AFTER))
    ap.add_argument("--op-id")
    ap.add_argument("--session-id")
    a = ap.parse_args(args)
    try:
        stale = _parse_duration(a.stale_after)
    except ValueError as e:
        print(f"lead: {e}", file=sys.stderr)
        return 2
    root = project_root()
    path = store.lease_file(root)
    host = socket.gethostname()
    sid = a.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or f"{host}:{os.getppid()}"
    cur = _read_lease(path) if path.exists() else None
    age = (now - _epoch(cur.get("heartbeat_at"))) if cur else None
    live = cur is not None and age is not None and age < stale
    mine = cur is not None and cur.get("session_id") == sid and cur.get("hostname") == host
    ts = _iso(now)

    def held_msg():
        return (f"lead lease held by session {cur.get('session_id')} (op {cur.get('op_id')}, host {cur.get('hostname')}, "
                f"pid {cur.get('pid')}), heartbeat {int(age)}s ago (stale after {int(stale)}s); {LOCAL_ONLY}")

    if a.action == "check":
        state = "none" if cur is None else ("mine" if mine and live else ("held" if live else "stale"))
        print(json.dumps({"lease": state, "path": str(path), "record": cur, "age_seconds": age,
                          "stale_after": stale, "note": LOCAL_ONLY}, indent=2))
        return 1 if state == "held" else 0
    if a.action == "acquire":
        if live and not mine:
            print("lead acquire refused: " + held_msg(), file=sys.stderr)
            return 1
        rec = {"op_id": a.op_id or os.environ.get("FOUNDRY_OP_ID") or getpass.getuser(), "hostname": host,
               "pid": os.getppid(), "session_id": sid,
               "acquired_at": cur["acquired_at"] if (mine and live and cur.get("acquired_at")) else ts, "heartbeat_at": ts}
        store.atomic_write_json(path, rec)
        back = _read_lease(path)
        if not back or back.get("session_id") != sid:
            print("lead acquire refused: lost a race to another session; " + LOCAL_ONLY, file=sys.stderr)
            return 1
        print(f"lead lease acquired by {sid}" + (" (took over a stale lease)" if cur and not live and not mine else ""))
        return 0
    if cur is None or not mine:
        print(f"lead {a.action}: no lease owned by this session ({'none exists' if cur is None else 'held by ' + str(cur.get('session_id'))})",
              file=sys.stderr)
        return 1
    if a.action == "renew":
        cur["heartbeat_at"] = ts
        store.atomic_write_json(path, cur)
        print("lead lease renewed")
        return 0
    path.unlink()
    print("lead lease released")
    return 0


def main(argv: list[str], run=None, now: float | None = None) -> int:
    """`run` (the one gh runner) and `now` (epoch seconds) are injectable for tests."""
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    verb, rest = argv[0], argv[1:]
    gh = run or default_run
    clock = time.time() if now is None else now
    table = {"start": cmd_start, "allow": cmd_allow, "done": cmd_done,
             "status": cmd_status, "clear": cmd_clear}
    owned = {"next": lambda r: cmd_next(r, gh, clock), "claim": lambda r: cmd_claim(r, gh, clock),
             "reconcile": lambda r: cmd_reconcile(r, gh, clock), "lead": lambda r: cmd_lead(r, clock)}
    if verb in owned:
        return owned[verb](rest)
    if verb not in table:
        sys.exit(f"foundry-ticket: unknown verb {verb!r}; one of {', '.join([*table, *owned])}")
    return table[verb](rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
