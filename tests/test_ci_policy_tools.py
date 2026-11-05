"""gv-19: the kit policy-tool tests run in CI (tools installed, pinned, checksum-verified) and FAIL rather than skip there."""
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

import conftest as C

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
REASONS = ["conftest binary not on PATH; rego evaluation needs it", "kyverno CLI not on PATH: policy evaluation not run",
           "opa not installed", "conftest not installed", "Skipped: opa not installed"]


@pytest.mark.parametrize("reason", REASONS)
def test_a_missing_tool_skip_fails_under_ci_and_not_outside(reason):
    assert C.policy_tool_skip_must_fail(reason, {"CI": "true"}) is True
    assert C.policy_tool_skip_must_fail(reason, {}) is False
    assert C.policy_tool_skip_must_fail(reason, {"CI": "false"}) is False


@pytest.mark.parametrize("reason", ["jq is not installed", "requires docker", "", None])
def test_other_skips_are_untouched(reason):
    assert C.policy_tool_skip_must_fail(reason, {"CI": "true"}) is False


def test_the_hook_turns_a_real_skip_into_a_failure_under_ci(tmp_path):
    t = tmp_path / "test_x.py"
    t.write_text(textwrap.dedent('''
        import pytest
        @pytest.mark.skipif(True, reason="opa not installed")
        def test_needs_opa(): pass
        @pytest.mark.skipif(True, reason="jq is not installed")
        def test_needs_jq(): pass
    '''))
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "conftest.py")
    import os
    env = dict(os.environ, CI="true", PYTHONPATH=str(ROOT / "scripts"))
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(t), "-rs"], cwd=tmp_path,
                       capture_output=True, text=True, env=env, timeout=120)
    assert re.search(r"1 skipped, 1 error", p.stdout), p.stdout     # a setup-phase skip becomes an error
    env.pop("CI")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(t)], cwd=tmp_path,
                       capture_output=True, text=True, env=env, timeout=120)
    assert re.search(r"2 skipped", p.stdout), p.stdout


def _install_step():
    doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = doc["jobs"]["selftests"]["steps"]
    hits = [s for s in steps if "conftest" in s.get("name", "") and "opa" in s.get("name", "")]
    assert len(hits) == 1
    return steps, hits[0]


def test_ci_installs_the_three_tools_pinned_and_verified_before_the_suite():
    steps, step = _install_step()
    names = [s.get("name", "") for s in steps]
    assert names.index(step["name"]) < names.index(next(n for n in names if n.startswith("pytest suite")))
    run = step["run"]
    urls = re.findall(r"https://github\.com/[\w./-]+", run)
    assert len(urls) == 3
    for u in urls:
        assert "/releases/download/v" in u and "latest" not in u
    hashes = re.findall(r"\b[0-9a-f]{64}\b", run)
    assert len(hashes) == 3 and len(set(hashes)) == 3
    assert "sha256sum -c" in run and "set -euo pipefail" in run


def test_the_pins_carry_a_research_timestamp():
    text = CI.read_text(encoding="utf-8")
    assert re.search(r"Research timestamp 20\d\d-\d\d-\d\dT\d\d:\d\dZ", text)
