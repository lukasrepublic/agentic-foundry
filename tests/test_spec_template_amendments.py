"""tests/test_spec_template_amendments.py — charter `spec-template-amendments-section`
(`.foundry/releases/ac-r0-living-spec-process/charters/spec-template-amendments-section.md`).

The spec is a living document (`.foundry/decisions/2026-09-18-spec-is-a-living-document.md`):
amendment during implementation is the normal path, not an exception that re-enters the front
gate. This atom gives both templates a place to record one, and teaches intake to emit it.

AC-AMD-1/2: both `context/feat-spec-template.md` and `context/charter-template.md` carry a
`## Amendments` section (spec template: after `## Clarifications`, outside the normative
region; charter template: inside the copyable charter block).
AC-AMD-3 (the workspace `CONSTITUTION.md` §12 wave-1-depth sentence) is out of this repo's
tree by design (the charter's Scope names the WORKSPACE file, not a path here) and is verified
by the charter's own `grep` command against the workspace checkout, not by this suite.
AC-AMD-4: `skills/intake/SKILL.md` "The template shape" section names `## Amendments` as a
required non-normative section intake emits empty.
AC-AMD-5: `scripts/foundry-spec-lint.py` passes unchanged on a spec rendered from the updated
template (the section adds no AC and sits outside `<!-- normative -->`).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

from conftest import REPO_ROOT

FEAT_SPEC_TEMPLATE = os.path.join(REPO_ROOT, "context", "feat-spec-template.md")
CHARTER_TEMPLATE = os.path.join(REPO_ROOT, "context", "charter-template.md")
INTAKE_SKILL = os.path.join(REPO_ROOT, "skills", "intake", "SKILL.md")
SPEC_LINT_SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-spec-lint.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestSpecTemplateHasAmendments:
    """AC-AMD-1: `## Amendments` in the spec template, after `## Clarifications`, outside the
    normative region."""

    def test_amendments_section_present(self):
        text = _read(FEAT_SPEC_TEMPLATE)
        assert re.search(r"^## Amendments$", text, re.MULTILINE), (
            "context/feat-spec-template.md must carry a top-level `## Amendments` section"
        )

    def test_amendments_has_the_required_columns(self):
        text = _read(FEAT_SPEC_TEMPLATE)
        section = text.split("## Amendments", 1)[1]
        table_block = section.split("##", 1)[0]
        assert "date" in table_block and "what changed" in table_block
        assert "why reality required it" in table_block and "auth_seq" in table_block

    def test_amendments_placed_after_clarifications(self):
        text = _read(FEAT_SPEC_TEMPLATE)
        idx_clar = text.index("## Clarifications")
        idx_amend = text.index("## Amendments")
        assert idx_clar < idx_amend, (
            "`## Amendments` must come after `## Clarifications` per AC-AMD-1"
        )

    def test_amendments_section_is_outside_the_normative_region(self):
        text = _read(FEAT_SPEC_TEMPLATE)
        normative_start = text.index("<!-- normative -->")
        normative_end = text.index("<!-- /normative -->") + len("<!-- /normative -->")
        idx_amend = text.index("## Amendments")
        assert not (normative_start <= idx_amend <= normative_end), (
            "`## Amendments` must sit outside the <!-- normative --> delimited region"
        )


class TestCharterTemplateHasAmendments:
    """AC-AMD-2: the same `## Amendments` table inside the copyable charter block."""

    def test_amendments_section_present(self):
        text = _read(CHARTER_TEMPLATE)
        assert re.search(r"^## Amendments$", text, re.MULTILINE), (
            "context/charter-template.md must carry a top-level `## Amendments` section"
        )

    def test_amendments_inside_the_copyable_block(self):
        text = _read(CHARTER_TEMPLATE)
        fence_start = text.index("```markdown")
        fence_end = text.index("```", fence_start + len("```markdown"))
        idx_amend = text.index("## Amendments")
        assert fence_start < idx_amend < fence_end, (
            "`## Amendments` must be inside the ```markdown copyable charter block"
        )

    def test_amendments_has_the_required_columns(self):
        text = _read(CHARTER_TEMPLATE)
        section = text.split("## Amendments", 1)[1]
        table_block = section.split("```", 1)[0]
        assert "date" in table_block and "what changed" in table_block
        assert "why reality required it" in table_block and "auth_seq" in table_block


class TestIntakeSkillNamesAmendments:
    """AC-AMD-4: "The template shape" section names `## Amendments` as a required
    non-normative section intake emits empty."""

    def test_template_shape_section_mentions_amendments(self):
        text = _read(INTAKE_SKILL)
        shape_start = text.index("## The template shape")
        # next top-level heading closes the section
        next_heading = re.search(r"\n## ", text[shape_start + len("## The template shape"):])
        shape_end = (
            shape_start + len("## The template shape") + next_heading.start()
            if next_heading
            else len(text)
        )
        section = text[shape_start:shape_end]
        assert "## Amendments" in section
        assert "empty" in section.lower()


class TestRenderedSpecPassesLintUnchanged:
    """AC-AMD-5: `foundry-spec-lint.py` still exits 0 on a spec rendered from the updated
    template — the `## Amendments` section adds no AC-ID and sits outside the normative
    region, so size + reference-closure checks are unaffected."""

    def test_lint_passes_on_a_template_rendered_spec(self, tmp_path):
        template_text = _read(FEAT_SPEC_TEMPLATE)
        # Render: fill the one placeholder AC-ID token pair so the normative region has a
        # real, well-formed criterion instead of a literal <TOKEN> placeholder.
        rendered = template_text.replace("<TOKEN>", "RENDER")
        spec_path = tmp_path / "feat-rendered-spec.md"
        spec_path.write_text(rendered, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, SPEC_LINT_SCRIPT, str(spec_path), "--project-dir", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        assert "OK" in r.stdout

    def test_rendered_spec_amendments_table_has_no_ac_rows(self, tmp_path):
        """The rendered `## Amendments` table contributes zero AC-IDs — confirms AC-AMD-5's
        "adds no AC" premise directly against the size counter, not just the lint's exit code."""
        prep_path = os.path.join(REPO_ROOT, "scripts", "foundry-audit-prepare.py")
        import importlib.util
        spec = importlib.util.spec_from_file_location("foundry_audit_prepare_amend_test", prep_path)
        prep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prep)

        template_text = _read(FEAT_SPEC_TEMPLATE)
        amendments_block = template_text.split("## Amendments", 1)[1].split("##", 1)[0]
        ac_count, _ = prep.spec_size_metrics(amendments_block)
        assert ac_count == 0
