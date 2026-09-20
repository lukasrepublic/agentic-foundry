"""tests/test_done_when_backfill.py — scripts/foundry-done-when-backfill.py (AC-SUB-3,
subtraction-wave, autonomy-continuation R4).

Drives the shipped CLI over throwaway fixture workspaces (never the real corpus) — fixture
contracts built inline, mirroring `tests/test_command_deck_watch.py`'s own idiom rather than a
separate fixtures/ directory, since each case needs a distinct hand-authored contract body.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

backfill = load_module("scripts/foundry-done-when-backfill.py", "foundry_done_when_backfill")

CLI_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-done-when-backfill.py")

SENTINEL = backfill.SENTINEL

TRAILER = (
    f"{SENTINEL}\n"
    "authorized:\n"
    "  operator_id: op_test\n"
    "  authorized_at: 2026-09-19T00:00:00Z\n"
    "  auth_seq: 1\n"
    "  supersedes: null\n"
    "  spec_sha256: 0000000000000000000000000000000000000000000000000000000000000000\n"
    "  contract_sha256: 0000000000000000000000000000000000000000000000000000000000000000\n"
    "  merge_autonomy_mode: lean\n"
)


def _write_contract(root, relpath, proper_body, with_trailer=True):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = proper_body
    if not text.endswith("\n"):
        text += "\n"
    if with_trailer:
        text += (
            "# /foundry:authorize appends the signed `authorized:` trailer below a sentinel.\n"
            + TRAILER
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


CONTRACT_NO_DONE_WHEN = """\
spec_ref: specs/features/foundry/x/y/feat-x.md
spec_sha256: "0000000000000000000000000000000000000000000000000000000000000000"
scope:
  allowed_paths:
    - "scripts/foo.py"
