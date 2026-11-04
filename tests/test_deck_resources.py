"""gv-12: deck-resources verdict (B2, F2). Every reading is injected from tests/fixtures/resources/;
nothing real is measured except the live-smoke test, which only reads."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

dr = load_module("scripts/foundry_deck_resources.py", "foundry_deck_resources")
FIX = os.path.join(REPO_ROOT, "tests", "fixtures", "resources")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry_deck_resources.py")


def fx(name):
    return os.path.join(FIX, name)


def run(name, **kw):
    return dr.evaluate(from_json=fx(name), project_dir=kw.pop("project_dir", None), **kw)


def test_ac_om_6_low_memory_is_red_with_control():
    red = run("low-memory.json")
    green = run("healthy.json")
    assert red["verdict"] == "RED"
    assert green["verdict"] == "GREEN"
    assert any("memory" in r for r in red["reasons"])
    assert green["reasons"] == []


def test_amber_reduces_max_builders_by_the_step():
    green = run("healthy.json", policy={"max_builders": 4, "heavy_slots": "auto"})
    amber = run("amber-load.json", policy={"max_builders": 4, "heavy_slots": "auto"})
    red = run("low-memory.json", policy={"max_builders": 4, "heavy_slots": "auto"})
    assert amber["verdict"] == "AMBER"
    assert green["max_builders_effective"] == 4
    assert amber["max_builders_effective"] == 3
    assert red["max_builders_effective"] == 1


def test_effective_is_never_above_policy_and_never_below_one():
    amber = run("amber-load.json", policy={"max_builders": 1, "heavy_slots": "auto"})
    assert amber["max_builders_effective"] == 1
    green = run("healthy.json", policy={"max_builders": 2, "heavy_slots": "auto"})
    assert green["max_builders_effective"] == 2


def test_swap_over_half_is_red():
    r = run("swap-heavy.json")
    assert r["verdict"] == "RED"
    assert any("swap" in x for x in r["reasons"])


def test_work_mount_is_measured_not_only_root():
    r = run("full-work-mount.json")
    assert r["verdict"] == "RED"          # 2 % free on the mount the work writes to
    assert any(x.startswith("disk(path)") for x in r["reasons"])
    assert not any(x.startswith("disk(root)") for x in r["reasons"])  # root is fine: the control


def test_heavy_slots_auto_and_int():
    assert run("healthy.json", policy={"max_builders": 4, "heavy_slots": "auto"})["heavy_slots"] == 4
    assert run("small-host.json", policy={"max_builders": 4, "heavy_slots": "auto"})["heavy_slots"] == 2
    assert run("healthy.json", policy={"max_builders": 4, "heavy_slots": 3})["heavy_slots"] == 3
    assert dr.auto_heavy_slots(cpus=1, mem_total_mb=512) == 1


def test_thresholds_are_overridable_from_policy():
    r = dr.evaluate(from_json=fx("amber-load.json"), policy={"max_builders": 4, "heavy_slots": "auto"},
                    thresholds={"load_amber": 2.0, "load_red": 0.5})
    assert r["verdict"] == "RED"   # 6.5 load on 8 cpus is over 0.5 x cpus: thresholds really drive it


def test_oom_recent_old_and_unknown():
    assert run("oom-recent.json")["oom_recent"] is True
    assert run("oom-old.json")["oom_recent"] is False
    assert run("healthy.json")["oom_recent"] == "unknown"     # no log injected: never required


def test_oom_recent_cuts_fanout_like_amber_but_does_not_turn_red():
    r = run("oom-recent.json", policy={"max_builders": 4, "heavy_slots": "auto"})
    assert r["verdict"] == "GREEN"
    assert r["max_builders_effective"] == 3
    assert any("oom" in x for x in r["reasons"])


def test_oom_baseline_reset_clears_a_self_caused_kill(tmp_path):
    r1 = run("oom-recent.json", project_dir=str(tmp_path), reset_oom_baseline=True)
    assert r1["oom_baseline"]["action"] == "reset"
    assert "reset" in r1["oom_baseline"]["meaning"]
    r2 = run("oom-recent.json", project_dir=str(tmp_path),
             policy={"max_builders": 4, "heavy_slots": "auto"})
    assert r2["oom_recent"] is False
    assert r2["max_builders_effective"] == 4
    assert (tmp_path / ".foundry" / "deck-resources-oom-baseline.json").is_file()


def test_cli_prints_json_and_exit_code_is_zero(tmp_path):
    out = subprocess.run([sys.executable, SCRIPT, "--from-json", fx("low-memory.json"), "--path", str(tmp_path)],
                         capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["verdict"] == "RED"
    for k in ("verdict", "readings", "heavy_slots", "max_builders_effective", "oom_recent"):
        assert k in d


def test_live_measurement_reads_only(tmp_path):
    d = dr.evaluate(path=str(tmp_path), project_dir=str(tmp_path))
    assert d["verdict"] in ("GREEN", "AMBER", "RED")
    assert d["readings"]["cpus"] >= 1
    assert d["readings"]["disk_path"]["total_mb"] > 0
    assert d["heavy_slots"] >= 1
