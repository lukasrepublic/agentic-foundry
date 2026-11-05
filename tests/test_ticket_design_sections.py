"""gv-16: `foundry-ticket.py start` parses `## Design`, `## Security`, `## Surfaces` and refuses a
design-required ticket that has no Design citing an existing rule id (`design-required`).

`gh` is never called: `_gh_json` / `read_issue` are patched (start) and a GraphQL stub answers `read-issue`.
Assertions are on parsed structures and exit codes, not on joined text.
"""
import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("ft_design_sections", SCRIPTS / "foundry-ticket.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)

ACCEPTANCE_OK = """
## Acceptance
- AC-1: WHEN the example hook runs, THE SYSTEM SHALL record the design.
Grounding: EARS requirement syntax.
"""

DESIGN_OK = """## Design
- Components: hooks/foundry-example.py, skills/dispatch/
- Reuses: the shared brief renderer
- Cites: FG-SEC-006, FG-QA-001

## Security
- FG-SEC-006

## Surfaces
- cli: the verb is reachable from the command line
- workflows: n/a: no workflow surface
"""


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(r))
    _git(r, "config", "user.email", "t@example.test")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    return r


def policy(repo, paths=(), size=None):
    d = repo / ".foundry"
    d.mkdir(exist_ok=True)
    lines = ["schema_version: 1", "governance:", f"  design_required_paths: {json.dumps(list(paths))}"]
    if size:
        lines.append(f"  size_tag_required: {size}")
    (d / "operating-model.yaml").write_text("\n".join(lines) + "\n")


def start(repo, monkeypatch, body, labels=(), number=7):
    issue = {"number": number, "title": "t", "body": body, "url": "https://github.com/o/r/issues/%d" % number,
             "author": {"login": "alice"}, "state": "OPEN", "labels": [{"name": n} for n in labels]}
    monkeypatch.setattr(ft, "_gh_json", lambda a: issue)
    monkeypatch.setattr(ft, "read_issue", lambda run, repo_, n, **k: {"last_edited_by": "alice", "name_with_owner": "o/r",
                                                                       "editors": ["alice"], "done_sha256": ft.store.command_digest("")})
    monkeypatch.setattr(ft, "author_trusted", lambda r, login, gh_json=None, **k: (True, "w"))
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = ft.cmd_start([str(number)])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                err.write(e.code)
    return code, out.getvalue(), err.getvalue()


def recorded(repo):
    p = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "foundry-ticket.json"
    return json.loads(p.read_text()) if p.is_file() else None


# ---- the parser --------------------------------------------------------------------------------

def test_parse_sections_design_security_surfaces():
    s = ft.parse_sections(DESIGN_OK)
    assert s["design"]["present"] is True
    assert s["design"]["components"] == ["hooks/foundry-example.py", "skills/dispatch"]
    assert s["design"]["cites"] == ["FG-SEC-006", "FG-QA-001"]
    assert s["design"]["reuses"] == "the shared brief renderer"
    assert s["security"] == {"present": True, "ids": ["FG-SEC-006"], "none_reason": None}
    assert [(x["surface"], x["na"]) for x in s["surfaces"]["lines"]] == [("cli", False), ("workflows", True)]


def test_parse_sections_none_and_na_need_a_reason():
    s = ft.parse_sections("## Security\nnone: no boundary is touched\n\n## Surfaces\nn/a: a pure library\n")
    assert s["security"]["none_reason"] == "no boundary is touched" and s["security"]["ids"] == []
    assert s["surfaces"]["na_reason"] == "a pure library"
    bare = ft.parse_sections("## Security\nnone\n\n## Surfaces\nn/a\n")
    assert bare["security"]["none_reason"] is None and bare["surfaces"]["na_reason"] is None


def test_parse_sections_absent():
    s = ft.parse_sections("## Done means\n```\ntrue\n```\n")
    assert s["design"]["present"] is False and s["security"]["present"] is False and s["surfaces"]["present"] is False


def test_nested_component_bullets():
    s = ft.parse_sections("## Design\n- Components:\n  - `src/a.py`\n  - src/b/\n- Cites: FG-QA-001\n")
    assert s["design"]["components"] == ["src/a.py", "src/b"]


# ---- the design-required class ------------------------------------------------------------------

def test_governed_path_without_design_is_refused(repo, monkeypatch):
    policy(repo, paths=["hooks/**"])
    code, out, err = start(repo, monkeypatch, "## Scope\nhooks/foundry-example.py\n")
    assert code != 0 and "design-required" in err and "## Design" in err
    assert recorded(repo) is None


def test_unknown_cited_id_is_refused_naming_it(repo, monkeypatch):
    policy(repo, paths=["hooks/**"])
    body = "## Design\n- Components: hooks/x.py\n- Cites: FG-NOPE-999\n"
    code, out, err = start(repo, monkeypatch, body)
    assert code != 0 and "design-required" in err and "FG-NOPE-999" in err
    assert recorded(repo) is None


