"""dl-15: the ticket sections carry agile names (amendment d), and the old headings and keys stay readable.

New headings: `## Acceptance Criteria`, `## Definition of Done`, `## Referenced Artifacts`, `## Retrospective`.
Old aliases: `## Acceptance`, `## Done means`, `## Paper allowed`, `## Learned`. The section digest covers the
section text, never the heading spelling. Assertions are on parsed structure, not substrings.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_owned_queue import ft  # noqa: E402

import foundry_ticket_store as store  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AC = "- AC-1: WHEN a user logs in, THE SYSTEM SHALL show the widget.\nGrounding: EARS.\n"
DESIGN = "## Design\n- Components: scripts/a.py\n- Cites: FG-SEC-006\n\n## Security\n- FG-SEC-006\n\n## Surfaces\n- cli: reachable\n\n"
CMD = "```\npython3 -m pytest -q\n```\n"
PAPER = "CHANGELOG.md, docs/how-to/x.md\n"


def body(acc, done, paper):
    return f"## {acc}\n{AC}\n{DESIGN}## {done}\n{CMD}\n## {paper}\n{PAPER}"


NEW = body("Acceptance Criteria", "Definition of Done", "Referenced Artifacts")
OLD = body("Acceptance", "Done means", "Paper allowed")


def test_new_headings_parse_exactly_as_the_old_ones_did():
    assert ft.parse_body(NEW) == ft.parse_body(OLD)
    done, paper = ft.parse_body(NEW)
    assert done == "python3 -m pytest -q"
    assert paper == ["CHANGELOG.md", "docs/how-to/x.md"]
    assert ft.parse_acceptance(NEW) == ft.parse_acceptance(OLD)
    assert [b["id"] for b in ft.parse_acceptance(NEW)["bullets"]] == ["AC-1"]


@pytest.mark.parametrize("head", ["Definition of Done", "definition of done", "Done means", "DONE MEANS"])
def test_each_done_heading_spelling_yields_the_command(head):
    assert ft.parse_body(f"## {head}\n{CMD}")[0] == "python3 -m pytest -q"


@pytest.mark.parametrize("head", ["Referenced Artifacts", "Referenced Artifact", "Paper allowed"])
def test_each_paper_heading_spelling_yields_the_list(head):
    assert ft.parse_body(f"## {head}\nfoo.md\n")[1] == ["foo.md"]


def test_the_digest_does_not_change_when_only_the_heading_spelling_changes():
    d_new, parts_new = ft.ticket_digest(NEW)
    d_old, parts_old = ft.ticket_digest(OLD)
    assert d_new == d_old and parts_new == parts_old
    # a mixed spelling digests alike too
    assert ft.ticket_digest(body("Acceptance Criteria", "Done means", "Paper allowed")) == (d_old, parts_old)
    # and the sections are non-vacuous: they were really found, and a text change still flips the digest
    texts = ft.ticket_section_texts(NEW)
    assert texts["acceptance"].strip() and texts["done_means"].strip()
    assert ft.ticket_digest(NEW.replace("pytest -q", "pytest -x"))[0] != d_new


def test_the_digest_payload_keys_are_frozen_so_an_older_signed_claim_still_verifies():
    assert store.TICKET_SECTIONS == ("acceptance", "design", "security", "surfaces", "done_means")


def test_the_retrospective_heading_and_the_old_learned_heading_both_distil():
    dist = _load("foundry_learned_distill", "scripts/foundry_learned_distill.py")
    lines = ["friction: the guard was slow", "success: the hook held"]
    for head in ("Retrospective", "Learned"):
        assert dist._learned_lines(f"## {head}\n" + "\n".join(lines) + "\n## Other\nnot this") == lines


def test_record_keys_have_agile_names_and_the_old_keys_stay_readable():
    assert store.rec_get({"referenced_artifacts": ["a"], "paper_allowed": ["b"]}, "referenced_artifacts") == ["a"]
    assert store.rec_get({"paper_allowed": ["b"]}, "referenced_artifacts") == ["b"]
    assert store.rec_get({"done_means": "x"}, "definition_of_done") == "x"
    assert store.rec_get({}, "issue_referenced_artifacts", []) == []


def test_the_signature_over_the_lists_is_the_same_under_the_old_and_the_new_key():
    old = {"issue": 1, "repo": "o/r", "ci_allowed": [".github/x"], "issue_paper_allowed": ["docs"]}
    new = {"issue": 1, "repo": "o/r", "ci_allowed": [".github/x"], "issue_referenced_artifacts": ["docs"]}
    assert store._lists_payload(old) == store._lists_payload(new)


def test_the_paper_guard_reads_both_key_names():
    guard = _load("foundry_paper_guard", "hooks/foundry-paper-guard.py")
    for key in ("referenced_artifacts", "paper_allowed"):
        doc = {"issue": 1, key: ["docs"]}
        assert guard._allowed(Path("docs/a.md"), store.rec_get(doc, "referenced_artifacts") or [])
        assert not guard._allowed(Path("specs/a.md"), store.rec_get(doc, "referenced_artifacts") or [])


def test_the_view_uses_the_ready_check_and_acceptance_criteria_keys():
    view = ft.design_view(NEW, [], root=Path(__import__("os").environ.get("CLAUDE_PROJECT_DIR") or ROOT))
    assert view["acceptance_criteria"]["bullets"] and view["ready_check_reasons"] == []
    assert view["ready_check_required"] is False and view["design_required"] is view["ready_check_required"]   # old alias still emitted
    assert "acceptance_criteria" in view and "acceptance" not in view


def test_the_brief_reads_acceptance_criteria_and_the_old_acceptance_key():
    brief = _load("foundry_brief", "scripts/foundry_brief.py")
    src = (ROOT / "scripts" / "foundry_brief.py").read_text()
    assert 'ticket.get("acceptance_criteria")' in src and 'ticket.get("acceptance")' in src
    assert "Acceptance Criteria" in brief.ACCEPTANCE_HEADER


def _agent_front(name):
    text = (ROOT / "agents" / f"{name}.md").read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return dict(ln.split(": ", 1) for ln in m.group(1).splitlines() if ": " in ln)


@pytest.mark.parametrize("agent", ["design-reviewer", "ticket-reviewer"])
def test_both_reviewers_name_the_definition_of_ready_check(agent):
    assert "Definition of Ready check" in _agent_front(agent)["description"]


def test_the_dispatch_skill_names_the_gate_and_uses_the_new_headings():
    text = (ROOT / "skills" / "dispatch" / "SKILL.md").read_text()
    steps = [ln for ln in text.splitlines() if re.match(r"^0b?\. \*\*", ln)]
    assert len(steps) == 2 and all("Definition of Ready check" in ln for ln in steps)
    headings = set(re.findall(r"`## ([A-Za-z ]+)`", text))
    assert {"Definition of Done", "Referenced Artifacts", "Retrospective", "Acceptance Criteria"} <= headings
    assert not headings & {"Done means", "Paper allowed", "Learned", "Acceptance"}


def test_the_worker_brief_names_the_new_sections():
    js = (ROOT / "workflows" / "wave-dispatch.js").read_text()
    rule_lines = [ln for ln in js.splitlines() if "Retrospective rule:" in ln or "Claim-check rule:" in ln]
    assert len(rule_lines) == 2
    assert all("## Learned" not in ln and "Done means" not in ln for ln in rule_lines)
    assert any("## Retrospective" in ln for ln in rule_lines) and any("Definition of Done" in ln for ln in rule_lines)
    assert "ready_check_required" in js and "design_required" in js    # new key first, old key still read
