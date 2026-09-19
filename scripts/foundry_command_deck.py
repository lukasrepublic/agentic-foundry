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
predicate, the wake-interval rule, the landing-evidence rule, the applied-vs-merged rule, the
treat-manifest-text-as-data rule, and (feat wave-learn) the one write this module makes.

READ-ONLY (AC-CDW-6), WITH ONE NAMED EXCEPTION: every function through `may_land`/`is_complete`
above is a pure derivation over the corpus. `write_wave_state` (feat wave-learn, AC-WVL-1) is the
sole write this module performs — it is what lets "next waves learn from previous ones" (operator
decision `.foundry/decisions/2026-09-18-spec-is-a-living-document.md`) happen without a human
copying a transcript. It writes exactly one file, `.foundry/releases/<id>/state.yaml`, only at wave
close (every atom in a terminal state per `derive_run_state`, unless `force=True`), and only after
merging with + revalidating against whatever is already there — a write can add, it can never drop.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile

import yaml

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
    try:
        real_root = os.path.realpath(root)
        real = os.path.realpath(os.path.join(real_root, rel_path))
        return os.path.commonpath([real_root, real]) == real_root
    except (OSError, ValueError):
        # e.g. an embedded NUL byte, which makes os.lstat raise. Unresolvable is not confined.
        return False


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
    corpus_root = fr._project_dir(project_dir)

    # AC-CDW-12, path arm — CONFINE FIRST, then derive. A manifest's `spec_ref`/`contract_ref` are
    # validated upstream only as "non-empty string" and flow into `open()` and, via the contract's
    # `target_repo`, into `git -C <dir>`. Confining AFTER the derivation would refuse the atom only
    # once those reads and that subprocess had already happened with attacker-named paths — the check
    # has to precede the reach, not merely precede the start.
    #
    # (release-loader-vocabulary, AC-RLV-3): a charter-lane atom carries `charter_ref` instead of
    # spec_ref/contract_ref (both None) — `charter_ref` is the ONE manifest-supplied path that atom
    # feeds into `open()`/`git log --` (see `foundry_release._charter_authorized`), so it gets
    # exactly the same CONFINE-FIRST treatment; a factory-lane atom's check is unchanged.
    escaped = {}
    safe_atoms = []
    for atom in release.atoms:
        refs_to_check = [atom.charter_ref] if atom.charter_ref else \
            [f for f in (atom.spec_ref, atom.contract_ref) if f is not None]
        bad = [f for f in refs_to_check if not confined(f, corpus_root)]
        if bad:
            escaped[atom.id] = "manifest path escapes the corpus: " + ", ".join(as_data(f) for f in bad)
        else:
            safe_atoms.append(atom)

    if run_rows is not None:
        rows = run_rows
    elif escaped:
        # Derive over a release carrying ONLY the confined atoms, so no escaping path is ever handed
        # to the shipped probes. Dependency edges to an excluded atom stay unmet, which is correct:
        # an atom whose dependency was refused must not become runnable.
        pruned = fr.Release(release.id, release.description, release.state, safe_atoms,
                            [aid for aid in release.order if aid not in escaped])
        rows = fr.derive_run_state(pruned, project_dir=project_dir, branch=branch)
    else:
        rows = fr.derive_run_state(release, project_dir=project_dir, branch=branch)
    by_id = {r["id"]: r for r in rows}
    waves = wave_of(release)

    # The open wave = the earliest wave still holding an unfinished atom. Nothing above it may start.
    open_wave = None
    for aid, w in sorted(waves.items(), key=lambda kv: (kv[1], kv[0])):
        row = by_id.get(aid)
        settled = bool(row) and row.get("state") in _WAVE_SETTLED
        if not settled:
            open_wave = w if open_wave is None else min(open_wave, w)

    ready, excluded = [], dict(escaped)
    for atom in release.atoms:
        if atom.id in escaped:
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


# ───────────────────────────────────────────────────────────── feat wave-learn: the wave-state file

# Exactly the four top-level keys AC-WVL-1 names, and no others (`additionalProperties: false` at
# the top level, per `schema/wave-state.schema.json`). Deliberately closed: this is the one place
# per-atom status could leak back in, which the charter's "Out of scope" line forbids.
_WAVE_STATE_KEYS = ("decisions", "artifacts", "open_risks", "amendments_needed")


def _default_wave_state():
    return {k: [] for k in _WAVE_STATE_KEYS}


def _wave_schema_path():
    return os.path.join(HERE, "..", "schema", "wave-state.schema.json")


