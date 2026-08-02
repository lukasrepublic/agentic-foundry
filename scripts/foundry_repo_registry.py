#!/usr/bin/env python3
"""scripts/foundry_repo_registry.py — the registry-integrity report
(feat-foundry-repo-registry-formalization, AC-RRF-3..7).

A READ-ONLY, independently invocable report over `.claude/foundry-project.json` `repos{}`. It
answers, per declared entry: does the path exist, is it paired with a root-anchored `.gitignore`
rule (and not already swept into the control plane's own index), and does the checkout's `origin`
match the declared `remote` -- with **not-yet-cloned** and **dangling** reported as distinct
states (the headline gap this atom closes: today both present as "the path isn't there").

IT SURFACES; IT NEVER FIXES. No clone, no fetch, no `.gitignore` edit, no manifest write (AC-RRF-6
(i)). NO GATE IN THIS PLUGIN CONSUMES THIS SCRIPT'S EXIT CODE -- it is purely advisory, following
the `terraform plan -detailed-exitcode` tri-state convention already shipped in
`scripts/foundry-config.py`: 0 clean / 2 findings / 1 unreadable manifest.

Path resolution is PHYSICAL (symlinks followed), mirroring `scripts/foundry-wt`'s `cd && pwd -P`
confinement (AC-RRF-3). The origin oracle is the RAW configured value -- `git config --get
remote.origin.url`, never `git remote get-url`, which expands `url.*.insteadOf` from the AUDITED
checkout's own config and would let that checkout forge a `match` (AC-RRF-5; precedent:
`_remote_url_raw` in scripts/foundry-prepublication-leak-scan.py:591-602). Every git invocation is
a fixed argv, executed without a shell, drawn only from a closed, config-read-only plumbing set,
with `--` before every manifest-derived value (AC-RRF-6(vi)).

Every string this report emits -- to stdout, to stderr, and to `--json` alike, including on an
uncaught-exception path -- passes through exactly ONE sanitizing sink (`sanitize()`, AC-RRF-7):
userinfo in a remote URL (https://, ssh://, or scp-like [user@]host:path) is replaced with the
fixed literal `***`, and every C0/C1 control character and ANSI CSI escape sequence is neutralized,
so no field value sourced from an untrusted manifest or an untrusted child checkout can leak a
credential or repaint the terminal.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback

# ---------------------------------------------------------------------------------------------
# Exit codes (AC-RRF-6(ii), the shipped tri-state convention -- see scripts/foundry-config.py).
EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS = 0, 1, 2

NO_GATE_STATEMENT = (
    "No gate in this plugin consumes this script's exit code; it is purely advisory."
)

ROLE_SET = ("product", "handbook", "infra", "app", "workspace")

# ---------------------------------------------------------------------------------------------
# AC-RRF-6(vi): the closed, config-read-only plumbing set. Every git call in this module funnels
# through `_git()`, which is the ONLY function that ever spawns a subprocess -- a fixed argv,
# `shell=False`, never a string command line.
GIT_READONLY_VERBS = frozenset(["check-ignore", "config", "ls-files", "rev-parse"])


def _git(args, cwd, check=False):
    """The single git-invocation choke point (AC-RRF-6(vi)). `args[0]` MUST be one of the closed
    read-only verbs; asserted here defensively (never reachable via manifest input) so a future
    edit cannot silently widen the vocabulary."""
    assert args and args[0] in GIT_READONLY_VERBS, "git verb outside the closed read-only set: %r" % (args,)
    return subprocess.run(
        ["git"] + list(args), cwd=cwd, shell=False, capture_output=True, text=True, check=check
    )


def _git_available():
    return shutil.which("git") is not None


def _is_work_tree(path):
    if not _git_available():
        return False
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return r.returncode == 0 and r.stdout.strip() == "true"


# ---------------------------------------------------------------------------------------------
# AC-RRF-7: ONE sanitizing sink, applied at the emission boundary -- never per-call-site.
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_C0_C1_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_HTTPS_SSH_USERINFO_RE = re.compile(r"(https|ssh)://([^/@\s]+)@")
# scp-like `[user[:pass]@]host:path` -- placeholder chars ('*') never match the userinfo class
# below, so a value already redacted by the https/ssh pass above cannot be double-processed here.
_SCP_USERINFO_RE = re.compile(r"\b([A-Za-z0-9][\w.~%!$&'()*+,;=:-]*)@([A-Za-z0-9][A-Za-z0-9.-]*):")


def sanitize(value):
    """The one emission-boundary sink (AC-RRF-7). Idempotent-safe to call more than once; every
    stdout/stderr/--json emission path in this module funnels every field through this exact
    function, including the uncaught-exception path."""
    if value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    text = _HTTPS_SSH_USERINFO_RE.sub(lambda m: "%s://***@" % m.group(1), text)
    text = _SCP_USERINFO_RE.sub(lambda m: "***@%s:" % m.group(2), text)
    text = _CSI_RE.sub("<CSI>", text)
    text = _C0_C1_RE.sub(lambda m: "\\x%02x" % ord(m.group(0)), text)
    return text


def _sanitize_row(row):
    """Sanitize every string-valued field of a row, for emission only -- callers must classify
    from the RAW (unsanitized) row, never this one."""
    return {k: (sanitize(v) if isinstance(v, str) else v) for k, v in row.items()}


# ---------------------------------------------------------------------------------------------
# Remote-URL normalization (AC-RRF-5): https://, ssh:// and scp-form compare equal -- host
# case-folded, userinfo and port ignored, one leading '/', one trailing '/' and one trailing
# '.git' stripped from the path.
_SCHEME_HOST_PATH_RE = re.compile(r"^(?:https|ssh)://(?:[^/@]*@)?([^/:]+)(?::\d+)?/?(.*)$")
_SCP_HOST_PATH_RE = re.compile(r"^(?:[^@]*@)?([^:/]+):(.*)$")


def normalize_remote(url):
    if not url:
        return ""
    u = url.strip()
    m = _SCHEME_HOST_PATH_RE.match(u)
    if m:
        host, path = m.group(1), m.group(2)
    else:
        m2 = _SCP_HOST_PATH_RE.match(u)
        if m2:
            host, path = m2.group(1), m2.group(2)
        else:
            host, path = "", u
    host = host.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return "%s/%s" % (host, path)


# ---------------------------------------------------------------------------------------------
# Path resolution (AC-RRF-3): lexical normalization drives the gitignore pattern; PHYSICAL
# (symlink-resolved) resolution drives confinement -- mirroring foundry-wt's `cd && pwd -P`.
def _normalize_lexical(declared_path):
    p = (declared_path or "").strip()
    if p in ("", "."):
        return "."
    return os.path.normpath(p)


def _is_within(physical_path, root_physical):
    if physical_path == root_physical:
        return True
    return physical_path.startswith(root_physical.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------------------------------------
# AC-RRF-4: the gitignore pairing oracle, PLUS the tracked-index probe.
def _is_root_anchored(pattern):
    """Git's own pattern rule: a '/' anywhere but the LAST character makes the pattern
    root-anchored (relative to the .gitignore's own directory); a bare trailing-slash-only (or
    no-slash) pattern matches at any depth."""
    if not pattern:
        return False
    return "/" in pattern[:-1]


def _parse_check_ignore_line(line):
    # `<source>:<linenum>:<pattern>\t<pathname>` (git's own -v format).
    source_part = line.split("\t", 1)[0]
    pattern = source_part.rsplit(":", 1)[-1] if source_part.count(":") >= 2 else source_part
    return source_part, pattern


def _check_gitignore(root, relpath, path_status):
    """Returns (gitignore, detail). `detail` is the matching source:line:pattern for
    `unanchored`, the suggested root-anchored line for `unpaired`, or the tracked-count for
    `tracked`; None otherwise. The pathspec is passed WITH a trailing '/' -- git's directory-only
    ('.../') patterns only match a pathspec git can tell is a directory, which for a not-yet-cloned
    entry it cannot infer from the filesystem alone; appending '/' ourselves makes the answer the
    RULE, independent of whether the directory exists (the design note's `--no-index` rationale)."""
    dir_pathspec = relpath.rstrip("/") + "/"
    r = _git(["check-ignore", "-v", "--no-index", "--", dir_pathspec], cwd=root)
    if r.returncode == 0:
        line = (r.stdout.splitlines() or [""])[0]
        source_part, pattern = _parse_check_ignore_line(line)
        if _is_root_anchored(pattern):
            base, detail = "ok", None
        else:
            base, detail = "unanchored", source_part
    elif r.returncode == 1:
        base, detail = "unpaired", "/%s/" % relpath
    else:
        return "unknown", None  # unexpected git failure -- degrade this entry defensively

    if path_status == "present":
        ls = _git(["ls-files", "--", dir_pathspec], cwd=root)
        if ls.returncode == 0:
            tracked = [line for line in ls.stdout.splitlines() if line.strip()]
            if tracked:
                return "tracked", len(tracked)
    return base, detail


# ---------------------------------------------------------------------------------------------
# AC-RRF-5: the origin oracle.
def _origin_raw(path):
    """`git config --get remote.origin.url` -- the RAW configured value. NEVER `git remote
    get-url`, which expands `url.*.insteadOf` from the audited checkout's own config."""
    r = _git(["config", "--get", "remote.origin.url"], cwd=path)
    if r.returncode != 0:
        return None
    val = r.stdout.strip()
    return val or None


def _classify_origin(physical_path, path_status, remote, degraded):
    if not remote:
        return "undeclared", None
    if path_status != "present":
        return "not-a-checkout", None
    if degraded:
        return "unknown", None
    if not _is_work_tree(physical_path):
        return "not-a-checkout", None
    origin = _origin_raw(physical_path)
    if origin is None:
        return "not-a-checkout", None
    if normalize_remote(origin) == normalize_remote(remote):
        return "match", origin
    return "mismatch", origin


# ---------------------------------------------------------------------------------------------
# Per-entry classification.
def _classify_entry(root, root_physical, key, entry, degraded):
    declared_path = entry.get("path") if isinstance(entry, dict) else None
    remote = (entry.get("remote") or None) if isinstance(entry, dict) else None
    lexical = _normalize_lexical(declared_path if isinstance(declared_path, str) else "")
    abs_lexical = root if lexical == "." else os.path.join(root, lexical)
    exists = os.path.isdir(abs_lexical)
    physical_path = os.path.realpath(abs_lexical)

    if not exists:
        path_status = "not-cloned" if remote else "dangling"
    elif not _is_within(physical_path, root_physical):
        path_status = "outside-workspace"
    else:
        path_status = "present"

    is_self = lexical == "." or physical_path == root_physical

    if path_status == "outside-workspace" or is_self:
        gitignore, gitignore_detail = "n/a", None
    elif degraded:
        gitignore, gitignore_detail = "unknown", None
    else:
        gitignore, gitignore_detail = _check_gitignore(root, lexical, path_status)

    origin, origin_value = _classify_origin(physical_path, path_status, remote, degraded)

    row = {
        "key": key,
        "path": declared_path if isinstance(declared_path, str) else "",
        "path_status": path_status,
        "gitignore": gitignore,
        "origin": origin,
        "declared_remote": remote,
        "discovered_origin": origin_value,
        "resolved_path": physical_path,
    }

    if path_status == "not-cloned":
        row["remedy_remote"] = remote
        row["remedy_target_path"] = physical_path
    elif path_status == "dangling":
        row["remedy_text"] = "this entry has no `remote` and no existing path -- declare a remote or remove the entry"

    if gitignore == "unanchored":
        row["gitignore_source"] = gitignore_detail
    elif gitignore == "unpaired":
        row["remedy_gitignore_line"] = gitignore_detail
    elif gitignore == "tracked":
        row["tracked_count"] = gitignore_detail
        row["remedy_text"] = (row.get("remedy_text") or "") + (
            " " if row.get("remedy_text") else ""
        ) + "already tracked in the control plane's index -- ignore rules do not apply to tracked files; remove it from the index"

    return row


_FINDING_PATH_STATUS = frozenset(["not-cloned", "dangling", "outside-workspace"])
_FINDING_GITIGNORE = frozenset(["unanchored", "unpaired", "tracked"])
_FINDING_ORIGIN = frozenset(["mismatch", "not-a-checkout"])


def _row_is_finding(row):
    return (
        row["path_status"] in _FINDING_PATH_STATUS
        or row["gitignore"] in _FINDING_GITIGNORE
        or row["origin"] in _FINDING_ORIGIN
    )


# ---------------------------------------------------------------------------------------------
# Report assembly.
class ManifestError(Exception):
    pass


def _load_manifest(root):
    path = os.path.join(root, ".claude", "foundry-project.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as e:
        raise ManifestError("cannot read manifest %s: %s" % (path, e))
    except ValueError as e:
        raise ManifestError("cannot parse manifest %s: %s" % (path, e))


def build_report(root):
    """Returns (outcome, degraded, degraded_reason, rows). `outcome` is 'no-repos' or 'ok'.
    Raises ManifestError when the manifest itself is absent/unreadable/unparseable (AC-RRF-6(ii)
    exit 1 case) -- callers decide the exit code."""
    manifest = _load_manifest(root)
    repos = manifest.get("repos") if isinstance(manifest, dict) else None
    if not repos or not isinstance(repos, dict):
        return "no-repos", False, None, []

    root_physical = os.path.realpath(root)
    degraded = (not _git_available()) or (not _is_work_tree(root))
    if not _git_available():
        degraded_reason = "git not found on PATH -- gitignore/origin checks report `unknown`"
    elif not _is_work_tree(root):
        degraded_reason = "workspace root is not a git work tree -- gitignore/origin checks report `unknown`"
    else:
        degraded_reason = None

    rows = [
        _classify_entry(root, root_physical, key, entry, degraded)
        for key, entry in repos.items()
    ]
    return "ok", degraded, degraded_reason, rows


# ---------------------------------------------------------------------------------------------
# Output (AC-RRF-7: every field sanitized at the emission boundary).
def _format_row_human(row):
    r = _sanitize_row(row)
    lines = [
        "%s: path=%s path_status=%s gitignore=%s origin=%s"
        % (r["key"], r["path"], r["path_status"], r["gitignore"], r["origin"])
    ]
    if r.get("remedy_remote") is not None or r.get("remedy_target_path") is not None:
        lines.append(
            "  remedy: remote=%s target_path=%s"
            % (r.get("remedy_remote") or "", r.get("remedy_target_path") or "")
        )
    if r.get("remedy_gitignore_line"):
        lines.append("  remedy: add root-anchored line to .gitignore: %s" % r["remedy_gitignore_line"])
    if r.get("gitignore_source"):
        lines.append("  gitignore rule (unanchored): %s" % r["gitignore_source"])
    if r.get("tracked_count") is not None:
        lines.append("  tracked files: %s" % r["tracked_count"])
    if r.get("remedy_text"):
        lines.append("  remedy: %s" % r["remedy_text"])
    if r.get("declared_remote") is not None or r.get("discovered_origin") is not None:
        lines.append(
            "  remote: declared=%s origin=%s"
            % (r.get("declared_remote") or "", r.get("discovered_origin") or "")
        )
    return "\n".join(lines)


def _print_human(outcome, degraded, degraded_reason, rows, stream):
    if outcome == "no-repos":
        print(sanitize("no-repos: manifest has no repos{} entries -- nothing was checked"), file=stream)
        return
    if degraded:
        print(sanitize("degraded: %s" % (degraded_reason or "")), file=stream)
    for row in rows:
        print(_format_row_human(row), file=stream)
    if not rows:
        print("(no repos{} entries)", file=stream)


def _json_envelope(outcome, degraded, degraded_reason, rows):
    sanitized_rows = [_sanitize_row(r) for r in rows]
    envelope = {
        "degraded": degraded,
        "degraded_reason": sanitize(degraded_reason),
        "rows": sanitized_rows,
    }
    if outcome == "no-repos":
        envelope["outcome"] = "no-repos"
    return envelope


def _emit_error(message, stream=sys.stderr):
    print(sanitize("error: %s" % message), file=stream)


# ---------------------------------------------------------------------------------------------
def run(root, as_json, stdout=sys.stdout, stderr=sys.stderr):
    try:
        outcome, degraded, degraded_reason, rows = build_report(root)
    except ManifestError as e:
        _emit_error(str(e), stream=stderr)
        return EXIT_ERROR
    except Exception:  # pragma: no cover -- AC-RRF-7's uncaught-exception path
        _emit_error("unexpected failure: %s" % traceback.format_exc().splitlines()[-1], stream=stderr)
        return EXIT_ERROR

    if as_json:
        print(json.dumps(_json_envelope(outcome, degraded, degraded_reason, rows), sort_keys=True), file=stdout)
    else:
        _print_human(outcome, degraded, degraded_reason, rows, stdout)

    if outcome == "no-repos":
        return EXIT_FINDINGS
    if any(_row_is_finding(r) for r in rows):
        return EXIT_FINDINGS
    return EXIT_CLEAN


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="foundry_repo_registry",
        description=(
            "Read-only registry-integrity report over .claude/foundry-project.json repos{}: "
            "presence, gitignore pairing, and origin-vs-declared-remote match. "
            + NO_GATE_STATEMENT
        ),
    )
    p.add_argument("--root", required=True, help="workspace root to evaluate")
    p.add_argument("--json", action="store_true", help="emit the {degraded, degraded_reason, rows} envelope")
    args = p.parse_args(argv)
    return run(os.path.abspath(args.root), args.json)


if __name__ == "__main__":
    sys.exit(main())
