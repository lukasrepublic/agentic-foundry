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
    assert sum(accepted) == 100_000 + 200_000 and sum(accepted) / len(accepted) == 150_000
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
    # re-running the fallback adds nothing
    out2 = fu.fallback_transcripts(ds, "2.1.317", projects_dir=os.path.join(FIX, "transcripts"))
    assert out2["records_new"] == 0


def _otlp_line(rid, sid="tsess", at_nano="1793610000000000000"):
    def a(k, v):
        return {"key": k, "value": {"stringValue": v}}
    attrs = [a("event.name", "api_request"), a("request_id", rid), a("session.id", sid), a("model", "m")]
    attrs += [{"key": k, "value": {"intValue": "7"}} for k in
              ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")]
    res = [a("foundry.operator", "alice"), a("service.version", "2.1.317")]
    return json.dumps({"resourceLogs": [{"resource": {"attributes": res}, "scopeLogs": [{"logRecords": [
        {"timeUnixNano": at_nano, "attributes": attrs}]}]}]}) + "\n"


def test_otel_replaces_fallback_with_same_request_id_never_the_reverse(ds, tmp_path):
    tdir = os.path.join(FIX, "transcripts")
    col = tmp_path / "col"
    col.mkdir()
    (col / "a.jsonl").write_text(_otlp_line("treq-1"))
    # fallback first, then OTEL for the same id: OTEL replaces it
    fu.fallback_transcripts(ds, "2.1.317", projects_dir=tdir, now=NOW)
    assert rj(os.path.join(ds, "usage.jsonl"))[0]["source"] == "transcript-fallback"
    out = fu.ingest(str(col), ds, now=NOW)
    rows = rj(os.path.join(ds, "usage.jsonl"))
    assert out["fallback_replaced"] == 1 and len(rows) == 1 and rows[0]["source"] == "otel"
    # OTEL first, then the fallback: the fallback line is a duplicate and changes nothing
    out2 = fu.fallback_transcripts(ds, "2.1.317", projects_dir=tdir, now=NOW)
    assert out2["records_new"] == 0 and out2["duplicates_skipped"] == 2
    assert [r["source"] for r in rj(os.path.join(ds, "usage.jsonl"))] == ["otel"]


def test_coverage_reports_fallback_overlap_unverified_when_both_sources_contribute(ds, tmp_path):
    col = tmp_path / "col"
    col.mkdir()
    (col / "a.jsonl").write_text(_otlp_line("other-id"))
    fu.ingest(str(col), ds, now=NOW)
    fu.fallback_transcripts(ds, "2.1.317", projects_dir=os.path.join(FIX, "transcripts"), now=NOW)
    assert "fallback-overlap-unverified" in fu.coverage(ds, now=NOW)["incomplete_reasons"]


def test_coverage_named_reasons_for_empty_window_export_null_operator_and_lineage(ds, tmp_path):
    cov = fu.coverage(ds, now=NOW)
    assert cov["complete"] is False
    assert {"no-records-in-window", "export-not-arriving"} <= set(cov["incomplete_reasons"])
    # null operator
    fu.write_jsonl(os.path.join(ds, "usage.jsonl"), [{
        "request_id": "r", "at": "2026-11-02T10:00:00Z", "session_id": "s", "agent_id": None, "operator": None,
        "source": "otel", "config": {"cli_version": "2.1.100"}, "tokens": {c: 1 for c in fu.CATEGORIES}}])
    cov = fu.coverage(ds, now=NOW)
    assert "records-with-null-operator" in cov["incomplete_reasons"]
    assert any(r.startswith("agent-lineage-unavailable-cli-versions: 2.1.100") for r in cov["incomplete_reasons"])
    # a window that excludes the old-version record no longer carries the lineage reason
    cov = fu.coverage(ds, fu.parse_ts("2026-11-03T00:00:00Z"), None, now=NOW)
    assert not any(r.startswith("agent-lineage") for r in cov["incomplete_reasons"])


def test_session_join_is_time_guarded_and_stable_when_a_binding_is_added_later():
    def u(rid, at, aid=None):
        return {"request_id": rid, "at": at, "session_id": "s", "agent_id": aid, "operator": "o",
                "tokens": {c: 1 for c in fu.CATEGORIES}}
    usage = [u("early", "2026-11-02T08:00:00Z"), u("mid", "2026-11-02T10:00:00Z"), u("late", "2026-11-02T12:00:00Z")]
    b1 = [{"session_id": "s", "agent_id": None, "issue": "1", "at": "2026-11-02T09:00:00Z"}]
    before = {r["request_id"]: r for r in fu.attribute_records(usage, b1)}
    assert (before["early"]["bucket"], before["early"]["basis"]) == ("lead-overhead", "before-first-binding")
    assert (before["mid"]["bucket"], before["mid"]["issue"]) == ("outcome", "1")
    b2 = b1 + [{"session_id": "s", "agent_id": None, "issue": "2", "at": "2026-11-02T11:00:00Z"}]
    after = {r["request_id"]: r for r in fu.attribute_records(usage, b2)}
    assert after["early"] == before["early"] and after["mid"] == before["mid"]
    assert after["late"]["bucket"] == "lead-overhead"  # now two outcomes are live
    # no binding in the session at all stays unallocated
    assert fu.attribute_records(usage, [])[0]["bucket"] == "unallocated"


def test_as_count_never_crashes_on_odd_numerics():
    for bad in ("\u00b2", "\u0663", float("inf"), float("nan"), "9" * 5000, -1, True, 1.5):
        assert fu._as_count(bad) is None
    assert fu._as_count("12") == 12 and fu._as_count(3.0) == 3


def test_ingest_skips_rewrite_when_nothing_is_new(ds):
    col, _ = _basic(ds)
    path = os.path.join(ds, "usage.jsonl")
    old = (time.time() - 1000,) * 2
    os.utime(path, old)
    fu.ingest(col, ds, now=NOW)
    assert os.stat(path).st_mtime == old[0]


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
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"github_scope": {"owner": "example-org", "planning_repo": "planning"}, "usage": {"dataset": "data/usage"}}))
    assert fu.dataset_dir(None, start=str(root)) == str(root / "data" / "usage")


