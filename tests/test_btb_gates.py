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
GH_STUB_DIR = Path(__file__).resolve().parent / "fixtures" / "gh-stub"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "btb-gates"


def _load_step_run(job_name: str) -> str:
    """Extract the REAL, verbatim `run:` script body of the named job's (single) step, straight
    out of the shipped workflow file. Pure data extraction — no branching/decision logic is
    read or reproduced here, only the script TEXT the real `bash` process below executes."""
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = doc["jobs"][job_name]["steps"]
    assert len(steps) == 1, f"job {job_name!r} grew a second step — the extraction helper assumed exactly one"
    return steps[0]["run"]


def _run_step_body(job_name: str, tmp_path, *, pr_body="", labels=(), files=(), extra_env=None):
    """Drive the REAL shipped step body under `bash -c`, with the fixture `gh` stub prepended to
    PATH and fed this row's PR body / labels / changed-file list via env vars (the SAME
    mechanism `gh pr view`/`gh api` would surface to the real script)."""
    script = _load_step_run(job_name)
    summary_path = tmp_path / f"{job_name}-summary.md"
    summary_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PATH": f"{GH_STUB_DIR}:{os.environ['PATH']}",
        "GH_TOKEN": "x-test-token",
        "PR": "123",
        "REPO": "acme/demo",
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
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    job_names = list(doc["jobs"].keys())
    assert set(job_names) == {"spec-link", "security-path", "shell-parse-bash32"}, job_names

    # Each job must STATE its enforcement tier, and state it DERIVED from the live protection
    # state rather than as a literal. The literal is what this test used to assert, and it is
    # exactly how the workflow came to print "tier: B (advisory) — private repo, Free plan" from a
    # public repo with enforcing branch protection: a wrong, public claim about our own merge floor
    # that no test could catch, because the test asserted the literal too.
    #
    # So drive each job's real step body three times over the same PASS row, varying only what the
    # branch endpoint reports, and require all three readings to differ correctly.
    rows = [
        ('{"name":"main","protected":true}',  "tier: A (enforced)",  "server-protected"),
        ('{"name":"main","protected":false}', "tier: B (advisory)",  "advisory"),
        ("",                                  "tier: UNKNOWN",       "advisory"),   # unreadable -> fail safe
    ]
    cases = (
        ("spec-link",     dict(pr_body="Spec: specs/features/foundry/x/feat-x.md\n", labels=[], files=["a.py"])),
        ("security-path", dict(pr_body="", labels=[], files=["docs/readme.md"])),
    )
    for job_name, kwargs in cases:
        seen = set()
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
            seen.add(expected_tier)
        # The three readings must be genuinely distinct — a helper that hardcoded any single
        # verdict would satisfy one row and fail the others, but a helper that echoed a constant
        # containing all three substrings would slip through without this.
        assert len(seen) == 3, f"{job_name} did not produce three distinct tier readings: {seen}"


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
