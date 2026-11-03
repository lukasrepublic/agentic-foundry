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
| `start` | Take the local lead lease (`lead acquire`), make the startup disclosures, arm the wake, run one tick. |
| `status` | Report the wake (from `CronList`), the lease (`lead check`), the inbox `last_full_check_at`, and owned work. Arms nothing. |
| `stop` | Delete the wake job, stop new dispatch, release the lease (`lead release`). |
| `tick` | Run ONE tick now. Arms nothing. |

Optional `--project <p>` / `--initiative <i>` narrow what the deck selects in step 6 of the tick. The lead
applies them by filtering the `next --json` candidates on their `labels` (a label equal to the value); the
base offers no other field. They never narrow the inbox.

Permission tier: `foundry_inbox.py check` is ask-tier. Before arming the deck the operator grants it a
standing allow (see `docs/how-to/standing-grants.md`). `foundry_inbox.py scan` (allow tier) is the read-only
fallback; it never records events.

## Start

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" lead acquire`. If the local mutex for this
   operator + workspace is already held, **refuse to start** and name the conflict (holder and age). The
   refusal says: this check is local to this machine; cross-machine duplicates are visible only through the
   GitHub assignee, the linked draft PR and the `start` record.
2. Fill `tick-prompt.template.md` (`{{WORKSPACE}}`, `{{OPERATOR}}`, `{{NARROWING}}`) and pass it verbatim as the
   `CronCreate` recurring prompt. Default cadence `*/2 * * * *` is a **target, not a deadline**; an
   operator-chosen longer cadence is allowed and is stated here. `ScheduleWakeup` is the alternative wake.
3. Make one successful `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" next --json` dry read (it makes the first tick's `--queue-status` `ok`), run the startup disclosures below, then one tick.

### Startup and resume disclosures

State all of these at `start` and on every resumed session:

- Whether a periodic wake is active (from `CronList`) and its expiry: recurring jobs expire after 7 days.
- A scheduled prompt fires only between turns, and a self-paced loop is not restored by `--resume`: on
  resume, check `CronList`; re-arm only if the wake is missing, expired, or was a self-paced loop (never
  double-arm a restored `CronCreate` task).
- Any observed missed, busy-delayed or interrupted checks, as observations, not a guaranteed cadence.
- The inbox `last_full_check_at`.

## Tick

Each tick runs these seven steps in this order (the template carries the same list):

1. Begin with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" lead renew`; if it exits non-zero ("no lease owned"), STOP this tick and report that the lease is lost (do nothing else). Then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_inbox.py" check --queue-status <ok|error|stale>` and reconcile pending (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_inbox.py" pending`). The `--queue-status` value is `ok` if the previous tick's `next` succeeded, `error` if it exited 3 or reported `queue: unavailable`, `stale` if the last successful `next` is older than three times the cadence (on the first tick, `ok` only after the successful `next --json` dry read made in `start`).
2. Send ONE `PushNotification` per NEW `escalate` key `(request_url, affected.url)` since the last tick, then begin permitted resolution ahead of routine work. Keep the set of already-notified keys in this session and do not re-push a key already in it; persisted blockers reappear in `pending` every tick but are not re-pushed. Re-notify a key only when gn-09 reports a material change (a new blocking relation time). `event_emitted: false` means the ack could not be posted: say so in the step 7 summary.
3. Ack or schedule the other requests: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_inbox.py" ack <url>` or `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_inbox.py" schedule <url> --target <when>`.
4. Review workers, CI, review feedback and ownership via `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" reconcile`.
5. Record meaningful changes through `foundry_deck_events` and verify claimed results, keeping a Done-means run (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" done`) distinct from acceptance. Event evidence is URLs only.
6. Resume owned work or select the next task within capacity (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" next --json`); only when `dispatch_allowed` is true and `lead renew` succeeded, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" claim <issue>` and then launch the worker per `skills/dispatch/SKILL.md` (Issue-assignment launch, gn-11). The narrowing above applies here only, the inbox always covers the full `github_scope`.
7. Emit one operator summary: done / running / waiting / decisions.

Discovery is gn-09, selection and capacity are gn-08, the worker launch contract is gn-11; this skill cites
them and does not restate them. Multiple-blocker ordering and capacity exceptions belong to the gn-01 working
agreement.

### Degraded operation

While the latest inbox result has `dispatch_allowed: false`, or the delivery queue is stale or unreadable,
skip new dispatch in step 6 and put the `decision_needed` text under decisions in the step-7 summary. Steps
1-5 and already-authorized running work continue.

## Stop and resume

- `stop` deletes the wake job (`CronDelete`), stops new dispatch and ends with `lead release`. Worktrees, branches, PRs and pending
  requests stay in place.
- When a stopped deck resumes, run `lead acquire` again, then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-ticket.py" reconcile` for every owned assignment **before any
  dispatch**.

## Sources

Work, requests and progress come only from GitHub, via `foundry-ticket.py`, `foundry_inbox.py` and the gn-04
events. Nothing else is a source of truth.
