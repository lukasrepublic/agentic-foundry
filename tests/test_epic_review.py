"""dl-12: scripts/foundry_epic_review.py (offline: one stubbed gh runner, a fake clock, a fake Done-means runner)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_epic_review as co  # noqa: E402
import foundry_release_plan as ep  # noqa: E402
import foundry_epic_issue as pi  # noqa: E402

SHA = "a" * 40
REPO = "acme/code"


def atom(i, blocked_by=(), paths=()):
    return {"id": i, "node_id": f"N_{i}", "item_id": f"I_{i}", "blocked_by": list(blocked_by),
            "paths": list(paths), "labels": [], "state": "open"}


class Clock:
    def __init__(self, left=60.0):
        self._left = left
        self.slept = []

    def left(self):
        return self._left

    def sleep(self, s):
        self.slept.append(s)
        self._left -= s

    def utcnow(self):
        import datetime
        return datetime.datetime(2026, 11, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)


class GH:
    """A stubbed GitHub. `writes` records every mutating call as (method, path, payload)."""

    def __init__(self):
        self.writes = []
        self.lease = False
        self.issues = {1: "open", 2: "open"}
        self.statuses = []          # newest first
        self.status_seq = None      # list of states returned one per read, for the pending case
        self.milestone_open = 0
        self.rulesets = []
        self.releases = []
        self.ruleset_denied = False
        self.reads = 0
        self.unreachable = False

    def __call__(self, args, payload=None):
        if self.unreachable:
            raise pi.Unreachable("down")
        method = "GET"
        if args[:1] == ["-X"]:
            method, args = args[1], args[2:]
        path = args[0]
        if args == ["graphql"]:
            method = "POST"
        if method != "GET":
            self.writes.append((method, path, payload))
        if path == f"repos/{REPO}/git/ref/heads/release/2.0.0":
            return {"object": {"sha": SHA}}
        if path == f"repos/{REPO}/git/refs" and method == "POST":
            if self.lease:
                raise pi.IssueError("gh api failed: Reference already exists (HTTP 422)")
            self.lease = True
            return {}
        if path.startswith(f"repos/{REPO}/git/ref/foundry/claims/"):
            if self.lease:
                return {}
            raise pi.IssueError("gh api failed: Not Found")
        if path.startswith(f"repos/{REPO}/git/refs/foundry/claims/") and method == "DELETE":
            self.lease = False
            return {}
        if path.startswith(f"repos/{REPO}/issues/"):
            n = int(path.rsplit("/", 1)[1])
            if method == "PATCH":
                self.issues[n] = "closed"
                return {}
            return {"state": self.issues[n]}
        if path.startswith(f"repos/{REPO}/rulesets"):
            if method in ("POST", "PUT"):
                if self.ruleset_denied:
                    raise pi.IssueError("gh api failed: Must have admin rights (HTTP 403)")
                if method == "PUT":
                    self.rulesets[:] = [dict(payload, id=11)]
                    return {}
                self.rulesets.append(dict(payload, id=11))
                return {"id": 11}
            if path.endswith("/rulesets/11"):
                return dict(self.rulesets[0])
            return [{"id": 11, "name": r["name"]} for r in self.rulesets]
        if path.startswith(f"repos/{REPO}/statuses/") and method == "POST":
            self.statuses.insert(0, {"context": payload["context"], "state": payload["state"]})
            return {}
        if "/statuses?" in path:
            self.reads += 1
            if self.status_seq:
                state = self.status_seq.pop(0) if len(self.status_seq) > 1 else self.status_seq[0]
                return [{"context": co.CHECK, "state": state}]
            return list(self.statuses)
        if "/check-runs?" in path:
            return {"check_runs": []}
        if path == f"repos/{REPO}/milestones/7":
            return {"open_issues": self.milestone_open}
        if path.startswith(f"repos/{REPO}/releases") and method == "GET":
            return list(self.releases)
        if path == f"repos/{REPO}/releases" and method == "POST":
            self.releases.append(dict(payload, id=99))
            return {}
        if path.startswith(f"repos/{REPO}/releases/") and method == "PATCH":
            return {}
        if args == ["graphql"]:
            if "createProjectV2StatusUpdate" in payload["query"]:
                return {"data": {"createProjectV2StatusUpdate": {"statusUpdate": {"id": "SU"}}}}
            return {"data": {"repositoryOwner": {"projectV2": {"id": "PVT"}}}}
        raise AssertionError(f"unstubbed gh call: {method} {path}")

    def kinds(self):
        return [(m, p) for m, p, _ in self.writes]

    def releases_written(self):
        return [w for w in self.writes if w[1] == f"repos/{REPO}/releases"]


@pytest.fixture
def gh(monkeypatch):
    g = GH()
    g.issue_rec = {"done_trusted": True, "done_reason": "", "done": "true"}
    g.read_calls = []
    g.head = SHA
    monkeypatch.setattr(pi, "gh_api", g)

    def reader(ref, trust):
        g.read_calls.append((ref, trust))
        return dict(g.issue_rec) if trust is False or g.issue_rec["done_trusted"] else dict(g.issue_rec, done_trusted=True)
    monkeypatch.setattr(co, "read_epic_issue", reader, raising=False)
    monkeypatch.setattr(co, "git_head", lambda d: g.head, raising=False)
    return g


@pytest.fixture
def files(tmp_path):
    plan = ep.build_plan("prog", {"project_id": "P", "atoms": [atom("a-one", paths=["x/"]),
                                                              atom("a-two", ["a-one"], ["y/"])]})
    (tmp_path / "plan.yaml").write_text(ep.render(plan))
    merged = {"states": {"a-one": "merged", "a-two": "merged"}}
    (tmp_path / "states.json").write_text(json.dumps(merged))
    (tmp_path / "atoms.json").write_text(json.dumps({"atoms": [{"id": "a-one", "issue": 1},
                                                              {"id": "a-two", "issue": 2}]}))
    (tmp_path / "learned.json").write_text(json.dumps({"atoms": []}))
    (tmp_path / "done.yaml").write_text(yaml.safe_dump([{"name": "suite", "argv": ["true"]}]))
    return tmp_path


def argv(files, *extra):
    return ["run", "prog", "--repo", REPO, "--integration", "release/2.0.0", "--tag", "v2.0.0",
            "--plan", str(files / "plan.yaml"), "--states", str(files / "states.json"),
            "--atoms", str(files / "atoms.json"), "--milestone", "7", "--project", "acme/3",
            "--learned-json", str(files / "learned.json"), *extra]


def run(files, *extra, clock=None, runner=lambda a, t, c=None: 0, capsys=None):
    rc = co.main(argv(files, *extra), clock or Clock(), runner)
    out, err = capsys.readouterr()
    return rc, out, err


def test_happy_path_creates_a_draft_release_pinned_to_the_head(gh, files, capsys):
    rc, out, err = run(files, "--epic-issue", "acme/code#9", capsys=capsys)
    assert rc == 0, err
    rep = json.loads(out)
    assert rep["sha"] == SHA and rep["acceptance"] == "success" and rep["release"] == "created"
    assert rep["atoms"]["closed"] == ["a-one", "a-two"] and gh.issues == {1: "closed", 2: "closed"}
    (_, _, rel), = gh.releases_written()
    assert rel["draft"] is True and rel["target_commitish"] == SHA and rel["tag_name"] == "v2.0.0"
    assert gh.statuses[0] == {"context": "epic/acceptance", "state": "success"}
    assert [r["name"] for r in gh.rulesets] == ["epic-acceptance-prog"]
    assert rep["ruleset_delete"] == f"gh api -X DELETE repos/{REPO}/rulesets/11"
    kinds = gh.kinds()
    assert kinds[0] == ("POST", f"repos/{REPO}/git/refs")                       # lease first
    assert kinds[-1] == ("DELETE", f"repos/{REPO}/git/refs/foundry/claims/epic-review/prog")   # lease dropped last
    assert ("POST", "graphql") in kinds and kinds.index(("POST", "graphql")) > kinds.index(("POST", f"repos/{REPO}/releases"))
    assert gh.lease is False
    assert not any(p.endswith("/tags") or "git/tags" in p for _, p in kinds)    # never tags
    assert not any(w[2] and w[2].get("draft") is False for w in gh.writes if w[2])


def test_refuses_and_names_an_unmerged_batch(gh, files, capsys):
    (files / "states.json").write_text(json.dumps({"states": {"a-one": "merged", "a-two": "in review"}}))
    rc, out, err = run(files, capsys=capsys)
    plan = yaml.safe_load((files / "plan.yaml").read_text())
    batch = next(b["id"] for w in plan["increments"] for b in w["batches"] if "a-two" in b["atoms"])
    assert rc == 2 and out == ""
    assert err.startswith("foundry_epic_review: refused: batch not merged: ") and batch in err
    assert gh.writes == []                                                        # no lease, no close, nothing


def test_second_session_is_refused_while_the_lease_is_live(gh, files, capsys):
    gh.lease = True
    rc, out, err = run(files, capsys=capsys)
    assert rc == 2 and "held by another session" in err
    assert gh.releases_written() == [] and gh.issues == {1: "open", 2: "open"}
    assert gh.lease is True                       # the other session's lease is untouched
    assert ("DELETE", f"repos/{REPO}/git/refs/foundry/claims/epic-review/prog") not in gh.kinds()


@pytest.mark.parametrize("why", ["red", "milestone"])
def test_red_acceptance_or_open_milestone_creates_no_release(gh, files, capsys, why):
    if why == "red":
        gh.statuses = [{"context": co.CHECK, "state": "failure"}]
        want = "is red"
    else:
        gh.statuses = [{"context": co.CHECK, "state": "success"}]
        gh.milestone_open = 2
        want = "2 open issue(s)"
    rc, out, err = run(files, capsys=capsys)
    assert rc == 2 and want in err
    assert gh.releases_written() == [] and ("POST", "graphql") not in gh.kinds()
    assert gh.lease is False                      # the lease is dropped on the way out


def test_failing_done_means_publishes_a_failure_and_no_release(gh, files, capsys):
    rc, out, err = run(files, "--epic-issue", "acme/code#9", runner=lambda a, t, c=None: 1, capsys=capsys)
    assert rc == 2 and "is red" in err
    assert gh.statuses[0]["state"] == "failure" and gh.releases_written() == []


def test_pending_check_is_recorded_with_a_recheck_time_and_never_waits_past_60s(gh, files, capsys):
    gh.status_seq = ["pending"]
    clock = Clock(left=60)
    rc, out, err = run(files, clock=clock, capsys=capsys)
    assert rc == 3, err
    rep = json.loads(out)
    assert rep["pending"] == [{"check": "epic/acceptance", "recheck_at": "2026-11-05T12:05:00Z"}]
    assert sum(clock.slept) <= 60 and gh.releases_written() == [] and gh.lease is False


def test_pending_that_turns_green_within_the_budget_completes(gh, files, capsys):
    gh.status_seq = ["pending", "pending", "success"]
    rc, out, err = run(files, clock=Clock(left=60), capsys=capsys)
    assert rc == 0, err
    assert json.loads(out)["release"] == "created"


def test_dry_run_reads_but_writes_nothing(gh, files, capsys):
    gh.statuses = [{"context": co.CHECK, "state": "success"}]
    rc, out, err = run(files, "--dry-run", capsys=capsys)
    assert rc == 0, err
    rep = json.loads(out)
    assert rep["dry_run"] is True and rep["release"] == "would-create" and rep["ruleset"] == "would-create"
    assert gh.writes == [] and gh.issues == {1: "open", 2: "open"}


def test_existing_draft_is_repointed_and_a_published_release_is_refused(gh, files, capsys):
    gh.statuses = [{"context": co.CHECK, "state": "success"}]
    gh.releases = [{"tag_name": "v2.0.0", "draft": True, "id": 5}]
    rc, out, err = run(files, capsys=capsys)
    assert rc == 0 and json.loads(out)["release"] == "updated"
    assert ("PATCH", f"repos/{REPO}/releases/5") in gh.kinds()
    gh.releases = [{"tag_name": "v2.0.0", "draft": False, "id": 5}]
    rc, out, err = run(files, capsys=capsys)
    assert rc == 2 and "already published" in err


def test_ruleset_that_cannot_be_created_is_an_external_provisioning_stop(gh, files, capsys):
    gh.ruleset_denied = True
    gh.statuses = [{"context": co.CHECK, "state": "success"}]
    rc, out, err = run(files, capsys=capsys)
    assert rc == 2 and "external-provisioning" in err and gh.releases_written() == []


def test_outage_changes_nothing(gh, files, capsys):
    gh.unreachable = True
    rc, out, err = run(files, capsys=capsys)
    assert rc == 2 and "unreachable" in err and gh.writes == []


def test_final_status_update_is_complete_and_names_the_release(gh, files, capsys):
    rc, out, err = run(files, "--epic-issue", "acme/code#9", capsys=capsys)
    assert rc == 0
    posts = [pl for m, p, pl in gh.writes if p == "graphql" and "createProjectV2StatusUpdate" in pl["query"]]
    assert len(posts) == 1
    pl = posts[0]
    inp = pl["variables"]["input"]
    assert inp["status"] == "COMPLETE" and SHA in inp["body"] and "v2.0.0" in inp["body"]


def test_ruleset_body_requires_the_acceptance_context():
    body = co.ruleset_body("prog", "main")
    (rule,) = body["rules"]
    assert rule["type"] == "required_status_checks"
    assert rule["parameters"]["required_status_checks"] == [{"context": "epic/acceptance"}]
    assert body["conditions"]["ref_name"]["include"] == ["refs/heads/main"]


@pytest.mark.parametrize("bad", [["--tag", "latest"], ["--repo", "no-slash"], ["--integration", "a..b"]])
def test_bad_arguments_are_refused(gh, files, capsys, bad):
    a = argv(files)
    for i in range(0, len(bad), 2):
        a[a.index(bad[i]) + 1] = bad[i + 1]
    rc = co.main(a, Clock(), lambda a, t, c=None: 0)
    assert rc == 2 and "refused" in capsys.readouterr().err and gh.writes == []


def test_help_runs():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "foundry_epic_review.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "epic-review" in r.stdout


# ---- fix round: Done-means provenance, own timeout, right tree, ruleset timing ----
def _executed():
    ran = []

    def runner(a, t, c=None):
        ran.append((list(a), t, c))
        return 0
    return ran, runner


def test_untrusted_epic_issue_is_refused_and_nothing_runs(gh, files, capsys):
    gh.issue_rec = {"done_trusted": False, "done_reason": "body edited by stranger", "done": ""}
    ran, runner = _executed()
    rc, out, err = run(files, "--epic-issue", "acme/code#9", runner=runner, capsys=capsys)
    assert rc == 2 and "done-means-untrusted" in err
    assert ran == [] and gh.writes == [] and gh.read_calls == [("acme/code#9", False)]


def test_trust_author_override_is_passed_to_the_reader(gh, files, capsys):
    gh.issue_rec = {"done_trusted": False, "done_reason": "x", "done": "true"}
    ran, runner = _executed()
    rc, out, err = run(files, "--epic-issue", "acme/code#9", "--trust-author", runner=runner, capsys=capsys)
    assert rc == 0, err
    assert gh.read_calls == [("acme/code#9", True)] and ran == [(["true"], 1800, ".")]


def test_local_done_means_must_be_a_committed_file(gh, files, capsys):
    rc, out, err = run(files, "--done-means", "done.yaml", "--run-dir", str(files), capsys=capsys)
    assert rc == 2 and "done-means-untrusted" in err and gh.writes == []      # tmp dir is no git work tree


def test_dry_run_lists_the_exact_argv_it_would_run(gh, files, capsys):
    gh.issue_rec = {"done_trusted": True, "done_reason": "", "done": "python3 -m pytest -q\nmake 'check all'"}
    gh.statuses = [{"context": co.CHECK, "state": "success"}]
    ran, runner = _executed()
    rc, out, err = run(files, "--epic-issue", "acme/code#9", "--dry-run", runner=runner, capsys=capsys)
    assert rc == 0, err
    assert json.loads(out)["done_means_plan"] == [["python3", "-m", "pytest", "-q"], ["make", "check all"]]
    assert ran == [] and gh.writes == []


def test_a_ninety_second_done_means_completes_and_posts_the_status(gh, files, capsys):
    seen = []

    def runner(a, t, c=None):
        seen.append(t)
        if t < 90:
            raise subprocess.TimeoutExpired(a, t)     # the old code capped the command at the 60 s budget
        return 0
    clock = Clock(left=60)
    rc, out, err = run(files, "--epic-issue", "acme/code#9", runner=runner, clock=clock, capsys=capsys)
    assert rc == 0, err
    assert seen == [1800] and gh.statuses[0] == {"context": "epic/acceptance", "state": "success"}
    assert clock.slept == []                          # running a command is not waiting on a check


def test_done_means_timeout_option_is_honoured_and_a_timeout_is_a_failure(gh, files, capsys):
    def slow(a, t, c=None):
        raise subprocess.TimeoutExpired(a, t)
    rc, out, err = run(files, "--epic-issue", "acme/code#9", "--done-means-timeout", "5", runner=slow, capsys=capsys)
    assert rc == 2 and gh.statuses[0]["state"] == "failure" and gh.releases_written() == []


def test_wrong_tree_is_refused_before_any_command_runs(gh, files, capsys):
    gh.head = "b" * 40
    ran, runner = _executed()
    rc, out, err = run(files, "--epic-issue", "acme/code#9", runner=runner, capsys=capsys)
    assert rc == 2 and "not at the integration head" in err
    assert ran == [] and gh.statuses == [] and gh.releases_written() == [] and gh.lease is False


@pytest.mark.parametrize("why", ["red", "milestone", "pending"])
def test_refused_or_pending_run_writes_no_ruleset(gh, files, capsys, why):
    gh.statuses = [{"context": co.CHECK, "state": "failure"}]
    if why == "milestone":
        gh.statuses = [{"context": co.CHECK, "state": "success"}]
        gh.milestone_open = 1
    if why == "pending":
        gh.statuses, gh.status_seq = [], ["pending"]
    rc, out, err = run(files, capsys=capsys)
    assert rc in (2, 3)
    assert gh.rulesets == [] and not any("rulesets" in p for _, p in gh.kinds())


def test_existing_ruleset_with_other_enforcement_is_updated(gh, files, capsys):
    gh.statuses = [{"context": co.CHECK, "state": "success"}]
    gh.rulesets = [dict(co.ruleset_body("prog", "main"), enforcement="disabled", id=11)]
    rc, out, err = run(files, capsys=capsys)
    assert rc == 0, err and json.loads(out)["ruleset"] == "updated"
    assert gh.rulesets[0]["enforcement"] == "active"
