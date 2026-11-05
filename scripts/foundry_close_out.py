#!/usr/bin/env python3
"""foundry_close_out -- the single final session of a programme (dl-12, /foundry:close-out).

    foundry_close_out.py run <programme> --repo <owner/name> --integration <branch> --tag <vX.Y.Z>
                         (--plan <execution-plan.yaml> | --plan-ref) --states <json> --atoms <json>
                         --milestone <number> --project <owner/number>
                         [--learned-json <file> | --learned-project <owner/number>]
                         [--done-means <yaml>] [--target-branch main] [--no-ruleset]
                         [--recheck-in SECONDS] [--dry-run]

Close-out runs once, after the last wave has merged into the integration branch. In this order:
  1. refuse unless every batch of the execution plan is merged (the unmerged batches are named);
  2. take the lease `refs/foundry/claims/close-out/<programme>` by creating the ref on the integration head
     (a second session finds the ref and is refused); the lease is dropped when the session ends;
  3. close the atom issues (closing keywords fire only on default-branch PRs);
  4. make sure the ruleset requiring `programme/acceptance` exists on the target branch;
  5. run the programme's Done means (--done-means) and publish the result as the commit status
     `programme/acceptance` on the integration head; with no --done-means it reads the status already there;
  6. confirm the milestone's open issue count is zero;
  7. run the Learned distill (dl-10, proposals only);
  8. create the DRAFT Release pinned to the integration head SHA, then post the final Project status update.
It never tags and never publishes: the operator tests, tags and publishes.

If `programme/acceptance` is red, or the milestone is not empty, NO release is created and the reason is printed.
The whole session waits at most 60 seconds on any check. A check still pending after that is recorded with the
time to re-check (JSON on stdout, `pending`), the lease is dropped and the session ends with exit 3.

--dry-run reads GitHub and writes nothing (no lease, close, ruleset, status, Release, update, Done means run).
Every network call goes through `foundry_programme_issue.gh_api` (argument list, JSON body on stdin).
Exit: 0 closed out, 2 refused (one line on stderr), 3 pending (re-check at the recorded time).
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import re
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_execution_plan as _ep  # noqa: E402
import foundry_learned_distill as _ld  # noqa: E402
import foundry_message_log as _fml  # noqa: E402
import foundry_programme_issue as _pi  # noqa: E402

CHECK = "programme/acceptance"
WAIT_MAX = 60               # seconds a whole session may wait on checks
POLL = 5
REPO_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+)$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
TAG_RE = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class Refused(Exception):
    pass


class Pending(Exception):
    def __init__(self, checks):
        super().__init__("pending: " + ", ".join(checks))
        self.checks = checks


class Clock:
    """The one place time passes; tests replace it. `left()` is what remains of the 60 s budget."""

    def __init__(self):
        self.start = time.monotonic()

    def left(self):
        return max(0.0, WAIT_MAX - (time.monotonic() - self.start))

    def sleep(self, s):
        time.sleep(s)

    def utcnow(self):
        return datetime.datetime.now(datetime.timezone.utc)


def gh(args, payload=None):
    """All GitHub traffic. Indirection so a test stubs one name."""
    return _pi.gh_api(args, payload)


def run_cmd(argv, timeout):
    """Run one Done-means command. -> exit code; raises subprocess.TimeoutExpired."""
    return subprocess.run(argv, capture_output=True, timeout=timeout).returncode


# ---------------------------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------------------------
def unmerged_batches(plan, states):
    st = _pi.atom_states(plan, states)
    out = []
    for w in plan["waves"]:
        for b in w["batches"]:
            open_atoms = [a for a in b["atoms"] if st.get(a) != "merged"]
            if open_atoms:
                out.append({"wave": w["n"], "batch": b["id"], "atoms": open_atoms})
    return out


def integration_head(repo, branch):
    doc = gh([f"repos/{repo}/git/ref/heads/{branch}"])
    sha = ((doc or {}).get("object") or {}).get("sha") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused(f"cannot read the head of {branch}")
    return sha


def lease_ref(programme):
    return f"refs/foundry/claims/close-out/{programme}"


def take_lease(repo, programme, sha):
    try:
        gh(["-X", "POST", f"repos/{repo}/git/refs"], {"ref": lease_ref(programme), "sha": sha})
    except _pi.Unreachable:
        raise
    except _pi.IssueError as e:
        if re.search(r"already exists|422", str(e), re.I):
            raise Refused(f"close-out/{programme} is held by another session (lease {lease_ref(programme)} is live)")
        raise


def drop_lease(repo, programme):
    try:
        gh(["-X", "DELETE", f"repos/{repo}/git/{lease_ref(programme)}"])
    except _pi.IssueError:
        pass


def close_atoms(repo, plan, atoms_doc, dry_run):
    """Close every atom issue that is still open. -> {"closed": [...], "already": [...], "no_issue": [...]}"""
    by_id = {a["id"]: a.get("issue") for a in (atoms_doc or {}).get("atoms", []) if isinstance(a, dict)}
    res = {"closed": [], "already": [], "no_issue": []}
    for atom in sorted({a for w in plan["waves"] for b in w["batches"] for a in b["atoms"]}):
        n = by_id.get(atom)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            res["no_issue"].append(atom)
            continue
        doc = gh([f"repos/{repo}/issues/{n}"])
        if (doc or {}).get("state") == "closed":
            res["already"].append(atom)
            continue
        if not dry_run:
            gh(["-X", "PATCH", f"repos/{repo}/issues/{n}"], {"state": "closed", "state_reason": "completed"})
        res["closed"].append(atom)
    return res


def ruleset_body(programme, target_branch):
    return {"name": f"programme-acceptance-{programme}", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": [f"refs/heads/{target_branch}"], "exclude": []}},
            "rules": [{"type": "required_status_checks",
                       "parameters": {"strict_required_status_checks_policy": False,
                                      "required_status_checks": [{"context": CHECK}]}}]}


def ensure_ruleset(repo, programme, target_branch, dry_run):
    """-> "present" | "created" | "would-create". A token that cannot create it is an external-provisioning stop."""
    body = ruleset_body(programme, target_branch)
    for rs in gh([f"repos/{repo}/rulesets?per_page=100"]) or []:
        if isinstance(rs, dict) and rs.get("name") == body["name"]:
            return "present"
    if dry_run:
        return "would-create"
    try:
        gh(["-X", "POST", f"repos/{repo}/rulesets"], body)
    except _pi.Unreachable:
        raise
    except _pi.IssueError as e:
        raise Refused(f"external-provisioning: the ruleset requiring {CHECK} cannot be created with this token ({e})")
    return "created"


def acceptance_state(repo, sha):
    """-> "success" | "failure" | "pending" | "missing" for the context `programme/acceptance` on `sha`."""
    for s in gh([f"repos/{repo}/commits/{sha}/statuses?per_page=100"]) or []:   # newest first
        if isinstance(s, dict) and s.get("context") == CHECK:
            return {"success": "success", "pending": "pending"}.get(s.get("state"), "failure")
    doc = gh([f"repos/{repo}/commits/{sha}/check-runs?check_name=programme%2Facceptance"]) or {}
    runs = [r for r in doc.get("check_runs", []) if isinstance(r, dict) and r.get("name") == CHECK]
    if runs:
        r = runs[0]
        if r.get("status") != "completed":
            return "pending"
        return "success" if r.get("conclusion") == "success" else "failure"
    return "missing"


def post_acceptance(repo, sha, state, description):
    gh(["-X", "POST", f"repos/{repo}/statuses/{sha}"],
       {"state": state, "context": CHECK, "description": description[:140]})


def run_done_means(path, clock, runner):
    """Run the Done-means commands in order. -> ("success"|"failure", description). Raises Pending on a timeout."""
    try:
        items = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise Refused(f"cannot read --done-means: {type(e).__name__}")
    if not isinstance(items, list) or not items:
        raise Refused("--done-means must be a non-empty list of {name, argv}")
    for it in items:
        ok = isinstance(it, dict) and isinstance(it.get("name"), str) and isinstance(it.get("argv"), list) and \
            it["argv"] and all(isinstance(x, str) for x in it["argv"])
        if not ok:
            raise Refused("each --done-means item needs a string name and a non-empty argv list of strings")
    for it in items:
        left = clock.left()
        if left <= 0:
            raise Pending([f"done-means:{it['name']}"])
        try:
            rc = runner(it["argv"], min(WAIT_MAX, left))
        except subprocess.TimeoutExpired:
            raise Pending([f"done-means:{it['name']}"])
        except OSError:
            return "failure", f"done-means {it['name']}: cannot run"
        if rc != 0:
            return "failure", f"done-means {it['name']} failed (exit {rc})"
    return "success", f"{len(items)} Done-means command(s) passed"


def settle_acceptance(repo, sha, clock):
    """Read the check; poll while it is pending and the 60 s budget lasts. -> state; raises Pending."""
    while True:
        state = acceptance_state(repo, sha)
        if state != "pending":
            return state
        if clock.left() < POLL:
            raise Pending([CHECK])
        clock.sleep(POLL)


def milestone_open(repo, number):
    doc = gh([f"repos/{repo}/milestones/{number}"]) or {}
    n = doc.get("open_issues")
    if not isinstance(n, int) or isinstance(n, bool):
        raise Refused(f"cannot read the open count of milestone {number}")
    return n


def learned(programme, args):
    """The Learned distill (proposals only). -> the summary line."""
    argv = ["propose", programme, "--dry-run"]
    if args.learned_json:
        argv += ["--from-json", args.learned_json]
    elif args.learned_project:
        argv += ["--project", args.learned_project]
    else:
        return "Learned distill: not run (no --learned-json or --learned-project)"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _ld.main(argv)
    if rc != 0:
        raise Refused("the Learned distill refused")
    return "Learned distill: " + (buf.getvalue().strip().splitlines() or ["0 proposals"])[0]


def draft_release(repo, tag, sha, name, body, dry_run):
    """Create (or re-point) the DRAFT Release. -> "created" | "updated" | "would-create"."""
    for r in gh([f"repos/{repo}/releases?per_page=100"]) or []:
        if isinstance(r, dict) and r.get("tag_name") == tag:
            if not r.get("draft"):
                raise Refused(f"release {tag} is already published; close-out never touches a published release")
            if dry_run:
                return "would-update"
            gh(["-X", "PATCH", f"repos/{repo}/releases/{r['id']}"],
               {"target_commitish": sha, "name": name, "body": body, "draft": True})
            return "updated"
    if dry_run:
        return "would-create"
    gh(["-X", "POST", f"repos/{repo}/releases"],
       {"tag_name": tag, "target_commitish": sha, "name": name, "body": body, "draft": True, "prerelease": False})
    return "created"


def final_status(project, programme, sha, summary, dry_run):
    body = f"Close-out of {programme}: COMPLETE at {sha}.\n\n" + summary
    return _pi.post_status(project, "COMPLETE", body, dry_run)


# ---------------------------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------------------------
def close_out(programme, args, clock=None, runner=run_cmd):
    clock = clock or Clock()
    plan = _pi.load_plan(programme, args.plan, args.plan_ref)
    states = _pi._read_json(args.states) if args.states else None
    open_batches = unmerged_batches(plan, states)
    if open_batches:
        names = ", ".join(f"{b['batch']} (wave {b['wave']})" for b in open_batches)
        raise Refused(f"batch not merged: {names}; close-out runs after the last wave has merged")
    atoms_doc = _pi._read_json(args.atoms)
    sha = integration_head(args.repo, args.integration)
    report = {"programme": programme, "sha": sha, "dry_run": bool(args.dry_run)}
    if args.dry_run:
        held = None
        try:
            gh([f"repos/{args.repo}/git/ref/{lease_ref(programme)[len('refs/'):]}"])
            held = True
        except _pi.Unreachable:
            raise
        except _pi.IssueError:
            held = False
        if held:
            raise Refused(f"close-out/{programme} is held by another session (lease {lease_ref(programme)} is live)")
    else:
        take_lease(args.repo, programme, sha)
    try:
        report["atoms"] = close_atoms(args.repo, plan, atoms_doc, args.dry_run)
        report["ruleset"] = "skipped" if args.no_ruleset else ensure_ruleset(args.repo, programme, args.target_branch,
                                                                              args.dry_run)
        if args.done_means and not args.dry_run:
            state, desc = run_done_means(args.done_means, clock, runner)
            post_acceptance(args.repo, sha, state, desc)
        state = settle_acceptance(args.repo, sha, clock)
        report["acceptance"] = state
        if state == "missing":
            raise Refused(f"{CHECK} is not published on {sha[:12]}; give --done-means or let CI publish it")
        if state != "success":
            raise Refused(f"{CHECK} is red on {sha[:12]}; no release created")
        left = milestone_open(args.repo, args.milestone)
        report["milestone_open"] = left
        if left != 0:
            raise Refused(f"milestone {args.milestone} has {left} open issue(s); no release created")
        report["learned"] = learned(programme, args)
        n_atoms = sum(len(b["atoms"]) for w in plan["waves"] for b in w["batches"])
        body = (f"Programme {programme}: {n_atoms} atoms merged into {args.integration} at {sha}.\n\n"
                f"{CHECK}: success. Milestone open count: 0.\n{report['learned']}\n\n"
                "Release notes: written by the cut-release step. Draft only: the operator tests, tags and publishes.")
        report["release"] = draft_release(args.repo, args.tag, sha, f"{programme} {args.tag}", body, args.dry_run)
        m = _pi.PROJECT_RE.match(args.project or "")
        if not m:
            raise Refused("--project must be <owner>/<number>")
        report["status_update"] = final_status((m.group(1), int(m.group(2))), programme, sha,
                                               f"{CHECK}: success. {report['learned']} Release {args.tag}: "
                                               f"{report['release']} (draft, pinned to {sha[:12]}).", args.dry_run)
        return report
    except Pending as p:
        p.report = report
        raise
    finally:
        if not args.dry_run:
            drop_lease(args.repo, programme)


def main(argv=None, clock=None, runner=run_cmd):
    ap = argparse.ArgumentParser(prog="foundry_close_out.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("run")
    p.add_argument("programme")
    p.add_argument("--repo", required=True)
    p.add_argument("--integration", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--plan")
    p.add_argument("--plan-ref", action="store_true")
    p.add_argument("--states")
    p.add_argument("--atoms", required=True)
    p.add_argument("--milestone", required=True, type=int)
    p.add_argument("--project", required=True)
    p.add_argument("--learned-json")
    p.add_argument("--learned-project")
    p.add_argument("--done-means")
    p.add_argument("--target-branch", default="main")
    p.add_argument("--no-ruleset", action="store_true")
    p.add_argument("--recheck-in", type=int, default=300)
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    clock = clock or Clock()
    try:
        if not _ep.PROGRAMME_RE.match(a.programme):
            raise Refused(f"programme name {a.programme!r} is not a plain name")
        if not REPO_RE.match(a.repo):
            raise Refused("--repo must be <owner>/<name>")
        if not BRANCH_RE.match(a.integration) or ".." in a.integration or not BRANCH_RE.match(a.target_branch):
            raise Refused("--integration and --target-branch must be plain branch names")
        if not TAG_RE.match(a.tag):
            raise Refused("--tag must look like v2.0.0")
        if a.milestone < 1:
            raise Refused("--milestone must be a milestone number")
        rep = close_out(a.programme, a, clock, runner)
        print(json.dumps(rep, sort_keys=True))
        return 0
    except Pending as p:
        at = (clock.utcnow() + datetime.timedelta(seconds=max(0, a.recheck_in))).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(json.dumps({"programme": a.programme, "pending": [{"check": c, "recheck_at": at} for c in p.checks],
                          "report": getattr(p, "report", {})}, sort_keys=True))
        return 3
    except _pi.Unreachable:
        print("foundry_close_out: refused: GitHub is unreachable; close-out needs it (nothing was changed offline)",
              file=sys.stderr)
        return 2
    except (Refused, _pi.IssueError, _ep.PlanError, OSError, subprocess.TimeoutExpired, KeyError, TypeError) as e:
        msg = str(e) or type(e).__name__
        print(f"foundry_close_out: refused: {_fml.redact(msg).splitlines()[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
