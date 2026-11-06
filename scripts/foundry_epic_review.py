#!/usr/bin/env python3
"""foundry_epic_review -- the single final session of an epic (dl-12, /foundry:epic-review).

    foundry_epic_review.py run <epic> --repo <owner/name> --integration <branch> --tag <vX.Y.Z>
                         (--plan <release-plan.yaml> | --plan-ref) --states <json> --atoms <json>
                         --milestone <number> --project <owner/number>
                         [--learned-json <file> | --learned-project <owner/number>]
                         [--done-means <committed yaml> | --epic-issue <o/r#n> [--trust-author]]
                         [--done-means-timeout 1800] [--run-dir .] [--target-branch main] [--no-ruleset]
                         [--recheck-in SECONDS] [--dry-run]

Epic review runs once, after the last increment has merged into the integration branch. In this order:
  1. refuse unless every batch of the release plan is merged (the unmerged batches are named);
  2. take the lease `refs/foundry/claims/epic-review/<epic>` by creating the ref on the integration head
     (a second session finds the ref and is refused); the lease is dropped when the session ends;
  3. close the atom issues (closing keywords fire only on default-branch PRs);
  4. run the epic's Done means (--epic-issue, whose Done means must pass the trust check, or a COMMITTED
     --done-means file) in a work tree at the integration head, each under --done-means-timeout, and publish the
     result as the commit status `epic/acceptance` on the integration head; without either it reads the status
     already there;
  5. confirm the milestone's open issue count is zero;
  6. make sure the ruleset requiring `epic/acceptance` exists on the target branch (only now, so a refused or
     pending run leaves no ruleset behind; the report prints the delete command);
  7. run the Learned distill (dl-10, proposals only);
  8. create the DRAFT Release pinned to the integration head SHA, then post the final Project status update.
It never tags and never publishes: the operator tests, tags and publishes.

If `epic/acceptance` is red, or the milestone is not empty, NO release is created and the reason is printed.
The whole session waits at most 60 seconds on any check. A check still pending after that is recorded with the
time to re-check (JSON on stdout, `pending`), the lease is dropped and the session ends with exit 3.

--dry-run reads GitHub and writes nothing (no lease, close, ruleset, status, Release, update, Done means run).
Every network call goes through `foundry_epic_issue.gh_api` (argument list, JSON body on stdin).
Exit: 0 closed out, 2 refused (one line on stderr), 3 pending (re-check at the recorded time).
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import io
import json
import re
import shlex
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_release_plan as _ep  # noqa: E402
import foundry_retrospective as _ld  # noqa: E402
import foundry_message_log as _fml  # noqa: E402
import foundry_epic_issue as _pi  # noqa: E402

CHECK = "epic/acceptance"
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


def run_cmd(argv, timeout, cwd=None):
    """Run one Done-means command (argv list, no shell). -> exit code; raises subprocess.TimeoutExpired."""
    return subprocess.run(argv, capture_output=True, timeout=timeout, cwd=cwd).returncode


def git_head(run_dir):
    """The commit the run directory is at."""
    r = subprocess.run(["git", "-C", str(run_dir), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise Refused(f"{run_dir} is not a git work tree")
    return r.stdout.strip()


def read_epic_issue(ref, trust_author):
    """The trust-checked view of the epic issue (foundry-ticket read_issue): done_trusted is true only when the
    issue author and every body editor pass the Done-means trust check. -> the record dict."""
    spec = importlib.util.spec_from_file_location("foundry_ticket", HERE / "foundry-ticket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    o, r, n = _pi._issue_ref(ref)
    try:
        return mod.read_issue(mod.default_run, f"{o}/{r}", n, trust_author=trust_author)
    except mod.GhError as e:
        raise Refused(f"cannot read the epic issue: {e}")


# ---------------------------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------------------------
def unmerged_batches(plan, states):
    st = _pi.atom_states(plan, states)
    out = []
    for w in plan["increments"]:
        for b in w["batches"]:
            open_atoms = [a for a in b["atoms"] if st.get(a) != "merged"]
            if open_atoms:
                out.append({"increment": w["n"], "batch": b["id"], "atoms": open_atoms})
    return out


def integration_head(repo, branch):
    doc = gh([f"repos/{repo}/git/ref/heads/{branch}"])
    sha = ((doc or {}).get("object") or {}).get("sha") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused(f"cannot read the head of {branch}")
    return sha


def lease_ref(epic):
    return f"refs/foundry/claims/epic-review/{epic}"


def take_lease(repo, epic, sha):
    try:
        gh(["-X", "POST", f"repos/{repo}/git/refs"], {"ref": lease_ref(epic), "sha": sha})
    except _pi.Unreachable:
        raise
    except _pi.IssueError as e:
        if re.search(r"already exists|422", str(e), re.I):
            raise Refused(f"epic-review/{epic} is held by another session (lease {lease_ref(epic)} is live)")
        raise


def drop_lease(repo, epic):
    try:
        gh(["-X", "DELETE", f"repos/{repo}/git/{lease_ref(epic)}"])
    except _pi.IssueError:
        pass


def close_atoms(repo, plan, atoms_doc, dry_run):
    """Close every atom issue that is still open. -> {"closed": [...], "already": [...], "no_issue": [...]}"""
    by_id = {a["id"]: a.get("issue") for a in (atoms_doc or {}).get("atoms", []) if isinstance(a, dict)}
    res = {"closed": [], "already": [], "no_issue": []}
    for atom in sorted({a for w in plan["increments"] for b in w["batches"] for a in b["atoms"]}):
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


def ruleset_body(epic, target_branch):
    return {"name": f"epic-acceptance-{epic}", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": [f"refs/heads/{target_branch}"], "exclude": []}},
            "rules": [{"type": "required_status_checks",
                       "parameters": {"strict_required_status_checks_policy": False,
                                      "required_status_checks": [{"context": CHECK}]}}]}


def ensure_ruleset(repo, epic, target_branch, dry_run):
    """-> (state, id): "present" | "updated" | "created" | "would-create" | "would-update". Enforcement, conditions
    and rules are compared, not only the name. A token that cannot write it is an external-provisioning stop."""
    body = ruleset_body(epic, target_branch)
    for rs in gh([f"repos/{repo}/rulesets?per_page=100"]) or []:
        if isinstance(rs, dict) and rs.get("name") == body["name"]:
            full = gh([f"repos/{repo}/rulesets/{rs.get('id')}"]) or {}
            same = all(full.get(k) == body[k] for k in ("target", "enforcement", "conditions", "rules"))
            if same:
                return "present", rs.get("id")
            if dry_run:
                return "would-update", rs.get("id")
            try:
                gh(["-X", "PUT", f"repos/{repo}/rulesets/{rs.get('id')}"], body)
            except _pi.Unreachable:
                raise
            except _pi.IssueError as e:
                raise Refused(f"external-provisioning: the ruleset cannot be updated with this token ({e})")
            return "updated", rs.get("id")
    if dry_run:
        return "would-create", None
    try:
        made = gh(["-X", "POST", f"repos/{repo}/rulesets"], body)
    except _pi.Unreachable:
        raise
    except _pi.IssueError as e:
        raise Refused(f"external-provisioning: the ruleset requiring {CHECK} cannot be created with this token ({e})")
    return "created", (made or {}).get("id") if isinstance(made, dict) else None


def acceptance_state(repo, sha):
    """-> "success" | "failure" | "pending" | "missing" for the context `epic/acceptance` on `sha`."""
    for s in gh([f"repos/{repo}/commits/{sha}/statuses?per_page=100"]) or []:   # newest first
        if isinstance(s, dict) and s.get("context") == CHECK:
            return {"success": "success", "pending": "pending"}.get(s.get("state"), "failure")
    doc = gh([f"repos/{repo}/commits/{sha}/check-runs?check_name=epic%2Facceptance"]) or {}
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


def committed_file(path, run_dir):
    """A local --done-means file is accepted only when it is a committed path of the repo, unmodified."""
    def g(*a):
        return subprocess.run(["git", "-C", str(run_dir), *a], capture_output=True, text=True, timeout=30)
    if g("ls-files", "--error-unmatch", "--", str(path)).returncode != 0 or \
            g("diff", "--quiet", "HEAD", "--", str(path)).returncode != 0:
        raise Refused(f"done-means-untrusted: {path} is not a committed, unmodified file of the repo")
    return Path(run_dir) / path


def load_done_means(args):
    """-> list of {name, argv} or None. Sources: a committed repo file, or the trust-checked epic issue."""
    if args.done_means:
        run_dir = args.run_dir
        path = committed_file(args.done_means, run_dir)
        try:
            items = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise Refused(f"cannot read --done-means: {type(e).__name__}")
        if not isinstance(items, list) or not items:
            raise Refused("--done-means must be a non-empty list of {name, argv}")
        for it in items:
            ok = isinstance(it, dict) and isinstance(it.get("name"), str) and isinstance(it.get("argv"), list) and \
                it["argv"] and all(isinstance(x, str) for x in it["argv"])
            if not ok:
                raise Refused("each --done-means item needs a string name and a non-empty argv list of strings")
        return items
    if not args.epic_issue:
        return None
    rec = read_epic_issue(args.epic_issue, args.trust_author)
    if not rec.get("done_trusted"):
        raise Refused(f"done-means-untrusted: the epic issue's Done means is not trusted ({rec.get('done_reason')}); "
                      "the operator reads it and passes --trust-author")
    lines = [ln.strip() for ln in (rec.get("done") or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return [{"name": f"done-means-{i}", "argv": shlex.split(ln)} for i, ln in enumerate(lines, 1)]
    except ValueError:
        raise Refused("the epic issue's Done means is not valid shell-word syntax")


def run_done_means(items, args, runner):
    """Run the Done-means commands in order, each under --done-means-timeout (running the epic's own commands is
    not waiting on a check, so it is not charged to the 60 s budget). -> ("success"|"failure", description)."""
    for it in items:
        try:
            rc = runner(it["argv"], args.done_means_timeout, args.run_dir)
        except subprocess.TimeoutExpired:
            return "failure", f"done-means {it['name']} timed out after {args.done_means_timeout} s"
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


def learned(epic, args):
    """The Learned distill (proposals only). -> the summary line."""
    argv = ["propose", epic, "--dry-run"]
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
                raise Refused(f"release {tag} is already published; epic review never touches a published release")
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


def final_status(project, epic, sha, summary, dry_run):
    body = f"Epic review of {epic}: COMPLETE at {sha}.\n\n" + summary
    return _pi.post_status(project, "COMPLETE", body, dry_run)


# ---------------------------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------------------------
def epic_review(epic, args, clock=None, runner=run_cmd):
    clock = clock or Clock()
    plan = _pi.load_plan(epic, args.plan, args.plan_ref)
    states = _pi._read_json(args.states) if args.states else None
    open_batches = unmerged_batches(plan, states)
    if open_batches:
        names = ", ".join(f"{b['batch']} (increment {b['increment']})" for b in open_batches)
        raise Refused(f"batch not merged: {names}; epic review runs after the last increment has merged")
    atoms_doc = _pi._read_json(args.atoms)
    items = load_done_means(args)          # trust is decided before any lease or write
    sha = integration_head(args.repo, args.integration)
    report = {"epic": epic, "sha": sha, "dry_run": bool(args.dry_run)}
    if args.dry_run:
        held = None
        try:
            gh([f"repos/{args.repo}/git/ref/{lease_ref(epic)[len('refs/'):]}"])
            held = True
        except _pi.Unreachable:
            raise
        except _pi.IssueError:
            held = False
        if held:
            raise Refused(f"epic-review/{epic} is held by another session (lease {lease_ref(epic)} is live)")
    else:
        take_lease(args.repo, epic, sha)
    try:
        report["atoms"] = close_atoms(args.repo, plan, atoms_doc, args.dry_run)
        if items is not None:
            report["done_means_plan"] = [it["argv"] for it in items]
        if items is not None and not args.dry_run:
            if git_head(args.run_dir) != sha:
                raise Refused(f"the run directory is not at the integration head {sha[:12]}; run epic review from a "
                              "work tree at that commit")
            state, desc = run_done_means(items, args, runner)
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
        report["ruleset"] = "skipped"
        if not args.no_ruleset:
            report["ruleset"], rid = ensure_ruleset(args.repo, epic, args.target_branch, args.dry_run)
            report["ruleset_delete"] = (f"gh api -X DELETE repos/{args.repo}/rulesets/{rid if rid else '<id of ' + 'epic-acceptance-' + epic + '>'}")
        report["learned"] = learned(epic, args)
        n_atoms = sum(len(b["atoms"]) for w in plan["increments"] for b in w["batches"])
        body = (f"Epic {epic}: {n_atoms} atoms merged into {args.integration} at {sha}.\n\n"
                f"{CHECK}: success. Milestone open count: 0.\n{report['learned']}\n\n"
                "Release notes: written by the cut-release step. Draft only: the operator tests, tags and publishes.")
        report["release"] = draft_release(args.repo, args.tag, sha, f"{epic} {args.tag}", body, args.dry_run)
        m = _pi.PROJECT_RE.match(args.project or "")
        if not m:
            raise Refused("--project must be <owner>/<number>")
        report["status_update"] = final_status((m.group(1), int(m.group(2))), epic, sha,
                                               f"{CHECK}: success. {report['learned']} Release {args.tag}: "
                                               f"{report['release']} (draft, pinned to {sha[:12]}).", args.dry_run)
        return report
    except Pending as p:
        p.report = report
        raise
    finally:
        if not args.dry_run:
            drop_lease(args.repo, epic)


def main(argv=None, clock=None, runner=run_cmd):
    ap = argparse.ArgumentParser(prog="foundry_epic_review.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("run")
    p.add_argument("epic")
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
    p.add_argument("--done-means", help="a COMMITTED, unmodified repo file (YAML list of {name, argv})")
    p.add_argument("--epic-issue", help="<owner>/<repo>#<n>: its trust-checked Done means is run")
    p.add_argument("--trust-author", action="store_true", help="operator override of the Done-means trust check")
    p.add_argument("--done-means-timeout", type=int, default=1800)
    p.add_argument("--run-dir", default=".")
    p.add_argument("--target-branch", default="main")
    p.add_argument("--no-ruleset", action="store_true")
    p.add_argument("--recheck-in", type=int, default=300)
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    clock = clock or Clock()
    try:
        if not _ep.PROGRAMME_RE.match(a.epic):
            raise Refused(f"epic name {a.epic!r} is not a plain name")
        if not REPO_RE.match(a.repo):
            raise Refused("--repo must be <owner>/<name>")
        if not BRANCH_RE.match(a.integration) or ".." in a.integration or not BRANCH_RE.match(a.target_branch):
            raise Refused("--integration and --target-branch must be plain branch names")
        if not TAG_RE.match(a.tag):
            raise Refused("--tag must look like v2.0.0")
        if a.done_means_timeout < 1:
            raise Refused("--done-means-timeout must be positive")
        if a.milestone < 1:
            raise Refused("--milestone must be a milestone number")
        rep = epic_review(a.epic, a, clock, runner)
        print(json.dumps(rep, sort_keys=True))
        return 0
    except Pending as p:
        at = (clock.utcnow() + datetime.timedelta(seconds=max(0, a.recheck_in))).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(json.dumps({"epic": a.epic, "pending": [{"check": c, "recheck_at": at} for c in p.checks],
                          "report": getattr(p, "report", {})}, sort_keys=True))
        return 3
    except _pi.Unreachable:
        print("foundry_epic_review: refused: GitHub is unreachable; epic review needs it (nothing was changed offline)",
              file=sys.stderr)
        return 2
    except (Refused, _pi.IssueError, _ep.PlanError, OSError, subprocess.TimeoutExpired, KeyError, TypeError) as e:
        msg = str(e) or type(e).__name__
        print(f"foundry_epic_review: refused: {_fml.redact(msg).splitlines()[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