def _hook(payload, cwd, env_extra=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(env_extra or {})
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
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"github_scope": {"owner": "example-org", "planning_repo": "planning"}, "usage": {"dataset": str(ds)}}))
    payload = json.dumps({"session_id": "s1", "agent_id": "a1", "agent_type": "implementer", "cwd": str(wt)})
    p, dt = _hook(payload, str(wt), {"CLAUDE_PROJECT_DIR": str(root)})
    assert p.returncode == 0 and dt < 1.0
    rows = rj(str(ds / "bindings.jsonl"))
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("session_id", "agent_id", "operator", "issue", "worktree")} == {
        "session_id": "s1", "agent_id": "a1", "operator": "op_x", "issue": "42", "worktree": str(wt)}
    assert "at" in rows[0]
    # assignment absent: tolerated, still one line, no issue
    os.remove(gitdir / "foundry-assignment.json")
    p, _ = _hook(payload, str(wt), {"CLAUDE_PROJECT_DIR": str(root)})
    assert p.returncode == 0
    rows = rj(str(ds / "bindings.jsonl"))
    assert len(rows) == 2 and rows[1]["issue"] is None and rows[1]["operator"] is None


@pytest.mark.parametrize("stdin", ["", "not json", "[]", json.dumps({"cwd": "/nonexistent"})])
def test_hook_is_fail_open(stdin, tmp_path):
    p, _ = _hook(stdin, str(tmp_path))
    assert p.returncode == 0 and p.stderr == ""


def test_hook_is_fail_open_on_unwritable_dataset_and_corrupt_assignment(tmp_path):
    root = tmp_path / "proj"
    gitdir = tmp_path / "gitdirs" / "wt"
    wt = root / "wt"
    (root / ".claude").mkdir(parents=True)
    gitdir.mkdir(parents=True)
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % gitdir)
    (gitdir / "foundry-assignment.json").write_text("{not json")
    blocker = tmp_path / "afile"
    blocker.write_text("x")  # a dataset path under a regular file can never be created
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"github_scope": {"owner": "example-org", "planning_repo": "planning"}, "usage": {"dataset": str(blocker / "ds")}}))
    payload = json.dumps({"session_id": "s1", "agent_id": "a1", "cwd": str(wt)})
    p, _ = _hook(payload, str(wt), {"CLAUDE_PROJECT_DIR": str(root)})
    assert p.returncode == 0 and len(p.stderr.strip().splitlines()) <= 1
    # corrupt assignment with a writable dataset: still one binding, no issue
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"github_scope": {"owner": "example-org", "planning_repo": "planning"}, "usage": {"dataset": str(tmp_path / "ds")}}))
    p, _ = _hook(payload, str(wt), {"CLAUDE_PROJECT_DIR": str(root)})
    assert p.returncode == 0 and len(p.stderr.strip().splitlines()) <= 1
    rows = rj(str(tmp_path / "ds" / "bindings.jsonl"))
    assert len(rows) == 1 and rows[0]["issue"] is None


