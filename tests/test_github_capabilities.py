"""gn-03: GitHub discovery-coverage probe + doctor advisory. No network: every subprocess goes
through the injectable runner, replaced here by fixture responses."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "gn_capabilities"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_github_capabilities as ghc  # noqa: E402

R = ghc.Result


def fx(name):
    return (FIX / name).read_text()


class Fake:
    """Routes argv to a response by predicate; records every call."""

    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        j = " ".join(argv)
        for key, res in self.overrides.items():
            if key in j:
                return res(argv) if callable(res) else res
        if argv[:3] == ["gh", "auth", "status"]:
            return R(stdout="Logged in")
        if "graphql" in argv:
            return R(stdout=fx("project_ok.json"))
        if argv[:2] == ["gh", "search"]:
            return R(stdout="[]")
        if "notifications" in j:
            return R(stdout=fx("notifications.txt"))
        if argv[:3] == ["gh", "issue", "edit"]:
            return R(stdout="--add-blocked-by <issues>")
        if argv[:3] == ["gh", "issue", "artifact"]:
            return R(stdout="usage")
        if argv[:2] == ["claude", "--version"]:
            return R(stdout="2.1.300 (Claude Code)")
        if argv[:2] == ["git", "-C"]:
            return R(stdout="git@github.com:example-org/code.git\n")
        if j.endswith("/rules/branches/main"):
            return R(stdout=fx("rules_rsc.json"))
        if j.endswith("repos/example-org/code"):
            return R(stdout='{"default_branch":"main"}')
        if j.endswith("repos/example-org/planning"):
            return R(stdout='{"name":"planning"}')
        if j.endswith(" user"):
            return R(stdout=fx("user_headers.txt"))
        raise AssertionError(f"unrouted argv {argv}")


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".claude").mkdir()
    shutil.copy(FIX / "manifest.json", tmp_path / ".claude" / "foundry-project.json")
    (tmp_path / "usage.jsonl").write_text(
        json.dumps({"ts": datetime.now(timezone.utc).isoformat()}) + "\n")
    return str(tmp_path)


def rows(res):
    return {r["name"]: r for r in res["sources"]}


def test_full_coverage(proj):
    fake = Fake()
    res = ghc.collect(proj, fake)
    r = rows(res)
    assert res["coverage"] == "full" and res["required_sources_ok"] is True
    assert res["summary"] == "full"
    for n in ("gh", "token_scopes", "planning_repo", "project", "assigned", "mentions",
              "review_requests", "approvals", "native_dependencies", "branch_protection:code",
              "claude_code", "scheduling", "usage", "issue_artifacts"):
        assert r[n]["status"] == "ok", (n, r[n])
        assert r[n]["scope"] and r[n]["detail"]
    assert r["scheduling"]["detail"] == "declared, verified at deck start"
    assert r["token_scopes"]["scopes"] == ["repo", "read:org", "gist"]
    assert "poll interval 60s" in r["approvals"]["detail"]


def test_partial_project_scope_missing(proj):
    fake = Fake({"graphql": R(rc=1, stderr="error: your authentication token is missing "
                                           "required scopes [read:project]")})
    res = ghc.collect(proj, fake)
    p = rows(res)["project"]
    assert p["status"] == "uncovered"
    assert "gh auth refresh -s read:project,project" in p["detail"]
    assert res["coverage"] == "partial"
    assert res["required_sources_ok"] is True  # project is not a required source
    assert res["summary"] == "partial — uncovered: project"


def test_notifications_refused_fine_grained(proj):
    fake = Fake({"notifications": R(rc=1, stderr="gh: Resource not accessible by personal "
                                                 "access token (HTTP 403)"),
                 " user": R(stdout=fx("user_headers_finegrained.txt"))})
    res = ghc.collect(proj, fake)
    r = rows(res)
    assert r["approvals"]["status"] == "uncovered" and r["approvals"]["http_status"] == 403
    assert r["token_scopes"]["scopes"] == "unknown"
    assert res["required_sources_ok"] is False
    assert res["summary"] == "partial — uncovered: approvals"  # assigned succeeded; still named


def test_summary_names_every_gap(proj):
    fake = Fake({"graphql": R(rc=1, stderr="missing required scopes [read:project]"),
                 "notifications": R(rc=1, stderr="HTTP 404")})
    assert ghc.collect(proj, fake)["summary"] == "partial — uncovered: project, approvals"


def test_empty_search_is_ok(proj):
    res = ghc.collect(proj, Fake({"--mentions": R(stdout="[]")}))
    assert rows(res)["mentions"]["status"] == "ok"


def test_timeout_is_error_not_uncovered(proj):
    fake = Fake({"--assignee": R(rc=124, timed_out=True)})
    res = ghc.collect(proj, fake)
    assert rows(res)["assigned"]["status"] == "error"
    assert res["required_sources_ok"] is False
    assert "error: assigned" in res["summary"]


def test_5xx_is_error_with_status(proj):
    res = ghc.collect(proj, Fake({"--review-requested": R(rc=1, stderr="HTTP 502 Bad Gateway")}))
    r = rows(res)["review_requests"]
    assert r["status"] == "error" and r["http_status"] == 502


def test_gh_absent_is_error(proj):
    def absent(argv, timeout):
        if argv[0] == "gh":
            return R(rc=127, missing=True)
        return Fake()(argv, timeout)
    res = ghc.collect(proj, absent)
    r = rows(res)
    assert r["gh"]["status"] == "error"
    assert all(r[n]["status"] == "error" for n in ghc.REQUIRED_SOURCES)
    assert res["coverage"] == "partial" and res["required_sources_ok"] is False


def test_unparseable_search_output_is_error(proj):
    res = ghc.collect(proj, Fake({"--assignee": R(stdout="not json")}))
    assert rows(res)["assigned"]["status"] == "error"


def test_old_gh_lacks_dependencies_and_artifacts(proj):
    fake = Fake({"issue edit": R(stdout="--add-assignee"),
                 "issue artifact": R(rc=1, stderr="unknown command")})
    res = ghc.collect(proj, fake)
    r = rows(res)
    assert r["native_dependencies"]["status"] == "uncovered"
    assert r["issue_artifacts"]["status"] == "uncovered"
    assert "fallback: issue description / linked document issue" in r["issue_artifacts"]["detail"]
    assert res["coverage"] == "full" and res["required_sources_ok"] is True  # evidence only


def test_branch_protection_without_rsc_rule(proj):
    res = ghc.collect(proj, Fake({"/rules/branches/main": R(stdout="[]")}))
    assert rows(res)["branch_protection:code"]["status"] == "uncovered"
    assert res["required_sources_ok"] is True


def test_claude_code_below_floor(proj):
    res = ghc.collect(proj, Fake({"claude --version": R(stdout="2.1.277")}))
    assert rows(res)["claude_code"]["status"] == "uncovered"
    assert ghc.CLAUDE_CODE_FLOOR == (2, 1, 278)


def test_usage_states(proj, tmp_path):
    def usage(manifest_usage, content=None):
        d = Path(proj)
        m = json.loads((d / ".claude/foundry-project.json").read_text())
        m.pop("usage", None)
        if manifest_usage is not None:
            m["usage"] = {"dataset": manifest_usage}
        (d / ".claude/foundry-project.json").write_text(json.dumps(m))
        if content is not None:
            (d / "usage.jsonl").write_text(content)
        return rows(ghc.collect(proj, Fake()))["usage"]
    assert usage(None)["state"] == "not-configured"
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    assert usage("usage.jsonl", json.dumps({"ts": old}) + "\n")["state"] == "stale"
    assert usage("usage.jsonl", "")["state"] == "stale"
    assert usage("usage.jsonl", "garbage\n")["state"] == "unknown"
    assert usage("missing.jsonl")["state"] == "unknown"
    fresh = datetime.now(timezone.utc).isoformat()
    assert usage("usage.jsonl", json.dumps({"ts": fresh}) + "\n")["state"] == "fresh"


def test_read_only_and_bounded(proj):
    fake = Fake()
    ghc.collect(proj, fake, timeout=11)
    assert fake.calls
    for argv, timeout in fake.calls:
        assert timeout == 11
        assert "-X" not in argv and "--method" not in argv
        assert not any(a in ("create", "delete", "close", "comment", "merge") for a in argv)
        if "graphql" in argv:
            q = next(a for a in argv if a.startswith("query="))
            assert q.startswith("query=query") and "mutation" not in q
        if argv[:2] == ["gh", "issue"]:
            assert "--help" in argv
        if argv[:2] == ["git", "-C"]:
            assert argv[3:] == ["remote", "get-url", "origin"]


def test_no_scope_configured(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "foundry-project.json").write_text("{}")
    assert ghc.collect(str(tmp_path), Fake()) is None
    assert ghc.advisory_line(str(tmp_path), Fake()) == "n/a (single-operator mode)"  # gn-17


def test_cli_help_and_no_scope(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "scripts/foundry_github_capabilities.py"), "--help"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and "--json" in p.stdout
    p = subprocess.run([sys.executable, str(ROOT / "scripts/foundry_github_capabilities.py"),
                        "--project-dir", str(tmp_path), "--json"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["coverage"] == "n/a"


# ------------------------------------------------ doctor advisory ------------------------------------------------ #

def _doctor():
    spec = importlib.util.spec_from_file_location("foundry_doctor_gn03", ROOT / "scripts/foundry-doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doctor_check_na(tmp_path):
    assert _doctor().check_github_capabilities(str(tmp_path)) == (True, "n/a (single-operator mode)")  # gn-17


def test_doctor_check_partial_and_crash(proj, monkeypatch):
    d = _doctor()
    monkeypatch.setattr(ghc, "advisory_line", lambda p: "partial — uncovered: project, approvals")
    assert d.check_github_capabilities(proj) == (d.ADVISORY, "partial — uncovered: project, approvals")
    monkeypatch.setattr(ghc, "advisory_line", lambda p: "full")
    assert d.check_github_capabilities(proj) == (True, "full")

    def boom(p):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ghc, "advisory_line", boom)
    ok, detail = d.check_github_capabilities(proj)
    assert ok is d.ADVISORY and "probe crashed" in detail


def _run_doctor(args, env_dir, extra_env=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(env_dir), **(extra_env or {}))
    return subprocess.run([sys.executable, str(ROOT / "scripts/foundry-doctor.py"), *args],
                          capture_output=True, text=True, env=env)


def test_doctor_renders_one_line_never_red_and_skips_session_start(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "foundry-project.json").write_text("{}")
    full = _run_doctor([], tmp_path)
    assert full.stdout.count("github-capabilities:") == 1
    assert "n/a (single-operator mode)" in full.stdout  # gn-17: default mode, no network
    ss = _run_doctor(["--session-start"], tmp_path)
    assert "github-capabilities" not in ss.stdout


def test_doctor_failing_gh_stays_advisory_end_to_end(tmp_path):
    """A github_scope with every gh call failing must not add a RED."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "foundry-project.json").write_text(
        json.dumps({"operating_mode": "team",
                    "github_scope": {"owner": "example-org", "planning_repo": "planning",
                                     "project_number": 1, "operators": {}},
                    "usage": {"dataset": 5}}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "gh").chmod(0o755)
    res = _run_doctor([], tmp_path, {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    line = [ln for ln in res.stdout.splitlines() if "github-capabilities:" in ln]
    assert len(line) == 1 and "[adv ]" in line[0]
    assert "[XX ] github-capabilities" not in res.stdout
