#!/usr/bin/env python3
"""foundry_grounding_conformance.py — Atom G of the reality-grounding gate (feat-foundry-
grounding-conformance-backfill, AC-GCB-1..7).

The **retroactive coverage / backfill** closure. Atoms authorized **before** the reality-grounding
gate existed (Atom C, #121) carry **no** `system_grounding` block at all — the gate is enforced going
forward only. This module classifies **every AUTHORIZED (frozen) contract** in the corpus against the
**current** Atom A snapshot (`foundry_system_snapshot.build_system_snapshot`, produced HOST-SIDE) as
**GROUNDED** / **UNGROUNDED** / **STALE**, and emits a deterministic **backfill worklist** naming every
non-conformant atom + a conformance **summary**. It is the **coverage** analogue of the drift sweep
(Atom E, #123): #123 answers "did a schema change just invalidate frozen specs?" (a *delta*), this
answers "which frozen specs are not yet reality-grounded at all?" (a *coverage* snapshot).

ADVISORY, the `coherence-check` model — tri-state fail-closed, fresh-walk-every-run, NEVER a merge
gate: `foundry-merge-gate.py` never imports or runs this module.

Reuses (imports, never edits):
  - `foundry_contract` (Atom C, #121) — `_system_grounding_structural_errors` (the STRUCTURAL,
    byte-shape validator) AND `system_grounding_errors` (the CONSISTENCY validator vs. the live
    snapshot). BOTH are run, structural FIRST — a structurally malformed block is silently skipped by
    the consistency check alone (it assumes the structural floor already ran) and would otherwise be
    falsely GROUNDED (the audited AC-GCB-1 fail-safe).
  - `foundry_authz` — the `authorized:` trailer detection. This sweep's scope is every contract that
    was ever **frozen** (carries an `authorized:` block, i.e. `_authorized_block(...) is not None`),
    NOT the narrower `is_authorized()` lifecycle predicate. `is_authorized()` re-runs the CURRENT
    `validate_contract_bytes` floors (which now unconditionally include the very structural check this
    sweep exists to surface) — an atom whose block has drifted structurally invalid under the CURRENT
    validator resolves to `RE_BASELINE_NEEDED`, not `AUTHORIZED`, under that narrower predicate, which
    would make it invisible to a sweep gated on `is_authorized()` alone (the exact STALE case this atom
    exists to surface would then never reach classification). Gating on "was ever frozen" instead keeps
    every atom that has been through `/foundry:authorize` in scope, including ones whose frozen block
    has since gone stale against the CURRENT structural/consistency floors or snapshot — precisely the
    backfill population this sweep reports on. A wholly unauthorized DRAFT/CONTRACT_FROZEN contract
    (no `authorized:` block at all) is excluded either way (AC-GCB-5).

Host-side snapshot-in (`classify_corpus(corpus_root, snapshot)`): this module opens no grounding
source itself and does no git plumbing — the caller (the CLI below, or the drop-in doctor check)
supplies the already-built Atom A snapshot, preserving the gate's host-side-injection invariant.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_authz as authz     # noqa: E402  (authorized-trailer detection — imported, never edited)
import foundry_contract as fc     # noqa: E402  (Atom C block shape + both validators — imported, never edited)

CONTRACT_FILENAME = "acceptance-contract.yaml"

GROUNDED = "GROUNDED"
UNGROUNDED = "UNGROUNDED"
STALE = "STALE"


class GroundingConformanceError(Exception):
    """Raised for an OPERATIONAL failure encountered mid-walk: a malformed/unreadable snapshot input,
    or an unreadable/corrupt corpus contract hit while scanning. The caller maps this to the tri-state
    exit `2` (AC-GCB-3) — a scan error is a HARD stop, NEVER silently swallowed into a partial 0/1
    worklist."""


# ── snapshot validation (AC-GCB-3 malformed-input branch) ─────────────────────────────────────


def _validate_snapshot(snapshot) -> None:
    """Raises `GroundingConformanceError` iff `snapshot` is not a well-formed Atom A snapshot dict
    (the minimal shape this engine depends on: `entities`, `modules`, `grounding_configured`)."""
    if not isinstance(snapshot, dict):
        raise GroundingConformanceError(f"snapshot is not a dict (got {type(snapshot).__name__})")
    for key in ("entities", "modules", "grounding_configured"):
        if key not in snapshot:
            raise GroundingConformanceError(f"snapshot missing required key {key!r}")
    entities = snapshot["entities"]
    if not isinstance(entities, dict):
        raise GroundingConformanceError(f"snapshot 'entities' must be a dict, got {type(entities).__name__}")
    for ent, val in entities.items():
        if not isinstance(val, dict) or not isinstance(val.get("columns"), list):
            raise GroundingConformanceError(f"snapshot entity {ent!r} lacks a usable 'columns' list")
    if not isinstance(snapshot["modules"], list):
        raise GroundingConformanceError(f"snapshot 'modules' must be a list, got "
                                        f"{type(snapshot['modules']).__name__}")
    if not isinstance(snapshot["grounding_configured"], bool):
        raise GroundingConformanceError("snapshot 'grounding_configured' must be a bool")


# ── AC-GCB-1: per-atom classification (the normative rule) ────────────────────────────────────


def _classify_atom(contract_data: dict, snapshot: dict) -> "tuple[str, list[str]]":
    """Returns `(classification, errors)` for ONE frozen atom, per the AC-GCB-1 normative rule:

      - unconfigured snapshot (AC-GCB-4): GROUNDED unconditionally — nothing to be non-conformant
        against, regardless of the block's own shape.
      - no `system_grounding` block at all: UNGROUNDED (pre-gate atom), errors == [].
      - a PRESENT block: run the STRUCTURAL validator FIRST (`_system_grounding_structural_errors`) —
        >=1 structural error => STALE, errors == the structural message(s). This precedence is
        load-bearing: `system_grounding_errors` (consistency) silently returns [] for a non-dict /
        malformed block (it assumes the structural floor already ran), so skipping the structural
        check would falsely resolve a malformed block to GROUNDED.
      - a structurally well-formed block: run the CONSISTENCY validator (`system_grounding_errors`) —
        >=1 consistency error => STALE, errors == the consistency message(s); zero errors (including a
        well-formed empty `artifacts: []` block — nothing to ground) => GROUNDED.
    """
    if not snapshot.get("grounding_configured"):
        return GROUNDED, []

    sg = (contract_data or {}).get("system_grounding")
    if sg is None:
        return UNGROUNDED, []

    structural_errors = fc._system_grounding_structural_errors(contract_data)
    if structural_errors:
        return STALE, list(structural_errors)

    consistency_errors = fc.system_grounding_errors(contract_data, snapshot)
    if consistency_errors:
        return STALE, list(consistency_errors)

    return GROUNDED, []


# ── corpus walk ─────────────────────────────────────────────────────────────────────────────────


def _walk_contract_paths(corpus_root: str) -> list:
    """Every `acceptance-contract.yaml` under `corpus_root`, sorted deterministically."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(corpus_root):
        if CONTRACT_FILENAME in filenames:
            found.append(os.path.join(dirpath, CONTRACT_FILENAME))
    return sorted(found)


