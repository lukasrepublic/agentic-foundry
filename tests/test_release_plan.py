"""dl-04: scripts/foundry_wave_plan.py + scripts/foundry_release_plan.py (offline, stubbed GitHub)."""
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_release_plan as ep  # noqa: E402
import foundry_wave_plan as wp  # noqa: E402

SCHEMA = json.loads((ROOT / "schema" / "release-plan.schema.json").read_text())


def atom(i, blocked_by=(), paths=(), **kw):
    d = {"id": i, "node_id": f"N_{i}", "item_id": f"I_{i}", "blocked_by": list(blocked_by),
         "paths": list(paths), "labels": [], "state": "open"}
    d.update(kw)
    return d


def cycle_members(msg):
    """The atom list after the marker, parsed (never a substring probe)."""
    marker = "dependency cycle among atoms: "
    assert marker in msg
    return msg.split(marker, 1)[1].splitlines()[0].split(", ")


def corpus(*atoms, **kw):
    c = {"atoms": list(atoms)}
    c.update(kw)
    return c


DIAMOND = corpus(
    atom("A", paths=["a/**"]),
    atom("B", ["A"], ["b/**"], labels=["size:l"]),
    atom("C", ["A"], ["c/**"]),
    atom("D", ["B", "C"], ["d/**"]),
    atom("E", paths=["a/x.py"]),          # overlaps A -> later increment
    atom("F", paths=["f/**"]),
)


# ---- increment planner -----------------------------------------------------------------------------
def test_waves_follow_dependencies_and_split_path_overlap():
    increments = wp.plan_waves([{"id": a["id"], "blocked_by": a["blocked_by"], "paths": a["paths"]}
                           for a in DIAMOND["atoms"]])
    assert increments == [["A", "F"], ["B", "C", "E"], ["D"]]


def test_cycle_refuses_and_names_every_atom_in_it_but_not_bystanders():
    atoms = [{"id": "A", "blocked_by": ["C"], "paths": []}, {"id": "B", "blocked_by": ["A"], "paths": []},
             {"id": "C", "blocked_by": ["B"], "paths": []}, {"id": "D", "blocked_by": ["C"], "paths": []},
             {"id": "Z", "blocked_by": [], "paths": []}]
    with pytest.raises(wp.WavePlanError) as e:
        wp.plan_waves(atoms)
    assert cycle_members(str(e.value)) == ["A", "B", "C"]      # exact: D is downstream, Z is a bystander


def test_self_loop_is_a_cycle():
    with pytest.raises(wp.WavePlanError):
        wp.plan_waves([{"id": "A", "blocked_by": ["A"], "paths": []}])


# ---- release plan ---------------------------------------------------------------------------
def test_same_corpus_gives_byte_identical_yaml():
    a = ep.render(ep.build_plan("prog", DIAMOND))
    b = ep.render(ep.build_plan("prog", json.loads(json.dumps(DIAMOND))))
    assert a == b and a.endswith("\n")


def test_no_two_batches_in_a_wave_share_an_edge_or_a_path():
    plan = ep.build_plan("prog", DIAMOND)
    blocked = {a["id"]: set(a["blocked_by"]) for a in DIAMOND["atoms"]}
    for w in plan["increments"]:
        bs = w["batches"]
        for i, x in enumerate(bs):
            for y in bs[i + 1:]:
                assert not any(blocked[p] & set(x["atoms"]) for p in y["atoms"])
                assert not any(blocked[p] & set(y["atoms"]) for p in x["atoms"])
                assert not any(wp.paths_overlap(p, q) for p in x["paths"] for q in y["paths"])


def test_plan_validates_and_records_critical_path_float_and_wip_limit():
    plan = ep.build_plan("prog", DIAMOND)
    jsonschema.validate(plan, SCHEMA)
    assert plan["epic"] == "prog"
    assert plan["increments"][0]["n"] == 1
    # weights: A=1, B=size:l (5), C=1, D=1 -> A,B,D
    by_atom = {b["atoms"][0]: b for w in plan["increments"] for b in w["batches"]}
    assert plan["critical_path"] == [by_atom["A"]["id"], by_atom["B"]["id"], by_atom["D"]["id"]]
    assert by_atom["C"]["total_float"] == 4
    assert by_atom["A"]["total_float"] == 0 and by_atom["B"]["total_float"] == 0
    assert plan["wip_limit"] == 2          # open increment 1 = {A, F}
    assert plan["generated_from"]["node_ids"] == sorted(f"N_{x}" for x in "ABCDEF")


