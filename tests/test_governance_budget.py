"""tests/test_governance_budget.py — scripts/foundry-governance-budget.py (AC-SUB-4,
subtraction-wave, autonomy-continuation R4).

Drives the shipped CLI over throwaway fixture trees (never the real repo, except for the one
"agrees with the real shipped counts" smoke test below) so each metric's derivation is proven
against a KNOWN answer, not just "the number the script happens to print today".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REPO_ROOT, load_module

gb = load_module("scripts/foundry-governance-budget.py", "foundry_governance_budget")

CLI_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-governance-budget.py")


def _run_cli(*args):
    return subprocess.run([sys.executable, CLI_PATH, *args], capture_output=True, text=True, timeout=30)


def _make_tree(tmp_path):
    root = tmp_path
    skills = os.path.join(root, "skills")
    os.makedirs(os.path.join(skills, "alpha"), exist_ok=True)
    os.makedirs(os.path.join(skills, "beta"), exist_ok=True)
    with open(os.path.join(skills, "alpha", "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("line1\nline2\nline3\n")
    with open(os.path.join(skills, "beta", "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("line1\nline2\n")
    # a directory under skills/ with no SKILL.md must not count / must not crash.
    os.makedirs(os.path.join(skills, "gamma-no-skill-file"), exist_ok=True)

    scripts_dir = os.path.join(root, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    with open(os.path.join(scripts_dir, "foundry_a.py"), "w", encoding="utf-8") as fh:
        fh.write("# a\n")
    with open(os.path.join(scripts_dir, "foundry_b.py"), "w", encoding="utf-8") as fh:
        fh.write("# b\n")
    exe = os.path.join(scripts_dir, "foundry-c.sh")
    with open(exe, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho hi\n")
    os.chmod(exe, 0o755)
    non_exe = os.path.join(scripts_dir, "notes.txt")
    with open(non_exe, "w", encoding="utf-8") as fh:
        fh.write("not invocable\n")
    # a subdirectory under scripts/ must not be recursed into (non-recursive, AC-PFM-2 shape).
    os.makedirs(os.path.join(scripts_dir, "foundry_checks"), exist_ok=True)
    with open(os.path.join(scripts_dir, "foundry_checks", "nested.py"), "w", encoding="utf-8") as fh:
        fh.write("# nested, must not count\n")

    docs_dir = os.path.join(root, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    with open(os.path.join(docs_dir, "permission-floor.json"), "w", encoding="utf-8") as fh:
        json.dump({"entries": [{"rule": "Bash(x)", "tier": "allow"}, {"rule": "Bash(y)", "tier": "ask"}],
                   "not_invoked": ["z"]}, fh)

    hooks_dir = os.path.join(root, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    with open(os.path.join(hooks_dir, "hooks.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "a.sh"},
                        {"type": "command", "command": "b.sh"},
                    ]},
                ],
                "PostToolUse": [
                    {"matcher": "Agent", "hooks": [{"type": "command", "command": "c.sh"}]},
                ],
            }
        }, fh)

    tests_dir = os.path.join(root, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    with open(os.path.join(tests_dir, "test_one.py"), "w", encoding="utf-8") as fh:
        fh.write(
            "def test_a():\n    pass\n\n\nasync def test_b():\n    pass\n\n\n"
            "class TestGroup:\n    def test_c(self):\n        pass\n"
        )
    with open(os.path.join(tests_dir, "test_two.py"), "w", encoding="utf-8") as fh:
        fh.write("def test_d():\n    pass\n")
    with open(os.path.join(tests_dir, "not_a_test_file.py"), "w", encoding="utf-8") as fh:
        fh.write("def test_should_not_count():\n    pass\n")
    with open(os.path.join(tests_dir, "conftest.py"), "w", encoding="utf-8") as fh:
        fh.write("def test_should_also_not_count():\n    pass\n")
    return root


# ─────────────────────────────────────────────────────────────────── each metric (in-process) ==== #

def test_count_skill_prose_lines_sums_every_skill_md_non_recursively(tmp_path):
    root = _make_tree(tmp_path)
    assert gb.count_skill_prose_lines(root) == 5  # 3 + 2, gamma's missing SKILL.md contributes 0


def test_count_shipped_scripts_is_non_recursive_and_execute_bit_gated(tmp_path):
    root = _make_tree(tmp_path)
    # foundry_a.py, foundry_b.py, foundry-c.sh (exec bit) == 3; notes.txt (no exec bit) and the
    # nested foundry_checks/nested.py (recursion) must NOT count.
    assert gb.count_shipped_scripts(root) == 3


def test_count_floor_rows_reads_only_the_entries_array(tmp_path):
    root = _make_tree(tmp_path)
    assert gb.count_floor_rows(root) == 2  # not_invoked is a different key, not counted


def test_count_hook_commands_walks_every_event_and_matcher(tmp_path):
    root = _make_tree(tmp_path)
    assert gb.count_hook_commands(root) == 3


def test_count_pytest_tests_counts_definitions_across_test_files_only(tmp_path):
    root = _make_tree(tmp_path)
    # test_one.py: test_a, test_b (async), TestGroup.test_c == 3; test_two.py: test_d == 1.
    # not_a_test_file.py and conftest.py are excluded by filename shape.
    assert gb.count_pytest_tests(root) == 4


def test_missing_files_and_dirs_all_resolve_to_zero_not_a_crash(tmp_path):
    root = tmp_path  # nothing created at all
    assert gb.budget(str(root)) == {
        "skill_prose_lines": 0,
        "shipped_scripts": 0,
        "floor_rows": 0,
        "hook_commands": 0,
        "pytest_tests": 0,
    }


def test_malformed_json_resolves_to_zero_not_a_crash(tmp_path):
    root = tmp_path
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "docs", "permission-floor.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    os.makedirs(os.path.join(root, "hooks"), exist_ok=True)
    with open(os.path.join(root, "hooks", "hooks.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    assert gb.count_floor_rows(str(root)) == 0
    assert gb.count_hook_commands(str(root)) == 0


def test_render_table_names_every_metric_and_its_count(tmp_path):
    root = _make_tree(tmp_path)
    counts = gb.budget(root)
    table = gb.render_table(counts)
    for _key, label, _fn in gb.LABELS:
        assert label in table
    for value in counts.values():
        assert str(value) in table


# ──────────────────────────────────────────────────────────────────────────────────────── CLI ==== #

def test_cli_prints_the_table_and_exits_zero(tmp_path):
    root = _make_tree(tmp_path)
    r = _run_cli("--root", str(root))
    assert r.returncode == 0, r.stderr
    assert "governance budget:" in r.stdout


def test_cli_json_flag_emits_a_parseable_object_with_all_five_keys(tmp_path):
    root = _make_tree(tmp_path)
    r = _run_cli("--root", str(root), "--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert set(doc.keys()) == {k for k, _l, _f in gb.LABELS}


def test_cli_writes_nothing_to_the_measured_tree(tmp_path):
    root = _make_tree(tmp_path)
    before = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            before[p] = os.path.getmtime(p)
    r = _run_cli("--root", str(root))
    assert r.returncode == 0, r.stderr
    after = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            after[p] = os.path.getmtime(p)
    assert before == after, "the reporter must never write into the measured tree"


def test_cli_defaults_root_to_the_real_shipped_plugin_tree():
    """A smoke test against the REAL repo (never asserting exact numbers, which would make this
    test itself part of the governance budget's own churn) -- only that every count is a
    non-negative integer and the script does not crash against its own real tree."""
    r = _run_cli("--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    for key, _label, _fn in gb.LABELS:
        assert isinstance(doc[key], int) and doc[key] >= 0
