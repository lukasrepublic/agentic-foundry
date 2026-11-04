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
    assert len(files) == 2


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
