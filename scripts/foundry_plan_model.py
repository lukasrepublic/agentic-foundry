#!/usr/bin/env python3
"""foundry_plan_model — the canonical IaC-change plane parsers (parse-only; v2, three plane-parsers).

The change-delivery verdict (feat-foundry-infra-change-verdict) needs PER-RESOURCE structured detail for
every plane it adjudicates. This is the SINGLE decoupled module (the anti-corruption layer HashiCorp's own
`terraform-json` embodies) holding the canonical parse of each plane's stable structured contract; every
consumer depends on it and NONE re-parses. The three SIBLING parsers:

  1. parse_actions_detail(plan_json) -> [{address, action}]                         (AC-IPM-1/2, v1, tofu plane)
       The structured `resource_changes` from `tofu show -json <planfile>` -> the canonical action enum.
  2. parse_gitops_changes(before_obj, after_obj) -> [{kind, namespace, name, action, changed_attrs}]
                                                                                    (AC-IPM-4, v2, GitOps plane)
       A PURE recursive structured dict-diff of two ALREADY-PARSED manifest objects (merge-base baseline vs
       candidate). The caller owns the YAML parse + any Helm/Kustomize render; this fn diffs the dicts.
  3. parse_policy_findings(conftest_json) -> [{rule, resource, severity, gating}]   (AC-IPM-5, v2, policy plane)
       A PURE parse of the `conftest test -o json` envelope into the canonical risk-finding shape, carrying
       the `gating ∈ {deny, warn, exception}` array-of-origin discriminator (hard-FAIL vs ackable).

SCOPE (v2, parse-only) — PARSE-ONLY. `match_blast` / blast-tiering stays DROPPED (v2.7 dissolved the native
cross-plane blast engine into policy-as-code; the policy plane owns change-risk). MATCHING / ADJUDICATION /
GATING live in the verdict + gitops-surfaces + ack consumers — NOT here. `parse_actions_detail` is UNCHANGED
for back-compat (no `attr[]` added).

Threat model — TRUSTED OPERATOR; floor-adjacent (the verdict consumes all three parsers' output). Three
PURE, DETERMINISTIC parses over stable structured contracts: no I/O, no network, no cluster call, no render,
no matching/heuristics. The two new parsers are TOTAL + FAIL-CLOSED exactly like `parse_actions_detail` — a
malformed envelope RAISES, never a silent empty parse that greens the gate. Mistakes are caught by the
drop-in selftest (scripts/foundry_checks/infra-plan-model.py).
"""


class PlanModelError(ValueError):
    """Raised on a malformed/unexpected plan shape — the parse is TOTAL and FAIL-CLOSED.

    A subclass of ValueError so a caller that only catches ValueError still fail-closes, while a caller
    that wants the precise type can catch PlanModelError.
    """


# The closed canonical enum. The `change.actions` arrays are mapped EXACTLY; anything else RAISES (an
# unrecognized/empty array is never silently dropped — it might be a mutation). `no-op`/`read` map to the
# sentinel _OMIT (the resource is intentionally excluded from actions_detail, no mutation).
_OMIT = object()

_ACTION_MAP = {
    ("create",): "create",
    ("update",): "update",
    ("delete",): "delete",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
    ("no-op",): _OMIT,
    ("read",): _OMIT,
}


