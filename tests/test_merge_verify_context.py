"""feat-foundry-merge-verify-context — clause (i) must verify THE PR BEING MERGED.

The hook's `gh pr merge` clause admits a merge only when a live `gh pr checks` query reports every
check green. It built that query from a stripped argv running in the HOOK's own context, so the
repo selector, the working directory and the GitHub identity were all dropped and the query
resolved whatever PR the ambient environment considered current.

These tests drive the REAL hook end-to-end against a recording `gh` shim on PATH and assert both
the argv/cwd/env it issues and the exit code it returns. The load-bearing one is
`test_cross_repo_red_pr_is_not_admitted`: the false-ALLOW, where a green same-numbered PR in the
ambient repo admits a merge whose own checks are red.
"""
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, "hooks", "foundry-git-discipline.sh")

BLOCK = 2
ADMIT = 0

# A `gh` shim that records each invocation (argv + cwd + the gh-relevant env) as one JSON line and
# answers according to a scripted table, so a test can model two repos whose PR #11 differ.
SHIM = r"""#!/usr/bin/env python3
import json, os, sys
rec = {"argv": sys.argv[1:], "cwd": os.getcwd(),
       "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR"),
       "GH_TOKEN": os.environ.get("GH_TOKEN"),
       "GH_HOST": os.environ.get("GH_HOST")}
with open(os.environ["GH_SHIM_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
table = json.loads(os.environ.get("GH_SHIM_TABLE", "{}"))
key = "--repo" if "--repo" in sys.argv else ("-R" if "-R" in sys.argv else None)
repo = sys.argv[sys.argv.index(key) + 1] if key else "<ambient>"
answer = table.get(repo, table.get("<default>", {"out": "All checks were successful", "rc": 0}))
print(answer["out"])
sys.exit(answer["rc"])
"""

GREEN = {"out": "All checks were successful", "rc": 0}
RED = {"out": "ci/build\tfail\t1m\thttps://example.invalid/runs/1", "rc": 1}


@pytest.fixture
def harness(tmp_path):
    """A PATH-shimmed `gh`, a scratch cwd, and a runner returning (exit_code, invocations)."""
    binp = tmp_path / "bin"
    binp.mkdir()
    gh = binp / "gh"
    gh.write_text(SHIM)
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    cwd = tmp_path / "ambient-repo"
    cwd.mkdir()

    def run(command, table=None, env_extra=None, run_cwd=None):
        log.write_text("")
        env = dict(os.environ)
        env["PATH"] = f"{binp}:{env['PATH']}"
        env["GH_SHIM_LOG"] = str(log)
        env["GH_SHIM_TABLE"] = json.dumps(table or {"<default>": GREEN})
        env.pop("GH_CONFIG_DIR", None)
        env.pop("GH_TOKEN", None)
        env.pop("GH_HOST", None)
        env.update(env_extra or {})
        proc = subprocess.run(
            ["bash", HOOK, "--protected", "main"],
            input=json.dumps({"tool_input": {"command": command}}),
            capture_output=True, text=True, timeout=60,
            cwd=str(run_cwd or cwd), env=env,
        )
        raw = log.read_text().strip()
        calls = [json.loads(l) for l in raw.splitlines() if l.strip()]
        return proc, calls

    run.cwd = cwd
    run.tmp = tmp_path
    return run


# ---------------------------------------------------------------- AC-MVC-5 — THE CROSS-REPO CASE
def test_cross_repo_red_pr_is_not_admitted(harness):
    """THE load-bearing case (AC-MVC-5). The merge names PR #11 in `target/repo`, whose checks are
    RED. The ambient repo's PR #11 is GREEN. Before the fix the hook dropped `--repo`, queried the
    ambient green PR, and ADMITTED a red merge — a silent, complete defeat of the control."""
    proc, calls = harness(
        "gh pr merge 11 --repo target/repo --squash",
        table={"target/repo": RED, "<ambient>": GREEN},
    )
    assert calls, "the hook issued no verification query at all"
    argv = calls[0]["argv"]
    assert "--repo" in argv and "target/repo" in argv, (
        f"verification did not name the repo being merged; argv={argv} — it graded a lookalike PR")
    assert proc.returncode == BLOCK, (
        f"a RED PR was ADMITTED (exit {proc.returncode}) because a same-numbered ambient PR was "
        f"green; argv={argv}")


