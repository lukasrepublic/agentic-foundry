"""gn-05: native OTEL usage ingest, dispatch binding, attribution and coverage (offline, fixtures only)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import foundry_usage as fu

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "gn_usage")
REPO = os.path.dirname(HERE)
HOOK = os.path.join(REPO, "hooks", "foundry-usage-bind.py")
NOW = fu.parse_ts("2026-11-03T12:00:00Z")


def rj(path):
    return fu.read_jsonl(path)


@pytest.fixture
def ds(tmp_path):
    d = tmp_path / "dataset"
    d.mkdir()
    return str(d)


def _fresh(collector, now=NOW):
    t = now.timestamp() - 60
    for p in fu._export_files(collector):
        os.utime(p, (t, t))


def _basic(ds):
    col = os.path.join(FIX, "collector")
    out = fu.ingest(col, ds, now=NOW)
    return col, out


def test_ingest_one_record_per_request_id_and_dedupes(ds):
    _, out = _basic(ds)
    usage = rj(os.path.join(ds, "usage.jsonl"))
    ids = sorted(r["request_id"] for r in usage)
    assert ids == ["req-1", "req-2", "req-3", "req-secret"]
    assert out["duplicates_skipped"] == 1
    r1 = next(r for r in usage if r["request_id"] == "req-1")
    assert set(r1) == {"request_id", "at", "session_id", "agent_id", "parent_agent_id", "model", "provider",
                       "operator", "tokens", "cost_usd_estimate", "rate_basis", "source", "config"}
    assert r1["tokens"] == {"input": 100, "output": 50, "cache_read": 200, "cache_creation": 10}
    assert (r1["source"], r1["provider"], r1["operator"]) == ("otel", "anthropic", "alice")
    assert r1["cost_usd_estimate"] == 0.5 and r1["rate_basis"] == "native-cost_usd"
    assert r1["agent_id"] is None
    sub = next(r for r in usage if r["request_id"] == "req-2")
    assert (sub["agent_id"], sub["parent_agent_id"]) == ("sub-a", "lead")
    # re-ingest is idempotent
    fu.ingest(os.path.join(FIX, "collector"), ds, now=NOW)
    assert len(rj(os.path.join(ds, "usage.jsonl"))) == 4


def test_missing_token_field_goes_to_unknown_shape_without_inferring(ds):
    _basic(ds)
    unknown = rj(os.path.join(ds, "unknown_shape.jsonl"))
    assert len(unknown) == 1
    assert unknown[0]["request_id"] == "req-bad"
    assert unknown[0]["missing"] == ["cache_read_tokens"]
    assert "tokens" not in unknown[0]
    assert "req-bad" not in {r["request_id"] for r in rj(os.path.join(ds, "usage.jsonl"))}


def test_dataset_never_holds_prompt_or_credential_keys(ds):
    _basic(ds)
    forbidden = {"prompt", "tool_input", "tool_result", "authorization"}

    def walk(o):
        if isinstance(o, dict):
            assert not (set(o) & forbidden)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for name in ("usage.jsonl", "unknown_shape.jsonl"):
        for row in rj(os.path.join(ds, name)):
            walk(row)
            assert all(not isinstance(v, str) or "SECRET" not in v and "Bearer" not in v for v in row.values())
    secret = next(r for r in rj(os.path.join(ds, "usage.jsonl")) if r["request_id"] == "req-secret")
    assert secret["tokens"] == {"input": 1, "output": 1, "cache_read": 1, "cache_creation": 1}


def test_attribution_join_order(ds):
    _basic(ds)
    shutil.copy(os.path.join(FIX, "bindings.jsonl"), os.path.join(ds, "bindings.jsonl"))
    fu.attribute(ds)
    rows = {r["request_id"]: r for r in rj(os.path.join(ds, "attribution.jsonl"))}
    assert (rows["req-2"]["bucket"], rows["req-2"]["issue"], rows["req-2"]["basis"]) == ("outcome", "101", "session+agent")
    # lead event, sess-1 has one bound outcome -> that outcome
    assert (rows["req-1"]["bucket"], rows["req-1"]["issue"]) == ("outcome", "101")
    # lead event while two outcomes are live in the session
    assert (rows["req-3"]["bucket"], rows["req-3"]["issue"]) == ("lead-overhead", None)
    # no binding at all -> unallocated, operator kept
    fu.write_jsonl(os.path.join(ds, "bindings.jsonl"), [])
    fu.attribute(ds)
    rows = {r["request_id"]: r for r in rj(os.path.join(ds, "attribution.jsonl"))}
    assert {r["bucket"] for r in rows.values()} == {"unallocated"}
    assert rows["req-1"]["operator"] == "alice"


def test_unbound_subagent_in_multi_outcome_session_is_unallocated():
    usage = [{"request_id": "r", "at": "2026-11-02T10:00:00Z", "session_id": "s", "agent_id": "zz",
              "operator": "o", "tokens": {c: 1 for c in fu.CATEGORIES}}]
    b = [{"session_id": "s", "agent_id": "a1", "issue": "1", "at": "2026-11-02T09:00:00Z"},
         {"session_id": "s", "agent_id": "a2", "issue": "2", "at": "2026-11-02T09:00:00Z"}]
    row = fu.attribute_records(usage, b)[0]
    assert (row["bucket"], row["issue"]) == ("unallocated", None)


def test_worked_example_from_doc_03(ds):
    wx = os.path.join(FIX, "worked-example")
    fu.ingest(wx, ds, now=NOW)  # reads *.jsonl incl. bindings.jsonl, which carries no api_request events
    shutil.copy(os.path.join(wx, "bindings.jsonl"), os.path.join(ds, "bindings.jsonl"))
    since, until = fu.parse_ts("2026-10-28T00:00:00Z"), fu.parse_ts("2026-11-04T00:00:00Z")
    week = fu.attribute(ds, since, until)
    assert week["total"]["total"] == 840_000
    assert week["outcomes"]["A"]["tokens"]["total"] == 40_000
    assert week["outcomes"]["B"]["tokens"]["total"] == 200_000
    assert week["outcomes"]["C"]["tokens"]["total"] == 500_000
    assert week["lead_overhead"]["tokens"]["total"] == 100_000
    assert week["unallocated"]["tokens"]["total"] == 0
    life = fu.attribute(ds)
    assert life["outcomes"]["A"]["tokens"]["total"] == 100_000
    assert life["outcomes"]["A"]["by_operator"] == {"alice": 60_000, "bob": 40_000}
    accepted = [life["outcomes"][k]["tokens"]["total"] for k in ("A", "B")]
    assert sum(accepted) == 300_000 and sum(accepted) / len(accepted) == 150_000
    # categories stay separate and sum to the displayed total
    t = life["outcomes"]["B"]["tokens"]
    assert t["total"] == t["input"] + t["output"] + t["cache_read"] + t["cache_creation"]


def test_coverage_complete_then_incomplete_reasons(ds, tmp_path, monkeypatch):
    wx = os.path.join(FIX, "worked-example")
    work = tmp_path / "col"
    shutil.copytree(wx, work)
    (work / "bindings.jsonl").unlink()
    fu.ingest(str(work), ds, now=NOW)
    _fresh(str(work))
    shutil.copy(os.path.join(wx, "bindings.jsonl"), os.path.join(ds, "bindings.jsonl"))
    monkeypatch.setattr(fu, "_user_env", lambda home=None: {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp"})
    cov = fu.coverage(ds, operators=["alice", "bob", "carol"], now=NOW)
    assert cov["complete"] is True and cov["incomplete_reasons"] == []
    assert cov["observed"]["total"] == 900_000
    assert cov["attributed"]["total"] == 700_000 + 100_000
    assert cov["lead_overhead"]["total"] == 100_000
    assert cov["sessions"] == 5 and cov["agents"] == 4
    assert cov["earliest"] == "2026-10-20T10:00:00Z" and cov["latest"] == "2026-11-03T12:00:00Z"
    assert cov["export"]["export"] == "arriving"
    # a configured operator with zero records
    cov = fu.coverage(ds, operators=["alice", "dave"], now=NOW)
    assert cov["complete"] is False and cov["operators_without_records"] == ["dave"]
    # window excluding everything for alice
    cov = fu.coverage(ds, fu.parse_ts("2026-11-01T00:00:00Z"), None, operators=["alice"], now=NOW)
    assert cov["operators_without_records"] == ["alice"] and cov["complete"] is False


def test_coverage_with_unknown_shape_is_incomplete(ds):
    _basic(ds)
    cov = fu.coverage(ds, operators=["alice"], now=NOW)
    assert cov["complete"] is False
    assert cov["unknown_shape"]["records"] == 1
    assert cov["unknown_shape"]["missing_fields"] == ["cache_read_tokens"]
    assert cov["unknown_shape"]["tokens"] == "unknown"


def test_transcript_fallback_is_labelled_and_never_complete(ds):
    out = fu.fallback_transcripts(ds, "2.1.317", projects_dir=os.path.join(FIX, "transcripts"), now=NOW)
    assert out["records_new"] == 1 and out["duplicates_skipped"] == 1 and out["skipped_other_cli_version"] == 1
    rows = rj(os.path.join(ds, "usage.jsonl"))
    assert len(rows) == 1
    assert rows[0]["source"] == "transcript-fallback"
    assert rows[0]["config"] == {"cli_version": "2.1.317", "exporter": "transcript-jsonl"}
    assert rows[0]["tokens"] == {"input": 10, "output": 5, "cache_read": 2, "cache_creation": 1}
    assert "SECRET" not in json.dumps(rows)
    cov = fu.coverage(ds, now=NOW)
    assert cov["complete"] is False and cov["transcript_fallback_records"] == 1
    # an OTEL record for the same request id wins; the transcript line is skipped as a duplicate
    out2 = fu.fallback_transcripts(ds, "2.1.317", projects_dir=os.path.join(FIX, "transcripts"))
    assert out2["records_new"] == 0


def test_export_not_arriving_reports_env_keys(ds, tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    st = fu.export_status(ds, now=NOW, home=str(home), environ={})
    assert st["export"] == "not-arriving"
    assert "CLAUDE_CODE_ENABLE_TELEMETRY" in st["check_user_scope_env_keys"]
    assert len(st["reasons"]) == 2
    on_disk = json.load(open(os.path.join(ds, "export-status.json")))  # what gn-03 reads
    assert on_disk["export"] == "not-arriving" and on_disk["user_scope_env_keys"] == list(fu.EXPORT_ENV_KEYS)
    # configured + fresh -> arriving; stale file -> not arriving again
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp"}}))
    col = tmp_path / "col"
    shutil.copytree(os.path.join(FIX, "collector"), col)
    fu.ingest(str(col), ds, now=NOW)
    _fresh(str(col))
    assert fu.export_status(ds, now=NOW, home=str(home), environ={})["export"] == "arriving"
    later = fu.parse_ts("2026-11-05T12:00:00Z")
    st = fu.export_status(ds, now=later, home=str(home), environ={})
    assert st["export"] == "not-arriving" and st["reasons"] == ["no collector file written in the last 24h"]


def test_dataset_resolution_from_project_json(tmp_path):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"usage": {"dataset": "data/usage"}}))
    assert fu.dataset_dir(None, start=str(root)) == str(root / "data" / "usage")


def _hook(payload, cwd, env_extra=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    t = time.time()
    p = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, cwd=cwd, env=env)
    return p, time.time() - t


def test_subagent_start_hook_writes_one_binding_and_is_fast(tmp_path):
    root = tmp_path / "proj"
    gitdir = tmp_path / "gitdirs" / "wt"
    wt = root / "wt"
    (root / ".claude").mkdir(parents=True)
    gitdir.mkdir(parents=True)
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % gitdir)
    (gitdir / "foundry-assignment.json").write_text(json.dumps({"operator": "op_x", "issue": 42}))
    ds = tmp_path / "ds"
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"usage": {"dataset": str(ds)}}))
    payload = json.dumps({"session_id": "s1", "agent_id": "a1", "agent_type": "implementer", "cwd": str(wt)})
    p, dt = _hook(payload, str(wt))
    assert p.returncode == 0 and dt < 1.0
    rows = rj(str(ds / "bindings.jsonl"))
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("session_id", "agent_id", "operator", "issue", "worktree")} == {
        "session_id": "s1", "agent_id": "a1", "operator": "op_x", "issue": "42", "worktree": str(wt)}
    assert "at" in rows[0]
    # assignment absent: tolerated, still one line, no issue
    os.remove(gitdir / "foundry-assignment.json")
    p, _ = _hook(payload, str(wt))
    assert p.returncode == 0
    rows = rj(str(ds / "bindings.jsonl"))
    assert len(rows) == 2 and rows[1]["issue"] is None and rows[1]["operator"] is None


@pytest.mark.parametrize("stdin", ["", "not json", "[]", json.dumps({"cwd": "/nonexistent"})])
def test_hook_is_fail_open(stdin, tmp_path):
    p, _ = _hook(stdin, str(tmp_path))
    assert p.returncode == 0 and p.stderr == ""


def test_hooks_json_wires_subagent_start_to_an_executable_script():
    h = json.load(open(os.path.join(REPO, "hooks", "hooks.json")))["hooks"]["SubagentStart"]
    cmd = h[0]["hooks"][0]["command"]
    assert cmd.endswith("/hooks/foundry-usage-bind.py")
    assert os.access(HOOK, os.X_OK)


def test_collector_config_and_env_example_shape():
    cfg = open(os.path.join(REPO, "context", "otelcol-usage.yaml")).read()
    assert "# version: PINNED IN docs/standing-versions.md (workspace) — research timestamp 2026-11-03" in cfg
    assert "latest" not in cfg.replace("never `latest`", "")
    keys = {l.split("=", 1)[0] for l in open(os.path.join(REPO, "context", "usage-export.env.example"))
            if "=" in l and not l.startswith("#")}
    assert keys == set(fu.EXPORT_ENV_KEYS)


def test_cli_roundtrip(ds):
    def run(*a):
        p = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "foundry_usage.py"), "--dataset", ds, *a],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)
    assert run("ingest", os.path.join(FIX, "collector"))["records_total"] == 4
    run("bind", "--session", "sess-1", "--operator", "alice", "--issue", "101")
    summ = run("attribute")
    assert "101" in summ["outcomes"]
    cov = run("coverage", "--operator", "alice")
    assert cov["attributed_records"] >= 1 and cov["complete"] is False
