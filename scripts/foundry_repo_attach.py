#!/usr/bin/env python3
"""scripts/foundry_repo_attach.py — the governed-repo attach flow
(feat-foundry-wizard-attach-repo-flow, AC-WAF-1..9).

The WRITE HALF of the governed-repo registry — the `mr register` / `nx import` shape — as a
reusable flow module the pre-session bootstrap CLI hosts:

  attach-existing — source (an admitted remote URL | an ABSOLUTE local path) -> local path
                    DEFAULTED from the source and CONFIRMED -> registry KEY (floored, defaulted,
                    never auto-transformed) -> role (the closed set) -> one-line description ->
                    default branch -> validate (never written on failure) -> preview (the exact
                    row, the exact gitignore BYTES, the reconcile plan) -> confirm -> ORDERED
                    ATOMIC pair write (gitignore line first, manifest row second) -> reconcile.
  create-new      — CONFIRM "create repo <owner>/<name>" FIRST -> `gh repo create` (+ optional
                    --template, NEVER --clone) -> bind the canonical identity from `gh`'s
                    STRUCTURED (`gh repo view --json`) output -> FALL THROUGH into the attach
                    path: one implementation writes the pair.

Writing a repos.<key> row IS the standing authority to reach that remote.
Every field is confirmed before anything is written.
No claim is made that a cloned tree is inert: a cloned repository's CLAUDE.md, .claude/** and .mcp.json become discoverable configuration for sessions rooted in the workspace.

THIS MODULE EXECUTES NO GIT COMMAND OF ITS OWN, EVER. Reconcile happens ONLY by calling the
imported function object `foundry_repo_fleet.reconcile` (feat-foundry-workspace-repo-verbs
AC-WRV-10, authorized, consumed unmodified), entered ONLY after the pair is durably on disk, with
`rows` equal to exactly the one row this flow just wrote — re-derived by re-classifying the
manifest FILE ON DISK through `foundry_repo_registry.build_report`, never the in-memory values
this flow collected (AC-WAF-8).

VALIDATION IS DECIDED BY THE SHIPPED ARTIFACTS, NEVER A RE-IMPLEMENTATION (AC-WAF-2): the shape
floor is `schema/foundry-project.schema.json` (validated via `jsonschema`, loaded from this
module's own directory); the admitted-remote-form predicate is LOADED from
`scripts/foundry-prepublication-leak-scan.py`'s `url_is_allowed_form` /
`_is_local_path_escape_hatch`, resolved from THIS module's own directory. This flow's own
additions to that floor (a floored registry key, a refused password-bearing userinfo, C0/C1
refused in `path` AND `description`, confinement against every governed root, a duplicate path, a
`tracked` target) are NARROWINGS, never widenings.

THE PAIR IS BOTH-OR-NEITHER, AND THE ORDER IS PINNED: the gitignore line lands first (its only
reachable partial state is the BENIGN half — an ignore rule for a path that does not yet exist),
the manifest row lands second; the rollback mirrors it, removing the row first. Each file is
replaced atomically — temp file in the target's own directory, `fsync`ed, mode preserved, `rename`
`(2)`d over the target, containing directory `fsync`ed — with the pre-image re-read and hashed
immediately before the write and re-verified immediately before the rename, so a concurrent writer
is reported, never clobbered. A completed clone and a repository `gh repo create` made are this
flow's two UNCOMPENSATED effects — never deleted, always named in the report as an orphan.

Every string this flow emits — preview, refusal, reconcile report, rollback report, the
uncaught-exception path — passes through `foundry_repo_registry.sanitize`, imported rather than
re-implemented, since the reconcile callable returns unsanitized values by contract.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------------------------
# Bootstrap: this module's own directory on sys.path (mirrors foundry_repo_fleet.py's own
# guarded, idempotent `append` — never `insert(0, ...)`, never shadowing a caller's own entry).
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.append(_HERE_DIR)
import foundry_repo_registry as REG  # noqa: E402 — the frozen classifier/envelope/sink
import foundry_repo_fleet as FLEET  # noqa: E402 — AC-WRV-10's imported reconcile callable

SINK = REG.sanitize  # AC-WAF-3: the ONE inherited sink, imported, never re-implemented.

# ---------------------------------------------------------------------------------------------
# AC-WAF-7: the three pinned sentences, verbatim, each their own line wherever they appear.
PINNED_SENTENCE_1 = "Writing a repos.<key> row IS the standing authority to reach that remote."
PINNED_SENTENCE_2 = "Every field is confirmed before anything is written."
PINNED_SENTENCE_3 = (
    "No claim is made that a cloned tree is inert: a cloned repository's CLAUDE.md, .claude/** "
    "and .mcp.json become discoverable configuration for sessions rooted in the workspace."
)

_HELP_DESCRIPTION = "\n".join([
    "The governed-repo attach flow (attach-existing / create-new) — the write half of the",
    "registry, hosted by the pre-session bootstrap CLI and independently invocable here.",
    "",
    PINNED_SENTENCE_1,
    PINNED_SENTENCE_2,
    PINNED_SENTENCE_3,
])

EXIT_OK, EXIT_REFUSED, EXIT_ERROR = 0, 2, 1

COMMITTED_FILE_ADVISORY = (
    ".claude/foundry-project.json is a COMMITTED file: this value will enter git history."
)
UNREDACTED_NOTE = "the unredacted, unescaped value is what will be written (not shown here)."


# ---------------------------------------------------------------------------------------------
# Terminology: the registry key floor.
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RESERVED_KEY = "workspace"


def key_satisfies_floor(key):
    return bool(key) and isinstance(key, str) and bool(_KEY_RE.match(key)) and key != RESERVED_KEY


# ---------------------------------------------------------------------------------------------
# Terminology: the gitignore line — "/" + escape(P) + "/" + LF. escape() prefixes "\" to each of
# \ * ? [ ] ! # anywhere, and to a trailing space; never to "/".
_GITIGNORE_METACHARS = set("\\*?[]!#")


def _gitignore_escape(p):
    n = len(p)
    out = []
    for i, ch in enumerate(p):
        if ch in _GITIGNORE_METACHARS:
            out.append("\\" + ch)
        elif ch == " " and i == n - 1:
            out.append("\\ ")
        else:
            out.append(ch)
    return "".join(out)


def gitignore_line_bytes(lexical_path):
    return ("/" + _gitignore_escape(lexical_path) + "/\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------
# C0/C1 control characters (Terminology, shared with the shape floor).
_C0_C1_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _has_c0_c1(value):
    return bool(value) and bool(_C0_C1_RE.search(value))


# ---------------------------------------------------------------------------------------------
# Local path normalization (lexical, relative to the workspace root; no leading "/", no ".."
# segment, any "./" prefix stripped).
def normalize_lexical_path(path):
    if not path or not isinstance(path, str):
        return None
    p = path
    if p.startswith("/"):
        return None
    if p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/")
    if p in ("", "."):
        return None
    segments = p.split("/")
    if any(seg in ("", "..") for seg in segments):
        return None
    return p


def _physical_resolve_nearest_existing(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(p):
            return os.path.realpath(p)
        parent = os.path.dirname(p)
        if parent == p:
            return os.path.realpath(p)
        p = parent


def _is_within_or_equal(inner, outer):
    if inner == outer:
        return True
    return inner.startswith(outer.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------------------------------------
# AC-WAF-2: the admitted-remote-form predicate is LOADED, never re-implemented, resolved from
# THIS module's own directory (never from an untrusted --root).
_LEAK_SCAN_MODULE_CACHE = []


def _load_leak_scan_module():
    if _LEAK_SCAN_MODULE_CACHE:
        return _LEAK_SCAN_MODULE_CACHE[0]
    import importlib.util

    path = os.path.join(_HERE_DIR, "foundry-prepublication-leak-scan.py")
    spec = importlib.util.spec_from_file_location("foundry_repo_attach_leak_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LEAK_SCAN_MODULE_CACHE.append(mod)
    return mod


def classify_source(source, leak_scan):
    """Returns "remote", "local", or None. The loaded predicate decides both directions."""
    if not source or not isinstance(source, str):
        return None
    if leak_scan.url_is_allowed_form(source):
        return "remote"
    if leak_scan._is_local_path_escape_hatch(source):
        return "local"
    return None


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def derive_local_name(source):
    """AC-WAF-1: the local path default — the source's final path segment, one trailing `.git`
    stripped — for an https/ssh URL, an scp-form remote, and an absolute local path alike."""
    s = (source or "").rstrip("/")
    if not s:
        return ""
    if _SCHEME_RE.match(s):
        seg = urlsplit(s).path.rstrip("/").rsplit("/", 1)[-1]
    elif s.startswith("/"):
        seg = s.rsplit("/", 1)[-1]
    else:
        path_part = (s.split(":", 1)[1] if ":" in s else s).rstrip("/")
        seg = path_part.rsplit("/", 1)[-1] if path_part else s
    if seg.endswith(".git") and len(seg) > 4:
        seg = seg[: -4]
    return seg


def _userinfo_of(source):
    """AC-WAF-2d: the source's userinfo, or None. https://ssh:// take it from the netloc (up to
    the LAST '@' before the first '/'); an absolute local path never carries one; an scp-like form
    takes it as the text before the FIRST '@' in the whole value."""
    s = source or ""
    if _SCHEME_RE.match(s):
        rest = s.split("://", 1)[1]
        before_slash = rest.split("/", 1)[0]
        if "@" in before_slash:
            return before_slash.rsplit("@", 1)[0]
        return None
    if s.startswith("/"):
        return None
    if "@" in s:
        return s.split("@", 1)[0]
    return None


def has_password_component(source):
    userinfo = _userinfo_of(source)
    return userinfo is not None and ":" in userinfo


def has_single_component_userinfo(source):
    userinfo = _userinfo_of(source)
    return userinfo is not None and ":" not in userinfo


# ---------------------------------------------------------------------------------------------
# AC-WAF-2(a): schema validation over the WHOLE PROSPECTIVE DOCUMENT, monotonicity rule.
_SCHEMA_CACHE = []


def _schema_path():
    return os.path.join(os.path.dirname(_HERE_DIR), "schema", "foundry-project.schema.json")


def _load_schema():
    if not _SCHEMA_CACHE:
        with open(_schema_path(), encoding="utf-8") as fh:
            _SCHEMA_CACHE.append(json.load(fh))
    return _SCHEMA_CACHE[0]


def schema_errors(document):
    """Structural error set: (path tuple, validator keyword, message) triples — compared by
    location, never joined prose (AC-WAF-2a)."""
    import jsonschema

    validator = jsonschema.Draft202012Validator(_load_schema())
    return [
        (tuple(e.absolute_path), e.validator, e.message)
        for e in validator.iter_errors(document)
    ]


# ---------------------------------------------------------------------------------------------
# Refusal vocabulary.
class Refusal(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


class ConcurrentWriterRefusal(Exception):
    def __init__(self, path):
        self.path = path
        super().__init__("concurrent writer detected at %s" % path)


class SymlinkRefusal(Exception):
    def __init__(self, path):
        self.path = path
        super().__init__("refusing to follow a symlink at %s" % path)


# ---------------------------------------------------------------------------------------------
# AC-WAF-4: the durable atomic-replace recipe. `_rename_choke` and `_atomic_replace_file` are
# deliberate, separately monkeypatchable seams for fault-injection tests.
def _read_bytes_or_none(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _fsync_dir(dir_path):
    fd = os.open(dir_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_choke(tmp_path, target_path):
    """The single rename(2) choke point — the last instant before a write becomes durable."""
    os.replace(tmp_path, target_path)


def _atomic_replace_file(target_path, compute_new_bytes, expected_pre_bytes, *, create_mode=0o644):
    """AC-WAF-4: temp file in the TARGET's own directory, fsync'd, mode preserved (or
    `create_mode` for a new file), `rename(2)`'d over the target, containing directory fsync'd
    after the rename. The pre-image is re-read fresh here (never a cached copy) and compared
    against `expected_pre_bytes`; re-verified again immediately before the rename. A symlink
    target is a named refusal, never a followed write. Returns the bytes actually written."""
    if os.path.islink(target_path):
        raise SymlinkRefusal(target_path)
    target_dir = os.path.dirname(os.path.abspath(target_path)) or "."
    fresh_pre = _read_bytes_or_none(target_path)
    if fresh_pre != expected_pre_bytes:
        raise ConcurrentWriterRefusal(target_path)
    new_bytes = compute_new_bytes(fresh_pre)
    mode = create_mode
    if os.path.exists(target_path):
        mode = stat.S_IMODE(os.stat(target_path).st_mode)
    fd, tmp_path = tempfile.mkstemp(prefix=".foundry-attach-", dir=target_dir)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        just_before = _read_bytes_or_none(target_path)
        if just_before != fresh_pre:
            raise ConcurrentWriterRefusal(target_path)
        _rename_choke(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _fsync_dir(target_dir)
    return new_bytes


def _restore_file(path, restore_to_bytes, expected_current_bytes):
    """AC-WAF-5 rollback restore: re-verify the on-disk file still hashes to what THIS flow wrote
    before overwriting it; abort (False) on a mismatch rather than clobber a concurrent writer."""
    current = _read_bytes_or_none(path)
    if current != expected_current_bytes:
        return False
    if restore_to_bytes is None:
        try:
            if os.path.islink(path):
                raise SymlinkRefusal(path)
            os.remove(path)
        except FileNotFoundError:
            pass
        return True
    _atomic_replace_file(path, lambda _pre: restore_to_bytes, current)
    return True


# ---------------------------------------------------------------------------------------------
# gitignore content builder.
def _normalize_trailing_newline(data):
    if not data:
        return b""
    return data if data.endswith(b"\n") else data + b"\n"


def _gitignore_lines_with_terminator(base):
    lines = base.split(b"\n")
    return [l + b"\n" for l in lines[:-1]]


def _gitignore_is_noop(pre_bytes, line_bytes):
    base = _normalize_trailing_newline(pre_bytes)
    return line_bytes in _gitignore_lines_with_terminator(base)


def _gitignore_new_bytes(pre_bytes, line_bytes):
    base = _normalize_trailing_newline(pre_bytes)
    if line_bytes in _gitignore_lines_with_terminator(base):
        return base
    return base + line_bytes


# ---------------------------------------------------------------------------------------------
# manifest content builder — preserves every other key/entry/formatting by re-deriving the WHOLE
# document from the freshly-read pre-image (parsed, insertion order preserved) plus exactly one
# new key, always re-serialized the same deterministic way.
def _parse_manifest_lenient(pre_bytes):
    if pre_bytes is None:
        return {"schema_version": 1}
    try:
        doc = json.loads(pre_bytes.decode("utf-8"))
    except ValueError:
        return {"schema_version": 1}
    return doc if isinstance(doc, dict) else {"schema_version": 1}


def _manifest_new_bytes(pre_bytes, key, row):
    doc = dict(_parse_manifest_lenient(pre_bytes))
    repos = doc.get("repos")
    repos = dict(repos) if isinstance(repos, dict) else {}
    repos[key] = row
    doc["repos"] = repos
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------
# AC-WAF-2(f/g/h): confinement against every governed root, duplicate-path, tracked-target.
def _confinement_refusal(root_abs, root_physical, lexical_path, existing_repos):
    target_abs = os.path.join(root_abs, lexical_path)
    nearest = _physical_resolve_nearest_existing(target_abs)
    if not _is_within_or_equal(nearest, root_physical):
        return Refusal("outside-workspace-root", "target path escapes the physical workspace root")
    target_physical = os.path.realpath(target_abs)
    for other_key, other_entry in (existing_repos or {}).items():
        other_lexical = normalize_lexical_path(
            other_entry.get("path") if isinstance(other_entry, dict) else None
        )
        other_abs = root_abs if other_lexical is None else os.path.join(root_abs, other_lexical)
        other_physical = os.path.realpath(other_abs)
        if other_physical == root_physical:
            continue  # the workspace self-entry
        if target_physical == other_physical:
            return Refusal("duplicate-path", "target path equals the declared path of row %r" % other_key)
        if _is_within_or_equal(target_physical, other_physical):
            return Refusal("nested-inside-governed-repo", "target path is inside already-declared repo %r" % other_key)
        if _is_within_or_equal(other_physical, target_physical):
            return Refusal("contains-governed-repo", "target path contains already-declared repo %r" % other_key)
    return None


def _is_tracked(root_abs, root_physical, key, lexical_path, remote_value):
    degraded = (not REG._git_available()) or (not REG._is_work_tree(root_abs))
    entry = {"path": lexical_path, "remote": remote_value}
    row = REG._classify_entry(root_abs, root_physical, key, entry, degraded)
    return row.get("gitignore") == "tracked", row


def _predict_reconcile_action(reg_row, remote_value, leak_scan):
    """A PREDICTION only — rendered in the preview before any write. The REAL decision is always
    made by the imported `FLEET.reconcile` callable, called by identity after the write (AC-WAF-8);
    this mirrors only its up-front branch, never its git-spawning execution."""
    path_status = reg_row.get("path_status")
    origin = reg_row.get("origin")
    admitted = bool(remote_value) and bool(
        leak_scan.url_is_allowed_form(remote_value) or leak_scan._is_local_path_escape_hatch(remote_value)
    )
    if path_status == "not-cloned":
        return "clone" if admitted else "refuse"
    if path_status == "present" and origin == "match":
        return "fetch" if admitted else "refuse"
    return "skip"


# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass
class PreparedRow:
    key: str
    row: dict
    lexical_path: str
    remote_value: str
    gitignore_line: bytes
    gitignore_noop: bool
    pre_existing_defects: list
    predicted_action: str
    userinfo_advisory: bool
    manifest_path: str
    gitignore_path: str


def prepare_row(root_abs, *, source, path, key, role, description, default_branch):
    """AC-WAF-2: validates every field against the shipped floor plus this flow's own narrowings.
    Raises Refusal on the first violation. Writes nothing. Returns a PreparedRow on success."""
    leak_scan = _load_leak_scan_module()

    source_kind = classify_source(source, leak_scan)
    if source_kind is None:
        raise Refusal(
            "source-not-admitted",
            "source is neither an admitted remote form nor an absolute local path resolving to "
            "an existing directory: %r" % source,
        )

    if has_password_component(source):
        raise Refusal(
            "credential-bearing-source",
            "the source's userinfo carries a password/token component, and the manifest is a "
            "COMMITTED file — refused rather than double-confirmed: %r" % SINK(source),
        )
    userinfo_advisory = has_single_component_userinfo(source)

    remote_value = source  # verbatim in both cases (AC-WAF-1)

    lexical_path = normalize_lexical_path(path)
    if lexical_path is None:
        raise Refusal("path-invalid", "path is empty, absolute, or carries a '..' segment: %r" % path)
    if lexical_path.startswith("-"):
        raise Refusal("path-leading-dash", "path begins with '-': %r" % path)
    if _has_c0_c1(path) or _has_c0_c1(description or ""):
        raise Refusal("control-character", "path or description contains a C0/C1 control character")

    if not key_satisfies_floor(key):
        raise Refusal("key-floor", "registry key fails the key floor or is the reserved key %r: %r" % (RESERVED_KEY, key))

    if role not in REG.ROLE_SET:
        raise Refusal("role-not-in-closed-set", "role %r is outside the closed set %r" % (role, REG.ROLE_SET))

    root_physical = os.path.realpath(root_abs)
    manifest_path = os.path.join(root_abs, ".claude", "foundry-project.json")
    gitignore_path = os.path.join(root_abs, ".gitignore")

    pre_bytes = _read_bytes_or_none(manifest_path)
    pre_doc = _parse_manifest_lenient(pre_bytes)
    existing_repos = pre_doc.get("repos") if isinstance(pre_doc.get("repos"), dict) else {}

    if key in existing_repos:
        raise Refusal("key-already-used", "registry key already present in the manifest: %r" % key)

    confinement = _confinement_refusal(root_abs, root_physical, lexical_path, existing_repos)
    if confinement is not None:
        raise confinement

    tracked, reg_row = _is_tracked(root_abs, root_physical, key, lexical_path, remote_value)
    if tracked:
        raise Refusal(
            "tracked-target",
            "the registry report classifies the target 'tracked': already inside the control "
            "plane's own git index",
        )

    row = {
        "path": lexical_path,
        "remote": remote_value,
        "role": role,
        "description": description or "",
        "default_branch": default_branch or "",
    }

    prospective_doc = dict(pre_doc)
    prospective_repos = dict(existing_repos)
    prospective_repos[key] = row
    prospective_doc["repos"] = prospective_repos

    pre_errors = schema_errors(pre_doc)
    prospective_errors = schema_errors(prospective_doc)
    new_errors = [e for e in prospective_errors if e not in pre_errors]
    if new_errors:
        raise Refusal(
            "schema-violation",
            "the prospective document introduces a schema error the pre-image did not carry: %s" % new_errors,
        )

    line_bytes = gitignore_line_bytes(lexical_path)
    gitignore_pre = _read_bytes_or_none(gitignore_path)
    gitignore_noop = _gitignore_is_noop(gitignore_pre, line_bytes)

    predicted_action = _predict_reconcile_action(reg_row, remote_value, leak_scan)

    return PreparedRow(
        key=key, row=row, lexical_path=lexical_path, remote_value=remote_value,
        gitignore_line=line_bytes, gitignore_noop=gitignore_noop,
        pre_existing_defects=pre_errors, predicted_action=predicted_action,
        userinfo_advisory=userinfo_advisory, manifest_path=manifest_path, gitignore_path=gitignore_path,
    )


# ---------------------------------------------------------------------------------------------
# AC-WAF-3: the preview.
def render_preview(prepared):
    lines = []
    sanitized_row = {k: (SINK(v) if isinstance(v, str) else v) for k, v in prepared.row.items()}
    lines.append("row: repos.%s = %s" % (prepared.key, json.dumps(sanitized_row, sort_keys=True)))
    if prepared.userinfo_advisory or SINK(prepared.row["remote"]) != prepared.row["remote"]:
        lines.append("  remote: %s" % UNREDACTED_NOTE)
    if prepared.userinfo_advisory:
        lines.append("  advisory: %s" % COMMITTED_FILE_ADVISORY)
    if prepared.gitignore_noop:
        lines.append("gitignore: no line will be added (already present)")
    else:
        lines.append("gitignore: will append the exact bytes %r" % prepared.gitignore_line)
    lines.append("reconcile plan: %s" % prepared.predicted_action)
    for e in prepared.pre_existing_defects:
        lines.append("pre-existing defect (unrelated to this write): %s" % SINK(str(e)))
    return lines


def _emit_lines(lines, stream):
    for line in lines:
        print(SINK(line), file=stream)


# ---------------------------------------------------------------------------------------------
# AC-WAF-8: post-write reconcile, called by identity, rows = exactly the new row read back from
# disk (re-derived via REG.build_report over the just-written manifest file).
def _reconcile_new_row(root_abs, key, timeout):
    outcome, degraded, degraded_reason, reg_rows = REG.build_report(root_abs)
    new_reg_row = next((r for r in reg_rows if r["key"] == key), None)
    result = FLEET.reconcile(root_abs, [new_reg_row] if new_reg_row is not None else [], timeout=timeout)
    rows = result.get("rows") or []
    return (rows[0] if rows else None), result


def _is_reconcile_failure(row_result):
    if row_result is None:
        return True
    return row_result.get("action") == "refuse" or row_result.get("result") in (
        "failed", "timeout", "spawn-failed",
    )


def _rollback(manifest_path, gitignore_path, manifest_pre, manifest_written_bytes,
              gitignore_pre, gitignore_written, gitignore_written_bytes):
    report = []
    ok_m = _restore_file(manifest_path, manifest_pre, manifest_written_bytes)
    report.append("manifest row removed (restore %s)" % ("ok" if ok_m else "ABORTED — a concurrent writer is present"))
    if gitignore_written:
        ok_g = _restore_file(gitignore_path, gitignore_pre, gitignore_written_bytes)
        report.append("gitignore line removed (restore %s)" % ("ok" if ok_g else "ABORTED — a concurrent writer is present"))
    return report


def _undeclared_checkout_after_rollback(root_abs, lexical_path):
    target = os.path.join(root_abs, lexical_path)
    marker = os.path.join(target, ".git")
    if os.path.exists(marker):
        return os.path.relpath(target, root_abs)
    return None


@dataclasses.dataclass
class AttachOutcome:
    status: str  # "written" | "declined" | "dry_run" | "refused" | "rolled_back"
    key: str = None
    row: dict = None
    reconcile_row: dict = None
    refusal_code: str = None
    refusal_message: str = None
    orphan_repo: str = None
    undeclared_checkout: str = None
    report_lines: list = dataclasses.field(default_factory=list)
    preview_lines: list = dataclasses.field(default_factory=list)


def _default_confirm(prompt, *, yes, stdin, stdout):
    if yes:
        return True
    print(SINK(prompt + " [y/N] "), end="", file=stdout)
    line = stdin.readline()
    if not line:
        raise EOFError("no confirmation available on stdin")
    return line.strip().lower() in ("y", "yes")


def attach_existing(
    root, *, source, path, key, role, description, default_branch,
    yes=False, dry_run=False, confirm_fn=None, reconcile_timeout=None,
    stdin=None, stdout=None,
):
    """AC-WAF-1..5/-8: the attach-existing flow. Pure of argv parsing / prompting concerns beyond
    the single `confirm_fn` seam — every field here is already resolved by the caller (the CLI's
    `collect_fields`, or the create-new fallthrough)."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    confirm_fn = confirm_fn or (lambda prompt: _default_confirm(prompt, yes=yes, stdin=stdin, stdout=stdout))

    root_abs = os.path.abspath(root)
    prepared = prepare_row(
        root_abs, source=source, path=path, key=key, role=role,
        description=description, default_branch=default_branch,
    )
    preview_lines = render_preview(prepared)

    if dry_run:
        return AttachOutcome(status="dry_run", key=prepared.key, row=prepared.row, preview_lines=preview_lines)

    if not confirm_fn("write repos.%s and its gitignore line?" % prepared.key):
        return AttachOutcome(status="declined", key=prepared.key, row=prepared.row, preview_lines=preview_lines)

    manifest_path, gitignore_path = prepared.manifest_path, prepared.gitignore_path
    gitignore_pre = gitignore_written_bytes = None
    gitignore_written = False
    manifest_pre = manifest_written_bytes = None

    try:
        if not prepared.gitignore_noop:
            gitignore_pre = _read_bytes_or_none(gitignore_path)
            gitignore_written_bytes = _atomic_replace_file(
                gitignore_path,
                lambda pre: _gitignore_new_bytes(pre, prepared.gitignore_line),
                gitignore_pre,
            )
            gitignore_written = True
        else:
            gitignore_pre = _read_bytes_or_none(gitignore_path)

        manifest_pre = _read_bytes_or_none(manifest_path)
        manifest_written_bytes = _atomic_replace_file(
            manifest_path,
            lambda pre: _manifest_new_bytes(pre, prepared.key, prepared.row),
            manifest_pre,
        )
    except ConcurrentWriterRefusal as e:
        return AttachOutcome(
            status="refused", refusal_code="concurrent-writer",
            refusal_message="a concurrent writer touched %s; nothing further was written" % e.path,
            preview_lines=preview_lines,
        )
    except SymlinkRefusal as e:
        return AttachOutcome(
            status="refused", refusal_code="symlink-target",
            refusal_message=str(e), preview_lines=preview_lines,
        )

    # Both writes are now durable (rename returned, directory fsync completed) — reconcile only now.
    try:
        row_result, _envelope = _reconcile_new_row(root_abs, prepared.key, reconcile_timeout)
        if _is_reconcile_failure(row_result):
            report = _rollback(
                manifest_path, gitignore_path, manifest_pre, manifest_written_bytes,
                gitignore_pre, gitignore_written, gitignore_written_bytes,
            )
            undeclared = _undeclared_checkout_after_rollback(root_abs, prepared.lexical_path)
            if undeclared:
                report.append(
                    "an undeclared checkout remains on disk at %s — declare a repos.<key> row for "
                    "it and re-run to complete the pair" % undeclared
                )
            return AttachOutcome(
                status="rolled_back", key=prepared.key, reconcile_row=row_result,
                undeclared_checkout=undeclared, report_lines=report, preview_lines=preview_lines,
            )
        return AttachOutcome(
            status="written", key=prepared.key, row=prepared.row, reconcile_row=row_result,
            preview_lines=preview_lines,
        )
    except Exception as e:  # AC-WAF-5: "the flow raises" also triggers rollback.
        report = _rollback(
            manifest_path, gitignore_path, manifest_pre, manifest_written_bytes,
            gitignore_pre, gitignore_written, gitignore_written_bytes,
        )
        undeclared = _undeclared_checkout_after_rollback(root_abs, prepared.lexical_path)
        if undeclared:
            report.append(
                "an undeclared checkout remains on disk at %s — declare a repos.<key> row for it "
                "and re-run to complete the pair" % undeclared
            )
        report.append("unexpected failure during reconcile: %s" % SINK(str(e)))
        return AttachOutcome(
            status="rolled_back", key=prepared.key, undeclared_checkout=undeclared,
            report_lines=report, preview_lines=preview_lines,
        )


