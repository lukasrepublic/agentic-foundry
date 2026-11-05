"""gv-16: the `governance-trace` CI check (governance/kits/delivery/checks/governance-trace.py) and its job template.

The script is pure over the files the job hands it (`gh pr view` / `gh issue view` JSON), so no `gh` and no network
here. Assertions are on the parsed JSON findings: check name, ok flag, and the path a finding names.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.py"
TEMPLATE = ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.yml.template"
GOV = ROOT / "scripts" / "foundry_governance.py"

DESIGN = """## Design
- Components: hooks/foundry-example.py, skills/dispatch/
- Reuses: the shared brief renderer
- Cites: FG-SEC-006, FG-QA-001

## Security
- FG-SEC-006

## Surfaces
- cli: n/a: a hook has no command line
"""


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    (p / ".foundry").mkdir(parents=True)
    (p / ".foundry" / "operating-model.yaml").write_text(
        'schema_version: 1\ngovernance:\n  design_required_paths: ["hooks/**", "skills/**"]\n')
    return p


def run(proj, tmp_path, *, pr_body="Ticket: #7\n", files=("hooks/foundry-example.py",), ticket_body=DESIGN, labels=(), today=None):
    pr = tmp_path / "pr.json"
    ticket = tmp_path / "ticket.json"
    pr.write_text(json.dumps({"number": 3, "body": pr_body, "files": [{"path": f} for f in files]}))
    ticket.write_text(json.dumps({"number": 7, "body": ticket_body, "labels": [{"name": n} for n in labels]}))
    cmd = [sys.executable, str(CHECK), "check", "--pr-json", str(pr), "--ticket-json", str(ticket), "--project-dir", str(proj),
           "--plugin-root", str(ROOT), "--json"]
    if today:
        cmd += ["--today", today]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return p, (json.loads(p.stdout) if p.stdout.strip().startswith("{") else None)


def by_check(doc):
    out = {}
    for f in doc["findings"]:
        out.setdefault(f["check"], []).append(f)
    return out


def test_a_diff_inside_the_designs_components_passes(proj, tmp_path):
    p, doc = run(proj, tmp_path, files=("hooks/foundry-example.py", "skills/dispatch/SKILL.md", "skills/dispatch/sub/x.md"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["ok"] is True
    assert all(f["ok"] for f in doc["findings"])
    assert {"ticket-named", "design-present", "cited-ids-exist", "paths-in-design", "rendered-fresh", "exceptions-live"} <= set(by_check(doc))


def test_a_path_outside_the_components_fails_naming_it(proj, tmp_path):
    p, doc = run(proj, tmp_path, files=("hooks/foundry-example.py", "hooks/other.py", "src/app.py"))
    assert p.returncode == 1
    bad = [f for f in doc["findings"] if not f["ok"]]
    assert {f["check"] for f in bad} == {"paths-in-design"}
    assert sorted(f["path"] for f in bad) == ["hooks/other.py", "src/app.py"]
    assert "hooks/other.py" in bad[0]["detail"] or "hooks/other.py" in bad[1]["detail"]


def test_a_component_glob_covers_what_it_matches(proj, tmp_path):
    body = DESIGN.replace("hooks/foundry-example.py, skills/dispatch/", "hooks/**/*.py, docs/guide.md")
    p, doc = run(proj, tmp_path, ticket_body=body, files=("hooks/a/b.py", "docs/guide.md"))
    assert p.returncode == 0, p.stdout
    p, doc = run(proj, tmp_path, ticket_body=body, files=("hooks/a/b.sh",))
    assert p.returncode == 1 and by_check(doc)["paths-in-design"][0]["path"] == "hooks/a/b.sh"


def test_the_generated_rules_directory_is_exempt(proj, tmp_path):
    p, doc = run(proj, tmp_path, files=("hooks/foundry-example.py", "governance/rendered/security.md"))
    assert by_check(doc)["paths-in-design"][0]["ok"] is True


def test_the_pr_must_name_a_ticket(proj, tmp_path):
    p, doc = run(proj, tmp_path, pr_body="no reference here")
    assert p.returncode == 1 and by_check(doc)["ticket-named"][0]["ok"] is False
    for body in ("Closes #7", "Fixes o/r#7", "Refs: #7", "ticket #7"):
        p, doc = run(proj, tmp_path, pr_body=body)
        assert by_check(doc)["ticket-named"][0]["ok"] is True, body


def test_ticket_ref_prints_the_number(tmp_path):
    f = tmp_path / "pr.json"
    f.write_text(json.dumps({"body": "Summary\n\nCloses o/r#41\n"}))
    p = subprocess.run([sys.executable, str(CHECK), "ticket-ref", "--pr-json", str(f)], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.strip() == "41"
    f.write_text(json.dumps({"body": "nothing"}))
    p = subprocess.run([sys.executable, str(CHECK), "ticket-ref", "--pr-json", str(f)], capture_output=True, text=True)
    assert p.returncode == 1


def test_an_unknown_cited_id_fails(proj, tmp_path):
    p, doc = run(proj, tmp_path, ticket_body=DESIGN.replace("FG-QA-001", "FG-NOPE-999"))
    assert p.returncode == 1
    f = by_check(doc)["cited-ids-exist"][0]
    assert f["ok"] is False and "FG-NOPE-999" in f["detail"]


def test_a_governed_path_with_no_design_fails(proj, tmp_path):
    p, doc = run(proj, tmp_path, ticket_body="## Done means\n```\ntrue\n```\n")
    assert p.returncode == 1
    f = by_check(doc)["design-present"][0]
    assert f["ok"] is False and "hooks/foundry-example.py" in f["detail"]


def test_below_the_class_no_design_is_a_pass_control(proj, tmp_path):
    p, doc = run(proj, tmp_path, ticket_body="## Done means\n```\ntrue\n```\n", files=("src/app.py",))
    assert p.returncode == 0, p.stdout
    assert by_check(doc)["design-present"][0]["ok"] is True


def test_the_size_tag_class_needs_a_design_too(proj, tmp_path):
    p, doc = run(proj, tmp_path, ticket_body="no design", files=("src/app.py",), labels=("size:L",))
    assert p.returncode == 1 and by_check(doc)["design-present"][0]["ok"] is False


def test_stale_rendered_rules_fail_and_a_fresh_render_passes(proj, tmp_path):
    (proj / "governance" / "rendered").mkdir(parents=True)
    (proj / "governance" / "rendered" / "security.md").write_text("stale\n")
    p, doc = run(proj, tmp_path)
    assert p.returncode == 1 and by_check(doc)["rendered-fresh"][0]["ok"] is False
    assert "rendered-stale" in by_check(doc)["rendered-fresh"][0]["detail"]
    r = subprocess.run([sys.executable, str(GOV), "--project-dir", str(proj), "--plugin-root", str(ROOT), "render"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    p, doc = run(proj, tmp_path)
    assert p.returncode == 0 and by_check(doc)["rendered-fresh"][0]["ok"] is True


def test_an_expired_exception_fails_and_a_live_one_does_not(proj, tmp_path):
    (proj / "governance" / "local").mkdir(parents=True)
    exc = {"rule": "FG-DLV-008", "scope": "**", "reason": "legacy flags", "owner": "team", "expires": "2026-01-01"}
    (proj / "governance" / "local" / "exceptions.yaml").write_text(yaml.safe_dump({"exceptions": [exc]}))
    p, doc = run(proj, tmp_path, today="2026-11-04")
    assert p.returncode == 1
    f = by_check(doc)["exceptions-live"][0]
    assert f["ok"] is False and "FG-DLV-008" in f["detail"] and "expired" in f["detail"]
    exc["expires"] = "2027-01-01"
    (proj / "governance" / "local" / "exceptions.yaml").write_text(yaml.safe_dump({"exceptions": [exc]}))
    p, doc = run(proj, tmp_path, today="2026-11-04")
    assert p.returncode == 0 and by_check(doc)["exceptions-live"][0]["ok"] is True


def test_bad_input_is_exit_2(tmp_path):
    p = subprocess.run([sys.executable, str(CHECK), "check", "--pr-json", str(tmp_path / "missing.json"),
                        "--ticket-json", str(tmp_path / "x.json")], capture_output=True, text=True)
    assert p.returncode == 2


# ---- the job template ---------------------------------------------------------------------------------------
def test_the_template_is_a_read_only_job_with_every_uses_pinned_to_a_40_char_sha():
    text = TEMPLATE.read_text()
    doc = yaml.safe_load(text)
    assert doc["permissions"] == {"contents": "read", "pull-requests": "read", "issues": "read"}
    assert "governance-trace" in doc["jobs"]
    uses = re.findall(r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*(\S+)", text, re.M)
    assert uses, "the template checks code out"
    for u in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u), f"unpinned: {u}"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for u in uses:
        assert u in ci, f"{u} is not a SHA this repository already pins"
    # the check runs from the PLUGIN checkout, never from a script of the PR head
    assert "plugin/governance/kits/delivery/checks/governance-trace.py" in text
    assert "pull_request_target" not in text
    assert "persist-credentials: false" in text
