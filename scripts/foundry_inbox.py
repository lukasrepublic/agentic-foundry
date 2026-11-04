#!/usr/bin/env python3
"""foundry_inbox.py — discover, persist, classify and escalate the operator's GitHub requests (gn-09).

Durable sources, each probed and reported separately (status ok|uncovered|error + scope):
  assigned         `request`-labelled open issues assigned to @me         (kind: request)
  mentions         open issues that mention @me                           (kind: mention)
  review_requests  open PRs with a review requested from @me              (kind: review)
  approvals        the notifications API, reasons approval_requested /    (kind: approval)
                   review_requested; the first poll uses `all=true` and follows `Link: rel="next"` up to
                   5 pages (per_page=50; a longer listing is flagged `truncated`); later polls use
                   If-Modified-Since, 304 = "no change", never faster than X-Poll-Interval (a cached
                   return keeps its ORIGINAL checked_at). Its items are add-only: a request stays pending
                   whatever the notification read state, until it is resolved. Two exceptions: a
                   notification whose subject URL cannot be parsed is surfaced with `ref: null` and pruned
                   once its notification id is gone from a fresh listing; a parseable approval whose PR is
                   merged/closed is pruned.

Two reach-disjoint verbs (the permission floor tiers them separately):
  scan   READ-ONLY. Discovers sources, blockers, coverage, dispatch_allowed, pending and the escalate
         candidates; writes only the gitignored runtime file. Tier `allow`. Never posts a comment.
  check  scan + records the gn-04 events (`blocker_ack`, `scheduled`) as marker comments on the operator's
         request issues. Tier `ask`, like ack / schedule / resolve.

Pending requests persist in `<workspace>/.foundry/runtime/inbox.json` (the directory carries its own
`.gitignore` of `*`; atomic temp+rename writes). A request that `blocks` an open issue owned by another
operator gets a `blocker_ack` (on the request issue; gn-04 records the `BlockingAddedEvent` there as a
`blocked` event with relation "blocking", so the ack validates) and an `escalate` item; any other request
gets a `scheduled` event with a target and queue position. Events go through the gn-04 writer
(`foundry_deck_events.emit`); already-recorded markers on the request issue are read back first, so a lost
runtime file causes no duplicate event.

Escalations persist: `escalate` is DERIVED on every run from the persisted blocked items (refreshed by the
live read-back) and stays surfaced - also by `pending` - until `resolve`. "Do not re-emit the event" is
independent of "still surface the escalation". A `resolved` marker suppresses its request only until new
activity after it (a later blocked episode, a reopen, or a human comment on the request).

State is a rebuildable cache, and there is NO file lock: two concurrent verbs may load-mutate-save over
each other (last writer wins). A lost update is repaired by the next scan, because every durable fact is
re-read from GitHub's own record (markers, relations); nothing authoritative lives only in inbox.json.

Degraded-dispatch policy (`github_scope.degraded_policy`, charter-facing): `dispatch_allowed` is permitted
despite a failed source only if the policy NAMES every failed source (the failed set must be a SUBSET of
`policy.sources`) and matches the scope. The subset rule alone makes a failure of an unnamed source deny,
whatever the age; a permanently uncovered source the policy names (e.g. `approvals` under a fine-grained
token) never denies forever. `max_age_min` (default 10) bounds only the STALENESS of the current full-or-
covered check itself: `last_covered_check_at` is persisted, and `dispatch_allowed` is computed at check
time AND re-evaluated by `pending` against `now - last_covered_check_at <= max_age_min`, so a stale runtime
file (e.g. the deck resumed after sleeping) reads false with decision_needed "inbox check is stale" until a
fresh check runs. `scan`/`check` always run fresh.

`resolve` evidence is a practice, not a control: it must be an https URL WITH a path (issue / PR / commit
shape), which rejects a bare host or a typo, but nothing verifies that the dependency is truly satisfied
- the operator's own judgement is the control.

Every subprocess goes through ONE injectable `runner(argv, timeout) -> Result` (tests stub it).

  foundry_inbox.py scan [--queue-status ok|stale|error] | check [--queue-status ...] | pending
                   | ack URL | schedule URL --target T | resolve URL --evidence https://host/path
                                                           (all accept --project-dir DIR and --json)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_deck_events as de  # noqa: E402
import foundry_ticket_store as store  # noqa: E402
import foundry_github_capabilities as _ghc  # noqa: E402
from foundry_deck_events import GhAdapter, _dt, _iso  # noqa: E402
from foundry_github_capabilities import (  # noqa: E402
    API_VERSION, DEFAULT_TIMEOUT, _body, _fail, _header, _json, _row, _status_of, default_runner, load_scope,
)

SOURCES = ("assigned", "mentions", "review_requests", "approvals")
KINDS = {"assigned": "request", "mentions": "mention", "review_requests": "review", "approvals": "approval"}
APPROVAL_REASONS = ("approval_requested", "review_requested")
DEFAULT_POLL = 60
DEFAULT_TARGET = "next-iteration"
DEFAULT_MAX_AGE_MIN = 10.0
STALE_TEXT = "inbox check is stale"
QUEUE_STATUSES = ("ok", "stale", "error")
RUNTIME_REL = os.path.join(".foundry", "runtime", "inbox.json")
SCHEMA = 1
MAX_PAGES = 5
_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_EVIDENCE_RE = re.compile(r"^https://[^\s/?#]+/[^\s/?#]\S*$")     # https URL with a real path component

_URL_REF = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/(?:issues|pull)/(\d+)/?$")
_API_REF = re.compile(r"/repos/([^/\s]+/[^/\s]+)/(issues|pulls)/(\d+)$")
_SEARCH_FIELDS = "number,url,title,createdAt,updatedAt"


# ------------------------------------------------------------------ helpers ------------------------------

def ref_of(url):
    """`owner/repo#n` for an issue/PR html URL, else None."""
    m = _URL_REF.match(url or "")
    return f"{m.group(1)}#{m.group(2)}" if m else None


def runtime_path(project_dir):
    return os.path.join(project_dir, RUNTIME_REL)


def _empty_state():
    return {"schema": SCHEMA, "checked_at": None, "scope": None, "coverage": "partial",
            "last_full_check_at": None, "last_covered_check_at": None, "sources": {}, "notifications": {}, "pending": {},
            "dispatch_allowed": False, "decision_needed": "no check has run"}


def load_state(project_dir):
    try:
        with open(runtime_path(project_dir), encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("pending"), dict):
            base = _empty_state()
            base.update(st)
            return base
    except (OSError, ValueError):
        pass
    return _empty_state()


def save_state(project_dir, state):
    path = runtime_path(project_dir)
    d = str(store.ensure_runtime_dir(project_dir))   # confined to the workspace; `*` .gitignore (O_EXCL, no-follow)
    fd, tmp = tempfile.mkstemp(prefix=".inbox.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class RunnerAdapter(GhAdapter):
    """gn-04's adapter, with its single subprocess seam replaced by the injected runner."""

    def __init__(self, runner, timeout=DEFAULT_TIMEOUT):
        self.runner, self.timeout = runner, timeout

    def _run(self, args, stdin=None):
        r = self.runner(["gh", *args], self.timeout)
        if r.rc != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {(r.stderr or '').strip()[:300]}")
        return r.stdout

    def post_comment(self, issue, body):
        owner_repo, num = issue.split("#")
        self._run(["issue", "comment", num, "-R", owner_repo, "--body", body])


