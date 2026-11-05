"""gv-16 round 1: the review's Block and Risks, each with a failing test and a control.

1 hooks.json wires the paper guard into the Bash matcher group      5 claim gate / reconcile with no ambient session id
2 ci-allow verifies the signature before renewing it                6 the agent-identity check runs first
3 Bash parsing: newlines, directory destinations, link+target,      7 governance-trace: author, broad components, base ref, paging, refs
  top-level package.json blocks                                     8 Design parsing: continuation bullets, ### subheadings, exact headings
4 _gate_root needs a project file at the cwd's toplevel             9 (tests/test_om_trace.py)   10 the nested pyproject.toml widening
No real gh and no network: `gh` is a PATH stub or a patched function.
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
CHECK = ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.py"
sys.path.insert(0, str(SCRIPTS))
import foundry_ticket_store as store  # noqa: E402

_spec = importlib.util.spec_from_file_location("ft_round1", SCRIPTS / "foundry-ticket.py")
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
    (r / "src" / "app.py").write_text("x = 1\n")
    (r / "README.md").write_text("x\n")
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


def run_hook(repo, payload, script="foundry-paper-guard.py"):
    return subprocess.run([str(HOOKS / script)], input=json.dumps(payload), capture_output=True, text=True,
                          env=env_for(repo), cwd=str(repo), timeout=60)


def bash(repo, command):
    return run_hook(repo, {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)})


def edit(repo, rel, **ti):
    return run_hook(repo, {"tool_name": "Edit", "tool_input": dict({"file_path": str(repo / rel)}, **ti)})


def cli(repo, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / "foundry-ticket.py"), *args], capture_output=True, text=True,
                          env=env_for(repo), cwd=str(repo), timeout=60)


def write_ticket(repo, **fields):
    doc = {"issue": 7, "repo": "o/r", "title": "t", "done": "true", "paper_allowed": [], "status": "open", "stop_blocks": 0}
    doc.update(fields)
    (gitdir(repo) / "foundry-ticket.json").write_text(json.dumps(doc))


# ============================================================================================ 1 the wiring
def _bash_group_commands():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    group = next(g for g in hooks if g["matcher"] == "Bash")
    return [h["command"] for h in group["hooks"]]


def test_the_paper_guard_is_wired_into_the_bash_matcher_group():
    cmds = _bash_group_commands()
    assert any(c.endswith("/hooks/foundry-paper-guard.py") for c in cmds), cmds
    # alongside the shape and secret guards, which are the same group's other file-level guards
    assert any("foundry-shell-shape.py" in c for c in cmds) and any("foundry-secret-read-guard.py" in c for c in cmds)


def test_a_bash_payload_through_the_wired_command_is_refused_for_ci_and_admitted_for_code(repo):
    cmd = next(c for c in _bash_group_commands() if "foundry-paper-guard.py" in c).replace('"${CLAUDE_PLUGIN_ROOT}"', str(ROOT))
    assert os.access(cmd, os.X_OK)
    for command, code in (("sed -i s/a/b/ .github/workflows/ci.yml", 2), ("sed -i s/a/b/ README.md", 0), ("cat .github/workflows/ci.yml", 0)):
        p = subprocess.run([cmd], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)}),
                           capture_output=True, text=True, env=env_for(repo), cwd=str(repo), timeout=60)
        assert p.returncode == code, (command, p.stdout, p.stderr)
        if code == 2:
            assert "CI path" in p.stderr and json.loads(p.stdout)["decision"] == "block"


# ============================================================================================ 2 ci-allow
def test_ci_allow_drops_and_names_entries_whose_signature_does_not_verify(repo):
    write_ticket(repo, ci_allowed=[".github/workflows/ci.yml"], issue_paper_allowed=["pyproject.toml"])   # hand-written, unsigned
    p = cli(repo, "ci-allow", "setup.cfg")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "DROPPED" in p.stdout and ".github/workflows/ci.yml" in p.stdout and "pyproject.toml" in p.stdout
    doc = json.loads((gitdir(repo) / "foundry-ticket.json").read_text())
    assert doc["ci_allowed"] == ["setup.cfg"] and doc["issue_paper_allowed"] == []
    assert edit(repo, "setup.cfg").returncode == 0
    assert edit(repo, ".github/workflows/ci.yml").returncode == 2           # the hand-written entry was not laundered


def test_ci_allow_keeps_verified_entries_and_prints_no_drop_control(repo):
    write_ticket(repo)
    assert cli(repo, "ci-allow", "setup.cfg").returncode == 0
    p = cli(repo, "ci-allow", ".coveragerc")
    assert p.returncode == 0 and "DROPPED" not in p.stdout
    doc = json.loads((gitdir(repo) / "foundry-ticket.json").read_text())
    assert doc["ci_allowed"] == ["setup.cfg", ".coveragerc"]
    f = gitdir(repo) / "foundry-ticket.json"
    doc["ci_allowed"].append("Makefile")                                    # a hand edit after signing
    f.write_text(json.dumps(doc))
    p = cli(repo, "ci-allow", "noxfile.py")
    assert "DROPPED" in p.stdout and "Makefile" in p.stdout and "setup.cfg" in p.stdout
    assert json.loads(f.read_text())["ci_allowed"] == ["noxfile.py"]


# ============================================================================================ 3 Bash parsing
@pytest.mark.parametrize("cmd", [
    "echo ok\nsed -i s/a/b/ .github/workflows/ci.yml",
    "git status\n\nrm .github/workflows/ci.yml",
    "echo 'one\ntwo' > ok.txt\nprintf x > Makefile",
    "cp x.yml .github/workflows",
    "cp x.yml .github/workflows/",
    "mv x.yml .github/workflows/",
    "install -m 644 x.yml .github/workflows",
    "rm -rf .github/workflows",
    "rm -r .github",
    "mv .github elsewhere",
    "mv .github/workflows /tmp/gone",
])
def test_bash_newlines_directory_destinations_and_directory_removals_are_ci(repo, cmd):
    r = bash(repo, cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)


@pytest.mark.parametrize("cmd", [
    "echo 'a\nb' > src/out.txt",            # a newline inside quotes is not a separator
    "cp x.py src", "cp x.py src/", "mv x.py src/", "rm -rf src/tmp", "mv src/app.py src/app2.py", "rm README.md",
    "cp .github/workflows/ci.yml src/copy.yml", "ls .github/workflows\ncat README.md",
])
def test_bash_controls_stay_admitted(repo, cmd):
    r = bash(repo, cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_a_signed_ci_allow_of_the_directory_admits_the_directory_destination(repo):
    write_ticket(repo)
    assert cli(repo, "ci-allow", ".github/workflows").returncode == 0
    assert bash(repo, "cp x.yml .github/workflows").returncode == 0


def test_both_the_link_and_the_resolved_target_are_judged(repo):
    # the LINK is a CI path, its target is code
    (repo / ".github" / "workflows" / "alias.yml").symlink_to(repo / "src" / "app.py")
    assert edit(repo, ".github/workflows/alias.yml").returncode == 2
    assert bash(repo, "echo x > .github/workflows/alias.yml").returncode == 2
    # the LINK is code, its target is a CI path
    (repo / "src" / "innocent.yml").symlink_to(repo / ".github" / "workflows" / "ci.yml")
    assert edit(repo, "src/innocent.yml").returncode == 2
    assert bash(repo, "sed -i s/a/b/ src/innocent.yml").returncode == 2
    (repo / "src" / "plain-link").symlink_to(repo / "src" / "app.py")
    assert edit(repo, "src/plain-link").returncode == 0                        # control: code to code


def test_package_json_only_top_level_blocks_count(repo):
    pkg = {"name": "x", "scripts": {"build": "tsc"}, "jest": {"testEnvironment": "node"},
           "devDependencies": {"jest": "^28", "vitest": "^1", "scripts": "1.0.0"}}
    (repo / "package.json").write_text(json.dumps(pkg, indent=2) + "\n")
    # a version bump under devDependencies is admitted even though the key is spelled "jest" / "vitest" / "scripts"
    assert edit(repo, "package.json", old_string='"jest": "^28"', new_string='"jest": "^29"').returncode == 0
    assert edit(repo, "package.json", old_string='"vitest": "^1"', new_string='"vitest": "^2"').returncode == 0
    assert edit(repo, "package.json", old_string='"scripts": "1.0.0"', new_string='"scripts": "1.0.1"').returncode == 0
    # the top-level blocks still count
    assert edit(repo, "package.json", old_string='"build": "tsc"', new_string='"build": "true"').returncode == 2
    assert edit(repo, "package.json", old_string='"testEnvironment": "node"', new_string='"testEnvironment": "jsdom"').returncode == 2


# ============================================================================================ 10 nested pyproject
def test_a_nested_pyproject_toml_is_ci_by_design(repo):
    """Round 1: the nested-pyproject widening is deliberate (a sub-package's pyproject.toml carries its own pytest/coverage config)
    and is documented in the hook's docstring; `src/pyproject.py` is the unrelated-name control."""
    assert "pyproject.toml` at any depth" in (HOOKS / "foundry-paper-guard.py").read_text()
    assert edit(repo, "svc/api/pyproject.toml").returncode == 2
    assert edit(repo, "pyproject.toml").returncode == 2
    assert edit(repo, "src/pyproject.py").returncode == 0
    write_ticket(repo)
    assert cli(repo, "ci-allow", "svc/api/pyproject.toml").returncode == 0
    assert edit(repo, "svc/api/pyproject.toml").returncode == 0


# ============================================================================================ 4/5 claims
H = 3600


def _team(tmp_path, scope=True):
    root = tmp_path / "team"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feature-x", str(root)], check=True)
    proj = {"github_scope": {"owner": "example-org", "planning_repo": "planning"}} if scope else {"schema_version": 1}
    (root / ".claude" / "foundry-project.json").write_text(json.dumps(proj))
    store.ticket_file(root).write_text(json.dumps({"issue": 2, "repo": "example-org/app", "status": "open"}))
    stamp = store.iso(time.time() - 60)
    store.update_claims(root, lambda cl: cl.__setitem__("example-org/app#2", {
        "issue": "example-org/app#2", "number": 2, "repo": "example-org/app", "operator": "a", "claimed_at": stamp,
        "heartbeat_at": stamp, "session_id": "A", "expires_after_s": 2 * H}))
    return root


def test_with_no_session_id_a_team_context_is_refused_and_a_non_team_context_admits(tmp_path):
    team = _team(tmp_path)
    ok, why = store.claim_gate(str(team), session_id="")
    assert not ok and "held by session A" in why and "no id yet" in why
    assert not store.claim_gate(str(team), session_id="B")[0]                    # a different id stays refused
    assert store.claim_gate(str(team), session_id="A")[0]                        # control
    sd = tmp_path / "solo-parent"
    sd.mkdir()
    solo = _team(sd, scope=False)
    assert store.claim_gate(str(solo), session_id="")[0] is True                 # no github_scope: claims are not in use


def test_gate_root_still_gates_a_product_clone_by_the_workspace_ticket(tmp_path, monkeypatch):
    team = _team(tmp_path)
    clone = tmp_path / "product"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(team))
    ok, why = store.claim_gate(str(clone), session_id="B")                       # pushed from the clone, judged in the workspace
    assert not ok and "held by session A" in why
    assert store.claim_gate(str(clone), session_id="A")[0]


