"""feat-foundry-cloud-guard-verb-path — a path-qualified cloud CLI must not escape the guard.

The guard matched its tool word by exact equality and declared the path-qualified form an accepted
residual in its own comment, citing the sibling git-discipline guard as the reference. That
reference stopped holding when the sibling closed the same hole in v1.0.1:

    aws s3 rm s3://bucket --recursive           -> exit 2 BLOCK
    /usr/bin/aws s3 rm s3://bucket --recursive  -> exit 0 ALLOW

Driven through the guard's own `--eval` harness under an explicit hermetic config, never the live
seam. The load-bearing groups are `legitimate_tool_paths_stay_admitted` (this runs on every Bash
call, so an over-block is expensive) and `widening_only_and_inert_unchanged`.
"""
import os
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO_ROOT, "hooks", "foundry-cloud-cli-exec-guard.sh")

BLOCK = 2
ALLOW = 0
WRAPPER = "cloudwrap exec"
TOOLS = "aws,gcloud"


def run(command, wrapper=WRAPPER, tools=TOOLS, exempt=""):
    """Drive the shipped evaluator hermetically; return (exit_code, verdict_text)."""
    proc = subprocess.run(["bash", GUARD, "--eval", command, wrapper, tools, exempt],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


PATH_FORMS = ["/usr/bin/{t}", "/usr/local/bin/{t}", "./{t}", "../bin/{t}", "~/bin/{t}", "bin/{t}"]


# ------------------------------------------------------------------------------- AC-CGP-1
@pytest.mark.parametrize("form", PATH_FORMS)
@pytest.mark.parametrize("tool", ["aws", "gcloud"])
def test_path_qualified_tool_is_matched(form, tool):
    rc, _ = run(f"{form.format(t=tool)} s3 rm s3://bucket --recursive")
    assert rc == BLOCK, f"{form.format(t=tool)!r} escaped the guard"


def test_bare_tool_behaviour_is_unchanged():
    assert run("aws s3 rm s3://bucket --recursive")[0] == BLOCK
    assert run("gcloud compute instances delete x")[0] == BLOCK


# ------------------------------------------------------------------------------- AC-CGP-2
@pytest.mark.parametrize("command", [
    "aws-vault exec prod -- terraform apply",
    "/usr/bin/aws-vault exec prod",
    "myaws s3 ls",
    "/opt/gcloud-wrapper compute list",
    "/usr/bin/awslogs get group",
])
def test_substring_tool_name_never_matches(command):
    """Matching is on the whole final path segment: aws-vault, myaws, gcloud-wrapper and awslogs
    are different programs."""
    assert run(command)[0] == ALLOW, f"{command!r} matched on a substring"


# ------------------------------------------------------------------------------- AC-CGP-3
@pytest.mark.parametrize("command", [
    "ls -la /usr/bin/aws",
    "cp /usr/bin/aws /tmp/",
    "cd ~/src/aws && make build",
    "cat /opt/gcloud/README",
    "grep -r aws /etc/hosts",
    "echo installing aws",
])
def test_legitimate_tool_paths_stay_admitted(command):
    """No false positives. This guard runs on every Bash call; over-blocking trains the operator to
    work around it, which costs more than the hole it closes."""
    assert run(command)[0] == ALLOW, f"{command!r} was falsely blocked"


# ------------------------------------------------------------------------------- AC-CGP-4
@pytest.mark.parametrize("tool_form", ["aws", "/usr/bin/aws", "./aws", "~/bin/aws"])
def test_wrapper_routed_path_form_is_admitted(tool_form):
    """A correctly routed invocation must keep passing when the tool is named by path, or the fix
    makes the guard unusable for operators who invoke tools that way."""
    rc, _ = run(f"cloudwrap exec {tool_form} s3 ls")
    assert rc == ALLOW, f"wrapper-routed {tool_form!r} was refused"


def test_path_qualified_wrapper_still_routes():
    """The wrapper word itself may be path-qualified too."""
    assert run("/usr/local/bin/cloudwrap exec aws s3 ls")[0] == ALLOW


# ------------------------------------------------------------------------------- AC-CGP-5
def test_exempt_applies_to_path_form():
    """An exemption written as a bare tool name must not be silently voided when the operator
    invokes that tool by path."""
    assert run("aws configure list", exempt="aws configure")[0] == ALLOW
    assert run("/usr/bin/aws configure list", exempt="aws configure")[0] == ALLOW, (
        "the exemption did not apply to the path-qualified form")
    # and the exemption must stay narrow — a different subcommand is still guarded
    assert run("/usr/bin/aws s3 rm s3://b --recursive", exempt="aws configure")[0] == BLOCK


# ------------------------------------------------------------------------------- AC-CGP-6
def test_widening_only_and_inert_unchanged():
    """Nothing that previously blocked may now admit, and INERT mode (no wrapper configured =>
    allow-all) must be untouched — an adopter who has not opted in must not start getting refusals.
    """
    for cmd in ["aws s3 rm s3://b --recursive", "gcloud compute instances delete x"]:
        assert run(cmd)[0] == BLOCK, f"{cmd!r} stopped blocking — this is a widening only"
    # INERT: empty wrapper => allow everything, including the newly-matched path forms. This is
    # the ONLY disable switch; it must be untouched.
    for cmd in ["aws s3 rm s3://b --recursive", "/usr/bin/aws s3 rm s3://b --recursive"]:
        assert run(cmd, wrapper="")[0] == ALLOW, "INERT mode (no wrapper configured) was changed"
    # An empty tools list is NOT "guard nothing" — it falls back to DEFAULT_TOOLS (guard line
    # 150, `... or DEFAULT_TOOLS`). Verified pre-existing. Pinned here because the natural
    # reading of an empty config is the opposite, and the path form must follow the bare form.
    assert run("aws s3 rm s3://b", tools="")[0] == BLOCK
    assert run("/usr/bin/aws s3 rm s3://b", tools="")[0] == BLOCK, (
        "the default tool set did not apply to the path-qualified form")
    assert run("totallyrandomcmd foo", tools="")[0] == ALLOW


# ------------------------------------------------------------------------------- AC-CGP-7
def test_residual_comment_is_accurate():
    """The comment declaring the path-qualified form uncovered must be gone, and what IS still
    uncovered must be stated."""
    src = open(GUARD, encoding="utf-8").read()
    assert "basename-strip a path-qualified form is a NAMED bounded residual (NOT covered)" not in src, (
        "the stale residual declaration is still in the guard")
    assert "_word(" in src, "no shared tool-word resolver found"
    assert "DIFFERENT NAME" in src.upper() or "different name" in src, (
        "the remaining residual (a tool reachable under another name) is not stated")