# ------------------------------------------------------------------ sources ------------------------------

def _search(name, argv_tail, cfg, timeout, runner):
    scope = cfg["owner"]
    res = runner(["gh", "search"] + argv_tail + ["--owner", scope, "--state", "open", "--limit", "100",
                                                  "--json", _SEARCH_FIELDS], timeout)
    if res.rc != 0:
        return _fail(name, res, scope), None
    data = _json(res.stdout)
    if not isinstance(data, list):
        return _row(name, "error", "unparseable search output", scope), None
    items = [{"url": d.get("url"), "title": d.get("title", ""), "created_at": d.get("createdAt", ""),
              "kind": KINDS[name]} for d in data if isinstance(d, dict) and d.get("url")]
    return _row(name, "ok", f"{len(items)} open", scope), items


def _notif_url(n):
    sub = n.get("subject") or {}
    api = sub.get("url") or ""
    m = _API_REF.search(api)
    if m:
        kind = "issues" if m.group(2) == "issues" else "pull"
        return f"https://github.com/{m.group(1)}/{kind}/{m.group(3)}"
    return f"notification:{n.get('id')}"


def _fetch_approvals(state, runner, timeout, now):
    scope = "notifications"
    notif = state["notifications"]
    prev = state["sources"].get("approvals")
    if prev and prev.get("status") == "ok" and notif.get("next_poll_at") and now < _dt(notif["next_poll_at"]):
        return dict(prev, cached=True, detail="within X-Poll-Interval; not polled"), []   # checked_at preserved
    first = not notif.get("last_modified")
    path = "notifications?all=true&per_page=50" if first else "notifications?per_page=50"
    data, pages, truncated, first_res = [], 0, False, None
    while True:
        argv = ["gh", "api", "-i", "-H", f"X-GitHub-Api-Version: {API_VERSION}"]
        if pages == 0 and notif.get("last_modified"):
            argv += ["-H", f"If-Modified-Since: {notif['last_modified']}"]
        argv.append(path)
        res = runner(argv, timeout)
        pages += 1
        if first_res is None:
            first_res = res

            def schedule_next(res=res):
                raw = _header(res.stdout, "X-Poll-Interval")
                p = int(raw) if raw and raw.isdigit() else DEFAULT_POLL
                notif["poll_interval"] = p
                notif["next_poll_at"] = _iso(now + timedelta(seconds=p))

            if _status_of(res) == 304:   # gh exits non-zero on 304; it means "nothing changed", not failure
                schedule_next()
                return _row("approvals", "ok", "304 not modified", scope, http_status=304), []
        if res.rc != 0:
            row = _fail("approvals", res, scope)
            if row["status"] == "uncovered":
                row["detail"] += " (notifications API refuses fine-grained/App tokens)"
            return row, None
        page = _json(_body(res.stdout))
        if not isinstance(page, list):
            return _row("approvals", "error", "unparseable notifications response", scope), None
        data += page
        m = _NEXT_RE.search(_header(res.stdout, "Link") or "")
        if not m:
            break
        if pages >= MAX_PAGES:
            truncated = True
            break
        path = m.group(1)
    lm = _header(first_res.stdout, "Last-Modified")
    if lm:
        notif["last_modified"] = lm
    schedule_next()
    items, signals = [], []
    for n in data:
        if isinstance(n, dict) and n.get("reason") in APPROVAL_REASONS:
            url = _notif_url(n)
            signals.append({"id": n.get("id"), "reason": n["reason"], "unread": n.get("unread"), "url": url})
            items.append({"url": url, "title": (n.get("subject") or {}).get("title", ""),
                          "created_at": n.get("updated_at", ""), "kind": "approval", "notif_id": n.get("id")})
    notif["signals"] = signals
    notif["listing_ids"] = [x["id"] for x in signals]
    notif["listing_at"] = _iso(now)
    detail = f"HTTP 200; {len(items)} approval/review notification(s); {pages} page(s)"
    extra = {}
    if truncated:
        detail += f"; truncated at {MAX_PAGES} pages"
        extra["truncated"] = True
    return _row("approvals", "ok", detail, scope, http_status=200, **extra), items


