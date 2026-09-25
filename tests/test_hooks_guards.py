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
    assert _eval_guard("aws s3 ls", wrapper="exec-wrapper run --").startswith("BLOCK")


def test_cloud_guard_allows_wrapped_invocation():
    assert _eval_guard("exec-wrapper run -- aws s3 ls", wrapper="exec-wrapper run --") == "ALLOW"


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
                  args=("--eval", cmd, "exec-wrapper run --", "aws,kubectl,tofu,terraform,helm,argocd", ""))
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
    _write_exec_guard_seam(project_dir, wrapper="exec-wrapper run --")
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
def _discipline(cmd, extra_env=None, cwd=None):
    body = {"tool_input": {"command": cmd}}
    if cwd is not None:
        body["cwd"] = str(cwd)          # the harness-reported session cwd (AC-V118B-3 resolves in it)
    payload = json.dumps(body)
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


# ==================================================================== compound / grouped =========
# Regression corpus for the compound-command and grouping shapes, added 2026-09-21 after the
# Claude Code 2.1.271–2.1.275 permission-parser fixes (a `cd`+`git` chain, two directory changes,
# a subshell, and one exempt component excusing a whole compound command). The paren rows were
# MEASURED ADMITTED before the paren rule in the normalizer: shlex kept `(git` and `main)` as
# single words, so the verb matched nothing and the refspec was not `main`. The spaced form
# `( git … )` blocked all along — the defect was the glue, not the grouping.

@pytest.mark.parametrize("cmd", [
    # cd + git chain, two directory changes, pushd
    "cd /tmp/x && git push --force origin main",
    "cd a && cd b && git push --force origin main",
    "pushd a && git push --force origin main",
    # a benign first component must not excuse the compound command
    "echo ok; git push --force origin main",
    "git status && git push -f origin main",
    "true || git push --force origin main",
    # grouping parens glued to the verb / the refspec / the PR selector (the measured bypass)
    "(git push --force origin main)",
    "(cd repo && git push --force origin main)",
    "git push --force origin main)",
    "(gh pr merge 1 --admin)",
    "(git branch -D main)",
    "gh pr view 1 && gh pr merge 1 --admin",
    # spaced grouping, already blocked — kept so the paren rule can never regress them
    "( git push --force origin main )",
    "{ git push --force origin main; }",
    # force intent spelled as a `+` refspec or a `src:dst` refspec
    "git push origin +main",
    "git push --force origin HEAD:main",
    # backticks: the same glued-word-boundary class (`` `git `` is not a verb) — measured ADMITTED
    # before the rule spaced them, found by the security review of the paren rule
    "`git push --force origin main`",
    "echo `git push --force origin main`",
    # an unquoted `$(…)` BEFORE the guarded token must not truncate the clause's argument run —
    # these three went BLOCK→ADMIT in the first version of the paren rule (which made `(` a
    # SEPARATOR), found by the same review; they are the reason the grouping tokens are plain
    # tokens, not clause boundaries
    "git push $(cat r) --force main",
    "git commit $(cat a) --no-verify",
    "rm -rf $(pwd)/.git",
    # the everyday spelling — force-push of the CURRENT branch, no refspec — wrapped: a stray
    # `)` counted as a refspec and disabled the no-refspec ⇒ protected rule (second-round
    # review, measured ADMIT; `git push -f origin)` had regressed from BLOCK)
    "(git push --force origin)",
    "`git push --force origin`",
    "git push -f origin)",
    "echo $(git push --force origin)",
    # a non-literal refspec resolves to a branch the scan cannot know ⇒ protected (pre-existing
    # ADMIT, closed in the same pass)
    "git push --force origin $BRANCH",
    "git push --force $(cat remote)",
    "git push --force origin 'feat-*'",
])
def test_discipline_convicts_compound_and_grouped_shapes(cmd, git_repo_on_main):
    # Run in a repo whose current branch is `main`: since AC-V118B-3 a no-refspec force-push
    # RESOLVES its destination, so the wrapped no-refspec rows convict because the branch they
    # resolve to is protected — the property those rows pin (wrapping must never loosen it).
    p = _discipline(cmd, cwd=git_repo_on_main)
    assert p.returncode == 2, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    "cd a && git status",
    "(cd a && git log -1)",
    "(git status)",
    "git push --force-with-lease origin feat",
    "echo $(git rev-parse --short HEAD)",
    "x=`cat v`; git status",
    # QUOTED parens stay one shlex token whatever the normalizer inserts inside the quotes —
    # a commit message is prose, not a clause (the CLAUDE.md "inline -m is fine" promise)
    'git commit -m "fix(scope): x"',
    'git commit -m "see (git push --force origin main)"',
])
def test_discipline_admits_benign_compound_and_grouped_shapes(cmd):
    p = _discipline(cmd)
    assert p.returncode == 0, p.stdout + p.stderr


