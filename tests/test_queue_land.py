"""gv-10: scripts/foundry_queue_land.py (operating-model rule A2).

Real git on throw-away repos (a bare local `origin`); every `gh` is tests/fixtures/queue/gh, a scenario stub
first on PATH. The stub's argv log proves what was called, and that nothing real was.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foundry_queue_land.py"
STUB_DIR = Path(__file__).resolve().parent / "fixtures" / "queue"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_queue_land as ql  # noqa: E402

URL = "https://github.com/o/r/pull/42"
RULES_OK = [{"type": "merge_queue", "parameters": {}},
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci"}]}}]


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@e", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """origin (bare) + a clone on branch `feat` with one commit ahead of `main`, plus the gh stub."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.will_push = False        # set once the base has moved: a push is due, so the queue is read once more first
        self.origin = tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.repo = tmp / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True, capture_output=True)
        git(self.repo, "checkout", "-q", "-b", "main")
        (self.repo / "README.md").write_text("x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "-b", "feat")
        (self.repo / "src.py").write_text("a = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "feat")
        git(self.repo, "push", "-q", "origin", "feat")
        self.log = tmp / "gh.log"
        self.scenario = tmp / "scenario.json"
        os.chmod(STUB_DIR / "gh", 0o755)
        self.env = dict(os.environ, PATH=str(STUB_DIR) + os.pathsep + os.environ["PATH"],
                        QL_LOG=str(self.log), QL_SCENARIO=str(self.scenario), CLAUDE_PROJECT_DIR=str(self.repo),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")

    def head(self):
        return git(self.repo, "rev-parse", "HEAD")

    def pr_json(self, **kw):
        d = {"number": 42, "state": "OPEN", "isDraft": False, "headRefName": "feat", "headRefOid": self.head(),
             "baseRefName": "main", "url": URL, "files": [{"path": "src.py"}], "reviewDecision": ""}
        d.update(kw)
        return json.dumps(d)

    def arm(self, *, rules=RULES_OK, checks=None, queue=None, poll=None, comments=None, extra=(), classic=None,
            already_queued=False, **pr):
        checks = checks if checks is not None else [{"name": "ci", "bucket": "pass", "workflow": "CI", "link": "u"}]
        queue = [_q(already_queued)] + ([_q(False)] if self.will_push and not already_queued else []) + (queue or [_q(True), _q(True), _q(False, merged=True)])   # first read: the pre-push check
        poll = poll or [json.dumps({"state": "MERGED", "mergedBy": {"login": "queue-bot"},
                                    "mergeCommit": {"oid": "c" * 40}})]
        r = [
            {"match": ["pr", "view", ql.PR_FIELDS], "stdout": self.pr_json(**pr)},
            {"match": ["pr", "view", ql.POLL_FIELDS], "outputs": poll},
            {"match": ["pr", "view", ql.COMMENT_FIELDS], "outputs": comments or ['{"comments": []}']},
            {"match": ["api", "repos/o/r/rules/branches/main"], "stdout": json.dumps(rules)},
            {"match": ["api", "repos/o/r/branches/main"], "stdout": json.dumps(classic or {"protected": False})},
            {"match": ["api", "user"], "stdout": json.dumps({"login": "queue-bot"})},
            {"match": ["pr", "checks"], "stdout": json.dumps(checks), "rc": 0},
            {"match": ["pr", "merge"], "stdout": ""},
            {"match": ["pr", "comment"], "stdout": ""},
            {"match": ["api", "graphql"], "outputs": queue},
            *extra,
        ]
        self.scenario.write_text(json.dumps({"responses": r}))
        counts = Path(str(self.scenario) + ".counts")
        if counts.exists():
            counts.unlink()

    def run(self, *args, pr="42"):
        extra = [] if "--local-green-cmd" in args else ["--local-green-cmd", "true"]
        if "--message-log" not in args:  # the shipped log script now exists beside queue-land: default to the PR-comment path
            extra += ["--message-log", str(self.repo / "no-such-message-log.py")]
        a = [sys.executable, str(SCRIPT), pr, "--repo-dir", str(self.repo), "--poll-sec", "0", "--read-gap-sec", "0",
             "--checks-timeout-min", "0.01", *extra, *args]
        p = subprocess.run(a, capture_output=True, text=True, env=self.env, cwd=str(self.repo), timeout=240)
        lines = [json.loads(x) for x in p.stdout.splitlines() if x.startswith("{")]
        return p, lines

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def bodies(self, kind=None):
        out = []
        for c in self.calls():
            if c[:2] == ["pr", "comment"]:
                body = c[c.index("--body") + 1]
                if kind is None or kind in body:
                    out.append(body)
        return out

    def merges(self):
        return [c for c in self.calls() if c[:2] == ["pr", "merge"]]


def _q(inq, merged=False):
    return json.dumps({"data": {"repository": {"pullRequest": {
        "state": "MERGED" if merged else "OPEN", "merged": merged, "isInMergeQueue": inq,
        "mergeQueueEntry": {"position": 1, "state": "QUEUED"} if inq else None}}}})


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def terminal(lines):
    return [x for x in lines if "status" in x][-1]


# ----------------------------------------------------------------------------- the record line (unit)

FULL = {"PR": "#42", "head": "abc", "base merged at": "main@def", "catalogue tests": "n/a", "branch full suite": "not required",
        "queued": "yes"}


def test_record_line_has_the_six_columns_in_order():
    assert ql.record_line(FULL) == "#42 | abc | main@def | n/a | not required | yes"


@pytest.mark.parametrize("col", ql.COLUMNS)
@pytest.mark.parametrize("bad", [None, "", "   "])
def test_an_unknown_column_refuses_and_names_it(col, bad):
    vals = dict(FULL, **{col: bad})
    with pytest.raises(ql.Refused) as ei:
        ql.record_line(vals)
    assert repr(col) in ei.value.reason and ei.value.extra["column"] == col


def test_a_column_that_is_not_in_the_record_is_refused_too():
    with pytest.raises(ql.Refused) as ei:
        ql.record_line(dict(FULL, extra="x"))
    assert "'extra'" in ei.value.reason


# ----------------------------------------------------------------------------- the happy path and its refusals

def test_lands_a_pr_records_first_enqueues_with_the_admitted_shape_and_reads_the_entry_twice(env):
    env.arm()
    p, lines = env.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert terminal(lines)["status"] == "merged"
    assert env.merges() == [["pr", "merge", "42", "--repo", "o/r", "--auto", "--squash"]]
    flat = [t for c in env.calls() for t in c]
    assert "--admin" not in flat and "--force" not in flat
    order = [c[0:2] for c in env.calls()]
    rec = next(i for i, c in enumerate(env.calls()) if c[:2] == ["pr", "comment"] and "queue-land" not in c[-1] and "|" in c[-1])
    assert rec < env.calls().index(env.merges()[0]), "the record is posted BEFORE the enqueue"
    record = env.bodies()[0].split(" | ")
    assert len(record) == 6 and record[0] == "#42" and record[1] == env.head()[:12]
    assert record[2].startswith("main@") and record[4].startswith("not required")
    assert sum(1 for c in env.calls() if c[:2] == ["api", "graphql"]) >= 3, "two reads after enqueue, then the wait"
    delivered = env.bodies("delivered-by:")[0]
    assert "merged by queue-bot" in delivered and env.head()[:12] in delivered
    assert order  # stub log exists: no real gh


def test_the_queue_entry_that_appears_late_is_still_seen(env):
    env.arm(queue=[_q(False), _q(True), _q(False, merged=True)])
    p, lines = env.run()
    assert terminal(lines)["status"] == "merged" and p.returncode == 0


def test_not_in_queue_after_two_reads_is_its_own_terminal_state(env):
    env.arm(queue=[_q(False)])
    p, lines = env.run()
    assert p.returncode == 8 and terminal(lines)["status"] == "not-in-queue"


def test_a_wait_past_the_timeout_is_a_timeout(env):
    env.arm(queue=[_q(True)])
    p, lines = env.run("--timeout-min", "0")
    assert p.returncode == 7 and terminal(lines)["status"] == "timeout"


def test_dry_run_posts_the_record_and_never_enqueues(env):
    env.arm()
    p, lines = env.run("--dry-run")
    assert p.returncode == 0 and terminal(lines)["status"] == "dry-run"
    assert env.merges() == [] and len(env.bodies()) == 1


@pytest.mark.parametrize("rules,needle", [
    ([{"type": "merge_queue"}], "name a required status check"),
    ([{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci"}]}}], "no merge-queue rule"),
    ([], "no merge-queue rule"),
])
def test_a_base_without_a_queue_or_without_a_required_check_is_refused_before_any_work(env, rules, needle):
    env.arm(rules=rules)
    p, lines = env.run()
    assert p.returncode == 2 and needle in terminal(lines)["reason"]
    assert env.merges() == [] and env.bodies() == []


def test_a_draft_or_closed_pr_is_refused(env):
    env.arm(isDraft=True)
    p, lines = env.run()
    assert p.returncode == 2 and "draft" in terminal(lines)["reason"]
    env.arm(state="CLOSED")
    p, lines = env.run()
    assert p.returncode == 2 and "CLOSED" in terminal(lines)["reason"]


def test_policy_queue_none_points_at_merge_when_green(env):
    (env.repo / ".foundry").mkdir()
    (env.repo / ".foundry" / "operating-model.yaml").write_text("merge:\n  queue: none\n")
    env.arm()
    p, lines = env.run()
    assert p.returncode == 2 and "merge-when-green" in terminal(lines)["reason"]


def test_policy_human_review_required_needs_an_approval(env):
    (env.repo / ".foundry").mkdir()
    (env.repo / ".foundry" / "operating-model.yaml").write_text("merge:\n  human_review_required: true\n")
    env.arm()
    p, lines = env.run()
    assert p.returncode == 2 and "approving review" in terminal(lines)["reason"]


@pytest.mark.parametrize("pr", ["abc", "0", "12 13", "$X"])
def test_pr_must_be_a_literal_number(env, pr):
    env.arm()
    p, lines = env.run(pr=pr)
    assert p.returncode == 2 and env.calls() == []


# ----------------------------------------------------------------------------- base merge, green head, skipped checks

def _advance_main(env):
    env.will_push = True
    git(env.repo, "checkout", "-q", "main")
    (env.repo / "other.txt").write_text("o\n")
    git(env.repo, "add", ".")
    git(env.repo, "commit", "-qm", "main moved")
    git(env.repo, "push", "-q", "origin", "main")
    git(env.repo, "checkout", "-q", "feat")


def test_the_base_is_merged_into_the_branch_with_a_merge_commit_and_a_plain_push(env):
    _advance_main(env)
    before = env.head()
    env.arm()
    p, lines = env.run()
    assert p.returncode == 0, p.stdout + p.stderr
    after = env.head()
    assert after != before
    assert len(git(env.repo, "rev-list", "--parents", "-n1", "HEAD").split()) == 3, "a merge commit, never a rebase"
    assert git(env.repo, "merge-base", "--is-ancestor", "origin/main", "HEAD") == ""
    assert git(env.origin, "rev-parse", "feat") == after, "pushed plain to the branch"
    base_ev = [x for x in lines if x.get("event") == "base-merged"][0]
    assert base_ev["merged_now"] is True


def test_a_base_merge_conflict_is_aborted_and_refused(env):
    git(env.repo, "checkout", "-q", "main")
    (env.repo / "src.py").write_text("a = 2\n")
    git(env.repo, "add", ".")
    git(env.repo, "commit", "-qm", "conflicting")
    git(env.repo, "push", "-q", "origin", "main")
    git(env.repo, "checkout", "-q", "feat")
    env.arm()
    p, lines = env.run()
    assert p.returncode == 2 and "conflicts in src.py" in terminal(lines)["reason"]
    assert git(env.repo, "status", "--porcelain") == "", "the merge was aborted"
    assert env.merges() == []


def test_an_unreachable_base_names_the_base_merged_at_column(env):
    git(env.repo, "remote", "set-url", "origin", str(env.tmp / "nowhere.git"))
    env.arm()
    p, lines = env.run()
    t = terminal(lines)
    assert p.returncode == 2 and t["column"] == "base merged at" and env.merges() == []


def test_a_checkout_that_is_not_the_prs_branch_is_refused(env):
    git(env.repo, "checkout", "-q", "main")
    env.arm()
    p, lines = env.run()
    assert p.returncode == 2 and "not the PR's branch" in terminal(lines)["reason"]


def test_a_red_check_refuses_and_a_skipped_required_check_is_not_a_pass(env):
    env.arm(checks=[{"name": "ci", "bucket": "fail", "workflow": "CI", "link": "u"}])
    p, lines = env.run()
    assert p.returncode == 2 and "failing: ci" in terminal(lines)["reason"] and env.merges() == []
    env.arm(checks=[{"name": "ci", "bucket": "skipping", "workflow": "CI", "link": "u"}],
            extra=[{"match": ["run", "list"], "stdout": json.dumps([{"conclusion": "skipped", "status": "completed", "name": "CI"}])}])
    p, lines = env.run()
    t = terminal(lines)
    assert p.returncode == 2 and "SKIPPED" in t["reason"] and "a skip is not a pass" in t["reason"] and "skipped" in t["reason"]
    assert any(c[:2] == ["run", "list"] and "CI" in c for c in env.calls()), "the owning workflow was read"
    assert env.merges() == []


def test_a_skipped_check_that_is_not_required_is_reported_and_does_not_block(env):
    env.arm(checks=[{"name": "ci", "bucket": "pass", "workflow": "CI", "link": "u"},
                    {"name": "docs-only", "bucket": "skipping", "workflow": "Docs", "link": "u"}],
            extra=[{"match": ["run", "list"], "stdout": json.dumps([{"conclusion": "skipped", "status": "completed"}])}])
    p, lines = env.run()
    assert p.returncode == 0
    rec = [x for x in lines if x.get("event") == "record"][0]
    assert rec["green"]["skipped_not_required"] == ["docs-only (workflow Docs: skipped)"]


def test_a_required_check_that_never_reported_is_not_green(env):
    env.arm(checks=[{"name": "other", "bucket": "pass", "workflow": "CI", "link": "u"}])
    p, lines = env.run()
    assert p.returncode == 2 and "waiting on ci" in terminal(lines)["reason"] and env.merges() == []


def test_the_pr_head_must_be_the_head_that_was_checked(env):
    env.arm(poll=[json.dumps({"state": "OPEN", "headRefOid": "f" * 40})])
    p, lines = env.run()
    assert p.returncode == 2 and "expected" in terminal(lines)["reason"] and env.merges() == []


# ----------------------------------------------------------------------------- catalogue tests and the full suite

def _catalogue_change(env):
    (env.repo / "hooks").mkdir(exist_ok=True)
    (env.repo / "hooks" / "hooks.json").write_text("{}\n")
    (env.repo / "tests").mkdir(exist_ok=True)
    (env.repo / "tests" / "test_hooks_catalogue.py").write_text("# names hooks/hooks.json\n")
    git(env.repo, "add", ".")
    git(env.repo, "commit", "-qm", "catalogue")
    git(env.repo, "push", "-q", "origin", "feat")


def test_a_catalogue_change_runs_every_test_that_names_it_and_the_full_suite(env, tmp_path):
    _catalogue_change(env)
    marks = tmp_path / "ran"
    env.arm()
    p, lines = env.run("--catalogue-test-cmd", "echo {files} >> %s" % marks, "--full-suite-cmd", "echo suite >> %s" % marks)
    assert p.returncode == 0, p.stdout + p.stderr
    ran = marks.read_text()
    assert "tests/test_hooks_catalogue.py" in ran and "suite" in ran
    rec = env.bodies()[0].split(" | ")
    assert rec[3].startswith("pass: 1 test file(s) name hooks/hooks.json") and rec[4].startswith("pass (shared-catalogue-change)")


def test_a_failing_catalogue_test_refuses_and_names_the_column(env):
    _catalogue_change(env)
    env.arm()
    p, lines = env.run("--catalogue-test-cmd", "false", "--full-suite-cmd", "true")
    t = terminal(lines)
    assert p.returncode == 2 and t["column"] == "catalogue tests" and env.merges() == [] and env.bodies() == []


def test_a_required_full_suite_with_no_command_names_the_column(env):
    _catalogue_change(env)
    env.arm()
    p, lines = env.run("--catalogue-test-cmd", "true")
    assert p.returncode == 2 and terminal(lines)["column"] == "branch full suite" and env.merges() == []


def test_a_catalogue_nobody_tests_says_so_instead_of_claiming_a_pass(env):
    (env.repo / "hooks").mkdir()
    (env.repo / "hooks" / "hooks.json").write_text("{}\n")
    git(env.repo, "add", ".")
    git(env.repo, "commit", "-qm", "c")
    git(env.repo, "push", "-q", "origin", "feat")
    env.arm()
    p, lines = env.run("--full-suite-cmd", "true")
    assert env.bodies()[0].split(" | ")[3].startswith("none: no test names hooks/hooks.json")


def test_no_catalogue_change_means_no_full_suite_and_the_record_says_why(env):
    env.arm()
    p, lines = env.run()
    rec = env.bodies()[0].split(" | ")
    assert rec[3].startswith("n/a") and rec[4].startswith("not required")


# ----------------------------------------------------------------------------- rejection handling (AC)

LOG_OUTSIDE = ("2026-11-04T10:00:00Z FAILED tests/test_billing.py::test_rounding - AssertionError\n"
               "2026-11-04T10:00:01Z E   assert 1 == 2\n")
LOG_OWN = "FAILED src.py::test_a - boom\n"
LOG_NO_FILE = "not ok 3 - something flaky\n"
RUN_LIST = json.dumps([{"databaseId": 900, "conclusion": "failure", "headBranch": "gh-readonly-queue/main/pr-42-abc", "workflowName": "CI",
                        "createdAt": "2999-01-01T00:00:00Z"},
                       {"databaseId": 901, "conclusion": "failure", "headBranch": "gh-readonly-queue/main/pr-77-abc", "workflowName": "CI",
                        "createdAt": "2999-01-01T00:00:00Z"}])


def _reject_extra(log):
    return [{"match": ["run", "list"], "stdout": RUN_LIST}, {"match": ["run", "view", "900"], "stdout": log}]


def marker(sig, kind="evidence", n=1, author="queue-bot"):
    return {"author": {"login": author}, "body": "x\n<!-- queue-land:v1 kind=%s sig=%s n=%d run=1 -->" % (kind, sig, n)}


def test_parse_failure_names_the_failing_test_and_file_and_ignores_timestamps():
    f = ql.parse_failure(LOG_OUTSIDE)
    assert f["ids"] == ["tests/test_billing.py::test_rounding"] and f["files"] == ["tests/test_billing.py"]
    assert f["signature"] == ql.parse_failure(LOG_OUTSIDE.replace("10:00:00", "11:11:11"))["signature"]
    assert ql.classify(f, ["src.py"]) == "outside-diff" and ql.classify(f, ["tests/test_billing.py"]) == "own-files"
    assert ql.classify(ql.parse_failure(LOG_NO_FILE), ["src.py"]) == "unclassified"


def test_an_outside_diff_rejection_requeues_exactly_once_and_records_the_evidence(env, tmp_path):
    marks = tmp_path / "suite"
    # round 1: in queue (2 reads), then gone twice; round 2: in queue twice, merged
    queue = [_q(True), _q(True), _q(False), _q(False), _q(True), _q(True), _q(False, merged=True)]
    env.arm(queue=queue, extra=_reject_extra(LOG_OUTSIDE))
    p, lines = env.run("--full-suite-cmd", "echo suite >> %s" % marks)
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(env.merges()) == 2, "enqueued, rejected, re-queued once"
    evidence = env.bodies("kind=evidence")
    assert len(evidence) == 1 and "tests/test_billing.py" in evidence[0] and "Re-queue 1 of 1" in evidence[0]
    assert any(x.get("event") == "requeue" for x in lines)
    assert marks.read_text().count("suite") == 1, "the full suite runs once, on the re-queue (after-queue-rejection)"
    assert len([b for b in env.bodies() if b.count(" | ") == 5]) == 2, "a record before EACH enqueue"
    log_read = [c for c in env.calls() if c[:2] == ["run", "view"]]
    assert log_read and "--log-failed" in log_read[0]
    # the failed log was read before the evidence was posted
    calls = env.calls()
    assert calls.index(log_read[0]) < next(i for i, c in enumerate(calls) if c[:2] == ["pr", "comment"] and "kind=evidence" in c[-1])


def test_the_same_failure_a_second_time_writes_a_quarantine_request_and_never_requeues(env):
    sig = ql.parse_failure(LOG_OUTSIDE)["signature"]
    queue = [_q(True), _q(True), _q(False), _q(False), _q(True), _q(True), _q(False), _q(False)]
    # comments: start of run (none), first rejection (none yet), second rejection (the evidence from the first)
    comments = ['{"comments": []}', '{"comments": []}', json.dumps({"comments": [marker(sig)]})]
    env.arm(queue=queue, comments=comments, extra=_reject_extra(LOG_OUTSIDE))
    p, lines = env.run("--full-suite-cmd", "true")
    t = terminal(lines)
    assert p.returncode == 4 and t["status"] == "quarantine-requested", p.stdout + p.stderr
    assert len(env.merges()) == 2, "exactly one re-queue, then no third roll"
    req = json.loads(Path(t["request"]).read_text())
    rec = req["quarantine"][0]
    assert set(rec) == {"test", "owner", "issue", "entry", "expiry"} and rec["test"] == "tests/test_billing.py::test_rounding"
    posted = env.bodies("QUARANTINE REQUEST")[0]
    assert json.loads(posted.split("```json\n")[1].split("\n```")[0]) == req, "the full record is IN the PR comment"
    assert rec["issue"] == URL and rec["owner"]
    assert len(rec["entry"]) == len(rec["expiry"]) == 10 and rec["expiry"] > rec["entry"]
    assert env.bodies("QUARANTINE REQUEST") and "Quarantine is not skip" in env.bodies("QUARANTINE REQUEST")[0]


def test_a_second_identical_failure_in_a_new_session_quarantines_without_a_roll(env):
    sig = ql.parse_failure(LOG_OUTSIDE)["signature"]
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False)],
            comments=[json.dumps({"comments": [marker(sig)]})], extra=_reject_extra(LOG_OUTSIDE))
    p, lines = env.run("--full-suite-cmd", "true")      # a re-run after a rejection owes the full suite
    assert p.returncode == 4 and len(env.merges()) == 1


def test_a_different_failure_after_the_budget_is_spent_is_never_requeued(env):
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False)],
            comments=[json.dumps({"comments": [marker("0" * 12)]})], extra=_reject_extra(LOG_OUTSIDE))
    p, lines = env.run("--full-suite-cmd", "true")
    t = terminal(lines)
    assert p.returncode == 5 and t["status"] == "requeue-budget-spent" and len(env.merges()) == 1


