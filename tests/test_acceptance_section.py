"""dl-01: the `## Acceptance` ticket section (decision 1 of the SDLC 2.0 review).

`foundry-ticket.py` parses EARS bullets with stable `AC-<n>` ids plus one `Grounding:` line. The section is
required for the design-required class (refusal `acceptance-required`), optional below it. `foundry_brief.py`
inlines the bullets; `agents/pr-reviewer.md` grades the diff per id. `gh` is never called.
Assertions are on parsed structures and exit codes.
"""
import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("ft_acceptance", SCRIPTS / "foundry-ticket.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)
import foundry_brief as brief  # noqa: E402

ACC = """## Acceptance
- AC-1: WHEN the file is missing, THE SYSTEM SHALL refuse and name the file.
- [AC-2] THE SYSTEM SHALL print one line per refusal.
  It SHALL keep the order of the input.
Grounding: EARS requirement syntax; prior-art sweep of ticket #269.
"""

DESIGN_OK = """## Design
- Components: hooks/foundry-example.py
- Reuses: the shared brief renderer
- Cites: FG-SEC-006
"""


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(r))
    _git(r, "config", "user.email", "t@example.test")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    (r / ".foundry").mkdir()
    (r / ".foundry" / "operating-model.yaml").write_text(
        'schema_version: 1\ngovernance:\n  design_required_paths: ["hooks/**"]\n')
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    return r


def start(repo, monkeypatch, body, labels=(), number=7):
    issue = {"number": number, "title": "t", "body": body, "url": "https://github.com/o/r/issues/%d" % number,
             "author": {"login": "alice"}, "state": "OPEN", "labels": [{"name": n} for n in labels]}
    monkeypatch.setattr(ft, "_gh_json", lambda a: issue)
    monkeypatch.setattr(ft, "read_issue", lambda run, repo_, n, **k: {"last_edited_by": "alice", "name_with_owner": "o/r",
                                                                       "editors": ["alice"], "done_sha256": ft.store.command_digest("")})
    monkeypatch.setattr(ft, "author_trusted", lambda r, login, gh_json=None, **k: (True, "w"))
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = ft.cmd_start([str(number)])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                err.write(e.code)
    return code, out.getvalue(), err.getvalue()


# ---- the parser ---------------------------------------------------------------------------------

def test_parse_bullets_ids_and_grounding():
    a = ft.parse_acceptance(ACC)
    assert a["present"] is True and a["errors"] == []
    assert [b["id"] for b in a["bullets"]] == ["AC-1", "AC-2"]
    assert a["bullets"][0]["text"].startswith("WHEN the file is missing, THE SYSTEM SHALL refuse")
    assert "keep the order" in a["bullets"][1]["text"]          # a continuation line joins its bullet
    assert a["grounding"].startswith("EARS requirement syntax")


def test_absent_section():
    a = ft.parse_acceptance("## Done means\n```\ntrue\n```\n")
    assert a["present"] is False and a["bullets"] == [] and a["grounding"] is None


def test_acceptance_criteria_heading_is_a_different_section():
    a = ft.parse_acceptance("## Acceptance criteria\n- AC-1: THE SYSTEM SHALL x\n")
    assert a["present"] is False


def test_duplicate_id_is_refused_naming_the_bullet():
    a = ft.parse_acceptance("## Acceptance\n- AC-1: THE SYSTEM SHALL a.\n- AC-1: THE SYSTEM SHALL b.\nGrounding: g\n")
    assert len(a["errors"]) == 1 and "AC-1" in a["errors"][0] and "THE SYSTEM SHALL b" in a["errors"][0]


def test_bullet_without_id_is_refused_naming_the_bullet():
    a = ft.parse_acceptance("## Acceptance\n- AC-1: THE SYSTEM SHALL a.\n- THE SYSTEM SHALL b.\nGrounding: g\n")
    assert len(a["errors"]) == 1 and "THE SYSTEM SHALL b" in a["errors"][0] and "AC-2" in a["errors"][0]


def test_bullet_that_is_not_ears_is_refused():
    a = ft.parse_acceptance("## Acceptance\n- AC-1: it should work\nGrounding: g\n")
    assert len(a["errors"]) == 1 and "AC-1" in a["errors"][0] and "SHALL" in a["errors"][0]


