#!/usr/bin/env python3
"""foundry_blueprint — pure discover / render / boundary-guard + blueprints_sha256 digest for the app-stack
blueprint library (feat-foundry-app-stack-blueprint, AC-ASBL-1/2/3).

A profile pack MAY carry a `blueprints/` subtree; each `packs/stack-profiles/<id>/blueprints/<bp-id>/SKILL.md`
is a granular PATTERN BLUEPRINT: standard `---`-fenced YAML frontmatter + a generic-HOW body parametrized at
load-time from the active profile's declarative slots via `{{ profile.<dotted-path> }}`. This module holds the
PURE functions (no I/O policy beyond reading the pack files it is handed a dir for, no command execution, no
network): discovery scoped to a pack dir (realpath/commonpath-contained, symlink-rejecting), frontmatter
parse+schema-validate, the fully-specified placeholder grammar + scalar-only render (fail-closed), the
deterministic `blueprints_sha256` digest, and the HOW/WHAT boundary mistake-catcher.

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). The operator authors profiles +
blueprints; this is a mechanical, fail-closed mistake-catcher (an unresolved placeholder, a path-traversing
blueprint, a non-scalar binding, or a product-literal leak → a clear raised error), NOT a defense against a
hostile blueprint-author (`pack-trust-model` deferred). The HOW/WHAT boundary guard (AC-ASBL-3) is
belt-and-suspenders for the common copy-the-literal mistake — explicitly NOT a security/tamper control and NOT
evasion-proof (reformatting/rewording defeats it). The integrity boundary for the blueprint BYTES is the
`blueprints_sha256` pin (AC-ASBL-2), not the guard.
"""
import hashlib
import os
import re

import yaml

SCHEMA_NAME = "app-stack-blueprint.schema.json"
BLUEPRINT_FILE = "SKILL.md"
BLUEPRINTS_DIR = "blueprints"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Placeholder grammar (exact): `{{ profile.<dotted-path> }}` — single space each side, dotted identifier path.
_PLACEHOLDER = re.compile(r"\{\{ profile\.([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*) \}\}")
# Default operator-extensible domain-id deny-list (AC-ASBL-3 rule (b)). CONCATENATED needles so this module's
# OWN self-referential selftest scan (and the boundary guard run over fixtures) does not flag the module file.
_DENY_DEFAULT = [r"\b" + "HBK" + r"-\d+\b", r"\b" + "FEAT" + r"-\d+\b"]
_NONDISTINCT_MIN_LEN = 4  # tokens shorter than this carry no domain signal (AC-ASBL-3 rule (a) exemption).


class BlueprintError(Exception):
    """Fail-closed blueprint discovery / render / guard error (never a silent partial/blank render)."""


# ── frontmatter ────────────────────────────────────────────────────────────────────────────────────────

def split_frontmatter(raw):
    """Parse standard `---`-fenced YAML frontmatter at file start. Returns (frontmatter_dict, body_str).
    Fail-closed: opening `---` must be line 1; an unterminated / non-parsing / non-mapping block → a clear
    BlueprintError (never a silent skip)."""
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise BlueprintError("frontmatter missing: file must open with a `---` fence on line 1")
    # find the closing fence.
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end = i
            break
    if end is None:
        raise BlueprintError("frontmatter unterminated: no closing `---` fence found")
    fm_text = "".join(lines[1:end])
    body = "".join(lines[end + 1:])
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise BlueprintError(f"frontmatter YAML parse error: {e}")
    if not isinstance(fm, dict):
        raise BlueprintError(f"frontmatter must be a YAML mapping, got {type(fm).__name__}")
    return fm, body


def _load_schema(plugin_root):
    import json
    path = os.path.join(plugin_root, "schema", SCHEMA_NAME)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_frontmatter(fm, *, plugin_root):
    """Validate a frontmatter mapping against schema/app-stack-blueprint.schema.json (additionalProperties:
    false; required id/covers/parametrizes_from). Fail-closed BlueprintError on any violation."""
    try:
        import jsonschema
    except ImportError as e:
        raise BlueprintError(f"jsonschema unavailable — cannot validate fail-closed: {e}")
    try:
        jsonschema.validate(fm, _load_schema(plugin_root))
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        raise BlueprintError(f"blueprint frontmatter schema violation at {loc}: {e.message}")
    return True


# ── discovery (AC-ASBL-1) — scoped to the resolved pack dir; realpath/commonpath-contained ───────────────

def _contained(base, candidate):
    """True iff `candidate`'s realpath is within `base`'s realpath (mirrors profile_path containment)."""
    rb, rc = os.path.realpath(base), os.path.realpath(candidate)
    try:
        return os.path.commonpath([rb, rc]) == rb
    except ValueError:
        return False  # different drives / mixed abs-rel → not contained, fail-closed.


