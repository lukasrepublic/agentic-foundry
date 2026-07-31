#!/usr/bin/env python3
"""foundry-upstream-submit — adopter→framework enhancement-request channel (feat-foundry-upstream-enhancement-request).

A Foundry adopter discovers a learning that belongs UPSTREAM in the framework (per the
`upstream-learnings/PROCESS.md` triage filters). This verb ships the adopter-side half of that
pipeline, cleaved at the trust boundary:

  generify  →  sanitize (fail-closed leak scan)  →  provenance + dedup key  →  gh ISSUE

The enhancement request (ER) is a *request*, not a contribution-to-`main`: it is filed as a
GitHub ISSUE against the upstream foundry repo, entering UPSTREAM of `/foundry:intake` (a new
pluggable intake source). The maintainer's own gated factory turns an accepted ER into a spec —
so an adopter request can never reach foundry `main` un-authorized (floor #1 preserved). This
module therefore contains NO pull-request / push / merge surface (AC-URSUBMIT-4, self-scanned).

Sanitization is THREE-LAYERED (AC-URSUBMIT-1) and the scan is fail-closed:
  1. generify  — restate as the mechanism, not the incident (operator does this in the candidate);
  2. scan      — refuse on any built-in floor pattern (structural Foundry-ecosystem ref shapes)
                 OR any adopter-declared token from .foundry/upstream-redaction.txt;
  3. eyes-on   — dry-run is the DEFAULT; nothing is filed without an explicit --create.
The scan is a FLOOR, not a ceiling: it catches known shapes/tokens, not a novel proprietary noun.
Same honest threat model as UL-0006 — protects a *cooperating* operator from an accidental leak,
not an adversarial agent.

  foundry-upstream-submit.py --candidate <er.md> [--repo o/r] [--adopter L] [--foundry-version V]
  foundry-upstream-submit.py --candidate <er.md> --create     # actually file the issue (after eyes-on)
  foundry-upstream-submit.py --selftest                       # exit 0 green / 1 red

Label bootstrap (feat-foundry-upstream-submit-first-use, AC-URFIRST-1..5): the label the ER issue
template declares (`enhancement-request`) is assumed to exist on the target repo and is
bootstrapped nowhere upstream of this module, so a missing label used to abort `gh issue create`
under `check=True` and surface an unhandled `CalledProcessError` traceback. This module now
ensures the label idempotently BEFORE filing via PROBE-THEN-CREATE — a read-only existence probe
first, a create only when the probe reports the label absent — a pure create-or-noop that makes NO
write call on the already-exists path (deliberately not `gh label create --force`, which is
create-or-**UPDATE** and PATCHes an existing label's colour/description on every privileged run
even when nothing needs to change — an unintended write to the maintainer's repo). If the ensure
does not succeed, the verb degrades to an UNLABELLED issue with a stderr warning (the common case —
an adopter usually holds issue-create but not label-write permission on a repo they do not own)
rather than aborting. Every `gh` invocation — label-probe, label-create, and issue-create alike —
funnels through ONE injectable runner seam (`_real_gh_runner` by default), so `--selftest` can
substitute a hermetic recording double and assert ordering, argv, and failure mapping without a
network call. The seam itself refuses any argv that is not exactly `gh issue create` / `gh label
create` / the one read-only `gh api` label-GET probe shape (a POSITIVE allowlist, fail-closed by
construction). The module's self-scan (`_forbidden_verbs_present` + `_count_spawn_calls`, an AST
walk, not a text/regex match) asserts the module contains exactly one process-spawning call site,
that it lives inside that seam, and that no contribution verb (PR/push/`gh api PUT`) appears in
real code.

Declared-identity composition (feat-foundry-upstream-submit-identity, AC-URIDENT-1..9, issue #21
item b): every `gh` invocation escapes the PreToolUse(Bash) guard the workspace template ships and
wires, because the spawned command string (`python3 …foundry-upstream-submit.py`) carries no
standalone `gh` token — so it used to run under whatever ambient `GH_CONFIG_DIR`/token/host
environment the session happened to inherit, and the printed command carried no identity at all.
The module now reads `<project root>/.claude/gh-identity` (the same whitespace-deleting read both
guard variants perform); with a declared handle that matches GitHub's handle grammar, every `gh`
invocation through the runner seam gets `GH_CONFIG_DIR=$HOME/.config/gh-<handle>` and has
`GH_TOKEN`/`GITHUB_TOKEN`/`GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN`/`GH_HOST` removed (those
outrank config-dir auth), and every printed command carries the matching `shlex.quote`d prefix. A
malformed declaration (present, readable, non-empty, grammar-failing) REFUSES — exit 4, zero `gh`
invocations, the offending string rendered nowhere — rather than degrading to ambient. With no
identity declared, prior behaviour is preserved exactly: the shipped guard is dormant in that
state by design.

Exit: 0 = ok (dry-run rendered / issue filed, possibly unlabelled / selftest green);
1 = selftest red / input error; 2 = REFUSED (leak detected — no issue, no network call, no
label-ensure either); 3 = a `gh` invocation (label-ensure or issue-create) failed or `gh` is
absent/not executable — a typed diagnostic is written, never a Python traceback; 4 = the declared
identity (`.claude/gh-identity`) is present but does not match the handle grammar — a typed
refusal, never a fall back to ambient. Fail-closed throughout.
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import inspect
import os
import re
import shlex
import shutil
import sys
import tempfile

# The canonical upstream foundry repo (overridable with --repo). Filing always targets an ISSUE here.
DEFAULT_UPSTREAM_REPO = "lukasrepublic/agentic-foundry"

# The label the shipped ER issue template declares (feat-foundry-upstream-submit-first-use).
_LABEL_NAME = "enhancement-request"
_LABEL_COLOR = "c5def5"
_LABEL_DESCRIPTION = "Filed via /foundry:upstream-submit (adopter enhancement request)."

# The stable, greppable AC-URFIRST-2 degrade-event token — adopter automation may count on it.
_DEGRADE_TOKEN = "UPSTREAM-SUBMIT-LABEL-DEGRADED"

# GhResult — the runner seam's uniform return shape (AC-URFIRST-5/-6): every gh invocation, real
# or doubled, returns one of these. `spawned=False` means the child process never ran at all
# (e.g. `gh` is absent/not executable) as distinct from ran-and-exited-non-zero.
GhResult = collections.namedtuple("GhResult", ["returncode", "stdout", "stderr", "spawned"])

# GitHub's published handle grammar (feat-foundry-upstream-submit-identity, AC-URIDENT-1):
# alphanumerics + single hyphens, no leading/trailing hyphen, <=39 chars. Admits no `/`, `.`, or
# whitespace, so a conforming handle can traverse no path and carry no shell metacharacter.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

# Precedence-carrying variables that outrank GH_CONFIG_DIR-stored auth (AC-URIDENT-2) — removed
# from the child environment whenever a declared identity pins GH_CONFIG_DIR, so no ambient
# variable of higher precedence can redirect a gh invocation to another account or host.
_AMBIENT_STRIP_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                        "GITHUB_ENTERPRISE_TOKEN", "GH_HOST")

# Required sections in a candidate ER (AC-URSUBMIT-2) — the generified structured shape that
# matches the foundry repo's enhancement-request issue template.
_REQUIRED_SECTIONS = (
    "Generalized problem",
    "Evidence (sanitized)",
    "Proposed mechanism",
    "Triage bucket",
)

# Floor patterns are ASSEMBLED FROM FRAGMENTS so no contiguous proprietary-trigger substring is
# shipped in this file: the CI leak gate is a plain grep that does NOT honor selfscan markers
# (UL-0001 residual), so spelling out a literal memory-marker / operator-id / handbook-id token
# here would self-trip it. The floor catches the *structural shapes* of Foundry-ecosystem
# proprietary refs; an adopter adds its own product names via .foundry/upstream-redaction.txt.
_MEM_MARKER = "[" + "memory:"
_FLOOR_PATTERNS = (
    r"op_" + r"[a-z]{2,}",      # operator-id shape (e.g. an op_<name> registry id)
    r"HBK" + r"-\d+",           # handbook-id shape
    re.escape(_MEM_MARKER),     # the memory citation marker
)

# Contribution verbs that, if present in THIS module, would breach "request, not contribution"
# (AC-URSUBMIT-4): the adopter side must never push/PR/merge into the upstream repo, nor mutate
# anything upstream via a raw `gh api` write. The self-scan asserts none appear in real code; the
# verb-list's OWN definition line is excluded from the scan (named marker below — narrowly scoped
# to just this tuple, not a generic per-line escape hatch other code could hide behind). The
# membership check is CASE-INSENSITIVE (`_forbidden_verbs_present`) so an upper-cased flag spelling
# (`gh`'s own convention for a single-letter option, `-X`) still fires against the lower-cased scan
# text instead of silently never matching — the two entries below spell that flag upper-case.
# (Deliberately paraphrased here rather than spelled contiguously in this comment block, so this
# very sentence doesn't itself trip the check it's describing — same discipline the floor patterns
# above use.)
_VERB_LIST_MARKER = "selfscan:verb-list-definition"
_FORBIDDEN_CONTRIB_VERBS = (
    "gh pr create", "gh pr merge", "git push", "gh api -X PUT", "gh api --method PUT",  # selfscan:verb-list-definition
)


# ------------------------------- pure logic -------------------------------- #

def normalize_problem(text: str) -> str:
    """Normalize a generalized problem statement for content-addressing (AC-URSUBMIT-3):
    lowercase + collapse all whitespace. Deterministic; carries no adopter identity."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def dedup_key(generalized_problem: str) -> str:
    """Stable dedup key = sha256 of the NORMALIZED generalized problem (12 hex chars).
    Two adopters filing the same normalized generalization collapse to one key; a re-submit of a
    known ER reproduces its key. Honest limit: collapses identical/normalized statements, not
    paraphrases — semantic dedup stays the maintainer's triage."""
    norm = normalize_problem(generalized_problem)
    return "ur-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def load_redaction_tokens(project_dir: str | None) -> list[str]:
    """Adopter-declared proprietary tokens from $CLAUDE_PROJECT_DIR/.foundry/upstream-redaction.txt
    (one literal/regex per line; blank + `#` lines ignored). Optional — the floor always applies."""
    root = project_dir or os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    path = os.path.join(root, ".foundry", "upstream-redaction.txt")
    if not os.path.exists(path):
        return []
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            s = ln.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out