def parse_actions_detail(plan_json):
    """Parse a stable `tofu plan -json` document (already parsed into Python) into the canonical
    `actions_detail`: a list of {"address": <str>, "action": <canonical>} in `resource_changes` order.

    Mapping (closed canonical enum):
      ["create"]                         -> "create"
      ["update"]                         -> "update"
      ["delete"]                         -> "delete"
      ["delete","create"] / ["create","delete"] -> "replace"
      ["no-op"] / ["read"]               -> OMITTED (no mutation; not emitted)

    TOTAL and FAIL-CLOSED at TWO levels:
      (i)  the plan ENVELOPE — a missing / None / non-list `resource_changes` (a malformed/unexpected
           plan shape) RAISES PlanModelError. Never a silent empty parse (a missing envelope must NOT
           pass as "no changes").
      (ii) each `change.actions` — an unrecognized / empty action array RAISES PlanModelError. Never a
           silent drop of a resource that might be a mutation.

    PURE: consumes already-parsed JSON; runs no command, performs no I/O.
    DETERMINISTIC: emits in the stable `resource_changes` array order; byte-identical on repeat.
    """
    if not isinstance(plan_json, dict):
        raise PlanModelError(
            f"plan envelope is not an object (got {type(plan_json).__name__}); expected a parsed "
            "`tofu plan -json` document"
        )

    # (i) ENVELOPE total/fail-closed: `resource_changes` must be present AND a list. Missing / None /
    # non-list RAISES — never a silent empty parse that would pass a malformed plan as "no changes".
    resource_changes = plan_json.get("resource_changes")
    if not isinstance(resource_changes, list):
        raise PlanModelError(
            "plan envelope missing a list `resource_changes` (got "
            f"{type(resource_changes).__name__}); a malformed/unexpected plan shape must not pass as "
            "'no changes'"
        )

    actions_detail = []
    for idx, rc in enumerate(resource_changes):
        if not isinstance(rc, dict):
            raise PlanModelError(
                f"resource_changes[{idx}] is not an object (got {type(rc).__name__})"
            )
        address = rc.get("address")
        if not isinstance(address, str) or not address:
            raise PlanModelError(
                f"resource_changes[{idx}] missing a non-empty string `address`"
            )
        change = rc.get("change")
        if not isinstance(change, dict):
            raise PlanModelError(
                f"resource_changes[{idx}] ({address}) missing an object `change`"
            )
        actions = change.get("actions")
        if not isinstance(actions, list):
            raise PlanModelError(
                f"resource_changes[{idx}] ({address}) `change.actions` is not a list (got "
                f"{type(actions).__name__})"
            )

        # (ii) ACTION total/fail-closed: the (possibly multi-element) actions array must map EXACTLY to a
        # known canonical entry; an unrecognized/empty array RAISES (never a silent drop).
        key = tuple(actions)
        if key not in _ACTION_MAP:
            raise PlanModelError(
                f"resource_changes[{idx}] ({address}) has an unrecognized/empty change.actions "
                f"{actions!r}; the action mapping is total and fail-closed"
            )
        canonical = _ACTION_MAP[key]
        if canonical is _OMIT:
            # no-op / read — intentionally omitted (no mutation).
            continue
        actions_detail.append({"address": address, "action": canonical})

    return actions_detail


# ----------------------------------------------------------------------------------------------------- #
# AC-IPM-4 — parse_gitops_changes: PURE recursive structured dict-diff (ADR C3).                          #
# ----------------------------------------------------------------------------------------------------- #

