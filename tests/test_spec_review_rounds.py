"""charter `coherence-and-two-rounds` (R0 `ac-r0-living-spec-process`) — corpus coherence blocks
the first authorization; two review rounds, then ship or park.

`scripts/foundry-coherence-check.py` already existed and was unused by `/foundry:spec-review`
(AC-CTR-1/-2). This module is out of scope to change its detection logic — it only detects broken
/ malformed citations, never a semantic "contradiction." The seeded fixture pair
(`tests/fixtures/coherence/specs/atom-a/`, `.../atom-b/`) encodes a real-world drift as a broken
citation: atom-a still cites atom-b's pre-migration filename and an AC-ID atom-b no longer makes,
so the broken edge IS the machine-detectable trace of the two atoms' disagreement (30 vs 5
minutes) — exactly the "two atoms each did what they said, and nothing owned the gap" case the
charter names. `test_contradiction_names_both` drives the REAL CLI over that fixture and asserts
both owning atoms are derivable from the reported finding, per the naming rule
`skills/spec-review/SKILL.md` Phase 0 documents.

`test_block_without_locator_is_risk` and `test_third_round_is_operator_fork` exercise small pure
functions defined in THIS test file (there is no helper script under `skills/spec-review/` to add
one to, and the charter's scope forbids adding one) that mirror the rule text
`agents/spec-reviewer.md` and `skills/spec-review/SKILL.md` document for a human/agent to apply;
each test also greps the two documents for the rule so the documented contract and the tested
behavior cannot silently drift apart.
"""
import json
import os
import re
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COHERENCE_CHECK = os.path.join(REPO_ROOT, "scripts", "foundry-coherence-check.py")
FIXTURE_ROOT = os.path.join(REPO_ROOT, "tests", "fixtures", "coherence")
SKILL_MD = os.path.join(REPO_ROOT, "skills", "spec-review", "SKILL.md")
REVIEWER_MD = os.path.join(REPO_ROOT, "agents", "spec-reviewer.md")


# --------------------------------------------------------------------------------------- #
# Pure helpers mirroring the documented rules (AC-CTR-2/-4).
# --------------------------------------------------------------------------------------- #

def owning_atom(specs_relative_path: str) -> str | None:
    """The path segment immediately after `specs/` — the owning-atom derivation
    `skills/spec-review/SKILL.md` Phase 0 documents. Works on a raw (unresolved) target string
    too: the citation names the atom directory regardless of whether the file exists."""
    m = re.search(r"specs/([^/]+)/", specs_relative_path)
    return m.group(1) if m else None


_CHECKPOINT_RE = re.compile(r"AC-[A-Z0-9]+-\d+")
_FILE_LINE_RE = re.compile(r"[\w./-]+\.\w+:\d+")


def classify_finding(category: str, text: str) -> str:
    """AC-CTR-4/-5: a Block citing neither a checkpoint id nor a file:line locator downgrades
    to Risk. Any other category (or a located Block) passes through unchanged."""
    if category != "Block":
        return category
    if _CHECKPOINT_RE.search(text) or _FILE_LINE_RE.search(text):
        return "Block"
    return "Risk"


def allowed_round(round_number: int, operator_fork_recorded: bool) -> bool:
    """AC-CTR-3: rounds 1 and 2 always allowed; round 3+ only with a recorded operator fork."""
    if round_number <= 2:
        return True
    return operator_fork_recorded


# --------------------------------------------------------------------------------------- #
# AC-CTR-1/-2 — the coherence check runs over the release and names both owning atoms.
# --------------------------------------------------------------------------------------- #

def test_contradiction_names_both():
    env = dict(os.environ, CLAUDE_PROJECT_DIR=FIXTURE_ROOT)
    r = subprocess.run(
        ["python3", COHERENCE_CHECK, "--scope", "specs"],
        capture_output=True, text=True, timeout=30, env=env, cwd=FIXTURE_ROOT,
    )
    # AC-CTR-1: the check ran over the seeded release corpus and reported >=1 broken finding
    # (exit 1 — advisory outside spec-review, a coherence Block inside it per AC-CTR-2).
    assert r.returncode == 1, f"expected the seeded contradiction to report broken>=1: {r.stdout} {r.stderr}"
    report = json.loads(r.stdout)
    assert report["counts"]["broken"] >= 1
    findings = report["findings"]
    assert findings, "expected the seeded contradiction to appear in findings"

    named = set()
    for f in findings:
        src_atom = owning_atom(f["src"])
        target_atom = owning_atom(f["target"])
        if src_atom:
            named.add(src_atom)
        if target_atom:
            named.add(target_atom)

    # AC-CTR-2: a Block over this report must name BOTH owning atoms — atom-a (the citing spec)
    # and atom-b (the target, even though its raw citation string never resolved to a real file).
    assert "atom-a" in named, f"citing atom not named: {named}"
    assert "atom-b" in named, f"cited (unresolved) atom not named: {named}"


def test_coherence_rule_is_documented_in_skill():
    text = open(SKILL_MD, encoding="utf-8").read()
    assert "foundry-coherence-check.py" in text
    assert "--scope specs" in text
    assert "owning atom" in text
    assert "BOTH owning atoms" in text or "both owning atoms" in text.lower()


# --------------------------------------------------------------------------------------- #
# AC-CTR-4/-5 — a Block without a locator downgrades to Risk.
# --------------------------------------------------------------------------------------- #

def test_block_without_locator_is_risk():
    assert classify_finding("Block", "the criterion is vague and hard to verify") == "Risk"
    assert classify_finding("Block", "AC-FOO-1 is unmeasurable, drop the adjective") == "Block"
    assert classify_finding("Block", "spec.md:42 has a dangling reference") == "Block"
    # Risk/Nit are never touched by the locator rule.
    assert classify_finding("Risk", "thin prior-art grounding, no locator given") == "Risk"
    assert classify_finding("Nit", "typo") == "Nit"


def test_locator_rule_is_documented_in_both_files():
    for path in (SKILL_MD, REVIEWER_MD):
        text = open(path, encoding="utf-8").read()
        assert "AC-CTR-4" in text or "AC-<TOKEN>-<n>" in text
        assert "file:line" in text
        assert "Risk" in text and "Block" in text


# --------------------------------------------------------------------------------------- #
# AC-CTR-3 — two rounds, then ship or park; round 3 is the operator's fork.
# --------------------------------------------------------------------------------------- #

def test_third_round_is_operator_fork():
    assert allowed_round(1, operator_fork_recorded=False) is True
    assert allowed_round(2, operator_fork_recorded=False) is True
    assert allowed_round(3, operator_fork_recorded=False) is False
    assert allowed_round(3, operator_fork_recorded=True) is True
    assert allowed_round(4, operator_fork_recorded=False) is False


def test_round_budget_is_documented_in_skill():
    text = open(SKILL_MD, encoding="utf-8").read()
    normalized = re.sub(r"\s+", " ", text)
    assert "one further remediation round" in normalized
    assert "## Clarifications" in text
    assert "no automatic third round" in normalized.lower() or "never a silent automatic loop" in normalized
