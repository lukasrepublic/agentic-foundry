"""foundry_deck_events — the deck's canonical workflow-event vocabulary and its GitHub records (gn-04).

One vocabulary (`EVENTS`), the durable GitHub record that establishes each (`MAPPING`), an idempotent
writer (`emit`) for the events native history lacks, and a pure reader (`parse`) that rebuilds history
as of a cutoff from already-fetched GraphQL `timelineItems` nodes and issue comments.

Design decisions (charter gn-04):
  * Event time is GitHub's `createdAt` (timeline node or comment), never the marker's self-declared `at`.
  * Native-only events (ready, blocked, reopened, withdrawn, cancelled, duplicate) cannot be `emit`ted.
  * A `completed` close is a `Closure`, not an acceptance (native close never marks a decision accepted).
  * A `blocker_ack` is valid per episode: only if a `blocked` record for its target exists at or before
    it (i.e. it is at/after the MOST RECENT `blocked` as of its own time). A later re-block opens a new
    episode the earlier ack does not cover: the target is listed in `unacknowledged` again. An ordinary
    `ack` never satisfies a blocker acknowledgement.
  * Two native records open a blocked episode, distinguished by `Event.relation`:
    `BlockedByAddedEvent` (on the BLOCKED issue; relation "blocked_by") and `BlockingAddedEvent` (on the
    BLOCKER issue, e.g. an operator-inbox request issue; relation "blocking"). Both record a `blocked`
    event whose target = blocking = the OTHER issue, and both close on their `...RemovedEvent`. The
    vocabulary stays the fixed fifteen. The blocker issue is the acknowledgeable thing: an inbox posts a
    `blocker_ack` on the request with target = the blocked issue, so `unacknowledged` covers both
    relations. A `relation: "blocking"` event does NOT mean this issue is itself blocked: consumers use
    `is_blocked(history)` (relation == "blocked_by", episode still open).
  * Default idempotency keys for start/ack/accepted carry an EPOCH (count of prior withdrawn+reopened
    events on the issue) and for blocker_ack the latest `blocked` time for its (target, blocking) pair, so
    a restart after withdrawal, a re-ack after re-block, or a re-acceptance after reopen is not swallowed.
    Epoch 0 / no prior block leaves the key identical to the pre-epoch key.
  * `parse` dedupes same-key markers (first by time wins); a `>`-quoted marker is not live; `emit` treats
    a same-key marker that is hand-edited (unparsed) as present and returns it rather than re-posting.
  * AUTHORITY (security): a marker is authoritative only when its COMMENT AUTHOR is trusted (`Trust`):
    the authenticated login (`gh api user`, cached per adapter), a login listed in
    `github_scope.operators` (the keys of that object in .claude/foundry-project.json), or, ONLY when
    the scope lists no operators, the repository OWNER or a login with write/maintain/admin permission on the comment's repo (a bare MEMBER or read-level COLLABORATOR is foreign). A marker
    from anyone else is returned as `unparsed` with detail `foreign_author` — visible, never
    authoritative: it cannot resolve/accept/ack anything, cannot start work, and cannot squat an
    idempotency key (`emit` ignores it in its same-key presence check). `parse`/`_markers_in` take a
    `trust` predicate (default: OWNER only, i.e. fail-closed); `GhAdapter.trust()`
    supplies the real one and `emit`/`fetch_history` thread it. `gh issue view --json comments` already
    carries `author.login` and `authorAssociation`, so no extra query is needed.
  * Every `gh` call lives in ONE adapter object (`GhAdapter`); tests inject a stub. `parse` is pure.

Marker comment:
  <!-- foundry-deck: {"v":1,"event":..,"idempotency_key":..,"target":..,"blocking":..,"at":..,
                       "by":..,"evidence":[..]} -->  followed by one human-readable line.

CLI: `foundry_deck_events.py parse OWNER/REPO#N --as-of ISO` | `emit OWNER/REPO#N EVENT --payload JSON`.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from foundry_ticket_store import redact as _redact  # noqa: E402

MARKER_VERSION = 1

EVENTS = (
    "ready", "start", "ack", "scheduled", "rescheduled", "blocker_ack", "resolved", "review_ready",
    "accepted", "handoff", "blocked", "reopened", "withdrawn", "cancelled", "duplicate",
)

# event -> {"native": [native records that establish it], "marker": bool (a marker comment may establish it)}
MAPPING = {
    "ready": {"native": ["ProjectV2ItemStatusChangedEvent(status=Ready)", "LabeledEvent(status:ready)"], "marker": False},
    "start": {"native": ["ProjectV2ItemStatusChangedEvent(status=In progress)", "LabeledEvent(status:in-progress)"], "marker": True},
    "ack": {"native": [], "marker": True},
    "scheduled": {"native": [], "marker": True},
    "rescheduled": {"native": [], "marker": True},
    "blocker_ack": {"native": [], "marker": True},
    "resolved": {"native": [], "marker": True},
    "review_ready": {"native": [], "marker": True},
    "accepted": {"native": [], "marker": True},
    "handoff": {"native": [], "marker": True},
    "blocked": {"native": ["BlockedByAddedEvent", "BlockingAddedEvent"], "marker": False},
    "reopened": {"native": ["ReopenedEvent"], "marker": False},
    "withdrawn": {"native": ["ProjectV2ItemStatusChangedEvent(status=Backlog, after first start)",
                             "LabeledEvent(status:backlog, after first start)"], "marker": False},
    "cancelled": {"native": ["ClosedEvent(stateReason=NOT_PLANNED)"], "marker": False},
    "duplicate": {"native": ["ClosedEvent(stateReason=DUPLICATE)", "MarkedAsDuplicateEvent"], "marker": False},
}
assert tuple(MAPPING) == EVENTS

EVIDENCE_REQUIRED = frozenset({"review_ready", "handoff", "resolved", "accepted"})
# payload fields (besides issue+event) that define an event for the default idempotency key
KEY_FIELDS = {"scheduled": ("target", "when"), "rescheduled": ("target", "when"),
              "blocker_ack": ("target", "blocking"), "ack": ("target",), "start": ("target",),
              "review_ready": ("target", "evidence"), "handoff": ("target", "evidence"),
              "resolved": ("target", "evidence"), "accepted": ("target", "evidence")}
MARKER_FIELDS = ("v", "event", "idempotency_key", "target", "blocking", "at", "by", "evidence")

_MARKER_RE = re.compile(r"<!--\s*foundry-deck:\s*(.*?)\s*-->", re.DOTALL)
_MARKER_PREFIX = re.compile(r"<!--\s*foundry-deck:")
_ISSUE_RE = re.compile(r"([A-Za-z0-9_-]+)/((?!\.{1,2}#)[A-Za-z0-9_.-]+)#([0-9]+)")
_URL_RE = re.compile(r"https?://[^\s/]+\S*")
_SHA_RE = re.compile(r"(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}")


WRITE_PERMISSIONS = frozenset({"write", "maintain", "admin"})
_SEG = r"(?!\.{1,2}(?:/|$))[A-Za-z0-9_.-]+"
_COMMENT_REPO_RE = re.compile(r"^https://[^/\s]+/(" + _SEG + "/" + _SEG + r")/(?:issues|pull)/\d+")


def comment_repo(comment):
    """The `owner/name` a comment's url points into ('.'/'..' segments never match), or None."""
    m = _COMMENT_REPO_RE.match(str((comment or {}).get("url") or ""))
    return m.group(1) if m else None
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\[bot\])?$")


