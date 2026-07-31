#!/usr/bin/env python3
"""foundry_realization — the POST-DEPLOY "did the merged change LAND?" realization frame.

Because **merge IS the deploy trigger**, a change realizes *after* it merges — so realization is a
SEPARATE post-merge verdict, distinct from the pre-merge attributable walk (`foundry_walk_evidence.py`,
which this module does NOT touch). `derive_realization_verdict` adjudicates whether the merged change
LANDED over three recorded signals:

  (a) `tofu plan == ∅` post-apply (reality == the merged IaC — the change applied, drift loop closed),
  (b) ArgoCD `Synced ∧ Healthy` (the post-merge `argocd app get -o json` live-cluster read),
  (c) deployed-artifact identity == the merged commit (the staleness STALE-image guard).

This atom OWNS the post-merge `argocd_adjudicate` (the §14.4 v2.8 R-Q4c·1 reconception, BINDING): the
`Synced ∧ Healthy` adjudicator is a post-deploy, live-cluster signal that does not exist pre-merge — it
is DEFINED here on a clean two-axis signature (NO `tier`, NO `diff`/`intended`), NOT imported from
`gitops-surfaces`. The producer `emit_realization_evidence` output IS exactly the verdict input — one
reconciled shape, no transform gap. `applicable` is COMPUTED from `change_scope`, never a free driver
boolean (closes the not-applicable fail-open). `candidate_sha` pins the gate-merged HEAD (R-Q4c·5).

Threat model — TRUSTED OPERATOR; floor-#3-adjacent (a realization verdict, post-merge). Fail-closed: a
missing REQUIRED signal ⇒ NOT_LANDED (never a false "landed"). It does NOT block a merge (merge already
happened — you can't un-merge); its consequence is a NAMED incident path (`feat-foundry-id-rollback`) +
a tracked state, the design's fix for the fail-open "merge already deployed, nobody checks" gap.

PURE: `argocd_adjudicate` + `derive_realization_verdict` perform NO I/O, no network, no cluster call.
`emit_realization_evidence` consumes already-read snapshots (the caller — `id-sync`/`id-verify` —
owns the live reads); it derives `post_apply_plan_empty` via the BUILT `parse_actions_detail`.
"""
import importlib.util
import os

# The id-rollback incident path every NOT_LANDED names (the design's "never silently dropped" fix).
_REMEDIATION = (
    "open the feat-foundry-id-rollback incident path: git revert -> reconcile -> re-verify landed"
)

# NOT_LANDED classification precedence (deterministic primary on multi-signal failure): the apply-plane
# drift dominates — an un-applied plan subsumes the downstream GitOps/artifact symptoms (don't
# under-report). PARTIAL_APPLY > UNREALIZED > STALE_ARTIFACT.
_PRECEDENCE = ("PARTIAL_APPLY", "UNREALIZED", "STALE_ARTIFACT")


