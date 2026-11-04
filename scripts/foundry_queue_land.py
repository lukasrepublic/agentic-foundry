#!/usr/bin/env python3
"""foundry_queue_land — land one PR through the merge queue (operating-model rule A2, FG-DLV-004, 010-019).

    foundry_queue_land.py <pr> [--repo OWNER/NAME] [--repo-dir DIR] [--dry-run]
                          [--catalogue GLOB ...] [--catalogue-test-cmd 'CMD {files}'] [--full-suite-cmd CMD]
                          [--message-log PATH] [--poll-sec N] [--read-gap-sec N] [--timeout-min N]
                          [--checks-timeout-min N] [--quarantine-days N]
                          [--local-green-cmd CMD]

Run it from a checkout of the PR's own branch (the front for this script is skills/merge-when-green). In order:

  1. READ THE LIVE RULESET of the PR's base (FG-DLV-019). The base must carry a merge-queue rule and at least one
     required status check, and policy `merge.queue` must not be `none`; otherwise refuse and point at
     merge-when-green. A queue waits only for the checks its ruleset names.
  2. MERGE THE BASE INTO THE PR BRANCH (FG-DLV-004): `git merge --no-ff origin/<base>` and a plain push. Never a
     rebase, never a force. A conflict is aborted and refused. Then wait for the required checks to be green AT THE
     CURRENT HEAD (FG-DLV-013). A SKIPPED check is not a pass (FG-DLV-018): for every skipped check the owning
     workflow's run is read, and a skipped REQUIRED check refuses.
  3. A SHARED-CATALOGUE CHANGE runs every test that names that catalogue (FG-DLV-012), and, when policy
     `merge.full_suite_on_branch` lists `shared-catalogue-change` (or `after-queue-rejection` on a re-queue), the
     branch full suite. Otherwise the queue's own run is the gate and the column says so.
  4. POST THE ONE-LINE RECORD (FG-DLV-016) before the enqueue:
         PR | head | base merged at | catalogue tests | branch full suite | queued
     to the shared message log when scripts/foundry_message_log.py exists beside this script, else as a PR comment.
     A column that cannot be filled honestly refuses the enqueue and names the column.
  5. ENQUEUE with the one admitted shape `gh pr merge <pr> --repo R --auto --squash` (never --admin, never a force flag;
     the git-discipline hook admits `--auto` only with a required check and a green head), READ THE QUEUE ENTRY TWICE
     (FG-DLV-015), and WAIT.
  6. A REJECTION (FG-DLV-017): the failed run log is read FIRST. A failure in the PR's own files exits `fix-own-files`.
     A failure outside the diff posts the evidence and re-queues ONCE (policy `merge.requeue_budget_outside_diff`,
     always 1). The SAME failure a second time writes a quarantine request (the quality kit's record shape) for the
     test's owner and never re-queues; a different failure after the budget is spent also never re-queues. The count
     is kept in marker comments on the PR, so it survives a new session.
  7. ON MERGE the delivered-by record (PR, merger login, head sha) is posted in the same action (FG-DLV-011).

NOT GUARDED BY THE HOOK. The git and gh calls this script makes are subprocesses; the PreToolUse hook sees only the
one command that starts the script. So the script re-applies, itself, what the hook would have enforced: it refuses
before ANY push when the PR is already in the queue (clause (l)), and it runs the repository's local-green runner
(`foundry-test.sh`) before the base-merge push (clause (j)). It also RUNS THE PR BRANCH'S CODE LOCALLY (the tests
that name a catalogue, the full suite): use it for the operator's own PRs, never for a fork's.

Every gh call goes through `Adapter.gh`, every git call through `Adapter.git`, so tests inject a stub.
Output: JSON lines; the last line is the terminal one {"status": ...}. Exit: 0 merged, 2 refused, 3 fix-own-files,
4 quarantine-requested, 5 requeue-budget-spent, 6 rejection-unclassified, 7 timeout, 8 not-in-queue.
Never uses --admin, auto-merge on a base without a required check, a force flag, or a push to a queued PR.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import foundry_merge_plan as mplan  # noqa: E402  (the catalogue globs are shared with the merge plan)

COLUMNS = ("PR", "head", "base merged at", "catalogue tests", "branch full suite", "queued")
PR_FIELDS = "number,state,isDraft,isCrossRepository,headRefName,headRefOid,baseRefName,url,files,reviewDecision"
POLL_FIELDS = "state,mergedAt,mergedBy,mergeCommit,headRefOid"
COMMENT_FIELDS = "comments"
REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")   # the git-discipline hook's own pattern for a branch name
CHECK_FIELDS = "name,bucket,workflow,link"
QUEUE_GQL = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
             "{state merged isInMergeQueue mergeQueueEntry{position state}}}}")
MARKER_RE = re.compile(r"<!-- queue-land:v1 kind=(\w+) sig=(\w+) n=(\d+)[^>]*-->")
EXIT = {"merged": 0, "refused": 2, "fix-own-files": 3, "quarantine-requested": 4, "requeue-budget-spent": 5,
        "rejection-unclassified": 6, "timeout": 7, "not-in-queue": 8}


class Refused(Exception):
    def __init__(self, reason, **extra):
        super().__init__(reason)
        self.reason, self.extra = reason, extra


def emit(status=None, **kw):
    print(json.dumps(({"status": status} if status else {}) | kw, sort_keys=True), flush=True)


# ------------------------------------------------------------------------------------------- adapter
class Adapter:
    """The ONE place this script touches gh, git, a test command, the clock or the message log."""

    def __init__(self, cwd):
        self.cwd = cwd

    def _run(self, argv, input=None, timeout=300, shell=False):
        try:
            p = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout,
                               cwd=self.cwd, shell=shell)
        except FileNotFoundError as exc:
            return 127, "", str(exc)
        except subprocess.TimeoutExpired:
            return 124, "", "timed out after %ss" % timeout
        return p.returncode, p.stdout, p.stderr

    def gh(self, *argv, input=None):
        return self._run(["gh", *argv], input=input, timeout=60)

    def git(self, *argv):
        return self._run(["git", "-C", self.cwd, *argv], timeout=120)

    def shell(self, cmd, timeout=3600):
        return self._run(cmd, shell=True, timeout=timeout)

    def python(self, script, *argv, input=None):
        return self._run([sys.executable, script, *argv], input=input, timeout=120)

    @staticmethod
    def sleep(sec):
        if sec > 0:
            time.sleep(sec)

    @staticmethod
    def now():
        return dt.datetime.now(dt.timezone.utc)


# ------------------------------------------------------------------------------------------- pure helpers
def record_line(values: dict) -> str:
    """The one-line record. Refuses, naming the column, when a column is unknown or empty (FG-DLV-016)."""
    for k in values:
        if k not in COLUMNS:
            raise Refused("unknown record column %r (the columns are: %s)" % (k, " | ".join(COLUMNS)), column=k)
    for col in COLUMNS:
        v = values.get(col)
        if v is None or not str(v).strip():
            raise Refused("record column %r cannot be filled honestly, so the PR is not queued" % col, column=col)
    return " | ".join(str(values[c]).strip().replace("\n", " ") for c in COLUMNS)


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STAMP = re.compile(r"^\S+\t\S+\t\d{4}-\d\d-\d\dT[\d:.]+Z\s|^\d{4}-\d\d-\d\dT[\d:.]+Z\s")
_FAIL_LINE = re.compile(r"FAILED|FAIL\b|--- FAIL|✖|\bnot ok\b|●|\bError\b|AssertionError|^E\s", re.I)
_FILE = re.compile(r"((?:\.{0,2}/)?[\w.@-]+(?:/[\w.@-]+)*\.(?:py|js|mjs|cjs|ts|tsx|jsx|go|rb|rs|java|kt|sh|bats))\b")
_ID_PATTERNS = (re.compile(r"FAILED\s+(\S+)"), re.compile(r"--- FAIL:\s+(\S+)"), re.compile(r"not ok \d+ - (.+)"),
                re.compile(r"✖\s+(.+?)(?:\s+\(\d.*)?$"), re.compile(r"●\s+(.+)"))


def parse_failure(log: str) -> dict:
    """{"ids": [...], "files": [...], "signature": str|None} from a failed CI log."""
    ids, files = set(), set()
    for raw in log.splitlines():
        line = _STAMP.sub("", _ANSI.sub("", raw)).strip()
        if not line or not _FAIL_LINE.search(line):
            continue
        for pat in _ID_PATTERNS:
            m = pat.search(line)
            if m:
                ids.add(m.group(1).strip())
        for m in _FILE.finditer(line):
            files.add(m.group(1).lstrip("./") if m.group(1).startswith("./") else m.group(1))
    basis = sorted(i.split(" - ")[0] for i in ids) or sorted(files)
    sig = hashlib.sha1("\n".join(basis).encode("utf-8")).hexdigest()[:12] if basis else None
    return {"ids": sorted(ids), "files": sorted(files), "signature": sig}


def classify(failure: dict, diff_files) -> str:
    """own-files | outside-diff | unclassified. Outside needs a named file and none in the diff."""
    diff = {d.lstrip("./") for d in diff_files}
    if not failure["files"]:
        return "unclassified"
    for f in failure["files"]:
        if f in diff or any(d.endswith("/" + f) or f.endswith("/" + d) for d in diff):
            return "own-files"
    return "outside-diff"


def markers(comments) -> list:
    out = []
    for c in comments or []:
        for m in MARKER_RE.finditer((c or {}).get("body", "") if isinstance(c, dict) else ""):
            out.append({"kind": m.group(1), "sig": m.group(2), "n": int(m.group(3))})
    return out


def _json(text, what):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise Refused("%s returned unreadable output" % what)


# ------------------------------------------------------------------------------------------- the lander
class Lander:
    def __init__(self, a, ad, policy):
        self.a, self.ad, self.policy = a, ad, policy
        self.pr = str(a.pr)
        self.slug = a.repo
        self.base = self.head_ref = None

    # -- gh helpers
    def gh_ok(self, *argv, what, input=None):
        rc, out, err = self.ad.gh(*argv, input=input)
        if rc != 0:
            raise Refused("%s failed (exit %d: %s)" % (what, rc, (err or out).strip()[:200]))
        return out

    def r(self):
        return ["--repo", self.slug] if self.slug else []

    def view(self, fields, what="gh pr view"):
        return _json(self.gh_ok("pr", "view", self.pr, *self.r(), "--json", fields, what=what), what)

    # -- 1. ruleset
    def read_ruleset(self):
        v = self.view(PR_FIELDS)
        self.base, self.head_ref = v.get("baseRefName"), v.get("headRefName")
        m = re.match(r"^https?://[^/]+/([^/]+/[^/]+)/pull/\d+", str(v.get("url", "")))
        if not (self.base and self.head_ref and m):
            raise Refused("the PR's base, head branch and repository could not be read")
        for what, ref in (("base", self.base), ("head", self.head_ref)):
            if not REF_RE.match(ref) or ".." in ref or ref.startswith("-"):
                raise Refused("the PR's %s branch name %r is not a plain ref name" % (what, ref))
        if v.get("isCrossRepository"):
            raise Refused("PR #%s comes from a fork: this script runs the branch's code locally and pushes to its "
                          "branch, so it is for the operator's own PRs only" % self.pr)
        self.slug = self.slug or m.group(1)
        if v.get("state") != "OPEN":
            raise Refused("PR #%s is %s, not OPEN" % (self.pr, v.get("state")))
        if v.get("isDraft"):
            raise Refused("PR #%s is a draft" % self.pr)
        if self.policy.get("merge.human_review_required") and v.get("reviewDecision") != "APPROVED":
            raise Refused("policy merge.human_review_required is on and the PR has no approving review")
        if self.policy.get("merge.queue") == "none":
            raise Refused("policy merge.queue is 'none': land this PR with /foundry:merge-when-green")
        rules = _json(self.gh_ok("api", "repos/%s/rules/branches/%s" % (self.slug, self.base),
                                 what="the ruleset query"), "the ruleset query")
        if not isinstance(rules, list):
            raise Refused("the ruleset of %s is not a rule list" % self.base)
        required = sorted({c.get("context") for r in rules if isinstance(r, dict) and r.get("type") == "required_status_checks"
                           for c in (r.get("parameters") or {}).get("required_status_checks") or [] if c.get("context")})
        has_queue = any(isinstance(r, dict) and r.get("type") == "merge_queue" for r in rules)
        if not has_queue:
            raise Refused("the ruleset of %s has no merge-queue rule: land this PR with /foundry:merge-when-green" % self.base)
        classic = self.classic_required()
        if not required and not classic:
            raise Refused("neither the rulesets nor the classic branch protection of %s name a required status check: "
                          "a queue waits only for the checks the base names, so this would merge without waiting" % self.base)
        required = sorted(set(required) | set(classic))
        self.required = required
        # Clause (l), re-applied here because this script's own git calls are not seen by the hook: a PR that is
        # ALREADY in the queue (a re-run after a timeout or an abort) must not be pushed to. Refuse before any push.
        q0 = self.read_queue()
        if q0.get("isInMergeQueue"):
            raise Refused("PR #%s is already in the merge queue: dequeue it first (a push would move the head the "
                          "queue is testing), then run again" % self.pr, reason_code="dequeue-first")
        self.diff_files = [f.get("path") for f in v.get("files") or [] if isinstance(f, dict)]
        return v

    def classic_required(self):
        """Required check contexts of classic branch protection ([] on any error: none named)."""
        rc, out, _ = self.ad.gh("api", "repos/%s/branches/%s" % (self.slug, self.base))
        try:
            rsc = ((json.loads(out).get("protection") or {}).get("required_status_checks") or {}) if rc == 0 else {}
            return sorted({c for c in list(rsc.get("contexts") or []) + [x.get("context") for x in rsc.get("checks") or [] if isinstance(x, dict)] if c})
        except (ValueError, AttributeError, TypeError):
            return []

    # -- 2. base merge + green head
    def merge_base(self):
        ad, base = self.ad, self.base
        rc, out, _ = ad.git("status", "--porcelain")
        if rc != 0 or out.strip():
            raise Refused("the working tree is not clean (or not a repository): commit or stash first")
        rc, cur, _ = ad.git("rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0 or cur.strip() != self.head_ref:
            raise Refused("this checkout is on %r, not the PR's branch %r" % (cur.strip(), self.head_ref))
        rc, _, err = ad.git("fetch", "-q", "--end-of-options", "origin", base)
        if rc != 0:
            raise Refused("origin/%s cannot be fetched (%s)" % (base, err.strip()[:120]), column="base merged at")
        rc, base_sha, _ = ad.git("rev-parse", "--end-of-options", "origin/%s" % base)
        if rc != 0 or not base_sha.strip():
            raise Refused("origin/%s does not resolve" % base, column="base merged at")
        self.base_sha = base_sha.strip()
        anc = ad.git("merge-base", "--is-ancestor", "--end-of-options", self.base_sha, "HEAD")[0] == 0
        if not anc:
            rc, _, err = ad.git("merge", "--no-ff", "--no-edit", "-m", "Merge %s into %s" % (base, self.head_ref),
                                "--end-of-options", "origin/%s" % base)
            if rc != 0:
                _, files, _ = ad.git("diff", "--name-only", "--diff-filter=U")
                ad.git("merge", "--abort")
                raise Refused("merging origin/%s into %s failed (%s): resolve it by hand, then run again"
                              % (base, self.head_ref, ("conflicts in " + ", ".join(files.split()[:8])) if files.strip()
                                 else err.strip()[:120] or "see git status"), reason_code="merge-conflict")
            self.local_green()
            rc, _, err = ad.git("push", "--end-of-options", "origin", "HEAD:%s" % self.head_ref)
            if rc != 0:
                raise Refused("pushing the base merge failed (%s)" % err.strip()[:160])
        rc, head, _ = ad.git("rev-parse", "HEAD")
        self.head = head.strip()
        if rc != 0 or not self.head:
            raise Refused("HEAD does not resolve", column="head")
        emit(event="base-merged", base=self.base, base_sha=self.base_sha[:12], merged_now=not anc, head=self.head[:12])

    def local_green(self):
        """Clause (j) for this script's own push: the repository's local runner must pass on the merge result."""
        cmd = self.a.local_green_cmd
        if cmd is None:
            runner = HERE / "foundry-test.sh"
            if not runner.is_file():
                emit(event="local-green", ran=False, why="no foundry-test.sh beside this script")
                return
            cmd = "%s --dir %s" % (shlex.quote(str(runner)), shlex.quote(self.a.repo_dir))
        rc, out, err = self.ad.shell(cmd)
        emit(event="local-green", ran=True, ok=rc == 0)
        if rc != 0:
            raise Refused("local-green failed on the base-merge result, so it was NOT pushed (the merge commit stays "
                          "local): %s" % (out + err).strip()[-300:], reason_code="local-green-failed")

    def changed_files(self):
        rc, out, _ = self.ad.git("diff", "--name-only", "--end-of-options", "origin/%s...HEAD" % self.base)
        if rc != 0:
            raise Refused("the changed files cannot be listed", column="catalogue tests")
        return [x for x in out.splitlines() if x]

    def wait_green(self):
        deadline = self.ad.now() + dt.timedelta(minutes=self.a.checks_timeout_min)
        while True:
            state, detail = self.check_state()
            if state == "green":
                return detail
            if state in ("red", "skipped-required"):
                raise Refused("%s at head %s: %s" % (state, self.head[:12], detail), checks=state)
            if self.ad.now() >= deadline:
                raise Refused("checks not green at head %s before the timeout (%s)" % (self.head[:12], detail), checks="timeout")
            self.ad.sleep(self.a.poll_sec)

    def check_state(self):
        v = self.view(POLL_FIELDS)
        if v.get("headRefOid") and v["headRefOid"] != self.head:
            return "pending", "PR head is %s, expected %s (the push has not landed yet)" % (str(v["headRefOid"])[:12], self.head[:12])
        _, out, _ = self.ad.gh("pr", "checks", self.pr, *self.r(), "--json", CHECK_FIELDS)
        try:
            rows = json.loads(out)
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            return "pending", "gh pr checks gave no readable answer"
        names = {r.get("name"): r for r in rows if isinstance(r, dict)}
        bad = [n for n, r in names.items() if r.get("bucket") in ("fail", "cancel")]
        if bad:
            return "red", "failing: " + ", ".join(sorted(bad))
        missing = [c for c in self.required if c not in names]
        pending = [n for n, r in names.items() if r.get("bucket") == "pending"]
        skipped = {n: r for n, r in names.items() if r.get("bucket") == "skipping"}
        notes = []
        for n, r in sorted(skipped.items()):
            concl = self.workflow_conclusion(r.get("workflow"))
            if n in self.required:
                return "skipped-required", "required check %r was SKIPPED (workflow %r concluded %s); a skip is not a pass" % (n, r.get("workflow"), concl)
            notes.append("%s (workflow %s: %s)" % (n, r.get("workflow"), concl))
        if missing or pending:
            return "pending", "waiting on " + ", ".join(sorted(missing + pending))
        return "green", {"required": self.required, "skipped_not_required": notes}

    def workflow_conclusion(self, workflow):
        """Read the workflow that owns a skipped check: its run's conclusion at this head."""
        rc, out, _ = self.ad.gh("run", "list", *self.r(), "--commit", self.head, "--workflow", str(workflow),
                                "--json", "conclusion,status,name")
        try:
            rows = json.loads(out)
            return ",".join(sorted({str(x.get("conclusion") or x.get("status")) for x in rows})) or "no run"
        except (ValueError, AttributeError, TypeError):
            return "unreadable"

    # -- 3. catalogue + full suite
    def catalogue(self, changed):
        globs = mplan.catalogue_globs(Path(self.a.repo_dir), self.a.catalogue)
        declared = bool(self.a.catalogue) or (Path(self.a.repo_dir) / mplan.CATALOGUE_FILE).is_file()
        if not declared:
            rc, tracked, _ = self.ad.git("ls-files")
            if not any(mplan._is_catalogue(f, globs) for f in tracked.splitlines()):
                return [], "n/a: no catalogue declared"
        hit = sorted(f for f in changed if mplan._is_catalogue(f, globs))
        if not hit:
            return [], "n/a: no shared catalogue changed"
        tests = set()
        for f in hit:
            for needle in {f, os.path.basename(f)}:
                rc, out, _ = self.ad.git("grep", "-l", "-F", "--", needle)
                if rc not in (0, 1):
                    raise Refused("the tests that name %s cannot be searched" % f, column="catalogue tests")
                tests |= {t for t in out.split() if re.search(r"(^|/)(tests?|__tests__|spec)/|test_|_test\.|\.test\.|\.spec\.", t)}
        tests -= set(hit)
        if not tests:
            return hit, "none: no test names %s" % ", ".join(hit)
        files = sorted(tests)
        cmd = self.a.catalogue_test_cmd
        if cmd:
            runs = [cmd.replace("{files}", " ".join(shlex.quote(t) for t in files))]
        else:
            py = [t for t in files if t.endswith(".py")]
            js = [t for t in files if re.search(r"\.(mjs|cjs|js|ts)$", t)]
            if len(py) + len(js) != len(files):
                raise Refused("tests naming the catalogue are of a kind this script cannot run: pass --catalogue-test-cmd",
                              column="catalogue tests")
            runs = ([f"{shlex.quote(sys.executable)} -m pytest -q " + " ".join(map(shlex.quote, py))] if py else []) + \
                   (["node --test " + " ".join(map(shlex.quote, js))] if js else [])
        for run in runs:
            rc, out, err = self.ad.shell(run)
            if rc != 0:
                raise Refused("tests that name the changed catalogue FAIL (%s): %s" % (run[:80], (out + err).strip()[-300:]),
                              column="catalogue tests")
        return hit, "pass: %d test file(s) name %s" % (len(files), ", ".join(hit))

    def full_suite(self, catalogue_hit, after_rejection):
        triggers = list(self.policy.get("merge.full_suite_on_branch") or [])
        why = []
        if catalogue_hit and "shared-catalogue-change" in triggers:
            why.append("shared-catalogue-change")
        if after_rejection and "after-queue-rejection" in triggers:
            why.append("after-queue-rejection")
        if not why:
            return "not required: the queue's own run is the gate"
        cmd = self.a.full_suite_cmd
        if not cmd:
            wrapper = Path(self.a.repo_dir) / "scripts" / "foundry-test.sh"
            cmd = shlex.quote(str(wrapper)) if wrapper.is_file() else None
        if not cmd:
            raise Refused("the branch full suite is required (%s) but no command is known: pass --full-suite-cmd" % ", ".join(why),
                          column="branch full suite")
        rc, out, err = self.ad.shell(cmd)
        if rc != 0:
            raise Refused("the branch full suite FAILS (%s): %s" % (", ".join(why), (out + err).strip()[-300:]),
                          column="branch full suite")
        return "pass (%s)" % ", ".join(why)

    # -- 4. record
    def post(self, text, kind="record"):
        log = self.a.message_log
        if kind == "record" and log and Path(log).is_file():
            rc, _, err = self.ad.python(str(log), "post", "--to", "all", "--kind", "FINDING",
                                        "--refs", "https://github.com/%s/pull/%s" % (self.slug, self.pr),
                                        "--body-file", "-", input=text + "\n")
            if rc == 0:
                return "message-log"
        rc, out, err = self.ad.gh("pr", "comment", self.pr, *self.r(), "--body", text)
        if rc != 0:
            raise Refused("the %s could not be posted (%s)" % (kind, (err or out).strip()[:160]))
        return "pr-comment"

    # -- 5. queue
    def read_queue(self):
        owner, name = self.slug.split("/", 1)
        out = self.gh_ok("api", "graphql", "-f", "query=" + QUEUE_GQL, "-f", "o=" + owner, "-f", "r=" + name,
                         "-F", "n=" + self.pr, what="the queue-entry query")
        pr = (((_json(out, "the queue-entry query") or {}).get("data") or {}).get("repository") or {}).get("pullRequest")
        if not isinstance(pr, dict):
            raise Refused("the queue-entry query returned no pull request")
        return pr

    def enqueue_and_wait(self):
        self.enqueued_at = self.ad.now()
        self.gh_ok("pr", "merge", self.pr, "--repo", self.slug, "--auto", "--squash", what="the enqueue")
        first = self.read_queue()
        self.ad.sleep(self.a.read_gap_sec)
        second = self.read_queue()                          # it can appear late: read twice (FG-DLV-015)
        emit(event="queue-read", first=first.get("isInMergeQueue"), second=second.get("isInMergeQueue"))
        if not (first.get("isInMergeQueue") or second.get("isInMergeQueue") or second.get("merged")):
            return "not-in-queue"
        deadline = self.ad.now() + dt.timedelta(minutes=self.a.timeout_min)
        absent = 0
        while True:
            q = self.read_queue()
            if q.get("merged") or q.get("state") == "MERGED":
                return "merged"
            absent = 0 if q.get("isInMergeQueue") else absent + 1
            if absent >= 2:                                  # gone twice while OPEN: rejected or dequeued
                return "rejected"
            if self.ad.now() >= deadline:
                return "timeout"
            self.ad.sleep(self.a.poll_sec)

    # -- 6. rejection
    def failed_log(self):
        out = self.gh_ok("run", "list", *self.r(), "--event", "merge_group", "--limit", "30",
                         "--json", "databaseId,conclusion,headBranch,workflowName,createdAt", what="the merge-group run list")
        floor = self.enqueued_at - dt.timedelta(seconds=60)           # only runs created after THIS enqueue
        rows = [x for x in _json(out, "the merge-group run list") if isinstance(x, dict)
                and x.get("conclusion") == "failure" and self.created_after(x.get("createdAt"), floor) and re.search(r"(^|/)pr-%s-" % re.escape(self.pr), str(x.get("headBranch", "")))]
        if not rows:
            return None, None
        run_id = rows[0]["databaseId"]
        return run_id, self.gh_ok("run", "view", str(run_id), *self.r(), "--log-failed", what="the failed log")

    @staticmethod
    def created_after(stamp, floor):
        try:
            return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) >= floor
        except ValueError:
            return False

    def trusted(self):
        """Logins whose marker comments count: the authenticated login plus github_scope.operators."""
        if getattr(self, "_trusted", None) is None:
            logins = set()
            try:
                import foundry_deck_events as de
                logins |= {str(x).lower() for x in de.scope_operators(self.a.repo_dir) or ()}
            except Exception:
                pass
            rc, out, _ = self.ad.gh("api", "user")
            try:
                me = json.loads(out).get("login") if rc == 0 else None
            except (ValueError, AttributeError):
                me = None
            if me:
                logins.add(str(me).lower())
            if not logins:
                raise Refused("the authenticated GitHub login cannot be read, so the re-queue count cannot be trusted")
            self._trusted = logins
        return self._trusted

    def comments(self):
        """The PR's comments by TRUSTED authors only: anyone can comment a forged marker."""
        rows = (self.view(COMMENT_FIELDS, "the PR comments").get("comments")) or []
        ok = self.trusted()
        return [c for c in rows if isinstance(c, dict) and str((c.get("author") or {}).get("login", "")).lower() in ok]

    def handle_rejection(self):
        run_id, log = self.failed_log()                      # the failed log is read FIRST
        if log is None:
            emit("rejection-unclassified", pr=self.pr, reason="no failed merge-group run log was found for this PR")
            return "rejection-unclassified", None
        failure = parse_failure(log)
        kind = classify(failure, self.diff_files)
        info = {"run": run_id, "failing": failure["ids"][:10], "files": failure["files"][:10], "signature": failure["signature"]}
        if kind == "unclassified" or not failure["signature"]:
            emit("rejection-unclassified", pr=self.pr, reason="the log names no file, so 'outside the diff' cannot be shown", **info)
            return "rejection-unclassified", None
        if kind == "own-files":
            emit("fix-own-files", pr=self.pr, reason="the failure is in files this PR changes: fix them, then run again", **info)
            return "fix-own-files", None
        prior = [m for m in markers(self.comments()) if m["kind"] == "evidence"]
        sig = failure["signature"]
        if any(m["sig"] == sig for m in prior) or any(m["kind"] == "quarantine" and m["sig"] == sig
                                                      for m in markers(self.comments())):
            path = self.quarantine_request(failure, run_id)
            emit("quarantine-requested", pr=self.pr, request=str(path), reason="the same failure a second time: never a third roll", **info)
            return "quarantine-requested", None
        if len(prior) >= self.policy.get("merge.requeue_budget_outside_diff"):
            self.post(self.evidence_text(failure, run_id, len(prior) + 1, requeue=False), "evidence")
            emit("requeue-budget-spent", pr=self.pr, reason="a different failure after the one allowed re-queue: no further re-queue", **info)
            return "requeue-budget-spent", None
        self.post(self.evidence_text(failure, run_id, len(prior) + 1, requeue=True), "evidence")
        emit(event="requeue", pr=self.pr, n=len(prior) + 1, **info)
        return "requeue", failure

    def evidence_text(self, f, run_id, n, requeue):
        return ("queue-land: PR #%s was rejected by the merge queue (run %s). The failing log names files outside this PR's diff: %s.\n"
                "Failing: %s\n%s\n<!-- queue-land:v1 kind=evidence sig=%s n=%d run=%s -->"
                % (self.pr, run_id, ", ".join(f["files"][:10]), "; ".join(f["ids"][:10]) or "(see log)",
                   "Re-queue %d of 1, within policy merge.requeue_budget_outside_diff." % n if requeue
                   else "No re-queue: the budget is spent.", f["signature"], n, run_id))

    def quarantine_request(self, f, run_id):
        today = self.ad.now().date()
        tests = (f["ids"] or f["files"])[:50]
        owner = self.owner_of(f["files"][0] if f["files"] else tests[0])
        issue = "https://github.com/%s/pull/%s" % (self.slug, self.pr)
        rec = {"quarantine": [{"test": t, "owner": owner, "issue": issue, "entry": today.isoformat(),
                               "expiry": (today + dt.timedelta(days=self.a.quarantine_days)).isoformat()} for t in tests]}
        text = json.dumps(rec, indent=2)
        d = Path(self.a.repo_dir) / ".foundry" / "quarantine-requests"      # gitignored: the tree stays clean
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("pr-%s-%s.json" % (self.pr, f["signature"]))
        path.write_text(text + "\n", encoding="utf-8")
        self.post("queue-land: QUARANTINE REQUEST for %d failing test(s), owner: %s. They failed the merge queue twice with the "
                  "same signature on PR #%s (latest run %s), outside the PR's diff. Quarantine is not skip: the tests still run "
                  "and are reported. The full record, in the quality kit's shape:\n```json\n%s\n```\n"
                  "<!-- queue-land:v1 kind=quarantine sig=%s n=1 run=%s -->"
                  % (len(tests), owner, self.pr, run_id, text, f["signature"], run_id), "quarantine request")
        return path

    def owner_of(self, path):
        rc, out, _ = self.ad.git("log", "-1", "--format=%an", "--", path)
        return out.strip() if rc == 0 and out.strip() else "unassigned"

    # -- 7. delivered-by
    def delivered_by(self):
        v = self.view(POLL_FIELDS)
        login = ((v.get("mergedBy") or {}).get("login")) or "unknown (the platform did not report it)"
        oid = ((v.get("mergeCommit") or {}).get("oid")) or "unknown"
        text = ("delivered-by: PR #%s | merged by %s | head %s | merge commit %s\n<!-- queue-land:v1 kind=delivered sig=%s n=1 -->"
                % (self.pr, login, self.head[:12], str(oid)[:12], self.head[:12]))
        self.post(text, "delivered-by record")
        return {"pr": self.pr, "merged_by": login, "head": self.head, "merge_commit": oid}

    # -- the loop
    def land(self):
        self.read_ruleset()
        after_rejection = any(m["kind"] == "evidence" for m in markers(self.comments()))
        for _attempt in range(1 + int(self.policy.get("merge.requeue_budget_outside_diff"))):
            self.merge_base()
            green = self.wait_green()
            hit, cat_col = self.catalogue(self.changed_files())
            suite_col = self.full_suite(hit, after_rejection)
            line = record_line({"PR": "#" + self.pr, "head": self.head[:12],
                                "base merged at": "%s@%s" % (self.base, self.base_sha[:12]),
                                "catalogue tests": cat_col, "branch full suite": suite_col,
                                "queued": "yes: enqueue follows with gh pr merge --auto"})
            where = self.post(line)
            emit(event="record", line=line, posted_to=where, green=green)
            if self.a.dry_run:
                return "dry-run", {"record": line}
            outcome = self.enqueue_and_wait()
            if outcome == "merged":
                return "merged", self.delivered_by()
            if outcome in ("timeout", "not-in-queue"):
                return outcome, {"pr": self.pr}
            status, _ = self.handle_rejection()
            if status != "requeue":
                return status, {"pr": self.pr}
            after_rejection = True
        return "requeue-budget-spent", {"pr": self.pr}