def _me(runner, timeout):
    res = runner(["gh", "api", "user"], timeout)
    d = _json(res.stdout) if res.rc == 0 else None
    login = d.get("login") if isinstance(d, dict) else None
    if not login:
        raise RuntimeError("cannot resolve the operator's login (gh api user)")
    return login


# ------------------------------------------------------------------ marker read-back ---------------------

def _read(adapter, ref):
    """(live parsed markers, raw comments) of one issue."""
    comments = adapter.comments(ref)
    trust = de.trust_of(adapter)
    out = []
    for c in comments:
        out += [e for e in de._markers_in(c, trust) if e.event != "unparsed"]
    return out, comments


def _markers(adapter, ref):
    return _read(adapter, ref)[0]


_ACTIVITY_NODES = ("BlockingAddedEvent", "BlockedByAddedEvent", "ReopenedEvent")


def _is_marker_comment(c, trust):
    """A comment that carries only authoritative markers (a FOREIGN marker is just a human comment)."""
    ms = de._markers_in(c, trust)
    return bool(ms) and not any(e.detail == "foreign_author" for e in ms)


def _resolved_holds(adapter, ref, marks, comments):
    """True iff a `resolved` marker exists AND nothing new happened after the latest one: no later blocked
    episode, no reopen, no human (non-marker) comment. New activity un-suppresses the request."""
    stamps = [_dt(e.at) for e in marks if e.event == "resolved" and e.target == ref]
    if not stamps:
        return False
    rt = max(stamps)
    trust = de.trust_of(adapter)
    for c in comments:
        if isinstance(c, dict) and c.get("createdAt") and _dt(c["createdAt"]) > rt and not _is_marker_comment(c, trust):
            return False
    for n in adapter.timeline(ref):
        if isinstance(n, dict) and n.get("__typename") in _ACTIVITY_NODES and n.get("createdAt") \
                and _dt(n["createdAt"]) > rt:
            return False
    return True


