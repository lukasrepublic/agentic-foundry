"""gn-10: the slim command deck. Asserts on parsed structure, not substrings."""
import re
import subprocess
import sys
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


STEP_MARKERS = ['foundry_inbox.py" check', "PushNotification", "Ack", 'foundry-ticket.py" reconcile',
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
    for sub in ("start", "status", "stop", "tick", "pause", "resume"):
        assert sub in fm["description"]


def test_deck_subcommand_table_exact():
    body = sections(SKILL.read_text())["Subcommands"]
    rows = [l for l in body.splitlines() if l.startswith("|")][2:]
    subs = [re.match(r"\|\s*`([^`\s]+)", r).group(1) for r in rows]
    assert subs == ["start", "status", "stop", "tick", "pause", "resume"]   # gv-13 added pause and resume (B9)


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
    assert re.search(r"narrowing above applies here only", items[6])
    assert all("narrowing" not in items[i] for i in (1, 2, 3, 4, 5, 7))


def test_deck_startup_disclosures():
    body = sections(SKILL.read_text())["Startup and resume disclosures"]
    bullets = bullets_of(body)
    assert len(bullets) == 4
    assert "CronList" in bullets[0] and "7 days" in bullets[0]
    assert "--resume" in bullets[1] and "check `CronList`; re-arm only if the wake is missing, expired" in bullets[1]
    assert "never" in bullets[1] and "double-arm" in bullets[1]
    assert "missed" in bullets[2] and "busy-delayed" in bullets[2] and "interrupted" in bullets[2]
    assert "last_full_check_at" in bullets[3]


def test_deck_stop_resume_reconcile_before_dispatch():
    bullets = bullets_of(sections(SKILL.read_text())["Stop and resume"])
    assert len(bullets) == 2
    assert "deletes the wake" in bullets[0] and "stops new dispatch" in bullets[0]
    assert "stay in place" in bullets[0]
    assert "lead release" in bullets[0]
    assert "lead acquire" in bullets[1]
    assert "foundry-ticket.py\" reconcile" in bullets[1] and "before any dispatch" in bullets[1]


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
    assert not re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", path.read_text().lower())


CMD = re.compile(r'python3 "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([\w.-]+)" ([a-z][\w-]*)')


def cited_commands():
    out = set()
    for path in (SKILL, TEMPLATE):
        out |= set(CMD.findall(path.read_text()))
    return sorted(out)


def test_deck_every_command_uses_full_path_form():
    """In the operative text (Start, Tick, Stop and the template) every script is invoked in full form."""
    sec = sections(SKILL.read_text())
    texts = [sec["Start"], sec["Tick"], sec["Stop and resume"], TEMPLATE.read_text()]
    prefix = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/'
    for text in texts:
        for m in re.finditer(r"foundry[_-][\w-]+\.py", text):
            assert text[max(0, m.start() - len(prefix)):m.start()] == prefix, (m.group(0), text[m.start() - 30:m.end()])


@pytest.mark.parametrize("script,verb", cited_commands(), ids=lambda x: str(x))
def test_deck_cited_verbs_exist(script, verb):
    py = ROOT / "scripts" / script
    assert py.is_file()
    # foundry-ticket.py verbs are hand-dispatched (`done`/`claim` execute rather than parse --help), so
    # its verbs are checked against the top-level usage; foundry_inbox.py has real argparse subcommands.
    argv = [str(py), "--help"] if script == "foundry-ticket.py" else [str(py), verb, "--help"]
    r = subprocess.run([sys.executable, *argv], capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, (script, verb, r.stderr[-300:])
    if script == "foundry-ticket.py":
        assert re.search(r"foundry-ticket\.py " + re.escape(verb) + r"\b", r.stdout), (verb, r.stdout[-300:])


def test_deck_cited_verbs_cover_the_tick():
    cmds = set(cited_commands())
    for c in [("foundry_inbox.py", v) for v in ("check", "pending", "ack", "schedule")] + [
            ("foundry-ticket.py", v) for v in ("lead", "reconcile", "done", "next", "claim")]:
        assert c in cmds, c


def test_deck_lease_renew_per_tick_and_release():
    for text in (numbered(sections(SKILL.read_text())["Tick"]), numbered(TEMPLATE.read_text())):
        step1 = dict(text)[1]
        assert step1.index("lead renew") < step1.index("foundry_inbox.py\" check")
        assert "exits non-zero" in step1 and "STOP this tick" in step1
        assert "lead renew" in dict(text)[6]
    start = dict(numbered(sections(SKILL.read_text())["Start"]))
    assert "lead acquire" in start[1]
    table = sections(SKILL.read_text())["Subcommands"]
    assert "lead check" in table and "lead release" in table


def test_deck_escalation_dedupe_and_queue_status():
    for text in (numbered(sections(SKILL.read_text())["Tick"]), numbered(TEMPLATE.read_text())):
        d = dict(text)
        assert "ONE `PushNotification` per NEW" in d[2]
        assert "(request_url, affected.url)" in d[2]
        assert "already-notified keys" in d[2] and "in this session" in d[2]
        assert "not re-pushed" in d[2] and "material change" in d[2]
        assert "event_emitted: false" in d[2] and "ack could not be posted" in d[2]
        assert "--queue-status <ok|error|stale>" in d[1]
        for v in ("ok", "error", "stale"):
            assert f"`{v}`" in d[1]
        assert "ack <url>" in d[3] and "schedule <url> --target <when>" in d[3]
        assert "URLs only" in d[5] and "foundry_deck_events" in d[5] and "foundry-ticket.py\" done" in d[5]
        assert "dispatch_allowed` is true and `lead renew` succeeded" in d[6]
        assert d[6].index("claim <issue>") < d[6].index("skills/dispatch/SKILL.md")


def test_deck_permission_tier_disclosed():
    body = SKILL.read_text()
    assert "ask-tier" in body and "docs/how-to/standing-grants.md" in body
    assert "foundry_inbox.py scan" in body and "never records events" in body
