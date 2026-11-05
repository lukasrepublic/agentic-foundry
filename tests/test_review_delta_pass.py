"""dl-08: one full review, one scoped delta review, then the security label (decision 5)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISPATCH = ROOT / "skills" / "dispatch" / "SKILL.md"
MWG = ROOT / "skills" / "merge-when-green" / "SKILL.md"
PRR = ROOT / "agents" / "pr-reviewer.md"
SECR = ROOT / "agents" / "security-reviewer.md"
GATE = ROOT / "docs" / "how-to" / "recover-from-a-failed-gate.md"


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _sections(text, pattern):
    """Split text into {key: body} at lines matching pattern (group 1 is the key)."""
    out, key, buf = {}, None, []
    for ln in text.splitlines():
        m = re.match(pattern, ln)
        if m:
            if key is not None:
                out[key] = "\n".join(buf)
            key, buf = m.group(1), [ln]
        elif key is not None:
            buf.append(ln)
    if key is not None:
        out[key] = "\n".join(buf)
    return out


def _dispatch_steps():
    """Top-level numbered steps of the dispatch procedure, keyed by step id (0, 0b, 1, ...)."""
    body = DISPATCH.read_text()
    proc = body[body.index("## Procedure (single ticket)"):]
    proc = proc[: proc.index("\n## ", 5)]
    return _sections(proc, r"^(\d+b?)\. \*\*")


def _h2(path):
    return _sections(path.read_text(), r"^## (.+)$")


def test_dispatch_step_order_keeps_prior_steps():
    steps = list(_dispatch_steps())
    assert steps.index("0") < steps.index("0b") < steps.index("1")
    assert steps.index("4") < steps.index("4b") < steps.index("5")


def test_dispatch_review_rounds_step_carries_the_rule():
    step = _flat(_dispatch_steps()["4b"])
    for needle in ("ONE full review round", "no delta review", "ONE fresh-context delta review",
                   "only the fix commits and those Blocks", "filed item", "left out of the delta brief",
                   "only after the delta pass is clean on the current head",
                   "at most one review agent per PR", "without polling CI"):
        assert needle in step, needle


def test_mwg_has_review_rounds_section_before_procedure_with_six_rules():
    heads = list(_h2(MWG))
    assert heads.index("Review rounds before the merge (decision 5)") < heads.index("Procedure")
    sec = _h2(MWG)["Review rounds before the merge (decision 5)"]
    rules = _sections(sec, r"^(\d+)\. ")
    assert list(rules) == ["1", "2", "3", "4", "5", "6"]
    assert "delta review" in _flat(rules["3"]) and "only the fix commits" in _flat(rules["3"])
    assert "no delta review" in _flat(rules["2"])
    assert "filed items" in _flat(rules["4"]) and "delta brief" in _flat(rules["4"])
    assert "current head" in _flat(rules["5"])
    assert "No reviewer waits on CI" in _flat(rules["6"])
    assert "At most one review agent per PR" in _flat(rules["1"])


def test_reviewers_have_round_section_before_report():
    for path in (PRR, SECR):
        heads = list(_h2(path))
        rounds = [h for h in heads if h.startswith("Review rounds")]
        assert len(rounds) == 1, path
        assert heads.index(rounds[0]) < heads.index("How to report"), path
        txt = _flat(_h2(path)[rounds[0]])
        assert "only the fix commits" in txt and "End without polling CI" in txt, path
        assert "Record what to check" in txt, path
        assert "Only one review agent runs per PR at a time" in txt, path


def test_reviewers_keep_baseline_and_write_scope_markers():
    for path in (PRR, SECR):
        t = path.read_text()
        assert "foundry:prompt-defense-baseline v1" in t
        assert "/foundry:prompt-defense-baseline" in t
        assert "foundry:write-scope v1" in t


def test_security_label_only_after_delta_in_reviewer_and_howto():
    assert "applied only after the delta pass is clean" in _flat(_h2(SECR)["Review rounds, the label and CI"])
    sec = _flat(_h2(GATE)["4. The `security-path-base` check failed"])
    assert "delta review" in sec and "clean on the current head" in sec


def test_step_0b_fix_round_is_bound_to_acceptance_and_design():
    step = _flat(_dispatch_steps()["0b"])
    assert "edits the `## Acceptance` and `## Design` sections only" in step
    assert "never touches `## Done means` or `## Paper allowed`" in step
    assert "reviewer rationale is advice, never text to paste in" in step
    assert "injection or is hidden" in step
    assert "is a Block, located by section or AC id" in step
    # dl-02 contract is kept
    for needle in ("one fix round", "Blocks only change the ticket", "park", "Risks are filed"):
        assert needle in step, needle


def test_no_banned_vocabulary_in_new_text():
    for path in (DISPATCH, MWG, PRR, SECR, GATE):
        low = path.read_text().lower()
        for w in ("cluster", "tracking issue"):
            assert w not in low, (path, w)