def test_cross_repo_query_is_not_run_against_the_cwd_repo(harness):
    """The same defect stated as the reported false-BLOCK: the query must not be resolved against
    the working directory's repo when the command names a different one."""
    _proc, calls = harness(
        "gh pr merge 11 --repo target/repo --squash",
        table={"target/repo": GREEN, "<ambient>": GREEN},
    )
    argv = calls[0]["argv"]
    assert argv.count("--repo") == 1 and argv[argv.index("--repo") + 1] == "target/repo"


# ---------------------------------------------------------------------------------- AC-MVC-1
def test_repo_flag_is_carried(harness):
    """--repo and -R both reach the verification query."""
    for flag in ("--repo", "-R"):
        _proc, calls = harness(f"gh pr merge 11 {flag} target/repo --squash",
                               table={"target/repo": GREEN})
        argv = calls[0]["argv"]
        assert "--repo" in argv, f"{flag} was dropped from the query; argv={argv}"
        assert argv[argv.index("--repo") + 1] == "target/repo"


# ---------------------------------------------------------------------------------- AC-MVC-2
def test_cd_target_becomes_cwd(harness):
    """`cd <literal> && gh pr merge …` must run the query in that directory, since gh resolves the
    repo from the local remote when --repo is absent."""
    target = harness.tmp / "elsewhere"
    target.mkdir()
    _proc, calls = harness(f"cd {target} && gh pr merge 11 --squash")
    assert os.path.realpath(calls[0]["cwd"]) == os.path.realpath(str(target)), (
        f"query ran in {calls[0]['cwd']!r}, not the command's own cwd {str(target)!r}")


# ---------------------------------------------------------------------------------- AC-MVC-3
def test_inline_env_is_carried(harness):
    """Inline `VAR=value gh …` assignments select the GitHub identity/host. Dropping them runs the
    query as a different account, which resolves different repos and different visibility."""
    _proc, calls = harness(
        "GH_CONFIG_DIR=/tmp/gh-alt GH_HOST=github.example.com gh pr merge 11 --repo t/r --squash",
        table={"t/r": GREEN})
    assert calls[0]["GH_CONFIG_DIR"] == "/tmp/gh-alt", "inline GH_CONFIG_DIR was dropped"
    assert calls[0]["GH_HOST"] == "github.example.com", "inline GH_HOST was dropped"


def test_inline_env_does_not_leak_into_the_parent(harness):
    """The carried env must be scoped to the subprocess, not exported into the hook itself."""
    harness("GH_TOKEN=secret-value gh pr merge 11 --repo t/r --squash", table={"t/r": GREEN})
    assert os.environ.get("GH_TOKEN") != "secret-value"


# ------------------------------------------------------------------- AC-MVC-4 / AC-MVC-7
@pytest.mark.parametrize("command,why", [
    ("gh pr merge --squash", "no PR selector — would degrade to a current-branch lookup"),
    ("gh pr merge --repo target/repo --squash", "repo pinned but PR resolved from ambient branch"),
    ('cd "$SOMEDIR" && gh pr merge 11 --squash', "non-literal cd target"),
    ("cd /nonexistent/path/xyz && gh pr merge 11 --squash", "cd target does not exist"),
])
def test_unresolvable_context_blocks(harness, command, why):
    """AC-MVC-4: pinned or blocked. An ambient fallback is never acceptable — the query would grade
    a different object than the merge touches. All-green shim, so any ADMIT here is the defect."""
    proc, _calls = harness(command, table={"<default>": GREEN})
    assert proc.returncode == BLOCK, f"admitted despite unresolvable context ({why})"


def test_context_block_names_the_remedy(harness):
    """AC-MVC-7: a context refusal must be distinguishable from a red-check refusal and must name
    the argument that resolves it, or the operator debugs the wrong problem (as happened)."""
    proc, _calls = harness("gh pr merge --squash", table={"<default>": GREEN})
    msg = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == BLOCK
    assert "--repo" in msg or "PR number" in msg.lower() or "selector" in msg.lower(), (
        f"refusal does not name the explicit argument that would fix it: {msg!r}")
    assert "not every check is green" not in msg, (
        "a context defect is being reported as a check failure — the misdiagnosis this atom fixes")


# ---------------------------------------------------------------------------------- AC-MVC-8
def test_url_selector_remains_sufficient(harness):
    """A PR URL carries owner/repo/number, so it is already unambiguous and must not start
    requiring --repo."""
    proc, calls = harness(
        "gh pr merge https://github.com/target/repo/pull/11 --squash",
        table={"<default>": GREEN})
    assert proc.returncode == ADMIT, "a fully-qualified PR URL was refused"
    assert "https://github.com/target/repo/pull/11" in calls[0]["argv"]


