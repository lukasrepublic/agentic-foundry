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
builders than ready clusters; either one needs a reason or the render is refused. Every item field is cleaned
(control and format characters removed, length bounded) and the retired word is masked, never a refusal.
`report.include_ids: false` moves every id to one trailing "IDs:" line.

Counting: `max_lines` counts EVERY line of the output: the first line, the four headings, the items, STALL and IDs.
An empty block is one line ("Ledger: none"). The message-log findings never make the render refuse: they are
fitted into the lines that are left (a summary and the first finding at most; with no room the summary rides on
the Needs you heading line). Only the lead's own data can overflow the limit.

Tick JSON (all keys are plain data):
  now (optional RFC 3339 UTC), progress {done,total,moved}, merged, verified (integers),
  decisions / building / needs_you / ledger (lists; an item is a string or {text, id, reason, stuck}),
  ready_clusters, running_builders (required integers: B1 compares them), stall_reason, plan (the A1 plan line;
  first line of Building now), paused, dispatched_new,
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
ITEM_MAX = 240
ID_MAX = 40
PLAN_MAX = 300
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
    text = " ".join(text.split())[:limit]      # cut FIRST: a cut can end in the word ("...bus" + "iness"), so mask after it
    return _WORD_RE.sub(MASK, text)


def _item(raw, block, index):
    """-> (text, id, reason, stuck). Every field is cleaned (control and format characters removed, bounded,
    the retired word masked). Raises Refused on a wrong shape or a stuck item without a reason."""
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        raise Refused(f"bad-item:{block}:{index}")
    text = clean(raw["text"], ITEM_MAX)
    if not text:
        raise Refused(f"bad-item:{block}:{index}")
    ident = clean(raw["id"], ID_MAX) if raw.get("id") not in (None, "") else None
    reason = clean(raw["reason"], ITEM_MAX) if isinstance(raw.get("reason"), str) else None
    stuck = block == "needs_you" or raw.get("stuck") is True
    if stuck and not reason:
        raise Refused(f"stuck-item-without-reason:{block}:{index}")
    return text, ident or None, reason or None, stuck


def _count(value, name):
    if value is None:
        raise Refused(f"missing-key:{name}")
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
    return {k: (clean(v, 200) if isinstance(v, str) else v) for k, v in data.items()
            if k in ("paused", "since", "until", "reason")}


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
_KNOWN_RULES = ("missing-corr", "after-without-before", "stop-without-before", "before-open", "malformed-prod-record",
                "sender-mismatch")


def _finding_line(f):
    """-> (text, reason) for one audit finding. Every borrowed string is cleaned."""
    rule = f.get("rule")
    corr = clean(f.get("corr", "no corr"), 40)
    if rule == "before-open":
        reason = f"the before line has had no outcome for {clean(f.get('age_minutes', '?'), 12)} minutes"
    elif rule == "malformed-prod-record":
        reason = f"a production record failed the format check: {clean(f.get('reason', 'no reason given'), 80)}"
    elif rule == "sender-mismatch":
        reason = (f"the {clean(f.get('kind', 'outcome'), 12)} line came from {clean(f.get('from', '?'), 40)} but the before line "
                  f"came from {clean(f.get('before_from', '?'), 40)}; check that one session did not close another session's step")
    else:
        reason = _AUDIT_REASONS.get(rule, "the audit reported a rule this deck does not know")
    label = clean(rule, 40) if rule in _KNOWN_RULES else "unknown-rule"
    return f"Process miss in the shared message log: {label} (corr {corr})", reason


def _malformed_unique(read, audit):
    """Malformed entries of `read` and `audit`, each distinct record once (by line text, else reason and kind)."""
    seen, out = set(), []
    for src in (read.get("malformed"), audit.get("malformed")):
        for b in src if isinstance(src, list) else []:
            if not isinstance(b, dict):
                continue
            key = (str(b.get("line")), str(b.get("reason")), str(b.get("kind")))
            if key not in seen:
                seen.add(key)
                out.append(b)
    return out


