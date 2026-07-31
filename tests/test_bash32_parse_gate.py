"""tests/test_bash32_parse_gate.py — feat-foundry-bash32-parse-guard, the shell-parse-bash32
job's gate-shape / fail-closed / discovery / coverage / identity coverage (AC-B32-4, -5, -6, -7,
-8, -10, -12).

EVIDENCE RULE (house precedent, tests/test_btb_gates.py's `_load_step_run`): the fail-closed /
discovery / minima / wrong-version / least-privilege tests below lift the `shell-parse-bash32`
job's PARSE step `run:` body VERBATIM out of `.github/workflows/btb-gates.yml` and execute it
under a real `bash`, with a stub `docker` placed FIRST on PATH — deterministic on any machine,
docker or not, and no re-implementation of the job's aggregation/exit logic in Python. The stub is
generated INLINE by this module (not a committed fixture): the contract's `allowed_paths` names
only the four concrete `tests/fixtures/bash32/*` paths a checkpoint's `surface:` binds to
(`unbalanced-backtick-fixture.sh.txt`, `pathstub`, `payload-aws.json`, `payload-force-push.json`)
— a `docker` stub is consumed only by this test PROCESS (never bind-mounted into a real
container), so it is generated per-test into `tmp_path`, the same shape as
`tests/test_hooks_guards.py::_python3_evaluator_stub`.

The REAL bash-3.2 parse evidence (the container genuinely parsing every shipped script) is proven
by the raw acceptance-contract locators, which run real docker directly — not reproduced here
(spec Clarifications: "Fail-closed evidence is driven with a stub docker").
"""
from __future__ import annotations

import copy
import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "btb-gates.yml"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
JOB_NAME = "shell-parse-bash32"

# The workflow's `on:` block, FROZEN — byte-verbatim of the shipped trigger surface. AC-B32-12
# requires this atom leave it byte-unchanged (security-path depends on the labeled/unlabeled
# trigger types to re-run after the `security-reviewed` label is posted; narrowing `types:` would
# silently degrade that gate). Same shape as test_btb_gates.py's `_PRE_WIDENING_ALTERNATION`.
_FROZEN_ON_BLOCK = {
    "pull_request": {"types": ["opened", "synchronize", "reopened", "labeled", "unlabeled"]},
}

_DOCKER_STUB = '''#!/bin/sh
set -u

if [ "${1:-}" != "run" ]; then
  echo "docker-stub: unsupported invocation (expected 'run'): $*" >&2
  exit 125
fi

mode=""
target=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-n" ]; then
    target="$a"
  fi
  case "$a" in
    --version) mode="version" ;;
    -n) mode="parse" ;;
  esac
  prev="$a"
done

if [ "$mode" = "version" ]; then
  printf '%s\\n' "${DOCKER_STUB_VERSION_LINE:-GNU bash, version 3.2.57(1)-release (x86_64-pc-linux-gnu)}"
  exit 0
fi

if [ "$mode" = "parse" ]; then
  if [ -n "${DOCKER_STUB_LOG:-}" ]; then
    printf '%s\\n' "$target" >> "$DOCKER_STUB_LOG"
  fi
  case "$target" in
    ${DOCKER_STUB_FAIL_PATTERN:-__docker_stub_no_such_pattern__})
      echo "docker-stub: simulated bash -n parse failure for $target" >&2
      exit 2
      ;;
  esac
  exit 0
fi

echo "docker-stub: could not classify invocation: $*" >&2
exit 125
'''


def _install_docker_stub(tmp_path) -> Path:
    """Write the stub `docker` fixture into a throwaway bin dir and return that dir, for
    prepending to PATH. Generated inline (see module docstring) rather than a committed fixture
    file — it is consumed by the test PROCESS's shell, never bind-mounted into a container."""
    bin_dir = tmp_path / "docker-stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(_DOCKER_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _load_doc(workflow_path=WORKFLOW_PATH):
    return yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8"))


def _parse_step_run(doc) -> str:
    """The VERBATIM `run:` text of the job's parse step (the step carrying `-n`, i.e. not the
    checkout step) — pure data extraction, no decision logic reproduced."""
    job = doc["jobs"][JOB_NAME]
    for step in job["steps"]:
        run = step.get("run")
        if run and " -n " in run:
            return run
    raise AssertionError(f"{JOB_NAME} has no step whose run: body invokes `bash -n`")


