"""gv-14: research save-and-rule. No network; synthetic sweeps only."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "foundry_research_note.py"
FIX = Path(__file__).resolve().parent / "fixtures" / "blockers"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_blocker_check as bc  # noqa: E402
import foundry_research_note as rn  # noqa: E402

DATE = "2026-11-04"


def cli(tmp_path, sweep, slug="example", *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--in", str(FIX / sweep), "--slug", slug,
                           "--project-dir", str(tmp_path), "--date", DATE, *extra],
                          capture_output=True, text=True)


def test_converging_sources_write_note_and_ruling(tmp_path):
    p = cli(tmp_path, "sweep-converging.json")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["status"] == "ruled" and out["candidate"] is None
    note = tmp_path / ".foundry" / "research" / f"{DATE}-example.md"
    ruling = tmp_path / ".foundry" / "decisions" / f"{DATE}-example.md"
    assert Path(out["note"]) == note and Path(out["ruling"]) == ruling
    n, r = note.read_text(), ruling.read_text()
    # provenance markers
    assert "wf_example0001" in n and "https://example.org/docs/manifest" in n
    assert "retrieved 2026-11-04" in n and "retrieved 2026-11-03" in n
    for heading in ("## Consensus", "## No consensus", "## Negative findings", "## Gaps", "## Sources"):
        assert heading in n
    assert "No registry documents a downgrade path." in n and "Pre-release naming not surveyed." in n
    # ruling is dated, adopts consensus, cites the note
    assert "**Date:** 2026-11-04" in r and "Use semver in the manifest." in r
    assert f".foundry/research/{DATE}-example.md" in r


def test_no_consensus_writes_note_and_candidate_not_ruling(tmp_path):
    p = cli(tmp_path, "sweep-split.json", "split")
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["status"] == "escalate" and out["ruling"] is None
    assert (tmp_path / ".foundry" / "research" / f"{DATE}-split.md").is_file()
    assert not (tmp_path / ".foundry" / "decisions").exists()
    c = out["candidate"]
    assert c["why_operator"] == "no-consensus-after-research"
    assert c["options"] and c["recommendation"].startswith("Recommend one file per period")
    assert c["one_word_ask"] and c["evidence"][0] == f".foundry/research/{DATE}-split.md"
    # the candidate survives the lint as a blocker
    v = bc.partition([c])
    assert v["blockers"] == [c], v["next_tasks"]


def test_rerun_is_idempotent(tmp_path):
    cli(tmp_path, "sweep-converging.json")
    files = sorted(p for p in (tmp_path / ".foundry").rglob("*") if p.is_file())
    before = [p.read_bytes() for p in files]
    cli(tmp_path, "sweep-converging.json")
    after = sorted(p for p in (tmp_path / ".foundry").rglob("*") if p.is_file())
    assert after == files and [p.read_bytes() for p in after] == before
    assert len(files) == 3     # note, ruling, raw input


def test_run_id_flag_and_bare_sweep_shape(tmp_path):
    sweep = json.loads((FIX / "sweep-converging.json").read_text())["sweeps"][0]
    f = tmp_path / "bare.json"
    f.write_text(json.dumps(sweep))
    p = subprocess.run([sys.executable, str(SCRIPT), "--in", str(f), "--slug", "bare", "--run-id", "wf_cli",
                        "--project-dir", str(tmp_path), "--date", DATE], capture_output=True, text=True)
    assert p.returncode == 0 and "wf_cli" in Path(json.loads(p.stdout)["note"]).read_text()


def test_no_consensus_and_no_consensus_list_empty_is_inconclusive(tmp_path):
    out = rn.write_all({"sweeps": [{"domain": "d", "sources": []}]}, "empty", str(tmp_path), DATE)
    assert out["status"] == "inconclusive" and out["ruling"] is None and out["candidate"] is None


@pytest.mark.parametrize("slug", ["../escape", "Upper", "a/b", "", "-lead", "x" * 80])
def test_unsafe_slug_is_refused(tmp_path, slug):
    p = cli(tmp_path, "sweep-converging.json", slug)
    assert p.returncode == 2 and not (tmp_path / ".foundry").exists()


def test_malformed_input_is_refused(tmp_path):
    f = tmp_path / "bad.json"
    for text in ("not json", "[]", '{"sweeps": []}', '{"x": 1}'):
        f.write_text(text)
        p = subprocess.run([sys.executable, str(SCRIPT), "--in", str(f), "--slug", "s",
                            "--project-dir", str(tmp_path), "--date", DATE], capture_output=True, text=True)
        assert p.returncode == 2, text
    assert not (tmp_path / ".foundry" / "research").exists()


def test_symlinked_foundry_dir_escaping_the_project_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    proj = tmp_path / "proj"
    (proj / ".foundry").mkdir(parents=True)
    (proj / ".foundry" / "research").symlink_to(outside)
    p = cli(proj, "sweep-converging.json")
    assert p.returncode == 2 and not list(outside.iterdir())


def test_fetched_text_cannot_break_the_layout(tmp_path):
    sweep = {"sweeps": [{"consensus": ["ok\n## Injected heading‮"], "sources": []}]}
    out = rn.write_all(sweep, "inj", str(tmp_path), DATE)
    text = Path(out["note"]).read_text()
    assert "\n## Injected heading" not in text and "‮" not in text


def test_no_network_imports():
    import ast
    mods = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert not mods & {"socket", "urllib", "http", "subprocess", "requests", "ssl"}


def sweep_with(**kw):
    sw = {"domain": "d", "consensus": ["Use semver."], "no_consensus": [], "sources": [
        {"title": "T", "url": "https://example.org/a", "retrieved": "2026-11-04"}]}
    sw.update(kw)
    return {"sweeps": [sw]}


def test_raw_input_is_kept_beside_the_note(tmp_path):
    out = rn.write_all(sweep_with(), "raw", str(tmp_path), DATE)
    raw = Path(out["raw"])
    assert raw.name == f"{DATE}-raw.json" and raw.parent == Path(out["note"]).parent
    assert json.loads(raw.read_text()) == sweep_with()
    assert f"{DATE}-raw.json" in Path(out["note"]).read_text()


def test_stale_ruling_is_removed_when_a_rerun_escalates(tmp_path):
    first = rn.write_all(sweep_with(), "flip", str(tmp_path), DATE)
    assert first["status"] == "ruled" and Path(first["ruling"]).is_file()
    second = rn.write_all(sweep_with(no_consensus=["A vs B. Recommend A."]), "flip", str(tmp_path), DATE)
    assert second["status"] == "escalate" and not Path(first["ruling"]).exists()
    third = rn.write_all(sweep_with(), "flip", str(tmp_path), DATE)       # and back again: ruling returns
    assert third["status"] == "ruled" and Path(third["ruling"]).is_file()


def test_foreign_file_at_the_ruling_path_is_a_ruling_conflict(tmp_path):
    d = tmp_path / ".foundry" / "decisions"
    d.mkdir(parents=True)
    (d / f"{DATE}-mine.md").write_text("# an operator-written decision\n")
    with pytest.raises(rn.SweepError, match="ruling-conflict"):
        rn.write_all(sweep_with(no_consensus=["x vs y"]), "mine", str(tmp_path), DATE)
    assert (d / f"{DATE}-mine.md").read_text().startswith("# an operator")
    p = cli(tmp_path, "sweep-split.json", "mine")
    assert p.returncode == 2 and "ruling-conflict" in p.stderr


@pytest.mark.parametrize("line", ["Rotate the signing token monthly.", "Store the secret in a vault.",
                                  "Keep the permission floor read-only.", "Use OAuth for auth."])
def test_consensus_touching_auth_secrets_or_the_floor_is_held(tmp_path, line):
    out = rn.write_all(sweep_with(consensus=[line]), "held", str(tmp_path), DATE)
    assert out["status"] == "escalate" and out["ruling"] is None
    assert not (tmp_path / ".foundry" / "decisions").exists()
    assert bc.partition([out["candidate"]])["blockers"] == [out["candidate"]]


def test_ruling_states_the_practice_not_a_control(tmp_path):
    out = rn.write_all(sweep_with(), "txt", str(tmp_path), DATE)
    text = Path(out["ruling"]).read_text()
    assert "practice the operator's test pass covers, not a control" in text


def test_sources_render_safely(tmp_path):
    srcs = [{"title": "Good", "url": "https://example.org/ok", "retrieved": "2026-11-04"},
            {"title": "Odd", "url": "javascript:alert(1)"},
            {"title": "Spaced", "url": "https://example.org/a b>x"},
            {"title": "No link"}]
    out = rn.write_all(sweep_with(sources=srcs), "src", str(tmp_path), DATE, run_id="wf`bad`")
    t = Path(out["note"]).read_text()
    assert "<https://example.org/ok>" in t
    assert "`javascript:alert(1)`" in t and "<javascript" not in t
    assert "`https://example.org/a b>x`" in t
    assert "No link — (no url)" in t
    assert "wf'bad'" in t and "wf`bad`" not in t


@pytest.mark.parametrize("ch", ["\u200b", "\u202e", "\ufeff", "\u2060", "\u061c", "\x00"])
def test_invisible_characters_are_stripped(tmp_path, ch):
    out = rn.write_all(sweep_with(consensus=[f"Use{ch} semver."]), "inv", str(tmp_path), DATE)
    assert ch not in Path(out["note"]).read_text() and "Use semver." in Path(out["note"]).read_text()


def test_no_literal_invisible_characters_in_the_script_source():
    bad = [c for c in SCRIPT.read_text() if c in "\u200b\u200e\u202e\u2060\u2066\ufeff\u061c"]
    assert bad == []
