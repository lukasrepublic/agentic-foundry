#!/usr/bin/env python3
"""foundry_command_deck — the command-deck watcher's resolution layer.

Implements feat-foundry-fleet-command-deck-watcher (AC-CDW-1..12). The watcher itself is BEHAVIOUR,
carried in `skills/mode-autonomous/SKILL.md`. This module is the part of it a machine can decide, and
it exists so that the two questions the driver gets wrong are COMPUTED rather than judged:

    "which atoms may I start right now?"      -> ready_set()
    "is there anything to do this tick?"      -> is_idle()

WHY THAT MATTERS (the measurement this atom is derived from): across 134 real sessions, 37 of 52
resumes were the agent STOPPING SILENTLY after finishing work. An agent asked to judge whether a quiet
tick is really quiet gets it wrong in both directions — inventing work to look busy, or halting while
work waits. `is_idle` is therefore a predicate over observed state, not a judgement.

COMPOSES, NEVER RE-IMPLEMENTS. Three shipped derivations do the heavy lifting and this module is thin
by construction (the atom's contract DENIES the first two so that stays true):
  * `foundry_release.load_release`  — slug-only resolution with a realpath/commonpath containment
                                      check. That IS AC-CDW-2; it is not re-derived here.
  * `foundry_release.derive_run_state` — per-atom authorization (re-derived via `foundry_authz`),
                                      superseded/merged/dispatched probes, and the depends_on gate.
                                      Its authorization re-derivation IS AC-CDW-3's carrier, and
                                      because it re-derives on every call it IS AC-CDW-4's carrier.
  * `foundry-wave-plan.compute_wave_plan` — wave grouping INCLUDING declared-write-path overlap, so
                                      two atoms that touch the same tree never share a wave. AC-CDW-5.

WHAT IS ACTUALLY NEW HERE: the in-flight listing, the wave barrier applied to a ready-set, the idle
predicate, the wake-interval rule, the landing-evidence rule, the applied-vs-merged rule, and the
treat-manifest-text-as-data rule.

READ-ONLY (AC-CDW-6): every function is a pure derivation. Nothing in this module writes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import foundry_release as fr  # noqa: E402  (path set above; the shipped release derivation)

# The long fallback heartbeat floor (AC-CDW-9). Sourced from the runtime's own ScheduleWakeup
# contract: harness-tracked work re-invokes the session on completion, so polling for it is waste;
# the fallback exists only to survive work that hangs or never notifies.
FALLBACK_FLOOR_SECONDS = 1200

# Terminal per-atom states from `derive_run_state` — an atom in one of these is finished for the
# purposes of the wave barrier. Everything else counts as UNFINISHED, which is the fail-closed
# direction: an UNKNOWN atom holds its wave rather than letting the next one start over it.
_WAVE_SETTLED = frozenset({"merged", "superseded"})

# States a release is in while it is worth driving (AC-CDW-1's "in-flight"). DERIVED FROM THE SHIPPED
# vocabulary rather than restated: `fr.STATES` is ["backlog", "planned", "active", "completed"], so
# in-flight is everything that is neither un-started backlog nor finished. Written as a subtraction
# from the shipped list so a future state cannot silently fall outside this set — the first draft
# invented five state names, none of which the loader accepts, and every programme read as not-in-flight.
_INFLIGHT_RELEASE_STATES = frozenset(fr.STATES) - {"backlog", "completed"}

# AC-CDW-12: manifest text is DATA. Three controls, matching `foundry_permission_floor.py`'s
# rendered-string handling — the control/ANSI/C1 class, the zero-width/bidi class, and a length cap.
# The first draft carried only the control class while claiming parity with that surface; the regex
# was byte-identical and the COVERAGE was not, which is how a bidi override or a zero-width run would
# have rendered deceptively in the executive status the operator reads.
_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f])")
_ZW_BIDI_RE = re.compile("[\u061c\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")
_RENDER_CAP = 200


class CommandDeckError(Exception):
    """A refusal. Every raise names the input it could not resolve."""


# ───────────────────────────────────────────────────────────── AC-CDW-12: manifest text is data

def as_data(value):
    """Neutralize a manifest-derived string for rendering or forwarding. Manifest free-text reaches
    the executive status, the native Task graph (whose tools bypass PreToolUse, so nothing downstream
    inspects it) and dispatch prompts — it is adversary-shaped input in the same sense a locator's
    stdout is, and it is never instructions."""
    if value is None:
        return ""
    out = _ZW_BIDI_RE.sub("", _CTRL_RE.sub(" ", str(value)))
    return out if len(out) <= _RENDER_CAP else out[:_RENDER_CAP] + "…"


def confined(rel_path, root):
    """True when `rel_path` resolves INSIDE `root`. AC-CDW-12's path arm: a manifest field naming a
    path outside the corpus is refused rather than followed. Mirrors the containment check
    `foundry_release.load_release` already applies to a release id."""
    if not isinstance(rel_path, str) or not rel_path or os.path.isabs(rel_path):
        return False
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(real_root, rel_path))
    return os.path.commonpath([real_root, real]) == real_root


# ───────────────────────────────────────────────────────────── AC-CDW-1/-2: programme resolution

def list_inflight(project_dir=None):
    """Every in-flight programme, as `[{id, description, state}]`, sorted by id. Read-only; a release
    whose manifest is unreadable is reported with `state: "unreadable"` rather than dropped — a
    programme you cannot parse is exactly the one worth showing the operator."""
    base = os.path.join(fr._project_dir(project_dir), ".foundry", "releases")
    if not os.path.isdir(base):
        return []
    out = []
    for rid in sorted(os.listdir(base)):
        if not os.path.isfile(os.path.join(base, rid, "release.yaml")):
            continue
        try:
            rel = fr.load_release(rid, project_dir=project_dir)
        except (fr.ReleaseError, OSError) as e:
            out.append({"id": as_data(rid), "description": "", "state": "unreadable",
                        "detail": as_data(str(e))})
            continue
        if rel.state in _INFLIGHT_RELEASE_STATES:
            out.append({"id": rel.id, "description": as_data(rel.description), "state": rel.state})
    return out


def resolve_programme(identifier, project_dir=None):
    """Resolve a programme identifier to exactly one Release, or REFUSE (AC-CDW-1, AC-CDW-2).

    Resolution is BY IDENTIFIER, delegated to `fr.load_release`, which enforces the `[a-z0-9-]+`
    slug shape and a realpath/commonpath containment check against the releases dir. A caller-supplied
    path — `../`, an absolute path, or a symlink escaping the dir — is therefore refused by
    construction rather than by a check written here. That delegation IS the criterion.

    The blast radius is why this is fail-closed: `ready_set` auto-starts what resolution returns, so a
    near-miss identifier that silently resolved to a sibling programme would start a different
    programme's atoms, unattended.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise CommandDeckError("no programme identifier given")
    try:
        return fr.load_release(identifier.strip(), project_dir=project_dir)
    except fr.ReleaseError as e:
        raise CommandDeckError(f"programme {as_data(identifier)!r} did not resolve: {as_data(str(e))}")