def scan_leaks(body: str, extra_tokens: list[str] | None = None) -> list[str]:
    """Return the list of leak hits (floor patterns + adopter tokens) found in `body`,
    case-insensitively. Empty list == clean. This is the fail-closed gate (AC-URSUBMIT-1)."""
    hits: list[str] = []
    patterns = list(_FLOOR_PATTERNS) + [re.escape(t) if not _looks_like_regex(t) else t
                                        for t in (extra_tokens or [])]
    for pat in patterns:
        try:
            m = re.search(pat, body, re.IGNORECASE)
        except re.error:
            m = re.search(re.escape(pat), body, re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


def _looks_like_regex(tok: str) -> bool:
    """Heuristic: a redaction line is treated as a regex if it contains regex metacharacters,
    else as a literal (escaped). Keeps the common case (a plain product name) safe."""
    return any(c in tok for c in r".^$*+?()[]{}|\\")


def parse_candidate(text: str) -> dict:
    """Parse a candidate ER markdown into {title, sections{name: body}}. The title is the first
    `# ` heading; sections are `## ` headings. Raises ValueError on a missing required section."""
    lines = text.splitlines()
    title = ""
    sections: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for ln in lines:
        if ln.startswith("# ") and not title:
            title = ln[2:].strip()
        elif ln.startswith("## "):
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur = ln[3:].strip()
            buf = []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    missing = [s for s in _REQUIRED_SECTIONS if s not in sections or not sections[s]]
    if not title:
        missing.append("title (# heading)")
    if missing:
        raise ValueError("candidate is missing required section(s): " + ", ".join(missing))
    return {"title": title, "sections": sections}


def render_er(parsed: dict, adopter: str, foundry_version: str) -> str:
    """Render the structured ER body (AC-URSUBMIT-2) with a machine provenance footer
    (AC-URSUBMIT-3). The footer's dedup key is computed over the generalized problem only."""
    s = parsed["sections"]
    key = dedup_key(s["Generalized problem"])
    parts = [f"# {parsed['title']}", ""]
    for name in _REQUIRED_SECTIONS:
        parts += [f"## {name}", s[name], ""]
    parts += [
        "## Provenance (machine)",
        f"- adopter: {adopter}",
        f"- foundry-version: {foundry_version}",
        f"- dedup-key: {key}",
        "",
        "_Filed via `/foundry:upstream-submit`. This is an enhancement REQUEST (an intake "
        "source), not a contribution to `main` — acceptance is a maintainer-gated factory run._",
    ]
    return "\n".join(parts)


def build_issue_command(repo: str, title: str, body_path: str,
                         label: str | None = _LABEL_NAME) -> list[str]:
    """The exact `gh issue create` argv (AC-URSUBMIT-4: an ISSUE, never a PR/push).
    `label=None` omits `--label` entirely — the AC-URFIRST-2 degrade path."""
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", title]
    if label:
        cmd += ["--label", label]
    cmd += ["--body-file", body_path]
    return cmd


def build_label_probe_command(repo: str) -> list[str]:
    """The read-only label-existence probe (AC-URFIRST-1), the FIRST half of probe-then-create: a
    plain GET against the label endpoint (`gh api repos/{repo}/labels/{name}`, no method flag —
    `gh api` defaults to GET). Exit `0` means the label already exists (the ensure succeeds with
    NO further call — a zero-write no-op); non-zero (typically an HTTP 404) means it does not, and
    `build_label_command` runs next. This is a pure existence check: it reads, never writes."""
    return ["gh", "api", f"repos/{repo}/labels/{_LABEL_NAME}"]


def build_label_command(repo: str) -> list[str]:
    """The label-CREATE argv (AC-URFIRST-1), invoked ONLY when `build_label_probe_command` reports
    the label absent. Deliberately a plain create — never `--force` (the vendor's create-or-UPDATE
    affordance), which would PATCH an existing label's colour/description on every privileged run
    even when the label already exists and nothing needs to change. Probe-then-create is a pure
    create-or-noop: this call happens on the absent path only, never on the already-exists path."""
    return ["gh", "label", "create", _LABEL_NAME, "--repo", repo,
            "--color", _LABEL_COLOR, "--description", _LABEL_DESCRIPTION]


def _ensure_label(repo: str, run, env: dict | None) -> tuple[bool, str]:
    """AC-URFIRST-1's ensure, PROBE-THEN-CREATE: probe first via `run(build_label_probe_command,
    env)`; if the label already exists (probe exit 0), the ensure succeeds with NO create call —
    zero writes on the already-exists path. Only when the probe reports the label absent does this
    call `run(build_label_command, env)` (a plain create, never `--force`/create-or-update). The
    probe is a `gh` invocation like any other (AC-URIDENT-2/-4): it carries the SAME composed
    identity `env` as every other call this module makes, so a declared identity pins the probe to
    the isolated config dir exactly as it pins the create and the issue-create. Returns
    `(ok, failure_detail)`; `failure_detail` is the create call's captured stderr when the create
    itself failed, else the probe's, else empty."""
    probe = run(build_label_probe_command(repo), env)
    if probe.returncode == 0 and probe.spawned:
        return True, ""
    create = run(build_label_command(repo), env)
    if create.returncode == 0 and create.spawned:
        return True, ""
    detail = (create.stderr or probe.stderr or "").strip()
    return False, detail


# --------------------------- declared identity ------------------------------ #
# feat-foundry-upstream-submit-identity (AC-URIDENT-1..5, issue #21 item b): the framework tells
# the adopter to jail `gh` to a per-project identity (`.claude/gh-identity` + `~/.config/gh-<handle>`,
# enforced by the shipped PreToolUse(Bash) guard) — this composes the VERB's own `gh` invocations
# and printed commands with that same declared identity, since the verb's own subprocess call
# carries no standalone `gh` token and so escapes the guard entirely.

class MalformedIdentityError(Exception):
    """Raised by resolve_identity() when .claude/gh-identity exists, is readable, and normalizes
    to a non-empty string that does NOT match the handle grammar (AC-URIDENT-1) — a refusal, never
    a fall back to ambient. Carries only the identity file's PATH; the offending string itself is
    deliberately not attached as a rendering-ready value anywhere a diagnostic could echo it."""

    def __init__(self, identity_file: str):
        self.identity_file = identity_file
        super().__init__(f"malformed declared identity in {identity_file}")


def resolve_identity(project_dir: str) -> str | None:
    """Read the declared identity from `<project_dir>/.claude/gh-identity`, deleting all ASCII
    whitespace characters — the same `tr -d '[:space:]'` normalization both shipped guard variants
    and the seeded `.envrc` perform, which is ASCII-only (POSIX `[:space:]` in the C locale `tr`
    both guard variants run under). `re.sub(r"\\s+", ...)` is Unicode-aware and strips characters
    (e.g. NBSP `\\xa0`) that `tr -d '[:space:]'` does NOT, so a handle carrying one would normalize
    to a VALID handle here but to a DIFFERENT string in the guard — the verb would pin one account's
    jail while the guard derives another. The explicit ASCII-only class below keeps both sides
    agreeing on exactly the same normalization. Returns the validated handle, or `None` when there
    is no declared identity — the file is absent, unreadable, or normalizes to the empty string
    (exactly the states in which the shipped guard is dormant). Raises `MalformedIdentityError` when
    the file exists, is readable, and normalizes to a non-empty string that fails the anchored
    handle grammar (AC-URIDENT-1) — pure, deterministic, no I/O beyond the one read. A present file
    that cannot be decoded as UTF-8 is likewise malformed-and-refused (never a fall back to
    ambient): the declaration exists but is unhonourable, the same disposition as a grammar
    failure — only an ABSENT/unreadable file (`OSError`) means "no identity declared"."""
    idfile = os.path.join(project_dir, ".claude", "gh-identity")
    try:
        with open(idfile, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    except UnicodeDecodeError:
        raise MalformedIdentityError(idfile) from None
    handle = re.sub(r"[ \t\n\r\f\v]+", "", raw)
    if not handle:
        return None
    if not _HANDLE_RE.fullmatch(handle):
        raise MalformedIdentityError(idfile)
    return handle


def isolated_config_dir(handle: str) -> str:
    """`$HOME/.config/gh-<handle>` — byte-for-byte the path the shipped guard compares
    `GH_CONFIG_DIR` against. Because `handle` is grammar-validated before this is ever called, the
    result can contain no separator, no `..`, and no metacharacter derived from file contents."""
    return os.path.join(os.path.expanduser("~"), ".config", f"gh-{handle}")


def gh_env(handle: str | None, base_env: dict) -> dict:
    """The child environment for a `gh` invocation (AC-URIDENT-2/-4), pure over its inputs.
    `handle=None` (no declared identity) returns an UNMODIFIED copy of `base_env` — neither
    setting nor overwriting `GH_CONFIG_DIR`, and removing nothing (AC-URIDENT-4: prior behaviour,
    exactly). A declared `handle` returns a copy with `GH_CONFIG_DIR` set to the isolated config
    dir for that handle and the precedence-carrying token/host variables removed (AC-URIDENT-2), so
    the invocation is guard-admissible and no higher-precedence ambient variable can redirect it."""
    if handle is None:
        return dict(base_env)
    env = dict(base_env)
    env["GH_CONFIG_DIR"] = isolated_config_dir(handle)
    for var in _AMBIENT_STRIP_VARS:
        env.pop(var, None)
    return env


def render_command(argv: list[str], handle: str | None) -> str:
    """Render a `gh` argv as paste-safe text (AC-URIDENT-3/-4): every element `shlex.quote`d, with
    the `GH_CONFIG_DIR=<isolated config dir>` prefix (itself quoted) ONLY when a handle is
    declared — bare `gh ...` otherwise. Pasting the result into a POSIX shell executes exactly
    `argv`, under that config dir when declared, interpolating nothing."""
    quoted_argv = " ".join(shlex.quote(a) for a in argv)
    if handle is None:
        return quoted_argv
    return f"GH_CONFIG_DIR={shlex.quote(isolated_config_dir(handle))} {quoted_argv}"


def _format_identity_refusal(identity_file: str) -> str:
    """AC-URIDENT-1's typed diagnostic: names the identity file's PATH and the required grammar
    only — never the offending string, which is treated as wholly unusable. Exit status 4."""
    return (
        "upstream-submit: declared identity is malformed (exit status 4) — no gh invocation was "
        "made, no gh command was rendered with it\n"
        f"  identity file    : {identity_file}\n"
        f"  required grammar : {_HANDLE_RE.pattern}\n"
        "  remedy           : fix the handle in that file (GitHub handles: alphanumerics and "
        "single hyphens, no leading/trailing hyphen, max 39 chars), or remove the file to run "
        "under the ambient environment.\n"
    )


# ------------------------------- self-scan --------------------------------- #

def _read_module_source() -> str:
    """This module's own full, UNFILTERED source text."""
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        return fh.read()


def _verb_scan_text() -> str:
    """Source used for the forbidden-contribution-verb check: the full module source with ONLY
    the `_FORBIDDEN_CONTRIB_VERBS` definition lines removed (the narrowly-named
    `_VERB_LIST_MARKER`, not a generic same-line comment any other line could also carry) — so the
    verb list's own definition doesn't self-trip the scan, while every other line — including
    code that spawns a process — stays fully scannable. Case folding happens in
    `_forbidden_verbs_present`, not here, so this text can be reused unmodified elsewhere."""
    lines = _read_module_source().splitlines(keepends=True)
    kept = [ln for ln in lines if _VERB_LIST_MARKER not in ln]
    return "".join(kept)


def _forbidden_verbs_present(text: str) -> list[str]:
    """Which `_FORBIDDEN_CONTRIB_VERBS` entries appear in `text`, CASE-INSENSITIVELY
    (AC-URSUBMIT-4 / AC-URFIRST-5). Case-insensitive on both sides so an upper-cased verb spelling
    in the list (two entries spell `gh`'s own `-X`-flag convention upper-case) still fires — this
    is the fix for a prior BLOCK where those two entries could never match a lower-cased scan text
    and the self-scan silently never asserted the property they encode at all."""
    lower_text = text.lower()
    return [v for v in _FORBIDDEN_CONTRIB_VERBS if v.lower() in lower_text]


# Fully-qualified (module.attribute) names that constitute a child-process spawn, for the
# AC-URFIRST-5 structural scan. An AST walk (below) resolves every `Call` node's callee to one of
# these — not a text/regex match — so it is whitespace-agnostic (`subprocess.run (cmd)` parses
# identically to `subprocess.run(cmd)`), immune to a trailing `# selfscan:exclude`-style comment
# on a real spawn line (AST has no concept of comments), and resolves `import ... as` /
# `from ... import ... as` aliasing so a rebind (`from subprocess import run as r; r(cmd)`) cannot
# evade it either. Because this is a plain set of strings — never itself a `Call` expression — it
# needs no exclusion marker: it cannot self-match its own definition.
_SPAWN_QUALNAMES = frozenset({
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
    "subprocess.Popen", "subprocess.popen",
    "os.system", "os.popen", "os.fork",
    "os.posix_spawn", "os.posix_spawnp",
    "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
    "os.execl", "os.execle", "os.execlp", "os.execlpe",
    "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "pty.spawn", "pty.fork",
})


class _SpawnCallVisitor(ast.NodeVisitor):
    """Counts every `Call` node whose resolved qualname is in `_SPAWN_QUALNAMES`, resolving
    module-import aliases (`import subprocess as sp`) and name-import aliases
    (`from subprocess import run as r`) so an alias/rebind form cannot hide a spawn from the
    count."""

    def __init__(self) -> None:
        self.count = 0
        self.hits: list[tuple[int, str]] = []
        self._module_aliases: dict[str, str] = {}
        self._name_aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._module_aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod in ("subprocess", "os", "pty"):
            for alias in node.names:
                self._name_aliases[alias.asname or alias.name] = f"{mod}.{alias.name}"
        self.generic_visit(node)

    def _resolve(self, func: ast.expr) -> str | None:
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = self._module_aliases.get(func.value.id, func.value.id)
            return f"{base}.{func.attr}"
        if isinstance(func, ast.Name):
            return self._name_aliases.get(func.id)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        qual = self._resolve(node.func)
        if qual in _SPAWN_QUALNAMES:
            self.count += 1
            self.hits.append((node.lineno, qual))
        self.generic_visit(node)


def _count_spawn_calls(source: str) -> tuple[int, list[tuple[int, str]]]:
    """AST-walk `source` for process-spawn call sites (see `_SpawnCallVisitor`). Returns
    `(count, [(lineno, qualname), ...])`."""
    tree = ast.parse(source)
    visitor = _SpawnCallVisitor()
    visitor.visit(tree)
    return visitor.count, visitor.hits


def _selfscan_ac5() -> dict:
    """AC-URFIRST-5's structural self-scan: (1) no forbidden contribution verb appears in real
    code (request-only envelope); (2) the module contains exactly one process-spawning call site
    (AST-resolved, not text/regex — see `_count_spawn_calls`); (3) that one call site lives inside
    the runner seam (`_real_gh_runner`), not somewhere a future direct spawn could bypass it.
    Spawn counting runs over the module's full, UNFILTERED source (no `# selfscan:exclude`-style
    marker can hide a real spawn line from this count) minus only the `_FORBIDDEN_CONTRIB_VERBS`
    definition — the one tuple that marker legitimately exists for."""
    present = _forbidden_verbs_present(_verb_scan_text())
    spawn_count, spawn_sites = _count_spawn_calls(_verb_scan_text())
    seam_source = _runner_seam_source()
    spawn_in_seam, _seam_sites = _count_spawn_calls(seam_source)
    return {
        "request_only": not present,
        "forbidden_present": present,
        "spawn_count": spawn_count,
        "spawn_sites": spawn_sites,
        "spawn_in_seam": spawn_in_seam,
        "single_spawn_site": spawn_count == 1 and spawn_in_seam == 1,
    }


# ------------------------------- the runner seam ---------------------------- #
# The single injectable callable every `gh` invocation goes through (AC-URFIRST-5/-6). Real code
# calls `runner(cmd, env)` where `runner` defaults to `_real_gh_runner`; `--selftest` and the test
# suite substitute `_RecordingGhRunner` — no real `gh` process, no network, and the exact argv,
# env, order, and count of every invocation become directly assertable.
#
# `env` carries an `= None` DEFAULT here (an intentional divergence from the identity feature's own
# original design note, which called for a required, no-default `env` so a call site that forgot to
# pass it would fail loudly instead of silently de-jailing). That signature is incompatible with the
# sibling `feat-foundry-upstream-submit-first-use` atom's OWN frozen regression test
# (`tests/test_upstream_submit.py::test_sec_argv_allowlist_refuses_out_of_envelope`, byte-frozen —
# never edited by this atom), which calls `_real_gh_runner(cmd)` with one argument because it
# predates (and is entitled to predate) the identity feature entirely. The protection the no-default
# design was after — no *internal* call site silently omitting `env` — is preserved by discipline,
# not by the type signature: both real call sites in `_submit`/`_ensure_label` always pass `env`
# explicitly (never rely on the default), and `--selftest`'s AC-URIDENT-2/-4 checks assert the
# composed/passthrough environment on every recorded call. The argv-envelope refusal itself (the
# property that regression test actually exercises) runs BEFORE `env` is inspected at all, so the
# default's presence changes nothing about what that test proves.

# POSITIVE argv allowlist (fail-closed by construction, not by denylist enumeration): the only
# `gh` invocation shapes this module is ever allowed to spawn. This subsumes the
# `_FORBIDDEN_CONTRIB_VERBS` denylist for anything reaching the seam — a PR-create argv, a
# raw-`gh-api` PUT-method argv, or any other out-of-envelope shape — is refused here even if some
# future code path built one and called the seam directly, not merely flagged after the fact by
# the text self-scan.
_ALLOWED_ARGV_PREFIXES = (("gh", "issue", "create"), ("gh", "label", "create"))

# The ONE `gh api` shape the seam admits: a bare, flag-free, three-token GET against the exact
# label-existence endpoint `build_label_probe_command` builds — `gh api repos/{repo}/labels/{name}`.
# `gh api` defaults to GET when no `-X`/`--method` flag is present, and this pattern has no room
# for one (exactly 3 tokens, the 3rd matched against this fixed shape) — so this widening admits a
# read-only probe and nothing else: no PUT/POST/PATCH/DELETE method flag can appear in an argv this
# regex accepts. A read-only GET is inside the request-only envelope (AC-URFIRST-5).
_LABEL_PROBE_PATH_RE = re.compile(r"^repos/[^/\s]+/[^/\s]+/labels/" + re.escape(_LABEL_NAME) + r"$")


def _is_allowed_label_probe(cmd: list[str]) -> bool:
    """True iff `cmd` is exactly the read-only label-probe shape: `gh api <labels-GET-path>`, no
    other tokens (so no method flag can ride along)."""
    return (len(cmd) == 3 and cmd[0] == "gh" and cmd[1] == "api"
            and _LABEL_PROBE_PATH_RE.match(cmd[2]) is not None)


def _real_gh_runner(cmd: list[str], env: dict | None = None) -> GhResult:
    """The runner seam's DEFAULT implementation, and the module's ONE process-spawning call site.
    Refuses (never spawns) any argv that is neither one of the `_ALLOWED_ARGV_PREFIXES` three-token
    prefixes NOR the one read-only label-probe shape (`_is_allowed_label_probe`) — a positive
    envelope, not a denylist: this envelope check runs BEFORE `env` (or anything else) is even
    inspected, so an out-of-envelope argv is refused identically whether or not it carries a
    declared-identity environment. `env=None` spawns with the caller's own process environment
    inherited untouched (identical to the pre-identity-composition behaviour); both real call sites
    in `_submit`/`_ensure_label` always pass the `gh_env(...)`-composed dict explicitly (or an
    unmodified copy of it when no identity is declared) — including for the read-only label probe,
    which is a `gh` invocation like any other — so the default is never actually relied on by this
    module's own code; it exists only so a caller outside this module's control (see the module-
    docstring note above the allowlist) can invoke this seam with one argument. Never raises on a
    missing/unexecutable `gh` — that maps to `spawned=False` instead of an interpreter exception
    escaping to the caller (AC-URFIRST-3: no traceback reaches the output)."""
    if tuple(cmd[:3]) not in _ALLOWED_ARGV_PREFIXES and not _is_allowed_label_probe(cmd):
        return GhResult(returncode=126, stdout="",
                         stderr=f"upstream-submit: refused out-of-envelope gh invocation "
                                f"(not `gh issue create` / `gh label create` / the read-only label "
                                f"probe): {' '.join(cmd)}",
                         spawned=False)
    import subprocess
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    except OSError as exc:
        return GhResult(returncode=127, stdout="", stderr=str(exc), spawned=False)
    return GhResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, spawned=True)


def _runner_seam_source() -> str:
    """Source text of the runner-seam function — used by `_selfscan_ac5` to confirm the module's
    one spawn call site sits inside the seam rather than merely somewhere in the file."""
    return inspect.getsource(_real_gh_runner)


class _RecordingGhRunner:
    """Hermetic recording double for the runner seam (AC-URFIRST-6): every call is captured
    verbatim in `.calls` and answered from `responses` (call-index -> GhResult, plus an optional
    `"*"` default) without ever spawning a real process or touching the network. Default response
    for an unmapped call index is a clean success. `.envs` additionally captures the child
    environment passed to each call (parallel-indexed with `.calls`), so a test can assert the
    declared-identity composition (AC-URIDENT-2/-4) directly from captured evidence."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []

    def __call__(self, cmd: list[str], env: dict | None = None) -> GhResult:
        """`env` carries the same `= None` default as `_real_gh_runner` (see that function's
        docstring for why) — the hermetic double mirrors the production seam's signature exactly so
        it cannot mask a real call-site behaviour difference, including this one."""
        idx = len(self.calls)
        self.calls.append(list(cmd))
        self.envs.append(dict(env) if env is not None else None)
        if idx in self.responses:
            return self.responses[idx]
        if "*" in self.responses:
            return self.responses["*"]
        return GhResult(returncode=0, stdout="", stderr="", spawned=True)


def _format_degrade_warning(label: str, repo: str, ensure_stderr: str) -> str:
    """AC-URFIRST-2's stderr warning: the stable `UPSTREAM-SUBMIT-LABEL-DEGRADED` token plus the
    label, the target repo, and the label-ensure's captured stderr. Wording beyond the token and
    these three facts is free."""
    return (
        f"{_DEGRADE_TOKEN}: could not ensure label '{label}' on {repo}; filing the issue WITHOUT "
        f"--label instead of aborting (the maintainer can label it on arrival).\n"
        f"  label            : {label}\n"
        f"  repo             : {repo}\n"
        f"  ensure stderr    : {ensure_stderr.strip() or '(empty)'}\n"
    )


def _format_gh_failure(rendered_command: str, stderr: str, ensure_stderr: str = "") -> str:
    """AC-URFIRST-3's typed diagnostic for a failed/unexecutable `gh issue create`: the invoked
    command, the captured stderr, and a remedy. `rendered_command` is the ALREADY shell-quoted,
    identity-prefixed text `render_command` produces (the same text the `rendered_command` field of
    `_submit`'s result dict carries on the success path, AC-URIDENT-3/-4) — this is a THIRD render
    point, alongside the dry-run line and the `--create` echo, and it was the one point that used to
    print a raw, unquoted `' '.join(argv)` instead: adopter-authored text (the candidate's title)
    flows into `argv` unescaped, so an unquoted join on this exit-3 path could print a command line
    that is not safe to copy-paste (shell metacharacters un-neutralized). Routing through the same
    `render_command` output the rest of the module already uses keeps the "every render point is
    shell-quoted" property literally true at all three points, not two of three.

    `ensure_stderr` is the label-ensure call's OWN captured stderr, folded in when non-empty: if
    the ensure ALSO failed (the degrade path), its failure often carries the more diagnostic cause
    (e.g. `HTTP 403: must have admin rights` vs. the create's downstream `HTTP 404`) and would
    otherwise be silently dropped — the caller sees only the second failure. Plain string
    formatting only — this function cannot raise on the values it is normally called with, so it
    never itself produces a traceback."""
    lines = [
        "upstream-submit: gh issue create failed — no issue was filed (exit status 3)",
        f"  command          : {rendered_command}",
        f"  gh stderr        : {(stderr or '').strip() or '(empty)'}",
    ]
    if ensure_stderr and ensure_stderr.strip():
        lines.append(f"  label-ensure err : {ensure_stderr.strip()} (label-ensure ALSO failed — "
                      f"often the more diagnostic cause)")
    lines.append("  remedy           : confirm `gh` is installed and on PATH and that you are "
                 "authenticated (`gh auth status`), then re-run.")
    return "\n".join(lines) + "\n"


# -------------------------------- selftest --------------------------------- #

def _selftest() -> int:
    failures: list[str] = []
    lines: list[str] = ["foundry-upstream-submit selftest"]

    # AC-URSUBMIT-1 — sanitize is fail-closed. Leaky fixtures are ASSEMBLED FROM FRAGMENTS so the
    # fixture itself ships no contiguous CI-trigger substring (same constraint as the floor).
    leaky_floor = "the " + "op_" + "demo operator hit " + "HBK" + "-7 and cited " + "[" + "memory:x]"
    floor_hits = scan_leaks(leaky_floor)
    a1a = len(floor_hits) >= 3
    extra_hits = scan_leaks("our product " + "acme" + "links is affected", ["acmelinks"])
    a1b = bool(extra_hits)
    clean_hits = scan_leaks("a workflow_run-gated CD pipeline silently skips on upstream ci false-red")
    a1c = not clean_hits
    # Leak aborts BEFORE any network/command build: submit() returns refusal, builds no argv, and
    # makes zero calls through the runner seam (double captured for a belt-and-braces check).
    a1_dbl = _RecordingGhRunner()
    refused = _submit(leaky_floor_candidate(), repo="o/r", adopter="x", foundry_version="0",
                      create=True, extra_tokens=[], runner=a1_dbl)
    a1d = (refused["refused"] and refused["issue_command"] is None and not refused["called_gh"]
           and a1_dbl.calls == [])
    ok1 = a1a and a1b and a1c and a1d
    if not ok1:
        failures.append(f"AC-1 sanitize: floor={a1a} extra={a1b} clean={a1c} abort-before-call={a1d}")
    lines.append(f"  [{'ok ' if ok1 else 'RED'}] AC-URSUBMIT-1 sanitize-fail-closed: "
                 f"{'PASS' if ok1 else 'FAIL'}")

    # AC-URSUBMIT-2 — clean candidate renders the structured shape; missing section is rejected.
    parsed = parse_candidate(_GOOD_CANDIDATE)
    body = render_er(parsed, adopter="example-adopter", foundry_version="0.1.4")
    a2a = all(f"## {s}" in body for s in _REQUIRED_SECTIONS)
    try:
        parse_candidate("# t\n## Generalized problem\nx\n")  # missing 3 sections
        a2b = False
    except ValueError:
        a2b = True
    ok2 = a2a and a2b
    if not ok2:
        failures.append(f"AC-2 structured-er: all-sections={a2a} rejects-incomplete={a2b}")
    lines.append(f"  [{'ok ' if ok2 else 'RED'}] AC-URSUBMIT-2 generified-structured-er: "
                 f"{'PASS' if ok2 else 'FAIL'}")

    # AC-URSUBMIT-3 — provenance footer + stable, identity-free dedup key.
    k1 = dedup_key("A gated CD pipeline silently SKIPS on upstream CI false-red.")
    k2 = dedup_key("a gated cd pipeline   silently skips on upstream ci false-red.")  # norm-equal
    k3 = dedup_key("Wiring-hash pin breaks every adopter on plugin upgrade.")
    a3a = k1 == k2 and k1 != k3                      # normalization collapses; distinct stays distinct
    a3b = k1.startswith("ur-") and "example-adopter" not in k1  # no adopter identity in the key
    a3c = all(x in body for x in ("adopter: example-adopter", "foundry-version: 0.1.4", "dedup-key: ur-"))
    ok3 = a3a and a3b and a3c
    if not ok3:
        failures.append(f"AC-3 provenance/dedup: stable={a3a} identity-free={a3b} footer={a3c}")
    lines.append(f"  [{'ok ' if ok3 else 'RED'}] AC-URSUBMIT-3 provenance-and-dedup-key: "
                 f"{'PASS' if ok3 else 'FAIL'}")

    # AC-URSUBMIT-4 — request, not contribution: command is `gh issue create`; no contrib verb in code.
    cmd = build_issue_command("o/r", "t", "/tmp/b")
    a4a = cmd[:3] == ["gh", "issue", "create"] and "pr" not in cmd
    present = _forbidden_verbs_present(_verb_scan_text())
    a4b = not present
    # The upper-cased-flag clause must actually fire on a SYNTHETIC string built at runtime from
    # fragments (so this proof itself never ships the contiguous phrase in the module's own
    # source text — same discipline the floor-pattern fixtures above use) — proves the
    # case-insensitive match is live, not structurally inert (see `_forbidden_verbs_present`).
    _put_probe = "some code did: gh api " + "-X" + " " + "PUT" + " something"
    a4c = bool(_forbidden_verbs_present(_put_probe))
    ok4 = a4a and a4b and a4c
    if not ok4:
        failures.append(f"AC-4 request-not-contribution: issue-cmd={a4a} no-contrib-verb={a4b} "
                         f"({present}) put-clause-fires={a4c}")
    lines.append(f"  [{'ok ' if ok4 else 'RED'}] AC-URSUBMIT-4 request-not-contribution: "
                 f"{'PASS' if ok4 else 'FAIL'}")

    # AC-URFIRST-1 — probe-then-create label-ensure precedes issue-create, and is idempotent with
    # ZERO writes on the already-exists path. Every gh invocation below is routed through a fresh,
    # per-check _RecordingGhRunner double — no real gh, no network — and each verdict is computed
    # from THAT double's own captured argv.
    # (a) label absent: probe reports 404, so a create call follows, before issue-create.
    urf1_dbl = _RecordingGhRunner(responses={
        0: GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),
    })
    _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
            extra_tokens=[], runner=urf1_dbl)
    ensure_first = (len(urf1_dbl.calls) == 3
                     and urf1_dbl.calls[0][:2] == ["gh", "api"]
                     and urf1_dbl.calls[1][:3] == ["gh", "label", "create"]
                     and urf1_dbl.calls[2][:3] == ["gh", "issue", "create"])
    # (b) label already present (every call defaults to a clean success == the probe finds it):
    # TWO consecutive submits make ZERO create calls between them — the no-duplicate-label,
    # zero-write-on-already-exists clause, proven by an actual absence of create calls.
    urf1b_dbl = _RecordingGhRunner()
    rep1 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                    extra_tokens=[], runner=urf1b_dbl)
    rep2 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                    extra_tokens=[], runner=urf1b_dbl)
    idempotent = (not rep1["label_degraded"] and not rep2["label_degraded"]
                  and len(urf1b_dbl.calls) == 4  # probe + issue-create, twice — no create calls
                  and urf1b_dbl.calls[0][:2] == ["gh", "api"]
                  and urf1b_dbl.calls[1][:3] == ["gh", "issue", "create"]
                  and urf1b_dbl.calls[0] == urf1b_dbl.calls[2])  # identical probe argv both times
    ok_urf1 = ensure_first and idempotent
    if not ok_urf1:
        failures.append(f"AC-URFIRST-1 ensure-precedes-create: ensure-first={ensure_first} "
                         f"idempotent={idempotent}")
    lines.append(f"  [{'ok ' if ok_urf1 else 'RED'}] AC-URFIRST-1 label-ensure-precedes-create: "
                 f"{'PASS' if ok_urf1 else 'FAIL'}")

    # AC-URFIRST-2 — a failing ensure (probe AND create both fail) degrades to an unlabelled issue
    # with a warning, and succeeds.
    urf2_dbl = _RecordingGhRunner(responses={
        0: GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),
        1: GhResult(returncode=1, stdout="", stderr="HTTP 403: must have admin rights", spawned=True),
    })
    rep_degrade = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                           extra_tokens=[], runner=urf2_dbl)
    no_label_arg = "--label" not in rep_degrade["issue_command"]
    warning = _format_degrade_warning(_LABEL_NAME, "o/r", rep_degrade.get("degrade_detail", ""))
    warning_ok = (_DEGRADE_TOKEN in warning and _LABEL_NAME in warning and "o/r" in warning
                  and "403" in warning)
    ok_urf2 = (rep_degrade["label_degraded"] and no_label_arg
               and not rep_degrade.get("gh_failed") and warning_ok)
    if not ok_urf2:
        failures.append(f"AC-URFIRST-2 degrade: degraded={rep_degrade['label_degraded']} "
                         f"no-label-arg={no_label_arg} warning={warning_ok}")
    lines.append(f"  [{'ok ' if ok_urf2 else 'RED'}] AC-URFIRST-2 degrade-to-unlabelled: "
                 f"{'PASS' if ok_urf2 else 'FAIL'}")

    # AC-URFIRST-3 — a gh issue-create failure (non-zero exit, or gh absent) is a typed exit-3
    # diagnostic, never a traceback, over BOTH failure shapes.
    urf3a_dbl = _RecordingGhRunner(responses={
        0: GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: GhResult(returncode=1, stdout="", stderr="HTTP 422: validation failed", spawned=True),
    })
    rep3a = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=urf3a_dbl)
    diag_a = _format_gh_failure(rep3a.get("rendered_command") or "", rep3a.get("gh_stderr", ""))
    case_a_ok = (rep3a.get("gh_failed") is True and "422" in diag_a and "Traceback" not in diag_a
                 and "gh issue create" in " ".join(rep3a.get("gh_argv") or []))
    urf3b_dbl = _RecordingGhRunner(responses={
        0: GhResult(returncode=0, stdout="", stderr="", spawned=True),
        1: GhResult(returncode=127, stdout="", stderr="[Errno 2] No such file: 'gh'", spawned=False),
    })
    rep3b = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                     extra_tokens=[], runner=urf3b_dbl)
    diag_b = _format_gh_failure(rep3b.get("rendered_command") or "", rep3b.get("gh_stderr", ""))
    case_b_ok = rep3b.get("gh_failed") is True and "Traceback" not in diag_b
    ok_urf3 = case_a_ok and case_b_ok
    if not ok_urf3:
        failures.append(f"AC-URFIRST-3 typed-gh-failure: non-zero-exit={case_a_ok} absent={case_b_ok}")
    lines.append(f"  [{'ok ' if ok_urf3 else 'RED'}] AC-URFIRST-3 typed-gh-failure-exit-3: "
                 f"{'PASS' if ok_urf3 else 'FAIL'}")

    # AC-URFIRST-4 — a dirty scan makes ZERO gh invocations of any kind (label-ensure included).
    urf4_dbl = _RecordingGhRunner()
    rep4 = _submit(leaky_floor_candidate(), repo="o/r", adopter="x", foundry_version="0",
                    create=True, extra_tokens=[], runner=urf4_dbl)
    ok_urf4 = rep4["refused"] and urf4_dbl.calls == []
    if not ok_urf4:
        failures.append(f"AC-URFIRST-4 leak-blocks-gh: refused={rep4['refused']} calls={urf4_dbl.calls}")
    lines.append(f"  [{'ok ' if ok_urf4 else 'RED'}] AC-URFIRST-4 leak-blocks-every-gh-call: "
                 f"{'PASS' if ok_urf4 else 'FAIL'}")

    # AC-URFIRST-5 — request-only envelope + exactly one process-spawning call site, inside the seam.
    scan5 = _selfscan_ac5()
    # The POSITIVE argv allowlist inside the seam itself refuses an out-of-envelope command (a
    # raw `gh api` call using the upper-cased `-X`/PUT-method flags) and spawns nothing —
    # fail-closed by construction, not merely by denylist enumeration — while the ONE admitted
    # `gh api` shape (the read-only label-GET probe) IS allowed through to the real spawn attempt.
    # Real `subprocess.run` is patched to explode if ever reached, so both properties are proven by
    # which call actually reaches it.
    import subprocess as _sp
    _orig_run = _sp.run
    _sp.run = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("out-of-envelope argv reached real subprocess.run"))
    try:
        put_result = _real_gh_runner(["gh", "api", "-X", "PUT", "repos/o/r/labels/x"], None)
        probe_reached_spawn = False
        try:
            _real_gh_runner(build_label_probe_command("o/r"), None)
        except AssertionError:
            probe_reached_spawn = True  # the in-envelope probe WAS admitted through to the spawn
    finally:
        _sp.run = _orig_run
    allowlist_ok = (not put_result.spawned and put_result.returncode != 0 and probe_reached_spawn)
    ok_urf5 = scan5["request_only"] and scan5["single_spawn_site"] and allowlist_ok
    if not ok_urf5:
        failures.append(f"AC-URFIRST-5 request-only-single-spawn-site: {scan5} "
                         f"argv-allowlist-refuses={allowlist_ok}")
    lines.append(f"  [{'ok ' if ok_urf5 else 'RED'}] AC-URFIRST-5 request-only-single-spawn-site: "
                 f"{'PASS' if ok_urf5 else 'FAIL'}")

    # ==================================================================================
    # feat-foundry-upstream-submit-identity (AC-URIDENT-1..5, issue #21 item b). Every check below
    # uses its OWN throwaway project root + its OWN fresh _RecordingGhRunner double (never a shared
    # aggregate), and every gh invocation is routed through that double — no real gh, no network.

    # AC-URIDENT-1 — a malformed declared identity REFUSES: zero gh calls, the offending string
    # rendered nowhere, and a diagnostic naming only the file path + the required grammar.
    urident_root1 = tempfile.mkdtemp(prefix="urident-ac1-")
    try:
        os.makedirs(os.path.join(urident_root1, ".claude"), exist_ok=True)
        bad_handle = "../evil;rm -rf ~"
        idfile1 = os.path.join(urident_root1, ".claude", "gh-identity")
        with open(idfile1, "w", encoding="utf-8") as fh:
            fh.write(bad_handle)
        dbl_a1 = _RecordingGhRunner()
        raised, exc_file = False, None
        try:
            _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                    extra_tokens=[], runner=dbl_a1, project_dir=urident_root1)
        except MalformedIdentityError as exc:
            raised, exc_file = True, exc.identity_file
        diag1 = _format_identity_refusal(exc_file or idfile1)
        ok_urident1 = (raised and dbl_a1.calls == [] and exc_file == idfile1
                       and "evil" not in diag1 and bad_handle not in diag1
                       and idfile1 in diag1 and _HANDLE_RE.pattern in diag1)
    finally:
        shutil.rmtree(urident_root1, ignore_errors=True)
    if not ok_urident1:
        failures.append(f"AC-URIDENT-1 malformed-identity-refused: raised={raised} "
                         f"calls-empty={dbl_a1.calls == []}")
    lines.append(f"  [{'ok ' if ok_urident1 else 'RED'}] AC-URIDENT-1 malformed-identity-refused: "
                 f"{'PASS' if ok_urident1 else 'FAIL'}")

    # AC-URIDENT-2 — declared identity: every gh call's child env carries GH_CONFIG_DIR for that
    # handle AND is stripped of the higher-precedence token/host variables, even when they were
    # present (as sentinels) in the base environment the call started from.
    urident_root2 = tempfile.mkdtemp(prefix="urident-ac2-")
    try:
        os.makedirs(os.path.join(urident_root2, ".claude"), exist_ok=True)
        with open(os.path.join(urident_root2, ".claude", "gh-identity"), "w", encoding="utf-8") as fh:
            fh.write("  octocat\n")
        dirty_env2 = dict(os.environ)
        for v in _AMBIENT_STRIP_VARS:
            dirty_env2[v] = "SENTINEL-" + v
        dbl_a2 = _RecordingGhRunner()
        rep_a2 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                          extra_tokens=[], runner=dbl_a2, project_dir=urident_root2,
                          base_env=dirty_env2)
        expect_dir2 = isolated_config_dir("octocat")
        ok_urident2 = (not rep_a2["refused"] and len(dbl_a2.envs) == 2 and all(
            e is not None and e.get("GH_CONFIG_DIR") == expect_dir2
            and all(v not in e for v in _AMBIENT_STRIP_VARS)
            for e in dbl_a2.envs))
    finally:
        shutil.rmtree(urident_root2, ignore_errors=True)
    if not ok_urident2:
        failures.append(f"AC-URIDENT-2 declared-identity-env: envs={dbl_a2.envs}")
    lines.append(f"  [{'ok ' if ok_urident2 else 'RED'}] AC-URIDENT-2 declared-identity-env: "
                 f"{'PASS' if ok_urident2 else 'FAIL'}")

    # AC-URIDENT-3 — the printed command (dry-run AND --create) is shell-quoted and identity-
    # prefixed: proven by round-tripping the rendered text back through shlex.split and confirming
    # it reconstructs the EXACT argv the verb built, using a title that carries shell metacharacters.
    urident_root3 = tempfile.mkdtemp(prefix="urident-ac3-")
    try:
        os.makedirs(os.path.join(urident_root3, ".claude"), exist_ok=True)
        with open(os.path.join(urident_root3, ".claude", "gh-identity"), "w", encoding="utf-8") as fh:
            fh.write("octo-cat")
        tricky_candidate = _GOOD_CANDIDATE.replace(
            "deploy-status false-greens a never-rolled commit",
            "deploy `whoami` && echo pwned; $(id)")
        dbl_a3 = _RecordingGhRunner()
        rep_dry3 = _submit(tricky_candidate, repo="o/r", adopter="x", foundry_version="0",
                            create=False, extra_tokens=[], runner=dbl_a3, project_dir=urident_root3)
        rep_create3 = _submit(tricky_candidate, repo="o/r", adopter="x", foundry_version="0",
                               create=True, extra_tokens=[], runner=dbl_a3, project_dir=urident_root3)
        expect_dir3 = isolated_config_dir("octo-cat")
        prefix3 = f"GH_CONFIG_DIR={shlex.quote(expect_dir3)}"

        def _round_trips(rendered, argv):
            if rendered is None or not rendered.startswith(prefix3 + " "):
                return False
            try:
                return shlex.split(rendered[len(prefix3) + 1:]) == argv
            except ValueError:
                return False

        ok_urident3 = (_round_trips(rep_dry3["rendered_command"], rep_dry3["issue_command"])
                       and _round_trips(rep_create3["rendered_command"], rep_create3["issue_command"]))
    finally:
        shutil.rmtree(urident_root3, ignore_errors=True)
    if not ok_urident3:
        failures.append(f"AC-URIDENT-3 printed-command-quoted: dry={rep_dry3['rendered_command']!r} "
                         f"create={rep_create3['rendered_command']!r}")
    lines.append(f"  [{'ok ' if ok_urident3 else 'RED'}] AC-URIDENT-3 printed-command-quoted: "
                 f"{'PASS' if ok_urident3 else 'FAIL'}")

    # AC-URIDENT-4 — no declared identity: the child environment passes through UNMODIFIED (a
    # sentinel ambient token survives untouched) and the printed command is bare `gh ...`.
    urident_root4 = tempfile.mkdtemp(prefix="urident-ac4-")
    try:
        dirty_env4 = dict(os.environ)
        dirty_env4["GH_TOKEN"] = "SENTINEL-GH_TOKEN"
        dbl_a4 = _RecordingGhRunner()
        rep4 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0", create=True,
                        extra_tokens=[], runner=dbl_a4, project_dir=urident_root4,
                        base_env=dirty_env4)
        ok_urident4 = (
            not rep4["refused"] and len(dbl_a4.envs) == 2
            and all(e == dirty_env4 for e in dbl_a4.envs)
            and rep4["rendered_command"] is not None
            and rep4["rendered_command"].startswith("gh ")
            and "GH_CONFIG_DIR=" not in rep4["rendered_command"]
        )
    finally:
        shutil.rmtree(urident_root4, ignore_errors=True)
    if not ok_urident4:
        failures.append(f"AC-URIDENT-4 undeclared-passthrough: {rep4.get('rendered_command')!r}")
    lines.append(f"  [{'ok ' if ok_urident4 else 'RED'}] AC-URIDENT-4 undeclared-passthrough: "
                 f"{'PASS' if ok_urident4 else 'FAIL'}")

    # AC-URIDENT-5 — diagnostic hygiene (no diagnostic echoes a stripped env var's VALUE) and
    # tempfile hygiene (nothing left behind in an isolated scratch tempdir) across the label-
    # degrade path and the gh-failure path.
    urident_root5 = tempfile.mkdtemp(prefix="urident-ac5-root-")
    tmp_scratch5 = tempfile.mkdtemp(prefix="urident-ac5-tmp-")
    saved_tmpdir = tempfile.tempdir
    try:
        os.makedirs(os.path.join(urident_root5, ".claude"), exist_ok=True)
        with open(os.path.join(urident_root5, ".claude", "gh-identity"), "w", encoding="utf-8") as fh:
            fh.write("octocat")
        dirty_env5 = dict(os.environ)
        for v in _AMBIENT_STRIP_VARS:
            dirty_env5[v] = "SENTINEL-" + v
        tempfile.tempdir = tmp_scratch5

        # Probe-then-create: BOTH the probe (index 0) and the create (index 1) must fail for the
        # ensure to degrade — matching AC-URFIRST-2's own fixture shape.
        dbl_degrade5 = _RecordingGhRunner(responses={
            0: GhResult(returncode=1, stdout="", stderr="HTTP 404: Not Found", spawned=True),
            1: GhResult(returncode=1, stdout="", stderr="HTTP 403: must have admin rights", spawned=True),
        })
        rep_degrade5 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                                create=True, extra_tokens=[], runner=dbl_degrade5,
                                project_dir=urident_root5, base_env=dirty_env5)
        warn5 = _format_degrade_warning(_LABEL_NAME, "o/r", rep_degrade5.get("degrade_detail", ""))

        dbl_fail5 = _RecordingGhRunner(responses={
            0: GhResult(returncode=0, stdout="", stderr="", spawned=True),
            1: GhResult(returncode=1, stdout="", stderr="HTTP 422: validation failed", spawned=True),
        })
        rep_fail5 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                             create=True, extra_tokens=[], runner=dbl_fail5,
                             project_dir=urident_root5, base_env=dirty_env5)
        diag5 = _format_gh_failure(rep_fail5.get("rendered_command") or "", rep_fail5.get("gh_stderr", ""))

        dbl_ok5 = _RecordingGhRunner()
        rep_ok5 = _submit(_GOOD_CANDIDATE, repo="o/r", adopter="x", foundry_version="0",
                           create=True, extra_tokens=[], runner=dbl_ok5,
                           project_dir=urident_root5, base_env=dirty_env5)

        no_leak5 = all(("SENTINEL-" + v) not in text
                       for v in _AMBIENT_STRIP_VARS for text in (warn5, diag5))
        no_tempfiles5 = os.listdir(tmp_scratch5) == []
        ok_urident5 = (no_leak5 and no_tempfiles5 and not rep_ok5["refused"]
                       and rep_degrade5["label_degraded"] and rep_fail5.get("gh_failed"))
    finally:
        tempfile.tempdir = saved_tmpdir
        shutil.rmtree(urident_root5, ignore_errors=True)
        shutil.rmtree(tmp_scratch5, ignore_errors=True)
    if not ok_urident5:
        failures.append(f"AC-URIDENT-5 diagnostic-tempfile-hygiene: no-leak={no_leak5} "
                         f"no-tempfiles={no_tempfiles5}")
    lines.append(f"  [{'ok ' if ok_urident5 else 'RED'}] AC-URIDENT-5 diagnostic-tempfile-hygiene: "
                 f"{'PASS' if ok_urident5 else 'FAIL'}")

    lines.append("UPSTREAM-SUBMIT-SELFTEST-" + ("GREEN" if not failures else "RED"))
    sys.stdout.write("\n".join(lines) + "\n")
    if failures:
        sys.stderr.write("selftest failures:\n  - " + "\n  - ".join(failures) + "\n")
        return 1
    return 0


_GOOD_CANDIDATE = """# deploy-status false-greens a never-rolled commit
## Generalized problem
A workflow_run-gated CD pipeline silently skips when upstream CI false-reds; the observe-only
deploy verb then reads Synced+Healthy against the stale image.
## Evidence (sanitized)
A gated build was skipped on a false-red; the old image kept serving while status read green.
## Proposed mechanism
Cross-check deployed artifact identity vs the expected merged-commit identity; surface STALE.
## Triage bucket
core-plugin
"""


def leaky_floor_candidate() -> str:
    """A candidate whose Evidence section carries a floor leak (assembled from fragments)."""
    leak = "the " + "op_" + "demo run cited " + "[" + "memory:x]"
    return _GOOD_CANDIDATE.replace(
        "A gated build was skipped on a false-red; the old image kept serving while status read green.",
        leak)


# ---------------------------------- main ----------------------------------- #

def _submit(candidate_text: str, repo: str, adopter: str, foundry_version: str,
            create: bool, extra_tokens: list[str], runner=None, project_dir: str | None = None,
            base_env: dict | None = None) -> dict:
    """Core flow: parse → render → SCAN (fail-closed) → resolve identity → label-ensure →
    issue-create (feat-foundry-upstream-submit-identity's Design/notes ordering). Returns a result
    dict; performs no I/O of its own (the caller renders diagnostics/warnings). The scan runs
    BEFORE any command build or `gh` call — a leak aborts with refused=True, a None command, and
    the runner is never invoked (AC-URFIRST-4). Identity resolution runs BEFORE any `gh` call too:
    a malformed declaration (AC-URIDENT-1) raises `MalformedIdentityError` here, before the
    tempfile is ever created and before any command is rendered with the offending string — the
    caller maps that to exit 4. `runner` is the injectable seam every `gh` invocation goes through
    (default: `_real_gh_runner`); tests/`--selftest` substitute a `_RecordingGhRunner`
    (AC-URFIRST-5/-6). `project_dir` (default: `$CLAUDE_PROJECT_DIR` or cwd) and `base_env`
    (default: `os.environ`) are injectable purely for hermetic testing (AC-URIDENT-6/-7). Label-
    ensure precedes issue-create and only runs in `--create` mode (AC-URFIRST-1); an ensure that
    does not succeed degrades to an unlabelled `gh issue create` rather than aborting
    (AC-URFIRST-2); a failed/unexecutable issue-create maps to `gh_failed=True` with the invoked
    argv and captured stderr for the caller to render as a typed exit-3 diagnostic (AC-URFIRST-3),
    never a raised exception. Every `gh` invocation carries the declared-identity child environment
    computed by `gh_env` (AC-URIDENT-2/-4), and every returned command is paired with its
    `render_command`-rendered, shell-quoted, identity-prefixed printable text (AC-URIDENT-3/-4).
    The rendered ER body's tempfile — created only on the `--create` path, after identity is
    resolved — is removed by a `finally` on every exit from that path (AC-URIDENT-5): success,
    label-degrade, and gh-failure alike."""
    run = runner or _real_gh_runner
    parsed = parse_candidate(candidate_text)
    body = render_er(parsed, adopter, foundry_version)
    hits = scan_leaks(body, extra_tokens)
    if hits:
        return {"refused": True, "hits": sorted(set(hits)), "issue_command": None,
                "called_gh": False, "body": body, "label_degraded": False, "gh_failed": False,
                "handle": None, "rendered_command": None}

    effective_project_dir = (project_dir if project_dir is not None
                              else os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    handle = resolve_identity(effective_project_dir)  # raises MalformedIdentityError (AC-URIDENT-1)
    env = gh_env(handle, base_env if base_env is not None else os.environ)

    if not create:
        cmd = build_issue_command(repo, parsed["title"], "<body-file>")
        return {"refused": False, "hits": [], "issue_command": cmd, "called_gh": False,
                "body": body, "label_degraded": False, "gh_failed": False, "handle": handle,
                "rendered_command": render_command(cmd, handle)}

    label_ok, degrade_detail = _ensure_label(repo, run, env)
    label_degraded = not label_ok

    tf_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
            # `tf_path` is assigned BEFORE `tf.write` runs (not after): `NamedTemporaryFile(...,
            # delete=False)` creates the file on disk at construction, before this block's body
            # ever executes — if `tf.write` were to raise, a `tf_path = tf.name` placed AFTER it
            # would never run, so `finally` below would see `tf_path is None` and skip the
            # `os.unlink`, leaking a tempfile holding the (possibly leak-scanned-but-still-private)
            # rendered ER body. Recording the path first makes the finally's cleanup unconditional
            # on the write's own success.
            tf_path = tf.name
            tf.write(body)
        cmd = build_issue_command(repo, parsed["title"], tf_path,
                                   label=None if label_degraded else _LABEL_NAME)
        issue_result = run(cmd, env)
        if issue_result.returncode != 0 or not issue_result.spawned:
            return {"refused": False, "hits": [], "issue_command": cmd, "called_gh": True,
                    "body": body, "label_degraded": label_degraded, "degrade_detail": degrade_detail,
                    "gh_failed": True, "gh_argv": cmd, "gh_stderr": issue_result.stderr,
                    "handle": handle, "rendered_command": render_command(cmd, handle)}

        return {"refused": False, "hits": [], "issue_command": cmd, "called_gh": True, "body": body,
                "label_degraded": label_degraded, "degrade_detail": degrade_detail, "gh_failed": False,
                "handle": handle, "rendered_command": render_command(cmd, handle),
                "gh_stdout": issue_result.stdout}
    finally:
        # AC-URIDENT-5: the rendered ER body's tempfile never outlives the run, on any exit path.
        if tf_path is not None:
            try:
                os.unlink(tf_path)
            except OSError:
                pass


def _run_live(args, runner=None) -> int:
    with open(args.candidate, encoding="utf-8") as fh:
        text = fh.read()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    extra = load_redaction_tokens(project_dir)
    adopter = args.adopter or os.path.basename(os.path.abspath(project_dir))
    fver = args.foundry_version or _detect_foundry_version()
    try:
        res = _submit(text, args.repo, adopter, fver, args.create, extra, runner=runner,
                       project_dir=project_dir)
    except MalformedIdentityError as e:
        sys.stderr.write(_format_identity_refusal(e.identity_file))
        return 4
    except ValueError as e:
        sys.stderr.write(f"upstream-submit: {e}\n")
        return 1
    if res["refused"]:
        sys.stderr.write("REFUSED — proprietary leak detected (no issue filed, no network call, "
                         "no label-ensure):\n"
                         "  - " + "\n  - ".join(res["hits"]) + "\n"
                         "Generify the wording or add the token to .foundry/upstream-redaction.txt, "
                         "then re-run.\n")
        return 2
    if res.get("gh_failed"):
        # If the label-ensure ALSO failed, its captured stderr is often the more diagnostic cause
        # (e.g. ensure: `HTTP 403 must have admin rights`; create: a downstream `HTTP 404`) — fold
        # it into the exit-3 diagnostic rather than silently dropping it behind the create failure.
        ensure_stderr = res.get("degrade_detail", "") if res.get("label_degraded") else ""
        rendered = res.get("rendered_command") or render_command(
            res.get("gh_argv") or res["issue_command"] or [], res.get("handle"))
        sys.stderr.write(_format_gh_failure(rendered, res.get("gh_stderr", ""), ensure_stderr))
        return 3
    if res.get("label_degraded"):
        sys.stderr.write(_format_degrade_warning(_LABEL_NAME, args.repo, res.get("degrade_detail", "")))
    print("foundry-upstream-submit (" + ("FILED" if res["called_gh"] else "DRY-RUN") + ")")
    print(f"  target repo : {args.repo}")
    print(f"  gh command  : {res['rendered_command']}")
    gh_stdout = (res.get("gh_stdout") or "").strip()
    if res["called_gh"] and gh_stdout:
        print(f"  issue url   : {gh_stdout}")
    if not res["called_gh"]:
        print("\n--- rendered ER (review before --create) ---\n" + res["body"])
    return 0


def _detect_foundry_version() -> str:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    pj = os.path.join(root, ".claude-plugin", "plugin.json") if root else ""
    if pj and os.path.exists(pj):
        import json
        try:
            return json.load(open(pj, encoding="utf-8")).get("version", "unknown")
        except Exception:
            return "unknown"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="adopter→framework enhancement-request submitter (issue, fail-closed)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--candidate", help="path to the candidate ER markdown (required sections enforced)")
    ap.add_argument("--repo", default=DEFAULT_UPSTREAM_REPO, help=f"upstream foundry repo (default {DEFAULT_UPSTREAM_REPO})")
    ap.add_argument("--adopter", default=None, help="adopter label for provenance (default: project dir name)")
    ap.add_argument("--foundry-version", default=None, help="foundry version (default: from CLAUDE_PLUGIN_ROOT)")
    ap.add_argument("--create", action="store_true", help="actually file the issue (default: dry-run)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.candidate:
        ap.error("--candidate is required (or use --selftest)")
    return _run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
