"""tests/test_dispatch_binding.py - gn-11: issue-bound worker launch.

Runs the real WorktreeCreate hook against a temp workspace + product repo, and checks the
dispatch skill's assignment-driven launch procedure structurally (parsed, by step index).
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from conftest import REPO_ROOT

HOOK = os.path.join(REPO_ROOT, "hooks", "foundry-worktree-create.sh")
SKILL = os.path.join(REPO_ROOT, "skills", "dispatch", "SKILL.md")
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _init(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(GIT + ["-C", path, "commit", "-q", "--allow-empty", "-m", "init"], check=True)


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    (root / ".foundry" / "dispatch-queue").mkdir(parents=True)
    _init(str(root))
    _init(str(root / "app"))
    (root / ".claude" / "foundry-project.json").write_text(
        json.dumps({"schema_version": 1, "repos": {"app": {"path": "app"}}}))
    return root


def _assignment(**over):
    a = {"issue_url": "https://github.com/o/r/issues/7", "issue_number": 7, "repo_key": "app",
         "operator": "op_lukas",
         "acceptance_revision": {"body_sha256": "ab" * 32,
                                 "documents": [{"url": "https://github.com/o/r/blob/main/d.md", "revision": "c0ffee"}]}}
    a.update(over)
    return a


def _manifest(ws, task="t1", target="app", **extra):
    m = {"target_repo": target, "task": task, "agent": "app-engineer", "dispatcher_session_id": "s1"}
    m.update(extra)
    (ws / ".foundry" / "dispatch-queue" / f"{task}.json").write_text(json.dumps(m))


def _run(ws):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(ws))
    return subprocess.run(["bash", HOOK], input="{}", capture_output=True, text=True, env=env)


def _log(ws):
    p = ws / ".foundry" / "dispatch.log"
    return p.read_text() if p.exists() else ""


def test_issue_only_manifest_binds_worktree(ws):
    a = _assignment()
    _manifest(ws, assignment=a)
    r = _run(ws)
    assert r.returncode == 0, r.stderr
    wt = r.stdout.strip()
    assert os.path.isdir(wt) and wt.startswith(str(ws.resolve() / ".worktrees" / "app"))
    gd = subprocess.check_output(["git", "-C", wt, "rev-parse", "--absolute-git-dir"], text=True).strip()
    rec = json.load(open(os.path.join(gd, "foundry-assignment.json")))
    assert rec["worktree"] == wt
    for k, v in a.items():
        assert rec[k] == v
    assert rec["operator"] == "op_lukas" and str(rec["issue"]) == "7"  # the keys gn-05 reads
    assert os.path.isfile(os.path.join(wt, ".agent", "assignment.json"))
    staged = subprocess.check_output(["git", "-C", wt, "status", "--porcelain"], text=True)
    assert "spec" not in staged and "contract" not in staged
    assert not os.path.exists(os.path.join(wt, "contract.yaml"))
    assert not list((ws / ".foundry" / "dispatch-queue").glob("*.json"))  # consumed


def test_workspace_target_is_claimed(ws):
    _manifest(ws, target="workspace", assignment=_assignment(repo_key="workspace"))
    r = _run(ws)
    assert r.returncode == 0, r.stderr
    gd = subprocess.check_output(["git", "-C", r.stdout.strip(), "rev-parse", "--absolute-git-dir"], text=True).strip()
    assert os.path.isfile(os.path.join(gd, "foundry-assignment.json"))


def test_contract_manifest_still_preflights_hash_mismatch(ws):
    (ws / "spec.md").write_text("# s\n\n<!-- normative -->\nx\n<!-- /normative -->\n")
    (ws / "contract.yaml").write_text("spec_ref: spec.md\ntarget_repo: app\n")
    _manifest(ws, contract_ref="contract.yaml", assignment=_assignment())
    r = _run(ws)
    assert r.returncode != 0
    assert r.stdout == ""
    assert "PREFLIGHT-FAIL" in _log(ws)


@pytest.mark.parametrize("drop", ["issue_url", "operator", "acceptance_revision", "issue_number"])
def test_incomplete_assignment_fails_closed(ws, drop):
    a = _assignment()
    del a[drop]
    _manifest(ws, assignment=a)
    r = _run(ws)
    assert r.returncode != 0 and r.stdout == ""
    assert "DROPPED-ASSIGNMENT-INCOMPLETE" in _log(ws)
    assert not (ws / ".worktrees").exists()


def test_neither_contract_nor_assignment_fails_closed(ws):
    _manifest(ws)
    r = _run(ws)
    assert r.returncode != 0 and r.stdout == ""
    assert "DROPPED-ASSIGNMENT-INCOMPLETE" in _log(ws)


def test_unknown_repo_key_fails_closed(ws):
    _manifest(ws, target="nope", assignment=_assignment(repo_key="nope"))
    r = _run(ws)
    assert r.returncode != 0 and r.stdout == ""
    assert "DROPPED-UNKNOWN-REPO-KEY" in _log(ws)


# ---- skill: parsed structure, not substrings
def _skill():
    return open(SKILL, encoding="utf-8").read()


def _section(text, heading):
    parts = re.split(r"^## ", text, flags=re.M)
    for p in parts:
        if p.startswith(heading):
            return p
    raise AssertionError(f"no section {heading}")


def test_late_result_runs_reconcile_before_review_ready():
    sec = _section(_skill(), "Issue-assignment launch")
    sub = re.findall(r"^   (\d+)\. (.*)$", sec, flags=re.M)  # the numbered late-result sub-steps
    texts = [t for _, t in sub]
    rec = [i for i, t in enumerate(texts) if "foundry-ticket.py reconcile" in t]
    rr = [i for i, t in enumerate(texts) if "review_ready" in t and t.startswith("Record")]
    integ = [i for i, t in enumerate(texts) if t.startswith("Integrate")]
    assert rec and rr and integ
    assert rec[0] < integ[0] < rr[0]


def test_skill_names_no_ambient_assignment_source():
    sec = _section(_skill(), "Issue-assignment launch")
    assert not re.search(r"\$[A-Z_]{3,}|\bFOUNDRY_[A-Z_]+|current[ -]ticket file|current-ticket", sec)


def test_result_schema_carries_url_evidence():
    sec = _section(_skill(), "Result schema")
    block = re.search(r"```json\n(.*?)```", sec, flags=re.S).group(1)
    block = re.sub(r"#.*", "", block)  # drop the annotation comment
    schema = json.loads(block.replace('"<https url|null>"', '"x"').replace('["<https url of a CI run>"]', '["x"]')
                        .replace("PASS|FAIL|EVIDENCE-MISSING|NOT-APPLICABLE", "PASS").replace("ready|failed", "ready")
                        .replace("<str>", "s").replace('["<path>"]', '["p"]'))
    assert "pr_url" in schema and "ci_run_urls" in schema
    assert "evidence" not in schema