def test_a_failure_in_the_prs_own_files_is_fixed_not_rolled(env):
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False)], extra=_reject_extra(LOG_OWN))
    p, lines = env.run()
    assert p.returncode == 3 and terminal(lines)["status"] == "fix-own-files" and len(env.merges()) == 1
    assert env.bodies("kind=evidence") == []


def test_a_log_that_names_no_file_cannot_be_called_outside_the_diff(env):
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False)], extra=_reject_extra(LOG_NO_FILE))
    p, lines = env.run()
    assert p.returncode == 6 and len(env.merges()) == 1


# ----------------------------------------------------------------------------- the record's destination, zero real gh

def test_the_record_goes_to_the_message_log_when_it_exists(env, tmp_path):
    fake = tmp_path / "foundry_message_log.py"
    sink = tmp_path / "sink.txt"
    fake.write_text("import sys\nopen(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n' + sys.stdin.read())\n" % str(sink))
    env.arm()
    p, lines = env.run("--message-log", str(fake))
    assert p.returncode == 0
    text = sink.read_text()
    assert "post --to all --kind FINDING" in text and " | " in text and URL in text
    assert not any(" | " in b for b in env.bodies() if "delivered-by" not in b), "not duplicated as a PR comment"


def test_no_call_escapes_the_stub(env):
    env.arm()
    env.run()
    which = subprocess.run(["bash", "-c", "command -v gh"], env=env.env, capture_output=True, text=True).stdout.strip()
    assert Path(which).resolve() == (STUB_DIR / "gh").resolve()
    assert env.calls(), "every call was journaled by the stub"


