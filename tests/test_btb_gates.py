"""tests/test_btb_gates.py — Block 4 + Block 5 of the subtraction-contract-widening atom
(AC-SCW-10..13, feat-foundry-subtraction-contract-widening.md).

EVIDENCE RULE (binding, restated from the spec/contract): every test here executes the REAL
`.github/workflows/btb-gates.yml` job step body — lifted VERBATIM out of the workflow YAML and
run under `bash` — with the stub `gh` at tests/fixtures/gh-stub/gh placed FIRST on PATH to serve
each fixture row's PR body / labels / changed-file list. Nothing here re-implements the
`spec-link` lane-selection logic or the `security-path` alternation in Python: the workflow file
is parsed only to EXTRACT its `run:` script text (data extraction, not decision logic), and the
step's real verdict is read from ITS OWN stdout/exit-code, compared against an expectation table
authored SEPARATELY (tests/fixtures/btb-gates/lane-matrix.yaml,
tests/fixtures/btb-gates/security-path-matrix.yaml) — never against the workflow's own emitted
literal (that tautology is the Block-4 self-assertion defect this atom closes).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "btb-gates.yml"
BASE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "btb-gates-base.yml"
RESET_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "security-label-reset.yml"
GH_STUB_DIR = Path(__file__).resolve().parent / "fixtures" / "gh-stub"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "btb-gates"

# The head SHA every row below is gated at. Arbitrary but FIXED, because the whole point of the
# post-2026-08-04 design is that the review label NAMES a commit: `security-reviewed:<sha12>`
# either matches the head being gated or it does not. Rows spell the sha12 out literally rather
# than slicing this constant, so a test that agreed with a buggy slice cannot exist.
TEST_HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"   # sha12 == "abcdef123456"
TEST_STALE_SHA = "0badc0d000000000000000000000000000000000"  # sha12 == "0badc0d00000"


# WHERE EACH GATE'S BODY NOW LIVES. `spec-link` and `security-path` moved to
# `btb-gates-base.yml` on `pull_request_target` (whose definition GitHub takes from the base
# repository's default branch), so a fork can no longer rewrite the gate that grades it. Only the
# FILE and the JOB NAME changed — the step bodies are byte-identical, which is why every call site
# below still names the gate by its logical name and none of them changed.
#
# This table is deliberately a lookup rather than a search across both files: a search would
# silently keep passing if a gate reappeared in the fork-evaluated workflow, which is the exact
# regression this migration exists to prevent. See test_the_gate_jobs_are_split_by_trigger.
GATE_SOURCE = {
    "spec-link":          (BASE_WORKFLOW_PATH, "spec-link-base"),
    "security-path":      (BASE_WORKFLOW_PATH, "security-path-base"),
    "shell-parse-bash32": (WORKFLOW_PATH,      "shell-parse-bash32"),
}


def _load_step_run(job_name: str, workflow_path: Path = None) -> str:
    """Extract the REAL, verbatim `run:` script body of the named job's (single) step, straight
    out of the shipped workflow file. Pure data extraction — no branching/decision logic is
    read or reproduced here, only the script TEXT the real `bash` process below executes.

    With no explicit `workflow_path`, the gate's file and real job name are resolved through
    GATE_SOURCE, so callers keep naming gates logically across the trigger split."""
    if workflow_path is None:
        workflow_path, job_name = GATE_SOURCE.get(job_name, (WORKFLOW_PATH, job_name))
    doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = doc["jobs"][job_name]["steps"]
    assert len(steps) == 1, f"job {job_name!r} grew a second step — the extraction helper assumed exactly one"
    return steps[0]["run"]


def _run_step_body(job_name: str, tmp_path, *, pr_body="", labels=(), files=(), extra_env=None,
                   head_sha=TEST_HEAD_SHA, workflow_path: Path = None):
    """Drive the REAL shipped step body under `bash -c`, with the fixture `gh` stub prepended to
    PATH and fed this row's PR body / labels / changed-file list via env vars (the SAME
    mechanism `gh pr view`/`gh api` would surface to the real script).

    `HEAD_SHA` is supplied because the shipped step bodies read it: the review label is bound to
    the commit under review. It is set here rather than defaulted inside the scripts on purpose —
    a step body that tolerated an absent HEAD_SHA would compare against the empty prefix, which
    every `security-reviewed:*` label matches."""
    script = _load_step_run(job_name, workflow_path)
    summary_path = tmp_path / f"{job_name}-summary.md"
    summary_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PATH": f"{GH_STUB_DIR}:{os.environ['PATH']}",
        "GH_TOKEN": "x-test-token",
        "PR": "123",
        "REPO": "acme/demo",
        # BASE is defaulted because `tier()` reads it. Without it, `local p; p="$(gh api …)"` dies
        # under `set -u` INSIDE the command substitution, so `summary "$(tier)"` emitted an EMPTY
        # merge-floor line — and every row tolerated a gate job that stated no tier at all, which
        # is exactly what AC-SCW-11 exists to prevent (review N2, 2026-08-06).
        "BASE": "main",
        "HEAD_SHA": head_sha,
        "GITHUB_STEP_SUMMARY": str(summary_path),
        "GH_STUB_PR_BODY": pr_body,
        "GH_STUB_PR_LABELS": "\n".join(labels),
        "GH_STUB_PR_FILES": "\n".join(files),
    })
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    proc_summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    return proc, proc_summary


def _load_fixture(name):
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


LANE_MATRIX = _load_fixture("lane-matrix.yaml")["rows"]
SECURITY_MATRIX = _load_fixture("security-path-matrix.yaml")


# ==================================================================== AC-SCW-10 (spec-link) ==

@pytest.mark.parametrize("row", LANE_MATRIX, ids=[r["name"] for r in LANE_MATRIX])
def test_spec_link_verdict_matches_independent_lane_matrix(row, tmp_path):
    proc, summary = _run_step_body(
        "spec-link", tmp_path,
        pr_body=row["pr_body"], labels=row["labels"], files=row["files"],
    )
    if row["expect_exit_zero"]:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    else:
        assert proc.returncode != 0, "expected the spec-link step to fail this row but it exited 0"
    assert row["expect_summary_contains"] in proc.stdout, proc.stdout + "\n---summary---\n" + summary
    assert row["expect_summary_contains"] in summary


# ==================================================================== AC-SCW-11 (tier honesty) =

def test_every_gate_job_states_its_tier_and_labels_tier_b_advisory(tmp_path):
    assert set(yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]) \
        | set(yaml.safe_load(BASE_WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]) \
        == {"spec-link-base", "security-path-base", "shell-parse-bash32"}, \
        "the gate set changed — every gate must still state its tier"

    # Each job must STATE its enforcement tier, and state it DERIVED from the live protection
    # state rather than as a literal. The literal is what this test used to assert, and it is
    # exactly how the workflow came to print "tier: B (advisory) — private repo, Free plan" from a
    # public repo with enforcing branch protection: a wrong, public claim about our own merge floor
    # that no test could catch, because the test asserted the literal too.
    #
    # So drive each job's real step body three times over the same PASS row, varying only what the
    # branch endpoint reports, and require all three readings to differ correctly.
    # The protected=true row deliberately does NOT expect a "tier: A (enforced)" claim. `.protected`
    # is a coarse boolean — true for a branch protected only by "restrict deletions" — so it proves
    # SOMETHING guards the branch, never that THESE contexts are required, and a read-only token
    # cannot enumerate that. An earlier revision did claim Tier A here and the security review
    # (Block 3, 2026-08-04) called it an unearned claim. The assertion now pins the honest wording,
    # so a future edit that re-inflates it goes red.
    rows = [
        ('{"name":"main","protected":true}',  "IS server-protected",       "cannot enumerate WHICH contexts"),
        ('{"name":"main","protected":false}', "tier: B (advisory)",        "advisory"),
        ("",                                  "merge floor: UNKNOWN",      "advisory"),   # unreadable -> fail safe
    ]
    cases = (
        ("spec-link",     dict(pr_body="Spec: specs/features/foundry/x/feat-x.md\n", labels=[], files=["a.py"])),
        ("security-path", dict(pr_body="", labels=[], files=["docs/readme.md"])),
    )
    for job_name, kwargs in cases:
        observed = set()
        for branch_body, expected_tier, expected_word in rows:
            proc, summary = _run_step_body(
                job_name, tmp_path,
                extra_env={"GH_STUB_BRANCH_BODY": branch_body, "BASE": "main"},
                **kwargs,
            )
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert expected_tier in summary, (
                f"{job_name} did not derive {expected_tier!r} when the branch endpoint returned "
                f"{branch_body!r}:\n{summary}")
            assert expected_word in summary, (
                f"{job_name} summary missing {expected_word!r}:\n{summary}")
            # Collect the OBSERVED tier line, not the row's expectation. An earlier revision did
            # `seen.add(expected_tier)` and then asserted `len(seen) == 3` — populated purely from
            # the literal table above, so it was 3 unconditionally and could not fail for ANY step
            # body. Worse, its comment claimed it caught "a helper that echoed a constant
            # containing all three substrings", which is precisely the body it let through: all
            # three `in summary` checks would pass and the count would still be 3. A vacuous
            # assertion in the test for the merge floor's own honesty claim (review R2, 2026-08-06).
            tier_lines = [ln for ln in summary.splitlines() if ln.startswith("**merge floor")]
            assert len(tier_lines) == 1, (
                f"{job_name} emitted {len(tier_lines)} merge-floor lines, expected exactly 1:\n{summary}")
            observed.add(tier_lines[0])
        assert len(observed) == 3, (
            f"{job_name} did not produce three DISTINCT tier readings — a body echoing one constant "
            f"would satisfy every substring check above and still land here: {observed}")


# ==================================================================== AC-SCW-12 (security-path) =

@pytest.mark.parametrize("row", SECURITY_MATRIX["rows"], ids=[r["name"] for r in SECURITY_MATRIX["rows"]])
def test_security_path_flags_skills_and_agents_and_spares_neighbours(row, tmp_path):
    proc, summary = _run_step_body(
        "security-path", tmp_path,
        pr_body="", labels=row["labels"], files=row["files"],
    )
    if row["expect_exit_zero"]:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    else:
        assert proc.returncode != 0, "expected the security-path step to fail this row but it exited 0"
    assert row["expect_summary_contains"] in proc.stdout, proc.stdout + "\n---summary---\n" + summary
    assert row["expect_summary_contains"] in summary


# ==================================================================== AC-SCW-13 (widen-only) ====

# The PRE-widening security-path alternation, byte-verbatim from the shipped
# `.github/workflows/btb-gates.yml` at the commit this atom branched from (56a1ffd, 2026-07-28,
# `git merge-base` of this branch and `origin/main` at authoring time). Frozen HISTORICAL DATA —
# used ONLY to derive which example paths were ALREADY flagged before this atom's widening, never
# to decide what the CURRENT shipped step body does (that verdict is always read from actually
# running it, below). Embedding this avoids a live `git show`/history dependency in CI, where a
# shallow checkout may not carry `origin/main`.
_PRE_WIDENING_ALTERNATION = (
    r'(auth|secret|credential|token|provenance|signing|\.rego$)|^\.github/|^hooks/|'
    r'^\.claude-plugin/|(^|/)(standing-versions|profile-version-ledger)|(^|/)(requirements[^/]*'
    r'\.txt|package(-lock)?\.json|pyproject\.toml|Pipfile[^/]*|go\.(mod|sum)|Cargo\.(toml|lock))$'
)


def test_security_path_widening_is_superset_of_prior_match_set(tmp_path):
    import re

    probes = SECURITY_MATRIX["widen_only_probe_paths"]
    assert probes, "the widen-only probe list must be non-empty to prove anything"

    # Sanity: every probe really was flagged under the frozen PRE-widening pattern (historical
    # ground truth) — if this fails the probe list itself is wrong, not the widening.
    for path in probes:
        assert re.search(_PRE_WIDENING_ALTERNATION, path), (
            f"probe path {path!r} was not actually flagged by the pre-widening alternation — "
            "fix the probe list, this is not a widen-only regression"
        )

    # The real assertion: drive the CURRENT (post-widening) shipped step body over each probe
    # path and confirm it is STILL flagged (unlabeled -> "security-path: FAIL").
    for path in probes:
        proc, summary = _run_step_body("security-path", tmp_path, pr_body="", labels=[], files=[path])
        assert proc.returncode != 0, (
            f"widen-only regression: {path!r} was flagged before this atom's change and must "
            f"still be flagged, but the current shipped step body admitted it:\n{proc.stdout}"
        )
        assert "security-path: FAIL" in summary, summary

    # Structural corroboration: the widened alternation is an OR-append over the frozen prior
    # string (every existing alternative untouched) — extracted as TEXT from the real step body,
    # not re-derived, so a future edit that instead NARROWED or REORDERED the pattern trips this.
    current_script = _load_step_run("security-path")
    assert _PRE_WIDENING_ALTERNATION in current_script, (
        "the security-path alternation no longer contains the pre-widening pattern verbatim — "
        "this is not a pure OR-append widening any more"
    )
    assert "^skills/" in current_script and "^agents/" in current_script


# ============================================ security-label-reset (2026-08-05, second pass) ====
#
# The stripper is BELT-AND-BRACES for the new-commits case — head-SHA binding is what makes
# `security-path` correct there, and the stripper races it with no ordering, so it is not relied
# on. It IS load-bearing for exactly one case: a base retarget moves the effective diff while the
# head SHA does not move, so a head-bound label survives a change it never described. These tests
# pin that asymmetry, because it is the one place the two mechanisms do not overlap.


def _run_reset(tmp_path, *, labels, head_sha=TEST_HEAD_SHA, base_from="", extra_env=None):
    """Drive the REAL security-label-reset.yml step body, journaling every `gh` call so the test
    reads WHICH labels were deleted off the actual invocations rather than off the script's own
    narration.

    `GH_STUB_DELETED_LOG` makes the stub STATEFUL — a label the stub accepted a DELETE for stops
    appearing in later `gh pr view --json labels` reads. That is what lets the workflow's
    re-read-and-verify branch (fail-closed when a review label survives a base retarget) be
    exercised for real instead of always seeing the pre-delete label set."""
    log = tmp_path / "gh-calls.log"
    deleted_log = tmp_path / "gh-deleted.log"
    deleted_log.write_text("", encoding="utf-8")
    # BASE_REF is the base branch the label is bound against; the workflow computes the label's
    # base component from it, so an unset value dies under `set -u` (loudly, which is correct —
    # a stripper that guessed the base would delete the wrong labels).
    env = {"BASE_FROM": base_from, "BASE_REF": "main", "GH_STUB_LOG": str(log),
           "GH_STUB_DELETED_LOG": str(deleted_log)}
    if extra_env:
        env.update(extra_env)
    proc, summary = _run_step_body(
        "reset", tmp_path, labels=labels, head_sha=head_sha,
        workflow_path=RESET_WORKFLOW_PATH, extra_env=env,
    )
    from urllib.parse import unquote
    deleted = [
        unquote(line.rsplit("/labels/", 1)[1].strip())
        for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])
        if "-X DELETE" in line and "/labels/" in line
    ]
    return proc, summary, deleted


def test_reset_keeps_this_heads_label_and_strips_every_other():
    """The ordinary push case: a label naming the current head is the one label that still
    describes what is proposed, so deleting it would force a pointless re-review."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        proc, _summary, deleted = _run_reset(
            Path(d),
            labels=["lane:light", "security-reviewed:0badc0d00000-0d6e4079", "security-reviewed:abcdef123456-0d6e4079",
                    "security-reviewed"],
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "security-reviewed:abcdef123456-0d6e4079" not in deleted, (
        "the label naming the CURRENT head was stripped — every push would demand a re-review of "
        f"a diff already reviewed at that commit. deleted={deleted}")
    assert sorted(deleted) == ["security-reviewed", "security-reviewed:0badc0d00000-0d6e4079"], deleted
    assert "lane:light" not in deleted, "the stripper deleted a label outside its remit"


