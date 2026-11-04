"""Brief renderer (gv-01): sections, the communication style block on/off, the inline cap, no network."""
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import foundry_brief as brief  # noqa: E402
import foundry_dispatch_lint as lint  # noqa: E402
from test_governance_core import make_plugin, make_project, rule  # noqa: E402

STYLE_MARK = "Communication style (simplified technical English"


def render(plugin, proj, ticket, *extra, paths=("src/a.py",)):
    t = Path(proj).parent / "ticket.json"
    t.write_text(json.dumps(ticket))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = brief.main(["--plugin-root", str(plugin), "--project-dir", str(proj), "render", "--ticket", str(t),
                         "--paths", *paths, *extra])
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture()
def shipped(tmp_path):
    return ROOT, make_project(tmp_path)


def test_style_block_present_by_default(shipped):
    rc, out, _ = render(*shipped, {})
    assert rc == 0 and STYLE_MARK in out and "Use short, direct sentences and active voice." in out


def test_style_off_removes_every_line_of_the_style_text(shipped):
    rc, out, _ = render(*shipped, {}, "--style", "off")
    assert rc == 0 and STYLE_MARK not in out
    assert "short, direct sentences" not in out and "ASD-STE100" not in out and "simplified technical English" not in out


def test_policy_loader_off_is_honoured(shipped, monkeypatch):
    import types
    fake = types.ModuleType("foundry_operating_model")
    fake.communication_style = lambda proj: "off"
    monkeypatch.setitem(sys.modules, "foundry_operating_model", fake)
    assert STYLE_MARK not in render(*shipped, {})[1]
    fake.communication_style = lambda proj: "simplified-technical-english"
    assert STYLE_MARK in render(*shipped, {})[1]  # control


def test_a_broken_policy_loader_defaults_on(shipped, monkeypatch):
    import types
    fake = types.ModuleType("foundry_operating_model")

    def boom(proj):
        raise RuntimeError("x")
    fake.communication_style = boom
    monkeypatch.setitem(sys.modules, "foundry_operating_model", fake)
    assert STYLE_MARK in render(*shipped, {})[1]


def test_conduct_hygiene_and_shell_shape_always_present(shipped):
    out = render(*shipped, {}, "--style", "off")[1]
    assert "FG-CND-001" in out and "Hygiene:" in out and "Shell shape:" in out and "Always-on core:" in out


def test_cited_ids_are_inlined_with_statements(tmp_path):
    plugin = make_plugin(tmp_path, [rule("FG-TST-001", level=2, statement="An agent MUST cite me."),
                                    rule("FG-TST-002", level=2, statement="Unrelated.")])
    rc, out, _ = render(plugin, make_project(tmp_path), {"cites": ["FG-TST-001"]})
    assert rc == 0 and "Rules this ticket cites:\n- FG-TST-001: An agent MUST cite me." in out


def test_unknown_cited_id_refused(tmp_path):
    plugin = make_plugin(tmp_path, [rule()])
    rc, _, err = render(plugin, make_project(tmp_path), {"cites": ["FG-TST-404"]})
    assert rc == 2 and "cited-rule-unknown:FG-TST-404" in err


def test_blueprint_level_one_rules_and_path_scoping(tmp_path):
    plugin = make_plugin(tmp_path, [
        rule("FG-TST-001", level=1, statement="LEVELONE.", applies_to={"paths": ["docs/**"], "blueprints": ["cli-tool"]}),
        rule("FG-TST-002", level=2, statement="SCOPED.", applies_to={"paths": ["src/**"], "blueprints": ["*"]}),
        rule("FG-TST-003", level=2, statement="OTHERPATH.", applies_to={"paths": ["ops/**"], "blueprints": ["*"]})])
    proj = make_project(tmp_path)
    out = render(plugin, proj, {}, "--blueprint", "cli-tool")[1]
    assert "LEVELONE." in out and "SCOPED." in out and "OTHERPATH." not in out
    assert "LEVELONE." not in render(plugin, proj, {}, "--blueprint", "node-web")[1]  # control: other blueprint


def test_a_cited_rule_is_not_repeated_in_other_sections(tmp_path):
    plugin = make_plugin(tmp_path, [rule("FG-TST-001", statement="ONCE.")])
    out = render(plugin, make_project(tmp_path), {"cites": ["FG-TST-001"]})[1]
    assert out.count("ONCE.") == 1


def test_product_charter_referenced_by_path_when_present(tmp_path):
    proj = make_project(tmp_path)
    (proj / "PRODUCT-CHARTER.md").write_text("# charter\n")
    out = render(ROOT, proj, {})[1]
    assert "Product charter: read PRODUCT-CHARTER.md" in out
    assert "Product charter" not in render(ROOT, make_project(tmp_path / "b"), {})[1]  # control


def test_output_respects_the_inline_cap(tmp_path):
    plugin = make_plugin(tmp_path, [rule(f"FG-TST-{i:03d}", statement="An agent MUST " + "x" * 300) for i in range(1, 30)])
    proj = make_project(tmp_path)
    (proj / ".foundry").mkdir()
    (proj / ".foundry" / "dispatch-inline-cap").write_text("1000\n")
    out = render(plugin, proj, {})[1]
    assert lint.lint_prompt(out, cap=1000) == []
    assert "FG-TST-029" in out or "[more]" in out


def test_default_output_is_lint_clean(shipped):
    assert lint.lint_prompt(render(*shipped, {})[1], cap=lint.read_inline_cap(str(shipped[1]))) == []


@pytest.mark.parametrize("bad", ["not json", "[]"])
def test_bad_ticket_refused(tmp_path, bad):
    t = tmp_path / "t.json"
    t.write_text(bad)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = brief.main(["--plugin-root", str(ROOT), "--project-dir", str(tmp_path), "render", "--ticket", str(t), "--paths", "a"])
    assert rc == 2 and "refused:" in err.getvalue()


def test_cli_runs_and_makes_no_process_calls(tmp_path, monkeypatch):
    t = tmp_path / "t.json"
    t.write_text("{}")
    calls = []
    for name in ("run", "Popen", "check_output", "check_call", "call"):
        monkeypatch.setattr(subprocess, name, lambda *a, **k: calls.append(a))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = brief.main(["--plugin-root", str(ROOT), "--project-dir", str(tmp_path), "render", "--ticket", str(t), "--paths", "a"])
    assert rc == 0 and calls == []