# ----------------------------------------------------------------------------- fix round 1

def test_a_pr_that_is_already_queued_is_refused_before_any_push(env):
    """A re-run after a timeout: the PR is in the queue. Even with the base moved, nothing may be pushed."""
    _advance_main(env)
    tip = git(env.origin, "rev-parse", "feat")
    env.arm(already_queued=True)
    p, lines = env.run()
    t = terminal(lines)
    assert p.returncode == 2 and t["reason_code"] == "dequeue-first" and "dequeue it first" in t["reason"]
    assert git(env.origin, "rev-parse", "feat") == tip and env.merges() == []


def test_the_base_merge_is_not_pushed_unless_local_green_passes(env):
    _advance_main(env)
    tip = git(env.origin, "rev-parse", "feat")
    env.arm()
    p, lines = env.run("--local-green-cmd", "false")
    assert p.returncode == 2 and terminal(lines)["reason_code"] == "local-green-failed"
    assert git(env.origin, "rev-parse", "feat") == tip, "the merge commit stayed local"
    git(env.repo, "reset", "-q", "--hard", "origin/feat")               # drop the unpushed merge commit, then the control
    env.arm()
    p, lines = env.run("--local-green-cmd", "true")
    assert p.returncode == 0 and git(env.origin, "rev-parse", "feat") == env.head()


