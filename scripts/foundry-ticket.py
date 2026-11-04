#!/usr/bin/env python3
"""foundry-ticket — the unit of work is a GitHub issue with a runnable "Done means".

    foundry-ticket.py start <issue|url> [--repo OWNER/NAME] [--trust-author]
    foundry-ticket.py allow <path> [<path>...]        permit writing a paper path for this ticket
    foundry-ticket.py done                            run the ticket's Done-means command
    foundry-ticket.py status                          print the active ticket
    foundry-ticket.py clear                           forget the active ticket
    foundry-ticket.py next [--json]                   the operator's next item, read from GitHub (gn-08)
    foundry-ticket.py read-issue <issue> [--repo R] [--json]   the trust-checked view of one issue (read-only)
    foundry-ticket.py claim <issue> [--repo R] [--allow-exception "<reason>"] [--trust-author] [--successor-of <id>]   record `start` (never self-assigns)
    foundry-ticket.py reconcile [<issue>] [--json]    stop_dispatch per started issue, from current GitHub state (also a claim heartbeat)
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
import uuid
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


def done_trust(repo: str | None, author: str, editor: str | None, gh_json=_gh_json) -> tuple[bool, str]:
    """Trust for a Done-means command: the issue AUTHOR must be trusted and, when a different login last
    edited the body, that editor too (a triage-level editor can rewrite a trusted author's command)."""
    ok, why = author_trusted(repo, author, gh_json)
    if ok and editor and editor != author:
        ok2, why2 = author_trusted(repo, editor, gh_json)
        if not ok2:
            return False, f"the issue body was last edited by {editor}: {why2}"
    return ok, why


RECOVERY = "`foundry-ticket.py claim <n> --rebase-acceptance` (a plain re-claim keeps the old vetted digest)"


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
    editor = None
    if done:
        # the same reader the claim path uses (never a second GraphQL shape): who last edited the body
        try:
            live = read_issue(default_run, issue_repo or "", int(issue["number"]), trust_author=True)
        except (GhError, ValueError):
            live = None
        if not live or live.get("done_sha256") != store.command_digest(done):
            sys.exit("foundry-ticket: could not read the issue's last editor consistently (or the body changed "
                     "while reading); nothing was recorded. Retry `start`.")
        editor = live.get("last_edited_by")
        issue_repo = live.get("name_with_owner") or issue_repo
        try:
            vetted = store.claim_done_digest(project_root(), issue_repo or "", issue["number"])
        except store.ClaimsUnreadable as e:
            sys.exit(f"foundry-ticket: claims-unreadable — the local claim record cannot be read ({e}); the Done-means "
                     "command will not run. Repair or remove the claims file in the git common dir. Nothing was recorded.")
        if vetted and vetted != store.command_digest(done):
            sys.exit("foundry-ticket: done-means-changed — the Done-means command differs from the one vetted at "
                     "`claim` time; it will not run. Re-read it and run " + RECOVERY +
                     " (--trust-author does not override this). Nothing was recorded.")
        if vetted is None:
            try:
                me = operator_login(default_run)
            except (GhError, QueueUnavailable, ValueError):
                me = None   # advisory only: a failed login lookup prints nothing and never aborts
            if me and me.lower() in {str(x).lower() for x in (live.get("assignees") or [])}:
                print(f"foundry-ticket: warning — {issue_repo}#{issue['number']} is assigned to you but has no local claim "
                      "record; its Done-means command was not vetted at claim time.", file=sys.stderr)
    if done and not trust:
        ok, why = done_trust(issue_repo, login, editor)
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
    try:
        vetted = store.claim_done_digest(root, doc.get("repo") or "", doc.get("issue"))
    except store.ClaimsUnreadable as e:
        return 125, f"claims-unreadable: the local claim record cannot be read ({e}); not run"
    if vetted and vetted != store.command_digest(cmd):
        return 125, "done-means-changed: the command differs from the one vetted at `claim` time; run " + RECOVERY
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
SKIP_REASONS = ("unassigned", "other-owner", "backlog", "blocked", "no-acceptance", "no-repo-key",
                "done-means-untrusted")
# Every `reason` a stop_dispatch verdict can carry (reconcile output + the `stop-dispatch:<reason>` skip in `next`).
STOP_REASONS = ("reassigned", "cancelled", "duplicate", "withdrawn", "acceptance_changed",
                "closed", "not_started", "unverified")
_PRIORITY_WORDS = {"critical": 0, "urgent": 0, "blocker": 0, "high": 1, "medium": 2, "normal": 2, "low": 3}
_ACCEPT_RE = re.compile(r"^##+[ \t]*acceptance[ \t]+criteria\b.*?$", re.I | re.M)
_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S", re.M)
_REF_RE = re.compile(r"^(?:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#)?(\d+)$")

# GraphQL fields used below that are NOT in the vendor record of the issue API (docs.github.com/en/graphql/reference/objects):
#   Issue.closedByPullRequestsReferences, ProjectV2Item.fieldValueByName, ProjectV2SingleSelectField.options
# [U] unverified — confirm in the pilot: no offline copy of GitHub's GraphQL schema is available in this
# environment, so these names were NOT schema-verified at implementation (2026-11-03). A wrong name fails
# the whole query -> GhError -> QueueUnavailable (fail-closed), so a mistake here is loud, never silent.
#
# OVER-LIMIT BEHAVIOUR (fail-closed): every connection below is fetched with a fixed `first:N` and carries
# `totalCount`; `_nodes` raises GhError when totalCount > the nodes returned, so an issue with more than
# 30 assignees / 60 labels / 50 blockers / 20 linked PRs / 20 project items is UNREADABLE (-> QueueUnavailable,
# `unverified`), never silently truncated. closedByPullRequestsReferences is covered the same way via its totalCount.
_ISSUE_FIELDS = """number title repository{nameWithOwner} body url state stateReason author{login} editor{login} lastEditedAt
assignees(first:30){totalCount nodes{login}} labels(first:60){totalCount nodes{name}}
blockedBy(first:50){totalCount nodes{number state repository{nameWithOwner}}}
blocking(first:50){totalCount nodes{number state repository{nameWithOwner}}}
parent{number repository{nameWithOwner}}
closedByPullRequestsReferences(first:20){totalCount nodes{number state url}}
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


_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REMOTE_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/\s]+@)?[^/:\s]+(?::\d+)?[:/]([^/:\s]+)/([^/\s]+?)(?:\.git)?/?$")


def default_git(args: list[str]) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise OSError(r.stderr.strip()[:200])
    return r.stdout


def parse_remote(url: str) -> str | None:
    """`OWNER/NAME` from an origin URL: https://host/o/n(.git), ssh://git@host/o/n.git, git@alias:o/n.git
    (the host may be an ssh alias such as `personal-github` — it is not required to be github.com)."""
    m = _REMOTE_RE.match((url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def origin_slug(git, checkout: Path) -> str | None:
    try:
        return parse_remote(git(["-C", str(checkout), "remote", "get-url", "origin"]))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def load_config(root: Path, git=None) -> dict:
    """The queue's scope from <root>/.claude/foundry-project.json → {scope, repos, owner, project_number, dropped}.

    The manifest's `repos{key: {path, kind, ...}}` carries NO slug: each repo's `OWNER/NAME` is derived from the
    `origin` of its checkout (`git -C <root>/<path> remote get-url origin`, via the injectable `git` runner
    `git(args) -> str`). A checkout with no readable origin is dropped and reported in `dropped`. A literal
    `OWNER/NAME` string (or a `repo`/`slug` field) is still accepted. `owner` = `github_scope.owner`, else the
    planning repo's owner (`github_scope.planning_repo`, else the workspace's own origin). `project_number`
    absent -> None -> label mode."""
    git = git or default_git
    p = root / ".claude" / "foundry-project.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise QueueUnavailable(f"project config unreadable ({p}): {e}")
    if not isinstance(doc, dict):
        raise QueueUnavailable(f"project config unreadable ({p}): not an object")
    scope = doc.get("github_scope") or {}
    repos: dict[str, str] = {}
    dropped: list[dict] = []
    for key, val in (doc.get("repos") or {}).items():
        explicit = val if isinstance(val, str) else ((val or {}).get("repo") or (val or {}).get("slug") or "")
        if _SLUG_RE.fullmatch(explicit or ""):
            repos[key] = explicit
            continue
        rel = (val or {}).get("path") if isinstance(val, dict) else None
        slug = origin_slug(git, root / rel) if rel else None
        if slug:
            repos[key] = slug
        else:
            dropped.append({"key": key, "path": rel, "reason": "no GitHub origin" if rel else "no path"})
    if not repos:
        raise QueueUnavailable("no repos{} entry in .claude/foundry-project.json resolves to a GitHub origin"
                               + (f" (dropped: {', '.join(d['key'] for d in dropped)})" if dropped else ""))
    planning = scope.get("planning_repo") or origin_slug(git, root) or ""
    owner = scope.get("owner") or (planning.split("/")[0] if "/" in planning else "") \
        or next(iter(repos.values())).split("/")[0]
    pn = scope.get("project_number")
    return {"scope": scope, "repos": repos, "owner": owner, "dropped": dropped, "planning_repo": planning or None,
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
    nwo = (node.get("repository") or {}).get("nameWithOwner")
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
        "author": (node.get("author") or {}).get("login") or "",
        "last_edited_by": (node.get("editor") or {}).get("login") or None,
        "last_edited_at": node.get("lastEditedAt") or None, "state": (node.get("state") or "").upper(),
        "state_reason": (node.get("stateReason") or "").upper() or None, "repo": repo,
        "name_with_owner": store.norm_repo(nwo) if nwo else None, "name_with_owner_raw": nwo or None,
        "assignees": [a["login"] for a in _nodes(node, "assignees") if a.get("login")],
        "labels": labels, "status": status, "status_source": source, "priority": prio,
        "blocked_by": refs("blockedBy"), "blocking": refs("blocking"),
        "parent": ({"number": par.get("number"), "repo": (par.get("repository") or {}).get("nameWithOwner")}
                   if par else None),
        "linked_prs": [{"number": x.get("number"), "state": (x.get("state") or "").upper(), "url": x.get("url")}
                       for x in _nodes(node, "closedByPullRequestsReferences")],
        "_has_acceptance": has_acceptance(body),
        # digest of the raw Done-means command (same value `read_issue` reports as done_digest), "" digest when none
        "done_digest": store.command_digest(parse_body(body)[0]), "_has_done": bool(parse_body(body)[0]),
    }


def parse_ref(arg: str, default_repo: str | None, repos: dict | None = None) -> tuple[str, int]:
    m = _ISSUE_URL_RE.match(arg or "")
    if m:
        if default_repo and default_repo != m.group(1):
            raise ValueError(f"--repo {default_repo} contradicts the issue URL's repository {m.group(1)}")
        return m.group(1), int(arg.rstrip("/").rsplit("/", 1)[1])
    m = _REF_RE.match(arg or "")
    if not m:
        raise ValueError(f"{arg!r} is not an issue number, OWNER/NAME#N, or an issue URL")
    if m.group(1) and default_repo and default_repo != m.group(1):
        raise ValueError(f"--repo {default_repo} contradicts {arg!r}")
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
        ok, why = (True, "operator passed --trust-author") if trust_author else done_trust(
            repo, rec["author"], rec.get("last_edited_by"), gh_json)
        rec["done_trusted"], rec["done_reason"] = ok, why
        if ok:
            rec["done"] = store.redact(done)
    rec["done_sha256"] = store.command_digest(done)
    rec["done_digest"] = rec["done_sha256"]
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
    """Claim records keyed by the lower-cased `owner/name#N` (common git dir; legacy per-worktree file read
    when no common one exists). An unreadable file yields {} here — the execution paths use
    `store.claim_done_digest`, which refuses instead."""
    try:
        return store.read_claims(root)[0]
    except store.ClaimsUnreadable:
        return {}


def save_claim(root: Path, key: str, rec: dict) -> Path:
    r, sep, n = key.rpartition("#")
    nk = store.claim_key(r, n) if sep else key
    try:
        return store.update_claims(root, lambda claims: claims.__setitem__(nk, rec))
    except store.ClaimsUnreadable as e:
        if "not a git repository" in str(e):
            raise GhError(str(e))
        raise


def judge(issue: dict, login: str, claim: dict | None, withdrawn_by: tuple | None = None) -> dict:
    """The reconcile verdict from CURRENT GitHub state alone. `authorized` ONLY for an open, assigned-to-operator
    issue in a started status (In progress / Review); anything else stops dispatch: `closed` (closed, or Project
    status Done), `cancelled`/`duplicate`, `reassigned`, `withdrawn`, `not_started` (Backlog, Ready, blocked,
    no status, unknown status), `acceptance_changed`. A local claim supplies only the baseline body
    hash to compare against — never an authorization. `withdrawn_by`: (True, actor) when history shows a
    return to Backlog after start."""
    key = store.claim_key(issue['repo'], issue['number'])
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
    if issue["status"] == "done":
        return {**base, "stop_dispatch": True, "reason": "closed"}
    if issue["status"] not in STARTED_STATUSES:       # Ready / blocked / none / unknown: nothing was started
        return {**base, "stop_dispatch": True, "reason": "not_started"}
    if claim and claim.get("body_sha256") and claim["body_sha256"] != issue["body_sha256"]:
        return {**base, "stop_dispatch": True, "reason": "acceptance_changed"}
    if claim and claim.get("done_digest") and claim["done_digest"] != issue.get("done_digest"):
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
    trust_cache: dict = {}

    def untrusted_done(i) -> bool:
        """True when the issue declares a Done-means command whose author/editor is not trusted (never auto-run)."""
        if not i.get("_has_done"):
            return False
        k = (i["repo"], i["author"], i.get("last_edited_by"))
        if k not in trust_cache:
            def gh_json(a):
                try:
                    return json.loads(run(a))
                except (GhError, ValueError):
                    return None
            trust_cache[k] = done_trust(i["repo"], i["author"], i.get("last_edited_by"), gh_json)[0]
        return not trust_cache[k]

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
            v = judge(i, login, claims.get(store.claim_key(i['repo'], i['number'])))
            if v["stop_dispatch"]:
                skip(i, "stop-dispatch:" + v["reason"])
            elif untrusted_done(i):
                skip(i, "done-means-untrusted")
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
        if untrusted_done(i):
            skip(i, "done-means-untrusted"); continue
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
        i.pop("_has_done", None)
    sel = None
    if pick is not None:
        pick = dict(pick)
        pick["repo_key"] = _repo_key(pick, cfg)
        pick.pop("_has_acceptance", None)
        pick.pop("_has_done", None)
        sel = pick
    return {"queue": "available", "operator": login, "mode": mode, "selected": sel, "skipped": skipped,
            "wip": wip_count(issues, login, claims), "dropped_repos": cfg.get("dropped", [])}


def wip_count(issues: list[dict], login: str, claims: dict, exclude: str | None = None) -> int:
    """Started, unfinished issues assigned to `login`. `blocked` is a PSEUDO-status: the normalized value of a
    `status:blocked` label / a Project `Blocked` option — an in-flight issue parked on something, which still
    occupies WIP; so does a Ready issue with an open blocker that this machine has claimed before."""
    n = 0
    for i in issues:
        key = store.claim_key(i['repo'], i['number'])
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
        for d in res.get("dropped_repos", []):
            print(f"  dropped repo {d['key']}: {d['reason']}")
        for sk in res["skipped"]:
            print(f"  skipped {sk['issue']}: {sk['reason']}")
    return 0


def cmd_claim(args: list[str], run, now: float) -> int:
    ap = argparse.ArgumentParser(prog="foundry-ticket.py claim",
                                 description="Record `start` for an issue assigned to you (never self-assigns).")
    ap.add_argument("issue")
    ap.add_argument("--repo")
    ap.add_argument("--limit", type=int, default=DEFAULT_WIP_LIMIT)
    ap.add_argument("--allow-exception", metavar="REASON", default=None,
                    help="a visible exception for the WIP limit, a Backlog issue or a blocked issue")
    ap.add_argument("--rebase-acceptance", action="store_true",
                    help="on a re-claim, adopt the issue's CURRENT body as the acceptance baseline")
    ap.add_argument("--trust-author", action="store_true",
                    help="the OPERATOR has read the issue's Done-means and vouches for it (never auto-run)")
    ap.add_argument("--successor-of", metavar="ID", default=None,
                    help="continue a long chunk: this claim succeeds ID (a claim THIS session holds); not a takeover")
    ap.add_argument("--session-id", default=None, help="the lease owner (default: CLAUDE_CODE_SESSION_ID, else a per-user id)")
    a = ap.parse_args(args)
    root = project_root()
    try:
        cfg = load_config(root)
        repo, number = parse_ref(a.issue, a.repo, cfg["repos"])
        pred_key = None
        if a.successor_of:
            prepo, pnum = parse_ref(a.successor_of, a.repo or repo, cfg["repos"])
            pred_key = store.claim_key(prepo, pnum)
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
    key = store.claim_key(repo, number)
    try:
        vetted = read_issue(run, repo, number, cfg["project_number"], trust_author=a.trust_author)
    except (GhError, ValueError) as e:
        print(f"claim refused: the issue could not be read completely: {store.redact(str(e))}", file=sys.stderr)
        return 2
    key = store.claim_key(vetted.get("name_with_owner") or repo, number)   # the canonical GraphQL name wins
    has_done = bool(vetted["_has_done"])
    if has_done and not vetted["done_trusted"]:
        print(f"done-means-untrusted: claim refused: the Done-means command in {key} is not author-trusted — "
              f"{store.redact(vetted['done_reason'])}. The worker would run it, so the OPERATOR must read it and re-run "
              f"`claim --trust-author`. Nothing was recorded.", file=sys.stderr)
        return 2
    try:
        claims = store.read_claims(root)[0]
    except store.ClaimsUnreadable as e:
        print(f"claims-unreadable: claim refused: the local claim record cannot be read ({store.redact(str(e))}); repair or "
              "remove the claims file in the git common dir (it holds every vetted Done-means digest). Nothing was recorded.",
              file=sys.stderr)
        return 2
    # B5 lease: a claim another session holds LIVE is not ours to take; only an EXPIRED lease can be taken over.
    sid = _session_id(root, a.session_id, socket.gethostname())
    _enabled, expires_s = store.claims_policy(root)
    prior_lease = claims.get(key) or {}
    taken_over = None
    if prior_lease.get("session_id") and prior_lease["session_id"] != sid:
        st = store.lease_state(prior_lease, now, expires_s)
        if st["state"] == "live":
            print(f"claim refused: lease-held: {key} is held by session {prior_lease['session_id']} (operator "
                  f"{prior_lease.get('operator')}), heartbeat {int(st['age'])}s ago; the lease lasts {st['expires_after']}s and "
                  "only an expired lease can be taken over", file=sys.stderr)
            return 2
        taken_over = {"session_id": prior_lease["session_id"], "operator": prior_lease.get("operator"),
                      "last_heartbeat_at": st["heartbeat_at"], "at": _iso(now)}
    if pred_key is not None:
        pred = claims.get(pred_key)
        if pred_key == key:
            print(f"claim refused: successor-refused: {key} cannot succeed itself (a plain `claim` re-claims it)", file=sys.stderr)
            return 2
        if not isinstance(pred, dict) or pred.get("session_id") != sid:
            print(f"claim refused: successor-refused: {pred_key} is not a claim this session holds; a successor continues YOUR "
                  "claim - to take over another session's, wait for its lease to expire and `claim` it plainly", file=sys.stderr)
            return 2
    wip = wip_count(issues, login, claims, exclude=key)
    reason = store.redact((a.allow_exception or "").strip()) or None
    exceptions = []
    if wip >= a.limit:
        if not reason:
            print(f"claim refused: {login} already has {wip} started, unfinished issue(s) (limit {a.limit}); "
                  f"pass --allow-exception \"<reason>\" to record a visible exception", file=sys.stderr)
            return 2
        exceptions.append("wip-limit")
    st = target["status"]
    if st not in STARTED_STATUSES and st != "ready":
        why = "blocked (status: blocked)" if st == "blocked" else f"not Ready (status: {st or 'none'})"
        if not reason:
            print(f"claim refused: {key} is {why}; pass --allow-exception \"<reason>\" to record a visible exception",
                  file=sys.stderr)
            return 2
        exceptions.append("backlog" if st in (None, "backlog") else st.replace(" ", "-"))
    if _open_blockers(target):
        if not reason:
            nums = ", ".join(f"#{b['number']}" for b in _open_blockers(target))
            print(f"claim refused: {key} is blocked by open issue(s) {nums}; pass --allow-exception \"<reason>\" "
                  f"to record a visible exception", file=sys.stderr)
            return 2
        exceptions.append("blocked")
    exc_text = f"{'+'.join(dict.fromkeys(exceptions))}: {reason}" if exceptions else None
    # The WIP-exception record is the human line of the `start` comment + the local claim record: gn-04's
    # marker fields are closed (and `evidence` must be shared references), so the reason rides in `note`.
    ref = f"{vetted.get('name_with_owner_raw') or repo}#{number}"   # canonical case for GitHub; `key` is only the local map key
    payload = {"by": login, "target": ref,
               "note": store.redact(f"start recorded by {login} for {ref}" + (f" (exception, {exc_text})" if exc_text else ""))}
    try:
        ev = deck.emit(ref, "start", payload, adapter=_Adapter(run))
    except (ValueError, RuntimeError) as e:
        print(f"claim refused: recording `start` failed: {store.redact(str(e))}", file=sys.stderr)
        return 2
    prior = claims.get(key) or {}
    keep = bool(prior.get("body_sha256")) and not a.rebase_acceptance
    keep_done = keep and "done_digest" in prior
    rec = {"issue": key, "number": number, "repo": repo, "operator": login,
           "body_sha256": prior["body_sha256"] if keep else vetted["body_sha256"],
           # the vetted Done-means: reconcile stops dispatch when the live digest differs from this one
           "done_digest": prior["done_digest"] if keep_done else vetted["done_digest"],
           "done_trusted": prior.get("done_trusted", False) if keep_done else (not has_done or vetted["done_trusted"]),
           "done_means": prior.get("done_means", "") if keep_done else vetted["done"],
           "done_trusted_by": "operator-flag" if (a.trust_author and has_done) else ("author" if has_done else None),
           "claimed_at": prior.get("claimed_at") or _iso(now) if keep else _iso(now),
           "start_idempotency_key": ev.idempotency_key, "exception": exc_text,
           "session_id": sid, "heartbeat_at": _iso(now), "expires_after_s": expires_s}
    if taken_over:
        rec["taken_over_from"] = taken_over
        rec["claimed_at"] = _iso(now)          # a new holder: the lease starts now
    if pred_key is not None:
        rec["successor_of"] = pred_key
    path = save_claim(root, key, rec)
    if pred_key is not None:
        def _supersede(cl, _p=pred_key, _k=key):
            if isinstance(cl.get(_p), dict):
                cl[_p]["superseded_by"] = _k
                cl[_p]["heartbeat_at"] = _iso(now)
        try:
            store.update_claims(root, _supersede)
        except store.ClaimsUnreadable as e:
            print(f"claim: warning - could not mark {pred_key} superseded ({store.redact(str(e))})", file=sys.stderr)
    note = " [acceptance baseline kept: pass --rebase-acceptance to adopt the current body]" \
        if keep and prior["body_sha256"] != vetted["body_sha256"] else ""
    lease = (f"; took over from session {taken_over['session_id']} (lease expired)" if taken_over else "") + \
            (f"; successor of {pred_key}" if pred_key else "")
    print(f"claimed {key} for {login} (wip {wip + 1}/{a.limit}{', EXCEPTION: ' + exc_text if exc_text else ''}); recorded {path}{note}{lease}")
    return 0


def cmd_read_issue(args: list[str], run) -> int:
    ap = argparse.ArgumentParser(prog="foundry-ticket.py read-issue",
                                 description="The trust-checked, read-only view of one issue: body, done_trusted, "
                                             "done_digest, last_edited_by. Never executes anything.")
    ap.add_argument("issue")
    ap.add_argument("--repo")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(args)
    root = project_root()
    try:
        cfg = load_config(root)
        repo, number = parse_ref(a.issue, a.repo, cfg["repos"])
        rec = read_issue(run, repo, number, cfg["project_number"])
    except (QueueUnavailable, ValueError, GhError) as e:
        print(f"read-issue: {store.redact(str(e))}", file=sys.stderr)
        return 2
    rec = {k: v for k, v in rec.items() if not k.startswith("_")}
    if a.json:
        print(json.dumps(rec, indent=2))
    else:
        print(f"{repo}#{number} — {rec['title']}  [{rec['state']}, status: {rec['status'] or 'none'}]")
        print(f"  author            : {rec['author']}  (last edited by: {rec['last_edited_by'] or 'n/a'})")
        print(f"  done_trusted      : {str(rec['done_trusted']).lower()}"
              + (f" — {rec['done_reason']}" if rec["done_reason"] else ""))
        print(f"  done_digest       : {rec['done_digest']}")
        print(f"  done means        : {rec['done'] or '(none, or withheld: not author-trusted)'}")
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
    ap.add_argument("--session-id", default=None, help="the lease owner whose claims are heartbeated")
    a = ap.parse_args(args)
    root = project_root()
    claims = load_claims(root)
    beat: list[str] = []
    try:   # B5: a reconcile is a heartbeat for every claim THIS session holds (never another session's)
        beat = store.heartbeat_claims(root, _session_id(root, a.session_id, socket.gethostname()), now)
    except store.ClaimsUnreadable as e:
        print(f"reconcile: claims-unreadable - not heartbeating ({store.redact(str(e))})", file=sys.stderr)
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
        claim = claims.get(store.claim_key(repo, number))
        if cfg is None or login is None:
            results.append({"issue": f"{repo}#{number}", "number": number, "repo": repo, "stop_dispatch": True,
                            "reason": "unverified", "cause": store.redact(cause or "")})
        else:
            results.append(reconcile_one(run, cfg, login, repo, number, claim, now))
    # a claim record for an issue GitHub reports closed is dead weight: prune it (a cache, never authority)
    closed = {r["issue"] for r in results if r["reason"] in ("closed", "cancelled", "duplicate")}
    nk = lambda k: store.claim_key(*k.rpartition("#")[::2])   # noqa: E731
    pruned = [k for k in closed if nk(k) in claims]
    if pruned:
        drop = {nk(x) for x in pruned}
        try:
            store.update_claims(root, lambda cl: [cl.pop(k, None) for k in drop])
        except store.ClaimsUnreadable as e:
            print(f"reconcile: claims-unreadable — not pruning ({store.redact(str(e))})", file=sys.stderr)
    out = {"results": results, "complete": cause is None, "pruned_claims": sorted(pruned)}
    if beat:
        out["heartbeat"] = sorted(beat)
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


def _session_id(root: Path, flag: str | None, host: str) -> str:
    """Stable across invocations of the CLI: --session-id (explicit beats ambient), else CLAUDE_CODE_SESSION_ID, else a per-user id
    generated once and kept in the git dir (`foundry-lead-session`). Never derived from a parent pid."""
    sid = flag or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        return sid
    p = store._git_path("foundry-lead-session", root)
    if p is None:
        return f"{host}:{getpass.getuser()}"
    try:
        got = p.read_text(encoding="utf-8").strip()
        if got:
            return got
    except OSError:
        pass
    new = uuid.uuid4().hex
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new + "\n")
        return new
    except FileExistsError:
        try:
            return p.read_text(encoding="utf-8").strip() or new
        except OSError:
            return new
    except OSError:
        return new


def _create_exclusive(path: Path, rec: dict) -> bool:
    """Create `path` only if it does not exist (write a temp file, then os.link — atomic and exclusive).
    True when this call created it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.new")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


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
    sid = _session_id(root, a.session_id, host)
    if a.action in ("acquire", "renew"):
        try:
            store.ensure_runtime_dir(root)     # confined to the workspace + a `*` .gitignore, like the inbox runtime file
        except OSError as e:
            print(f"lead {a.action}: {e}", file=sys.stderr)
            return 1
    exists = path.exists()
    cur = _read_lease(path) if exists else None
    age = (now - _epoch(cur.get("heartbeat_at"))) if cur else None
    live = cur is not None and age is not None and 0 <= age < stale     # a future heartbeat (age < 0) is stale
    mine = cur is not None and cur.get("session_id") == sid and cur.get("hostname") == host
    ts = _iso(now)

    def held_msg():
        return (f"lead lease held by session {cur.get('session_id')} (op {cur.get('op_id')}, host {cur.get('hostname')}, "
                f"pid {cur.get('pid')}), heartbeat {int(age)}s ago (stale after {int(stale)}s); {LOCAL_ONLY}")

    if a.action == "check":
        state = "none" if not exists else ("mine" if mine and live else ("held" if live else "stale"))
        print(json.dumps({"lease": state, "path": str(path), "record": cur, "age_seconds": age,
                          "stale_after": stale, "note": LOCAL_ONLY}, indent=2))
        return 1 if state == "held" else 0
    if a.action == "acquire":
        if live and not mine:
            print("lead acquire refused: " + held_msg(), file=sys.stderr)
            return 1
        rec = {"op_id": a.op_id or os.environ.get("FOUNDRY_OP_ID") or getpass.getuser(), "hostname": host,
               "pid": os.getpid(), "session_id": sid,
               "acquired_at": cur["acquired_at"] if (mine and live and cur.get("acquired_at")) else ts, "heartbeat_at": ts}
        if mine and live:
            store.atomic_write_json(path, rec)            # re-acquire of my own live lease: refresh in place
        else:
            if exists:
                # take over a stale/unreadable lease: only ONE contender can rename it away, then create-exclusive
                gone = path.with_name(f"{path.name}.stale.{uuid.uuid4().hex[:8]}")
                try:
                    os.rename(path, gone)
                except FileNotFoundError:
                    pass
                else:
                    taken = _read_lease(gone)
                    gone.unlink(missing_ok=True)
                    if taken != cur:       # it was renewed/replaced between our read and the rename
                        print("lead acquire refused: the lease changed while taking it over; " + LOCAL_ONLY, file=sys.stderr)
                        return 1
            if not _create_exclusive(path, rec):
                print("lead acquire refused: lost a race to another session; " + LOCAL_ONLY, file=sys.stderr)
                return 1
        back = _read_lease(path)
        if not back or back.get("session_id") != sid:
            print("lead acquire refused: lost a race to another session; " + LOCAL_ONLY, file=sys.stderr)
            return 1
        print(f"lead lease acquired by {sid}" + (" (took over a stale lease)" if exists and not (mine and live) and not mine else ""))
        return 0
    if cur is None or not mine:
        print(f"lead {a.action}: no lease owned by this session ({'none exists' if not exists else 'held by ' + str(cur.get('session_id') if cur else 'unreadable')})",
              file=sys.stderr)
        return 1
    if a.action == "renew":
        cur["heartbeat_at"] = ts
        store.atomic_write_json(path, cur)
        beat = []
        try:   # B5: renewing the lead lease is also the heartbeat of every claim this session holds
            beat = store.heartbeat_claims(root, sid, now)
        except store.ClaimsUnreadable as e:
            print(f"lead renew: claims-unreadable - claims not heartbeated ({store.redact(str(e))})", file=sys.stderr)
        print("lead lease renewed" + (f"; heartbeat on {len(beat)} claim(s)" if beat else ""))
        return 0
    path.unlink(missing_ok=True)
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
    owned = {"next": lambda r: cmd_next(r, gh, clock), "read-issue": lambda r: cmd_read_issue(r, gh),
             "claim": lambda r: cmd_claim(r, gh, clock),
             "reconcile": lambda r: cmd_reconcile(r, gh, clock), "lead": lambda r: cmd_lead(r, clock)}
    if verb in owned:
        import foundry_github_capabilities as _ghc  # next|claim|reconcile|lead need github_scope
        refused = _ghc.gate(project_root(), f"foundry-ticket.py {verb}")
        if refused is not None:
            return refused
        return owned[verb](rest)
    if verb not in table:
        sys.exit(f"foundry-ticket: unknown verb {verb!r}; one of {', '.join([*table, *owned])}")
    return table[verb](rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
