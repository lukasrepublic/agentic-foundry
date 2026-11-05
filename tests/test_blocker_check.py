"""gv-14: the blocker lint (rules B3/B4/C1, AC-OM-8). No network, no gh; synthetic data only."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "foundry_blocker_check.py"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_blocker_check as bc  # noqa: E402


QUESTIONS = {"capability": "the credential cannot create it", "authority": "not permitted for the agent",
             "judgement": "the operator's call"}


def attempt(**kw):
    return dict({"command": "example-cli deploy --dry-run", "outcome": "403 forbidden on example-resource"}, **kw)


def base(**kw):
    c = {"claim": "example-resource cannot be created by the agent",
         "evidence": ["403 forbidden on example-resource"],
         "attempted": [attempt()],
         "control": "example-cli list on a resource the agent owns succeeded with the same credential",
         "why_operator": "external-provisioning", "handoff": handoff(),
         "questions": QUESTIONS}
    c.update(kw)
    return c


def handoff(**kw):
    h = {"cwd": "/srv/example", "command": "example-cli grant --role reader",
         "why": "only the account owner can grant the role", "expect": "example-cli whoami lists the role"}
    h.update(kw)
    return h


def priced(rows=None, **kw):
    r = {"purpose": "an always-on example worker",
         "cost_breakdown": rows if rows is not None else [{"item": "worker", "monthly_cost": 12.5, "currency": "USD"}]}
    r.update(kw)
    return r


def run(cands):
    p = subprocess.run([sys.executable, str(SCRIPT), "--in", "-"], input=json.dumps(cands),
                       capture_output=True, text=True)
    return p


def reasons(cands):
    return [t["reason"] for t in bc.partition(cands)["next_tasks"]]


# -- AC-OM-8 ------------------------------------------------------------------------------

def test_complete_candidate_is_a_blocker():
    v = bc.partition([base()])
    assert v["blockers"] == [base()] and v["next_tasks"] == []


@pytest.mark.parametrize("field,needle", [("attempted", "no attempted"), ("control", "no control")])
def test_missing_attempt_or_control_is_a_next_task_with_reason(field, needle):
    c = base()
    del c[field]
    v = bc.partition([c])
    assert v["blockers"] == [] and len(v["next_tasks"]) == 1
    assert needle in v["next_tasks"][0]["reason"] or f"missing required field '{field}'" in v["next_tasks"][0]["reason"]
    assert v["next_tasks"][0]["candidate"] == c


@pytest.mark.parametrize("bad", [[], ["a bare string"], [{"command": "x"}], [{"outcome": "y"}]])
def test_attempted_must_be_command_outcome_objects(bad):
    assert bc.partition([base(attempted=bad)])["blockers"] == []


@pytest.mark.parametrize("bad", ["", "   ", None, 3])
def test_control_must_be_a_real_string(bad):
    assert bc.partition([base(control=bad)])["blockers"] == []


def test_empty_evidence_and_unknown_why_operator_are_demoted():
    assert bc.partition([base(evidence=[])])["blockers"] == []
    assert bc.partition([base(why_operator="security-widening")])["blockers"] == []
    assert bc.partition([base(unexpected=1)])["blockers"] == []


# -- credential-step: try the running job's credential first --------------------------------

def test_credential_step_without_running_job_attempt_is_demoted():
    v = bc.partition([base(why_operator="credential-step", handoff=handoff())])
    assert v["blockers"] == []
    assert "running job" in v["next_tasks"][0]["reason"]


def test_credential_step_with_flagged_attempt_is_a_blocker():
    c = base(why_operator="credential-step", handoff=handoff(),
             attempted=[attempt(used_running_job_credential=True)])
    assert bc.partition([c])["blockers"] == [c]


@pytest.mark.parametrize("text", ["never tried the job token", "retried with the running job's token: still 403",
                                  "GITHUB_TOKEN not attempted"])
def test_free_text_never_satisfies_the_running_job_credential_rule(text):
    c = base(why_operator="credential-step", handoff=handoff(), attempted=[attempt(outcome=text)])
    v = bc.partition([c])
    assert v["blockers"] == [] and "running job" in v["next_tasks"][0]["reason"]
    c2 = base(why_operator="credential-step", handoff=handoff(),
              attempted=[attempt(outcome=text, used_running_job_credential="yes")])
    assert bc.partition([c2])["blockers"] == []


# -- priced-ask ---------------------------------------------------------------------------

def test_priced_ask_complete_is_a_blocker_and_needs_handoff():
    c = base(why_operator="priced-ask", handoff=handoff(), priced_ask=priced())
    assert bc.partition([c])["blockers"] == [c]
    no_handoff = {k: v for k, v in c.items() if k != "handoff"}
    assert "handoff" in reasons([no_handoff])[0]


@pytest.mark.parametrize("pa", [priced(rows=[]), {"purpose": "x"}])
def test_priced_ask_with_empty_cost_breakdown_is_refused(pa):
    c = base(why_operator="priced-ask", handoff=handoff(), priced_ask=pa)
    p = run([base(), c])
    assert p.returncode == 2 and p.stdout == "" and "cost_breakdown" in p.stderr
    with pytest.raises(bc.BlockerCheckError):
        bc.partition([c])


def test_priced_ask_without_priced_ask_object_is_refused():
    c = base(why_operator="priced-ask", handoff=handoff())
    assert run([c]).returncode == 2


def test_priced_ask_row_shape_is_checked():
    c = base(why_operator="priced-ask", handoff=handoff(),
             priced_ask=priced(rows=[{"item": "w", "monthly_cost": -1, "currency": "USD"}]))
    assert "monthly_cost" in reasons([c])[0]


# -- handoff command shape ----------------------------------------------------------------

BAD_COMMANDS = [
    ("example-cli a && example-cli b", "&&"), ("example-cli a & ", "&"), ("example-cli a; example-cli b", ";"),
    ("example-cli a | tee out", "|"), ("example-cli a\nexample-cli b", "newline"),
    ("example-cli a\rexample-cli b", "carriage return"),
    ("echo $(whoami)", "$("), ("echo `whoami`", "`"),
    ("diff <(a) <(b)", "<"), ("tee >(cat)", ">"), ("example-cli a > out", ">"), ("example-cli a >> out", ">"),
    ("example-cli a < in", "<"),
    ("set -e", "set -e"), ("set -eu", "set -eu"), ("set -ex", "set -ex"), ("set -o errexit", "errexit"),
    ("trap true EXIT", "trap"), ("exec example-cli", "exec"), ("exit 1", "exit"),
    ("FOO=1 exec example-cli", "exec"), ("A=1 B=2 set -e", "set -e"),
    ("command exec example-cli", "exec"), ("builtin exec example-cli", "exec"),
]


@pytest.mark.parametrize("cmd,token", BAD_COMMANDS)
def test_handoff_command_shape_refusals(cmd, token):
    c = base(why_operator="external-provisioning", handoff=handoff(command=cmd))
    v = bc.partition([c])
    assert v["blockers"] == [] and token in v["next_tasks"][0]["reason"]


@pytest.mark.parametrize("cmd", [c for c, _ in BAD_COMMANDS])
def test_python_floor_and_json_schema_agree_on_commands(cmd):
    js = pytest.importorskip("jsonschema")
    c = base(why_operator="external-provisioning", handoff=handoff(command=cmd))
    with pytest.raises(js.ValidationError):
        js.validate(c, bc.load_schema())


@pytest.mark.parametrize("cmd", ["example-cli grant --role reader", "./trap_handler.sh --flag",
                                 "FOO=1 example-cli run", "./execute.sh", "settings-tool set --flag", "gh issue view 7"])
def test_good_commands_pass_both_layers(cmd):
    c = base(why_operator="external-provisioning", handoff=handoff(command=cmd))
    assert bc.partition([c])["blockers"] == [c]
    js = pytest.importorskip("jsonschema")
    js.validate(c, bc.load_schema())


def test_schema_patterns_are_the_floor_patterns():
    cmd = bc.load_schema()["properties"]["handoff"]["properties"]["command"]["allOf"]
    assert [x["not"]["pattern"] for x in cmd] == [bc.HANDOFF_CHAIN_PATTERN, bc.HANDOFF_WORD_PATTERN]


def test_handoff_bare_command_and_relative_cwd():
    ok = base(why_operator="external-provisioning", handoff=handoff(command="./trap_handler.sh --flag"))
    assert bc.partition([ok])["blockers"] == [ok]
    rel = base(why_operator="external-provisioning", handoff=handoff(cwd="relative/dir"))
    assert bc.partition([rel])["blockers"] == []


def test_external_provisioning_requires_handoff():
    assert "handoff" in reasons([{k: v for k, v in base().items() if k != "handoff"}])[0]


# -- questions / options --------------------------------------------------------------------

def test_questions_are_required_and_the_missing_sub_field_is_named():
    c = base()
    del c["questions"]
    v = bc.partition([c])
    assert v["blockers"] == [] and "questions" in v["next_tasks"][0]["reason"]
    for missing in ("capability", "authority", "judgement"):
        q = {k: x for k, x in QUESTIONS.items() if k != missing}
        assert f"questions.{missing}" in reasons([base(questions=q)])[0]


def test_no_consensus_needs_two_options_a_recommendation_and_a_one_word_ask():
    ok = base(why_operator="no-consensus-after-research", options=["a", "b"], recommendation="a", one_word_ask="a or b")
    ok.pop("handoff")
    assert bc.partition([ok])["blockers"] == [ok]
    for drop in ("options", "recommendation", "one_word_ask"):
        c = {k: v for k, v in ok.items() if k != drop}
        assert drop in reasons([c])[0]
    assert "at least 2" in reasons([dict(ok, options=["only"])])[0]


def test_one_malformed_candidate_refuses_the_batch_and_lists_every_index():
    bad_a = base(why_operator="priced-ask", handoff=handoff(), priced_ask=priced(rows=[]))
    bad_b = base(why_operator="priced-ask", handoff=handoff(), priced_ask={"purpose": "x"})
    p = run([base(), bad_a, base(), bad_b])
    assert p.returncode == 2 and p.stdout == "" and "index 1" in p.stderr and "index 3" in p.stderr


def test_partition_does_not_crash_on_odd_shapes():
    odd = [base(attempted=[None, 3, {"command": []}]), base(evidence=[1, None]), base(questions="x"),
           base(handoff=5), base(priced_ask=[]), {"why_operator": {"x": 1}}]
    v = bc.partition(odd)
    assert v["blockers"] == [] and len(v["next_tasks"]) == len(odd)


# -- CLI --------------------------------------------------------------------------------------

def test_cli_valid_exit_0_and_malformed_exit_2(tmp_path):
    p = run([base(), base(control="")])
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert len(out["blockers"]) == 1 and len(out["next_tasks"]) == 1
    f = tmp_path / "c.json"
    f.write_text('{"not": "an array"}')
    for arg in (str(f), str(tmp_path / "missing.json")):
        q = subprocess.run([sys.executable, str(SCRIPT), "--in", arg], capture_output=True, text=True)
        assert q.returncode == 2 and q.stdout == ""
    f.write_text("not json")
    assert subprocess.run([sys.executable, str(SCRIPT), "--in", str(f)], capture_output=True, text=True).returncode == 2


def test_non_object_candidate_is_demoted_not_crashed():
    assert len(bc.partition(["x", 3, None])["next_tasks"]) == 3


def test_schema_file_matches_the_lint_vocabulary():
    s = json.loads((ROOT / "schema" / "blocker.schema.json").read_text())
    assert set(s["properties"]["why_operator"]["enum"]) == bc._WHY_OPERATOR_SET
    assert set(s["required"]) == set(bc._REQUIRED_FIELDS)
    assert set(s["properties"]) == set(bc._ALLOWED_FIELDS)
    assert s["properties"]["attempted"]["minItems"] == 1


def test_json_schema_agrees_when_available():
    js = pytest.importorskip("jsonschema")
    schema = bc.load_schema()
    js.validate(base(), schema)
    js.validate(base(why_operator="priced-ask", handoff=handoff(), priced_ask=priced()), schema)
    for bad in (base(attempted=[]), {k: v for k, v in base().items() if k != "control"},
                base(why_operator="priced-ask", handoff=handoff()),
                base(why_operator="priced-ask", handoff=handoff(), priced_ask=priced(rows=[])),
                {k: v for k, v in base(why_operator="credential-step").items() if k != "handoff"},
                {k: v for k, v in base().items() if k != "questions"},
                base(why_operator="no-consensus-after-research")):
        with pytest.raises(js.ValidationError):
            js.validate(bad, schema)


def test_no_network_imports():
    import ast
    mods = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert not mods & {"socket", "urllib", "http", "subprocess", "requests", "ssl"}