def _schedule_view(markers, ref):
    sched = sorted((e for e in markers if e.event in ("scheduled", "rescheduled") and e.target == ref),
                   key=lambda e: _dt(e.at))
    if not sched:
        return None
    firsts = [e for e in sched if e.event == "scheduled"] or sched
    # a structured `original_target` on a rescheduled marker wins; the first `scheduled` marker is the fallback
    structured = [e.original_target for e in sched if e.original_target]
    return {"target": sched[-1].when, "original_target": structured[0] if structured else firsts[0].when,
            "count": len(sched)}


def _blockers(ref, adapter, runner, timeout, me, operators):
    """Open issues owned by another operator that `ref` is blocking: [{url, ref, owner, created_at}]."""
    owner_repo, num = ref.split("#")
    res = runner(["gh", "issue", "view", num, "-R", owner_repo, "--json", "state,blocking"], timeout)
    if res.rc != 0:
        raise RuntimeError(f"cannot read blocking relations of {ref}")
    data = _json(res.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("blocking", []), list):
        raise RuntimeError(f"unparseable blocking relations of {ref}")
    found = []
    for b in data.get("blocking") or []:
        url = b.get("url") if isinstance(b, dict) else None
        bref = ref_of(url)
        if not bref:
            continue
        r2 = runner(["gh", "issue", "view", url, "--json", "state,assignees,url"], timeout)
        d2 = _json(r2.stdout) if r2.rc == 0 else None
        if not isinstance(d2, dict):
            raise RuntimeError(f"cannot read blocked issue {url}")
        if str(d2.get("state", "")).upper() != "OPEN":
            continue
        owners = [a.get("login") for a in d2.get("assignees") or [] if isinstance(a, dict)]
        other = [o for o in owners if o in operators and o != me]
        if other:
            found.append({"url": url, "ref": bref, "owner": other[0], "created_at": None})
    if found:
        created = {}
        for n in adapter.timeline(ref):
            if isinstance(n, dict) and n.get("__typename") == "BlockingAddedEvent":
                r = de._ref(n.get("blockedIssue"))
                if r and n.get("createdAt"):
                    created[r] = max(created.get(r, ""), n["createdAt"])
        for f in found:
            f["created_at"] = created.get(f["ref"])
    return found


def _blocker_key(ref, b):
    raw = json.dumps([ref, b["ref"], b["created_at"]], ensure_ascii=True)
    return "inbox-blocker-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


# ------------------------------------------------------------------ scan / check -----------------------