def test_the_default_local_green_runner_is_the_plugins_foundry_test_script(env):
    _advance_main(env)
    env.arm()
    p, lines = env.run("--local-green-cmd", "echo ran >> %s" % (env.tmp / "lg"))
    assert (env.tmp / "lg").read_text() == "ran\n" and p.returncode == 0
    assert "foundry-test.sh" in (ROOT / "scripts" / "foundry_queue_land.py").read_text()


def test_a_fork_pr_is_refused(env):
    env.arm(isCrossRepository=True)
    p, lines = env.run()
    assert p.returncode == 2 and "fork" in terminal(lines)["reason"] and env.merges() == []


@pytest.mark.parametrize("ref", ["--upload-pack=x", "a..b", "bad ref", "x;y"])
def test_branch_names_are_validated_with_the_hooks_pattern(env, ref):
    env.arm(baseRefName=ref)
    p, lines = env.run()
    assert p.returncode == 2 and "plain ref name" in terminal(lines)["reason"] and env.merges() == []


def test_classic_branch_protection_counts_as_a_required_check(env):
    env.arm(rules=[{"type": "merge_queue"}],
            classic={"protected": True, "protection": {"required_status_checks": {"contexts": ["ci"], "checks": []}}})
    p, lines = env.run()
    assert p.returncode == 0, p.stdout + p.stderr
    env.arm(rules=[{"type": "merge_queue"}])
    p, lines = env.run()
    assert p.returncode == 2 and "classic branch protection" in terminal(lines)["reason"]


