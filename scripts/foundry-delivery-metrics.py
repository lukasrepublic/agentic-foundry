#!/usr/bin/env python3
"""foundry-delivery-metrics.py — ONE measured line per repo per week (ticket-first default, #269).

Replaces prose status reports: lines added per class (paper / code / infra / tests / plumbing /
other) from `git log --numstat` on the default branch, the paper:code ratio, merged-PR count, median
ticket->merge lead time (via `gh`, n/a on any failure) and, with --transcripts, the operator
frustration-turn rate and the silent-yield rate.

Excluded from every count: lockfiles, node_modules, vendored/, binaries, and any *.json file that
adds more than 2,000 lines inside the window (generated dumps).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import sys

DUMP_LIMIT = 2000
CLASSES = ("paper", "code", "infra", "tests", "plumbing", "other")
LOCKS = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "uv.lock",
         "Gemfile.lock", "go.sum", "composer.lock", "Pipfile.lock", ".terraform.lock.hcl"}
SKIP_DIRS = {"node_modules", ".worktrees", "vendor", "vendored", "third_party", ".git"}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".sh", ".c", ".h",
            ".cc", ".cpp", ".cs", ".swift", ".php", ".scala", ".sql", ".css", ".scss", ".html", ".vue",
            ".svelte", ".mjs", ".cjs", ".lua", ".ex", ".exs"}
PAPER_DIRS = {"specs", "intake", "docs", "status-reports", ".foundry", "context", "charters"}
PAPER_NAMES = {"acceptance-contract.yaml", "release.yaml", "state.yaml"}
INFRA_PATH = re.compile(r"(^|/)(k8s|kubernetes|helm|charts|argocd|kustomize|gitops|\.github/workflows|\.gitlab-ci)"
                        r"|kustomization\.ya?ml$|(^|/)Dockerfile", re.I)
# Copied from docs/micro-workflows/empirical-mining/mine_failures.py (FRUST, Q_PHRASE).
FRUST = re.compile(r"\b(wtf|fuck|fucking|damn|shit|why the|why is|why do|why does|again\b|keeps? (happening|failing|breaking)|still (not|broken|failing)|not working|broken|stop asking|just do|i (already )?(told|said)|how many times|seriously|come on|jesus|ffs|useless|wrong again|no\b.{0,20}\bno\b)", re.I)
Q_PHRASE = re.compile(r"\b(want me to|should i|do you want|would you like|which (one|option|do you)|let me know|shall i|can you confirm|please confirm|your call|up to you|say the word)\b", re.I)


def classify(path):
    """Return a class name, or None when the file is excluded outright."""
    parts = path.split("/")
    base = parts[-1]
    if base in LOCKS or base.endswith(".lock") or SKIP_DIRS & set(parts[:-1]):
        return None
    if parts[0] in (".claude", "config") or "hooks" in parts[:-1] or base in ("CLAUDE.md", "WORKFLOW.md"):
        return "plumbing"
    if parts[0] in ("tests", "test") or "__tests__" in parts or base.startswith("test_") \
            or re.search(r"_test\.[^.]+$|\.(test|spec)\.[jt]sx?$", base):
        return "tests"
    if parts[0] in PAPER_DIRS or base in PAPER_NAMES or base.endswith(".md"):
        return "paper"
    ext = os.path.splitext(base)[1].lower()
    if ext in (".tf", ".hcl", ".tofu") or base == "Dockerfile" or base.startswith("Dockerfile.") \
            or (ext in (".yaml", ".yml") and INFRA_PATH.search(path)):
        return "infra"
    return "code" if ext in CODE_EXT else "other"


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else ""


def find_repos(root):
    out, root = [], os.path.abspath(root)

    def walk(d, depth):
        if os.path.exists(os.path.join(d, ".git")):
            out.append(d)
        if depth >= 2:
            return
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for n in names:
            p = os.path.join(d, n)
            if n not in SKIP_DIRS and not n.startswith(".") and os.path.isdir(p) and not os.path.islink(p):
                walk(p, depth + 1)
    walk(root, 0)
    return out


def default_ref(repo):
    for ref in ("origin/main", "main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip():
            return ref
    return "HEAD"


def parse_since(s):
    m = re.fullmatch(r"(\d+)d", s)
    if m:
        return dt.datetime.now() - dt.timedelta(days=int(m.group(1)))
    return dt.datetime.strptime(s, "%Y-%m-%d")


def line_counts(repo, ref, since):
    per_file = {}
    for line in git(repo, "log", ref, "--since", since.isoformat(), "--no-merges", "--numstat", "--format=").splitlines():
        a = line.split("\t")
        if len(a) == 3 and a[0].isdigit():  # binaries show '-'
            per_file[a[2]] = per_file.get(a[2], 0) + int(a[0])
    counts = dict.fromkeys(CLASSES, 0)
    for path, n in per_file.items():
        cls = classify(path)
        if cls is None or (path.endswith(".json") and n > DUMP_LIMIT):
            continue
        counts[cls] += n
    return counts


def count_prs(repo, ref, since):
    n = 0
    for line in git(repo, "log", ref, "--since", since.isoformat(), "--format=%P\t%s").splitlines():
        parents, _, subj = line.partition("\t")
        if len(parents.split()) > 1 or re.search(r"\(#\d+\)$", subj):
            n += 1
    return n


def lead_time_h(repo, since):
    if not shutil.which("gh") or "github" not in git(repo, "remote", "get-url", "origin"):
        return None
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "merged", "--limit", "200", "--json",
                            "number,createdAt,mergedAt,closingIssuesReferences"],
                           cwd=repo, capture_output=True, text=True, timeout=60)
        if r.returncode:
            return None
        ts = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        floor = since.astimezone(dt.timezone.utc)
        hours = []
        for pr in json.loads(r.stdout):
            merged = ts(pr["mergedAt"])
            if merged < floor:
                continue
            start = pr["createdAt"]
            for iss in pr.get("closingIssuesReferences") or []:
                if iss.get("createdAt"):
                    start = iss["createdAt"]
                    break
            hours.append((merged - ts(start)).total_seconds() / 3600)
        return round(statistics.median(hours), 1) if hours else None
    except Exception:
        return None


def transcript_rates(tdir, since):
    floor = since.astimezone(dt.timezone.utc)
    ops = frust = finals = silent = 0
    for dp, _, fns in os.walk(tdir):
        for fn in fns:
            if not fn.endswith(".jsonl"):
                continue
            last_final = None
            with open(os.path.join(dp, fn), errors="replace") as fh:
                for raw in fh:
                    try:
                        r = json.loads(raw)
                        if r.get("isSidechain") or r.get("isMeta") or r.get("isCompactSummary"):
                            continue
                        if dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) < floor:
                            continue
                        c = (r.get("message") or {}).get("content")
                        blocks = [{"type": "text", "text": c}] if isinstance(c, str) else (c or [])
                        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                        tool_use = any(b.get("type") == "tool_use" for b in blocks)
                        tool_res = any(b.get("type") == "tool_result" for b in blocks)
                    except Exception:
                        continue
                    if r.get("type") == "assistant":
                        last_final = text if text and not tool_use else None
                    elif r.get("type") == "user" and text and not tool_res:
                        ops += 1
                        frust += bool(FRUST.search(text))
                        if last_final is not None:
                            finals += 1
                            silent += not ("?" in last_final or Q_PHRASE.search(last_final))
                        last_final = None
    return (round(frust / ops, 2) if ops else None, round(silent / finals, 2) if finals else None)


def measure(repo, since, name, rates):
    ref = default_ref(repo)
    c = line_counts(repo, ref, since)
    denom = c["code"] + c["infra"] + c["tests"]
    ratio = "inf" if denom == 0 and c["paper"] else (round(c["paper"] / denom, 2) if denom else 0.0)
    return {"repo": name, **{k: c[k] for k in CLASSES}, "paper_ratio": ratio, "prs": count_prs(repo, ref, since),
            "lead_time_h": lead_time_h(repo, since), "frustration_turn_rate": rates[0], "silent_yield_rate": rates[1]}


def fmt(d, today):
    f = lambda v: "n/a" if v is None else v
    return (f"{today.isoformat()} wk{today.isocalendar()[1]:02d} {d['repo']} paper:code {d['paper_ratio']} "
            f"(paper {d['paper']:,} / code {d['code']:,} / infra {d['infra']:,} / tests {d['tests']:,}) "
            f"PRs {d['prs']} lead_time_h {f(d['lead_time_h'])} frustration {f(d['frustration_turn_rate'])} "
            f"silent_yield {f(d['silent_yield_rate'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", action="append", help="repo path (repeatable); default: . plus nested repos to depth 2")
    ap.add_argument("--since", default="7d", help="7d | YYYY-MM-DD (default 7d)")
    ap.add_argument("--json", action="store_true", help="emit a JSON array instead of text lines")
    ap.add_argument("--transcripts", help="dir of Claude Code *.jsonl transcripts (frustration / silent-yield rates)")
    a = ap.parse_args(argv)
    try:
        since = parse_since(a.since).astimezone()
    except ValueError:
        ap.error("--since must be like 7d or YYYY-MM-DD")
    repos = []
    for p in (a.repo or ["."]):
        repos += find_repos(p)
    repos = list(dict.fromkeys(repos))
    rates = transcript_rates(a.transcripts, since) if a.transcripts else (None, None)
    rows = [measure(r, since, os.path.basename(r) or r, rates) for r in repos]
    tot = {"repo": "TOTAL", **{k: sum(r[k] for r in rows) for k in CLASSES}}
    den = tot["code"] + tot["infra"] + tot["tests"]
    tot["paper_ratio"] = "inf" if den == 0 and tot["paper"] else (round(tot["paper"] / den, 2) if den else 0.0)
    tot["prs"] = sum(r["prs"] for r in rows)
    lts = [r["lead_time_h"] for r in rows if r["lead_time_h"] is not None]
    tot["lead_time_h"] = round(statistics.median(lts), 1) if lts else None
    tot["frustration_turn_rate"], tot["silent_yield_rate"] = rates
    rows.append(tot)
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        today = dt.date.today()
        for r in rows:
            print(fmt(r, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
