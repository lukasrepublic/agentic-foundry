#!/usr/bin/env python3
"""deck-cost-census -- the S5 cost line for one deck tick (SDLC review amendment (c), rules 7 and 8).

Prints one line: `agents started N | longest agent C calls | items closed K`.
  N = subagent transcripts started after --since (first record timestamp, else file mtime).
  C = the largest tool-call count among agents started after --since OR still active (file mtime after --since),
      so one long agent that spans several ticks stays visible.
  K = PRs merged + atoms authorized since --since.
Agents are read from `<session-dir>/subagents/` and `<session-dir>/tasks/` only; if neither exists the census
refuses (exit 2). Symlinks and non-regular files are skipped.

Usage:
  deck-cost-census.py --session-dir DIR [--since ISO] [--prs FILE] [--append LOG] [--ci-pending]
  deck-cost-census.py --session-dir DIR --repo OWNER/NAME --branch-prefix PREFIX [--since ISO] [--prs FILE]
  deck-cost-census.py --stall-check LOG [--stall-after N]

--prs FILE is JSON, either a list of items or an object {"merged": [...], "authorized": [...]}. An item is an
  ISO-8601 string or an object with one of `mergedAt`, `authorizedAt`, `at`, `merged_at`, `authorized_at`.
  Items at or before --since do not count; without --since all count.
--repo reads merged PRs with `gh` (searching `merged:>SINCE` when --since is given, up to 1000 PRs) and counts
  only PRs whose head branch starts with --branch-prefix (required with --repo). With --repo, merged PRs come
  from gh and authorized atoms come only from the `authorized` list of --prs.
--append LOG adds `<UTC stamp> <line>` to the log; --ci-pending tags it `ci-pending` (rule 8: it is skipped).
--stall-check exits 3 and prints `cost-without-progress` when the last N counted log lines have N > 0 and K = 0.
  N comes from --stall-after, else the policy `deck.stall_after_ticks` (default 3); it must be at least 1.
Exit 0 ok / 2 usage or input error / 3 stall. Stdlib plus the shared cost-log code in foundry_deck_report.py.
"""
import argparse
import datetime as dt
import json
import os
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_deck_report as _dr  # noqa: E402  (the one cost-log implementation)

_TS_KEYS = ("mergedAt", "authorizedAt", "at", "merged_at", "authorized_at")


class Refusal(Exception):
    pass


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
    roots = [os.path.join(session_dir, d) for d in ("subagents", "tasks")]
    roots = [r for r in roots if os.path.isdir(r) and not os.path.islink(r)]
    if not roots:
        raise Refusal("--session-dir has neither subagents/ nor tasks/")
    files = []
    for root in roots:
        for dirpath, _dirs, names in os.walk(root, followlinks=False):
            for n in names:
                path = os.path.join(dirpath, n)
                try:
                    regular = stat.S_ISREG(os.lstat(path).st_mode)
                except OSError:
                    continue
                if n.endswith(".jsonl") and regular:
                    files.append(path)
    return sorted(files)


def _transcript(path):
    """-> (start datetime, last-modified datetime, tool-call count). Read line by line."""
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
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
    return (start or mtime), mtime, calls


def count_agents(session_dir, since=None):
    """-> (N, C). N counts agents started after `since`; C is the longest among those started or still active."""
    n, longest = 0, 0
    for path in _agent_files(session_dir):
        start, mtime, calls = _transcript(path)
        started = since is None or start > since
        active = since is None or mtime > since
        n += 1 if started else 0
        if started or active:
            longest = max(longest, calls)
    return n, longest


def _stamp(item):
    if isinstance(item, str):
        return parse_ts(item)
    if isinstance(item, dict):
        for k in _TS_KEYS:
            if k in item:
                return parse_ts(item[k])
    return None


def _groups(prs):
    """Validate the --prs shape -> (merged list, authorized list). Raises Refusal on a wrong type."""
    if isinstance(prs, list):
        merged, authorized = prs, []
    elif isinstance(prs, dict):
        merged, authorized = prs.get("merged", []), prs.get("authorized", [])
    else:
        raise Refusal("--prs must be a JSON list or object")
    for name, group in (("merged", merged), ("authorized", authorized)):
        if not isinstance(group, list) or not all(isinstance(i, (str, dict)) for i in group):
            raise Refusal(f"--prs {name} must be a list of strings or objects")
    return merged, authorized


def count_since(items, since=None):
    return sum(1 for i in items if (t := _stamp(i)) is not None and (since is None or t > since))


def gh_merged(repo, prefix, since=None):
    cmd = ["gh", "pr", "list", "--repo", repo, "--state", "merged", "--limit", "1000",
           "--json", "mergedAt,headRefName"]
    if since is not None:
        cmd += ["--search", "merged:>" + since.strftime("%Y-%m-%dT%H:%M:%SZ")]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
    rows = json.loads(out.stdout)
    if not isinstance(rows, list):
        raise Refusal("gh returned an unexpected shape")
    return [r for r in rows if isinstance(r, dict) and str(r.get("headRefName", "")).startswith(prefix)]


def run(a):
    if a.stall_check:
        ticks = a.stall_after
        if ticks is None:
            try:
                ticks = _dr.stall_ticks()
            except Exception as exc:  # noqa: BLE001 - a broken policy must not become a silent default
                raise Refusal(f"cannot read deck.stall_after_ticks: {type(exc).__name__}")
        if ticks < 1:
            raise Refusal("--stall-after must be at least 1")
        if _dr.stall_check(a.stall_check, ticks):
            print("cost-without-progress")
            return 3
        return 0
    if not a.session_dir or not os.path.isdir(a.session_dir):
        raise Refusal("--session-dir must be an existing directory")
    since = None
    if a.since:
        since = parse_ts(a.since)
        if since is None:
            raise Refusal("--since is not an ISO-8601 time")
    if a.repo and not a.branch_prefix:
        raise Refusal("--branch-prefix is required with --repo")
    merged, authorized = [], []
    if a.prs:
        with open(a.prs, encoding="utf-8") as fh:
            raw = json.load(fh)
        if a.repo and isinstance(raw, list):
            raise Refusal("--repo with a list-shaped --prs is ambiguous: use {\"authorized\": [...]} with --repo")
        merged, authorized = _groups(raw)
    if a.repo:
        merged = gh_merged(a.repo, a.branch_prefix, since)
    k = count_since(merged, since) + count_since(authorized, since)
    n, c = count_agents(a.session_dir, since)
    line = _dr.cost_line_text(n, c, k)
    print(line)
    if a.append:
        _dr.append_cost(a.append, line, ci_pending=a.ci_pending)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deck-cost-census.py", description="the S5 cost line")
    ap.add_argument("--session-dir")
    ap.add_argument("--since")
    ap.add_argument("--prs", help="JSON file with merged PRs and authorized atoms")
    ap.add_argument("--repo", help="OWNER/NAME; read merged PRs with gh")
    ap.add_argument("--branch-prefix", help="count only merged PRs whose head branch starts with this (with --repo)")
    ap.add_argument("--append", metavar="LOG")
    ap.add_argument("--ci-pending", action="store_true")
    ap.add_argument("--stall-check", metavar="LOG")
    ap.add_argument("--stall-after", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        return run(a)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
    except (OSError, ValueError, subprocess.SubprocessError, _dr.Refused) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
