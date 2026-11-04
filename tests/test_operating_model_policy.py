"""gv-03: the operating-model policy file -- schema, loader, validator, renderer, doctor row.

AC-OM-1 (defaults), AC-OM-2 (floor refusal), AC-OM-9 (no-names census + planted control),
AC-OM-11 half (E2 on/off). Offline; no `gh`.
"""
import json
import os
import re
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FIX = os.path.join(ROOT, "tests", "fixtures", "operating-model")
sys.path.insert(0, SCRIPTS)
import foundry_operating_model as om  # noqa: E402

CLI = os.path.join(SCRIPTS, "foundry_operating_model.py")
SCHEMA = os.path.join(ROOT, "schema", "operating-model.schema.json")
TEMPLATE = os.path.join(ROOT, "cli", "templates", "operating-model.yaml.tmpl")

FLOOR_KEYS = {
    "merge.requeue_budget_outside_diff": 1,
    "merge.record_line_before_enqueue": True,
    "deck.resource_check": True,
    "deck.workflow_size_source": "smallest",
    "decisions.research_first": True,
    "cost.weigh_in_every_decision": True,
    "cost.priced_ask_for_always_on": True,
    "hygiene.teardown_required": True,
    "secrets.value_reads_forbidden": True,
}


def _ws(tmp_path, text=None, name="operating-model.yaml"):
    d = tmp_path / "ws"
    (d / ".foundry").mkdir(parents=True)
    if text is not None:
        (d / ".foundry" / name).write_text(text)
    return str(d)


def _set(key, value):
    cur = out = {}
    parts = key.split(".")
    for p in parts[:-1]:
        cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    return yaml.safe_dump(out)