def test_critical_path_by_atom_count_without_size_labels():
    c = corpus(atom("A"), atom("B", ["A"]), atom("C", ["B"]), atom("X"))
    plan = ep.build_plan("p", c)
    ids = {b["atoms"][0]: b["id"] for w in plan["increments"] for b in w["batches"]}
    assert plan["critical_path"] == [ids["A"], ids["B"], ids["C"]]
    assert {b["atoms"][0]: b["total_float"] for w in plan["increments"] for b in w["batches"]}["X"] == 2


def test_hold_label_keeps_the_label_and_leaves_the_open_increment():
    c = corpus(atom("A", labels=["hold"]), atom("B"), atom("C", ["A"]))
    plan = ep.build_plan("p", c)
    assert plan["wip_limit"] == 1
    assert plan["held"] == ["A"]
    assert c["atoms"][0]["labels"] == ["hold"]
    # a held atom still blocks its dependents
    assert [w["batches"][0]["atoms"] for w in plan["increments"]][-1] == ["C"]


def test_closed_blockers_do_not_block():
    c = corpus(atom("A", state="closed"), atom("B", ["A"]))
    plan = ep.build_plan("p", c)
    assert [b["atoms"] for w in plan["increments"] for b in w["batches"]] == [["B"]]


def test_shared_component_forms_one_batch():
    c = corpus(atom("A", paths=["x/a.py"], component="k"), atom("B", paths=["y/b.py"], component="k"),
               atom("C", paths=["z/c.py"]))
    plan = ep.build_plan("p", c)
    assert sorted(len(b["atoms"]) for b in plan["increments"][0]["batches"]) == [1, 2]


def test_cycle_in_corpus_refuses():
    c = corpus(atom("A", ["B"]), atom("B", ["A"]))
    with pytest.raises(wp.WavePlanError):
        ep.build_plan("p", c)


def test_sub_issue_order_of_an_atom_parent_is_an_edge():
    c = corpus(atom("P"), atom("S1"), atom("S2"), atom("S3"))
    c["atoms"][0]["sub_issues"] = ["S1", "S2", "S3"]
    plan = ep.build_plan("p", c)
    order = [b["atoms"] for w in plan["increments"] for b in w["batches"]]
    assert order.index(["S1"]) < order.index(["S2"]) < order.index(["S3"])


def test_design_components_line_gives_paths():
    body = "intro\n## Design\nWhy.\nComponents: `scripts/a.py`, tests/b.py\n## Other\nComponents: no.py\n"
    assert ep.components_from_body(body) == ["scripts/a.py", "tests/b.py"]
    assert ep.components_from_body("no design") == []


# ---- GitHub: one function, stubbed ------------------------------------------------------------
OVERRIDE = {}


@pytest.fixture(autouse=True)
def _reset_override():
    OVERRIDE.clear()
    yield
    OVERRIDE.clear()


def _fake_graphql(calls):
    def fake(query, variables=None):
        calls.append((query, variables))
        if "createProjectV2Field" in query:
            n = variables["name"]
            return {"createProjectV2Field": {"projectV2Field": {"id": f"F_{n}", "name": n}}}
        if "clearProjectV2ItemFieldValue" in query:
            return {"clearProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}
        if "updateProjectV2ItemFieldValue" in query:
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}
        conn = lambda nodes: {"totalCount": len(nodes), "pageInfo": {"hasNextPage": False}, "nodes": nodes}  # noqa: E731
        node = lambda num, body, blocked=(), labels=(), sub=(): {  # noqa: E731
            "id": f"N{num}", "number": num, "state": "OPEN", "body": body, "repository": {"nameWithOwner": "o/r"},
            "labels": conn([{"name": x} for x in labels]),
            "blockedBy": conn([{"id": b} for b in blocked]),
            "subIssues": conn([{"id": s} for s in sub])}
        extra = OVERRIDE.get("items", [])
        items = extra + [{"id": "PI1", "content": node(1, "## Design\nComponents: a/x.py\n")},
                 {"id": "PI2", "content": node(2, "## Design\nComponents: b/y.py\n", blocked=["N1"], labels=["hold"])},
                 {"id": "PI3", "content": {}}]
        return {"repositoryOwner": {"projectV2": {
            "id": "PVT1",
            "fields": conn(OVERRIDE.get("fields", [{"id": "F_title", "name": "Title", "dataType": "TEXT"}])),
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
    assert creates == ["increment", "batch"]          # absent -> created
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
    assert yaml.safe_load(_git(bare, "show", "plan/prog:release-plan.yaml")) == plan
    assert ep.publish_ref(work, "origin", "prog", ep.render(plan)) == sha1      # same plan, no new commit
    plan2 = ep.build_plan("prog", corpus(atom("A")))
    sha2 = ep.publish_ref(work, "origin", "prog", ep.render(plan2))
    assert sha2 != sha1 and _git(bare, "rev-parse", f"{sha2}^") == sha1


def test_bad_epic_name_refused(repos):
    work, _ = repos
    with pytest.raises(ep.PlanError):
        ep.publish_ref(work, "origin", "../x", "y: 1\n")


# ---- CLI --------------------------------------------------------------------------------------
def cli(*args, cwd=None, root=None):
    env = dict(__import__("os").environ)
    if root:
        env["CLAUDE_PROJECT_DIR"] = str(root)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "foundry_release_plan.py"), *args],
                          capture_output=True, text=True, timeout=60, cwd=cwd, env=env)


