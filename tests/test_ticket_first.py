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


def _ticket(root, done="true", paper=(), status="open", blocks=0, trusted_by="author", digest=True):
    # the ticket lives in the GIT DIR (never the work tree) — security review of #270, B1
    tf = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "foundry-ticket.json"
    doc = {"issue": 7, "title": "t", "done": done, "paper_allowed": list(paper),
           "status": status, "stop_blocks": blocks, "trusted_by": trusted_by,
           "done_sha256": foundry_ticket.store.command_digest(done) if digest else "bogus"}
    tf.write_text(json.dumps(doc))
    return tf


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
    ok, why = foundry_ticket.author_trusted("o/r", "me", gh, operators=())       # gv-16: the gh user is the agent itself
    assert not ok and "agent itself" in why
    ok, why = foundry_ticket.author_trusted("o/r", "me", gh, operators=("me",))   # ... even a registered operator: the check runs first
    assert not ok and "agent itself" in why
    assert foundry_ticket.author_trusted("o/r", "ann", gh, operators=("ann",))[0]  # control: an operator that is not the gh user
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


def test_stop_hook_ignores_a_work_tree_ticket_file_and_digest_mismatch(tmp_path):
    root = _repo(tmp_path)
    # a committed/checked-out .claude/foundry-ticket.json (a fork could ship one) is never read
    (root / ".claude").mkdir()
    (root / ".claude" / "foundry-ticket.json").write_text(json.dumps(
        {"issue": 1, "done": "touch " + str(root / "PWNED"), "status": "open", "stop_blocks": 0,
         "done_sha256": foundry_ticket.store.command_digest("touch " + str(root / "PWNED"))}))
    assert _hook("foundry-ticket-stop.py", {}, root).returncode == 0
    assert not (root / "PWNED").exists()
    p = _hook("foundry-paper-guard.py", {"tool_name": "Write", "tool_input": {"file_path": str(root / "docs/x.md")}}, root)
    assert p.returncode == 2 and "no active ticket" in p.stderr
    # a git-dir ticket whose command no longer matches its digest is not run
    _ticket(root, done="touch " + str(root / "PWNED2"), digest=False)
    p = _hook("foundry-ticket-stop.py", {}, root)
    assert p.returncode == 0 and "digest" in p.stderr and not (root / "PWNED2").exists()
    # a --trust-author ticket is not auto-run by the Stop hook
    _ticket(root, done="touch " + str(root / "PWNED3"), trusted_by="flag")
    p = _hook("foundry-ticket-stop.py", {}, root)
    assert p.returncode == 0 and "trust-author" in p.stderr and not (root / "PWNED3").exists()


def test_stop_hook_kills_the_process_group_and_clamps_the_timeout(tmp_path):
    root = _repo(tmp_path)
    marker = root / "still-running"
    _ticket(root, done=f"(sleep 20; touch {marker}) >/dev/null 2>&1 & exit 1")
    env = _env(root)
    env["FOUNDRY_TICKET_STOP_TIMEOUT"] = "99999"   # clamped to 300; irrelevant here, command exits fast
    p = subprocess.run([str(HOOKS / "foundry-ticket-stop.py")], input="{}", capture_output=True, text=True,
                       env=env, cwd=str(root), timeout=60)
    assert p.returncode == 2
    import time as _t
    _t.sleep(1.5)
    assert not marker.exists()   # the background sleep was killed with the group


def test_plugin_scripts_allow_gives_no_silent_decision_for_executing_shapes():
    import importlib.util as iu
    spec = iu.spec_from_file_location("psa", HOOKS / "foundry-plugin-scripts-allow.py")
    psa = iu.module_from_spec(spec)
    spec.loader.exec_module(psa)
    root = str(ROOT)
    assert psa.allowed(f"python3 {root}/scripts/foundry-ticket.py status", root) is True
    assert psa.allowed(f"python3 {root}/scripts/foundry-ticket.py done", root) is False
    assert psa.allowed(f"python3 {root}/scripts/foundry-ticket.py start 7 --trust-author", root) is False
    assert psa.allowed(f"{root}/scripts/foundry-test.sh", root) is False


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


# --- security follow-up of #286: the vetted Done-means digest is enforced where the command EXECUTES ------

