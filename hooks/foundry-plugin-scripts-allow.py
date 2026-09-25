#!/usr/bin/env python3
"""PreToolUse(Bash): let the plugin's own scripts run without a prompt (v1.18.0, AC-V118A-1).

WHY A HOOK. Measured 2026-09-25 with live `claude -p` runs: no Bash permission rule naming a script
path matched — not the permission floor's `~/.claude/plugins/cache/*/foundry/*/scripts/...` shape,
not even an exact absolute path — while skills invoke every script as
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<x>.py"`. So the floor's allow rows granted nothing and
every foundry script call prompted the operator or went to the auto-mode classifier. A PreToolUse
hook returning `permissionDecision: "allow"` did make the same command run (and without it the
command was denied). Operator decision 2026-09-25: no foundry script prompts, authorize included.

WHAT IT ALLOWS — exactly one invocation of a `.py`/`.sh` file whose REAL path is under THIS plugin's
`scripts/` or `hooks/` directory (the hook's own resolved plugin root), optionally after
`python3`/`python`/`bash`/`sh`, followed only by plain arguments. The path must be written as a real
path (absolute, or `~/…`): a `$` anywhere means no decision, because the shell that runs the command
does not see the hook's environment — measured 2026-09-25, `CLAUDE_PLUGIN_ROOT` is EMPTY in the Bash
tool's shell, so a literal `${CLAUDE_PLUGIN_ROOT}/scripts/x.py` would run `/scripts/x.py` (and a
quoted or escaped token a cwd-relative file) while the hook checked a different one (v1.18.0 security
review Blocks 1-2).

NEVER ALLOWED SILENTLY (security review Blocks 3-4) — scripts that execute commands read from their
input (`foundry-verify.py` runs stack-profile commands through a shell; `foundry-decommission.py`
passes register slots to `/bin/sh -c`), and `foundry-permissions-compile.py` with `--root` (which
could point the settings write at another tree). "Every foundry script runs silently" (operator
decision) means the plugin's own code, not an arbitrary command routed through it; these keep the
session's normal permission mode.

WHAT IT NEVER DOES — decide anything else. A compound command (`;`, `&&`, `|`), a redirection, a
subshell or command substitution, any other `$` expansion, a newline, a path outside the plugin
root, or anything it cannot parse gets NO output: the platform's normal permission mode decides,
exactly as if this hook did not exist. It never denies and never blocks; any error exits 0 silently.
"""
import json
import os
import shlex
import sys

INTERPRETERS = {"python3", "python", "bash", "sh"}
SUFFIXES = (".py", ".sh")
# Characters that make a command more than one plain invocation, or that the shell would expand
# differently from shlex: `$` (any expansion), quotes/backslash handled by requiring the parsed path
# to equal a real file, `(` `)` subshells, backticks, redirections, separators, newlines.
METACHARS = set(";&|<>()`$\\\n\r")
# Scripts that execute commands taken from their input: never allowed silently.
EXECUTES_INPUT = {"foundry-verify.py", "foundry-decommission.py"}


def _plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.realpath(root)


def _inside(path, base):
    return path == base or path.startswith(base + os.sep)


def allowed(command, root):
    if not isinstance(command, str) or not command.strip():
        return False
    if any(c in METACHARS for c in command):
        return False
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not argv:
        return False
    if argv[0] in INTERPRETERS:
        argv = argv[1:]
        if not argv or argv[0].startswith("-"):
            return False  # `python3 -c ...`, `bash -c ...`: not a script invocation
    script = argv[0]
    script = os.path.expanduser(script)
    if not os.path.isabs(script):
        return False
    real = os.path.realpath(script)
    if not os.path.isfile(real) or not real.endswith(SUFFIXES):
        return False
    if not any(_inside(real, os.path.join(root, d)) for d in ("scripts", "hooks")):
        return False
    name = os.path.basename(real)
    if name in EXECUTES_INPUT:
        return False
    if name == "foundry-permissions-compile.py" and any(a == "--root" or a.startswith("--root=") for a in argv[1:]):
        return False
    return True


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — never block
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command")
    try:
        ok = allowed(command, _plugin_root())
    except Exception:  # noqa: BLE001 — never block
        return 0
    if ok:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "foundry plugin script (foundry-plugin-scripts-allow)",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
