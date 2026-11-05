"""gv-16: the `governance-trace` CI check (governance/kits/delivery/checks/governance-trace.py) and its job template.

The script is pure over the files the job hands it (`gh pr view` / `gh issue view` JSON), so no `gh` and no network
here. Assertions are on the parsed JSON findings: check name, ok flag, and the path a finding names.
"""
import json
import os
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
    (p / ".claude").mkdir()
    (p / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "o", "planning_repo": "o/p", "operators": {"alice": "op_a"}}}))
    return p


@pytest.fixture(autouse=True)
def gh_path(tmp_path):
    """A `gh` on PATH that answers the two trust probes and logs argv: nothing real is called."""
    d = tmp_path / "ghbin"
    d.mkdir()
    g = d / "gh"
    g.write_text("#!/bin/sh\necho \"$@\" >> \"$GH_LOG\"\ncase \"$*\" in\n"
                 "  'api user') echo '{\"login\": \"agent-bot\"}';;\n"
                 "  *collaborators/carol/*) echo '{\"permission\": \"write\"}';;\n"
                 "  *) echo '{\"permission\": \"read\"}';;\nesac\n")
    g.chmod(0o755)
    return d


def run(proj, tmp_path, *, pr_body="Ticket: #7\n", files=("hooks/foundry-example.py",), ticket_body=DESIGN, labels=(), today=None,
        author="alice", base=None, paged=False, extra=()):
    pr = tmp_path / "pr.json"
    ticket = tmp_path / "ticket.json"
    doc = {"number": 3, "body": pr_body}
    if not paged:
        doc["files"] = [{"path": f} for f in files]
    pr.write_text(json.dumps(doc))
    ticket.write_text(json.dumps({"number": 7, "body": ticket_body, "labels": [{"name": n} for n in labels],
                                  "author": {"login": author}}))
    cmd = [sys.executable, str(CHECK), "check", "--pr-json", str(pr), "--ticket-json", str(ticket), "--project-dir", str(proj),
           "--plugin-root", str(ROOT), "--repo", "o/r", "--json", *extra]
    if paged:
        fj = tmp_path / "files.json"
        fj.write_text(json.dumps([{"path": f} for f in files]))
        cmd += ["--files-json", str(fj)]
    cmd += ["--base-dir", str(base if base is not None else proj)]       # gv-20: the CLI refuses to run without a base checkout
    if today:
        cmd += ["--today", today]
    env = dict(os.environ, PATH=str(tmp_path / "ghbin") + os.pathsep + os.environ["PATH"], GH_LOG=str(tmp_path / "gh.log"))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
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
    assert p.returncode == 0 and p.stdout.strip() == "o/r#41"                              # a cross-repo ref keeps its repo
    f.write_text(json.dumps({"body": "Closes #9\n"}))
    assert subprocess.run([sys.executable, str(CHECK), "ticket-ref", "--pr-json", str(f)], capture_output=True, text=True).stdout.strip() == "#9"
    f.write_text(json.dumps({"body": "Summary\n\nCloses o/r#41\n"}))
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


# ---- dl-07: the built-from rule, the .tmpl template, the Learned comment ------------------------------------
GOOD_SHA = "a" * 40
MISSING_SHA = "b" * 40
TMPL = ROOT / "governance" / "kits" / "delivery" / "templates" / "governance-trace.yml.tmpl"


@pytest.fixture
def ws_proj(proj):
    """A product repo o/r governed from the workspace o/ws, and a `gh` that knows one workspace commit."""
    (proj / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "o", "planning_repo": "o/ws", "repos": ["o/r"], "operators": {"alice": "op_a"}}}))
    return proj


@pytest.fixture
def ws_gh(tmp_path, gh_path):
    g = gh_path / "gh"
    g.write_text("#!/bin/sh\necho \"$@\" >> \"$GH_LOG\"\ncase \"$*\" in\n"
                 f"  'api repos/o/ws/commits/{GOOD_SHA}') echo '{{\"sha\": \"{GOOD_SHA}\"}}';;\n"
                 "  *repos/o/ws/commits/*) exit 1;;\n"
                 "  'api user') echo '{\"login\": \"agent-bot\"}';;\n"
                 "  *) echo '{\"permission\": \"read\"}';;\nesac\n")
    g.chmod(0o755)
    return g


