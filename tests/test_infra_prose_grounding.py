"""tests/test_infra_prose_grounding.py — prose-grounding invariants for the infra-delivery surface.

Asserts that the shipped prose describes the reality the code atoms ship: the framework EXECUTES
standard `tofu` / `kubectl` / `aws` against the AWS context the OPERATOR has already configured,
whose IAM restrictions are the control and are outside the framework's scope. There is no posture,
no prod-vs-non-prod branch, no guard state, no break-glass.

Each test below is a POSITIVE invariant over a named file: it asserts what the prose must SAY, not
what it must avoid saying. The module's former absence-sweeps — a frozen token set and a
forbidden-phrase set evaluated over an enumerated file surface — were retired once the retired
framework was gone from the tree, on the industry lifecycle for migration scaffolding: a guard is
removed when the condition it detects becomes unrepresentable, rather than carried forever as
inventory. What survives here is the coverage that guards the REPLACEMENT doctrine, which stays
load-bearing indefinitely.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT

ROOT = Path(REPO_ROOT)


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


_LEADING_COMMENT_MARKER_RE = re.compile(r"^\s*#\s?")


def _norm(text: str) -> str:
    """Collapse every run of whitespace (including a line wrap) to one space. A leading `#`
    shell/python comment marker is stripped per-line FIRST, so a phrase that line-wraps across
    consecutive `#`-prefixed comment lines (e.g. a bash header paragraph) reads as continuous
    prose rather than being interrupted by a literal `#` at the join point."""
    stripped = "\n".join(_LEADING_COMMENT_MARKER_RE.sub("", ln) for ln in text.splitlines())
    return re.sub(r"\s+", " ", stripped)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-2 ═════

def test_id_apply_states_operator_supplied_context_and_no_credential_acquisition():
    text = _read("skills/id-apply/SKILL.md")
    norm = _norm(text)
    assert re.search(
        r"(operator has (already )?configured.{0,10}AWS context"
        r"|AWS context.{0,10}the operator has (already )?configured)",
        norm, re.I,
    )
    assert re.search(r"IAM restrictions.{0,40}(are|is) the control", norm, re.I)
    assert re.search(r"outside the framework.s scope", norm, re.I)
    assert re.search(r"never acquires credentials or connectivity", norm, re.I)
    for verb in ["aws sso login", "aws configure", "assume-role", "VPN"]:
        assert verb.lower() in norm.lower(), f"missing named non-action: {verb!r}"


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-3 ═════

def test_id_apply_frames_gitops_routing_as_correctness():
    text = _read("skills/id-apply/SKILL.md")
    norm = _norm(text)
    assert "classify_gitops" in norm
    assert "gitops_paths" in norm
    assert re.search(r"ArgoCD controller reconciles it", norm, re.I)
    assert re.search(r"fight the controller", norm, re.I)
    assert re.search(r"correctness", norm, re.I)
    assert re.search(r"read-only.{0,20}infra_binding\.verify", norm, re.I)
    assert re.search(r"frozen.{0,20}infra_binding\.apply", norm, re.I)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-4 ═════

@pytest.mark.parametrize("case", ["literal_plan_path_both_renderings", "no_placeholder_either_rendering"])
def test_id_apply_states_saved_plan_apply_as_correctness(case):
    text = _read("skills/id-apply/SKILL.md")
    norm = _norm(text)
    if case == "literal_plan_path_both_renderings":
        assert "-out=.foundry/infra.tfplan" in norm, "plan-slot rendering must name the literal path"
        assert re.search(r"apply.{0,60}\.foundry/infra\.tfplan", norm), (
            "apply-slot rendering must consume the same literal path"
        )
        assert re.search(r"exactly what was planned", norm, re.I)
    elif case == "no_placeholder_either_rendering":
        assert "<planfile>" not in text
        assert not re.search(r"<[a-zA-Z_-]*plan[a-zA-Z_-]*>", text), (
            "no angle-bracket placeholder may stand in for the plan path"
        )


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-5 ═════

def test_glossary_defines_the_guarded_exec_wrapper():
    text = _read("docs/glossary.md")
    norm = _norm(text)
    assert "guarded-exec wrapper" in norm.lower()
    assert "cloud_cli_exec_guard" in norm
    assert re.search(r"adopter-supplied", norm, re.I)
    assert re.search(r"config-gated.{0,20}fail-INERT", norm, re.I)
    assert re.search(r"defense-in-depth", norm, re.I)
    assert re.search(r"not a security boundary", norm, re.I)
    assert re.search(r"IAM restrictions", norm, re.I)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-6 ═════

@pytest.mark.parametrize("relpath", [
    "packs/stack-profiles/aws-eks-karpenter/conventions.md",
    "packs/stack-profiles/aws-eks-karpenter/skills/implement-aws-eks-karpenter.md",
])
def test_pack_files_reattribute_slot_pinning_and_apply_execution(relpath):
    norm = _norm(_read(relpath))
    assert re.search(r"read-only leading-verb allowlist", norm, re.I)
    assert "foundry-stack-profile.py" in norm or "foundry_stack_profile" in norm
    assert re.search(r"id-apply", norm)
    assert re.search(r"AWS context the operator has (already )?configured", norm, re.I)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-7 ═════

def test_stack_profile_loader_comments_reattribute_the_read_verb_allowlist():
    norm = _norm(_read("scripts/foundry-stack-profile.py"))
    assert re.search(r"this loader.s own static validation", norm, re.I)
    assert re.search(r"sole (floor|static floor)", norm, re.I)
    assert re.search(r"no external runtime enforcement backs it", norm, re.I)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-8 ═════

@pytest.mark.parametrize("relpath", [
    "hooks/foundry-cloud-cli-exec-guard.sh",
    "scripts/foundry-decommission.py",
])
def test_exec_guard_and_decommission_classify_the_guard_as_defense_in_depth(relpath):
    norm = _norm(_read(relpath))
    assert re.search(r"adopter-configured", norm, re.I)
    assert re.search(r"config-gated", norm, re.I)
    assert re.search(r"fail-INERT", norm, re.I)
    assert re.search(r"defense-in-depth against the framework.s own mistakes", norm, re.I)
    assert re.search(r"not a security boundary", norm, re.I)
    assert re.search(r"IAM", norm)
    assert re.search(r"(load-bearing control|credential scoping)", norm, re.I)


# ══════════════════════════════════════════════════════════════════════════════════ AC-CXD-9 ═════

def test_wrapper_reference_examples_use_the_generic_placeholder():
    guard = _read("hooks/foundry-cloud-cli-exec-guard.sh")
    fixtures = _read("tests/test_hooks_guards.py")
    assert "exec-wrapper" in guard
    assert "exec-wrapper" in fixtures


# ═══════════════════════════════════════════════════════════════════════════════ AC-CXD-11 ═════

@pytest.mark.parametrize("relpath,heading_needle", [
    ("skills/id-validate/SKILL.md", "Offline (no-guarded-exec) mode"),
    ("skills/id-test/SKILL.md", "Offline (no-guarded-exec) mode"),
    ("skills/id-simulate/SKILL.md", "OFFLINE"),
    ("skills/id-architect/SKILL.md", None),
])
def test_offline_steps_restate_the_offline_truth_without_a_session(relpath, heading_needle):
    text = _read(relpath)
    norm = _norm(text)
    assert re.search(r"entirely offline", norm, re.I)
    assert re.search(r"no live cloud account", norm, re.I)
    assert re.search(r"no (live )?cluster", norm, re.I)
    assert re.search(r"no credentials", norm, re.I)
    assert re.search(r"issues no mutating command", norm, re.I) or re.search(
        r"never issues a mutating verb", norm, re.I
    )
    if heading_needle is not None:
        headings = [ln for ln in text.splitlines() if ln.startswith("#")]
        assert any(heading_needle in h for h in headings), (
            f"{relpath} must carry a first-class heading containing {heading_needle!r}; "
            f"headings found: {headings}"
        )


def test_id_validate_and_id_test_heading_is_the_frozen_AC_IDOFF_1_marker_verbatim():
    """Supersession clause: the exact literal `## Offline (no-guarded-exec) mode` marker
    (AC-IDOFF-1, frozen) must survive byte-for-byte — this atom supersedes only the session framing
    inside the section, never the heading itself."""
    for relpath in ("skills/id-validate/SKILL.md", "skills/id-test/SKILL.md"):
        text = _read(relpath)
        assert "## Offline (no-guarded-exec) mode" in text, relpath


# ═══════════════════════════════════════════════════════════════════════════════ AC-CXD-12 ═════

@pytest.mark.parametrize("case", ["frontmatter_description", "body"])
def test_id_promote_describes_per_environment_execution(case):
    text = _read("skills/id-promote/SKILL.md")
    if case == "frontmatter_description":
        fm_match = re.search(r"^---\n(.*?)\n---", text, re.S)
        assert fm_match, "id-promote must carry a YAML frontmatter block"
        fm = _norm(fm_match.group(1))
        assert re.search(r"changed_paths", fm)
        assert re.search(r"(GitOps class|classify_gitops)", fm, re.I)
        assert re.search(r"per env|target env|target environment", fm, re.I)
        assert re.search(r"id-apply", fm)
        assert re.search(r"AWS context.{0,40}operator.{0,30}(already )?configured", fm, re.I)
    elif case == "body":
        body = _norm(text)
        assert re.search(r"re-derive[sd]?.{0,40}(change scope|changed_paths)", body, re.I)
        assert re.search(r"GitOps class", body, re.I) or re.search(r"classify_gitops", body)
        assert re.search(r"each environment.s apply", body, re.I) or re.search(
            r"target environment.s apply", body, re.I
        )
        assert re.search(r"id-apply", body)
        assert re.search(
            r"AWS context the operator has (already )?configured for (that|the target) environment",
            body, re.I,
        )


# ═══════ Retired-NARRATIVE guard — token-free by construction, and NOT spent scaffolding ══════ #
# These forbidden phrases carry no retired-framework token at all, which is precisely their point:
# they close
# the hole where a minimal edit deletes the retired identifier and leaves the retired prod-branch
# narrative intact under a different name. That condition is NOT made unrepresentable by removing
# the identifier, so unlike the token sweep this guard is kept. Only the single token-bearing
# pattern was dropped; the identifier it named no longer exists.
_FORBIDDEN_PATTERNS = [
    r"posture[ -]gated",
    r"posture gate",
    r"guarded[ -]prod",
    r"break[ _-]glass",
    r"GENERATE_RUNBOOK",
    r"resolve_posture",
    r"Posture\.decision",
    r"production_flag",
    r"guard_state",
    r"stale_state",
    r"command-policy",
]
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS), re.IGNORECASE)

SURFACE_FILES = [
    "skills/id-apply/SKILL.md",
    "skills/id-promote/SKILL.md",
    "skills/id-simulate/SKILL.md",
    "skills/id-test/SKILL.md",
    "skills/id-validate/SKILL.md",
    "skills/id-drift/SKILL.md",
    "skills/id-architect/SKILL.md",
    "skills/id-rollback/SKILL.md",
    "skills/id-discover/SKILL.md",
    "skills/id-plan/SKILL.md",
    "skills/id-verify/SKILL.md",
    "skills/id-import/SKILL.md",
    "skills/id-implement/SKILL.md",
    "skills/id-baseline/SKILL.md",
    "skills/id-sync/SKILL.md",
    "skills/infra-sandboxed-apply/SKILL.md",
    "skills/fleet/SKILL.md",
    "agents/infra-engineer.md",
    "docs/glossary.md",
    "packs/stack-profiles/aws-eks-karpenter/conventions.md",
    "packs/stack-profiles/aws-eks-karpenter/skills/implement-aws-eks-karpenter.md",
    "scripts/foundry-stack-profile.py",
    "scripts/foundry-decommission.py",
    "hooks/foundry-cloud-cli-exec-guard.sh",
    "tests/test_hooks_guards.py",
]

# The single named carve-out (AC-CXD-1): the sentinel line + its two-line print-format follow-on in
# scripts/foundry-stack-profile.py. Located by content, not a hardcoded line number, so an unrelated
# line shift elsewhere in the file does not churn this test.

# ═══════════════════════════════════════════════════════════════════════════════ AC-CXD-13 ═════

# Row 1: whole-surface zero-hit assertion.
# Rows 2-6: PRECISION NEGATIVE CONTROLS — five live, unrelated strings the forbidden set must NOT
# match (lifted verbatim from the shipped tree, per the sibling contract's preamble).
# Row 7: RECALL CONTROL — five strings lifted verbatim from the PRE-CHANGE surface (none carries a
# retired token), proving the forbidden-phrase set is non-vacuous, bundled as one row (any miss fails
# the row).
_PRECISION_NEGATIVE_CONTROLS = [
    ("test_hooks_guards_session_posture", 'assert "posture: " in p.stdout'),
    ("exec_guard_wrapper_owns_posture", "the wrapper owns posture"),
    ("exec_guard_config_gated_inert_posture", "CONFIG-GATED-INERT posture"),
    ("conventions_autonomous_operations_posture", "an autonomous-operations posture"),
    # NOTE: unlike the other four, this fifth control's literal string DOES contain the frozen
    # set's bare `break[ _-]glass` member (the set is not word/context-narrowed on that member) —
    # so pattern-level non-match is not the mechanism that keeps this live, unrelated use safe.
    # Its precision is SCOPE-level: `agents/security-reviewer.md` (where this string ships) is not
    # in SURFACE_FILES, so the surface-scoped AC-CXD-13 scan never encounters it. See the dedicated
    # scope-precision assertion below rather than a raw pattern non-match for this one.
    ("security_reviewer_break_glass_admin", None),
]

_RECALL_CONTROLS = [
    ("id_implement_posture_gated", "mutation is the posture-gated"),
    ("infra_engineer_posture_gate", "id-apply posture gate"),
    ("id_implement_guarded_prod", "in guarded prod"),
    ("infra_sandboxed_apply_generate_runbook", "GENERATE_RUNBOOK"),
    ("id_baseline_guarded_prod_posture", "even under the guarded-prod posture"),
]

_AC_CXD_13_CASES = (
    [("surface_is_clean", None)]
    + [(f"precision_negative__{name}", literal) for name, literal in _PRECISION_NEGATIVE_CONTROLS]
    + [("recall_pre_change_surface", None)]
)


@pytest.mark.parametrize(
    "case,literal",
    _AC_CXD_13_CASES,
    ids=[name for name, _ in _AC_CXD_13_CASES],
)
def test_atom_surface_is_free_of_the_abolished_narrative(case, literal):
    if case == "surface_is_clean":
        offenders = []
        for relpath in SURFACE_FILES:
            text = _norm(_read(relpath))
            if _FORBIDDEN_RE.search(text):
                offenders.append(relpath)
        assert not offenders, f"forbidden-phrase (abolished-narrative) hit(s) in: {offenders}"

    elif case == "recall_pre_change_surface":
        # Non-vacuousness (RECALL): the set must still fire on real pre-change strings that carry
        # no retired token, proving the "surface_is_clean" zero-hit result is not trivially true of
        # an empty/no-op pattern.
        misses = [lit for _, lit in _RECALL_CONTROLS if _FORBIDDEN_RE.search(_norm(lit)) is None]
        assert not misses, f"forbidden-phrase set failed to match known pre-change strings: {misses}"

    elif case == "precision_negative__security_reviewer_break_glass_admin":
        # Scope-level precision (see the _PRECISION_NEGATIVE_CONTROLS note above): the live
        # `break-glass admin exception` use ships in agents/security-reviewer.md:110, a file that
        # is NOT part of this atom's declared surface — so the surface-scoped AC-CXD-13 check never
        # scans it, and its own text is confirmed to genuinely carry the string (not a stale claim).
        assert "agents/security-reviewer.md" not in SURFACE_FILES
        assert "break-glass admin exception" in _read("agents/security-reviewer.md")

    else:
        # A PRECISION negative control: the frozen set must NOT match this live, unrelated string.
        assert _FORBIDDEN_RE.search(_norm(literal)) is None, (
            f"the forbidden-phrase set wrongly matches the live, unrelated string {literal!r}"
        )