def test_hook_refuses_when_the_cwd_belongs_to_another_project_tree(tmp_path):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    ds, evil_ds = tmp_path / "ds", tmp_path / "evil-ds"
    team = {"github_scope": {"owner": "example-org", "planning_repo": "planning"}}
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({**team, "usage": {"dataset": str(ds)}}))
    foreign = root / "nested"           # a nested checkout with its OWN manifest
    (foreign / ".claude").mkdir(parents=True)
    (foreign / ".claude" / "foundry-project.json").write_text(json.dumps({**team, "usage": {"dataset": str(evil_ds)}}))
    payload = json.dumps({"session_id": "s1", "agent_id": "a1", "cwd": str(foreign)})
    p, _ = _hook(payload, str(foreign), {"CLAUDE_PROJECT_DIR": str(root)})
    assert p.returncode == 0 and not (evil_ds / "bindings.jsonl").exists() and not (ds / "bindings.jsonl").exists()
    # no workspace named at all: nothing is trusted, nothing is written
    p, _ = _hook(payload, str(foreign))
    assert p.returncode == 0 and not (evil_ds / "bindings.jsonl").exists()


def test_bindings_are_never_appended_through_a_symlink(tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n")
    (ds / "bindings.jsonl").symlink_to(victim)
    with pytest.raises(OSError):
        fu.bind(str(ds), "s1", issue=1)
    assert victim.read_text() == "keep\n"


def test_relative_dataset_escaping_the_workspace_by_symlink_is_not_resolved(tmp_path):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "data").symlink_to(outside)
    (root / ".claude" / "foundry-project.json").write_text(json.dumps({"usage": {"dataset": "data/usage"}}))
    assert fu.dataset_dir(None, start=str(root)) is None


def test_hooks_json_wires_subagent_start_to_an_executable_script():
    h = json.load(open(os.path.join(REPO, "hooks", "hooks.json")))["hooks"]["SubagentStart"]
    cmd = h[0]["hooks"][0]["command"]
    assert cmd.endswith("/hooks/foundry-usage-bind.py")
    assert os.access(HOOK, os.X_OK)


def test_collector_config_and_env_example_shape():
    cfg = open(os.path.join(REPO, "context", "otelcol-usage.yaml")).read()
    assert "# pinned: otelcol-contrib 0.164.0" in cfg
    assert "@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b" in cfg
    assert "latest" not in cfg.replace("do not float :latest", "")
    assert "UNCERTAIN" not in cfg and "append:" not in cfg.replace("`append: true`", "")
    keys = {l.split("=", 1)[0] for l in open(os.path.join(REPO, "context", "usage-export.env.example"))
            if "=" in l and not l.startswith("#")}
    assert keys == set(fu.EXPORT_ENV_KEYS)


def test_cli_roundtrip(ds, tmp_path):
    proj = tmp_path / "teamproj"   # the CLI needs github_scope
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "foundry-project.json").write_text('{"github_scope": {"owner": "example-org", "planning_repo": "planning"}}')

    def run(*a):
        p = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "foundry_usage.py"), "--dataset", ds, *a],
                           capture_output=True, text=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(proj)))
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)
    assert run("ingest", os.path.join(FIX, "collector"))["records_total"] == 4
    run("bind", "--session", "sess-1", "--operator", "alice", "--issue", "101", "--at", "2026-11-01T00:00:00Z")
    summ = run("attribute")
    assert "101" in summ["outcomes"]
    cov = run("coverage", "--operator", "alice")
    assert cov["attributed_records"] >= 1 and cov["complete"] is False