def _policy_covers(policy, failed, scope):
    """The degraded policy covers `failed` iff it names every failed source (subset rule), matches the scope
    and carries a valid max_age_min (which bounds the covering check's staleness, see `_max_age`)."""
    if not isinstance(policy, dict):
        return False
    srcs = policy.get("sources")
    try:
        max_age = float(policy.get("max_age_min"))
    except (TypeError, ValueError):
        return False
    return (isinstance(srcs, list) and set(failed) <= set(srcs) and policy.get("scope") == scope
            and 0 <= max_age)


def _max_age(policy):
    """max_age_min of a well-formed policy, else the default."""
    try:
        v = float(policy.get("max_age_min")) if isinstance(policy, dict) else DEFAULT_MAX_AGE_MIN
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_MIN
    return v if v >= 0 else DEFAULT_MAX_AGE_MIN


def _staleness(state, policy, now):
    """(allowed, decision_needed) of a persisted state re-evaluated at `now`: a stale covering check never allows."""
    allowed, needed = state.get("dispatch_allowed"), state.get("decision_needed")
    covered = state.get("last_covered_check_at")
    if allowed and (not covered or now - _dt(covered) > timedelta(minutes=_max_age(policy))):
        return False, STALE_TEXT
    return bool(allowed), needed


def _escalations(pending, emitted=()):
    """Escalate items derived from the persisted blocked items; they stay until `resolve` removes the request."""
    out = []
    for url, it in sorted(pending.items()):
        for b in it.get("blocked") or []:
            out.append({"type": "escalate", "request": url,
                        "action": f"resolve or unblock {it.get('ref') or url} for {b['owner']}",
                        "affected": {"url": b["url"], "owner": b["owner"]},
                        "evidence": [url, b["url"]], "event_emitted": (url, b["url"]) in emitted})
    return out


def _pr_closed(runner, timeout, url):
    res = runner(["gh", "pr", "view", url, "--json", "state"], timeout)
    d = _json(res.stdout) if res.rc == 0 else None
    return isinstance(d, dict) and str(d.get("state", "")).upper() in ("MERGED", "CLOSED")


