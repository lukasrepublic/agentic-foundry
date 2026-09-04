#!/usr/bin/env python3
"""foundry-command-deck-watch — the watcher lifecycle behind `/foundry:command-deck`.

`foundry_command_deck.py` answers *what is true right now* (the ready-set, the wave barrier, the
clock, the landing evidence). This module is the surface around it: render the tick prompt a
scheduled watcher fires, and remember that a watcher was armed at all.

**Why the record exists.** A scheduled tick job is SESSION-ONLY — it lives in the running session,
is written to no file, dies when that session exits, and auto-expires after 7 days regardless. So
the fact that a programme is *supposed* to be watched cannot live in the job. It lives here, on
disk, under `.foundry/watchers/<programme>.json`, and a session that starts to find a record whose
job it does not hold knows to re-arm rather than to assume someone else is driving.

The record is a NOTE, never an authority: it grants nothing, gates nothing, and is not consulted by
any derivation. `status` reports the record and the live measurement side by side precisely so a
stale record cannot be mistaken for a running watcher.

Everything here is read-only against the corpus. The only writes are the watcher record itself.

    foundry_command_deck_watch.py prompt  <programme> [--cron EXPR] [--root DIR]
    foundry_command_deck_watch.py status  <programme> [--root DIR] [--json]
    foundry_command_deck_watch.py record  <programme> --job-id ID [--cron EXPR] [--root DIR]
    foundry_command_deck_watch.py forget  <programme> [--root DIR]
    foundry_command_deck_watch.py list    [--root DIR] [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundry_command_deck as cd  # noqa: E402
import foundry_release as fr  # noqa: E402

# Off-the-hour by default: `*/20` lands every user on the same instant, and a deck that contends
# with sibling sessions for the same machine is the failure that moved a real run off it.
DEFAULT_CRON = "7,27,47 * * * *"

# The runtime auto-expires a recurring job after 7 days. Not ours to change; ours to SAY.
JOB_TTL_DAYS = 7

TEMPLATE_NAME = "tick-prompt.template.md"


class WatchError(Exception):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _slug(programme: str) -> str:
    """The same `[a-z0-9-]+` shape `load_release` enforces, applied before this module touches a
    path. Resolution is still delegated to `resolve_programme` — this only keeps a bad identifier
    out of `os.path.join` on the way there."""
    if not isinstance(programme, str) or not re.fullmatch(r"[a-z0-9-]+", programme.strip()):
        raise WatchError(f"programme identifier {programme!r} is not a [a-z0-9-]+ slug")
    return programme.strip()


# ── the watcher record ───────────────────────────────────────────────────────────────────────

def record_path(programme: str, project_dir=None) -> str:
    root = fr._project_dir(project_dir)
    return os.path.join(root, ".foundry", "watchers", f"{_slug(programme)}.json")


def read_record(programme: str, project_dir=None):
    """The record, or None when there is none. Distinguishes "no watcher recorded" from "could not
    read": an unreadable record RAISES rather than reading as absent."""
    path = record_path(programme, project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise WatchError(f"watcher record exists but could not be read: {path}: {e}")
    # Valid JSON that is not an object is still an unreadable record. Without this the value flows
    # into `.get()` downstream and raises an uncaught AttributeError — a traceback where this
    # module promises a REFUSED line, which is the same "could not look" case wearing a crash.
    if not isinstance(rec, dict):
        raise WatchError(
            f"watcher record is not a JSON object ({type(rec).__name__}): {path}"
        )
    return rec


def write_record(programme: str, job_id: str, cron: str, project_dir=None) -> dict:
    path = record_path(programme, project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    armed = _now()
    rec = {
        "programme": _slug(programme),
        "job_id": cd.as_data(job_id),
        "cron": cd.as_data(cron),
        "armed_at": _stamp(armed),
        "expires_at": _stamp(armed + dt.timedelta(days=JOB_TTL_DAYS)),
        "armed_by_session": os.environ.get("CLAUDE_SESSION_ID", ""),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return rec


def forget_record(programme: str, project_dir=None) -> bool:
    path = record_path(programme, project_dir)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def list_records(project_dir=None) -> list:
    base = os.path.join(fr._project_dir(project_dir), ".foundry", "watchers")
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        if not name.endswith(".json"):
            continue
        try:
            out.append(read_record(name[:-5], project_dir) or {})
        except WatchError as e:
            out.append({"programme": name[:-5], "unreadable": str(e)})
    return out


def record_age(rec) -> dict:
    """Whether the runtime would still be holding this job. `unknown` when the stamp will not
    parse — never silently 'fresh'."""
    expires = _parse_stamp((rec or {}).get("expires_at"))
    if expires is None:
        return {"expired": None, "remaining_hours": None}
    remaining = (expires - _now()).total_seconds() / 3600
    return {"expired": remaining <= 0, "remaining_hours": round(remaining, 1)}


# ── measurement ──────────────────────────────────────────────────────────────────────────────

def measure(programme: str, project_dir=None, branch="main") -> dict:
    """The live programme measurement plus the watcher record, side by side.

    The two are reported separately and never reconciled here: a record is a note that a watcher was
    armed, and only the session holding the job can say whether it still fires.
    """
    release = cd.resolve_programme(programme, project_dir=project_dir)
    rs = cd.ready_set(release, project_dir=project_dir, branch=branch)
    rows = fr.derive_run_state(release, project_dir=project_dir, branch=branch)

    counts = {}
    for row in rows:
        counts[row.get("state") or "UNKNOWN"] = counts.get(row.get("state") or "UNKNOWN", 0) + 1

    rec = read_record(programme, project_dir)
    return {
        "programme": release.id,
        "description": cd.as_data(release.description),
        "release_state": release.state,
        "measured_at": _stamp(_now()),
        "total": len(release.atoms),
        "counts": counts,
        "ready": rs["ready"],
        "excluded": rs["excluded"],
        "open_wave": rs["open_wave"],
        "watcher": rec,
        "watcher_age": record_age(rec) if rec else None,
    }


# ── the tick prompt ──────────────────────────────────────────────────────────────────────────

def _template_text() -> str:
    """The tick-prompt template shipped beside the skill."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "skills", "command-deck", TEMPLATE_NAME),
        os.path.join(os.environ.get("CLAUDE_PLUGIN_ROOT", ""), "skills", "command-deck", TEMPLATE_NAME),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
    raise WatchError(
        "the tick-prompt template was not found. Looked in: "
        + ", ".join(os.path.normpath(c) for c in candidates if c)
    )


def _snapshot(m: dict) -> str:
    """§5 of the prompt: a state line that WILL be stale by the next tick, and says so.

    Deliberately a snapshot rather than a live query. It carries "re-measure, do not trust this
    line" and its job is to train re-measurement — a prompt that quietly stayed current would
    instead train the agent to trust a number nothing re-derives.
    """
    counts = " · ".join(f"{k} {v}" for k, v in sorted(m["counts"].items())) or "(no rows derived)"
    lines = [
        f"{m['programme']} — {m['total']} atoms, release state {m['release_state']}.",
        f"Counts at arm time: {counts}.",
        f"Open wave: {m['open_wave'] if m['open_wave'] is not None else 'none — no unfinished wave'}.",
    ]
    if m["ready"]:
        lines.append(f"Ready to dispatch at arm time: {', '.join(m['ready'])}.")
    else:
        lines.append("Nothing was ready at arm time. Every atom's exclusion reason:")
        for aid, why in sorted(m["excluded"].items()):
            lines.append(f"  - {aid}: {why}")
    return "\n".join(lines)


def render_prompt(programme: str, project_dir=None, branch="main", cron=DEFAULT_CRON) -> str:
    root = fr._project_dir(project_dir)
    m = measure(programme, project_dir=project_dir, branch=branch)
    # The prompt renders shell the tick will run, so the workspace path is quoted for the shell.
    # A macOS home directory with a space in it otherwise renders a `cd` that silently truncates —
    # and running from the wrong directory is the exact failure §0b of the template exists to catch.
    quoted_root = shlex.quote(root)
    measurement = (
        f"python3 ${{CLAUDE_PLUGIN_ROOT}}/scripts/foundry_command_deck_watch.py status "
        f"{m['programme']} --root {quoted_root}"
    )
    subs = {
        "{{PROGRAMME_ID}}": m["programme"],
        "{{WORKSPACE_PATH}}": quoted_root,
        "{{MEASUREMENT_COMMAND}}": measurement,
        "{{MANIFEST_PATH}}": f".foundry/releases/{m['programme']}/release.yaml",
        "{{CRON}}": cron,
        "{{ARMED_AT}}": m["measured_at"],
        "{{SNAPSHOT}}": _snapshot(m),
    }
    text = _template_text()
    for key, value in subs.items():
        text = text.replace(key, value)
    return text


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────

def _render_status(m: dict) -> None:
    print(f"COMMAND DECK — {m['programme']}  [{m['release_state']}]")
    print(f"  measured    {m['measured_at']}")
    print(f"  atoms       {m['total']} total · " +
          (" · ".join(f"{k} {v}" for k, v in sorted(m["counts"].items())) or "no rows derived"))
    print(f"  open wave   {m['open_wave'] if m['open_wave'] is not None else 'none'}")
    print()
    if m["ready"]:
        print(f"  READY ({len(m['ready'])}) — dispatchable right now:")
        for aid in m["ready"]:
            print(f"    - {aid}")
    else:
        print("  READY (0) — nothing dispatchable. Why, per atom:")
        for aid, why in sorted(m["excluded"].items()):
            print(f"    - {aid}: {why}")
    print()
    rec, age = m["watcher"], m["watcher_age"]
    if rec is None:
        print("  watcher     NO RECORD — no watcher has been armed for this programme from this")
        print("              workspace, or the record was forgotten. This is not proof that no")
        print("              session is driving it; it is only the absence of a note.")
    else:
        expiry = "expiry UNKNOWN (unparseable stamp)"
        if age and age["expired"] is True:
            expiry = "EXPIRED — the runtime has dropped this job; re-arm"
        elif age and age["expired"] is False:
            expiry = f"expires in {age['remaining_hours']}h"
        print(f"  watcher     job {rec.get('job_id')} · cron {rec.get('cron')}")
        print(f"              armed {rec.get('armed_at')} · {expiry}")
        print("              A RECORD IS NOT A RUNNING WATCHER. The job is session-only: if the")
        print("              session that armed it has exited, nothing fires. Confirm with the")
        print("              scheduler's own listing before believing this line.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="command-deck watcher lifecycle")
    ap.add_argument("cmd", choices=["prompt", "status", "record", "forget", "list"])
    ap.add_argument("programme", nargs="?", default=None)
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--cron", default=DEFAULT_CRON)
    ap.add_argument("--job-id", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "list":
            recs = list_records(args.root)
            if args.json:
                print(json.dumps(recs, indent=2, sort_keys=True))
            elif not recs:
                print("no watcher records in this workspace "
                      "(.foundry/watchers/ is empty or absent)")
            else:
                for rec in recs:
                    age = record_age(rec)
                    state = ("UNREADABLE" if rec.get("unreadable") else
                             "EXPIRED" if age["expired"] else
                             f"{age['remaining_hours']}h left" if age["expired"] is False else
                             "expiry UNKNOWN")
                    print(f"  {rec.get('programme'):<40} job {rec.get('job_id','-'):<12} {state}")
            return 0

        if not args.programme:
            print("no programme given; in-flight programmes:")
            for r in cd.list_inflight(args.root):
                print(f"  {r['id']}  [{r['state']}]  {r['description'][:80]}")
            return 2

        if args.cmd == "prompt":
            print(render_prompt(args.programme, project_dir=args.root,
                                branch=args.branch, cron=args.cron))
            return 0

        if args.cmd == "status":
            m = measure(args.programme, project_dir=args.root, branch=args.branch)
            print(json.dumps(m, indent=2, sort_keys=True)) if args.json else _render_status(m)
            return 0

        if args.cmd == "record":
            if not args.job_id:
                print("command-deck: REFUSED — record needs the scheduler's --job-id. Recording a "
                      "watcher with no job id would leave a note nothing can be matched against.",
                      file=sys.stderr)
                return 2
            rec = write_record(args.programme, args.job_id, args.cron, project_dir=args.root)
            print(f"recorded watcher for {rec['programme']}: job {rec['job_id']}, "
                  f"cron {rec['cron']}, expires {rec['expires_at']}")
            return 0

        if args.cmd == "forget":
            if forget_record(args.programme, project_dir=args.root):
                print(f"forgot the watcher record for {args.programme}. The scheduled job itself "
                      f"is NOT cancelled by this — cancel it with the scheduler.")
            else:
                print(f"no watcher record for {args.programme}; nothing to forget")
            return 0

    except (WatchError, cd.CommandDeckError, fr.ReleaseError) as e:
        print(f"command-deck: REFUSED — {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
