"""dl-10: scripts/foundry_learned_distill.py (offline; GitHub never called)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foundry_learned_distill.py"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_learned_distill as ld  # noqa: E402
import foundry_governance as gov  # noqa: E402


def comment(*lines):
    return {"body": "Done.\n\n## Learned\n" + "\n".join(lines) + "\n\n## Other\n- friction: [security] not read"}


def corpus(*atoms):
    return {"atoms": [{"id": i, "url": f"https://github.com/acme-corp/work/issues/{i}", "comments": cs}
                      for i, cs in atoms]}


FIXTURE = corpus(
    ("11", [comment("- friction: [delivery] Branch names collide when two atoms share a slug.",
                    "- success: [quality] Running the suite in the background kept the build moving.",
                    "- distill-candidate: [security] Review secrets paths with ACME-SEC-007 on every atom.")]),
    ("12", [comment("- friction: [delivery] branch names collide when two atoms share a slug.",
                    "- distill-candidate: FG-QA-002 should also name the formatter version.")]),
    ("13", [comment("- friction: [upstream] [operations] The FG-OPS-004 abort text omits the rollback owner.",
                    "- friction: something with no category at all.")]),
)


def write(tmp_path, doc=FIXTURE, name="c.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return str(p)


def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd)


def test_help_runs():
    r = run("--help")
    assert r.returncode == 0 and "collect" in r.stdout and "propose" in r.stdout


def test_collect_reads_only_the_learned_section_and_normalises(tmp_path):
    items = ld.collect(json.loads(Path(write(tmp_path)).read_text()))
    assert len(items) == 7  # the "## Other" friction line and none else is dropped
    assert all(set(i) >= {"atom", "line", "kind", "category"} for i in items)
    assert {i["kind"] for i in items} == {"friction", "success", "distill-candidate"}
    by = {(i["atom"], i["line"][:12]): i for i in items}
    assert by[("12", "FG-QA-002 sh")]["category"] == "quality"   # a rule id maps to its category
    assert by[("12", "FG-QA-002 sh")]["rule_id"] == "FG-QA-002"
    assert not any("not read" in i["line"] for i in items)


def test_collect_cli_json(tmp_path):
    r = run("collect", "prog", "--from-json", write(tmp_path))
    assert r.returncode == 0
    assert len(json.loads(r.stdout)) == 7


def test_grouping_one_proposal_per_distinct_lesson_with_source_atoms(tmp_path):
    lines = ld.collect(FIXTURE)
    props, unrouted = ld.group(lines)
    dlv = [p for p in props if p["category"] == "delivery"]
    assert len(dlv) == 1                       # atoms 11 and 12 say the same thing
    assert dlv[0]["atoms"] == ["11", "12"]
    assert [p["category"] for p in props].count("quality") == 1
    assert len(unrouted) == 1 and unrouted[0]["atom"] == "13"
    assert not any(p["lesson"].startswith("Running the suite") for p in props)   # success is not a proposal


def test_routing(tmp_path):
    props, _ = ld.group(ld.collect(FIXTURE))
    route = {p["category"]: p["route"] for p in props}
    assert route["delivery"] == "private"
    assert route["security"] == "private"      # names a private id
    assert route["quality"] == "upstream"      # names an FG- id
    assert route["operations"] == "upstream"   # explicit marker plus FG- id


def test_private_id_never_reaches_upstream_output(tmp_path):
    doc = corpus(("21", [comment("- distill-candidate: [upstream] [security] ACME-SEC-007 should be an FG rule.",
                                 "- friction: [upstream] [delivery] Acme-Corp pipelines drop the trailer.")]))
    out = tmp_path / "out"
    (tmp_path / "lit.txt").write_text("Acme-Corp\n")
    r = run("propose", "prog", "--from-json", write(tmp_path, doc), "--out", str(out),
            "--literals", str(tmp_path / "lit.txt"), "--project-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert not (out / "upstream").exists()      # both lessons were kept private
    assert "2 private" in r.stdout and "1 demoted" in r.stdout


def test_upstream_ticket_carries_no_atom_id_or_url(tmp_path):
    out = tmp_path / "out"
    r = run("propose", "prog", "--from-json", write(tmp_path), "--out", str(out), "--project-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    tickets = sorted((out / "upstream").glob("kit-fix-*.md"))
    assert len(tickets) == 2
    for t in tickets:
        text = t.read_text()
        assert "acme-corp" not in text.lower() and "ACME-" not in text and "issues/" not in text
        heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
        assert heads == ["## Kit", "## Rule", "## Lesson", "## Proposed edit", "## Evidence"]
        assert gov.leak_findings({t.name: text}, lock=None) == []


def test_private_outputs_validate_against_rule_schema(tmp_path):
    out = tmp_path / "out"
    (tmp_path / "governance" / "local").mkdir(parents=True)
    existing = {"rules": [{"id": "ACME-DLV-004", "title": "t", "statement": "s MUST x", "category": "delivery",
                           "level": 1, "floor": False, "status": "active",
                           "applies_to": {"paths": ["**"], "blueprints": ["*"]}, "check": {"kind": "none"},
                           "exception": {"allowed": True}}]}
    (tmp_path / "governance/local/rules.yaml").write_text(yaml.safe_dump(existing))
    r = run("propose", "prog", "--from-json", write(tmp_path), "--out", str(out), "--project-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    patch = yaml.safe_load((out / "private/rules.patch.yaml").read_text())["rules"]
    ids = [x["id"] for x in patch]
    assert ids == ["ACME-DLV-005", "ACME-SEC-001"]          # org inferred, numbering continues
    for rule in patch:
        assert gov.rule_errors(rule, upstream=False) == []
        assert rule["sources"] and rule["sources"][0].startswith("https://github.com/acme-corp/work/issues/")
    # the merged local layer validates as a whole
    errs = gov.validate_all(gov.load_kits(ROOT), {"rules": existing["rules"] + patch, "overrides": [],
                                                  "exceptions": []})
    assert errs == []
    assert "ACME-DLV-005" in (out / "private/pr-body.md").read_text()


def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "out"
    r = run("propose", "prog", "--from-json", write(tmp_path), "--out", str(out), "--dry-run",
            "--project-dir", str(tmp_path))
    assert r.returncode == 0 and "proposals" in r.stdout
    assert not out.exists()


def test_no_learned_comments_reports_zero_and_creates_nothing(tmp_path):
    out = tmp_path / "out"
    doc = corpus(("31", [{"body": "just a comment"}]), ("32", []))
    r = run("propose", "prog", "--from-json", write(tmp_path, doc), "--out", str(out), "--project-dir", str(tmp_path))
    assert r.returncode == 0 and r.stdout.startswith("0 proposals")
    assert not out.exists()


def test_proposes_only_never_opens_without_flags(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ld, "_run", lambda argv: calls.append(argv) or "")
    monkeypatch.setattr(ld.ep, "gh_graphql", lambda *a, **k: calls.append("graphql"))
    rc = ld.main(["propose", "prog", "--from-json", write(tmp_path), "--out", str(tmp_path / "o"),
                  "--project-dir", str(tmp_path)])
    assert rc == 0 and calls == []


def test_open_tickets_uses_the_ticket_files(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ld, "_run", lambda argv: calls.append(argv) or "https://example.test/1")
    rc = ld.main(["propose", "prog", "--from-json", write(tmp_path), "--out", str(tmp_path / "o"),
                  "--project-dir", str(tmp_path), "--open-tickets"])
    assert rc == 0
    assert len(calls) == 2 and all(c[:3] == ["gh", "issue", "create"] for c in calls)


def test_live_corpus_uses_graphql_nodes_not_search(monkeypatch):
    seen = []

    def fake(query, variables=None):
        seen.append(query)
        return {"repositoryOwner": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"content": {"number": 5, "url": "https://x/5",
                                       "comments": {"totalCount": 1, "nodes": [comment("- friction: [cost] Tags drift.")]}}}]}}}}
    monkeypatch.setattr(ld.ep, "gh_graphql", fake)
    items = ld.collect(ld.fetch_corpus("o", 1))
    assert [(i["atom"], i["category"]) for i in items] == [("5", "cost")]
    assert "search" not in seen[0].lower()


def test_bad_programme_and_missing_corpus_refuse(tmp_path):
    assert run("collect", "bad name", "--from-json", write(tmp_path)).returncode == 2
    r = run("collect", "prog")
    assert r.returncode == 2 and "refused" in r.stderr


def test_every_category_has_an_id_abbreviation_in_the_kits():
    for cat, ab in ld.ABBREV.items():
        if cat == "factory":
            continue
        text = (ROOT / "governance" / "kits" / cat / "rules.yaml").read_text()
        assert f"FG-{ab}-" in text, cat
    assert set(ld.ABBREV) == set(ld.CATEGORIES) and len(ld.CATEGORIES) == 16
