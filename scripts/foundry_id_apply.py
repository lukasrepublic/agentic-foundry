#!/usr/bin/env python3
"""foundry_id_apply — the posture-gated APPLY gate: the one place the framework may MUTATE infra, and
only as the CTX posture permits (feat-foundry-id-apply).

`id-apply` composes two inputs into a closed `ApplyDecision`:
  * the `ctx-posture` resolver's `Posture` (decision ∈ {EXECUTE, GENERATE, REFUSE}, audited: bool) — the
    operator-owned CTX session posture (trusted under the cooperating-operator model); AND
  * the change's GitOps CLASS, RE-DERIVED here from the frozen change-scope (`changed_paths`) × the
    profile's `infra_binding.gitops_paths` glob set — NOT a caller-supplied boolean (the §8 Critical:
    software-delivery never trusts a self-reported flag for a routing decision).

The decision table is TOTAL and fail-closed; REFUSE dominates; there is NO path from an
absent/ambiguous/unclassified input to a silent mutation. `decide_apply` is PURE — decision only, no
I/O, runs no command. The runbook content (EXECUTE / GENERATE_RUNBOOK) is the FROZEN
`infra_binding.apply` string (operator-authored + audited at profile-authorize time) — never freeform
text the gate could be tricked into emitting. Post-apply verification is the DISTINCT
`infra_binding.verify` slot (read-only), never a second `plan` call.

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). A fail-closed gate over machine
inputs: any unresolved/ambiguous input → REFUSE (never a silent mutation). It governs whether a prod
mutation runs → SECURITY-REVIEW surface (floor #3). The framework NEVER engages break-glass and NEVER
mutates in the GENERATE_RUNBOOK / VERIFY_ONLY / REFUSE branches. Forgery of `Posture` is bounded
out-of-threat-model at the human root of trust; v1 adds no Posture-attestation mechanism.

v1 scope: posture × gitops only. The blast-tier / `high_blast_acked` coupling is deferred to v2
(Q4a/Q4b) — this module carries NO blast_tier parameter.
"""
import fnmatch
from dataclasses import dataclass

# The closed ApplyAction enum — every branch of the total table resolves to exactly one of these.
EXECUTE = "EXECUTE"                  # non-prod / break-glass, non-GitOps: the framework runs infra_binding.apply.
GENERATE_RUNBOOK = "GENERATE_RUNBOOK"  # guarded prod, non-GitOps: EMIT the frozen apply runbook for the operator.
VERIFY_ONLY = "VERIFY_ONLY"         # GitOps (non-REFUSE): the ArgoCD controller mutates; the framework verifies.
REFUSE = "REFUSE"                   # the most-restrictive outcome (stale / unreachable / ambiguous / unmatched).

# The closed GitOps classification enum (re-derived; never caller-asserted).
GITOPS = "gitops"        # changed_paths non-empty AND all fall under a gitops_paths glob.
DIRECT = "direct"        # changed_paths non-empty AND none falls under a gitops_paths glob.
AMBIGUOUS = "ambiguous"  # mixed/unmatched — OR an EMPTY changed_paths (the vacuous-quantifier guard) → REFUSE.

# The posture-decision values consumed from the ctx-posture resolver's Posture.decision (read-only).
_POSTURE_EXECUTE = "EXECUTE"
_POSTURE_GENERATE = "GENERATE"
_POSTURE_REFUSE = "REFUSE"


@dataclass(frozen=True)
class RunbookPayload:
    """The constrained runbook payload for an EXECUTE / GENERATE_RUNBOOK decision.

    `command` is EXACTLY the profile's frozen `infra_binding.apply` string — the same operator-authored,
    profile-authorize-time-audited command the EXECUTE branch would run, here handed to the operator
    (GENERATE_RUNBOOK) or run by the framework (EXECUTE). It is NOT freeform text the gate could be
    tricked into emitting. `verify` is the DISTINCT read-only `infra_binding.verify` slot — the
    post-apply confirmation the framework runs read-only (NOT a second `plan` call). `executed_by`
    records the SoD split (`framework` for EXECUTE, `operator` for GENERATE_RUNBOOK)."""
    command: str          # == infra_binding.apply (frozen; never freeform).
    verify: str           # == infra_binding.verify (distinct read-only slot; never .plan).
    executed_by: str      # "framework" (EXECUTE) | "operator" (GENERATE_RUNBOOK).


