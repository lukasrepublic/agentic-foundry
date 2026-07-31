#!/usr/bin/env python3
"""foundry_reconcile — Atom F (#122, feat-foundry-merge-reconcile-fast-path) — the
PROVABLE-PURE-RENAME predicate over a frozen `system_grounding` block, a HEAD live
snapshot (Atom A, host-side-fed), and a `.foundry/reconciliation.yaml` manifest.

MECHANISM. A build MAY carry a `reconciliation:` manifest: a list of `{kind,
old_identifier, new_identifier}` rename entries + a `provenance` string. The predicate
(`reconcile_verdict`) holds IFF **all** of the frozen spec's 5 clauses hold:

  1. each old_identifier is present in the frozen block's declared identifiers AND
     absent from the head snapshot (it disappeared);
  2. each new_identifier is present in the head snapshot AND was absent from the
     frozen block (it appeared);
  3. the map is a BIJECTION — no old_identifier / new_identifier repeats, AND each
     entry's declared kind equals BOTH the old identifier's actual kind in the frozen
     block AND the new identifier's actual kind in the head snapshot;
  4. after substituting old->new, `foundry_contract.system_grounding_errors` against
     the head snapshot returns ZERO errors (reused, never reimplemented);
  5. the substitution operates over the frozen block's STRUCTURED, identifier-typed
     fields ONLY (never a raw string/regex replace over the serialized block) and
     changes ONLY those identifier fields — no AC-ID / classification / kind anywhere
     in the frozen block/contract changes.

Any failure / ambiguity / malformed manifest -> `allowed=False` (fail-closed, NEVER
raise-into-allow, AC-MRF-8). Pure / deterministic: same inputs -> same verdict. This
module imports ONLY `foundry_contract` (Atom C's `system_grounding_errors`, reused for
clause 4) — it opens no grounding source itself, performs no git/network I/O, and
never mutates real state. `foundry_merge_gate.py`'s reconcile check (Atom F's `evaluate`
addition) and the drop-in doctor check `scripts/foundry_checks/merge-reconcile-
fastpath.py` are the only consumers.
"""
from __future__ import annotations

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_contract as fc      # noqa: E402  (system_grounding_errors — imported, never edited)

_SG_KINDS = {"table", "column", "fk", "module", "queue", "event", "resource"}
_GROUNDED_KINDS = {"table", "column", "module"}


# --------------------------------------------------------------------------- #
# manifest schema validation (AC-MRF-1) — hand-rolled floor is the PRIMARY gate
# (never optional), an opportunistic jsonschema pass adds extra strictness when
# the dependency is present (mirrors foundry_contract's UL-0011 pattern).
# --------------------------------------------------------------------------- #
def _schema_path() -> str:
    return os.path.join(HERE, "..", "schema", "reconciliation.schema.json")


def _jsonschema_check(data) -> list[str]:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    try:
        with open(_schema_path()) as fh:
            schema = json.load(fh)
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:  # type: ignore
        return [f"schema: {e.message}"]
    except Exception as e:  # pragma: no cover
        return [f"schema: could not validate ({e})"]
    return []


