"""gv-10: the paper guard's `ci` class (operating-model rule A2 — never edit CI to make a PR pass).

A CI path is refused unless the active ticket names it in `paper_allowed` or `ci_allowed`. Code
elsewhere stays never-gated. `foundry-ticket.py ci-allow` writes `ci_allowed`, and the plugin
scripts allow-hook never grants that verb silently.
"""
import importlib.util
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
import foundry_ticket_store as store  # noqa: E402


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / ".github" / "workflows").mkdir(parents=True)
    (r / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: bash scripts/gate-check.sh\n      - run: python3 ./tools/lint.py\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(r))
    _git(r, "config", "user.email", "t@example.test")
    _git(r, "config", "user.name", "t")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


def _env(root):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    return env


def guard(root, rel, tool="Edit"):
    payload = {"tool_name": tool, "tool_input": {"file_path": str(root / rel)}}
    return subprocess.run([str(HOOKS / "foundry-paper-guard.py")], input=json.dumps(payload),
                          capture_output=True, text=True, env=_env(root), cwd=str(root), timeout=60)


def ticket(root, **lists):
    tf = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "foundry-ticket.json"
    doc = {"issue": 7, "title": "t", "done": "true", "paper_allowed": [], "status": "open",
           "stop_blocks": 0, "trusted_by": "author"}
    doc.update(lists)
    store.sign_ticket_lists(doc, root, create=True)     # gv-16: ci_allowed / issue_paper_allowed count only when signed
    tf.write_text(json.dumps(doc))


CI_PATHS = [
    ".github/workflows/ci.yml", ".github/actions/setup/action.yml", ".gitlab-ci.yml", ".circleci/config.yml",
    "Jenkinsfile", "scripts/ci/run.sh", "pyproject.toml", "setup.cfg", ".coveragerc", "pytest.ini",
    "svc/tox.ini", "web/vitest.config.ts", "web/playwright.config.ts", "jest.config.js",
    "tests/quarantine.json", "ops/quarantine-list.yaml", "ops/quarantine.yml",
    "scripts/gate-check.sh", "tools/lint.py",       # named by the repo's own workflow
    "Makefile", "noxfile.py", ".pre-commit-config.yaml", "conftest.py", "tests/unit/conftest.py", "web/karma.conf.js",
    "svc/pyproject.toml",                              # gv-16: a nested pyproject.toml carries test config too
]


@pytest.mark.parametrize("rel", CI_PATHS)
def test_ci_edit_is_refused_without_a_ticket(repo, rel):
    p = guard(repo, rel)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "CI path" in p.stderr and "CI path" in p.stdout


def test_workflow_edit_without_ci_allowed_is_refused_and_with_it_admitted(repo):
    ticket(repo)
    refused = guard(repo, ".github/workflows/ci.yml")
    assert refused.returncode == 2 and "ci-allow .github/workflows/ci.yml" in refused.stderr
    ticket(repo, ci_allowed=[".github/workflows/ci.yml"])
    assert guard(repo, ".github/workflows/ci.yml").returncode == 0


def test_ci_allowed_names_only_what_it_names(repo):
    ticket(repo, ci_allowed=[".github/workflows/ci.yml"])
    assert guard(repo, ".github/workflows/other.yml").returncode == 2
    assert guard(repo, "pyproject.toml").returncode == 2
    ticket(repo, ci_allowed=[".github/workflows"])                       # a parent directory covers its children
    assert guard(repo, ".github/workflows/other.yml").returncode == 0


def test_cli_written_paper_allowed_never_lifts_a_ci_path(repo):
    """The silent lift: `allow <ci-path>` (no --ci) used to write paper_allowed, which the guard honoured."""
    ticket(repo, paper_allowed=["pyproject.toml", ".github", ".github/workflows/ci.yml"])
    for rel in ("pyproject.toml", ".github/workflows/ci.yml"):
        assert guard(repo, rel).returncode == 2, rel


def test_the_issue_authors_own_paper_list_may_name_a_ci_path(repo):
    ticket(repo, issue_paper_allowed=["pyproject.toml"])
    assert guard(repo, "pyproject.toml").returncode == 0


@pytest.mark.parametrize("path,probe", [("pyproject.toml", "pyproject.toml"),
                                        (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
                                        (".github", ".github/workflows/ci.yml"), ("scripts/ci", "scripts/ci/run.sh"),
                                        ("Makefile", "Makefile")])
def test_plain_allow_of_a_ci_path_is_refused_by_the_cli(repo, path, probe):
    ticket(repo)
    cli = subprocess.run([sys.executable, str(SCRIPTS / "foundry-ticket.py"), "allow", path],
                         capture_output=True, text=True, env=_env(repo), cwd=str(repo), timeout=60)
    assert cli.returncode != 0 and "ci-path-needs-ci-flag" in cli.stderr + cli.stdout
    doc = json.loads((Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "foundry-ticket.json").read_text())
    assert doc["paper_allowed"] == [] and not doc.get("ci_allowed")
    assert guard(repo, probe).returncode == 2


def test_allow_dash_dash_ci_is_not_a_verb_and_plain_paper_allow_still_works(repo):
    ticket(repo)
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "foundry-ticket.py"), "allow", *a],
                                    capture_output=True, text=True, env=_env(repo), cwd=str(repo), timeout=60)
    assert run("--ci", "setup.cfg").returncode != 0 and run("setup.cfg", "--ci").returncode != 0
    assert run("docs/how-to/x.md").returncode == 0                       # control: ordinary paper allow