def _run(project_dir, runner, timeout, queue_status, clock, record):
    if queue_status not in QUEUE_STATUSES:
        raise ValueError(f"queue_status must be one of {QUEUE_STATUSES}")
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    manifest, cfg = load_scope(project_dir)
    if cfg is None:
        raise ValueError("no github_scope configured in .claude/foundry-project.json")
    by = _me(runner, timeout)            # precondition, resolved ONCE up front: fails before anything is written
    state = load_state(project_dir)
    scope = cfg["owner"]
    adapter = RunnerAdapter(runner, timeout)
    operators = cfg.get("operators") if isinstance(cfg.get("operators"), dict) else {}

    fetched = {
        "assigned": _search("assigned", ["issues", "--assignee", "@me", "--label", "request"], cfg, timeout, runner),
        "mentions": _search("mentions", ["issues", "--mentions", "@me"], cfg, timeout, runner),
        "review_requests": _search("review_requests", ["prs", "--review-requested", "@me"], cfg, timeout, runner),
        "approvals": _fetch_approvals(state, runner, timeout, now),
    }
    rows, pending = {}, state["pending"]
    for name in SOURCES:
        row, items = fetched[name]
        row = dict(row)
        if not row.get("cached"):
            row["checked_at"] = _iso(now)    # a cached-within-interval row keeps its original checked_at
        rows[name] = row
        if items is None:                        # failed: keep the items, mark them stale below
            continue
        returned = {i["url"] for i in items}
        for it in items:
            cur = pending.setdefault(it["url"], dict(it, sources=[]))
            cur["title"] = it["title"] or cur.get("title", "")
            if name not in cur["sources"]:
                cur["sources"].append(name)
        if name != "approvals":                  # approvals is add-only (read state is irrelevant)
            for url, cur in list(pending.items()):
                if name in cur["sources"] and url not in returned:
                    cur["sources"].remove(name)
                    if not cur["sources"]:
                        del pending[url]

    # ---- approvals pruning (R6) -----------------------------------------------------------------------
    notif = state["notifications"]
    fresh = notif.get("listing_at") == _iso(now)
    gone = set(notif.get("listing_ids") or []) if fresh else None
    for url, it in list(pending.items()):
        if it["kind"] != "approval":
            continue
        it.setdefault("ref", ref_of(url))
        if url.startswith("notification:"):
            if gone is not None and it.get("notif_id") not in gone:
                del pending[url]
        elif "/pull/" in url and _pr_closed(runner, timeout, url):
            del pending[url]

    # ---- read back durable markers, detect blockers --------------------------------------------------
    events, planned, emitted, held = [], [], set(), {}
    for url, it in list(pending.items()):
        ref = ref_of(url)
        it["ref"] = ref
        if not ref:
            continue
        try:
            marks, comments = _read(adapter, ref)
            if _resolved_holds(adapter, ref, marks, comments):
                del pending[url]
                continue
            blockers = []
            if it["kind"] == "request":
                blockers = _blockers(ref, adapter, runner, timeout, by, operators)
            held[url] = (marks, blockers)
            it["blocked"] = [{"url": b["url"], "owner": b["owner"]} for b in blockers]   # persisted
        except RuntimeError as exc:
            for s in it["sources"]:
                rows[s].update(status="error", detail=f"read-back failed for {url}: {exc}"[:240])
            held.pop(url, None)

    failed = [n for n in SOURCES if rows[n]["status"] != "ok"]
    for it in pending.values():
        it["stale"] = bool(set(it["sources"]) & set(failed))

    # ---- queue positions among schedulable, non-blocker items ---------------------------------------
    queue = sorted((u for u, it in pending.items() if it["kind"] != "mention" and u in held and not held[u][1]),
                   key=lambda u: (pending[u].get("created_at") or "", u))
    position = {u: i + 1 for i, u in enumerate(queue)}
    default_target = str(cfg.get("default_target") or DEFAULT_TARGET)

    for url, (marks, blockers) in held.items():
        it, ref = pending[url], pending[url]["ref"]
        it["acked"] = any(e.event == "ack" and e.target == ref for e in marks)
        it["blocker"] = bool(blockers)
        try:
            for b in blockers:
                key = _blocker_key(ref, b)
                if any(e.idempotency_key == key for e in marks):
                    continue        # already recorded: no new event (the escalation is still surfaced)
                at = max(now, _dt(b["created_at"])) if b["created_at"] else now
                payload = {"target": b["ref"], "blocking": b["ref"], "by": by, "key": key, "at": _iso(at),
                           "evidence": [url, b["url"]],
                           "note": f"blocker_ack: {ref} blocks {b['ref']} (owner {b['owner']})"}
                if record:
                    ev = de.emit(ref, "blocker_ack", payload, adapter=adapter, clock=lambda: now)
                    events.append(ev.as_dict())
                    emitted.add((url, b["url"]))
                else:
                    planned.append({"issue": ref, "event": "blocker_ack", "target": b["ref"]})
            if it["kind"] != "mention" and not blockers:
                view = _schedule_view(marks, ref)
                if view is None:
                    if record:
                        ev = de.emit(ref, "scheduled", {"target": ref, "when": default_target, "by": by,
                                                        "note": f"scheduled for {default_target}, queue "
                                                                f"position {position[url]}"},
                                     adapter=adapter, clock=lambda: now)
                        events.append(ev.as_dict())
                    else:
                        planned.append({"issue": ref, "event": "scheduled", "target": default_target})
                    view = {"target": default_target, "original_target": default_target, "count": 1}
                it["scheduled"] = {"target": view["target"], "original_target": view["original_target"],
                                   "position": position[url]}
        except (RuntimeError, ValueError) as exc:
            for s in it["sources"]:
                rows[s].update(status="error", detail=f"event write failed for {url}: {exc}"[:240])
            it["stale"] = True

    # ---- coverage + dispatch decision ----------------------------------------------------------------
    failed = [n for n in SOURCES if rows[n]["status"] != "ok"]
    coverage = "full" if not failed else "partial"
    last_full = _iso(now) if coverage == "full" else state.get("last_full_check_at")
    policy = cfg.get("degraded_policy")
    allowed, needed = False, None
    if queue_status != "ok":
        needed = f"delivery queue is {queue_status}; decide before dispatching"
    elif coverage == "full":
        allowed = True
    elif _policy_covers(policy, failed, scope):
        allowed = True
    else:
        needed = (f"required source(s) {', '.join(failed)} not fully covered and github_scope.degraded_policy "
                  f"does not cover them for scope {scope}; decide whether to dispatch")
    covered = coverage == "full" or _policy_covers(policy, failed, scope)
    state.update(last_covered_check_at=_iso(now) if covered else state.get("last_covered_check_at"))
    state.update(checked_at=_iso(now), scope=scope, coverage=coverage, last_full_check_at=last_full,
                 sources=rows, dispatch_allowed=allowed, decision_needed=needed, queue_status=queue_status)
    save_state(project_dir, state)
    return {"coverage": coverage, "scope": scope, "sources": rows, "last_full_check_at": last_full,
            "dispatch_allowed": allowed, "decision_needed": needed, "queue_status": queue_status,
            "pending": sorted(pending.values(), key=lambda i: i["url"]),
            "escalate": _escalations(pending, emitted), "events": events, "planned_events": planned,
            "notifications": state["notifications"]}