def validate_manifest(manifest) -> tuple[bool, list[str]]:
    """AC-MRF-1: structural validation of a `.foundry/reconciliation.yaml`-shaped dict
    `{reconciliation: {renames: [{kind, old_identifier, new_identifier}], provenance: str}}`.
    Returns (ok, errors). A malformed manifest is ALWAYS rejected regardless of whether
    the optional jsonschema dependency is installed (never silently ignored, AC-MRF-1/-8)."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return False, ["manifest must be a mapping"]
    recon = manifest.get("reconciliation")
    if not isinstance(recon, dict):
        return False, ["manifest missing required 'reconciliation' object"]

    renames = recon.get("renames")
    if not isinstance(renames, list) or not renames:
        errors.append("reconciliation.renames must be a non-empty list")
        renames = []

    provenance = recon.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        errors.append("reconciliation.provenance must be a non-empty string")

    for i, r in enumerate(renames):
        if not isinstance(r, dict):
            errors.append(f"reconciliation.renames[{i}] must be a mapping")
            continue
        for key in ("kind", "old_identifier", "new_identifier"):
            if key not in r:
                errors.append(f"reconciliation.renames[{i}] missing required key {key!r}")
        kind = r.get("kind")
        if "kind" in r and kind not in _SG_KINDS:
            errors.append(f"reconciliation.renames[{i}] unknown kind {kind!r}")
        for key in ("old_identifier", "new_identifier"):
            if key in r and (not isinstance(r.get(key), str) or not r.get(key, "").strip()):
                errors.append(f"reconciliation.renames[{i}].{key} must be a non-empty string")

    errors += _jsonschema_check(manifest)
    return (len(errors) == 0), errors


# --------------------------------------------------------------------------- #
# identifier-match helpers (mirror foundry_contract's identifier-match predicate,
# kept LOCAL/independent — this module's sole reuse obligation is clause 4's
# `system_grounding_errors`, never the private structural helpers).
# --------------------------------------------------------------------------- #
def _identifier_present(kind: str, identifier: str, snapshot: dict) -> bool:
    """True iff `identifier` (of the given `kind`) is live in `snapshot`. Ungrounded
    kinds (fk/queue/event/resource) have no snapshot dimension and are never
    'present' — a rename declared over an ungrounded kind never satisfies clause 1/2."""
    if not isinstance(identifier, str) or not identifier or not isinstance(snapshot, dict):
        return False
    entities = snapshot.get("entities") or {}
    modules = snapshot.get("modules") or []
    if kind == "table":
        return identifier in entities
    if kind == "module":
        return identifier in modules
    if kind == "column":
        if identifier.count("::") != 1:
            return False
        entity, col = identifier.split("::", 1)
        return entity in entities and col in (entities.get(entity, {}).get("columns") or [])
    return False


def _kind_in_snapshot(identifier: str, snapshot: dict) -> "str | None":
    """Infers which grounded kind (if any) `identifier` matches in `snapshot`. A
    column-shaped identifier (`entity::col`) is checked before a bare table/module
    match (structurally distinct shapes never collide). None when unmatched."""
    if not isinstance(identifier, str) or not identifier or not isinstance(snapshot, dict):
        return None
    entities = snapshot.get("entities") or {}
    modules = snapshot.get("modules") or []
    if identifier.count("::") == 1:
        entity, col = identifier.split("::", 1)
        if entity in entities and col in (entities.get(entity, {}).get("columns") or []):
            return "column"
    if identifier in entities:
        return "table"
    if identifier in modules:
        return "module"
    return None


def _frozen_artifacts(frozen_contract_data) -> "list | None":
    sg = (frozen_contract_data or {}).get("system_grounding")
    if not isinstance(sg, dict):
        return None
    artifacts = sg.get("artifacts")
    return artifacts if isinstance(artifacts, list) else None


def _frozen_kinds_of(identifier: str, frozen_artifacts: list) -> set:
    """The set of DISTINCT kinds under which `identifier` is declared in the frozen
    block. Ordinarily a singleton (the structural floor forbids a duplicate (kind,
    identifier) pair, but NOT a duplicate identifier under two different kinds) —
    a non-singleton result is ambiguous and MUST fail closed (clause 1)."""
    kinds = set()
    for art in frozen_artifacts or []:
        if isinstance(art, dict) and art.get("identifier") == identifier:
            kinds.add(art.get("kind"))
    return kinds


# --------------------------------------------------------------------------- #
# clause 5 — structured-field-only substitution + the structural-diff guard
# --------------------------------------------------------------------------- #
def _substitute(frozen_contract_data: dict, renames: list) -> dict:
    """Returns a DEEP-COPIED contract dict with ONLY each `system_grounding.
    artifacts[i].identifier` value substituted (old->new, per `renames`) — never a
    string/regex replace over the serialized block (clause 5a). No other key of any
    artifact, and no field outside `system_grounding.artifacts[*].identifier`
    anywhere in the contract, is ever touched by this function."""
    after = copy.deepcopy(frozen_contract_data)
    rename_map = {r["old_identifier"]: r["new_identifier"] for r in renames
                  if isinstance(r, dict) and "old_identifier" in r and "new_identifier" in r}
    artifacts = _frozen_artifacts(after) or []
    for art in artifacts:
        if not isinstance(art, dict):
            continue
        ident = art.get("identifier")
        if isinstance(ident, str) and ident in rename_map:
            art["identifier"] = rename_map[ident]
    return after


def structural_diff_beyond_identifiers(before: dict, after: dict) -> bool:
    """The clause-5 GUARD (AC-MRF-9(k)): True iff `before`/`after` differ ANYWHERE
    other than a `system_grounding.artifacts[i].identifier` value — i.e. an AC-ID,
    `classification`, `kind`, `checkpoints`, `scope`, or any other field changed.
    `reconcile_verdict` checks this UNCONDITIONALLY before ever returning
    `allowed=True`, so a semantic change can never be laundered as a rename (a
    'smuggle') even if some future caller/implementation path stopped using
    `_substitute`'s structured-only replacement. Given `_substitute` itself only ever
    touches an `identifier` field, this returns False for any output of `_substitute`
    by construction — the guard is genuine defense-in-depth, not dead code: it fires
    whenever `before`/`after` disagree beyond the identifier-only shape, however that
    disagreement arose."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return before != after
    if set(before.keys()) != set(after.keys()):
        return True
    for k in before.keys():
        if k == "system_grounding":
            continue
        if before[k] != after[k]:
            return True

    bsg = before.get("system_grounding") or {}
    asg = after.get("system_grounding") or {}
    if not isinstance(bsg, dict) or not isinstance(asg, dict):
        return bsg != asg
    if set(bsg.keys()) != set(asg.keys()):
        return True
    for k in bsg.keys():
        if k == "artifacts":
            continue
        if bsg[k] != asg[k]:
            return True

    barts = bsg.get("artifacts") or []
    aarts = asg.get("artifacts") or []
    if len(barts) != len(aarts):
        return True
    for ba, aa in zip(barts, aarts):
        if not isinstance(ba, dict) or not isinstance(aa, dict):
            if ba != aa:
                return True
            continue
        if set(ba.keys()) != set(aa.keys()):
            return True
        for k in ba.keys():
            if k == "identifier":
                continue
            if ba[k] != aa[k]:
                return True
    return False