def test_design_citing_no_id_is_refused(repo, monkeypatch):
    policy(repo, paths=["hooks/**"])
    code, out, err = start(repo, monkeypatch, "## Design\n- Components: hooks/x.py\n- Reuses: nothing\n")
    assert code != 0 and "design-required" in err and "cites no rule id" in err


def test_governed_path_with_an_existing_id_admits_and_records_the_design(repo, monkeypatch):
    policy(repo, paths=["hooks/**"])
    code, out, err = start(repo, monkeypatch, DESIGN_OK + ACCEPTANCE_OK)
    assert code == 0, err
    doc = recorded(repo)
    assert doc["cites"] == ["FG-SEC-006", "FG-QA-001"]
    assert doc["design"]["components"] == ["hooks/foundry-example.py", "skills/dispatch"]


def test_size_tag_l_is_in_the_class_and_m_is_not(repo, monkeypatch):
    policy(repo)
    code, _, err = start(repo, monkeypatch, "no sections", labels=["size:L"])
    assert code != 0 and "design-required" in err
    code, _, err = start(repo, monkeypatch, "no sections", labels=["size:M"], number=8)
    assert code == 0, err


def test_security_boundary_path_is_in_the_class(repo, monkeypatch):
    policy(repo)
    (repo / ".foundry" / "governance.lock").write_text(json.dumps({
        "blueprint": {"id": "cli-tool", "version": "1", "sha256": "0" * 64}, "kits": [],
        "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": ["src/auth/**"], "surfaces": ["cli"]},
        "facts_sha256": "0" * 64, "intake_at": "2026-01-01", "plugin_version": "2"}))
    code, _, err = start(repo, monkeypatch, "## Scope\nsrc/auth/login.py\n")
    assert code != 0 and "design-required" in err


def test_blueprint_design_required_paths_join_the_class(repo, monkeypatch):
    policy(repo)
    (repo / ".foundry" / "governance.lock").write_text(json.dumps({
        "blueprint": {"id": "claude-plugin", "version": "1", "sha256": "0" * 64}, "kits": [],
        "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": [], "surfaces": ["cli"]},
        "facts_sha256": "0" * 64, "intake_at": "2026-01-01", "plugin_version": "2"}))
    code, _, err = start(repo, monkeypatch, "## Scope\nskills/x/SKILL.md\n")
    assert code != 0 and "design-required" in err


def test_below_the_class_the_sections_are_a_checklist_only(repo, monkeypatch):
    policy(repo, paths=["hooks/**"])
    code, out, err = start(repo, monkeypatch, "## Scope\nsrc/app.py\n")
    assert code == 0, err
    assert "checklist" in (out + err) and "## Design" in (out + err)
    assert recorded(repo)["issue"] == 7


# ---- read-issue exposes the parse ---------------------------------------------------------------

def _gql_stub(body):
    node = {"number": 3, "title": "t", "repository": {"nameWithOwner": "o/r"}, "body": body, "url": "https://github.com/o/r/issues/3",
            "state": "OPEN", "stateReason": None, "author": {"login": "alice"}, "editor": None, "lastEditedAt": None,
            "assignees": {"totalCount": 0, "nodes": []}, "labels": {"totalCount": 0, "nodes": []},
            "blockedBy": {"totalCount": 0, "nodes": []}, "blocking": {"totalCount": 0, "nodes": []}, "parent": None,
            "closedByPullRequestsReferences": {"totalCount": 0, "nodes": []}, "projectItems": {"totalCount": 0, "nodes": []}}

    def run(args, stdin=None):
        if args[:2] == ["api", "graphql"]:
            return json.dumps({"data": {"repository": {"issue": node}}})
        if args[:2] == ["api", "user"]:
            return json.dumps({"login": "alice"})
        return json.dumps({"permission": "write"})
    return run


def test_read_issue_json_exposes_sections_and_applicable_ids(repo, monkeypatch):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "foundry-project.json").write_text(json.dumps({
        "github_scope": {"owner": "o", "planning_repo": "o/r"}, "repos": {"app": "o/r"}}))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ft.cmd_read_issue(["3", "--repo", "o/r", "--json"], _gql_stub(DESIGN_OK))
    assert rc == 0, err.getvalue()
    doc = json.loads(out.getvalue())
    assert doc["design"]["cites"] == ["FG-SEC-006", "FG-QA-001"]
    assert doc["security"]["ids"] == ["FG-SEC-006"]
    assert doc["surfaces"]["lines"][0]["surface"] == "cli"
    assert isinstance(doc["applicable_ids"], list) and "FG-SEC-006" in doc["applicable_ids"]
    assert doc["design_required"] is False          # DESIGN_OK names hooks/ and skills/, but no policy lists them in this workspace