def _cli(ws, *args):
    r = subprocess.run([sys.executable, CLI, "--project-dir", ws, *args], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


# ---- AC-OM-1 ---------------------------------------------------------------------------------
def test_ac_om_1_absent_file_loads_every_default(tmp_path):
    ws = _ws(tmp_path)
    p = om.load(ws)
    assert not p.file_present
    assert set(p.sources.values()) == {"default"}
    schema = json.load(open(SCHEMA))
    leaves = dict(om._leaves(schema))
    assert set(p.values) == set(leaves)
    assert len(p.values) >= 30
    for k, v in FLOOR_KEYS.items():
        assert p.values[k] == v and p.kind(k) == "floor"
    assert p.get("deck.max_builders") == 4 and p.kind("deck.max_builders") == "tunable"
    assert p.get("messaging.open_change_minutes") == 120
    assert p.get("governance.size_tag_required") == "L" and p.get("governance.design_required_paths") == []


def test_ac_om_1_status_marks_every_key_default(tmp_path):
    ws = _ws(tmp_path)
    rc, out, _ = _cli(ws, "status", "--json")
    assert rc == 0
    rows = json.loads(out)["rows"]
    assert rows and all(r["source"] == "default" for r in rows)
    assert {r["kind"] for r in rows} == {"floor", "tunable"}
    rc, text, _ = _cli(ws, "status")
    assert "defaults (no file)" in text and "source: file" not in text


def test_status_marks_file_keys_and_off_style(tmp_path):
    ws = _ws(tmp_path, open(os.path.join(FIX, "tuned.yaml")).read())
    rows = {r["key"]: r for r in json.loads(_cli(ws, "status", "--json")[1])["rows"]}
    assert rows["deck.max_builders"]["source"] == "file" and rows["deck.max_builders"]["value"] == 2
    assert rows["report.format"]["source"] == "default"
    assert "communication.style is off" in _cli(ws, "status")[1]


def test_shipped_template_equals_the_defaults_and_validates(tmp_path):
    ws = _ws(tmp_path, open(TEMPLATE).read())
    p = om.load(ws)
    assert set(p.sources.values()) == {"file"}
    assert p.values == om.load(_ws(tmp_path / "x")).values
    assert _cli(ws, "validate")[0] == 0


# ---- AC-OM-2 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(FLOOR_KEYS))
@pytest.mark.parametrize("bad", [False, 0, 2, "no", None])
def test_ac_om_2_floor_key_set_to_a_non_const_value_is_refused(tmp_path, key, bad):
    if bad == FLOOR_KEYS[key] and type(bad) is type(FLOOR_KEYS[key]):
        pytest.skip("that is the const")
    ws = _ws(tmp_path, _set(key, bad))
    rc, out, _ = _cli(ws, "validate")
    assert rc == 2 and f"floor-rule-disabled:{key}" in out
    with pytest.raises(om.PolicyError) as ei:
        om.load(ws)
    assert f"floor-rule-disabled:{key}" in ei.value.errors


def test_ac_om_2_floor_key_at_its_const_loads(tmp_path):
    for key, v in FLOOR_KEYS.items():
        assert om.load(_ws(tmp_path / key, _set(key, v))).source(key) == "file"


def test_true_is_not_one_for_the_requeue_floor(tmp_path):
    ws = _ws(tmp_path, _set("merge.requeue_budget_outside_diff", True))
    assert _cli(ws, "validate")[0] == 2


def test_ac_om_2_tunable_within_enum_loads(tmp_path):
    ws = _ws(tmp_path, "pr_strategy:\n  granularity: fine\nreport:\n  format: detailed\nclaims:\n  expires_after: 30m\n")
    p = om.load(ws)
    assert p.get("pr_strategy.granularity") == "fine" and p.source("pr_strategy.granularity") == "file"
    assert _cli(ws, "validate")[0] == 0


@pytest.mark.parametrize("text,needle", [
    ("pr_strategy:\n  granularity: huge\n", "invalid-value:pr_strategy.granularity"),
    ("deck:\n  max_builders: 0\n", "invalid-value:deck.max_builders"),
    ("deck:\n  max_builders: true\n", "invalid-value:deck.max_builders"),
    ("deck:\n  max_buildrs: 3\n", "unknown-key:deck.max_buildrs"),
    ("nope: 1\n", "unknown-key:nope"),
    ("claims:\n  expires_after: soon\n", "invalid-value:claims.expires_after"),
    ("merge:\n  full_suite_on_branch: [always]\n", "invalid-value:merge.full_suite_on_branch[0]"),
    ("deck: 3\n", "invalid-value:deck"),
    ("- a\n- b\n", "malformed:top level"),
    ("a: [unclosed\n", "malformed:"),
])
def test_broken_file_is_refused_naming_the_key_never_defaulted(tmp_path, text, needle):
    ws = _ws(tmp_path, text)
    rc, out, _ = _cli(ws, "validate")
    assert rc == 2 and needle in out
    assert _cli(ws, "status")[0] == 2
    with pytest.raises(om.PolicyError):
        om.load(ws)


def test_unreadable_file_is_refused(tmp_path):
    ws = _ws(tmp_path)
    p = tmp_path / "ws" / ".foundry" / "operating-model.yaml"
    p.write_bytes(b"\xff\xfe\x00bad")
    assert _cli(ws, "validate")[0] == 2


def test_empty_file_is_all_defaults(tmp_path):
    p = om.load(_ws(tmp_path, ""))
    assert p.file_present and set(p.sources.values()) == {"default"}


def test_schema_every_object_closed_and_floors_are_const():
    s = json.load(open(SCHEMA))

    def walk(node, path):
        if "properties" in node:
            assert node.get("additionalProperties") is False, path
            for k, v in node["properties"].items():
                walk(v, path + "." + k)
    walk(s, "$")
    consts = {k for k, leaf in om._leaves(s) if "const" in leaf}
    assert set(FLOOR_KEYS) <= consts
    # every non-const leaf carries a default
    for k, leaf in om._leaves(s):
        assert "const" in leaf or "default" in leaf, k


# ---- explain / render ------------------------------------------------------------------------
def test_explain_names_rule_why_and_primitive_for_every_key():
    for key, _ in om._leaves(json.load(open(SCHEMA))):
        hit = om.explain(key)
        assert hit and all(hit), key
    rc, out, _ = _cli(os.devnull, "explain", "secrets.value_reads_forbidden")
    assert rc == 0 and "rule:" in out and "why:" in out and "enforced by:" in out and "floor" in out
    assert _cli(os.devnull, "explain", "no.such")[0] == 2


@pytest.mark.parametrize("name", om.SECTIONS)
def test_every_named_section_renders(tmp_path, name):
    p = om.load(_ws(tmp_path))
    assert om.render_tick_section(p, name, str(tmp_path)).strip()


def test_sections_are_policy_driven(tmp_path):
    p = om.load(_ws(tmp_path, "pr_strategy:\n  granularity: fine\nreport:\n  max_lines: 12\n  include_ids: true\n"))
    assert "Fine-grained" in om.render_tick_section(p, "A1")
    e1 = om.render_tick_section(p, "E1")
    assert "at most 12 lines" in e1 and "IDs are allowed" in e1
    d = om.load(_ws(tmp_path / "d"))
    assert "Merge overlapping items" in om.render_tick_section(d, "A1")
    assert "at most 8 lines" in om.render_tick_section(d, "E1")


def test_unknown_section_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        om.render_tick_section(om.load(_ws(tmp_path)), "Z9")
    assert _cli(_ws(tmp_path / "w"), "render-tick-section", "Z9")[0] == 2


def test_workflow_size_smallest_wins(tmp_path):
    p = om.load(_ws(tmp_path))  # max_builders 4
    assert om.resolve_workflow_size(p) == 4
    assert om.resolve_workflow_size(p, session_size=2, prompt_size=6) == 2
    assert om.resolve_workflow_size(p, session_size=9, prompt_size=3) == 3
    assert om.resolve_workflow_size(p, session_size=9, prompt_size=8) == 4
    ws = _ws(tmp_path / "t", _set("deck.max_builders", 2))
    assert om.resolve_workflow_size(om.load(ws), prompt_size=5) == 2
    rc, out, _ = _cli(ws, "render-tick-section", "B8", "--session-size", "1", "--prompt-size", "3")
    assert rc == 0 and "The size for this tick is 1" in out
    assert "up to 1 builders" in _cli(ws, "render-tick-section", "B1", "--session-size", "1")[1]


# ---- E2 (AC-OM-11 half): on / off / operator-defined -----------------------------------------
def test_e2_default_on_returns_the_style_text(tmp_path):
    p = om.load(_ws(tmp_path))
    text = om.render_tick_section(p, "E2")
    assert "Simplified Technical English" in text and "active voice" in text
    assert "Do not claim strict ASD-STE100 compliance" in text
    rc, out, _ = _cli(_ws(tmp_path / "c"), "render-tick-section", "E2")
    assert rc == 0 and "Simplified Technical English" in out


def test_unquoted_yaml_off_means_off(tmp_path):
    ws = _ws(tmp_path, "communication:\n  style: off\nmessaging:\n  chat_mirror: on\n")
    p = om.load(ws)
    assert p.get("communication.style") == "off" and p.get("messaging.chat_mirror") == "on"
    ws2 = _ws(tmp_path / "f", "secrets:\n  value_reads_forbidden: off\n")
    assert _cli(ws2, "validate")[0] == 2


def test_e2_off_is_empty_and_status_says_so(tmp_path):
    ws = _ws(tmp_path, _set("communication.style", "off"))
    assert om.render_tick_section(om.load(ws), "E2") == ""
    rc, out, _ = _cli(ws, "render-tick-section", "E2")
    assert rc == 0 and out.strip() == ""
    assert "communication.style is off" in _cli(ws, "status")[1]


def test_e2_operator_defined_uses_the_operators_file(tmp_path):
    ws = _ws(tmp_path, open(os.path.join(FIX, "operator-defined.yaml")).read())
    open(os.path.join(ws, "style.txt"), "w").write(open(os.path.join(FIX, "style.txt")).read())
    out = om.render_tick_section(om.load(ws), "E2", ws)
    assert out.startswith("Write in plain") and "Simplified Technical" not in out


def test_e2_operator_defined_refuses_missing_or_escaping_file(tmp_path):
    ws = _ws(tmp_path, "communication:\n  style: operator-defined\n")
    rc, out, _ = _cli(ws, "render-tick-section", "E2")
    assert rc == 2 and "communication.operator_style_file" in out
    ws2 = _ws(tmp_path / "b", "communication:\n  style: operator-defined\n  operator_style_file: ../../etc/hostname\n")
    rc, out, _ = _cli(ws2, "render-tick-section", "E2")
    assert rc == 2 and "leaves the project" in out
    ws3 = _ws(tmp_path / "c", "communication:\n  style: operator-defined\n  operator_style_file: gone.txt\n")
    assert _cli(ws3, "render-tick-section", "E2")[0] == 2


# ---- doctor row ------------------------------------------------------------------------------
def _load_doctor():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fdoc_om", os.path.join(SCRIPTS, "foundry-doctor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DOCTOR = _load_doctor()


def _doctor_row(ws):
    return _DOCTOR, _DOCTOR.check_operating_model(project_dir=ws)


def test_doctor_row_states(tmp_path):
    mod, (ok, line) = _doctor_row(_ws(tmp_path / "a"))
    assert ok is True and line == "defaults (no file)"
    assert _doctor_row(_ws(tmp_path / "b", open(TEMPLATE).read()))[1] == (True, "ok")
    ok, line = _doctor_row(_ws(tmp_path / "c", _set("hygiene.teardown_required", False)))[1]
    assert ok is mod.ADVISORY and line == "invalid:hygiene.teardown_required"


# ---- AC-OM-9: no workspace / account / person names in defaults or examples -------------------
BANNED = [r"lukasrepublic", r"lsliwka", r"sliwka", r"agentic-workspace", r"agentic-handbook",
          r"digital-distillery", r"\b\d{12}\b"]
CENSUS_FILES = [SCHEMA, TEMPLATE, CLI, os.path.join(ROOT, "skills", "operating-model", "SKILL.md")]


def _census(paths):
    hits = []
    for p in paths:
        if os.path.isdir(p):
            sub = [os.path.join(p, f) for f in sorted(os.listdir(p))]
            hits += _census(sub)
            continue
        text = open(p, encoding="utf-8").read()
        for pat in BANNED:
            if re.search(pat, text, re.IGNORECASE):
                hits.append((os.path.relpath(p, ROOT) if p.startswith(ROOT) else p, pat))
    return hits


def test_ac_om_9_census_is_clean():
    assert _census(CENSUS_FILES + [FIX]) == []


def test_ac_om_9_planted_name_is_caught(tmp_path):
    planted = tmp_path / "planted.yaml"
    planted.write_text("report:\n  handoff_dir: ~/Documents/agentic-workspace\n")
    assert _census([str(planted)]) == [(str(planted), r"agentic-workspace")]
    acct = tmp_path / "acct.yaml"
    acct.write_text("account: 123456789012\n")
    assert _census([str(acct)])