def test_a_stale_failed_run_from_an_earlier_enqueue_is_not_this_rejection(env):
    old = json.loads(RUN_LIST)
    old[0]["createdAt"] = "2001-01-01T00:00:00Z"
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False)],
            extra=[{"match": ["run", "list"], "stdout": json.dumps(old)}, {"match": ["run", "view", "900"], "stdout": LOG_OUTSIDE}])
    p, lines = env.run()
    assert p.returncode == 6 and len(env.merges()) == 1 and env.bodies("kind=evidence") == []


def test_a_marker_from_a_stranger_does_not_count(env):
    sig = ql.parse_failure(LOG_OUTSIDE)["signature"]
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False), _q(True), _q(True), _q(False, merged=True)],
            comments=[json.dumps({"comments": [marker(sig, author="stranger")]})], extra=_reject_extra(LOG_OUTSIDE))
    p, lines = env.run("--full-suite-cmd", "true")
    assert p.returncode == 0 and len(env.merges()) == 2, "the forged marker was ignored: a normal first re-queue"


def test_without_a_readable_login_the_count_is_not_trusted(env):
    env.arm()
    cfg = json.loads(env.scenario.read_text())
    cfg["responses"] = [r for r in cfg["responses"] if r["match"] != ["api", "user"]] + [{"match": ["api", "user"], "rc": 1}]
    env.scenario.write_text(json.dumps(cfg))
    p, lines = env.run()
    assert p.returncode == 2 and "login" in terminal(lines)["reason"]


