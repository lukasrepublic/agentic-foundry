"""gv-16: the `governance-trace` CI check (governance/kits/delivery/checks/governance-trace.py) and its job template.

The script is pure over the files the job hands it (`gh pr view` / `gh issue view` JSON), so no `gh` and no network
here. Assertions are on the parsed JSON findings: check name, ok flag, and the path a finding names.
"""
import importlib.util
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
TEMPLATE = ROOT / "governance" / "kits" / "delivery" / "templates" / "governance-trace.yml.tmpl"
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
        author="alice", base=None, paged=False, extra=(), env_extra=None, repo="o/r"):
    pr = tmp_path / "pr.json"
    ticket = tmp_path / "ticket.json"
    doc = {"number": 3, "body": pr_body}
    if not paged:
        doc["files"] = [{"path": f} for f in files]
    pr.write_text(json.dumps(doc))
    ticket.write_text(json.dumps({"number": 7, "body": ticket_body, "labels": [{"name": n} for n in labels],
                                  "author": {"login": author}}))
    cmd = [sys.executable, str(CHECK), "check", "--pr-json", str(pr), "--ticket-json", str(ticket), "--project-dir", str(proj),
           "--plugin-root", str(ROOT), "--repo", repo, "--json", *extra]
    if paged:
        fj = tmp_path / "files.json"
        fj.write_text(json.dumps([{"path": f} for f in files]))
        cmd += ["--files-json", str(fj)]
    cmd += ["--base-dir", str(base if base is not None else proj)]       # gv-20: the CLI refuses to run without a base checkout
    if today:
        cmd += ["--today", today]
    env = dict(os.environ, PATH=str(tmp_path / "ghbin") + os.pathsep + os.environ["PATH"], GH_LOG=str(tmp_path / "gh.log"))
    env.pop("FOUNDRY_WORKSPACE_REPO", None)
    env.update(env_extra or {})
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
OTHER_SHA = "c" * 40
MISSING_SHA = "b" * 40
FORK_SHA = "d" * 40
UNMERGED_SHA = "9" * 40
HEAD_SHA = "e" * 40
TMPL = ROOT / "governance" / "kits" / "delivery" / "templates" / "governance-trace.yml.tmpl"


def load_gt():
    import importlib.util
    sys.dont_write_bytecode = True                  # never leave a .pyc inside the kit directory
    spec = importlib.util.spec_from_file_location("gt", CHECK)
    gt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gt)
    return gt


@pytest.fixture
def ws_proj(proj):
    """A product repo o/r governed from the workspace o/ws (binding file source), and a `gh` that knows the workspace commits."""
    (proj / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "o", "planning_repo": "o/ws", "repos": ["o/r"], "operators": {"alice": "op_a"}}}))
    return proj


def _commit_reply(sha):
    return f"echo '{{\"sha\": \"{sha}\"}}'"


@pytest.fixture
def ws_gh(tmp_path, gh_path):
    """GOOD and OTHER are on the workspace default branch (ahead/identical), FORK exists but is diverged, HEAD is the PR head commit of o/r (its JSON reply is the file $HEAD_JSON)."""
    g = gh_path / "gh"
    lines = ["#!/bin/sh", 'echo "$@" >> "$GH_LOG"', 'case "$*" in']
    for sha in (GOOD_SHA, OTHER_SHA, FORK_SHA, UNMERGED_SHA):
        lines.append(f"  'api repos/o/ws/commits/{sha}') {_commit_reply(sha)};;")
    lines += [
        "  'api repos/o/ws') echo '{\"default_branch\": \"main\"}';;",
        f"  'api repos/o/ws/compare/{GOOD_SHA}...main') echo '{{\"status\": \"ahead\", \"behind_by\": 0}}';;",
        f"  'api repos/o/ws/compare/{UNMERGED_SHA}...main') echo '{{\"status\": \"behind\"}}';;",
        f"  'api repos/o/ws/compare/{OTHER_SHA}...main') echo '{{\"status\": \"identical\"}}';;",
        f"  'api repos/o/ws/compare/{FORK_SHA}...main') echo '{{\"status\": \"diverged\"}}';;",
        "  *repos/o/ws/*) exit 1;;",
        f"  'api repos/o/r/commits/{HEAD_SHA}') cat \"$HEAD_JSON\";;",
        "  'api user') echo '{\"login\": \"agent-bot\"}';;",
        "  *) echo '{\"permission\": \"read\"}';;", "esac"]
    g.write_text("\n".join(lines) + "\n")
    g.chmod(0o755)
    return g


def built_from(doc):
    (f,) = by_check(doc)["built-from"]
    return f


