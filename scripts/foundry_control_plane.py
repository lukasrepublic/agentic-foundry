#!/usr/bin/env python3
"""foundry_control_plane — the shared bounded ancestor walk (feat-foundry-control-plane-preflight).

Catches the wrong session root: a Claude Code session started INSIDE a repo that some ancestor
`.claude/foundry-project.json` already governs as a hosted repo (`repos{}` entry), or anywhere
below an ancestor control plane at all. `claude plugin install` writes `enabledPlugins` USER-WIDE,
so the plugin (and this check) is reachable from a hosted repo too — pointed at the wrong root,
which is exactly the mistake this module exists to name.

This module is the SINGLE shared implementation both `scripts/foundry-doctor.py` (AC-CPP-1/-2/-3)
and this file's own CLI (AC-CPP-4, invoked by `/foundry:init`) drive — so doctor and init cannot
diverge on what "hosted" means.

The walk (AC-CPP-5) DELIBERATELY crosses ancestor `.git` boundaries and filesystem-mount
boundaries: a hosted repo carries its own `.git`, so a walk that stopped at the nearest repository
root (as `git` itself does) would halt INSIDE the hosted repo and never reach the plane above it —
defeating the whole point. Traversal safety is carried by a 32-level bound, stopping at the
filesystem root, and never following a symlink introduced mid-walk (the starting directory is
canonicalized via `os.path.realpath` exactly once, up front; every ancestor after that is a plain
`os.path.dirname` slice of an already-fully-resolved path, so there is nothing left to "follow").
An unreadable or malformed ancestor manifest is treated as "not a control plane" — skip and
continue upward — never raised.

THIS IS A MISTAKE-CATCHER FOR THE OPERATOR, NOT A FLOOR. `--session-start` (driven from
`foundry-doctor.py`) fails OPEN by contract; the ONLY enforcement is the operator-invoked
`/foundry:doctor` exit code, and this CLI's own exit code when NOT run with `--override`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

MAX_LEVELS = 32


# --------------------------------------------------------------------------------------- #
# path equality — the ONE shared normalization helper (AC-CPP-9)
# --------------------------------------------------------------------------------------- #
def paths_equal(a: str, b: str) -> bool:
    """Two paths name the SAME directory. Normalizes both through `os.path.realpath` (which
    collapses a trailing slash, a redundant separator, and a `.`/`..` segment, and resolves
    symlinks) then `os.path.normcase` (a no-op on POSIX; case-folds on Windows/macOS's default
    case-insensitive filesystems) — never a hand-rolled string comparison per call site."""
    return _canon(a) == _canon(b)


def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


# --------------------------------------------------------------------------------------- #
# manifest reads — malformed/unreadable is "not a control plane", never raises
# --------------------------------------------------------------------------------------- #
def _manifest_path(dirpath: str) -> str:
    return os.path.join(dirpath, ".claude", "foundry-project.json")


def _read_manifest(dirpath: str):
    """Read `<dirpath>/.claude/foundry-project.json`. Returns the parsed dict, or `None` if
    absent, unreadable, or malformed JSON (AC-CPP-5: skip and continue, never raise)."""
    path = _manifest_path(dirpath)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    return doc


# --------------------------------------------------------------------------------------- #
# the bounded ancestor walk (AC-CPP-5)
# --------------------------------------------------------------------------------------- #
def walk_ancestors(start_dir: str, max_levels: int = MAX_LEVELS):
    """Yield each ancestor directory of `start_dir`, nearest first, starting at its PARENT
    (never `start_dir` itself — a correctly-rooted session must stay green). `start_dir` is
    canonicalized via `os.path.realpath` exactly once before the walk begins, so no symlink
    encountered while walking upward is ever separately followed. Stops at the filesystem root
    or after `max_levels` ancestors, whichever comes first. Deliberately does NOT stop at a
    `.git` boundary or a filesystem-mount boundary (AC-CPP-5)."""
    current = os.path.realpath(start_dir)
    for _ in range(max_levels):
        parent = os.path.dirname(current)
        if parent == current:
            return
        yield parent
        current = parent


# --------------------------------------------------------------------------------------- #
# AC-CPP-2 / AC-CPP-3 / AC-CPP-3b — the ancestor finding
# --------------------------------------------------------------------------------------- #
def _resolve_repo_record_path(ancestor_dir: str, record) -> "str | None":
    if not isinstance(record, dict):
        return None
    rel = record.get("path")
    if not rel or not isinstance(rel, str):
        return None
    return rel if os.path.isabs(rel) else os.path.join(ancestor_dir, rel)


def _named_hosted_repo(ancestor_dir: str, manifest: dict, start_dir: str) -> "str | None":
    """If `manifest`'s `repos{}` (declared at `ancestor_dir`) names `start_dir` as a hosted
    repo, return the offending key. Else `None`."""
    repos = manifest.get("repos")
    if not isinstance(repos, dict):
        return None
    for key, record in repos.items():
        resolved = _resolve_repo_record_path(ancestor_dir, record)
        if resolved is not None and paths_equal(resolved, start_dir):
            return key
    return None


def _is_linked_worktree(dir_path: str) -> bool:
    """A LINKED git worktree (`git worktree add`) carries a `.git` that is a regular FILE (a
    `gitdir: <path>` pointer), never a directory. This factory's own worker-dispatch machinery
    creates exactly this shape to isolate a build inside a repo's working tree without disturbing
    the primary checkout — AC-CPP-6(d) requires it stay green unconditionally, since it is a
    deliberate git-native secondary checkout, never the operator mistake this atom targets."""
    p = os.path.join(dir_path, ".git")
    return os.path.isfile(p)


def find_ancestor_control_plane(start_dir: str, max_levels: int = MAX_LEVELS):
    """Walk ancestors of `start_dir` (AC-CPP-5). Return `None` if none carries a manifest (the
    single-repo default / a standalone repo / a CI checkout — AC-CPP-6). Else return a finding
    dict for the NEAREST ancestor that carries one:

      {"kind": "hosted", "ancestor": <dir>, "key": <repos{} key>}   # AC-CPP-2 — start_dir IS
                                                                     # that ancestor's named repo
      {"kind": "subdir", "ancestor": <dir>}                         # AC-CPP-3 — start_dir is
                                                                     # merely below that ancestor

    AC-CPP-3b: when BOTH conditions hold at the SAME (nearest) ancestor — the primary case, since
    a named hosted repo is necessarily also a subdirectory of the plane — exactly one finding is
    returned and it is the "hosted" one, whose remedy is the more specific.

    Per the spec's own out-of-scope clarification, a plane nested inside another plane is NOT
    interpreted further: the walk reports the NEAREST ancestor plane and stops there.

    A LINKED git worktree (`.git` a file, not a directory — AC-CPP-6(d)) is unconditionally
    exempt: it is always `None` regardless of what any ancestor manifest says.
    """
    real_start = os.path.realpath(start_dir)
    if _is_linked_worktree(real_start):
        return None
    for ancestor in walk_ancestors(real_start, max_levels=max_levels):
        manifest = _read_manifest(ancestor)
        if manifest is None:
            continue
        key = _named_hosted_repo(ancestor, manifest, real_start)
        if key is not None:
            return {"kind": "hosted", "ancestor": ancestor, "key": key}
        return {"kind": "subdir", "ancestor": ancestor}
    return None


def format_ancestor_finding(finding: dict, start_dir: str) -> str:
    ancestor = finding["ancestor"]
    if finding["kind"] == "hosted":
        return (
            f"session rooted in a HOSTED repo: {start_dir!r} is named by repos.{finding['key']!r} "
            f"in {ancestor!r}'s .claude/foundry-project.json — remedy: start the session at "
            f"{ancestor!r} instead."
        )
    return (
        f"session rooted BELOW a control plane: {start_dir!r} is a subdirectory of the control "
        f"plane at {ancestor!r} (carries .claude/foundry-project.json) — remedy: start the "
        f"session at {ancestor!r} instead."
    )


# --------------------------------------------------------------------------------------- #
# AC-CPP-1 — dangling repos{} entries in the CURRENT project's OWN manifest
# --------------------------------------------------------------------------------------- #
def dangling_repo_paths(project_dir: str) -> dict:
    """Return `{key: resolved_path}` for every `repos{}` entry in `project_dir`'s OWN
    `.claude/foundry-project.json` whose resolved `path` does not exist as a directory. A
    manifest with no `repos{}`, or one carrying only the `workspace` self-entry (`path: "."`),
    naturally returns `{}` (AC-CPP-1)."""
    manifest = _read_manifest(project_dir)
    if manifest is None:
        return {}
    repos = manifest.get("repos")
    if not isinstance(repos, dict):
        return {}
    bad = {}
    for key, record in repos.items():
        resolved = _resolve_repo_record_path(project_dir, record)
        if resolved is None:
            continue
        if not os.path.isdir(resolved):
            bad[key] = resolved
    return bad


def format_dangling(key: str, resolved_path: str) -> str:
    return (
        f"repos.{key}.path does not resolve to an existing directory: {resolved_path!r} — "
        f"remedy: fix the path in .claude/foundry-project.json, or clone the repo there."
    )


# --------------------------------------------------------------------------------------- #
# CLI (AC-CPP-4) — the scripted, independently invocable preflight `/foundry:init` runs
# --------------------------------------------------------------------------------------- #
def _project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="foundry_control_plane — refuse (or warn) when the session is rooted in a "
                     "hosted repo, or below one, per an ancestor .claude/foundry-project.json."
    )
    ap.add_argument("start_dir", nargs="?", default=None,
                     help="directory to check (default: $CLAUDE_PROJECT_DIR or cwd)")
    ap.add_argument("--override", action="store_true",
                     help="exit 0 even on a finding; the finding is still printed — for a "
                          "deliberately independent hosted-repo adopter (AC-CPP-4)")
    args = ap.parse_args(argv)

    start = args.start_dir or _project_dir()
    finding = find_ancestor_control_plane(start)
    if finding is None:
        print(f"control-plane preflight: ok — no ancestor control plane governs {start!r}")
        return 0

    print(f"control-plane preflight: {format_ancestor_finding(finding, start)}")
    if args.override:
        print("control-plane preflight: --override given — continuing despite the finding above.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
