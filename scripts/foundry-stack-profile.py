#!/usr/bin/env python3
"""foundry-stack-profile — Stack Profile schema validator + loader/resolver (Phase 2, §1; UL-0012/D4).

The keystone of the stack-aware factory's SECOND pluggable axis (Factory Workflows being the first). It
ships ONLY the schema + validator + loader + resolution/pinning contract; the profiles themselves live in a
loader-resolved in-repo `packs/stack-profiles/` tree (D4 — monorepo now, split later), each pinned by a
content sha256 in `.foundry/stack-profile.lock`. Tier 1 (D2): LOAD profile skills/conventions into the
generic worker — never generate.

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). A profile is operator-curated
machinery, authorized at the normal /foundry:authorize gate. This validator/loader is a mechanical
mistake-catcher: the content sha256 + requires_core + fail-closed resolution catch a MISTAKE (unpinned /
typo'd / drifted / core-incompatible profile), NOT a hostile pack author. The adversarial pack-trust
hardening (signed locks, full-transitive hashing, co-residence containment, boot/skills security-review) is
the DEFERRED pack-trust-model. No network fetch — resolution is the pinned in-repo packs/ tree only.
"""
import argparse
import hashlib
import json
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)
SCHEMA_PATH = os.path.join(PLUGIN_ROOT, "schema", "stack-profile.schema.json")
LOCK_NAME = "stack-profile.lock"
PROFILE_FILE = "stack-profile.yaml"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class StackProfileError(Exception):
    """Fail-closed validation / resolution error (never a silent partial load)."""


# ── app-stack blueprint library (AC-ASBL) — lazy bridge to the pure discover/render/guard/digest module ──
#
# Additive + back-compatible: the blueprint machinery lives in scripts/foundry_blueprint.py (pure functions).
# This loader bridges to it WITHOUT changing content_sha256 (which still hashes ONLY stack-profile.yaml) or
# the schema. A pack with no blueprints/ subtree exposes an EMPTY blueprint set and a None blueprints_sha256,
# so resolution reduces to today's behavior exactly.