def test_package_json_is_ci_only_when_a_test_script_is_touched(repo):
    def g(ti):
        payload = {"tool_name": "Edit", "tool_input": dict(ti, file_path=str(repo / "package.json"))}
        return subprocess.run([str(HOOKS / "foundry-paper-guard.py")], input=json.dumps(payload), capture_output=True,
                              text=True, env=_env(repo), cwd=str(repo), timeout=60).returncode
    assert g({"old_string": '"left-pad": "1.0.0"', "new_string": '"left-pad": "1.1.0"'}) == 0
    assert g({"old_string": '"test": "jest"', "new_string": '"test": "true"'}) == 2
    assert g({"edits": [{"old_string": "a", "new_string": '"pretest": "x"'}]}) == 2


def test_a_quarantine_directory_does_not_make_every_file_under_it_a_quarantine_list(repo):
    assert guard(repo, "src/quarantine/handler.json").returncode == 0
    assert guard(repo, "src/quarantine-notes/data.json").returncode == 0


def test_ci_allowed_does_not_unlock_paper(repo):
    ticket(repo, ci_allowed=["docs"])
    assert guard(repo, "docs/plan.md").returncode == 2


@pytest.mark.parametrize("rel", [
    "src/app.py", "tests/test_a.py", "scripts/deploy.sh", "src/pyproject.py", "CLAUDE.md",
    ".github/ISSUE_TEMPLATE/bug.md", ".github/CODEOWNERS", "README.md", "src/quarantined_users.py",
])
def test_code_elsewhere_stays_never_gated(repo, rel):
    p = guard(repo, rel)
    assert p.returncode == 0, p.stdout + p.stderr


def test_the_ticket_cli_writes_ci_allowed_and_the_guard_honours_it(repo):
    ticket(repo)
    cli = subprocess.run([sys.executable, str(SCRIPTS / "foundry-ticket.py"), "ci-allow", "setup.cfg"],
                         capture_output=True, text=True, env=_env(repo), cwd=str(repo), timeout=60)
    assert cli.returncode == 0, cli.stdout + cli.stderr
    doc = json.loads((Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "foundry-ticket.json").read_text())
    assert doc["ci_allowed"] == ["setup.cfg"] and doc["paper_allowed"] == []
    assert guard(repo, "setup.cfg").returncode == 0


def test_the_allow_hook_never_grants_allow_ci_silently():
    spec = importlib.util.spec_from_file_location("allow_hook", HOOKS / "foundry-plugin-scripts-allow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = str(ROOT)
    ticket_py = str(SCRIPTS / "foundry-ticket.py")
    assert mod.allowed(f"python3 {ticket_py} allow docs/a.md", root) is True       # control: paper allow stays silent
    assert mod.allowed(f"python3 {ticket_py} ci-allow setup.cfg", root) is False
    assert mod.allowed(f"python3 {SCRIPTS / 'foundry_queue_land.py'} 12", root) is False


def _start_with_issue(repo, monkeypatch, author, trusted):
    spec = importlib.util.spec_from_file_location("ft_under_test", SCRIPTS / "foundry-ticket.py")
    ft = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(ft)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    issue = {"number": 7, "title": "t", "body": "## Paper allowed\n.github/workflows/\n", "url": "https://github.com/o/r/issues/7",
             "author": {"login": author}, "state": "OPEN"}
    monkeypatch.setattr(ft, "_gh_json", lambda a: issue)
    monkeypatch.setattr(ft, "read_issue", lambda run, repo_, n, **k: {"last_edited_by": author, "name_with_owner": "o/r"})
    monkeypatch.setattr(ft, "author_trusted", lambda r, login, gh_json=None: (login in trusted, "w"))
    assert ft.cmd_start(["7"]) == 0
    return json.loads((Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "foundry-ticket.json").read_text())


def test_an_untrusted_authors_paper_allowed_does_not_lift_the_ci_guard(repo, monkeypatch):
    doc = _start_with_issue(repo, monkeypatch, "mallory", trusted=("alice",))
    assert doc["issue_paper_allowed"] == [] and doc["paper_allowed"] == [".github/workflows"]
    assert guard(repo, ".github/workflows/ci.yml").returncode == 2


def test_a_trusted_authors_paper_allowed_does_lift_it_control(repo, monkeypatch):
    doc = _start_with_issue(repo, monkeypatch, "alice", trusted=("alice",))
    assert doc["issue_paper_allowed"] == [".github/workflows"]
    assert guard(repo, ".github/workflows/ci.yml").returncode == 0