def test_cli_plan_from_json_prints_valid_yaml(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(DIAMOND))
    r = cli("plan", "prog", "--from-json", str(f), root=tmp_path)
    assert r.returncode == 0, r.stderr
    jsonschema.validate(yaml.safe_load(r.stdout), SCHEMA)


def test_cli_cycle_exits_2_naming_atoms(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(corpus(atom("A", ["B"]), atom("B", ["A"]))))
    r = cli("plan", "prog", "--from-json", str(f), root=tmp_path)
    assert r.returncode == 2 and r.stderr.count("refused:") == 1
    assert cycle_members(r.stderr) == ["A", "B"]


def test_cli_publish_dry_run_writes_nothing(repos, tmp_path):
    work, bare = repos
    f = tmp_path / "c.json"
    f.write_text(json.dumps(DIAMOND))
    r = cli("publish", "prog", "--from-json", str(f), "--dry-run", cwd=work, root=tmp_path)
    assert r.returncode == 0 and "epic: prog" in r.stdout
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "-q", "refs/heads/plan/prog"],
                          capture_output=True).returncode != 0


def test_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


# ---- fix round: review findings ---------------------------------------------------------------
def test_hold_propagates_to_dependents_counter_example():
    # A held alone in increment 1; C blocked_by A in increment 2 -> nothing is startable, C in particular.
    plan = ep.build_plan("p", corpus(atom("A", labels=["hold"]), atom("C", ["A"])))
    assert plan["wip_limit"] == 0 and plan["open_increment"] is None and plan["held"] == ["A"]


def test_dependents_of_an_unmerged_batch_are_not_counted_in_wip_limit():
    plan = ep.build_plan("p", corpus(atom("A"), atom("B", ["A"]), atom("X")))
    assert plan["wip_limit"] == 2          # A and X; B waits for A


def test_overlap_forced_order_is_a_cpm_edge_so_float_is_not_optimistic():
    plan = ep.build_plan("prog", DIAMOND)
    by_atom = {b["atoms"][0]: b for w in plan["increments"] for b in w["batches"]}
    assert by_atom["E"]["total_float"] == 5      # E follows A (overlap): ES=1, LS=6; without the edge it read 6
    assert by_atom["F"]["total_float"] == 6


def test_batch_id_names_the_sorted_first_member():
    c = corpus(atom("B", paths=["y/b.py"], component="k"), atom("A", paths=["x/a.py"], component="k"))
    ids = [b["id"] for w in ep.build_plan("p", c)["increments"] for b in w["batches"]]
    assert ids == ["w1-A"]


@pytest.mark.parametrize("weird", ["", "/", ".", "*.py", "**", "../x", "a/../../x", "*/foo"])
def test_a_path_that_reaches_anywhere_overlaps_everything(weird):
    assert wp.paths_overlap(weird, "src/deep/file.py") and wp.paths_overlap("src/deep/file.py", weird)
    increments = wp.plan_waves([{"id": "A", "blocked_by": [], "paths": [weird]},
                           {"id": "B", "blocked_by": [], "paths": ["src/x.py"]}])
    assert increments == [["A"], ["B"]]


def test_ordinary_disjoint_paths_still_do_not_overlap():
    assert not wp.paths_overlap("src/a/**", "src/b/**") and not wp.paths_overlap("a/x.py", "a/y.py")