def message_log_summary(data):
    """-> (findings, malformed): `findings` is a list of (text, reason); `malformed` a de-duplicated list."""
    if not isinstance(data, dict):
        return [], []
    audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
    read = data.get("read") if isinstance(data.get("read"), dict) else {}
    raw = audit.get("findings") if isinstance(audit.get("findings"), list) else []
    return [_finding_line(f) for f in raw if isinstance(f, dict)], _malformed_unique(read, audit)


def _malformed_line(bad):
    shown = "; ".join(f"{i + 1}: {clean(b.get('reason', 'no reason'), 60)}" for i, b in enumerate(bad[:MALFORMED_CAP]))
    return (f"{len(bad)} malformed records in the shared message log (first {min(len(bad), MALFORMED_CAP)} shown as data: {shown})",
            _ml.NOTICE)


def budget_message_lines(data, room):
    """Fit the message-log findings into `room` lines under Needs you. Returns (lines, heading_suffix).
    Never more than 2 lines: a summary, then the first finding. With 1 line: the summary. With none: the
    summary rides on the heading line, so the findings are always visible and never cost a line."""
    findings, bad = message_log_summary(data)
    n, m = len(findings), len(bad)
    if not n and not m:
        return [], ""
    summary = f"{n} process findings, {m} malformed records - see audit"
    first = _finding_line_text(findings[0]) if n else _finding_line_text(_malformed_line(bad))
    if n + (1 if m else 0) == 1 and room >= 1:
        return [first], ""                       # exactly one thing to say: say it
    if room >= 2:
        return [summary, first], ""
    if room == 1:
        return [summary], ""
    return [], f" ({summary})"


def _finding_line_text(pair):
    return f"{pair[0]} -- reason: {pair[1]}"


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
    except ImportError:
        raise Refused(f"bad-timezone:{clean(name, 40)}: no time zone database is installed")
    except zoneinfo.ZoneInfoNotFoundError:
        raise Refused(f"bad-timezone:{clean(name, 40)}: the time zone database has no such zone")
    except (ValueError, OSError) as exc:
        raise Refused(f"bad-timezone:{clean(name, 40)}: {type(exc).__name__}")


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


# ---------------------------------------------------------------------------------------------
# S5 rule 7: the cost line, the cost log and the rule-8 stall check
# ---------------------------------------------------------------------------------------------
COST_LINE_RE = re.compile(r"^agents started (\d+) \| longest agent (\d+) calls \| items closed (\d+)$")
COST_LOG_RE = re.compile(r"^(\S+) (agents started (\d+) \| longest agent (\d+) calls \| items closed (\d+))( ci-pending)?$")


def cost_line_text(n, c, k):
    return f"agents started {n} | longest agent {c} calls | items closed {k}"


def _cost_from_tick(tick):
    """-> the cost line text, or raises Refused('cost-line-missing'). Accepts `cost_line` (the exact line) or
    `cost` {agents_started, longest_agent_calls, items_closed} (non-negative integers)."""
    line = tick.get("cost_line")
    if isinstance(line, str):
        if COST_LINE_RE.match(line):
            return line
        raise Refused("cost-line-missing")
    cost = tick.get("cost")
    if isinstance(cost, dict):
        vals = [cost.get(k) for k in ("agents_started", "longest_agent_calls", "items_closed")]
        if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in vals):
            return cost_line_text(*vals)
    raise Refused("cost-line-missing")


def append_cost(log_path, line, now=None, ci_pending=False):
    """Append `<UTC stamp> <cost line>[ ci-pending]` to the cost log."""
    if not COST_LINE_RE.match(line):
        raise Refused("cost-line-missing")
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {line}{' ci-pending' if ci_pending else ''}\n")


def stall_check(log_path, ticks=3):
    """Rule 8: True when the last `ticks` log lines are all counted (not ci-pending) with N > 0 and K = 0.
    A ci-pending line is not counted and breaks the run."""
    try:
        raw = open(log_path, encoding="utf-8").read().splitlines()
    except OSError:
        return False
    rows = [COST_LOG_RE.match(r) for r in raw if r.strip()]
    rows = [m for m in rows if m]
    if len(rows) < ticks:
        return False
    return all(m.group(6) is None and int(m.group(3)) > 0 and int(m.group(5)) == 0 for m in rows[-ticks:])