def _run_script(script, cwd, extra_env=None, path_prepend=None):
    """Drive the (real or mutated) parse-step body under `bash -c`. The GITHUB_STEP_SUMMARY sink
    is ALWAYS a throwaway file under the system temp dir — never under `cwd` (which, for the
    real-repo checkpoints, IS this repo's own working tree; writing a summary artifact there would
    leak an untracked file into the real repo)."""
    env = dict(os.environ)
    if path_prepend:
        env["PATH"] = f"{path_prepend}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    if "GITHUB_STEP_SUMMARY" not in env:
        fd, summary_file = tempfile.mkstemp(prefix="bash32-step-summary-", suffix=".md")
        os.close(fd)
        env["GITHUB_STEP_SUMMARY"] = summary_file
    summary_path = Path(env["GITHUB_STEP_SUMMARY"])
    try:
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, cwd=str(cwd), timeout=120,
        )
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    finally:
        if "GITHUB_STEP_SUMMARY" not in (extra_env or {}):
            summary_path.unlink(missing_ok=True)
    return proc, summary


def _make_scratch_repo(tmp_path, *, hook_count, script_count, extra_hook_files=()):
    """A throwaway git repo (tracked via `git add`, no commit needed — `git ls-files` reads the
    index) shaped like the real one for the discovery/minima checkpoints: `hooks/*.sh` and
    `scripts/*.sh`, each a trivially-valid one-liner so a real `bash -n` (were it invoked) would
    not itself be the reason for any failure."""
    repo = tmp_path / "scratch-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "hooks").mkdir()
    (repo / "scripts").mkdir()
    for i in range(hook_count):
        (repo / "hooks" / f"hook-{i}.sh").write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    for i in range(script_count):
        (repo / "scripts" / f"script-{i}.sh").write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    for rel in extra_hook_files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


# ==================================================================== AC-B32-4 (job present) ===

def test_job_present_structurally_shaped_and_never_skippable():
    doc = _load_doc()
    assert JOB_NAME in doc["jobs"], f"{JOB_NAME} missing from {WORKFLOW_PATH}"
    job = doc["jobs"][JOB_NAME]

    # (b) no `if:` / `continue-on-error` on the job or ANY of its steps.
    assert "if" not in job, "job declares an `if:` — a gate that can be silently skipped is not a gate"
    assert "continue-on-error" not in job
    steps = job["steps"]
    assert len(steps) == 2, "expected exactly checkout + parse steps"
    for step in steps:
        assert "if" not in step
        assert "continue-on-error" not in step

    run = _parse_step_run(doc)
    assert "-n" in run, "the parse step must pass -n to the pinned image"
    assert "--entrypoint bash" in run or "--entrypoint" in run

    # (a) inherits the workflow's existing pull_request trigger (jobs never carry their own
    # `on:` — the workflow-level trigger is what fires this job). NOTE: PyYAML's safe_load
    # parses the bare `on:` scalar key as the YAML-1.1 boolean `True`, not the string "on" — a
    # well-known GitHub-Actions-YAML gotcha; `doc[True]` is the correct, deliberate lookup.
    assert "pull_request" in doc[True]


# ==================================================================== AC-B32-4 (tier honesty) ==

def test_states_tier_advisory(tmp_path):
    docker_bin = _install_docker_stub(tmp_path)
    run = _parse_step_run(_load_doc())
    proc, summary = _run_script(run, cwd=REPO_ROOT, path_prepend=docker_bin)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "tier: B" in summary, f"missing tier statement:\n{summary}"
    assert "advisory" in summary, f"missing the honest 'advisory' label:\n{summary}"


# ==================================================================== AC-B32-5 (fails closed) ==

def test_fails_closed_on_a_single_parse_failure(tmp_path):
    docker_bin = _install_docker_stub(tmp_path)
    run = _parse_step_run(_load_doc())
    proc, summary = _run_script(
        run, cwd=REPO_ROOT, path_prepend=docker_bin,
        extra_env={"DOCKER_STUB_FAIL_PATTERN": "*/hooks/foundry-cwd-enforce.sh"},
    )
    assert proc.returncode != 0, "one simulated parse failure must fail the whole step"
    assert "FAIL" in summary


# ==================================================================== AC-B32-6 (discovery) =====