def test_reconcile_refuses_a_session_flag_when_there_is_no_ambient_id(tmp_path, monkeypatch):
    team = _team(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(team))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    before = store.read_claims(team)[0]["example-org/app#2"]["heartbeat_at"]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        ft.cmd_reconcile(["--session-id", "A", "--json"], lambda a, stdin=None: (_ for _ in ()).throw(ft.GhError("offline")), time.time() + 30)
    assert store.read_claims(team)[0]["example-org/app#2"]["heartbeat_at"] == before
    assert "no ambient session id" in err.getvalue() and "heartbeat_refused" in json.loads(out.getvalue())


# ============================================================================================ 7 governance-trace
DESIGN = "## Design\n- Components: hooks/x.py\n- Reuses: none\n- Cites: FG-QA-001\n"


def _trace(tmp_path, base, head, *, author="alice", files=("hooks/x.py",), body=DESIGN, pr_body="Closes #7", extra=(), paged=False):
    pr, ticket, fj = tmp_path / "pr.json", tmp_path / "ticket.json", tmp_path / "files.json"
    doc = {"number": 3, "body": pr_body}
    if not paged:
        doc["files"] = [{"path": f} for f in files]
    pr.write_text(json.dumps(doc))
    ticket.write_text(json.dumps({"number": 7, "body": body, "labels": [], "author": {"login": author}}))
    cmd = [sys.executable, str(CHECK), "check", "--pr-json", str(pr), "--ticket-json", str(ticket), "--project-dir", str(head),
           "--base-dir", str(base), "--plugin-root", str(ROOT), "--repo", "o/r", "--json", *extra]
    if paged:
        fj.write_text(json.dumps([{"path": f} for f in files]))
        cmd += ["--files-json", str(fj)]
    gb = tmp_path / "ghbin"
    gb.mkdir(exist_ok=True)
    g = gb / "gh"
    g.write_text("#!/bin/sh\necho \"$@\" >> \"$GH_LOG\"\ncase \"$*\" in\n  'api user') echo '{\"login\": \"agent-bot\"}';;\n"
                 "  *collaborators/carol/*) echo '{\"permission\": \"write\"}';;\n  *) echo '{\"permission\": \"read\"}';;\nesac\n")
    g.chmod(0o755)
    env = dict(os.environ, PATH=str(gb) + os.pathsep + os.environ["PATH"], GH_LOG=str(tmp_path / "gh.log"))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    return p, json.loads(p.stdout)


