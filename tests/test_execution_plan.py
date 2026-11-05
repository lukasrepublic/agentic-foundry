"""dl-04: scripts/foundry_wave_plan.py + scripts/foundry_execution_plan.py (offline, stubbed GitHub)."""
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_execution_plan as ep  # noqa: E402
import foundry_wave_plan as wp  # noqa: E402

SCHEMA = json.loads((ROOT / "schema" / "execution-plan.schema.json").read_text())


def atom(i, blocked_by=(), paths=(), **kw):
    d = {"id": i, "node_id": f"N_{i}", "item_id": f"I_{i}", "blocked_by": list(blocked_by),
         "paths": list(paths), "labels": [], "state": "open"}
    d.update(kw)
    return d


def corpus(*atoms, **kw):
    c = {"atoms": list(atoms)}
    c.update(kw)
    return c


DIAMOND = corpus(
    atom("A", paths=["a/**"]),
    atom("B", ["A"], ["b/**"], labels=["size:l"]),
    atom("C", ["A"], ["c/**"]),
    atom("D", ["B", "C"], ["d/**"]),
    atom("E", paths=["a/x.py"]),          # overlaps A -> later wave
    atom("F", paths=["f/**"]),
)


# ---- wave planner -----------------------------------------------------------------------------
def test_waves_follow_dependencies_and_split_path_overlap():
    waves = wp.plan_waves([{"id": a["id"], "blocked_by": a["blocked_by"], "paths": a["paths"]}
                           for a in DIAMOND["atoms"]])
    assert waves == [["A", "F"], ["B", "C", "E"], ["D"]]


def test_cycle_refuses_and_names_every_atom_in_it_but_not_bystanders():
    atoms = [{"id": "A", "blocked_by": ["C"], "paths": []}, {"id": "B", "blocked_by": ["A"], "paths": []},
             {"id": "C", "blocked_by": ["B"], "paths": []}, {"id": "D", "blocked_by": ["C"], "paths": []},
             {"id": "Z", "blocked_by": [], "paths": []}]
    with pytest.raises(wp.WavePlanError) as e:
        wp.plan_waves(atoms)
    msg = str(e.value)
    assert all(x in msg for x in ("A", "B", "C"))
    assert "D" not in msg.replace("dependency", "").replace("cycle", "")  # D is downstream, not in the cycle


def test_self_loop_is_a_cycle():
    with pytest.raises(wp.WavePlanError):
        wp.plan_waves([{"id": "A", "blocked_by": ["A"], "paths": []}])


# ---- execution plan ---------------------------------------------------------------------------
def test_same_corpus_gives_byte_identical_yaml():
    a = ep.render(ep.build_plan("prog", DIAMOND))
    b = ep.render(ep.build_plan("prog", json.loads(json.dumps(DIAMOND))))
    assert a == b and a.endswith("\n")


def test_no_two_batches_in_a_wave_share_an_edge_or_a_path():
    plan = ep.build_plan("prog", DIAMOND)
    blocked = {a["id"]: set(a["blocked_by"]) for a in DIAMOND["atoms"]}
    for w in plan["waves"]:
        bs = w["batches"]
        for i, x in enumerate(bs):
            for y in bs[i + 1:]:
                assert not any(blocked[p] & set(x["atoms"]) for p in y["atoms"])
                assert not any(blocked[p] & set(y["atoms"]) for p in x["atoms"])
                assert not any(wp.paths_overlap(p, q) for p in x["paths"] for q in y["paths"])


def test_plan_validates_and_records_critical_path_float_and_max_concurrent():
    plan = ep.build_plan("prog", DIAMOND)
    jsonschema.validate(plan, SCHEMA)
    assert plan["programme"] == "prog"
    assert plan["waves"][0]["n"] == 1
    # weights: A=1, B=size:l (5), C=1, D=1 -> A,B,D
    by_atom = {b["atoms"][0]: b for w in plan["waves"] for b in w["batches"]}
    assert plan["critical_path"] == [by_atom["A"]["id"], by_atom["B"]["id"], by_atom["D"]["id"]]
    assert by_atom["C"]["total_float"] == 4
    assert by_atom["A"]["total_float"] == 0 and by_atom["B"]["total_float"] == 0
    assert plan["max_concurrent"] == 2          # open wave 1 = {A, F}
    assert plan["generated_from"]["node_ids"] == sorted(f"N_{x}" for x in "ABCDEF")


