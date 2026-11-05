#!/usr/bin/env python3
"""foundry_programme_issue -- the programme issue and the Project status update (dl-06, /foundry:command-deck).

    foundry_programme_issue.py render        <programme> (--plan <execution-plan.yaml> | --plan-ref) [options]
    foundry_programme_issue.py publish       <programme> --issue <owner/repo#N> (--plan F | --plan-ref) [options]
    foundry_programme_issue.py overrides     <programme> --issue <owner/repo#N>
    foundry_programme_issue.py status-update <programme> --project <owner/number> (--plan F | --plan-ref) [options]

Options: --states <json>   {"states": {atom: merged|in review|building|ready|blocked|held|parked},
                            "parked": [{"item", "reason"}]}  (the tick's view; absent: held / ready / blocked from the plan)
         --report <md>     the four-block report text (written by `foundry_deck_report.py --programme-issue`); it MUST end
                           with the cost line, or the render is refused (S5 rule 7)
         --existing-body <md>   the current issue body (render only; publish reads it from GitHub)
         --cost-log <log>  the deck cost log; a cost-without-progress stall there makes the status OFF_TRACK
         --dry-run         print what would be sent; send nothing

RENDER rebuilds the parent-issue body from the execution plan: a header line (done/total, open wave), the waves with
their batches and atom states, the critical path, the parked items, the latest report, and an override block of
checkboxes with hidden markers: `- [ ] <!-- hold:<atom> -->`, `- [ ] <!-- release-hold:<atom> -->`,
`- [ ] <!-- pause-wave:<n> -->`. A box the operator checked is read from the existing body and kept checked; a
regenerate never clears it (a marker that left the plan stays, checked). PUBLISH edits the issue body only when the
rendered body differs from the live one. STATUS-UPDATE posts ONE Project status update: COMPLETE (every atom merged),
OFF_TRACK (a stall is recorded), AT_RISK (the critical path has an unstarted batch and the open wave is empty), else
ON_TRACK; the body is the report text. OVERRIDES prints the checked boxes as JSON (the tick reads it before dispatch).

OUTAGE. When GitHub is unreachable, the plan file (or the local planner ref) is the only source: render from it, keep
every claim and escalation the caller holds, print `outage: rendered from cached plan`, and send nothing.

Every network call goes through the one function `gh_api`. Exit 0 ok, 2 refused (one line on stderr).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_deck_report as _dr  # noqa: E402
import foundry_execution_plan as _ep  # noqa: E402
import foundry_message_log as _fml  # noqa: E402

STATES = ("merged", "in review", "building", "ready", "blocked", "held", "parked")
STATUSES = ("ON_TRACK", "AT_RISK", "OFF_TRACK", "COMPLETE")
OUTAGE_LINE = "outage: rendered from cached plan"
MARK_RE = re.compile(r"^- \[([ xX])\] <!-- (hold|release-hold|pause-wave):([A-Za-z0-9._#/-]+) -->", re.M)
ISSUE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+)#(\d+)$")
PROJECT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)/(\d+)$")
MAX_BODY = 60000           # GitHub's issue body limit is 65536
TOTAL_RE = re.compile(r"<!-- foundry-total:(\d+) -->")
BODY_BEGIN = "<!-- foundry-programme-issue:begin -->"


class IssueError(Exception):
    pass


class Unreachable(IssueError):
    pass


# ---------------------------------------------------------------------------------------------
# GitHub: ONE network function
# ---------------------------------------------------------------------------------------------
def gh_api(args, payload=None):
    """The only network call in this file. `gh api <args>` with an argv list, a timeout and the JSON body on stdin
    (`--input -`). Returns the parsed JSON. Raises Unreachable when gh cannot reach GitHub, IssueError otherwise."""
    argv = ["gh", "api"] + list(args) + (["--input", "-"] if payload is not None else [])
    try:
        r = subprocess.run(argv, input=json.dumps(payload) if payload is not None else None,
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise Unreachable("gh api timed out")
    except OSError as e:
        raise Unreachable(f"gh cannot run: {type(e).__name__}")
    if r.returncode != 0:
        err = _fml.redact(r.stderr.strip())[:300]
        if re.search(r"could not resolve|connection|timed out|network|dial tcp|no route|EOF|50[234]", err, re.I):
            raise Unreachable(f"gh api unreachable: {err}")
        raise IssueError(f"gh api failed: {err}")
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        raise IssueError("gh api returned invalid JSON")


# ---------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------
def load_plan(programme, plan_path=None, plan_ref=False, cwd=None):
    if plan_path:
        try:
            plan = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise IssueError(f"cannot read plan {plan_path!r}: {type(e).__name__}")
    elif plan_ref:
        r = subprocess.run(["git", "show", f"refs/heads/plan/{programme}:{_ep.PLAN_FILE}"], cwd=cwd or os.getcwd(),
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise IssueError(f"plan ref plan/{programme} is not readable (fail closed: no plan, no issue)")
        plan = yaml.safe_load(r.stdout)
    else:
        raise IssueError("give --plan <file> or --plan-ref")
    if not isinstance(plan, dict):
        raise IssueError("the plan is not a mapping")
    try:
        _ep.validate(plan)
    except _ep.PlanError as e:
        raise IssueError(str(e))
    if plan.get("programme") != programme:
        raise IssueError(f"the plan is for {plan.get('programme')!r}, not {programme!r}")
    return plan


def _override_section(body):
    """The text between the FIRST `## Overrides` heading and the next heading. Markers anywhere else (parked items,
    the report fence) are text written by other sessions and are never overrides."""
    lines = (body or "").splitlines()
    try:
        start = lines.index("## Overrides") + 1
    except ValueError:
        return ""
    out = []
    for l in lines[start:]:
        if l.startswith("#"):
            break
        out.append(l)
    return "\n".join(out)


def _neutral(text):
    """Defuse hidden markers and checkbox starts in borrowed text."""
    t = str(text).replace("<!--", "&lt;!--")
    return re.sub(r"^(\s*(?:[-*+]\s*)?)\[", r"\1\\[", t, flags=re.M)


def read_overrides(body):
    """-> {"hold": [...], "release-hold": [...], "pause-wave": [...]} for the CHECKED boxes, plus "all" markers."""
    checked = {"hold": [], "release-hold": [], "pause-wave": []}
    seen = []
    for m in MARK_RE.finditer(_override_section(body)):
        seen.append((m.group(2), m.group(3)))
        if m.group(1) in "xX":
            checked[m.group(2)].append(m.group(3))
    return checked, seen


def _atoms(plan):
    return [(w["n"], b, a) for w in plan["waves"] for b in w["batches"] for a in b["atoms"]]


def prior_total(body):
    m = TOTAL_RE.search(body or "")
    return int(m.group(1)) if m else 0


def merged_extra(plan, states):
    """Atoms the caller says are merged that are no longer in the plan (the plan holds open atoms only)."""
    inplan = {a for _n, _b, a in _atoms(plan)}
    return {a for a in ((states or {}).get("merged") or []) if isinstance(a, str)} - inplan


def totals(plan, st, states=None, prior=0):
    """-> (done, total). The plan lists open atoms; merged ones come from `states.merged` and from the total the
    previous body recorded, so the count never shrinks as atoms merge."""
    extra = len(merged_extra(plan, states))
    total = max(len(st) + extra, prior)
    return total - sum(1 for s in st.values() if s != "merged"), total


def atom_states(plan, states=None, checked=None):
    """-> {atom: state}. The caller's `states` win; a missing atom is held / ready (open wave) / blocked. A checked
    `hold:` box holds the atom, a checked `release-hold:` box releases a plan hold."""
    given = dict((states or {}).get("states") or {})
    held = set(plan.get("held") or [])
    checked = checked or {}
    held |= set(checked.get("hold", []))
    held -= set(checked.get("release-hold", []))
    out = {}
    for n, _b, a in _atoms(plan):
        s = given.get(a)
        if s is not None and s not in STATES:
            raise IssueError(f"unknown state {s!r} for atom {a!r}")
        if a in held and s in (None, "ready", "blocked"):
            s = "held"
        elif s is None:
            s = "ready" if n == plan.get("open_wave") else "blocked"
        out[a] = s
    return out


def _open_wave(plan, st):
    for w in plan["waves"]:
        if any(st[a] != "merged" for b in w["batches"] for a in b["atoms"]):
            return w["n"]
    return None


def status_for(plan, st, stalled=False, done_total=None):
    """COMPLETE / OFF_TRACK / AT_RISK / ON_TRACK from the plan and the atom states."""
    done, total = done_total if done_total else (sum(1 for s in st.values() if s == "merged"), len(st))
    if total and done == total:
        return "COMPLETE"
    if stalled:
        return "OFF_TRACK"
    n = _open_wave(plan, st)
    wave_atoms = [a for w in plan["waves"] if w["n"] == n for b in w["batches"] for a in b["atoms"]]
    empty = not any(st[a] in ("ready", "building", "in review") for a in wave_atoms)
    crit = set(plan.get("critical_path") or [])
    unstarted = any(b["id"] in crit and all(st[a] in ("ready", "blocked", "held", "parked") for a in b["atoms"])
                    and not all(st[a] == "merged" for a in b["atoms"])
                    for w in plan["waves"] for b in w["batches"])
    return "AT_RISK" if (unstarted and empty) else "ON_TRACK"


# ---------------------------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------------------------
def _clean(text, limit=200):
    return _dr.clean(text, limit)


def check_report(report):
    """The report must end with the cost line (S5 rule 7); anything else is refused."""
    lines = [l for l in (report or "").splitlines() if l.strip()]
    if not lines or not _dr.COST_LINE_RE.fullmatch(lines[-1]):
        raise IssueError("report has no cost line: refusing to render (S5 rule 7)")
    return "\n".join(lines)


def render_body(plan, states=None, existing_body="", report=None):
    checked, seen = read_overrides(existing_body)
    st = atom_states(plan, states, checked)
    done, total = totals(plan, st, states, prior_total(existing_body))
    wave = _open_wave(plan, st)
    paused = {int(n) for n in checked["pause-wave"] if n.isdigit()}
    out = [f"**{plan['programme']}: {done}/{total} done, open wave {wave if wave is not None else 'none'}**", "",
           "_This body is rewritten each tick from the execution plan. Tick a box in the override block to give the "
           "deck an instruction; a ticked box is never cleared._", "", "## Waves", ""]
    for w in plan["waves"]:
        tag = " (paused)" if w["n"] in paused else ""
        out.append(f"### Wave {w['n']}{tag}")
        for b in w["batches"]:
            out.append(f"- batch `{b['id']}` (float {b['total_float']})")
            for a in b["atoms"]:
                out.append(f"  - `{a}`: {st[a]}")
        out.append("")
    out += ["## Critical path", "", " -> ".join(f"`{c}`" for c in plan.get("critical_path") or []) or "none", "",
            "## Parked", ""]
    parked = [a for a, s in st.items() if s == "parked"]
    extra = (states or {}).get("parked") or []
    if not parked and not extra:
        out.append("none")
    for a in parked:
        out.append(f"- `{a}`")
    for p in extra:
        item, why = (p.get("item"), p.get("reason")) if isinstance(p, dict) else (p, None)
        out.append("- " + _neutral(_clean(item)) + (f" -- {_neutral(_clean(why))}" if why else ""))
    out += ["", "## Overrides", ""]
    wanted = [("hold", a) for a in st] + [("release-hold", a) for a in sorted(set(plan.get("held") or []))] \
        + [("pause-wave", str(w["n"])) for w in plan["waves"]]
    for kind, name in seen:                       # a marker that left the plan stays (checked or not)
        if (kind, name) not in wanted:
            wanted.append((kind, name))
    for kind, name in wanted:
        box = "x" if name in checked[kind] else " "
        label = {"hold": f"Hold `{name}`", "release-hold": f"Release the hold on `{name}`",
                 "pause-wave": f"Pause wave {name}"}[kind]
        out.append(f"- [{box}] <!-- {kind}:{name} --> {label}")
    if report is not None:
        out += ["", "## Latest tick report", "", "```text", _neutral(check_report(report)).replace("```", "'''"), "```"]
    body = "\n".join(out) + f"\n\n<!-- foundry-total:{total} -->\n"
    if len(body) > MAX_BODY:
        raise IssueError("rendered body is over the issue size limit")
    return body


# ---------------------------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------------------------
def _issue_ref(arg):
    m = ISSUE_RE.match(arg or "")
    if not m:
        raise IssueError("--issue must be <owner>/<repo>#<number>")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_issue_body(ref):
    o, r, n = ref
    doc = gh_api([f"repos/{o}/{r}/issues/{n}"])
    return doc.get("body") or ""


def publish(ref, body, dry_run=False):
    """Edit the issue body unless it already equals `body`. -> "edited" | "unchanged" | "dry-run"."""
    o, r, n = ref
    if fetch_issue_body(ref) == body:
        return "unchanged"
    if dry_run:
        return "dry-run"
    gh_api(["-X", "PATCH", f"repos/{o}/{r}/issues/{n}"], {"body": body})
    return "edited"


_PROJECT_Q = ("query($owner:String!,$number:Int!){repositoryOwner(login:$owner){... on ProjectV2Owner{"
              "projectV2(number:$number){id}}}}")
_STATUS_M = ("mutation($input:CreateProjectV2StatusUpdateInput!){createProjectV2StatusUpdate(input:$input)"
             "{statusUpdate{id}}}")


def post_status(project, status, body, dry_run=False):
    if status not in STATUSES:
        raise IssueError(f"unknown status {status!r}")
    if dry_run:
        return "dry-run"
    owner, number = project
    q = gh_api(["graphql"], {"query": _PROJECT_Q, "variables": {"owner": owner, "number": number}})
    pid = ((((q.get("data") or {}).get("repositoryOwner") or {}).get("projectV2")) or {}).get("id")
    if not pid:
        raise IssueError(f"project {owner}/{number} not found")
    r = gh_api(["graphql"], {"query": _STATUS_M,
                             "variables": {"input": {"projectId": pid, "status": status, "body": body}}})
    ok = isinstance(r, dict) and not r.get("errors") and isinstance(r.get("data"), dict) and \
        (((r["data"].get("createProjectV2StatusUpdate") or {}).get("statusUpdate")) or {}).get("id")
    if not ok:
        raise IssueError("createProjectV2StatusUpdate failed: " + _fml.redact(json.dumps(r.get("errors") if isinstance(r, dict) else r))[:300])
    return "posted"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise IssueError(f"cannot read {path!r}")


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        raise IssueError(f"cannot read {path!r}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="foundry_programme_issue.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for name in ("render", "publish", "overrides", "status-update"):
        p = sub.add_parser(name)
        p.add_argument("programme")
        p.add_argument("--plan")
        p.add_argument("--plan-ref", action="store_true")
        p.add_argument("--states")
        p.add_argument("--report")
        p.add_argument("--existing-body")
        p.add_argument("--cost-log")
        p.add_argument("--issue")
        p.add_argument("--project")
        p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        if not _ep.PROGRAMME_RE.match(a.programme):
            raise IssueError(f"programme name {a.programme!r} is not a plain name")
        if a.verb == "overrides":
            checked, _ = read_overrides(fetch_issue_body(_issue_ref(a.issue)))
            print(json.dumps(checked, sort_keys=True))
            return 0
        plan = load_plan(a.programme, a.plan, a.plan_ref)
        states = _read_json(a.states) if a.states else None
        report = _read_text(a.report) if a.report else None
        if a.verb == "render":
            sys.stdout.write(render_body(plan, states, _read_text(a.existing_body) if a.existing_body else "", report))
            return 0
        if a.verb == "publish":
            ref = _issue_ref(a.issue)
            try:
                live = fetch_issue_body(ref)
                body = render_body(plan, states, live, report)
                print(f"{publish(ref, body, a.dry_run)}: {a.issue}")
            except Unreachable:
                sys.stdout.write(render_body(plan, states, "", report))
                print(OUTAGE_LINE)
            return 0
        m = PROJECT_RE.match(a.project or "")
        if not m:
            raise IssueError("--project must be <owner>/<number>")
        st = atom_states(plan, states)
        prior = 0
        if a.issue:
            try:
                prior = prior_total(fetch_issue_body(_issue_ref(a.issue)))
            except Unreachable:
                prior = 0
        stalled = bool(a.cost_log and _dr.stall_check(a.cost_log))
        status = status_for(plan, st, stalled, totals(plan, st, states, prior))
        text = check_report(report) if report is not None else None
        if text is None:
            raise IssueError("status-update needs --report (the four-block report with its cost line)")
        try:
            print(f"{status}: {post_status((m.group(1), int(m.group(2))), status, text, a.dry_run)}")
        except Unreachable:
            print(f"{status}: not posted")
            print(OUTAGE_LINE)
        return 0
    except (IssueError, _dr.Refused, _ep.PlanError, OSError, subprocess.TimeoutExpired, KeyError, TypeError) as e:
        msg = "; ".join(getattr(e, "errors", None) or [str(e)]) or type(e).__name__
        print(f"foundry_programme_issue: refused: {_fml.redact(msg).splitlines()[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
