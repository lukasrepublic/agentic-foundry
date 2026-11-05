"""gv-16 round 2: four fail-open items, each with a failing test and a control.

1 the paper guard fails closed on a payload it cannot read           3 _gate_root: a project file with no github_scope never shadows one with it
2 trust fails closed when `gh api user` fails                        4 governance-trace: author vetted in the PR's repo; cross-repo refs must be listed
No real gh and no network.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "foundry-paper-guard.py"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_ticket_store as store  # noqa: E402
from test_gv16_round1 import _tree, _trace, by  # noqa: E402

_spec = importlib.util.spec_from_file_location("ft_round2", ROOT / "scripts" / "foundry-ticket.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)


# ----------------------------------------------------------------------------------------------- 1 fail closed
def run_raw(stdin, tmp_path):
    return subprocess.run([str(HOOK)], input=stdin, capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path), CLAUDE_PLUGIN_ROOT=str(ROOT)), cwd=str(tmp_path))


@pytest.mark.parametrize("stdin", ["", "{not json", "[]", "[1, 2]", '"a string"', "null", "42",
                                   '{"tool_name": "Edit", "tool_input": "not an object"}',
                                   '{"tool_name": "Bash", "tool_input": ["x"]}',
                                   '{"tool_name": "Write", "tool_input": {"file_path": ["a"]}}'])
def test_a_malformed_payload_exits_2_with_a_reason(tmp_path, stdin):
    p = run_raw(stdin, tmp_path)
    assert p.returncode == 2, (stdin, p.returncode, p.stdout, p.stderr)
    assert "foundry-paper-guard:" in p.stderr and json.loads(p.stdout)["decision"] == "block"
    assert "Traceback" not in p.stderr


def test_well_formed_payloads_are_still_judged_normally_control(tmp_path):
    for payload, code in (({"tool_name": "Read", "tool_input": {"file_path": "x"}}, 0),
                          ({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(tmp_path)}, 0),
                          ({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "src.py")}}, 0),
                          ({"tool_name": "Bash", "tool_input": {"command": "echo x > .github/workflows/ci.yml"}, "cwd": str(tmp_path)}, 2)):
        assert run_raw(json.dumps(payload), tmp_path).returncode == code, payload


def test_the_wiring_description_and_the_documented_limits():
    hooks = (ROOT / "hooks" / "hooks.json").read_text()
    assert "secret-read-guard, paper-guard" in json.loads(hooks)["//"].replace("(CI paths named by a Bash write), ", "")
    doc = HOOK.read_text().split('"""')[1]
    for limit in ("does not track `cd`", "`env -i`", "`sudo -u x`", "--target-directory", "cp -r dir .github/", "rm -rf tests",
                  "echo > package.json", "visibility control"):
        assert limit in doc, limit


# ----------------------------------------------------------------------------------------------- 2 identity lookup
def test_trust_fails_closed_when_the_identity_lookup_fails():
    def dead(args):          # `gh api user` returns nothing (offline, expired token, 403)
        return None if args[:2] == ["api", "user"] else {"permission": "admin"}
    ok, why = ft.author_trusted("o/r", "carol", dead, operators=("carol",))
    assert ok is False and "identity lookup failed" in why
    ok, why = ft.done_trust("o/r", "carol", None, dead, editors=["carol"])
    assert ok is False and "identity lookup failed" in why
    def live(args):
        return {"login": "agent"} if args[:2] == ["api", "user"] else {"permission": "write"}
    assert ft.author_trusted("o/r", "carol", live, operators=())[0] is True                 # control


# ----------------------------------------------------------------------------------------------- 3 gate root
def _ws(root, scope):
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    proj = {"github_scope": {"owner": "o", "planning_repo": "p"}} if scope else {"schema_version": 1}
    (root / ".claude" / "foundry-project.json").write_text(json.dumps(proj))
    return root


