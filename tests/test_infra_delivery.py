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
ctx_posture = load_module("scripts/foundry_ctx_posture.py", "foundry_ctx_posture")
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

_FROZEN_APPLY = "tofu apply -auto-approve"
_FROZEN_VERIFY = "tofu plan -detailed-exitcode"
_INFRA_BINDING = {"apply": _FROZEN_APPLY, "verify": _FROZEN_VERIFY, "gitops_paths": ("clusters/**", "argocd/*")}


def _posture(decision, *, audited=False):
    return ctx_posture.Posture(decision=decision, audited=audited)


def _classify(paths):
    return id_apply.classify_gitops(changed_paths=paths, infra_binding=_INFRA_BINDING)


def _decide(decision, paths, *, audited=False):
    return id_apply.decide_apply(posture=_posture(decision, audited=audited),
                                  gitops_class=_classify(paths), infra_binding=_INFRA_BINDING)


class TestIdApplyGate:
    def test_gitops_classification_rederivation(self):
        assert _classify(["infra/vpc.tf", "infra/iam.tf"]) == id_apply.DIRECT
        assert _classify(["clusters/prod/app.yaml", "argocd/root.yaml"]) == id_apply.GITOPS
        assert _classify(["infra/vpc.tf", "clusters/prod/app.yaml"]) == id_apply.AMBIGUOUS
        assert _classify([]) == id_apply.AMBIGUOUS  # empty scope is ambiguous, never a vacuous pass.

    def test_nonprod_execute_direct(self):
        d = _decide("EXECUTE", ["infra/vpc.tf"])
        assert d.action == id_apply.EXECUTE and d.audited is False
        assert d.runbook is not None and d.runbook.command == _FROZEN_APPLY

    def test_guarded_generate_runbook_is_frozen_and_no_mutating_verb_issued(self):
        d = _decide("GENERATE", ["infra/vpc.tf"])
        assert d.action == id_apply.GENERATE_RUNBOOK
        assert d.runbook.command == _FROZEN_APPLY == _INFRA_BINDING["apply"]
        assert d.runbook.verify == _INFRA_BINDING["verify"]
        assert d.runbook.executed_by == "operator"
        mutating = {"apply", "destroy", "delete", "replace", "install", "upgrade", "uninstall", "sync", "create"}
        assert not (set(d.runbook.verify.split()) & mutating)

    def test_gitops_is_verify_only_regardless_of_posture(self):
        for decision in ("EXECUTE", "GENERATE"):
            d = _decide(decision, ["clusters/prod/app.yaml"])
            assert d.action == id_apply.VERIFY_ONLY and d.runbook is None

    def test_refuse_posture_dominates(self):
        assert _decide("REFUSE", ["infra/vpc.tf"]).action == id_apply.REFUSE
        assert _decide("REFUSE", ["clusters/prod/app.yaml"]).action == id_apply.REFUSE

    def test_ambiguous_scope_fails_closed_even_with_execute_posture(self):
        assert _decide("EXECUTE", ["infra/vpc.tf", "clusters/prod/app.yaml"]).action == id_apply.REFUSE
        assert _decide("EXECUTE", []).action == id_apply.REFUSE

    def test_breakglass_execute_carries_audited_flag(self):
        d = _decide("EXECUTE", ["infra/vpc.tf"], audited=True)
        assert d.action == id_apply.EXECUTE and d.audited is True

    def test_decision_is_a_record_not_a_bare_enum(self):
        d = _decide("EXECUTE", ["infra/vpc.tf"])
        assert isinstance(d, id_apply.ApplyDecision)
        assert hasattr(d, "action") and hasattr(d, "audited") and hasattr(d, "runbook")
        g = _decide("GENERATE", ["infra/vpc.tf"])
        assert isinstance(g.runbook, id_apply.RunbookPayload)

    def test_fix_and_rerun_flips_from_refuse_to_execute(self):
        before = _decide("REFUSE", ["infra/vpc.tf", "clusters/prod/app.yaml"])
        after = _decide("EXECUTE", ["infra/vpc.tf"])
        assert before.action == id_apply.REFUSE
        assert after.action == id_apply.EXECUTE


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