class Trust:
    """Predicate over one comment: is its author allowed to write authoritative deck markers?

    Fallback (ONLY when no operators are configured): the repository OWNER, or a login whose
    `repos/{repo}/collaborators/{login}/permission` is write/maintain/admin. `permission(repo, login)` is
    the injected lookup (None => no lookup possible => OWNER only); a failed lookup is untrusted; results
    are cached per Trust instance. Bare MEMBER / read-level COLLABORATOR never qualifies."""

    def __init__(self, operators=(), me=None, permission=None):
        self.logins = {str(x).lower() for x in (operators or ()) if x}
        self.me = str(me).lower() if me else None
        self.permission = permission
        self._cache = {}

    def _writes(self, repo, login) -> bool:
        if not self.permission or not repo or not _LOGIN_RE.match(login):
            return False
        key = (repo.lower(), login.lower())
        if key not in self._cache:
            try:
                self._cache[key] = str(self.permission(repo, login) or "").lower() in WRITE_PERMISSIONS
            except Exception:  # noqa: BLE001 — a failed lookup is untrusted
                self._cache[key] = False
        return self._cache[key]

    def __call__(self, comment) -> bool:
        login = ((comment.get("author") or {}).get("login") if isinstance(comment, dict) else None)
        if not isinstance(login, str) or not login:
            return False
        low = login.lower()
        if low == self.me or low in self.logins:
            return True
        if not self.logins:     # no configured operators: the repository write-permission fallback
            assoc = str(comment.get("authorAssociation") or "").upper()
            if assoc == "OWNER":
                return True
            if assoc in ("MEMBER", "COLLABORATOR"):
                repo = comment_repo(comment)
                fetched = comment.get("_fetched_repo")   # stamped by the adapter that fetched the comments
                if repo and fetched and repo.lower() != str(fetched).lower():
                    return False
                return self._writes(repo, login)
        return False