def discover_blueprints(pack_dir, *, plugin_root):
    """Discover every blueprint under <pack_dir>/blueprints/<bp-id>/SKILL.md, SCOPED to pack_dir only (NOT a
    global scan). Returns a sorted list of dicts {id, dir, path, frontmatter, body}. A profile pack with no
    `blueprints/` dir → an EMPTY list (back-compat; byte-identical load). Fail-closed BlueprintError on a
    missing/unterminated/non-parsing frontmatter, an id!=dir, a symlink, a path escaping the pack, an unknown
    frontmatter key, or a missing required field (all via containment + schema)."""
    bp_root = os.path.join(pack_dir, BLUEPRINTS_DIR)
    if not os.path.isdir(bp_root):
        return []  # no blueprints/ subtree ⇒ empty set (the back-compat invariant).
    if os.path.islink(bp_root):
        raise BlueprintError(f"blueprints dir {bp_root!r} is a symlink (rejected — containment)")
    if not _contained(pack_dir, bp_root):
        raise BlueprintError(f"blueprints dir {bp_root!r} escapes the pack dir (containment)")
    out = []
    for entry in sorted(os.listdir(bp_root)):
        bp_dir = os.path.join(bp_root, entry)
        # reject any symlink along the path (dir or the SKILL.md) — containment + symlink-rejection.
        if os.path.islink(bp_dir):
            raise BlueprintError(f"blueprint {entry!r}: directory is a symlink (rejected)")
        if not os.path.isdir(bp_dir):
            continue  # stray files under blueprints/ are ignored for discovery (only <bp-id>/ dirs count).
        if not _SLUG.match(entry):
            raise BlueprintError(f"blueprint dir {entry!r} is not a [a-z0-9-] slug")
        if not _contained(bp_root, bp_dir):
            raise BlueprintError(f"blueprint {entry!r}: resolved dir escapes the blueprints subtree")
        skill_path = os.path.join(bp_dir, BLUEPRINT_FILE)
        if os.path.islink(skill_path):
            raise BlueprintError(f"blueprint {entry!r}: {BLUEPRINT_FILE} is a symlink (rejected)")
        if not os.path.isfile(skill_path):
            raise BlueprintError(f"blueprint {entry!r}: no {BLUEPRINT_FILE} at {skill_path}")
        if not _contained(bp_dir, skill_path):
            raise BlueprintError(f"blueprint {entry!r}: {BLUEPRINT_FILE} escapes its dir (containment)")
        with open(skill_path, encoding="utf-8") as f:
            raw = f.read()
        fm, body = split_frontmatter(raw)
        validate_frontmatter(fm, plugin_root=plugin_root)
        if fm.get("id") != entry:
            raise BlueprintError(
                f"blueprint id {fm.get('id')!r} does not match its directory {entry!r}")
        out.append({"id": entry, "dir": bp_dir, "path": skill_path,
                    "frontmatter": fm, "body": body})
    return out


# ── slot resolution + render (AC-ASBL-2) — fully-specified grammar, scalar-only, fail-closed ─────────────

_MISSING = object()


def resolve_slot(profile, dotted):
    """Resolve a dotted slot path against the profile mapping. Returns the value, or _MISSING if any segment
    is absent / not a mapping. Pure read — no coercion here."""
    cur = profile
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return _MISSING
        cur = cur[seg]
    return cur


def _coerce_scalar(value, dotted):
    """A scalar (str/int/float/bool) renders as str(value) (coverage_gate int 80 -> "80"). A non-scalar
    (list/dict/None) — or bool subclass handled explicitly — fail-closes. NOTE bool IS a scalar here."""
    # bool is a subtype of int; treat it as a scalar explicitly for clarity.
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (str, int, float)):
        return str(value)
    raise BlueprintError(
        f"slot {dotted!r} resolves to a non-scalar ({type(value).__name__}) — inline list/dict/null "
        f"rendering is out of scope (fail-closed; a blueprint must bind only scalar slots)")


def render_blueprint(body, profile, parametrizes_from):
    """Render `{{ profile.<dotted> }}` placeholders against the profile's scalar slots, fail-closed.

    Grammar (exact): a placeholder is `{{ profile.<dotted-path> }}`; a `parametrizes_from` entry is the SAME
    dotted path WITHOUT the `profile.` prefix; the match key is the dotted path. Pure stdlib substitution — no
    Jinja/copier, executes no code, reads no network.

    Fail-closed: (1) a placeholder whose slot is absent or non-scalar → error; (2) a `parametrizes_from` ref
    absent from the profile → error; (3) a declared `parametrizes_from` slot never referenced by the body →
    error. Returns the rendered body string."""
    referenced = set(_PLACEHOLDER.findall(body))

    # (2) every declared parametrizes_from ref must resolve to a present scalar slot.
    for ref in parametrizes_from:
        val = resolve_slot(profile, ref)
        if val is _MISSING:
            raise BlueprintError(
                f"parametrizes_from ref {ref!r} is absent from the profile (fail-closed)")
        _coerce_scalar(val, ref)  # non-scalar declared ref → fail-closed even if unreferenced.
        # (3) a declared slot never referenced by the body is a fail-closed render error.
        if ref not in referenced:
            raise BlueprintError(
                f"parametrizes_from declares {ref!r} but the body references no "
                f"{{{{ profile.{ref} }}}} placeholder (fail-closed)")

    # (1) every placeholder in the body must resolve to a present scalar slot.
    def _sub(m):
        dotted = m.group(1)
        val = resolve_slot(profile, dotted)
        if val is _MISSING:
            raise BlueprintError(
                f"placeholder {{{{ profile.{dotted} }}}} does not resolve to a present profile slot "
                f"(fail-closed)")
        return _coerce_scalar(val, dotted)

    return _PLACEHOLDER.sub(_sub, body)


