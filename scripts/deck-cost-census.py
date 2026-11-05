#!/usr/bin/env python3
"""deck-cost-census -- the S5 cost line for one deck tick (SDLC review amendment (c), rules 7 and 8).

Prints one line: `agents started N | longest agent C calls | items closed K`.
  N = subagent transcripts started after --since, from the session's subagents (or tasks) directory.
  C = the largest tool-call count among those agents.
  K = PRs merged + atoms authorized since --since, from --prs (JSON) or `gh` when given --repo.

Usage:
  deck-cost-census.py --session-dir DIR [--since ISO] [--prs FILE | --repo OWNER/NAME] [--append LOG] [--ci-pending]
  deck-cost-census.py --stall-check LOG [--stall-after N]

--append LOG adds `<UTC stamp> <line>` to the log (`ci-pending` tag with --ci-pending).
--stall-check exits 3 and prints `cost-without-progress` when the last N log lines (default 3) are counted
and have N > 0 and K = 0; a ci-pending line is not counted and breaks the run. Otherwise exit 0.
Exit 0 ok / 2 usage or input error / 3 stall. Stdlib only.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

_LINE_RE = re.compile(r"^agents started (\d+) \| longest agent (\d+) calls \| items closed (\d+)$")
_LOG_RE = re.compile(r"^(\S+) agents started (\d+) \| longest agent (\d+) calls \| items closed (\d+)( ci-pending)?$")


def parse_ts(value):
    """ISO-8601 text -> aware UTC datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _agent_files(session_dir):
    roots = [os.path.join(session_dir, d) for d in ("subagents", "tasks") if os.path.isdir(os.path.join(session_dir, d))]
    files = []
    for root in roots or [session_dir]:
        for dirpath, _dirs, names in os.walk(root):
            files += [os.path.join(dirpath, n) for n in names if n.endswith(".jsonl")]
    return sorted(files)


def _transcript(path):
    """-> (start datetime, tool-call count). Start = first record timestamp, else the file mtime."""
    start, calls = None, 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if start is None:
                start = parse_ts(rec.get("timestamp"))
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                calls += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
    if start is None:
        start = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
    return start, calls


def count_agents(session_dir, since=None):
    """-> (N, C)."""
    counts = []
    for path in _agent_files(session_dir):
        start, calls = _transcript(path)
        if since is None or start > since:
            counts.append(calls)
    return len(counts), max(counts, default=0)


def _stamp(item):
    if isinstance(item, str):
        return parse_ts(item)
    if isinstance(item, dict):
        for k in ("mergedAt", "authorizedAt", "at", "merged_at", "authorized_at"):
            if k in item:
                return parse_ts(item[k])
    return None


def count_closed(prs, since=None):
    """prs: a list of PR objects/stamps, or {"merged": [...], "authorized": [...]}. -> K."""
    groups = [prs.get("merged", []), prs.get("authorized", [])] if isinstance(prs, dict) else [prs]
    k = 0
    for group in groups:
        for item in group:
            t = _stamp(item)
            if t is not None and (since is None or t > since):
                k += 1
    return k


def _gh_merged(repo):
    out = subprocess.run(["gh", "pr", "list", "--repo", repo, "--state", "merged", "--limit", "100",
                          "--json", "mergedAt"], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def cost_line(n, c, k):
    return f"agents started {n} | longest agent {c} calls | items closed {k}"


def stall(log_path, ticks=3):
    try:
        lines = open(log_path, encoding="utf-8").read().splitlines()
    except OSError:
        return False
    rows = [m for m in (_LOG_RE.match(x) for x in lines) if m]
    if len(rows) < ticks:
        return False
    return all(m.group(5) is None and int(m.group(2)) > 0 and int(m.group(4)) == 0 for m in rows[-ticks:])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deck-cost-census.py", description="the S5 cost line")
    ap.add_argument("--session-dir")
    ap.add_argument("--since")
    ap.add_argument("--prs", help="JSON file with merged PRs and authorized atoms")
    ap.add_argument("--repo", help="OWNER/NAME; read merged PRs with gh")
    ap.add_argument("--append", metavar="LOG")
    ap.add_argument("--ci-pending", action="store_true")
    ap.add_argument("--stall-check", metavar="LOG")
    ap.add_argument("--stall-after", type=int, default=3)
    a = ap.parse_args(argv)
    if a.stall_check:
        if stall(a.stall_check, a.stall_after):
            print("cost-without-progress")
            return 3
        return 0
    if not a.session_dir or not os.path.isdir(a.session_dir):
        print("refused: --session-dir must be an existing directory", file=sys.stderr)
        return 2
    since = None
    if a.since:
        since = parse_ts(a.since)
        if since is None:
            print("refused: --since is not an ISO-8601 time", file=sys.stderr)
            return 2
    try:
        if a.prs:
            with open(a.prs, encoding="utf-8") as fh:
                prs = json.load(fh)
        elif a.repo:
            prs = _gh_merged(a.repo)
        else:
            prs = []
        k = count_closed(prs, since)
        n, c = count_agents(a.session_dir, since)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    line = cost_line(n, c, k)
    print(line)
    if a.append:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(a.append, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}{' ci-pending' if a.ci_pending else ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
