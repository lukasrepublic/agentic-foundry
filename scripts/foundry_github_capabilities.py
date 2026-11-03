#!/usr/bin/env python3
"""foundry_github_capabilities.py — prove which GitHub request sources the operator's own
credentials actually cover, for the `github_scope` in `.claude/foundry-project.json` (gn-03).

A successful issue query alone does not establish a functioning inbox: a token missing
`read:project`, or a fine-grained token the notifications API refuses, silently narrows what the
deck can see. This probe names every such gap instead of letting it surface as an empty inbox.

Config (`.claude/foundry-project.json`):
  "github_scope": {"owner": ..., "planning_repo": ..., "project_number": N,
                   "operators": {"<login>": "<op_id>"}}

Every row carries `status` in {ok, uncovered, error}, its `scope` and a `detail`:
  uncovered  the cause is a token scope/type refusal (missing scope, HTTP 401/403/404)
  error      anything else (gh absent, timeout, 5xx, unparseable output)
A successful probe with zero results is `ok`.

Classification detail: the scope-complaint text (`_SCOPE_RE`) is consulted only when NO HTTP status
was parsed; a parsed 401/403/404 is `uncovered` by itself, any other status (5xx, ...) is `error`, and a
403/429 whose body names a rate limit ("rate limit", "secondary") is `error` (throttled, not refused).

Overall deadline: the whole probe set is bounded by `deadline` seconds (default 90); every call after
it is skipped and its row is `error` with detail `deadline`, and the summary says so.

Exit codes of `main`: 0 full coverage; 2 partial coverage with only `uncovered` rows; 1 when no
github_scope is configured, or any inbox row is `error` (or the deadline was hit).

Deviation (recorded): branch-protection rules are read through the single injectable runner plus
`foundry_tier_preflight.probe_has_rsc_rule`, NOT through that module's `run_gh`, so that the one-runner
read-only/timeout/deadline discipline holds for every subprocess.

READ-ONLY: only REST GET, `gh search`, `--help`, and GraphQL `query` are issued; every subprocess
goes through ONE injectable `runner(argv, timeout)` and is bounded by a timeout. Fixing access
(`gh auth refresh`, scope changes) is only NAMED, never performed.

  foundry_github_capabilities.py [--project-dir DIR] [--json] [--timeout SECONDS]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foundry_tier_preflight import API_VERSION, probe_has_rsc_rule  # noqa: E402

# Claude Code harness floor: the `claude_code: ">=2.1.278"` entry of the workspace
# docs/standing-versions.lock.yaml (source-verified, researched 2026-09-21). Not a new pin.
CLAUDE_CODE_FLOOR = (2, 1, 278)

DEFAULT_TIMEOUT = 30
DEFAULT_DEADLINE = 90
REFRESH_REMEDIATION = "gh auth refresh -s read:project,project"
REQUIRED_SOURCES = ("planning_repo", "assigned", "mentions", "review_requests", "approvals")
INBOX_ROWS = ("planning_repo", "project", "assigned", "mentions", "review_requests", "approvals")
USAGE_WINDOW = timedelta(hours=24)

_HTTP_RE = re.compile(r"HTTP[/ ]?(?:\d\.\d )?(\d{3})")
_SCOPE_RE = re.compile(r"missing required scopes|required scope|insufficient|read:project|"
                       r"resource not accessible|must have admin|bad credentials|"
                       r"not logged in|authentication", re.I)
_RATE_RE = re.compile(r"rate limit|secondary", re.I)
_NOT_FOUND_RE = re.compile(r"NOT_FOUND|Could not resolve to a", re.I)


@dataclass
class Result:
    rc: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    missing: bool = False
    deadline: bool = False


def default_runner(argv, timeout):
    """The one place a subprocess is spawned."""
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(rc=124, timed_out=True)
    except FileNotFoundError:
        return Result(rc=127, missing=True)
    return Result(rc=p.returncode, stdout=p.stdout, stderr=p.stderr)


def _status_of(res) -> int | None:
    m = _HTTP_RE.search(res.stderr or "")
    if m:
        return int(m.group(1))
    m = re.match(r"HTTP/\S+ (\d{3})", res.stdout or "")
    return int(m.group(1)) if m else None


def _row(name, status, detail, scope, **extra):
    r = {"name": name, "status": status, "scope": scope, "detail": detail}
    r.update(extra)
    return r


def _fail(name, res, scope, **extra):
    """Classify a failed call: refusal -> uncovered, anything else -> error."""
    if res.missing:
        return _row(name, "error", "gh/executable not found on PATH", scope, exit_code=res.rc)
    if res.deadline:
        return _row(name, "error", "deadline", scope, exit_code=res.rc)
    if res.timed_out:
        return _row(name, "error", "timed out", scope, exit_code=res.rc)
    http = _status_of(res)
    text = " ".join(((res.stderr or "") + " " + (res.stdout or "")).split())[:200]
    if http is None:
        refusal = bool(_SCOPE_RE.search(text))
    else:
        refusal = http in (401, 403, 404) and not _RATE_RE.search(text)
    detail = (f"HTTP {http}; " if http else f"exit {res.rc}; ") + text
    return _row(name, "uncovered" if refusal else "error", detail, scope,
                exit_code=res.rc, **({"http_status": http} if http else {}), **extra)


def _gh_api(path, timeout, runner, include=False, extra=()):
    argv = ["gh", "api"] + (["-i"] if include else []) + \
           ["-H", f"X-GitHub-Api-Version: {API_VERSION}"] + list(extra) + [path]
    return runner(argv, timeout)


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _body(stdout):
    """Body of a `gh api -i` response (after the blank line)."""
    parts = re.split(r"\r?\n\r?\n", stdout or "", maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _header(stdout, name):
    head = re.split(r"\r?\n\r?\n", stdout or "", maxsplit=1)[0]
    for line in head.splitlines():
        k, _, v = line.partition(":")
        if k.strip().lower() == name.lower():
            return v.strip()
    return None


# ----------------------------------------------- probes ----------------------------------------------- #

def probe_gh(timeout, runner):
    res = runner(["gh", "auth", "status"], timeout)
    if res.rc == 0:
        return _row("gh", "ok", "gh present and authenticated", "gh")
    return _fail("gh", res, "gh")


def probe_token_scopes(timeout, runner):
    res = _gh_api("user", timeout, runner, include=True)
    if res.rc != 0:
        return _fail("token_scopes", res, "token")
    scopes = _header(res.stdout, "X-OAuth-Scopes")
    if scopes is None:
        return _row("token_scopes", "ok", "unknown (no X-OAuth-Scopes header: fine-grained token)",
                    "token", scopes="unknown")
    lst = [s.strip() for s in scopes.split(",") if s.strip()]
    return _row("token_scopes", "ok", "scopes: " + (", ".join(lst) or "(none)"), "token", scopes=lst)


def probe_planning_repo(cfg, timeout, runner):
    slug = f"{cfg['owner']}/{cfg['planning_repo']}"
    res = _gh_api(f"repos/{slug}", timeout, runner)
    if res.rc != 0:
        return _fail("planning_repo", res, slug)
    return _row("planning_repo", "ok", "planning repo readable", slug)


_PROJECT_QUERY = ("query($o:String!,$n:Int!){repositoryOwner(login:$o){... on ProjectV2Owner"
                  "{projectV2(number:$n){id title}}}}")


def _scope_row(scope, **extra):
    return _row("project", "uncovered",
                f"missing read:project/project scope; remediation: {REFRESH_REMEDIATION}",
                scope, remediation=REFRESH_REMEDIATION, **extra)


def probe_project(cfg, timeout, runner):
    scope = f"{cfg['owner']}/project/{cfg.get('project_number')}"
    if cfg.get("_project_number_error"):
        return _row("project", "error", cfg["_project_number_error"], scope)
    argv = ["gh", "api", "graphql", "-f", f"query={_PROJECT_QUERY}", "-f", f"o={cfg['owner']}",
            "-F", f"n={int(cfg['project_number'])}"]
    res = runner(argv, timeout)
    blob = (res.stderr or "") + (res.stdout or "")
    if res.rc != 0:
        if res.missing or res.timed_out or res.deadline:
            return _fail("project", res, scope)
        if re.search(r"missing required scopes|read:project|INSUFFICIENT_SCOPES", blob, re.I):
            return _scope_row(scope, exit_code=res.rc)
        if _NOT_FOUND_RE.search(blob):
            return _row("project", "uncovered", "project not found or not visible to this token",
                        scope, exit_code=res.rc)
        return _fail("project", res, scope)
    doc = _json(res.stdout)
    node = (((doc or {}).get("data") or {}).get("repositoryOwner") or {}).get("projectV2") \
        if isinstance(doc, dict) else None
    if node:
        return _row("project", "ok", f"project reachable: {node.get('title', '')}", scope)
    errs = json.dumps(doc.get("errors")) if isinstance(doc, dict) and doc.get("errors") else ""
    if errs:
        if re.search(r"INSUFFICIENT_SCOPES|read:project", errs):
            return _scope_row(scope)
        if _NOT_FOUND_RE.search(errs):
            return _row("project", "uncovered", "project not found or not visible to this token", scope)
        return _row("project", "error", "GraphQL errors: " + errs[:150], scope)
    if isinstance(doc, dict) and doc.get("data") is not None:
        return _row("project", "uncovered", "project not found or not visible to this token", scope)
    return _row("project", "error", "unparseable GraphQL response", scope)


def probe_search(name, argv_tail, cfg, timeout, runner):
    kind, rest = argv_tail[0], argv_tail[1:]
    res = runner(["gh", "search", kind, "--owner", cfg["owner"]] + rest +
                 ["--limit", "1", "--json", "number"], timeout)
    scope = f"owner:{cfg['owner']}"
    if res.rc != 0:
        return _fail(name, res, scope)
    if not isinstance(_json(res.stdout), list):
        return _row(name, "error", "unparseable search output", scope)
    return _row(name, "ok", "search answered", scope)


def probe_approvals(cfg, timeout, runner):
    res = _gh_api("notifications?per_page=50", timeout, runner, include=True)
    scope = "notifications"
    if res.rc != 0:
        row = _fail("approvals", res, scope)
        if row["status"] == "uncovered":
            row["detail"] += " (notifications API refuses fine-grained/App tokens)"
        return row
    items = _json(_body(res.stdout))
    if not isinstance(items, list):
        return _row("approvals", "error", "unparseable notifications response", scope, http_status=200)
    n = sum(1 for i in items if isinstance(i, dict)
            and i.get("reason") in ("review_requested", "approval_requested"))
    poll = _header(res.stdout, "X-Poll-Interval")
    return _row("approvals", "ok",
                f"HTTP 200; {n} review/approval notification(s)" + (f"; poll interval {poll}s" if poll else ""),
                scope, http_status=200)


def probe_native_dependencies(timeout, runner):
    res = runner(["gh", "issue", "edit", "--help"], timeout)
    if res.rc != 0:
        return _fail("native_dependencies", res, "gh")
    if "--add-blocked-by" in (res.stdout or "") + (res.stderr or ""):
        return _row("native_dependencies", "ok", "gh issue edit supports --add-blocked-by", "gh")
    return _row("native_dependencies", "uncovered", "gh lacks --add-blocked-by; upgrade gh", "gh")


def _repo_slug(path, timeout, runner):
    res = runner(["git", "-C", path, "remote", "get-url", "origin"], timeout)
    if res.rc != 0:
        return None, res
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", res.stdout.strip())
    return (m.group(1) if m else None), res


def probe_branch_protection(project_dir, manifest, timeout, runner):
    repos = manifest.get("repos") if isinstance(manifest.get("repos"), dict) else {}
    rows = []
    for key, rec in repos.items():
        rel = rec.get("path") if isinstance(rec, dict) else None
        if not isinstance(rel, str) or not rel:
            continue
        path = rel if os.path.isabs(rel) else os.path.join(project_dir, rel)
        name = f"branch_protection:{key}"
        slug, sres = _repo_slug(path, timeout, runner)
        if not slug:
            if sres.deadline:
                rows.append(_fail(name, sres, key))
                continue
            rows.append(_row(name, "error", "no GitHub origin remote resolved", key))
            continue
        res = _gh_api(f"repos/{slug}", timeout, runner)
        if res.rc != 0:
            rows.append(_fail(name, res, slug))
            continue
        branch = (_json(res.stdout) or {}).get("default_branch") if isinstance(_json(res.stdout), dict) else None
        if not branch:
            rows.append(_row(name, "error", "default branch not found in response", slug))
            continue
        res = _gh_api(f"repos/{slug}/rules/branches/{branch}", timeout, runner)
        if res.rc != 0:
            rows.append(_fail(name, res, f"{slug}@{branch}"))
            continue
        rules = _json(res.stdout)
        if not isinstance(rules, list):
            rows.append(_row(name, "error", "unparseable rules response", f"{slug}@{branch}"))
        elif probe_has_rsc_rule(rules):
            rows.append(_row(name, "ok", "required_status_checks enforced", f"{slug}@{branch}"))
        else:
            rows.append(_row(name, "uncovered", "no enforced required_status_checks rule",
                             f"{slug}@{branch}"))
    return rows or [_row("branch_protection", "ok", "no repos{} entries to check", "-")]


def probe_claude_code(timeout, runner):
    floor = ".".join(map(str, CLAUDE_CODE_FLOOR))
    res = runner(["claude", "--version"], timeout)
    if res.rc != 0:
        return _fail("claude_code", res, "claude")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", res.stdout or "")
    if not m:
        return _row("claude_code", "error", "unparseable version output", "claude")
    v = tuple(int(x) for x in m.groups())
    ver = ".".join(map(str, v))
    if v >= CLAUDE_CODE_FLOOR:
        return _row("claude_code", "ok", f"{ver} >= floor {floor}", "claude")
    return _row("claude_code", "uncovered", f"{ver} < floor {floor}", "claude")


def probe_scheduling():
    return _row("scheduling", "ok", "declared, verified at deck start", "claude",
                state="declared, verified at deck start")


def _usage_dataset(manifest):
    u = manifest.get("usage")
    if isinstance(u, dict) and "dataset" in u:
        return u["dataset"]
    return manifest.get("usage.dataset")


def probe_usage(project_dir, manifest, now=None):
    ds = _usage_dataset(manifest)
    if ds in (None, ""):
        return _row("usage", "uncovered", "not-configured", "usage.dataset", state="not-configured")

    def unknown(why):
        return _row("usage", "error", f"unknown ({why})", str(ds), state="unknown")

    if not isinstance(ds, str):
        return unknown("usage.dataset is not a path")
    p = ds if os.path.isabs(ds) else os.path.join(project_dir, ds)
    try:
        lines = [ln for ln in Path(p).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, UnicodeDecodeError):
        return unknown("unreadable")
    now = now or datetime.now(timezone.utc)
    newest = None
    for ln in lines:
        rec = _json(ln)
        if not isinstance(rec, dict):
            return unknown("unrecognised record")
        raw = rec.get("ts") or rec.get("timestamp")
        try:
            t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return unknown("record without a timestamp")
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        newest = t if newest is None or t > newest else newest
    if newest is None or now - newest > USAGE_WINDOW:
        return _row("usage", "uncovered", "stale (no record in the last 24h)", str(ds), state="stale")
    return _row("usage", "ok", "fresh (record within 24h)", str(ds), state="fresh")


def probe_issue_artifacts(timeout, runner):
    res = runner(["gh", "issue", "artifact", "--help"], timeout)
    if res.rc == 0:
        return _row("issue_artifacts", "ok", "gh issue artifact available (gh >= 2.103.0)", "gh")
    if res.missing or res.timed_out:
        return _fail("issue_artifacts", res, "gh")
    return _row("issue_artifacts", "uncovered",
                "fallback: issue description / linked document issue", "gh", exit_code=res.rc)


# ------------------------------------------- orchestration -------------------------------------------- #

def load_scope(project_dir):
    """(manifest, github_scope) — github_scope is None when absent or malformed."""
    try:
        with open(os.path.join(project_dir, ".claude", "foundry-project.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return {}, None
    if not isinstance(manifest, dict):
        return {}, None
    cfg = manifest.get("github_scope")
    if not isinstance(cfg, dict) or not cfg.get("owner") or not cfg.get("planning_repo"):
        return manifest, None
    cfg = dict(cfg)
    n = cfg.get("project_number")
    if isinstance(n, str) and n.strip().isdigit():
        n = int(n.strip())
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        cfg["_project_number_error"] = ("github_scope.project_number is missing or not a positive "
                                        "integer in .claude/foundry-project.json")
    else:
        cfg["project_number"] = n
    return manifest, cfg


def summarize(rows):
    by = {r["name"]: r for r in rows}
    bad = [n for n in INBOX_ROWS if by.get(n, {}).get("status") != "ok"]
    unc = [n for n in bad if by[n]["status"] == "uncovered"]
    err = [n for n in bad if by[n]["status"] != "uncovered"]
    coverage = "full" if not bad else "partial"
    line = "full" if not bad else "partial — " + "; ".join(
        p for p in ((f"uncovered: {', '.join(unc)}" if unc else ""),
                    (f"error: {', '.join(err)}" if err else "")) if p)
    ok = all(by.get(n, {}).get("status") == "ok" for n in REQUIRED_SOURCES)
    return {"coverage": coverage, "required_sources_ok": ok, "summary": line,
            "has_error": bool(err)}


class _Bounded:
    """Wraps the runner with the overall deadline: past it no subprocess is spawned, and each call's
    timeout is capped to the time that remains."""

    def __init__(self, runner, deadline):
        self.runner, self.end, self.hit = runner, time.monotonic() + deadline, False

    def __call__(self, argv, timeout):
        left = self.end - time.monotonic()
        if left <= 0:
            self.hit = True
            return Result(rc=124, deadline=True)
        return self.runner(argv, min(timeout, left))


def collect(project_dir, runner=default_runner, timeout=DEFAULT_TIMEOUT, now=None,
            deadline=DEFAULT_DEADLINE):
    """Run every probe. Returns None when no github_scope is configured."""
    manifest, cfg = load_scope(project_dir)
    if cfg is None:
        return None
    runner = _Bounded(runner, deadline)
    out = _collect(project_dir, manifest, cfg, runner, timeout, now)
    out["deadline_hit"] = runner.hit
    if runner.hit:
        out["summary"] += f" (deadline {deadline:g}s reached; remaining rows are error: deadline)"
    return out


def _collect(project_dir, manifest, cfg, runner, timeout, now):
    me = "@me"
    rows = [
        probe_gh(timeout, runner),
        probe_token_scopes(timeout, runner),
        probe_planning_repo(cfg, timeout, runner),
        probe_project(cfg, timeout, runner),
        probe_search("assigned", ["issues", "--assignee", me], cfg, timeout, runner),
        probe_search("mentions", ["issues", "--mentions", me], cfg, timeout, runner),
        probe_search("review_requests", ["prs", "--review-requested", me], cfg, timeout, runner),
        probe_approvals(cfg, timeout, runner),
        probe_native_dependencies(timeout, runner),
        *probe_branch_protection(project_dir, manifest, timeout, runner),
        probe_claude_code(timeout, runner),
        probe_scheduling(),
        probe_usage(project_dir, manifest, now),
        probe_issue_artifacts(timeout, runner),
    ]
    out = summarize(rows)
    out["sources"] = rows
    return out


def advisory_line(project_dir, runner=default_runner, timeout=DEFAULT_TIMEOUT,
                  deadline=DEFAULT_DEADLINE):
    """The doctor's one-line value: `full`, `partial — uncovered: ...`, or the n/a text."""
    res = collect(project_dir, runner, timeout, deadline=deadline)
    if res is None:
        return "n/a (no github_scope configured)"
    return res["summary"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Probe which GitHub request sources the operator's "
                                             "credentials cover (read-only).")
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--json", action="store_true", help="emit a single JSON object")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-call bound, seconds")
    ap.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE,
                    help="bound for the whole probe set, seconds")
    args = ap.parse_args(argv)
    res = collect(args.project_dir, timeout=args.timeout, deadline=args.deadline)
    if res is None:
        msg = "n/a (no github_scope configured)"
        print(json.dumps({"coverage": "n/a", "required_sources_ok": False, "sources": [],
                          "summary": msg}) if args.json else msg)
        return 1
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        for r in res["sources"]:
            print(f"{r['status']:<9} {r['name']:<28} {r['scope']}: {r['detail']}")
        print(f"coverage: {res['summary']}  (required_sources_ok={str(res['required_sources_ok']).lower()})")
    if res["coverage"] == "full":
        return 0
    return 1 if res["has_error"] or res["deadline_hit"] else 2


if __name__ == "__main__":
    sys.exit(main())