# ── blueprints_sha256 (AC-ASBL-2) — deterministic, reproducible, symlink-rejecting ───────────────────────

def blueprints_sha256(pack_dir):
    """Deterministic integrity digest over a pack's `blueprints/` subtree, or None when no non-empty subtree
    exists (absent subtree ⇒ field absent ⇒ resolution identical to today).

    Algorithm (EXACT, reproducible): for every regular file under blueprints/, sorted by POSIX relpath:
        sha256( "\\n".join( f"{posix_relpath}\\x00{sha256(raw_file_bytes).hexdigest()}" ) )
    Raw bytes (no newline normalization). Symlinks (dir or file) are REJECTED fail-closed (AC-ASBL-1
    containment)."""
    bp_root = os.path.join(pack_dir, BLUEPRINTS_DIR)
    if not os.path.isdir(bp_root) or os.path.islink(bp_root):
        if os.path.islink(bp_root):
            raise BlueprintError(f"blueprints dir {bp_root!r} is a symlink (rejected)")
        return None
    entries = []  # (posix_relpath, per_file_sha_hex)
    for dirpath, dirnames, filenames in os.walk(bp_root):
        # reject symlinked subdirectories.
        for dn in dirnames:
            if os.path.islink(os.path.join(dirpath, dn)):
                raise BlueprintError(f"blueprints subtree contains a symlinked dir {dn!r} (rejected)")
        for fn in sorted(filenames):
            fpath = os.path.join(dirpath, fn)
            if os.path.islink(fpath):
                raise BlueprintError(f"blueprints subtree contains a symlinked file {fn!r} (rejected)")
            if not os.path.isfile(fpath):
                continue
            rel = os.path.relpath(fpath, bp_root)
            posix_rel = rel.replace(os.sep, "/")
            with open(fpath, "rb") as f:
                fsha = hashlib.sha256(f.read()).hexdigest()
            entries.append((posix_rel, fsha))
    if not entries:
        return None  # empty subtree ⇒ treat as no blueprints (field absent).
    entries.sort(key=lambda t: t[0])
    joined = "\n".join(f"{rel}\x00{fsha}" for rel, fsha in entries)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ── HOW/WHAT boundary mistake-catcher (AC-ASBL-3) — bounded, NOT a security control ──────────────────────

def _compile_deny(extra=None):
    pats = list(_DENY_DEFAULT)
    if extra:
        pats.extend(extra)
    return [re.compile(p) for p in pats]


def guard_blueprint(body, profile, parametrizes_from, *, deny_extra=None):
    """The HOW/WHAT boundary mistake-catcher. Returns a list of finding strings (EMPTY ⇒ clean). Two rules:

    (a) bound-slot literal — for each `parametrizes_from` slot, its resolved literal value must NOT appear
        verbatim, WORD-BOUNDARY-anchored, in the SOURCE body (it must be the placeholder). EXEMPTS
        non-distinctive values: pure-numeric, or any token shorter than 4 chars.
    (b) domain-id deny-list — the body matches none of an operator-extensible set of project-domain ID shapes
        (default \\bHBK-\\d+\\b and \\bFEAT-\\d+\\b). `matches.*` is explicitly NOT a deny-list source.

    BOUNDED (out-of-threat-model): catches the common copy-the-literal mistake; trivially evadable by
    rewording. A mistake-catcher, NOT a security/tamper control (that boundary is the blueprints_sha256 pin +
    the trusted operator + the §8 prior-art lens)."""
    findings = []
    # (a) bound-slot literal check (word-boundary-anchored, non-distinctive-exempt).
    for ref in parametrizes_from:
        val = resolve_slot(profile, ref)
        if val is _MISSING:
            continue  # an absent ref is a render concern, not a guard finding.
        if isinstance(val, bool) or not isinstance(val, (str, int, float)):
            continue  # non-scalar is a render concern.
        literal = str(val)
        # non-distinctive exemption: pure-numeric, or a short token (< 4 chars) carries no domain signal.
        if literal.isdigit() or len(literal) < _NONDISTINCT_MIN_LEN:
            continue
        if re.search(r"\b" + re.escape(literal) + r"\b", body):
            findings.append(
                f"bound-slot literal {literal!r} (slot {ref!r}) appears verbatim in the body — "
                f"source it via {{{{ profile.{ref} }}}} instead of hardcoding it")
    # (b) domain-id deny-list.
    for pat in _compile_deny(deny_extra):
        for m in pat.finditer(body):
            findings.append(
                f"body matches the domain-id deny-list pattern {pat.pattern!r}: {m.group(0)!r} "
                f"(a product-domain id must not be hardcoded in a generic blueprint)")
    return findings
