"""tests/test_charter_default_routing.py — charter `charter-default-routing`
(`.foundry/releases/ac-r0-living-spec-process/charters/charter-default-routing.md`).

Measured: piiq authorized 462 atoms and built 24; 438 authorized-never-built came from bulk
pre-authorization of a backlog. The operator decided the charter lane is the default lane and
authorization is just-in-time (`.foundry/decisions/2026-09-18-spec-is-a-living-document.md`,
decision 4 in `.foundry/decisions/2026-09-18-autonomy-continuation-forks-resolved.md`). This
atom makes `/foundry:intake` route each atom to a lane by Beck's game test, retires the
bulk-signing loop in `authorize-release`, and updates the posture text to match.

AC-CDR-1: `skills/intake/SKILL.md` routes an atom to the charter lane by default; a
`security: true` atom, or one whose scope names auth/secrets/custody/a production
mutation/a cross-repo pin, routes to the factory lane instead.
AC-CDR-2: an operator lane override is recorded in the atom's charter `## Amendments` table
or spec `## Clarifications` section — never silently.
AC-CDR-3/4: `skills/authorize-release/SKILL.md` displays the release's atoms with lane +
readiness and routes only the next unblocked atom to a single `/foundry:authorize`
invocation — no procedure authorizes more than one atom per operator confirmation.
AC-CDR-5: `skills/mode/SKILL.md`'s noninteractive paragraph states the charter is the default
lane for product work and the factory lane is reserved for the security set.
"""
from __future__ import annotations

import os
import re

from conftest import REPO_ROOT

INTAKE_SKILL = os.path.join(REPO_ROOT, "skills", "intake", "SKILL.md")
AUTHORIZE_RELEASE_SKILL = os.path.join(REPO_ROOT, "skills", "authorize-release", "SKILL.md")
MODE_SKILL = os.path.join(REPO_ROOT, "skills", "mode", "SKILL.md")

# The exact security-set trigger surfaces named by AC-CDR-1 / the charter.
_SECURITY_TRIGGERS = ["auth", "secrets", "custody", "production mutation", "cross-repo pin"]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestIntakeRoutesByTheGameTest:
    """AC-CDR-1: intake routes to the charter lane by default, factory lane for the
    security set."""

    def test_charter_lane_named_as_the_default(self):
        text = _read(INTAKE_SKILL)
        assert re.search(r"charter lane[^.\n]*default", text, re.IGNORECASE) or re.search(
            r"default[^.\n]*charter lane", text, re.IGNORECASE
        ), "intake must state the charter lane is the default"

    def test_factory_lane_named_for_the_security_set(self):
        text = _read(INTAKE_SKILL)
        assert "factory lane" in text
        assert "security" in text.lower()

    def test_all_five_security_triggers_named(self):
        text = _read(INTAKE_SKILL).lower()
        for trigger in _SECURITY_TRIGGERS:
            assert trigger in text, f"missing security-set trigger: {trigger!r}"

    def test_mandatory_review_security_trigger_named(self):
        text = _read(INTAKE_SKILL)
        assert "mandatory_review" in text

    def test_lane_routing_section_present(self):
        text = _read(INTAKE_SKILL)
        assert re.search(r"^## Lane routing", text, re.MULTILINE)


class TestIntakeRecordsOperatorOverride:
    """AC-CDR-2: an operator lane override is recorded, never silent."""

    def test_override_recorded_in_charter_amendments_or_spec_clarifications(self):
        text = _read(INTAKE_SKILL)
        section_match = re.search(r"^## Lane routing.*", text, re.MULTILINE | re.DOTALL)
        assert section_match, "expected a 'Lane routing' section"
        section = section_match.group(0)
        assert "override" in section.lower()
        assert "## Amendments" in section
        assert "## Clarifications" in section

    def test_override_is_not_silent(self):
        text = _read(INTAKE_SKILL)
        assert "never silently" in text or "not silently" in text.lower()


class TestAuthorizeReleaseRetiresTheBulkLoop:
    """AC-CDR-3/4: display + just-in-time single-atom routing; no bulk-sign loop."""

    def test_no_procedural_batch_loop_wording(self):
        text = _read(AUTHORIZE_RELEASE_SKILL)
        count = len(re.findall(r"for each atom|every atom|batch", text, re.IGNORECASE))
        assert count == 0, (
            f"skills/authorize-release/SKILL.md must contain no batch-loop wording "
            f"(found {count} match(es))"
        )

    def test_displays_atoms_with_lane_and_readiness(self):
        text = _read(AUTHORIZE_RELEASE_SKILL).lower()
        assert "lane" in text
        assert "readiness" in text

    def test_routes_only_the_next_atom(self):
        text = _read(AUTHORIZE_RELEASE_SKILL)
        assert re.search(r"\bnext\b.*atom|atom.*\bnext\b", text, re.IGNORECASE)
        assert "depends_on" in text

    def test_single_authorize_invocation_per_atom(self):
        text = _read(AUTHORIZE_RELEASE_SKILL)
        # exactly one bash code fence shows the routing mechanism — no loop over N fences
        assert text.count("```bash") == 1
        assert text.count("```") == 2  # one fenced block: one open, one close

    def test_no_all_flag_used_on_the_authorize_script(self):
        text = _read(AUTHORIZE_RELEASE_SKILL)
        fence_start = text.index("```bash")
        fence_end = text.index("```", fence_start + len("```bash"))
        invocation = text[fence_start:fence_end]
        assert "--all" not in invocation


class TestModeSkillStatesCharterDefault:
    """AC-CDR-5: the noninteractive paragraph states charter is the default lane, factory
    reserved for the security set."""

    def test_noninteractive_paragraph_names_charter_as_default(self):
        text = _read(MODE_SKILL)
        idx = text.index("- **`noninteractive`**")
        # bound to the next bullet (`- **`interactive`**`) or end of section
        next_bullet = text.index("- **`interactive`**", idx)
        paragraph = text[idx:next_bullet]
        assert "charter lane" in paragraph
        assert "default" in paragraph.lower()
        assert "factory lane" in paragraph
        assert "security" in paragraph.lower()


class TestFoundryAuthorizeScriptUnchanged:
    """Out-of-scope confirmation: `scripts/foundry-authorize.py` already authorizes one atom
    per call — this atom must not add a batch/`--all` mode to it (denied_paths)."""

    def test_script_has_no_all_flag(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "foundry-authorize.py")
        text = _read(script_path)
        assert "--all" not in text
        assert text.count('ap.add_argument("--spec"') == 1
        assert text.count('ap.add_argument("--contract"') == 1
