"""tests/test_delivery_metrics.py — scripts/foundry-delivery-metrics.py (ticket-first default, #269).

Hermetic: two throwaway git repos under tmp_path (one nested in the other), driven in-process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from conftest import load_module

dm = load_module("scripts/foundry-delivery-metrics.py", "foundry_delivery_metrics")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _write(root, rel, n):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"line {i}\n" for i in range(n)))


@pytest.fixture
def ws(tmp_path):
    outer, inner = tmp_path / "ws", tmp_path / "ws" / "svc"
    for r in (outer, inner):
        r.mkdir(parents=True, exist_ok=True)
        _git(r, "init", "-q", "-b", "main")
    _write(outer, "specs/feat-a.md", 10)
    _write(outer, "src/app.py", 20)
    _write(outer, "infra/main.tf", 5)
    _write(outer, "tests/test_x.py", 3)
    _write(outer, "data/dump.json", 3000)
    _write(outer, "package-lock.json", 500)
    _git(outer, "add", "-A", "--", ".", ":!svc")
    _git(outer, "commit", "-qm", "work (#1)")
    _write(inner, "README.md", 4)
    _write(inner, "main.go", 8)
    _git(inner, "add", "-A")
    _git(inner, "commit", "-qm", "init")
    return outer


def _run(args, capsys):
    assert dm.main(args) == 0
    return capsys.readouterr().out


def test_counts_ratio_discovery_and_dump_exclusion(ws, capsys):
    rows = json.loads(_run(["--repo", str(ws), "--json", "--since", "1d"], capsys))
    by = {r["repo"]: r for r in rows}
    assert set(by) == {"ws", "svc", "TOTAL"}
    o = by["ws"]
    assert (o["paper"], o["code"], o["infra"], o["tests"]) == (10, 20, 5, 3)  # dump + lockfile excluded
    assert o["paper_ratio"] == round(10 / 28, 2) and o["prs"] == 1
    assert (by["svc"]["paper"], by["svc"]["code"]) == (4, 8)
    assert by["TOTAL"]["paper"] == 14 and by["TOTAL"]["code"] == 28


def test_json_shape_and_text_line(ws, capsys):
    row = json.loads(_run(["--repo", str(ws), "--json"], capsys))[0]
    assert {"repo", "paper", "code", "infra", "tests", "plumbing", "other", "paper_ratio", "prs",
            "lead_time_h", "frustration_turn_rate", "silent_yield_rate"} <= set(row)
    out = _run(["--repo", str(ws)], capsys).splitlines()
    assert len(out) == 3 and "paper:code 0.36 (paper 10 / code 20 / infra 5 / tests 3) PRs 1" in out[0]
    assert out[0].endswith("frustration n/a silent_yield n/a") and out[-1].split()[2] == "TOTAL"


def test_missing_gh_is_na(ws, capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda *_: None)
    assert json.loads(_run(["--repo", str(ws), "--json"], capsys))[0]["lead_time_h"] is None
    assert "lead_time_h n/a" in _run(["--repo", str(ws)], capsys)


def test_classify_and_zero_denominator():
    assert dm.classify("CLAUDE.md") == "plumbing" and dm.classify("docs/x.py") == "paper"
    assert dm.classify("k8s/app.yaml") == "infra" and dm.classify("a/node_modules/x.js") is None
    assert dm.classify("web/__tests__/a.ts") == "tests" and dm.classify("x.bin") == "other"


def test_transcripts_rates(ws, tmp_path, capsys):
    ts = "2099-01-01T00:00:00Z"
    recs = [
        {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": "Done."}]}},
        {"type": "user", "timestamp": ts, "message": {"content": "why is this broken again"}},
        {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": "Want me to retry?"}]}},
        {"type": "user", "timestamp": ts, "message": {"content": "yes"}},
    ]
    d = tmp_path / "tr"
    d.mkdir()
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    row = json.loads(_run(["--repo", str(ws), "--json", "--transcripts", str(d), "--since", "1d"], capsys))[-1]
    assert row["frustration_turn_rate"] == 0.5 and row["silent_yield_rate"] == 0.5


def test_bad_since_is_argument_error():
    with pytest.raises(SystemExit) as e:
        dm.main(["--since", "soon"])
    assert e.value.code == 2