def _load_plan_model():
    """Load scripts/foundry_plan_model.py (the SINGLE canonical IaC-change parser; the empties-check
    source). The producer derives `post_apply_plan_empty` via its `parse_actions_detail` — NEVER a free
    driver boolean. Raises if absent (fail-loud — a missing parser must not green a realization)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "foundry_plan_model.py")
    spec = importlib.util.spec_from_file_location("foundry_plan_model_realization", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------------------------- #
# (i) The OWNED post-merge ArgoCD adjudicator — the two ORTHOGONAL axes (§14.4 R-Q4c·1).          #
# --------------------------------------------------------------------------------------------- #

def argocd_adjudicate(*, sync_status, health_status):
    """The post-merge ArgoCD realization adjudicator. GREEN iff `sync_status == "Synced"` AND
    `health_status == "Healthy"` — the two ORTHOGONAL axes (a `Synced`-but-`Degraded` app is RED: it
    converged to the desired manifest but the live workload is unhealthy). Else RED with a `reason`.

    This atom DEFINES it (it is NOT imported from gitops-surfaces — that is a pre-merge git-diff plane,
    a different concern). It has NO `tier` (v2.6 deleted blast-from-adjudicator) and NO `diff`/`intended`
    (post-merge has no git-diff). `id-sync`/`id-verify` (and `derive_realization_verdict`) CONSUME it.

    Returns {"verdict": "GREEN"|"RED", "reason": str}.
    """
    synced = sync_status == "Synced"
    healthy = health_status == "Healthy"
    if synced and healthy:
        return {"verdict": "GREEN", "reason": "Synced ∧ Healthy"}
    reasons = []
    if not synced:
        reasons.append(f"sync_status={sync_status!r} (expected 'Synced')")
    if not healthy:
        reasons.append(f"health_status={health_status!r} (expected 'Healthy')")
    return {"verdict": "RED", "reason": "; ".join(reasons)}


# --------------------------------------------------------------------------------------------- #
# (ii) The producer — emit_realization_evidence. Output == the verdict input (one shape).         #
# --------------------------------------------------------------------------------------------- #

class RealizationEvidenceError(ValueError):
    """Raised when the producer is handed a contradictory/malformed input — a frozen-`gitops_paths`
    `change_scope` cannot produce `argocd.applicable == False`; a bare (non-3-field) artifact cannot be
    recorded. Fail-loud: a producer mistake must surface, never silently record a fail-open shape."""


def emit_realization_evidence(*, change_scope, candidate_sha, post_apply_plan_results, argocd_status,
                              artifact):
    """PRODUCER — record the post-deploy snapshot into the EXACT shape `derive_realization_verdict`
    consumes (one reconciled contract, no transform gap; closes the DC3-class producer->consumer break).

    Output (== the verdict input):
        {
          "candidate_sha": <str>,                          # the gate-merged HEAD pin (R-Q4c·5)
          "post_apply_plan_empty": <bool>,                 # via the BUILT parse_actions_detail
          "argocd": {"applicable": <bool>, "sync_status", "health_status"},
          "artifact": {"applicable": <bool>, "deployed_identity", "merged_commit"},
        }

    Who computes each field:
      - `post_apply_plan_empty` is DERIVED from `post_apply_plan_results` (a parsed `tofu plan -json`
        document) via the built `parse_actions_detail`: empty iff the parse yields NO mutating actions.
        NOT a free driver boolean. A None `post_apply_plan_results` (the signal was never read) is
        recorded as None -> the verdict treats a missing REQUIRED signal as NOT_LANDED (fail-closed).
      - `argocd.{sync_status, health_status}` are recorded verbatim from `argocd_status` (the post-merge
        `argocd app get -o json` read).
      - `artifact.{deployed_identity, merged_commit}` are recorded from the staleness read.

    `applicable` is COMPUTED from `change_scope`, NEVER a free driver boolean (closes the not-applicable
    fail-open):
      - `argocd.applicable = bool(change_scope.get("gitops_paths"))` — a change with frozen GitOps paths
        in scope CANNOT claim NA. A `change_scope` that carries `gitops_paths` while the supplied
        `argocd_status` claims `applicable=False` is a CONTRADICTION -> RealizationEvidenceError.
      - `artifact.applicable` is True iff `change_scope` says the change deploys an image artifact
        (`deploys_artifact` truthy, or a non-empty `artifact_image`/`image` hint).

    The `artifact` parameter MUST be the FULL `{deployed_identity, merged_commit}` shape (a bare value is
    the producer<->consumer shape-break the contract forbids) -> RealizationEvidenceError on a bare value.
    The recorded `evidence.artifact` is the full 3-field `{applicable, deployed_identity, merged_commit}`.
    """
    change_scope = change_scope or {}

    # --- argocd.applicable: COMPUTED from frozen gitops_paths (never driver-chosen). ---
    gitops_applicable = bool(change_scope.get("gitops_paths"))
    argocd_status = argocd_status or {}
    # A driver may not contradict the scope-derived applicability: frozen gitops_paths present but the
    # driver's argocd_status claims applicable=False is rejected (cannot claim NA when GitOps is in scope).
    driver_argocd_applicable = argocd_status.get("applicable")
    if gitops_applicable and driver_argocd_applicable is False:
        raise RealizationEvidenceError(
            "change_scope carries frozen gitops_paths (argocd.applicable computes True) but argocd_status "
            "claims applicable=False — a change cannot claim ArgoCD-not-applicable while GitOps is in scope"
        )

    argocd_block = {
        "applicable": gitops_applicable,
        "sync_status": argocd_status.get("sync_status"),
        "health_status": argocd_status.get("health_status"),
    }

    # --- artifact.applicable: COMPUTED from change_scope (does the change deploy an image artifact?). ---
    artifact_applicable = bool(
        change_scope.get("deploys_artifact")
        or change_scope.get("artifact_image")
        or change_scope.get("image")
    )
    # The artifact parameter must be the FULL shape (a bare value -> shape-break -> reject).
    if not isinstance(artifact, dict):
        raise RealizationEvidenceError(
            f"artifact must be the full {{deployed_identity, merged_commit}} object, not a bare value "
            f"(got {type(artifact).__name__}) — the producer<->consumer contract forbids a bare artifact"
        )
    artifact_block = {
        "applicable": artifact_applicable,
        "deployed_identity": artifact.get("deployed_identity"),
        "merged_commit": artifact.get("merged_commit"),
    }

    # --- post_apply_plan_empty: DERIVED via the built parse_actions_detail (never a driver boolean). ---
    if post_apply_plan_results is None:
        # the signal was never read — record None so the verdict fail-closes (missing REQUIRED signal).
        post_apply_plan_empty = None
    else:
        pm = _load_plan_model()
        actions = pm.parse_actions_detail(post_apply_plan_results)
        post_apply_plan_empty = (len(actions) == 0)

    return {
        "candidate_sha": candidate_sha,
        "post_apply_plan_empty": post_apply_plan_empty,
        "argocd": argocd_block,
        "artifact": artifact_block,
    }


# --------------------------------------------------------------------------------------------- #
# (iii) The verdict — derive_realization_verdict. PURE; fail-closed; precedence-classified.       #
# --------------------------------------------------------------------------------------------- #

def derive_realization_verdict(evidence):
    """PURE — adjudicate whether the merged change LANDED over the recorded realization evidence.

    LANDED iff ALL:
      (a) post_apply_plan_empty == True;
      (b) argocd.applicable == False OR argocd_adjudicate(sync, health).verdict == GREEN;
      (c) artifact.applicable == False OR artifact.deployed_identity == artifact.merged_commit.

    Any missing/false/ambiguous REQUIRED signal (applicable == True but the sub-signal is absent) ⇒
    NOT_LANDED (fail-closed — it NEVER defaults to landed). Both-NA reducing LANDED to
    `post_apply_plan_empty` alone is CORRECT ONLY for a genuine pure-tofu, no-gitops, no-artifact change.

    NOT_LANDED classifications + deterministic precedence (primary on multi-signal failure):
      PARTIAL_APPLY  (post_apply_plan_empty is not True)            >
      UNREALIZED     (argocd.applicable ∧ not GREEN / signal absent) >
      STALE_ARTIFACT (artifact.applicable ∧ identity mismatch / signal absent).

    Returns {"verdict": "LANDED"|"NOT_LANDED", "classification": str|None,
             "remediation": str|None, "reasons": [str, ...]}. Every NOT_LANDED carries a `remediation`
    naming the id-rollback path; the verdict is an OBSERVATION + incident trigger, NOT a merge block.
    """
    evidence = evidence or {}
    argocd = evidence.get("argocd") or {}
    artifact = evidence.get("artifact") or {}

    failures = {}  # classification -> reason
    reasons = []

    # (a) apply-plane: post_apply_plan_empty MUST be exactly True. None (never read) / False both fail.
    plan_empty = evidence.get("post_apply_plan_empty")
    if plan_empty is not True:
        if plan_empty is None:
            reason = "post_apply_plan_empty signal absent (REQUIRED) — fail-closed"
        else:
            reason = "post-apply tofu plan is NON-empty (partial apply — reality != merged IaC)"
        failures["PARTIAL_APPLY"] = reason
        reasons.append(reason)

    # (b) GitOps plane: applicable iff frozen gitops_paths in scope; then MUST adjudicate GREEN.
    if argocd.get("applicable") is True:
        sync_status = argocd.get("sync_status")
        health_status = argocd.get("health_status")
        if sync_status is None or health_status is None:
            reason = ("ArgoCD applicable but a REQUIRED sub-signal is absent "
                      f"(sync_status={sync_status!r}, health_status={health_status!r}) — fail-closed")
            failures["UNREALIZED"] = reason
            reasons.append(reason)
        else:
            adj = argocd_adjudicate(sync_status=sync_status, health_status=health_status)
            if adj["verdict"] != "GREEN":
                reason = f"ArgoCD not realized: {adj['reason']}"
                failures["UNREALIZED"] = reason
                reasons.append(reason)

    # (c) artifact plane: applicable iff the change deploys an image; then identity MUST == merged commit.
    if artifact.get("applicable") is True:
        deployed = artifact.get("deployed_identity")
        merged = artifact.get("merged_commit")
        if deployed is None or merged is None:
            reason = ("artifact applicable but a REQUIRED sub-signal is absent "
                      f"(deployed_identity={deployed!r}, merged_commit={merged!r}) — fail-closed")
            failures["STALE_ARTIFACT"] = reason
            reasons.append(reason)
        elif deployed != merged:
            reason = (f"deployed-artifact identity {deployed!r} != merged commit {merged!r} "
                      "(STALE / NOT-ROLLED)")
            failures["STALE_ARTIFACT"] = reason
            reasons.append(reason)

    if not failures:
        return {"verdict": "LANDED", "classification": None, "remediation": None,
                "reasons": ["post_apply_plan_empty ∧ (ArgoCD NA or GREEN) ∧ (artifact NA or identity-match)"]}

    # Deterministic PRIMARY incident category by precedence (don't under-report apply-plane drift).
    primary = next(c for c in _PRECEDENCE if c in failures)
    return {
        "verdict": "NOT_LANDED",
        "classification": primary,
        "remediation": _REMEDIATION,
        "reasons": reasons,
    }
