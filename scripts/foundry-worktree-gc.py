#!/usr/bin/env python3
"""foundry-worktree-gc — the codified cleanup `foundry-work-isolation.sh cleanup` never became
(branch-and-worktree-discipline, AC-BWD-2, v1.16.0).

Sixty-three remote branches accumulated on the plugin repo across one programme: worktrees and
branches are shipped by `/foundry:dispatch` (native `Agent isolation:worktree`) and cut down by
`skills/work-isolation/SKILL.md`'s manual `foundry-work-isolation.sh cleanup <repo> <branch>` --
a step nobody runs -- and a squash merge leaves the remote branch behind even when it does. This
script REPLACES that manual step for the post-merge sweep (the work-isolation skill's cleanup
recipe is now SUPERSEDED by this script; the script itself is left in place, per the charter, not
deleted): list every linked worktree and every local/remote branch matching the release-discipline
glob set, classify each by real git ancestry (never an operator's say-so), and -- ONLY under
`--apply` -- delete exactly the `merged` class.

Classification is a PURE function of three git-plumbing primitives, each invoked as an argv LIST
(never `shell=True`, never a string command):
  * `git for-each-ref`          -- every local/remote branch ref + its tip SHA + committer date.
  * `git merge-base --is-ancestor` -- is a branch's tip contained in `origin/main` (or another
    base)? The primary, offline signal.
  * `git worktree list --porcelain` -- every linked worktree + the branch it has checked out.

`gh pr list --head <branch> --state merged|open --json number,headRefOid` is a SECONDARY,
best-effort signal (AC-BWD-2's own text: "...or whose PR is MERGED per `gh pr list --state merged
--head`") for the case a squash/rebase merge left the branch tip NOT a literal ancestor of
`origin/main` even though its content landed -- and for telling `open-pr` apart from
`unmerged-no-pr` when ancestry alone says "not merged". A row only counts when its `headRefOid`
equals the branch's OWN current tip (round-2 review finding 1): a branch NAME is not unique over a
repo's history (this repo reuses `release/*-repin`, `fix/*`, `docs/*`), so a stale `merged` PR
record for a REUSED name must never make a later, genuinely-unmerged push at that same name look
merged. `gh` unavailable/erroring/tip-mismatched never raises; it only means these two finer
classes degrade toward the conservative `unmerged-no-pr` (never toward `merged` -- a missing or
stale `gh` signal can never manufacture a false-positive deletion candidate).

Four classes, every candidate branch gets exactly one:
  * `protected`       -- the repo's default branch (`main`, by convention) or any name passed via
                         `--protected` (repeatable). Never touched by `--apply`.
  * `merged`           -- tip is an ancestor of the base, OR `gh` reports a MERGED PR for it. The
                         ONLY class `--apply` ever deletes.
  * `open-pr`          -- not merged, but `gh` reports an OPEN PR for it.
  * `unmerged-no-pr`   -- neither of the above (or `gh` was unavailable) -- listed with its age,
                         NEVER deleted, regardless of age.

`--dry-run` (the default -- passing neither flag, or `--dry-run` explicitly, behaves identically)
NEVER deletes anything; it only classifies and prints the JSON summary. `--apply` deletes ONLY the
`merged` class: the linked worktree (`git worktree remove`), the local branch (`git branch -d`
FIRST -- git's own fast-forward/merged check, falling back to `-D` ONLY on that refusal, narrated
in the `force_deleted` field of the JSON summary -- see `apply_deletions`' own docstring), and the
remote branch (`git push origin --delete <branch>`) -- each a separate argv-list subprocess call,
each independently best-effort (one failure does not abort the others).

Refusals (both modes, fail-closed before any classification runs):
  * `--repo` resolves outside the operator's home directory (`os.path.expanduser("~")`) -- this is
    a workspace-cleanup tool, never a general-purpose remote-branch deleter pointed at an arbitrary
    path.
  * the repo's working tree is dirty (`git status --porcelain` reports anything) -- a worktree
    mid-edit is never GC'd out from under an operator.

Usage (round-2 review finding 3: `--dry-run`/`--apply` FIRST, always -- the permission-floor rows
that tier this script are argv PREFIX rules, `...foundry-worktree-gc.py --dry-run:*` /
`...--apply:*`, which only match when the mode flag is the first argument; `--repo` trailing is
what every rule, doc example, and test in this repo now uses):
    foundry-worktree-gc.py --dry-run --repo <dir>     # default-safe; prints the classification
    foundry-worktree-gc.py --apply --repo <dir>       # deletes the `merged` class only

Exit codes: 0 on a completed run (dry-run OR apply, regardless of how many branches classify into
each bucket -- an empty `merged` set is not a failure). 1 on a refusal (bad `--repo`, dirty tree).
Prints exactly one JSON object on stdout.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone

# The release-discipline glob set (AC-BWD-1/-2): every branch shape the discipline's own naming
# convention produces -- atom branches, release branches, hotfixes, and the conventional
# fix/feat/docs prefixes an adopter's own workflow may use alongside them.
GLOB_PATTERNS = ("atom/*", "release/*", "hotfix/*", "fix/*", "feat/*", "docs/*")

_GIT_TIMEOUT_SEC = 30
_GH_TIMEOUT_SEC = 30


class GcRefused(Exception):
    """A fail-closed refusal (bad --repo, dirty tree) -- never a partial run."""


def _run(argv, cwd=None, timeout=_GIT_TIMEOUT_SEC):
    """The ONE subprocess entry point every git/gh call in this module goes through -- an argv
    LIST, never a shell string, never `shell=True` (module docstring: "pure ... no shell")."""
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(argv, 124, "", str(e))


def _git(repo, *args, timeout=_GIT_TIMEOUT_SEC):
    return _run(["git", "-C", repo, *args], timeout=timeout)


# ----------------------------------------------------------------------------------------------- #
# git plumbing primitives
# ----------------------------------------------------------------------------------------------- #


def is_git_repo(repo):
    p = _git(repo, "rev-parse", "--git-dir")
    return p.returncode == 0


def is_dirty(repo):
    """`git status --porcelain` -- ANY output at all (staged, unstaged, or untracked) counts as
    dirty. Fails closed: a status query that itself errors is treated as dirty (never silently
    proceeds against a repo git could not even inspect)."""
    p = _git(repo, "status", "--porcelain")
    if p.returncode != 0:
        return True
    return bool(p.stdout.strip())


def default_branch(repo):
    """`origin/HEAD`'s target short name, falling back to `main` when the symbolic ref is absent
    (a shallow/bare clone, or `origin/HEAD` never set) -- never raises."""
    p = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def list_worktrees(repo):
    """Parses `git worktree list --porcelain` into a list of {"path", "branch", "head", ...}
    dicts. `branch` is the short name (refs/heads/ prefix stripped) or absent for a detached
    worktree. Entries are blank-line separated (git's own porcelain format)."""
    p = _git(repo, "worktree", "list", "--porcelain")
    worktrees = []
    cur = {}
    for line in p.stdout.splitlines():
        if not line.strip():
            if cur:
                worktrees.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "bare":
            cur["bare"] = True
        elif line == "detached":
            cur["detached"] = True
    if cur:
        worktrees.append(cur)
    return worktrees


def _matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def list_branch_refs(repo, patterns=GLOB_PATTERNS, include_names=None):
    """`git for-each-ref` over refs/heads + refs/remotes/origin, filtered to the glob set PLUS any
    name in `include_names` (used for the protected default branch, e.g. `main`, which never
    matches the discipline's own naming glob but must still surface as a `protected` row -- AC-
    BWD-2: "main ... are protected"). Returns (local, remote) dicts of name -> {"sha",
    "committerdate"}. `origin/HEAD` is never a branch candidate and is always excluded."""
    include_names = include_names or set()
    fmt = "%(refname)%09%(objectname)%09%(committerdate:iso-strict)"
    p = _git(repo, "for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes/origin")
    local, remote = {}, {}
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        refname, sha, cdate = parts[0], parts[1], parts[2]
        if refname.startswith("refs/heads/"):
            name, bucket = refname[len("refs/heads/"):], local
        elif refname.startswith("refs/remotes/origin/"):
            name = refname[len("refs/remotes/origin/"):]
            if name == "HEAD":
                continue
            bucket = remote
        else:
            continue
        if not _matches_any(name, patterns) and name not in include_names:
            continue
        bucket[name] = {"sha": sha, "committerdate": cdate}
    return local, remote


def is_ancestor(repo, sha, base):
    """`git merge-base --is-ancestor <sha> <base>` -- True iff `sha` is reachable from `base`.
    Any non-zero exit (including "unknown revision", the base not fetched locally) reads as False,
    never raises -- the conservative direction (never a false `merged`)."""
    p = _git(repo, "merge-base", "--is-ancestor", sha, base)
    return p.returncode == 0


# ----------------------------------------------------------------------------------------------- #
# gh (best-effort secondary signal)
# ----------------------------------------------------------------------------------------------- #


def gh_pr_info(branch, tip_sha):
    """Returns {"state": "merged"|"open", "number": <int|None>} or None. Two separate literal
    calls -- `gh pr list --head <branch> --state merged --json number,headRefOid` then (only if
    that found no TIP-MATCHING row) `--state open` -- mirroring AC-BWD-2's own literal text so a
    test can assert on the exact argv shape.

    Round-2 review finding 1: a branch NAME is not unique over a repo's history (this repo reuses
    `release/*-repin`, `fix/*`, `docs/*`) -- a `merged` PR record for a name that was later reused
    for new, unmerged commits would otherwise make THIS run's unmerged tip look merged and hand
    `--apply` a false-positive deletion candidate. So a row only counts when its `headRefOid`
    equals `tip_sha`, the branch's OWN current tip -- a stale merged-PR record for a reused name
    is filtered out and the loop falls through to the next state/None, never to a false `merged`.
    `gh` missing/erroring/timing out on EITHER call, or every row's `headRefOid` mismatching,
    degrades to None (never raises, never guesses)."""
    for state in ("merged", "open"):
        p = _run(["gh", "pr", "list", "--head", branch, "--state", state,
                  "--json", "number,headRefOid"], timeout=_GH_TIMEOUT_SEC)
        if p.returncode != 0 or not (p.stdout or "").strip():
            continue
        try:
            rows = json.loads(p.stdout)
        except json.JSONDecodeError:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("headRefOid") == tip_sha:
                return {"state": state, "number": row.get("number")}
    return None


# ----------------------------------------------------------------------------------------------- #
# classification -- a PURE function of already-collected data (no I/O below this line)
# ----------------------------------------------------------------------------------------------- #


def classify_branch(name, *, protected_names, ancestor_merged, pr_state):
    """AC-BWD-2's four-class partition. Pure: takes only already-derived booleans/strings, no
    filesystem or subprocess call. `ancestor_merged` (git ancestry) and `pr_state == "merged"`
    (the gh fallback) are EITHER sufficient for `merged` -- ancestry is checked first and is the
    unconditionally-trustworthy signal; `pr_state` only ever adds coverage, never removes it."""
    if name in protected_names:
        return "protected"
    if ancestor_merged or pr_state == "merged":
        return "merged"
    if pr_state == "open":
        return "open-pr"
    return "unmerged-no-pr"


def _age_days(committerdate_iso, *, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(committerdate_iso)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


def classify_repo(repo, *, protected_names=None, use_gh=True, base=None):
    """Orchestrates the primitives above into one row per candidate branch. Returns
    (rows, worktrees). `base` defaults to `origin/<default_branch>`."""
    base = base or f"origin/{default_branch(repo)}"
    protected = set(protected_names or []) | {default_branch(repo)}
    local, remote = list_branch_refs(repo, include_names=protected)
    worktrees = list_worktrees(repo)
    wt_by_branch = {w["branch"]: w["path"] for w in worktrees if w.get("branch")}

    rows = []
    for name in sorted(set(local) | set(remote)):
        l, r = local.get(name), remote.get(name)
        primary = l or r
        ancestor_merged = is_ancestor(repo, primary["sha"], base)
        pr_info = None
        if use_gh and name not in protected and not ancestor_merged:
            pr_info = gh_pr_info(name, primary["sha"])
        pr_state = pr_info["state"] if pr_info else None
        cls = classify_branch(name, protected_names=protected, ancestor_merged=ancestor_merged,
                              pr_state=pr_state)
        rows.append({
            "name": name,
            "class": cls,
            "local": l is not None,
            "remote": r is not None,
            "worktree": wt_by_branch.get(name),
            "committerdate": primary["committerdate"],
            "age_days": _age_days(primary["committerdate"]),
            # set only when `cls == "merged"` was decided via the gh/headRefOid fallback (never
            # via ancestry) -- apply_deletions' -d->-D fallback narrates this PR number.
            "pr_number": pr_info["number"] if (pr_info and pr_info["state"] == "merged") else None,
        })
    return rows, worktrees


# ----------------------------------------------------------------------------------------------- #
# refusals
# ----------------------------------------------------------------------------------------------- #


def _refuse_outside_home(repo):
    home = os.path.realpath(os.path.expanduser("~"))
    real = os.path.realpath(repo)
    if real != home and not real.startswith(home + os.sep):
        raise GcRefused(f"--repo {repo!r} resolves outside the operator's home ({home}); refusing")


def _refuse_dirty(repo):
    if is_dirty(repo):
        raise GcRefused(f"--repo {repo!r} has a dirty working tree; refusing (commit or stash first)")


def _refuse_not_a_repo(repo):
    if not is_git_repo(repo):
        raise GcRefused(f"--repo {repo!r} is not a git repository")


# ----------------------------------------------------------------------------------------------- #
# --apply deletion -- ONLY the `merged` class, never anything else
# ----------------------------------------------------------------------------------------------- #


def apply_deletions(repo, rows):
    """Deletes ONLY rows classified `merged`: the linked worktree (if any), the local branch (if
    any), the remote branch (if any) -- three independent best-effort argv-list subprocess calls
    per row; one failing never aborts the others or the loop. NEVER called under --dry-run (the
    caller only invokes this when args.apply is set).

    Round-2 review finding 2: the local branch delete is `git branch -d` FIRST -- git's own
    fast-forward/merged check, a second line of defense this script's own classification does not
    get to skip. A `-d` refusal is expected and SAFE for a squash/rebase-merged branch (its tip is
    genuinely not an ancestor even though its content landed, which is exactly why `classify_branch`
    accepted the gh/headRefOid-verified `merged` class for it in the first place) -- ONLY THEN does
    this fall back to `-D` (force), and the record narrates why: `force_deleted` carries one
    {"name", "reason"} entry naming the verified PR number when the row's own `pr_number` is set
    (the gh/headRefOid path), or "ancestry-verified" otherwise (defensive; `-d` should never
    actually refuse an ancestor-merged branch, but the fallback is unconditional so a git edge case
    is still narrated rather than silently forced)."""
    removed_worktrees, deleted_local, deleted_remote, force_deleted = [], [], [], []
    for row in rows:
        if row["class"] != "merged":
            continue
        wt = row.get("worktree")
        if wt:
            p = _git(repo, "worktree", "remove", wt)
            if p.returncode == 0:
                removed_worktrees.append(wt)
        if row["local"]:
            p = _git(repo, "branch", "-d", row["name"])
            if p.returncode == 0:
                deleted_local.append(row["name"])
            else:
                p2 = _git(repo, "branch", "-D", row["name"])
                if p2.returncode == 0:
                    deleted_local.append(row["name"])
                    reason = (f"squash-merged, PR #{row['pr_number']} verified by headRefOid"
                              if row.get("pr_number") else "squash-merged, ancestry-verified")
                    force_deleted.append({"name": row["name"], "reason": reason})
        if row["remote"]:
            p = _git(repo, "push", "origin", "--delete", row["name"])
            if p.returncode == 0:
                deleted_remote.append(row["name"])
    return removed_worktrees, deleted_local, deleted_remote, force_deleted


# ----------------------------------------------------------------------------------------------- #
# CLI
# ----------------------------------------------------------------------------------------------- #


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="List + classify (and, under --apply, delete) merged worktrees/branches.",
    )
    ap.add_argument("--repo", default=os.getcwd(), help="the repo to garbage-collect")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="classify only (default)")
    mode.add_argument("--apply", action="store_true", help="delete the merged class")
    ap.add_argument("--protected", action="append", default=[],
                    help="an additional protected branch name (repeatable); the default branch "
                         "is always protected")
    ap.add_argument("--no-gh", action="store_true",
                    help="skip the gh pr list fallback -- ancestry-only classification")
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.repo)
    try:
        _refuse_outside_home(repo)
        _refuse_not_a_repo(repo)
        _refuse_dirty(repo)
    except GcRefused as e:
        print(json.dumps({"status": "refused", "reason": str(e)}))
        return 1

    rows, _worktrees = classify_repo(repo, protected_names=args.protected, use_gh=not args.no_gh)

    removed_worktrees, deleted_local, deleted_remote, force_deleted = [], [], [], []
    if args.apply:
        removed_worktrees, deleted_local, deleted_remote, force_deleted = apply_deletions(repo, rows)

    counts = Counter(r["class"] for r in rows)
    doc = {
        "status": "ok",
        "repo": repo,
        "dry_run": not args.apply,
        "counts": {k: counts.get(k, 0) for k in ("merged", "open-pr", "unmerged-no-pr", "protected")},
        "branches": rows,
        "removed_worktrees": removed_worktrees,
        "deleted_local_branches": deleted_local,
        "deleted_remote_branches": deleted_remote,
        "force_deleted": force_deleted,
    }
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