def load_policy(project_dir):
    sys.path.insert(0, str(HERE))
    import foundry_operating_model as om
    try:
        return om.load(project_dir)
    except om.PolicyError as exc:
        raise Refused("the operating-model policy is unusable: %s" % exc)


def main(argv=None, adapter=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_queue_land.py", description=__doc__.split("\n")[0])
    ap.add_argument("pr", help="the PR number (a literal)")
    ap.add_argument("--repo", help="OWNER/NAME (default: read from the PR)")
    ap.add_argument("--repo-dir", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true", help="do everything up to and including the record; do not enqueue")
    ap.add_argument("--catalogue", action="append", default=None)
    ap.add_argument("--catalogue-test-cmd")
    ap.add_argument("--full-suite-cmd")
    ap.add_argument("--message-log", default=str(HERE / "foundry_message_log.py"))
    ap.add_argument("--poll-sec", type=float, default=30)
    ap.add_argument("--read-gap-sec", type=float, default=5)
    ap.add_argument("--timeout-min", type=float, default=90)
    ap.add_argument("--checks-timeout-min", type=float, default=30)
    ap.add_argument("--quarantine-days", type=int, default=14)
    ap.add_argument("--local-green-cmd", default=None,
                    help="command that proves local-green before the base-merge push (default: foundry-test.sh)")
    a = ap.parse_args(argv)
    try:
        if not re.fullmatch(r"[1-9][0-9]{0,8}", a.pr):
            raise Refused("the PR must be a literal number")
        a.repo_dir = os.path.abspath(a.repo_dir)
        policy = load_policy(a.repo_dir)
        lander = Lander(a, adapter or Adapter(a.repo_dir), policy)
        status, extra = lander.land()
    except Refused as exc:
        emit("refused", reason=exc.reason, **exc.extra)
        return EXIT["refused"]
    if status == "dry-run":
        emit("dry-run", **extra)
        return 0
    if status == "merged":
        emit("merged", **extra)
    elif status not in ("fix-own-files", "quarantine-requested", "requeue-budget-spent", "rejection-unclassified"):
        emit(status, **extra)                                # timeout / not-in-queue (the others emitted already)
    return EXIT.get(status, 2)


if __name__ == "__main__":
    sys.exit(main())