def test_discovers_new_script(tmp_path):
    repo = _make_scratch_repo(tmp_path, hook_count=11, script_count=5)
    # Add the new file AFTER the initial tree is built and tracked — proving discovery is a
    # run-time query, not a snapshot taken once.
    new_file = repo / "hooks" / "brand-new-hook.sh"
    new_file.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    docker_bin = _install_docker_stub(tmp_path)
    log_path = tmp_path / "docker-log.txt"
    run = _parse_step_run(_load_doc())
    proc, summary = _run_script(
        run, cwd=repo, path_prepend=docker_bin,
        extra_env={"DOCKER_STUB_LOG": str(log_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr + summary
    logged = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert "hooks/brand-new-hook.sh" in logged, f"new file not covered with no workflow edit:\n{logged}"


def test_no_enumerated_filenames():
    """A hand-maintained filename list is the documented failure mode of a discovery-based gate.
    The job body must name no shell-script FILENAME — checked against the real, currently
    tracked *.sh set so this convicts a future enumeration too, not just today's."""
    run = _parse_step_run(_load_doc())
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    sh_files = [f for f in tracked if f.endswith(".sh")]
    assert sh_files, "sanity: the real repo must carry at least one tracked *.sh"
    for f in sh_files:
        assert f not in run, f"job body names a literal shipped script path: {f!r}"
        basename = f.rsplit("/", 1)[-1]
        assert basename not in run, f"job body names a literal shipped script filename: {basename!r}"


def test_enforces_directory_minimums(tmp_path):
    # Below both minima: 3 hooks/*.sh (< 11), 2 scripts/*.sh (< 5).
    repo = _make_scratch_repo(tmp_path, hook_count=3, script_count=2)
    docker_bin = _install_docker_stub(tmp_path)
    log_path = tmp_path / "docker-log.txt"
    run = _parse_step_run(_load_doc())
    proc, summary = _run_script(
        run, cwd=repo, path_prepend=docker_bin,
        extra_env={"DOCKER_STUB_LOG": str(log_path)},
    )
    assert proc.returncode != 0, "a collapsed discovery must fail the job body itself, not just a walk-time locator"
    assert "FAIL" in summary
    # The minima check must gate BEFORE the parse loop — no file should ever have reached `-n`.
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == "", (
        "the parse loop ran despite a collapsed discovery — the minima guard is not load-bearing"
    )


# ==================================================================== AC-B32-7 (pins exact) =====

def test_pins_are_exact():
    doc = _load_doc()
    job = doc["jobs"][JOB_NAME]

    checkout_pin = None
    for step in job["steps"]:
        uses = step.get("uses")
        if uses:
            assert "@" in uses, f"unpinned uses:: {uses!r}"
            name, sha = uses.split("@", 1)
            assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
                f"uses: {uses!r} is not pinned to a 40-char commit SHA"
            )
            if name == "actions/checkout":
                checkout_pin = sha

    ci_doc = _load_doc(CI_WORKFLOW_PATH)
    ci_pin = None
    for job_body in ci_doc["jobs"].values():
        for step in job_body.get("steps", []):
            uses = step.get("uses", "")
            if uses.startswith("actions/checkout@"):
                ci_pin = uses.split("@", 1)[1]
                break
        if ci_pin:
            break
    assert ci_pin, "sanity: ci.yml must itself carry a pinned actions/checkout"
    assert checkout_pin == ci_pin, (
        f"the new job's checkout pin ({checkout_pin!r}) must equal ci.yml's ({ci_pin!r}) — "
        "one pin per dependency per repo"
    )

    assert job["runs-on"] == "ubuntu-24.04", job["runs-on"]

    run = _parse_step_run(doc)
    import re
    assert re.search(r"docker\.io/library/bash@sha256:[a-f0-9]{64}", run), (
        "the image reference must be the fully-qualified canonical digest form"
    )
    # No floating tag survives beside the digest in an EXECUTABLE position — only inside a `#`
    # comment (the human tag + research date belong there, never in code).
    for line in run.splitlines():
        code = line.split("#", 1)[0]
        assert "docker.io/library/bash:" not in code, (
            f"a floating bash tag appears outside a comment: {line!r}"
        )


# ==================================================================== AC-B32-8 (coverage) =======

def _covers_every_shipped_script(workflow_path, repo_root, tmp_path) -> bool:
    """The canary's own assertion logic, factored so it can be driven against BOTH the real
    workflow (must be True) and a mutated scratch copy (must be False) — AC-B32-8's paired shape.
    Returns False (never raises) for "job absent" so both mutation halves share one call site."""
    try:
        doc = _load_doc(workflow_path)
        if JOB_NAME not in doc.get("jobs", {}):
            return False
        run = _parse_step_run(doc)
    except Exception:
        return False

    docker_bin = _install_docker_stub(tmp_path)
    log_path = tmp_path / f"cov-log-{os.getpid()}-{id(workflow_path)}.txt"
    proc, _summary = _run_script(
        run, cwd=repo_root, path_prepend=docker_bin,
        extra_env={"DOCKER_STUB_LOG": str(log_path)},
    )
    if proc.returncode != 0:
        return False
    logged = set(log_path.read_text(encoding="utf-8").splitlines()) if log_path.exists() else set()

    tracked_hooks = subprocess.run(
        ["git", "ls-files", "hooks"], cwd=repo_root, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    tracked_scripts = subprocess.run(
        ["git", "ls-files", "scripts"], cwd=repo_root, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    expected = {f for f in tracked_hooks + tracked_scripts if f.endswith(".sh")}
    logged_relpaths = {p[len("/src/"):] if p.startswith("/src/") else p for p in logged}
    return expected.issubset(logged_relpaths)


def test_covers_every_shipped_script(tmp_path):
    assert _covers_every_shipped_script(WORKFLOW_PATH, REPO_ROOT, tmp_path), (
        "the derived covered set does not include every shipped shell script under both "
        "hooks/ and scripts/"
    )


def test_canary_convicts_gate_removal(tmp_path):
    real_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    real_run = _parse_step_run(_load_doc())

    # Mutation 1: the job entirely removed.
    doc_no_job = _load_doc()
    del doc_no_job["jobs"][JOB_NAME]
    mutated_removed = tmp_path / "btb-gates-job-removed.yml"
    mutated_removed.write_text(yaml.safe_dump(doc_no_job, sort_keys=False), encoding="utf-8")
    assert _covers_every_shipped_script(mutated_removed, REPO_ROOT, tmp_path) is False, (
        "the canary passed even though the job was removed — it convicts nothing"
    )

    # Mutation 2: discovery truncated (a literal short list replaces `git ls-files | grep`).
    assert "done < <(git ls-files | grep -e '[.]sh$')" in real_run, (
        "sanity: the real discovery line's exact text moved — update this mutation"
    )
    truncated_run = real_run.replace(
        "done < <(git ls-files | grep -e '[.]sh$')",
        "done < <(printf '%s\\n' hooks/foundry-cwd-enforce.sh)",
    )
    doc_truncated = _load_doc()
    for step in doc_truncated["jobs"][JOB_NAME]["steps"]:
        if step.get("run") == real_run:
            step["run"] = truncated_run
    mutated_truncated = tmp_path / "btb-gates-discovery-truncated.yml"
    mutated_truncated.write_text(yaml.safe_dump(doc_truncated, sort_keys=False), encoding="utf-8")
    assert _covers_every_shipped_script(mutated_truncated, REPO_ROOT, tmp_path) is False, (
        "the canary passed even though discovery was truncated to one literal file — it "
        "convicts nothing"
    )

    # Positive control: the REAL, unmutated workflow still passes the same logic.
    assert real_text == WORKFLOW_PATH.read_text(encoding="utf-8"), "sanity: real file untouched"
    assert _covers_every_shipped_script(WORKFLOW_PATH, REPO_ROOT, tmp_path) is True


# ==================================================================== AC-B32-10 (identity) ======

def test_rejects_wrong_bash_version(tmp_path):
    docker_bin = _install_docker_stub(tmp_path)
    log_path = tmp_path / "docker-log.txt"
    run = _parse_step_run(_load_doc())
    proc, summary = _run_script(
        run, cwd=REPO_ROOT, path_prepend=docker_bin,
        extra_env={
            "DOCKER_STUB_VERSION_LINE": "GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu)",
            "DOCKER_STUB_LOG": str(log_path),
        },
    )
    assert proc.returncode != 0, "a bash-5 image must be rejected, not silently used"
    assert "FAIL" in summary
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == "", (
        "bash -n ran despite the image not being bash 3.2 — the version guard is not load-bearing"
    )


# ==================================================================== AC-B32-12 (checkout) =======

def test_least_privilege_checkout():
    doc = _load_doc()
    job = doc["jobs"][JOB_NAME]
    permissions = job.get("permissions")
    assert permissions == {"contents": "read"}, (
        f"job-level permissions must grant no more than contents: read, got {permissions!r}"
    )
    checkout_step = next(s for s in job["steps"] if "uses" in s)
    assert checkout_step.get("with", {}).get("persist-credentials") is False, (
        "the checkout step must set persist-credentials: false"
    )


def test_trigger_surface_unchanged():
    doc = _load_doc()
    # See the note in test_job_present_structurally_shaped_and_never_skippable: PyYAML parses the
    # bare `on:` key as the YAML-1.1 boolean `True`.
    assert doc[True] == _FROZEN_ON_BLOCK, (
        f"the on: block changed — expected byte-equivalent to the frozen trigger surface, got {doc[True]!r}"
    )
    # The structural equality above already proves `on:` carries only `pull_request` — restate
    # it as an explicit negative for readability (never `pull_request_target`, which would run
    # fork code with a privileged token).
    assert "pull_request_target" not in doc[True], doc[True]
