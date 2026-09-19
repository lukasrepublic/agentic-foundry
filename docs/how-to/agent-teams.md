# How to enable agent teams

Nothing in Foundry uses the native team surface by default. Enabling it is an **adopter opt-in** —
no shipped verb, hook, or skill flips this flag for you, and this workspace's own settings are the
operator's, not something Foundry writes.

## Enable it

Add the flag to your own `settings.json`, per Anthropic's own documentation:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

While it is on, a subagent the lead **NAMES** — the `Agent` tool's `name` parameter, not a fork,
no `isolation` on the call — launches as a **teammate** instead of an ordinary subagent.

Run `/foundry:doctor` afterward. It prints one advisory line:

```
agent-teams: on (settings env)      # the effective settings' env block sets the flag to "1"
agent-teams: off                    # it does not
```

derived from your EFFECTIVE settings files (`~/.claude/settings.json`, then the project's
`.claude/settings.json`, then `.claude/settings.local.json`, in that ascending-precedence order).
The line is **never RED** — an absent or stale flag is informational, not a defect.

## The documented limitations

Straight from the primary documentation, not a summary:

- A subagent the lead **NAMES** (Agent tool `name`, not a fork, no `isolation` on the call)
  launches as a teammate — an UNNAMED subagent call stays an ordinary subagent even with the flag
  on.
- **`-p` sessions never spawn teammates.** The flag has no effect there.
- **One team per session; no nested teams.**
- **In-process teammates do not survive `/resume`.**
- **Task status can lag** — what you read from the task list can be briefly behind what a
  teammate has actually done.
- **Permission prompts bubble up to the lead.** A teammate cannot answer its own tool-permission
  prompt; the lead session sees and answers it.

## The token-cost stance

Anthropic's own guidance is direct about this: running a team costs **significantly more** than a
single chat session — on the order of **roughly fifteen times** a chat session's token spend, per
the swarm documentation. Price that in before arming a team, not after.

## When NOT to use a team

- **Sequential work.** When one piece of work's output feeds the next piece's input, there is
  nothing independent to parallelize — a team adds coordination overhead and cost for no benefit.
- **Same-file edits.** Two teammates racing to edit the same file produce exactly the conflict a
  team was supposed to let you avoid. Reach for a team only when the work is genuinely
  disjoint-scope.

## Foundry's own posture

- `workflows/release-wave.js`'s fan-out workers are **always unnamed** `agent(...)` calls — they
  stay ordinary subagents whether or not the operator has this flag on. See the comment at the top
  of that file and `tests/test_agent_teams_enablement.py`, which greps every `agent(...)` call's
  option object for a `name` key and asserts none carry one.
- `skills/command-deck/SKILL.md`'s "Teammates" section documents the one place Foundry's own
  machinery *does* consider spawning teammates — the command deck's tick, on explicit request
  only.

## Related

- `skills/command-deck/SKILL.md` — the deck's spawn convention (teammates only by explicit
  request, disjoint-scope atoms, `builder-<atom>`/`reviewer-<atom>` naming).
- `/foundry:doctor` — prints the `agent-teams` advisory line described above.
