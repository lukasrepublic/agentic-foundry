"""Regression coverage for the two LIVE PreToolUse security guards + the uncovered hook
selftests (PR #270 floor-#3 review findings 1/2), PLUS the subtraction-contract-widening atom's
Block-1 fail-closed enumeration for the `gh pr merge` clause (AC-SCW-1..5) and the
compact-reinject removal smoke (AC-SCW-14). These drive the real shipped hooks:

- hooks/foundry-cloud-cli-exec-guard.sh via its hermetic `--eval` seam (block / allow-wrapped /
  no-wrapper-inert — the same evaluator the live path runs).
- hooks/foundry-git-discipline.sh via real hook-JSON stdin (destructive-git block, benign allow,
  `gh pr merge --admin` outright block, plain `gh pr merge` admitted only on a checks-green
  `gh pr checks` query — exercised against tests/fixtures/gh-stub/gh, the ONE committed stub
  `gh` binary the evidence rule requires, placed first on PATH and driven entirely by env vars;
  fail-closed when the query cannot complete or the internal evaluator itself misbehaves).
- hooks/foundry-compact-reinject.sh via real `source: compact` hook-JSON stdin, post the
  AC-SCW-8 dead-spawn removal, proving the surviving (a)/(b) sections still assemble.
- The three previously-uncovered hook selftests (env-reap / worktree-remove /
  harvest-learnings) as sentinel wrappers.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
GH_STUB_DIR = Path(__file__).resolve().parent / "fixtures" / "gh-stub"


def _run_hook(script, stdin_text="", extra_env=None, args=()):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(HOOKS / script), *args],
        input=stdin_text, capture_output=True, text=True, env=env, timeout=60,
    )


# ---------------------------------------------------------------- cloud-cli-exec-guard
def _eval_guard(cmd, wrapper, tools="aws,kubectl,tofu,terraform,helm,argocd", exempt=""):
    p = _run_hook("foundry-cloud-cli-exec-guard.sh",
                  args=("--eval", cmd, wrapper, tools, exempt))
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"rc={p.returncode}"


def test_cloud_guard_blocks_bare_cloud_cli():
    assert _eval_guard("aws s3 ls", wrapper="ctx run --").startswith("BLOCK")


def test_cloud_guard_allows_wrapped_invocation():
    assert _eval_guard("ctx run -- aws s3 ls", wrapper="ctx run --") == "ALLOW"


def test_cloud_guard_inert_without_wrapper():
    # No adopter wrapper configured => the guard is INERT by design (fail-inert, documented).
    assert _eval_guard("aws s3 ls", wrapper="") == "ALLOW"


# ==================================================================== AC-B32-3 ==================
# feat-foundry-bash32-parse-guard: the guard's command-position coverage over ALL ELEVEN members
# of SEPARATORS (line 235 — the code is authoritative; the guard's own header comment miscounts
# nine, omitting `)` and `}`), each row in the GLUED (no-whitespace) separator form — a spaced row
# still passes with the whole connector-normalization pass (lines 189-208) deleted, proving
# nothing about the defence that exists. This convicts the specific wrong "fix" for the
# bash-3.2 parse defect: dropping the backtick from SEPARATORS (or narrowing the set to the nine
# the stale header names), which would clear the parse error and silently widen the blind spot.
#
# The newline row is `\n` GLUED directly to the front of the guarded tool with nothing else
# before it (no separator preceding "x", unlike the other ten rows) — the shipped tokenizer
# (shlex, whitespace_split mode) treats an embedded "\n" purely as inter-token whitespace and
# never yields it as a standalone token, so a MID-command newline can never satisfy `is_sep`; this
# is a measured, pre-existing property of the unmodified evaluator (present before AND after this
# atom's parse-only fix — verified against the merge-base file), not something this atom may
# change (no behavioural change to any guard clause). A leading "\naws …" is still a literal,
# faithfully-glued newline-introduced command position (nothing between the separator and the
# adjacent token) and is what the shipped guard actually blocks on.
_SEPARATOR_MATRIX_GLUED = [
    ("&&", "true&&aws s3 ls"),
    ("||", "false||aws s3 ls"),
    (";", "true;aws s3 ls"),
    ("|", "echo hi|aws s3 ls"),
    ("&", "sleep.1&aws s3 ls"),
    ("(", "x(aws s3 ls)"),
    (")", "(true)aws s3 ls"),
    ("{", "x{aws s3 ls"),
    ("}", "true}aws s3 ls"),
    ("`", "x`aws s3 ls`"),
    ("\\n", "\naws s3 ls"),
]


@pytest.mark.parametrize("sep,cmd", _SEPARATOR_MATRIX_GLUED,
                         ids=[f"separator_matrix_glued[{sep}]" for sep, _ in _SEPARATOR_MATRIX_GLUED])
def test_separator_matrix_glued(sep, cmd):
    # Raw stdout, not the `_eval_guard` last-non-empty-LINE helper: the newline row's glued
    # separator is itself embedded (as `cmd`) inside the guard's own printed message, which would
    # otherwise corrupt a last-line split. The verdict word is always the message's FIRST token
    # (`cmd` is substituted only at the tail, after "Command: "), so a prefix check on raw stdout
    # is robust for every row, embedded newline included.
    p = _run_hook("foundry-cloud-cli-exec-guard.sh",
                  args=("--eval", cmd, "ctx run --", "aws,kubectl,tofu,terraform,helm,argocd", ""))
    assert p.stdout.startswith("BLOCK"), (
        f"separator {sep!r} (glued form {cmd!r}) did not BLOCK: stdout={p.stdout!r} stderr={p.stderr!r}"
    )
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-B32-11 =================
# feat-foundry-bash32-parse-guard: the LIVE PreToolUse path (hook-JSON on stdin, no `--eval`) —
# the payload reader (lines 58-68), the config-seam resolution + three-line base64 protocol
# (lines 77-118) — none of which `--eval` exercises. `--eval` bypasses this path structurally, and
# every existing guard test above uses it; a restructuring that broke the live seam (or the
# payload reader) would make the guard a silent allow-all in production with the whole `--eval`
# suite green. The BLOCK half and the no-seam ADMIT half are the pair: a silent allow-all passes
# the second and fails the first.

def _write_exec_guard_seam(project_dir, wrapper, guarded_tools=None, offline_exempt=None):
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    doc = {"cloud_cli_exec_guard": {"wrapper": wrapper}}
    if guarded_tools is not None:
        doc["cloud_cli_exec_guard"]["guarded_tools"] = guarded_tools
    if offline_exempt is not None:
        doc["cloud_cli_exec_guard"]["offline_exempt"] = offline_exempt
    (claude_dir / "foundry-project.json").write_text(json.dumps(doc), encoding="utf-8")


def test_live_seam_blocks_configured(tmp_path):
    project_dir = tmp_path / "project-with-seam"
    project_dir.mkdir()
    _write_exec_guard_seam(project_dir, wrapper="ctx run --")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "aws s3 ls"}})
    p = _run_hook("foundry-cloud-cli-exec-guard.sh", stdin_text=payload,
                 extra_env={"CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 2, p.stdout + p.stderr


def test_live_seam_inert_without_seam(tmp_path):
    project_dir = tmp_path / "project-no-seam"
    project_dir.mkdir()  # no .claude/foundry-project.json at all
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "aws s3 ls"}})
    p = _run_hook("foundry-cloud-cli-exec-guard.sh", stdin_text=payload,
                 extra_env={"CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stdout + p.stderr


# ==================================================================== AC-B32-13 =================
# feat-foundry-bash32-parse-guard: the floor guard (never-relaxed floor #4, git discipline) still
# discriminates after the comment reword that clears its bash-3.2 parse defect — driven through
# the real shipped hook file with its shipped `--protected main` arguments, the SAME real-stdin
# driver every other test in this module uses (never a helper reimplementation). The pair is the
# point: a hook that blocks everything passes the block half and fails the admit half; a
# hollowed-out one fails the block half.

def test_git_discipline_verdicts_preserved():
    blocked = _discipline("git push --force origin main")
    assert blocked.returncode == 2, blocked.stdout + blocked.stderr
    admitted = _discipline("git status")
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr


# ==================================================================== AC-B32-14 =================
# feat-foundry-bash32-parse-guard: the re-injector (SessionStart:compact, advisory, fail-open)
# still re-injects after the comment reword that clears its bash-3.2 parse defect. Advisory +
# fail-open hooks always exit 0 and print nothing when nothing resolves, so exit status alone
# cannot distinguish "healthy and quiet" from "did not run at all" — its parse defect made it
# silently DEAD on bash 3.2 rather than blocking. This is therefore an EMISSION check: the
# smallest input that forces a non-empty manifest without depending on any release state — a
# `.agent/assignment.json` dispatch/work marker naming a non-empty `contract_ref`.

def test_compact_reinject_emits_preserved(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    cwd_dir = tmp_path / "worktree"
    agent_dir = cwd_dir / ".agent"
    agent_dir.mkdir(parents=True)
    contract_ref = "specs/features/foundry/fixture/acceptance-contract.yaml"
    (agent_dir / "assignment.json").write_text(
        json.dumps({"contract_ref": contract_ref}), encoding="utf-8",
    )

    payload = json.dumps({"source": "compact", "session_id": "b32-14-emit-session", "cwd": str(cwd_dir)})
    p = _run_hook("foundry-compact-reinject.sh", stdin_text=payload,
                 extra_env={"CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "[foundry:compact-reinject]" in p.stdout, p.stdout
    assert "posture: " in p.stdout, p.stdout
    assert contract_ref in p.stdout, p.stdout


# ---------------------------------------------------------------- git-discipline
def _discipline(cmd, extra_env=None):
    payload = json.dumps({"tool_input": {"command": cmd}})
    return _run_hook("foundry-git-discipline.sh", stdin_text=payload,
                     extra_env=extra_env, args=("--protected", "main"))


def test_discipline_blocks_force_push_to_protected():
    p = _discipline("git push --force origin main")
    assert p.returncode == 2, p.stdout + p.stderr


def test_discipline_allows_benign_git():
    p = _discipline("git status")
    assert p.returncode == 0, p.stdout + p.stderr


def test_discipline_blocks_admin_merge_outright():
    p = _discipline("gh pr merge 42 --admin --merge")
    assert p.returncode == 2, p.stdout + p.stderr


# ---- heredoc bodies are DATA, not commands (CONVICT-SAFE excision) --------------------------
# A heredoc body is fed to a program's STDIN; the shell never parses it as commands. This guard
# normalizes newlines into clause separators, so before the excision every body line became its
# own clause, and merely WRITING a file that MENTIONED a blocked command was refused as though it
# ran it. That fired on ordinary work: authoring a doc, a commit message, or a test fixture that
# names the verbs the guard refuses — including, while this very change was being written, the
# test cases below.
#
# Because exoneration is the FAIL-OPEN direction, the admit half is worth nothing without the
# BYPASS-CONTROL half beneath it: `bash <<EOF` really does execute its body, and a sink that is
# unlisted or unidentifiable must keep convicting.
#
# NOT asserted here, deliberately: `cat <<EOF` whose body holds a `$(…)` substitution, and
# `bash <<< "…"`. Both are ADMITTED by the shipped guard before and after this change — the
# command-substitution and shell-indirection §8 bounded residuals. Pinning them with an assert
# would enshrine a known fail-open as intended behavior. The excision's obligation is only that
# it not WIDEN them, which is what the unquoted-delimiter expansion condition guarantees.

@pytest.mark.parametrize("cmd", [
    # an inert sink, whose body merely mentions a blocked command
    "cat > /tmp/x <<EOF\ngh pr merge 93 --admin\nEOF",
    # a QUOTED delimiter — bash performs no expansion at all inside it
    "cat > /tmp/x <<'EOF'\ngit push --force origin main\nEOF",
    "tee /tmp/x <<EOF\ngit push --force origin main\nEOF",
    # the motivating case: a commit message that names the guard's own verbs
    "git commit -F - <<EOF\nnote: gh pr merge --admin is refused outright\nEOF",
    # `<<-` strips leading tabs, so the closing delimiter may be indented
    "cat <<-EOF\n\tgit push --force origin main\n\tEOF",
    # an all-inert pipeline: every member downstream of the opener only moves the bytes
    "cat <<EOF | sort | uniq\ngit push --force origin main\nEOF",
    "cat <<EOF | tee /tmp/x\ngit push --force origin main\nEOF",
    # UPSTREAM of the opener is not a consumer: cat's stdin IS the heredoc, so what bash writes
    # is discarded and never reaches the body. The prefix is cut at `|` for exactly this reason.
    "bash | cat <<EOF\ngit push --force origin main\nEOF",
])
def test_discipline_admits_mentions_inside_an_inert_heredoc_body(cmd):
    p = _discipline(cmd)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    # sinks that EXECUTE their body — the excision must never reach these
    "bash <<EOF\ngit push --force origin main\nEOF",
    "sh <<EOF\ngh pr merge 93 --admin\nEOF",
    "python3 <<EOF\ngit push --force origin main\nEOF",
    # an UNLISTED sink is treated as executing: unknown CONVICTS
    "perl <<EOF\ngit push --force origin main\nEOF",
    # THE PIPELINE CONTROLS. A real fail-open the adversarial pass on this change found and this
    # pins shut: the body reaches an inert `cat`, but cat's STDOUT is piped into a shell that
    # executes it. Every member DOWNSTREAM of the opener consumes the body, so all of them must
    # be inert — checking only the command the opener is attached to exonerated a body that ran.
    "cat <<EOF | bash\ngit push --force origin main\nEOF",
    "cat <<EOF | sh -s\ngit push --force origin main\nEOF",
    # a redirection containing `&` must not truncate the scan of the rest of the pipeline
    "cat <<EOF 2>&1 | bash\ngit push --force origin main\nEOF",
    # an inert member in between does not launder the executing one at the end
    "cat <<EOF | tee /tmp/x | bash\ngit push --force origin main\nEOF",
    # UNTERMINATED — no closing delimiter, so nothing is excised and the body stays visible
    "cat > /tmp/x <<EOF\ngit push --force origin main",
    # a real command AFTER a legitimately-excised body is still scanned
    "cat > /tmp/x <<EOF\nharmless\nEOF\ngit push --force origin main",
    # THE EXPANSION CONTROL. An inert sink, but an UNQUOTED delimiter and a body containing a
    # substitution: bash would expand it, so the body is NOT excised and its other lines stay
    # scannable. Asserted this way round on purpose — it convicts on the plain command rather
    # than pinning what the guard does with the `$(…)` itself, so it covers the condition
    # without enshrining the substitution residual as intended behavior.
    "cat > /tmp/x <<EOF\n$(echo hi)\ngit push --force origin main\nEOF",
])
def test_discipline_still_convicts_through_a_heredoc(cmd):
    p = _discipline(cmd)
    assert p.returncode == 2, p.stdout + p.stderr


# ---- the EVIDENCE-RULE stub: the ONE committed tests/fixtures/gh-stub/gh, driven by env vars.
# `_stub_gh` (the old per-test ad hoc shell-body generator) is retired in favor of this single
# fixture for every AC-SCW-1..5 row — a reimplemented/bespoke mock does not satisfy the atom's
# evidence rule; the SAME real stub binary must serve every row.
def _gh_stub_env(**stub_vars):
    env = {"PATH": f"{GH_STUB_DIR}:{os.environ['PATH']}"}
    for k, v in stub_vars.items():
        if v is not None:
            env[k] = str(v)
    return env


def _no_gh_path_env():
    """A curated PATH containing ONLY the binaries the hook itself needs (bash/env/cat/python3/
    sh) and NO `gh` anywhere — row (iv) of AC-SCW-4 ('no gh binary is present on PATH'). Built
    from `shutil.which` symlinks rather than stripping PATH wholesale, since the hook's own
    payload-recovery + evaluator subshells need a real python3/bash/cat to run at all."""
    import tempfile
    bin_dir = Path(tempfile.mkdtemp(prefix="foundry-no-gh-path-"))
    for name in ("bash", "env", "cat", "python3", "sh"):
        real = shutil.which(name)
        if real:
            (bin_dir / name).symlink_to(real)
    return {"PATH": str(bin_dir)}


# ==================================================================== AC-SCW-1 ==================

def test_discipline_admin_merge_blocked_without_checks_query(tmp_path):
    log = tmp_path / "gh-invocations.log"
    env = _gh_stub_env(GH_STUB_LOG=str(log))
    p = _discipline("gh pr merge 42 --admin --merge", extra_env=env)
    assert p.returncode == 2, p.stdout + p.stderr
    logged = log.read_text(encoding="utf-8") if log.exists() else ""
    assert logged == "", f"gh must not be queried at all when --admin is present, but saw: {logged!r}"


# ==================================================================== AC-SCW-2 ==================

def test_discipline_admits_plain_merge_only_on_green_checks():
    env = _gh_stub_env(GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl")
    p = _discipline("gh pr merge 42 --merge", extra_env=env)
    assert p.returncode == 0, p.stdout + p.stderr


# ==================================================================== AC-SCW-3 ==================

@pytest.mark.parametrize("row_output", [
    "check-a\tfail\t1s\turl",
    "check-b\tpending\t1s\turl",
], ids=["failing-row", "pending-row"])
def test_discipline_blocks_merge_on_failing_or_pending_row(row_output):
    env = _gh_stub_env(GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT=row_output)
    p = _discipline("gh pr merge 42 --merge", extra_env=env)
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-SCW-4 ==================

def _row_stub_nonzero():
    return _gh_stub_env(GH_STUB_CHECKS_EXIT=8)


def _row_nonexistent_pr_selector():
    return _gh_stub_env(GH_STUB_CHECKS_EXIT=1, GH_STUB_CHECKS_OUTPUT="no pull requests found for branch \"does-not-exist\"")


def _row_api_auth_error():
    return _gh_stub_env(GH_STUB_CHECKS_EXIT=4, GH_STUB_CHECKS_OUTPUT="gh: authentication required, run `gh auth login`")


def _row_no_gh_on_path():
    return _no_gh_path_env()


def _row_query_timeout():
    # The clause's own `subprocess.run(..., timeout=30)` — sleep past it for a real TimeoutExpired.
    return _gh_stub_env(GH_STUB_SLEEP=31, GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl")


@pytest.mark.parametrize("env_factory", [
    _row_stub_nonzero,
    _row_nonexistent_pr_selector,
    _row_api_auth_error,
    _row_no_gh_on_path,
    _row_query_timeout,
], ids=["stub-nonzero", "nonexistent-pr-selector", "api-auth-error", "no-gh-on-path", "query-timeout"])
def test_discipline_blocks_merge_when_checks_query_unavailable(env_factory):
    p = _discipline("gh pr merge 42 --merge", extra_env=env_factory())
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-SCW-5 ==================

def _python3_evaluator_stub(tmp_path, mode):
    """Substitute the `python3` the hook shells out to, so the SECOND (heredoc, argv==['-'])
    invocation — the decision evaluator — can be made to fail on demand, while the FIRST
    invocation (`python3 -c '...'`, the JSON payload-recovery step) is passed straight through
    to the real interpreter so a command is still recovered (the AC's own precondition: "while a
    command has already been recovered from the payload"). This substitutes the RUNTIME the real
    shipped hook (`hooks/foundry-git-discipline.sh`) invokes — it does not reimplement the
    hook's own decision logic, which is never read or copied here."""
    real_python3 = shutil.which("python3")
    stub = tmp_path / "python3"
    if mode == "nonzero":
        tail = 'exit 1'
    elif mode == "empty":
        tail = 'exit 0'
    elif mode == "garbage":
        tail = 'echo "MAYBE"; exit 0'
    else:
        raise ValueError(mode)
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then\n'
        f'  exec "{real_python3}" "$@"\n'
        "fi\n"
        f"{tail}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{tmp_path}:{os.environ['PATH']}"}


