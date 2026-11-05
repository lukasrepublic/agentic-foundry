"""gv-16 / rule C2: budgets are tools, not gates. `foundry_ci_budget_lint.py` flags a performance or request-count budget
wired as a required check; as a non-required report it passes. Workflow fixtures are YAML written to tmp_path; no network.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foundry_ci_budget_lint.py"

WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  unit:
    runs-on: ubuntu-24.04
    steps:
      - run: python3 -m pytest -q
  perf-budget:
    runs-on: ubuntu-24.04
    %s
    steps:
      - name: Lighthouse performance budget
        run: npx lhci autorun --budget-path=budget.json
  requests:
    name: request-count
    runs-on: ubuntu-24.04
    steps:
      - name: assert max requests per page
        run: node scripts/max-requests.js
"""


def run(tmp_path, workflow, *extra):
    f = tmp_path / "ci.yml"
    f.write_text(workflow)
    return subprocess.run([sys.executable, str(SCRIPT), str(f), "--json", *extra], capture_output=True, text=True, timeout=60)


def jobs(p):
    return sorted(f["job"] for f in json.loads(p.stdout)["findings"])


def test_a_budget_named_in_the_required_checks_is_flagged(tmp_path):
    p = run(tmp_path, WORKFLOW % "continue-on-error: true", "--required", "perf-budget", "--required", "unit")
    assert p.returncode == 1, p.stdout + p.stderr
    assert jobs(p) == ["perf-budget"]
    f = json.loads(p.stdout)["findings"][0]
    assert "required check" in f["reason"] and "Lighthouse" in f["match"] and f["check"] == "perf-budget"


def test_the_check_name_is_the_jobs_name_when_it_has_one(tmp_path):
    p = run(tmp_path, WORKFLOW % "continue-on-error: true", "--required", "request-count")
    assert p.returncode == 1 and jobs(p) == ["requests"]
    p = run(tmp_path, WORKFLOW % "continue-on-error: true", "--required", "requests")      # the id: also matches
    assert jobs(p) == ["requests"]


def test_a_budget_that_is_not_required_passes(tmp_path):
    p = run(tmp_path, WORKFLOW % "continue-on-error: true", "--required", "unit")
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout) == {"findings": [], "required_known": True}


def test_without_a_required_list_a_blocking_budget_is_flagged_and_a_report_is_not(tmp_path):
    blocking = run(tmp_path, WORKFLOW % "")
    assert blocking.returncode == 1 and "perf-budget" in jobs(blocking) and "requests" in jobs(blocking)
    assert json.loads(blocking.stdout)["required_known"] is False
    report = run(tmp_path, (WORKFLOW % "continue-on-error: true").replace(
        "      - name: assert max requests per page", "      - continue-on-error: true\n        name: assert max requests per page").replace(
        "  requests:\n    name: request-count\n", "  requests:\n    name: request-count\n    continue-on-error: true\n"))
    assert report.returncode == 0, report.stdout


def test_required_from_a_ruleset_document(tmp_path):
    rs = tmp_path / "rules.json"
    rs.write_text(json.dumps([{"type": "required_status_checks",
                               "parameters": {"required_status_checks": [{"context": "unit"}, {"context": "perf-budget"}]}}]))
    p = run(tmp_path, WORKFLOW % "continue-on-error: true", "--required-from", str(rs))
    assert p.returncode == 1 and jobs(p) == ["perf-budget"]
    rs.write_text(json.dumps(["unit"]))
    assert run(tmp_path, WORKFLOW % "continue-on-error: true", "--required-from", str(rs)).returncode == 0


def test_a_workflow_with_no_budget_passes_and_plain_output(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), str(_write(tmp_path, "jobs:\n  t:\n    steps:\n      - run: pytest\n"))],
                       capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.strip() == "ci-budget-lint-ok"


def _write(tmp_path, text):
    f = tmp_path / "w.yml"
    f.write_text(text)
    return f


def test_a_directory_is_scanned_and_bad_input_is_exit_2(tmp_path):
    d = tmp_path / "wf"
    d.mkdir()
    (d / "a.yml").write_text(WORKFLOW % "")
    p = subprocess.run([sys.executable, str(SCRIPT), str(d), "--json"], capture_output=True, text=True)
    assert p.returncode == 1 and len(json.loads(p.stdout)["findings"]) == 2
    assert subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope")], capture_output=True, text=True).returncode == 2
    (d / "bad.yml").write_text("jobs: [unclosed")
    assert subprocess.run([sys.executable, str(SCRIPT), str(d)], capture_output=True, text=True).returncode == 2


def test_the_repos_own_workflows_have_no_budget_gate():
    p = subprocess.run([sys.executable, str(SCRIPT), str(ROOT / ".github" / "workflows")], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