def _scan_one(contract_path: str, snapshot: dict):
    """Returns `(spec_ref, classification, errors)` for ONE contract, or `None` when it is out of
    scope (no `spec_ref`, or never frozen — DRAFT/CONTRACT_FROZEN, AC-GCB-5). Raises
    `GroundingConformanceError` on any unreadable/corrupt contract encountered — a scan error is a
    HARD stop (AC-GCB-3), never a silent skip."""
    try:
        contract_data = fc.load_contract(contract_path)
    except Exception as e:  # noqa: BLE001 — deliberately broad: ANY read/parse failure is fail-closed
        raise GroundingConformanceError(f"unreadable/corrupt contract at {contract_path}: {e}") from e

    spec_ref = contract_data.get("spec_ref")
    if not isinstance(spec_ref, str) or not spec_ref.strip():
        return None  # not a well-formed contract to attribute to an atom — not this sweep's concern

    try:
        blk = authz._authorized_block(contract_path)
    except Exception as e:  # noqa: BLE001
        raise GroundingConformanceError(f"authorization-record check raised for {contract_path}: {e}") from e
    if blk is None:
        return None  # DRAFT / CONTRACT_FROZEN — never frozen, excluded (AC-GCB-5)

    classification, errors = _classify_atom(contract_data, snapshot)
    return spec_ref, classification, errors


# ── report shaping ─────────────────────────────────────────────────────────────────────────────


def _error_report(abs_root: str, message: str) -> dict:
    return {
        "provenance": {"corpus_root": abs_root, "source": "error", "error": message},
        "worklist": [],
        "summary": {"grounded": 0, "ungrounded": 0, "stale": 0, "total": 0},
    }


