---
name: command-deck
description: The operator's lead over their own GitHub queue (/foundry:command-deck [start|status|stop|tick], optional --project / --initiative narrowing). start arms a recurring native wake in this session; status reports what is true now; stop deletes the wake and halts new dispatch; tick runs one pass now. Work, requests and progress come only from GitHub (foundry_inbox.py, foundry-ticket.py, the gn-04 events); each tick runs a fixed seven-step order - inbox, escalation, acknowledgement, reconcile, verification, dispatch within capacity, one operator summary. Trigger on "command deck", "/foundry:command-deck", "start the deck", "stop the deck", "is the deck running", "re-arm the deck".
---

# /foundry:command-deck

The deck is **this session**, woken by a native recurring prompt. It is the operator's lead over their
GitHub queue: it reads requests and work from GitHub, keeps owned work moving, and escalates what needs
the operator's hands. It is never a subagent and never a background shell loop.

## Subcommands

| Subcommand | What it does |
|---|---|
| `start` | Take the local lead lease, make the startup disclosures, arm the wake, run one tick. |
| `status` | Report the wake (from `CronList`), the lease, the inbox `last_full_check_at`, and owned work. Arms nothing. |
| `stop` | Delete the wake job, stop new dispatch, release the lease. |
| `tick` | Run ONE tick now. Arms nothing. |

Optional `--project <p>` / `--initiative <i>` narrow what the deck selects in step 6 of the tick. They never
narrow the inbox.

## Start

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py lead acquire`. If the local mutex for this
   operator + workspace is already held, **refuse to start** and name the conflict (holder and age). The
   refusal says: this check is local to this machine; cross-machine duplicates are visible only through the
   GitHub assignee, the linked draft PR and the `start` record.
2. Fill `tick-prompt.template.md` (`{{WORKSPACE}}`, `{{OPERATOR}}`, `{{NARROWING}}`) and pass it verbatim as the
   `CronCreate` recurring prompt. Default cadence `*/2 * * * *` is a **target, not a deadline**; an
   operator-chosen longer cadence is allowed and is stated here. `ScheduleWakeup` is the alternative wake.
3. Run the startup disclosures below, then one tick.

### Startup and resume disclosures

State all of these at `start` and on every resumed session:

- Whether a periodic wake is active (from `CronList`) and its expiry: recurring jobs expire after 7 days.
- A scheduled prompt fires only between turns, and a self-paced loop is not restored by `--resume`, so a
  resumed deck re-arms its wake explicitly.
- Any observed missed, busy-delayed or interrupted checks, as observations, not a guaranteed cadence.
- The inbox `last_full_check_at`.

## Tick

Each tick runs these seven steps in this order (the template carries the same list):

1. `foundry_inbox.py check` and reconcile pending (`foundry_inbox.py pending`).
2. Escalate each returned `escalate` item with one `PushNotification` and begin permitted resolution ahead of routine work.
3. Ack or schedule the other requests (`foundry_inbox.py ack|schedule`).
4. Review workers, CI, review feedback and ownership via `foundry-ticket.py reconcile`.
5. Record meaningful changes and verify claimed results, keeping a Done-means run distinct from acceptance.
6. Resume owned work or select the next task within capacity (`foundry-ticket.py next`); narrowing applies here only.
7. Emit one operator summary: done / running / waiting / decisions.

Discovery is gn-09, selection and capacity are gn-08, the worker launch contract is gn-11; this skill cites
them and does not restate them. Multiple-blocker ordering and capacity exceptions belong to the gn-01 working
agreement.

### Degraded operation

While the latest inbox result has `dispatch_allowed: false`, or the delivery queue is stale or unreadable,
skip new dispatch in step 6 and put the `decision_needed` text under decisions in the step-7 summary. Steps
1-5 and already-authorized running work continue.

## Stop and resume

- `stop` deletes the wake job (`CronDelete`) and stops new dispatch. Worktrees, branches, PRs and pending
  requests stay in place.
- When a stopped deck resumes, run `foundry-ticket.py reconcile` for every owned assignment **before any
  dispatch**.

## Sources

Work, requests and progress come only from GitHub, via `foundry-ticket.py`, `foundry_inbox.py` and the gn-04
events. Nothing else is a source of truth.
