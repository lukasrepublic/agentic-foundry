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
input (`foundry-verify.py` runs stack-profile commands through a shell), and `foundry-permissions-compile.py` with `--root` (which
could point the settings write at another tree). "Every foundry script runs silently" (operator
decision) means the plugin's own code, not an arbitrary command routed through it; these keep the
session's normal permission mode.

ALSO NEVER ALLOWED SILENTLY (security review of #286) — the verbs the permission floor tiers `ask` because
they write to GitHub or redirect writes: `foundry_migrate.py cutover`, `foundry_deck_events.py emit`,
`foundry_inbox.py check|ack|schedule|resolve`, `foundry-ticket.py claim` and `ci-allow`,
`foundry_governance.py render|baseline|select|declare|apply|upgrade`, `foundry_message_log.py post|import`, `foundry_research_note.py` (any invocation), `foundry_execution_plan.py` (any invocation, dl-04), `foundry_learned_distill.py` (any invocation, dl-10), `foundry_programme_issue.py` (any invocation, dl-06), `foundry_deck_resources.py --reset-oom-baseline|--from-json`, `foundry-env-hygiene.py mark-worktree|reap`, `foundry-deck-heavy` (runs a command), `foundry_prod_step.py` (runs a command, gv-13), `foundry_deck_report.py pause|resume`, `foundry_usage.py bind|fallback-transcripts` and `ingest|attribute` when `--dataset` is named, and any
abbreviation of `--trust-author`. The hook is the only silent-allow mechanism, so without this the `ask` rows
would never be enforced.

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
METACHARS = set(";&|<>()`$\\\n\r{}*?[]")   # {} * ? [ ] : brace/glob expansion builds a verb the token check never sees
# Scripts that execute commands taken from their input: never allowed silently.
EXECUTES_INPUT = {"foundry-verify.py", "foundry-test.sh", "foundry-deck-heavy", "foundry_prod_step.py"}
# Lifecycle hooks are not tools: they reap sessions/worktrees and run on harness events. Invoking one through Bash
# never gets a silent allow (the session's normal permission mode decides).
LIFECYCLE_HOOKS = {"foundry-env-reap.sh", "foundry-worktree-remove.sh"}
# foundry-ticket.py executes input only for these shapes (v2.0.0; security review of #270, R1):
# `done` runs the ticket's command; `--trust-author` adopts a command from outside the write team.
_TICKET_EXECUTING = ("done", "--trust-author")
# GitHub-WRITING (or otherwise floor-`ask`) verbs: the permission floor tiers them `ask`, but this hook is
# the only thing that grants a silent allow, so it must not grant one for them (security review of #286).
# Matched as plain tokens anywhere after the script (a false hit only costs a prompt, never a silent allow).
# `foundry-ticket.py lead acquire|renew|release` is deliberately NOT here: a local lease file only, floor
# tier allow, and the 2-minute deck tick calls `lead renew` unattended.
_ASK_VERBS = {
    "foundry_migrate.py": {"cutover"},
    "foundry_deck_events.py": {"emit"},
    "foundry_inbox.py": {"check", "ack", "schedule", "resolve"},
    "foundry-ticket.py": {"claim", "ci-allow"},   # `ci-allow` lifts the CI-edit guard (gv-10): the operator's call
    # render / baseline write governance/rendered/ and governance/local/baselines/; select / declare write the intake
    # state; apply / upgrade write the lock, the adopter layer and installed CI snippets and, under --pr, push a branch
    # and open a pull request (gv-15). discover, check, validate, load, applicable, exceptions and leak-check are read-only.
    "foundry_governance.py": {"render", "baseline", "select", "declare", "apply", "upgrade"},
    "foundry_message_log.py": {"post", "import"},  # push to the remote / read an issue; read|audit|ruleset-template stay silent
    "foundry_deck_report.py": {"pause", "resume"},  # write/clear .foundry/runtime/deck-pause.json (gv-13); `--in` and `state` stay silent
    "foundry_usage.py": {"bind", "fallback-transcripts"},  # the floor row is ask with or without --dataset
}
# Scripts the floor tiers `ask` as a whole because every invocation writes (gv-14: foundry_research_note.py writes
# .foundry/research/ and .foundry/decisions/). foundry_blocker_check.py is read-only and stays silently allowed.
# gv-10: foundry_queue_land.py enqueues a PR (writes to GitHub, pushes the base merge), so it asks as a whole too.
# dl-06: foundry_programme_issue.py edits the programme issue and posts a Project status update, so the whole script asks.
# dl-04: foundry_execution_plan.py publish pushes the planner ref and mutates the Project, so the whole script asks.
# dl-10: foundry_learned_distill.py writes proposal files and, on --open-prs/--open-tickets, pushes and opens PRs/issues, so the whole script asks.
# dl-12: foundry_close_out.py closes issues, takes a lease ref, creates a draft Release and posts a status update, so the whole script asks.
# dl-11: foundry_fleet.py creates and moves claim refs on GitHub with an installation token, so the whole script asks.
_ASK_SCRIPTS = {"foundry_research_note.py", "foundry_queue_land.py", "foundry_execution_plan.py", "foundry_learned_distill.py",
                "foundry_programme_issue.py", "foundry_close_out.py", "foundry_fleet.py"}
_USAGE_DATASET_VERBS = {"ingest", "attribute"}   # ask only when a dataset is NAMED (`--dataset`, any argparse abbreviation)


def _names_option(args, full):
    for a in args:
        head = a.split("=", 1)[0]
        if len(head) >= 3 and head.startswith("--") and full.startswith(head):
            return True
    return False


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
    if name == "foundry-ticket.py" and (any(a in _TICKET_EXECUTING for a in argv[1:])
                                         or _names_option(argv[1:], "--trust-author")):
        return False
    if name in _ASK_SCRIPTS:
        return False
    if name == "foundry_message_log.py" and ("FOUNDRY_MESSAGE_REMOTE" in os.environ or "FOUNDRY_MESSAGE_REPO" in os.environ):
        return False  # gv-20: the hook process's own environment redirects the remote even when the command text does not name it
    if name == "foundry_message_log.py" and (_names_option(argv[1:], "--remote") or _names_option(argv[1:], "--repo")
                                            or "FOUNDRY_MESSAGE_REMOTE" in command or "FOUNDRY_MESSAGE_REPO" in command):
        return False  # read|audit stay silent only against the default remote and repo (an injected --remote is an exfil path)
    if any(a in _ASK_VERBS.get(name, ()) for a in argv[1:]):
        return False
    if name == "foundry_usage.py" and _names_option(argv[1:], "--dataset") \
            and any(a in _USAGE_DATASET_VERBS for a in argv[1:]):
        return False
    if name in EXECUTES_INPUT or name in LIFECYCLE_HOOKS:
        return False
    if name == "foundry_governance.py" and "discover" in argv[1:] and _names_option(argv[1:], "--out"):
        return False   # gv-15: `discover --out` writes a file; the verb is otherwise read-only, so only that shape asks
    if name == "foundry_deck_report.py" and (_names_option(argv[1:], "--append-cost")
                                                 or _names_option(argv[1:], "--programme-issue")):
        return False   # dl-06: --programme-issue writes the body file. dl-14: --append-cost writes the cost log; the plain `--in` render and `state` stay silent
    if name == "deck-cost-census.py" and any(_names_option(argv[1:], o) for o in ("--append", "--repo", "--prs")):
        return False   # dl-14: --append writes a cost log; --repo runs gh; --prs reads a named file. The plain census and --stall-check stay silent
    if name == "foundry_deck_resources.py" and (_names_option(argv[1:], "--reset-oom-baseline")
                                                or _names_option(argv[1:], "--from-json")):
        return False   # the one write this script has, and the fixture-file read (gv-12): both ask
    if name == "foundry-env-hygiene.py" and ("mark-worktree" in argv[1:] or "reap" in argv[1:]):
        return False   # stamps / removes worktrees and their files (gv-12): every tool-call reap asks; the SessionEnd hook is not PreToolUse
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