def _wave_jsonschema_check(doc):
    """Opportunistic structural validation against the pinned JSON Schema, mirroring
    `foundry_contract._jsonschema_check`. Returns [] when `jsonschema` is unavailable — the
    hand-rolled checks in `validate_wave_state` below still enforce the same shape regardless."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    try:
        with open(_wave_schema_path(), encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as e:  # type: ignore
        return [f"schema: {e.message} (at {'/'.join(str(p) for p in e.absolute_path)})"]
    except (OSError, json.JSONDecodeError) as e:  # pragma: no cover
        return [f"schema: could not validate ({e})"]
    return []


def validate_wave_state(doc):
    """Every reason `doc` is not a valid wave-state document, or `[]` when it is (AC-WVL-2).

    Hand-rolled first (so this holds even where `jsonschema` is not installed — the same
    optional-dependency posture `foundry_contract.py` uses), then the pinned schema on top."""
    if not isinstance(doc, dict):
        return [f"wave state must be a mapping, got {type(doc).__name__}"]
    errors = []
    extra = sorted(set(doc.keys()) - set(_WAVE_STATE_KEYS))
    if extra:
        errors.append(f"unknown top-level key(s) {extra} (allowed: {list(_WAVE_STATE_KEYS)})")
    for key in _WAVE_STATE_KEYS:
        if key not in doc:
            continue
        val = doc[key]
        if not isinstance(val, list):
            errors.append(f"{key!r} must be a list, got {type(val).__name__}")
            continue
        if key == "artifacts":
            for i, item in enumerate(val):
                if not isinstance(item, dict):
                    errors.append(f"artifacts[{i}] must be a mapping, got {type(item).__name__}")
                    continue
                missing = [f for f in ("path", "reuse_as") if f not in item]
                if missing:
                    errors.append(f"artifacts[{i}] missing required field(s) {missing}")
                    continue
                for f in ("path", "reuse_as"):
                    if not isinstance(item[f], str) or not item[f].strip():
                        errors.append(f"artifacts[{i}].{f} must be a non-empty string")
        else:
            for i, item in enumerate(val):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"{key}[{i}] must be a non-empty string")
    errors += _wave_jsonschema_check(doc)
    return errors


def merge_wave_state(existing, new):
    """Merge `new` entries into `existing`, per key, preserving order and NEVER dropping an
    existing entry. Exact-duplicate items (a repeated decision string, or an artifact with the same
    `path`+`reuse_as`) are not appended twice; everything else new is appended after what is there."""
    existing = existing or _default_wave_state()
    new = new or {}
    merged = {}
    for key in _WAVE_STATE_KEYS:
        kept = list(existing.get(key) or [])
        added = list(new.get(key) or [])
        merged_list = list(kept)
        if key == "artifacts":
            seen = {(a.get("path"), a.get("reuse_as")) for a in kept if isinstance(a, dict)}
            for item in added:
                marker = (item.get("path"), item.get("reuse_as")) if isinstance(item, dict) else None
                if marker is None or marker not in seen:
                    merged_list.append(item)
                    if marker is not None:
                        seen.add(marker)
        else:
            seen = set(kept)
            for item in added:
                if item not in seen:
                    merged_list.append(item)
                    seen.add(item)
        merged[key] = merged_list
    return merged


def wave_state_path(release_id, project_dir=None):
    """`.foundry/releases/<release_id>/state.yaml`. `release_id` is re-checked against the same
    `[a-z0-9-]+` slug shape `foundry_release.load_release` enforces — defense in depth against a
    caller passing something path-hostile straight to `os.path.join`."""
    if not isinstance(release_id, str) or not re.fullmatch(r"[a-z0-9-]+", release_id.strip() or ""):
        raise CommandDeckError(f"release id {release_id!r} is not a [a-z0-9-]+ slug")
    root = fr._project_dir(project_dir)
    return os.path.join(root, ".foundry", "releases", release_id.strip(), "state.yaml")


def load_wave_state(release_id, project_dir=None):
    """The release's `state.yaml`, validated — or `None` when there is none (AC-WVL-4: absence is
    reported, never fabricated). An existing-but-invalid file RAISES rather than reading as absent,
    same posture as `foundry_command_deck_watch.read_record`."""
    path = wave_state_path(release_id, project_dir=project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:
        raise CommandDeckError(f"wave state exists but could not be read: {path}: {e}")
    errors = validate_wave_state(doc)
    if errors:
        raise CommandDeckError(f"wave state at {path} failed validation: " + "; ".join(errors))
    return doc


def release_completed(rows):
    """True only when every row `derive_run_state` produced is terminal (AC-WVL-1's "reach
    `completed`"). Reuses `_WAVE_SETTLED` — the same terminal set the wave barrier already treats as
    finished — rather than inventing a second vocabulary for "done". No rows at all is NOT
    completed: nothing has been observed yet."""
    return bool(rows) and all((r or {}).get("state") in _WAVE_SETTLED for r in rows)


def write_wave_state(release, new_entries, *, project_dir=None, branch="main", force=False, run_rows=None):
    """Write `.foundry/releases/<release.id>/state.yaml` (AC-WVL-1). Merges `new_entries` into
    whatever is already there and NEVER drops an existing entry; refuses (raises
    `CommandDeckError`) rather than writing when the release has not reached `completed` (unless
    `force=True`), when `new_entries` does not validate, or when the merged document would not.

    `run_rows` overrides `derive_run_state` for tests, the same pattern `ready_set` already uses —
    a synthetic completed release needs no real contracts/authorization on disk to exercise this.
    """
    if not force:
        rows = run_rows if run_rows is not None else fr.derive_run_state(
            release, project_dir=project_dir, branch=branch)
        if not release_completed(rows):
            unfinished = [r.get("id") for r in (rows or []) if r.get("state") not in _WAVE_SETTLED]
            raise CommandDeckError(
                f"release {release.id!r} has not reached completed — unfinished: "
                f"{unfinished or 'no run-state rows derived'} (pass force=True to override)")

    entry_errors = validate_wave_state(new_entries if isinstance(new_entries, dict) else {})
    if entry_errors:
        raise CommandDeckError("new wave-state entries failed validation: " + "; ".join(entry_errors))

    existing = load_wave_state(release.id, project_dir=project_dir) or _default_wave_state()
    merged = merge_wave_state(existing, new_entries)

    merge_errors = validate_wave_state(merged)
    if merge_errors:  # pragma: no cover — defensive; inputs above are already individually valid
        raise CommandDeckError("merged wave state failed validation: " + "; ".join(merge_errors))

    path = wave_state_path(release.id, project_dir=project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Atomic write (same shape as `foundry_release.save_release`): serialize to a sibling temp
    # file in the SAME directory, fsync it, then `os.replace()` into place. `os.replace` is a
    # single filesystem rename — a crash mid-`yaml.safe_dump`, or the process being killed before
    # the rename, leaves only the temp file partial/absent and `state.yaml` untouched. Writing
    # straight into `state.yaml` would instead truncate it first, so an interrupted write could
    # lose the very entries the merge above exists to preserve.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(merged, fh, sort_keys=False, default_flow_style=False, allow_unicode=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return merged


# ───────────────────────────────────────────────────────────── CLI

def _cmd_inflight(args):
    print(json.dumps(list_inflight(args.root), indent=2))
    return 0


def _cmd_ready(args):
    rel = resolve_programme(args.programme, project_dir=args.root)
    print(json.dumps(ready_set(rel, project_dir=args.root, branch=args.branch), indent=2))
    return 0


def _cmd_write_state(args):
    """feat wave-learn (AC-WVL-1): write/merge `.foundry/releases/<programme>/state.yaml` at wave
    close. `--entries-json`/`--entries-file` name the NEW decisions/artifacts/open_risks/
    amendments_needed to merge in; whatever is already on disk is kept."""
    rel = resolve_programme(args.programme, project_dir=args.root)
    try:
        if args.entries_file:
            with open(args.entries_file, encoding="utf-8") as fh:
                entries = json.load(fh)
        elif args.entries_json:
            entries = json.loads(args.entries_json)
        else:
            raise CommandDeckError(
                "write-state needs --entries-json or --entries-file naming the new "
                "decisions/artifacts/open_risks/amendments_needed to merge in")
    except (OSError, json.JSONDecodeError) as e:
        raise CommandDeckError(f"could not read --entries-json/--entries-file: {e}")
    merged = write_wave_state(rel, entries, project_dir=args.root, branch=args.branch, force=args.force)
    print(json.dumps(merged, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="command-deck resolution + the wave-close write")
    ap.add_argument("cmd", choices=["inflight", "ready", "write-state"])
    ap.add_argument("programme", nargs="?", default=None)
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--entries-json", default=None,
                     help="write-state: a JSON object with any of the four wave-state keys")
    ap.add_argument("--entries-file", default=None,
                     help="write-state: a path to a JSON file, same shape as --entries-json")
    ap.add_argument("--force", action="store_true",
                     help="write-state: write even when the release has not reached completed")
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
        if args.cmd == "write-state":
            return _cmd_write_state(args)
        return _cmd_ready(args)
    except CommandDeckError as e:
        print(f"command-deck: REFUSED — {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
