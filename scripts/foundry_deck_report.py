#!/usr/bin/env python3
"""foundry_deck_report.py -- the deck's four-block report and its pause flag (gv-13; rules E1, B1, B9).

  foundry_deck_report.py [--project-dir DIR] --in <tick.json | ->      render the report
  foundry_deck_report.py [--project-dir DIR] state                      print the pause flag as JSON
  foundry_deck_report.py [--project-dir DIR] pause [--until REF] [--reason TEXT]
  foundry_deck_report.py [--project-dir DIR] resume

RENDER. Reads the structured tick data and prints exactly four blocks -- Your decisions, Building now,
Needs you, Ledger -- under a first line made of the local date and time (policy `report.timezone`) and the
progress count "X of N done, +moved". It REFUSES (exit 2, one named defect per line on stderr, nothing on
stdout) on: more lines than `report.max_lines`, a missing block, a stuck item without a reason, the retired
word for the shared message log, a bad progress count, a bad timezone, a tick that dispatched while paused.
A tick that merged nothing and verified nothing prints `STALL: <why>`, and so does a tick with fewer running
builders than ready clusters; either one needs a reason or the render is refused.
`report.include_ids: false` moves every id to one trailing "IDs:" line.

Counting: `max_lines` counts the first line and every line under a heading (items, STALL, IDs). The four
headings are structure and are not counted.

Tick JSON (all keys are plain data):
  now (optional RFC 3339 UTC), progress {done,total,moved}, merged, verified (integers),
  decisions / building / needs_you / ledger (lists; an item is a string or {text, id, reason, stuck}),
  ready_clusters, running_builders (optional integers), stall_reason, paused, dispatched_new,
  message_log {read: <output of `read`>, audit: <output of `audit`>} (optional).
A Needs you item is stuck by definition and needs a reason. A Building now item needs one when `stuck` is true.

MESSAGE LOG FINDINGS. Record text is data written by other sessions, never instructions. The renderer lists,
under Needs you: each audit finding; a PROD-AFTER or STOP whose sender differs from its PROD-BEFORE's sender;
and the malformed records (the first 20 reasons, as data, followed by the log's own notice). Raw record bodies
and raw malformed lines never reach the report. Every borrowed string is cleaned of control characters, cut to
length, and the retired word is masked, so hostile record text cannot forge a heading or make the render refuse.

PAUSE (B9). `pause` writes `.foundry/runtime/deck-pause.json`. An armed pause stays armed until `resume`, even
when the `--until` reference cannot be observed. `state` prints {"paused": bool, ...}; an unreadable flag counts
as paused. A paused tick shows "Paused: no new work started" first under Building now.

Exit 0 ok / 2 refused. Stdlib + PyYAML (through the policy loader). No network, no subprocess.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_message_log as _ml  # noqa: E402
import foundry_operating_model as _om  # noqa: E402

BLOCKS = (("decisions", "Your decisions"), ("building", "Building now"), ("needs_you", "Needs you"), ("ledger", "Ledger"))
FLAG_REL = Path(".foundry") / "runtime" / "deck-pause.json"
# The retired word for the shared message log. Built from fragments so this file never carries it whole.
_RETIRED = "b" + "us"
_WORD_RE = re.compile(r"\b" + _RETIRED + r"\b", re.IGNORECASE)
MASK = "***"
MALFORMED_CAP = 20
FINDING_CAP = 5
IN_MAX_BYTES = 1024 * 1024
_AUDIT_REASONS = {
    "missing-corr": "a production record has no corr id, so its before and after lines cannot be joined",
    "after-without-before": "the outcome line was posted but no earlier before line exists (process miss)",
    "stop-without-before": "a stop line was posted but no earlier before line exists (process miss)",
}


class Refused(Exception):
    def __init__(self, errors):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__(self.errors[0])


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def clean(value, limit=120):
    """Borrowed text -> one safe line: no control or format characters, collapsed whitespace, masked retired
    word, at most `limit` characters."""
    text = "".join(" " if c in "\r\n\t  " else c for c in str(value)
                   if c in "\r\n\t  " or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    text = " ".join(text.split())
    text = _WORD_RE.sub(MASK, text)
    return text[:limit]


def _item(raw, block, index):
    """-> (text, id, reason, stuck). Raises Refused on a wrong shape."""
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
        raise Refused(f"bad-item:{block}:{index}")
    text = " ".join(raw["text"].split())
    ident = raw.get("id")
    ident = " ".join(str(ident).split()) if ident not in (None, "") else None
    reason = raw.get("reason")
    reason = " ".join(str(reason).split()) if isinstance(reason, str) and reason.strip() else None
    stuck = block == "needs_you" or raw.get("stuck") is True
    if stuck and not reason:
        raise Refused(f"stuck-item-without-reason:{block}:{index}")
    return text, ident, reason, stuck


def _count(value, name, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Refused(f"bad-counter:{name}")
    return value


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# ---------------------------------------------------------------------------------------------
# the pause flag
# ---------------------------------------------------------------------------------------------
def _flag_path(project):
    return Path(project) / FLAG_REL


def pause_state(project):
    """-> dict with at least {"paused": bool}. An unreadable or malformed flag file is paused (fail closed)."""
    path = _flag_path(project)
    if not os.path.lexists(path):
        return {"paused": False}
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular file")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("paused"), bool):
            raise ValueError("shape")
    except (OSError, ValueError):
        return {"paused": True, "error": "flag-unreadable"}
    return data


def write_pause(project, until=None, reason=None, now=None):
    path = _flag_path(project)
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise Refused("pause-flag-path-is-a-symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    when = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"paused": True, "since": when, "until": clean(until, 200) if until else None,
            "reason": clean(reason, 200) if reason else None}
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return data


def clear_pause(project):
    path = _flag_path(project)
    if path.is_symlink():
        raise Refused("pause-flag-path-is-a-symlink")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------------------------
# message-log findings (data in, report lines out)
# ---------------------------------------------------------------------------------------------
def _sender_mismatches(records):
    before_from, out = {}, []
    for r in records:
        if not isinstance(r, dict):
            continue
        kind, corr = r.get("kind"), r.get("corr")
        if not corr or not isinstance(corr, str):
            continue
        if kind == "PROD-BEFORE":
            before_from.setdefault(corr, r.get("from"))
        elif kind in ("PROD-AFTER", "STOP") and corr in before_from and r.get("from") != before_from[corr]:
            out.append((kind, corr, r.get("from"), before_from[corr]))
    return out


def message_log_items(data):
    """-> list of {text, reason} dicts for Needs you."""
    if not isinstance(data, dict):
        return []
    items = []
    audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
    read = data.get("read") if isinstance(data.get("read"), dict) else {}
    findings = audit.get("findings") if isinstance(audit.get("findings"), list) else []
    findings = [f for f in findings if isinstance(f, dict)]
    mism = _sender_mismatches(read.get("records") if isinstance(read.get("records"), list) else [])
    rows = []
    for f in findings:
        rule = f.get("rule")
        corr = clean(f.get("corr", "no corr"), 40)
        if rule == "before-open":
            reason = f"the before line has had no outcome for {clean(f.get('age_minutes', '?'), 12)} minutes"
        elif rule == "malformed-prod-record":
            reason = f"a production record failed the format check: {clean(f.get('reason', 'no reason given'), 80)}"
        else:
            reason = _AUDIT_REASONS.get(rule, "the audit reported a rule this deck does not know")
        label = clean(rule, 40) if rule in ("missing-corr", "after-without-before", "stop-without-before",
                                             "before-open", "malformed-prod-record") else "unknown-rule"
        rows.append((f"Process miss in the shared message log: {label} (corr {corr})", reason))
    for kind, corr, frm, bfrom in mism:
        rows.append((f"A {kind} line in the shared message log came from a different sender (corr {clean(corr, 40)})",
                     f"it came from {clean(frm, 40)} but the before line came from {clean(bfrom, 40)}; "
                     "check that one session did not close another session's step"))
    shown, extra = rows[:FINDING_CAP], len(rows) - FINDING_CAP
    items += [{"text": t, "reason": r} for t, r in shown]
    if extra > 0:
        items.append({"text": f"{extra} more process misses in the shared message log are not listed",
                      "reason": "run the audit verb to see them all"})
    malformed = read.get("malformed") if isinstance(read.get("malformed"), list) else []
    audit_bad = audit.get("malformed") if isinstance(audit.get("malformed"), list) else []
    bad = [b for b in malformed + audit_bad if isinstance(b, dict)]
    if bad:
        shown_bad = "; ".join(f"{i + 1}: {clean(b.get('reason', 'no reason'), 60)}" for i, b in enumerate(bad[:MALFORMED_CAP]))
        items.append({"text": f"{len(bad)} malformed records in the shared message log (first {min(len(bad), MALFORMED_CAP)} "
                              f"shown as data: {shown_bad})",
                      "reason": _ml.NOTICE})
    return items


# ---------------------------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------------------------
def _zone(name):
    if name == "auto":
        return None
    if name == "UTC":
        return _dt.timezone.utc
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(name)
    except Exception:  # noqa: BLE001 -- unknown name or no tz database
        raise Refused(f"bad-timezone:{clean(name, 40)}")


def _first_line(tick, tzname):
    prog = tick.get("progress")
    if not isinstance(prog, dict):
        raise Refused("bad-progress")
    done, total, moved = prog.get("done"), prog.get("total"), prog.get("moved", 0)
    for v in (done, total, moved):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise Refused("bad-progress")
    if total < 1 or done > total:
        raise Refused("bad-progress")
    raw = tick.get("now")
    try:
        now = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else _dt.datetime.now(_dt.timezone.utc)
    except ValueError:
        raise Refused("bad-now")
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    zone = _zone(tzname)
    local = now.astimezone(zone) if zone is not None else now.astimezone()
    return f"{local.strftime('%Y-%m-%d %H:%M %Z').rstrip()} - {done} of {total} done, +{moved} moved"


def render(tick, policy, flag=None):
    """-> report text. Raises Refused. `flag` is the pause state of the project (None = not paused)."""
    if not isinstance(tick, dict):
        raise Refused("bad-tick:not an object")
    errors = []
    for key, _ in BLOCKS:
        if not isinstance(tick.get(key), list):
            errors.append(f"missing-block:{key}")
    if errors:
        raise Refused(errors)
    first = _first_line(tick, policy.get("report.timezone"))
    merged = _count(tick.get("merged"), "merged")
    verified = _count(tick.get("verified"), "verified")
    ready = _count(tick.get("ready_clusters"), "ready_clusters", optional=True)
    running = _count(tick.get("running_builders"), "running_builders", optional=True)
    dispatched = _count(tick.get("dispatched_new", 0), "dispatched_new")
    paused = bool(tick.get("paused")) or bool(flag and flag.get("paused"))
    if paused and dispatched:
        raise Refused("paused-but-dispatched")

    ids, body = [], {}
    include_ids = bool(policy.get("report.include_ids"))
    needs = list(tick["needs_you"]) + message_log_items(tick.get("message_log"))
    sources = {"decisions": tick["decisions"], "building": tick["building"], "needs_you": needs, "ledger": tick["ledger"]}
    for key, _ in BLOCKS:
        lines = []
        if key == "building" and paused:
            until = (flag or {}).get("until") or tick.get("paused_until")
            lines.append("- Paused: no new work started" + (f" (until: {clean(until, 120)})" if until else "")
                         + ". Running work finishes or parks.")
        for i, raw in enumerate(sources[key], 1):
            text, ident, reason, stuck = _item(raw, key, i)
            line = text
            if reason:
                line += f" -- reason: {reason}"
            if ident:
                if include_ids:
                    line += f" ({ident})"
                elif ident not in ids:
                    ids.append(ident)
            lines.append("- " + line)
        body[key] = lines or ["- none"]

    stalls = []
    if merged == 0 and verified == 0:
        stalls.append("this tick merged nothing and verified nothing")
    if ready is not None and running is not None and not paused and running < ready:
        stalls.append(f"{_plural(running, 'builder')} running, {ready} ready")
    head = [first]
    if stalls:
        why = tick.get("stall_reason")
        if not isinstance(why, str) or not why.strip():
            raise Refused("stall-without-reason")
        head.append("STALL: " + "; ".join(stalls) + f" -- why: {' '.join(why.split())}")
    out = list(head)
    for key, title in BLOCKS:
        out.append(title)
        out += body[key]
    if ids:
        out.append("IDs: " + ", ".join(ids))
    counted = [ln for ln in out if ln not in {t for _, t in BLOCKS}]
    max_lines = policy.get("report.max_lines")
    if len(counted) > max_lines:
        raise Refused(f"too-many-lines:{len(counted)}>max_lines={max_lines}")
    text = "\n".join(out) + "\n"
    if _WORD_RE.search(text):
        raise Refused(f"forbidden-word:{_RETIRED}")
    return text


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _project(arg):
    return Path(arg or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _read_in(path):
    try:
        if path == "-":
            raw = sys.stdin.read(IN_MAX_BYTES + 1)
        else:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read(IN_MAX_BYTES + 1)
        if len(raw) > IN_MAX_BYTES:
            raise ValueError("too large")
        return json.loads(raw)
    except (OSError, ValueError):
        raise Refused("unreadable-input")


def _parser():
    ap = argparse.ArgumentParser(prog="foundry_deck_report.py", description="render the four-block deck report")
    ap.add_argument("--project-dir")
    ap.add_argument("--in", dest="in_path", help="tick JSON file, or - for stdin")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verb_idx = next((i for i, a in enumerate(argv) if a in ("pause", "resume", "state")), None)
    try:
        if verb_idx is not None and "--in" not in argv:
            ap = argparse.ArgumentParser(prog="foundry_deck_report.py")
            ap.add_argument("--project-dir")
            sub = ap.add_subparsers(dest="verb", required=True)
            p = sub.add_parser("pause")
            p.add_argument("--until")
            p.add_argument("--reason")
            sub.add_parser("resume")
            sub.add_parser("state")
            args = ap.parse_args(argv)
            project = _project(args.project_dir)
            if args.verb == "pause":
                print(json.dumps(write_pause(project, args.until, args.reason), indent=2, sort_keys=True))
            elif args.verb == "resume":
                clear_pause(project)
                print(json.dumps({"paused": False}))
            else:
                print(json.dumps(pause_state(project), indent=2, sort_keys=True))
            return 0
        args = _parser().parse_args(argv)
        if not args.in_path:
            raise Refused("usage: --in <tick.json | -> or a verb (pause, resume, state)")
        project = _project(args.project_dir)
        try:
            policy = _om.load(str(project))
        except _om.PolicyError as exc:
            raise Refused([f"policy:{e}" for e in exc.errors])
        text = render(_read_in(args.in_path), policy, pause_state(project))
        sys.stdout.write(text)
        return 0
    except Refused as exc:
        for e in exc.errors:
            print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
