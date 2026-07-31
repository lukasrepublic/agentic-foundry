"""tests/test_session.py — converted from scripts/foundry_checks/session-posture.py.

Ports the AC-POS-1..8 / AC-AFP-1/-4/-6 real behavioral assertions the drop-in selftest drove
directly against `scripts/foundry_session_mode.py` (the session posture store/resolver) — the
CLI scaffolding (argparse, sentinel-token printing, doctor auto-discovery) is dropped; the
throwaway-tempdir fixtures and computed assertions are kept as-is.
"""
import os

import pytest

from conftest import load_module

fsm = load_module("scripts/foundry_session_mode.py", "foundry_session_mode")

SID_A = "0927afe0-4ed1-4401-abb6-85fa7c380564"
SID_B = "12340000-0000-4000-8000-000000000000"
SID_C = "abcdefab-cdef-4bcd-8bcd-abcdefabcdef"


def test_default_factory_no_cross_session_leak(tmp_path):
    assert fsm.resolve(str(tmp_path), SID_A) == fsm.DEFAULT_MODE
    assert fsm.resolve(str(tmp_path), SID_B) == fsm.DEFAULT_MODE


def test_closed_set_fail_closed(tmp_path):
    rej = fsm.set_mode(str(tmp_path), "bogus-mode", session_id=SID_A)
    assert rej["ok"] is False
    assert "factory" in rej["reason"] and "noninteractive" in rej["reason"] and "interactive" in rej["reason"]
    assert fsm.resolve(str(tmp_path), SID_A) == fsm.DEFAULT_MODE


def test_set_persist_idempotent_no_leak(tmp_path):
    root = str(tmp_path)
    r1 = fsm.set_mode(root, "noninteractive", session_id=SID_A)
    assert r1["ok"] is True and r1["mode"] == "noninteractive"
    assert fsm.resolve(root, SID_A) == "noninteractive"
    r2 = fsm.set_mode(root, "noninteractive", session_id=SID_A)  # idempotent re-issue
    assert r2["ok"] is True and r2["mode"] == "noninteractive"
    assert fsm.resolve(root, SID_A) == "noninteractive"
    assert fsm.resolve(root, SID_B) == fsm.DEFAULT_MODE  # not leaked


def test_writeside_reject_closed_no_fallback(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    no_id = fsm.set_mode(root, "interactive", session_id=None)
    assert no_id["ok"] is False and "mode" not in no_id
    assert not os.path.isfile(fsm._store_path(root, "unknown"))
    bad_id = fsm.set_mode(root, "interactive", session_id="not-a-uuid")
    assert bad_id["ok"] is False


def test_failsafe_default_on_invalid_or_unreadable(tmp_path):
    import json
    root = str(tmp_path)
    os.makedirs(fsm._store_dir(root), exist_ok=True)
    with open(fsm._store_path(root, SID_B), "w", encoding="utf-8") as f:
        json.dump({"session_id": SID_B, "mode": "not-a-real-mode"}, f)
    assert fsm.resolve(root, SID_B) == fsm.DEFAULT_MODE

    with open(fsm._store_path(root, SID_B), "w", encoding="utf-8") as f:
        f.write("not json {{")
    assert fsm.resolve(root, SID_B) == fsm.DEFAULT_MODE


def test_fork_policy_default_park(tmp_path):
    assert fsm.resolve_fork_policy(str(tmp_path), SID_C) == fsm.DEFAULT_FORK_POLICY


def test_fork_policy_closed_set_fail_closed(tmp_path):
    root = str(tmp_path)
    rej = fsm.set_fork_policy(root, "bogus-policy", session_id=SID_C)
    assert rej["ok"] is False
    assert "park" in rej["reason"] and "two-way-auto" in rej["reason"]
    assert fsm.resolve_fork_policy(root, SID_C) == fsm.DEFAULT_FORK_POLICY


def test_fork_policy_set_resolve_round_trip_persists_no_clobber(tmp_path):
    root = str(tmp_path)
    r = fsm.set_fork_policy(root, "two-way-auto", session_id=SID_C)
    assert r["ok"] is True and r["fork_policy"] == "two-way-auto"
    assert fsm.resolve_fork_policy(root, SID_C) == "two-way-auto"
    assert fsm.resolve_fork_policy(root, SID_C) == "two-way-auto"
    # sibling `mode` field untouched by the fork-policy write (merge-write, no clobber).
    assert fsm.resolve(root, SID_C) == fsm.DEFAULT_MODE