def scope_operators(project_dir=None):
    """The configured `github_scope.operators` logins (empty when absent/unreadable)."""
    try:
        with open(os.path.join(project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
                               ".claude", "foundry-project.json"), encoding="utf-8") as f:
            ops = (json.load(f).get("github_scope") or {}).get("operators")
    except (OSError, ValueError, AttributeError):
        return []
    return [k for k in ops if isinstance(k, str)] if isinstance(ops, dict) else []


def trust_of(adapter) -> "Trust":
    fn = getattr(adapter, "trust", None)
    return fn() if callable(fn) else Trust()


@dataclass
class Event:
    event: str
    at: str
    by: str | None = None
    target: str | None = None
    blocking: str | None = None
    evidence: list = field(default_factory=list)
    first: bool = False
    invalid: str | None = None
    source: str = "marker"          # marker | timeline
    comment_id: str | None = None
    author: str | None = None       # comment author (marker) — `by` is the claimed actor
    idempotency_key: str | None = None
    detail: str | None = None       # unparsed reason
    when: str | None = None
    original_target: str | None = None   # rescheduled only: the first-scheduled target (structured, optional)
    relation: str = "blocked_by"    # blocked events only: "blocked_by" (this issue is blocked) | "blocking"

    def as_dict(self):
        return asdict(self)


@dataclass
class Closure:
    at: str
    by: str | None
    state_reason: str = "completed"


@dataclass
class History:
    events: list            # ordered Event list (unparsed markers included, event == "unparsed")
    intervals: list         # [{"assignee", "start", "end"(None = open at cutoff)}]
    closures: list          # completed closes — not acceptances
    unacknowledged: list    # targets (either relation) whose latest blocked record has no valid blocker_ack
    open_blocks: dict = field(default_factory=dict)   # target -> relation of each still-open episode

    @property
    def unparsed(self):
        return [e for e in self.events if e.event == "unparsed"]


# ---- time ------------------------------------------------------------------------------------------