@dataclass(frozen=True)
class ApplyDecision:
    """The closed apply DECISION record (resolving the §8 enum-vs-payload contradiction): an `action`
    enum, the break-glass `audited` flag carried from the Posture, and the `runbook` payload.

    `runbook` is None for VERIFY_ONLY and REFUSE (nothing is emitted/run). For EXECUTE and
    GENERATE_RUNBOOK it is a RunbookPayload whose `command` is the FROZEN `infra_binding.apply` and whose
    `verify` is the distinct read-only `infra_binding.verify` — never freeform. `reason` is a
    human-readable trace of which table row fired (audit-ledger evidence)."""
    action: str
    audited: bool = False
    runbook: "RunbookPayload | None" = None
    reason: str = ""


def _binding_get(infra_binding, key):
    """Read a slot from infra_binding, tolerating a dict OR an attribute-bearing object. Returns None
    when absent — the caller fail-closes on a missing required slot."""
    if infra_binding is None:
        return None
    if isinstance(infra_binding, dict):
        return infra_binding.get(key)
    return getattr(infra_binding, key, None)


def _gitops_globs(infra_binding):
    """The profile's declared gitops_paths glob list (or an empty tuple). A non-list/absent value yields
    an empty set of globs → every non-empty change classifies `direct` (no false `gitops` route)."""
    globs = _binding_get(infra_binding, "gitops_paths")
    if not isinstance(globs, (list, tuple)):
        return tuple()
    return tuple(g for g in globs if isinstance(g, str) and g)


def _under_any_glob(path, globs):
    """True iff `path` matches any gitops glob. fnmatch with `*` spanning `/` mirrors the profile authors'
    glob intent (e.g. `clusters/**` / `manifests/*`); a leading-`./` is normalized off."""
    if not isinstance(path, str) or not path:
        return False
    p = path[2:] if path.startswith("./") else path
    return any(fnmatch.fnmatch(p, g) for g in globs)


def classify_gitops(*, changed_paths, infra_binding):
    """RE-DERIVE the GitOps class of a change from its FROZEN scope × the profile's gitops_paths globs —
    NOT a caller-supplied boolean (AC-IDAPP-1). Returns one of {"gitops","direct","ambiguous"}:

      * `gitops`    iff `changed_paths` is NON-EMPTY **and ALL** paths fall under a gitops glob;
      * `direct`    iff `changed_paths` is NON-EMPTY **and NONE** does;
      * `ambiguous` otherwise — a MIXED/unmatched set, OR an **EMPTY** `changed_paths`.

    The EMPTY → ambiguous guard is load-bearing (the re-audit High): "all" and "none" both go vacuously
    true on an empty set, so empty MUST resolve to ambiguous ⇒ REFUSE, never a silent EXECUTE/VERIFY
    route. Pure — no I/O.
    """
    # Normalize to a list of non-empty string paths; anything else is not a valid scope member.
    paths = [p for p in (changed_paths or []) if isinstance(p, str) and p.strip()]
    if not paths:
        return AMBIGUOUS  # empty / no valid paths → ambiguous (the vacuous-quantifier guard).

    globs = _gitops_globs(infra_binding)
    if not globs:
        # No gitops globs declared: a non-empty change has none-under-glob → `direct` (never `gitops`).
        return DIRECT

    matches = [_under_any_glob(p, globs) for p in paths]
    if all(matches):
        return GITOPS
    if not any(matches):
        return DIRECT
    return AMBIGUOUS  # mixed (some under a gitops glob, some not) → ambiguous → REFUSE.


def _refuse(reason):
    return ApplyDecision(action=REFUSE, audited=False, runbook=None, reason=reason)