def test_gh_graphql_sends_a_real_json_body_with_typed_field_values(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input")
        return subprocess.CompletedProcess(argv, 0, '{"data": {"ok": {}}}', "")
    monkeypatch.setattr(ep.subprocess, "run", fake_run)
    ep.gh_graphql(ep._SET_Q, {"project": "P", "item": "I", "field": "F", "value": {"number": 3}})
    assert seen["argv"] == ["gh", "api", "graphql", "--input", "-"]
    body = json.loads(seen["input"])
    assert body["variables"] == {"project": "P", "item": "I", "field": "F", "value": {"number": 3}}
    assert body["query"] == ep._SET_Q
    ep.gh_graphql(ep._SET_Q, {"value": {"text": "w1-A"}})
    assert json.loads(seen["input"])["variables"]["value"] == {"text": "w1-A"}


@pytest.mark.parametrize("stdout,exc", [("not json", None), ('{"errors":[{"message":"x"}]}', None), ("[]", None)])
def test_gh_graphql_refuses_bad_replies(monkeypatch, stdout, exc):
    monkeypatch.setattr(ep.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout, ""))
    with pytest.raises(ep.PlanError):
        ep.gh_graphql("q")


def test_gh_graphql_timeout_is_a_plan_error(monkeypatch):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(ep.subprocess, "run", boom)
    with pytest.raises(ep.PlanError):
        ep.gh_graphql("q")


def test_main_turns_malformed_graphql_shapes_into_one_refused_line(monkeypatch, capsys):
    monkeypatch.setattr(ep, "gh_graphql", lambda q, v=None: {"repositoryOwner": {"projectV2": {"id": "P"}}})  # no fields
    assert ep.main(["plan", "prog", "--project", "o/1"]) == 2
    err = capsys.readouterr().err
    assert err.count("refused:") == 1 and err.count("\n") == 1


def test_truncated_connections_refuse(monkeypatch):
    for what in ("labels", "blockedBy", "subIssues"):
        calls = []
        fake = _fake_graphql(calls)

        def patched(q, v=None, fake=fake, what=what):
            d = fake(q, v)
            if "repositoryOwner" in d:
                d["repositoryOwner"]["projectV2"]["items"]["nodes"][0]["content"][what]["pageInfo"]["hasNextPage"] = True
            return d
        monkeypatch.setattr(ep, "gh_graphql", patched)
        with pytest.raises(ep.PlanError, match="truncated"):
            ep.fetch_corpus("o", 7)


def test_total_count_above_nodes_refuses(monkeypatch):
    fake = _fake_graphql([])

    def patched(q, v=None):
        d = fake(q, v)
        if "repositoryOwner" in d:
            d["repositoryOwner"]["projectV2"]["fields"]["totalCount"] = 99
        return d
    monkeypatch.setattr(ep, "gh_graphql", patched)
    with pytest.raises(ep.PlanError, match="truncated"):
        ep.fetch_corpus("o", 7)


def test_page_cap_and_repeated_cursor_refuse(monkeypatch):
    fake = _fake_graphql([])
    n = {"i": 0}

    def endless(q, v=None):
        d = fake(q, v)
        n["i"] += 1
        info = d["repositoryOwner"]["projectV2"]["items"]["pageInfo"]
        info.update(hasNextPage=True, endCursor=f"c{n['i']}")
        return d
    monkeypatch.setattr(ep, "gh_graphql", endless)
    monkeypatch.setattr(ep, "MAX_PAGES", 3)
    with pytest.raises(ep.PlanError, match="more than 3 pages"):
        ep.fetch_corpus("o", 7)
    assert n["i"] == 3

    def same(q, v=None):
        d = fake(q, v)
        d["repositoryOwner"]["projectV2"]["items"]["pageInfo"].update(hasNextPage=True, endCursor="same")
        return d
    monkeypatch.setattr(ep, "gh_graphql", same)
    with pytest.raises(ep.PlanError, match="repeated a cursor"):
        ep.fetch_corpus("o", 7)


def test_components_cap_refuses():
    body = "## Design\nComponents: " + ", ".join(f"p{i}.py" for i in range(ep.MAX_PATHS_PER_ATOM + 1)) + "\n"
    with pytest.raises(ep.PlanError):
        ep.components_from_body(body)
    ok = "## Design\nComponents: " + ", ".join(f"p{i}.py" for i in range(ep.MAX_PATHS_PER_ATOM)) + "\n"
    assert len(ep.components_from_body(ok)) == ep.MAX_PATHS_PER_ATOM


def test_wrong_typed_wave_or_batch_field_is_refused_not_used(monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "gh_graphql", _fake_graphql(calls))
    OVERRIDE["fields"] = [{"id": "F_w", "name": "increment", "dataType": "TEXT"}]
    c = ep.fetch_corpus("o", 7)
    with pytest.raises(ep.PlanError, match="increment"):
        ep.project(ep.build_plan("p", c), c)
    assert not any("updateProjectV2ItemFieldValue" in q for q, _ in calls)