def scan(project_dir, runner=default_runner, timeout=DEFAULT_TIMEOUT, queue_status="ok", clock=None):
    """READ-ONLY inbox scan: writes only the gitignored runtime file, never a GitHub comment."""
    return _run(project_dir, runner, timeout, queue_status, clock, record=False)


def check(project_dir, runner=default_runner, timeout=DEFAULT_TIMEOUT, queue_status="ok", clock=None):
    """scan + record the `blocker_ack` / `scheduled` events as marker comments (tier ask)."""
    return _run(project_dir, runner, timeout, queue_status, clock, record=True)


# ------------------------------------------------------------------ ack / schedule / resolve -------------

def _actor(runner, timeout, by):
    return by or _me(runner, timeout)


def _ref_or_raise(url):
    ref = ref_of(url)
    if not ref:
        raise ValueError(f"{url!r} is not an issue or pull-request URL; no GitHub record to write to")
    return ref


def ack(project_dir, url, runner=default_runner, timeout=DEFAULT_TIMEOUT, by=None, note=None, clock=None):
    ref = _ref_or_raise(url)
    adapter = RunnerAdapter(runner, timeout)
    ev = de.emit(ref, "ack", {"target": ref, "by": _actor(runner, timeout, by), "note": note},
                 adapter=adapter, clock=clock)
    state = load_state(project_dir)
    if url in state["pending"]:
        state["pending"][url]["acked"] = True
        save_state(project_dir, state)
    return ev.as_dict()


def schedule(project_dir, url, target, runner=default_runner, timeout=DEFAULT_TIMEOUT, by=None, clock=None):
    if not isinstance(target, str) or not target.strip():
        raise ValueError("a target time or iteration is required")
    ref = _ref_or_raise(url)
    adapter = RunnerAdapter(runner, timeout)
    actor = _actor(runner, timeout, by)
    view = _schedule_view(_markers(adapter, ref), ref)
    state = load_state(project_dir)
    item = state["pending"].get(url)
    if view is None:
        ev = de.emit(ref, "scheduled", {"target": ref, "when": target, "by": actor}, adapter=adapter, clock=clock)
        original = target
    elif view["target"] == target:
        return {"event": None, "unchanged": True, "target": target, "original_target": view["original_target"]}
    else:
        original = view["original_target"]
        key = "inbox-resched-" + hashlib.sha256(
            json.dumps([ref, target, view["count"]]).encode()).hexdigest()[:20]
        ev = de.emit(ref, "rescheduled", {"target": ref, "when": target, "by": actor, "key": key,
                                          "original_target": original,
                                          "note": f"rescheduled from {view['target']} to {target} "
                                                  f"(original target {original})"},
                     adapter=adapter, clock=clock)
    if item is not None:
        item["scheduled"] = dict(item.get("scheduled") or {}, target=target, original_target=original)
        save_state(project_dir, state)
    out = ev.as_dict()
    out["original_target"] = original
    return out