def _diff_paths(before, after, prefix=""):
    """Recursive structured diff of two already-parsed values. Yields the dotted JSONPath of every leaf
    that differs, is added, or is removed between `before` and `after`. Dicts recurse by key; lists recurse
    by index. A value present on one side only emits its path; a scalar that differs emits its path.

    Deterministic: dict keys are visited in sorted order, list indices in numeric order. The CALLER sorts
    the collected set, so traversal order is moot for the output — but stable here for clarity.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                # whole sub-tree added/removed — emit the sub-tree's path (not every descendant leaf).
                yield child
            else:
                yield from _diff_paths(before[key], after[key], child)
        return
    if isinstance(before, list) and isinstance(after, list):
        for idx in range(max(len(before), len(after))):
            child = f"{prefix}.{idx}" if prefix else str(idx)
            if idx >= len(before) or idx >= len(after):
                yield child
            else:
                yield from _diff_paths(before[idx], after[idx], child)
        return
    # type-mismatch (e.g. dict-vs-list, dict-vs-scalar) OR scalar comparison: differ iff not equal.
    if before != after:
        yield prefix


def _gitops_identity(obj):
    """Extract (kind, namespace, name) from a parsed manifest object's OWN fields. `kind` <- obj['kind'];
    `namespace` <- metadata.namespace (cluster-scoped -> None per the C3 normal form); `name` <-
    metadata.name. Missing fields default to None (the identity is best-effort metadata, not a gate)."""
    kind = obj.get("kind") if isinstance(obj, dict) else None
    metadata = obj.get("metadata") if isinstance(obj, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "kind": kind,
        "namespace": metadata.get("namespace"),
        "name": metadata.get("name"),
    }


def parse_gitops_changes(before_obj, after_obj):
    """Parse two already-parsed GitOps manifest objects (merge-base baseline vs candidate) into the
    canonical gitops change-set: a list with at most ONE {kind, namespace, name, action, changed_attrs}.

    The caller owns the YAML parse + any Helm/Kustomize render (at merge-base AND candidate); this function
    is a PURE recursive structured dict-diff over the two parsed objects — NO YAML parse, NO render, NO
    cluster fetch, NO command.

    action (from PRESENCE):
      before-only (after is None)  -> "delete"   (identity drawn from before_obj; changed_attrs = [])
      after-only  (before is None) -> "create"   (identity drawn from after_obj;  changed_attrs = [])
      both present, NON-EMPTY delta -> "update"   (identity drawn from after_obj;  changed_attrs = sorted set)
      both present, ZERO delta (byte-identical) -> OMITTED  (an empty list is returned — never a phantom
                                                             {action: update, changed_attrs: []})

    This mirrors parse_actions_detail's no-op -> OMIT: an unchanged resource never pollutes the change-set
    and never forces a spurious ack. An `update` therefore ALWAYS carries >=1 entry in changed_attrs.

    changed_attrs is the sorted set of changed dotted JSONPaths (e.g. "spec.template.spec.amiSelectorTerms";
    list elements addressed by index). For create/delete it is [] (the action carries the whole-object
    signal). Stable-sorted (deterministic order, not set-iteration order).

    TOTAL + FAIL-CLOSED like parse_actions_detail:
      a before_obj/after_obj that is neither a dict nor None (a malformed shape) RAISES PlanModelError;
      both None (an undefined diff) RAISES — never a silent empty result that greens the gate.

    PURE (no I/O, no command, no network). DETERMINISTIC (byte-identical changed_attrs ordering on repeat).
    """
    if before_obj is not None and not isinstance(before_obj, dict):
        raise PlanModelError(
            f"before_obj is neither a dict nor None (got {type(before_obj).__name__}); a malformed "
            "manifest shape must not pass as 'no change'"
        )
    if after_obj is not None and not isinstance(after_obj, dict):
        raise PlanModelError(
            f"after_obj is neither a dict nor None (got {type(after_obj).__name__}); a malformed "
            "manifest shape must not pass as 'no change'"
        )
    if before_obj is None and after_obj is None:
        raise PlanModelError(
            "both before_obj and after_obj are None — an undefined diff must not pass as 'no change'"
        )

    if after_obj is None:
        # before-only -> delete (whole-object remove; identity from the before object).
        ident = _gitops_identity(before_obj)
        return [{**ident, "action": "delete", "changed_attrs": []}]
    if before_obj is None:
        # after-only -> create (whole-object add; identity from the after object).
        ident = _gitops_identity(after_obj)
        return [{**ident, "action": "create", "changed_attrs": []}]

    # both present -> update IFF there is a non-empty changed-path set; else OMIT (zero-delta, no phantom).
    changed_attrs = sorted(set(_diff_paths(before_obj, after_obj)))
    if not changed_attrs:
        return []
    ident = _gitops_identity(after_obj)
    return [{**ident, "action": "update", "changed_attrs": changed_attrs}]


# ----------------------------------------------------------------------------------------------------- #
# AC-IPM-5 — parse_policy_findings: PURE conftest `-o json` parse, gating-bearing (ADR C1).               #
# ----------------------------------------------------------------------------------------------------- #

_SEVERITY_ENUM = ("high", "medium", "low")

# The three finding arrays parsed, each mapped to its gating origin. `successes` is an INT count — never
# iterated. Order matters for the deterministic file-then-(failures-before-warnings-before-exceptions) sort.
_GATING_ARRAYS = (
    ("failures", "deny"),
    ("warnings", "warn"),
    ("exceptions", "exception"),
)


def parse_policy_findings(conftest_json):
    """Parse the `conftest test -o json` envelope (already parsed into Python) into the canonical
    risk-finding shape: a list of {rule, resource, severity, gating}.

    The top level is an ARRAY of per-file objects: {filename, namespace, successes (int COUNT),
    warnings[], failures[], exceptions[]}. ALL THREE finding arrays are iterated — `successes` is an int
    COUNT and is NEVER iterated. Each entry {msg, metadata} maps to a finding:

      gating   <- the array of origin (closed enum): failures -> "deny", warnings -> "warn",
                  exceptions -> "exception". THE hard-FAIL-vs-ackable discriminator. exceptions[] is NOT
                  dropped — a self-waived deny stays VISIBLE (gating: exception).
      rule     <- metadata.rule (the policy-pack's stable id) if present, else derived from metadata.query.
      resource <- metadata.resource if a non-empty string, ELSE the entry's filename (always non-empty —
                  every gating finding has a nameable, ackable identity, never None).
      severity <- metadata.severity normalized to {high, medium, low}; an absent/unrecognized severity on a
                  `warn` finding FAILS CLOSED to "high" (gate-toward-stricter — a mistagged severity must
                  not silently skip the ack). For a deny finding severity is recorded but moot.

    The PARSER only CLASSIFIES; it does NOT gate. The verdict applies: gating==deny is a hard FAIL (no ack
    path); gating==warn ∧ severity==high is the ackable-risk gate; gating==exception is recorded + surfaced.

    TOTAL + FAIL-CLOSED: a non-array envelope RAISES PlanModelError; an entry in any of
    failures/warnings/exceptions missing `msg` RAISES — never a silent empty parse that greens the gate.

    PURE (consumes already-parsed JSON; runs no command, no conftest). DETERMINISTIC (stable
    file-then-(failures-before-warnings-before-exceptions) order; byte-identical on repeat).
    """
    if not isinstance(conftest_json, list):
        raise PlanModelError(
            f"conftest envelope is not an array (got {type(conftest_json).__name__}); expected a parsed "
            "`conftest test -o json` document — a malformed shape must not pass as 'no findings'"
        )

    findings = []
    for f_idx, per_file in enumerate(conftest_json):
        if not isinstance(per_file, dict):
            raise PlanModelError(
                f"conftest[{f_idx}] is not an object (got {type(per_file).__name__})"
            )
        filename = per_file.get("filename")
        # filename is the resource fallback / identity safety net — it must be a usable non-empty string.
        if not isinstance(filename, str) or not filename:
            raise PlanModelError(
                f"conftest[{f_idx}] missing a non-empty string `filename` (the resource fallback identity)"
            )

        for array_key, gating in _GATING_ARRAYS:
            entries = per_file.get(array_key)
            if entries is None:
                continue
            if not isinstance(entries, list):
                raise PlanModelError(
                    f"conftest[{f_idx}] `{array_key}` is not a list (got {type(entries).__name__})"
                )
            for e_idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise PlanModelError(
                        f"conftest[{f_idx}].{array_key}[{e_idx}] is not an object "
                        f"(got {type(entry).__name__})"
                    )
                # FAIL-CLOSED: an entry missing `msg` RAISES (never a silent skip that greens the gate).
                if "msg" not in entry:
                    raise PlanModelError(
                        f"conftest[{f_idx}].{array_key}[{e_idx}] missing `msg` — a malformed finding "
                        "entry must not pass as 'no finding'"
                    )
                metadata = entry.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}

                # rule: metadata.rule (stable id) else derived from metadata.query.
                rule = metadata.get("rule")
                if not isinstance(rule, str) or not rule:
                    query = metadata.get("query")
                    rule = query if isinstance(query, str) and query else None

                # resource: metadata.resource if a non-empty string, else the filename fallback.
                resource = metadata.get("resource")
                if not isinstance(resource, str) or not resource:
                    resource = filename

                # severity: normalize to the closed enum; absent/unrecognized on a `warn` -> "high"
                # (fail-closed gate-toward-stricter). On deny/exception an unrecognized severity is
                # recorded as-given normalized form, defaulting to "high" (moot for deny; conservative).
                raw_sev = metadata.get("severity")
                sev = raw_sev.lower() if isinstance(raw_sev, str) else None
                if sev not in _SEVERITY_ENUM:
                    # absent/unrecognized -> fail CLOSED to "high" (strictest); for a warn this is the
                    # gate-toward-stricter rule, for deny it is moot, for exception it is conservative.
                    sev = "high"

                findings.append({
                    "rule": rule,
                    "resource": resource,
                    "severity": sev,
                    "gating": gating,
                })

    return findings


if __name__ == "__main__":  # pragma: no cover - convenience CLI: parse a plan JSON on stdin.
    import json
    import sys

    print(json.dumps(parse_actions_detail(json.load(sys.stdin)), indent=2, sort_keys=True))