def test_critical_path_by_atom_count_without_size_labels():
    c = corpus(atom("A"), atom("B", ["A"]), atom("C", ["B"]), atom("X"))
    plan = ep.build_plan("p", c)
    ids = {b["atoms"][0]: b["id"] for w in plan["waves"] for b in w["batches"]}
    assert plan["critical_path"] == [ids["A"], ids["B"], ids["C"]]
    assert {b["atoms"][0]: b["total_float"] for w in plan["waves"] for b in w["batches"]}["X"] == 2


def test_hold_label_keeps_the_label_and_leaves_the_open_wave():
    c = corpus(atom("A", labels=["hold"]), atom("B"), atom("C", ["A"]))
    plan = ep.build_plan("p", c)
    assert plan["max_concurrent"] == 1
    assert plan["held"] == ["A"]
    assert c["atoms"][0]["labels"] == ["hold"]
    # a held atom still blocks its dependents
    assert [w["batches"][0]["atoms"] for w in plan["waves"]][-1] == ["C"]


def test_closed_blockers_do_not_block():
    c = corpus(atom("A", state="closed"), atom("B", ["A"]))
    plan = ep.build_plan("p", c)
    assert [b["atoms"] for w in plan["waves"] for b in w["batches"]] == [["B"]]


def test_shared_component_forms_one_batch():
    c = corpus(atom("A", paths=["x/a.py"], component="k"), atom("B", paths=["y/b.py"], component="k"),
               atom("C", paths=["z/c.py"]))
    plan = ep.build_plan("p", c)
    assert sorted(len(b["atoms"]) for b in plan["waves"][0]["batches"]) == [1, 2]


def test_cycle_in_corpus_refuses():
    c = corpus(atom("A", ["B"]), atom("B", ["A"]))
    with pytest.raises(wp.WavePlanError):
        ep.build_plan("p", c)


def test_sub_issue_order_of_an_atom_parent_is_an_edge():
    c = corpus(atom("P"), atom("S1"), atom("S2"), atom("S3"))
    c["atoms"][0]["sub_issues"] = ["S1", "S2", "S3"]
    plan = ep.build_plan("p", c)
    order = [b["atoms"] for w in plan["waves"] for b in w["batches"]]
    assert order.index(["S1"]) < order.index(["S2"]) < order.index(["S3"])


def test_design_components_line_gives_paths():
    body = "intro\n## Design\nWhy.\nComponents: `scripts/a.py`, tests/b.py\n## Other\nComponents: no.py\n"
    assert ep.components_from_body(body) == ["scripts/a.py", "tests/b.py"]
    assert ep.components_from_body("no design") == []


# ---- GitHub: one function, stubbed ------------------------------------------------------------
def _fake_graphql(calls):
    def fake(query, variables=None):
        calls.append((query, variables))
        if "createProjectV2Field" in query:
            n = variables["name"]
            return {"createProjectV2Field": {"projectV2Field": {"id": f"F_{n}", "name": n}}}
        if "updateProjectV2ItemFieldValue" in query:
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}
        node = lambda num, body, blocked=(), labels=(), sub=(): {  # noqa: E731
            "id": f"N{num}", "number": num, "state": "OPEN", "body": body, "repository": {"nameWithOwner": "o/r"},
            "labels": {"nodes": [{"name": x} for x in labels]},
            "blockedBy": {"nodes": [{"id": b} for b in blocked]},
            "subIssues": {"nodes": [{"id": s} for s in sub]}}
        items = [{"id": "PI1", "content": node(1, "## Design\nComponents: a/x.py\n")},
                 {"id": "PI2", "content": node(2, "## Design\nComponents: b/y.py\n", blocked=["N1"], labels=["hold"])},
                 {"id": "PI3", "content": {}}]
        return {"repositoryOwner": {"projectV2": {
            "id": "PVT1",
            "fields": {"nodes": [{"id": "F_title", "name": "Title", "dataType": "TEXT"}]},
            "items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": items}}}}
    return fake


