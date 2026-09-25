"""v1.18.0 AC-V118A-1/-8: the PreToolUse(Bash) hook that lets the plugin's own scripts run silently.

Driven as the platform drives it (JSON on stdin, JSON or nothing on stdout) against a fake plugin root
and a fake home, so every accepted form and every rejected shape is pinned.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

HOOK = os.path.join(REPO_ROOT, "hooks", "foundry-plugin-scripts-allow.py")


@pytest.fixture()
def env(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude" / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.18.0"
    (root / "scripts").mkdir(parents=True)
    (root / "hooks").mkdir()
    (root / "scripts" / "foundry-doctor.py").write_text("print('x')\n")
    (root / "scripts" / "foundry-authorize.py").write_text("print('x')\n")
    (root / "hooks" / "foundry-git-discipline.sh").write_text("#!/bin/sh\n")
    for n in ("foundry-verify.py", "foundry-decommission.py", "foundry-permissions-compile.py"):
        (root / "scripts" / n).write_text("print('x')\n")
    (root / "scripts" / "floor.json").write_text("{}\n")
    other = home / ".claude" / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.17.6" / "scripts"
    other.mkdir(parents=True)
    (other / "foundry-doctor.py").write_text("print('x')\n")
    evil = tmp_path / "evil" / "scripts"
    evil.mkdir(parents=True)
    (evil / "foundry-doctor.py").write_text("print('x')\n")
    look = home / ".claude" / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.18.0-evil" / "x"
    look.mkdir(parents=True)
    (look / "run.py").write_text("print('x')\n")
    return {"home": str(home), "root": str(root), "evil": str(evil), "tmp": str(tmp_path)}


def decide(env, command, tool="Bash"):
    e = dict(os.environ, HOME=env["home"], CLAUDE_PLUGIN_ROOT=env["root"])
    p = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": tool, "tool_input": {"command": command}}),
                       capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return None
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out["permissionDecision"]


@pytest.mark.parametrize("cmd", [
    "{root}/scripts/foundry-doctor.py",
    'python3 "{root}/scripts/foundry-doctor.py" --help',
    "python3 '{root}/scripts/foundry-doctor.py' --json",
    "python {root}/scripts/foundry-doctor.py",
    "python3 {root}/scripts/foundry-authorize.py specs/a.md --operator op_x",
    "bash {root}/hooks/foundry-git-discipline.sh --protected main",
    "~/.claude/plugins/cache/agentic-foundry/foundry/1.18.0/scripts/foundry-doctor.py",
    "python3 {root}/scripts/foundry-doctor.py --message 'a quoted arg with spaces'",
    "python3 {root}/scripts/foundry-permissions-compile.py --check",
])
def test_one_plain_invocation_of_a_plugin_script_is_allowed(env, cmd):
    assert decide(env, cmd.replace("{root}", env["root"])) == "allow"


@pytest.mark.parametrize("cmd", [
    # v1.18.0 security review B1/B2: ANY `$` — the Bash tool's shell does not see the hook's
    # CLAUDE_PLUGIN_ROOT (it is empty there), and a quoted/escaped token runs a cwd-relative file
    'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py"',
    "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py",
    "python3 '${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py'",
    "python3 \\${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py",
    'python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-doctor.py"',
    # B3: scripts that execute commands from their input are never silent
    "python3 {root}/scripts/foundry-verify.py --project-dir /tmp/evil",
    "python3 {root}/scripts/foundry-decommission.py gate-check --register /tmp/r.yaml",
    # B4: the compiler pointed at another tree
    "python3 {root}/scripts/foundry-permissions-compile.py --write --root /Users/someone",
    "python3 {root}/scripts/foundry-permissions-compile.py --write --root=/Users/someone",
    # R3: another version / another marketplace's `foundry` in the cache is not THIS plugin
    "python3 ~/.claude/plugins/cache/agentic-foundry/foundry/1.17.6/scripts/foundry-doctor.py --json",
    # R4: only .py/.sh files
    "bash {root}/scripts/floor.json",
    # any backslash
    "python3 {root}/scripts/foundry-doctor.py \\\n --x",
    # compound, piped, redirected, backgrounded, substituted
    'python3 "{root}/scripts/foundry-doctor.py"; rm -rf ~',
    'python3 "{root}/scripts/foundry-doctor.py" && curl http://x',
    'python3 "{root}/scripts/foundry-doctor.py" | sh',
    'python3 "{root}/scripts/foundry-doctor.py" > ~/.bashrc',
    'python3 "{root}/scripts/foundry-doctor.py" < /etc/passwd',
    'python3 "{root}/scripts/foundry-doctor.py" &',
    'python3 "{root}/scripts/foundry-doctor.py" $(whoami)',
    'python3 "{root}/scripts/foundry-doctor.py" `whoami`',
    'python3 "{root}/scripts/foundry-doctor.py" "$HOME"',
    'python3 "{root}/scripts/foundry-doctor.py"\nrm -rf ~',
    '(python3 "{root}/scripts/foundry-doctor.py")',
    # not a script invocation
    'python3 -c "import os"',
    "bash -c 'rm -rf ~'",
    "sh -c {root}/scripts/foundry-doctor.py",
    "python3 -m foundry",
    "FOO=1 python3 {root}/scripts/foundry-doctor.py",
    "exec python3 {root}/scripts/foundry-doctor.py",
    "python3",
    "",
    "   ",
    # outside the plugin root, or a look-alike
    "python3 {evil}/foundry-doctor.py",
    "python3 {root}/../1.18.0-evil/x/run.py",
    "python3 {root}/cli/whatever.py",
    "python3 {root}/scripts/missing.py",
    "python3 scripts/foundry-doctor.py",
    "python3 ./scripts/foundry-doctor.py",
    "cat {root}/scripts/foundry-doctor.py",
    "rm {root}/scripts/foundry-doctor.py",
    "python3 '{root}/scripts/foundry-doctor.py",
])
def test_anything_else_gets_no_decision(env, cmd):
    assert decide(env, cmd.replace("{root}", env["root"]).replace("{evil}", env["evil"])) is None


def test_a_symlink_into_the_root_from_outside_is_judged_by_its_target(env):
    link = os.path.join(env["tmp"], "link.py")
    os.symlink(os.path.join(env["root"], "scripts", "foundry-doctor.py"), link)
    assert decide(env, f"python3 {link}") == "allow"
    out = os.path.join(env["root"], "scripts", "escape.py")
    os.symlink(os.path.join(env["evil"], "foundry-doctor.py"), out)
    assert decide(env, f"python3 {out}") is None


def test_non_bash_tools_and_garbage_input_never_decide(env):
    assert decide(env, "python3 " + env["root"] + "/scripts/foundry-doctor.py", tool="Edit") is None
    e = dict(os.environ, HOME=env["home"], CLAUDE_PLUGIN_ROOT=env["root"])
    for raw in ("not json", "{}", json.dumps({"tool_name": "Bash"}), json.dumps({"tool_name": "Bash", "tool_input": {"command": 7}})):
        p = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True, text=True, env=e, timeout=30)
        assert p.returncode == 0 and p.stdout.strip() == ""


def test_the_hook_is_wired_for_bash():
    hooks = json.load(open(os.path.join(REPO_ROOT, "hooks", "hooks.json")))
    bash = [g for g in hooks["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"]
    cmds = [h["command"] for g in bash for h in g["hooks"]]
    assert any(c.endswith("/hooks/foundry-plugin-scripts-allow.py") for c in cmds)
