#!/usr/bin/env python3
"""scripts/foundry_repo_fleet.py — the control plane's governed-repo fleet verbs
(feat-foundry-workspace-repo-verbs, AC-WRV-1..12).

Four verbs over `scripts/foundry_repo_registry.py`'s read-only classification:
  sync     — idempotent reconcile: clone the not-cloned rows that declare a remote, fetch the
             present checkouts whose origin is exactly `match`, report everything else untouched.
  status   — one honest line per `repos.<key>` entry: present / origin / branch / ahead-behind / dirty.
  foreach  — shell-free argv fan-out over the PRESENT repos, fail-collecting, child output captured.
  validate — the manifest <-> reality <-> gitignore round-trip in BOTH directions, including the
             reverse direction (`undeclared-checkout`) the registry atom deferred by name.

CLONE AND FETCH ARE THE ENTIRE MUTATION VOCABULARY. No checkout, reset, merge, rebase, pull, push,
clean, stash, branch, remote, submodule, gc, and no --force/-f/--hard/--force-sync exists in this
module's vocabulary — an existing tree is never rewritten; drift is SURFACED, NEVER FIXED (the
id-drift posture). Every git invocation this tool makes funnels through the single choke point
`_git()`, which asserts its verb is drawn from the closed read-only-or-mutating set below, prepends
the fixed, non-manifest-derived HARDENING SET as leading `-c` overrides, and runs under the
subtractive SINK ENVIRONMENT — regardless of whether the invocation is network-capable (AC-WRV-11).

THE ADMITTED REMOTE FORM PREDICATE IS LOADED, NEVER RE-IMPLEMENTED: AC-WRV-3's boundary
re-validation decides remote form with `url_is_allowed_form` / `_is_local_path_escape_hatch`,
loaded from `scripts/foundry-prepublication-leak-scan.py` via `importlib.util.spec_from_file_
location`, resolved from THIS module's own directory (never from `--root`, which is untrusted
input). That module and its suites are contract-denied here and consumed unmodified.

THE SINGLE REDACTION SINK IS INHERITED, NOT RE-IMPLEMENTED: every string this module emits — to
stdout, stderr, or `--json`, including the uncaught-exception path — passes through
`scripts/foundry_repo_registry.py`'s own `sanitize()` function object (imported, never forked).

NO GATE IN THIS PLUGIN CONSUMES ANY VERB'S EXIT CODE. Exit codes follow the shipped advisory
tri-state (`terraform plan -detailed-exitcode` convention, already shipped in
`scripts/foundry-config.py` and `scripts/foundry_repo_registry.py`): 0 clean / 2 findings / 1 when
the manifest is absent, unreadable or unparseable.

NO PROMISE THAT A CLONED TREE IS INERT: a cloned repository's content is untrusted, and inside a
Claude Code workspace root its CLAUDE.md, .claude/** and .mcp.json become discoverable
configuration for sessions rooted there (AC-WRV-12). This tool clones and fetches; it does not
sandbox, review, or vouch for what it retrieves.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback

# R8 (security review, PR #59): guarded + idempotent (`append`, not `insert(0, ...)`) so this
# module's own directory is added to sys.path AT MOST once and never shadows an entry a caller
# (or an earlier-imported sibling module) already placed ahead of it — an unconditional
# `insert(0, ...)` re-run (e.g. a caller re-importing this module's directory bootstrap logic, or
# module reload under a test harness) would otherwise grow sys.path unbounded and could shadow a
# same-named module the caller intentionally put first.
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.append(_HERE_DIR)
import foundry_repo_registry as REG  # noqa: E402 — the frozen classifier/envelope/sink, consumed

# ---------------------------------------------------------------------------------------------
# Exit codes — the shipped advisory tri-state (AC-WRV-7).
EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS = 0, 1, 2

NO_GATE_STATEMENT = "No gate in this plugin consumes any verb's exit code; every exit code here is advisory."
INERTNESS_STATEMENT = (
    "A cloned repository's content is untrusted: this tool makes no promise that a cloned tree is "
    "inert. Inside a Claude Code workspace root, a cloned repo's CLAUDE.md, .claude/** and .mcp.json "
    "become discoverable configuration for sessions rooted there."
)

# ---------------------------------------------------------------------------------------------
# AC-WRV-1: the closed git-verb set. Every subprocess this module spawns that is a `git`
# invocation funnels through `_git()`, which asserts args[0] is one of these — defensively, never
# reachable via manifest input, so a future edit cannot silently widen the vocabulary.
_ALLOWED_GIT_VERBS = frozenset(
    ["clone", "fetch", "config", "rev-parse", "rev-list", "status", "check-ignore", "ls-files"]
)

# Terminology (normative): the hardening set, fixed order, non-manifest-derived.
_HARDENING_SET = [
    "-c", "credential.helper=",
    "-c", "core.askPass=",
    "-c", "core.fsmonitor=",
    "-c", "core.sshCommand=ssh -o BatchMode=yes",
    "-c", "protocol.ext.allow=never",
    "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always",
    "-c", "protocol.ssh.allow=always",
    "-c", "protocol.file.allow=always",
    "-c", "fetch.recurseSubmodules=no",
    "-c", "submodule.recurse=false",
]

# Terminology (normative): the sink environment — subtractive, never a rebuilt allow-list.
SINK_ENV_REMOVED_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
)

DEFAULT_FOREACH_TIMEOUT_SECONDS = 30.0
DEFAULT_REVERSE_SCAN_MAX_DEPTH = 8
# R6 (security review, PR #59): sync's --timeout previously defaulted to None -- unbounded network
# egress on every clone/fetch. A declared default bounds it; --timeout still overrides. This is a
# CLI-default only: the AC-WRV-10-pinned `reconcile(..., timeout: float | None = None)` signature
# and default are unchanged, since a caller (the Wave-3 wizard included) may legitimately want an
# unbounded reconcile and the pinned signature is not this residual's to alter.
DEFAULT_SYNC_TIMEOUT_SECONDS = 600.0

# AC-WRV-6: the closed reverse-scan exclusion set — exactly these three names, each for a distinct
# reason (see the SKILL/doc prose): `.git` is the checkout's own marker, `.worktrees` is
# `scripts/foundry-wt`'s own dispatch-worktree convention (already-governed repos by construction),
# `node_modules` is a package-manager tree, never a governed repo.
REVERSE_SCAN_EXCLUDED_NAMES = frozenset({".git", "node_modules", ".worktrees"})

# ---------------------------------------------------------------------------------------------
# AC-WRV-8: the ONE inherited sanitizing sink — the registry module's own function OBJECT, never
# re-implemented or wrapped in a way that would break identity.
SANITIZE = REG.sanitize


# ---------------------------------------------------------------------------------------------
# AC-WRV-3: the admitted-remote-form predicate is LOADED from the leak-scan module, resolved from
# THIS module's own directory — never re-implemented, never resolved from an untrusted `--root`.
_LEAK_SCAN_MODULE_CACHE = []


def _load_leak_scan_module():
    if _LEAK_SCAN_MODULE_CACHE:
        return _LEAK_SCAN_MODULE_CACHE[0]
    import importlib.util

    this_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(this_dir, "foundry-prepublication-leak-scan.py")
    spec = importlib.util.spec_from_file_location("foundry_repo_fleet_leak_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LEAK_SCAN_MODULE_CACHE.append(mod)
    return mod


_DASH_LEADING_RE = re.compile(r"^-")
_C0_C1_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def remote_is_admitted(remote):
    """AC-WRV-3: an admitted remote form — re-derived from the value itself, independent of any
    classification a row carries. Never `-`-leading, never carrying a C0/C1 control character, and
    accepted by the LOADED leak-scan predicate `url_is_allowed_form(v) or
    _is_local_path_escape_hatch(v)`."""
    if not remote or not isinstance(remote, str):
        return False
    if _DASH_LEADING_RE.match(remote):
        return False
    if _C0_C1_RE.search(remote):
        return False
    leak_scan = _load_leak_scan_module()
    return bool(leak_scan.url_is_allowed_form(remote) or leak_scan._is_local_path_escape_hatch(remote))


def key_is_hostile(key):
    """AC-WRV-3/-10: a hostile `repos.<key>` key — leading `-` or a C0/C1 control character. Never
    alters an accept/refuse decision and never reaches an argv option position; used only to decide
    whether a key needs the sink at emission (every key passes through the sink regardless)."""
    if not isinstance(key, str) or not key:
        return False
    return bool(_DASH_LEADING_RE.match(key) or _C0_C1_RE.search(key))


# ---------------------------------------------------------------------------------------------
# The single subprocess-creation choke point (a test seam: monkeypatch this, not subprocess.run).
def _spawn(argv, cwd=None, env=None, timeout=None, input_bytes=None):
    return subprocess.run(
        argv, cwd=cwd, shell=False, env=env, timeout=timeout, input=input_bytes, capture_output=True
    )


class _FailedInvocation:
    """A CompletedProcess-shaped stand-in for a git invocation that could not even be spawned
    (git absent from PATH) — callers that only read .returncode/.stdout/.stderr need not special-
    case an OSError at every call site."""

    def __init__(self, returncode=127, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _sink_env(base_env=None):
    """Terminology: the sink environment — the ambient environment with GIT_TERMINAL_PROMPT=0 set
    and every member of SINK_ENV_REMOVED_VARS removed, individually, by subtraction."""
    env = dict(base_env if base_env is not None else os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    for var in SINK_ENV_REMOVED_VARS:
        env.pop(var, None)
    # R1 (security review, PR #59): an additional, explicit over-removal beyond the
    # spec-pinned SINK_ENV_REMOVED_VARS tuple (byte-identical to the leak-scan sink's own
    # `_LS_REMOTE_SINK_ENV_REMOVED_VARS` and NOT extended here). An inherited GIT_ALLOW_PROTOCOL
    # could narrow git's own protocol allow-list out from under this module's `-c
    # protocol.*.allow=` hardening overrides; removing it is strictly additive safety and is not
    # itself a Terminology-pinned member. Pending a spec Terminology amendment to fold it into the
    # named sink-environment tuple.
    env.pop("GIT_ALLOW_PROTOCOL", None)
    return env


def _git(args, cwd, timeout=None, env=None):
    """AC-WRV-1/-11: the SINGLE git-invocation choke point. `args[0]` MUST be one of the closed
    verbs (asserted defensively). Prepends the hardening set; runs under the sink environment.
    Raises OSError/subprocess.TimeoutExpired to the caller — callers that need graceful
    degradation (status/validate oracles) use `_git_safe`; callers that need to distinguish
    spawn-failed/timeout as a named row result (clone/fetch) catch these themselves."""
    assert args and args[0] in _ALLOWED_GIT_VERBS, "git verb outside the closed set: %r" % (args,)
    argv = ["git"] + list(_HARDENING_SET) + list(args)
    use_env = env if env is not None else _sink_env()
    return _spawn(argv, cwd=cwd, env=use_env, timeout=timeout)


def _git_safe(args, cwd, timeout=None):
    """Same choke point, degrading OSError/TimeoutExpired to a failed-invocation stand-in — for
    the read-only oracles (status/validate) where "git is unavailable" must render `unknown`,
    never crash the verb."""
    try:
        return _git(args, cwd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _FailedInvocation(127, b"", str(e).encode("utf-8", "replace"))


# ---------------------------------------------------------------------------------------------
# Path helpers — mirror scripts/foundry_repo_registry.py's own (small, non-security-predicate)
# lexical/physical resolution so this module never trusts a row's own claimed `resolved_path`.
def _normalize_lexical(declared_path):
    p = (declared_path or "").strip()
    if p in ("", "."):
        return "."
    return os.path.normpath(p)


def _physical_path_for(root_physical, declared_path):
    lexical = _normalize_lexical(declared_path)
    abs_lexical = root_physical if lexical == "." else os.path.join(root_physical, lexical)
    return abs_lexical


def _is_within(physical_path, root_physical):
    if physical_path == root_physical:
        return True
    return physical_path.startswith(root_physical.rstrip(os.sep) + os.sep)


def _physically_resolve_nearest_existing(path):
    """The target's own physical resolution if it exists, else that of its nearest EXISTING
    physical ancestor (AC-WRV-3) — so a not-yet-created clone target is still confinement-checked
    via the first real directory above it."""
    p = os.path.abspath(path)
    while True:
        if os.path.exists(p):
            return os.path.realpath(p)
        parent = os.path.dirname(p)
        if parent == p:
            return os.path.realpath(p)
        p = parent


# ---------------------------------------------------------------------------------------------
# AC-WRV-10: the reconcile seam. Pure function of (root, rows); no argv parsing, no stream writes.
_FINDING_PATH_STATUS = frozenset(["not-cloned", "dangling", "outside-workspace"])
_FINDING_GITIGNORE = frozenset(["unanchored", "unpaired", "tracked"])
_FINDING_ORIGIN = frozenset(["mismatch", "not-a-checkout"])


def _row_is_finding(row):
    """Mirrors scripts/foundry_repo_registry.py's own `_row_is_finding` (consumed via
    REG._row_is_finding when the row carries the full registry shape) but tolerant of a
    fabricated/partial row's missing fields, for reconcile's hostile-input safety."""
    try:
        return REG._row_is_finding(row)
    except KeyError:
        return (
            row.get("path_status") in _FINDING_PATH_STATUS
            or row.get("gitignore") in _FINDING_GITIGNORE
            or row.get("origin") in _FINDING_ORIGIN
        )


def _reconcile_row(key, path, remote, action, result, finding, detail):
    return {
        "key": key,
        "path": path,
        "remote": remote,
        "action": action,
        "result": result,
        "finding": bool(finding),
        "detail": detail,
    }


def _process_reconcile_row(root_physical, row, timeout):
    key = row.get("key") if isinstance(row, dict) else None
    declared_path = row.get("path") if isinstance(row, dict) else None
    remote = (row.get("declared_remote") if isinstance(row, dict) else None) or (
        row.get("remote") if isinstance(row, dict) else None
    )
    path_status = row.get("path_status") if isinstance(row, dict) else None
    origin = row.get("origin") if isinstance(row, dict) else None

    abs_lexical = _physical_path_for(root_physical, declared_path)

    if path_status == "not-cloned":
        if not remote:
            return _reconcile_row(key, declared_path, remote, "skip", "n/a", True,
                                   "not-cloned with no declared remote — nothing to clone")
        if not remote_is_admitted(remote):
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: remote is not an admitted form")
        if os.path.lexists(abs_lexical):
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: clone target already exists")
        resolved_ancestor = _physically_resolve_nearest_existing(abs_lexical)
        if not _is_within(resolved_ancestor, root_physical):
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: target path escapes the physical workspace root")
        argv = ["clone", "--no-recurse-submodules", "--", remote, abs_lexical]
        try:
            p = _git(argv, cwd=root_physical, timeout=timeout)
        except subprocess.TimeoutExpired:
            return _reconcile_row(key, declared_path, remote, "clone", "timeout", True, "clone timed out")
        except OSError as e:
            return _reconcile_row(key, declared_path, remote, "clone", "spawn-failed", True,
                                   "could not spawn git: %s" % e)
        ok = p.returncode == 0
        detail = "cloned" if ok else p.stderr.decode("utf-8", "replace").strip()[-2000:]
        return _reconcile_row(key, declared_path, remote, "clone", "ok" if ok else "failed", not ok, detail)

    if path_status == "present" and origin == "match":
        if not remote or not remote_is_admitted(remote):
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: declared remote is not an admitted form")
        if not os.path.exists(abs_lexical):
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: path no longer present")
        physical_path = os.path.realpath(abs_lexical)
        if not _is_within(physical_path, root_physical):
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: resolved path escapes the physical workspace root")
        git_marker = os.path.join(physical_path, ".git")
        if not (os.path.isdir(git_marker) or os.path.isfile(git_marker)):
            # R3 (security review, PR #59): a row can claim present/match without the path
            # actually being a git checkout (a worktree's `.git` is a FILE, not a dir — both
            # admitted). Re-derived here independently of the row's own classification, before
            # any git command is spawned for it.
            return _reconcile_row(key, declared_path, remote, "refuse", "n/a", True,
                                   "refused: resolved path is not a git checkout (no .git)")
        argv = ["fetch", "--no-recurse-submodules", "--", remote]
        try:
            p = _git(argv, cwd=physical_path, timeout=timeout)
        except subprocess.TimeoutExpired:
            return _reconcile_row(key, declared_path, remote, "fetch", "timeout", True, "fetch timed out")
        except OSError as e:
            return _reconcile_row(key, declared_path, remote, "fetch", "spawn-failed", True,
                                   "could not spawn git: %s" % e)
        ok = p.returncode == 0
        detail = "fetched" if ok else p.stderr.decode("utf-8", "replace").strip()[-2000:]
        return _reconcile_row(key, declared_path, remote, "fetch", "ok" if ok else "failed", not ok, detail)

    # AC-WRV-2: every other row — dangling, outside-workspace, a present-but-not-a-checkout row,
    # and any present row whose origin is not exactly `match` (mismatch/undeclared/not-a-checkout).
    # NO git command at all; reported skipped, counted a finding where the sibling counts one.
    finding = _row_is_finding(row) if isinstance(row, dict) else True
    return _reconcile_row(
        key, declared_path, remote, "skip", "n/a", finding,
        "no action: path_status=%r origin=%r" % (path_status, origin),
    )


def reconcile(root: str, rows: list, *, timeout: float | None = None) -> dict:
    """AC-WRV-10: the ONE reconcile seam AC-WRV-2/-3 realize — the Wave-3 wizard's import target.

    Performs NO argv parsing and writes to NO stream. Physically resolves `root` itself (so no
    caller, including a lexically-spoofed path, can drive it against the wrong directory), refuses
    with a degraded envelope (no git command at all) when no manifest exists at that physical root,
    and re-validates EVERY row's remote/path per AC-WRV-3 regardless of what the row itself claims
    — a hostile fabricated row is refused exactly like a genuine one would be.

    Returns the {degraded, degraded_reason, rows} envelope; every row's values are UNSANITIZED
    (sanitizing would corrupt data a caller must act on). Every consumer that RENDERS these values
    — including the Wave-3 wizard — MUST render them through `SANITIZE` (AC-WRV-8's sink) first;
    this callable itself never writes to a stream.
    """
    root_physical = os.path.realpath(root)
    manifest_path = os.path.join(root_physical, ".claude", "foundry-project.json")
    if not os.path.isfile(manifest_path):
        return {
            "degraded": True,
            "degraded_reason": "no manifest at physical root %s" % root_physical,
            "rows": [],
        }

    out_rows = []
    for row in rows or []:
        try:
            out_rows.append(_process_reconcile_row(root_physical, row, timeout))
        except Exception as e:  # never let one hostile/malformed row crash the whole reconcile
            key = row.get("key") if isinstance(row, dict) else None
            path = row.get("path") if isinstance(row, dict) else None
            remote = (row.get("declared_remote") if isinstance(row, dict) else None) or (
                row.get("remote") if isinstance(row, dict) else None
            )
            out_rows.append(_reconcile_row(
                key, path, remote, "refuse", "n/a", True,
                "refused: unexpected error processing row: %s" % e,
            ))
    return {"degraded": False, "degraded_reason": None, "rows": out_rows}


# ---------------------------------------------------------------------------------------------
# `status`
def _current_branch(physical_path):
    r = _git_safe(["rev-parse", "--abbrev-ref", "HEAD"], cwd=physical_path)
    if r.returncode != 0:
        return "unknown"
    val = r.stdout.decode("utf-8", "replace").strip()
    if not val:
        return "unknown"
    return "detached" if val == "HEAD" else val


def _ahead_behind(physical_path, branch):
    if branch in ("unknown",):
        return "unknown"
    if branch == "detached":
        return "no-upstream"
    up = _git_safe(["rev-parse", "--abbrev-ref", "%s@{upstream}" % branch], cwd=physical_path)
    if up.returncode != 0:
        return "no-upstream"
    upstream_ref = up.stdout.decode("utf-8", "replace").strip()
    if not upstream_ref:
        return "no-upstream"
    # R4 (security review, PR #59): --end-of-options guards the revision-range positional so it
    # cannot be mistaken for a flag by `git rev-list`; verified locally (git 2.43.0) to leave
    # `--left-right --count` output byte-identical. The sibling `git rev-parse --abbrev-ref
    # <branch>@{upstream}` call above deliberately does NOT get this treatment: verified locally
    # that `git rev-parse --abbrev-ref --end-of-options <ref>` echoes a spurious
    # "--end-of-options" line ahead of the resolved ref in this git's non-`--verify` rev-parse
    # mode, corrupting `upstream_ref` — a functional rejection, not a syntax one, so that call is
    # left unguarded and recorded as a residual rather than "fixed" incorrectly.
    r = _git_safe(
        ["rev-list", "--left-right", "--count", "--end-of-options", "%s...%s" % (branch, upstream_ref)],
        cwd=physical_path,
    )
    if r.returncode != 0:
        return "unknown"
    parts = r.stdout.decode("utf-8", "replace").split()
    if len(parts) != 2:
        return "unknown"
    return "%s/%s" % (parts[0], parts[1])


def _dirty(physical_path):
    r = _git_safe(["status", "--porcelain"], cwd=physical_path)
    if r.returncode != 0:
        return "unknown"
    return bool(r.stdout.strip())


def _status_row(row):
    key = row["key"]
    present = row["path_status"] == "present"
    origin = row["origin"]
    if not present:
        return {"key": key, "present": False, "origin": origin, "branch": "unknown",
                "ahead_behind": "unknown", "dirty": "unknown"}, True
    physical_path = row["resolved_path"]
    branch = _current_branch(physical_path)
    ahead_behind = _ahead_behind(physical_path, branch)
    dirty = _dirty(physical_path)
    unknown_seen = branch == "unknown" or ahead_behind == "unknown" or dirty == "unknown"
    return {"key": key, "present": True, "origin": origin, "branch": branch,
            "ahead_behind": ahead_behind, "dirty": dirty}, unknown_seen


def status(root):
    """Returns (degraded, degraded_reason, rows, exit_code). Raises REG.ManifestError when the
    manifest is absent/unreadable/unparseable — callers decide the exit code (EXIT_ERROR)."""
    outcome, degraded, degraded_reason, reg_rows = REG.build_report(root)
    rows = []
    any_unknown = degraded
    for row in reg_rows:
        r, unknown = _status_row(row)
        rows.append(r)
        any_unknown = any_unknown or unknown
    reason = degraded_reason if degraded else ("one or more status oracles unavailable" if any_unknown else None)
    return any_unknown, reason, rows, (EXIT_FINDINGS if any_unknown or outcome == "no-repos" else EXIT_CLEAN)


# ---------------------------------------------------------------------------------------------
# `foreach`
def _sanitize_captured(data):
    """AC-WRV-5: captured bytes -> decode -> split on U+000A -> each line through SANITIZE ->
    re-join on U+000A. Line structure (count and order) is preserved; no line's content can
    repaint the terminal."""
    text = (data or b"").decode("utf-8", "replace")
    lines = text.split("\n")
    return "\n".join(SANITIZE(line) for line in lines)


def foreach(root, command_argv, *, timeout=None):
    """Returns (degraded, degraded_reason, rows, exit_code). `rows` values are UNSANITIZED —
    callers render through SANITIZE. Raises REG.ManifestError on an absent/unreadable manifest."""
    if timeout is None:
        timeout = DEFAULT_FOREACH_TIMEOUT_SECONDS
    outcome, degraded, degraded_reason, reg_rows = REG.build_report(root)
    rows = []
    for row in reg_rows:
        if row["path_status"] != "present":
            continue
        physical_path = row["resolved_path"]
        child_env = dict(os.environ)
        child_env.pop("GIT_DIR", None)
        child_env.pop("GIT_WORK_TREE", None)
        start = time.monotonic()
        try:
            p = _spawn(list(command_argv), cwd=physical_path, env=child_env, timeout=timeout)
        except subprocess.TimeoutExpired:
            rows.append({
                "key": row["key"], "path": row["path"], "result": "timeout", "finding": True,
                "returncode": None, "stdout": "", "stderr": "",
                "detail": "child exceeded the %.1fs timeout" % timeout,
                "duration_s": round(time.monotonic() - start, 3),
            })
            continue
        except OSError as e:
            rows.append({
                "key": row["key"], "path": row["path"], "result": "spawn-failed", "finding": True,
                "returncode": None, "stdout": "", "stderr": "",
                "detail": "could not spawn child: %s" % e,
                "duration_s": round(time.monotonic() - start, 3),
            })
            continue
        ok = p.returncode == 0
        rows.append({
            "key": row["key"], "path": row["path"], "result": "ok" if ok else "failed", "finding": not ok,
            "returncode": p.returncode,
            "stdout": _sanitize_captured(p.stdout),
            "stderr": _sanitize_captured(p.stderr),
            "detail": "exit %d" % p.returncode,
            "duration_s": round(time.monotonic() - start, 3),
        })
    any_finding = degraded or any(r["finding"] for r in rows)
    return degraded, degraded_reason, rows, (EXIT_FINDINGS if any_finding else EXIT_CLEAN)


# ---------------------------------------------------------------------------------------------
# `validate`
def _forward_validate_row(row):
    r = dict(row)
    r["kind"] = "forward"
    r["finding"] = _row_is_finding(row)
    return r


def _declared_physical_paths(reg_rows):
    out = set()
    for row in reg_rows:
        rp = row.get("resolved_path")
        if rp:
            out.add(os.path.normpath(rp))
    return out


def _undeclared_checkout_row(root_physical, physical_path):
    r = _git_safe(["config", "--get", "remote.origin.url"], cwd=physical_path)
    origin = r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""
    return {
        "key": None,
        "path": os.path.relpath(physical_path, root_physical),
        "kind": "undeclared-checkout",
        "discovered_origin": origin or "undeclared",
        "remedy": "declare a repos.<key> row for this path and add a root-anchored .gitignore line",
        "finding": True,
    }


def _reverse_scan(root_physical, declared_paths, max_depth):
    """AC-WRV-6: every directory beneath the physical root carrying a `.git` entry (file or dir),
    not the root itself, and not a declared row's path -> `undeclared-checkout`. Excludes exactly
    REVERSE_SCAN_EXCLUDED_NAMES, never descends into a discovered checkout, never traverses a
    symlink, and physically confines every candidate before reporting it."""
    findings = []

    def walk(current_dir, depth):
        try:
            names = sorted(os.listdir(current_dir))
        except OSError:
            return
        for name in names:
            if name in REVERSE_SCAN_EXCLUDED_NAMES:
                continue
            full = os.path.join(current_dir, name)
            if os.path.islink(full):
                continue  # never traverse a symlink
            if not os.path.isdir(full):
                continue
            physical = os.path.realpath(full)
            if not _is_within(physical, root_physical):
                continue
            if physical == root_physical:
                continue  # never the root itself
            git_marker = os.path.join(full, ".git")
            if os.path.exists(git_marker):
                if os.path.normpath(physical) not in declared_paths:
                    findings.append(_undeclared_checkout_row(root_physical, physical))
                continue  # never descend into a discovered checkout
            if depth < max_depth:
                walk(full, depth + 1)

    walk(root_physical, 0)
    return findings


def validate(root, *, max_depth=None):
    """Returns (degraded, degraded_reason, rows, exit_code). Raises REG.ManifestError on an
    absent/unreadable/unparseable manifest. `rows` values are UNSANITIZED — callers render through
    SANITIZE."""
    if max_depth is None:
        max_depth = DEFAULT_REVERSE_SCAN_MAX_DEPTH
    root_physical = os.path.realpath(root)
    outcome, degraded, degraded_reason, reg_rows = REG.build_report(root)
    forward_rows = [_forward_validate_row(r) for r in reg_rows]
    declared_paths = _declared_physical_paths(reg_rows)
    reverse_rows = _reverse_scan(root_physical, declared_paths, max_depth)
    all_rows = forward_rows + reverse_rows
    any_finding = degraded or any(r["finding"] for r in all_rows)
    return degraded, degraded_reason, all_rows, (EXIT_FINDINGS if any_finding else EXIT_CLEAN)


# ---------------------------------------------------------------------------------------------
# Rendering — AC-WRV-7's envelope, AC-WRV-8's sink applied to every field.
def _sanitize_scalar(v):
    if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    return SANITIZE(v if isinstance(v, str) else str(v))


def _sanitize_row(row):
    return {k: _sanitize_scalar(v) for k, v in row.items()}


def envelope(degraded, degraded_reason, rows):
    return {
        "degraded": bool(degraded),
        "degraded_reason": SANITIZE(degraded_reason) if degraded_reason else None,
        "rows": [_sanitize_row(r) for r in rows],
    }


def _emit_error(message, stream):
    print(SANITIZE("error: %s" % message), file=stream)


def _print_human_rows(rows, stdout):
    if not rows:
        print("(no rows)", file=stdout)
        return
    for row in rows:
        r = _sanitize_row(row)
        print(" ".join("%s=%s" % (k, r[k]) for k in r), file=stdout)


# ---------------------------------------------------------------------------------------------
# CLI
def cmd_sync(args, stdout, stderr):
    root = os.path.abspath(args.root)
    try:
        outcome, degraded, degraded_reason, reg_rows = REG.build_report(root)
    except REG.ManifestError as e:
        _emit_error(str(e), stderr)
        return EXIT_ERROR
    result = reconcile(root, reg_rows, timeout=args.timeout)
    rows = result["rows"]
    top_degraded = degraded or result["degraded"]
    top_reason = degraded_reason if degraded else result["degraded_reason"]
    env = envelope(top_degraded, top_reason, rows)
    if args.json:
        print(json.dumps(env, sort_keys=True), file=stdout)
    else:
        if top_degraded:
            print(SANITIZE("degraded: %s" % (top_reason or "")), file=stdout)
        _print_human_rows(rows, stdout)
    return EXIT_FINDINGS if (top_degraded or any(r["finding"] for r in rows)) else EXIT_CLEAN


def cmd_status(args, stdout, stderr):
    root = os.path.abspath(args.root)
    try:
        degraded, reason, rows, exit_code = status(root)
    except REG.ManifestError as e:
        _emit_error(str(e), stderr)
        return EXIT_ERROR
    env = envelope(degraded, reason, rows)
    if args.json:
        print(json.dumps(env, sort_keys=True), file=stdout)
    else:
        if degraded:
            print(SANITIZE("degraded: %s" % (reason or "")), file=stdout)
        _print_human_rows(rows, stdout)
    return exit_code


def cmd_foreach(args, child_argv, stdout, stderr):
    root = os.path.abspath(args.root)
    if not child_argv:
        _emit_error("foreach requires a command after `--`", stderr)
        return EXIT_ERROR
    try:
        degraded, reason, rows, exit_code = foreach(root, child_argv, timeout=args.timeout)
    except REG.ManifestError as e:
        _emit_error(str(e), stderr)
        return EXIT_ERROR
    env = envelope(degraded, reason, rows)
    if args.json:
        print(json.dumps(env, sort_keys=True), file=stdout)
    else:
        if degraded:
            print(SANITIZE("degraded: %s" % (reason or "")), file=stdout)
        _print_human_rows(rows, stdout)
    return exit_code


def cmd_validate(args, stdout, stderr):
    root = os.path.abspath(args.root)
    try:
        degraded, reason, rows, exit_code = validate(root, max_depth=args.max_depth)
    except REG.ManifestError as e:
        _emit_error(str(e), stderr)
        return EXIT_ERROR
    env = envelope(degraded, reason, rows)
    if args.json:
        print(json.dumps(env, sort_keys=True), file=stdout)
    else:
        if degraded:
            print(SANITIZE("degraded: %s" % (reason or "")), file=stdout)
        _print_human_rows(rows, stdout)
    return exit_code


def build_parser():
    p = argparse.ArgumentParser(
        prog="foundry_repo_fleet",
        description=(
            "The control plane's governed-repo fleet verbs (sync/status/foreach/validate) over "
            "the scripts/foundry_repo_registry.py classification. Clone and fetch are the ENTIRE "
            "mutation vocabulary; an existing checkout is never rewritten and drift is surfaced, "
            "never fixed. " + NO_GATE_STATEMENT + " " + INERTNESS_STATEMENT
        ),
    )
    sub = p.add_subparsers(dest="verb", required=True)

    sy = sub.add_parser("sync", help="clone not-cloned rows, fetch present+match rows, report the rest")
    sy.add_argument("--root", required=True, help="workspace root to evaluate")
    sy.add_argument("--json", action="store_true", help="emit the {degraded, degraded_reason, rows} envelope")
    sy.add_argument("--timeout", type=float, default=DEFAULT_SYNC_TIMEOUT_SECONDS,
                     help="per-invocation git timeout in seconds (default %(default)s)")

    st = sub.add_parser("status", help="one line per repos.<key> entry: present/origin/branch/ahead-behind/dirty")
    st.add_argument("--root", required=True, help="workspace root to evaluate")
    st.add_argument("--json", action="store_true", help="emit the {degraded, degraded_reason, rows} envelope")

    fe = sub.add_parser("foreach", help="fan out a command (after `--`) over the present repos")
    fe.add_argument("--root", required=True, help="workspace root to evaluate")
    fe.add_argument("--json", action="store_true", help="emit the {degraded, degraded_reason, rows} envelope")
    fe.add_argument("--timeout", type=float, default=DEFAULT_FOREACH_TIMEOUT_SECONDS,
                     help="per-child timeout in seconds (default %(default)s)")

    va = sub.add_parser("validate", help="the manifest <-> reality <-> gitignore round trip, both directions")
    va.add_argument("--root", required=True, help="workspace root to evaluate")
    va.add_argument("--json", action="store_true", help="emit the {degraded, degraded_reason, rows} envelope")
    va.add_argument("--max-depth", type=int, default=DEFAULT_REVERSE_SCAN_MAX_DEPTH,
                     help="reverse-scan traversal depth bound (default %(default)s)")
    return p


def main(argv=None, stdout=sys.stdout, stderr=sys.stderr):
    raw = list(sys.argv[1:] if argv is None else argv)
    child_argv = None
    pre = raw
    if raw and raw[0] == "foreach" and "--" in raw:
        idx = raw.index("--")
        pre, child_argv = raw[:idx], raw[idx + 1:]

    parser = build_parser()
    args = parser.parse_args(pre)

    try:
        if args.verb == "sync":
            return cmd_sync(args, stdout, stderr)
        if args.verb == "status":
            return cmd_status(args, stdout, stderr)
        if args.verb == "foreach":
            return cmd_foreach(args, child_argv or [], stdout, stderr)
        if args.verb == "validate":
            return cmd_validate(args, stdout, stderr)
        parser.error("unknown verb %r" % args.verb)
        return EXIT_ERROR
    except Exception:  # AC-WRV-8: the uncaught-exception path emits a sanitized line, never a raw traceback
        _emit_error("unexpected failure: %s" % traceback.format_exc().splitlines()[-1], stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