def _blueprint_module():
    """Load scripts/foundry_blueprint.py as a module (sibling of this CLI). Returns the module."""
    import importlib.util
    path = os.path.join(HERE, "foundry_blueprint.py")
    spec = importlib.util.spec_from_file_location("foundry_blueprint", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _blueprints_sha256_of(pack_dir):
    """The deterministic blueprints_sha256 digest for a pack dir (None when no non-empty subtree). Raises
    StackProfileError (mapping the pure module's BlueprintError) so resolve_lock fail-closes on a symlink."""
    bp = _blueprint_module()
    try:
        return bp.blueprints_sha256(pack_dir)
    except bp.BlueprintError as e:
        raise StackProfileError(f"blueprints digest error: {e}")


# ── standing-versions/ digest (feat-foundry-standing-versions-matrix, AC-SVM-4) ──────────────────────────
#
# ADDITIVE + CONDITIONED: mirrors the _blueprints_sha256_of shape exactly (None when not applicable, so a
# profile that does not declare the `standing-versions` block — e.g. the non-infra node-web profile —
# resolves BYTE-IDENTICALLY through resolve_lock/relock; no new error is introduced for it). Computed via
# the SAME content_sha256_bytes primitive every other certified digest uses (never a parallel hasher), over
# the two DATA artifacts' raw bytes in a FIXED order (manifest.yaml then compatibility.yaml) so the digest
# is deterministic and reproducible.

_STANDING_VERSIONS_FILES = ("manifest.yaml", "compatibility.yaml")


def _standing_versions_sha256_of(pack_dir, doc):
    """The deterministic standing-versions/ digest for a pack dir (AC-SVM-4). None when the profile
    document does not declare the `standing-versions` block (the extension is CONDITIONED on the profile
    carrying the artifact set — a profile lacking it, e.g. node-web, is never driven RED by this addition).
    When the block IS declared, both `standing-versions/manifest.yaml` and `standing-versions/
    compatibility.yaml` MUST exist under the pack dir — a declared-but-missing artifact fails closed (it is
    certified content the loader reads, never a silent skip)."""
    if not isinstance(doc, dict) or "standing-versions" not in doc:
        return None
    sv_dir = os.path.join(pack_dir, "standing-versions")
    raw_parts = []
    for name in _STANDING_VERSIONS_FILES:
        p = os.path.join(sv_dir, name)
        if not os.path.isfile(p):
            raise StackProfileError(
                f"profile declares `standing-versions` but {name} is missing under {sv_dir} "
                "(the two DATA artifacts are the certified surface — AC-SVM-4)")
        with open(p, "rb") as f:
            raw_parts.append(f.read())
    return content_sha256_bytes(b"".join(raw_parts))


# ── version range (a small comparator; the pinned grammar lives in the future pack-trust-model) ────────

def _pad(s):
    parts = [int(x) for x in s.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _parse_range(r):
    # Two accepted shapes: the two-bound ">=X.Y[.Z],<X.Y[.Z]" AND the lower-bound-only ">=X.Y[.Z]" (no cap,
    # meaning "any core >= the lower bound"). The non-speculative lower-bound-only form is the industry-
    # consensus expression of "compatible until proven otherwise" — a speculative upper cap pinned to the
    # authoring minor false-excludes every future core (the all-clients-DOCTOR-RED bug this fixes).
    # Each bound is a 2-or-3 component dotted number; the capture is TIGHTENED from the historical loose
    # "[0-9.]+" so degenerate shapes (">=0.", ">=.1", ">=0..1", ">=0.1.2.3", a bare number, a "<"-only
    # string) are rejected HERE as StackProfileError — never leaked as a raw ValueError from _pad. This
    # regex is the byte-equivalent mirror of schema/stack-profile.schema.json's requires_core.pattern.
    # Returns (lo, hi) where hi is None for the unbounded (lower-bound-only) form.
    m = re.match(r"^>=(\d+\.\d+(?:\.\d+)?)(?:,<(\d+\.\d+(?:\.\d+)?))?$", r or "")
    if not m:
        raise StackProfileError(f"requires_core range {r!r} is not '>=X.Y[.Z]' or '>=X.Y[.Z],<X.Y[.Z]'")
    lo = _pad(m.group(1))
    hi = _pad(m.group(2)) if m.group(2) is not None else None
    return lo, hi


def core_satisfies(requires_core, core_version):
    # A lower-bound-only range (hi is None) admits EVERY core at or above the lower bound; a two-bound range
    # keeps its exact half-open [lo, hi) semantics. A reversed/empty two-bound range (e.g. ">=0.3,<0.2") is
    # well-formed but unsatisfiable — it admits no core (caught downstream by the ship-time guard, not here).
    lo, hi = _parse_range(requires_core)
    cv = _pad(core_version)
    return lo <= cv and (hi is None or cv < hi)


def core_version(plugin_root=None):
    pj = os.path.join(plugin_root or PLUGIN_ROOT, ".claude-plugin", "plugin.json")
    with open(pj, encoding="utf-8") as f:
        return json.load(f)["version"]


# ── schema validation (AC-SPS-1) ──────────────────────────────────────────────────────────────────────

def load_schema(plugin_root=None):
    path = os.path.join(plugin_root or PLUGIN_ROOT, "schema", "stack-profile.schema.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── profile_kind discriminator + infra_binding (AC-SPIB-1/2) ──────────────────────────────────────────
#
# Additive + back-compatible: profile_kind is OPTIONAL and absent ⇒ "app", so every existing app profile
# (incl. node-web) validates byte-identically. The draft-07-safe conditional in the schema toggles only the
# `required` array (see schema/stack-profile.schema.json `allOf`). The two infra semantics that JSON Schema
# cannot express cleanly — the READ-ROLE command guard and the ≥1-HIGH blast-radius rule — are enforced HERE
# as a post-schema, fail-closed check, so they hold for BOTH the `--validate` and `load_profile` paths and
# for the `infra_binding`-exposing check. The block is declarative data — NEVER executed here.

def profile_kind(doc):
    """The effective profile_kind: an explicit value, else the back-compat default 'app' (absent ⇒ app)."""
    return (doc or {}).get("profile_kind", "app")


def infra_binding_of(doc):
    """Expose the resolved infra_binding for a profile object (AC-SPIB-3): the block for profile_kind:infra,
    None for an app/absent-kind profile. Pure read-only accessor — no command execution.
    AC-IDMULTI-1 (FF-3 / ER #57): the returned block ALWAYS carries a `work_dirs` key — the profile's
    per-role {infra, gitops} map when declared, else None (single-dir; every command part runs in the one
    existing CWD). Returned as a SHALLOW copy so the normalization never mutates the loaded doc —
    nested values (work_dirs/blast_radius/gitops_paths) remain aliased to the source; consumers are
    READ-ONLY by contract and must never mutate them (sec-review nit)."""
    if profile_kind(doc) == "infra":
        ib = (doc or {}).get("infra_binding")
        if isinstance(ib, dict):
            ib = dict(ib)
            ib.setdefault("work_dirs", None)
            # AC-GOP-1 (ER #89): the GitOps toggle rides the same normalized exposure — the
            # profile's explicit bool when declared, else None (the legacy tri-state).
            ib.setdefault("gitops_enabled", None)
        return ib
    return None


# Read-only leading-verb allowlist, MIRRORED from CTX's command-policy (AC-SPIB-2). The plan/verify/policy
# slots are read-only; a mutating verb (tofu apply|destroy, kubectl apply|delete, helm install|upgrade) in a
# read-only slot is REJECTED fail-closed (the static belt-and-suspenders; the authoritative floor is the CTX
# runtime guard). Keyed by tool → the set of allowed read-only subcommands; a `*`-bearing entry (kubectl)
# additionally requires a server-dry-run flag for the otherwise-mutating verbs.
_READROLE_SLOTS = ("plan", "verify", "policy")
_READONLY_VERBS = {
    "tofu": {"plan", "validate", "output", "fmt"},
    "terraform": {"plan", "validate", "output", "fmt"},
    "kubectl": {"get", "describe", "diff"},
    "argocd": {"app"},          # only `argocd app diff|get` (sub-subcommand gated below)
    "conftest": {"test", "verify", "parse"},
    "helm": {"template", "lint"},
}
# Subcommand-LESS read-only tools (FF-4): a tool with NO mutating mode whose canonical invocation takes
# files/flags as its second token, not a subcommand — so the `sub in allowed` check above does not apply.
# `kubeconform` is pure Kubernetes-manifest SCHEMA validation: it reads manifests + (offline) schemas, never
# contacts a cluster/cloud and has no apply/write mode, so EVERY invocation is read-only. Admitting it is a
# minimal, safe widening of the read-role allowlist (it adds exactly `kubeconform`; it admits no mutating verb).
_READONLY_NOSUB_TOOLS = {"kubeconform"}
# Mutating verbs that may appear ONLY in the `apply` slot — an explicit denylist so a novel tool that is not
# in the allowlist above does not silently pass; we reject on a KNOWN mutating verb regardless of tool match.
_MUTATING_VERBS = {
    ("tofu", "apply"), ("tofu", "destroy"),
    ("terraform", "apply"), ("terraform", "destroy"),
    ("kubectl", "apply"), ("kubectl", "delete"), ("kubectl", "replace"), ("kubectl", "create"),
    ("helm", "install"), ("helm", "upgrade"), ("helm", "uninstall"),
    ("argocd", "app"),  # `argocd app sync` is mutating — gated by sub-subcommand below
}
_KUBECTL_DRYRUN = "--dry-run=server"
_ARGOCD_READONLY_SUB = {"diff", "get", "list"}


def _tokens(cmd):
    return (cmd or "").split()


# Shell connectors a slot command may chain on. We split on these and require EVERY segment to be read-only.
# HEURISTIC connector-split (a static belt-and-suspenders), NOT a full shell parser: it splits on the bare
# token forms of `&&`, `||`, `;`, and pipe `|`, so it does NOT catch a connector hidden inside a quoted arg
# (e.g. `kubeconform "a && b"`) or other shell metacharacters. The AUTHORITATIVE enforcement remains the CTX
# runtime command-policy guard; this loader-side check is the fail-closed mistake-catcher (a mutating verb
# CHAINED after a read-only leading verb in a read-only slot is the bypass it closes).
_CONNECTOR_RE = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def _readonly_segment_ok(cmd):
    """True iff a SINGLE segment's leading verb is on the read-only allowlist (AC-SPIB-2). Fail-closed: an
    empty segment, an unknown tool, or a known mutating verb (not server-dry-run-guarded) ⇒ False."""
    toks = _tokens(cmd)
    if not toks:
        return False
    tool = os.path.basename(toks[0])
    sub = toks[1] if len(toks) > 1 else ""
    # argocd: only `app <diff|get|list>` is read-only; `app sync` (and bare `app`) is mutating.
    if tool == "argocd":
        subsub = toks[2] if len(toks) > 2 else ""
        return sub == "app" and subsub in _ARGOCD_READONLY_SUB
    # kubectl: get/describe/diff are read-only; an otherwise-mutating verb is allowed ONLY with server dry-run.
    if tool == "kubectl":
        if sub in _READONLY_VERBS.get("kubectl", set()):
            return True
        if (tool, sub) in _MUTATING_VERBS:
            return _KUBECTL_DRYRUN in toks
        return False
    # Known mutating verb for a known tool ⇒ reject outright.
    if (tool, sub) in _MUTATING_VERBS:
        return False
    # Subcommand-less read-only tool (FF-4: kubeconform) ⇒ admitted on the leading verb alone (no mutating
    # mode exists). Gated AFTER the _MUTATING_VERBS denylist so it can never re-admit a known mutating verb.
    if tool in _READONLY_NOSUB_TOOLS:
        return True
    allowed = _READONLY_VERBS.get(tool)
    if allowed is None:
        return False  # unknown tool ⇒ fail-closed (operator must extend the allowlist deliberately).
    return sub in allowed


def _readonly_command_ok(cmd):
    """True iff `cmd` is read-only as a WHOLE (AC-SPIB-2). A slot command may CHAIN segments on shell
    connectors (e.g. the legitimate `kubeconform … && conftest test …` policy slot, or `tofu plan && helm
    template …`); EVERY non-empty segment must independently pass the read-only leading-verb check. This
    closes the command-policy bypass where a read-only leading verb (kubeconform) is chained with a trailing
    MUTATING verb (`… && kubectl apply -f x.yaml`, `… ; tofu destroy`) — the trailing segment is examined and
    REJECTS. Fail-closed: an empty command, or ANY empty/unparseable segment (e.g. a dangling `&&`), ⇒ False."""
    if not (cmd or "").strip():
        return False
    segments = _CONNECTOR_RE.split(cmd)
    if not segments:
        return False
    for seg in segments:
        if not _readonly_segment_ok(seg):
            return False  # an empty segment (dangling connector) or a non-read-only segment ⇒ fail-closed.
    return True


def validate_work_dirs(work_dirs, repo_root=None):
    """AC-IDMULTI-1 (FF-3 / ER #57) — fail-closed confinement for `infra_binding.work_dirs`. Each role
    value must be a non-empty RELATIVE path with no `..` segment (the profile_path/foundry-wt containment
    precedent: a path-traversal work_dir is an authoring error, never a silent out-of-repo CWD). When a
    repo_root is supplied, the resolved path must additionally stay under that root (realpath commonpath).
    `None`/absent is valid (single-dir). Raises StackProfileError on violation."""
    if work_dirs is None:
        return True
    if not isinstance(work_dirs, dict):
        raise StackProfileError("infra_binding.work_dirs must be an object of role -> relative dir")
    for role, val in work_dirs.items():
        if role not in ("infra", "gitops"):
            raise StackProfileError(f"infra_binding.work_dirs role {role!r} is not one of infra|gitops")
        if not isinstance(val, str) or not val.strip():
            raise StackProfileError(f"infra_binding.work_dirs.{role} must be a non-empty string")
        if os.path.isabs(val):
            raise StackProfileError(
                f"infra_binding.work_dirs.{role} {val!r} is absolute — work dirs are RELATIVE paths "
                "confined under the active code repo's root (containment)")
        if ".." in val.replace("\\", "/").split("/"):
            raise StackProfileError(
                f"infra_binding.work_dirs.{role} {val!r} contains a `..` segment — rejected (containment)")
        if repo_root:
            real_root = os.path.realpath(repo_root)
            real_path = os.path.realpath(os.path.join(repo_root, val))
            if os.path.commonpath([real_root, real_path]) != real_root:
                raise StackProfileError(
                    f"infra_binding.work_dirs.{role} {val!r} escapes the repo root (containment)")
    return True


def validate_infra_binding(doc):
    """Post-schema infra semantics (AC-SPIB-2), fail-closed. Assumes JSON Schema has already validated the
    SHAPE (required keys, enums, types). Enforces the rules JSON Schema cannot: (1) the READ-ROLE guard —
    plan/verify/policy must carry a read-only leading verb; (2) ≥1 blast_radius rule with tier HIGH;
    (3) `work_dirs` confinement (AC-IDMULTI-1: relative, no `..`; the realpath-escape check runs where a
    repo root is known — validate_work_dirs(…, repo_root=…)). Raises StackProfileError on violation.
    A no-op for an app/absent-kind profile."""
    if profile_kind(doc) != "infra":
        return True
    ib = doc.get("infra_binding")
    if not isinstance(ib, dict):  # schema guarantees this for infra, but stay fail-closed.
        raise StackProfileError("profile_kind:infra requires an infra_binding object")
    # (3) work_dirs confinement (structural part; the escape check re-runs with a root where known).
    validate_work_dirs(ib.get("work_dirs"))
    # (4) gitops_enabled consistency (AC-GOP-2, ER #89) — an EXPLICIT toggle beats sentinel emptiness:
    # false + non-empty paths is a contradiction (declared N/A yet carrying reconcile paths); true +
    # empty paths is the silent-skip risk (declared load-bearing yet pathless). ABSENT => legacy
    # semantics unchanged (empty or non-empty both valid).
    ge = ib.get("gitops_enabled")
    paths = ib.get("gitops_paths") or []
    if ge is False and paths:
        raise StackProfileError(
            "infra_binding.gitops_enabled: false contradicts a NON-EMPTY gitops_paths — a declared "
            "non-GitOps target carries no reconcile paths (fail-closed)")
    if ge is True and not paths:
        raise StackProfileError(
            "infra_binding.gitops_enabled: true requires a NON-EMPTY gitops_paths — a declared "
            "GitOps target with no paths would silently skip the reconcile surface (fail-closed)")
    # (1) read-role guard on the read-only slots.
    for slot in _READROLE_SLOTS:
        cmd = ib.get(slot)
        if not _readonly_command_ok(cmd):
            raise StackProfileError(
                f"infra_binding.{slot} {cmd!r} is not a read-only command "
                f"(a mutating verb in a read-only slot is rejected — read-role guard)")
    # (2) ≥1 HIGH blast-radius tier (machine-evaluable rules — shape already schema-checked).
    tiers = [r.get("tier") for r in ib.get("blast_radius", []) if isinstance(r, dict)]
    if "HIGH" not in tiers:
        raise StackProfileError(
            "infra_binding.blast_radius must contain ≥1 HIGH tier rule (a no-HIGH profile is a "
            "fail-closed authoring error)")
    return True


def validate_profile(doc, *, plugin_root=None):
    """Validate a parsed profile against the JSON Schema, fail-closed, THEN the infra semantics for
    profile_kind:infra. Raises StackProfileError on any missing required key, unknown top-level key
    (additionalProperties:false), malformed value, OR an infra read-role / blast-radius violation. The app
    path is unchanged: for an app/absent-kind profile validate_infra_binding is a no-op."""
    try:
        import jsonschema
    except ImportError as e:
        raise StackProfileError(f"jsonschema unavailable — cannot validate fail-closed: {e}")
    try:
        jsonschema.validate(doc, load_schema(plugin_root))
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        raise StackProfileError(f"profile schema violation at {loc}: {e.message}")
    validate_infra_binding(doc)
    return True


# ── content hash + path resolution (D4 — packs/ tree only, never the bundle) ──────────────────────────

def content_sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def content_sha256(path) -> str:
    with open(path, "rb") as f:
        return content_sha256_bytes(f.read())


def _profiles_dir(root=None):
    return os.path.join(root or PLUGIN_ROOT, "packs", "stack-profiles")


def profile_path(id, *, root=None):
    """Resolve a profile id STRICTLY within <root>/packs/stack-profiles/<id>/stack-profile.yaml (the
    CHECK-0b path-containment precedent). NEVER the core plugin's version-keyed skills/ bundle (D4
    guardrail 1). Raises StackProfileError on a non-slug id or a path escaping the packs tree."""
    if not isinstance(id, str) or not _SLUG.match(id):
        raise StackProfileError(f"profile id {id!r} is not a [a-z0-9-]+ slug (no path separators/traversal)")
    base = _profiles_dir(root)
    path = os.path.join(base, id, PROFILE_FILE)
    real_base, real_path = os.path.realpath(base), os.path.realpath(path)
    if os.path.commonpath([real_base, real_path]) != real_base:
        raise StackProfileError(f"profile id {id!r}: resolved path escapes the stack-profiles dir (containment)")
    return path


def load_profile(id, *, root=None, plugin_root=None):
    """Load + schema-validate a single profile by id from the packs/ tree. Returns (doc, path, sha256).
    Fail-closed on missing file, malformed YAML, or schema violation."""
    path = profile_path(id, root=root)
    if not os.path.isfile(path):
        raise StackProfileError(f"profile {id!r}: no profile at {path}")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise StackProfileError(f"profile {id!r}: malformed YAML in {path}: {e}")
    validate_profile(doc, plugin_root=plugin_root)
    if doc.get("id") != id:
        raise StackProfileError(f"profile id {doc.get('id')!r} does not match its directory {id!r}")
    return doc, path, content_sha256(path)


def bundle_leak(id, *, plugin_root=None):
    """D4 guardrail 3: a profile must NOT be resolvable from the core plugin's version-keyed skills/ bundle.
    Returns True iff a stack-profile for `id` leaks into <plugin_root>/skills/ (a decoupling-erosion RED)."""
    root = plugin_root or PLUGIN_ROOT
    leaked = os.path.join(root, "skills", id, PROFILE_FILE)
    return os.path.isfile(leaked)


# ── lock (AC-SPS-2) + load/resolve (AC-SPS-3) ─────────────────────────────────────────────────────────

def _project_dir(project_dir=None):
    return project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def lock_path(project_dir=None):
    return os.path.join(_project_dir(project_dir), ".foundry", LOCK_NAME)


def read_lock(project_dir=None):
    path = lock_path(project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise StackProfileError(f"stack-profile.lock unreadable/malformed: {e}")


def resolve_lock(project_dir=None, *, root=None, plugin_root=None):
    """AC-SPS-3: resolve every locked profile from the pinned packs/ tree, fail-closed. For each lock
    entry: the profile loads + validates, requires_core admits the running core, and the resolved content
    sha256 EQUALS the locked sha256 (the pin actually gates). Returns the list of resolved profile docs;
    raises StackProfileError on any failure. NO code generation, NO network fetch."""
    lock = read_lock(project_dir)
    if lock is None:
        raise StackProfileError(f"no {LOCK_NAME} at {lock_path(project_dir)}")
    entries = lock.get("profiles")
    if not isinstance(entries, list) or not entries:
        raise StackProfileError("stack-profile.lock has no `profiles` list")
    cv = core_version(plugin_root)
    resolved = []
    for ent in entries:
        if not isinstance(ent, dict):
            raise StackProfileError(f"lock entry not a mapping: {ent!r}")
        pid, pver, psha = ent.get("id"), ent.get("version"), ent.get("sha256")
        if not pid or not pver or not psha:
            raise StackProfileError(f"lock entry missing id/version/sha256: {ent!r}")
        doc, path, actual_sha = load_profile(pid, root=root, plugin_root=plugin_root)
        if doc.get("version") != pver:
            raise StackProfileError(
                f"profile {pid!r}: resolved version {doc.get('version')!r} != locked {pver!r}")
        if not core_satisfies(doc["requires_core"], cv):
            raise StackProfileError(
                f"profile {pid!r}: requires_core {doc['requires_core']!r} excludes running core {cv}")
        if actual_sha != psha:
            raise StackProfileError(
                f"profile {pid!r}: content sha256 {actual_sha[:12]}… != locked {str(psha)[:12]}… (drift/tamper)")
        if bundle_leak(pid, plugin_root=plugin_root):
            raise StackProfileError(
                f"profile {pid!r}: leaks into the core plugin skills/ bundle (D4 guardrail 3)")
        # AC-ASBL-2: ADDITIVE, OPTIONAL blueprints_sha256 verify — fires ONLY when the resolved pinned pack
        # has a non-empty blueprints/ subtree. content_sha256 is UNCHANGED (it still hashes ONLY
        # stack-profile.yaml), so every existing pin resolves byte-identically: a pack with no blueprints/
        # has actual_bsha is None ⇒ the lock entry's blueprints_sha256 (absent ⇒ None) matches and NO new
        # error is introduced. When a blueprints/ subtree exists, the digest binds its bytes — a tamper/swap
        # of any blueprint byte mismatches ⇒ resolve_lock fail-closes.
        actual_bsha = _blueprints_sha256_of(os.path.dirname(path))
        locked_bsha = ent.get("blueprints_sha256")
        if actual_bsha is not None or locked_bsha is not None:
            if actual_bsha != locked_bsha:
                raise StackProfileError(
                    f"profile {pid!r}: blueprints sha256 {str(actual_bsha)[:12]}… != locked "
                    f"{str(locked_bsha)[:12]}… (blueprint drift/tamper/swap — fail-closed)")
        # AC-SVM-4 (feat-foundry-standing-versions-matrix): ADDITIVE, CONDITIONED standing-versions/ verify —
        # fires ONLY when the resolved profile declares the `standing-versions` block (e.g. the infra
        # aws-eks-karpenter profile). A profile without it (node-web) computes None on both sides ⇒ no new
        # error is introduced ⇒ byte-identical resolution to before this extension.
        actual_svsha = _standing_versions_sha256_of(os.path.dirname(path), doc)
        locked_svsha = ent.get("standing_versions_sha256")
        if actual_svsha is not None or locked_svsha is not None:
            if actual_svsha != locked_svsha:
                raise StackProfileError(
                    f"profile {pid!r}: standing-versions sha256 {str(actual_svsha)[:12]}… != locked "
                    f"{str(locked_svsha)[:12]}… (pin-catalog drift/tamper — fail-closed)")
        resolved.append(doc)
    return resolved


def write_lock(lock_obj, project_dir=None):
    """Atomically write the lock object to .foundry/stack-profile.lock (tmp file + os.replace) — never a
    partial lock on a crash mid-write."""
    path = lock_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lock_obj, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:                                    # never leave a stray .tmp behind on a write failure
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def relock_lock(project_dir=None, *, root=None, plugin_root=None):
    """AC-SPRL-1/2: re-resolve the ALREADY-locked profile ids against the trusted packs/ tree and
    atomically re-write `.foundry/stack-profile.lock` with each id's CURRENT
    {version, sha256, blueprints_sha256}, preserving the locked id-set and any other per-entry/top-level
    keys (e.g. pack_ref). VALIDATE-BEFORE-WRITE: every entry is checked first; the lock is written ONLY if
    ALL entries pass, else StackProfileError is raised with NO write (fail-closed). Refuses an absent/
    malformed lock, an absent-from-packs / schema-invalid / core-incompatible / bundle-leaking profile, or
    a DOWNGRADE (resolved version < locked version) — adopting a trusted profile-version advance from the
    trusted packs/, NEVER laundering a downgrade or an incompatible profile. Returns the
    (id, old_version, new_version) deltas. The stack-profile lock's threat model is the module's own
    (a mistake-catcher, not a hostile pack author); this verb is the missing operator re-lock."""
    lock = read_lock(project_dir)            # raises StackProfileError on malformed
    if lock is None:
        raise StackProfileError(f"no {LOCK_NAME} at {lock_path(project_dir)} — nothing to relock")
    entries = lock.get("profiles")
    if not isinstance(entries, list) or not entries:
        raise StackProfileError("stack-profile.lock has no `profiles` list")
    cv = core_version(plugin_root)
    new_entries, deltas = [], []
    # NB: the per-entry trusted checks below (load_profile + core_satisfies + bundle_leak + the
    # blueprints digest) intentionally MIRROR resolve_lock's, minus the sha/version-match gates (which are
    # exactly what relock is replacing). They are hand-duplicated, not shared — if a NEW trusted-resolve
    # guardrail is ever added to resolve_lock, add it here too, or relock could write a lock that then fails
    # resolve_lock ("relocked OK but doctor still RED"). The selftest's relock→resolve round-trip catches it.
    for ent in entries:                      # validate EVERY entry before writing ANY
        if not isinstance(ent, dict):
            raise StackProfileError(f"lock entry not a mapping: {ent!r}")
        pid, locked_ver = ent.get("id"), ent.get("version")
        if not pid:
            raise StackProfileError(f"lock entry missing id: {ent!r}")
        doc, path, actual_sha = load_profile(pid, root=root, plugin_root=plugin_root)  # raises if absent/invalid
        if not core_satisfies(doc["requires_core"], cv):
            raise StackProfileError(
                f"profile {pid!r}: requires_core {doc['requires_core']!r} excludes running core {cv} — refuse relock")
        if bundle_leak(pid, plugin_root=plugin_root):
            raise StackProfileError(f"profile {pid!r}: leaks into the core plugin skills/ bundle — refuse relock")
        new_ver = doc["version"]
        try:
            is_downgrade = locked_ver is not None and _pad(new_ver) < _pad(locked_ver)
        except (ValueError, AttributeError) as e:
            raise StackProfileError(f"profile {pid!r}: unparseable version for the downgrade check: {e}")
        if is_downgrade:
            raise StackProfileError(
                f"profile {pid!r}: resolved version {new_ver} < locked {locked_ver} (downgrade) — refuse relock")
        updated = dict(ent)                  # preserve id / pack_ref / any other per-entry keys
        updated["version"] = new_ver
        updated["sha256"] = actual_sha
        bsha = _blueprints_sha256_of(os.path.dirname(path))
        if bsha is not None:
            updated["blueprints_sha256"] = bsha
        else:
            updated.pop("blueprints_sha256", None)
        # AC-SVM-4: re-derive the standing-versions/ digest exactly as resolve_lock verifies it.
        svsha = _standing_versions_sha256_of(os.path.dirname(path), doc)
        if svsha is not None:
            updated["standing_versions_sha256"] = svsha
        else:
            updated.pop("standing_versions_sha256", None)
        new_entries.append(updated)
        deltas.append((pid, locked_ver, new_ver))
    new_lock = dict(lock)                    # preserve other top-level lock keys
    new_lock["profiles"] = new_entries
    write_lock(new_lock, project_dir)        # atomic — reached ONLY after all entries validated
    return deltas


def loaded_context(resolved, *, root=None, plugin_root=None):
    """The deterministic context seam: the implementation_skills + conventions docs the generic worker
    reads. Pure data assembly — no generation.

    AC-ASBL-2 ADDITIVE return-shape extension: each entry now ALSO carries `blueprints` — the resolved +
    RENDERED granular pattern blueprints discovered WITHIN the resolved profile's own pack dir (NOT a global
    scan), each `{{ profile.<dotted> }}` placeholder rendered against the profile's own scalar slots. A
    profile with no blueprints/ subtree exposes `blueprints: []` (back-compat; the prior keys are unchanged).
    Discovery is scoped to the lock-pinned pack; render is pure stdlib substitution, fail-closed."""
    bp = _blueprint_module()
    out = []
    for doc in resolved:
        entry = {"id": doc["id"], "version": doc["version"],
                 "implementation_skills": doc["implementation_skills"],
                 "conventions_doc": doc["architecture"]["conventions_doc"]}
        pack_dir = os.path.dirname(profile_path(doc["id"], root=root))
        blueprints = []
        try:
            for bpd in bp.discover_blueprints(pack_dir, plugin_root=plugin_root or PLUGIN_ROOT):
                rendered = bp.render_blueprint(bpd["body"], doc,
                                               bpd["frontmatter"].get("parametrizes_from", []))
                blueprints.append({"id": bpd["id"],
                                   "covers": bpd["frontmatter"].get("covers", []),
                                   "parametrizes_from": bpd["frontmatter"].get("parametrizes_from", []),
                                   "rendered": rendered})
        except bp.BlueprintError as e:
            raise StackProfileError(f"profile {doc['id']!r}: blueprint load/render fail-closed: {e}")
        entry["blueprints"] = blueprints
        out.append(entry)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stack Profile validator + loader (Tier 1; load, never generate).")
    ap.add_argument("--validate", metavar="ID_OR_PATH", help="validate a profile (id under packs/, or a path)")
    ap.add_argument("--load", action="store_true", help="resolve the active stack-profile.lock (fail-closed)")
    ap.add_argument("--relock", action="store_true",
                    help="re-resolve the already-locked profiles against packs/ and re-write the lock "
                         "(validate-before-write; fail-closed on downgrade/invalid/core-incompatible)")
    args = ap.parse_args(argv)
    try:
        if args.validate:
            tgt = args.validate
            if os.path.isfile(tgt):
                with open(tgt, encoding="utf-8") as f:
                    doc = yaml.safe_load(f)
                validate_profile(doc)
                print(f"profile valid: {tgt}")
            else:
                doc, path, sha = load_profile(tgt)
                print(f"profile valid: {tgt} (sha256={sha[:12]}…)")
            return 0
        if args.load:
            resolved = resolve_lock()
            for ctx in loaded_context(resolved):
                print(f"loaded {ctx['id']}@{ctx['version']}: {len(ctx['implementation_skills'])} skill(s), "
                      f"conventions={ctx['conventions_doc']}")
            return 0
        if args.relock:
            deltas = relock_lock()
            for pid, old, new in deltas:
                print(f"relocked: {pid} {old}→{new}" if old != new else f"relocked: {pid} {new} (sha refreshed)")
            print(f"stack-profile.lock re-locked ({len(deltas)} profile(s))")
            return 0
        ap.print_help()
        return 0
    except StackProfileError as e:
        print(f"stack-profile error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
