"""tests/test_governance_docs.py -- the governance pages (gv-17) say what ships.

Pages reference live files and never copy rule text, so the checks here are about references and coverage:
  * every policy key of the schema is on the policy page with the right floor/tunable kind;
  * the floor-rule table of docs/governance.md equals the floors the kits declare, with the same enforcing primitive;
  * every repository path a new page names exists, and every relative link resolves;
  * the vocabulary and command rules (no "bus", no retired substring, no `cd` in a documented command);
  * the new how-tos are linked from docs/README.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NEW_PAGES = [
    "docs/governance.md",
    "docs/operating-model-policy.md",
    "docs/how-to/scale-to-zero-privileged-jobs.md",
    "docs/how-to/ci-budgets-are-tools.md",
    "docs/how-to/evidence-baseline-rule.md",
    "docs/how-to/operator-handoff-files.md",
]
TOUCHED_PAGES = NEW_PAGES + ["docs/how-to/shared-message-log.md", "context/operating-model.md"]
# Paths that exist in an adopter workspace, not in this repository.
WORKSPACE_ONLY = ("governance/local", "governance/rendered", ".foundry/", "docs/runbooks", "scripts/foundry_om_trace.py --")
ROOTS = ("scripts/", "hooks/", "governance/", "skills/", "agents/", "cli/", "schema/", "tests/", "context/", "packs/", "docs/")
RETIRED = "c" + "t" + "x"                      # the retired three-letter substring, built from fragments


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def code_spans(text):
    return re.findall(r"`([^`\n]+)`", text)


def test_every_policy_key_is_on_the_policy_page_with_its_kind():
    schema = json.loads(read("schema/operating-model.schema.json"))
    page = read("docs/operating-model-policy.md")
    rows = {}
    for line in page.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) >= 6:
            rows[cells[0].strip("`")] = cells[1]
    expected = {}
    for sec, sv in schema["properties"].items():
        if sec == "schema_version":
            continue
        for key, spec in sv["properties"].items():
            expected[f"{sec}.{key}"] = "floor" if "const" in spec else "tunable"
    assert expected, "the schema yielded no keys: the check below would be vacuous"
    assert rows == expected, (sorted(set(expected) ^ set(rows)), {k: (rows.get(k), v) for k, v in expected.items() if rows.get(k) != v})


def test_the_floor_table_equals_the_floors_the_kits_declare():
    declared = {}
    for f in sorted((ROOT / "governance" / "kits").glob("*/rules.yaml")):
        for r in yaml.safe_load(f.read_text(encoding="utf-8"))["rules"]:
            if r.get("floor"):
                declared[r["id"]] = r["check"]["ref"]
    assert declared, "no floor rules found: the comparison would be vacuous"
    table = {}
    section = read("docs/governance.md").split("The floor rules, and the primitive that enforces each:", 1)[1].split("\n\n", 2)[1]
    for line in section.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0].startswith("FG-"):
            table[cells[0]] = cells[1]
    assert table == declared


def _repo_path_tokens(text):
    for tok in code_spans(text):
        first = tok.split()[0] if tok.split() else tok
        if first.startswith(ROOTS) and not first.endswith("/") and not any(w in tok for w in WORKSPACE_ONLY) \
                and not any(ch in first for ch in "<>*{}$") and "…" not in first:
            yield first


def test_every_repository_path_a_new_page_names_exists():
    checked = 0
    for rel in TOUCHED_PAGES:
        for tok in _repo_path_tokens(read(rel)):
            checked += 1
            assert (ROOT / tok).exists(), f"{rel} names {tok}, which does not exist"
    assert checked > 40, "far fewer path references than expected: the extraction is broken"


def test_every_directory_a_new_page_names_exists():
    for rel in NEW_PAGES:
        for tok in code_spans(read(rel)):
            if tok.startswith(ROOTS) and tok.endswith("/") and not any(w in tok for w in WORKSPACE_ONLY) and not any(c in tok for c in "<>*"):
                assert (ROOT / tok).is_dir(), f"{rel} names the directory {tok}, which does not exist"


def test_relative_links_resolve():
    link = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
    for rel in TOUCHED_PAGES + ["docs/README.md", "README.md"]:
        base = (ROOT / rel).parent
        for target in link.findall(read(rel)):
            if "://" in target or target.startswith("mailto:"):
                continue
            assert (base / target).resolve().exists(), f"{rel}: broken link {target}"


def test_vocabulary_and_command_rules():
    for rel in TOUCHED_PAGES + ["CHANGELOG.md"]:
        text = read(rel)
        assert not re.search(r"\bbus\b", text, re.I), f"{rel}: say 'the shared message log', never 'bus'"
        assert RETIRED not in text.lower(), f"{rel}: the retired substring appears"
    for rel in NEW_PAGES + ["docs/how-to/shared-message-log.md"]:
        for block in re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", read(rel), re.S):
            for line in block.splitlines():
                assert not re.match(r"\s*cd\s", line) and " && cd " not in line, f"{rel}: a documented command uses cd: {line}"


def test_the_new_how_tos_are_linked_from_the_docs_index():
    index = read("docs/README.md")
    for rel in NEW_PAGES:
        target = rel[len("docs/"):]
        assert f"]({target})" in index, f"docs/README.md does not link {target}"


def sections(text, level="## "):
    """{heading: body} for the headings of one level; the body runs to the next heading of that level."""
    parts = re.split(rf"(?m)^{re.escape(level)}(.+)$", text)
    return {parts[k].strip(): parts[k + 1] for k in range(1, len(parts) - 1, 2)}


def flat(s):
    return " ".join(s.split())


def test_the_routed_documentation_notes_are_in_their_sections():
    policy = sections(read("docs/operating-model-policy.md"))
    notes = flat(policy["Notes on the primitives"])
    assert "non-interactive commands only" in notes.lower() and "controlling terminal" in notes
    assert "Never persist an allow for the `foundry_prod_step.py` row" in notes
    assert "tzdata" in notes and "bad-timezone" in notes and "`auto`" in notes
    assert "does not add a D2 line" in notes
    assert "tzdata" not in flat(policy["Floor and tunable"])               # the note lives in its own section, not by accident elsewhere

    ticket = flat(sections(read("docs/governance.md"))["One ticket carries the rules to the agent"])
    assert "GH_TOKEN" in ticket and "fails closed" in ticket and "declarations-pending" in ticket and "`pending`" in ticket

    log = sections(read("docs/how-to/shared-message-log.md"))
    protection = flat(log["How far the protection goes"])
    assert re.search(r"not (?:caught|tamper-evident)", protection) and "fast-forward" in protection
    assert "history-rewritten" in protection and "planned" in protection
    assert "self-declared" in protection
    branch = flat(log["The branch that `queue-land` can create"])
    assert "orphan branch" in branch and "foundry/messages" in branch
    assert "--repo" in branch and "CLAUDE_PROJECT_DIR" in branch and branch.index("--repo") < branch.index("CLAUDE_PROJECT_DIR")
    assert "tamper-evident, and can be" not in read("docs/how-to/shared-message-log.md")

    top = sections(read("CHANGELOG.md"), "### ")
    head = next(v for k, v in top.items() if k.startswith("Foundry Governance, one operating mode"))
    hooks = flat(head.split("**Added: hooks.**", 1)[1].split("**Changed", 1)[0])
    assert "by project label only" in hooks and "is a no-op until" not in hooks


# The "Acts through" value of every key, pinned here against the code. A change of behavior must change this table and the page.
ACTS_THROUGH = {
    "pr_strategy.granularity": "prompt", "pr_strategy.one_pr_per_hot_file_per_round": "prompt",
    "merge.queue": "script", "merge.requeue_budget_outside_diff": "const floor", "merge.full_suite_on_branch": "script",
    "merge.record_line_before_enqueue": "const floor", "merge.human_review_required": "script",
    "deck.max_builders": "script", "deck.heavy_slots": "script", "deck.resource_check": "const floor",
    "deck.workflow_size_source": "const floor",
    "decisions.research_first": "const floor", "decisions.escalation_set": "key only",
    "claims.enabled": "script", "claims.expires_after": "script",
    "cost.weigh_in_every_decision": "const floor", "cost.priced_ask_for_always_on": "const floor",
    "report.format": "prompt", "report.timezone": "script", "report.plain_language": "prompt", "report.max_lines": "script",
    "report.include_ids": "script", "report.handoff_dir": "key only",
    "communication.style": "script", "communication.operator_style_file": "prompt",
    "models.builder": "prompt", "models.reviewer": "prompt", "models.security_reviewer": "prompt",
    "hygiene.disk_sweep_each_tick": "key only", "hygiene.teardown_required": "const floor",
    "secrets.value_reads_forbidden": "const floor", "secrets.forbidden_verbs": "script", "secrets.no_print_roots": "script",
    "messaging.machine_channel": "key only", "messaging.people_channel": "key only", "messaging.chat_mirror": "key only",
    "messaging.open_change_minutes": "script",
    "governance.design_required_paths": "script", "governance.size_tag_required": "script",
}
# A "script" key must be read by a named file; a "key only" key by none (the policy module and its template excluded).
READERS = {
    "merge.queue": "scripts/foundry_queue_land.py", "merge.full_suite_on_branch": "scripts/foundry_queue_land.py",
    "merge.human_review_required": "scripts/foundry_queue_land.py", "deck.heavy_slots": "scripts/foundry_deck_resources.py",
    "claims.enabled": "scripts/foundry_ticket_store.py", "claims.expires_after": "scripts/foundry_ticket_store.py",
    "report.timezone": "scripts/foundry_deck_report.py", "report.max_lines": "scripts/foundry_deck_report.py",
    "report.include_ids": "scripts/foundry_deck_report.py", "secrets.forbidden_verbs": "hooks/foundry-secret-read-guard.py",
    "secrets.no_print_roots": "hooks/foundry-secret-read-guard.py", "messaging.open_change_minutes": "scripts/foundry_message_log.py",
    "governance.design_required_paths": "scripts/foundry-ticket.py", "governance.size_tag_required": "scripts/foundry-ticket.py",
}


def test_acts_through_is_pinned_per_key():
    page = read("docs/operating-model-policy.md")
    rows = {}
    for line in page.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) >= 6:
            rows[cells[0].strip("`")] = (cells[1], cells[4])
    schema = json.loads(read("schema/operating-model.schema.json"))
    floors = {f"{s}.{k}" for s, sv in schema["properties"].items() if s != "schema_version" for k, v in sv["properties"].items() if "const" in v}
    assert {k: v[1] for k, v in rows.items()} == ACTS_THROUGH
    assert {k for k, v in ACTS_THROUGH.items() if v == "const floor"} == floors, "every floor row, and only a floor row, is a const floor"
    assert all(rows[k][0] == "floor" for k in floors)
    for key, rel in READERS.items():
        assert f'"{key}"' in read(rel), f"{key} is not read by {rel}"
    # a "key only" key is read by no program (the policy module and its seed are the definition, not a consumer)
    skip = {"scripts/foundry_operating_model.py"}
    for key, how in ACTS_THROUGH.items():
        if how != "key only":
            continue
        for d in ("scripts", "hooks"):
            for f in (ROOT / d).iterdir():
                if f.is_file() and f"scripts/{f.name}" not in skip and f.suffix in (".py", ".sh"):
                    assert f'"{key}"' not in f.read_text(encoding="utf-8", errors="replace"), f"{key} is read by {f.name}: not 'key only'"
    chat = read("cli/templates/workflows/message-log-chat-mirror.yml")
    assert "MESSAGE_LOG_CHAT_MIRROR" in chat and "messaging.chat_mirror" not in chat.split("jobs:", 1)[1]


def test_the_handoff_page_states_there_is_no_pdf_skill():
    text = read("docs/how-to/operator-handoff-files.md")
    assert "report.handoff_dir" in text and "No PDF skill" in text
    assert not list((ROOT / "skills").glob("*pdf*")), "a PDF skill now exists: update the hand-off page"


def test_the_pages_agree_on_sixteen_categories_and_fifteen_kits():
    kits = [p for p in (ROOT / "governance" / "kits").iterdir() if p.is_dir()]
    assert len(kits) == 15 and not (ROOT / "governance" / "kits" / "factory").exists()
    for rel in ("docs/governance.md", "context/operating-model.md", "CHANGELOG.md"):
        text = read(rel)
        if rel == "CHANGELOG.md":
            text = text.split("### One artifact per change", 1)[0]
        flat_text = flat(text.lower())
        assert "sixteen categories" in flat_text or "sixteenth" in flat_text, rel
        assert "fifteen" in flat_text and "`factory`" in text, rel