def test_grounding_line_is_not_a_bullet():
    a = ft.parse_acceptance("## Acceptance\n- AC-1: THE SYSTEM SHALL a.\n- Grounding: the sweep\n")
    assert [b["id"] for b in a["bullets"]] == ["AC-1"] and a["grounding"] == "the sweep" and a["errors"] == []


# ---- the class rule -----------------------------------------------------------------------------

def test_design_required_without_section_is_refused(repo, monkeypatch):
    code, _, err = start(repo, monkeypatch, "## Scope\nhooks/x.py\n\n" + DESIGN_OK)
    assert code != 0 and "acceptance-required" in err and "## Acceptance" in err


def test_design_required_without_bullet_names_the_piece(repo, monkeypatch):
    code, _, err = start(repo, monkeypatch, DESIGN_OK + "\n## Acceptance\nGrounding: g\n")
    assert code != 0 and "acceptance-required" in err and "EARS bullet" in err


def test_design_required_without_grounding_names_the_piece(repo, monkeypatch):
    body = DESIGN_OK + "\n## Acceptance\n- AC-1: THE SYSTEM SHALL a.\n"
    code, _, err = start(repo, monkeypatch, body)
    assert code != 0 and "acceptance-required" in err and "Grounding:" in err


def test_design_required_with_a_full_section_is_admitted(repo, monkeypatch):
    code, out, err = start(repo, monkeypatch, DESIGN_OK + "\n" + ACC)
    assert code == 0, err


def test_duplicate_id_refuses_start(repo, monkeypatch):
    body = DESIGN_OK + "\n## Acceptance\n- AC-1: THE SYSTEM SHALL a.\n- AC-1: THE SYSTEM SHALL b.\nGrounding: g\n"
    code, _, err = start(repo, monkeypatch, body)
    assert code != 0 and "AC-1" in err


def test_below_the_class_no_section_is_admitted(repo, monkeypatch):
    code, out, err = start(repo, monkeypatch, "## Scope\nsrc/app.py\n")
    assert code == 0, err
    assert "Acceptance" in (out + err)                      # part of the printed checklist


def test_view_exposes_acceptance(repo):
    v = ft.design_view(DESIGN_OK + "\n" + ACC, [])
    assert [b["id"] for b in v["acceptance"]["bullets"]] == ["AC-1", "AC-2"]
    assert v["acceptance"]["grounding"]
    assert v["acceptance_defects"] == []


# ---- the brief and the reviewer -----------------------------------------------------------------

def test_brief_inlines_every_bullet_with_its_id(tmp_path):
    t = tmp_path / "ticket.json"
    a = ft.parse_acceptance(ACC)
    t.write_text(json.dumps({"title": "x", "cites": [], "acceptance": a["bullets"], "grounding": a["grounding"]}))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = brief.main(["--plugin-root", str(ROOT), "--project-dir", str(tmp_path), "render", "--ticket", str(t),
                         "--paths", "src/a.py"])
    assert rc == 0
    lines = out.getvalue().split("\n")
    i = lines.index("Acceptance (grade each id):")
    assert lines[i + 1].startswith("- AC-1: WHEN the file is missing")
    assert lines[i + 2].startswith("- AC-2: THE SYSTEM SHALL print one line")


def test_brief_without_acceptance_has_no_section(tmp_path):
    t = tmp_path / "ticket.json"
    t.write_text(json.dumps({"title": "x", "cites": []}))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = brief.main(["--plugin-root", str(ROOT), "--project-dir", str(tmp_path), "render", "--ticket", str(t),
                         "--paths", "src/a.py"])
    assert rc == 0 and "Acceptance (grade each id):" not in out.getvalue()


def test_reviewer_grades_per_ac_id():
    text = (ROOT / "agents" / "pr-reviewer.md").read_text()
    assert "AC-<n>" in text and "Block" in text.split("Acceptance lens", 1)[1].split("\n- **", 1)[0]


def test_governance_documents_the_section():
    text = (ROOT / "docs" / "governance.md").read_text()
    assert "`## Acceptance`" in text and "acceptance-required" in text
