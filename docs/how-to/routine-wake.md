# How to wake the command deck with a Routine

`CronCreate` is session-only: it lives inside the running session, is written to no file, and
auto-expires after a fixed number of days regardless. A **Routine** is different — it runs from
Anthropic's cloud on its own schedule, an API `/fire` call, or a GitHub event, and survives the
laptop closing. But a cloud session cannot spawn teammates, and it can message a local session only
while that local session is connected to Remote Control. So a Routine's whole job here is one
message: `TICK <programme> <UTC-stamp>` to the deck session, addressed by name. The deck stays
interactive and does the actual work; the Routine only wakes it.

## The recipe

Render the Routine's self-contained prompt, plus the `/schedule` recipe and the prerequisites it
needs, with:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry-routine-wake-prompt.py <programme> --deck-name <name>
```

Both `<programme>` and `<name>` must be a `[a-z0-9-]+` slug — the same shape every other release
identifier in this workspace already takes — or the script refuses rather than rendering a prompt
around a malformed value. `<name>` is the literal `--name` the deck session is running under; a
Routine finds it by that name via `ListAgents`, never by session id, so an unnamed deck session is
invisible to it.

The printed prompt is everything the Routine needs pasted verbatim into `/schedule`'s prompt
field: list the running sessions, find the one named `<name>`, send it exactly one `TICK` message
with a stamp the Routine computes itself at send time, then stop. It carries no other capability
and touches no file.

## Prerequisites (verify each before scheduling)

- The deck session is running with `--name <name>` — the exact name the rendered prompt will look
  for.
- The deck session is connected to Remote Control. A Routine is a cloud session; it can reach a
  local session only while that local session is connected.
- The deck session's `crossSessionInbound` setting is not `refuse`. Any other value that accepts
  an incoming message from a Routine will do.
- The Routine's own cadence is an hour or longer — Routines below that floor are not offered — and
  its repository is set to this workspace's own repository, its environment to default, and its
  connectors to none.

## What the deck does with an incoming TICK

The deck session treats an incoming `TICK <programme> <stamp>` exactly like its own scheduled
cron wake — see `skills/command-deck/SKILL.md`, "Incoming TICK". If a tick is already running when
one arrives, the deck ignores the duplicate with a single line rather than starting a second,
overlapping tick over the same programme.

## Honest limits

- **A Routine is `-p`-style.** It runs once per fire, with no memory of a prior fire and no
  standing authority over this workspace — its only capability, by design, is the one message
  above.
- **A message never counts as consent.** Receiving the `TICK` is not authorization for anything;
  the deck session still runs its own front-authorization and merge-floor checks on every tick,
  exactly as if the message had never arrived.
- **The deck's own permission prompts still fire.** Waking the deck does not pre-approve any tool
  call the tick goes on to make; a classifier denial surfaces exactly the way it would from a
  cron-armed tick.
- **Creating the Routine itself is an operator step**, via `/schedule` or the web — it belongs to
  the operator's own account, and this script never creates one on their behalf.

## Related

- `skills/command-deck/SKILL.md` — the deck's own "Incoming TICK" section, and "Cross-session
  messages" for the closed message-kind vocabulary `TICK` was added to.
- `docs/how-to/deck-and-containers.md` — the reach rules a `TICK` message rides the same as any
  other cross-session message.
- `scripts/foundry_message_kind.py` — the lint `TICK`'s shape (`TICK <programme> <UTC-stamp>`) is
  checked against.
