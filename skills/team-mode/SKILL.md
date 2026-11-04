---
name: team-mode
description: Show or change the workspace operating mode (/foundry:team-mode status|enable|disable). Single-operator mode is the default - the ticket-first loop with no team features and no GitHub team surfaces. Team mode is the opt-in GitHub-native model (from plugin 2.0) - capability check, deck events, usage collection, owned queue, operator inbox, slim command deck, issue-bound dispatch, leader-board. Trigger on "team mode", "enable team mode", "disable team mode", "single operator mode", "upgrade to team mode", "operating mode".
---

# /foundry:team-mode

The workspace runs in one of two **operating modes**, stored as `operating_mode` in
`.claude/foundry-project.json`. This is **not** the session posture (`/foundry:mode`
factory | noninteractive | interactive), which is a different axis and is untouched by this verb.

## The two modes

| | single-operator (default) | team (opt-in, from 2.0) |
|---|---|---|
| What it is | The ticket-first loop as it has always been: init, dispatch, merge-when-green, your own CI | Everything the GitHub-native model adds |
| Team features | none | capability check, deck events, usage collection, owned queue, operator inbox, slim command deck, issue-bound dispatch, leader-board |
| GitHub team surfaces | none (no Projects queue, no request discovery, no usage attribution) | on |
| Dispatch | contract lane only | contract lane and the issue-assignment lane |

Absent or malformed `operating_mode` resolves to `single-operator`, and `doctor` names the reason.
`team` is only valid with a well-formed `github_scope`; without one it also resolves to
`single-operator`.

## Commands

Run the script from the plugin root (the `status|enable|disable` argument maps to the CLI subcommands
`status`, `enable-team`, `disable-team`):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_mode.py" status [--json]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_mode.py" enable-team
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_mode.py" disable-team
```

Exit codes: 0 ok, 2 refused, 1 error.

- **status** prints the mode, where it came from, whether a `github_scope` is present, the installed
  plugin version, and whether `enable-team` would be eligible.
- **enable-team** checks, in this order, and refuses (exit 2, file untouched) at the first failure:
  1. the installed plugin version is **2.0.0 or later** (read from the running plugin root; on an older
     version it names that version and points at the `npx update-agentic-workspace` upgrade);
  2. `github_scope` is present and well-formed (owner and planning repo);
  3. the GitHub capability check (`foundry_github_capabilities.py`) is at least `partial` - `none` or
     unreachable refuses; `partial` passes and the uncovered sources are printed.
  Then it writes `operating_mode: team` atomically, preserving every other key, and prints what now
  changes. Running it again changes nothing.
- **disable-team** writes `operating_mode: single-operator` and prints what stops.

## What changes, and what is kept

Enabling turns on the team verbs: `foundry-ticket.py next|claim|reconcile|lead`, `foundry_inbox.py`,
`foundry_leader_board.py`, the capability check, usage binding on subagent start, and the issue-assignment
dispatch lane. In single-operator mode each of these refuses before any network call or write with:
`<verb> is a team-mode verb; this workspace is in single-operator mode (default). Enable it with: ...`

Disabling is fully reversible. It keeps `github_scope`, every runtime file and all history; it only stops
the team surfaces. Enable again at any time.

The 2.0 upgrade itself lands in single-operator mode; nothing changes until you run `enable-team`.