def render(tick, policy, flag=None):
    """-> report text. Raises Refused for the lead's own data. The message-log findings never cause a refusal:
    they are fitted into the lines that are left."""
    if not isinstance(tick, dict):
        raise Refused("bad-tick:not an object")
    errors = [f"missing-block:{key}" for key, _ in BLOCKS if not isinstance(tick.get(key), list)]
    if errors:
        raise Refused(errors)
    cost_line = _cost_from_tick(tick)
    first = _first_line(tick, policy.get("report.timezone"))
    merged = _count(tick.get("merged"), "merged")
    verified = _count(tick.get("verified"), "verified")
    ready = _count(tick.get("ready_clusters"), "ready_clusters")
    running = _count(tick.get("running_builders"), "running_builders")
    dispatched = _count(tick.get("dispatched_new", 0), "dispatched_new")
    paused = bool(tick.get("paused")) or bool(flag and flag.get("paused"))
    if paused and dispatched:
        raise Refused("paused-but-dispatched")

    ids, body = [], {}
    include_ids = bool(policy.get("report.include_ids"))
    for key, _ in BLOCKS:
        lines = []
        if key == "building":
            if paused:
                until = (flag or {}).get("until") or tick.get("paused_until")
                lines.append("- Paused: no new work started" + (f" (until: {clean(until, 120)})" if until else "")
                             + ". Running work finishes or parks.")
            if tick.get("plan"):
                lines.append("- Plan: " + clean(tick["plan"], PLAN_MAX))
        for i, raw in enumerate(tick[key], 1):
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
        body[key] = lines

    stalls = []
    if merged == 0 and verified == 0:
        stalls.append("this tick merged nothing and verified nothing")
    if not paused and running < ready:
        stalls.append(f"{_plural(running, 'builder')} running, {ready} ready")
    head = [first]
    if stalls:
        why = clean(tick.get("stall_reason") or "", ITEM_MAX)
        if not why:
            raise Refused("stall-without-reason")
        head.append("STALL: " + "; ".join(stalls) + f" -- why: {why}")
    head.append(cost_line)

    max_lines = policy.get("report.max_lines")
    tail = ["IDs: " + ", ".join(ids)] if ids else []
    base = len(head) + sum(1 if not body[k] else 1 + len(body[k]) for k, _ in BLOCKS) + len(tail)
    if base > max_lines:
        raise Refused(f"too-many-lines:{base}>max_lines={max_lines}")
    extra, suffix = budget_message_lines(tick.get("message_log"), max_lines - base)
    body["needs_you"] = body["needs_you"] + ["- " + e for e in extra]
    out = list(head)
    for key, title in BLOCKS:
        if not body[key] and not (suffix and key == "needs_you"):
            out.append(f"{title}: none")
        else:
            out.append(title + (suffix if key == "needs_you" else ""))
            out += body[key]
    out += tail
    text = "\n".join(out) + "\n"
    if _WORD_RE.search(text):          # unreachable after clean(); kept as a last check on the final text
        raise Refused(f"forbidden-word:{_RETIRED}")
    return text


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _project(arg):
    """--project-dir, else CLAUDE_PROJECT_DIR, else the working directory ONLY when it is a workspace (it has a
    `.foundry` directory). Anything else is refused: a flag read from the wrong place would say "not paused"."""
    raw = arg or os.environ.get("CLAUDE_PROJECT_DIR")
    if raw:
        return Path(raw)
    cwd = Path(os.getcwd())
    if (cwd / ".foundry").is_dir():
        return cwd
    raise Refused("no-project-dir: pass --project-dir or set CLAUDE_PROJECT_DIR (the working directory is not a workspace)")


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
    ap.add_argument("--append-cost", metavar="LOG", help="append the cost line to this cost-reports.log")
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
        tick = _read_in(args.in_path)
        text = render(tick, policy, pause_state(project))
        if args.append_cost:
            append_cost(args.append_cost, _cost_from_tick(tick))
        sys.stdout.write(text)
        return 0
    except Refused as exc:
        for e in exc.errors:
            print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
