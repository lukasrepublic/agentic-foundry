"""gv-19: the plugin governs itself. The lock is committed and valid, `check` is clean at the head, the upstream private layer
(`governance/local/`) is empty of adopter rules by construction, every frozen baseline carries a reason and a future
expiry, and the governance-trace job passes on a PR that cites ids and fails on one that does not (a control).

Assertions are on parsed structure (YAML/JSON fields, parsed findings), never on substrings of joined output.
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

import test_governance_trace as T
from test_governance_trace import gh_path  # noqa: F401  (autouse fixture: a fake `gh` on PATH, nothing real is called)

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "scripts" / "foundry_governance.py"
LOCK = ROOT / ".foundry" / "governance.lock"
LOCAL = ROOT / "governance" / "local"
ORG_ID = re.compile(r"\b<ORG>-")


def _gov(*args):
    return subprocess.run([sys.executable, str(GOV), "--project-dir", str(ROOT), "--plugin-root", str(ROOT), *args],
                          capture_output=True, text=True, timeout=300)


def test_the_lock_exists_and_validates_against_its_schema():
    assert LOCK.is_file()
    doc = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schema" / "governance-lock.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(doc)) == []
    assert doc["blueprint"]["id"] == "claude-plugin"


def test_check_exits_zero_at_the_head():
    p = _gov("check")
    assert p.returncode == 0, p.stdout + p.stderr
    # zero drift and zero expired exceptions are both reported as findings by `check`; none may be printed
    assert [ln for ln in p.stdout.splitlines() if ln.strip() and ln.strip() != "governance-check-ok"] == []


def test_local_layer_carries_no_adopter_rule_ids():
    assert LOCAL.is_dir()
    assert not (LOCAL / "rules.yaml").exists(), "upstream's own private layer stays empty of rules"
    ex = yaml.safe_load((LOCAL / "exceptions.yaml").read_text(encoding="utf-8"))
    assert ex == {"exceptions": []}
    bad = []
    for f in LOCAL.rglob("*"):
        if f.is_file() and ".kit-base" not in f.parts:
            text = f.read_text(encoding="utf-8", errors="replace")
            if ORG_ID.search(text):
                bad.append(str(f.relative_to(ROOT)))
    assert bad == []


def test_every_baseline_has_a_reason_and_a_future_expiry():
    today = datetime.date.today()
    files = sorted((LOCAL / "baselines").glob("*.json"))
    assert files, "the dogfood run baselined its known findings"
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert doc["check"] == f.stem
        assert doc["findings"]
        for item in doc["findings"]:
            assert isinstance(item, dict), f"{f.name}: a baseline entry must carry its reason, not a bare id"
            assert isinstance(item.get("reason"), str) and len(item["reason"].split()) >= 6, f"{f.name}: reason"
            expires = datetime.date.fromisoformat(item["expires"])
            assert expires > today, f"{f.name}: expired {expires}"


# ---- governance-trace as a control, run against the real tree (its real lock and rendered rules) ----

REAL_DESIGN = """## Design
- Components: hooks/foundry-example.py
- Reuses: the shared brief renderer
- Cites: FG-SEC-006, FG-QA-001

## Security
- FG-SEC-006

## Surfaces
- cli: n/a: a hook has no command line
"""


def _trace(tmp_path, **kw):
    p, doc = T.run(ROOT, tmp_path, **kw)
    return p, doc


def test_trace_passes_on_a_pr_that_cites_ids_against_the_real_tree(tmp_path):
    p, doc = _trace(tmp_path, ticket_body=REAL_DESIGN, extra=("--agent-login", "agent-bot"))
    findings = {f["check"]: f for f in doc["findings"]}
    assert findings["cited-ids-exist"]["ok"] is True, doc
    assert findings["design-present"]["ok"] is True, doc
    assert findings["rendered-fresh"]["ok"] is True, doc
    assert findings["exceptions-live"]["ok"] is True, doc


def test_trace_fails_on_a_fixture_pr_that_cites_no_design_or_an_unknown_id(tmp_path):
    p, doc = _trace(tmp_path, ticket_body="## Done means\n```\ntrue\n```\n", extra=("--agent-login", "agent-bot"))
    assert p.returncode == 1, p.stdout + p.stderr
    assert doc["ok"] is False
    assert {f["check"] for f in doc["findings"] if not f["ok"]} >= {"design-present"}

    p2, doc2 = _trace(tmp_path, ticket_body=REAL_DESIGN.replace("FG-QA-001", "FG-NOPE-999"), extra=("--agent-login", "agent-bot"))
    assert p2.returncode == 1
    bad = [f for f in doc2["findings"] if f["check"] == "cited-ids-exist" and not f["ok"]]
    assert bad and "FG-NOPE-999" in bad[0]["detail"]

    p3, doc3 = _trace(tmp_path, pr_body="no ticket here\n", ticket_body=REAL_DESIGN, extra=("--agent-login", "agent-bot"))
    assert p3.returncode == 1
    assert [f for f in doc3["findings"] if f["check"] == "ticket-named" and not f["ok"]]
