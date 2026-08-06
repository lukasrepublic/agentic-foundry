"""tests/test_tier_preflight.py — feat-foundry-tier-a-ruleset-as-code, AC-TARC-1..15.

EVIDENCE RULE (binding, restated from the spec/contract): every test here either (a) drives
`scripts/foundry_tier_preflight.py` as a REAL SUBPROCESS with the shared fixture `gh` binary
(`tests/fixtures/gh-stub/gh`) placed FIRST on PATH, the scenario selected entirely by
`GH_STUB_*` env vars, asserting on exit code + the final stdout line + the stub's `GH_STUB_LOG`
invocation journal; (b) reads `rulesets/tier-a-merge-floor.json` from disk as shipped; or
(c) imports the shipped module and calls its own `resolve_verdict_line_and_exit` function
directly (AC-TARC-10 row iv). Nothing here re-implements the verdict logic, the cause
precedence, or the plan-limitation classifier in Python and asserts against its own copy.

Test-naming convention (load-bearing for the sibling acceptance-contract's checkpoints): every
test function carries a ZERO-PADDED two-digit AC token (`test_tarc_01_...`), so
`pytest -k tarc_NN` selects exactly that criterion's tests. AC-TARC-2 is retired (merged into
AC-TARC-1); AC-TARC-12 (docs) and AC-TARC-15 (security-path widening, driven by the existing
tests/test_btb_gates.py harness) carry no pytest surface here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "foundry_tier_preflight.py"
TEMPLATE_PATH = REPO_ROOT / "rulesets" / "tier-a-merge-floor.json"
GH_STUB_DIR = Path(__file__).resolve().parent / "fixtures" / "gh-stub"

REPO = "acme/widgets"
TEMPLATE_NAME = "tier-a-merge-floor"

_VERDICT_LINE_RE = re.compile(r"^(TIER-A|TIER-B \([a-z-]+\)|PREFLIGHT-ERROR):")

PLAN_TEXT = "plan-limitation: server-side enforcement requires a public repository"
ENABLE_TEXT = "enable enforcement in settings"
CONTEXT_TEXT = "check the context name"
NO_RULESET_TEXT = "re-run with --apply"


# --------------------------------------------- helpers ---------------------------------------- #

def _run_cli(tmp_path, args, env_extra=None, *, on_path=True, timeout=30):
    env = dict(os.environ)
    if on_path:
        env["PATH"] = f"{GH_STUB_DIR}{os.pathsep}{env.get('PATH', '')}"
    else:
        # Strip every PATH entry that carries a `gh` executable (AC-TARC-7 row v): no gh
        # resolvable at all, not even a real system install.
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and not (Path(p) / "gh").exists()]
        env["PATH"] = os.pathsep.join(parts)
    log_path = tmp_path / "gh.log"
    env["GH_STUB_LOG"] = str(log_path)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True, text=True, env=env, timeout=timeout,
    )
    journal = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return proc, journal


def _final_line(proc) -> str:
    out = proc.stdout.rstrip("\n")
    return out.splitlines()[-1] if out else ""


def _readonly_env(default_branch="main", probe_contexts=None, extra=None, with_pull_request=True):
    # A genuinely Tier-A-protected branch carries BOTH the status-check rule and an enforced
    # pull_request rule (the shipped template ships both) — checks gate a PR, not a direct push,
    # so the amended AC-TARC-5 requires both. `with_pull_request=False` is the negative control.
    probe_rules = []
    if probe_contexts is not None:
        probe_rules = [{
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": c} for c in probe_contexts]},
        }]
        if with_pull_request:
            probe_rules.append({"type": "pull_request", "parameters": {}})
    env = {
        "GH_STUB_REPO_BODY": json.dumps({"default_branch": default_branch}),
        "GH_STUB_PROBE_BODY": json.dumps(probe_rules),
        "GH_STUB_RULESETS_PAGE1": "",
    }
    if extra:
        env.update(extra)
    return env


def _apply_env(extra=None):
    env = {
        "GH_STUB_REPO_BODY": json.dumps({"default_branch": "main"}),
        "GH_STUB_PROBE_BODY": json.dumps([]),
        "GH_STUB_RULESETS_PAGE1": "",
        "GH_STUB_CREATE_BODY": json.dumps({"id": 1, "name": TEMPLATE_NAME}),
    }
    if extra:
        env.update(extra)
    return env


def _apply_env_403(stderr_msg, extra=None):
    env = _apply_env({
        "GH_STUB_CREATE_EXIT": "1",
        "GH_STUB_CREATE_STDERR": stderr_msg,
        "GH_STUB_CREATE_BODY": json.dumps({"message": stderr_msg}),
    })
    if extra:
        env.update(extra)
    return env


def _call_stub(args, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run([str(GH_STUB_DIR / "gh"), *args], capture_output=True, text=True, env=env, timeout=10)


def _load_module():
    name = "foundry_tier_preflight_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass type-hint resolution needs the module registered
    spec.loader.exec_module(mod)
    return mod


def _load_template() -> dict:
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _contains_key(obj, key) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_contains_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(v, key) for v in obj)
    return False


# ============================================ AC-TARC-1 ======================================= #
# The shipped template: create-shape + the merge-floor rules it must carry (5 rows).

def test_tarc_01_parses_and_carries_create_shape_fields():
    tpl = _load_template()
    assert tpl["name"]
    assert tpl["target"] == "branch"
    assert tpl["enforcement"] == "active"
    assert isinstance(tpl["rules"], list) and tpl["rules"]
    assert tpl["bypass_actors"] == []


def test_tarc_01_ref_name_condition_targets_default_branch_only():
    tpl = _load_template()
    ref_name = tpl["conditions"]["ref_name"]
    assert ref_name["include"] == ["~DEFAULT_BRANCH"]
    assert ref_name["exclude"] == []


def test_tarc_01_carries_zero_read_only_response_shape_keys():
    tpl = _load_template()
    for key in ("id", "node_id", "source", "created_at", "updated_at", "_links"):
        assert not _contains_key(tpl, key), f"read-only response-shape key {key!r} present in template"


def test_tarc_01_required_status_checks_rule_carries_one_placeholder_and_boolean_strict_flag():
    tpl = _load_template()
    rsc = [r for r in tpl["rules"] if r.get("type") == "required_status_checks"]
    assert len(rsc) == 1
    params = rsc[0]["parameters"]
    assert isinstance(params["strict_required_status_checks_policy"], bool)
    checks = params["required_status_checks"]
    assert len(checks) == 1
    assert checks[0]["context"] == "<MERGE-GATE-CONTEXT>"


def test_tarc_01_carries_deletion_non_fast_forward_and_zero_approval_pull_request_rules():
    tpl = _load_template()
    types = {r["type"] for r in tpl["rules"]}
    assert "deletion" in types
    assert "non_fast_forward" in types
    pr_rules = [r for r in tpl["rules"] if r.get("type") == "pull_request"]
    assert len(pr_rules) == 1
    assert pr_rules[0]["parameters"]["required_approving_review_count"] == 0


# ============================================ AC-TARC-3 ======================================= #
# Apply: template expansion, one POST, idempotent skip (4 rows).

def test_tarc_03_apply_issues_exactly_one_post_with_pinned_header(tmp_path):
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], _apply_env())
    post_lines = [ln for ln in journal.splitlines() if "-X POST" in ln]
    assert len(post_lines) == 1, journal
    assert "X-GitHub-Api-Version: 2026-03-10" in post_lines[0]
    assert f"repos/{REPO}/rulesets" in post_lines[0]


def test_tarc_03_two_context_values_expand_to_two_entries_in_supplied_order(tmp_path):
    proc, journal = _run_cli(
        tmp_path,
        ["--repo", REPO, "--context", "security-path", "--context", "spec-link", "--apply"],
        _apply_env(),
    )
    body_line = next(ln for ln in journal.splitlines() if ln.startswith("INPUT-BODY: "))
    body = json.loads(body_line[len("INPUT-BODY: "):])
    rsc = next(r for r in body["rules"] if r["type"] == "required_status_checks")
    contexts = [e["context"] for e in rsc["parameters"]["required_status_checks"]]
    assert contexts == ["security-path", "spec-link"]


def test_tarc_03_existing_ruleset_skips_post_and_still_probes(tmp_path):
    env = _apply_env({
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert "-X POST" not in journal
    assert "apply: skipped (ruleset already present)" in proc.stdout
    assert "rules/branches/" in journal


def test_tarc_03_apply_path_never_issues_put_patch_or_delete(tmp_path):
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], _apply_env())
    for verb in ("-X PUT", "-X PATCH", "-X DELETE"):
        assert verb not in journal


# ============================================ AC-TARC-4 ======================================= #
# The ambiguous 403: positive plan match -> Tier B; anything else -> loud error (3 rows).

def test_tarc_04_plan_literal_403_resolves_tier_b_plan_gated(tmp_path):
    env = _apply_env_403("gh: Upgrade your plan to enable this feature. (HTTP 403)")
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert proc.returncode == 10, proc.stdout + proc.stderr
    assert _final_line(proc).startswith("TIER-B (plan-gated):")


def test_tarc_04_permission_403_resolves_preflight_error_naming_administration_write(tmp_path):
    env = _apply_env_403("gh: Must have admin rights to Repository. (HTTP 403)")
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")
    assert "Administration (write)" in proc.stdout


def test_tarc_04_unclassifiable_403_resolves_preflight_error(tmp_path):
    env = _apply_env_403("gh: Something else entirely happened. (HTTP 403)")
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")


# ============================================ AC-TARC-5 ======================================= #
# The probe is the SOLE Tier A evidence (4 rows).

def test_tarc_05_probe_superset_resolves_tier_a(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link", "security-path", "extra-check"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--context", "security-path"], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _final_line(proc).startswith("TIER-A:")


def test_tarc_05_default_branch_read_from_repo_and_probed_by_that_name(tmp_path):
    env = _readonly_env(default_branch="trunk", probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert proc.returncode == 0
    assert f"repos/{REPO}/rules/branches/trunk" in journal
    assert f"repos/{REPO}/rules/branches/main" not in journal


def test_tarc_05_superset_without_pull_request_rule_is_not_tier_a(tmp_path):
    """NEGATIVE CONTROL (amendment): enforced status checks WITHOUT an enforced pull_request
    rule must never yield TIER-A — checks gate a pull request, they do not gate a direct push."""
    env = _readonly_env(probe_contexts=["spec-link", "security-path"], with_pull_request=False)
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--context", "security-path"], env)
    final = _final_line(proc)
    assert not final.startswith("TIER-A"), proc.stdout
    assert "pull-request-missing" in final, final
    assert "do not gate a direct push" in proc.stdout


def test_tarc_05_reserved_char_default_branch_is_percent_encoded(tmp_path):
    """NEGATIVE CONTROL (amendment): a server-supplied default_branch carrying a reserved
    character must be percent-encoded so the probe cannot be truncated onto another branch."""
    env = _readonly_env(default_branch="release#1", probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert f"repos/{REPO}/rules/branches/release%231" in journal, journal
    assert f"repos/{REPO}/rules/branches/release#1" not in journal


def _assert_repo_refused_pre_flight(tmp_path, bad):
    proc, journal = _run_cli(tmp_path, ["--repo", bad, "--context", "spec-link"],
                             _readonly_env(probe_contexts=["spec-link"]))
    assert proc.returncode == 1, (bad, proc.stdout)
    assert "PREFLIGHT-ERROR" in proc.stdout, (bad, proc.stdout)
    assert journal.strip() == "", (bad, journal)


def test_tarc_07_repo_with_traversal_segment_refused_before_any_api_call(tmp_path):
    """--repo reaches an -X POST path; `..` could retarget the write. Zero api calls."""
    _assert_repo_refused_pre_flight(tmp_path, "acme/widgets/../other")


def test_tarc_07_repo_with_reserved_char_refused_before_any_api_call(tmp_path):
    _assert_repo_refused_pre_flight(tmp_path, "acme/wid#gets")


def test_tarc_07_repo_with_extra_segment_refused_before_any_api_call(tmp_path):
    _assert_repo_refused_pre_flight(tmp_path, "acme/widgets/extra")


def test_tarc_05_probe_missing_one_context_is_not_tier_a(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--context", "security-path"], env)
    assert proc.returncode != 0
    assert not _final_line(proc).startswith("TIER-A")


def test_tarc_05_byte_exact_comparison_rejects_case_and_whitespace_near_misses(tmp_path):
    for near_miss in ("Spec-Link", " spec-link"):
        env = _readonly_env(probe_contexts=[near_miss])
        proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
        assert not _final_line(proc).startswith("TIER-A"), f"near-miss {near_miss!r} incorrectly resolved TIER-A"


# ============================================ AC-TARC-6 ======================================= #
# Tier B: a STRICT PARTITION by declared precedence, each cause with ITS OWN remediation (7 rows).

def test_tarc_06_cause_plan_gated_emits_plan_limitation_text(tmp_path):
    env = _apply_env_403("gh: Upgrade your plan to enable this feature. (HTTP 403)")
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert _final_line(proc).startswith("TIER-B (plan-gated):")
    assert PLAN_TEXT in proc.stdout


def test_tarc_06_cause_evaluate_emits_enable_enforcement_text(tmp_path):
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "evaluate"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (evaluate):")
    assert ENABLE_TEXT in proc.stdout


def test_tarc_06_cause_disabled_emits_enable_enforcement_text(tmp_path):
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "disabled"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (disabled):")
    assert ENABLE_TEXT in proc.stdout


def test_tarc_06_cause_created_not_enforced_emits_plan_limitation_text(tmp_path):
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (created-not-enforced):")
    assert PLAN_TEXT in proc.stdout


def test_tarc_06_cause_context_missing_emits_context_text_and_both_sets_not_plan_text(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"], extra={"GH_STUB_RULESETS_PAGE1": ""})
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--context", "security-path"], env)
    assert _final_line(proc).startswith("TIER-B (context-missing):")
    assert CONTEXT_TEXT in proc.stdout
    assert "spec-link" in proc.stdout and "security-path" in proc.stdout
    assert PLAN_TEXT not in proc.stdout


def test_tarc_06_cause_pull_request_missing_names_its_own_remediation(tmp_path):
    """Cause row 6 (amendment): contexts satisfied but no enforced pull_request rule. The
    regression this guards is that the state used to fall through to `no-ruleset` and deny the
    existence of a ruleset the probe had just returned."""
    env = _readonly_env(probe_contexts=["spec-link"], with_pull_request=False)
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (pull-request-missing):"), proc.stdout
    assert "do not gate a direct push" in proc.stdout
    assert NO_RULESET_TEXT not in proc.stdout
    assert PLAN_TEXT not in proc.stdout


def test_tarc_06_cause_no_ruleset_emits_reapply_text_not_plan_text(tmp_path):
    env = _readonly_env(extra={"GH_STUB_RULESETS_PAGE1": ""})
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (no-ruleset):")
    assert NO_RULESET_TEXT in proc.stdout
    assert PLAN_TEXT not in proc.stdout


def test_classic_branch_protection_is_not_reported_as_no_ruleset(tmp_path):
    """No ruleset, but `main` IS protected the classic way.

    Before this, the branch-rules probe returned `[]` for classic protection and the verdict fell
    through to `no-ruleset` — indistinguishable from a branch with NO protection at all. That was a
    false negative about the merge floor, observed live on this repository 2026-08-04 (`main` was
    `protected=true` with six required contexts while the probe read empty). Still TIER-B, because
    read-only we can learn THAT the branch is protected but not WHICH contexts it requires — and
    claiming TIER-A on "something is protecting this" is the overclaim the tool exists to prevent.
    """
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": "",
        "GH_STUB_BRANCH_BODY": json.dumps({"name": "main", "protected": True}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (classic-protection):")
    assert "IS protected by classic branch protection" in proc.stdout
    # The OLD remediation sentence must be gone. Asserted on the full sentence, not on the
    # NO_RULESET_TEXT fragment ("re-run with --apply"), which the new remediation legitimately
    # contains too — migrating to a ruleset is still the recommended fix.
    assert "no ruleset carrying the template's name exists" not in proc.stdout
    # And the final line must not read as "nothing is enforced", which is the misreading this
    # whole cause exists to prevent.
    assert "ruleset NOT enforced" not in _final_line(proc)
    # The disambiguation must use the READ-ONLY branch endpoint, never the admin-only
    # /protection sub-resource — the tool's least-privilege promise is load-bearing.
    assert "/protection" not in journal


def test_unprotected_branch_still_reports_no_ruleset(tmp_path):
    """The negative control for the row above: `protected: false` must NOT borrow the new cause."""
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": "",
        "GH_STUB_BRANCH_BODY": json.dumps({"name": "main", "protected": False}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (no-ruleset):")
    assert NO_RULESET_TEXT in proc.stdout


def test_tarc_06_overlapping_input_precedence_prefers_created_not_enforced_over_context_missing(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"], extra={
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--context", "security-path"], env)
    assert _final_line(proc).startswith("TIER-B (created-not-enforced):")
    assert proc.stdout.count("TIER-B (") == 1
    assert PLAN_TEXT in proc.stdout
    assert CONTEXT_TEXT not in proc.stdout


# ============================================ AC-TARC-7 ======================================= #
# Fail-closed on every failure mode, INCLUDING a hang (8 rows).

def test_tarc_07_probe_nonzero_exit_resolves_preflight_error(tmp_path):
    env = _readonly_env()
    env["GH_STUB_PROBE_EXIT"] = "1"
    env["GH_STUB_PROBE_STDERR"] = "gh: unexpected error"
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")


def test_tarc_07_repo_404_resolves_preflight_error(tmp_path):
    env = _readonly_env()
    env["GH_STUB_REPO_EXIT"] = "1"
    env["GH_STUB_REPO_STDERR"] = "gh: Not Found (HTTP 404)"
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")
    assert "404" in proc.stdout


def test_tarc_07_probe_5xx_resolves_preflight_error(tmp_path):
    env = _readonly_env()
    env["GH_STUB_PROBE_EXIT"] = "1"
    env["GH_STUB_PROBE_STDERR"] = "gh: Internal Server Error (HTTP 502)"
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert proc.returncode == 1
    assert "502" in proc.stdout


def test_tarc_07_malformed_json_body_resolves_preflight_error(tmp_path):
    env = _readonly_env()
    env["GH_STUB_PROBE_BODY"] = "{not-json"
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")


def test_tarc_07_no_gh_on_path_resolves_preflight_error(tmp_path):
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], {}, on_path=False)
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")


def test_tarc_07_stall_on_repo_read_times_out_under_declared_bound(tmp_path):
    env = _readonly_env()
    env["GH_STUB_SLEEP_SECONDS"] = "5"
    env["GH_STUB_SLEEP_MATCH"] = f"repos/{REPO}"
    start = time.time()
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--timeout", "1"], env, timeout=20)
    elapsed = time.time() - start
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")
    assert f"repos/{REPO}" in proc.stdout
    assert elapsed < 4, f"took {elapsed}s — the declared timeout bound was not honored"


def test_tarc_07_stall_on_rulesets_cause_read_times_out_under_declared_bound(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"])
    env["GH_STUB_SLEEP_SECONDS"] = "5"
    env["GH_STUB_SLEEP_MATCH"] = f"repos/{REPO}/rulesets"
    start = time.time()
    proc, journal = _run_cli(
        tmp_path,
        ["--repo", REPO, "--context", "spec-link", "--context", "security-path", "--timeout", "1"],
        env, timeout=20,
    )
    elapsed = time.time() - start
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")
    assert elapsed < 4, f"took {elapsed}s — the declared timeout bound was not honored"


def test_tarc_07_stall_on_probe_times_out_under_declared_bound(tmp_path):
    env = _readonly_env()
    env["GH_STUB_SLEEP_SECONDS"] = "5"
    env["GH_STUB_SLEEP_MATCH"] = f"repos/{REPO}/rules/branches/main"
    start = time.time()
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--timeout", "1"], env, timeout=20)
    elapsed = time.time() - start
    assert proc.returncode == 1
    assert _final_line(proc).startswith("PREFLIGHT-ERROR:")
    assert elapsed < 4, f"took {elapsed}s — the declared timeout bound was not honored"


# ============================================ AC-TARC-8 ======================================= #
# The anti-inference sweep — every "looks capable" signal, probe empty, never TIER-A (5 rows).

def test_tarc_08_create_201_alone_is_not_tier_a(tmp_path):
    env = _apply_env()
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert not _final_line(proc).startswith("TIER-A")


def test_tarc_08_owner_plan_team_alone_is_not_tier_a(tmp_path):
    env = _readonly_env()
    env["GH_STUB_REPO_BODY"] = json.dumps({"default_branch": "main", "owner": {"plan": {"name": "team"}}})
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert not _final_line(proc).startswith("TIER-A")


def test_tarc_08_visibility_public_alone_is_not_tier_a(tmp_path):
    env = _readonly_env()
    env["GH_STUB_REPO_BODY"] = json.dumps({"default_branch": "main", "visibility": "public"})
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert not _final_line(proc).startswith("TIER-A")


def test_tarc_08_listed_ruleset_active_alone_is_not_tier_a(tmp_path):
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert not _final_line(proc).startswith("TIER-A")


def test_tarc_08_compound_all_four_signals_together_is_still_not_tier_a(tmp_path):
    env = _apply_env({
        "GH_STUB_REPO_BODY": json.dumps({
            "default_branch": "main", "visibility": "public", "owner": {"plan": {"name": "team"}},
        }),
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert not _final_line(proc).startswith("TIER-A")


# ============================================ AC-TARC-9 ======================================= #
# Preflight without apply is read-only, and the token requirement is stated upfront (3 rows).

def test_tarc_09_readonly_run_issues_no_write_verbs(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    for verb in ("-X POST", "-X PUT", "-X PATCH", "-X DELETE"):
        assert verb not in journal


def test_tarc_09_readonly_run_still_resolves_a_verdict(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert proc.returncode == 0
    assert _final_line(proc).startswith("TIER-A:")


def test_tarc_09_help_states_readonly_suffices_and_apply_needs_administration_write():
    proc = subprocess.run([sys.executable, str(SCRIPT_PATH), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "read-only" in proc.stdout.lower()
    assert "Administration (write)" in proc.stdout
    assert "--apply" in proc.stdout


# ============================================ AC-TARC-10 ====================================== #
# One machine-greppable verdict line, from one mapping (4 rows).

def test_tarc_10_final_line_matches_anchored_verdict_form_for_every_vocabulary_member(tmp_path):
    cases = [
        _readonly_env(probe_contexts=["spec-link"]),
        _readonly_env(extra={"GH_STUB_RULESETS_PAGE1": ""}),
        {"GH_STUB_REPO_EXIT": "1", "GH_STUB_REPO_STDERR": "gh: Not Found (HTTP 404)"},
    ]
    for env in cases:
        proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
        assert _VERDICT_LINE_RE.match(_final_line(proc)), proc.stdout


def test_tarc_10_exactly_one_verdict_token_line_initial_per_run(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    matches = [ln for ln in proc.stdout.splitlines() if _VERDICT_LINE_RE.match(ln)]
    assert len(matches) == 1


def test_tarc_10_nothing_after_the_final_verdict_line(tmp_path):
    env = _readonly_env(probe_contexts=["spec-link"])
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    lines = proc.stdout.rstrip("\n").splitlines()
    assert _VERDICT_LINE_RE.match(lines[-1])
    assert proc.stderr == ""  # gh's own stderr is captured into a variable, never re-emitted


def test_tarc_10_one_mapping_function_agrees_with_the_subprocess(tmp_path):
    mod = _load_module()
    assert mod.resolve_verdict_line_and_exit("TIER-A") == ("TIER-A", 0)
    assert mod.resolve_verdict_line_and_exit("PREFLIGHT-ERROR") == ("PREFLIGHT-ERROR", 1)
    expected_prefix, expected_code = mod.resolve_verdict_line_and_exit("TIER-B", "no-ruleset")
    assert (expected_prefix, expected_code) == ("TIER-B (no-ruleset)", 10)

    env = _readonly_env(extra={"GH_STUB_RULESETS_PAGE1": ""})
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    line = _final_line(proc)
    assert line.startswith(expected_prefix + ":")
    assert proc.returncode == expected_code


# ============================================ AC-TARC-11 ====================================== #
# The invocation parameters are pinned single literals (3 rows).

def test_tarc_11_api_version_literal_is_a_single_module_level_constant():
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert src.count("2026-03-10") == 1
    assert re.search(r'^API_VERSION = "2026-03-10"', src, re.MULTILINE)


def test_tarc_11_timeout_default_is_a_single_constant_and_override_applies_for_the_run(tmp_path):
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    assert len(re.findall(r"^GH_TIMEOUT_SECONDS = \d+", src, re.MULTILINE)) == 1

    env = _readonly_env()
    env["GH_STUB_SLEEP_SECONDS"] = "5"
    env["GH_STUB_SLEEP_MATCH"] = f"repos/{REPO}"
    start = time.time()
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--timeout", "1"], env, timeout=20)
    elapsed = time.time() - start
    assert proc.returncode == 1
    assert elapsed < 4, "the --timeout override was not honored for this run's calls"


def test_tarc_11_every_journaled_invocation_carries_the_pinned_header(tmp_path):
    env = _apply_env()
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    api_lines = [ln for ln in journal.splitlines() if ln.startswith("api ")]
    assert api_lines, journal
    for ln in api_lines:
        assert "X-GitHub-Api-Version: 2026-03-10" in ln


# ============================================ AC-TARC-13 ====================================== #
# The shared gh fixture stays additive (2 rows).

def test_tarc_13_api_branch_dispatches_on_request_path():
    probe = _call_stub(
        ["api", f"repos/{REPO}/rules/branches/main", "-H", "X-GitHub-Api-Version: 2026-03-10"],
        {"GH_STUB_PROBE_BODY": '[{"marker":"probe-response"}]'},
    )
    repo = _call_stub(
        ["api", f"repos/{REPO}", "-H", "X-GitHub-Api-Version: 2026-03-10"],
        {"GH_STUB_REPO_BODY": '{"marker":"repo-response"}'},
    )
    assert "probe-response" in probe.stdout and "repo-response" not in probe.stdout
    assert "repo-response" in repo.stdout and "probe-response" not in repo.stdout


def test_tarc_13_pulls_files_request_still_emits_shipped_fixture_behaviour():
    proc = _call_stub(
        ["api", "--paginate", f"repos/{REPO}/pulls/7/files", "-q", ".[].filename"],
        {"GH_STUB_PR_FILES": "a.py\nb.py", "GH_STUB_API_EXIT": "0"},
    )
    assert proc.stdout.strip() == "a.py\nb.py"
    assert proc.returncode == 0


# ============================================ AC-TARC-14 ====================================== #
# Every ruleset-list read is paginated (3 rows).

def test_tarc_14_every_rulesets_request_is_paginated(tmp_path):
    env = _apply_env({"GH_STUB_RULESETS_PAGE1": ""})
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    rulesets_lines = [
        ln for ln in journal.splitlines()
        if ln.startswith("api ") and f"repos/{REPO}/rulesets" in ln and "-X POST" not in ln
    ]
    assert rulesets_lines, journal
    for ln in rulesets_lines:
        assert "--paginate" in ln


def test_tarc_14_ruleset_on_page_2_resolves_created_not_enforced_not_no_ruleset(tmp_path):
    env = _readonly_env(extra={
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": "unrelated-ruleset", "enforcement": "active"}),
        "GH_STUB_RULESETS_PAGE2": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link"], env)
    assert _final_line(proc).startswith("TIER-B (created-not-enforced):")
    assert "no-ruleset" not in _final_line(proc)


def test_tarc_14_page_2_ruleset_on_apply_path_skips_duplicate_post(tmp_path):
    env = _apply_env({
        "GH_STUB_RULESETS_PAGE1": json.dumps({"name": "unrelated-ruleset", "enforcement": "active"}),
        "GH_STUB_RULESETS_PAGE2": json.dumps({"name": TEMPLATE_NAME, "enforcement": "active"}),
    })
    proc, journal = _run_cli(tmp_path, ["--repo", REPO, "--context", "spec-link", "--apply"], env)
    assert "-X POST" not in journal
    assert "apply: skipped (ruleset already present)" in proc.stdout