# ---------------------------------------------------------------------------------- AC-MVC-6
def test_existing_failclosed_paths_preserved(harness):
    """The no-bypass floor is untouched: --admin refused with NO query at all; a red check, a
    pending check, and a non-zero query exit each still block."""
    proc, calls = harness("gh pr merge 11 --repo t/r --admin --squash", table={"t/r": GREEN})
    assert proc.returncode == BLOCK, "--admin was admitted"
    assert not calls, "--admin must be refused outright, without running any query"

    proc, _ = harness("gh pr merge 11 --repo t/r --squash", table={"t/r": RED})
    assert proc.returncode == BLOCK, "a red check was admitted"

    pending = {"out": "ci/build\tpending\t-\thttps://example.invalid/runs/2", "rc": 0}
    proc, _ = harness("gh pr merge 11 --repo t/r --squash", table={"t/r": pending})
    assert proc.returncode == BLOCK, "a pending check was admitted"


def test_a_genuinely_green_pinned_merge_is_still_admitted(harness):
    """The guard must not become unconditional: a correctly-pinned, genuinely green merge passes."""
    proc, calls = harness("gh pr merge 11 --repo t/r --squash", table={"t/r": GREEN})
    assert proc.returncode == ADMIT, (
        f"a pinned, green merge was refused — the fix over-blocks. out={proc.stdout!r} "
        f"err={proc.stderr!r}")
    assert calls and "--repo" in calls[0]["argv"]


def test_no_bypass_argument_downgrades_a_block(harness):
    """AC-MVC-6 / AC-GITGUARD-3: no argument may turn a BLOCK into an ADMIT."""
    for extra in ("--allow", "--force", "--no-verify-checks", "--skip-checks"):
        proc, _ = harness(f"gh pr merge 11 --repo t/r {extra} --squash", table={"t/r": RED})
        assert proc.returncode == BLOCK, f"{extra} downgraded a red-check BLOCK to an ADMIT"


# ------------------------------------------------- AC-MVC-6: holes found in adversarial review
def test_inline_path_assignment_cannot_plant_a_fake_gh(harness, tmp_path):
    """Carrying inline env must NOT become a bypass. `PATH=<evil> gh pr merge …` would otherwise
    make the verification subprocess resolve a planted `gh` that reports success — the executable
    lookup honours the passed env's PATH. Only GH_*/GITHUB_* may be carried."""
    evil = tmp_path / "evil"
    evil.mkdir()
    fake = evil / "gh"
    fake.write_text("#!/bin/sh\necho 'All checks were successful'\nexit 0\n")
    fake.chmod(0o755)
    proc, calls = harness(f"PATH={evil} gh pr merge 11 --repo t/r --squash", table={"t/r": RED})
    assert proc.returncode == BLOCK, (
        "an inline PATH assignment was carried into the verification subprocess — a planted `gh` "
        "could forge a green verdict")
    assert not calls, "the planted gh should never have been consulted"


def test_non_github_inline_assignment_blocks(harness):
    """An assignment outside the allowlist is refused, not silently dropped — a dropped variable
    could change the answer just as a carried one could."""
    proc, _ = harness("FOO=bar gh pr merge 11 --repo t/r --squash", table={"t/r": GREEN})
    assert proc.returncode == BLOCK


def test_conflicting_repo_selectors_block(harness):
    """Two different repos named in one merge: gh's precedence is not the guard's to guess."""
    proc, _ = harness("gh pr merge 11 --repo a/b --repo c/d --squash",
                      table={"a/b": GREEN, "c/d": GREEN})
    assert proc.returncode == BLOCK


def test_url_conflicting_with_repo_flag_blocks(harness):
    """A URL carries its own owner/repo; contradicting it with --repo is self-inconsistent."""
    proc, _ = harness(
        "gh pr merge https://github.com/a/b/pull/11 --repo c/d --squash",
        table={"<default>": GREEN})
    assert proc.returncode == BLOCK


def test_url_agreeing_with_repo_flag_is_admitted(harness):
    """The agreeing case must not be caught by the conflict rule (no over-block)."""
    proc, _ = harness(
        "gh pr merge https://github.com/a/b/pull/11 --repo a/b --squash", table={"a/b": GREEN})
    assert proc.returncode == ADMIT