def test_built_from_passes_with_a_verified_trailer(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\n\nBuilt-from: {GOOD_SHA}\n")
    assert built_from(doc)["ok"] is True and GOOD_SHA in built_from(doc)["detail"]
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\n\nBuilt-from: {OTHER_SHA}\n")      # identical to the default branch tip
    assert built_from(doc)["ok"] is True


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


def test_built_from_sha_not_on_the_default_branch_fails(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {FORK_SHA}\n")
    assert built_from(doc)["ok"] is False and "not an ancestor" in built_from(doc)["detail"]


def test_built_from_a_behind_reply_fails(ws_proj, ws_gh, tmp_path):
    """`behind` means the commit is ahead of the default branch (unmerged or fork-only): not an ancestor."""
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {UNMERGED_SHA}\n")
    assert built_from(doc)["ok"] is False and "not an ancestor" in built_from(doc)["detail"]


def test_strip_quoted_nested_fence_indented_code_and_html_comment():
    gt = load_gt()
    body = (f"````\n```\nBuilt-from: {MISSING_SHA}\n```\nBuilt-from: {MISSING_SHA}\n````\n"   # the inner ``` does not close the outer
            f"    Built-from: {MISSING_SHA}\n"                                                  # indented code block
            f"<!-- Built-from: {MISSING_SHA}\n-->\nBuilt-from: {GOOD_SHA}\n")
    assert gt._built_from_values(body) == [GOOD_SHA]
    assert gt._built_from_values(f"<!-- Built-from: {GOOD_SHA}") == []                           # unterminated comment hides the rest


def test_built_from_two_trailers_must_agree(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {GOOD_SHA}\nBuilt-from: {OTHER_SHA}\n")
    assert built_from(doc)["ok"] is False and "disagree" in built_from(doc)["detail"]
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {GOOD_SHA}\nBuilt-from: {GOOD_SHA}\n")
    assert built_from(doc)["ok"] is True


def test_built_from_ignores_fenced_and_quoted_lines(ws_proj, ws_gh, tmp_path):
    body = f"Ticket: #7\n> Built-from: {OTHER_SHA}\n```\nBuilt-from: {MISSING_SHA}\n```\nBuilt-from: {GOOD_SHA}\n"
    p, doc = run(ws_proj, tmp_path, pr_body=body)
    assert built_from(doc)["ok"] is True and GOOD_SHA in built_from(doc)["detail"]
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\n> Built-from: {GOOD_SHA}\n")           # a quote alone is no trailer
    assert built_from(doc)["ok"] is False and "no `Built-from:" in built_from(doc)["detail"]


def test_built_from_reads_the_head_commit_through_the_cli(ws_proj, ws_gh, tmp_path):
    def head_commit(sha):
        (tmp_path / "head.json").write_text(json.dumps({"commit": {"message": f"feat: x\n\nBuilt-from: {sha}\n"}}))
    head_commit(GOOD_SHA)
    extra = ("--head-sha", HEAD_SHA)
    env = {"HEAD_JSON": str(tmp_path / "head.json")}
    p, doc = run(ws_proj, tmp_path, extra=extra, env_extra=env)             # the body has no trailer; the head commit message does
    assert built_from(doc)["ok"] is True and GOOD_SHA in built_from(doc)["detail"]
    assert f"repos/o/r/commits/{HEAD_SHA}" in (tmp_path / "gh.log").read_text()
    head_commit(OTHER_SHA)                       # body and head commit now disagree
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {GOOD_SHA}\n", extra=extra, env_extra=env)
    assert built_from(doc)["ok"] is False and "disagree" in built_from(doc)["detail"]
    p, doc = run(ws_proj, tmp_path, extra=("--head-sha", "f" * 40), env_extra=env)      # an unreadable head commit fails closed
    assert built_from(doc)["ok"] is False and "could not be read" in built_from(doc)["detail"]


def test_built_from_single_repo_skips_with_a_named_reason(proj, tmp_path):
    p, doc = run(proj, tmp_path)                                           # o/r is not in the binding: single-repo
    f = built_from(doc)
    assert f["ok"] is True and f["detail"].startswith("skipped: single-repo")


def test_built_from_arms_from_the_variable_with_no_binding_file(ws_gh, tmp_path):
    """The documented multi-repo layout: the product repo has no .claude/foundry-project.json."""
    base = tmp_path / "base"
    (base / ".foundry").mkdir(parents=True)
    ok = dict(env_extra={"FOUNDRY_WORKSPACE_REPO": "o/ws"})
    p, doc = run(base, tmp_path, pr_body="Ticket: #7\n", **ok)
    assert built_from(doc)["ok"] is False and "no `Built-from:" in built_from(doc)["detail"]
    p, doc = run(base, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {GOOD_SHA}\n", **ok)
    assert built_from(doc)["ok"] is True
    p, doc = run(base, tmp_path, env_extra={"FOUNDRY_WORKSPACE_REPO": "o/r"})        # the workspace itself
    assert built_from(doc)["detail"].startswith("skipped: this repository is the workspace")


def test_built_from_with_neither_source_is_a_named_skip(ws_gh, tmp_path):
    base = tmp_path / "base"
    (base / ".foundry").mkdir(parents=True)
    p, doc = run(base, tmp_path)
    assert built_from(doc)["ok"] is True and built_from(doc)["detail"].startswith("skipped: single-repo")
    p, doc = run(base, tmp_path, env_extra={"FOUNDRY_WORKSPACE_REPO": "unset"})      # what the template passes for an undefined variable
    assert built_from(doc)["detail"].startswith("skipped: single-repo")


@pytest.mark.parametrize("value", ["", "  ", "not-a-slug", "a/b/c"])
def test_built_from_a_set_but_bad_variable_fails_never_skips(ws_gh, tmp_path, value):
    base = tmp_path / "base"
    (base / ".foundry").mkdir(parents=True)
    p, doc = run(base, tmp_path, env_extra={"FOUNDRY_WORKSPACE_REPO": value})
    assert built_from(doc)["ok"] is False and "empty or malformed" in built_from(doc)["detail"]


def test_built_from_an_unreadable_binding_fails_never_skips(ws_gh, tmp_path):
    base = tmp_path / "base"
    (base / ".claude").mkdir(parents=True)
    (base / ".claude" / "foundry-project.json").write_text("{not json")
    p, doc = run(base, tmp_path)
    assert built_from(doc)["ok"] is False and "cannot be read" in built_from(doc)["detail"]


def test_built_from_is_skipped_in_the_workspace_repo_itself(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, repo="o/ws")
    assert built_from(doc)["detail"].startswith("skipped: single-repo")


def test_built_from_makes_the_workspace_calls_and_no_others(ws_proj, ws_gh, tmp_path):
    p, doc = run(ws_proj, tmp_path, pr_body=f"Ticket: #7\nBuilt-from: {GOOD_SHA}\n")
    log = (tmp_path / "gh.log").read_text().splitlines()
    ws_calls = [ln for ln in log if "repos/o/ws" in ln]
    assert ws_calls == [f"api repos/o/ws/commits/{GOOD_SHA}", "api repos/o/ws", f"api repos/o/ws/compare/{GOOD_SHA}...main"]


def test_the_workspace_token_swap_sets_gh_token_and_falls_back(monkeypatch):
    gt = load_gt()
    seen = []

    def fake_run(cmd, **kw):
        seen.append(kw["env"].get("GH_TOKEN"))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("GH_TOKEN", "job-token-value")
    monkeypatch.setenv("FOUNDRY_WORKSPACE_READ_TOKEN", "workspace-token-value")
    gt._workspace_gh(gt._default_gh_json, ["api", "repos/o/ws"])
    assert seen[-1] == "workspace-token-value"
    monkeypatch.delenv("FOUNDRY_WORKSPACE_READ_TOKEN")
    gt._workspace_gh(gt._default_gh_json, ["api", "repos/o/ws"])
    assert seen[-1] == "job-token-value"                                    # unset: the job token is used
    monkeypatch.setenv("FOUNDRY_WORKSPACE_READ_TOKEN", "")
    gt._workspace_gh(gt._default_gh_json, ["api", "repos/o/ws"])
    assert seen[-1] == "job-token-value"                                    # empty: also the fallback


def test_the_tmpl_template_is_the_one_the_kit_lists():
    assert TMPL.is_file()
    assert "governance-trace.yml.tmpl" in (ROOT / "governance" / "kits" / "delivery" / "rules.yaml").read_text()
    kit = yaml.safe_load((ROOT / "governance" / "kits" / "delivery" / "kit.yaml").read_text())
    assert "templates/governance-trace.yml.tmpl" in kit["checks"] and "templates/governance-trace.yml.tmpl" in kit["templates"]
    assert not (ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.yml.template").exists()
    assert "FOUNDRY_WORKSPACE_READ_TOKEN" in TMPL.read_text() and "FAILS CLOSED" in TMPL.read_text()
    text = TMPL.read_text()
    assert "--json number,body > pr.json" in text and '--head-sha "$HEAD_SHA"' in text
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in text and "${{ github.event" not in text.split("run: >-")[1]
    assert "FOUNDRY_WORKSPACE_REPO: ${{ vars.FOUNDRY_WORKSPACE_REPO ||" in text
    assert "contents: read only" in text and "protected environment" in text
    assert "ref: <PLUGIN_REF>" in text                       # the placeholder an adopter must replace; the installer does not (a known kit gap)
    uses = re.findall(r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*(\S+)", text, re.M)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses)


def test_the_installed_workflow_still_carries_the_plugin_ref_placeholder():
    """The installer copies the snippet verbatim, so `<PLUGIN_REF>` survives into the adopter's workflow: the gap stays visible."""
    spec = importlib.util.spec_from_file_location("fg", GOV)
    fg = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(fg)
    kits = fg.load_kits(ROOT)
    plan = fg.snippet_plan(ROOT, kits, ["delivery"])
    (w,) = [x for x in plan if x["dest"] == ".github/workflows/governance-trace.yml"]
    assert "<PLUGIN_REF>" in w["text"]


def test_dispatch_skill_instructs_the_learned_comment_and_the_trailer_rule():
    text = (ROOT / "skills" / "dispatch" / "SKILL.md").read_text()
    assert "## Retrospective" in text and "at most ten lines" in text
    assert "Built-from: <workspace sha>" in text
    prompt = text.split("`prompt`: the worker contract")[1].split("3. **Consume")[0]
    quoted = "\n".join(ln for ln in prompt.splitlines() if ln.lstrip().startswith(">"))
    assert "Built-from: <workspace sha>" in quoted and "## Retrospective" in quoted and "workspace main sha" in quoted
