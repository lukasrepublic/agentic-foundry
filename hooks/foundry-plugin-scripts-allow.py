#!/usr/bin/env python3
"""PreToolUse(Bash): let the plugin's own scripts run without a prompt (v1.18.0, AC-V118A-1).

WHY A HOOK. Measured 2026-09-25 with live `claude -p` runs: no Bash permission rule naming a script
path matched — not the permission floor's `~/.claude/plugins/cache/*/foundry/*/scripts/...` shape,
not even an exact absolute path — while skills invoke every script as
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<x>.py"`. So the floor's allow rows granted nothing and
every foundry script call prompted the operator or went to the auto-mode classifier. A PreToolUse
hook returning `permissionDecision: "allow"` did make the same command run (and without it the
command was denied). Operator decision 2026-09-25: no foundry script prompts, authorize included.

WHAT IT ALLOWS — exactly one invocation of a file under `<plugin root>/scripts/` or
`<plugin root>/hooks/`, optionally after `python3`/`python`/`bash`/`sh`, followed only by plain
arguments. The root may be written as the resolved `$CLAUDE_PLUGIN_ROOT`, the literal
`${CLAUDE_PLUGIN_ROOT}` / `$CLAUDE_PLUGIN_ROOT`, or any path that resolves inside a foundry plugin
cache (`~/.claude/plugins/cache/<marketplace>/foundry/<version>/`).

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
ROOT_TOKENS = ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT")
# Characters that make a command more than one plain invocation. `(` `)` also exclude subshells and
# `$(...)`; `$` is handled separately (only the root token may carry it).
METACHARS = set(";&|<>()`\n\r")


def _plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.realpath(root)


def _inside(path, base):
    return path == base or path.startswith(base + os.sep)


def _foundry_cache_script(real):
    """True when `real` is <home>/.claude/plugins/cache/<m>/foundry/<v>/(scripts|hooks)/<file>."""
    cache = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "plugins", "cache"))
    if not _inside(real, cache):
        return False
    parts = os.path.relpath(real, cache).split(os.sep)
    return len(parts) >= 5 and parts[1] == "foundry" and parts[3] in ("scripts", "hooks")


def allowed(command, root):
    if not isinstance(command, str) or not command.strip():
        return False
    if any(c in METACHARS for c in command):
        return False
    stripped = command
    for tok in ROOT_TOKENS:
        stripped = stripped.replace(tok, "")
    if "$" in stripped:
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
    for tok in ROOT_TOKENS:
        if script.startswith(tok):
            script = root + script[len(tok):]
            break
    script = os.path.expanduser(script)
    if not os.path.isabs(script):
        return False
    real = os.path.realpath(script)
    if not os.path.isfile(real):
        return False
    in_root = any(_inside(real, os.path.join(root, d)) for d in ("scripts", "hooks"))
    return in_root or _foundry_cache_script(real)


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
