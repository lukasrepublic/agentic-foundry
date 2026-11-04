"""gv-10: git-discipline clauses (k) and (l) — AC-OM-4.

(k) `gh pr merge --auto` is admitted only when the base branch's LIVE ruleset names at least one
required status check AND `gh pr checks` is green at the head. `--admin` and force flags stay refused.
(l) a `git push` to a branch whose open PR is in the merge queue is refused ("dequeue first").
Both fail closed on any query error.

Every `gh` here is tests/fixtures/queue/gh, a scenario-driven stub placed first on PATH. Its argv log
is the proof that no real call is made, and that a refused-before-query command made no call at all.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "foundry-git-discipline.sh"
STUB_DIR = Path(__file__).resolve().parent / "fixtures" / "queue"

PR_VIEW = json.dumps({"baseRefName": "main", "url": "https://github.com/o/r/pull/42"})
RULES_REQUIRED = json.dumps([
    {"type": "pull_request", "parameters": {}},
    {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci"}]}},
])
RULES_NONE = json.dumps([{"type": "pull_request", "parameters": {}}])
RULES_EMPTY_CHECKS = json.dumps([{"type": "required_status_checks",
                                  "parameters": {"required_status_checks": []}}])
GREEN = "ci\tpass\t1s\turl\n"
PENDING = "ci\tpending\t1s\turl\n"


def _ensure_stub_executable():
    os.chmod(STUB_DIR / "gh", 0o755)


@pytest.fixture
def stub(tmp_path):
    _ensure_stub_executable()
    log = tmp_path / "gh.log"
    scenario = tmp_path / "scenario.json"

    def arm(responses):
        scenario.write_text(json.dumps({"responses": responses}))

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    arm([])
    env = dict(os.environ)
    env.update(PATH=str(STUB_DIR) + os.pathsep + env["PATH"], QL_LOG=str(log), QL_SCENARIO=str(scenario))
    env.pop("GH_TOKEN", None)
    return type("Stub", (), {"arm": staticmethod(arm), "calls": staticmethod(calls), "env": env})


def run_hook(cmd, env, cwd=None, args=("--local-green=off",)):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    if cwd:
        payload["cwd"] = str(cwd)
    return subprocess.run(["bash", str(HOOK), *args], input=json.dumps(payload), capture_output=True,
                          text=True, env=env, cwd=str(cwd or ROOT), timeout=120)


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "feat")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "x")
    _git(r, "remote", "add", "origin", "https://github.com/o/r.git")
    return r


CLASSIC_NONE = json.dumps({"name": "main", "protected": False})
CLASSIC_REQUIRED = json.dumps({"name": "main", "protected": True, "protection": {
    "enabled": True, "required_status_checks": {"enforcement_level": "everyone", "contexts": ["ci"], "checks": []}}})


def _auto_responses(rules, checks=GREEN, checks_rc=0, view=PR_VIEW, classic=CLASSIC_NONE, classic_rc=0):
    return [
        {"match": ["pr", "view"], "stdout": view},
        {"match": ["api", "repos/o/r/branches/main"], "stdout": classic, "rc": classic_rc},
        {"match": ["api", "--hostname", "ghe.example.com", "repos/o/r/branches/release/2.0.0"], "stdout": classic},
        {"match": ["api"], "stdout": rules},
        {"match": ["pr", "checks"], "stdout": checks, "rc": checks_rc},
    ]


# ------------------------------------------------------------------ (k) --admin / force / --auto

def test_admin_is_refused_with_zero_gh_calls(stub):
    stub.arm(_auto_responses(RULES_REQUIRED))
    p = run_hook("gh pr merge 42 --admin --squash", stub.env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert stub.calls() == []


@pytest.mark.parametrize("cmd", ["gh pr merge 42 --auto --admin", "gh pr merge 42 --admin=true --auto"])
def test_admin_with_auto_is_refused_with_zero_gh_calls(stub, cmd):
    stub.arm(_auto_responses(RULES_REQUIRED))
    p = run_hook(cmd, stub.env)
    assert p.returncode == 2
    assert stub.calls() == []


@pytest.mark.parametrize("cmd", [
    "git push --force origin main",
    "git push -f origin main",
    "git push --force-with-lease origin main",
    "git push origin +main",
    "gh pr merge 42 --squash --force",
])
def test_force_flags_are_refused_with_zero_gh_calls(stub, cmd):
    stub.arm(_auto_responses(RULES_REQUIRED))
    p = run_hook(cmd, stub.env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert stub.calls() == []


def test_auto_without_a_required_check_is_refused(stub):
    stub.arm(_auto_responses(RULES_NONE))
    p = run_hook("gh pr merge 42 --auto --squash", stub.env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "neither the rulesets" in p.stdout + p.stderr
    assert not any(c[:3] == ["pr", "checks", "42"] for c in stub.calls()), "refused before the checks query"


@pytest.mark.parametrize("rules", [RULES_EMPTY_CHECKS, "[]", "{}", "not json", ""])
def test_auto_with_an_empty_or_unreadable_ruleset_is_refused(stub, rules):
    stub.arm(_auto_responses(rules))
    p = run_hook("gh pr merge 42 --auto", stub.env)
    assert p.returncode == 2, p.stdout + p.stderr


def test_auto_is_admitted_on_classic_branch_protection_with_required_checks(stub):
    """A repo with no ruleset but classic protection naming a required check is a real gate."""
    stub.arm(_auto_responses(RULES_NONE, classic=CLASSIC_REQUIRED))
    p = run_hook("gh pr merge 42 --auto", stub.env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert ["api", "repos/o/r/branches/main"] in stub.calls()


def test_classic_protection_without_required_checks_or_unreadable_is_still_refused(stub):
    for classic, rc in ((CLASSIC_NONE, 0), (CLASSIC_REQUIRED, 1), ("not json", 0)):
        stub.arm(_auto_responses(RULES_NONE, classic=classic, classic_rc=rc))
        p = run_hook("gh pr merge 42 --auto", stub.env)
        assert p.returncode == 2, (classic, p.stdout + p.stderr)
    msg = p.stdout + p.stderr
    assert "rulesets" in msg and "classic branch protection" in msg and "cannot be used to wait" in msg


def test_auto_is_admitted_with_a_required_check_and_green_head(stub):
    """Control for the refusals above: same command, ruleset names a check, head is green."""
    stub.arm(_auto_responses(RULES_REQUIRED))
    p = run_hook("gh pr merge 42 --auto --squash", stub.env)
    assert p.returncode == 0, p.stdout + p.stderr
    argvs = stub.calls()
    assert ["api", "repos/o/r/rules/branches/main"] in argvs
    assert any(a[:3] == ["pr", "checks", "42"] for a in argvs)
    assert all(a[0] in ("pr", "api") and a[:2] != ["pr", "merge"] for a in argvs), "the hook never merges"


def test_auto_with_a_pending_head_is_refused_even_with_a_required_check(stub):
    stub.arm(_auto_responses(RULES_REQUIRED, checks=PENDING, checks_rc=8))
    p = run_hook("gh pr merge 42 --auto", stub.env)
    assert p.returncode == 2, p.stdout + p.stderr


def test_auto_with_a_failing_head_is_refused(stub):
    stub.arm(_auto_responses(RULES_REQUIRED, checks="ci\tfail\t1s\turl\n", checks_rc=1))
    assert run_hook("gh pr merge 42 --auto", stub.env).returncode == 2


@pytest.mark.parametrize("failing", ["view", "api"])
def test_auto_fails_closed_on_a_query_error(stub, failing):
    resp = _auto_responses(RULES_REQUIRED)
    for r in resp:
        if r["match"][-1] == failing:
            r["rc"] = 1
            r["stderr"] = "boom"
    stub.arm(resp)
    p = run_hook("gh pr merge 42 --auto", stub.env)
    assert p.returncode == 2, p.stdout + p.stderr


def test_auto_without_a_pr_selector_is_refused(stub):
    stub.arm(_auto_responses(RULES_REQUIRED))
    p = run_hook("gh pr merge --auto", stub.env)
    assert p.returncode == 2
    assert stub.calls() == []


def test_auto_names_the_host_for_an_enterprise_pr(stub):
    stub.arm(_auto_responses(RULES_REQUIRED, view=json.dumps(
        {"baseRefName": "release/2.0.0", "url": "https://ghe.example.com/o/r/pull/42"})))
    p = run_hook("gh pr merge 42 --auto", stub.env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert ["api", "--hostname", "ghe.example.com", "repos/o/r/rules/branches/release/2.0.0"] in stub.calls()


def test_plain_merge_is_unchanged_no_ruleset_query(stub):
    stub.arm(_auto_responses(RULES_NONE))
    p = run_hook("gh pr merge 42 --squash", stub.env)
    assert p.returncode == 0
    assert [c for c in stub.calls() if c[0] == "api"] == []


# ------------------------------------------------------------------ (l) push to a queued PR

def _list(rows, rc=0, stderr="", more=False):
    """The GraphQL answer for the open-PRs query (`gh pr list --json` has no queue field)."""
    body = {"data": {"repository": {"pullRequests": {"nodes": rows, "pageInfo": {"hasNextPage": more}}}}}
    return [{"match": ["api", "graphql"], "stdout": json.dumps(body), "rc": rc, "stderr": stderr}]


def _graphql_calls(stub):
    return [c for c in stub.calls() if c[:2] == ["api", "graphql"]]


def test_push_to_an_unqueued_pr_is_admitted(stub, repo):
    """Control for the queued refusals: the same push, the PR exists but is not in the queue."""
    stub.arm(_list([{"number": 7, "headRefName": "feat", "isInMergeQueue": False, "mergeQueueEntry": None}]))
    p = run_hook("git push origin feat", stub.env, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert any("h=feat" in c for c in _graphql_calls(stub))


def test_push_with_no_open_pr_is_admitted(stub, repo):
    stub.arm(_list([]))
    assert run_hook("git push origin feat", stub.env, cwd=repo).returncode == 0


@pytest.mark.parametrize("row", [
    {"number": 7, "headRefName": "feat", "isInMergeQueue": True, "mergeQueueEntry": None},
    {"number": 7, "headRefName": "feat", "isInMergeQueue": False, "mergeQueueEntry": {"position": 1}},
])
def test_push_to_a_queued_pr_is_refused(stub, repo, row):
    stub.arm(_list([row]))
    p = run_hook("git push origin feat", stub.env, cwd=repo)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "merge queue" in p.stdout and "Dequeue" in p.stdout


def test_push_with_no_refspec_resolves_the_current_branch(stub, repo):
    stub.arm(_list([{"number": 7, "headRefName": "feat", "isInMergeQueue": True}]))
    assert run_hook("git push", stub.env, cwd=repo).returncode == 2
    assert any("h=feat" in c for c in _graphql_calls(stub))


def test_force_push_of_a_queued_branch_is_refused_too(stub, repo):
    stub.arm(_list([{"number": 7, "headRefName": "feat", "isInMergeQueue": True}]))
    assert run_hook("git push --force-with-lease origin feat", stub.env, cwd=repo).returncode == 2


def test_push_fails_closed_on_a_query_error(stub, repo):
    stub.arm(_list([], rc=1, stderr="HTTP 502"))
    p = run_hook("git push origin feat", stub.env, cwd=repo)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "fail-closed" in p.stdout


def test_push_fails_closed_on_unparseable_output(stub, repo):
    stub.arm([{"match": ["api", "graphql"], "stdout": "<html>"}])
    assert run_hook("git push origin feat", stub.env, cwd=repo).returncode == 2


def test_unresolvable_destination_is_refused_only_while_something_is_queued(stub, repo):
    cmd = "git push origin $TARGET_BRANCH"
    stub.arm(_list([{"number": 9, "headRefName": "other", "isInMergeQueue": True}]))
    assert run_hook(cmd, stub.env, cwd=repo).returncode == 2
    stub.arm(_list([{"number": 9, "headRefName": "other", "isInMergeQueue": False}]))
    assert run_hook(cmd, stub.env, cwd=repo).returncode == 0
    stub.arm(_list([], more=True))
    assert run_hook(cmd, stub.env, cwd=repo).returncode == 2, "more than a page of PRs: nothing-queued is unproven"


@pytest.mark.parametrize("url", ["git@mygh-alias:o/r.git", "https://example.invalid/o/r.git", "mygh-alias:o/r.git",
                                 "ssh://git@mygh-alias/o/r.git"])
def test_a_remote_on_a_host_gh_does_not_know_is_refused_remote_host_unresolvable(stub, repo, url):
    """An SSH alias or unknown host is NOT a local path: skipping the query there would let a queued PR be pushed to."""
    _git(repo, "remote", "add", "elsewhere", url)
    stub.arm(_list([]))
    p = run_hook("git push elsewhere feat", stub.env, cwd=repo)
    assert p.returncode == 2 and "remote-host-unresolvable" in p.stdout, p.stdout + p.stderr
    assert stub.calls() == []


@pytest.mark.parametrize("url", ["github.com:o/r.git", "git@github.com:o/r.git", "ssh://git@github.com/o/r.git",
                                 "https://github.com/o/r"])
def test_every_github_url_spelling_reaches_the_queue_query(stub, repo, url):
    _git(repo, "remote", "set-url", "origin", url)
    stub.arm(_list([{"number": 7, "headRefName": "feat", "isInMergeQueue": True}]))
    assert run_hook("git push origin feat", stub.env, cwd=repo).returncode == 2
    assert "repo=o/r" not in "".join(sum(stub.calls(), [])) and "r=r" in sum(stub.calls(), [])


@pytest.mark.parametrize("cmd", [
    "git push -o ci.skip origin feat", "git push --push-option ci.skip origin feat",
    "git push --push-option=x origin feat", "git push --receive-pack /bin/true origin feat",
    "git push --repo=origin feat", "git push --repo origin feat",
])
def test_value_taking_push_flags_do_not_hide_the_remote(stub, repo, cmd):
    stub.arm(_list([{"number": 7, "headRefName": "feat", "isInMergeQueue": True}]))
    assert run_hook(cmd, stub.env, cwd=repo).returncode == 2, cmd


def test_the_push_url_not_the_fetch_url_decides(stub, repo):
    _git(repo, "remote", "set-url", "--push", "origin", "git@mygh-alias:o/r.git")
    stub.arm(_list([]))
    assert run_hook("git push origin feat", stub.env, cwd=repo).returncode == 2


def test_a_file_url_remote_makes_no_query(stub, repo):
    _git(repo, "remote", "add", "fileremote", "file:///nonexistent/o/r.git")
    assert run_hook("git push fileremote feat", stub.env, cwd=repo).returncode == 0
    assert stub.calls() == []


def test_an_unresolvable_repo_with_a_github_remote_is_refused(stub, repo, tmp_path):
    stub.arm(_list([]))
    p = run_hook("cd %s && git push origin feat" % tmp_path, stub.env, cwd=repo)
    assert p.returncode == 2, p.stdout + p.stderr
    assert stub.calls() == []


def test_dry_run_and_local_remotes_make_no_query(stub, repo, tmp_path):
    stub.arm(_list([{"number": 7, "headRefName": "feat", "isInMergeQueue": True}]))
    assert run_hook("git push --dry-run origin feat", stub.env, cwd=repo).returncode == 0
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "local", str(bare))
    assert run_hook("git push local feat", stub.env, cwd=repo).returncode == 0
    assert stub.calls() == []


def test_no_real_gh_is_ever_spawned(stub, repo):
    """Every call that reached the stub is in the log; the stub is first on PATH, so a call the hook
    made to a real `gh` would be absent from it. Prove the stub is what resolves."""
    stub.arm(_list([]))
    run_hook("git push origin feat", stub.env, cwd=repo)
    which = subprocess.run(["bash", "-c", "command -v gh"], env=stub.env, capture_output=True, text=True)
    assert Path(which.stdout.strip()).resolve() == (STUB_DIR / "gh").resolve()
    assert stub.calls(), "the hook's query went through the stub"