def _tree(root, policy_paths, operators=("alice",)):
    (root / ".foundry").mkdir(parents=True, exist_ok=True)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".foundry" / "operating-model.yaml").write_text(
        "schema_version: 1\ngovernance:\n  design_required_paths: %s\n" % json.dumps(policy_paths))
    (root / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "o", "planning_repo": "o/p", "operators": {o: "op" for o in operators}}}))
    return root


def by(doc, name):
    return [f for f in doc["findings"] if f["check"] == name]


def test_an_untrusted_ticket_author_fails_and_a_trusted_one_passes(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    p, doc = _trace(tmp_path, base, base, author="mallory")
    assert p.returncode == 1 and by(doc, "ticket-author")[0]["ok"] is False and "mallory" in by(doc, "ticket-author")[0]["detail"]
    p, doc = _trace(tmp_path, base, base, author="alice")
    assert p.returncode == 0 and by(doc, "ticket-author")[0]["ok"] is True
    p, doc = _trace(tmp_path, base, base, author="carol")                       # a write collaborator is trusted too (control)
    assert by(doc, "ticket-author")[0]["ok"] is True


def test_the_gh_user_authoring_the_ticket_is_not_trusted_even_when_registered(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"], operators=("agent-bot",))
    p, doc = _trace(tmp_path, base, base, author="agent-bot")
    assert by(doc, "ticket-author")[0]["ok"] is False and "agent itself" in by(doc, "ticket-author")[0]["detail"]


def test_a_component_that_matches_everything_is_refused(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    for comp in ("**", "**/*", "*"):
        p, doc = _trace(tmp_path, base, base, body=DESIGN.replace("hooks/x.py", comp), files=("hooks/x.py", "src/a.py"))
        f = by(doc, "paths-in-design")[0]
        assert p.returncode == 1 and f["ok"] is False and "match every path" in f["detail"], comp
    p, doc = _trace(tmp_path, base, base, body=DESIGN.replace("hooks/x.py", "hooks/**"), files=("hooks/a/b.py",))
    assert p.returncode == 0                                                   # control: a directory glob is fine


def test_policy_and_boundaries_come_from_the_base_not_the_pr_head(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    head = _tree(tmp_path / "head", [])                                        # the PR empties the class that grades it
    p, doc = _trace(tmp_path, base, head, body="## Done means\n```\ntrue\n```\n", files=("hooks/x.py",))
    assert p.returncode == 1 and by(doc, "design-present")[0]["ok"] is False and "hooks/x.py" in by(doc, "design-present")[0]["detail"]
    # control: when the BASE has no such class either, no Design is needed
    base2 = _tree(tmp_path / "base2", [])
    p, doc = _trace(tmp_path, base2, head, body="## Done means\n```\ntrue\n```\n", files=("hooks/x.py",))
    assert p.returncode == 0, p.stdout


def test_a_paged_file_list_is_read_in_full(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"])
    files = ["hooks/x.py"] + [f"src/gen/f{i}.py" for i in range(150)]
    p, doc = _trace(tmp_path, base, base, files=files, paged=True)
    bad = [f["path"] for f in by(doc, "paths-in-design") if not f["ok"]]
    assert p.returncode == 1 and len(bad) == 150 and "src/gen/f149.py" in bad


def test_a_cross_repo_ticket_ref_is_judged_in_round_2(tmp_path):
    """Superseded in round 2: see tests/test_gv16_round2.py (the author is vetted in the PR's repo; the ref repo must be listed)."""
    base = _tree(tmp_path / "base", ["hooks/**"])
    p, doc = _trace(tmp_path, base, base, author="alice", pr_body="Closes other/repo#7")
    assert by(doc, "ticket-named")[0]["ok"] is False


def test_the_template_states_which_ref_each_checkout_is():
    text = (ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.yml.template").read_text()
    assert "MERGE commit" in text and "not the head alone" in text
    assert "ref: ${{ github.event.pull_request.base.sha }}" in text and "--base-dir base" in text
    assert "gh api --paginate" in text and "--files-json files.json" in text and "${R:-$REPO}" in text


# ============================================================================================ 8 Design parsing
def test_continuation_bullets_at_column_zero_and_subheadings_stay_in_the_design():
    body = ("## Design\n- Components:\n- src/a.py\n- src/b/\n### Notes\n- Cites:\n- FG-QA-001\n- FG-SEC-006\n"
            "- Reuses: the brief renderer\n\n## Security\nnone: nothing crosses a boundary\n")
    d = ft.parse_sections(body)["design"]
    assert d["components"] == ["src/a.py", "src/b"] and d["cites"] == ["FG-QA-001", "FG-SEC-006"] and d["reuses"] == "the brief renderer"
    assert ft.parse_sections(body)["security"]["none_reason"] == "nothing crosses a boundary"


def test_headings_match_exactly_not_by_prefix():
    s = ft.parse_sections("## Design review\n- Components: a.py\n- Cites: FG-QA-001\n\n## Security notes\nnone: x\n\n## Surfaces and more\n- cli: x\n")
    assert s["design"]["present"] is False and s["security"]["present"] is False and s["surfaces"]["present"] is False
    s = ft.parse_sections("## Design\n- Cites: FG-QA-001\n")                      # control: the exact heading
    assert s["design"]["present"] is True


def _classify(body, labels=()):
    return ft.design_view(body, [{"name": n} for n in labels], root=Path(os.environ["CLAUDE_PROJECT_DIR"]))


def test_omitting_the_design_and_scope_cannot_evade_the_class(repo):
    (repo / ".foundry").mkdir()
    (repo / ".foundry" / "operating-model.yaml").write_text('schema_version: 1\ngovernance:\n  design_required_paths: ["hooks/**"]\n')
    (repo / ".foundry" / "governance.lock").write_text(json.dumps({
        "blueprint": {"id": "cli-tool", "version": "1", "sha256": "0" * 64}, "kits": [],
        "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": ["src/auth/**"], "surfaces": ["cli"]},
        "facts_sha256": "0" * 64, "intake_at": "2026-01-01", "plugin_version": "2"}))
    by_paper = _classify("## Paper allowed\nhooks/foundry-x.py\n")                  # no Design, no Scope: the Paper-allowed list says it
    assert by_paper["design_required"] is True and by_paper["design_defects"]
    assert _classify("no sections at all", ["size:L"])["design_required"] is True   # the size tag
    sec = _classify("## Design\n- Components: src/auth/login.py\n- Cites: FG-QA-001\n")
    assert sec["design_required"] is True and "trust boundary" in " ".join(sec["design_required_reasons"])
    # controls: nothing governed, no size tag
    ctl = _classify("## Paper allowed\nsrc/app.py\n", ["size:M"])
    assert ctl["design_required"] is False and ctl["design_defects"] == []
