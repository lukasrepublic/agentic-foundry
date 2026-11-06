"""gv-16: the filed follow-ups routed to this atom, each with a failing test and a control.

(a) trust of the issue author / editors (scripts/foundry-ticket.py)
(b) the signed CI-allow lists + the paper guard's CI class (hooks/foundry-paper-guard.py)
(c) URL-userinfo redaction in scripts/foundry_queue_land.py
(d) the claim gate holds THIS session's lease; reconcile does not heartbeat another session; the gate root
No real gh and no network: `gh` is a stub or a patched function; the argv logs prove it.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(SCRIPTS))
import foundry_ticket_store as store  # noqa: E402

_spec = importlib.util.spec_from_file_location("ft_gv16", SCRIPTS / "foundry-ticket.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / ".github" / "workflows").mkdir(parents=True)
    (r / ".github" / "workflows" / "ci.yml").write_text("jobs: {}\n")
    (r / "src").mkdir()
    (r / "web").mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(r))
    _git(r, "config", "user.email", "t@example.test")
    _git(r, "config", "user.name", "t")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return r


def gitdir(repo):
    return Path(_git(repo, "rev-parse", "--absolute-git-dir"))


def env_for(repo):
    return dict(os.environ, CLAUDE_PROJECT_DIR=str(repo), CLAUDE_PLUGIN_ROOT=str(ROOT))


def guard(repo, payload):
    return subprocess.run([str(HOOKS / "foundry-paper-guard.py")], input=json.dumps(payload), capture_output=True,
                          text=True, env=env_for(repo), cwd=str(repo), timeout=60)


def edit(repo, rel, **ti):
    return guard(repo, {"tool_name": "Edit", "tool_input": dict({"file_path": str(repo / rel)}, **ti)})


def bash(repo, command):
    return guard(repo, {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)})


def write_ticket(repo, **fields):
    doc = {"issue": 7, "repo": "o/r", "title": "t", "done": "true", "paper_allowed": [], "status": "open",
           "stop_blocks": 0, "trusted_by": "author"}
    doc.update(fields)
    (gitdir(repo) / "foundry-ticket.json").write_text(json.dumps(doc))
    return doc


def cli(repo, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / "foundry-ticket.py"), *args], capture_output=True, text=True,
                          env=env_for(repo), cwd=str(repo), timeout=60)


# =============================================================================================== (a) trust
def gh_user(login, perms=None):
    def f(args):
        if args[:2] == ["api", "user"]:
            return {"login": login}
        if args[0] == "api" and "collaborators" in args[1]:
            who = args[1].split("/collaborators/")[1].split("/")[0]
            return {"permission": (perms or {}).get(who, "read")}
        return None
    return f


def test_the_authenticated_gh_user_is_not_trusted_by_that_fact(repo):
    ok, why = ft.author_trusted("o/r", "agent", gh_user("agent", {"agent": "admin"}))
    assert ok is False and "agent itself" in why


def test_a_registered_operator_is_trusted_unless_it_is_the_gh_user(repo):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "o", "planning_repo": "o/p", "operators": {"alice": "op_a"}}}))
    ok, why = ft.author_trusted("o/r", "Alice", gh_user("agent"))
    assert ok is True and "registered operator" in why
    # round 1: the agent-identity check runs FIRST, so a registered operator that IS the gh user is not trusted for its own bodies
    ok, why = ft.author_trusted("o/r", "Alice", gh_user("alice"))
    assert ok is False and "agent itself" in why and "nominal" in why


def test_the_operator_registry_github_handle_counts(repo):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "foundry-operators.json").write_text(json.dumps(
        {"schema_version": 1, "operators": {"op_b": {"name": "B", "github": "bob", "added_at": "2026-01-01"}}}))
    assert ft.author_trusted("o/r", "bob", gh_user("agent"))[0] is True
    assert ft.author_trusted("o/r", "mallory", gh_user("agent", {"mallory": "triage"}))[0] is False   # not listed, no write access


def test_a_write_collaborator_other_than_the_agent_is_still_trusted_control(repo):
    assert ft.author_trusted("o/r", "carol", gh_user("agent", {"carol": "write"}))[0] is True
    assert ft.author_trusted("o/r", "dave", gh_user("agent", {"dave": "read"}))[0] is False


def test_done_trust_considers_every_editor_not_only_the_last(repo):
    trusted = gh_user("agent", {"alice": "write", "bob": "write", "mallory": "triage"})
    # an untrusted account edited the body and a trusted one re-saved it: the LAST editor alone would pass
    ok, why = ft.done_trust("o/r", "alice", "bob", trusted, editors=["alice", "mallory", "bob"])
    assert ok is False and "mallory" in why
    assert ft.done_trust("o/r", "alice", "bob", trusted, editors=["alice", "bob"])[0] is True       # control


def test_done_trust_fails_closed_when_the_history_is_unreadable_and_someone_else_edited(repo):
    trusted = gh_user("agent", {"alice": "write", "bob": "write"})
    ok, why = ft.done_trust("o/r", "alice", "bob", trusted, editors=None)
    assert ok is False and "edit history is not readable" in why
    assert ft.done_trust("o/r", "alice", "alice", trusted, editors=None)[0] is True                  # control: author only
    assert ft.done_trust("o/r", "alice", None, trusted, editors=None)[0] is True                     # never edited
    assert ft.done_trust("o/r", "alice", None, trusted, editors=None, edited_at="2026-01-01T00:00:00Z")[0] is False


def test_normalize_reads_the_edit_history_and_marks_an_unreadable_one_none(repo):
    base = {"number": 1, "title": "t", "repository": {"nameWithOwner": "o/r"}, "body": "b", "url": "u", "state": "OPEN",
            "author": {"login": "a"}, "assignees": {"nodes": []}, "labels": {"nodes": []}, "blockedBy": {"nodes": []},
            "blocking": {"nodes": []}, "closedByPullRequestsReferences": {"nodes": []}, "projectItems": {"nodes": []}}
    ok = dict(base, userContentEdits={"totalCount": 2, "nodes": [{"editor": {"login": "a"}}, {"editor": {"login": "m"}}]})
    assert ft.normalize(ok, "o/r", None)["editors"] == ["a", "m"]
    assert ft.normalize(base, "o/r", None)["editors"] is None
    trunc = dict(base, userContentEdits={"totalCount": 9, "nodes": [{"editor": {"login": "a"}}]})
    assert ft.normalize(trunc, "o/r", None)["editors"] is None
    ghost = dict(base, userContentEdits={"totalCount": 1, "nodes": [{"editor": None}]})
    assert ft.normalize(ghost, "o/r", None)["editors"] is None


def _start(repo, monkeypatch, author, body, trust, editors=None, trusted=("alice",)):
    issue = {"number": 7, "title": "t", "body": body, "url": "https://github.com/o/r/issues/7",
             "author": {"login": author}, "state": "OPEN", "labels": []}
    monkeypatch.setattr(ft, "_gh_json", lambda a: issue)
    monkeypatch.setattr(ft, "read_issue", lambda run, repo_, n, **k: {
        "last_edited_by": author, "name_with_owner": "o/r", "editors": editors if editors is not None else [author],
        "done_sha256": store.command_digest(ft.parse_body(body)[0])})
    monkeypatch.setattr(ft, "author_trusted", lambda r, login, gh_json=None, **k: (login in trusted, "stub"))
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = ft.cmd_start(["7", "--trust-author"] if trust else ["7"])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            err.write(str(e.code))
    doc = json.loads((gitdir(repo) / "foundry-ticket.json").read_text()) if (gitdir(repo) / "foundry-ticket.json").exists() else None
    return code, out.getvalue(), err.getvalue(), doc


BODY = "## Done means\n```\ntrue\n```\n## Paper allowed\n.github/workflows\n"


def test_trust_author_does_not_copy_an_untrusted_authors_paper_list_and_says_so(repo, monkeypatch):
    code, out, err, doc = _start(repo, monkeypatch, "mallory", BODY, trust=True)
    assert code == 0, err
    assert doc["issue_referenced_artifacts"] == [] and doc["referenced_artifacts"] == [".github/workflows"]
    assert "NOT copied into issue_referenced_artifacts" in out and ".github/workflows" in out
    assert edit(repo, ".github/workflows/ci.yml").returncode == 2                 # the CI guard is still in force


def test_a_trusted_authors_paper_list_is_copied_with_trust_author_control(repo, monkeypatch):
    code, out, err, doc = _start(repo, monkeypatch, "alice", BODY, trust=True)
    assert code == 0, err
    assert doc["issue_referenced_artifacts"] == [".github/workflows"] and "NOT copied" not in out
    assert edit(repo, ".github/workflows/ci.yml").returncode == 0


def test_an_untrusted_earlier_editor_blocks_the_paper_lift_and_the_command(repo, monkeypatch):
    code, out, err, doc = _start(repo, monkeypatch, "alice", BODY, trust=False, editors=["alice", "mallory", "alice"],
                                 trusted=("alice",))
    assert code != 0 and "mallory" in err and doc is None           # the command is not adopted at all
    code, out, err, doc = _start(repo, monkeypatch, "alice", "## Paper allowed\n.github/workflows\n", trust=False,
                                 editors=["alice", "mallory", "alice"], trusted=("alice",))
    assert code == 0 and doc["issue_referenced_artifacts"] == [] and "NOT copied" in out


# =============================================================================================== (b) signing
def test_ci_allow_signs_the_lists_and_the_guard_honours_a_valid_signature(repo):
    write_ticket(repo)
    p = cli(repo, "ci-allow", "setup.cfg")
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads((gitdir(repo) / "foundry-ticket.json").read_text())
    assert doc["ci_allowed"] == ["setup.cfg"] and isinstance(doc.get("lists_sig"), str)
    assert (gitdir(repo) / "foundry-ticket.key").exists()
    assert oct((gitdir(repo) / "foundry-ticket.key").stat().st_mode & 0o777) == "0o600"
    assert edit(repo, "setup.cfg").returncode == 0


def test_a_hand_edit_of_ci_allowed_lifts_nothing(repo):
    write_ticket(repo, ci_allowed=[".github/workflows/ci.yml"])                    # no signature at all
    r = edit(repo, ".github/workflows/ci.yml")
    assert r.returncode == 2 and "no valid signature" in r.stderr
    # a signed list, then a hand edit that adds a path: the signature no longer matches
    write_ticket(repo)
    assert cli(repo, "ci-allow", "setup.cfg").returncode == 0
    f = gitdir(repo) / "foundry-ticket.json"
    doc = json.loads(f.read_text())
    doc["ci_allowed"].append(".github/workflows/ci.yml")
    f.write_text(json.dumps(doc))
    assert edit(repo, ".github/workflows/ci.yml").returncode == 2
    assert edit(repo, "setup.cfg").returncode == 2                                  # the whole list is void, not just the new path


def test_a_hand_edit_of_issue_paper_allowed_lifts_nothing_and_a_signed_one_does(repo, monkeypatch):
    write_ticket(repo, issue_paper_allowed=["pyproject.toml"])
    assert edit(repo, "pyproject.toml").returncode == 2
    code, out, err, doc = _start(repo, monkeypatch, "alice", "## Paper allowed\npyproject.toml\n", trust=False)
    assert code == 0, err
    assert doc["issue_referenced_artifacts"] == ["pyproject.toml"] and doc.get("lists_sig")
    assert edit(repo, "pyproject.toml").returncode == 0                              # control: start signed it
    doc["issue_referenced_artifacts"].append("setup.cfg")
    (gitdir(repo) / "foundry-ticket.json").write_text(json.dumps(doc))
    assert edit(repo, "setup.cfg").returncode == 2 and edit(repo, "pyproject.toml").returncode == 2


def test_a_signature_does_not_travel_to_another_git_dir(repo, tmp_path):
    write_ticket(repo)
    cli(repo, "ci-allow", "setup.cfg")
    doc = json.loads((gitdir(repo) / "foundry-ticket.json").read_text())
    other = tmp_path / "other"
    other.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(other))
    assert store.verify_ticket_lists(doc, other) is False                            # a different key (or none)


def test_the_paper_allowed_list_still_never_lifts_ci(repo):
    write_ticket(repo, paper_allowed=["setup.cfg"])
    assert edit(repo, "setup.cfg").returncode == 2


@pytest.mark.parametrize("cmd", [
    "sed -i 's/a/b/' .github/workflows/ci.yml",
    "sed -i.bak -e 's/a/b/' .github/workflows/ci.yml",
    "echo x | tee .github/workflows/ci.yml",
    "echo x | tee -a setup.cfg",
    "echo x > .github/workflows/ci.yml",
    "echo x >> pyproject.toml",
    "printf x 2>/dev/null >Makefile",
    "cp /tmp/x .github/workflows/ci.yml",
    "mv /tmp/x codecov.yml",
    "rm .github/workflows/ci.yml",
    "truncate -s 0 setup.cfg",
    "dd if=/dev/zero of=pyproject.toml",
    "cd src && echo x > /nonexistent && sed -i s/a/b/ ../pyproject.toml",
])
def test_bash_edits_of_a_ci_path_are_refused(repo, cmd):
    r = bash(repo, cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)
    assert "CI path" in r.stderr


@pytest.mark.parametrize("cmd", [
    "cat .github/workflows/ci.yml", "grep -n run .github/workflows/ci.yml", "echo x > src/app.py",
    "sed -i 's/a/b/' src/app.py", "tee src/out.txt", "cp .github/workflows/ci.yml src/copy.yml", "ls .github",
    "echo hi > /dev/null", "git status", "python3 -m pytest tests -q 2>&1 | tail -5",
])
def test_bash_reads_and_ordinary_writes_stay_admitted(repo, cmd):
    r = bash(repo, cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_a_signed_ci_allow_admits_the_bash_edit_too(repo):
    write_ticket(repo)
    assert cli(repo, "ci-allow", "setup.cfg").returncode == 0
    assert bash(repo, "sed -i s/a/b/ setup.cfg").returncode == 0
    assert bash(repo, "sed -i s/a/b/ pyproject.toml").returncode == 2


def test_the_file_path_is_resolved_not_only_its_parent(repo):
    link = repo / "src" / "innocent.yml"
    link.symlink_to(repo / ".github" / "workflows" / "ci.yml")
    r = edit(repo, "src/innocent.yml")
    assert r.returncode == 2 and ".github/workflows/ci.yml" in r.stderr
    assert bash(repo, "echo x > src/innocent.yml").returncode == 2
    (repo / "src" / "plain.txt").write_text("x")
    assert edit(repo, "src/plain.txt").returncode == 0                               # control
    outside = repo.parent / "outside.txt"
    outside.write_text("x")
    (repo / "src" / "out-link").symlink_to(outside)
    assert edit(repo, "src/out-link").returncode == 0                                # lands outside the project: not our business


@pytest.mark.parametrize("rel", ["codecov.yml", ".nycrc", ".nycrc.json", "web/cypress.config.ts", "vitest.workspace.ts",
                                 "web/vitest.workspace.js", "svc/pyproject.toml", "a/b/pyproject.toml", "GNUmakefile",
                                 "makefile"])
def test_more_ci_paths(repo, rel):
    r = edit(repo, rel)
    assert r.returncode == 2 and "CI path" in r.stderr, rel


def test_package_json_scripts_jest_and_vitest_blocks_are_ci(repo):
    pkg = {"name": "x", "scripts": {"build": "tsc", "lint": "eslint ."}, "jest": {"testEnvironment": "node"},
           "vitest": {"globals": True}, "dependencies": {"left-pad": "1.0.0"}}
    (repo / "package.json").write_text(json.dumps(pkg, indent=2) + "\n")
    # a snippet that sits INSIDE the scripts block and never names it
    assert edit(repo, "package.json", old_string='"build": "tsc"', new_string='"build": "true"').returncode == 2
    assert edit(repo, "package.json", old_string='"testEnvironment": "node"', new_string='"testEnvironment": "jsdom"').returncode == 2
    assert edit(repo, "package.json", old_string='"globals": true', new_string='"globals": false').returncode == 2
    assert edit(repo, "package.json", old_string='"left-pad": "1.0.0"', new_string='"left-pad": "1.1.0"').returncode == 0   # control
    changed = dict(pkg, jest={"testEnvironment": "jsdom"})
    assert guard(repo, {"tool_name": "Write", "tool_input": {"file_path": str(repo / "package.json"),
                                                            "content": json.dumps(changed)}}).returncode == 2
    same_but_deps = dict(pkg, dependencies={"left-pad": "2.0.0"})
    assert guard(repo, {"tool_name": "Write", "tool_input": {"file_path": str(repo / "package.json"),
                                                            "content": json.dumps(same_but_deps)}}).returncode == 0


# =============================================================================================== (c) redaction
TOKEN = "gh" + "p_" + "A" * 24            # built from fragments: not a real credential


def test_refused_reason_and_json_pass_through_the_url_userinfo_redaction():
    import foundry_queue_land as ql
    msg = f"git push failed (fatal: unable to access 'https://bob:{TOKEN}@github.com/o/r.git/': 403) GH_TOKEN={TOKEN}"
    exc = ql.Refused(msg, detail=f"remote https://bob:{TOKEN}@github.com/o/r")
    assert TOKEN not in exc.reason and "bob:" not in exc.reason and "github.com/o/r.git" in exc.reason
    assert TOKEN not in json.dumps(exc.extra) and "bob:" not in json.dumps(exc.extra)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ql.emit("refused", reason=msg, nested={"x": [f"https://u:{TOKEN}@h/"]})
    assert TOKEN not in buf.getvalue() and "u:" not in json.loads(buf.getvalue())["nested"]["x"][0]


def test_queue_land_end_to_end_never_prints_the_userinfo(tmp_path):
    from test_queue_land import Env
    env = Env(tmp_path)
    env.arm()
    sc = json.loads(env.scenario.read_text())
    first = sc["responses"][0]
    first.pop("stdout", None)
    first.update(rc=1, stderr=f"fatal: unable to access 'https://bob:{TOKEN}@github.com/o/r/': The requested URL returned error: 403")
    env.scenario.write_text(json.dumps(sc))
    p, lines = env.run()
    assert p.returncode == 2
    assert TOKEN not in p.stdout + p.stderr and "bob:" not in p.stdout + p.stderr
    assert lines[-1]["status"] == "refused" and "github.com/o/r" in lines[-1]["reason"]


# =============================================================================================== (d) claims
H = 3600


def _team(tmp_path, name="team"):
    root = tmp_path / name
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feature-x", str(root)], check=True)
    (root / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "example-org", "planning_repo": "planning"}}))
    store.ticket_file(root).write_text(json.dumps({"issue": 2, "repo": "example-org/app", "status": "open"}))
    return root


def _claim(root, session, age=60):
    stamp = store.iso(time.time() - age)
    r = {"issue": "example-org/app#2", "number": 2, "repo": "example-org/app", "operator": "alice", "claimed_at": stamp,
         "heartbeat_at": stamp, "session_id": session, "expires_after_s": 2 * H}
    store.update_claims(root, lambda cl: cl.__setitem__("example-org/app#2", r))


def test_claim_gate_requires_THIS_sessions_live_lease(tmp_path):
    root = _team(tmp_path)
    _claim(root, "A")
    assert store.claim_gate(str(root), session_id="A") == (True, "live claim on example-org/app#2")      # control
    ok, why = store.claim_gate(str(root), session_id="B")
    assert not ok and why.startswith("claim-required:") and "held by session A" in why
    ok, why = store.claim_gate(str(root), session_id="")
    assert not ok and "held by session A" in why


def test_claim_gate_through_the_hook_uses_the_ambient_session(tmp_path):
    root = _team(tmp_path)
    _claim(root, "A")
    hook = ROOT / "hooks" / "foundry-git-discipline.sh"

    def run(sid):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), CLAUDE_CODE_SESSION_ID=sid)
        return subprocess.run([str(hook), "--local-green=off"], capture_output=True, text=True, env=env,
                              input=json.dumps({"tool_input": {"command": "git push -u origin feature-x"}, "cwd": str(root)}))
    assert run("A").returncode == 0
    r = run("B")
    assert r.returncode == 2 and "claim-required" in r.stdout + r.stderr


def test_gate_root_prefers_the_pushing_cwds_toplevel_over_the_project_dir(tmp_path, monkeypatch):
    a, b = _team(tmp_path, "a"), _team(tmp_path, "b")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    assert store._gate_root(str(b)) == b.resolve()                                    # the cwd's repo carries a project file: it wins
    sub = b / "deep"
    sub.mkdir()
    assert store._gate_root(str(sub)) == b.resolve()                                  # its toplevel, not the cwd itself
    clone = tmp_path / "product-clone"                                                # round 1: a clone with NO project file
    clone.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    assert store._gate_root(str(clone)) == a.resolve()                                # ... is still gated by the workspace
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert store._gate_root(str(clone)) == clone.resolve()                            # no project dir at all: the toplevel
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    assert store._gate_root(str(tmp_path / "not-a-repo-dir-xyz")) == a.resolve()      # control: no repo at the cwd
    assert store._gate_root(None) == a.resolve()


def test_reconcile_with_another_sessions_id_does_not_refresh_its_heartbeat(tmp_path, monkeypatch):
    root = _team(tmp_path)
    _claim(root, "B", age=H)                                    # B's lease, an hour old
    before = store.read_claims(root)[0]["example-org/app#2"]["heartbeat_at"]
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "A")           # THIS session is A
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ft.cmd_reconcile(["--session-id", "B", "--json"], lambda a, stdin=None: (_ for _ in ()).throw(ft.GhError("offline")),
                              time.time())
    assert rc in (0, 3)
    assert store.read_claims(root)[0]["example-org/app#2"]["heartbeat_at"] == before
    assert "heartbeat_refused" in json.loads(out.getvalue()) and "not this session" in err.getvalue()
    # control: its own id refreshes
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        ft.cmd_reconcile(["--session-id", "A", "--json"], lambda a, stdin=None: (_ for _ in ()).throw(ft.GhError("offline")), time.time())
    _claim(root, "A", age=H)
    stale = store.read_claims(root)[0]["example-org/app#2"]["heartbeat_at"]
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        ft.cmd_reconcile(["--json"], lambda a, stdin=None: (_ for _ in ()).throw(ft.GhError("offline")), time.time())
    assert store.read_claims(root)[0]["example-org/app#2"]["heartbeat_at"] != stale