def test_fetch_reads_live_issue_nodes_never_search(monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "gh_graphql", _fake_graphql(calls))
    c = ep.fetch_corpus("o", 7)
    assert [a["id"] for a in c["atoms"]] == ["o/r#1", "o/r#2"]
    assert c["atoms"][1]["blocked_by"] == ["o/r#1"] and "hold" in c["atoms"][1]["labels"]
    assert c["atoms"][0]["paths"] == ["a/x.py"] and c["project_id"] == "PVT1"
    assert all("search(" not in q for q, _ in calls)


def test_project_projection_sets_wave_number_and_batch_text(monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "gh_graphql", _fake_graphql(calls))
    c = ep.fetch_corpus("o", 7)
    plan = ep.build_plan("p", c)
    ep.project(plan, c)
    creates = [v["name"] for q, v in calls if "createProjectV2Field" in q]
    assert creates == ["wave", "batch"]          # absent -> created
    sets = [v for q, v in calls if "updateProjectV2ItemFieldValue" in q]
    assert {s["item"] for s in sets} == {"PI1", "PI2"} and len(sets) == 4
    assert {"number": 1} in [s["value"] for s in sets]
    assert any("text" in s["value"] for s in sets)


# ---- publish to the planner ref ---------------------------------------------------------------
def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    _git(work, "remote", "add", "origin", str(bare))
    return work, bare


def test_publish_commits_plan_to_planner_ref_and_is_idempotent(repos):
    work, bare = repos
    plan = ep.build_plan("prog", DIAMOND)
    sha1 = ep.publish_ref(work, "origin", "prog", ep.render(plan))
    assert _git(bare, "rev-parse", "refs/heads/plan/prog") == sha1
    assert yaml.safe_load(_git(bare, "show", "plan/prog:execution-plan.yaml")) == plan
    assert ep.publish_ref(work, "origin", "prog", ep.render(plan)) == sha1      # same plan, no new commit
    plan2 = ep.build_plan("prog", corpus(atom("A")))
    sha2 = ep.publish_ref(work, "origin", "prog", ep.render(plan2))
    assert sha2 != sha1 and _git(bare, "rev-parse", f"{sha2}^") == sha1


def test_bad_programme_name_refused(repos):
    work, _ = repos
    with pytest.raises(ep.PlanError):
        ep.publish_ref(work, "origin", "../x", "y: 1\n")


# ---- CLI --------------------------------------------------------------------------------------
def cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "foundry_execution_plan.py"), *args],
                          capture_output=True, text=True, timeout=60, cwd=cwd)


def test_cli_plan_from_json_prints_valid_yaml(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(DIAMOND))
    r = cli("plan", "prog", "--from-json", str(f))
    assert r.returncode == 0, r.stderr
    jsonschema.validate(yaml.safe_load(r.stdout), SCHEMA)


def test_cli_cycle_exits_2_naming_atoms(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(corpus(atom("A", ["B"]), atom("B", ["A"]))))
    r = cli("plan", "prog", "--from-json", str(f))
    assert r.returncode == 2 and "A" in r.stderr and "B" in r.stderr


def test_cli_publish_dry_run_writes_nothing(repos, tmp_path):
    work, bare = repos
    f = tmp_path / "c.json"
    f.write_text(json.dumps(DIAMOND))
    r = cli("publish", "prog", "--from-json", str(f), "--dry-run", cwd=work)
    assert r.returncode == 0 and "programme: prog" in r.stdout
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "-q", "refs/heads/plan/prog"],
                          capture_output=True).returncode != 0


def test_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
