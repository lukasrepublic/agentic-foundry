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


PINS = {   # the single place the expected pins are written: tool -> (version, release URL, sha256)
    "conftest": ("0.71.1", "https://github.com/open-policy-agent/conftest/releases/download/v0.71.1/conftest_0.71.1_Linux_x86_64.tar.gz",
                 "c3f6b2a753bd56e377a1e51a95ae3e057f926f2e9139f7d5b5dfbbaf810f7985"),
    "opa": ("1.22.0", "https://github.com/open-policy-agent/opa/releases/download/v1.22.0/opa_linux_amd64_static",
            "791790254c598a942eb140959bcec9a47f844535a632fd01d11d870ba2a47047"),
    "kyverno": ("1.19.2", "https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_linux_x86_64.tar.gz",
                "357eaf1df8bc24ddd5a73979fadeaae6a032e8710f11af264229756e4956bf4a"),
}


def _fetches():
    script = (ROOT / "scripts" / "ci-install-policy-tools.sh").read_text(encoding="utf-8")
    return [m.groups() for m in re.finditer(r"(?m)^fetch (\S+) (https://\S+) ([0-9a-f]{64})$", script)]


def test_ci_installs_the_three_tools_pinned_and_verified_before_the_suite():
    steps, step = _install_step()
    names = [s.get("name", "") for s in steps]
    assert names.index(step["name"]) < names.index(next(n for n in names if n.startswith("pytest suite")))
    assert step["run"].strip() == "bash scripts/ci-install-policy-tools.sh"
    script = (ROOT / "scripts" / "ci-install-policy-tools.sh").read_text(encoding="utf-8")
    assert "sha256sum -c" in script and "set -euo pipefail" in script
    # each (URL, sha256) pair, together, exactly as pinned; nothing else is fetched
    assert sorted((url, sha) for _, url, sha in _fetches()) == sorted((u, h) for _, u, h in PINS.values())
    assert len(re.findall(r"(?m)^\s*(?:fetch|curl)\b", script.replace("fetch() {", ""))) == 3 + 1   # three fetch calls + the one curl in fetch()
    for tool, (ver, url, _) in PINS.items():
        assert f"/download/v{ver}/" in url and "latest" not in url


def test_the_versions_named_in_the_ci_comment_match_the_script():
    text = CI.read_text(encoding="utf-8")
    named = dict(re.findall(r"(conftest|opa|kyverno)(?:-cli)? v(\d+\.\d+\.\d+) \(20", text))
    assert named == {tool: ver for tool, (ver, _, _) in PINS.items()}
    for _, url, _ in _fetches():
        tool = "kyverno" if "/kyverno/" in url else ("opa" if "/opa/" in url else "conftest")
        assert f"/download/v{named[tool]}/" in url


def test_the_pins_carry_a_research_timestamp():
    text = CI.read_text(encoding="utf-8")
    assert re.search(r"Research timestamp 20\d\d-\d\d-\d\dT\d\d:\d\dZ", text)
