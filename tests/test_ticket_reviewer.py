"""dl-02: the ticket-reviewer agent and dispatch step 0b."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agents" / "ticket-reviewer.md"
SKILL = ROOT / "skills" / "dispatch" / "SKILL.md"


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    return dict(
        (k.strip(), v.strip())
        for k, v in (ln.split(":", 1) for ln in m.group(1).splitlines() if ":" in ln)
    )


def test_agent_frontmatter():
    fm = _frontmatter(AGENT.read_text())
    assert fm["name"] == "ticket-reviewer"
    assert fm["model"] == "sonnet"
    assert {t.strip() for t in fm["tools"].split(",")} == {"Read", "Grep", "Glob"}
    assert fm["description"]


def test_agent_covers_both_lenses_and_output_contract():
    body = _flat(AGENT.read_text())
    for needle in ("steel-man", "adversarial", "## Acceptance", "## Design",
                   "Block", "Risk", "Nit", "locator", "fail open", "vacuous"):
        assert needle in body, needle
    assert "A Block without a locator is a Risk" in body


def test_agent_prompt_has_no_edit_run_or_merge_instruction():
    body = AGENT.read_text()
    assert "write-paths: none" in body
    # the only allowed mentions are negations; no imperative to act
    for bad in (r"\byou (?:should|must|may) (?:edit|run|merge|write|execute)\b",
                r"\b(?:please )?(?:run|execute|merge) the\b",
                r"\bgh pr merge\b", r"\bgit push\b"):
        assert not re.search(bad, body, re.I), bad
    assert "never merge" in body or "do not approve, gate, or merge" in body


def test_agent_avoids_banned_vocabulary():
    low = AGENT.read_text().lower()
    for w in ("cluster", "tracking issue"):
        assert w not in low


def test_skill_step_0b_present_after_step_0_before_resolve():
    body = SKILL.read_text()
    i0 = body.index("\n0. **Design review")
    i0b = body.index("\n0b.")
    i1 = body.index("\n1. **Resolve**")
    assert i0 < i0b < i1
    step = _flat(body[i0b:i1])
    for needle in ("ticket-reviewer", "foundry:ticket-reviewer", "design_required",
                   "read-issue", "--json", "foundry_brief.py render", "review:<atom>",
                   "Deliverable:", "Done when:", "one fix round", "Blocks only change the ticket",
                   "park", "Risks"):
        assert needle in step, needle
    assert "skipped" in step or "skip" in step


def test_skill_keeps_dl07_lines():
    body = SKILL.read_text()
    assert "Built-from: <workspace sha>" in body
    assert "## Learned" in body