def test_a_repo_with_no_catalogue_declared_says_so(env):
    env.arm()
    p, lines = env.run()
    assert env.bodies()[0].split(" | ")[3] == "n/a: no catalogue declared"


def test_the_quarantine_request_lists_every_failing_test(env):
    log = LOG_OUTSIDE + "FAILED tests/test_billing.py::test_tax - boom\n"
    sig = ql.parse_failure(log)["signature"]
    env.arm(queue=[_q(True), _q(True), _q(False), _q(False)], comments=[json.dumps({"comments": [marker(sig)]})],
            extra=_reject_extra(log))
    p, lines = env.run("--full-suite-cmd", "true")
    req = json.loads(Path(terminal(lines)["request"]).read_text())
    assert [r["test"] for r in req["quarantine"]] == ["tests/test_billing.py::test_rounding", "tests/test_billing.py::test_tax"]


def test_quarantine_requests_are_gitignored():
    ig = (ROOT / ".gitignore").read_text().splitlines()
    assert ".foundry/quarantine-requests/" in ig


def test_the_queue_is_re_read_right_before_the_base_merge_push(env):
    """Not queued at the start, queued by the time the push is due: nothing is pushed."""
    _advance_main(env)
    tip = git(env.origin, "rev-parse", "feat")
    env.will_push = False
    env.arm(queue=[_q(True)])                       # read 1 (start): not queued; read 2 (pre-push): queued
    p, lines = env.run()
    assert p.returncode == 2 and terminal(lines)["reason_code"] == "dequeue-first"
    assert git(env.origin, "rev-parse", "feat") == tip and env.merges() == []


def test_an_up_to_date_branch_is_not_merged_or_pushed_again(env):
    """Regression: `rev-parse --end-of-options` prints the marker line, which once made every branch look behind."""
    env.arm()
    p, lines = env.run()
    ev = [x for x in lines if x.get("event") == "base-merged"][0]
    assert ev["merged_now"] is False and not [x for x in lines if x.get("event") == "local-green"]
    assert len(ev["base_sha"]) == 12 and ev["base_sha"] == git(env.repo, "rev-parse", "origin/main")[:12]
