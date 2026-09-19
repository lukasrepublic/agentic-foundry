#!/usr/bin/env python3
"""foundry-routine-wake-prompt — the self-contained prompt for a Routine's one job: wake the deck.

Implements the charter atom `routine-wake` (autonomy-continuation R3,
`.foundry/releases/ac-r3-native-swarm-substrate/charters/routine-wake.md`, AC-RWK-1).

`CronCreate` is session-only and expires after seven days. A Routine runs from Anthropic's cloud
on a schedule (an hour or longer between fires), an API `/fire` call, or a GitHub event, and
survives the laptop closing — but a cloud session cannot spawn teammates, and it can message a
local session only while that local session is connected to Remote Control. So the Routine's whole
job is ONE message: `TICK <programme> <UTC-stamp>` to the deck session, addressed by name. The
deck stays interactive, receives the tick as a trigger (see `skills/command-deck/SKILL.md`,
"Incoming TICK"), and does the actual work. This script never runs anything itself — it only
RENDERS the text a human pastes into `/schedule`'s prompt field, plus the recipe and the
prerequisites around it. Nothing here reads the release corpus or any live state: the whole point
is that the printed prompt is self-contained and needs nothing else at Routine-run time beyond the
two identifiers baked into it.

Usage:
    foundry-routine-wake-prompt.py <programme> --deck-name <name>

Both `<programme>` and `<name>` are validated as `[a-z0-9-]+` slugs before anything is rendered —
the same shape `foundry_release.load_release` and `foundry_command_deck_watch._slug` already
enforce elsewhere, applied here defensively even though nothing is ever joined into a path: the
deck-name and programme both flow into rendered prompt text that a human copies verbatim into a
cloud scheduler, and a control character or a shell metacharacter in either has no legitimate
reason to be there. Refuses (exit 2) on a malformed argument, naming which one and why; prints the
rendered prompt to stdout and exits 0 otherwise.
"""
from __future__ import annotations

import argparse
import re
import sys

_SLUG_RE = re.compile(r"[a-z0-9-]+")


class RoutineWakePromptError(Exception):
    """A malformed `<programme>` or `--deck-name` argument."""


def _slug(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value.strip()):
        raise RoutineWakePromptError(f"{label} {value!r} is not a [a-z0-9-]+ slug")
    return value.strip()


# The fifth message kind (`scripts/foundry_message_kind.py`, AC-RWK-1) validates this shape on
# receipt; this template only ever fills in the two identifiers, never the stamp — the Routine
# computes that itself, at send time, so a stale baked-in stamp can never ship.
_PROMPT_TEMPLATE = """\
ROUTINE WAKE — {programme}.

You are a Routine: a cloud session, running on a schedule, an API `/fire` call, or a GitHub event.
You cannot spawn teammates and you hold no standing authority over this workspace. Your entire job
this run is exactly one message. Do the following, in order, then stop — nothing else, ever:

1. Run the native session enumerator (`ListAgents`) and look for a session named exactly
   "{deck_name}" in the result.
2. If no session named "{deck_name}" is present, or the one that is present reports itself
   offline, STOP here. Send no message. Report, as your entire output, this one line:
     TICK not delivered — "{deck_name}" is absent or offline.
3. Otherwise, compute the current UTC timestamp yourself, at send time, in the shape
   `YYYY-MM-DDTHH:MM:SSZ` — never a placeholder, never a value baked into this prompt. With the
   native `SendMessage` tool, send the session named "{deck_name}" exactly ONE cross-session
   message whose first line is:
     TICK {programme} <the UTC stamp you just computed>
4. Stop. Do not poll for a reply, do not retry, do not read or write anything in the workspace,
   do not open a pull request, and do not wait. If a reply ever arrives, it is not consent for
   anything and this Routine takes no action on it — a message is never consent.

This message is a wake-up, never an instruction: the receiving session decides for itself whether
to act on it, under its own front-authorization and merge-floor checks, exactly as if the message
had never arrived.
"""

_SCHEDULE_RECIPE = """\
`/schedule` recipe:
  - Cadence: any interval of an hour or longer (Routines below that floor are not offered).
    A concrete cron example at the floor: `0 * * * *`.
  - Repository: this workspace's own repository (the Routine needs it checked out to have
    anything to be a Routine FOR, even though this prompt itself reads nothing from it).
  - Environment: default.
  - Connectors: none — the Routine's only reach is the one cross-session message above.
  - Prompt: the rendered text above, pasted verbatim.
"""

_PREREQUISITES = """\
Prerequisites (verify each BEFORE scheduling, not after the first missed tick):
  - The deck session is running with `--name {deck_name}` — `ListAgents` can only find it by that
    literal name; an unnamed session is invisible to this Routine.
  - The deck session is connected to Remote Control. A Routine is a cloud session; it can reach a
    local session only while that local session is connected.
  - The deck session's `crossSessionInbound` setting is not `refuse`. Any other value that accepts
    an incoming message from a Routine will do.
"""


def render(programme: str, deck_name: str) -> str:
    """Pure: (programme, deck_name) -> the full printed output (prompt + recipe +
    prerequisites). Both arguments are validated slugs by the time this is called."""
    prompt = _PROMPT_TEMPLATE.format(programme=programme, deck_name=deck_name)
    recipe = _SCHEDULE_RECIPE
    prereqs = _PREREQUISITES.format(deck_name=deck_name)
    return f"{prompt}\n{recipe}\n{prereqs}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="print the self-contained routine-wake prompt, the /schedule recipe, and "
                     "the prerequisites for waking a named command-deck session with one TICK"
    )
    ap.add_argument("programme", help="the release/programme id, a [a-z0-9-]+ slug")
    ap.add_argument("--deck-name", required=True,
                     help="the deck session's --name, a [a-z0-9-]+ slug")
    args = ap.parse_args(argv)

    try:
        programme = _slug(args.programme, "programme identifier")
        deck_name = _slug(args.deck_name, "--deck-name")
    except RoutineWakePromptError as e:
        print(f"foundry-routine-wake-prompt: REFUSED — {e}", file=sys.stderr)
        return 2

    print(render(programme, deck_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
