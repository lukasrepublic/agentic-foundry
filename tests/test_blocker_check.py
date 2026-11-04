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


def attempt(**kw):
    return dict({"command": "example-cli deploy --dry-run", "outcome": "403 forbidden on example-resource"}, **kw)


def base(**kw):
    c = {"claim": "example-resource cannot be created by the agent",
         "evidence": ["403 forbidden on example-resource"],
         "attempted": [attempt()],
         "control": "example-cli list on a resource the agent owns succeeded with the same credential",
         "why_operator": "no-consensus-after-research"}
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


def test_credential_step_with_outcome_naming_the_job_credential_is_a_blocker():
    c = base(why_operator="credential-step", handoff=handoff(),
             attempted=[attempt(outcome="retried with the running job's token: still 403")])
    assert bc.partition([c])["blockers"] == [c]


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

@pytest.mark.parametrize("cmd,token", [
    ("example-cli a && example-cli b", "&&"),
    ("example-cli a; example-cli b", ";"),
    ("example-cli a | tee out", "|"),
    ("example-cli a\nexample-cli b", "newline"),
    ("echo $(whoami)", "$("),
    ("set -e", "set -e"),
    ("set -eu", "set -eu"),
    ("trap true EXIT", "trap"),
    ("exec example-cli", "exec"),
])
def test_handoff_command_shape_refusals(cmd, token):
    c = base(why_operator="external-provisioning", handoff=handoff(command=cmd))
    v = bc.partition([c])
    assert v["blockers"] == [] and token in v["next_tasks"][0]["reason"]


def test_handoff_bare_command_and_relative_cwd():
    ok = base(why_operator="external-provisioning", handoff=handoff(command="./trap_handler.sh --flag"))
    assert bc.partition([ok])["blockers"] == [ok]
    rel = base(why_operator="external-provisioning", handoff=handoff(cwd="relative/dir"))
    assert bc.partition([rel])["blockers"] == []


def test_external_provisioning_requires_handoff():
    assert "handoff" in reasons([base(why_operator="external-provisioning")])[0]


# -- questions / options --------------------------------------------------------------------

def test_questions_when_present_need_all_three():
    full = {"capability": "yes", "authority": "yes", "judgement": "the operator's"}
    assert bc.partition([base(questions=full)])["blockers"] != []
    part = {"capability": "yes", "authority": "yes"}
    assert "questions.judgement" in reasons([base(questions=part)])[0]


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
                base(why_operator="credential-step")):
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
