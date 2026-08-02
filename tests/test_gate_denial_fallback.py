"""tests/test_gate_denial_fallback.py — feat-foundry-gate-denial-fallback.

Asserts AC-GDF-1..3 over the REAL repository tree (`docs/harness-denial-fallback.md` plus the
seven ceremony-instructing `skills/*/SKILL.md` files), plus a parametrized negative control
(AC-GDF-4) over a throwaway fixture built from the real, already-compliant clause + skills and
mutated in exactly one of five ways.

Test-function naming is load-bearing: each is `test_ac_gdf_<n>_<slug>` so `pytest -k` selects on
the node id the acceptance contract's checkpoints match on. The AC-GDF-4 negative control is a
single parametrized function whose param ids are exactly the five mutation-class slugs.
"""
from __future__ import annotations

import os
import shutil

import pytest

from conftest import REPO_ROOT
import support_gate_denial_fallback as support


# ============================================================ live-tree evaluation ==== #

@pytest.fixture(scope="module")
def live_result():
    return support.evaluate_gate_denial_fallback(REPO_ROOT)


class TestGateDenialFallbackLiveTree:
    def test_ac_gdf_1_clause_region_carries_three_limbs(self, live_result):
        assert live_result["ac1_ok"], live_result["ac1_detail"]

    def test_ac_gdf_2_every_ceremony_skill_carries_the_pointer(self, live_result):
        assert live_result["ac2_ok"], live_result["ac2_detail"]

    def test_ac_gdf_3_retry_tokens_only_appear_negated(self, live_result):
        assert live_result["ac3_ok"], live_result["ac3_detail"]


# ============================================================ AC-GDF-4 negative control ==== #

def _build_valid_fixture(tmp_path):
    """Copy the REAL clause file + the REAL seven ceremony skills into a throwaway tree, so
    every mutation test starts from a tree that is known-GREEN except for its one deliberate
    change."""
    docs_dst = tmp_path / "docs"
    docs_dst.mkdir()
    shutil.copyfile(
        os.path.join(REPO_ROOT, support.CLAUSE_REL_PATH),
        docs_dst / "harness-denial-fallback.md",
    )

    skills_dst = tmp_path / "skills"
    skills_dst.mkdir()
    for relpath in support.SEVEN_CEREMONY_SKILLS:
        name = relpath.split("/")[1]
        dst_dir = skills_dst / name
        dst_dir.mkdir()
        shutil.copyfile(os.path.join(REPO_ROOT, relpath), dst_dir / "SKILL.md")


def _mutate_pointer_removed(tmp_path):
    relpath = "skills/authorize/SKILL.md"
    path = tmp_path / relpath
    text = path.read_text(encoding="utf-8")
    pointers = support.find_pointer_lines(text)
    assert pointers, "fixture precondition: expected a pointer line in skills/authorize/SKILL.md"
    remove_linenos = {ln for ln, _ in pointers}
    lines = text.split("\n")
    kept = [line for i, line in enumerate(lines, start=1) if i not in remove_linenos]
    path.write_text("\n".join(kept), encoding="utf-8")
    return relpath


def _mutate_limb_dropped(tmp_path):
    relpath = support.CLAUSE_REL_PATH
    path = tmp_path / relpath
    data = path.read_bytes()
    text = data.decode("utf-8")
    region, _n_s, _n_e, ok = support.extract_delimited_region(data, support.CLAUSE_START, support.CLAUSE_END)
    assert ok, "fixture precondition: clause region must be well-formed"
    region_text = region.decode("utf-8")
    parsed = support.parse_limbs(region_text)
    assert parsed["ok"], "fixture precondition: limbs must parse cleanly"
    b_text = parsed["limbs"][support.LIMB_B_LABEL]
    assert b_text in text
    new_text = text.replace(b_text, "", 1)
    path.write_text(new_text, encoding="utf-8")
    return relpath


def _mutate_limb_a_literal_dropped(tmp_path):
    relpath = support.CLAUSE_REL_PATH
    path = tmp_path / relpath
    text = path.read_text(encoding="utf-8")
    literal = "byte-identical"
    assert literal in text, "fixture precondition: expected limb-(a) literal not found"
    new_text = text.replace(literal, "IDENTICAL-BYTES", 1)
    assert literal not in new_text
    path.write_text(new_text, encoding="utf-8")
    return relpath


def _mutate_retry_instruction(tmp_path):
    relpath = support.CLAUSE_REL_PATH
    path = tmp_path / relpath
    text = path.read_text(encoding="utf-8")
    marker = support.LIMB_A_LABEL
    assert marker in text
    injected = "You may retry the denied call once things look fine.\n\n"
    new_text = text.replace(marker, injected + marker, 1)
    path.write_text(new_text, encoding="utf-8")
    return relpath


def _mutate_enumeration_desynced(tmp_path):
    relpath = support.CLAUSE_REL_PATH
    path = tmp_path / relpath
    text = path.read_text(encoding="utf-8")
    line_to_remove = "- `skills/id-apply/SKILL.md`"
    assert line_to_remove in text, "fixture precondition: expected skills-section list item not found"
    lines = [ln for ln in text.split("\n") if ln != line_to_remove]
    path.write_text("\n".join(lines), encoding="utf-8")
    return "skills/id-apply/SKILL.md"


_MUTATIONS = {
    "pointer-removed": (_mutate_pointer_removed, "ac2_ok", "ac2_detail"),
    "limb-dropped": (_mutate_limb_dropped, "ac1_ok", "ac1_detail"),
    "limb-a-literal-dropped": (_mutate_limb_a_literal_dropped, "ac1_ok", "ac1_detail"),
    "retry-instruction": (_mutate_retry_instruction, "ac3_ok", "ac3_detail"),
    "enumeration-desynced": (_mutate_enumeration_desynced, "ac2_ok", "ac2_detail"),
}


class TestGateDenialFallbackNegativeControl:
    @pytest.mark.parametrize("mutation_slug", sorted(_MUTATIONS), ids=sorted(_MUTATIONS))
    def test_ac_gdf_4_mutated_fixture_is_red(self, tmp_path, mutation_slug):
        mutate_fn, ok_key, detail_key = _MUTATIONS[mutation_slug]
        _build_valid_fixture(tmp_path)

        # Sanity: the freshly-copied, unmutated fixture must itself be GREEN across all three
        # invariants — otherwise a failure below wouldn't be attributable to the mutation.
        pre = support.evaluate_gate_denial_fallback(str(tmp_path))
        assert pre["ac1_ok"] and pre["ac2_ok"] and pre["ac3_ok"], (
            mutation_slug, "fixture not green before mutation",
            pre["ac1_detail"], pre["ac2_detail"], pre["ac3_detail"],
        )

        offending = mutate_fn(tmp_path)

        post = support.evaluate_gate_denial_fallback(str(tmp_path))
        assert post[ok_key] is False, (mutation_slug, "mutation did not flip the criterion RED", post)
        detail_text = " ".join(post[detail_key])
        assert offending in detail_text, (mutation_slug, offending, detail_text)