@pytest.mark.parametrize("mode", ["nonzero", "empty", "garbage"],
                         ids=["evaluator-nonzero", "evaluator-empty-verdict", "evaluator-unrecognized-verdict"])
def test_discipline_blocks_on_unrecognized_evaluator_verdict(tmp_path, mode):
    env = _python3_evaluator_stub(tmp_path, mode)
    p = _discipline("git push --force origin main", extra_env=env)
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-SCW-14 =================

def test_compact_reinject_still_emits_release_and_posture_after_removal(tmp_path):
    """Post the AC-SCW-8 dead run-ledger-spawn removal: the real hook, driven as a subprocess
    over a `source: compact` payload against a fixture project dir holding exactly one active
    release, still exits 0 and still writes a manifest carrying both the release line and the
    posture line — the surviving (a)/(b) sections assemble unchanged."""
    project_dir = tmp_path / "project"
    release_dir = project_dir / ".foundry" / "releases" / "scw-fixture"
    release_dir.mkdir(parents=True)
    release_doc = {
        "id": "scw-fixture",
        "description": "AC-SCW-14 fixture release for the compact-reinject smoke.",
        "state": "active",
        "atoms": [{
            "id": "atom-one",
            "spec_ref": "specs/features/foundry/fixture/feat-fixture.md",
            "contract_ref": "specs/features/foundry/fixture/acceptance-contract.yaml",
            "depends_on": [],
        }],
    }
    (release_dir / "release.yaml").write_text(yaml.safe_dump(release_doc, sort_keys=False), encoding="utf-8")

    payload = json.dumps({"source": "compact", "session_id": "scw-14-smoke-session"})
    p = _run_hook("foundry-compact-reinject.sh", stdin_text=payload,
                 extra_env={"CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "release: scw-fixture" in p.stdout, p.stdout
    assert "posture: " in p.stdout, p.stdout


# ---------------------------------------------------------------- uncovered hook selftests
def test_env_reap_selftest_green():
    p = _run_hook("foundry-env-reap.sh", args=("--selftest",))
    assert p.returncode == 0, p.stdout + p.stderr


def test_worktree_remove_selftest_green():
    p = _run_hook("foundry-worktree-remove.sh", args=("--selftest",))
    assert p.returncode == 0, p.stdout + p.stderr


def test_harvest_learnings_selftest_green():
    p = _run_hook("foundry-harvest-learnings.sh", args=("--selftest",))
    assert p.returncode == 0, p.stdout + p.stderr
