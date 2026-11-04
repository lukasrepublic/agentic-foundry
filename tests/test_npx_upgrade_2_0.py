"""tests/test_npx_upgrade_2_0.py — the pytest half of feat-foundry-adoption-npx-upgrade-2-0.

AC-NPX-1  : the package set (two bins, one exact dependency, nothing vendored, no lifecycle scripts).
AC-NPX-10 : the shipped skills/post-upgrade/SKILL.md reads the schema-2 report's adoption fields.
AC-NPX-14 : docs/how-to/post-upgrade.md documents restore, repair and the rollback limits.

AC-NPX-2 ... -9 and -11 ... -13 are behavioural and live in cli/test/upgrade-2-0.test.mjs (node --test).
Both documents are PROSE, so AC-NPX-10 and -14 are checked structurally: sections are located by
heading and steps by list-item index, never by a substring over the joined text.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_DIR = REPO_ROOT / "cli"
CLI_UPDATE_DIR = REPO_ROOT / "cli-update"
SKILL = REPO_ROOT / "skills" / "post-upgrade" / "SKILL.md"
HOWTO = REPO_ROOT / "docs" / "how-to" / "post-upgrade.md"

# AC-NPX-10's closed procedure-step set, in the order the skill runs them.
CLOSED_STEPS = [
    "commit the updater's writes",
    "standing grants",
    "stack-profile relock",
    "truth pass",
    "retired-artifact cleanup",
    "branch garbage collection",
    "required-check review",
    "verification",
    "one pull request",
]
PHASES = ["marketplace-refresh", "plugin-update", "python-deps", "cleanup", "reinitialization"]
LIFECYCLE = ("preinstall", "install", "postinstall", "prepare")
EXACT_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


# ── markdown helpers: headings and list items, located structurally ─────────────────────────────


def _split_sections(text: str) -> list[dict]:
    """[{level, title, lines}] for every `#`..`###` heading, in order; `lines` run to the next heading
    of any level. Fenced code blocks never open a heading."""
    sections: list[dict] = []
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,3}) (.+?)\s*$", line)
        if m:
            sections.append({"level": len(m.group(1)), "title": m.group(2), "lines": []})
        elif sections:
            sections[-1]["lines"].append(line)
    return sections


def _section(sections: list[dict], starts: str) -> dict:
    hits = [s for s in sections if s["level"] in (2, 3) and s["title"].startswith(starts)]
    assert len(hits) == 1, f"expected exactly one '##'/'###' heading starting {starts!r}, found {[s['title'] for s in hits]}"
    return hits[0]


def _items(lines: list[str], marker: str) -> list[str]:
    """Top-level list items (`marker` is a regex such as r'\\d+\\.' or '-'): each item is its first line
    plus every following non-blank line, up to the next top-level item (or the end of the section)."""
    items: list[str] = []
    for line in lines:
        if re.match(rf"^{marker} ", line):
            items.append(re.sub(rf"^{marker} ", "", line))
        elif items and line.strip():
            items[-1] += " " + line.strip()
    return items


def _lead(item: str) -> str:
    """The leading bold phrase of a list item, lower-cased, without its trailing full stop."""
    m = re.match(r"^\*\*(.+?)\*\*", item)
    assert m, f"list item does not open with a bold title: {item[:80]!r}"
    return m.group(1).rstrip(".").strip().lower()


# ── AC-NPX-1 ────────────────────────────────────────────────────────────────────────────────────


def test_ac_npx_1_two_bins_exact_dependency_no_vendored_modules():
    cli = json.loads((CLI_DIR / "package.json").read_text())
    upd = json.loads((CLI_UPDATE_DIR / "package.json").read_text())

    # exactly two bins, one per package
    assert cli["bin"] == {"create-agentic-workspace": "bin/create-agentic-workspace.mjs"}
    assert upd["bin"] == {"update-agentic-workspace": "bin/update-agentic-workspace.mjs"}

    # the updater declares exactly one dependency: the bootstrap, at one exact version
    deps = upd.get("dependencies", {})
    assert list(deps) == ["create-agentic-workspace"], deps
    assert EXACT_SEMVER.fullmatch(deps["create-agentic-workspace"]), (
        f"not an exact semver (a range operator, tag or prefix): {deps['create-agentic-workspace']!r}"
    )
    for key in ("devDependencies", "optionalDependencies", "peerDependencies", "bundledDependencies", "bundleDependencies"):
        assert not upd.get(key), f"cli-update declares {key}"

    # the bootstrap's dependencies are absent or empty (and so is every other dependency kind)
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        assert cli.get(key, {}) == {}, f"cli declares {key}: {cli.get(key)}"

    # neither package runs code on install
    for name, pkg in (("cli", cli), ("cli-update", upd)):
        for hook in LIFECYCLE:
            assert hook not in pkg.get("scripts", {}), f"{name}/package.json carries a {hook} script"

    # no cli-update file is a copy of a cli/src module (by name or by content hash)
    src_files = [p for p in (CLI_DIR / "src").rglob("*") if p.is_file()]
    src_names = {p.name for p in src_files}
    src_hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in src_files}
    vendored = []
    for p in CLI_UPDATE_DIR.rglob("*"):
        if not p.is_file() or "node_modules" in p.parts:
            continue
        if p.name in src_names or hashlib.sha256(p.read_bytes()).hexdigest() in src_hashes:
            vendored.append(str(p.relative_to(REPO_ROOT)))
    assert vendored == [], f"cli-update vendors a copy of a cli/src module: {vendored}"

    # the check is not vacuous: the updater's bin sits beside real modules to be compared against
    assert src_files and (CLI_UPDATE_DIR / "bin" / "update-agentic-workspace.mjs").is_file()


# ── AC-NPX-10 ───────────────────────────────────────────────────────────────────────────────────


def test_ac_npx_10_post_upgrade_skill_reads_adoption_fields():
    sections = _split_sections(SKILL.read_text())

    # Preconditions: the only stops, located by heading and list-item index
    pre = _items(_section(sections, "Preconditions")["lines"], r"\d+\.")
    assert len(pre) == 5, f"expected five precondition items, found {len(pre)}"
    schema, stop, attention = pre[0], pre[1], pre[2]
    assert "schema_version" in schema and "is not 2" in schema and "run the updater first" in schema
    assert "outcome" in stop and "`refused`" in stop and "`partial`" in stop and "next_action" in stop
    assert stop.lstrip().startswith("**`outcome` is `refused` or `partial`**") and "stop" in stop
    assert attention.lstrip().startswith("**`outcome` is `attention-needed`**")
    assert "compatibility.diagnoses" in attention and "For the operator" in attention and "continue" in attention
    assert "do not stop" in attention

    # Procedure: every numbered step maps to exactly one member of the closed set, in order
    steps = _items(_section(sections, "Procedure")["lines"], r"\d+\.")
    titles = [_lead(s) for s in steps]
    assert titles == CLOSED_STEPS, f"procedure steps are not the closed set, in order: {titles}"
    for retired in ("requires_capabilities", "amendments backfill", "manifest"):
        assert not any(retired in t for t in titles), f"a retired step survives: {retired}"

    # the truth pass takes its worklist from adoption.instructions_pending; the PR states adoption.cutover verbatim
    truth = steps[titles.index("truth pass")]
    assert "adoption.instructions_pending" in truth and "worklist" in truth
    pr = steps[titles.index("one pull request")]
    assert "adoption.cutover" in pr and "verbatim" in pr
    # retired-artifact cleanup works from the new report's cleanup[] list
    assert "cleanup[]" in steps[titles.index("retired-artifact cleanup")]

    # the front matter still describes a schema-2 reader (and names no retired step)
    front = SKILL.read_text().split("---")[1]
    assert "name: post-upgrade" in front and "schema-2" in front
    assert "requires_capabilities" not in front


# ── AC-NPX-14 ───────────────────────────────────────────────────────────────────────────────────


def test_ac_npx_14_post_upgrade_howto_documents_restore_repair_and_rollback_limits():
    sections = _split_sections(HOWTO.read_text())

    # Restore before cutover: one list item per phase, each naming a non-empty undo
    restore = _section(sections, "Restore before cutover")
    items = _items(restore["lines"], "-")
    parsed = {}
    for item in items:
        m = re.match(r"^\*\*([a-z-]+)\*\* — (.+)$", item)
        assert m, f"restore item is not '**phase** — undo': {item[:80]!r}"
        assert m.group(1) not in parsed, f"phase listed twice: {m.group(1)}"
        parsed[m.group(1)] = m.group(2).strip()
    assert list(parsed) == PHASES, f"restore items are not the five phases in order: {list(parsed)}"
    for phase, undo in parsed.items():
        assert len(undo) >= 20, f"{phase}: the undo text is empty or a stub: {undo!r}"

    # Repair after cutover: names what is repaired forward instead of restored
    repair = " ".join(l.strip() for l in _section(sections, "Repair after cutover")["lines"] if l.strip())
    assert "forward" in repair and "restore" in repair and len(repair) > 80

    # Rollback limits: exactly the three statements, one list item each
    limits = _items(_section(sections, "Rollback limits")["lines"], "-")
    assert len(limits) == 3, limits
    assert "never installs a lower plugin version" in limits[0]
    assert "only on the clone that holds it" in limits[1] and ".foundry/retired/" in limits[1]
    assert "Nothing prunes the quarantine automatically" in limits[2]

    # the guide's description of the skill's steps names only members of AC-NPX-10's closed set
    judgement = _section(sections, "2. The judgement half")
    steps = _items(judgement["lines"], r"\d+\.")
    assert [_lead(s) for s in steps] == CLOSED_STEPS