def resolve(project_dir, url, evidence, runner=default_runner, timeout=DEFAULT_TIMEOUT, by=None, clock=None):
    ev_urls = list(evidence or [])
    if not ev_urls or not all(isinstance(e, str) and _EVIDENCE_RE.match(e) for e in ev_urls):
        raise ValueError("resolve needs at least one https evidence URL with a path (issue / PR / commit) "
                         "showing the dependency is satisfied; a bare host is not evidence")
    ref = _ref_or_raise(url)
    actor = _actor(runner, timeout, by)   # precondition: resolved before anything is emitted
    ev = de.emit(ref, "resolved", {"target": ref, "by": actor, "evidence": ev_urls},
                 adapter=RunnerAdapter(runner, timeout), clock=clock)
    state = load_state(project_dir)
    if state["pending"].pop(url, None) is not None:
        save_state(project_dir, state)
    return ev.as_dict()


# ------------------------------------------------------------------ CLI ------------------------------

def _render(cmd, res):
    if cmd in ("check", "scan"):
        lines = [f"coverage: {res['coverage']}  dispatch_allowed: {str(res['dispatch_allowed']).lower()}"]
        lines += [f"  {n}: {r['status']} ({r['scope']})" for n, r in res["sources"].items()]
        lines += [f"  escalate: {e['action']} -> {e['affected']['url']}" for e in res["escalate"]]
        lines += [f"  planned: {p['event']} on {p['issue']}" for p in res.get("planned_events", [])]
        lines.append(f"  pending: {len(res['pending'])}")
        if res["decision_needed"]:
            lines.append(f"  decision needed: {res['decision_needed']}")
        return "\n".join(lines)
    if cmd == "pending":
        lines = [f"{i['kind']:<9} {i['url']}{' [stale]' if i.get('stale') else ''}" for i in res["pending"]]
        lines += [f"escalate: {e['action']} -> {e['affected']['url']}" for e in res.get("escalate", [])]
        return "\n".join(lines) or "no pending requests"
    return json.dumps(res, indent=2)


def main(argv=None, runner=default_runner, clock=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("scan", "check"):
        c = sub.add_parser(nm)
        c.add_argument("--queue-status", choices=QUEUE_STATUSES, default="ok")
    sub.add_parser("pending")
    for name in ("ack", "schedule", "resolve"):
        p = sub.add_parser(name)
        p.add_argument("url")
        p.add_argument("--by")
        if name == "ack":
            p.add_argument("--note")
        if name == "schedule":
            p.add_argument("--target", required=True)
        if name == "resolve":
            p.add_argument("--evidence", action="append", default=[])
    # allow --json / --project-dir after the subcommand too
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true", dest="json_sub")
        sp.add_argument("--project-dir", dest="project_dir_sub")
    a = ap.parse_args(argv)
    pd = a.project_dir_sub or a.project_dir
    as_json = a.json or a.json_sub
    refused = _ghc.gate(pd, f"foundry_inbox.py {a.cmd}")  # before any gh call/write
    if refused is not None:
        return refused
    try:
        if a.cmd in ("scan", "check"):
            res = (scan if a.cmd == "scan" else check)(pd, runner, a.timeout, a.queue_status, clock)
        elif a.cmd == "pending":
            st = load_state(pd)
            _, cfg = load_scope(pd)
            now = (clock or (lambda: datetime.now(timezone.utc)))()
            allowed, needed = _staleness(st, (cfg or {}).get("degraded_policy"), now)
            res = {"pending": sorted(st["pending"].values(), key=lambda i: i["url"]),
                   "coverage": st["coverage"], "dispatch_allowed": allowed, "decision_needed": needed,
                   "last_full_check_at": st["last_full_check_at"],
                   "escalate": _escalations(st["pending"])}
        elif a.cmd == "ack":
            res = ack(pd, a.url, runner, a.timeout, a.by, a.note, clock)
        elif a.cmd == "schedule":
            res = schedule(pd, a.url, a.target, runner, a.timeout, a.by, clock)
        else:
            res = resolve(pd, a.url, a.evidence, runner, a.timeout, a.by, clock)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if as_json else _render(a.cmd, res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