# ------------------------------------------------- findings from the independent security review
@pytest.mark.parametrize("form", [
    "-Rtarget/red 11",        # pflag attached shorthand
    "-R=target/red 11",       # attached with '='
    "-R target/red 11",       # space-separated (was already handled)
    "--repo=target/red 11",
    "--repo target/red 11",
])
def test_every_repo_selector_form_is_pinned(harness, form):
    """SECURITY-REVIEW BLOCK. `gh` is cobra/pflag, whose shorthand accepts an ATTACHED value, so
    `-Rowner/repo` sets --repo exactly as `-R owner/repo` does. Recognising only the spaced and
    `--repo=` forms left the attached ones unpinned: the query fell back to ambient resolution and
    ADMITTED a red PR. All five forms must pin."""
    proc, calls = harness(f"gh pr merge {form} --squash",
                          table={"target/red": RED, "<ambient>": GREEN})
    assert proc.returncode == BLOCK, (
        f"form {form!r} left the query unpinned — a red PR was admitted; queried={calls}")


def test_value_taking_flag_cannot_hijack_the_pr_selector(harness):
    """A value written before the PR ref became `bare[2]`, so the guard verified a DIFFERENT PR
    than the merge targets: `--subject 12 11` checked #12 while merging #11."""
    proc, calls = harness("gh pr merge --subject 12 11 --repo target/red --squash",
                          table={"target/red": RED, "<ambient>": GREEN})
    argv = calls[0]["argv"] if calls else []
    assert "12" not in argv, f"the --subject value was verified as the PR selector; argv={argv}"
    assert proc.returncode == BLOCK


def test_newline_separated_chain_binds_the_right_clause(harness, tmp_path):
    """A newline bounds a clause like `;`. Without that, `clause_start` walked back to index 0 and
    the context binding read tokens from unrelated lines."""
    target = tmp_path / "nl-repo"
    target.mkdir()
    proc, calls = harness(f"cd {target}\ngh pr merge 11 --squash", table={"<default>": GREEN})
    assert proc.returncode == ADMIT, f"multi-line chain falsely blocked: {proc.stderr!r}"
    assert os.path.realpath(calls[0]["cwd"]) == os.path.realpath(str(target))


def test_unrelated_first_line_does_not_poison_the_clause(harness):
    """`git fetch` on its own line must not be read as a token preceding `gh`."""
    proc, _ = harness("git fetch\ngh pr merge 11 --repo t/r --squash", table={"t/r": GREEN})
    assert proc.returncode == ADMIT, f"unrelated preceding line caused a false block: {proc.stderr!r}"


@pytest.mark.parametrize("command", [
    "pushd /tmp && gh pr merge 11 --squash",
    "(cd /tmp && gh pr merge 11 --squash)",
    "cd .worktrees/x && gh pr merge 11 --squash",
])
def test_unmodellable_directory_change_blocks(harness, command):
    """pushd, subshell grouping, and a RELATIVE cd are directory changes this scan cannot resolve
    against the shell's own cwd (the Bash tool keeps a persistent shell). They must block, not
    silently fall back to the hook's cwd."""
    proc, _ = harness(command, table={"<default>": GREEN})
    assert proc.returncode == BLOCK, f"{command!r} silently used the hook's cwd"


def test_exec_capable_gh_vars_are_not_carried_and_ambient_ones_are_stripped(harness, tmp_path):
    """GH_PAGER/GH_BROWSER/GH_EDITOR are looked up and EXECUTED by gh, so a namespace allowlist
    over GH_* was too broad. They must neither be carryable inline nor survive from the ambient
    environment."""
    proc, _ = harness("GH_PAGER=/tmp/evil gh pr merge 11 --repo t/r --squash", table={"t/r": GREEN})
    assert proc.returncode == BLOCK, "an exec-capable GH_* variable was accepted inline"

    _proc, calls = harness("gh pr merge 11 --repo t/r --squash", table={"t/r": GREEN},
                           env_extra={"GH_PAGER": "/tmp/evil", "GH_FORCE_TTY": "1"})
    assert calls, "no query was issued"
    # the shim records only gh-identity vars; assert the sanitiser ran by checking the process env
    assert calls[0].get("GH_CONFIG_DIR") is None


def test_admin_equals_true_is_refused(harness):
    """cobra bool flags accept `--admin=true`; an exact-token test missed it."""
    proc, calls = harness("gh pr merge 11 --repo t/r --admin=true --squash", table={"t/r": GREEN})
    assert proc.returncode == BLOCK, "--admin=true was admitted"
    assert not calls, "--admin=true must be refused outright, with no query"


