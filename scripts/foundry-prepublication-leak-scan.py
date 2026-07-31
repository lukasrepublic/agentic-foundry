#!/usr/bin/env python3
"""foundry-prepublication-leak-scan.py — the pre-visibility-flip leak scan
(feat-foundry-runtime-gitignore-leak-scan, AC-RGLS-8..14).

A CLEAN verdict is unreachable unless FOUR scopes each run to completion and each report no
finding:

  1. working-tree  -- delegates to the shared module's own scan_tree() over the checked-out tree.
  2. history       -- every blob reachable from every ref, AND every commit/tag message reachable
                       from every ref (a content-only rewrite can miss a leaked term sitting only
                       in a commit message -- GO-PUBLIC.md §5.5 recorded exactly that gap).
  3. tracked-partition -- pure PATH enumeration (index + every ref's tree) of runtime-partition
                       (`.foundry/`) paths outside the designed-tracked set. Ignore rules do not
                       apply to already-tracked files, so this is independent of any term match --
                       it is the realized incident class this atom exists to close.
  4. remote-probe  -- a hermetic, disposable-store direct-SHA fetch of every recorded-bad SHA
                       against the scanned repository's configured remote, PLUS a positive control
                       that must resolve an object the remote holds but does not advertise as a
                       ref tip (GO-PUBLIC.md §5.4: a fresh clone scanned clean while the remote
                       still served the leaked object by direct SHA).

Also gated, independent of the four scopes (AC-RGLS-13): the scanned repository's root
`.gitignore` must carry the FOUNDRY-RUNTIME-GITIGNORE managed block at all -- an unprotected
repository is itself a finding, never a silent pass.

REUSE, NOT REWRITE (AC-RGLS-11): terms and the matcher come SOLELY from the shared module
(`.github/actions/leak-gate/leak_scan.py`, denied path here) via its pinned `load_denylist` /
`build_matcher`, resolved from THIS SCRIPT's own resolved location -- never from `--root`, and
never from anything inside the scanned repository, so scanning a stale or hostile tree cannot
substitute the term set.

Output hygiene (AC-RGLS-12d): every hit is reported as a path, a line number where one applies,
and a per-term INDEX -- never the matched text. No file is written into the scanned repository.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------------------------
# Resolution: ALWAYS relative to this script's own resolved location (AC-RGLS-11) -- never
# relative to --root, and never anything discovered inside the scanned repository.
SELF_PATH = os.path.realpath(os.path.abspath(__file__))
SELF_DIR = os.path.dirname(SELF_PATH)
_SCRIPTS_PARENT = os.path.dirname(SELF_DIR)  # repo root of THIS (agentic-foundry) checkout

SHARED_MODULE_PATH = os.path.realpath(
    os.path.join(_SCRIPTS_PARENT, ".github", "actions", "leak-gate", "leak_scan.py")
)
# The denylist is NOT in this repository (feat-foundry-denylist-out-of-tree) -- a term list committed
# to a tree that goes public discloses exactly what it exists to protect, and the F3 zero-history
# reset closes the history vector only, never the tree. It resolves from the operator's private
# workspace via CLAUDE_PROJECT_DIR (the established workspace-corpus resolution), or from an explicit
# --denylist argument. Resolution is fail-closed by construction: when CLAUDE_PROJECT_DIR is unset
# this returns None, and load_denylist is never reached with a tree-relative fallback that would
# quietly scan against nothing.
DENYLIST_WORKSPACE_RELPATH = os.path.join(".claude", "foundry-leak-denylist.txt")


def default_denylist_path():
    """Resolve the denylist OUTSIDE this tree, or None. Never falls back to a path inside the repo --
    a fallback is how an off-tree list silently becomes an in-tree one again."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return None
    return os.path.realpath(os.path.join(project_dir, DENYLIST_WORKSPACE_RELPATH))

BEGIN_TOKEN = "FOUNDRY-RUNTIME-GITIGNORE-BEGIN"

# SECURITY-SENSITIVE ALLOW-LIST (AC-LSR-8): this is scope 3's (tracked-partition) sole allow-list
# of `.foundry/` paths a scanned repository may legitimately track. It mirrors the re-include
# lines of the shipped `scripts/foundry-runtime.gitignore` fragment. SECURITY-REVIEW-REQUIRED for
# any edit to this set, or to that fragment's re-include lines -- the editing atom's acceptance
# contract SHALL declare `mandatory_review: security`; nothing here enforces that automatically.
DESIGNED_TRACKED_SET = frozenset(
    [
        ".foundry/README.md",
        ".foundry/build-provenance.yaml",
        ".foundry/stack-profile.lock",
    ]
)

DEFAULT_KNOWN_BAD_SHAS_RELPATH = os.path.join(".foundry", "leak-scan", "known-bad-shas.txt")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
URL_SCHEME_RE = re.compile(r"^(https://|ssh://|git@)")
NUL = b"\x00"
BINARY_SNIFF_BYTES = 8192

# ---------------------------------------------------------------------------------------------
# scp-like remote form (feat-foundry-leak-scan-scp-remote-form, AC-SCP-1..4/6). Git's own
# documented precedence, verbatim: (1) transport-helper syntax excluded FIRST and generically;
# (2) a scheme-bearing value decided ONLY by the pre-existing scheme allow-list above; (3) only
# then the scp-like rule `[user@]host:path`. See the Terminology section of the sibling spec.
SCHEME_BEARING_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
# ASCII-anchored and ASCII-only (re.ASCII): a Unicode `\w`-style class would admit a homoglyph
# host that renders indistinguishably in the status line (AC-SCP-1). The anchored first
# character is also what rejects a dash-leading authority (`-evil.example:x`, AC-SCP-3).
SCP_LIKE_AUTHORITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9][A-Za-z0-9._-]*)?$", re.ASCII)
_C0_C1_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


_SHARED_MODULE_CACHE = []


