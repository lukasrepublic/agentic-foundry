#!/usr/bin/env python3
"""foundry_inbox.py — discover, persist, classify and escalate the operator's GitHub requests (gn-09).

Durable sources, each probed and reported separately (status ok|uncovered|error + scope):
  assigned         `request`-labelled open issues assigned to @me         (kind: request)
  mentions         open issues that mention @me                           (kind: mention)
  review_requests  open PRs with a review requested from @me              (kind: review)
  approvals        the notifications API, reasons approval_requested /    (kind: approval)
                   review_requested; polled with If-Modified-Since, 304 = "no change", never faster
                   than X-Poll-Interval. Its items are add-only: a request stays pending whatever the
                   notification read state, until it is resolved.

Pending requests persist in `<workspace>/.foundry/runtime/inbox.json` (the directory carries its own
`.gitignore` of `*`; atomic temp+rename writes). A request that `blocks` an open issue owned by another
operator gets a `blocker_ack` and one `escalate` item; any other request gets a `scheduled` event with a
target and queue position. Events go through the gn-04 writer (`foundry_deck_events.emit`); already-recorded
markers on the request issue are read back first, so a lost runtime file causes no duplicate event.

Every subprocess goes through ONE injectable `runner(argv, timeout) -> Result` (tests stub it).

  foundry_inbox.py check [--queue-status ok|stale|error] | pending | ack URL | schedule URL --target T
                   | resolve URL --evidence https://...        (all accept --project-dir DIR and --json)
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
from foundry_deck_events import GhAdapter, _dt, _iso  # noqa: E402
from foundry_github_capabilities import (  # noqa: E402
    API_VERSION, DEFAULT_TIMEOUT, _body, _fail, _header, _json, _row, _status_of, default_runner, load_scope,
)

SOURCES = ("assigned", "mentions", "review_requests", "approvals")
KINDS = {"assigned": "request", "mentions": "mention", "review_requests": "review", "approvals": "approval"}
APPROVAL_REASONS = ("approval_requested", "review_requested")
DEFAULT_POLL = 60
DEFAULT_TARGET = "next-iteration"
QUEUE_STATUSES = ("ok", "stale", "error")
RUNTIME_REL = os.path.join(".foundry", "runtime", "inbox.json")
SCHEMA = 1

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
            "last_full_check_at": None, "sources": {}, "notifications": {}, "pending": {},
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
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("*\n")
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
        return dict(prev, cached=True, detail="within X-Poll-Interval; not polled"), []
    argv = ["gh", "api", "-i", "-H", f"X-GitHub-Api-Version: {API_VERSION}"]
    if notif.get("last_modified"):
        argv += ["-H", f"If-Modified-Since: {notif['last_modified']}"]
    argv.append("notifications?per_page=50")
    res = runner(argv, timeout)
    poll = DEFAULT_POLL

    def schedule_next():
        raw = _header(res.stdout, "X-Poll-Interval")
        p = int(raw) if raw and raw.isdigit() else DEFAULT_POLL
        notif["poll_interval"] = p
        notif["next_poll_at"] = _iso(now + timedelta(seconds=p))

    if _status_of(res) == 304:      # gh exits non-zero on 304; it means "nothing changed", not failure
        schedule_next()
        return _row("approvals", "ok", "304 not modified", scope, http_status=304), []
    if res.rc != 0:
        row = _fail("approvals", res, scope)
        if row["status"] == "uncovered":
            row["detail"] += " (notifications API refuses fine-grained/App tokens)"
        return row, None
    data = _json(_body(res.stdout))
    if not isinstance(data, list):
        return _row("approvals", "error", "unparseable notifications response", scope), None
    lm = _header(res.stdout, "Last-Modified")
    if lm:
        notif["last_modified"] = lm
    schedule_next()
    items, signals = [], []
    for n in data:
        if isinstance(n, dict) and n.get("reason") in APPROVAL_REASONS:
            url = _notif_url(n)
            signals.append({"id": n.get("id"), "reason": n["reason"], "unread": n.get("unread"), "url": url})
            items.append({"url": url, "title": (n.get("subject") or {}).get("title", ""),
                          "created_at": n.get("updated_at", ""), "kind": "approval"})
    notif["signals"] = signals
    return _row("approvals", "ok", f"HTTP 200; {len(items)} approval/review notification(s)", scope,
                http_status=200), items


def _me(runner, timeout):
    res = runner(["gh", "api", "user"], timeout)
    d = _json(res.stdout) if res.rc == 0 else None
    login = d.get("login") if isinstance(d, dict) else None
    if not login:
        raise RuntimeError("cannot resolve the operator's login (gh api user)")
    return login


# ------------------------------------------------------------------ marker read-back ---------------------

def _markers(adapter, ref):
    out = []
    for c in adapter.comments(ref):
        out += [e for e in de._markers_in(c) if e.event != "unparsed"]
    return out


def _schedule_view(markers, ref):
    sched = sorted((e for e in markers if e.event in ("scheduled", "rescheduled") and e.target == ref),
                   key=lambda e: _dt(e.at))
    if not sched:
        return None
    firsts = [e for e in sched if e.event == "scheduled"] or sched
    return {"target": sched[-1].when, "original_target": firsts[0].when, "count": len(sched)}


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


# ------------------------------------------------------------------ check ------------------------------

def _policy_covers(policy, failed, scope, last_full, now):
    if not isinstance(policy, dict) or not last_full:
        return False
    srcs = policy.get("sources")
    try:
        max_age = float(policy.get("max_age_min"))
    except (TypeError, ValueError):
        return False
    return (isinstance(srcs, list) and set(failed) <= set(srcs) and policy.get("scope") == scope
            and now - _dt(last_full) <= timedelta(minutes=max_age))


def check(project_dir, runner=default_runner, timeout=DEFAULT_TIMEOUT, queue_status="ok", clock=None):
    """Run one inbox check. Returns the result dict (also persisted, minus escalations, to inbox.json)."""
    if queue_status not in QUEUE_STATUSES:
        raise ValueError(f"queue_status must be one of {QUEUE_STATUSES}")
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    manifest, cfg = load_scope(project_dir)
    if cfg is None:
        raise ValueError("no github_scope configured in .claude/foundry-project.json")
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
        row = dict(row, checked_at=_iso(now))
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

    # ---- read back durable markers, detect blockers --------------------------------------------------
    events, escalate, ctx = [], [], {}
    me = None
    for url, it in list(pending.items()):
        ref = ref_of(url)
        it["ref"] = ref
        if not ref:
            continue
        try:
            marks = _markers(adapter, ref)
            if any(e.event == "resolved" and e.target == ref for e in marks):
                del pending[url]
                continue
            blockers = []
            if it["kind"] == "request":
                me = me or _me(runner, timeout)
                blockers = _blockers(ref, adapter, runner, timeout, me, operators)
            ctx[url] = (marks, blockers)
        except RuntimeError as exc:
            for s in it["sources"]:
                rows[s].update(status="error", detail=f"read-back failed for {url}: {exc}"[:240])
            ctx.pop(url, None)

    failed = [n for n in SOURCES if rows[n]["status"] != "ok"]
    for it in pending.values():
        it["stale"] = bool(set(it["sources"]) & set(failed))

    # ---- queue positions among schedulable, non-blocker items ---------------------------------------
    queue = sorted((u for u, it in pending.items() if it["kind"] != "mention" and u in ctx and not ctx[u][1]),
                   key=lambda u: (pending[u].get("created_at") or "", u))
    position = {u: i + 1 for i, u in enumerate(queue)}
    default_target = str(cfg.get("default_target") or DEFAULT_TARGET)
    by = me

    for url, (marks, blockers) in ctx.items():
        it, ref = pending[url], pending[url]["ref"]
        by = by or _me(runner, timeout)
        it["acked"] = any(e.event == "ack" and e.target == ref for e in marks)
        it["blocker"] = bool(blockers)
        it["blocked"] = [{"url": b["url"], "owner": b["owner"]} for b in blockers]
        try:
            for b in blockers:
                key = _blocker_key(ref, b)
                if any(e.idempotency_key == key for e in marks):
                    continue
                at = max(now, _dt(b["created_at"])) if b["created_at"] else now
                ev = de.emit(ref, "blocker_ack", {"target": b["ref"], "blocking": b["ref"], "by": by, "key": key,
                                                  "at": _iso(at), "evidence": [url, b["url"]],
                                                  "note": f"blocker_ack: {ref} blocks {b['ref']} (owner {b['owner']})"},
                             adapter=adapter, clock=lambda: now)
                events.append(ev.as_dict())
                escalate.append({"type": "escalate", "request": url,
                                 "action": f"resolve or unblock {ref} for {b['owner']}",
                                 "affected": {"url": b["url"], "owner": b["owner"]},
                                 "evidence": [url, b["url"]]})
            if it["kind"] != "mention" and not blockers:
                view = _schedule_view(marks, ref)
                if view is None:
                    ev = de.emit(ref, "scheduled", {"target": ref, "when": default_target, "by": by,
                                                    "note": f"scheduled for {default_target}, queue position "
                                                            f"{position[url]}"},
                                 adapter=adapter, clock=lambda: now)
                    events.append(ev.as_dict())
                    view = {"target": default_target, "original_target": default_target, "count": 1}
                it["scheduled"] = {"target": view["target"], "original_target": view["original_target"],
                                   "position": position[url]}
        except (RuntimeError, ValueError) as exc:
            for s in it["sources"]:
                rows[s].update(status="error", detail=f"event write failed for {url}: {exc}"[:240])
            failed = [n for n in SOURCES if rows[n]["status"] != "ok"]
            it["stale"] = True

    # ---- coverage + dispatch decision ----------------------------------------------------------------
    failed = [n for n in SOURCES if rows[n]["status"] != "ok"]
    coverage = "full" if not failed else "partial"
    prev_full = state.get("last_full_check_at")
    last_full = _iso(now) if coverage == "full" else prev_full
    allowed, needed = False, None
    if queue_status != "ok":
        needed = f"delivery queue is {queue_status}; decide before dispatching"
    elif coverage == "full":
        allowed = True
    elif _policy_covers(cfg.get("degraded_policy"), failed, scope, prev_full, now):
        allowed = True
    else:
        needed = (f"required source(s) {', '.join(failed)} not fully covered and github_scope.degraded_policy "
                  f"does not cover them for scope {scope} within max_age_min; decide whether to dispatch")
    state.update(checked_at=_iso(now), scope=scope, coverage=coverage, last_full_check_at=last_full,
                 sources=rows, dispatch_allowed=allowed, decision_needed=needed, queue_status=queue_status)
    save_state(project_dir, state)
    return {"coverage": coverage, "scope": scope, "sources": rows, "last_full_check_at": last_full,
            "dispatch_allowed": allowed, "decision_needed": needed, "queue_status": queue_status,
            "pending": sorted(pending.values(), key=lambda i: i["url"]), "escalate": escalate, "events": events,
            "notifications": state["notifications"]}


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
    if not ev_urls or not all(isinstance(e, str) and re.match(r"^https://[^\s/]+\S*$", e) for e in ev_urls):
        raise ValueError("resolve needs at least one accessible https evidence URL showing the dependency is "
                         "satisfied")
    ref = _ref_or_raise(url)
    ev = de.emit(ref, "resolved", {"target": ref, "by": _actor(runner, timeout, by), "evidence": ev_urls},
                 adapter=RunnerAdapter(runner, timeout), clock=clock)
    state = load_state(project_dir)
    if state["pending"].pop(url, None) is not None:
        save_state(project_dir, state)
    return ev.as_dict()


# ------------------------------------------------------------------ CLI ------------------------------

def _render(cmd, res):
    if cmd == "check":
        lines = [f"coverage: {res['coverage']}  dispatch_allowed: {str(res['dispatch_allowed']).lower()}"]
        lines += [f"  {n}: {r['status']} ({r['scope']})" for n, r in res["sources"].items()]
        lines += [f"  escalate: {e['action']} -> {e['affected']['url']}" for e in res["escalate"]]
        lines.append(f"  pending: {len(res['pending'])}")
        if res["decision_needed"]:
            lines.append(f"  decision needed: {res['decision_needed']}")
        return "\n".join(lines)
    if cmd == "pending":
        return "\n".join(f"{i['kind']:<9} {i['url']}{' [stale]' if i.get('stale') else ''}" for i in res["pending"]) \
            or "no pending requests"
    return json.dumps(res, indent=2)


def main(argv=None, runner=default_runner, clock=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
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
    try:
        if a.cmd == "check":
            res = check(pd, runner, a.timeout, a.queue_status, clock)
        elif a.cmd == "pending":
            st = load_state(pd)
            res = {"pending": sorted(st["pending"].values(), key=lambda i: i["url"]),
                   "coverage": st["coverage"], "dispatch_allowed": st["dispatch_allowed"],
                   "last_full_check_at": st["last_full_check_at"]}
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