def test_publish_clears_stale_wave_and_batch_on_closed_atoms(monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "gh_graphql", _fake_graphql(calls))
    OVERRIDE["fields"] = [{"id": "F_wave", "name": "increment", "dataType": "NUMBER"},
                          {"id": "F_batch", "name": "batch", "dataType": "TEXT"}]
    c = ep.fetch_corpus("o", 7)
    c["atoms"].append({"id": "o/r#9", "node_id": "N9", "item_id": "PI9", "blocked_by": [], "paths": [],
                       "labels": [], "state": "closed"})
    ep.project(ep.build_plan("p", c), c)
    cleared = [(v["item"], v["field"]) for q, v in calls if "clearProjectV2ItemFieldValue" in q]
    assert sorted(cleared) == [("PI9", "F_batch"), ("PI9", "F_wave")]
    assert not any("createProjectV2Field" in q for q, _ in calls)


def test_field_values_reach_the_mutation_as_objects(monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "gh_graphql", _fake_graphql(calls))
    c = ep.fetch_corpus("o", 7)
    ep.project(ep.build_plan("p", c), c)
    vals = [v["value"] for q, v in calls if "updateProjectV2ItemFieldValue" in q]
    assert all(isinstance(x, dict) and len(x) == 1 for x in vals)


def test_a_field_failure_leaves_the_planner_ref_unpublished(repos, tmp_path, monkeypatch):
    work, bare = repos
    calls = []
    ok = _fake_graphql(calls)

    def flaky(q, v=None):
        if "updateProjectV2ItemFieldValue" in q:
            raise ep.PlanError("boom")
        return ok(q, v)
    monkeypatch.setattr(ep, "gh_graphql", flaky)
    monkeypatch.chdir(work)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert ep.main(["publish", "prog", "--project", "o/7"]) == 2
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "-q", "refs/heads/plan/prog"],
                          capture_output=True).returncode != 0
    assert _git(work, "for-each-ref", "refs/foundry") == ""          # no leftover private ref


def test_failed_publish_removes_the_private_fetched_ref(repos):
    work, bare = repos
    ep.publish_ref(work, "origin", "prog", "a: 1\n")
    with pytest.raises(RuntimeError):
        ep.publish_ref(work, "origin", "prog", "a: 2\n", before_push=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert _git(work, "for-each-ref", "refs/foundry") == ""


def test_publish_refuses_a_ref_holding_foreign_paths(repos):
    work, bare = repos
    (work / "other.txt").write_text("x")
    _git(work, "add", "other.txt")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/plan/prog")
    before = _git(bare, "rev-parse", "refs/heads/plan/prog")
    with pytest.raises(ep.PlanError, match="other than"):
        ep.publish_ref(work, "origin", "prog", "a: 1\n")
    assert _git(bare, "rev-parse", "refs/heads/plan/prog") == before


def test_from_json_outside_the_project_root_is_refused(tmp_path):
    inside, outside = tmp_path / "proj", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    f = outside / "c.json"
    f.write_text(json.dumps(DIAMOND))
    r = cli("plan", "prog", "--from-json", str(f), root=inside)
    assert r.returncode == 2 and "outside the project root" in r.stderr


def test_publish_from_json_without_a_project_says_fields_were_skipped(repos, tmp_path):
    work, bare = repos
    f = tmp_path / "c.json"
    f.write_text(json.dumps(DIAMOND))
    r = cli("publish", "prog", "--from-json", str(f), cwd=work, root=tmp_path)
    assert r.returncode == 0 and "Project fields skipped (no --project)" in r.stdout


def test_publish_from_json_never_mutates_a_project_without_project_flag(repos, tmp_path, monkeypatch, capsys):
    work, bare = repos
    calls = []
    monkeypatch.setattr(ep, "gh_graphql", _fake_graphql(calls))
    c = ep.fetch_corpus("o", 7)            # carries project_id and item_ids
    calls.clear()
    f = tmp_path / "c.json"
    f.write_text(json.dumps(c))
    monkeypatch.chdir(work)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert ep.main(["publish", "prog", "--from-json", str(f)]) == 0
    assert calls == [] and "Project fields skipped (no --project)" in capsys.readouterr().out


def test_a_field_without_a_data_type_is_refused():
    c = {"project_id": "P", "fields": [{"id": "F", "name": "increment", "dataType": None}],
         "atoms": [atom("A")]}
    with pytest.raises(ep.PlanError, match="increment"):
        ep.project(ep.build_plan("p", c), c)
