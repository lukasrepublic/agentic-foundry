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


def test_the_routed_documentation_notes_are_present():
    policy = read("docs/operating-model-policy.md")
    assert "non-interactive commands only" in policy.lower() and "controlling terminal" in policy
    assert "Never persist an allow for the `foundry_prod_step.py` row" in policy
    assert "tzdata" in policy and "bad-timezone" in policy
    governance = read("docs/governance.md")
    assert "GH_TOKEN" in governance and "fails closed" in governance and "declarations-pending" in governance
    log = read("docs/how-to/shared-message-log.md")
    assert "not tamper-evident" in log and "foundry/messages" in log and "orphan branch" in log
    assert "tamper-evident, and can be" not in log
    changelog = read("CHANGELOG.md")
    assert "is a no-op until the label-scoped sweep lands" not in changelog
    assert "by project label only" in changelog


def test_the_handoff_page_states_there_is_no_pdf_skill():
    text = read("docs/how-to/operator-handoff-files.md")
    assert "report.handoff_dir" in text and "No PDF skill" in text
    assert not list((ROOT / "skills").glob("*pdf*")), "a PDF skill now exists: update the hand-off page"