# --------------------------------------------------------------------------- #
# the predicate
# --------------------------------------------------------------------------- #
def _blocked(reason: str) -> dict:
    return {"allowed": False, "reason": reason, "mappings": [], "provenance": None}


def reconcile_verdict(frozen_contract_data: dict, head_snapshot: "dict | None", manifest: dict) -> dict:
    """AC-MRF-3/-4/-8: `{allowed, reason, mappings, provenance}`. `allowed=True` ONLY
    when every one of the 5 predicate clauses holds (module docstring). Any failure,
    ambiguity, or exception fails closed to `allowed=False` — this function NEVER
    raises (a caught internal error is folded into `reason`, never propagated into an
    ALLOW)."""
    try:
        ok, errs = validate_manifest(manifest)
        if not ok:
            return _blocked("malformed reconciliation manifest (AC-MRF-1/-8): " + "; ".join(errs))

        renames = manifest["reconciliation"]["renames"]
        provenance = manifest["reconciliation"].get("provenance") or ""

        if not isinstance(head_snapshot, dict):
            return _blocked("no head snapshot available to prove the rename-map against (AC-MRF-8)")

        frozen_artifacts = _frozen_artifacts(frozen_contract_data)
        if frozen_artifacts is None:
            return _blocked("frozen contract carries no system_grounding.artifacts to reconcile")

        # clause 3a — bijection: no repeats among old_identifier / new_identifier, and no
        # identifier appears as both an old and a new (a self-referential 'loop').
        olds = [r["old_identifier"] for r in renames]
        news = [r["new_identifier"] for r in renames]
        if len(olds) != len(set(olds)) or len(news) != len(set(news)):
            return _blocked("rename-map is not a bijection: a repeated old_identifier or "
                            "new_identifier across entries (clause 3)")
        if set(olds) & set(news):
            return _blocked("rename-map is not a bijection: an identifier appears as both an "
                            "old_identifier and a new_identifier (clause 3)")

        mappings = []
        for r in renames:
            kind, old_id, new_id = r["kind"], r["old_identifier"], r["new_identifier"]

            # clause 1 — old present in the frozen block (unambiguously), absent from head.
            frozen_kinds = _frozen_kinds_of(old_id, frozen_artifacts)
            if len(frozen_kinds) != 1:
                return _blocked(f"old_identifier {old_id!r} is not uniquely declared in the "
                                f"frozen system_grounding block ({len(frozen_kinds)} distinct "
                                f"kind(s)) — ambiguous (clause 1)")
            frozen_kind = next(iter(frozen_kinds))
            if _identifier_present(frozen_kind, old_id, head_snapshot):
                return _blocked(f"old_identifier {old_id!r} is still present in the head "
                                f"snapshot — it did not disappear (clause 1)")

            # clause 2 — new present in head, absent from the frozen block.
            head_kind = _kind_in_snapshot(new_id, head_snapshot)
            if head_kind is None:
                return _blocked(f"new_identifier {new_id!r} is absent from the head snapshot — "
                                f"it did not appear (clause 2)")
            if _frozen_kinds_of(new_id, frozen_artifacts):
                return _blocked(f"new_identifier {new_id!r} is already declared in the frozen "
                                f"system_grounding block — not new (clause 2)")

            # clause 3b — kind agreement: declared == frozen(old) == head(new).
            if not (kind == frozen_kind == head_kind):
                return _blocked(f"kind mismatch for {old_id!r}->{new_id!r}: manifest declared "
                                f"{kind!r}, frozen block has {frozen_kind!r}, head snapshot has "
                                f"{head_kind!r} (clause 3)")

            mappings.append({"kind": kind, "old_identifier": old_id, "new_identifier": new_id})

        # clause 5 — structured-field-only substitution; the guard MUST hold before any ALLOW.
        substituted = _substitute(frozen_contract_data, renames)
        if structural_diff_beyond_identifiers(frozen_contract_data, substituted):
            return _blocked("substitution altered a field other than a system_grounding "
                            "artifact identifier (AC-ID / classification / kind / other) — "
                            "semantic change laundered as a rename, refused (clause 5)")

        # clause 4 — zero residual system_grounding_errors against head (reused, never
        # reimplemented).
        residual = fc.system_grounding_errors(substituted, head_snapshot)
        if residual:
            return _blocked("residual system_grounding divergence after substitution — not "
                            "fully explained by the rename-map (clause 4): " + "; ".join(residual[:3]))

        return {
            "allowed": True,
            "reason": "provable pure rename-map — system_grounding divergence reconciled "
                      "without re-authorization",
            "mappings": mappings,
            "provenance": provenance,
        }
    except Exception as e:  # noqa: BLE001 — fail-closed, never raise-into-allow (AC-MRF-8).
        return _blocked(f"reconcile predicate raised unexpectedly (fail-closed): {e}")
