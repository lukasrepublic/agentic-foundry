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

**3 — Capability preflight, then load the scheduler tool and arm it.**

Before arming, run the capability preflight over every atom the ready-set names this wave
(feat-foundry-authorization-capability-preflight-at-dispatch, AC-CPD-3 — the same rule
`skills/mode-autonomous/SKILL.md`'s *Capability preflight before dispatch* section carries for the
implementation driver, cited here rather than restated):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry-capability-preflight.py --contract <atom's acceptance-contract.yaml>
```

On `missing`, surface **ONE blocker** (`why_operator: operator-approval`) whose `handoff.command`
carries the verdict's `rule_to_add` — a **`/permissions` RULE-ADDITION LINE** (the bare rule
string the operator pastes into `/permissions`, or the `.foundry/permissions.yaml` grant to add),
**never a shell command to run** — **instead of arming the watcher** — a tick that starts is a tick
that will hit the same classifier wall every wake. On `preconditions_unverified`, verify each
precondition by command before relying on the grant. On `ok`, arm as below.

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

### Before surfacing ANY permission request

**Consult `.foundry/permissions.yaml` first** (feat-foundry-authorization-standing-grants-as-policy,
AC-SGP-8) — the same rule `skills/mode-autonomous/SKILL.md` carries for the implementation driver,
cited here rather than restated so the two never drift apart. Before the deck surfaces a permission
request (`CronCreate` below, or any other): an `automatic` grant whose preconditions you have
verified by command → proceed, record the grant `id`; `approval_required` → one blocker line
naming the grant `id`; no matching grant → the request as today.

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

## Learning across waves (wave-learn)

When a tick observes the programme reach `completed` — every atom in a terminal state per
`derive_run_state` — write what the wave learned before you leave it, so the NEXT release's
`/foundry:intake` (see `skills/intake/SKILL.md`, step 1) reads it before asking anything:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry_command_deck.py write-state <programme-id> \
  --entries-json '{"decisions": ["…"], "artifacts": [{"path": "…", "reuse_as": "…"}], "open_risks": ["…"], "amendments_needed": ["…"]}'
```

This writes/merges `.foundry/releases/<programme-id>/state.yaml` — exactly the four keys
`decisions` / `artifacts` / `open_risks` / `amendments_needed`, no per-atom status, validated
against `schema/wave-state.schema.json`. It **merges**: whatever is already recorded stays: a new
call only adds entries, it never drops one. It **refuses** (exit 2) rather than writing an invalid
document, and it refuses the write itself — unless you pass `--force` — until the programme has
actually reached `completed`, so this is a wave-close step, not a running log kept mid-wave.

## Escalation is a closed set

The vocabulary is the atom's contract `escalate_when` field, not restated here: the closed enum
`external-provisioning`, `credential-step`, `no-consensus-after-research`, `security-widening`,
`irreversible-action` (schema `schema/acceptance-contract.schema.json`), or, for a charter-lane
atom, the charter's own `## Escalate when` section — the one carve-out from *yield ONLY on
`done_when` met | `escalate_when` hit | a fork the fork policy parks; anything else is a Next
Task and the tick continues* (feat-foundry-contract-done-when-escalate-when, AC-DWE-1/-4). Of the
five members, only **external-provisioning** / **credential-step** and a genuine
**no-consensus-after-research** fork reach the operator as a deck blocker; **security-widening**
and **irreversible-action** are the two-way-door fork policy's PARK members instead (see
`mode-autonomous`'s fork policy) — authorization-adjacent or irreversible parks rather than
escalates. Everything else — including CI waits, review rounds and the deck's own unfinished
work — is *Next Tasks*, never a blocker.

**A blocker without evidence is itself a Next Task.** Before the tick writes its Blockers section
(§5c of the tick prompt), it shapes each candidate as `schema/blocker.schema.json` — `claim`,
`evidence[]`, `attempted[]`, and `why_operator` drawn from the same closed set above — and runs
`scripts/foundry_blocker_check.py --in <candidates.json>`, which partitions them into `blockers`
(reported) and `next_tasks` (demoted, with why). This is a lint the tick runs on itself, not a new
authority: it cannot promote a Next Task into a blocker, only catch a blocker asserted without the
shape to back it.

**Anything the operator must run is handed over as data, not prose.** A `credential-step` or
`external-provisioning` blocker carries a `handoff` object — `cwd` (absolute or `~`-relative),
`command` (one bare command: no `&&`/`;`/`|`/newline, and no top-level `set -e`/`trap`/`exec`),
`why`, `expect` — instead of leaving the operator to parse a working directory and a runnable
command out of the claim's prose. The executive report itself is capped at twelve bullet lines
across its three sections (§2 of the tick prompt): ids go in a trailing `ids:` line or in a
blocker's `handoff` fields, human names stay in prose. The tick sends a native `PushNotification`
**only** when the Blockers partition is non-empty — one per tick, naming the blocker's `claim`
(§5d of the tick prompt) — never one per candidate and never for an empty partition.

## Related

- `/foundry:mode-autonomous` — the implementation driver for one authorized release's atoms, and
  the record of merge authority. The deck is a clock over a whole programme; that is a per-wave
  fan-out. Reach for it when the deck's tick says "implement these three atoms".
- `scripts/foundry_command_deck.py` — the derivation this skill measures with: `ready_set` (with the
  authorization re-derivation and the wave barrier), `is_idle`, `wake_seconds`, `may_land`,
  `graph_action`. Every one of those is read-only, no cursor, no memo: every tick re-derives from
  disk. `write_wave_state` (the `write-state` CLI subcommand, above) is the one named exception —
  the wave-close write "next waves learn from previous ones" needs.
- `/foundry:authorize-release` — how a batch of atoms awaiting the gate is put in front of the
  operator in one turn.
