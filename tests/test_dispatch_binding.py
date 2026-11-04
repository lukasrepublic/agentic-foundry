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
        json.dumps({"schema_version": 1, "operating_mode": "team", "github_scope": {"owner": "example-org", "planning_repo": "planning"}, "repos": {"app": {"path": "app"}}}))
    (root / ".claude" / "foundry-operators.json").write_text(
        json.dumps({"schema_version": 1, "operators": {"op_lukas": {"name": "L"}}}))
    return root


def _assignment(**over):
    a = {"issue_url": "https://github.com/o/r/issues/7", "issue_number": 7, "repo_key": "app",
         "operator": "op_lukas", "lane": "charter",
         "acceptance_revision": {"body_sha256": "ab" * 32,
                                 "documents": [{"url": "https://github.com/o/r/blob/main/d.md", "revision": "c0ffee"}]}}
    a.update(over)
    return a


def _manifest(ws, task="t1", target="app", **extra):
    m = {"target_repo": target, "task": task, "agent": "app-engineer", "dispatcher_session_id": "s1"}
    m.update(extra)
    (ws / ".foundry" / "dispatch-queue" / "q.json").write_text(json.dumps(m))


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


def _refused(ws, token):
    r = _run(ws)
    assert r.returncode != 0 and r.stdout == "", (r.stdout, r.stderr)
    assert token in _log(ws), _log(ws)
    assert not (ws / ".worktrees").exists() and not (ws.parent / "other-repo").exists()
    assert not list((ws / ".foundry" / "dispatch-queue").glob("*.json"))


@pytest.mark.parametrize("lane_extra", [{"assignment": None}, {"contract_ref": "c.yaml", "assignment": None}])
@pytest.mark.parametrize("task", ["../../..", "../../../other-repo", "a/b", "-x", ".hid", "a" * 65, "a..b", "a b"])
def test_unsafe_task_label_refused_on_both_lanes(ws, task, lane_extra):
    extra = {k: (_assignment() if v is None else v) for k, v in lane_extra.items()}
    _manifest(ws, task=task, **extra)
    _refused(ws, "DROPPED-UNSAFE-LABEL")


@pytest.mark.parametrize("agent", ["a/b", "a\nb", "..", "x y"])
@pytest.mark.parametrize("with_contract", [False, True])
def test_unsafe_agent_label_refused(ws, agent, with_contract):
    extra = {"contract_ref": "c.yaml"} if with_contract else {}
    _manifest(ws, agent=agent, assignment=_assignment(), **extra)
    _refused(ws, "DROPPED-UNSAFE-LABEL")


def test_agent_with_newline_does_not_slide_to_issue_lane(ws):
    _manifest(ws, agent="x\ny", contract_ref="contract.yaml")
    _refused(ws, "DROPPED-UNSAFE-LABEL")
    assert "assignment-bound" not in _log(ws)


def test_empty_target_repo_fails_closed(ws):
    _manifest(ws, target="", assignment=_assignment())
    _refused(ws, "DROPPED-MALFORMED-MANIFEST")


@pytest.mark.parametrize("bad", ["", None, 5, ["x"]])
def test_contract_ref_present_but_invalid_fails_closed(ws, bad):
    _manifest(ws, contract_ref=bad, assignment=_assignment())
    _refused(ws, "DROPPED-CONTRACT-REF-INVALID")


def test_factory_lane_without_contract_refused(ws):
    _manifest(ws, assignment=_assignment(lane="factory"))
    _refused(ws, "DROPPED-FACTORY-LANE-NO-CONTRACT")


@pytest.mark.parametrize("field,val", [
    ("issue_url", "http://github.com/o/r/issues/7"),
    ("issue_url", "https://github.com/o/r/issues/8"),
    ("issue_url", "https://github.com/o/r/pull/7"),
    ("issue_url", "https://github.com/o/r/issues/7/../x"),
    ("lane", "other"),
    ("repo_key", "workspace"),
    ("repo_key", None),
    ("acceptance_revision", {"body_sha256": "xyz", "documents": []}),
    ("acceptance_revision", {"body_sha256": "ab" * 31, "documents": []}),
    ("acceptance_revision", {"body_sha256": "ab" * 32, "documents": [{"url": "http://x/y", "revision": "r"}]}),
])
def test_strict_assignment_validation(ws, field, val):
    a = _assignment()
    if val is None:
        del a[field]
    else:
        a[field] = val
    _manifest(ws, assignment=a)
    _refused(ws, "DROPPED-ASSIGNMENT-INCOMPLETE")


