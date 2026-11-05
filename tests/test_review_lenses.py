"""gv-16: the reviewer personas and the dispatch step that carry the design review and the conformance lens.

Structure assertions on the Markdown (headings, bullets, front matter, the plugin registry), not substring scans of joined text.
"""
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def front_matter(path: Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.S)
    return yaml.safe_load(m.group(1))


def sections(path: Path) -> dict:
    out, cur = {}, None
    for line in path.read_text().splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def bullets(text: str) -> list:
    items, cur = [], None
    for line in text.splitlines():
        if re.match(r"^- ", line):
            cur = line[2:]
            items.append(cur)
        elif cur is not None and line.startswith("  "):
            items[-1] += " " + line.strip()
    return items


def test_design_reviewer_is_a_registered_read_only_persona():
    p = ROOT / "agents" / "design-reviewer.md"
    fm = front_matter(p)
    assert fm["name"] == "design-reviewer" and fm["model"] == "sonnet"
    assert [t.strip() for t in fm["tools"].split(",")] == ["Read", "Grep", "Glob"]
    assert "./agents/design-reviewer.md" in json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["agents"]
    secs = sections(p)
    assert {"What to review", "How to report", "Write scope (least agency)"} <= set(secs)
    assert "write-paths: none" in secs["Write scope (least agency)"]
    review = {re.match(r"\*\*(.+?)\*\*", b).group(1).rstrip("."): b for b in bullets(secs["What to review"])}
    assert list(review) == ["Conformance to cited ids", "Missing ids", "Unknown ids", "Components", "Reused pattern", "Security", "Surfaces"]
    # each bullet states its own severity: the unknown-id, components, security and surfaces bullets are Blocks
    for name in ("Unknown ids", "Components", "Security", "Surfaces", "Conformance to cited ids"):
        assert "Block" in review[name], name
    assert "Risk" in review["Reused pattern"] and "Block" not in review["Reused pattern"]
    assert "floor rule" in review["Missing ids"] and "Risk" in review["Missing ids"]
    assert "verdict" in secs["How to report"] and "Block" in secs["How to report"]


def test_pr_reviewer_gains_the_conformance_flag_cleanup_and_rollout_lenses():
    secs = sections(ROOT / "agents" / "pr-reviewer.md")
    lens = {re.match(r"\*\*(.+?)\*\*", b).group(1): b for b in bullets(secs["Governance lenses"])}
    assert set(lens) == {"Conformance lens", "Flag-cleanup lens", "Rollout lens"}
    conf = lens["Conformance lens"]
    # the three Block conditions of the conformance lens
    assert conf.count("**Block**") == 3
    for needle in ("deviates from a cited id", "governed path", "Surfaces"):
        assert needle in conf
    flag, rollout = lens["Flag-cleanup lens"], lens["Rollout lens"]
    assert "FG-DLV-008" in flag and "**Risk**" in flag and "**Nit**" in flag and "**Block**" not in flag
    assert "FG-OPS-008" in rollout and "**Risk**" in rollout and "**Block**" in rollout
    assert "foundry_brief.py render" in secs["The rule block you receive"]


def test_security_reviewer_triggers_on_declared_boundaries_and_gets_the_rule_block():
    secs = sections(ROOT / "agents" / "security-reviewer.md")
    trig = secs["When you are triggered, and the rule block you receive"]
    assert "trust_boundaries" in trig and "foundry_brief.py render" in trig and "Block" in trig


def test_dispatch_step_zero_runs_the_design_review_for_the_class():
    secs = sections(ROOT / "skills" / "dispatch" / "SKILL.md")
    steps = re.findall(r"^(\d+)\.\s+\*\*(.+?)\*\*", secs["Procedure (single ticket)"], re.M)
    assert steps[0][0] == "0" and steps[0][1].startswith("Design review")
    assert [s[0] for s in steps[:3]] == ["0", "1", "2"]
    body = secs["Procedure (single ticket)"]
    assert "foundry:design-reviewer" in body and "design_required" in body and "one word" in body


def _lens_refs():
    for kit in sorted((ROOT / "governance" / "kits").iterdir()):
        f = kit / "rules.yaml"
        if not f.is_file():
            continue
        for r in yaml.safe_load(f.read_text())["rules"]:
            chk = r.get("check") or {}
            if chk.get("kind") == "review-lens":
                yield kit.name, r["id"], chk.get("ref")


def test_the_conduct_kit_lens_ref_resolves_to_the_reviewer_persona():
    refs = {rid: ref for kit, rid, ref in _lens_refs() if kit == "conduct"}
    assert refs["FG-CND-002"] == "agents/pr-reviewer.md"
    assert (ROOT / refs["FG-CND-002"]).is_file()


# The exceptions kit still carries the same dangling ref the conduct kit had (outside gv-16's write boundary: reported as an
# open item). When that kit is fixed this set must shrink to empty, which this test forces.
KNOWN_DANGLING = {("exceptions", "FG-EXC-004")}


def test_every_review_lens_ref_resolves_except_the_known_dangling_one():
    dangling = {(kit, rid) for kit, rid, ref in _lens_refs() if ref and not (ROOT / ref).exists()}
    assert dangling == KNOWN_DANGLING


def test_the_lenses_the_kits_name_exist_in_the_persona():
    text = (ROOT / "agents" / "pr-reviewer.md").read_text()
    want = {"FG-QA-001": "quality", "FG-DLV-008": "delivery", "FG-OPS-008": "operations"}
    for rid, kit in want.items():
        assert rid in text
        rule = next(r for r in yaml.safe_load((ROOT / "governance" / "kits" / kit / "rules.yaml").read_text())["rules"] if r["id"] == rid)
        assert rule["check"] == {"kind": "review-lens", "ref": "agents/pr-reviewer.md"}
