"""dl-09-cut-release-cadence: no calendar rule on a cut; release notes list programmes + other PRs."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "foundry-cut-release.py"
_spec = importlib.util.spec_from_file_location("fcr_dl09", SCRIPT)
fcr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fcr)


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True, text=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _merge(d, pr, branch):
    _git(d, "commit", "--allow-empty", "-qm", f"work {pr}")
    _git(d, "commit", "--allow-empty", "-qm", f"Merge pull request #{pr} from o/{branch}")


def test_cut_one_day_after_previous_tag_is_not_refused_for_cadence(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")  # tagged just now: 0 days old
    assert not hasattr(fcr, "release_cadence") and not hasattr(fcr, "CADENCE_DAYS")
    monkeypatch.setattr(fcr, "preflight", lambda tree, version, suite_runner=None: [("stub", True, "ok")])
    monkeypatch.setattr(fcr, "publish_plan", lambda tree, version: ["step"])
    monkeypatch.setattr(fcr, "read_versions", lambda tree: {"repo": None})
    r = fcr.cut_release(str(root), "1.1.0", acceptance_fn=lambda tree: {"verdict": "pass"},
                        er_state_fn=lambda ers, repo=None: {}, suite_runner=lambda t: (0, "", ""),
                        draft_fn=lambda t: None)
    assert r["state"] == "ready" and r["release_notes"]["previous_tag"] == "v1.0.0"
    assert all("cadence" not in c[0] for c in r["preflight"])


def test_hotfix_flag_is_an_unknown_option(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "--tree", str(tmp_path), "--version", "1.1.0", "--hotfix"],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "unrecognized arguments: --hotfix" in p.stderr


def _pr(n, head):
    return {"number": n, "headRefName": head}


def test_release_notes_from_prs_fn_lists_programmes_and_other(tmp_path):
    root = _repo(tmp_path)
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    seen = {}

    def prs(tree, since):
        seen["since"] = since
        return [_pr(11, "alpha/a-1"), _pr(12, "alpha/a-2"), _pr(13, "beta/b-1"), _pr(14, "adhoc-fix"),
                _pr(15, "hotfix/x"), _pr(16, "release/2.0.0"), _pr(17, "dependabot/pip/y"), _pr(18, "renovate/z")]
    n = fcr.release_notes(str(root), "1.1.0", draft_fn=lambda t: None, prs_fn=prs)
    assert n["source"] == "merged PRs (gh)" and seen["since"]
    assert [(p["id"], p["prs"]) for p in n["programmes"]] == [("alpha", [11, 12]), ("beta", [13])]
    assert n["other_prs"] == 5
    assert n["lines"] == ["programme alpha (2 PR(s))", "programme beta (1 PR(s))", "5 other PR(s) since v1.0.0"]


def test_release_notes_git_fallback_handles_merge_and_squash_subjects(tmp_path):
    root = _repo(tmp_path)
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    _merge(root, 11, "alpha/a-1")
    _merge(root, 12, "alpha/a-2")
    _merge(root, 14, "adhoc-fix")
    _git(root, "commit", "--allow-empty", "-qm", "feat: squashed thing (#31)")
    _git(root, "commit", "--allow-empty", "-qm", "no pr number here")
    n = fcr.release_notes(str(root), "1.1.0", draft_fn=lambda t: None, prs_fn=lambda t, s: None)
    assert n["source"] == "git history"
    assert [(p["id"], p["prs"]) for p in n["programmes"]] == [("alpha", [11, 12])]
    assert n["other_prs"] == 2


def test_release_notes_offline_never_calls_gh(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _merge(root, 5, "alpha/a")
    real = subprocess.run

    def guard(cmd, *a, **k):
        assert cmd[0] != "gh", cmd
        return real(cmd, *a, **k)
    monkeypatch.setattr(fcr.subprocess, "run", guard)
    assert fcr.release_notes(str(root), "1.1.0")["source"] == "git history"        # pytest => offline
    assert fcr.release_notes(str(root), "1.1.0", offline=True)["source"] == "git history"


def test_release_notes_no_previous_tag_and_no_prs(tmp_path):
    root = _repo(tmp_path)
    n = fcr.release_notes(str(root), "1.0.0", draft_fn=lambda t: None, prs_fn=lambda t, s: [])
    assert n["previous_tag"] is None and n["programmes"] == [] and n["other_prs"] == 0
    assert n["lines"] == ["0 other PR(s) since the start of history"]


def test_release_notes_prefer_the_closeout_draft_release(tmp_path):
    root = _repo(tmp_path)
    _merge(root, 21, "ignored/x")
    n = fcr.release_notes(str(root), "1.1.0",
                          draft_fn=lambda t: {"programmes": ["p-one", "p-two"], "other_prs": 3})
    assert n["source"] == "close-out draft release"
    assert [p["id"] for p in n["programmes"]] == ["p-one", "p-two"] and n["other_prs"] == 3
    assert n["lines"][-1] == "3 other PR(s) in the close-out draft release"


def test_previous_tag_rules(tmp_path):
    root = _repo(tmp_path)
    assert fcr._previous_tag(str(root), "1.0.0") is None
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    _git(root, "tag", "v1.1.0")                 # the version being cut: excluded
    _git(root, "tag", "staging-20261007")       # not v<digits>: ignored
    assert fcr._previous_tag(str(root), "1.1.0") == "v1.0.0"
    _git(root, "tag", "v1.0.5")                 # lightweight tag counts
    assert fcr._previous_tag(str(root), "1.1.0") in ("v1.0.5", "v1.0.0")
    assert fcr._previous_tag(str(root), "1.0.5") != "v1.0.5"


def _fake_gh(monkeypatch, stdout, rc=0):
    calls = []

    def run(cmd, *a, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    monkeypatch.setattr(fcr.subprocess, "run", run)
    return calls


def test_draft_release_input_matches_by_tag_and_grammar(tmp_path, monkeypatch):
    import json
    rows = [{"tag": "v1.0.0", "body": "- programme: old"},
            {"tag": "v1.1.0", "body": "notes\n- programme: p-one\n* programme: p-two\n- other: 4\nx"}]
    calls = _fake_gh(monkeypatch, "\n".join(json.dumps(r) for r in rows))
    assert fcr._draft_release_input(str(tmp_path), "1.1.0") == {"programmes": ["p-one", "p-two"], "other_prs": 4}
    assert "--paginate" in calls[0]
    assert fcr._draft_release_input(str(tmp_path), "1.0.0")["programmes"] == ["old"]
    assert fcr._draft_release_input(str(tmp_path), "1.1.0")["other_prs"] == 4


def test_draft_release_input_no_match_bad_json_or_gh_failure_is_none(tmp_path, monkeypatch):
    import json
    _fake_gh(monkeypatch, json.dumps({"tag": "v9.9.9", "body": "- programme: x"}))
    assert fcr._draft_release_input(str(tmp_path), "1.1.0") is None
    _fake_gh(monkeypatch, "{not json")
    assert fcr._draft_release_input(str(tmp_path), "1.1.0") is None
    _fake_gh(monkeypatch, "", rc=1)
    assert fcr._draft_release_input(str(tmp_path), "1.1.0") is None
    _fake_gh(monkeypatch, json.dumps({"tag": "v1.1.0", "body": "no grammar"}))
    assert fcr._draft_release_input(str(tmp_path), "1.1.0") == {"programmes": [], "other_prs": 0}


def test_gh_merged_prs_pages_and_filters_by_date(tmp_path, monkeypatch):
    import json
    pages = [[{"number": i, "headRefName": "a/x", "mergedAt": "2026-11-0%dT10:00:00Z" % (2 + i % 3)}
              for i in range(500)],
             [{"number": 500 + i, "headRefName": "b/y", "mergedAt": "2026-11-01T0%d:00:00Z" % i} for i in range(3)]]
    queue = [subprocess.CompletedProcess([], 0, "main\n", "")] + [
        subprocess.CompletedProcess([], 0, json.dumps(p), "") for p in pages]
    monkeypatch.setattr(fcr.subprocess, "run", lambda *a, **k: queue.pop(0))
    out = fcr._gh_merged_prs(str(tmp_path), "2026-11-01T01:30:00+00:00")
    nums = {p["number"] for p in out}
    assert len(out) == 501 and 500 not in nums and 501 not in nums and 502 in nums


def test_release_path_never_tags_or_pushes(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    before = subprocess.run(["git", "-C", str(root), "tag"], capture_output=True, text=True).stdout
    seen = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        seen.append(list(cmd))
        return real(cmd, *a, **k)
    monkeypatch.setattr(fcr.subprocess, "run", spy)
    monkeypatch.setattr(fcr, "preflight", lambda tree, version, suite_runner=None: [("stub", True, "ok")])
    monkeypatch.setattr(fcr, "publish_plan", lambda tree, version: ["git tag v1.1.0", "git push origin v1.1.0"])
    monkeypatch.setattr(fcr, "read_versions", lambda tree: {"repo": None})
    r = fcr.cut_release(str(root), "1.1.0", acceptance_fn=lambda tree: {"verdict": "pass"},
                        er_state_fn=lambda ers, repo=None: {})
    assert r["state"] == "ready"
    after = real(["git", "-C", str(root), "tag"], capture_output=True, text=True).stdout
    assert before == after                                              # no tag was created
    assert not [c for c in seen if c[0] == "git" and ("push" in c or "tag" in c)]   # nothing ran tag/push
    assert not [c for c in seen if c[0] == "gh"]
