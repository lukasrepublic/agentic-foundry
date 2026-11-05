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
    for n in ("foundry-verify.py", "foundry-permissions-compile.py"):
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


# ---- security review of #286: the floor's GitHub-writing `ask` verbs are never silently allowed -----------

_WRITERS = [
    ("foundry_migrate.py", "cutover --apply"), ("foundry_migrate.py", "cutover record"),
    ("foundry_deck_events.py", "emit o/r#1 ack --payload {}"),
    ("foundry_inbox.py", "check"), ("foundry_inbox.py", "ack https://github.com/o/r/issues/1"),
    ("foundry_inbox.py", "schedule u --target t"), ("foundry_inbox.py", "resolve u --evidence e"),
    ("foundry-ticket.py", "claim 7"), ("foundry-ticket.py", "claim 7 --trust"),
    ("foundry-ticket.py", "start 7 --trust-author"), ("foundry-ticket.py", "start 7 --trust"),
    ("foundry_usage.py", "bind --session s --issue i"),
    ("foundry_usage.py", "fallback-transcripts --cli-version 1"),
    ("foundry_usage.py", "--dataset /d fallback-transcripts --cli-version 1"),
    ("foundry_usage.py", "--dat=/d fallback-transcripts --cli-version 1"),
    ("foundry_usage.py", "--dataset /d ingest /c"), ("foundry_usage.py", "--dat=/d attribute"),
    ("foundry_execution_plan.py", "plan prog --from-json c.json"),   # dl-04: the whole script asks
    ("foundry_execution_plan.py", "publish prog --project o/1"),
    ("foundry_programme_issue.py", "render prog --plan p.yaml"),     # dl-06: the whole script asks
    ("foundry_programme_issue.py", "publish prog --issue o/r#1 --plan p.yaml"),
    ("foundry_deck_report.py", "--in t.json --programme-issue /b.md"), ("foundry_deck_report.py", "--in t.json --programme /b.md"),
    ("foundry_learned_distill.py", "propose prog --from-json c.json"),   # dl-10: the whole script asks
    ("foundry_learned_distill.py", "collect prog --project o/1"),
    ("foundry_research_note.py", "--in s.json --slug x"),   # gv-14: every invocation writes under .foundry/
    ("foundry_deck_report.py", "--in t.json --append-cost /l"), ("foundry_deck_report.py", "--in t.json --append-c /l"),
    ("deck-cost-census.py", "--session-dir /s --append /l"), ("deck-cost-census.py", "--session-dir /s --app /l"),
    ("deck-cost-census.py", "--session-dir /s --repo o/r --branch-prefix dl/"),
    ("deck-cost-census.py", "--session-dir /s --prs /p.json"), ("deck-cost-census.py", "--session-dir /s --pr=/p.json"),
]
_READERS = [
    ("foundry_migrate.py", "inventory"), ("foundry_deck_events.py", "parse o/r#1 --as-of 2026-01-01T00:00:00Z"),
    ("foundry_inbox.py", "scan"), ("foundry_inbox.py", "pending"),
    ("foundry-ticket.py", "read-issue 7 --json"), ("foundry-ticket.py", "next --json"),
    ("foundry-ticket.py", "reconcile"), ("foundry-ticket.py", "lead renew"),
    ("foundry_operating_model.py", "status"), ("foundry_operating_model.py", "validate"),
    ("foundry_operating_model.py", "explain deck.max_builders"), ("foundry_operating_model.py", "render-tick-section E2"),
    ("foundry_usage.py", "ingest /c"), ("foundry_usage.py", "attribute"), ("foundry_usage.py", "coverage"),
    ("foundry_blocker_check.py", "--in x"),               # gv-14: read-only lint
    ("foundry_om_trace.py", "--charters x"),              # gv-16: read-only traceability table
    ("foundry_ci_budget_lint.py", "x.yml"),               # gv-16: read-only CI budget lint
    ("deck-cost-census.py", "--session-dir /s --since 2026-01-01T00:00:00Z"),   # dl-14: read-only census
    ("foundry_deck_report.py", "--in t.json"),                                  # dl-14: the plain render stays silent
    ("deck-cost-census.py", "--stall-check /l"),                                # dl-14: read-only log check
]


@pytest.mark.parametrize("script,args", _WRITERS)
def test_writing_verbs_get_no_silent_allow(env, script, args):
    with open(os.path.join(env["root"], "scripts", script), "w") as f:
        f.write("print('x')\n")
    assert decide(env, f"python3 {env['root']}/scripts/{script} {args}") is None


@pytest.mark.parametrize("script,args", _READERS)
def test_read_only_verbs_stay_silently_allowed(env, script, args):
    with open(os.path.join(env["root"], "scripts", script), "w") as f:
        f.write("print('x')\n")
    assert decide(env, f"python3 {env['root']}/scripts/{script} {args}") == "allow"