def test_a_toplevel_without_github_scope_does_not_shadow_a_project_dir_that_has_it(tmp_path, monkeypatch):
    env_ws = _ws(tmp_path / "env", scope=True)
    plain = _ws(tmp_path / "plain", scope=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env_ws))
    assert store._gate_root(str(plain)) == env_ws.resolve()                       # the team workspace still gates the push
    ok, why = store.claim_gate(str(plain), session_id="X")
    assert "claims are not in use" not in why                                      # it was judged in the team workspace
    scoped = _ws(tmp_path / "scoped", scope=True)
    assert store._gate_root(str(scoped)) == scoped.resolve()                       # control: a toplevel WITH github_scope wins


def test_claims_are_not_in_use_only_when_neither_root_declares_github_scope(tmp_path, monkeypatch):
    env_ws = _ws(tmp_path / "env", scope=False)
    plain = _ws(tmp_path / "plain", scope=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env_ws))
    assert store._gate_root(str(plain)) == plain.resolve()
    assert store.claim_gate(str(plain), session_id="X") == (True, "no github_scope: claims are not in use")
    # a malformed github_scope on either side is treated as declared (fail closed)
    (env_ws / ".claude" / "foundry-project.json").write_text(json.dumps({"github_scope": "oops"}))
    assert store._gate_root(str(plain)) == env_ws.resolve()


# ----------------------------------------------------------------------------------------------- 4 governance-trace
def test_the_author_is_vetted_in_the_prs_repo_and_an_unlisted_ref_repo_is_refused(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    p, doc = _trace(tmp_path, base, base, author="mallory", pr_body="Closes other/repo#7")
    f = by(doc, "ticket-named")[0]
    assert p.returncode == 1 and f["ok"] is False and "other/repo" in f["detail"] and "not listed" in f["detail"]
    log = (tmp_path / "gh.log").read_text()
    assert "repos/o/r/collaborators/mallory/permission" in log and "other/repo" not in log   # the PR's repo, never the ref's


def test_a_listed_ref_repo_is_accepted_and_still_vetted_in_the_prs_repo(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    cfg = json.loads((base / ".claude" / "foundry-project.json").read_text())
    cfg["github_scope"]["repos"] = ["other/repo"]
    (base / ".claude" / "foundry-project.json").write_text(json.dumps(cfg))
    p, doc = _trace(tmp_path, base, base, author="mallory", pr_body="Closes other/repo#7")
    assert by(doc, "ticket-named")[0]["ok"] is True
    assert by(doc, "ticket-author")[0]["ok"] is False and "repos/other/repo" not in (tmp_path / "gh.log").read_text()
    # planning_repo (bare name takes the scope owner) and repos{} slugs count; the PR's own repo and a bare #N always do
    cfg = {"github_scope": {"owner": "o", "planning_repo": "planning", "operators": {"alice": "x"}}, "repos": {"app": {"repo": "o/app"}}}
    (base / ".claude" / "foundry-project.json").write_text(json.dumps(cfg))
    for body, ok in (("Closes o/planning#7", True), ("Closes o/app#7", True), ("Closes o/r#7", True), ("Closes #7", True),
                     ("Closes o/elsewhere#7", False)):
        p, doc = _trace(tmp_path, base, base, author="alice", pr_body=body)
        assert by(doc, "ticket-named")[0]["ok"] is ok, body


def test_the_template_passes_a_token_and_the_previous_filename_of_a_rename():
    text = (ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.yml.template").read_text()
    step = text.split("- name: governance-trace\n")[1]
    assert "GH_TOKEN: ${{ github.token }}" in step and "REPO: ${{ github.repository }}" in step and '--repo "$REPO"' in step
    assert "previous_filename" in text and "ticket-repo.txt" not in text
    assert text.count("permissions:") == 1 and "contents: read" in text           # the token stays read-only


def test_a_rename_out_of_a_governed_directory_is_seen_through_its_old_name(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    # the file list the template builds carries both names of a rename: the old governed one and the new one
    p, doc = _trace(tmp_path, base, base, body="## Done means\n```\ntrue\n```\n", files=("hooks/old.py", "src/new.py"), paged=True)
    assert p.returncode == 1 and by(doc, "design-present")[0]["ok"] is False and "hooks/old.py" in by(doc, "design-present")[0]["detail"]
    p, doc = _trace(tmp_path, base, base, body="## Done means\n```\ntrue\n```\n", files=("src/new.py",), paged=True)   # control
    assert p.returncode == 0
