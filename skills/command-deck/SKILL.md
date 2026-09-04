---
name: command-deck
description: The programme-level command deck (/foundry:command-deck <programme-id>) — arm a recurring watcher that drives one release forward unattended, then manage it with the subcommands status | stop | restart | tick | prompt | list. Each tick re-measures the ready-set from disk, dispatches what the wave barrier has unblocked, verifies independently, lands what passes, and reports Accomplishments / Next / Blockers. Trigger on "command deck", "/foundry:command-deck", "watch this programme", "arm the watcher", "drive this release unattended", "start the command deck", "stop the watcher", "is the watcher running", "re-arm the deck". NOT /foundry:mode-autonomous, which is the per-wave IMPLEMENTATION driver for an already-authorized release and holds the merge-authority record; reach for the command deck when you want a CLOCK over a whole programme and one executive status per tick, and for mode-autonomous when you are driving one authorized release's atoms through implementation right now.
---

# /foundry:command-deck

Hand it one programme and walk away. The deck is a **recurring scheduled prompt that re-wakes this
session** — dispatch, verify, land, report — and escalates only what genuinely needs the operator's
hands.

```
/foundry:command-deck <programme-id>          arm the watcher (the default)
/foundry:command-deck status  [<programme>]   what is true right now + is a watcher recorded
/foundry:command-deck stop    <programme>     cancel the job and forget the record
/foundry:command-deck restart <programme>     cancel, re-render the prompt, re-arm
/foundry:command-deck tick    <programme>     run ONE tick now, arm nothing
/foundry:command-deck prompt  <programme>     print the rendered tick prompt, arm nothing
/foundry:command-deck list                    every watcher recorded in this workspace
```

## The shape that works — and the two that do not

Measured over the runs where this pattern actually drove releases to completion:

- **The deck is THIS session, woken by a recurring cron job.** It therefore holds the operator's
  authority by construction and never has to be handed it.
- **It is never a subagent.** Packaging operator authority into a brief and delegating it to an
  `Agent` is the exact shape a permission classifier is built to refuse — and refusing it is
  correct. Subagents are dispatched *by* a tick, as narrow implementers and reviewers with explicit
  "do not commit / do not authorize / do not merge" briefs.
- **It is never a background shell loop.** No `tick.py`, no `while true`. The tick is a wake-up of
  this session, not a process.

If you find yourself writing `Agent(... "you are the COMMAND DECK" ...)`, stop: that is the failure
mode, not the capability.

## Arming it

**1 — Resolve and measure first.** Never arm a watcher over a programme you have not measured.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry_command_deck_watch.py status <programme-id>
```

It prints the ready-set, a per-atom exclusion reason for everything not ready, the open wave, and
any existing watcher record. With no programme id it lists what is in flight. It REFUSES loudly
(exit 2) rather than printing a plausible zero.

**2 — Render the tick prompt.**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry_command_deck_watch.py prompt <programme-id>
```

The renderer fills in the programme, the absolute workspace path, the measurement command, the
manifest path, and a state snapshot. Read it before arming — it is the operating discipline the
watcher will run under, and the one section you should extend is §4, with the traps a worker on
*this* project has already hit twice.

**3 — Load the scheduler tool, then arm it.**

```
ToolSearch("select:CronCreate,CronDelete,CronList")
CronCreate(cron: "7,27,47 * * * *", recurring: true, prompt: "<the rendered prompt>")
```

Off-the-hour minutes are the default deliberately: `*/20` lands every session on the same instant,
and a deck contending with siblings for one machine is a failure a real run had to move off. Ten
minutes suits sub-hour work units; longer units want 20–30.

**4 — Record the job**, so a later session can tell a watcher was meant to be running:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry_command_deck_watch.py record <programme-id> \
  --job-id <id from CronCreate> --cron "7,27,47 * * * *"
```

**5 — Watch the first three ticks, then leave it.** If the first three are honest about what is
idle, the rest will be. If tick one manufactures work, §6 of the prompt is not strong enough yet —
`restart` with it hardened.

### If `CronCreate` is denied

Surface it as the single blocker and **stop**. Do not retry, and do not edit settings to grant it —
self-granting is refused, correctly, and every attempt is wasted turns. The operator grants it once,
in their own terminal:

```
/permissions
```

then adds an allow rule for `CronCreate`. It is a one-time grant; every later arm succeeds.

## Managing it

| subcommand | what it does |
|---|---|
| `status` | the live measurement + the watcher record, reported **separately** — see the caveat below |
| `stop` | `CronDelete(<job id>)`, then `… forget <programme>` to drop the record |
| `restart` | `CronDelete`, re-render the prompt, `CronCreate`, `… record` again. This is how you EDIT a tick prompt — there is no in-place edit, and rewriting the prompt every time the deck learns a rule is why the prompt gets good |
| `tick` | run one tick by hand: measure, then act on the rendered prompt without arming anything. The right way to dry-run before committing to a cadence |
| `list` | every watcher record in the workspace, with how long the job has left |

**A record is not a running watcher.** The scheduled job is **session-only** — written to no file,
dead the moment the session that armed it exits — and it **auto-expires after 7 days** regardless.
So `status` reports the on-disk record and the live measurement side by side and never reconciles
them: only `CronList` in the session holding the job can say whether it still fires. A record whose
job is gone means **re-arm**, and the record exists precisely so a restarted session knows to.

## What the deck may and may not do

- It **drives**; it is not an authority within the lane it drives. It never authorizes an atom, and
  an un-authorized spec never reaches `main`. Atoms awaiting the operator gate are batched into
  **one** blocker line, not one turn each.
- Merge authority is **not** restated here. It lives in `skills/mode-autonomous/SKILL.md` — cited,
  never copied, so the two skills cannot drift into disagreeing about who may merge.
- It adds no gate, no ledger, no tracker and no approval surface. The native Task graph is the work
  tracker; the release manifest is the queue.
- **Do not invent review or security machinery for a gap the operator's own authorize gate already
  covers.** A five-lens review of this capability once returned 16 Blocks of which 12 were phantom
  gaps assuming the operator had left the room. They have not.

## Escalation is a closed set

Only two things reach the operator: **external provisioning or an interactive credential step the
deck cannot perform**, and **a genuine no-consensus fork after prior-art research**. Anything
authorization-adjacent or irreversible parks. Everything else — including CI waits, review rounds
and the deck's own unfinished work — is *Next Tasks*, never a blocker.

## Related

- `/foundry:mode-autonomous` — the implementation driver for one authorized release's atoms, and
  the record of merge authority. The deck is a clock over a whole programme; that is a per-wave
  fan-out. Reach for it when the deck's tick says "implement these three atoms".
- `scripts/foundry_command_deck.py` — the derivation this skill measures with: `ready_set` (with the
  authorization re-derivation and the wave barrier), `is_idle`, `wake_seconds`, `may_land`,
  `graph_action`. Read-only, no cursor, no memo: every tick re-derives from disk.
- `/foundry:authorize-release` — how a batch of atoms awaiting the gate is put in front of the
  operator in one turn.
