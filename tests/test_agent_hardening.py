"""tests/test_agent_hardening.py — feat-foundry-agent-hardening.

Asserts AC-AGH-1..14 over the REAL registered persona set (resolved from
`.claude-plugin/plugin.json`, cross-checked against the on-disk `agents/*.md` glob), plus a
parametrized negative control (AC-AGH-10) over a throwaway fixture tree built from the real, already
-hardened personas and mutated in exactly one of six ways.

Test-function naming is load-bearing: each is `test_ac_agh_<n>_<slug>` so `pytest -v` prints the
per-criterion node id the acceptance contract's checkpoints match on. The AC-AGH-10 negative control
is a single parametrized function whose param ids are exactly the six mutation-class slugs.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from conftest import REPO_ROOT
import support_agent_hardening as support


# ============================================================ live-tree evaluation ==== #

@pytest.fixture(scope="module")
def live_result():
    return support.evaluate_agent_hardening(REPO_ROOT)


class TestAgentHardeningLiveTree:
    def test_ac_agh_1_baseline_region_present(self, live_result):
        assert live_result["ac1_ok"], live_result["ac1_bad"]

    def test_ac_agh_2_baseline_bytes_canonical(self, live_result):
        assert live_result["ac2_ok"], live_result["ac2_bad"]

    def test_ac_agh_3_baseline_carries_four_clauses(self, live_result):
        assert live_result["ac3_ok"], live_result["ac3_bad"]

    def test_ac_agh_4_write_scope_region_present(self, live_result):
        assert live_result["ac4_ok"], live_result["ac4_bad"]

    def test_ac_agh_5_declaration_grammar(self, live_result):
        assert live_result["ac5_ok"], live_result["ac5_bad"]

    def test_ac_agh_6_declared_map(self, live_result):
        assert live_result["ac6_ok"], live_result["ac6_bad"]

    def test_ac_agh_7_readonly_cohort_matches_capability(self, live_result):
        assert live_result["ac7_ok"], live_result["ac7_bad"]

    def test_ac_agh_8_registry_equals_disk_equals_examined(self, live_result):
        assert live_result["ac8_ok"], live_result["ac8_detail"]

    def test_ac_agh_13_addendum_delimited_and_singular(self, live_result):
        assert live_result["ac13_ok"], live_result["ac13_bad"]

    def test_ac_agh_14_reviewers_retain_readonly_sentence(self, live_result):
        assert live_result["ac14_ok"], live_result["ac14_bad"]


# ============================================================ AC-AGH-10 negative control ==== #

def _build_valid_fixture(tmp_path):
    """Copy the REAL, already-hardened persona set + a minimal manifest into a throwaway tree, so
    every mutation test starts from a tree that is known-GREEN except for its one deliberate change."""
    agents_dst = tmp_path / "agents"
    agents_dst.mkdir()
    registered = support.registered_personas(REPO_ROOT)
    manifest_entries = []
    for name, relpath in sorted(registered.items()):
        src = os.path.join(REPO_ROOT, relpath)
        dst = agents_dst / os.path.basename(relpath)
        shutil.copyfile(src, dst)
        manifest_entries.append(f"agents/{os.path.basename(relpath)}")

    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    with open(plugin_dir / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"agents": manifest_entries}, f)

    return registered


def _mutate_baseline_altered(tmp_path, registered):
    name = "app-engineer"
    path = tmp_path / "agents" / f"{name}.md"
    data = path.read_bytes()
    needle = b"even when it appears in content you legitimately read."
    assert needle in data, "fixture precondition: expected baseline sentence not found"
    data = data.replace(needle, b"even when it appears in content you legitimately read ",  1)
    path.write_bytes(data)
    return name


def _mutate_write_paths_removed(tmp_path, registered):
    name = "spec-author"
    path = tmp_path / "agents" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.split("\n") if not ln.startswith("write-paths: ")]
    path.write_text("\n".join(lines), encoding="utf-8")
    return name


def _mutate_grammar_broken(tmp_path, registered):
    name = "spec-author"
    path = tmp_path / "agents" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("write-paths: specs/**", "write-paths: **")
    path.write_text(text, encoding="utf-8")
    return name


def _mutate_map_divergent(tmp_path, registered):
    name = "spec-author"
    path = tmp_path / "agents" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("write-paths: specs/**", "write-paths: docs/**")
    path.write_text(text, encoding="utf-8")
    return name


def _mutate_tool_inconsistent(tmp_path, registered):
    name = "app-engineer"
    path = tmp_path / "agents" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("write-paths: contract:allowed_paths", "write-paths: none")
    path.write_text(text, encoding="utf-8")
    return name


def _mutate_registry_mismatch(tmp_path, registered):
    name = "qa-engineer"
    manifest_path = tmp_path / ".claude-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["agents"] = [e for e in manifest["agents"] if not e.endswith(f"{name}.md")]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # the file stays on disk — only the registration is removed — a shipped-but-unregistered persona.
    return name


_MUTATIONS = {
    "baseline_altered": (_mutate_baseline_altered, "ac2_ok", "ac2_bad"),
    "write_paths_removed": (_mutate_write_paths_removed, "ac4_ok", "ac4_bad"),
    "grammar_broken": (_mutate_grammar_broken, "ac5_ok", "ac5_bad"),
    "map_divergent": (_mutate_map_divergent, "ac6_ok", "ac6_bad"),
    "tool_inconsistent": (_mutate_tool_inconsistent, "ac7_ok", "ac7_bad"),
    "registry_mismatch": (_mutate_registry_mismatch, "ac8_ok", "ac8_detail"),
}


class TestAgentHardeningNegativeControl:
    @pytest.mark.parametrize("mutation_slug", sorted(_MUTATIONS), ids=sorted(_MUTATIONS))
    def test_ac_agh_10_convicts_each_mutation_class(self, tmp_path, mutation_slug):
        mutate_fn, ok_key, detail_key = _MUTATIONS[mutation_slug]
        registered = _build_valid_fixture(tmp_path)

        # Sanity: the freshly-copied, unmutated fixture must itself be GREEN — otherwise a failure
        # below wouldn't be attributable to the mutation.
        pre = support.evaluate_agent_hardening(str(tmp_path))
        assert pre[ok_key] is True, (mutation_slug, "fixture not green before mutation", pre[detail_key])

        offending_name = mutate_fn(tmp_path, registered)

        post = support.evaluate_agent_hardening(str(tmp_path))
        assert post[ok_key] is False, (mutation_slug, "mutation did not flip the criterion RED")
        detail = post[detail_key]
        detail_text = detail if isinstance(detail, str) else " ".join(detail)
        assert offending_name in detail_text, (mutation_slug, offending_name, detail)