def _dt(value) -> datetime:
    if isinstance(value, datetime):
        d = value
    else:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no timezone")
    return d.astimezone(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- evidence / payload validation -------------------------------------------------------------------

def is_shared_reference(s) -> bool:
    return isinstance(s, str) and bool(_URL_RE.fullmatch(s) or _ISSUE_RE.fullmatch(s) or _SHA_RE.fullmatch(s))


def _check_issue(issue: str):
    if not isinstance(issue, str) or not _ISSUE_RE.fullmatch(issue):
        raise ValueError(f"issue must be 'owner/repo#n', got {issue!r}")


def _norm(s) -> str:
    return re.sub(r"[\s_-]+", " ", str(s or "")).strip().lower().replace("status: ", "status:")


def default_key(issue: str, event: str, payload: dict, epoch=None) -> str:
    fields = KEY_FIELDS.get(event, ("target",))
    body = {}
    for f in fields:
        v = payload.get(f)
        body[f] = sorted(v) if f == "evidence" and isinstance(v, list) else v
    if epoch not in (None, 0):
        body["epoch"] = epoch
    raw = json.dumps([issue, event, body], sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---- marker rendering / parsing ---------------------------------------------------------------------

def render_marker(event: Event, line: str) -> str:
    obj = {"v": MARKER_VERSION, "event": event.event, "idempotency_key": event.idempotency_key,
           "target": event.target, "blocking": event.blocking, "at": event.at, "by": event.by,
           "evidence": list(event.evidence)}
    if event.when is not None:
        obj["when"] = event.when
    if event.original_target is not None:
        obj["original_target"] = event.original_target
    # '<' and '>' only occur inside JSON strings; escaping them keeps a payload from closing or opening a comment.
    js = json.dumps(obj, ensure_ascii=True, separators=(",", ":")).replace(">", "\\u003e").replace("<", "\\u003c")
    line = " ".join(str(line).split()).replace("<!--", "").replace("-->", "")
    return f"<!-- foundry-deck: {js} -->\n{line}\n"


_KEY_RE = re.compile(r'"idempotency_key"\s*:\s*"([^"]*)"')


def _unparsed(c, why, raw=None):
    key = None
    if raw is not None:
        m = _KEY_RE.search(raw)
        key = m.group(1) if m else None
    return Event(event="unparsed", at=_iso(_dt(c["createdAt"])), source="marker", comment_id=str(c.get("id")),
                 author=((c.get("author") or {}).get("login")), detail=why, idempotency_key=key)


def _live_body(body) -> str:
    """The comment body without `>`-quoted lines (a quoted marker is not a live marker)."""
    return "\n".join(ln for ln in str(body).split("\n") if not ln.lstrip().startswith(">"))


def _markers_in(comment, trust=None) -> list:
    """Every marker in one comment, as Event (parsed) or Event('unparsed'). A comment whose author is
    not trusted yields only `unparsed` / `foreign_author` entries (never authoritative)."""
    body = _live_body(comment.get("body") or "")
    out = []
    n_prefix = len(_MARKER_PREFIX.findall(body))
    found = _MARKER_RE.findall(body)
    if n_prefix and not (trust if trust is not None else Trust())(comment):
        return ([_unparsed(comment, "foreign_author", raw) for raw in found]
                + [_unparsed(comment, "foreign_author") for _ in range(n_prefix - len(found))])
    for raw in found:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            out.append(_unparsed(comment, "malformed_json", raw))
            continue
        if not isinstance(obj, dict):
            out.append(_unparsed(comment, "not_an_object", raw))
        elif type(obj.get("v")) is not int or obj.get("v") != MARKER_VERSION:
            out.append(_unparsed(comment, "unknown_version", raw))
        elif any(f not in obj for f in MARKER_FIELDS):
            out.append(_unparsed(comment, "missing_fields", raw))
        elif obj["event"] not in EVENTS or not MAPPING[obj["event"]]["marker"]:
            out.append(_unparsed(comment, "event_not_markable", raw))
        elif not isinstance(obj["evidence"], list) or not isinstance(obj["idempotency_key"], str):
            out.append(_unparsed(comment, "bad_field_type", raw))
        else:
            out.append(Event(event=obj["event"], at=_iso(_dt(comment["createdAt"])), by=obj["by"],
                             target=obj["target"], blocking=obj["blocking"], evidence=list(obj["evidence"]),
                             source="marker", comment_id=str(comment.get("id")),
                             author=((comment.get("author") or {}).get("login")),
                             idempotency_key=obj["idempotency_key"],
                             when=obj.get("when") if isinstance(obj.get("when"), str) else None,
                             original_target=(obj["original_target"]
                                              if isinstance(obj.get("original_target"), str) else None)))
    if n_prefix > len(found):   # a prefix with no closing `-->`
        out.append(_unparsed(comment, "unterminated_marker"))
    return out


# ---- the pure reader --------------------------------------------------------------------------------

def _ref(node) -> str | None:
    if not isinstance(node, dict):
        return None
    repo = (node.get("repository") or {}).get("nameWithOwner")
    return f"{repo}#{node['number']}" if repo and node.get("number") is not None else None


def parse(timeline, comments, as_of, trust=None) -> History:
    """Rebuild history from fetched `timelineItems` nodes + comments, using only records <= as_of. Pure."""
    cutoff = _dt(as_of)
    recs = []   # (time, source_rank, index, kind, payload)
    for i, n in enumerate(timeline or []):
        if isinstance(n, dict) and n.get("createdAt") and _dt(n["createdAt"]) <= cutoff:
            recs.append((_dt(n["createdAt"]), 0, i, "tl", n))
    for i, c in enumerate(comments or []):
        if isinstance(c, dict) and c.get("createdAt") and _dt(c["createdAt"]) <= cutoff:
            for j, ev in enumerate(_markers_in(c, trust)):
                recs.append((_dt(c["createdAt"]), 1, i * 1000 + j, "mk", ev))
    recs.sort(key=lambda r: r[:3])

    events, closures = [], []
    open_iv, intervals = {}, []
    st = {"ready_open": False, "started": False, "ever_started": False, "ever_ready": False,
          "accepted_seen": False, "dup_open": False}
    blocked_open = {}   # target -> time of latest blocked record
    block_rel = {}      # target -> relation of the open episode
    seen_keys = set()

    for t, _, _, kind, rec in recs:
        if kind == "mk":
            ev = rec
            if ev.event != "unparsed" and ev.idempotency_key is not None:
                if ev.idempotency_key in seen_keys:
                    continue    # same idempotency key: the first marker by time wins
                seen_keys.add(ev.idempotency_key)
            if ev.event == "start":
                if st["started"]:
                    continue    # already started; a repeat start marker is not a new start
                _do_start(ev, st)
            elif ev.event == "accepted":
                ev.first = not st["accepted_seen"]
                st["accepted_seen"] = True
            events.append(ev)
            continue
        n = rec
        tn = n.get("__typename")
        actor = (n.get("actor") or {}).get("login")
        at = _iso(t)

        def mk(name, **kw):
            return Event(event=name, at=at, by=actor, source="timeline", **kw)

        if tn == "ProjectV2ItemStatusChangedEvent" or (tn == "LabeledEvent" and
                                                      _norm((n.get("label") or {}).get("name")).startswith("status:")):
            status = _norm(n.get("status")) if tn != "LabeledEvent" else _norm(n["label"]["name"])[len("status:"):]
            _status(status, mk, events, st)
        elif tn in ("BlockedByAddedEvent", "BlockingAddedEvent"):
            rel = "blocked_by" if tn == "BlockedByAddedEvent" else "blocking"
            target = _ref(n.get("blockingIssue" if rel == "blocked_by" else "blockedIssue"))
            events.append(mk("blocked", target=target, blocking=target, relation=rel))
            blocked_open[target] = t
            block_rel[target] = rel
        elif tn in ("BlockedByRemovedEvent", "BlockingRemovedEvent"):
            tg = _ref(n.get("blockingIssue" if tn == "BlockedByRemovedEvent" else "blockedIssue"))
            blocked_open.pop(tg, None)
            block_rel.pop(tg, None)
        elif tn == "ReopenedEvent":
            events.append(mk("reopened"))
            st["started"] = False
            st["dup_open"] = False
        elif tn == "ClosedEvent":
            reason = str(n.get("stateReason") or "COMPLETED").upper()
            if reason == "NOT_PLANNED":
                events.append(mk("cancelled"))
            elif reason == "DUPLICATE":
                if not st["dup_open"]:
                    events.append(mk("duplicate"))
                st["dup_open"] = True
            else:
                closures.append(Closure(at=at, by=actor))
        elif tn == "MarkedAsDuplicateEvent":
            if not st["dup_open"]:
                events.append(mk("duplicate"))
            st["dup_open"] = True
        elif tn == "AssignedEvent":
            who = (n.get("assignee") or {}).get("login")
            if who and who not in open_iv:
                iv = {"assignee": who, "start": at, "end": None}
                open_iv[who] = iv
                intervals.append(iv)
        elif tn == "UnassignedEvent":
            who = (n.get("assignee") or {}).get("login")
            if who in open_iv:
                open_iv.pop(who)["end"] = at
        # LabeledEvent (non-status), CrossReferencedEvent and others carry no deck event.

    _validate_blockers(events, blocked_open)
    unack = sorted(tg for tg in blocked_open if not any(
        e.event == "blocker_ack" and e.invalid is None and e.target == tg and _dt(e.at) >= blocked_open[tg]
        for e in events))
    return History(events=events, intervals=intervals, closures=closures, unacknowledged=unack,
                   open_blocks=dict(block_rel))


def is_blocked(history):
    """True iff THIS issue is itself blocked: an open episode with relation "blocked_by". A
    relation "blocking" episode (this issue blocks another) never counts."""
    return any(rel == "blocked_by" for rel in history.open_blocks.values())


def _do_start(ev, st):
    ev.first = not st["ever_started"]
    st["ever_started"] = True
    st["started"] = True
    st["ready_open"] = False


def _status(status, mk, events, st):
    if status == "ready":
        if not st["ready_open"]:
            e = mk("ready", target=None)
            e.first = (not st["ever_ready"]) and (not st["ever_started"])
            st["ever_ready"] = True
            st["ready_open"] = True
            events.append(e)
    elif status == "in progress":
        if not st["started"]:
            e = mk("start")
            _do_start(e, st)
            events.append(e)
    elif status == "backlog":
        st["ready_open"] = False
        if st["ever_started"] and st["started"]:
            events.append(mk("withdrawn"))
            st["started"] = False


def _validate_blockers(events, blocked_open):
    """Flag each blocker_ack with no blocked record for its target at or before it (per-episode: an ack
    is judged against the most recent blocked as of its own time; the summary in `parse` additionally
    requires the ack to be at/after the CURRENT episode's blocked time)."""
    seen = {}   # target -> times of blocked records so far (events are time-ordered)
    for e in events:
        if e.event == "blocked":
            seen.setdefault(e.target, []).append(_dt(e.at))
        elif e.event == "blocker_ack":
            if not any(bt <= _dt(e.at) for bt in seen.get(e.target, [])):
                e.invalid = "before_blocking"


# ---- the gh adapter (the ONLY place a subprocess runs) ----------------------------------------------

_TIMELINE_QUERY = """
query($owner:String!,$repo:String!,$number:Int!,$after:String){repository(owner:$owner,name:$repo){
issue(number:$number){timelineItems(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{
__typename
... on BlockedByAddedEvent{createdAt actor{login} blockingIssue{number repository{nameWithOwner}}}
... on BlockingAddedEvent{createdAt actor{login} blockedIssue{number repository{nameWithOwner}}}
... on BlockedByRemovedEvent{createdAt actor{login} blockingIssue{number repository{nameWithOwner}}}
... on BlockingRemovedEvent{createdAt actor{login} blockedIssue{number repository{nameWithOwner}}}
... on ClosedEvent{createdAt actor{login} stateReason}
... on ReopenedEvent{createdAt actor{login}}
... on MarkedAsDuplicateEvent{createdAt actor{login}}
... on ProjectV2ItemStatusChangedEvent{createdAt actor{login} status previousStatus}
... on AssignedEvent{createdAt actor{login} assignee{... on User{login} ... on Bot{login}}}
... on UnassignedEvent{createdAt actor{login} assignee{... on User{login} ... on Bot{login}}}
... on LabeledEvent{createdAt actor{login} label{name}}
... on CrossReferencedEvent{createdAt actor{login}}
}}}}}
"""


class GhAdapter:
    """Production adapter: the single object through which `gh` is invoked. Tests substitute a stub with
    the same three methods: timeline(issue), comments(issue), post_comment(issue, body) and optionally
    trust() (default: association fallback only)."""

    _trust = None

    def trust(self) -> "Trust":
        """The comment-author trust predicate: authenticated login (one `gh api user` per adapter)
        + `github_scope.operators` + the association fallback (see `Trust`)."""
        if self._trust is None:
            me = None
            try:
                me = (json.loads(self._run(["api", "user"])) or {}).get("login")
            except (RuntimeError, ValueError, AttributeError, OSError):
                me = None
            self._trust = Trust(scope_operators(), me, permission=self._permission)
        return self._trust

    def _permission(self, repo, login):
        return (json.loads(self._run(["api", f"repos/{repo}/collaborators/{login}/permission"])) or {}).get("permission")

    def _run(self, args, stdin=None):
        r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
        return r.stdout

    def comments(self, issue):
        owner_repo, num = issue.split("#")
        out = self._run(["issue", "view", num, "-R", owner_repo, "--json", "comments"])
        cms = json.loads(out).get("comments", [])
        return [dict(c, _fetched_repo=owner_repo) if isinstance(c, dict) else c for c in cms]

    def post_comment(self, issue, body):
        owner_repo, num = issue.split("#")
        self._run(["issue", "comment", num, "-R", owner_repo, "--body-file", "-"], stdin=body)

    def timeline(self, issue):
        m = _ISSUE_RE.fullmatch(issue)
        owner, repo, num = m.groups()
        nodes, after = [], None
        while True:
            args = ["api", "graphql", "-f", f"query={_TIMELINE_QUERY}", "-f", f"owner={owner}",
                    "-f", f"repo={repo}", "-F", f"number={num}"]
            if after:
                args += ["-f", f"after={after}"]
            resp = json.loads(self._run(args))
            if not isinstance(resp, dict) or resp.get("errors"):
                raise RuntimeError(f"graphql error for {issue}: {json.dumps((resp or {}).get('errors'))[:300]}")
            repo_node = (resp.get("data") or {}).get("repository")
            issue_node = (repo_node or {}).get("issue")
            if not issue_node or not issue_node.get("timelineItems"):
                raise RuntimeError(f"issue not found: {issue}")
            data = issue_node["timelineItems"]
            nodes += data["nodes"]
            if not data["pageInfo"]["hasNextPage"]:
                return nodes
            after = data["pageInfo"]["endCursor"]


# ---- the idempotent writer --------------------------------------------------------------------------

def _epoch(issue, event, target, blocking, adapter, comments, at, trust=None):
    """Epoch for the default idempotency key: how many times the work was withdrawn/reopened (start, ack,
    accepted) or the latest `blocked` time for the (target, blocking) pair (blocker_ack)."""
    if event not in ("start", "ack", "accepted", "blocker_ack"):
        return None
    h = parse(adapter.timeline(issue), comments, at, trust)
    if event == "blocker_ack":
        times = [e.at for e in h.events if e.event == "blocked" and e.target == target and e.blocking == blocking]
        return max(times) if times else None
    return sum(1 for e in h.events if e.event in ("withdrawn", "reopened"))


def emit(issue, event, payload, adapter=None, clock=None) -> Event:
    """Record `event` on `issue` as a marker comment unless a marker with the same idempotency key exists.

    Raises ValueError for an unknown or native-only event, or evidence that is not shared references.
    `payload`: target, blocking, by (required), evidence, when, original_target, key (explicit idempotency_key), at, note.
    """
    if event not in EVENTS or not MAPPING[event]["marker"]:
        raise ValueError(f"event {event!r} cannot be emitted"
                         + (" (native-only: GitHub's own record establishes it)" if event in EVENTS else ""))
    _check_issue(issue)
    payload = dict(payload or {})
    evidence = payload.get("evidence") or []
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    if event in EVIDENCE_REQUIRED and not evidence:
        raise ValueError(f"{event} requires evidence (a shared reference)")
    bad = [x for x in evidence if not is_shared_reference(x)]
    if bad:
        raise ValueError(f"evidence must be a URL, owner/repo#n or commit SHA (not a local path): {bad!r}")
    by = payload.get("by")
    if not isinstance(by, str) or not by:
        raise ValueError("by (the recording actor) is required")
    adapter = adapter or GhAdapter()
    at = payload.get("at") or _iso((clock or (lambda: datetime.now(timezone.utc)))())
    blocking = payload.get("blocking")
    if event == "blocker_ack" and blocking is None:
        blocking = payload.get("target")
    comments = adapter.comments(issue)
    trust = trust_of(adapter)
    key = payload.get("key")
    if not key:
        key = default_key(issue, event, payload,
                          _epoch(issue, event, payload.get("target"), blocking, adapter, comments, at, trust))
    for c in comments:
        for ex in _markers_in(c, trust):
            if ex.detail == "foreign_author":
                continue    # a foreign marker never counts as present (no key squatting)
            if ex.idempotency_key == key and ex.event in (event, "unparsed"):
                return ex   # present (a hand-edited, unparsed marker with the key counts as present)
    by = _redact(by)
    ev = Event(event=event, at=_iso(_dt(at)), by=by, target=payload.get("target"), blocking=blocking,
               evidence=list(evidence), source="marker", idempotency_key=key, when=payload.get("when"),
               original_target=payload.get("original_target"))
    note = _redact(str(payload.get("note") or f"{event} recorded by {by}"
                       + (f" for {ev.target}" if ev.target else "")))
    adapter.post_comment(issue, render_marker(ev, note))
    return ev


def fetch_history(issue, as_of, adapter=None) -> History:
    _check_issue(issue)
    adapter = adapter or GhAdapter()
    return parse(adapter.timeline(issue), adapter.comments(issue), as_of, trust_of(adapter))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse")
    p.add_argument("issue")
    p.add_argument("--as-of", required=True)
    e = sub.add_parser("emit")
    e.add_argument("issue")
    e.add_argument("event")
    e.add_argument("--payload", default="{}")
    a = ap.parse_args(argv)
    import foundry_github_capabilities as _ghc  # the CLI needs github_scope (module imports stay ungated)
    refused = _ghc.gate(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), f"foundry_deck_events.py {a.cmd}")
    if refused is not None:
        return refused
    try:
        if a.cmd == "parse":
            h = fetch_history(a.issue, a.as_of)
            print(json.dumps({"events": [x.as_dict() for x in h.events], "intervals": h.intervals,
                              "closures": [asdict(c) for c in h.closures], "unacknowledged": h.unacknowledged},
                             indent=2))
        else:
            print(json.dumps(emit(a.issue, a.event, json.loads(a.payload)).as_dict(), indent=2))
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
