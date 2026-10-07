"""Ticket-first default (v2.0.0, ticket #269): the ticket script, the paper guard, the stop hook,
the local test runner and git-discipline clause (j), each through its real entry point."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("foundry_ticket", SCRIPTS / "foundry-ticket.py")
foundry_ticket = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(foundry_ticket)


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.test")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


def _env(root):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    return env


def _hook(script, payload, root, args=()):
    return subprocess.run([str(HOOKS / script), *args], input=json.dumps(payload), capture_output=True,
                          text=True, env=_env(root), cwd=str(root), timeout=120)


def _ticket(root, done="true", paper=(), status="open", blocks=0):
    d = root / ".claude"
    d.mkdir(exist_ok=True)
    doc = {"issue": 7, "title": "t", "done": done, "paper_allowed": list(paper),
           "status": status, "stop_blocks": blocks}
    (d / "foundry-ticket.json").write_text(json.dumps(doc))
    return d / "foundry-ticket.json"


# --- parse_body ---------------------------------------------------------------------------

def test_parse_body_reads_done_fence_and_paper_list():
    body = ("## What\nx\n## Done means\n```\npytest -q && ./check.sh\n```\n"
            "## Paper allowed\nCHANGELOG.md, docs/how-to/x.md\n- specs/foo.md\n## Notes\nnone")
    done, paper = foundry_ticket.parse_body(body)
    assert done == "pytest -q && ./check.sh"
    assert paper == ["CHANGELOG.md", "docs/how-to/x.md", "specs/foo.md"]


def test_parse_body_without_sections_is_empty():
    assert foundry_ticket.parse_body("just prose") == ("", [])


def test_parse_body_crlf_unfenced_and_heading_inside_fence():
    crlf = "## Done means\r\n```\r\nmake check\r\n```\r\n## Paper allowed\r\n./docs/a.md — the runbook\r\n"
    assert foundry_ticket.parse_body(crlf) == ("make check", ["docs/a.md"])
    # prose under the heading is never a command
    assert foundry_ticket.parse_body("## Done means\nthe dashboard loads and tests pass\n## Notes\nx")[0] == ""
    # a shell comment line that looks like a heading inside the fence does not cut the block
    body = "## Done means\n```bash\n## build\nmake all\n```\n## Paper allowed\nCHANGELOG.md\n"
    assert foundry_ticket.parse_body(body) == ("## build\nmake all", ["CHANGELOG.md"])


def test_author_trust_uses_collaborator_permission():
    calls = {"api user": {"login": "me"}, "api repos/o/r/collaborators/bob/permission": {"permission": "read"},
             "api repos/o/r/collaborators/ann/permission": {"permission": "write"}}
    gh = lambda args: calls.get(" ".join(args))
    assert foundry_ticket.author_trusted("o/r", "me", gh)[0]
    assert foundry_ticket.author_trusted("o/r", "ann", gh)[0]
    ok, why = foundry_ticket.author_trusted("o/r", "bob", gh)
    assert not ok and "read" in why


# --- paper guard --------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["specs/features/x/feat-x.md", ".foundry/releases/r/release.yaml",
                                 "docs/plan.md", "status-reports/w40.md", "charters/c.md",
                                 "any/where/acceptance-contract.yaml"])
def test_paper_guard_refuses_paper_without_ticket(tmp_path, rel):
    root = _repo(tmp_path)
    p = _hook("foundry-paper-guard.py", {"tool_name": "Write", "tool_input": {"file_path": str(root / rel)}}, root)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "no active ticket" in p.stderr and "no active ticket" in p.stdout


@pytest.mark.parametrize("rel", ["src/app.py", "infra/main.tf", "tests/test_a.py", "CLAUDE.md",
                                 "k8s/deploy.yaml", ".claude/settings.json"])
def test_paper_guard_admits_code_and_config(tmp_path, rel):
    root = _repo(tmp_path)
    p = _hook("foundry-paper-guard.py", {"tool_name": "Edit", "tool_input": {"file_path": str(root / rel)}}, root)
    assert p.returncode == 0, p.stdout + p.stderr


def test_paper_guard_honours_ticket_allow_list(tmp_path):
    root = _repo(tmp_path)
    _ticket(root, paper=["docs/how-to", "CHANGELOG.md"])
    ok = _hook("foundry-paper-guard.py", {"tool_name": "Write", "tool_input": {"file_path": str(root / "docs/how-to/x.md")}}, root)
    assert ok.returncode == 0
    no = _hook("foundry-paper-guard.py", {"tool_name": "Write", "tool_input": {"file_path": str(root / "docs/other.md")}}, root)
    assert no.returncode == 2 and "ticket #7 does not allow" in no.stderr


def test_paper_guard_ignores_paths_outside_project(tmp_path):
    root = _repo(tmp_path)
    outside = tmp_path / "elsewhere" / "docs" / "x.md"
    p = _hook("foundry-paper-guard.py", {"tool_name": "Write", "tool_input": {"file_path": str(outside)}}, root)
    assert p.returncode == 0


# --- stop hook ----------------------------------------------------------------------------

def test_stop_hook_refuses_while_done_means_fails_then_caps(tmp_path):
    root = _repo(tmp_path)
    tf = _ticket(root, done="false")
    for n in (1, 2, 3):
        p = _hook("foundry-ticket-stop.py", {"session_id": "s"}, root)
        assert p.returncode == 2, (n, p.stdout, p.stderr)
        assert f"refusal {n}/3" in p.stderr
    p = _hook("foundry-ticket-stop.py", {"session_id": "s"}, root)
    assert p.returncode == 0
    assert json.loads(tf.read_text())["stop_blocks"] == 3


def test_stop_hook_marks_done_and_admits_when_check_passes(tmp_path):
    root = _repo(tmp_path)
    tf = _ticket(root, done="true")
    p = _hook("foundry-ticket-stop.py", {"session_id": "s"}, root)
    assert p.returncode == 0
    assert json.loads(tf.read_text())["status"] == "done"


def test_stop_hook_counts_a_timeout_as_a_refusal(tmp_path):
    root = _repo(tmp_path)
    tf = _ticket(root, done="sleep 30")
    env = _env(root)
    env["FOUNDRY_TICKET_STOP_TIMEOUT"] = "1"
    p = subprocess.run([str(HOOKS / "foundry-ticket-stop.py")], input="{}", capture_output=True, text=True,
                       env=env, cwd=str(root), timeout=60)
    assert p.returncode == 2 and "timed out" in p.stderr
    assert json.loads(tf.read_text())["stop_blocks"] == 1


def test_stop_hook_is_fail_open_without_ticket_or_command(tmp_path):
    root = _repo(tmp_path)
    assert _hook("foundry-ticket-stop.py", {}, root).returncode == 0
    _ticket(root, done="")
    assert _hook("foundry-ticket-stop.py", {}, root).returncode == 0


# --- foundry-test.sh + clause (j) -----------------------------------------------------------

def _discipline(root, command):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(root)}
    return subprocess.run([str(HOOKS / "foundry-git-discipline.sh")], input=json.dumps(payload),
                          capture_output=True, text=True, env=_env(root), cwd=str(root), timeout=120)


def test_foundry_test_writes_marker_for_head(tmp_path):
    root = _repo(tmp_path)
    env = _env(root)
    env["FOUNDRY_TEST_CMD"] = "true"
    p = subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    marker = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "foundry-local-green"
    assert marker.read_text().split()[0] == _git(root, "rev-parse", "HEAD")
    env["FOUNDRY_TEST_CMD"] = "false"
    p = subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode != 0 and not marker.exists()


def test_foundry_test_refuses_a_dirty_tree_and_finds_a_taskfile(tmp_path):
    root = _repo(tmp_path)
    env = _env(root)
    env.pop("FOUNDRY_TEST_CMD", None)
    (root / "Taskfile.yml").write_text("version: '3'\ntasks:\n  ci:\n    cmds:\n      - true\n")
    p = subprocess.run([str(SCRIPTS / "foundry-test.sh"), "--print-cmd"], cwd=root, env=env, capture_output=True, text=True)
    assert p.stdout.strip() == "task ci"
    (root / "a.py").write_text("x = 2\n")  # tracked file modified → dirty
    env["FOUNDRY_TEST_CMD"] = "true"
    p = subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode == 3 and "uncommitted" in p.stderr
    marker = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "foundry-local-green"
    assert not marker.exists()


def test_notests_marker_is_named_untested_in_the_admission(tmp_path):
    root = _repo(tmp_path)
    env = _env(root)
    env.pop("FOUNDRY_TEST_CMD", None)
    p = subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode == 0 and "UNTESTED" in p.stderr
    assert _discipline(root, "git push origin feature-1").returncode == 0


def test_push_after_cd_or_git_dir_is_unresolvable_and_refused(tmp_path):
    root = _repo(tmp_path)
    env = _env(root)
    env["FOUNDRY_TEST_CMD"] = "true"
    subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, check=True, capture_output=True)
    p = _discipline(root, "cd /tmp && git push origin feature-1")
    assert p.returncode == 2 and "directory change" in (p.stdout + p.stderr)
    p = _discipline(root, "git --git-dir=/tmp/x/.git push origin feature-1")
    assert p.returncode == 2 and "unresolvable" in (p.stdout + p.stderr)


def test_gh_repo_selector_does_not_hide_pr_create(tmp_path):
    root = _repo(tmp_path)
    p = _discipline(root, "gh -R owner/repo pr create --title t --body b")
    assert p.returncode == 2 and "non-draft" in (p.stdout + p.stderr)
    assert _discipline(root, "gh -R owner/repo pr create --draft --title t --body b").returncode == 0


def test_push_refused_without_local_green_and_admitted_with_it(tmp_path):
    root = _repo(tmp_path)
    p = _discipline(root, "git push origin feature-1")
    assert p.returncode == 2, p.stdout + p.stderr
    assert "no local-green marker" in (p.stdout + p.stderr)
    env = _env(root)
    env["FOUNDRY_TEST_CMD"] = "true"
    subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, check=True, capture_output=True)
    p = _discipline(root, "git push origin feature-1")
    assert p.returncode == 0, p.stdout + p.stderr
    # a new commit invalidates the marker
    (root / "b.py").write_text("y = 2\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "more")
    p = _discipline(root, "git push origin feature-1")
    assert p.returncode == 2 and "but HEAD is" in (p.stdout + p.stderr)


@pytest.mark.parametrize("cmd", ["git push origin --delete old-branch", "git push -d origin old",
                                 "git push --dry-run origin feature", "git push origin --tags",
                                 "git push origin :gone"])
def test_push_maintenance_forms_need_no_marker(tmp_path, cmd):
    root = _repo(tmp_path)
    p = _discipline(root, cmd)
    assert p.returncode == 0, cmd + "\n" + p.stdout + p.stderr


@pytest.mark.parametrize("cmd", ["git push --tags origin mybranch", "git push --prune origin refs/heads/x:refs/heads/x"])
def test_push_of_commits_dressed_as_maintenance_still_needs_marker(tmp_path, cmd):
    root = _repo(tmp_path)
    assert _discipline(root, cmd).returncode == 2


def test_pr_create_draft_admitted_non_draft_needs_marker(tmp_path):
    root = _repo(tmp_path)
    assert _discipline(root, "gh pr create --draft --title t --body b").returncode == 0
    p = _discipline(root, "gh pr create --title t --body b")
    assert p.returncode == 2 and "non-draft" in (p.stdout + p.stderr)
    env = _env(root)
    env["FOUNDRY_TEST_CMD"] = "true"
    subprocess.run([str(SCRIPTS / "foundry-test.sh")], cwd=root, env=env, check=True, capture_output=True)
    assert _discipline(root, "gh pr create --title t --body b").returncode == 0


def test_local_green_off_switch(tmp_path):
    root = _repo(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": "git push origin feature-1"}, "cwd": str(root)}
    p = subprocess.run([str(HOOKS / "foundry-git-discipline.sh"), "--local-green=off"], input=json.dumps(payload),
                       capture_output=True, text=True, env=_env(root), cwd=str(root), timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_hooks_json_wires_the_three_entry_points():
    h = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    cmds = json.dumps(h)
    assert "foundry-paper-guard.py" in cmds and "foundry-ticket-stop.py" in cmds
    assert any("fork" in (e.get("matcher") or "") for e in h["SessionStart"])


# --- cut-release cadence --------------------------------------------------------------------

def test_release_cadence_refuses_within_30_days_unless_hotfix(tmp_path):
    import datetime as dt
    spec = importlib.util.spec_from_file_location("fcr", SCRIPTS / "foundry-cut-release.py")
    fcr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fcr)
    root = _repo(tmp_path)
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    ok, detail = fcr.release_cadence(str(root), "1.1.0")
    assert not ok and "cadence floor is 30 days" in detail
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=31)
    ok, _ = fcr.release_cadence(str(root), "1.1.0", now=later)
    assert ok
    r = fcr.cut_release(str(root), "1.1.0", acceptance_fn=lambda tree: {"verdict": "pass"},
                        er_state_fn=lambda ers, repo=None: {}, suite_runner=lambda t: (0, "", ""))
    assert r["state"] == "refused" and r["stage"] == "cadence"
    (tmp_path / "u").mkdir()
    untagged = _repo(tmp_path / "u")
    assert fcr.release_cadence(str(untagged), "1.0.0")[0]
    # the tag of the version being cut is ignored (a re-run must not refuse itself); a lightweight
    # tag counts; a non-release tag (no v<digits>) is ignored
    _git(root, "tag", "v1.1.0")
    _git(root, "tag", "staging-20261007")
    ok, detail = fcr.release_cadence(str(root), "1.1.0")
    assert not ok and "v1.0.0" in detail
