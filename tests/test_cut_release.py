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


def test_release_notes_list_two_programmes_and_count_other_prs(tmp_path):
    root = _repo(tmp_path)
    _git(root, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    _merge(root, 11, "alpha/a-1")
    _merge(root, 12, "alpha/a-2")
    _merge(root, 13, "beta/b-1")
    _merge(root, 14, "adhoc-fix")
    _merge(root, 15, "other-fix")
    n = fcr.release_notes(str(root), "1.1.0", draft_fn=lambda t: None)
    assert n["source"] == "git history" and n["previous_tag"] == "v1.0.0"
    assert [(p["id"], p["prs"]) for p in n["programmes"]] == [("alpha", [11, 12]), ("beta", [13])]
    assert n["other_prs"] == 2
    assert n["lines"] == ["programme alpha (2 PR(s))", "programme beta (1 PR(s))", "2 other PR(s) since v1.0.0"]


def test_release_notes_prefer_the_closeout_draft_release(tmp_path):
    root = _repo(tmp_path)
    _merge(root, 21, "ignored/x")
    n = fcr.release_notes(str(root), "1.1.0",
                          draft_fn=lambda t: {"programmes": ["p-one", "p-two"], "other_prs": 3})
    assert n["source"] == "close-out draft release"
    assert [p["id"] for p in n["programmes"]] == ["p-one", "p-two"] and n["other_prs"] == 3


def test_cut_never_tags_or_pushes():
    src = SCRIPT.read_text()
    assert "release_notes(tree, version" in src
    assert '"git", "tag"' not in src.split("def _selftest")[0] and '"push"' not in src.split("def _selftest")[0]
