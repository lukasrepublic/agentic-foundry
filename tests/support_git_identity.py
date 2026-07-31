"""tests/support_git_identity.py — the `check()` predicate ported from the (now-deleted, BTB
Phase 3) `scripts/foundry_checks/commit-identity-isolation.py`.

A pure git-config reader (never production runtime code — nothing in the shipped plugin imports
this at runtime; the REAL commit-identity wiring is `scripts/foundry-bootstrap.sh`'s writer side,
unchanged and untouched by this move), so it lives alongside the tests that are its only consumer.
"""
from __future__ import annotations

import os
import re
import subprocess

_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GH_IDENTITY_RELPATH = os.path.join(".claude", "gh-identity")


def _config_home():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "git")


def _normalize_slug(raw):
    if raw is None:
        return None
    slug = "".join(raw.split())
    if not slug or slug in (".", ".."):
        return None
    if not _SLUG_RE.match(slug):
        return None
    return slug


def _canonical(path):
    return os.path.realpath(path)


def _git_config_get(cwd_or_file, key, is_file=False):
    if is_file:
        cmd = ["git", "config", "--file", cwd_or_file, key]
    else:
        cmd = ["git", "-C", cwd_or_file, "config", key]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    val = out.stdout.rstrip("\n")
    return val if val != "" else None


def check(root=None):
    project = root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    canon_project = _canonical(project)

    marker = os.path.join(canon_project, GH_IDENTITY_RELPATH)
    if not os.path.isfile(marker):
        return True, "commit-identity isolation not adopted (no .claude/gh-identity) — dormant GREEN no-op"

    try:
        raw_slug = open(marker, encoding="utf-8").read()
    except Exception as e:
        return False, f"DOCTOR-RED: .claude/gh-identity unreadable at {marker}: {e}"

    slug = _normalize_slug(raw_slug)
    if slug is None:
        return False, (f"DOCTOR-RED: malformed account slug in {marker} "
                       f"(need ^[A-Za-z0-9._-]+$, not '.'/'..') — refusing to interpolate a path")

    inc = os.path.join(_config_home(), f"identity-{slug}")
    if not os.path.isfile(inc):
        return False, (f"DOCTOR-RED: per-account include file ABSENT at {inc} "
                       f"(isolation declared for '{slug}' but the bootstrap declared-identity record is missing)")

    declared = _git_config_get(inc, "user.email", is_file=True)
    if declared is None:
        return False, f"DOCTOR-RED: include file {inc} carries no [user] email (declared identity absent)"

    effective = _git_config_get(canon_project, "user.email", is_file=False)
    if effective is None:
        return False, (f"DOCTOR-RED: project {canon_project} resolves NO effective user.email — the includeIf "
                       f"did not fire (path mismatch?) / no identity configured (useConfigOnly will fail commits)")

    if effective != declared:
        return False, (f"DOCTOR-RED: project effective user.email {effective!r} != DECLARED {declared!r} "
                       f"(slug '{slug}') — a stray --local override, a non-matching includeIf, or an "
                       f"inherited global default is shadowing the declared identity")

    return True, (f"commit-identity isolation verified: project '{slug}' resolves user.email {effective!r} "
                  f"= the declared include-file value (includeIf wiring + useConfigOnly intact)")