# ---- gv-11 round 2: expansion characters and lifecycle hooks --------------------------------------------------
@pytest.mark.parametrize("args", ["{reap,--include-ignored}", "{reap,}", "r[e]ap", "re?p", "*", "reap {a,b}", "--x=[1]"])
def test_r2_brace_and_glob_characters_in_an_argument_get_no_silent_allow(env, args):
    with open(os.path.join(env["root"], "scripts", "foundry-env-hygiene.py"), "w") as f:
        f.write("print('x')\n")
    with open(os.path.join(env["root"], "scripts", "foundry-ticket.py"), "w") as f:
        f.write("print('x')\n")
    assert decide(env, f"python3 {env['root']}/scripts/foundry-env-hygiene.py {args}") is None
    assert decide(env, f"python3 {env['root']}/scripts/foundry-ticket.py {args}") is None
    assert decide(env, f"python3 {env['root']}/scripts/foundry-doctor.py {args}") is None


def test_r2_plain_arguments_are_unchanged(env):
    with open(os.path.join(env["root"], "scripts", "foundry-env-hygiene.py"), "w") as f:
        f.write("print('x')\n")
    assert decide(env, f"python3 {env['root']}/scripts/foundry-env-hygiene.py status --session s1") == "allow"
    assert decide(env, f"python3 {env['root']}/scripts/foundry-doctor.py --json") == "allow"


@pytest.mark.parametrize("hook", ["foundry-env-reap.sh", "foundry-worktree-remove.sh"])
@pytest.mark.parametrize("lead", ["bash", "sh", ""])
def test_r2_lifecycle_hooks_are_not_tools(env, hook, lead):
    with open(os.path.join(env["root"], "hooks", hook), "w") as f:
        f.write("#!/bin/sh\n")
    cmd = f"{lead} {env['root']}/hooks/{hook} --subagent-stop".strip()
    assert decide(env, cmd) is None
    # control: another hook script is still allowed
    assert decide(env, f"bash {env['root']}/hooks/foundry-git-discipline.sh --protected main") == "allow"


# Rows that were already `ask` in the floor but not enforced by the hook when this ratchet was added (dl-04). Frozen:
# the test fails for any NEW ask row the hook would silently allow. Shrink this set, never grow it.
_PRE_EXISTING_UNENFORCED = {
    "deck-cost-census.py",   # dl-14: asks only for --append/--repo/--prs in the hook code; the plain census stays silent by design
    "foundry-permissions-compile.py --write",
    "foundry_usage.py --dataset",
    "foundry-authorize.py",
    "foundry-bootstrap.sh",
    "foundry-cut-release.py",
    "foundry-doctor.py --heal",
    "foundry-merge-when-green.py",
    "foundry-stack-profile.py --relock",
    "foundry-worktree-gc.py --apply",
    "foundry_repo_fleet.py foreach",
    "foundry_repo_fleet.py sync",
    "foundry_repo_attach.py attach",
    "foundry_repo_attach.py create",
    "foundry_tier_preflight.py",
    "foundry_migrate.py",
    "foundry_governance.py",
    "foundry_deck_events.py",
}


def test_every_ask_floor_row_naming_a_script_is_enforced_by_the_hook():
    """Structural (third recurrence of this class): each floor row tiered `ask` that names scripts/<x> must be
    matched by _ASK_SCRIPTS (whole script), _ASK_VERBS (the row's verb), EXECUTES_INPUT or _TICKET_EXECUTING, else it
    is silently allowed. The frozen exemption set may only shrink: every entry must still be a real, still-unenforced row."""
    import importlib.util
    import re
    spec = importlib.util.spec_from_file_location("psa_hook", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    with open(os.path.join(REPO_ROOT, "docs", "permission-floor.json"), encoding="utf-8") as f:
        entries = json.load(f)["entries"]
    rows = [e["rule"] for e in entries if e.get("tier") == "ask" and "/scripts/" in e["rule"]]
    checked, missing, still_unenforced = 0, [], set()
    for rule in rows:
        m = re.search(r"/scripts/([^/\s):]+?)(?:\s+([^):]*?))?:\*\)$", rule)
        assert m, f"an ask row naming /scripts/ has a shape the scan cannot read: {rule}"
        script, rest = m.group(1), (m.group(2) or "").strip()
        checked += 1
        verb = rest.split()[0] if rest else None
        enforced = (script in hook._ASK_SCRIPTS or script in hook.EXECUTES_INPUT
                    or bool(verb and verb in hook._ASK_VERBS.get(script, set()))
                    or (script == "foundry-ticket.py" and verb in hook._TICKET_EXECUTING))
        key = script + (" " + rest if rest else "")
        if enforced:
            assert key not in _PRE_EXISTING_UNENFORCED, f"{key!r} is now enforced: remove it from _PRE_EXISTING_UNENFORCED"
        elif key in _PRE_EXISTING_UNENFORCED:
            still_unenforced.add(key)
        else:
            missing.append(rule)
    assert checked == len(rows) >= 5, "the structural scan did not cover every ask row naming /scripts/"
    assert len(_PRE_EXISTING_UNENFORCED) <= 18, "the exemption set may only shrink"
    assert still_unenforced == _PRE_EXISTING_UNENFORCED, (
        f"exemptions that no longer name an unenforced ask row: {sorted(_PRE_EXISTING_UNENFORCED - still_unenforced)}")
    assert not missing, f"ask floor rows the hook would silently allow: {missing}"