checkpoints:
  - {ac_id: AC-X-1, surface: "test:tests/test_foo.py::test_one", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
  - {ac_id: AC-X-2, surface: "test:tests/test_foo.py::test_two", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
  - {ac_id: AC-X-3, surface: "test:tests/test_bar.py", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
  - {ac_id: AC-X-4, surface: "cli:foo", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
"""

CONTRACT_ALREADY_HAS = """\
spec_ref: specs/features/foundry/x/y/feat-x.md
spec_sha256: "0000000000000000000000000000000000000000000000000000000000000000"
scope:
  allowed_paths:
    - "scripts/foo.py"
done_when:
  - "test:tests/test_existing.py"
checkpoints:
  - {ac_id: AC-X-1, surface: "test:tests/test_foo.py", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
"""

CONTRACT_NO_TEST_SURFACE = """\
spec_ref: specs/features/foundry/x/y/feat-x.md
spec_sha256: "0000000000000000000000000000000000000000000000000000000000000000"
scope:
  allowed_paths:
    - "scripts/foo.py"
checkpoints:
  - {ac_id: AC-X-1, surface: "cli:foo", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
  - {ac_id: AC-X-2, surface: "file:scripts/foo.py", locator: "true", expect: {op: matches, value: "ok", baseline: none}}
"""


# ─────────────────────────────────────────────────────────────── derivation (in-process) ==== #

def test_derives_one_locator_per_distinct_test_file_sorted():
    doc = yaml.safe_load(CONTRACT_NO_DONE_WHEN)
    locators = backfill._test_surfaces(doc)
    assert locators == ["test:tests/test_bar.py", "test:tests/test_foo.py"]


def test_ignores_non_test_surfaces():
    doc = yaml.safe_load(CONTRACT_NO_TEST_SURFACE)
    assert backfill._test_surfaces(doc) == []


def test_strips_method_granularity_to_file_granularity():
    doc = yaml.safe_load("checkpoints:\n  - {surface: \"test:tests/x.py::test_a::sub\"}\n")
    assert backfill._test_surfaces(doc) == ["test:tests/x.py"]


def test_has_done_when_detects_existing_key():
    doc = yaml.safe_load(CONTRACT_ALREADY_HAS)
    assert backfill._has_done_when(doc) is True
    doc2 = yaml.safe_load(CONTRACT_NO_DONE_WHEN)
    assert backfill._has_done_when(doc2) is False


# ────────────────────────────────────────────────────────────────────── plan_for (per file) ==== #

def test_plan_for_backfill_case(tmp_path):
    path = _write_contract(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml",
                           CONTRACT_NO_DONE_WHEN)
    plan = backfill.plan_for(path)
    assert plan.action == "backfill"
    assert plan.done_when == ["test:tests/test_bar.py", "test:tests/test_foo.py"]


def test_plan_for_already_has_case(tmp_path):
    path = _write_contract(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml",
                           CONTRACT_ALREADY_HAS)
    plan = backfill.plan_for(path)
    assert plan.action == "skip-has"


def test_plan_for_no_test_surface_case(tmp_path):
    path = _write_contract(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml",
                           CONTRACT_NO_TEST_SURFACE)
    plan = backfill.plan_for(path)
    assert plan.action == "skip-no-test"


def test_plan_for_malformed_yaml_is_skip_unreadable(tmp_path):
    path = os.path.join(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("checkpoints: [this is not: valid: yaml: at all\n")
    plan = backfill.plan_for(path)
    assert plan.action == "skip-unreadable"


def test_plan_never_parses_the_trailer_region(tmp_path):
    """A malformed TRAILER (unparseable as YAML on its own) must never block deriving/reporting
    the proper region's own done_when -- only the proper region (before the sentinel) is fed to
    the YAML parser."""
    path = os.path.join(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(CONTRACT_NO_DONE_WHEN + backfill.SENTINEL + "\nauthorized: [not: valid: yaml\n")
    plan = backfill.plan_for(path)
    assert plan.action == "backfill"
    assert plan.done_when == ["test:tests/test_bar.py", "test:tests/test_foo.py"]


# ───────────────────────────────────────────────────────── apply_backfill (byte preservation) ==== #

def test_apply_inserts_immediately_before_the_sentinel_preserving_the_trailer_byte_for_byte(tmp_path):
    path = _write_contract(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml",
                           CONTRACT_NO_DONE_WHEN)
    with open(path, encoding="utf-8") as fh:
        before = fh.read()
    trailer_before = before.split(SENTINEL, 1)[1]

    backfill.apply_backfill(path, ["test:tests/test_bar.py", "test:tests/test_foo.py"])

    with open(path, encoding="utf-8") as fh:
        after = fh.read()
    before_sentinel, sentinel_and_after = after.split(SENTINEL, 1)

    # The trailer (sentinel line onward) is untouched, byte for byte.
    assert sentinel_and_after == trailer_before
    # The done_when block landed immediately before the sentinel.
    assert before_sentinel.rstrip("\n").endswith('  - "test:tests/test_foo.py"')
    assert "done_when:" in before_sentinel
    # Every byte of the original proper region is still present (a pure insertion).
    assert before.split(SENTINEL, 1)[0] in before_sentinel
    # The rewritten file still parses as YAML (proper region).
    doc = yaml.safe_load(before_sentinel)
    assert doc["done_when"] == ["test:tests/test_bar.py", "test:tests/test_foo.py"]


def test_apply_appends_at_eof_when_no_sentinel_present(tmp_path):
    path = _write_contract(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml",
                           CONTRACT_NO_DONE_WHEN, with_trailer=False)
    with open(path, encoding="utf-8") as fh:
        before = fh.read()

    backfill.apply_backfill(path, ["test:tests/test_bar.py"])

    with open(path, encoding="utf-8") as fh:
        after = fh.read()
    assert after.startswith(before)
    assert after[len(before):] == 'done_when:\n  - "test:tests/test_bar.py"\n'


def test_apply_never_recomputes_or_touches_contract_sha256(tmp_path):
    """The trailer's contract_sha256 is the operator-proxy's own re-freeze step, in a separate
    commit -- this script must never touch it."""
    path = _write_contract(tmp_path, "specs/features/foundry/x/y/acceptance-contract.yaml",
                           CONTRACT_NO_DONE_WHEN)
    backfill.apply_backfill(path, ["test:tests/test_bar.py"])
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "contract_sha256: 0000000000000000000000000000000000000000000000000000000000000000" in text


# ────────────────────────────────────────────────────────────────────────────── CLI: --dry-run ==== #

def _run_cli(*args):
    return subprocess.run([sys.executable, CLI_PATH, *args], capture_output=True, text=True, timeout=30)


def _fixture_workspace(tmp_path):
    _write_contract(tmp_path, "specs/features/foundry/a/backfill-me/acceptance-contract.yaml",
                    CONTRACT_NO_DONE_WHEN)
    _write_contract(tmp_path, "specs/features/foundry/a/already-has/acceptance-contract.yaml",
                    CONTRACT_ALREADY_HAS)
    _write_contract(tmp_path, "specs/features/foundry/a/no-test-surface/acceptance-contract.yaml",
                    CONTRACT_NO_TEST_SURFACE)
    return tmp_path


def test_cli_dry_run_writes_nothing(tmp_path):
    ws = _fixture_workspace(tmp_path)
    target = os.path.join(ws, "specs/features/foundry/a/backfill-me/acceptance-contract.yaml")
    with open(target, encoding="utf-8") as fh:
        before = fh.read()

    r = _run_cli("--dry-run", str(ws))
    assert r.returncode == 0, r.stderr

    with open(target, encoding="utf-8") as fh:
        after = fh.read()
    assert after == before, "--dry-run must never write"


def test_cli_dry_run_reports_all_three_dispositions(tmp_path):
    ws = _fixture_workspace(tmp_path)
    r = _run_cli("--dry-run", str(ws))
    assert r.returncode == 0, r.stderr
    assert "BACKFILL" in r.stdout and "backfill-me" in r.stdout
    assert "SKIP (already has done_when)" in r.stdout and "already-has" in r.stdout
    assert "SKIP (no test: surface)" in r.stdout and "no-test-surface" in r.stdout
    assert "3 contract(s) scanned: 1 to backfill, 1 already declared, 1 skipped" in r.stdout


def test_cli_dry_run_lists_every_skipped_no_test_surface_contract_by_name(tmp_path):
    """AC-SUB-3: 'skip contracts with no test surface — list them' -- not just a count."""
    ws = _fixture_workspace(tmp_path)
    r = _run_cli("--dry-run", str(ws))
    rel = os.path.join("specs", "features", "foundry", "a", "no-test-surface", "acceptance-contract.yaml")
    assert rel in r.stdout


def test_cli_dry_run_never_touches_a_contract_outside_specs_features_foundry(tmp_path):
    outside = os.path.join(tmp_path, "specs", "features", "handbook", "z")
    os.makedirs(outside, exist_ok=True)
    with open(os.path.join(outside, "acceptance-contract.yaml"), "w", encoding="utf-8") as fh:
        fh.write(CONTRACT_NO_DONE_WHEN)
    ws = _fixture_workspace(tmp_path)
    r = _run_cli("--dry-run", str(ws))
    assert "handbook" not in r.stdout


def test_cli_requires_exactly_one_of_dry_run_or_apply():
    r = _run_cli("/tmp")
    assert r.returncode != 0
    r2 = _run_cli("--dry-run", "--apply", "/tmp")
    assert r2.returncode != 0


def test_cli_refuses_a_nonexistent_workspace_root(tmp_path):
    r = _run_cli("--dry-run", str(tmp_path / "does-not-exist"))
    assert r.returncode == 2


# ────────────────────────────────────────────────────────────────────────────── CLI: --apply ==== #

def test_cli_apply_writes_only_the_backfill_contract(tmp_path):
    ws = _fixture_workspace(tmp_path)
    already_has = os.path.join(ws, "specs/features/foundry/a/already-has/acceptance-contract.yaml")
    no_test = os.path.join(ws, "specs/features/foundry/a/no-test-surface/acceptance-contract.yaml")
    backfill_me = os.path.join(ws, "specs/features/foundry/a/backfill-me/acceptance-contract.yaml")
    with open(already_has, encoding="utf-8") as fh:
        already_has_before = fh.read()
    with open(no_test, encoding="utf-8") as fh:
        no_test_before = fh.read()

    r = _run_cli("--apply", str(ws))
    assert r.returncode == 0, r.stderr

    with open(already_has, encoding="utf-8") as fh:
        assert fh.read() == already_has_before, "an already-declared contract must never be rewritten"
    with open(no_test, encoding="utf-8") as fh:
        assert fh.read() == no_test_before, "a no-test-surface contract must never be rewritten"
    with open(backfill_me, encoding="utf-8") as fh:
        text = fh.read()
    assert "done_when:" in text
    assert '  - "test:tests/test_bar.py"' in text
    assert '  - "test:tests/test_foo.py"' in text
    doc = yaml.safe_load(text.split(SENTINEL, 1)[0])
    assert doc["done_when"] == ["test:tests/test_bar.py", "test:tests/test_foo.py"]


def test_cli_apply_is_idempotent_a_second_run_backfills_nothing_new(tmp_path):
    ws = _fixture_workspace(tmp_path)
    r1 = _run_cli("--apply", str(ws))
    assert r1.returncode == 0, r1.stderr
    r2 = _run_cli("--dry-run", str(ws))
    assert r2.returncode == 0, r2.stderr
    assert "0 to backfill" in r2.stdout
