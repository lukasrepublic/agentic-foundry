"""gn-10: the slim command deck. Asserts on parsed structure, not substrings."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills/command-deck/SKILL.md"
TEMPLATE = ROOT / "skills/command-deck/tick-prompt.template.md"

RETIRED = ["foundry_command_deck_watch.py", "foundry_command_deck.py", "release.yaml",
           "state.yaml", "watcher", "wave", "status-reports", "fleet registry"]


def sections(text):
    """Map '## heading' / '### heading' -> body (up to the next heading)."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^#{2,3}\s+(.*)$", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def items(text, pattern):
    """List items with wrapped continuation lines joined; (marker, body) pairs."""
    out = []
    for line in text.splitlines():
        m = re.match(pattern, line)
        if m:
            out.append([m.group(1), m.group(2)])
        elif out and line.startswith("  ") and line.strip():
            out[-1][1] += " " + line.strip()
    return [tuple(o) for o in out]


def numbered(text):
    return [(int(n), b) for n, b in items(text, r"^(\d+)\.\s+(.*)$")]


def bullets_of(text):
    return [b for _, b in items(text, r"^(-)\s+(.*)$")]


STEP_KEYS = ["inbox", "PushNotification", "ack", "reconcile", "verify", "capacity", "summary"]
STEP_MARKERS = ["foundry_inbox.py check", "PushNotification", "Ack", "foundry-ticket.py reconcile",
                "verify claimed results", "within capacity", "done / running / waiting / decisions"]


def check_steps(items):
    assert [n for n, _ in items] == list(range(1, 8))
    for (n, body), marker in zip(items, STEP_MARKERS):
        assert marker in body, (n, marker)


def test_deck_frontmatter_parses():
    text = SKILL.read_text()
    assert text.startswith("---")
    end = text.find("\n---", 3)
    fm = yaml.safe_load(text[3:end])
    assert fm["name"] == "command-deck"
    assert isinstance(fm["description"], str) and "\n" not in fm["description"].strip()
    for sub in ("start", "status", "stop", "tick"):
        assert sub in fm["description"]


def test_deck_subcommand_table_exact():
    body = sections(SKILL.read_text())["Subcommands"]
    rows = [l for l in body.splitlines() if l.startswith("|")][2:]
    subs = [re.match(r"\|\s*`([^`]+)`", r).group(1) for r in rows]
    assert subs == ["start", "status", "stop", "tick"]


def test_deck_template_seven_steps_in_order():
    t = TEMPLATE.read_text()
    check_steps(numbered(t))


def test_deck_skill_tick_seven_steps_in_order():
    check_steps(numbered(sections(SKILL.read_text())["Tick"]))


def test_deck_template_placeholders_exact():
    assert sorted(set(re.findall(r"\{\{(\w+)\}\}", TEMPLATE.read_text()))) == [
        "NARROWING", "OPERATOR", "WORKSPACE"]


def test_deck_narrowing_step6_only():
    items = dict(numbered(sections(SKILL.read_text())["Tick"]))
    assert "narrowing applies here only" in items[6]
    assert all("narrowing" not in items[i] for i in (1, 2, 3, 4, 5, 7))


def test_deck_startup_disclosures():
    body = sections(SKILL.read_text())["Startup and resume disclosures"]
    bullets = bullets_of(body)
    assert len(bullets) == 4
    assert "CronList" in bullets[0] and "7 days" in bullets[0]
    assert "--resume" in bullets[1] and "re-arms" in bullets[1]
    assert "missed" in bullets[2] and "busy-delayed" in bullets[2] and "interrupted" in bullets[2]
    assert "last_full_check_at" in bullets[3]


def test_deck_stop_resume_reconcile_before_dispatch():
    bullets = bullets_of(sections(SKILL.read_text())["Stop and resume"])
    assert len(bullets) == 2
    assert "deletes the wake" in bullets[0] and "stops new dispatch" in bullets[0]
    assert "stay in place" in bullets[0]
    assert "foundry-ticket.py reconcile" in bullets[1] and "before any dispatch" in bullets[1]


def test_deck_lease_refusal_disclaimer():
    body = sections(SKILL.read_text())["Start"]
    steps = dict(numbered(body))
    assert "refuse to start" in steps[1]
    assert "local to this machine" in steps[1]
    for term in ("assignee", "draft PR", "`start` record"):
        assert term in steps[1]


def test_deck_degraded_rule():
    for text in (sections(SKILL.read_text())["Degraded operation"], TEMPLATE.read_text()):
        assert "dispatch_allowed: false" in text
        assert "decision_needed" in text
        assert "skip new" in text


def test_deck_cadence_is_target():
    assert "*/2 * * * *" in TEMPLATE.read_text()
    assert "target, not a deadline" in TEMPLATE.read_text()


@pytest.mark.parametrize("path", [SKILL, TEMPLATE], ids=lambda p: p.name)
@pytest.mark.parametrize("name", RETIRED)
def test_deck_retired_names_absent(path, name):
    assert name not in path.read_text().lower()