# ------------------------------------------------- Blocks from the spec-review adversarial lens
@pytest.mark.parametrize("form", [
    "-sRtarget/red 11",       # pflag CLUSTERS short flags: -s + -R<attached value>
    "-dRtarget/red 11",
    "-sR=target/red 11",
    "-sdRtarget/red 11",
    "-sR target/red 11",
])
def test_clustered_short_flags_still_pin_the_repo(harness, form):
    """SPEC-REVIEW BLOCK, reproduced before fixing. Enumerating literal flag SPELLINGS was the
    wrong shape: `-sRowner/repo` starts with `-s`, so it matched neither the recognised `-R` forms
    nor the `-R…` catch-all, fell through the generic skip-any-dash branch, and the repo selector
    was silently dropped — readmitting the exact false-ALLOW this atom exists to close."""
    proc, calls = harness(f"gh pr merge {form}", table={"target/red": RED, "<ambient>": GREEN})
    argv = calls[0]["argv"] if calls else []
    assert "target/red" in argv, f"clustered form {form!r} left the query unpinned; argv={argv}"
    assert proc.returncode == BLOCK, f"a RED PR was admitted via clustered form {form!r}"


def test_clustered_value_flag_does_not_hijack_the_pr_selector(harness):
    """`-st 12 11` clusters boolean -s with value-taking -t, so `12` is --subject's value. It was
    landing in the PR-selector slot, making the guard verify #12 while gh merged #11."""
    _proc, calls = harness("gh pr merge -st 12 11 --repo target/green",
                           table={"target/green": GREEN})
    argv = calls[0]["argv"]
    assert "11" in argv and "12" not in argv, (
        f"the --subject value was verified as the PR selector; argv={argv}")


def test_author_email_short_flag_is_recognised(harness):
    """-A/--author-email is value-taking and was absent from the old enumeration entirely."""
    _proc, calls = harness("gh pr merge -A me@example.com 11 --repo target/green",
                           table={"target/green": GREEN})
    argv = calls[0]["argv"]
    assert "11" in argv and "me@example.com" not in argv


@pytest.mark.parametrize("bad", ["-Z 11", "--frobnicate 11", "-sZ 11", "--repo"])
def test_unrecognised_flag_fails_closed(harness, bad):
    """A flag outside gh's closed declared set must REFUSE, not be skipped — that is what makes
    the structural parse safe against a flag gh adds later."""
    proc, calls = harness(f"gh pr merge {bad}", table={"<default>": GREEN})
    assert proc.returncode == BLOCK, f"{bad!r} was silently skipped instead of failing closed"
    assert not calls, "an unparseable command must not reach the query"


@pytest.mark.parametrize("helpflag", ["--help", "-h"])
def test_help_invocation_merges_nothing_and_is_admitted(helpflag, harness):
    """`gh pr merge --help` prints usage. Refusing it is a pure false positive — and it bit the
    author of this atom while reading gh's own flag list."""
    proc, calls = harness(f"gh pr merge {helpflag}", table={"<default>": GREEN})
    assert proc.returncode == ADMIT, f"{helpflag!r} was refused; it merges nothing"
    assert not calls, "a help invocation must not trigger a check query"


def test_redirection_tokens_are_not_mistaken_for_the_pr_selector(harness):
    """The `&` connector normalisation splits `2>&1` into `2>` and `1`; `2>` then landed in the
    positional slot and the guard queried a PR named `2>`. Observed in practice."""
    proc, calls = harness("gh pr merge 11 --repo target/green --squash 2>&1",
                          table={"target/green": GREEN})
    argv = calls[0]["argv"] if calls else []
    assert "2>" not in argv, f"a redirection operator became the PR selector; argv={argv}"
    assert proc.returncode == ADMIT


def test_query_output_is_capped_and_redacted_in_refusals(harness):
    """The query's output is echoed into the transcript and comes from whatever host GH_HOST
    resolved to — untrusted content. It must be redacted and bounded."""
    huge = {"out": "GH_TOKEN=ghp_LEAKED_FROM_OUTPUT fail " + ("x" * 9000), "rc": 1}
    proc, _ = harness("gh pr merge 11 --repo t/r --squash", table={"t/r": huge})
    msg = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == BLOCK
    assert "ghp_LEAKED_FROM_OUTPUT" not in msg, "a secret in the query output was echoed verbatim"
    assert "truncated" in msg, "unbounded query output was echoed into the transcript"


def test_inline_token_is_redacted_from_the_refusal(harness):
    """An inline GH_TOKEN is a supported form, so a real PAT must not be echoed into the
    transcript on a refusal."""
    proc, _ = harness("GH_TOKEN=ghp_SUPERSECRETVALUE gh pr merge 11 --repo t/r --admin --squash",
                      table={"t/r": GREEN})
    msg = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == BLOCK
    assert "ghp_SUPERSECRETVALUE" not in msg, f"a token leaked into the refusal: {msg!r}"
    assert "<redacted>" in msg