def _clean_report(abs_root: str, worklist: list, summary: dict) -> dict:
    return {
        "provenance": {"corpus_root": abs_root, "source": "fresh-walk", "error": None},
        "worklist": worklist,
        "summary": summary,
    }


# ── the engine entrypoint ──────────────────────────────────────────────────────────────────────


def classify_corpus(corpus_root: str, snapshot: dict) -> "tuple[dict, int]":
    """AC-GCB-1..6. Returns `(report, exit_code)`:

      0 — ran, ZERO non-conformant atoms.
      1 — ran, >=1 non-conformant atom (an ADVISORY worklist — never a veto).
      2 — OPERATIONAL failure: a malformed/unreadable snapshot input, a missing/mis-resolved
          `corpus_root`, or the corpus walk raising (including an unreadable/corrupt frozen contract
          encountered mid-walk) — a HARD stop, NEVER a silent skip to 0/1 (AC-GCB-3).

    `report` = `{"provenance": {...}, "worklist": [...], "summary": {...}}`. `worklist` entries are
    the pinned field set `{spec_ref, classification, errors}`, sorted ascending by `spec_ref`
    (AC-GCB-2). `summary` = `{grounded, ungrounded, stale, total}`.

    Host-side snapshot-in: this function opens no grounding source and does no git plumbing — the
    caller supplies the already-built Atom A snapshot (AC-GCB-4: `grounding_configured` false =>
    every atom GROUNDED, empty worklist, exit 0).
    """
    abs_root = os.path.abspath(corpus_root) if corpus_root else os.getcwd()

    try:
        _validate_snapshot(snapshot)
    except GroundingConformanceError as e:
        return _error_report(abs_root, str(e)), 2

    if not os.path.isdir(abs_root):
        return _error_report(abs_root, f"corpus root is not a directory: {abs_root}"), 2

    grounded = ungrounded = stale = 0
    worklist = []
    try:
        for contract_path in _walk_contract_paths(abs_root):
            result = _scan_one(contract_path, snapshot)
            if result is None:
                continue
            spec_ref, classification, errors = result
            if classification == GROUNDED:
                grounded += 1
            elif classification == UNGROUNDED:
                ungrounded += 1
                worklist.append({"spec_ref": spec_ref, "classification": classification, "errors": errors})
            else:  # STALE
                stale += 1
                worklist.append({"spec_ref": spec_ref, "classification": classification, "errors": errors})
    except GroundingConformanceError as e:
        return _error_report(abs_root, str(e)), 2
    except Exception as e:  # noqa: BLE001 — ANY other raise mid-walk is ALSO a hard stop (AC-GCB-3)
        return _error_report(abs_root, f"corpus walk raised: {type(e).__name__}: {e}"), 2

    worklist.sort(key=lambda e: e["spec_ref"])
    total = grounded + ungrounded + stale
    summary = {"grounded": grounded, "ungrounded": ungrounded, "stale": stale, "total": total}
    report = _clean_report(abs_root, worklist, summary)
    return report, (1 if worklist else 0)


# ── CLI (thin, mirrors foundry_drift_sweep.py's) ───────────────────────────────────────────────


def _resolve_root() -> str:
    pd = os.environ.get("CLAUDE_PROJECT_DIR")
    if pd and not pd.startswith("${"):
        return pd
    return os.getcwd()


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="ADVISORY grounding-conformance backfill sweep (the retroactive coverage "
                    "closure of the reality-grounding gate). Never a merge gate.")
    ap.add_argument("--corpus-root", default=None,
                    help="workspace corpus root to walk for acceptance-contract.yaml "
                         "(default: $CLAUDE_PROJECT_DIR or cwd)")
    ap.add_argument("--snapshot", default=None,
                    help="path to a JSON-serialized Atom A snapshot (default: build a fresh one "
                         "host-side via build_system_snapshot against --corpus-root)")
    args = ap.parse_args(argv)

    root = args.corpus_root or _resolve_root()
    abs_root = os.path.abspath(root)

    if args.snapshot:
        try:
            with open(args.snapshot, encoding="utf-8") as f:
                snapshot = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(json.dumps(_error_report(abs_root, f"snapshot input unreadable: {e}"), sort_keys=True))
            return 2
    else:
        from foundry_system_snapshot import build_system_snapshot, GroundingSourceError
        try:
            snapshot = build_system_snapshot(project_dir=root)
        except GroundingSourceError as e:
            print(json.dumps(_error_report(abs_root, f"GroundingSourceError: {e}"), sort_keys=True))
            return 2

    report, code = classify_corpus(root, snapshot)
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