@pytest.mark.parametrize("registry", ["other", "missing"])
def test_unknown_operator_refused(ws, registry):
    p = ws / ".claude" / "foundry-operators.json"
    if registry == "missing":
        p.unlink()
    else:
        p.write_text(json.dumps({"operators": {"op_other": {}}}))
    _manifest(ws, assignment=_assignment())
    _refused(ws, "DROPPED-OPERATOR-UNKNOWN")


@pytest.mark.parametrize("link", ["dir", "file"])
def test_symlinked_agent_dir_or_file_refused(ws, link):
    # the product repo itself tracks the symlink, so every worktree checks it out
    app = ws / "app"
    out = ws.parent / "elsewhere"
    out.mkdir()
    if link == "dir":
        os.symlink(str(out), str(app / ".agent"))
        subprocess.run(["git", "-C", str(app), "add", ".agent"], check=True)
    else:
        (app / ".agent").mkdir()
        os.symlink(str(out / "victim.json"), str(app / ".agent" / "assignment.json"))
        subprocess.run(["git", "-C", str(app), "add", "-f", ".agent/assignment.json"], check=True)
    subprocess.run(GIT + ["-C", str(app), "commit", "-q", "-m", "link"], check=True)
    _manifest(ws, assignment=_assignment())
    r = _run(ws)
    assert r.returncode != 0 and r.stdout == ""
    assert "BINDING-FAILED" in _log(ws)
    assert os.listdir(out) == []


def test_contract_lane_with_assignment_also_writes_binding(ws, monkeypatch):
    import importlib.util  # noqa: F401  (frozen fixture built through the hook's own selftest recipe)
    spec = os.path.join(REPO_ROOT, "scripts")
    import sys
    sys.path.insert(0, spec)
    import foundry_contract as fc
    (ws / "spec.md").write_text("# s\n\n<!-- normative -->\nx\n<!-- /normative -->\n")
    sh = fc.spec_sha256(str(ws / "spec.md"))
    proper = ("spec_ref: spec.md\nspec_sha256: %s\ntarget_repo: app\nscope:\n  allowed_paths:\n    - \"demo/**\"\n"
              "checkpoints:\n  - ac_id: \"AC-DEMO-1\"\n    surface: \"cli:demo\"\n    locator: \"true\"\n"
              "    expect: {op: \"non-empty\", baseline: \"pre-change\"}\n") % sh
    pb = proper.encode()
    trailer = ("authorized:\n  operator_id: op_test\n  authorized_at: \"2026-01-01T00:00:00Z\"\n  auth_seq: 1\n"
               "  spec_sha256: %s\n  contract_sha256: %s\n  merge_autonomy_mode: lean\n") % (sh, fc.contract_sha256_bytes(pb))
    (ws / "contract.yaml").write_bytes(fc.freeze_proper_and_trailer(pb, trailer))
    _manifest(ws, contract_ref="contract.yaml", assignment=_assignment())
    r = _run(ws)
    assert r.returncode == 0, _log(ws)
    wt = r.stdout.strip()
    gd = subprocess.check_output(["git", "-C", wt, "rev-parse", "--absolute-git-dir"], text=True).strip()
    assert json.load(open(os.path.join(gd, "foundry-assignment.json")))["issue_url"] == _assignment()["issue_url"]
    assert json.load(open(os.path.join(wt, ".agent", "assignment.json")))["contract_ref"] == "contract.yaml"


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


def test_skill_states_factory_lane_requires_contract_ref_and_binding_reuse():
    sec = _section(_skill(), "Issue-assignment launch")
    steps = dict(re.findall(r"^(\d+)\. (.*?)(?=^\d+\. |\Z)", sec, flags=re.M | re.S))
    manifest = " ".join(steps["2"].split())
    assert "lane" in manifest and "charter|factory" in manifest
    assert "REQUIRES `contract_ref`" in manifest and "gn-07" in manifest and "gn-16" in manifest
    assert "same `issue_url`" in " ".join(steps["4"].split()) and "reconcile" in steps["4"]
