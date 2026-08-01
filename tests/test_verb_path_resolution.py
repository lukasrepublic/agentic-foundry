"""feat-foundry-verb-path-resolution — a path-qualified `git`/`gh` must not escape the guard.

The hook located guarded operations by exact token equality (`if t != "git"`, `if t != "gh"`), so
any path-qualified invocation bypassed EVERY clause at once:

    git push --force origin main            -> exit 2 (blocked)
    /usr/bin/git push --force origin main   -> exit 0 (ADMITTED)

These tests drive the REAL hook end-to-end over a matrix of invocation spellings crossed with the
whole active set. The two load-bearing groups are `every_clause_inherits` (the widening actually
reaches all clauses, not just the one with a reported symptom) and `legitimate_paths_stay_admitted`
(the matcher does not start convicting `cd ~/src/git`).
"""
import json
import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, "hooks", "foundry-git-discipline.sh")

BLOCK = 2
ADMIT = 0


def run_hook(command, extra_args=()):
    """Drive the real hook with a PreToolUse-shaped payload; return its exit code."""
    proc = subprocess.run(
        ["bash", HOOK, "--protected", "main", *extra_args],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode


# The path spellings that must all resolve to the verb.
PATH_FORMS = ["/usr/bin/{v}", "/usr/local/bin/{v}", "./{v}", "../bin/{v}", "~/bin/{v}", "bin/{v}"]


# ------------------------------------------------------------------------------- AC-VPR-1
@pytest.mark.parametrize("form", PATH_FORMS)
def test_path_qualified_verb_is_matched(form):
    """A force-push to a protected branch must block however `git` is spelled."""
    git = form.format(v="git")
    assert run_hook(f"{git} push --force origin main") == BLOCK, (
        f"{git!r} escaped the guard — the path prefix carries no security meaning")


def test_bare_verb_behaviour_is_unchanged():
    """The regression floor: the bare form must still block, and benign ones still admit."""
    assert run_hook("git push --force origin main") == BLOCK
    assert run_hook("git status") == ADMIT
    assert run_hook("git push origin feature-branch") == ADMIT


# ------------------------------------------------------------------------------- AC-VPR-3
# (verb, command-suffix) for every member of the default active set, plus the gh clause.
ACTIVE_SET = [
    ("git", "push --force origin main", "force-push to a protected branch"),
    ("git", "push -f origin main", "force-push (-f) to a protected branch"),
    ("git", "push origin +main", "force-push via leading-+ refspec"),
    ("git", "branch -D main", "branch -D of a protected branch"),
    ("git", "branch --delete --force main", "branch --delete --force of a protected branch"),
    ("git", "filter-repo --force", "filter-repo"),
    ("git", "filter-branch --all", "filter-branch"),
    ("git", "commit --no-verify -m x", "commit --no-verify"),
    ("git", "commit -n -m x", "commit -n"),
    ("gh", "pr merge 11 --admin --squash", "gh pr merge --admin"),
]


@pytest.mark.parametrize("verb,suffix,label", ACTIVE_SET)
def test_every_clause_inherits_the_path_resolution(verb, suffix, label):
    """THE load-bearing case. The bypass defeated the WHOLE hook, not one clause, so the fix has to
    reach every clause — that is what a single shared matcher buys."""
    assert run_hook(f"/usr/bin/{verb} {suffix}") == BLOCK, (
        f"path-qualified /usr/bin/{verb} escaped the {label} clause")


@pytest.mark.parametrize("suffix", ["reset --hard HEAD~3", "rebase -i HEAD~3"])
def test_strict_history_clauses_inherit_the_path_resolution(suffix):
    """The opt-in widening set must inherit it too, or --strict-history is a partial guard."""
    assert run_hook(f"/usr/bin/git {suffix}", extra_args=("--strict-history",)) == BLOCK
    # and stays out of the DEFAULT set (the widening is still opt-in)
    assert run_hook(f"/usr/bin/git {suffix}") == ADMIT


def test_rm_rf_dot_git_clause_is_unaffected_by_the_matcher():
    """Clause (g) keys off the ARGUMENT `.git`, not the verb, so it must keep working — and must
    not be broken by the new basename logic."""
    assert run_hook("rm -rf .git") == BLOCK
    assert run_hook("rm -rf ./.git") == BLOCK


@pytest.mark.parametrize("form", PATH_FORMS)
def test_path_qualified_rm_is_matched(form):
    """The third clause loop needs its own POSITIVE case. AC-VPR-1 names `/bin/rm -rf .git`
    explicitly, and the atom's thesis is that no clause may be left behind — asserting only that
    the BARE rm form still works would leave the widening itself untested here."""
    rm = form.format(v="rm")
    assert run_hook(f"{rm} -rf .git") == BLOCK, f"{rm!r} escaped the rm -rf .git clause"


def test_ordinary_rm_commands_are_untouched():
    """`rm` is the most commonly typed of the three verbs; routing it through the matcher must not
    change which arguments the clause inspects."""
    for cmd in ["rm -rf node_modules", "rm -rf /tmp/scratch", "rm file.txt",
                "docker run --rm alpine true", "cp /bin/rm /tmp/", "find . -name rm -type f"]:
        assert run_hook(cmd) == ADMIT, f"{cmd!r} was falsely blocked"


# ------------------------------------------- evasions found by the independent security review
def test_backslash_line_continuation_cannot_splice_a_verb():
    """A `\\`+newline is removed by the shell BEFORE it parses words, so `gi\\<newline>t push`
    really does execute `git push`. The scan saw `["gi ;", "t", …]` and ADMITTED."""
    assert run_hook("gi\\\nt push --force origin main") == BLOCK, (
        "a line-continuation splice hid the verb from the scan")


def test_line_continuation_does_not_cause_a_false_block():
    """The same defect's other direction: an ordinary continuation produced a bogus `' ;'` token
    that the gh clause reported as an unrecognized token preceding `gh`."""
    assert run_hook("cd /tmp && \\\n  git status") == ADMIT, (
        "an ordinary line continuation caused a false block")


def test_shlex_failure_fallback_still_sees_quoted_verbs():
    """When shlex refuses the string (an apostrophe in a trailing comment bash ignores), the
    fallback split preserved quotes, so '\"git\"' matched no verb and a real force-push was
    admitted. The degraded path must still detect."""
    assert run_hook('"git" push --force origin main # don\'t') == BLOCK, (
        "the shlex-failure fallback let a quoted verb escape")


def test_path_qualified_wrapper_before_gh_is_recognized():
    """Wrappers must resolve through the same matcher as the verbs, or `/usr/bin/env` before `gh`
    is refused as an unrecognized token instead of taking the env-reproduction path."""
    assert run_hook("/usr/bin/env gh pr merge 11 --admin --squash") == BLOCK  # --admin still refused
    # a path-qualified wrapper must not turn a legitimate command into a context refusal
    assert run_hook("/usr/bin/env git status") == ADMIT


# ------------------------------------------------------------------------------- AC-VPR-4
@pytest.mark.parametrize("command", [
    "ls -la /usr/bin/git",
    "cp /usr/bin/git /tmp/",
    "cd ~/src/git && make build",
    "rm -rf /home/me/projects/git",          # a directory merely NAMED git
    "grep -r push /opt/gh",
    "cat /usr/share/doc/git/README",
    "git status",
    "git push origin feature-branch",
    "gh pr list",
])
def test_legitimate_paths_stay_admitted(command):
    """No false positives. This guard runs on every Bash call; a matcher that convicts ordinary
    commands trains the operator to work around the hook, which costs more than the hole it closes."""
    assert run_hook(command) == ADMIT, f"{command!r} was falsely blocked"


# ------------------------------------------------------------------------------- AC-VPR-5
@pytest.mark.parametrize("command", [
    "/usr/bin/gitlab-runner push --force origin main",
    "/opt/github-cli pr merge 11 --admin",
    "mygit push --force origin main",
    "/usr/bin/git-lfs push --force origin main",
    "/usr/bin/gitk --all",
])
def test_substring_basename_never_matches(command):
    """Matching is on the whole final path segment, never a substring: gitlab-runner, github-cli,
    git-lfs and gitk are different programs and must not be convicted."""
    assert run_hook(command) == ADMIT, f"{command!r} matched on a substring basename"


# ------------------------------------------------------------------------------- AC-VPR-6
def test_widening_only_no_admit_path():
    """AC-GITGUARD-3. Nothing added here may turn a BLOCK into an ADMIT, and no argument is an
    off-switch. Every previously-blocking form must still block."""
    for cmd in ["git push --force origin main", "git branch -D main", "git filter-repo --force",
                "git filter-branch --all", "git commit --no-verify -m x", "rm -rf .git"]:
        assert run_hook(cmd) == BLOCK, f"{cmd!r} stopped blocking — this is a widening only"
    for flag in ("--allow", "--force", "--off", "--disable"):
        assert run_hook("/usr/bin/git push --force origin main", extra_args=(flag,)) == BLOCK, (
            f"the hook argument {flag} acted as an off-switch")


def test_protected_branch_widening_still_applies_to_path_forms():
    """--protected must widen for path-qualified forms exactly as for bare ones."""
    assert run_hook("/usr/bin/git push --force origin release",
                    extra_args=("--protected", "release")) == BLOCK
    assert run_hook("/usr/bin/git push --force origin scratch") == ADMIT


# ------------------------------------------------------------------------------- AC-VPR-2
def test_single_shared_matcher():
    """The match must live in ONE helper both clause loops call, so a future guarded verb needs no
    per-clause edit and no clause can silently be left behind."""
    src = open(HOOK, encoding="utf-8").read()
    assert "def _is_verb" in src, "no shared verb matcher found"
    # Every clause loop must route through the helper. Asserting only the ABSENCE of `!= "git"`
    # would pass for a future clause written in the positive form (`if t == "gh":`) or for a
    # fourth guarded verb, so pin the correspondence directly: one `_is_verb` guard per loop.
    loops = re.findall(r"^for i, t in enumerate\(low\):\n(.*)$", src, re.MULTILINE)
    assert len(loops) == 3, f"expected 3 clause loops, found {len(loops)}"
    for body in loops:
        assert "_is_verb(" in body, (
            f"a clause loop does not route its verb match through the shared helper: {body.strip()!r}")
    for pat in ('!= "git"', '!= "gh"', '!= "rm"', '== "git"', '== "gh"'):
        assert pat not in src, f"a clause still compares the verb by exact equality ({pat})"


# ------------------------------------------------------------------------------- AC-VPR-7/-8
def test_header_states_the_coverage():
    """The file must say what the verb match covers now."""
    head = "".join(open(HOOK, encoding="utf-8").readlines()[:60])
    assert "path-qualified" in head.lower(), (
        "the header does not state that path-qualified invocations are matched")


def test_indirection_remains_a_declared_residual():
    """Shell indirection is NOT closed by this atom and must still be declared, not quietly
    implied away. The behaviour and the claim must agree."""
    src = open(HOOK, encoding="utf-8").read()
    assert "bash -c" in src and "RESIDUAL" in src.upper(), (
        "the indirection residual is no longer declared in the hook")
    # ...and it genuinely still escapes — the claim matches reality.
    assert run_hook('bash -c "git push --force origin main"') == ADMIT, (
        "indirection now blocks — good, but the declared residual is then stale and must be updated")