def decide_apply(*, posture, gitops_class, infra_binding):
    """Map a (Posture × re-derived gitops_class) onto a closed `ApplyDecision` by a TOTAL, fail-closed
    table — REFUSE DOMINATES; no path from an absent/ambiguous/unclassified input to a silent mutation
    (AC-IDAPP-1/-2). PURE — decision only; no I/O; runs no command.

    The table:
      (a)  posture.decision == REFUSE                              → REFUSE (dominates).
      (a′) gitops_class == "ambiguous"                             → REFUSE (an unclassifiable change is
           never routed to a mutation — fail-closed, no guessing — INCLUDING an empty changed_paths).
      (b)  gitops_class == "gitops" AND posture ≠ REFUSE           → VERIFY_ONLY (the ArgoCD controller
           realizes it; the framework only verifies read-only — issues NO mutating verb).
      (c)  posture.decision == EXECUTE AND gitops_class == "direct" → EXECUTE (runs infra_binding.apply;
           carries posture.audited — True for break-glass).
      (d)  posture.decision == GENERATE AND gitops_class == "direct" → GENERATE_RUNBOOK (emits the FROZEN
           infra_binding.apply runbook for the operator to run in CTX; framework verifies read-only).
      else → REFUSE (the table is total; any unmatched/unknown input fail-closes).

    The EXECUTE / GENERATE_RUNBOOK runbook `command` is EXACTLY the frozen `infra_binding.apply` (never
    freeform); `verify` is the DISTINCT read-only `infra_binding.verify` slot. If either required slot is
    missing/empty for a branch that needs them, the decision fail-closes to REFUSE rather than emitting a
    half-formed runbook.
    """
    decision = getattr(posture, "decision", None)

    # (a) REFUSE dominates — a stale/unreachable/refused posture never mutates.
    if decision == _POSTURE_REFUSE:
        return _refuse("posture REFUSE dominates (stale/unreachable/unmatched CTX)")

    # (a′) an unclassifiable change (mixed/unmatched, OR empty changed_paths) never routes to a mutation.
    if gitops_class == AMBIGUOUS:
        return _refuse("gitops_class ambiguous (mixed/unmatched/empty changed_paths) → fail-closed REFUSE")

    # Guard against an unknown gitops_class value (only gitops/direct survive past (a′)).
    if gitops_class not in (GITOPS, DIRECT):
        return _refuse(f"unrecognized gitops_class {gitops_class!r} → fail-closed REFUSE")

    # (b) GitOps (non-REFUSE posture) → VERIFY_ONLY. The ArgoCD controller mutates; the framework issues
    #     ONLY the read-only verify. REFUSE was already handled above, so posture is EXECUTE or GENERATE.
    if gitops_class == GITOPS:
        verify = _binding_get(infra_binding, "verify")
        if not isinstance(verify, str) or not verify.strip():
            return _refuse("VERIFY_ONLY needs infra_binding.verify, missing/empty → fail-closed REFUSE")
        return ApplyDecision(
            action=VERIFY_ONLY,
            audited=False,
            runbook=None,
            reason="gitops change, posture non-REFUSE → VERIFY_ONLY (controller realizes; framework verifies)",
        )

    # From here gitops_class == DIRECT. The mutation branches need BOTH the frozen apply + the verify slot.
    apply_cmd = _binding_get(infra_binding, "apply")
    verify_cmd = _binding_get(infra_binding, "verify")
    if not isinstance(apply_cmd, str) or not apply_cmd.strip():
        return _refuse("direct change needs frozen infra_binding.apply, missing/empty → fail-closed REFUSE")
    if not isinstance(verify_cmd, str) or not verify_cmd.strip():
        return _refuse("direct change needs infra_binding.verify, missing/empty → fail-closed REFUSE")

    # (c) non-prod / break-glass EXECUTE + direct → the framework runs the frozen apply (carry audited).
    if decision == _POSTURE_EXECUTE:
        return ApplyDecision(
            action=EXECUTE,
            audited=bool(getattr(posture, "audited", False)),
            runbook=RunbookPayload(command=apply_cmd, verify=verify_cmd, executed_by="framework"),
            reason="posture EXECUTE + direct → framework runs frozen infra_binding.apply",
        )

    # (d) guarded-prod GENERATE + direct → emit the frozen apply runbook for the OPERATOR (SoD).
    if decision == _POSTURE_GENERATE:
        return ApplyDecision(
            action=GENERATE_RUNBOOK,
            audited=False,
            runbook=RunbookPayload(command=apply_cmd, verify=verify_cmd, executed_by="operator"),
            reason="posture GENERATE + direct → emit frozen runbook for operator (SoD); framework verifies",
        )

    # else — any unmatched/unknown posture.decision → fail-closed REFUSE (the table is total).
    return _refuse(f"unmatched posture.decision {decision!r} → fail-closed REFUSE")