def built_from(doc):
    (f,) = by_check(doc)["built-from"]
    return f


def test_built_from_passes_with_a_resolvable_trailer(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\n\nBuilt-from: {GOOD_SHA}\n")
    assert built_from(doc)["ok"] is True and GOOD_SHA in built_from(doc)["detail"]


def test_built_from_reads_the_head_commit_too(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path)
    assert built_from(doc)["ok"] is False                                  # base case: no trailer anywhere
    pr = tmp_path / "pr.json"
    import importlib.util
    spec = importlib.util.spec_from_file_location("gt", CHECK)
    gt = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True                  # never leave a .pyc inside the kit directory
    spec.loader.exec_module(gt)
    pr_doc = {"body": "Ticket: #7", "commits": [{"messageHeadline": "old", "messageBody": ""},
                                                {"messageHeadline": "feat: x", "messageBody": f"Built-from: {GOOD_SHA}"}]}
    assert gt._built_from_values(pr_doc) == [GOOD_SHA]


def test_built_from_missing_trailer_fails_and_names_the_rule(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path)
    f = built_from(doc)
    assert p.returncode == 1 and f["ok"] is False and "no `Built-from:" in f["detail"]


@pytest.mark.parametrize("value", ["abc123", "A" * 40, "a" * 39, "a" * 41, ""])
def test_built_from_malformed_sha_fails(ws_proj, ws_gh, tmp_path, value):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {value}\n")
    assert built_from(doc)["ok"] is False and "malformed" in built_from(doc)["detail"]


def test_built_from_unresolvable_sha_fails_closed(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {MISSING_SHA}\n")
    f = built_from(doc)
    assert f["ok"] is False and "does not resolve" in f["detail"] and p.returncode == 1


def test_built_from_single_repo_skips_with_a_named_reason(proj, tmp_path):
    p, doc = run(proj, tmp_path)                                           # o/r is not in the binding: single-repo
    f = built_from(doc)
    assert f["ok"] is True and f["detail"].startswith("skipped: single-repo")


def test_built_from_is_skipped_in_the_workspace_repo_itself(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, extra=())                              # --repo is o/r; re-run as the workspace
    cmd_repo = ["--repo", "o/ws"]
    import subprocess as sp
    pr, ticket = tmp_path / "pr.json", tmp_path / "ticket.json"
    r = sp.run([sys.executable, str(CHECK), "check", "--pr-json", str(pr), "--ticket-json", str(ticket), "--project-dir", str(ws_proj),
                "--plugin-root", str(ROOT), *cmd_repo, "--json", "--base-dir", str(ws_proj)], capture_output=True, text=True,
               env=dict(os.environ, PATH=str(tmp_path / "ghbin") + os.pathsep + os.environ["PATH"], GH_LOG=str(tmp_path / "gh.log")))
    assert built_from(json.loads(r.stdout))["detail"].startswith("skipped: single-repo")


def test_built_from_makes_one_workspace_call_and_never_prints_the_token(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {GOOD_SHA}\n")
    log = (tmp_path / "gh.log").read_text()
    assert log.count("repos/o/ws/commits/") == 1
    assert "WORKSPACE_GH_TOKEN" not in p.stdout + p.stderr


def test_the_tmpl_template_is_the_one_the_kit_lists():
    assert TMPL.is_file()
    assert "governance-trace.yml.tmpl" in (ROOT / "governance" / "kits" / "delivery" / "rules.yaml").read_text()
    # the legacy name stays only because older tests read it; it must not drift from the installed template
    assert TEMPLATE.read_text() == TMPL.read_text()
    text = TMPL.read_text()
    assert "--json number,body,commits" in text
    uses = re.findall(r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*(\S+)", text, re.M)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses)


def test_dispatch_skill_instructs_the_learned_comment_and_the_trailer_rule():
    text = (ROOT / "skills" / "dispatch" / "SKILL.md").read_text()
    assert "## Learned" in text and "at most ten lines" in text
    assert "Built-from: <workspace sha>" in text
