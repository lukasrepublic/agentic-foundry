"""tests/test_agents_workflow.py — converted from scripts/foundry_checks/{persona-model-selection,
workflow-agent-model-pins, reference-agents}.py.

Ports the real behavioral assertions those three drop-in selftests drove — the parsers/predicates
are pure (frontmatter `model:` extraction, alias-only hygiene, tool-allowlist equality) and are
exercised BOTH over throwaway fixtures (isolated negative controls) and over the REAL shipped
`.claude-plugin/plugin.json` + `agents/*.md` + `workflows/*.js` (the acceptance evidence that
actually matters: this governs which model powers every dispatched persona, including the
framework-engineer persona this very test suite was authored under).
"""
from __future__ import annotations

import json
import os
import re

from conftest import REPO_ROOT
import support_agents_workflow as support


# =================================================== persona-model-selection.py ==== #

class TestParseModel:
    def test_extracts_quoted_model_scalar(self):
        text = "---\nname: x\nmodel: 'sonnet'\n---\nbody\n"
        model, fm_ok = support.parse_model(text)
        assert fm_ok is True and model == "sonnet"

    def test_no_frontmatter_is_not_ok(self):
        model, fm_ok = support.parse_model("no frontmatter here\n")
        assert fm_ok is False

    def test_absent_model_field_is_none(self):
        model, fm_ok = support.parse_model("---\nname: x\n---\nbody\n")
        assert fm_ok is True and model is None


class TestAliasHygiene:
    def test_none_is_ok(self):
        assert support.alias_hygiene(None) == (True, "")

    def test_allowed_alias_ok(self):
        ok, _ = support.alias_hygiene("sonnet")
        assert ok is True

    def test_dated_model_id_rejected(self):
        ok, reason = support.alias_hygiene("claude-opus-4-8")
        assert ok is False and "dated/full model id" in reason

    def test_inherit_all_star_rejected(self):
        ok, reason = support.alias_hygiene("*")
        assert ok is False

    def test_garbage_value_rejected(self):
        ok, reason = support.alias_hygiene("banana")
        assert ok is False


class TestPersonaModelSelectionLiveTree:
    def test_live_shipped_registry_satisfies_ac_pms_1_and_2(self):
        result = support.evaluate_persona_model_selection(root=REPO_ROOT)
        assert result["ac1"] is True, result["detail"]
        assert result["ac2"] is True, result["detail"]

    def test_fixture_wrong_alias_flips_ac1_red(self, tmp_path):
        plugin_root = tmp_path
        os.makedirs(plugin_root / ".claude-plugin")
        os.makedirs(plugin_root / "agents")
        json.dump({"agents": ["agents/security-reviewer.md"]},
                  open(plugin_root / ".claude-plugin" / "plugin.json", "w"))
        with open(plugin_root / "agents" / "security-reviewer.md", "w", encoding="utf-8") as f:
            f.write("---\nname: security-reviewer\nmodel: sonnet\n---\nbody\n")  # should be opus.
        result = support.evaluate_persona_model_selection(root=str(plugin_root))
        assert result["ac1"] is False

    def test_fixture_dated_id_flips_ac2_red_then_fix_reruns_green(self, tmp_path):
        plugin_root = tmp_path
        os.makedirs(plugin_root / ".claude-plugin")
        os.makedirs(plugin_root / "agents")
        json.dump({"agents": ["agents/pr-reviewer.md"]},
                  open(plugin_root / ".claude-plugin" / "plugin.json", "w"))
        agent_path = plugin_root / "agents" / "pr-reviewer.md"
        agent_path.write_text("---\nname: pr-reviewer\nmodel: claude-opus-4-8\n---\nbody\n", encoding="utf-8")
        broken = support.evaluate_persona_model_selection(root=str(plugin_root))
        assert broken["ac2"] is False
        agent_path.write_text("---\nname: pr-reviewer\nmodel: sonnet\n---\nbody\n", encoding="utf-8")
        fixed = support.evaluate_persona_model_selection(root=str(plugin_root))
        assert fixed["ac2"] is True  # the dated-id violation is fixed (ac1 still flags the other
        # 7 unregistered personas in this deliberately-minimal fixture — not this control's concern).


# =================================================== reference-agents.py ==== #

class TestReferenceAgentsLiveTree:
    def test_live_manifest_registers_exactly_the_canonical_set(self):
        manifest = json.load(open(os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")))
        names, _ = zip(*[(os.path.basename(str(e))[:-3] if str(e).endswith(".md") else str(e), e)
                         for e in manifest.get("agents") or []])
        assert set(names) == support.CANONICAL_AGENTS, set(names)

    def test_live_agents_carry_exact_tool_allowlists(self):
        manifest = json.load(open(os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")))
        for entry in manifest.get("agents") or []:
            name = os.path.basename(str(entry))
            name = name[:-3] if name.endswith(".md") else name
            path = os.path.normpath(os.path.join(REPO_ROOT, str(entry)))
            assert os.path.isfile(path), (name, path)
            text = open(path, encoding="utf-8").read()
            m = re.search(r"(?m)^tools:\s*(.+)$", text)
            assert m, f"{name}: no tools: frontmatter line"
            tools_line = m.group(1).strip()
            declared = {t.strip() for t in tools_line.split(",") if t.strip()}
            expected = support.ROLE_TOOLS.get(name)
            assert expected is not None, f"{name}: outside the known role map"
            assert declared == expected, (name, declared, expected)


# =================================================== workflow-agent-model-pins.py ==== #

class TestWorkflowAgentModelPinsLiveTree:
    def test_live_workflows_pin_every_fanout_agent_to_an_alias(self):
        result = support.evaluate_workflow_agent_model_pins(root=REPO_ROOT)
        assert result["ac1"] is True, result["detail"]
        assert result["ac2"] is True, result["detail"]

    def test_dated_model_id_in_fixture_workflow_flips_red(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "sample.js").write_text(
            "agent({ label: 'audit:', model: 'claude-opus-4-8' });\n", encoding="utf-8")
        result = support.evaluate_workflow_agent_model_pins(root=str(tmp_path))
        assert result["ac2"] is False

    def test_missing_model_pin_on_labeled_agent_flips_red(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "sample.js").write_text("agent({ label: 'audit:' });\n", encoding="utf-8")
        result = support.evaluate_workflow_agent_model_pins(root=str(tmp_path))
        assert result["ac1"] is False