# ───────────────────────────────────────────────────────────── AC-CDW-5: the wave barrier

def _import_wave_plan():
    """Import the hyphenated shipped wave planner. It is DENIED to this atom's scope precisely so the
    grouping (and its declared-write-path overlap analysis) is consumed, never forked."""
    path = os.path.join(HERE, "foundry-wave-plan.py")
    spec = importlib.util.spec_from_file_location("_foundry_wave_plan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wave_of(release):
    """`{atom_id: wave_index}` from the shipped planner."""
    _waves, meta = _import_wave_plan().compute_wave_plan(release)
    return {aid: m["wave"] for aid, m in meta.items()}


# ───────────────────────────────────────────────────────────── AC-CDW-3/-4/-5: the ready-set

def ready_set(release, *, project_dir=None, branch="main", run_rows=None):
    """The atoms that may be STARTED right now, and why every other atom was excluded.

    Returns `{"ready": [ids...], "excluded": {id: reason}, "open_wave": int|None}`.

    Three properties, each carried by a named criterion:

    AC-CDW-3 — an atom enters ONLY when its contract re-derives as authorized at derivation time.
      `derive_run_state` re-derives via `foundry_authz.is_authorized` on every call, and fail-safes to
      False on any error, so an absent authorization, a drifted spec/contract hash, and an unreadable
      contract all exclude. Each exclusion is reported WITH the state that caused it.

    AC-CDW-4 — membership is a function of observed state alone, never of which tick first saw the
      atom. There is no cursor, no memo, and no "already seen" set anywhere in this module; the rows
      are re-derived on every call. An atom that becomes ready on the hundredth tick is in the
      hundredth tick's ready-set exactly as it would have been in the first.

    AC-CDW-5 — the wave barrier: no atom from a later wave enters while an earlier wave still holds
      an unfinished atom. `derive_run_state`'s dependency gate only reads `depends_on`; the wave plan
      additionally separates atoms whose DECLARED WRITE PATHS overlap, which is the conflict a
      depends_on edge does not express and which unbounded fan-out would walk straight into.
    """
    rows = run_rows if run_rows is not None else fr.derive_run_state(
        release, project_dir=project_dir, branch=branch)
    by_id = {r["id"]: r for r in rows}
    waves = wave_of(release)

    # The open wave = the earliest wave still holding an unfinished atom. Nothing above it may start.
    open_wave = None
    for aid, w in sorted(waves.items(), key=lambda kv: (kv[1], kv[0])):
        row = by_id.get(aid)
        settled = bool(row) and row.get("state") in _WAVE_SETTLED
        if not settled:
            open_wave = w if open_wave is None else min(open_wave, w)

    corpus_root = fr._project_dir(project_dir)
    ready, excluded = [], {}
    for atom in release.atoms:
        # AC-CDW-12, path arm — applied HERE and not merely defined. A manifest's `spec_ref` /
        # `contract_ref` are validated upstream only as "non-empty string", and they flow into
        # `open()` and (via target_repo) into `git -C <dir>`. An absolute path or a `..` escapes the
        # corpus, because os.path.join discards the root when the second argument is absolute.
        escaping = [f for f in (atom.spec_ref, atom.contract_ref) if not confined(f, corpus_root)]
        if escaping:
            excluded[atom.id] = "manifest path escapes the corpus: " + ", ".join(as_data(f) for f in escaping)
            continue
        row = by_id.get(atom.id)
        if row is None:                                   # fail-closed: no derivation, no start
            excluded[atom.id] = "no run-state row derived"
            continue
        state = row.get("state")
        if state in _WAVE_SETTLED:
            excluded[atom.id] = state
            continue
        if not row.get("authorized"):                      # AC-CDW-3, stated positively
            excluded[atom.id] = f"{state} (not authorized)" if state else "not authorized"
            continue
        if not row.get("runnable"):
            excluded[atom.id] = row.get("blocked_reason") or state or "not runnable"
            continue
        if open_wave is not None and waves.get(atom.id, 0) > open_wave:
            excluded[atom.id] = f"wave {waves.get(atom.id)} held: wave {open_wave} unfinished"
            continue
        ready.append(atom.id)
    return {"ready": ready, "excluded": excluded, "open_wave": open_wave}


# ───────────────────────────────────────────────────────────── AC-CDW-7: task-graph regeneration

def graph_action(graph_present):
    """`"leave"` when a native Task graph exists, `"regenerate"` when it does not (AC-CDW-7).

    Presence is an INPUT rather than something read here: the Task graph is harness state and this
    module cannot see it. That is also why there is no staleness predicate — a present graph is never
    regenerated. Its transitions ARE the started-ness state, so regenerating a present graph would
    destroy the very run state AC-CDW-6 designates. "Absent or stale" was the first draft's wording
    and, with no mtime available on harness state, its only implementable reading was "always".
    """
    return "leave" if graph_present else "regenerate"


# ───────────────────────────────────────────────────────────── AC-CDW-8: idle is computed

def is_idle(ready, workers_running):
    """A tick is idle IF AND ONLY IF the ready-set is empty AND no dispatched worker is running.

    Both directions matter and both were observed failures. Reporting idle while the ready-set is
    non-empty is the silent halt (37 of 52 resumes). Treating a genuinely quiet tick as needing an
    action is how a loop invents work to look busy — and a fabricated task in a governance programme
    is worse than an idle tick.
    """
    return not ready and not workers_running


# ───────────────────────────────────────────────────────────── AC-CDW-9: the clock

def wake_seconds(*, awaiting_tracked, awaiting_external, external_interval=None):
    """Seconds until the next wake.

    Harness-tracked work re-invokes the session on completion, so the wake for it is a FALLBACK
    heartbeat — floored at `FALLBACK_FLOOR_SECONDS` — that exists only to survive work which hangs or
    never notifies. State the harness cannot observe (a CI run, a deploy, an external queue) needs an
    interval matched to how fast that state actually changes. When both are awaited the SHORTER wins,
    because the matched interval is the one carrying real information.
    """
    if awaiting_external:
        matched = int(external_interval if external_interval is not None else 300)
        if matched <= 0:
            raise CommandDeckError("external_interval must be a positive number of seconds")
        return min(matched, FALLBACK_FLOOR_SECONDS) if awaiting_tracked else matched
    return FALLBACK_FLOOR_SECONDS


# ───────────────────────────────────────────────────────────── AC-CDW-10: landing evidence

def may_land(check_conclusion):
    """True only for an AFFIRMATIVE success conclusion reported by the forge for the head commit.

    Everything else is False, and the enumeration is deliberate rather than a `!= "failure"` test:
    an absent conclusion, an EMPTY check set, `pending`, `neutral` and `skipped` are all non-evidence,
    and so is any result reported by the worker that produced the commit. The first draft constrained
    only where the evidence came FROM, which made it satisfiable by `neutral` — weaker than the
    shipped `foundry-git-discipline.sh` clause it sits beside. Unknown input convicts.
    """
    return isinstance(check_conclusion, str) and check_conclusion.strip().lower() == "success"


# ───────────────────────────────────────────────────────────── AC-CDW-11: merged is not applied

def is_complete(run_row, deploy_verdict=None, *, has_live_surface=True):
    """True only when the atom is merged AND — for an atom with a live surface — its deploy
    observation does not report the artifact stale or not rolled.

    An SCP was once coded, reviewed, gated and merged, and was still not in effect days later; the
    live policy list showed it absent. Merged and applied are different states, and an executive
    status that collapses them reports work as done that no user can observe. The verdict vocabulary
    is `foundry-deploy-status.py`'s shipped `STALE/NOT-ROLLED`, consumed rather than re-derived.
    """
    if not (run_row or {}).get("merged_on_main"):
        return False
    if not has_live_surface:
        return True
    if deploy_verdict is None:                   # unobserved is not observed-good
        return False
    return "STALE" not in str(deploy_verdict).upper() and "NOT-ROLLED" not in str(deploy_verdict).upper()


# ───────────────────────────────────────────────────────────── CLI

def _cmd_inflight(args):
    print(json.dumps(list_inflight(args.root), indent=2))
    return 0


def _cmd_ready(args):
    rel = resolve_programme(args.programme, project_dir=args.root)
    print(json.dumps(ready_set(rel, project_dir=args.root, branch=args.branch), indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="command-deck resolution (read-only)")
    ap.add_argument("cmd", choices=["inflight", "ready"])
    ap.add_argument("programme", nargs="?", default=None)
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    ap.add_argument("--branch", default="main")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "inflight":
            return _cmd_inflight(args)
        if not args.programme:
            # AC-CDW-1: no unambiguous programme => report what IS in flight, derive no ready-set.
            print("no programme given; in-flight programmes:")
            for r in list_inflight(args.root):
                print(f"  {r['id']}  [{r['state']}]  {r['description'][:80]}")
            return 2
        return _cmd_ready(args)
    except CommandDeckError as e:
        print(f"command-deck: REFUSED — {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
