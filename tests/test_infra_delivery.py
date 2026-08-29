"""tests/test_infra_delivery.py — converted from scripts/foundry_checks/{infra-plan-model,
infra-realization-gate, id-apply, id-allocation-mutex, id-impact-change-tier}.py.

Ports the real behavioral assertions those five drop-in selftests drove directly against the
shipped infra-delivery decision modules: `scripts/foundry_plan_model.py` (the three canonical
IaC-change plane parsers), `scripts/foundry_realization.py` (the post-deploy LANDED/NOT_LANDED
verdict), `scripts/foundry_id_apply.py` (the posture-gated apply gate) + `foundry_ctx_posture.py`,
and `scripts/foundry_id_alloc.py` (the flock-guarded ID-allocation mutex, real os.fork()
concurrency). CLI/doctor scaffolding is dropped; the computed fixtures/assertions are kept.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from conftest import load_module

plan_model = load_module("scripts/foundry_plan_model.py", "foundry_plan_model")
realization = load_module("scripts/foundry_realization.py", "foundry_realization")
id_apply = load_module("scripts/foundry_id_apply.py", "foundry_id_apply")
id_alloc = load_module("scripts/foundry_id_alloc.py", "foundry_id_alloc")


# ===================================================================== foundry_plan_model.py ==== #

def _plan(*resource_changes):
    return {"format_version": "1.2", "resource_changes": list(resource_changes)}


def _rc(address, actions):
    return {"address": address, "change": {"actions": list(actions)}}


class TestParseActionsDetail:
    def test_canonical_total_mapping(self):
        plan = _plan(
            _rc("aws_s3_bucket.a", ["create"]),
            _rc("aws_iam_role.b", ["update"]),
            _rc("aws_instance.c", ["delete"]),
            _rc("aws_db_instance.d", ["delete", "create"]),
            _rc("aws_eip.e", ["create", "delete"]),
            _rc("aws_vpc.f", ["no-op"]),
            _rc("data.aws_ami.g", ["read"]),
        )
        got = plan_model.parse_actions_detail(plan)
        assert got == [
            {"address": "aws_s3_bucket.a", "action": "create"},
            {"address": "aws_iam_role.b", "action": "update"},
            {"address": "aws_instance.c", "action": "delete"},
            {"address": "aws_db_instance.d", "action": "replace"},
            {"address": "aws_eip.e", "action": "replace"},
        ]

    def test_fail_closed_envelope_and_actions(self):
        parse = plan_model.parse_actions_detail
        with pytest.raises(Exception):
            parse({"format_version": "1.2"})
        with pytest.raises(Exception):
            parse({"resource_changes": None})
        with pytest.raises(Exception):
            parse({"resource_changes": {"not": "a list"}})
        assert parse(_plan()) == []  # a well-formed empty envelope does NOT raise.
        with pytest.raises(Exception):
            parse(_plan(_rc("x.y", ["frobnicate"])))
        with pytest.raises(Exception):
            parse(_plan(_rc("x.y", [])))

    def test_deterministic_and_stable_order(self):
        plan = _plan(_rc("aws_s3_bucket.a", ["create"]), _rc("aws_iam_role.b", ["update"]))
        run1 = json.dumps(plan_model.parse_actions_detail(plan))
        run2 = json.dumps(plan_model.parse_actions_detail(plan))
        assert run1 == run2
        assert [d["address"] for d in plan_model.parse_actions_detail(plan)] == [
            "aws_s3_bucket.a", "aws_iam_role.b"]

    def test_fix_and_rerun(self):
        broken = _plan(_rc("aws_s3_bucket.z", ["explode"]))
        with pytest.raises(Exception):
            plan_model.parse_actions_detail(broken)
        fixed = _plan(_rc("aws_s3_bucket.z", ["create"]))
        assert plan_model.parse_actions_detail(fixed) == [{"address": "aws_s3_bucket.z", "action": "create"}]


def _manifest(kind, ns, name, spec):
    return {"kind": kind, "metadata": {"namespace": ns, "name": name}, "spec": spec}


class TestParseGitopsChanges:
    def setup_method(self):
        self.before = _manifest("NodePool", "karpenter", "default", {
            "template": {"spec": {"amiSelectorTerms": [{"alias": "al2023@v20240101"}]}},
            "disruption": {"consolidationPolicy": "WhenEmpty"},
        })
        self.after = _manifest("NodePool", "karpenter", "default", {
            "template": {"spec": {"amiSelectorTerms": [{"alias": "al2023@v20240601"}]}},
            "disruption": {"consolidationPolicy": "WhenEmpty"},
        })

    def test_update_canonical(self):
        g = plan_model.parse_gitops_changes(self.before, self.after)
        assert len(g) == 1
        row = g[0]
        assert (row["kind"], row["namespace"], row["name"], row["action"]) == (
            "NodePool", "karpenter", "default", "update")
        assert "spec.template.spec.amiSelectorTerms.0.alias" in row["changed_attrs"]
        assert not any(p.startswith("spec.disruption") for p in row["changed_attrs"])

    def test_create_and_delete(self):
        g_create = plan_model.parse_gitops_changes(None, self.after)
        assert g_create[0]["action"] == "create" and g_create[0]["changed_attrs"] == []
        g_delete = plan_model.parse_gitops_changes(self.before, None)
        assert g_delete[0]["action"] == "delete" and g_delete[0]["changed_attrs"] == []

    def test_noop_is_omitted_entirely(self):
        import copy
        same = copy.deepcopy(self.before)
        assert plan_model.parse_gitops_changes(self.before, same) == []

    def test_fail_closed(self):
        parse = plan_model.parse_gitops_changes
        with pytest.raises(Exception):
            parse("not-a-dict", self.after)
        with pytest.raises(Exception):
            parse(self.before, ["not", "a", "dict"])
        with pytest.raises(Exception):
            parse(None, None)

    def test_deterministic(self):
        g1 = json.dumps(plan_model.parse_gitops_changes(self.before, self.after))
        g2 = json.dumps(plan_model.parse_gitops_changes(self.before, self.after))
        assert g1 == g2


def _pfile(filename, **arrays):
    out = {"filename": filename, "namespace": "main", "successes": arrays.pop("successes", 3)}
    out.update(arrays)
    return out


class TestParsePolicyFindings:
    def test_gating_discriminator(self):
        conftest = [_pfile(
            "nodepool.yaml",
            failures=[{"msg": "never-allowed change", "metadata": {
                "rule": "deny-public-ami", "resource": "NodePool/karpenter/default", "severity": "low"}}],
            warnings=[{"msg": "high-blast AMI change", "metadata": {
                "rule": "warn-ami-change", "resource": "NodePool/karpenter/default", "severity": "high"}}],
            exceptions=[{"msg": "self-waived deny", "metadata": {
                "rule": "deny-public-ami", "resource": "NodePool/karpenter/default"}}],
        )]
        findings = plan_model.parse_policy_findings(conftest)
        deny = next((f for f in findings if f["gating"] == "deny"), None)
        warn = next((f for f in findings if f["gating"] == "warn"), None)
        exc = next((f for f in findings if f["gating"] == "exception"), None)
        assert deny is not None and deny["rule"] == "deny-public-ami"
        assert warn is not None and warn["severity"] == "high"
        assert exc is not None and exc["rule"] == "deny-public-ami"

    def test_metadata_less_failure_still_denies(self):
        meta_less = plan_model.parse_policy_findings([_pfile("x.yaml", failures=[{"msg": "metadata-less deny"}])])
        assert len(meta_less) == 1
        assert meta_less[0]["gating"] == "deny"
        assert meta_less[0]["resource"] == "x.yaml"
        assert meta_less[0]["rule"] is None

    def test_severity_fail_closed_to_high(self):
        sev_absent = plan_model.parse_policy_findings(
            [_pfile("a.yaml", warnings=[{"msg": "w", "metadata": {"rule": "r"}}])])
        sev_garbage = plan_model.parse_policy_findings(
            [_pfile("b.yaml", warnings=[{"msg": "w", "metadata": {"rule": "r", "severity": "CATASTROPHIC"}}])])
        assert sev_absent[0]["severity"] == "high"
        assert sev_garbage[0]["severity"] == "high"

    def test_resource_fallback_to_filename(self):
        no_resource = plan_model.parse_policy_findings(
            [_pfile("c.yaml", failures=[{"msg": "f", "metadata": {"rule": "r", "severity": "high"}}])])
        assert no_resource[0]["resource"] == "c.yaml"

    def test_rule_from_query_and_successes_not_iterated(self):
        rule_from_query = plan_model.parse_policy_findings(
            [_pfile("d.yaml", successes=7,
                    warnings=[{"msg": "w", "metadata": {"query": "data.main.deny", "severity": "medium"}}])])
        assert rule_from_query[0]["rule"] == "data.main.deny"
        assert plan_model.parse_policy_findings([_pfile("e.yaml", successes=5)]) == []

    def test_fail_closed(self):
        with pytest.raises(Exception):
            plan_model.parse_policy_findings({"not": "an array"})
        with pytest.raises(Exception):
            plan_model.parse_policy_findings([_pfile("z.yaml", failures=[{"metadata": {"rule": "r"}}])])
        assert plan_model.parse_policy_findings([]) == []


# =================================================================== foundry_realization.py ==== #

_EMPTY_PLAN = {"format_version": "1.2", "resource_changes": [
    {"address": "data.aws_ami.x", "change": {"actions": ["read"]}}]}


def _evidence(*, plan_empty=True, argocd_applicable=False, sync="Synced", health="Healthy",
              artifact_applicable=False, deployed="sha-merged", merged="sha-merged",
              candidate_sha="sha-merged"):
    return {
        "candidate_sha": candidate_sha,
        "post_apply_plan_empty": plan_empty,
        "argocd": {"applicable": argocd_applicable, "sync_status": sync, "health_status": health},
        "artifact": {"applicable": artifact_applicable, "deployed_identity": deployed, "merged_commit": merged},
    }


class TestRealizationVerdict:
    def test_all_good_lands(self):
        v = realization.derive_realization_verdict(_evidence(argocd_applicable=True, artifact_applicable=True))
        assert v["verdict"] == "LANDED" and v["classification"] is None

    def test_synced_degraded_not_landed(self):
        adj_red = realization.argocd_adjudicate(sync_status="Synced", health_status="Degraded")
        adj_green = realization.argocd_adjudicate(sync_status="Synced", health_status="Healthy")
        adj_oos = realization.argocd_adjudicate(sync_status="OutOfSync", health_status="Healthy")
        v = realization.derive_realization_verdict(_evidence(argocd_applicable=True, sync="Synced", health="Degraded"))
        assert adj_red["verdict"] == "RED"
        assert adj_green["verdict"] == "GREEN"
        assert adj_oos["verdict"] == "RED"
        assert v["verdict"] == "NOT_LANDED" and v["classification"] == "UNREALIZED"

    def test_nonempty_plan_partial_apply(self):
        v = realization.derive_realization_verdict(
            _evidence(plan_empty=False, argocd_applicable=True, artifact_applicable=True))
        assert v["verdict"] == "NOT_LANDED" and v["classification"] == "PARTIAL_APPLY"
        assert "id-rollback" in v["remediation"]

    def test_artifact_mismatch_stale(self):
        v = realization.derive_realization_verdict(
            _evidence(artifact_applicable=True, deployed="sha-stale", merged="sha-merged"))
        assert v["verdict"] == "NOT_LANDED" and v["classification"] == "STALE_ARTIFACT"

    def test_both_not_applicable_pure_tofu(self):
        v_pos = realization.derive_realization_verdict(_evidence(argocd_applicable=False, artifact_applicable=False))
        v_neg = realization.derive_realization_verdict(
            _evidence(plan_empty=False, argocd_applicable=False, artifact_applicable=False))
        assert v_pos["verdict"] == "LANDED"
        assert v_neg["verdict"] == "NOT_LANDED" and v_neg["classification"] == "PARTIAL_APPLY"

    def test_applicable_computed_from_change_scope_not_driver_claim(self):
        frozen_gitops_scope = {"gitops_paths": ["clusters/prod/app.yaml"]}
        ev = realization.emit_realization_evidence(
            change_scope=frozen_gitops_scope, candidate_sha="sha-merged",
            post_apply_plan_results=_EMPTY_PLAN,
            argocd_status={"sync_status": "Synced", "health_status": "Healthy"},
            artifact={"deployed_identity": None, "merged_commit": None})
        assert ev["argocd"]["applicable"] is True

        with pytest.raises(realization.RealizationEvidenceError):
            realization.emit_realization_evidence(
                change_scope=frozen_gitops_scope, candidate_sha="sha-merged",
                post_apply_plan_results=_EMPTY_PLAN,
                argocd_status={"applicable": False, "sync_status": "Synced", "health_status": "Healthy"},
                artifact={"deployed_identity": None, "merged_commit": None})

        ev_no = realization.emit_realization_evidence(
            change_scope={}, candidate_sha="sha-merged", post_apply_plan_results=_EMPTY_PLAN,
            argocd_status={"sync_status": None, "health_status": None},
            artifact={"deployed_identity": None, "merged_commit": None})
        assert ev_no["argocd"]["applicable"] is False

    def test_fail_closed_missing_required_signal(self):
        v_argocd = realization.derive_realization_verdict({
            "candidate_sha": "x", "post_apply_plan_empty": True,
            "argocd": {"applicable": True, "sync_status": None, "health_status": None},
            "artifact": {"applicable": False, "deployed_identity": None, "merged_commit": None}})
        v_artifact = realization.derive_realization_verdict({
            "candidate_sha": "x", "post_apply_plan_empty": True,
            "argocd": {"applicable": False, "sync_status": None, "health_status": None},
            "artifact": {"applicable": True, "deployed_identity": None, "merged_commit": None}})
        v_plan = realization.derive_realization_verdict({
            "candidate_sha": "x", "post_apply_plan_empty": None,
            "argocd": {"applicable": False, "sync_status": None, "health_status": None},
            "artifact": {"applicable": False, "deployed_identity": None, "merged_commit": None}})
        assert v_argocd["verdict"] == "NOT_LANDED" and v_argocd["classification"] == "UNREALIZED"
        assert v_artifact["verdict"] == "NOT_LANDED" and v_artifact["classification"] == "STALE_ARTIFACT"
        assert v_plan["verdict"] == "NOT_LANDED" and v_plan["classification"] == "PARTIAL_APPLY"

    def test_multi_signal_precedence(self):
        v_all_three = realization.derive_realization_verdict(_evidence(
            plan_empty=False, argocd_applicable=True, sync="OutOfSync", health="Degraded",
            artifact_applicable=True, deployed="sha-stale", merged="sha-merged"))
        v_argocd_artifact = realization.derive_realization_verdict(_evidence(
            argocd_applicable=True, sync="OutOfSync", health="Healthy",
            artifact_applicable=True, deployed="sha-stale", merged="sha-merged"))
        assert v_all_three["classification"] == "PARTIAL_APPLY"
        assert v_argocd_artifact["classification"] == "UNREALIZED"

    def test_candidate_sha_pinned_and_full_artifact_shape(self):
        ev = realization.emit_realization_evidence(
            change_scope={}, candidate_sha="sha-merged-HEAD", post_apply_plan_results=_EMPTY_PLAN,
            argocd_status={"sync_status": None, "health_status": None},
            artifact={"deployed_identity": None, "merged_commit": None})
        assert ev["candidate_sha"] == "sha-merged-HEAD"
        assert set(ev["artifact"].keys()) == {"applicable", "deployed_identity", "merged_commit"}
        with pytest.raises(realization.RealizationEvidenceError):
            realization.emit_realization_evidence(
                change_scope={}, candidate_sha="x", post_apply_plan_results=_EMPTY_PLAN,
                argocd_status={"sync_status": None, "health_status": None},
                artifact="sha-bare-value")

    def test_producer_verdict_round_trip(self):
        ev = realization.emit_realization_evidence(
            change_scope={"gitops_paths": ["clusters/prod/app.yaml"], "deploys_artifact": True},
            candidate_sha="sha-merged", post_apply_plan_results=_EMPTY_PLAN,
            argocd_status={"sync_status": "Synced", "health_status": "Healthy"},
            artifact={"deployed_identity": "img@sha-merged", "merged_commit": "img@sha-merged"})
        v = realization.derive_realization_verdict(ev)
        assert v["verdict"] == "LANDED"

    def test_fix_and_rerun(self):
        broken = _evidence(plan_empty=False, argocd_applicable=True, artifact_applicable=True)
        fixed = _evidence(plan_empty=True, argocd_applicable=True, artifact_applicable=True)
        assert realization.derive_realization_verdict(broken)["verdict"] == "NOT_LANDED"
        assert realization.derive_realization_verdict(fixed)["verdict"] == "LANDED"


# ==================================================================== foundry_id_apply.py ==== #
# feat-foundry-apply-gate-regrounding — decide_apply lost `posture`, `GENERATE_RUNBOOK` and `audited`;
# it now derives the GitOps class itself from `changed_paths` x `infra_binding` (AC-IDAGR-10) and
# routes on a CLOSED four-refusal table (AC-IDAGR-2). See tests/test_stack_profile.py for the
# AC-IDAGR-3/-7/-8/-12 checkpoints over the real shipped pack/schema/loader prose.

import dataclasses
import inspect

_FROZEN_APPLY = "tofu apply -auto-approve"
_FROZEN_VERIFY = "tofu plan -detailed-exitcode"
_INFRA_BINDING = {"apply": _FROZEN_APPLY, "verify": _FROZEN_VERIFY, "gitops_paths": ["clusters/**", "argocd/*"]}


def _classify(paths):
    return id_apply.classify_gitops(changed_paths=paths, infra_binding=_INFRA_BINDING)


def _decide(paths, infra_binding=_INFRA_BINDING):
    return id_apply.decide_apply(changed_paths=paths, infra_binding=infra_binding)


class TestIdApplyGate:
    def test_gitops_classification_rederivation(self):
        assert _classify(["infra/vpc.tf", "infra/iam.tf"]) == id_apply.DIRECT
        assert _classify(["clusters/prod/app.yaml", "argocd/root.yaml"]) == id_apply.GITOPS
        assert _classify(["infra/vpc.tf", "clusters/prod/app.yaml"]) == id_apply.AMBIGUOUS
        assert _classify([]) == id_apply.AMBIGUOUS  # empty scope is ambiguous, never a vacuous pass.

    def test_direct_change_executes(self):
        d = _decide(["infra/vpc.tf"])
        assert d.action == id_apply.EXECUTE
        assert d.runbook is not None and d.runbook.command == _FROZEN_APPLY

    def test_gitops_is_verify_only_and_no_mutating_verb_issued(self):
        d = _decide(["clusters/prod/app.yaml"])
        assert d.action == id_apply.VERIFY_ONLY and d.runbook is None
        mutating = {"apply", "destroy", "delete", "replace", "install", "upgrade", "uninstall", "sync", "create"}
        assert not (set(_FROZEN_VERIFY.split()) & mutating)

    def test_ambiguous_scope_fails_closed(self):
        assert _decide(["infra/vpc.tf", "clusters/prod/app.yaml"]).action == id_apply.REFUSE
        assert _decide([]).action == id_apply.REFUSE

    def test_decision_is_a_record_not_a_bare_enum(self):
        d = _decide(["infra/vpc.tf"])
        assert isinstance(d, id_apply.ApplyDecision)
        assert hasattr(d, "action") and hasattr(d, "runbook") and hasattr(d, "reason")
        assert isinstance(d.runbook, id_apply.RunbookPayload)

    def test_fix_and_rerun_flips_from_refuse_to_execute(self):
        before = _decide(["infra/vpc.tf", "clusters/prod/app.yaml"])
        after = _decide(["infra/vpc.tf"])
        assert before.action == id_apply.REFUSE
        assert after.action == id_apply.EXECUTE

    # ── AC-IDAGR-1 — the record's shape carries no field whose only possible source was the retired
    # posture probe: no `posture` parameter, no `GENERATE_RUNBOOK`, no `audited`, no nested
    # `RunbookPayload.executed_by`.
    def test_decision_record_carries_no_posture_derived_field(self):
        assert _dead_shape_violations(
            decide_apply_fn=id_apply.decide_apply,
            apply_decision_cls=id_apply.ApplyDecision,
            runbook_cls=id_apply.RunbookPayload,
            module=id_apply,
        ) == []
        d = _decide(["infra/vpc.tf"])
        assert not hasattr(d, "audited")
        assert not hasattr(d.runbook, "executed_by")
        print("IDAGR-1-RECORD-OK")

    # ── AC-IDAGR-1 DEAD-SHAPE MUTANT — the four realistic no-ops, driven at 4of4. Each is a SYNTHETIC
    # stand-in for exactly the shape a lazy "delete the posture branch" edit would leave behind; the
    # SAME `_dead_shape_violations` witness used by the row above must convict every one of them.
    def test_retained_dead_shape_is_convicted_4of4(self):
        convicted = 0

        # (1) posture kept as an ignored/defaulted parameter.
        def mutant_decide_apply(*, posture=None, changed_paths, infra_binding):
            return id_apply.decide_apply(changed_paths=changed_paths, infra_binding=infra_binding)

        v1 = _dead_shape_violations(
            decide_apply_fn=mutant_decide_apply, apply_decision_cls=id_apply.ApplyDecision,
            runbook_cls=id_apply.RunbookPayload, module=id_apply)
        assert v1, "a retained `posture` parameter must be convicted"
        convicted += 1

        # (2) `audited` retained as a permanently-false field on ApplyDecision.
        MutantApplyDecision = dataclasses.make_dataclass(
            "MutantApplyDecision", [("action", str), ("audited", bool, dataclasses.field(default=False)),
                                     ("runbook", object, dataclasses.field(default=None)),
                                     ("reason", str, dataclasses.field(default=""))])
        v2 = _dead_shape_violations(
            decide_apply_fn=id_apply.decide_apply, apply_decision_cls=MutantApplyDecision,
            runbook_cls=id_apply.RunbookPayload, module=id_apply)
        assert v2, "a retained `audited` field must be convicted"
        convicted += 1

        # (3) GENERATE_RUNBOOK left in the enum as an unreachable member.
        class MutantModule:
            GENERATE_RUNBOOK = "GENERATE_RUNBOOK"

        v3 = _dead_shape_violations(
            decide_apply_fn=id_apply.decide_apply, apply_decision_cls=id_apply.ApplyDecision,
            runbook_cls=id_apply.RunbookPayload, module=MutantModule)
        assert v3, "a retained `GENERATE_RUNBOOK` enum member must be convicted"
        convicted += 1

        # (4) the one a top-level field sweep misses: RunbookPayload.executed_by hardcoded "framework"
        #     inside the NESTED record.
        MutantRunbookPayload = dataclasses.make_dataclass(
            "MutantRunbookPayload", [("command", str), ("verify", str),
                                      ("executed_by", str, dataclasses.field(default="framework"))])
        v4 = _dead_shape_violations(
            decide_apply_fn=id_apply.decide_apply, apply_decision_cls=id_apply.ApplyDecision,
            runbook_cls=MutantRunbookPayload, module=id_apply)
        assert v4, "a retained nested `RunbookPayload.executed_by` must be convicted"
        convicted += 1

        assert convicted == 4
        print("IDAGR-1-DEADSHAPE-MUTANT-4of4-OK")

    # ── AC-IDAGR-2 — the whole table as a CLOSED enumeration, both directions in one row.
    def test_router_is_total_with_exactly_four_refusing_inputs(self):
        # direct EXECUTEs unconditionally — the default path, no second condition.
        assert _decide(["infra/vpc.tf"]).action == id_apply.EXECUTE
        # gitops VERIFY_ONLYs.
        assert _decide(["clusters/prod/app.yaml"]).action == id_apply.VERIFY_ONLY
        # (i) ambiguous, INCLUDING an empty changed_paths, refuses.
        assert _decide(["infra/vpc.tf", "clusters/prod/app.yaml"]).action == id_apply.REFUSE
        assert _decide([]).action == id_apply.REFUSE
        # (ii) an out-of-set class value refuses (monkeypatch classify_gitops for this one call).
        real_classify = id_apply.classify_gitops
        try:
            id_apply.classify_gitops = lambda *, changed_paths, infra_binding: "not-a-real-class"
            assert _decide(["infra/vpc.tf"]).action == id_apply.REFUSE
        finally:
            id_apply.classify_gitops = real_classify
        # (iii) a missing/empty required slot for the chosen branch refuses.
        no_apply = dict(_INFRA_BINDING); del no_apply["apply"]
        assert _decide(["infra/vpc.tf"], no_apply).action == id_apply.REFUSE
        no_verify = dict(_INFRA_BINDING); del no_verify["verify"]
        assert _decide(["infra/vpc.tf"], no_verify).action == id_apply.REFUSE
        assert _decide(["clusters/prod/app.yaml"], no_verify).action == id_apply.REFUSE
        # (iv) a malformed gitops_paths refuses — an EMPTY list is asserted WELL-FORMED in this SAME
        # row, so the new condition cannot be implemented as "non-empty required".
        empty_paths = dict(_INFRA_BINDING, gitops_paths=[])
        assert _decide(["infra/vpc.tf"], empty_paths).action == id_apply.EXECUTE  # empty is well-formed.
        absent_paths = {"apply": _FROZEN_APPLY, "verify": _FROZEN_VERIFY}
        assert _decide(["infra/vpc.tf"], absent_paths).action == id_apply.REFUSE
        not_a_list = dict(_INFRA_BINDING, gitops_paths="gitops/apps/**")
        assert _decide(["infra/vpc.tf"], not_a_list).action == id_apply.REFUSE
        blank_member = dict(_INFRA_BINDING, gitops_paths=["clusters/**", ""])
        assert _decide(["infra/vpc.tf"], blank_member).action == id_apply.REFUSE
        print("IDAGR-2-ROUTER-TOTAL-OK")

    # ── AC-IDAGR-2 MALFORMED-GLOBS MUTANT — the one-character YAML slip: gitops_paths as a bare STRING.
    # Pre-fix _gitops_globs(:88-94) silently yields () and classify_gitops then returns `direct` for
    # every non-empty change, so a change under the string-typo'd glob would EXECUTE and race ArgoCD.
    def test_malformed_gitops_paths_routed_to_execute_is_convicted(self):
        typo_binding = dict(_INFRA_BINDING, gitops_paths="gitops/apps/**")

        def mutant_decide_apply_no_iv_check(*, changed_paths, infra_binding):
            # Reproduces the pre-fix behaviour: skip condition (iv) and derive the class directly —
            # _gitops_globs silently yields () for a non-list, so classify_gitops returns `direct`.
            gitops_class = id_apply.classify_gitops(changed_paths=changed_paths, infra_binding=infra_binding)
            if gitops_class == id_apply.DIRECT:
                return id_apply.ApplyDecision(
                    action=id_apply.EXECUTE,
                    runbook=id_apply.RunbookPayload(
                        command=infra_binding["apply"], verify=infra_binding["verify"]),
                    reason="mutant: no condition (iv) check")
            return id_apply.ApplyDecision(action=id_apply.REFUSE, runbook=None, reason="mutant")

        # the mutant routes a GitOps-glob-shaped change to EXECUTE — the defect this atom fixes.
        assert mutant_decide_apply_no_iv_check(
            changed_paths=["gitops/apps/root.yaml"], infra_binding=typo_binding).action == id_apply.EXECUTE
        # the REAL decide_apply must REFUSE the same malformed input — this is the conviction.
        assert id_apply.decide_apply(
            changed_paths=["gitops/apps/root.yaml"], infra_binding=typo_binding).action == id_apply.REFUSE
        print("IDAGR-2-MALFORMED-GLOBS-MUTANT-OK")

    # ── AC-IDAGR-2 RESIDUAL-POLICY MUTANT — a well-formed `direct` change EXECUTEs under a matrix of
    # irrelevant ambient conditions, convicting any implementation that kept or reintroduced a policy
    # refusal (a prod test, an environment guard, an execution-context test).
    def test_any_surviving_policy_refusal_is_convicted(self):
        ambient_matrix = [
            {}, {"env": "production"}, {"account": "111111111111", "production": True},
            {"blast_tier": "HIGH"}, {"high_blast_acked": False}, {"approved": False},
            {"execution_context": "ci"}, os.environ,
        ]
        for ambient in ambient_matrix:
            sig = inspect.signature(id_apply.decide_apply)
            kwargs = {"changed_paths": ["infra/vpc.tf"], "infra_binding": _INFRA_BINDING}
            # decide_apply's signature has no slot for `ambient` at all — the matrix is asserted by
            # NOT being able to inject it, plus the unconditional EXECUTE below.
            assert set(sig.parameters) == {"changed_paths", "infra_binding"}
            assert id_apply.decide_apply(**kwargs).action == id_apply.EXECUTE
        print("IDAGR-2-NO-POLICY-MUTANT-OK")

    # ── AC-IDAGR-2 REASON — VERIFY_ONLY's recorded reason states controller ownership, not a restriction.
    def test_verify_only_reason_names_controller_ownership(self):
        d = _decide(["clusters/prod/app.yaml"])
        assert d.action == id_apply.VERIFY_ONLY
        reason = d.reason.lower()
        assert "controller" in reason and ("reconcil" in reason or "owns" in reason)
        assert "restrict" not in reason and "permission" not in reason
        print("IDAGR-2-REASON-OK")

    # ── AC-IDAGR-4 — never-acquire: decide_apply runs NOTHING across the full input matrix, and the
    # only mutating command any branch carries is the frozen infra_binding.apply BYTE-FOR-BYTE.
    def test_decision_runs_nothing_and_emits_only_the_frozen_apply(self):
        calls = []
        id_apply.subprocess_run_injected_for_test = lambda *a, **k: calls.append((a, k))
        try:
            sentinel_binding = dict(_INFRA_BINDING, apply="tofu apply -auto-approve # SENTINEL-9f3c")
            matrix = [
                (["infra/vpc.tf"], sentinel_binding),
                (["clusters/prod/app.yaml"], sentinel_binding),
                ([], sentinel_binding),
                (["infra/vpc.tf", "clusters/prod/app.yaml"], sentinel_binding),
                (["infra/vpc.tf"], dict(sentinel_binding, gitops_paths="not-a-list")),
            ]
            for paths, binding in matrix:
                id_apply.decide_apply(changed_paths=paths, infra_binding=binding)
            assert calls == []  # decide_apply invoked nothing that runs a command.

            d = id_apply.decide_apply(changed_paths=["infra/vpc.tf"], infra_binding=sentinel_binding)
            assert d.action == id_apply.EXECUTE
            assert d.runbook.command == sentinel_binding["apply"]  # byte-for-byte, no composition.
        finally:
            del id_apply.subprocess_run_injected_for_test
        print("IDAGR-4-PURE-FROZEN-OK")

    # ── AC-IDAGR-5 — REGRESSION FLOOR: classify_gitops's keyword-only signature, all three return
    # values, and the empty-scope guard drive unchanged.
    def test_classify_gitops_is_unchanged_4of4(self):
        sig = inspect.signature(id_apply.classify_gitops)
        assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())
        assert set(sig.parameters) == {"changed_paths", "infra_binding"}
        checked = 0
        assert _classify(["infra/vpc.tf"]) == id_apply.DIRECT; checked += 1
        assert _classify(["clusters/prod/app.yaml"]) == id_apply.GITOPS; checked += 1
        assert _classify(["infra/vpc.tf", "clusters/prod/app.yaml"]) == id_apply.AMBIGUOUS; checked += 1
        assert _classify([]) == id_apply.AMBIGUOUS; checked += 1  # the empty-scope guard.
        assert checked == 4
        print("IDAGR-5-CLASSIFIER-4of4-OK")

    # ── AC-IDAGR-6 — the procedure skill: no posture/CTX reference survives, the IAM-context statement
    # is present, and BOTH prompt-injection rules survive. Parsed by section, not a joined-text substring.
    def test_id_apply_skill_names_no_posture_and_keeps_both_injection_rules(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "skills", "id-apply", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        frontmatter, body = text.split("---", 2)[1:3]
        assert "posture" not in text.lower()
        assert "ctx-posture" not in text.lower()
        assert "ctx status" not in text.lower()
        sections = {}
        current = None
        for line in body.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        injection_section = "\n".join(sections.get("Prompt-injection discipline (load-bearing)", []))
        assert "classify_gitops" in injection_section and "RE-DERIVED" in injection_section
        assert "FROZEN" in injection_section and "infra_binding.apply" in injection_section
        procedure_text = "\n".join(sections.get("The procedure", []))
        assert "AWS" in procedure_text or "IAM" in procedure_text or "iam" in body.lower()
        assert "iam restrictions" in body.lower()
        assert "operator" in body.lower()
        print("IDAGR-6-SKILL-OK")

    # ── AC-IDAGR-9 — the gate module's OWN prose, all five regions, parsed per-region so a clean module
    # docstring cannot pass for a stale branch table.
    def test_gate_module_prose_describes_only_surviving_behaviour_5of5(self):
        regions = _module_prose_regions(id_apply)
        assert len(regions) == 5
        for name, text in regions.items():
            hits = _forbidden_prose_hits(text)
            assert hits == [], f"region {name!r} carries forbidden prose: {hits}"
        print("IDAGR-9-MODULE-PROSE-5of5-OK")

    # ── AC-IDAGR-9 STALE-DOCSTRING MUTANT — restores decide_apply's four-branch posture table verbatim
    # into its docstring while every behavioural row stays green; the AC-IDAGR-9 witness must go RED.
    def test_a_restored_posture_table_docstring_is_convicted(self):
        stale_docstring = (
            "The table:\n"
            "  (a)  posture.decision == REFUSE                              -> REFUSE (dominates).\n"
            "  (b)  gitops_class == \"gitops\" AND posture != REFUSE           -> VERIFY_ONLY.\n"
            "  (c)  posture.decision == EXECUTE AND gitops_class == \"direct\" -> EXECUTE (carries "
            "posture.audited -- True for break-glass).\n"
            "  (d)  posture.decision == GENERATE AND gitops_class == \"direct\" -> GENERATE_RUNBOOK "
            "(SoD: authorized generator != executor)."
        )
        hits = _forbidden_prose_hits(stale_docstring)
        assert hits, "a restored posture-table docstring must be convicted, not pass silently"
        # and the SAME witness reports the real module clean, proving this isn't a vacuous ban.
        assert _forbidden_prose_hits(id_apply.decide_apply.__doc__) == []
        print("IDAGR-9-STALE-DOCSTRING-MUTANT-OK")

    # ── AC-IDAGR-10 — the class is STRUCTURALLY underivable from a caller claim.
    def test_decide_apply_derives_the_class_and_offers_no_override(self):
        sig = inspect.signature(id_apply.decide_apply)
        assert set(sig.parameters) == {"changed_paths", "infra_binding"}
        assert "gitops_class" not in sig.parameters and "posture" not in sig.parameters
        # the internal call is load-bearing: pin classify_gitops to "gitops" while the caller-implied
        # class (from the paths alone) would have been "direct" -- the routing must follow the pin.
        real_classify = id_apply.classify_gitops
        try:
            id_apply.classify_gitops = lambda *, changed_paths, infra_binding: id_apply.GITOPS
            d = id_apply.decide_apply(changed_paths=["infra/vpc.tf"], infra_binding=_INFRA_BINDING)
            assert d.action == id_apply.VERIFY_ONLY
        finally:
            id_apply.classify_gitops = real_classify
        print("IDAGR-10-INTERNAL-DERIVE-OK")

    # ── AC-IDAGR-11 — BOTH directions in one row: the rendered/logged form is scrubbed; the executed
    # form (decision.runbook.command) is the frozen, UNALTERED bytes.
    def test_rendered_decision_is_scrubbed_and_executed_command_is_not(self):
        sentinel = "SENTINEL-db-pw-7f2a"
        leaky_binding = dict(_INFRA_BINDING, apply=f"TF_VAR_db_password={sentinel} tofu apply -auto-approve")
        d = id_apply.decide_apply(changed_paths=["infra/vpc.tf"], infra_binding=leaky_binding)
        assert d.action == id_apply.EXECUTE
        # the EXECUTE branch's runnable command is the frozen bytes, sentinel included, unaltered.
        assert d.runbook.command == leaky_binding["apply"]
        assert sentinel in d.runbook.command
        rendered = id_apply.render_decision(d)
        assert sentinel not in rendered["command"]
        assert sentinel not in (rendered["reason"] or "")
        print("IDAGR-11-SCRUB-RENDERED-ONLY-OK")


def _dead_shape_violations(*, decide_apply_fn, apply_decision_cls, runbook_cls, module):
    """The AC-IDAGR-1 witness: returns a list of violations (empty ⇒ clean) for exactly the four
    realistic dead-shape no-ops named in the contract. Shared between the primary AC-IDAGR-1 row and
    its mutant row so both drive the SAME check."""
    violations = []
    sig = inspect.signature(decide_apply_fn)
    if "posture" in sig.parameters:
        violations.append("decide_apply retains a `posture` parameter")
    ad_fields = {f.name for f in dataclasses.fields(apply_decision_cls)}
    if "audited" in ad_fields:
        violations.append("ApplyDecision retains an `audited` field")
    if hasattr(module, "GENERATE_RUNBOOK"):
        violations.append("module retains a `GENERATE_RUNBOOK` member")
    rb_fields = {f.name for f in dataclasses.fields(runbook_cls)}
    if "executed_by" in rb_fields:
        violations.append("RunbookPayload retains an `executed_by` field")
    return violations


_FORBIDDEN_PROSE_PHRASES = (
    "posture", "generate_runbook", "break-glass", "breakglass", "segregation of duties",
    "governs whether a prod mutation runs", "security-review surface", "ctx-posture", "ctx status",
    " sod ", "sod split", "sod:",
)


def _forbidden_prose_hits(text):
    lowered = (text or "").lower()
    return [phrase for phrase in _FORBIDDEN_PROSE_PHRASES if phrase in lowered]


def _module_prose_regions(module):
    """The five AC-IDAGR-9 regions of scripts/foundry_id_apply.py, each isolated so a clean module
    docstring cannot pass for a stale branch table hiding elsewhere."""
    src = inspect.getsource(module)
    enum_start = src.index("# The closed ApplyAction enum")
    enum_end = src.index("@dataclass", enum_start)
    return {
        "module_docstring": module.__doc__ or "",
        "enum_comments": src[enum_start:enum_end],
        "dataclass_docstrings": (
            (module.RunbookPayload.__doc__ or "") + "\n" + inspect.getsource(module.RunbookPayload)
            + "\n" + (module.ApplyDecision.__doc__ or "") + "\n" + inspect.getsource(module.ApplyDecision)
        ),
        "decide_apply_docstring": module.decide_apply.__doc__ or "",
        "decide_apply_body_comments": inspect.getsource(module.decide_apply),
    }


# ================================================================= foundry_id_alloc.py ==== #

_N_PARALLEL = 16  # >= 16 per AC-IDMUTEX-2; trimmed from the original 32 for pytest wall-time.


def _fork_allocate_into_pipe(alloc_fn, counter_file, n):
    readers, children = [], []
    for _ in range(n):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            try:
                got = alloc_fn(counter_file)
                os.write(w, f"{got}".encode())
            finally:
                os.close(w)
                os._exit(0)
        os.close(w)
        readers.append(r)
        children.append(pid)
    ids = []
    for r in readers:
        chunk = b""
        while True:
            part = os.read(r, 64)
            if not part:
                break
            chunk += part
        os.close(r)
        ids.append(int(chunk.decode().strip()))
    for pid in children:
        os.waitpid(pid, 0)
    return sorted(ids)


def _lockfree_allocate(counter_file):
    import time
    try:
        with open(counter_file, encoding="utf-8") as fh:
            raw = fh.read().strip()
        current = int(raw) if raw else 0
    except FileNotFoundError:
        current = 0
    time.sleep(0.005)
    new_value = current + 1
    with open(counter_file, "w", encoding="utf-8") as fh:
        fh.write(str(new_value))
        fh.flush()
        os.fsync(fh.fileno())
    return new_value


requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX os.fork()")


@requires_fork
class TestIdAllocationMutex:
    def test_structural_uses_flock_lock_ex(self):
        import inspect
        src = inspect.getsource(id_alloc.allocate_id)
        assert "LOCK_EX" in src and "flock" in src

    def test_held_lock_blocks_concurrent_allocation(self, tmp_path):
        import fcntl
        import time
        c1 = str(tmp_path / "ac1.counter")
        lock_path = id_alloc._lock_path(c1)
        ready_r, ready_w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(ready_r)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(ready_w, b"x")
            os.close(ready_w)
            time.sleep(0.4)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            os._exit(0)
        os.close(ready_w)
        os.read(ready_r, 1)
        os.close(ready_r)
        t0 = time.monotonic()
        got = id_alloc.allocate_id(c1)
        blocked_for = time.monotonic() - t0
        os.waitpid(pid, 0)
        assert blocked_for >= 0.25
        assert got == 1

    def test_n_parallel_zero_collision_monotonic_with_negative_control(self, tmp_path):
        c2 = str(tmp_path / "ac2.counter")
        pos_ids = _fork_allocate_into_pipe(id_alloc.allocate_id, c2, _N_PARALLEL)
        assert len(set(pos_ids)) == _N_PARALLEL
        assert pos_ids == list(range(1, _N_PARALLEL + 1))
        with open(c2, encoding="utf-8") as fh:
            persisted = int(fh.read().strip())
        assert persisted == _N_PARALLEL

        # NEGATIVE control: the SAME harness against a deliberately lock-free allocator must be ABLE
        # to collide (proves the positive result above is not vacuous).
        neg_can_collide = False
        for round_ in range(5):
            cneg = str(tmp_path / f"neg_{round_}.counter")
            neg_ids = _fork_allocate_into_pipe(_lockfree_allocate, cneg, _N_PARALLEL)
            if len(set(neg_ids)) < _N_PARALLEL:
                neg_can_collide = True
                break
        assert neg_can_collide

    def test_crash_safe_no_stale_lock(self, tmp_path):
        import fcntl
        import signal
        import time
        c3 = str(tmp_path / "ac3.counter")
        lock3 = id_alloc._lock_path(c3)
        held_r, held_w = os.pipe()
        kpid = os.fork()
        if kpid == 0:
            os.close(held_r)
            fd = os.open(lock3, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(held_w, b"x")
            os.close(held_w)
            time.sleep(60)
            os._exit(0)
        os.close(held_w)
        os.read(held_r, 1)
        os.close(held_r)
        os.kill(kpid, signal.SIGKILL)
        os.waitpid(kpid, 0)
        assert os.path.isfile(lock3)  # the sidecar lock FILE persists; only the lock released.
        t0 = time.monotonic()
        got3 = id_alloc.allocate_id(c3)
        acquired_in = time.monotonic() - t0
        assert got3 == 1 and acquired_in < 5.0

    def test_crash_between_acquire_and_write_leaves_counter_unchanged(self, tmp_path):
        import fcntl
        import signal
        c3b = str(tmp_path / "ac3b.counter")
        id_alloc._write_counter_durable(c3b, 5)
        lock3b = id_alloc._lock_path(c3b)
        rdy_r, rdy_w = os.pipe()
        cpid = os.fork()
        if cpid == 0:
            os.close(rdy_r)
            fd = os.open(lock3b, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            with open(c3b, encoding="utf-8") as fh:
                fh.read()
            os.write(rdy_w, b"x")
            os.close(rdy_w)
            import time
            time.sleep(60)
            os._exit(0)
        os.close(rdy_w)
        os.read(rdy_r, 1)
        os.close(rdy_r)
        os.kill(cpid, signal.SIGKILL)
        os.waitpid(cpid, 0)
        with open(c3b, encoding="utf-8") as fh:
            counter_after_crash = int(fh.read().strip())
        assert counter_after_crash == 5  # the ID was NOT consumed.
        next_after_crash = id_alloc.allocate_id(c3b)
        assert next_after_crash == 6


# ============================================================ id-impact-change-tier (thin) ==== #

def test_plan_model_policy_findings_importable_for_id_impact_change_tier():
    """AC-IDIMT: build-order dependency — `id-impact`'s v2 policy-risk read depends on
    `foundry_plan_model.parse_policy_findings` being present and callable; the deep behavioral
    coverage lives in TestParsePolicyFindings above."""
    assert callable(getattr(plan_model, "parse_policy_findings", None))


# ── The UNFILTERED full-suite regression row (AC-IDAGR-5's "FULL-FILE REGRESSION" checkpoint) ── #
# The frozen contract's locator for this row is `python3 -m pytest tests/test_infra_delivery.py
# tests/test_stack_profile.py -q -s` — BOTH files, no `-k`. A `-k`-filtered row cannot see a
# regression it does not select for, so the token has to be emitted by the RUN, not by a selected
# test, and only when nothing in the session failed.
#
# A session-scoped fixture teardown is the seam that works from inside a test module: pytest reads
# hooks only from conftest.py/plugins, and conftest.py is outside this atom's allowed_paths (mirrors
# tests/test_contract_authz.py's identical AC-RGR-5 row). At session teardown
# `request.session.testsfailed` reflects the WHOLE session — both files, since both are passed on one
# `pytest` invocation — so the token prints iff every test across both files passed.
@pytest.fixture(scope="session")
def _idagr_suite_green_token(request):
    yield
    if request.session.testsfailed == 0:
        print("IDAGR-SUITE-GREEN-OK")


def test_infra_delivery_suite_green_token(_idagr_suite_green_token):
    """Requests the session fixture whose TEARDOWN emits the suite token. Asserts nothing itself —
    deliberately: "the suite is green" is not knowable from inside a test, which is exactly why the
    token is emitted at session teardown instead."""