def _load_shared_module():
    if _SHARED_MODULE_CACHE:
        return _SHARED_MODULE_CACHE[0]
    spec = importlib.util.spec_from_file_location("foundry_rgls_leak_scan_shared", SHARED_MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _SHARED_MODULE_CACHE.append(mod)
    return mod


def _is_binary(data):
    return NUL in data[:BINARY_SNIFF_BYTES]


# =================================================================================================
# Findings
# =================================================================================================
class Finding:
    __slots__ = ("scope", "category", "message")

    def __init__(self, scope, category, message):
        self.scope = scope
        self.category = category  # "finding" | "denylist-origin" | "error"
        self.message = message

    def __str__(self):
        return f"[{self.scope}] {self.category.upper()}: {self.message}"


def _hit_line(path, line_no, term_index):
    """AC-RGLS-12d: path[:line] + a per-term INDEX -- never the matched text."""
    if line_no is not None:
        return f"{path}:{line_no} (term#{term_index})"
    return f"{path} (term#{term_index})"


def _term_index_for_match(matched_text, terms):
    """Map a matched substring back to its position in the loaded `terms` list (case-
    insensitive, matching build_matcher's own case-insensitivity) -- an index, never the text."""
    low = (matched_text or "").lower()
    for i, t in enumerate(terms):
        if t.lower() == low:
            return i
    return -1


# `_locate_term` lived here until feat-foundry-denylist-out-of-tree. It re-read and re-matched a
# file the shared module had already flagged, purely to recover (term_index, line_no) without
# touching that module's text-echoing hit string. The shared module now emits the line and index
# itself and never emits the term, so the re-derivation had no remaining caller and was removed
# rather than left as dead code in a security-sensitive scanner. The history scope does its OWN
# matching over raw blobs the module never sees, and uses `_term_index_for_match` directly.


# ALLOW-list (Risk #4): only these exact shapes, all of which are already known to the shared
# module's `scan_tree()` and are known to never embed matched denylist-term text (only a path
# and/or an OSError/UnicodeDecodeError message), are ever re-printed verbatim. Any shape NOT in
# this list -- including a future/unrecognized one from the shared module -- falls through to a
# REDACTED label, never a raw echo of the shared module's line.
_KNOWN_ERROR_PREFIXES = ("DENYLIST-ERROR: ", "WALK-ERROR: ", "READ-ERROR: ", "DECODE-ERROR: ",
                         "EMPTY-CORPUS-ERROR: ")  # fixed text, no path and no term


_NAME_TERM_TAIL_RE = re.compile(r"^(?P<path>.*):(?P<line>\d+): term\[(?P<idx>\d+|\?)\]$")


def _split_name_term_rest(rest):
    """`rest` is the shared module's NAME-TERM hit tail, now `{path}:{line}: term[{index}]`
    (feat-foundry-denylist-out-of-tree). The module no longer emits the matched text at all, so
    there is nothing left to strip -- this only recovers the three fields.

    Returns `(path, line_no, term_index)`, or None if `rest` does not have the expected shape.

    Retargeted from the previous `{path}: {repr(matched_text)}` form. That parser required the last
    character to be a quote; against the new shape it returned None for EVERY hit, so every real
    finding degraded to `REDACTED` -- fail-closed, but with the path, line and index the operator
    needs stripped out. `path` is matched greedily so a path containing `:` still resolves: the
    `:\\d+: term[...]` tail is anchored to the END, and only the final such tail can match.
    `?` is accepted for `idx` because the module emits it when a matcher carries no groups."""
    m = _NAME_TERM_TAIL_RE.match(rest)
    if not m:
        return None
    idx = m.group("idx")
    return m.group("path"), int(m.group("line")), (-1 if idx == "?" else int(idx))


def _sanitize_working_tree_hit(root, hit, terms, matcher):
    """Reformat one of the shared module's raw hit strings into an AC-RGLS-12d-hygienic Finding:
    path[:line] + a per-term index, or (for a structural marker / operational error) a label that
    names the RULE, never the matched text. ALLOW-listed shapes only (Risk #4) -- anything else is
    redacted, never echoed raw."""
    if hit.startswith("PATH-TERM: "):
        # Shared-module shape `PATH-TERM: <redacted rel path>: term[<i>]` -- already carries no
        # term text (the module's redactor built it); safe to re-frame verbatim.
        return Finding("working-tree", "finding", hit[len("PATH-TERM: "):] + " (term in PATH)")
    if hit.startswith("NAME-TERM: "):
        rest = hit[len("NAME-TERM: ") :]
        parsed = _split_name_term_rest(rest)
        if parsed is None:
            return Finding("working-tree", "error", "REDACTED (unrecognized NAME-TERM hit shape from the shared module)")
        path, line_no, term_index = parsed
        # The shared module now supplies line and index directly (and never the term), so this path
        # no longer re-reads and re-matches the file to recover them.
        return Finding("working-tree", "finding", _hit_line(os.path.relpath(path, root), line_no, term_index))
    if hit.startswith("MARKER-HBK: "):
        path = hit[len("MARKER-HBK: ") :]
        return Finding("working-tree", "finding", f"{os.path.relpath(path, root)} (structural marker: HBK id)")
    if hit.startswith("MARKER-MEMORY: "):
        path = hit[len("MARKER-MEMORY: ") :]
        return Finding("working-tree", "finding", f"{os.path.relpath(path, root)} (structural marker: memory-bracket)")
    for prefix in _KNOWN_ERROR_PREFIXES:
        if hit.startswith(prefix):
            # DENYLIST-ERROR / WALK-ERROR / READ-ERROR / DECODE-ERROR -- operational, never a
            # term-match hit; safe to re-print verbatim.
            return Finding("working-tree", "error", hit)
    # R5: `str.split(":", 1)[0]` returns the ENTIRE string when there is no colon -- so this
    # last-line-of-defence fallback, whose whole job is "never a raw echo", would print a full
    # unrecognized hit (matched text and all) under a REDACTED label. A future hit shape without an
    # early colon is exactly the case it exists for. Bounded, and restricted to the label-ish
    # characters a rule name can contain, so content cannot ride out inside it.
    unknown_prefix = hit.split(":", 1)[0][:32]
    if not re.fullmatch(r"[A-Z][A-Z0-9-]*", unknown_prefix or ""):
        unknown_prefix = "<unprintable>"
    return Finding("working-tree", "error", f"REDACTED (unrecognized hit shape from the shared module: prefix {unknown_prefix!r})")


# =================================================================================================
# git plumbing helpers
# =================================================================================================
class GitError(Exception):
    pass


# Risk #7: every read-side git invocation against the SCANNED repository gets the same hardening
# the remote probe already applies to itself -- fsmonitor/hooks/ext-transport disabled, so nothing
# in the scanned repo's own config (a hooksPath, an fsmonitor hook, an ext:: submodule/alternate
# reference) can execute or redirect during a read-only enumeration.
_HARDENED_GIT_CONFIG = [
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "protocol.ext.allow=never",
]


def _hardened_env():
    """A fixed, deterministic env for every scanned-repo git invocation (Risk #7): starts from the
    real environment (PATH/HOME/etc. are required for git to run at all) but always pins
    GIT_TERMINAL_PROMPT=0 explicitly rather than depending on whatever the caller's ambient
    environment happens to already contain."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args, cwd, input_bytes=None, env=None, timeout=None):
    try:
        return subprocess.run(
            ["git"] + _HARDENED_GIT_CONFIG + args,
            cwd=cwd,
            input=input_bytes,
            capture_output=True,
            env=env if env is not None else _hardened_env(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitError(str(e)) from e


def _git_text(args, cwd, check=True):
    p = _git(args, cwd)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {p.stderr.decode('utf-8', 'replace')}")
    return p.stdout.decode("utf-8", "replace")


# =================================================================================================
# AC-RGLS-13 -- the scanned repository must itself carry the managed block
# =================================================================================================
def fragment_coverage_finding(root):
    gi = os.path.join(root, ".gitignore")
    try:
        with open(gi, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return Finding("fragment-coverage", "finding", f"{root}: no readable .gitignore (no managed block)")
    if BEGIN_TOKEN not in text:
        return Finding("fragment-coverage", "finding", f"{root}: .gitignore carries no FOUNDRY-RUNTIME-GITIGNORE managed block")
    return None


# =================================================================================================
# Scope 1 -- working tree (delegates to the shared module)
# =================================================================================================
def working_tree_scope(root, denylist_path, terms, matcher, leak_scan_mod):
    excluded_real = os.path.realpath(denylist_path)
    exit_code, hits = leak_scan_mod.scan_tree(root, denylist_path)
    findings = [_sanitize_working_tree_hit(root, h, terms, matcher) for h in hits]
    ok = exit_code == 0
    return ok, findings, excluded_real


# =================================================================================================
# Scope 2 -- history (blobs + commit/tag messages), path-excluding the denylist file
# =================================================================================================
def _rev_list_objects(root):
    """Return list of (sha, path_or_empty) for every object reachable from every ref."""
    out = _git_text(["rev-list", "--objects", "--all"], root)
    entries = []
    for line in out.splitlines():
        if not line:
            continue
        bits = line.split(" ", 1)
        sha = bits[0]
        path = bits[1] if len(bits) > 1 else ""
        entries.append((sha, path))
    return entries


def _batch_check_types(root, shas):
    if not shas:
        return {}
    input_data = ("\n".join(shas) + "\n").encode("utf-8")
    p = _git(["cat-file", "--batch-check"], root, input_bytes=input_data)
    if p.returncode != 0:
        raise GitError(f"git cat-file --batch-check failed: {p.stderr.decode('utf-8', 'replace')}")
    types = {}
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        bits = line.split()
        if len(bits) >= 2:
            types[bits[0]] = bits[1]
    return types


def _batch_blob_contents(root, shas):
    """Yield (sha, bytes) for each blob sha, via a single `git cat-file --batch` round-trip."""
    if not shas:
        return
    input_data = ("\n".join(shas) + "\n").encode("utf-8")
    p = subprocess.Popen(
        ["git"] + _HARDENED_GIT_CONFIG + ["cat-file", "--batch"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_hardened_env(),
    )
    out, err = p.communicate(input_data)
    if p.returncode != 0 and not out:
        raise GitError(f"git cat-file --batch failed: {err.decode('utf-8', 'replace')}")
    idx = 0
    n = len(out)
    for sha in shas:
        nl = out.find(b"\n", idx)
        if nl < 0:
            raise GitError("git cat-file --batch: truncated output")
        header = out[idx:nl].decode("utf-8", "replace")
        idx = nl + 1
        parts = header.split()
        if len(parts) < 2 or parts[1] in ("missing", "ambiguous"):
            continue
        size = int(parts[2])
        data = out[idx : idx + size]
        idx += size + 1  # skip the trailing newline git appends after object content
        yield sha, data


def _commit_messages(root):
    sep = "\x03"
    out = _git_text(["log", "--all", "--no-notes", f"--format=%H%x00%B{sep}"], root, check=False)
    result = []
    for chunk in out.split(sep):
        chunk = chunk.strip("\n")
        if not chunk or "\x00" not in chunk:
            continue
        sha, msg = chunk.split("\x00", 1)
        result.append((sha.strip(), msg))
    return result


def _tag_messages(root):
    out = _git_text(["for-each-ref", "refs/tags", "--format=%(objectname) %(objecttype)"], root, check=False)
    result = []
    for line in out.splitlines():
        bits = line.split()
        if len(bits) != 2:
            continue
        sha, otype = bits
        if otype != "tag":
            continue  # lightweight tags point straight at a commit, already covered by _commit_messages
        p = _git(["cat-file", "-p", sha], root)
        if p.returncode != 0:
            continue
        raw = p.stdout.decode("utf-8", "replace")
        parts = raw.split("\n\n", 1)
        body = parts[1] if len(parts) > 1 else ""
        result.append((sha, body))
    return result


def _redact_path_text(path, matcher):
    """Span-exact redaction of term matches inside a PATH string (AC-LPS-2/3). DELEGATES to
    the shared module's `redact_path` -- one redactor, by construction, per the module's own
    one-implementation principle (R3, PR #308 review: the first copy had already diverged on
    the no-groups and None-path cases)."""
    if path is None:
        return path
    return _load_shared_module().redact_path(path, matcher)


def history_scope(root, denylist_relpaths, terms, matcher, denylist_content=None, denylist_inside_root=False):
    """Every blob reachable from every ref, plus every commit/tag message reachable from every
    ref. Risk #2 fix: NO blob is ever excluded from the scan corpus by its reported path. `git
    rev-list --objects` dedupes each object to a SINGLE reported path (the first one found during
    traversal); excluding-by-path would (a) make the `denylist-origin` category structurally
    unreachable whenever that single reported path is the denylist's own path (a real hit elsewhere
    sharing that blob SHA would never even enter the corpus), and (b) silently drop a genuine leak
    that happens to share an object SHA with the denylist's own historical content purely because of
    which path git's traversal happened to report first. Every blob is scanned; a hit is bucketed
    'denylist-origin' (AC-RGLS-12c) only when its CONTENT is byte-identical to the denylist file's
    OWN current content -- content-based, never path-based."""
    findings = []
    denylist_findings = []
    try:
        entries = _rev_list_objects(root)
        # AC-LPS-3: historical PATHS are a scanned surface too -- a term in a deleted or
        # renamed file's NAME is one `git log --raw` away on a public repo. Deduped on the
        # redacted form so one bad path convicts once, not once per object it ever named.
        seen_path_hits = set()
        for _sha, hist_path in entries:
            if not hist_path:
                continue
            pm = matcher.search(hist_path)
            if pm:
                red = _redact_path_text(hist_path, matcher)
                if red not in seen_path_hits:
                    seen_path_hits.add(red)
                    findings.append(Finding("history", "finding",
                                            f"historical PATH {red} (term in path)"))
        all_shas = [sha for sha, _ in entries]
        types = _batch_check_types(root, all_shas)

        blob_entries = [(sha, path) for sha, path in entries if types.get(sha) == "blob"]
        blob_shas = [sha for sha, _ in blob_entries]
        path_by_sha = {}
        for sha, path in blob_entries:
            path_by_sha.setdefault(sha, path)

        for sha, data in _batch_blob_contents(root, blob_shas):
            if _is_binary(data):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            m = matcher.search(text)
            if not m:
                continue
            path = _redact_path_text(path_by_sha.get(sha, sha), matcher)
            line_no = text.count("\n", 0, m.start()) + 1
            term_index = _term_index_for_match(m.group(0), terms)
            hit = Finding(
                "history",
                "finding",
                f"blob {sha[:12]} (historical path {path}) line {line_no} (term#{term_index})",
            )
            # THE `denylist-origin` BUCKET IS GATED ON THE DENYLIST ACTUALLY LIVING IN THIS REPO.
            #
            # Its original premise: the denylist was a TRACKED FILE of the scanned repository, so its
            # own historical blobs were an artifact that was SUPPOSED to be there and must not fail
            # the scope. feat-foundry-denylist-out-of-tree inverted that premise -- the list is no
            # longer in this tree, so a blob byte-identical to it is not an expected artifact, it IS
            # the leak this atom exists to close.
            #
            # Left ungated, the bucket blinded the gate to exactly that: the natural migration copies
            # the in-tree denylist to the workspace byte-for-byte, so `data == denylist_content`
            # holds for the RETIRED in-tree blob, which was then excluded from the verdict and the
            # scan reported CLEAN with all terms one `git cat-file` from any visitor. Verified live
            # on this repo before the fix -- the historical blob and the workspace copy shared SHA
            # fdd065c2. Whether the pre-publication gate caught the headline leak turned on whether
            # the operator's copy happened to be byte-exact.
            if denylist_content is not None and data == denylist_content and denylist_inside_root:
                hit.category = "denylist-origin"
                denylist_findings.append(hit)
            else:
                findings.append(hit)

        for sha, msg in _commit_messages(root):
            m = matcher.search(msg)
            if m:
                term_index = _term_index_for_match(m.group(0), terms)
                findings.append(Finding("history", "finding", f"commit message {sha[:12]} (term#{term_index})"))

        for sha, msg in _tag_messages(root):
            m = matcher.search(msg)
            if m:
                term_index = _term_index_for_match(m.group(0), terms)
                findings.append(Finding("history", "finding", f"tag message {sha[:12]} (term#{term_index})"))

    except GitError as e:
        return False, [Finding("history", "error", f"history enumeration error: {e}")], []

    ok = len(findings) == 0
    return ok, findings, denylist_findings


# =================================================================================================
# Scope 3 -- tracked-partition (pure path enumeration: index + every ref's tree)
# =================================================================================================
def _tracked_paths_index(root):
    out = _git_text(["ls-files", "-z"], root, check=True)
    return [p for p in out.split("\x00") if p]


def _all_refs(root):
    out = _git_text(["for-each-ref", "--format=%(refname)"], root, check=True)
    return [l for l in out.splitlines() if l]


def _tracked_paths_ref(root, ref):
    p = _git(["ls-tree", "-r", "--name-only", "-z", ref], root)
    if p.returncode != 0:
        raise GitError(f"git ls-tree -r {ref} failed: {p.stderr.decode('utf-8', 'replace')}")
    return [x for x in p.stdout.decode("utf-8", "replace").split("\x00") if x]


def tracked_partition_scope(root, known_bad_shas_relpath, remote_url, has_remote_error, matcher=None):
    """Enumerate every runtime-partition path present in the index or the tree of ANY ref
    (AC-RGLS-14). Any such path outside the designed-tracked set is a finding on its own,
    independent of content. If the recorded-bad-SHA file is ITSELF tracked on any ref and any
    SHA it lists classifies as resolved against the remote, that is an additional finding."""
    findings = []
    try:
        tracked = set(_tracked_paths_index(root))
        for ref in _all_refs(root):
            tracked.update(_tracked_paths_ref(root, ref))
    except GitError as e:
        return False, [Finding("tracked-partition", "error", f"enumeration error: {e}")]

    runtime_paths = sorted(p for p in tracked if p == ".foundry" or p.startswith(".foundry/"))
    for p in runtime_paths:
        if p not in DESIGNED_TRACKED_SET:
            findings.append(Finding("tracked-partition", "finding", f"{_redact_path_text(p, matcher)} is tracked (ignore rules do not apply to tracked files)"))

    if known_bad_shas_relpath in tracked and not has_remote_error and remote_url is not None:
        try:
            content = _git_text(["show", f"HEAD:{known_bad_shas_relpath}"], root, check=False)
        except GitError:
            content = ""
        shas = [l.strip() for l in content.splitlines() if SHA_RE.match(l.strip())]
        for sha in shas:
            outcome, _ = probe_sha(remote_url, sha)
            if outcome == "resolved":
                findings.append(
                    Finding(
                        "tracked-partition",
                        "finding",
                        f"{known_bad_shas_relpath} is tracked AND lists a SHA that resolves ({sha[:12]}) -- a published recovery index",
                    )
                )

    ok = len(findings) == 0
    return ok, findings


# =================================================================================================
# Scope 4 -- remote probe (sink-validated, hermetic, disposable, positive-controlled)
# =================================================================================================
def validate_object_name(value):
    return bool(SHA_RE.match(value or ""))


def _configured_remote_names(root):
    out = _git_text(["remote"], root, check=False)
    return set(l.strip() for l in out.splitlines() if l.strip())


def _remote_url_raw(root, name):
    """BLOCK (a): read the RAW configured value of `remote.<name>.url` via `git config --get`,
    never `git remote get-url` -- the latter EXPANDS `url.*.insteadOf` (documented git behaviour),
    so a `[url "http://attacker.example/"] insteadOf = https://github.com/` entry in the SCANNED
    repository's own `.git/config` would silently redirect every subsequent probe to the attacker
    host while `resolve_remote` still believed it was validating the real, configured URL. `git
    config --get` returns the literal stored value, with no insteadOf substitution."""
    p = _git(["config", "--get", f"remote.{name}.url"], root)
    if p.returncode != 0:
        return None
    url = p.stdout.decode("utf-8", "replace").strip()
    return url or None


def _is_local_path_escape_hatch(value):
    """A narrow, EXPLICIT escape hatch (BLOCK (b)) for local bare-repo remotes used by this test
    suite (and legitimately by some adopters testing against a local mirror): an absolute
    filesystem path to an EXISTING directory. Not `file://`, not a relative path, not anything a
    remote value could conjure without the scanned repository's own `.git/config` already pointing
    at a real, already-present directory on this machine."""
    return isinstance(value, str) and value.startswith("/") and os.path.isdir(value)


def _resolve_configured_remote_url(root, name):
    """BLOCK (b): a CONFIGURED remote's raw URL must pass the SAME allow-list as a directly-passed
    `--remote` URL value (https://, ssh://, an scp-like [user@]host:path -- never a transport-helper
    form, http://, file://, or a bare path), with only the narrow local-path escape hatch above.
    Previously this allow-list was applied ONLY on the directly-passed-URL branch, so a configured
    remote could resolve to `file://`, a bare path, `http://`, or any other transport form entirely
    unchecked."""
    url = _remote_url_raw(root, name)
    if not url:
        return None, f"configured remote {name!r} has no resolvable URL"
    if _is_transport_helper_syntax(url):
        return None, f"configured remote {name!r} resolves to disallowed transport-helper syntax: {url!r}"
    if url_is_allowed_form(url):
        return url, None
    if _is_local_path_escape_hatch(url):
        return url, None
    return None, f"configured remote {name!r} resolves to a disallowed URL form: {url!r}"


def resolve_remote(root, remote_value):
    """AC-RGLS-9a: use a remote value only if it is a configured remote NAME of the scanned
    repository, or a URL of https://, ssh:// or git@ form (or the narrow local-path escape hatch,
    for a CONFIGURED remote only). Returns (url, error, remote_name) -- `remote_name` is the
    configured remote NAME that resolved the URL, or None when `remote_value` was used as a literal
    URL (Risk #1: lets callers print which remote/host a scope actually probed)."""
    if not remote_value:
        configured = sorted(_configured_remote_names(root))
        if not configured:
            return None, "no remote configured", None
        if "origin" in configured:
            remote_value = "origin"
        elif len(configured) == 1:
            remote_value = configured[0]
        else:
            # Risk #1: a repo with e.g. {backup, fork, origin} and no --remote must never silently
            # probe an arbitrarily-chosen one (`sorted(...)[0]`) -- that is a FINDING, not a pick.
            return (
                None,
                f"multiple remotes configured ({', '.join(configured)}) and none is named 'origin' "
                "-- ambiguous; pass --remote explicitly",
                None,
            )

    configured = _configured_remote_names(root)
    if remote_value in configured:
        url, err = _resolve_configured_remote_url(root, remote_value)
        if err is not None:
            return None, err, remote_value
        return url, None, remote_value

    if url_is_allowed_form(remote_value):
        return remote_value, None, None

    return (
        None,
        f"remote value {remote_value!r} is neither a configured remote name nor an https://, "
        "ssh://, or scp-like ([user@]host:path) URL",
        None,
    )


# --------------------------------------------------------------------------------------------
# scp-like remote form -- git's own precedence order (feat-foundry-leak-scan-scp-remote-form).
# --------------------------------------------------------------------------------------------
def _is_transport_helper_syntax(value):
    """Terminology: a remote value containing '::' at any position before its first '/' (or
    containing '::' and no '/' at all). Deliberately BROADER than git's own helper-name class --
    no enumeration, no character class for the helper name -- and MUST be evaluated FIRST, before
    any other test. `ext::sh -c whoami` has no '/' before its first ':', so it also satisfies the
    scp-like shape below; if this check did not run first, that value would be admitted."""
    dcolon_idx = value.find("::")
    if dcolon_idx == -1:
        return False
    slash_idx = value.find("/")
    return slash_idx == -1 or dcolon_idx < slash_idx


def _is_scheme_bearing(value):
    """Terminology: a remote value matching `^[A-Za-z][A-Za-z0-9+.-]*://`."""
    return bool(SCHEME_BEARING_RE.match(value))


def _is_scp_like_shape(value):
    """Terminology: neither transport-helper syntax nor scheme-bearing, contains at least one
    ':', and has no '/' before the first ':'. SHAPE only -- does not by itself decide acceptance
    (a shape match still has to pass the authority-segment pattern and the character bound in
    `_scp_like_is_allowed`). Every `<scheme>://` URL also satisfies this shape in isolation
    (`file:///tmp/x` has no '/' before its ':' and a "host part" of `file`) -- callers MUST decide
    scheme-bearing values via `_is_scheme_bearing` before ever reaching this function."""
    if _is_transport_helper_syntax(value) or _is_scheme_bearing(value):
        return False
    colon_idx = value.find(":")
    if colon_idx == -1:
        return False
    slash_idx = value.find("/")
    return slash_idx == -1 or colon_idx < slash_idx


def _scp_like_authority(value):
    """Terminology: the authority segment -- for an scp-like value, the text before the first ':'."""
    return value.split(":", 1)[0]


def _scp_like_host_part(authority):
    """Terminology: the host part -- the authority segment with everything up to and including its
    LAST '@' removed (OpenSSH splits the user from the host at the last '@'; a value with no '@'
    has a host part equal to its authority segment)."""
    idx = authority.rfind("@")
    return authority[idx + 1 :] if idx != -1 else authority


def _has_disallowed_chars(value):
    """Whitespace (any kind) or a C0/C1 control character anywhere in the full value. Defence in
    depth (Clarifications): git itself hands almost any pre-colon text to SSH, but the value
    reaches an argv position in a subprocess, so whitespace/shell-metacharacter-adjacent/control
    content is refused here even though `--` (AC-SCP-5) already precedes every positional arg."""
    if any(ch.isspace() for ch in value):
        return True
    return bool(_C0_C1_CONTROL_RE.search(value))


def _scp_like_is_allowed(value):
    """AC-SCP-1: accept an scp-like value only when its authority segment fullmatches the
    ASCII-anchored host-shape pattern AND the whole value contains no whitespace/control char."""
    if not _is_scp_like_shape(value):
        return False
    if _has_disallowed_chars(value):
        return False
    authority = _scp_like_authority(value)
    return bool(SCP_LIKE_AUTHORITY_RE.match(authority))


def url_is_allowed_form(value):
    """The allow-list predicate, in git's own precedence order (AC-SCP-1..3):
      1. transport-helper syntax -- rejected first, generically, by rule.
      2. scheme-bearing -- decided ONLY by the pre-existing scheme allow-list (URL_SCHEME_RE):
         https:// and ssh:// accepted, file:///http:///git:// (and anything else) rejected.
      3. otherwise, the scp-like rule: [user@]host:path, host-shape validated.
    """
    value = value or ""
    if _is_transport_helper_syntax(value):
        return False
    if _is_scheme_bearing(value):
        return bool(URL_SCHEME_RE.match(value))
    return _scp_like_is_allowed(value)


def redact_url_to_host(url):
    """Render a URL down to just its host (or a local-path marker, or an scp-like alias label) --
    never the full URL, which may embed a credentialed form like
    `https://x-access-token:<token>@host/...` (Risk #6). Scheme dispatch runs FIRST (AC-SCP-4): an
    scp-like branch placed ahead of the scheme branches would render `https://h/p` as host
    `https`, destroying the host signal for forms that were already accepted before this atom."""
    if not url:
        return "?"
    m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)", url)
    if m:
        return m.group(1)
    m = re.match(r"^ssh://(?:[^@/]+@)?([^/]+)", url)
    if m:
        return m.group(1)
    if url.startswith("/"):
        return "local-path"
    if _is_scp_like_shape(url):
        # AC-SCP-4: an scp-like host may be an ~/.ssh/config Host alias whose real HostName,
        # Port and ProxyCommand live in a file this scan never reads -- render it distinguishably
        # as an unresolved alias, never as a proven endpoint.
        host_part = _scp_like_host_part(_scp_like_authority(url))
        return f"{host_part} (unresolved alias)"
    return "unrecognized-url-form"


_REFUSAL_MARKERS = (
    "does not allow request for unadvertised object",
    "not our ref",
    "no such remote ref",
    "reference is not a tree",
    "remote does not support",
)


def classify_probe_result(returncode, stderr_text):
    """Pure classifier over an already-executed fetch's outcome (AC-RGLS-9d): resolved / refused
    / error. Never classifies anything but a clean refusal as "refused" -- every other non-zero
    outcome (network failure, bad URL, auth failure, ...) is "error", never "clean"."""
    if returncode == 0:
        return "resolved"
    low = (stderr_text or "").lower()
    if any(marker in low for marker in _REFUSAL_MARKERS):
        return "refused"
    return "error"


def build_probe_argv(disposable_dir, url, sha):
    """AC-RGLS-9b: argv vector (never a shell string), the per-invocation hardening overrides,
    protocol v0 (the documented, deterministic want-negotiation semantics the classification above
    depends on). BLOCK (c): `--` precedes BOTH positional arguments (`<url> <sha>`), so an
    attacker-controlled `url` (e.g. one starting with `-`) can never land in git's OPTION position
    ahead of `--` -- the previous `fetch <flags> <url> -- <sha>` shape put `url` there."""
    return [
        "git",
        "-C",
        disposable_dir,
        "-c",
        "protocol.version=0",
        "-c",
        "credential.helper=",
        "-c",
        "core.fsmonitor=",
        "-c",
        "core.sshCommand=ssh",
        "fetch",
        "--filter=blob:none",
        "--depth=1",
        "--no-write-fetch-head",
        "--",
        url,
        sha,
    ]


def run_probe_fetch(disposable_dir, url, sha, timeout=30):
    """AC-RGLS-9c: hermetic -- runs entirely inside `disposable_dir` (a fresh bare store outside
    the scanned repository), writing no object/ref/FETCH_HEAD into the scanned repository."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        p = subprocess.run(
            build_probe_argv(disposable_dir, url, sha),
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def probe_sha(url, sha, timeout=30):
    """Validate at the sink (AC-RGLS-9a), then fetch inside a fresh disposable store, classify,
    and remove the store before returning. Returns (outcome, detail)."""
    if not validate_object_name(sha):
        return "error", f"not a 40-hex object name: {sha!r}"
    disposable_dir = tempfile.mkdtemp(prefix="foundry-rgls-probe-")
    try:
        init = subprocess.run(["git", "init", "-q", "--bare", disposable_dir], capture_output=True)
        if init.returncode != 0:
            return "error", f"could not create disposable store: {init.stderr.decode('utf-8', 'replace')}"
        rc, _out, err = run_probe_fetch(disposable_dir, url, sha, timeout=timeout)
        outcome = classify_probe_result(rc, err)
        return outcome, err
    finally:
        shutil.rmtree(disposable_dir, ignore_errors=True)


# AC-LSH-2: the listing sink's hardening set, carried as per-invocation `-c` overrides on the
# REAL call (not the argv builder called in isolation). `core.sshCommand=ssh` and
# `protocol.ext.allow=never` are new here; the sibling probe sink already carries an equivalent
# set. Only `core.sshCommand` is a live execution vector at this sink today (verified 2026-07-29 --
# `core.fsmonitor` does not fire under `ls-remote`, which touches no index); the rest are kept for
# uniformity with the sibling sinks and defence in depth, and are named here so a future reader
# does not mistake them for decoration.
_LS_REMOTE_HARDENING_SET = [
    "-c",
    "credential.helper=",
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.sshCommand=ssh",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "protocol.version=0",
]

# AC-LSH-6: variables removed from the sink environment BY SUBTRACTION from the ambient
# environment (never a fixed allow-list built up from scratch, so nothing this atom does not know
# about is dropped by construction). `GIT_DIR` IS the repository regardless of anchor/cwd/-C;
# `GIT_SSH_COMMAND` OUTRANKS the `-c core.sshCommand=ssh` override above (both verified
# 2026-07-29 by execution). Neither is reachable by an adversary holding only the scanned
# repository's `.git/config`, so neither is in this atom's threat model -- they are removed
# because the capability this atom claims is otherwise not true, and because a `pre-push`-hook
# wiring of the scan puts `GIT_DIR` into the environment as a matter of course.
_LS_REMOTE_SINK_ENV_REMOVED_VARS = (
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


def _ls_remote_sink_env():
    """AC-LSH-6: the sink environment -- the ambient environment with `GIT_TERMINAL_PROMPT=0`
    pinned and every member of `_LS_REMOTE_SINK_ENV_REMOVED_VARS` removed, individually, by
    subtraction."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    for var in _LS_REMOTE_SINK_ENV_REMOVED_VARS:
        env.pop(var, None)
    return env


def build_ls_remote_argv(url, heads_and_tags_only):
    """BLOCK (c): argv vector, `--` before the URL, so an attacker-controlled remote value can
    never land in git's OPTION position (the previous `ls-remote --heads --tags <url>` shape had no
    `--` at all). AC-LSH-2: also carries the hardening set as per-invocation `-c` overrides.
    Signature UNCHANGED (`url`, `heads_and_tags_only`) -- the anchor is applied by the caller as
    the invocation's `cwd=`, never as an argv token here, so this builder's existing two-argument
    signature (and the sibling atom's `--`-placement test that calls it) is undisturbed."""
    argv = ["git"] + _LS_REMOTE_HARDENING_SET + ["ls-remote"]
    if heads_and_tags_only:
        argv += ["--heads", "--tags"]
    argv += ["--", url]
    return argv


def _ls_remote_anchor():
    """AC-LSH-1: a fresh, disposable, INITIALIZED bare repository created for THIS invocation
    only -- never the scanned repository, nor a descendant or ancestor of it. `git init --bare` is
    not decoration: an uninitialized `mkdtemp()` merely relocates git's upward repository
    discovery (verified 2026-07-29 -- with a valid gitdir planted in a bare `mkdtemp()`'s PARENT,
    `git -C <that mkdtemp>` adopted it and returned its `core.sshCommand`). After `git init
    --bare`, `rev-parse --git-dir` resolves to the anchor itself and discovery terminates there.
    Returns the anchor path, or None if it could not be initialized (caller treats that as the
    sink failing closed -- an empty listing, never a fall-back to an unanchored invocation)."""
    anchor = tempfile.mkdtemp(prefix="foundry-rgls-lsremote-")
    try:
        init = subprocess.run(
            ["git", "init", "-q", "--bare", anchor],
            cwd=anchor,
            capture_output=True,
            env=_ls_remote_sink_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        shutil.rmtree(anchor, ignore_errors=True)
        return None
    if init.returncode != 0:
        shutil.rmtree(anchor, ignore_errors=True)
        return None
    return anchor


def _ls_remote(url, heads_and_tags_only, timeout=30):
    anchor = _ls_remote_anchor()
    if anchor is None:
        return []
    try:
        p = subprocess.run(
            build_ls_remote_argv(url, heads_and_tags_only),
            cwd=anchor,
            capture_output=True,
            env=_ls_remote_sink_env(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    finally:
        shutil.rmtree(anchor, ignore_errors=True)
    if p.returncode != 0:
        return []
    shas = []
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        bits = line.split()
        if len(bits) == 2 and SHA_RE.match(bits[0]):
            shas.append(bits[0])
    return shas


def _advertised_tips(url, timeout=30):
    """Refs limited to `refs/heads` and `refs/tags` -- the CANDIDATE SOURCE for control-object
    parents (branches/tags are the objects worth fetching and inspecting for a `parent` header)."""
    return _ls_remote(url, heads_and_tags_only=True, timeout=timeout)


def _advertised_all(url, timeout=30):
    """Risk #3: the FULL advertisement (every ref the remote lists at all -- not just
    `refs/heads`/`refs/tags`). A host such as GitHub also advertises `refs/pull/*/head` and other
    non-heads/tags refs; a candidate excluded only against `_advertised_tips` could still be
    advertised via one of those, which would make the positive control validate tip-reachable want
    handling (the exact failure shape AC-RGLS-10 rejects) rather than genuinely dangling-object
    handling."""
    return set(_ls_remote(url, heads_and_tags_only=False, timeout=timeout))


def find_unadvertised_control_object(url, timeout=30):
    """AC-RGLS-10: locate one object the remote holds but does NOT advertise via ANY ref (Risk #3:
    checked against the FULL advertisement, not just heads/tags) -- a parent of an advertised
    branch/tag commit. Fetches only the (small) advertised tip commit objects (never their blobs,
    via --filter=blob:none) into a disposable store to read each one's own `parent` header. Returns
    the candidate SHA, or None if none is constructible (a genuinely-dangling object cannot be
    proven to exist -- the caller must then treat the scope as INDETERMINATE, never clean)."""
    tips = _advertised_tips(url, timeout=timeout)
    if not tips:
        return None
    all_advertised = _advertised_all(url, timeout=timeout)
    disposable_dir = tempfile.mkdtemp(prefix="foundry-rgls-control-")
    try:
        init = subprocess.run(["git", "init", "-q", "--bare", disposable_dir], capture_output=True)
        if init.returncode != 0:
            return None
        for tip in tips:
            rc, _out, _err = run_probe_fetch(disposable_dir, url, tip, timeout=timeout)
            if rc != 0:
                continue
            p = subprocess.run(["git", "-C", disposable_dir, "cat-file", "-p", tip], capture_output=True)
            if p.returncode != 0:
                continue
            for line in p.stdout.decode("utf-8", "replace").splitlines():
                if line.startswith("parent "):
                    candidate = line.split()[1]
                    if SHA_RE.match(candidate) and candidate not in all_advertised:
                        return candidate
        return None
    finally:
        shutil.rmtree(disposable_dir, ignore_errors=True)


def remote_probe_scope(root, remote_value, known_bad_entries, known_bad_shas_display=None):
    """Returns (ok, findings, url_or_None, had_error, remote_name, probe_count). `ok` requires: a
    resolvable remote, the positive control resolving an unadvertised object, and every
    recorded-bad SHA classifying as "refused" (never "error"). `known_bad_entries` is an iterable
    of (sha, line_no) pairs (`line_no` may be None) -- Risk #5: the message for a resolving
    recorded-bad SHA never prints the full 40-hex object name, only a 12-char prefix plus a
    pointer to the recorded-bad-SHA file/line, since the full object name IS the recovery index.
    `probe_count` (Risk #1) is the number of actual `probe_sha` calls made (the positive control
    plus each recorded-bad SHA reached), so a status line can prove the scope actually touched the
    repository rather than short-circuiting silently."""
    url, err, remote_name = resolve_remote(root, remote_value)
    if url is None:
        return False, [Finding("remote-probe", "error", f"unreachable remote: {err}")], None, True, remote_name, 0

    control_sha = find_unadvertised_control_object(url)
    if control_sha is None:
        return (
            False,
            [Finding("remote-probe", "error", "positive control indeterminate: no unadvertised object could be constructed")],
            url,
            True,
            remote_name,
            0,
        )
    outcome, _detail = probe_sha(url, control_sha)
    probe_count = 1
    if outcome != "resolved":
        # Risk #6: never echo raw git stderr (`_detail`) here -- it may embed a credentialed
        # remote URL (e.g. https://x-access-token:<token>@host/...). Classification + a host-only
        # rendering is all a finding ever needs.
        return (
            False,
            [Finding("remote-probe", "error", f"positive control did not resolve (outcome={outcome}, host={redact_url_to_host(url)})")],
            url,
            True,
            remote_name,
            probe_count,
        )

    findings = []
    had_error = False
    host = redact_url_to_host(url)
    for sha, line_no in known_bad_entries:
        outcome, _detail = probe_sha(url, sha)
        probe_count += 1
        if outcome == "resolved":
            where = f"{known_bad_shas_display} line {line_no}" if known_bad_shas_display and line_no is not None else (known_bad_shas_display or "the recorded-bad-shas file")
            findings.append(
                Finding(
                    "remote-probe",
                    "finding",
                    f"recorded-bad SHA resolves against the remote (host={host}): {sha[:12]} -- see {where}",
                )
            )
        elif outcome == "refused":
            continue
        else:
            had_error = True
            # Risk #6: classification + host only, never the raw stderr (`_detail`).
            findings.append(Finding("remote-probe", "error", f"probe error for {sha[:12]} (host={host}, classification={outcome})"))

    ok = (not findings) and (not had_error)
    return ok, findings, url, had_error, remote_name, probe_count


# =================================================================================================
# recorded-bad-SHA file loading
# =================================================================================================
def load_recorded_bad_shas(path):
    """Returns (shas, error) -- `shas` is a plain list of 40-hex strings (the API existing callers
    use), and error is None + shas == [] for a valid (possibly empty) file. Absent/unreadable file,
    or any non-blank/non-comment/non-40-hex line, is an error."""
    entries, err = load_recorded_bad_sha_entries(path)
    if err is not None:
        return None, err
    return [sha for sha, _line_no in entries], None


def load_recorded_bad_sha_entries(path):
    """Returns (entries, error) where `entries` is a list of (sha, line_no) pairs -- Risk #5: the
    line number lets a finding point at "see <path> line N" instead of printing the full 40-hex
    object name (the object name IS the recovery index)."""
    if not os.path.isfile(path):
        return None, (
            f"recorded-bad-SHA file not found at {path} -- create it (an empty file is a valid "
            "statement: nothing currently recorded as bad; the applier stubs this on first run)"
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return None, f"recorded-bad-SHA file at {path} is unreadable: {e}"
    except UnicodeDecodeError as e:
        return None, f"recorded-bad-SHA file at {path} is undecodable: {e}"
    entries = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not SHA_RE.match(s):
            return None, f"recorded-bad-SHA file at {path} line {i} is neither blank, a comment, nor a 40-hex object name: {s!r}"
        entries.append((s, i))
    return entries, None


# =================================================================================================
# orchestration
# =================================================================================================
def run_scan(root, remote_value, known_bad_shas_path, denylist_path, denylist_is_override):
    root = os.path.abspath(root)
    status_lines = []
    all_findings = []
    denylist_origin_findings = []
    overall_ok = True

    try:
        leak_scan_mod = _load_shared_module()
    except Exception as e:
        print(f"foundry-prepublication-leak-scan: could not load the shared module at {SHARED_MODULE_PATH}: {e}", file=sys.stderr)
        # Risk #8: a consumer keying on the verdict sentinel must never see a bare non-zero exit
        # with no sentinel at all on this early-exit path -- that fails OPEN for such a consumer.
        print("PREPUB-LEAK-SCAN-FOUND")
        return 1

    try:
        terms = leak_scan_mod.load_denylist(denylist_path)
    except leak_scan_mod.DenylistError as e:
        print(f"foundry-prepublication-leak-scan: denylist error: {e}", file=sys.stderr)
        print("PREPUB-LEAK-SCAN-FOUND")  # Risk #8
        return 1
    matcher = leak_scan_mod.build_matcher(terms)

    label = " (--denylist override, NON-DEFAULT)" if denylist_is_override else " (default)"
    print(f"foundry-prepublication-leak-scan: denylist={denylist_path}{label} terms={len(terms)}")
    print(f"foundry-prepublication-leak-scan: excluding the denylist file itself from every scope's corpus (by resolved real path / by history path)")

    # AC-RGLS-13 -- gates independently of the four scopes.
    frag_finding = fragment_coverage_finding(root)
    if frag_finding is not None:
        all_findings.append(frag_finding)
        overall_ok = False
        status_lines.append(f"SCOPE fragment-coverage: FOUND -- {frag_finding.message}")
    else:
        status_lines.append("SCOPE fragment-coverage: clean")

    # ---- scope 1: working tree (the shared module already excludes the denylist file itself,
    # by resolved real path, from its own walk -- AC-RGLS-12b) ----
    wt_ok, wt_findings, _denylist_realpath = working_tree_scope(root, denylist_path, terms, matcher, leak_scan_mod)
    all_findings.extend(wt_findings)
    overall_ok = overall_ok and wt_ok
    status_lines.append(f"SCOPE working-tree: {'clean' if wt_ok else 'FOUND'}")

    # ---- scope 2: history ----
    denylist_relpaths = {os.path.relpath(denylist_path, root).replace(os.sep, "/")}
    try:
        with open(denylist_path, "rb") as fh:
            denylist_content = fh.read()
    except OSError:
        denylist_content = None
    # Is the denylist a file OF the scanned repository? Post-feat-foundry-denylist-out-of-tree it is
    # not, in either the CI or the local configuration -- so a historical blob matching it is a
    # finding, not an expected artifact. Computed by containment, never assumed.
    _root_real = os.path.realpath(root)
    denylist_inside_root = os.path.realpath(denylist_path).startswith(_root_real + os.sep)
    hist_ok, hist_findings, hist_denylist_findings = history_scope(
        root, denylist_relpaths, terms, matcher, denylist_content=denylist_content,
        denylist_inside_root=denylist_inside_root)
    all_findings.extend(hist_findings)
    denylist_origin_findings.extend(hist_denylist_findings)
    overall_ok = overall_ok and hist_ok
    status_lines.append(f"SCOPE history (blobs + commit/tag messages): {'clean' if hist_ok else 'FOUND'}")

    # ---- recorded-bad-SHA file ----
    known_bad_relpath = os.path.relpath(known_bad_shas_path, root).replace(os.sep, "/")
    known_bad_entries, kbs_err = load_recorded_bad_sha_entries(known_bad_shas_path)
    if kbs_err is not None:
        all_findings.append(Finding("recorded-bad-shas", "error", kbs_err))
        overall_ok = False
        status_lines.append(f"SCOPE recorded-bad-shas: FOUND -- {kbs_err}")
        known_bad_entries = []
    else:
        status_lines.append(f"SCOPE recorded-bad-shas: clean ({len(known_bad_entries)} SHA(s) loaded from {known_bad_shas_path})")

    # ---- scope 4: remote probe (resolved first so scope 3's second clause can reuse the URL) ----
    remote_ok, remote_findings, remote_url, remote_had_error, remote_name, probe_count = remote_probe_scope(
        root, remote_value, known_bad_entries, known_bad_shas_display=known_bad_relpath
    )
    all_findings.extend(remote_findings)
    overall_ok = overall_ok and remote_ok
    # Risk #1: name the remote/host/probe-count actually touched -- a default-remote pick or a
    # scope that never got as far as a single probe must never look identical to one that did.
    status_lines.append(
        f"SCOPE remote-probe: {'clean' if remote_ok else 'FOUND'} "
        f"(remote={remote_name or '<literal-url>'} url-host={redact_url_to_host(remote_url)} probes={probe_count})"
    )

    # ---- scope 3: tracked partition ----
    tp_ok, tp_findings = tracked_partition_scope(root, known_bad_relpath, remote_url, remote_had_error, matcher=matcher)
    all_findings.extend(tp_findings)
    overall_ok = overall_ok and tp_ok
    status_lines.append(f"SCOPE tracked-partition: {'clean' if tp_ok else 'FOUND'}")

    for line in status_lines:
        print(line)

    if denylist_origin_findings:
        print(f"DENYLIST-ORIGIN-HITS: {len(denylist_origin_findings)} (the denylist's own historical/working content matching its own terms -- expected, categorised separately, never counted toward the verdict)")
        for f in denylist_origin_findings:
            print(f"  {f}")

    for f in all_findings:
        print(f"  {f}")

    if overall_ok:
        print("PREPUB-LEAK-SCAN-CLEAN")
        return 0
    print("PREPUB-LEAK-SCAN-FOUND")
    return 1


def build_arg_parser():
    ap = argparse.ArgumentParser(description="Pre-publication leak scan: four scopes must run clean before a repository may be made public.")
    ap.add_argument("--root", default=".", help="root of the repository to scan (default: .)")
    ap.add_argument("--remote", default=None, help="configured remote NAME of the scanned repository, or an https://, ssh:// or git@ URL")
    ap.add_argument(
        "--known-bad-shas",
        default=None,
        help="path to the recorded-bad-SHA file (default: <root>/.foundry/leak-scan/known-bad-shas.txt)",
    )
    ap.add_argument("--denylist", default=None, help="denylist path. The default resolves OUTSIDE this tree, from $CLAUDE_PROJECT_DIR/" + DENYLIST_WORKSPACE_RELPATH + " -- the list is not shipped in this repository.")
    return ap


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    root = os.path.abspath(args.root)
    known_bad_shas_path = args.known_bad_shas or os.path.join(root, DEFAULT_KNOWN_BAD_SHAS_RELPATH)
    denylist_path = os.path.realpath(args.denylist) if args.denylist else default_denylist_path()
    if not denylist_path:
        print("foundry-prepublication-leak-scan: no denylist. The term list is not shipped in this\n"
              "repository; set CLAUDE_PROJECT_DIR to the private workspace, or pass --denylist.\n"
              "Refusing rather than scanning against an empty term set (fail-closed).", file=sys.stderr)
        # Risk #8 -- EVERY early exit emits the verdict sentinel ON STDOUT. A consumer keying on
        # `... | grep -q PREPUB-LEAK-SCAN-FOUND` reads stdout only; a refusal that spoke solely to
        # stderr would present as "no FOUND token" and be read as not-found, letting a visibility
        # flip proceed with no scan having run. The two pre-existing early exits already do this;
        # this atom added a third and must not be the one that reopens the hole.
        print("PREPUB-LEAK-SCAN-FOUND")
        return 1
    return run_scan(root, args.remote, known_bad_shas_path, denylist_path, denylist_is_override=bool(args.denylist))


if __name__ == "__main__":
    sys.exit(main())
