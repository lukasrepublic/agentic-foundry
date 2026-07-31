"""tests/test_ctx_posture.py — converted from scripts/foundry_checks/ctx-posture.py.

Ports the real behavioral assertions over `scripts/foundry_ctx_posture.py`'s pure,
total, fail-closed `resolve_posture` table + the fail-closed `probe_ctx` parser —
REFUSE dominates, the mislabelled-prod cross-check, break-glass audited=True, and
probe-side fail-closed sentinels on every failure mode.
"""
from conftest import load_module

cp = load_module("scripts/foundry_ctx_posture.py", "foundry_ctx_posture")


def _state(**over):
    base = dict(reachable=True, active_env="dev", production_flag=False,
                guard_state="ON", stale_state="OK", detail="ok")
    base.update(over)
    return cp.CtxState(**base)


def test_unreachable_refuses():
    p = cp.resolve_posture(cp.CtxState(reachable=False, detail="down"))
    assert p.decision == cp.REFUSE


def test_stale_refuses():
    p = cp.resolve_posture(_state(stale_state="STALE"))
    assert p.decision == cp.REFUSE


def test_unknown_stale_value_refuses_no_fail_open():
    p = cp.resolve_posture(_state(stale_state="WEIRD"))
    assert p.decision == cp.REFUSE


def test_nonprod_execute():
    p = cp.resolve_posture(_state(production_flag=False, active_env="dev"))
    assert p.decision == cp.EXECUTE
    assert p.audited is False


def test_mislabelled_prod_cross_check_generates_not_executes():
    # production_flag says non-prod but active_env is a prod token -> GENERATE, never EXECUTE.
    p = cp.resolve_posture(_state(production_flag=False, active_env="prod-us"))
    assert p.decision == cp.GENERATE


def test_staging_is_not_a_prod_token():
    p = cp.resolve_posture(_state(production_flag=False, active_env="staging"))
    assert p.decision == cp.EXECUTE


def test_guarded_prod_generates():
    p = cp.resolve_posture(_state(production_flag=True, active_env="prod", guard_state="ON"))
    assert p.decision == cp.GENERATE
    assert p.audited is False


def test_breakglass_prod_executes_audited():
    p = cp.resolve_posture(_state(production_flag=True, active_env="prod", guard_state="OFF"))
    assert p.decision == cp.EXECUTE
    assert p.audited is True


def test_prod_unrecognized_guard_state_refuses():
    p = cp.resolve_posture(_state(production_flag=True, guard_state="WAT"))
    assert p.decision == cp.REFUSE


def test_probe_ctx_binary_absent_fails_closed():
    def runner(argv):
        raise FileNotFoundError("no ctx binary")

    state = cp.probe_ctx(runner=runner)
    assert state.reachable is False


def test_probe_ctx_nonzero_exit_fails_closed():
    class P:
        returncode = 1
        stdout = ""

    state = cp.probe_ctx(runner=lambda argv: P())
    assert state.reachable is False


def test_probe_ctx_unparseable_json_fails_closed():
    class P:
        returncode = 0
        stdout = "not json {{"

    state = cp.probe_ctx(runner=lambda argv: P())
    assert state.reachable is False


def test_probe_ctx_no_active_session_fails_closed():
    import json as _json

    class P:
        returncode = 0
        stdout = _json.dumps({"active_env": None})

    state = cp.probe_ctx(runner=lambda argv: P())
    assert state.reachable is False


def test_probe_ctx_only_issues_readonly_status_argv():
    seen = []

    class P:
        returncode = 0
        stdout = '{"active_env":"dev","production_flag":false,"guard_state":"ON","stale_state":"OK"}'

    def runner(argv):
        seen.append(tuple(argv))
        return P()

    state = cp.probe_ctx(runner=runner)
    assert state.reachable is True
    assert seen == [cp.CTX_STATUS_ARGV]