def test_reset_strips_this_heads_label_too_when_the_base_was_retargeted():
    """The one case SHA-binding cannot close by construction. Retargeting the base changes the
    effective diff with NO new commits and NO change to the head SHA, so the head-bound label is
    exactly the stale one and must go with the rest."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        proc, _summary, deleted = _run_reset(
            Path(d),
            labels=["security-reviewed:abcdef123456-0d6e4079"],
            base_from="some-docs-branch",
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert deleted == ["security-reviewed:abcdef123456-0d6e4079"], (
        "a base retarget left the head-bound review label in place — the diff moved while the head "
        f"SHA did not, so that label describes a review that never happened. deleted={deleted}")


def test_reset_is_a_no_op_for_a_title_or_body_edit():
    """`BASE_FROM` is populated only when the base ref actually changed, so title/body edits — the
    common `edited` event — must cost nothing. If they stripped, every description fix would
    demand a re-review, and the gate would be trained into the operator's ignore-list."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        proc, _summary, deleted = _run_reset(
            Path(d), labels=["security-reviewed:abcdef123456-0d6e4079"], base_from="",
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert deleted == [], f"a non-base edit stripped labels: {deleted}"


def test_reset_never_checks_out_the_pull_request():
    """`pull_request_target` runs with the BASE repo's write-scoped token. A checkout step here is
    the pwn-request pattern. Asserted structurally, on the shipped YAML."""
    doc = yaml.safe_load(RESET_WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert list(doc[True].keys()) == ["pull_request_target"], doc[True]
    assert doc["permissions"] == {"contents": "read", "pull-requests": "write"}, doc["permissions"]
    for job in doc["jobs"].values():
        for step in job["steps"]:
            assert "uses" not in step, (
                f"security-label-reset grew an action step ({step.get('uses')!r}) — this workflow "
                "holds a write-scoped token on a fork-triggerable event and must run no repo code")
            # The property that actually keeps this workflow safe is NOT "no action step" — it is
            # that no GitHub expression is interpolated into a shell body. Every fork-influenced
            # value (PR, REPO, HEAD_SHA, BASE_FROM) arrives via `env:`, where it is a shell
            # variable rather than text spliced into the script before bash ever sees it. A future
            # edit adding `${{ github.event.pull_request.title }}` to a `run:` would pass the
            # checkout assertion above while handing a `pull-requests: write` token to
            # attacker-controlled string interpolation (review R4, 2026-08-06).
            assert "${{" not in step.get("run", ""), (
                "a GitHub expression is interpolated directly into a run: body of a "
                "pull_request_target workflow — pass it through env: instead")


def test_security_path_fails_closed_when_the_base_was_retargeted(tmp_path):
    """R1: retargeting the base moves the effective diff with NO new commits and NO change to the
    head SHA, so a head-bound label survives a change it never described — the one case SHA
    binding cannot cover.

    The gate reads `BASE_FROM` from its OWN event payload and fails BEFORE consulting labels.
    Doing it here rather than in the stripper is the point: the two workflows share an event with
    no ordering, a GITHUB_TOKEN strip cannot re-run this gate, so a green recorded here would be
    terminal. Reading our own payload has no race to lose.

    Note the label supplied below is the CURRENT head's and would otherwise PASS — that is what
    makes this row prove the arm rather than merely observe a failure.
    """
    proc, summary = _run_step_body(
        "security-path", tmp_path, pr_body="",
        labels=["security-reviewed:abcdef123456-0d6e4079"], files=[".github/workflows/ci.yml"],
        extra_env={"BASE_FROM": "some-docs-branch"},
    )
    assert proc.returncode != 0, (
        "a base retarget must fail closed even when the label names the current head — the head "
        f"did not move but the diff did:\n{summary}")
    assert "base was retargeted" in summary, summary


def test_security_path_rejects_a_label_bound_to_a_different_base(tmp_path):
    """THE reason for base-ref binding. Without it the retarget defence was EVENT-keyed
    (`changes.base.ref.from`), so it depended on the `edited` event firing AND the stripper's
    DELETE succeeding. Retarget away, title-edit, retarget back — break either link and a later
    title edit re-greens at an unchanged head, which is the supersede-a-red bypass this atom
    already closed once.

    Bound to (head, base ref), the verdict is STATE: a label earned against `main` simply does not
    name the PR now targeting `release/x`, whatever event is in flight. Note BASE_FROM is EMPTY
    here — this is the quiet title-edit event, the exact moment the old design let through.

    HONEST BASELINE: this row would have passed BEFORE base binding too — the fixture label carries
    a `-<base8>` suffix the old bare matcher did not expect either, so it failed for an unrelated
    reason. It is NOT the proven-red regression its name suggests. The evidence for base binding
    came from running EACH DESIGN WITH THE LABEL IT WOULD ITSELF HAVE ISSUED:
        head-only gate + head-only label, base moved -> PASS  (the bypass)
        ref-bound gate + main-bound label, base moved -> FAIL  (closed)
    What genuinely goes red if `-${BASE_TAG}` is dropped from REVIEW_LABEL is
    `flagged-label-names-this-head`. Recorded because a row that reads like proof and is not is
    exactly the vacuous-assertion class this atom spent three rounds removing.
    """
    proc, summary = _run_step_body(
        "security-path", tmp_path, pr_body="",
        labels=["security-reviewed:abcdef123456-0d6e4079"],   # 0d6e4079 == sha256('main')[:8]
        files=[".github/workflows/ci.yml"],
        extra_env={"BASE": "release/x", "BASE_FROM": ""},
    )
    assert proc.returncode != 0, (
        "a review label earned against 'main' admitted a PR now targeting 'release/x' — the label "
        f"must name the base it was earned against:\n{summary}")
    assert "security-path: FAIL" in summary, summary


def test_security_path_retarget_does_not_demand_a_review_label_for_a_clean_diff(tmp_path):
    """Risk 3: the retarget arm used to sit ABOVE the `hits` test, so a docs-only PR that got
    retargeted was told "…then apply 'security-reviewed:<sha12>'". Applying that label passed —
    `hits` was empty anyway — which teaches the operator that handing out the review label is how
    you clear this red. That is round 1's lesson verbatim: a gate whose documented remedy is the
    bypass will be followed. Not-applicable must win over the retarget arm."""
    proc, summary = _run_step_body(
        "security-path", tmp_path, pr_body="", labels=[], files=["docs/readme.md"],
        extra_env={"BASE_FROM": "some-docs-branch"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS (not applicable)" in summary, summary
    assert "security-reviewed:" not in summary, (
        "a diff with no security surface was told to apply a review label — that instruction is "
        f"how an operator learns to hand out review labels for unreviewed diffs:\n{summary}")


def test_security_path_ignores_base_from_when_it_is_empty(tmp_path):
    """Negative control for the row above. `changes.base.ref.from` is populated ONLY on a real base
    retarget, so a title/body edit must cost nothing — otherwise every description fix would demand
    a re-review and the gate would be trained into the ignore-list."""
    proc, summary = _run_step_body(
        "security-path", tmp_path, pr_body="",
        labels=["security-reviewed:abcdef123456-0d6e4079"], files=[".github/workflows/ci.yml"],
        extra_env={"BASE_FROM": ""},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "security-path: PASS" in summary, summary


def test_reset_fails_when_a_base_retarget_strip_did_not_take(tmp_path):
    """R3: `|| true` on the DELETE is right for the ordinary case (GitHub 404s "label not on
    issue"), but on the base-retarget path this strip is the only thing acting. A 5xx or a
    rate-limit there left the stale label in place while the job logged "stripped" and exited 0 —
    asserting something that did not happen.

    Driven with the stub refusing every DELETE, so the label genuinely survives."""
    proc, summary, _deleted = _run_reset(
        tmp_path,
        labels=["security-reviewed:0badc0d00000-0d6e4079"],
        base_from="some-docs-branch",
        extra_env={"GH_STUB_LABEL_EXIT": "1"},
    )
    assert proc.returncode != 0, (
        "the DELETE failed and a review label survived a base retarget, but the job reported "
        f"success:\n{proc.stdout}\n{summary}")
    assert "survive" in (proc.stdout + summary), proc.stdout + summary


def test_reset_trigger_types_are_frozen():
    """`edited` in this list is load-bearing, not incidental: a base retarget arrives ONLY as
    `edited`, and it is the one case a head-bound label cannot describe. Dropping it would leave
    `test_reset_strips_this_heads_label_too_when_the_base_was_retargeted` green — that test injects
    BASE_FROM directly into the step body, so it cannot notice that production would never supply
    it. The sibling workflow's `on:` block is frozen byte-verbatim for exactly this reason
    (`_FROZEN_ON_BLOCK` in test_bash32_parse_gate.py); the asymmetry was an oversight
    (review R4, 2026-08-06)."""
    doc = yaml.safe_load(RESET_WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert doc[True]["pull_request_target"]["types"] == ["synchronize", "reopened", "edited"], (
        "security-label-reset's trigger types changed — if `edited` was dropped, the base-retarget "
        "strip can never fire in production even though its test still passes")


# ============================================== the trigger split (fork-evaluated gate surface) ==

def test_the_gate_jobs_are_split_by_trigger():
    """THE security property of the migration, asserted structurally.

    `pull_request` runs a workflow FROM THE PR'S MERGE REF, so a fork author's edits to that file
    take effect in the run that grades their own PR. `pull_request_target` takes its definition
    from the base repository's default branch instead. The two metadata gates therefore live in
    `btb-gates-base.yml`; `shell-parse-bash32` must NOT follow them, because it checks out fork
    code and that is the one thing a privileged trigger must never do.

    A prose comment cannot enforce this. Without this test, moving a job back — or adding a new
    metadata gate to the fork-evaluated file out of habit — reopens the hole silently, and every
    other test in this module would keep passing because it resolves gates through GATE_SOURCE.
    """
    fork_doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    base_doc = yaml.safe_load(BASE_WORKFLOW_PATH.read_text(encoding="utf-8"))

    # `on:` parses as the boolean True under YAML 1.1 — read it by identity, not by the string.
    def triggers(doc):
        return set((doc.get(True) if True in doc else doc["on"]).keys())

    assert triggers(fork_doc) == {"pull_request"}, triggers(fork_doc)
    assert triggers(base_doc) == {"pull_request_target"}, triggers(base_doc)

    assert set(fork_doc["jobs"]) == {"shell-parse-bash32"}, (
        "a job other than shell-parse-bash32 is in the FORK-EVALUATED workflow; a fork PR can "
        f"rewrite it and report green under its own name: {set(fork_doc['jobs'])}")
    assert set(base_doc["jobs"]) == {"spec-link-base", "security-path-base"}, set(base_doc["jobs"])

    # The whole safety case for `pull_request_target` is that these jobs run NO repository code.
    # One `uses:` — an `actions/checkout` above all — turns a metadata gate into arbitrary fork
    # code executing with a privileged token, which is strictly worse than the hole being closed.
    assert "uses:" not in BASE_WORKFLOW_PATH.read_text(encoding="utf-8"), (
        "btb-gates-base.yml uses an action; on pull_request_target it must fetch and execute "
        "nothing at all")

    # Least privilege is load-bearing here rather than tidy: without it the default token carries
    # WRITE on a fork-authored event.
    assert base_doc.get("permissions") == {"contents": "read", "pull-requests": "read"}, \
        base_doc.get("permissions")
