#!/usr/bin/env python3
"""foundry_intake_grounding — the AUTHORING-time schema-aware machine-check seam (Atom D, #124,
feat-foundry-schema-aware-authoring-grounding, AC-SAG-3/-4/-9).

MECHANISM. Atom A (`foundry_system_snapshot.build_system_snapshot`) recovers the live
persisted-schema + module surface; Atom C (`foundry_contract.system_grounding_errors`) freezes a
declared `system_grounding` block against that snapshot at `/foundry:authorize`. This atom moves
the SAME reconciliation one phase earlier — to `/foundry:intake` authoring — so a declared
`net-new` artifact that is already live is a defect the AUTHOR sees before hand-off to
`/foundry:audit`, not weeks later at freeze. It is a *prevent* aid, NOT a new enforcement floor:
the load-bearing gates remain #120 (audit) and #121 (authorize freeze).

REUSE, NOT REIMPLEMENTATION (AC-SAG-3/AC-SAG-4). This module constructs the
`{"system_grounding": {"artifacts": [...]}}`-shaped input Atom C's schema expects and delegates
to `foundry_contract.resolve_grounding_snapshot` + `foundry_contract.system_grounding_errors` —
the intake *prevent* verdict and the authorize *freeze* verdict are, by construction, the SAME
function of the SAME snapshot. `foundry_contract.py` is imported, never edited (denied_paths).

Import discipline (load-bearing for the live-seam, AC-SAG-9(i)): this module does a plain
`import foundry_contract` (module-level, not `from foundry_contract import X`) and every call
site goes through the `foundry_contract.<name>(...)` attribute lookup. That is what makes the
resolution path monkeypatchable: the live-seam check patches
`foundry_contract.resolve_grounding_snapshot` on the very module object this file references
(Python caches modules by name in `sys.modules`, so the patch is visible here immediately) to
prove the seam is actually driven through the real resolution path, not a bypassed/hardcoded one.

FAIL-CLOSED (AC-SAG-4(b)). `foundry_contract.GroundingSourceError` (a present-but-broken
grounding source) is never caught here — it PROPAGATES to the caller. No other exception is
caught either: there is no blanket `except: return []` anywhere in this module. A malformed
declared artifact (unrecognized `kind`/`classification`, or a missing/empty `identifier`) is
surfaced as an `intake-defect:` entry rather than silently dropped (the malformed-input floor;
`system_grounding_errors` itself `continue`s past a malformed artifact under the assumption the
UNCONDITIONAL structural floor already rejected it at authorize-time — intake has no such floor,
so this module checks shape itself, reusing Atom C's own taxonomy sets).

UNCONFIGURED / EMPTY NO-OP (AC-SAG-7). An empty `declared_artifacts` list still constructs an
EMPTY-BUT-PRESENT `{"artifacts": []}` block (never an omitted `system_grounding` key), so it never
trips Atom C's "grounding is configured but no block is declared" (AC-SGC-5) error — that error is
authorize-only machinery for a FROZEN contract; intake is pre-declaration, so "nothing declared
yet" must stay a clean no-op, not a spurious defect.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import foundry_contract  # noqa: E402  — module-level import; see the import-discipline note above.

# Re-exported for callers that want to catch the fail-closed oracle error without importing
# foundry_contract directly (mirrors foundry_contract's own re-export of GroundingSourceError).
GroundingSourceError = foundry_contract.GroundingSourceError


def _artifact_malformed_reason(art) -> "str | None":
    """None when `art` is a well-formed declared-artifact shape; else a short human reason.
    Reuses Atom C's taxonomy sets (`_SG_KINDS`/`_SG_CLASSIFICATIONS`) — never re-declares them."""
    if not isinstance(art, dict):
        return f"artifact is not a mapping, got {type(art).__name__}"
    kind = art.get("kind")
    classification = art.get("classification")
    identifier = art.get("identifier")
    if kind not in foundry_contract._SG_KINDS:
        return f"unknown kind {kind!r} (not in {sorted(foundry_contract._SG_KINDS)})"
    if classification not in foundry_contract._SG_CLASSIFICATIONS:
        return (f"unknown classification {classification!r} "
                f"(not in {sorted(foundry_contract._SG_CLASSIFICATIONS)})")
    if not isinstance(identifier, str) or not identifier.strip():
        return f"identifier missing/empty (got {identifier!r})"
    return None


def intake_schema_defects(declared_artifacts, project_dir=None) -> list:
    """AC-SAG-3/-4/-9: the machine-check seam. `declared_artifacts` is a list of
    `{kind, classification, identifier}` dicts (the same shape Atom C's `system_grounding.artifacts`
    freezes). `project_dir` is the resolved code-repo root the atom builds against (the same root
    Atom C's authorize freeze floor resolves the snapshot at).

    Returns a list of `intake-defect: ...` strings — empty when there is nothing to resolve.
    PROPAGATES `foundry_contract.GroundingSourceError` on a present-but-broken oracle (AC-SAG-4(b))
    — never swallowed. Does NOT catch any other exception either (no fail-open path).
    """
    declared_artifacts = list(declared_artifacts or [])

    # Malformed-input floor (preamble / AC-SAG-9(j)): surfaced BEFORE resolving the snapshot, so a
    # malformed declaration is caught even if the resolution below happens to raise afterward.
    defects: list = []
    for i, art in enumerate(declared_artifacts):
        reason = _artifact_malformed_reason(art)
        if reason:
            defects.append(f"intake-defect: artifacts[{i}] malformed declared artifact — {reason} "
                           "(malformed-input floor — surfaced, not silently dropped)")

    # AC-SAG-3: resolve the snapshot via the REAL resolution path — never build it inline. Left
    # unguarded on purpose: a GroundingSourceError (AC-SAG-4(b)) propagates to the caller.
    snapshot = foundry_contract.resolve_grounding_snapshot(project_dir)

    # AC-SAG-3/-4: construct the equivalent system_grounding-shaped input and delegate to Atom C's
    # single source of truth. An empty declared_artifacts list still yields an EMPTY-BUT-PRESENT
    # `artifacts: []` block (AC-SAG-7) — never an omitted `system_grounding` key.
    contract_data = {"system_grounding": {"artifacts": declared_artifacts}}
    sg_errors = foundry_contract.system_grounding_errors(contract_data, snapshot)
    defects.extend(f"intake-defect: {e}" for e in sg_errors)

    return defects


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────


def _main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="foundry_intake_grounding",
        description="Machine-check declared data-model artifacts against Atom A's live system "
                    "snapshot at authoring time (Atom D, AC-SAG-3/-4).",
    )
    parser.add_argument("--project-dir", default=None,
                        help="resolved code-repo root to check against (default: workspace root "
                             "via CLAUDE_PROJECT_DIR/cwd, per build_system_snapshot's own default)")
    parser.add_argument("--json", dest="json_str", default=None,
                        help="declared artifacts as a JSON list, inline")
    parser.add_argument("--file", dest="json_file", default=None,
                        help="path to a JSON file containing the declared artifacts list "
                             "(default: read from stdin)")
    args = parser.parse_args(argv)

    if args.json_str is not None:
        declared = json.loads(args.json_str)
    elif args.json_file:
        with open(args.json_file, encoding="utf-8") as f:
            declared = json.load(f)
    else:
        declared = json.load(sys.stdin)

    defects = intake_schema_defects(declared, project_dir=args.project_dir)
    if defects:
        for d in defects:
            print(d)
        print(f"intake-schema-grounding: {len(defects)} defect(s) — resolve before hand-off to "
              "/foundry:audit")
        return 1
    print("intake-schema-grounding: no defects")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