# ---------------------------------------------------------------------------------------------
# AC-WAF-6: create-new.
_REPO_ARG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$")
_TEMPLATE_ARG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _floor_repo_arg(value):
    if not value or not isinstance(value, str) or not _REPO_ARG_RE.match(value):
        raise Refusal("create-repo-arg-invalid", "repository argument fails its floor: %r" % value)
    return value


def _floor_template_arg(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _TEMPLATE_ARG_RE.match(value):
        raise Refusal("create-template-arg-invalid", "--template value fails its floor: %r" % value)
    return value


def _spawn_gh(argv, timeout=None):
    """The ONE choke point for the ONE network-capable subprocess this module ever spawns."""
    return subprocess.run(["gh"] + list(argv), shell=False, capture_output=True, timeout=timeout)


def create_new(
    root, *, repo, template=None, path, key, role, description, default_branch,
    yes=False, dry_run=False, confirm_fn=None, reconcile_timeout=None, gh_timeout=60,
    stdin=None, stdout=None,
):
    """AC-WAF-6: confirm the creation FIRST, then `gh repo create` (create-then-view, never
    `--clone`), then fall through into `attach_existing` with `gh`'s STRUCTURED canonical URL as
    the source — one implementation writes the pair."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    confirm_fn = confirm_fn or (lambda prompt: _default_confirm(prompt, yes=yes, stdin=stdin, stdout=stdout))

    repo_arg = _floor_repo_arg(repo)
    template_arg = _floor_template_arg(template)

    confirm_line = "create repo %s" % repo_arg
    if template_arg:
        confirm_line += " from template %s" % template_arg
    confirm_line += ". " + PINNED_SENTENCE_3

    if dry_run:
        return AttachOutcome(status="dry_run", preview_lines=[confirm_line])

    if not confirm_fn(confirm_line):
        return AttachOutcome(status="declined", preview_lines=[confirm_line])

    create_argv = ["repo", "create"]
    if template_arg:
        create_argv += ["--template", template_arg]
    create_argv += ["--", repo_arg]
    try:
        p = _spawn_gh(create_argv, timeout=gh_timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refusal("gh-create-failed", "gh repo create could not run: %s" % e)
    if p.returncode != 0:
        raise Refusal(
            "gh-create-failed",
            "gh repo create failed (exit %s): %s" % (p.returncode, SINK(p.stderr.decode("utf-8", "replace"))),
        )

    stdout_text = p.stdout.decode("utf-8", "replace").strip()
    created_ref = stdout_text.splitlines()[-1].strip() if stdout_text else repo_arg
    try:
        pv = _spawn_gh(
            ["repo", "view", "--json", "nameWithOwner,url", "--", created_ref], timeout=gh_timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refusal("gh-view-failed", "gh repo view could not run: %s" % e)
    if pv.returncode != 0:
        raise Refusal(
            "gh-view-failed",
            "gh repo view failed (exit %s): %s" % (pv.returncode, SINK(pv.stderr.decode("utf-8", "replace"))),
        )
    try:
        info = json.loads(pv.stdout.decode("utf-8", "replace"))
        canonical_name = info["nameWithOwner"]
        canonical_url = info["url"]
    except (ValueError, KeyError, TypeError) as e:
        raise Refusal("gh-view-unparseable", "gh repo view --json produced an unexpected shape: %s" % e)

    orphan = canonical_name  # from this point on, a created repo exists — every exit names it.
    diverged = "/" in repo_arg and repo_arg.lower() != canonical_name.lower()
    preview_lines = [confirm_line, "created: %s (%s)" % (canonical_name, canonical_url)]
    if diverged:
        note = (
            "the created repository's canonical identity (%s) differs from what was requested "
            "(%s)." % (canonical_name, repo_arg)
        )
        preview_lines.append(note)
        if not confirm_fn(note + " continue and write the row for the canonical identity?"):
            return AttachOutcome(
                status="declined", orphan_repo=orphan, preview_lines=preview_lines,
                report_lines=[note],
            )

    try:
        result = attach_existing(
            root, source=canonical_url, path=path, key=key, role=role, description=description,
            default_branch=default_branch, yes=yes, dry_run=False, confirm_fn=confirm_fn,
            reconcile_timeout=reconcile_timeout, stdin=stdin, stdout=stdout,
        )
    except Refusal as e:
        return AttachOutcome(
            status="refused", refusal_code=e.code, refusal_message=e.message,
            orphan_repo=orphan, preview_lines=preview_lines,
        )

    result.preview_lines = preview_lines + result.preview_lines
    if result.status != "written":
        result.orphan_repo = orphan
    return result


# ---------------------------------------------------------------------------------------------
# CLI
def _read_flag_or_refuse(value, flag_name, yes):
    if value is not None:
        return value
    if yes:
        raise Refusal("missing-required-flag", "under --yes, %s is required and was not supplied" % flag_name)
    return None


def _prompt(prompt_text, default=None, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    suffix = " [%s]" % default if default else ""
    print(SINK(prompt_text + suffix + ": "), end="", file=stdout)
    line = stdin.readline()
    if not line:
        raise EOFError("no input available on stdin for: %s" % prompt_text)
    line = line.strip()
    return line if line else default


def collect_attach_fields(args, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    yes = args.yes

    source = _read_flag_or_refuse(args.source, "--source", yes)
    if source is None:
        source = _prompt("source (remote URL or absolute local path)", stdin=stdin, stdout=stdout)

    default_path = derive_local_name(source or "")
    path = _read_flag_or_refuse(args.path, "--path", yes)
    if path is None:
        path = _prompt("local path", default=default_path, stdin=stdin, stdout=stdout)

    default_key = default_path if key_satisfies_floor(default_path) else None
    key = _read_flag_or_refuse(args.key, "--key", yes)
    if key is None:
        key = _prompt("registry key", default=default_key, stdin=stdin, stdout=stdout)

    role = _read_flag_or_refuse(args.role, "--role", yes)
    if role is None:
        role = _prompt("role %s" % (REG.ROLE_SET,), stdin=stdin, stdout=stdout)

    description = _read_flag_or_refuse(args.description, "--description", yes)
    if description is None:
        description = _prompt("one-line description", default="", stdin=stdin, stdout=stdout)

    default_branch = _read_flag_or_refuse(args.default_branch, "--default-branch", yes)
    if default_branch is None:
        default_branch = _prompt("default branch", stdin=stdin, stdout=stdout)

    return dict(
        source=source, path=path, key=key, role=role, description=description,
        default_branch=default_branch,
    )


def _report(outcome, as_json, stdout, stderr):
    if as_json:
        payload = {
            "status": outcome.status,
            "key": outcome.key,
            "refusal_code": outcome.refusal_code,
            "refusal_message": SINK(outcome.refusal_message) if outcome.refusal_message else None,
            "orphan_repo": SINK(outcome.orphan_repo) if outcome.orphan_repo else None,
            "undeclared_checkout": outcome.undeclared_checkout,
            "report_lines": [SINK(l) for l in outcome.report_lines],
        }
        print(json.dumps(payload, sort_keys=True), file=stdout)
        return
    _emit_lines(outcome.preview_lines, stdout)
    if outcome.refusal_message:
        print(SINK("refused (%s): %s" % (outcome.refusal_code, outcome.refusal_message)), file=stderr)
    if outcome.orphan_repo:
        print(SINK("orphan repository (created, never deleted): %s" % outcome.orphan_repo), file=stdout)
    _emit_lines(outcome.report_lines, stdout)
    print(SINK("outcome: %s" % outcome.status), file=stdout)


def build_parser():
    p = argparse.ArgumentParser(
        prog="foundry_repo_attach",
        description=_HELP_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--root", required=True, help="workspace root to write into")
    p.add_argument("--yes", action="store_true", help="non-interactive: every field must come from a flag")
    p.add_argument("--dry-run", action="store_true", help="show the preview; write nothing; invoke neither gh nor reconcile")
    p.add_argument("--json", action="store_true", help="emit the outcome as JSON")
    p.add_argument("--path", default=None)
    p.add_argument("--key", default=None)
    p.add_argument("--role", default=None)
    p.add_argument("--description", default=None)
    p.add_argument("--default-branch", default=None)

    sub = p.add_subparsers(dest="mode", required=True)

    at = sub.add_parser("attach", help="attach-existing: register a remote or an existing local checkout")
    at.add_argument("--source", default=None, help="remote URL (admitted form) or absolute local path")

    cr = sub.add_parser("create", help="create-new: gh repo create, then fall through into attach-existing")
    cr.add_argument("--repo", default=None, help="repository to create: NAME or OWNER/NAME")
    cr.add_argument("--template", default=None, help="template repository: OWNER/NAME")

    return p


def main(argv=None, stdin=None, stdout=None, stderr=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    root = os.path.abspath(args.root)

    try:
        if args.mode == "attach":
            fields = collect_attach_fields(args, stdin=stdin, stdout=stdout)
            outcome = attach_existing(
                root, yes=args.yes, dry_run=args.dry_run, stdin=stdin, stdout=stdout, **fields
            )
        else:
            repo = _read_flag_or_refuse(args.repo, "--repo", args.yes)
            if repo is None:
                repo = _prompt("repository to create (NAME or OWNER/NAME)", stdin=stdin, stdout=stdout)
            template = args.template
            # create-new's `path`/`key` defaults derive from the CREATED identity, not a typed
            # source, so their prompt-defaults are deferred here until `repo` is known; role,
            # description and default_branch have no such dependency and are gathered up front,
            # matching AC-WAF-1's field ordering.
            path = args.path
            key = args.key
            if path is None and args.yes:
                raise Refusal("missing-required-flag", "under --yes, --path is required and was not supplied")
            if key is None and args.yes:
                raise Refusal("missing-required-flag", "under --yes, --key is required and was not supplied")
            role = _read_flag_or_refuse(args.role, "--role", args.yes)
            if role is None:
                role = _prompt("role %s" % (REG.ROLE_SET,), stdin=stdin, stdout=stdout)
            description = _read_flag_or_refuse(args.description, "--description", args.yes)
            if description is None:
                description = _prompt("one-line description", default="", stdin=stdin, stdout=stdout)
            default_branch = _read_flag_or_refuse(args.default_branch, "--default-branch", args.yes)
            if default_branch is None:
                default_branch = _prompt("default branch", stdin=stdin, stdout=stdout)
            if path is None:
                path = _prompt("local path", default=_floor_repo_arg(repo).rsplit("/", 1)[-1], stdin=stdin, stdout=stdout)
            if key is None:
                derived = path if key_satisfies_floor(path) else None
                key = _prompt("registry key", default=derived, stdin=stdin, stdout=stdout)
            outcome = create_new(
                root, repo=repo, template=template, path=path, key=key, role=role,
                description=description, default_branch=default_branch,
                yes=args.yes, dry_run=args.dry_run, stdin=stdin, stdout=stdout,
            )
    except Refusal as e:
        outcome = AttachOutcome(status="refused", refusal_code=e.code, refusal_message=e.message)
        _report(outcome, args.json, stdout, stderr)
        return EXIT_REFUSED
    except Exception:
        last_line = traceback.format_exc().splitlines()[-1]
        print(SINK("unexpected failure: %s" % last_line), file=stderr)
        return EXIT_ERROR

    _report(outcome, args.json, stdout, stderr)
    if outcome.status == "refused":
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