def _start_world(tmp_path, monkeypatch, body_cmd, editor, claim_cmd="true", author="alice", trusted=("alice",)):
    root = _repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    body = "## Done means\n```\n%s\n```\n" % body_cmd
    issue = {"number": 7, "title": "t", "body": body, "url": "https://github.com/o/r/issues/7",
             "author": {"login": author}, "state": "OPEN"}
    monkeypatch.setattr(foundry_ticket, "_gh_json", lambda a: issue)
    digest = foundry_ticket.store.command_digest(body_cmd)
    monkeypatch.setattr(foundry_ticket, "read_issue", lambda run, repo, n, **k: {"done_sha256": digest, "last_edited_by": editor})
    monkeypatch.setattr(foundry_ticket, "author_trusted", lambda repo, login, gh_json=None: (login in trusted, "w"))

    def _no_gh(run):    # no test may spawn the real gh (CI has no token): a failed lookup is the default
        raise foundry_ticket.QueueUnavailable("stubbed: no gh in tests")
    monkeypatch.setattr(foundry_ticket, "operator_login", _no_gh)
    if claim_cmd is not None:
        cf = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "foundry-claims.json"
        cf.write_text(json.dumps({"o/r#7": {"done_digest": foundry_ticket.store.command_digest(claim_cmd)}}))
    return root


def test_start_refuses_a_post_claim_edit_by_an_untrusted_editor(tmp_path, monkeypatch):
    root = _start_world(tmp_path, monkeypatch, "true", editor="mallory", claim_cmd=None)
    with pytest.raises(SystemExit) as e:
        foundry_ticket.cmd_start(["7"])
    assert "mallory" in str(e.value) and foundry_ticket.load(root) is None


def test_start_refuses_a_changed_command_even_from_a_trusted_author_and_with_the_flag(tmp_path, monkeypatch):
    root = _start_world(tmp_path, monkeypatch, "touch PWNED", editor="alice", claim_cmd="true")
    for argv in (["7"], ["7", "--trust-author"]):
        with pytest.raises(SystemExit) as e:
            foundry_ticket.cmd_start(argv)
        assert "done-means-changed" in str(e.value)
    assert foundry_ticket.load(root) is None


def test_start_happy_path_still_records_and_run_done_executes(tmp_path, monkeypatch):
    root = _start_world(tmp_path, monkeypatch, "true", editor="alice", claim_cmd="true")
    assert foundry_ticket.cmd_start(["7"]) == 0
    doc = foundry_ticket.load(root)
    assert foundry_ticket.run_done(doc, root)[0] == 0


def test_run_done_and_stop_hook_refuse_when_the_stored_command_differs_from_the_claim(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    cmd = "touch " + str(root / "PWNED4")
    tf = _ticket(root, done=cmd)
    doc = json.loads(tf.read_text())
    doc.update({"repo": "o/r", "issue": 7})
    tf.write_text(json.dumps(doc))
    cf = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "foundry-claims.json"
    cf.write_text(json.dumps({"o/r#7": {"done_digest": foundry_ticket.store.command_digest("true")}}))
    rc, why = foundry_ticket.run_done(doc, root)
    assert rc == 125 and "done-means-changed" in why
    p = _hook("foundry-ticket-stop.py", {}, root)
    assert p.returncode == 0 and "done-means-changed" in p.stderr and not (root / "PWNED4").exists()
    # claim digest matching the stored command: the hook runs it
    cf.write_text(json.dumps({"o/r#7": {"done_digest": foundry_ticket.store.command_digest(cmd)}}))
    assert _hook("foundry-ticket-stop.py", {}, root).returncode == 0 and (root / "PWNED4").exists()


# --- review round on #287: claim-key normalisation, the shared (common-dir) claims file, unreadable files ----

def test_claim_key_case_insensitive_both_directions(tmp_path):
    root = _repo(tmp_path)
    st = foundry_ticket.store
    d = st.command_digest("true")
    foundry_ticket.save_claim(root, "Example-Org/Repo#7", {"done_digest": d})
    assert st.claim_done_digest(root, "example-org/repo", 7) == d
    assert st.claim_done_digest(root, "EXAMPLE-ORG/REPO", 7) == d
    other = tmp_path / "r2"
    other.mkdir()
    _git(other, "init", "-q")
    foundry_ticket.save_claim(other, "example-org/repo#7", {"done_digest": d})
    assert st.claim_done_digest(other, "Example-Org/Repo", 7) == d
    assert st.claim_done_digest(other, "example-org/repo", 8) is None


def test_claim_in_main_checkout_protects_a_linked_worktree(tmp_path, monkeypatch):
    main = _repo(tmp_path)
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "w", str(wt))
    st = foundry_ticket.store
    foundry_ticket.save_claim(main, "o/r#7", {"done_digest": st.command_digest("true")})
    assert st.claim_done_digest(wt, "o/r", 7) == st.command_digest("true")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    body_cmd = "touch PWNED5"
    issue = {"number": 7, "title": "t", "body": "## Done means\n```\n%s\n```\n" % body_cmd,
             "url": "https://github.com/O/R/issues/7", "author": {"login": "alice"}, "state": "OPEN"}
    monkeypatch.setattr(foundry_ticket, "_gh_json", lambda a: issue)
    monkeypatch.setattr(foundry_ticket, "read_issue", lambda run, repo, n, **k: {
        "done_sha256": st.command_digest(body_cmd), "last_edited_by": "alice", "name_with_owner": "o/r"})
    monkeypatch.setattr(foundry_ticket, "author_trusted", lambda *a, **k: (True, "w"))
    with pytest.raises(SystemExit) as e:
        foundry_ticket.cmd_start(["7"])
    assert "done-means-changed" in str(e.value) and "--rebase-acceptance" in str(e.value)


