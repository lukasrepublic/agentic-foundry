"""gv-10: scripts/foundry_merge_plan.py (operating-model rule A1) — cluster, ride an open PR, order."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "foundry_merge_plan.py"
sys.path.insert(0, str(SCRIPT.parent))
import foundry_merge_plan as mp  # noqa: E402


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


def test_two_items_sharing_a_catalogue_file_form_one_cluster_stated_in_one_line():
    r = cli("--ready", json.dumps(["a", "b", "c"]),
            "--paths", json.dumps({"a": ["hooks/hooks.json", "x.py"], "b": ["hooks/hooks.json", "y.py"], "c": ["z.py"]}))
    assert r.returncode == 0, r.stdout + r.stderr
    line, body = r.stdout.split("\n", 1)
    assert "\n" not in line and line.startswith("merge-plan: 3 ready -> 2 cluster(s)")
    assert "{a+b} share hooks/hooks.json" in line
    plan = json.loads(body)
    assert [c["items"] for c in plan["clusters"]] == [["a", "b"], ["c"]]
    assert plan["catalogue_once"] == {"hooks/hooks.json": 1}


def test_control_items_with_disjoint_files_stay_apart():
    p = mp.plan(["a", "b"], {"a": ["a.py"], "b": ["b.py"]})
    assert [c["items"] for c in p["clusters"]] == [["a"], ["b"]]
    assert p["catalogue_once"] == {}


def test_overlap_is_transitive_and_an_explicit_component_joins_items():
    p = mp.plan([{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d", "component": "billing"}, {"id": "e", "component": "billing"}],
                {"a": ["1"], "b": ["1", "2"], "c": ["2"], "d": ["d"], "e": ["e"]})
    assert sorted(c["items"] for c in p["clusters"]) == [["a", "b", "c"], ["d", "e"]]


def test_work_for_an_area_joins_that_areas_open_unqueued_pr():
    p = mp.plan(["a"], {"a": ["svc/x.py"]}, [{"number": 12, "files": ["svc/x.py"], "queued": False}])
    c = p["clusters"][0]
    assert (c["action"], c["pr"], c["waits_for"]) == ("add-to-pr", 12, None)
    assert "adds to open PR #12" in mp.report_line(p)


def test_a_queued_pr_is_never_the_ride_it_is_waited_for():
    p = mp.plan(["a"], {"a": ["svc/x.py"]}, [{"number": 14, "files": ["svc/x.py"], "queued": True}])
    c = p["clusters"][0]
    assert (c["action"], c["pr"], c["waits_for"]) == ("wait-for-queued", None, 14)
    # control: an unqueued PR on the same files wins over the queued one
    p2 = mp.plan(["a"], {"a": ["svc/x.py"]}, [{"number": 14, "files": ["svc/x.py"], "queued": True},
                                              {"number": 15, "files": ["svc/x.py"], "queued": False}])
    assert p2["clusters"][0]["pr"] == 15


def test_order_is_largest_first_then_catalogue_first():
    p = mp.plan(["a", "b", "c", "d"], {"a": ["1"], "b": ["1"], "c": ["hooks/hooks.json"], "d": ["d.py"]})
    assert [c["items"] for c in p["clusters"]] == [["a", "b"], ["c"], ["d"]]


def test_an_item_without_paths_is_flagged_not_guessed():
    p = mp.plan(["a", "b"], {"a": ["a.py"]})
    assert p["unmapped"] == ["b"] and "NO PATHS for b" in mp.report_line(p)


@pytest.mark.parametrize("args", [
    ["--ready", "{", "--paths", "{}"],
    ["--ready", '["a","a"]', "--paths", "{}"],
    ["--ready", '["a"]', "--paths", "[]"],
    ["--ready", "no-such-file.json", "--paths", "{}"],
])
def test_bad_input_is_refused_on_one_line(args):
    r = cli(*args)
    assert r.returncode == 2 and r.stdout.startswith("refused: ") and r.stdout.count("\n") == 1


def test_inputs_may_be_files(tmp_path):
    (tmp_path / "r.json").write_text('["a"]')
    (tmp_path / "p.json").write_text('{"a": ["x"]}')
    assert cli("--ready", str(tmp_path / "r.json"), "--paths", str(tmp_path / "p.json")).returncode == 0