def test_discipline_grouped_merge_reads_no_stray_selector():
    """A `)` glued to the PR selector is a grouping token, not a second selector: with green
    checks the grouped plain merge admits exactly as the bare one does."""
    env = _gh_stub_env(GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl")
    p = _discipline("(gh pr merge 42 --merge)", extra_env=env)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    "( cd /tmp && gh pr merge 42 --merge )",
    "(:; cd /tmp) && gh pr merge 42 --merge",
    "d=`cd /tmp && pwd`; gh pr merge 42 --merge",
], ids=["cd-first-in-subshell", "cd-later-in-subshell", "cd-inside-backticks"])
def test_discipline_blocks_merge_after_subshell_scoped_cd(cmd):
    """A `cd` anywhere inside a `( … )` group or a backtick span is scoped to that subshell in
    real bash, so the checkout `gh` resolves the PR from is NOT the one the scan would pin the
    check query to. Fail-closed even on green checks — this is the AC-MVC-4 false-ALLOW shape."""
    env = _gh_stub_env(GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl")
    p = _discipline(cmd, extra_env=env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "directory change" in p.stderr, p.stderr


def test_discipline_backtick_selector_is_refused_as_non_literal():
    """The backtick is deliberately NOT skipped in the merge-args positional slot: left there,
    `` gh pr merge `cat n` `` trips the PR-selector literal check with the precise refusal
    instead of querying gh for a PR named `cat` and failing closed by accident."""
    env = _gh_stub_env(GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl")
    p = _discipline("gh pr merge `cat prnum` --merge", extra_env=env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "not a literal" in p.stderr, p.stderr


@pytest.mark.parametrize("cmd", [
    # The declared BOUNDED RESIDUAL (the hook's own header) is shell indirection whose TEXT the
    # scan never sees: `bash -c "…"` admits because a quoted string is one shlex token. `$(…)`
    # is different — its text IS scanned once `(` is spaced out, so a guarded verb inside it
    # blocks (over-matching, the safe direction), while its evaluation is still not modelled.
    # Both directions are pinned so a change to either is deliberate and visible.
    # (The `bash -c` row duplicates tests/test_verb_path_resolution.py's residual assertion on
    # purpose: that file owns the header-claim/behaviour agreement, this file owns the corpus.)
    ("echo $(git push --force origin main)", 2),
    ('bash -c "git push --force origin main"', 0),
])
def test_discipline_indirection_residual_is_pinned(cmd):
    cmd, expected = cmd
    p = _discipline(cmd)
    assert p.returncode == expected, (
        p.stdout + p.stderr + "\n— if indirection now BLOCKS, good: then update the hook header's "
        "declared residual and tests/test_verb_path_resolution.py in the same change, never "
        "just this expectation.")


# ---- TRIPWIRE: heredoc bodies must stay in the scan -----------------------------------------
# These pass trivially against the guard as it stands, which does not treat a heredoc specially.
# They are here for the NEXT person who tries to make it treat one specially.
#
# The guard convicts on blocked text inside a heredoc body even though the shell feeds that body
# to a program's STDIN and never parses it as commands. That false BLOCK is real and annoying —
# it refuses the authoring of a doc, a commit message, or a test fixture that merely NAMES a
# blocked verb, including the fixtures in this file. An excision that exonerates such bodies was
# built, reviewed, and WITHDRAWN.
#
# THE ROWS ARE OF TWO KINDS, and the difference is the point. Every DEFEAT row was admitted by
# the withdrawn excision AND then confirmed to EXECUTE under real bash 3.2, each one run with a
# harmless payload in a scratch directory — the guard said nothing and the shell ran the command.
# The CONTROL rows are ordinary shapes the excision handled correctly; they are kept because a
# re-attempt has to keep handling them. Do not blur the two, and do not add a row to the DEFEAT
# group on reasoning alone: a row's value is that it records what was actually demonstrated.
#
#   a consumer that is neither a pipe member nor a first word (process substitution, an fd
#     hand-off, or a redirect target whose basename collides with an allowlisted one)
#   the continuation pre-pass splicing a body line into the terminator, so the closer resolves
#     to a LATER delimiter and the live shell between them is excised — fired by an ordinary
#     trailing backslash, not an adversarial one
#   a closer accepted where real `<<` would not accept one (indented), so the scan resumes
#     INSIDE bash's real body, where a data line that looks like an opener excises past the
#     real terminator
#   an opener matched where there is no redirection at all (in a comment, in a quoted argument,
#     in `$(( 1 << n ))`), deleting live command text from the scan
#
# The cause is singular: knowing where a heredoc begins and ends requires PARSING THE SHELL, and
# this guard is explicitly a heuristic scanner that does not. A heuristic exoneration on top of
# a heuristic scanner multiplies failure modes. If the false BLOCK is worth fixing, it is worth
# a real parse — not another allowlist. Until then the workaround is to author the text with the
# file-editing tool rather than a Bash heredoc, which is the better channel for writing files.

@pytest.mark.parametrize("cmd", [
    # --- CONTROL: ordinary shapes the withdrawn excision handled CORRECTLY -------------------
    # The first was its intended admit; the rest were its own bypass-control rows, which it
    # convicted as designed. None of these defeated it.
    "cat > /tmp/x <<EOF\ngit push --force origin main\nEOF",
    "bash <<EOF\ngit push --force origin main\nEOF",
    "cat <<EOF | bash\ngit push --force origin main\nEOF",
    "cat <<EOF | tee /tmp/x | bash\ngit push --force origin main\nEOF",

    # --- DEFEAT: admitted by the withdrawn excision, and each one confirmed EXECUTING ---------
    # a consumer that is neither a pipe member nor a first word — process substitution
    "tee >(bash) <<EOF\ngit push --force origin main\nEOF",
    "cat <<EOF > >(bash)\ngit push --force origin main\nEOF",
    "cat <<EOF | tee >(bash)\ngit push --force origin main\nEOF",
    # an fd hand-off, where the consumer is not even on the opener's line
    "exec 3> >(bash) ; cat <<EOF >&3\ngit push --force origin main\nEOF",
    # a redirect target whose basename collides with an allowlisted sink
    "2>/tmp/cat bash <<EOF\ngit push --force origin main\nEOF",
    # the trailing-backslash terminator splice — fired by an ordinary trailing backslash rather
    # than an adversarial construction, which is what made this the worst of the set
    "cat <<'EOF'\nx\\\nEOF\ngit push --force origin main\nEOF",
    # an indented closer real `<<` would not honour, so the scan resumes inside bash's real body
    # and a data line that looks like an opener excises past the real terminator
    "cat <<EOF\n EOF\ncat <<X\nEOF\ngit push --force origin main\nX",
    # `<<` in a comment, i.e. in no redirection position at all
    "cat notes.txt   # heredocs are written <<EOF\ngit push --force origin main\nEOF",

    # --- feat-foundry-guards-guard-structured-observations (AC-GSO-3): the round-1-remediation
    # rows, extending the tripwire from twelve to fifteen (still convict-only; the BLOCK set
    # does not shrink). All three are otherwise-plausible "inert sink" shapes that must NOT be
    # admitted by the new heredoc-aware tokenizer.
    #
    # A same-call write-then-run: the heredoc writes a script AND a later clause in the SAME
    # command string runs it — AC-GSO-2(iv) requires the sink path to be unmentioned elsewhere.
    "cat > f.sh <<EOF\ngit push --force origin main\nEOF\n; bash f.sh",
    # The same write-then-run with the consumer wrapped in a subshell: `(bash` / `f.sh)` are not
    # the sink path to a glue-blind tokenizer, so the body read as data and was neutralized.
    # The mentioned-elsewhere scan now spaces grouping tokens (security review, 2026-09-21).
    "cat > f.sh <<EOF\ngit push --force origin main\nEOF\n; (bash f.sh)",
    # A /dev/fd sink — AC-GSO-2(iii) excludes any path under /dev/ or /proc/ outright.
    "cat > /dev/fd/3 <<EOF\ngit push --force origin main\nEOF",
    # --- PR #178 security review round 1 (spec amendment auth_seq 3): the trailing-backslash
    # terminator splice WITH an inert sink — withdrawn bypass class 2, reopened by an early
    # version of this atom that removed backslash continuations BEFORE finding heredoc
    # boundaries. A quoted delimiter (`<<'EOF'`) suppresses ALL body processing, so real bash
    # ends the body at the FIRST raw line equal to "EOF" (the one right after "x \") — everything
    # from "git push --force origin main" onward is a REAL, EXECUTED top-level command, not
    # heredoc body at all. Splicing the continuation before boundary discovery merged "x \" with
    # the next line into "x EOF" (no longer equal to the delimiter), so the scan ran past the
    # real terminator, swallowed the live push into the (wrongly extended) body, and admitted it.
    "cat > notes.md <<'EOF'\nx \\\nEOF\ngit push --force origin main\nEOF",
])
def test_discipline_convicts_through_heredoc_shapes(cmd):
    p = _discipline(cmd)
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-GSO-2/5 ================
# feat-foundry-guards-guard-structured-observations: the flip side of the tripwire above — a
# guarded verb mentioned ONLY in prose inside a provably inert-sink heredoc body is now admitted
# (exit 0) through the real, live hook, not just through the tokenizer in isolation
# (tests/test_shell_scan.py covers the tokenizer unit; this proves the wiring).

def test_discipline_admits_guarded_verb_mentioned_only_inside_inert_sink_heredoc():
    admit_cmd = "cat > docs/x.md <<'EOF'\nSee `git push --force origin main` for details.\nEOF"
    p = _discipline(admit_cmd)
    assert p.returncode == 0, p.stdout + p.stderr


# ==================================================================== AC-GSO-4 ===================
# feat-foundry-guards-guard-structured-observations: the verdict carrier. A BLOCK prints exactly
# one JSON object on stdout — hookSpecificOutput.{hookEventName,permissionDecision,
# permissionDecisionReason} + observation.{status,guard,reason,evidence,retryable,remediation} —
# and the stderr line is the reason followed by the remediation; the retired "run the command
# yourself" sentence must not appear.

def test_block_emits_structured_observation_with_remediation():
    p = _discipline("git push --force origin main")
    assert p.returncode == 2, p.stdout + p.stderr
    payload = json.loads(p.stdout.strip().splitlines()[0])
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]
    obs = payload["observation"]
    assert obs["status"] == "blocked"
    assert obs["guard"] == "git-discipline"
    assert obs["reason"]
    assert isinstance(obs["evidence"], list) and obs["evidence"]
    assert isinstance(obs["retryable"], bool)
    assert obs["remediation"]
    assert "run the command yourself" not in p.stderr
    assert obs["reason"] in p.stderr
    assert obs["remediation"] in p.stderr


def test_cloud_guard_block_emits_structured_observation_with_remediation(tmp_path):
    project_dir = tmp_path / "project-with-seam"
    project_dir.mkdir()
    _write_exec_guard_seam(project_dir, wrapper="exec-wrapper run --")
    payload_in = json.dumps({"tool_name": "Bash", "tool_input": {"command": "aws s3 ls"}})
    p = _run_hook("foundry-cloud-cli-exec-guard.sh", stdin_text=payload_in,
                 extra_env={"CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 2, p.stdout + p.stderr
    payload = json.loads(p.stdout.strip().splitlines()[0])
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    obs = payload["observation"]
    assert obs["status"] == "blocked"
    assert obs["guard"] == "cloud-cli-exec-guard"
    assert "exec-wrapper run -- exec aws" in obs["remediation"]
    assert "run it yourself outside the agent" not in p.stderr


# ==================================================================== PR #178 round 2 ===========
# feat-foundry-guards-guard-structured-observations: the bash wrapper built `observation.evidence`
# from its own RAW, unredacted `$cmd` shell variable while `reason`/`permissionDecisionReason`
# (built in python via `_redact()`) were already correctly scrubbed — a secret redacted out of
# the reason text leaked verbatim through the sibling `evidence` field. Mirrors
# `tests/test_cloud_guard_verb_path.py::test_refusal_redacts_inline_secrets`.

def test_cloud_guard_observation_json_redacts_inline_secrets(tmp_path):
    project_dir = tmp_path / "project-with-seam"
    project_dir.mkdir()
    _write_exec_guard_seam(project_dir, wrapper="exec-wrapper run --")
    payload_in = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": "AWS_SECRET_ACCESS_KEY=s3cr3tvalue aws s3 ls"}})
    p = _run_hook("foundry-cloud-cli-exec-guard.sh", stdin_text=payload_in,
                 extra_env={"CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 2, p.stdout + p.stderr
    assert "s3cr3tvalue" not in p.stdout, f"the observation JSON leaked a secret: {p.stdout}"
    assert "s3cr3tvalue" not in p.stderr, f"stderr leaked a secret: {p.stderr}"
    payload = json.loads(p.stdout.strip().splitlines()[0])
    obs = payload["observation"]
    assert obs["evidence"] == ["AWS_SECRET_ACCESS_KEY=*** aws s3 ls"], obs["evidence"]
    assert "AWS_SECRET_ACCESS_KEY=***" in obs["reason"]


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


# ==================================================================== v1.18 fixtures ============
_GIT_ID = ["-c", "user.name=Foundry Test", "-c", "user.email=test@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def _git(repo, *args):
    return subprocess.run(["git", *_GIT_ID, "-C", str(repo), *args], capture_output=True,
                          text=True, check=True)


def _make_repo(path, branch="main", upstream_merge=None, config=None):
    """A real repo with one commit, checked out on `branch`, optionally tracking an upstream whose
    merge ref is `refs/heads/<upstream_merge>`, plus any extra `config` key/values."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "commit", "-q", "--allow-empty", "-m", "init")
    _git(path, "branch", "-M", "main")
    if branch != "main":
        _git(path, "checkout", "-q", "-b", branch)
    _git(path, "remote", "add", "origin", "https://example.invalid/o/r.git")
    if upstream_merge:
        _git(path, "config", f"branch.{branch}.remote", "origin")
        _git(path, "config", f"branch.{branch}.merge", f"refs/heads/{upstream_merge}")
    for k, v in (config or {}).items():
        _git(path, "config", k, v)
    return path


@pytest.fixture
def git_repo_on_main(tmp_path):
    return _make_repo(tmp_path / "repo-main", branch="main")


@pytest.fixture
def git_repo_on_feat(tmp_path):
    return _make_repo(tmp_path / "repo-feat", branch="feat-x", upstream_merge="feat-x")


# ==================================================================== AC-V118B-3 ================
# A force-push naming NO refspec resolves its destination (current branch / its upstream merge
# ref) in the repository it runs in; refused only when that is protected, and fail-closed when
# anything could make the branch at execution time differ from the one read now.

@pytest.mark.parametrize("cmd", [
    "git push --force-with-lease",
    "git push --force",
    "git push -f origin",
    "git push --force-with-lease origin",
    "git push --force-with-lease=feat-x origin",
    "git rebase origin/main && git push --force-with-lease",
    "git add -A && git commit -m wip && git push -f",
    "(git push --force origin)",
])
def test_v118b3_no_refspec_force_push_of_feature_branch_admitted(cmd, git_repo_on_feat):
    p = _discipline(cmd, cwd=git_repo_on_feat)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    "git push --force-with-lease",
    "git push -f origin",
    "(git push --force origin)",
])
def test_v118b3_no_refspec_force_push_of_protected_branch_refused(cmd, git_repo_on_main):
    p = _discipline(cmd, cwd=git_repo_on_main)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "PROTECTED branch 'main'" in p.stdout, p.stdout


def test_v118b3_upstream_merge_ref_protected_is_refused(tmp_path):
    # feature branch tracking origin/main: push.default=upstream would land on main.
    repo = _make_repo(tmp_path / "r", branch="feat-y", upstream_merge="main")
    p = _discipline("git push --force-with-lease", cwd=repo)
    assert p.returncode == 2, p.stdout + p.stderr


def test_v118b3_widened_protected_list_applies(tmp_path):
    repo = _make_repo(tmp_path / "r", branch="release")
    payload = json.dumps({"tool_input": {"command": "git push -f"}, "cwd": str(repo)})
    p = _run_hook("foundry-git-discipline.sh", stdin_text=payload, args=("--protected", "release"))
    assert p.returncode == 2, p.stdout + p.stderr
    p = _discipline("git push -f", cwd=repo)          # `release` not protected by default
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    "git push --force --all",
    "git push --force --mirror origin",
    "git push --force --tags",
    "git checkout main && git push -f",
    "git switch main; git push -f",
    "gh pr checkout 7 && git push -f",
    "git rebase origin/main main && git push -f",
    "git branch -u origin/main && git push -f",
    "git config push.default upstream && git push -f",
    "cd /tmp && git push -f",
    "GIT_DIR=/tmp/x git push -f",
    "HOME=/tmp git push -f",
    "git -c push.default=matching push -f",
    "git --git-dir=/tmp/x push -f",
    "git -C $DIR push -f",
])
def test_v118b3_unresolvable_no_refspec_force_push_stays_refused(cmd, git_repo_on_feat):
    p = _discipline(cmd, cwd=git_repo_on_feat)
    assert p.returncode == 2, p.stdout + p.stderr


def test_v118b3_repo_state_that_hides_the_destination_is_refused(tmp_path):
    detached = _make_repo(tmp_path / "det", branch="feat-x")
    _git(detached, "checkout", "-q", "--detach")
    matching = _make_repo(tmp_path / "match", branch="feat-x", config={"push.default": "matching"})
    pushref = _make_repo(tmp_path / "pref", branch="feat-x",
                         config={"remote.origin.push": "refs/heads/*:refs/heads/main"})
    notrepo = tmp_path / "not-a-repo"
    notrepo.mkdir()
    for d in (detached, matching, pushref, notrepo):
        p = _discipline("git push --force-with-lease", cwd=d)
        assert p.returncode == 2, (d, p.stdout + p.stderr)


def test_v118b3_dash_C_resolves_in_the_named_repo(git_repo_on_main, git_repo_on_feat):
    p = _discipline(f"git -C {git_repo_on_main} push -f", cwd=git_repo_on_feat)
    assert p.returncode == 2, p.stdout + p.stderr
    p = _discipline(f"git -C {git_repo_on_feat} push -f", cwd=git_repo_on_main)
    assert p.returncode == 0, p.stdout + p.stderr


# ==================================================================== AC-V118B-5 ================
# Every other refusal is unchanged — asserted from a NON-protected feature-branch cwd, so no row
# can pass merely because the current branch happens to be protected.
@pytest.mark.parametrize("cmd", [
    "git push --force origin main",
    "git push -f origin HEAD",
    "git push origin +main",
    "git push --force-with-lease origin feat:main",
    "git branch -D main",
    "git filter-repo --force",
    "git filter-branch --tree-filter true",
    "rm -rf .git",
    "git commit -n -m x",
    "gh pr merge 42 --admin --squash",
])
def test_v118b5_other_refusals_unchanged(cmd, git_repo_on_feat):
    p = _discipline(cmd, cwd=git_repo_on_feat)
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-V118B-2 ================
# `gh pr merge --auto` is admitted WITHOUT the live checks query (the platform's required checks
# enforce the wait); `--admin` stays refused; a non-auto merge still needs green checks.
_PENDING = dict(GH_STUB_CHECKS_EXIT=8, GH_STUB_CHECKS_OUTPUT="check-a\tpending\t1s\turl")


@pytest.mark.parametrize("cmd", [
    "gh pr merge 42 --auto --squash",
    "gh pr merge 42 --auto --merge",
    "gh pr merge 42 --auto --rebase",
    "gh pr merge 42 --auto",
    "gh pr merge --auto=true -s 42",
    "gh pr merge https://github.com/o/r/pull/42 --auto -d",
])
def test_v118b2_auto_merge_admitted_without_checks_query(cmd, tmp_path):
    log = tmp_path / "gh.log"
    p = _discipline(cmd, extra_env=_gh_stub_env(GH_STUB_LOG=log, **_PENDING))
    assert p.returncode == 0, p.stdout + p.stderr
    assert not log.exists() or "checks" not in log.read_text(), "the live query must not run"


@pytest.mark.parametrize("cmd", [
    "gh pr merge 42 --auto --admin --squash",
    "gh pr merge 42 --admin=true --auto",
])
def test_v118b2_auto_with_admin_still_refused(cmd):
    p = _discipline(cmd, extra_env=_gh_stub_env(GH_STUB_CHECKS_EXIT=0,
                                                GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl"))
    assert p.returncode == 2, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    "gh pr merge 42 --squash",
    "gh pr merge 42 --auto=false --squash",
    "gh pr merge 42 --disable-auto",
])
def test_v118b2_non_auto_merge_still_queries_and_refuses_pending(cmd):
    p = _discipline(cmd, extra_env=_gh_stub_env(**_PENDING))
    assert p.returncode == 2, p.stdout + p.stderr


# ==================================================================== AC-V118B-1 ================
# The write-jail blocks a linked-worktree session's write into a SIBLING checkout of the same
# repository and admits everything else (own worktree, ~/.claude, the temp dirs, unrelated paths).

def _cwd_enforce(target, cwd, tool="Write", extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    payload = json.dumps({"tool_name": tool, "tool_input": {key: str(target)}})
    return subprocess.run([str(HOOKS / "foundry-cwd-enforce.sh")], input=payload,
                          capture_output=True, text=True, env=env, cwd=str(cwd), timeout=60)


@pytest.fixture
def repo_with_worktrees(tmp_path):
    main = _make_repo(tmp_path / "main-checkout", branch="main")
    wt = tmp_path / "wt-a"
    sib = tmp_path / "wt-b"
    nested = main / ".wt" / "nested"
    _git(main, "worktree", "add", "-q", "-b", "a", str(wt))
    _git(main, "worktree", "add", "-q", "-b", "b", str(sib))
    _git(main, "worktree", "add", "-q", "-b", "n", str(nested))
    return {"main": main, "wt": wt, "sib": sib, "nested": nested, "tmp": tmp_path}


def test_v118b1_own_worktree_admitted(repo_with_worktrees):
    r = repo_with_worktrees
    for t in (r["wt"] / "a.txt", r["wt"] / "new" / "deep" / "b.txt"):
        p = _cwd_enforce(t, cwd=r["wt"])
        assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("which", ["main", "sib", "nested"])
def test_v118b1_sibling_checkout_blocked(repo_with_worktrees, which):
    r = repo_with_worktrees
    for t in (r[which] / "README.md", r[which] / "brand" / "new" / "file.py"):
        p = _cwd_enforce(t, cwd=r["wt"])
        assert p.returncode == 2, (t, p.stdout + p.stderr)
        assert '"decision":"block"' in p.stdout


def test_v118b1_shared_git_dir_and_traversal_blocked(repo_with_worktrees):
    r = repo_with_worktrees
    for t in (r["main"] / ".git" / "config",
              r["wt"] / ".." / "main-checkout" / "x.txt",
              r["wt"] / "sub" / ".." / ".." / "wt-b" / "y.txt"):
        p = _cwd_enforce(t, cwd=r["wt"])
        assert p.returncode == 2, (t, p.stdout + p.stderr)


def test_v118b1_symlink_into_sibling_resolved_physically(repo_with_worktrees):
    r = repo_with_worktrees
    link = r["tmp"] / "innocent-link"
    link.symlink_to(r["main"], target_is_directory=True)
    p = _cwd_enforce(link / "not-yet-existing.txt", cwd=r["wt"])
    assert p.returncode == 2, p.stdout + p.stderr
    # ...and a symlink INTO the own worktree is the own worktree.
    own_link = r["tmp"] / "own-link"
    own_link.symlink_to(r["wt"], target_is_directory=True)
    p = _cwd_enforce(own_link / "ok.txt", cwd=r["wt"])
    assert p.returncode == 0, p.stdout + p.stderr


def test_v118b1_nested_own_worktree_inside_main_admitted(repo_with_worktrees):
    r = repo_with_worktrees
    p = _cwd_enforce(r["nested"] / "mine.txt", cwd=r["nested"])
    assert p.returncode == 0, p.stdout + p.stderr
    p = _cwd_enforce(r["main"] / ".wt" / "not-a-worktree.txt", cwd=r["nested"])
    assert p.returncode == 2, p.stdout + p.stderr


def test_v118b1_home_claude_temp_and_unrelated_paths_admitted(repo_with_worktrees, tmp_path):
    r = repo_with_worktrees
    targets = [
        Path.home() / ".claude" / "projects" / "x" / "memory" / "new-note.md",   # (b)
        Path("/tmp") / "foundry-v118b1" / "scratch.txt",                          # (c)
        tmp_path / "unrelated" / "file.txt",                                      # (c) $TMPDIR / pytest tmp
        Path("/opt/foundry-v118b1-nonexistent/elsewhere.txt"),                    # (d)
    ]
    other_repo = _make_repo(tmp_path / "other-repo")                              # (d) a DIFFERENT repo
    targets.append(other_repo / "README.md")
    for t in targets:
        p = _cwd_enforce(t, cwd=r["wt"])
        assert p.returncode == 0, (t, p.stdout + p.stderr)
    p = _cwd_enforce(Path.home() / ".claude" / "plans" / "p.md", cwd=r["wt"], tool="Edit")
    assert p.returncode == 0, p.stdout + p.stderr


def test_v118b1_notebook_and_non_write_tools(repo_with_worktrees):
    r = repo_with_worktrees
    p = _cwd_enforce(r["main"] / "n.ipynb", cwd=r["wt"], tool="NotebookEdit")
    assert p.returncode == 2, p.stdout + p.stderr
    p = _cwd_enforce(r["main"] / "README.md", cwd=r["wt"], tool="Read")
    assert p.returncode == 0, p.stdout + p.stderr


def test_v118b1_main_clone_session_unjailed(repo_with_worktrees):
    r = repo_with_worktrees
    p = _cwd_enforce(r["sib"] / "x.txt", cwd=r["main"])
    assert p.returncode == 0, p.stdout + p.stderr


def test_v118_review_b5_case_variant_path_into_the_main_checkout_is_blocked(repo_with_worktrees):
    """v1.18.0 security review Block 5: on a case-insensitive volume a case-variant spelling of the
    main checkout is the SAME directory; containment is by (st_dev, st_ino), not by spelling."""
    r = repo_with_worktrees
    main = str(r["main"])
    variant = main[:-len("main-checkout")] + "MAIN-CHECKOUT"
    if not os.path.exists(variant):
        pytest.skip("case-sensitive filesystem: the variant is a different path")
    p = _cwd_enforce(os.path.join(variant, "README.md"), cwd=str(r["wt"]))
    assert p.returncode == 2, p.stdout + p.stderr


@pytest.mark.parametrize("cmd", [
    'bash -c "git checkout main" && git push --force-with-lease',
    "./x.sh; git push -f",
    "make release && git push --force",
    "git stash branch main && git push --force",
])
def test_v118_review_r1_an_earlier_non_git_or_branch_changing_clause_keeps_the_push_refused(tmp_path, cmd):
    """v1.18.0 security review R1: only branch-preserving git clauses may precede a bare force-push."""
    repo = _make_repo(tmp_path / "r", branch="feature/x")
    p = _discipline(cmd, cwd=repo)
    assert p.returncode == 2, p.stdout + p.stderr


def test_v118_review_r1_a_mirror_remote_keeps_the_push_refused(tmp_path):
    repo = _make_repo(tmp_path / "r", branch="feature/x", config={"remote.origin.mirror": "true"})
    p = _discipline("git push --force", cwd=repo)
    assert p.returncode == 2, p.stdout + p.stderr