def test_legacy_per_worktree_claims_file_is_still_read(tmp_path):
    main = _repo(tmp_path)
    wt = tmp_path / "wt2"
    _git(main, "worktree", "add", "-q", "-b", "w2", str(wt))
    st = foundry_ticket.store
    assert st.legacy_claims_file(wt) != st.claims_file(wt)
    st.legacy_claims_file(wt).parent.mkdir(parents=True, exist_ok=True)
    st.legacy_claims_file(wt).write_text(json.dumps({"o/r#7": {"done_digest": "abc"}}))
    assert not st.claims_file(wt).exists()
    assert st.claim_done_digest(wt, "O/R", 7) == "abc"


def test_unreadable_claims_file_refuses_everywhere_but_absent_means_unclaimed(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    st = foundry_ticket.store
    assert st.claim_done_digest(root, "o/r", 7) is None                       # no file: unclaimed path
    st.claims_file(root).write_text("{not json")
    with pytest.raises(st.ClaimsUnreadable):
        st.claim_done_digest(root, "o/r", 7)
    tf = _ticket(root, done="touch " + str(root / "PWNED6"))
    doc = json.loads(tf.read_text())
    doc.update({"repo": "o/r", "issue": 7})
    tf.write_text(json.dumps(doc))
    rc, why = foundry_ticket.run_done(doc, root)
    assert rc == 125 and "claims-unreadable" in why
    p = _hook("foundry-ticket-stop.py", {}, root)
    assert p.returncode == 0 and "claims-unreadable" in p.stderr and not (root / "PWNED6").exists()
    st.claims_file(root).write_text(json.dumps({"o/r#7": "garbage"}))
    with pytest.raises(st.ClaimsUnreadable):
        st.claim_done_digest(root, "o/r", 7)


def test_start_warns_when_assigned_to_me_but_unclaimed(tmp_path, monkeypatch, capsys):
    root = _start_world(tmp_path, monkeypatch, "true", editor="alice", claim_cmd=None)
    monkeypatch.setattr(foundry_ticket, "read_issue", lambda run, repo, n, **k: {
        "done_sha256": foundry_ticket.store.command_digest("true"), "last_edited_by": "alice", "assignees": ["me"]})
    monkeypatch.setattr(foundry_ticket, "operator_login", lambda run: "me")
    assert foundry_ticket.cmd_start(["7"]) == 0
    assert "no local claim" in capsys.readouterr().err


def test_start_unclaimed_warning_path_survives_a_failed_login_lookup_and_compares_case_insensitively(
        tmp_path, monkeypatch, capsys):
    root = _start_world(tmp_path, monkeypatch, "true", editor="alice", claim_cmd=None)
    assert foundry_ticket.cmd_start(["7"]) == 0           # QueueUnavailable from operator_login: no traceback, no warning
    assert "no local claim" not in capsys.readouterr().err
    monkeypatch.setattr(foundry_ticket, "read_issue", lambda run, repo, n, **k: {
        "done_sha256": foundry_ticket.store.command_digest("true"), "last_edited_by": "alice", "assignees": ["Me"]})
    monkeypatch.setattr(foundry_ticket, "operator_login", lambda run: "ME")
    assert foundry_ticket.cmd_start(["7"]) == 0
    assert "no local claim" in capsys.readouterr().err


def test_save_claim_never_overwrites_an_unreadable_claims_file(tmp_path):
    root = _repo(tmp_path)
    st = foundry_ticket.store
    p = st.claims_file(root)
    p.write_text("{corrupt")
    with pytest.raises(st.ClaimsUnreadable):
        foundry_ticket.save_claim(root, "o/r#7", {"done_digest": "x"})
    assert p.read_text() == "{corrupt"


def test_read_claims_merges_legacy_and_common(tmp_path):
    main = _repo(tmp_path)
    wt = tmp_path / "wa"
    _git(main, "worktree", "add", "-q", "-b", "wa", str(wt))
    st = foundry_ticket.store
    st.legacy_claims_file(wt).parent.mkdir(parents=True, exist_ok=True)
    st.legacy_claims_file(wt).write_text(json.dumps({"o/r#7": {"done_digest": "legacy7"},
                                                     "o/r#9": {"done_digest": "legacy9"}}))
    foundry_ticket.save_claim(main, "o/r#8", {"done_digest": "common8"})
    foundry_ticket.save_claim(main, "o/r#9", {"done_digest": "common9"})
    assert st.claim_done_digest(wt, "o/r", 7) == "legacy7"      # legacy-only key survives a common file
    assert st.claim_done_digest(wt, "o/r", 8) == "common8"
    assert st.claim_done_digest(wt, "o/r", 9) == "common9"      # common wins on collision
    st.legacy_claims_file(wt).write_text("{bad")
    with pytest.raises(st.ClaimsUnreadable):
        st.claim_done_digest(wt, "o/r", 8)


def test_raw_keys_that_normalise_alike_prefer_the_newest_claimed_at(tmp_path):
    root = _repo(tmp_path)
    st = foundry_ticket.store
    st.claims_file(root).write_text(json.dumps({
        "O/R#7": {"done_digest": "new", "claimed_at": "2026-11-02T00:00:00Z"},
        "o/r#7": {"done_digest": "old", "claimed_at": "2026-11-01T00:00:00Z"}}))
    assert st.claim_done_digest(root, "o/R", 7) == "new"


def test_concurrent_claim_writers_do_not_lose_updates(tmp_path):
    import threading
    root = _repo(tmp_path)
    st = foundry_ticket.store
    errs = []

    def writer(i):
        try:
            for j in range(10):
                foundry_ticket.save_claim(root, f"o/r#{i * 100 + j}", {"done_digest": f"d{i}-{j}"})
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=writer, args=(i,)) for i in range(1, 5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    claims, _ = st.read_claims(root)
    assert len(claims) == 40


def test_claim_refuses_when_the_claims_file_is_unreadable(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    foundry_ticket.store.claims_file(root).write_text("{corrupt")
    target = {"repo": "o/r", "number": 7, "assignees": ["me"], "status": "ready", "blocked_by": [], "state": "OPEN"}
    monkeypatch.setattr(foundry_ticket, "load_config", lambda r: {"repos": {"r": "o/r"}, "project_number": None})
    monkeypatch.setattr(foundry_ticket, "parse_ref", lambda i, r, repos: ("o/r", 7))
    monkeypatch.setattr(foundry_ticket, "operator_login", lambda run: "me")
    monkeypatch.setattr(foundry_ticket, "read_queue", lambda run, cfg: [target])
    monkeypatch.setattr(foundry_ticket, "read_issue", lambda *a, **k: {
        "_has_done": False, "done_trusted": True, "done": "", "done_digest": "d", "body_sha256": "b",
        "ticket_digest": "t" * 64, "ticket_sections": {},
        "name_with_owner_raw": "O/R", "name_with_owner": "o/r"})
    rc = foundry_ticket.cmd_claim(["7"], lambda a, stdin=None: "", 0.0)
    assert rc == 2 and "claims-unreadable" in capsys.readouterr().err


def test_claim_emits_the_start_marker_with_the_canonical_case_target(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    target = {"repo": "o/r", "number": 7, "assignees": ["me"], "status": "ready", "blocked_by": [], "state": "OPEN"}
    monkeypatch.setattr(foundry_ticket, "load_config", lambda r: {"repos": {"r": "o/r"}, "project_number": None})
    monkeypatch.setattr(foundry_ticket, "parse_ref", lambda i, r, repos: ("o/r", 7))
    monkeypatch.setattr(foundry_ticket, "operator_login", lambda run: "me")
    monkeypatch.setattr(foundry_ticket, "read_queue", lambda run, cfg: [target])
    monkeypatch.setattr(foundry_ticket, "read_issue", lambda *a, **k: {
        "_has_done": False, "done_trusted": True, "done": "", "done_digest": "d", "body_sha256": "b",
        "ticket_digest": "t" * 64, "ticket_sections": {},
        "name_with_owner_raw": "Org/Repo", "name_with_owner": "org/repo"})
    seen = {}

    class Ev:
        idempotency_key = "k"
    monkeypatch.setattr(foundry_ticket.deck, "emit", lambda ref, ev, payload, adapter=None: seen.update(ref=ref, p=payload) or Ev())
    rc = foundry_ticket.cmd_claim(["7"], lambda a, stdin=None: "", 0.0)
    assert rc == 0, rc
    assert seen["ref"] == "Org/Repo#7" and seen["p"]["target"] == "Org/Repo#7"
    assert foundry_ticket.store.read_claims(root)[0].get("org/repo#7")
