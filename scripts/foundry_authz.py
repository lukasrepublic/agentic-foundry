"""foundry_authz — the per-spec authorization state machine + operator registry +
the freeze-write behind /foundry:authorize (§22.6 / §22.1 / §22.12).

State machine (§22.6 Figure 4):

    DRAFT ──audit──► HARDENED ──contract authored──► CONTRACT_FROZEN
                                                          │ /foundry:authorize
                                                          ▼
                                                     AUTHORIZED
    ANY change to spec_sha256 OR contract_sha256 invalidates the record →
    RE-BASELINE (re-enter the full forward path; never a lighter re-sign — NIST CM-3).

This module determines the state from committed artifacts and performs the
authorize freeze-write. It does NOT re-implement the §8 audit (HARDENED is gated by
the existing adversarial-spec-audit trail, checked by the skill, not here).

operator_id is resolved against .claude/foundry-operators.json (the §19.5 item-0
operator-identity substrate). Without the registry operator_id is unvalidated free
text and the §22.5a attribution silently degrades — so resolution FAILS CLOSED when
the registry is absent or the id is unregistered.

(The delegable-authorization-envelope apparatus — the WAVE-authorization/`drift_policy`
opt-in, `envelope_auto_rebaseline(...)`, and the hash-chained `.foundry/authz-ledger.jsonl`
ledger primitives — was retired by [Atom: feat-foundry-btb-subtraction-merge-side]:
its consumer, the atom-admission gate, is deleted, and the dead consumer path this module
carried for it was removed with it. This module now carries the
front-authorization core only.

Public surface: `resolve_operator(...)` / `load_operators(...)` (the operator-registry
resolution above), `spec_state(...)` (the state-machine read), and `authorize(...)` (the
freeze-write) — see each function's own docstring below.)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from datetime import datetime, timezone

import foundry_contract as fc

_HERE_DIR = os.path.dirname(os.path.abspath(__file__))

# State labels.
DRAFT = "DRAFT"
CONTRACT_FROZEN = "CONTRACT_FROZEN"   # contract validates (floors 1-4) but unauthorized
AUTHORIZED = "AUTHORIZED"
RE_BASELINE_NEEDED = "RE_BASELINE_NEEDED"  # authorized block present but a hash drifted


class AuthzError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Operator registry (.claude/foundry-operators.json)
# --------------------------------------------------------------------------- #
def operators_registry_path(repo_root: str | None = None) -> str:
    root = repo_root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(root, ".claude", "foundry-operators.json")


def load_operators(repo_root: str | None = None) -> dict:
    path = operators_registry_path(repo_root)
    if not os.path.exists(path):
        raise AuthzError(
            f"operator registry not found at {path} — operator_id cannot be validated "
            f"(fail-closed §22.12). Create it with at least one operator before authorizing."
        )
    with open(path) as fh:
        data = json.load(fh)
    ops = data.get("operators")
    if not isinstance(ops, dict) or not ops:
        raise AuthzError(f"operator registry {path} has no operators")
    return ops


def resolve_operator(explicit: str | None = None, repo_root: str | None = None) -> str:
    """Resolve + validate the active operator_id. Precedence: explicit arg →
    $FOUNDRY_OPERATOR. FAILS CLOSED if unset or unregistered."""
    op = explicit or os.environ.get("FOUNDRY_OPERATOR")
    if not op:
        raise AuthzError(
            "no operator_id: pass --operator or set FOUNDRY_OPERATOR (direnv does not "
            "load in Claude Code's Bash tool — set it explicitly)"
        )
    ops = load_operators(repo_root)
    if op not in ops:
        raise AuthzError(
            f"operator_id '{op}' is not in the registry ({sorted(ops)}). "
            f"Register it in .claude/foundry-operators.json first (fail-closed §22.12)."
        )
    return op


# --------------------------------------------------------------------------- #
# State determination
# --------------------------------------------------------------------------- #
def _authorized_block(contract_path: str) -> dict | None:
    data = fc.load_contract(contract_path)
    blk = data.get("authorized")
    return blk if isinstance(blk, dict) else None


def spec_state(spec_path: str, contract_path: str) -> tuple[str, list[str]]:
    """Return (state, notes). Determines the authorization state from committed
    artifacts alone."""
    notes: list[str] = []
    if not os.path.exists(contract_path):
        return DRAFT, ["no acceptance-contract.yaml beside the spec"]

    ok, errors, _warn = validate_spec_contract(spec_path, contract_path)
    blk = _authorized_block(contract_path)

    if not ok:
        # An unauthorized invalid contract is still DRAFT; an AUTHORIZED record over a
        # now-invalid contract is a re-baseline (the contract changed under the auth).
        if blk is None:
            return DRAFT, ["contract does not pass freeze floors: " + "; ".join(errors)]
        return RE_BASELINE_NEEDED, ["authorized record over a contract that no longer validates"]

    if blk is None:
        return CONTRACT_FROZEN, ["contract validates; awaiting /foundry:authorize"]

    # Authorized block present — verify the frozen hashes still match the artifacts.
    cur_contract_hash = fc.contract_sha256(contract_path)
    cur_spec_hash = fc.spec_sha256(spec_path) if os.path.exists(spec_path) else None
    if blk.get("contract_sha256") != cur_contract_hash:
        notes.append("contract_sha256 drift since authorization")
        return RE_BASELINE_NEEDED, notes
    if cur_spec_hash is not None and blk.get("spec_sha256") != cur_spec_hash:
        notes.append("spec_sha256 (normative region) drift since authorization")
        return RE_BASELINE_NEEDED, notes
    return AUTHORIZED, ["authorized; hashes match current spec + contract"]


def is_authorized(spec_path: str, contract_path: str) -> bool:
    state, _ = spec_state(spec_path, contract_path)
    return state == AUTHORIZED


# --------------------------------------------------------------------------- #
# Spec-side AC-ID extraction (freeze-floor-ac-extraction, ER #142, AC-ACX-1..8)
# --------------------------------------------------------------------------- #
# Definition-scoped, suffix-aware AC-ID grammar. AC-ACX-2: the terminal is
# `-\d+[a-z]?` (a single trailing lowercase letter for sub-criteria); an uppercase or
# multi-letter suffix (AC-FOO-3A, AC-FOO-3ab) does NOT satisfy this grammar at all —
# it is neither recognized as a definition subject NOR silently truncated to its
# numeric prefix, because the mandatory `\b` boundary after the optional single-letter
# suffix fails to hold against a following word character (another letter/digit),
# which forces the WHOLE candidate match (including any shorter numeric-only prefix)
# to fail rather than fall back to a truncated match.
_AC_TOKEN = r"AC(?:-[A-Z0-9]+)*-\d+[a-z]?"

# A definition line: optional leading indentation (nested sub-criteria), a Markdown
# unordered-list bullet (-, *, or +), an optional `[ ]`/`[x]` checkbox, optional `**`
# bold markers, then the AC-ID as the immediate subject. Ordered/numbered list lines
# (`1. **AC-…**`) are deliberately NOT recognized (foundry authors no such form —
# Clarifications N1/N2).
_DEF_LINE_RE = re.compile(
    r"^[ \t]*[-*+][ \t]+(?:\[[ xX]\][ \t]+)?\*{0,2}(" + _AC_TOKEN + r")\b"
)

# Whole-file mention scan (suffix-aware) — used ONLY to power the AC-ACX-6
# "mentions-but-does-not-define" diagnostic, never to widen the definition-scoped set
# that feeds the freeze-floor-3 bijection (AC-ACX-1/-3/-4).
_MENTION_RE = re.compile(r"\b" + _AC_TOKEN + r"\b")


def _fences_balanced(raw: bytes) -> bool:
    """True when the spec's `<!-- normative -->` / `<!-- /normative -->` fences are
    ABSENT entirely, or present with EQUAL open/close counts AND every open is
    followed by a close (mirrors foundry_contract.spec_normative_bytes' own pairing
    walk). False for a MALFORMED fence set (unequal counts, or an open with no
    following close) — the case foundry_contract would otherwise silently reduce to a
    partial region (AC-ACX-1, the B2 fix)."""
    o_count = raw.count(fc._FENCE_OPEN)
    c_count = raw.count(fc._FENCE_CLOSE)
    if o_count != c_count:
        return False
    if o_count == 0:
        return True
    pos = 0
    for _ in range(o_count):
        o = raw.find(fc._FENCE_OPEN, pos)
        if o == -1:
            return False
        c = raw.find(fc._FENCE_CLOSE, o)
        if c == -1:
            return False
        pos = c + len(fc._FENCE_CLOSE)
    return True


def _whole_body_minus_changelog(raw: bytes) -> bytes:
    """The SAME whole-body-minus-§Changelog fallback foundry_contract.spec_normative_bytes
    applies when no fences are present — reused directly (not re-derived) here for the
    MALFORMED-fence case, where spec_normative_bytes' own fenced branch would instead
    silently return a partial (truncated) region. Never "extract nothing", never a
    silent partial (AC-ACX-1)."""
    m = fc._CHANGELOG_HEADING.search(raw)
    if not m:
        return raw
    nxt = re.compile(rb"(?m)^##\s").search(raw, m.end())
    end = nxt.start() if nxt else len(raw)
    return raw[:m.start()] + raw[end:]


def _extraction_region_bytes(spec_path: str) -> bytes:
    """Resolve the bytes AC-definition extraction runs over: the normative region
    (reusing foundry_contract.spec_normative_bytes — the SAME region that backs the
    signed spec_sha256) when the fences are absent or well-formed; the
    whole-body-minus-§Changelog fallback, computed independently of
    spec_normative_bytes' own (silently-partial) fenced branch, when the fences are
    malformed (AC-ACX-1)."""
    raw = open(spec_path, "rb").read()
    if _fences_balanced(raw):
        return fc.spec_normative_bytes(spec_path)
    return _whole_body_minus_changelog(raw)


def _spec_ac_ids(spec_path: str) -> list[str]:
    """AC-ACX-1/-2/-5: the spec-side AC-ID set a spec DEFINES — an AC-ID that is the
    SUBJECT of a definition line within the spec's normative region (fenced, or the
    whole-body-minus-§Changelog fallback when fences are absent/malformed) — suffix-aware,
    deterministic (first-definition order, de-duplicated). Does NOT include an AC-ID that
    appears only as a mention (prose, §Changelog, a cross-reference, or later in the same
    definition line — e.g. an inline `realizes AC-<PARENT>-<n>` citation); AC-ACX-3."""
    region = _extraction_region_bytes(spec_path)
    text = region.decode("utf-8", errors="replace")
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        m = _DEF_LINE_RE.match(line)
        if not m:
            continue
        ac = m.group(1)
        if ac not in seen:
            seen.add(ac)
            out.append(ac)
    return out


def _spec_mention_ac_ids(spec_path: str) -> list[str]:
    """Every AC-token (suffix-aware) that appears ANYWHERE in the spec file — a
    superset of `_spec_ac_ids`'s definition-scoped set. Powers ONLY the AC-ACX-6
    mentions-but-does-not-define diagnostic; never feeds the bijection."""
    text = open(spec_path, encoding="utf-8", errors="replace").read()
    seen: set[str] = set()
    out: list[str] = []
    for m in _MENTION_RE.findall(text):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def validate_spec_contract(spec_path: str, contract_path: str) -> tuple[bool, list[str], list[str]]:
    """AC-ACX-6 — the `foundry_authz` validation path: computes the definition-scoped
    spec-side AC set (`_spec_ac_ids`) that drives the byte-unchanged freeze-floor-3
    bijection (`foundry_contract.validate_contract_bytes`, AC-ACX-4) AND, additionally,
    the whole-file mention set (`_spec_mention_ac_ids`) — then enriches the returned
    `errors` with a diagnostic carrying the stable marker substring
    `mentions-but-does-not-define` for every checkpoint `ac_id` that matches a spec-side
    mention but no normative-region definition. Purely additive: the underlying floor-3
    verdict and its own error strings are untouched (`foundry_contract` stays
    byte-unchanged); this only makes the cause legible when it already fails."""
    with open(contract_path, "rb") as fh:
        raw = fh.read()
    spec_exists = os.path.exists(spec_path)
    spec_ac_ids = _spec_ac_ids(spec_path) if spec_exists else None
    ok, errors, warnings = fc.validate_contract_bytes(raw, spec_ac_ids)

    if not ok and spec_exists and spec_ac_ids is not None:
        try:
            data = fc.load_contract(contract_path)
        except fc.ContractError:
            data = None
        if isinstance(data, dict):
            cp_ac_union: set[str] = set()
            for cp in (data.get("checkpoints") or []):
                if not isinstance(cp, dict):
                    continue
                v = cp.get("ac_id")
                if isinstance(v, str):
                    cp_ac_union.add(v)
                elif isinstance(v, list):
                    cp_ac_union.update(x for x in v if isinstance(x, str))
            spec_set = set(spec_ac_ids)
            mention_set = set(_spec_mention_ac_ids(spec_path))
            mention_orphans = sorted((cp_ac_union - spec_set) & mention_set)
            for ac in mention_orphans:
                errors.append(
                    f"freeze floor 3 diagnostic: checkpoint ac_id {ac!r} "
                    f"mentions-but-does-not-define — {ac} appears in the spec but not as "
                    f"a normative-region definition line (only a textual mention); "
                    f"checkpoint an AC the spec actually DEFINES, or add the definition"
                )
    return ok, errors, warnings


# --------------------------------------------------------------------------- #
# Authorize (the freeze-write)
# --------------------------------------------------------------------------- #
def _yaml_trailer(block: dict) -> str:
    """Deterministic, hand-emitted YAML for the authorized block (fixed key order;
    no third-party emitter so the bytes are reproducible)."""
    order = ["operator_id", "authorized_at", "auth_seq", "supersedes",
             "spec_sha256", "contract_sha256", "merge_autonomy_mode", "reauth_after_impl"]
    lines = ["authorized:"]
    for k in order:
        if k not in block:
            continue
        v = block[k]
        if v is None:
            rv = "null"
        elif isinstance(v, bool):
            rv = "true" if v else "false"
        elif isinstance(v, int):
            rv = str(v)
        else:
            rv = str(v)
        lines.append(f"  {k}: {rv}")
    return "\n".join(lines) + "\n"


def authorize(spec_path: str, contract_path: str, operator_id: str,
              merge_autonomy_mode: str, authorized_at: str,
              reauth_after_impl: bool = False) -> dict:
    """Freeze the authorization onto acceptance-contract.yaml. Computes the
    monotonic auth_seq + supersedes from any prior block, writes the trailer below
    the sentinel. Returns the authorized block (for logging). The CALLER is
    responsible for operator confirmation + §22.5a logging (the skill)."""
    if merge_autonomy_mode not in ("regular", "lean"):
        raise AuthzError(f"merge_autonomy_mode must be regular|lean, got {merge_autonomy_mode!r}")

    with open(contract_path, "rb") as fh:
        raw = fh.read()

    # The hashes the operator signs (over the contract-proper region + spec normative).
    contract_hash = fc.contract_sha256_bytes(raw)
    spec_hash = fc.spec_sha256(spec_path) if os.path.exists(spec_path) else None

    prior = _authorized_block(contract_path)
    if prior is not None and isinstance(prior.get("auth_seq"), int):
        auth_seq = prior["auth_seq"] + 1
        supersedes = prior.get("contract_sha256")
    else:
        auth_seq = 1
        supersedes = None

    block = {
        "operator_id": operator_id,
        "authorized_at": authorized_at,
        "auth_seq": auth_seq,
        "supersedes": supersedes,
        "spec_sha256": spec_hash,
        "contract_sha256": contract_hash,
        "merge_autonomy_mode": merge_autonomy_mode,
    }
    if reauth_after_impl:
        block["reauth_after_impl"] = True

    frozen = fc.freeze_proper_and_trailer(raw, _yaml_trailer(block))

    # The signed hash must cover exactly the bytes that get written. Since both the hasher and the
    # writer canonicalize, `contract_sha256_bytes(frozen) == contract_hash` holds by construction —
    # asserting it would be a tautology that cannot fail, and, worse, would still pass if the WRITER
    # stopped canonicalizing (the hasher would re-canonicalize on the way back in and agree with
    # itself). It read as coverage while checking nothing that could break.
    #
    # Assert the byte-level property instead: the frozen file must literally BEGIN with the
    # canonical contract-proper followed by the sentinel. That convicts a writer-side regression —
    # the exact defect the old message named and could not detect.
    _expected_prefix = fc.canonicalize_proper(fc.split_contract_bytes(raw)[0]) + fc._SENTINEL_B
    if not frozen.startswith(_expected_prefix):
        raise AuthzError(
            "internal: the frozen bytes do not begin with the canonical contract-proper + sentinel, "
            "so the recorded contract_sha256 would not cover what was written "
            "(freeze_proper_and_trailer regression)"
        )

    tmp = contract_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(frozen)
    os.replace(tmp, contract_path)
    return block
