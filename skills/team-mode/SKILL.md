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
  Then it writes `operating_mode: team` atomically and prints what now changes. The file is re-serialised
  as 2-space JSON: every key and value and the key order are preserved, but the bytes are not guaranteed
  identical (a 4-space file is reformatted). A symlinked `foundry-project.json` has its target rewritten.
  Running it again on a team workspace is a no-op that makes no network call. A pre-release plugin
  version (for example `2.0.0-rc.1`) never enables team mode.
- **disable-team** writes `operating_mode: single-operator` and prints what stops.

## What changes, and what is kept

Enabling turns on the team verbs: `foundry-ticket.py next|claim|reconcile|lead`, `foundry_inbox.py`,
`foundry_leader_board.py`, the `foundry_deck_events.py` and `foundry_usage.py` command-line tools, the capability check, usage binding on subagent start, and the issue-assignment
dispatch lane. In single-operator mode each of these refuses before any network call or write with:
`<verb> is a team-mode verb; this workspace is in single-operator mode (default). Enable it with: ...`

## Who may write deck markers, and which Done-means may run

A `<!-- foundry-deck: ... -->` marker comment (`resolved`, `accepted`, `ack`, `start`, `blocker_ack`, ...) is
authoritative only when its comment author is trusted: the authenticated `gh` login, a login listed in
`github_scope.operators` (the keys of that object in `.claude/foundry-project.json` - list every operator
there), or, only when no operators are listed, a repository OWNER, MEMBER or COLLABORATOR. Anyone else's marker
is shown as `unparsed` / `foreign_author`: visible, never authoritative, and it cannot suppress the operator's
own record. Likewise an issue's `## Done means` command is run by the dispatch path only when its author (and
last editor) has write access: `foundry-ticket.py read-issue <issue> --json` reports `done_trusted`,
`claim` refuses an untrusted one (`done-means-untrusted`) unless the operator, having read it, passes
`--trust-author`, and `next` skips such issues.

Disabling is fully reversible. It keeps `github_scope`, every runtime file and all history; it only stops
the team surfaces. Enable again at any time.

The 2.0 upgrade itself lands in single-operator mode; nothing changes until you run `enable-team`.
