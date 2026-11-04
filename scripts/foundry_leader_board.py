#!/usr/bin/env python3
"""foundry_leader_board — deterministic operator and outcome statistics (gn-15, intake 03 verbatim).

`compute(inputs, as_of, filters, sort)` is a PURE calculator over already-fetched inputs: it reads no clock,
no file, no network. One adapter (`fetch_inputs`, through the injectable gh `runner`) gathers issues,
timelines and comments; gn-04 `parse` rebuilds each issue's history AS OF the cutoff, gn-05 supplies the
attributed usage records and coverage, gn-03 `github_scope` supplies the operator mapping.

Windows (one cutoff for all): 1d, 1w, 1m (= trailing 30 days, labelled). Membership is [start, end), and
each window is shown beside its immediately preceding comparison window. Nothing here is a score: no
composite, no ranking claim; `--sort` only orders a descriptive table.

Read-only. Nothing is ever written to GitHub or to the workspace.

  foundry_leader_board.py [--as-of ISO] [--operator X] [--project P] [--work-type T] [--service S]
                          [--sort METRIC[@1d|@1w|@1m]] [--json] [--inputs FILE] [--deployments FILE]
                          [--dataset DIR] [--project-dir DIR] [--timeout S]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_deck_events as de  # noqa: E402
import foundry_operating_mode as _om  # noqa: E402
import foundry_usage as us  # noqa: E402
from foundry_github_capabilities import default_runner, load_scope  # noqa: E402

SCHEMA = 1
WINDOWS = (("1d", 1), ("1w", 7), ("1m", 30))
WINDOW_LABELS = {"1d": "preceding 24 hours", "1w": "preceding 7 days",
                 "1m": "preceding 30 days (trailing window, not a calendar month)"}
SMALL_SAMPLE_N = 5
UNATTRIBUTED = "(unattributed)"
UNKNOWN_OP = "unknown"
SORT_METRICS = ("name", "throughput", "cycle_median", "ready_lead_median", "blockers_resolved",
                "blocker_ack_median", "blocker_resolution_median", "tokens")
LOOKBACK_DAYS = 61            # closed issues older than this cannot influence the 1m + preceding-1m windows
LIST_LIMIT = 500
CATEGORIES = us.CATEGORIES


# ------------------------------------------------------------------ time / numbers ------------------------

def _d(v):
    return de._dt(v)


def _s(dt):
    return de._iso(dt)


def _sec(delta):
    return int(delta.total_seconds())


def _num(x):
    """Fraction/int/float -> int when integral else a 2-decimal float (deterministic)."""
    f = Fraction(x)
    return int(f) if f.denominator == 1 else round(float(f), 2)


def nearest_rank(sorted_vals, p_tenths=9):
    """Nearest-rank percentile, rank = ceil(0.9 * n) in integer arithmetic (no float rounding)."""
    n = len(sorted_vals)
    rank = -((-p_tenths * n) // 10)
    return sorted_vals[max(rank, 1) - 1]


def median(sorted_vals):
    n = len(sorted_vals)
    if n % 2:
        return sorted_vals[n // 2]
    return _num(Fraction(sorted_vals[n // 2 - 1] + sorted_vals[n // 2], 2))


def dist(obs, population=None, source="records", empty_reason="no observations in the window"):
    """Distribution over [{"id","value"}]; `population` = the ids that SHOULD have an observation."""
    vals = sorted(o["value"] for o in obs)
    ids = sorted({o["id"] for o in obs})
    n = len(vals)
    missing = sorted(set(population or ()) - {o["id"] for o in obs}) if population is not None else []
    out = {"n": n, "ids": ids, "missing_ids": missing}
    if n == 0:
        out.update(status="unavailable", median=None, p90=None,
                   reason=(f"{source} missing for {len(missing)} of {len(population)} outcomes"
                           if missing else empty_reason))
        return out
    out.update(status="partial" if missing else "ok", median=median(vals), p90=nearest_rank(vals),
               label="small sample" if n < SMALL_SAMPLE_N else None)
    if missing:
        out["reason"] = f"{source} missing for {len(missing)} of {len(population)} outcomes"
    return out


def fmt_dur(sec):
    if sec is None:
        return "unavailable"
    sec = int(round(sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return (f"{d}d " if d else "") + f"{h}h {m:02d}m"


# ------------------------------------------------------------------ per-issue facts -------------------------

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_deadline(when):
    """A schedule target -> instant. A date-only target is due at the END of that UTC day (documented)."""
    if not isinstance(when, str) or not when:
        return None
    try:
        if _DATE_ONLY.match(when):
            return _d(when + "T00:00:00Z") + timedelta(days=1)
        return _d(when)
    except ValueError:
        return None


def _label(labels, prefix):
    for lb in labels:
        if lb.lower().startswith(prefix):
            return lb[len(prefix):].strip()
    return None


def _field_text(v):
    """A Project single-select value: a plain string or {"name": ...}; anything else is absent."""
    if isinstance(v, dict):
        v = v.get("name")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _owner_at(intervals, t):
    act = sorted(iv["assignee"] for iv in intervals
                 if _d(iv["start"]) <= t and (iv["end"] is None or _d(iv["end"]) > t))
    if len(act) == 1:
        return act[0], "owner"
    return None, ("ambiguous" if act else "unassigned")


def _attrib(intervals, base, end):
    """Operator targeted for a response measured base -> end: (login|None, clock_start, ambiguous)."""
    login, why = _owner_at(intervals, base)
    start = base
    if login is None and why == "unassigned":
        later = sorted((_d(iv["start"]), iv["assignee"]) for iv in intervals if _d(iv["start"]) > base)
        if later:
            start, login = later[0]
    if login is None:
        return None, base, why == "ambiguous"
    if end is not None:
        at_end, _ = _owner_at(intervals, end)
        if at_end != login:
            return None, start, True        # handoff in the interval: disclosed, left unattributed
    return login, start, False


class Facts:
    pass


def _facts(iss, cutoff):
    created = _d(iss["created_at"]) if iss.get("created_at") else None
    if created is not None and created > cutoff:
        return None
    h = de.parse(iss.get("timeline") or [], iss.get("comments") or [], _s(cutoff))
    f = Facts()
    f.ref, f.h, f.iss = iss["ref"], h, iss
    labels = [str(x) for x in iss.get("labels") or []]
    f.labels = labels
    f.created = created
    f.title = iss.get("title")
    f.author = iss.get("author")
    f.work_type = (_label(labels, "work:") or _field_text(iss.get("work_type")) or "unclassified").lower()
    f.service = iss.get("service") or _label(labels, "service:")
    f.project = iss.get("project")
    f.is_request = any(lb.lower() == "request" for lb in labels)
    f.deliverable = any(lb.lower() == "deliverable" for lb in labels)
    f.request_type = _label(labels, "request:") or "unspecified"
    f.parent = iss.get("parent")
    f.children = list(iss.get("children") or [])
    f.regression_of = iss.get("regression_of")
    f.revert_of = iss.get("revert_of")
    ev = h.events

    def of(name):
        return [e for e in ev if e.event == name and e.invalid is None]
    f.of = of
    f.intervals = h.intervals
    f.ready = next((e for e in of("ready") if e.first), None)
    f.start = next((e for e in of("start") if e.first), None)
    f.accepted = next((e for e in of("accepted") if e.first), None)
    f.reaccepted = [e for e in of("accepted") if not e.first]
    f.reopened = of("reopened")
    term = [e for e in ev if e.event in ("cancelled", "duplicate")]
    last_reopen = max((_d(e.at) for e in f.reopened), default=None)
    f.terminal = bool(term) and (last_reopen is None or _d(term[-1].at) > last_reopen)
    marks = [(_d(e.at), e.event) for e in ev if e.event in ("accepted", "reopened")]
    f.done = bool(marks) and sorted(marks)[-1][1] == "accepted"
    return f


def _is_outcome(f, parents):
    if f.is_request and not f.deliverable:
        return False
    return f.ref not in parents


# ------------------------------------------------------------------ observation tables ----------------------

class Ctx:
    pass


def _opkey(login, mapping):
    if login is None:
        return UNATTRIBUTED
    return mapping.get(login, login)


def _observations(facts, cutoff, mapping):
    """Every per-issue observation, attributed to an operator key, as plain dicts with datetime `at`."""
    parents = {f.parent for f in facts if f.parent} | {f.ref for f in facts if f.children}
    byref = {f.ref: f for f in facts}
    o = {k: [] for k in ("accepted", "reopen", "reaccept", "regression", "revert", "blocker_ack",
                         "blocker_res", "turnaround", "reliability", "request_done", "invalid_ack",
                         "excluded_cancelled")}
    wip, queue = [], {"blockers": [], "overdue": [], "never_responded": [], "unscheduled": [],
                      "reviews": []}
    notes = {"unparseable_targets": [], "original_target_unavailable": []}
    unmapped = set()

    def op(login):
        if login is not None and login not in mapping:
            unmapped.add(login)
        return _opkey(login, mapping)
    op.mapping = mapping

    for f in facts:
        meta = {"project": f.project, "work_type": f.work_type, "service": f.service}
        if _is_outcome(f, parents):
            item = None
            if f.accepted is not None:
                t = _d(f.accepted.at)
                login, why = _owner_at(f.intervals, t)
                owners = []
                for iv in f.intervals:
                    if _d(iv["start"]) <= t and iv["assignee"] not in owners:
                        owners.append(iv["assignee"])
                handoff = bool(f.of("handoff")) or len(owners) > 1
                item = {"id": f.ref, "at": t, "op": op(login), "login": login, "meta": meta,
                        "owner_status": why, "handoff": handoff,
                        "previous_owners": [x for x in owners if x != login] if handoff else [],
                        "accepting_actor": f.accepted.by, "evidence": list(f.accepted.evidence),
                        "parent": f.parent,
                        "cycle": _sec(t - _d(f.start.at)) if f.start and _d(f.start.at) <= t else None,
                        "ready_lead": (_sec(t - _d(f.ready.at))
                                       if f.ready and _d(f.ready.at) <= t else None),
                        "start": f.start.at if f.start else None, "ready": f.ready.at if f.ready else None,
                        "created": _s(f.created) if f.created else None}
                o["accepted"].append(item)
            for e in f.reopened:
                if f.accepted is not None:
                    o["reopen"].append({"id": f.ref, "at": _d(e.at), "op": item["op"], "meta": meta})
            for e in f.reaccepted:
                o["reaccept"].append({"id": f.ref, "at": _d(e.at), "op": item["op"], "meta": meta})
            if f.start is not None and not f.terminal and not (f.done and f.accepted is not None):
                login, _ = _owner_at(f.intervals, cutoff)
                st = _d(f.start.at)
                rr = [_d(e.at) for e in f.of("review_ready")]
                wip.append({"id": f.ref, "op": op(login), "started_at": f.start.at, "age": _sec(cutoff - st),
                            "blocked": de.is_blocked(f.h),
                            "reopened": bool(f.reopened) and f.accepted is not None,
                            "in_review": bool(rr) and max(rr) >= st, "meta": meta})
            rr = [_d(e.at) for e in f.of("review_ready")]
            if rr and not f.terminal and not f.done:
                login, _ = _owner_at(f.intervals, cutoff)
                queue["reviews"].append({"id": f.ref, "op": op(login), "since": _s(max(rr)),
                                         "age": _sec(cutoff - max(rr)), "kind": "outcome", "meta": meta})
        # quality links (regressions / reverts) are their own issues pointing at an outcome
        for kind, tgt in (("regression", f.regression_of), ("revert", f.revert_of)):
            if tgt and f.created is not None:
                aff = byref.get(tgt)
                aff_op = None
                if aff is not None and aff.accepted is not None:
                    aff_op = op(_owner_at(aff.intervals, _d(aff.accepted.at))[0])
                o[kind].append({"id": f.ref, "at": f.created, "op": aff_op or UNATTRIBUTED, "affects": tgt,
                                "meta": meta})
        if f.is_request:
            _request_obs(f, cutoff, o, queue, notes, op, meta, byref)
    return o, wip, queue, notes, unmapped, parents


def _request_obs(f, cutoff, o, queue, notes, op, meta, byref):
    ev = f.h.events
    req_at = f.created
    cands = [_d(e.at) for e in f.of("resolved")] + [_d(c.at) for c in f.h.closures]
    last_reopen = max((_d(e.at) for e in f.reopened), default=None)
    open_now = not f.terminal and not any(last_reopen is None or c > last_reopen for c in cands)
    completed = min(cands) if cands else None
    owner_now, _ = _owner_at(f.intervals, cutoff)
    ack = next((e for e in f.of("ack")), None)
    # ---- turnaround / routine completion
    if req_at is not None and completed is not None and not f.terminal:
        login, start, amb = _attrib(f.intervals, req_at, completed)
        o["turnaround"].append({"id": f.ref, "at": completed, "op": op(login), "value": _sec(completed - start),
                                "rtype": f.request_type, "ambiguous_handoff": amb, "meta": meta})
        o["request_done"].append({"id": f.ref, "at": completed, "op": op(login), "meta": meta})
    # ---- blocker episodes (this request blocks another issue)
    blocks = [e for e in ev if e.event == "blocked" and e.relation == "blocking"]
    for i, b in enumerate(blocks):
        tb = _d(b.at)
        nxt = next((_d(x.at) for x in blocks[i + 1:] if x.target == b.target), None)
        inside = lambda t: t >= tb and (nxt is None or t < nxt)  # noqa: E731
        bid = f"{f.ref}->{b.target}@{b.at}"
        acks_in = [e for e in f.of("blocker_ack") if e.target == b.target and inside(_d(e.at))]
        # a resolution closes the episode when it names the blocked issue or the request itself
        res = next((e for e in f.of("resolved") if inside(_d(e.at)) and e.target in (b.target, f.ref, None)), None)
        end = _d(res.at) if res else (_d(acks_in[0].at) if acks_in else None)
        login, t0, amb = _attrib(f.intervals, tb, end)
        opk = op(login)
        # an acknowledgement before the request targeted anyone is invalid: excluded and listed, never clamped
        for e in acks_in:
            if _d(e.at) < t0:
                o["invalid_ack"].append({"id": bid, "at": _d(e.at), "op": opk, "meta": meta})
        ack_b = next((e for e in acks_in if _d(e.at) >= t0), None)
        if ack_b is not None:
            o["blocker_ack"].append({"id": bid, "at": _d(ack_b.at), "op": opk, "ambiguous_handoff": amb,
                                     "value": _sec(_d(ack_b.at) - t0), "meta": meta})
        # cross-engineer: the blocked issue's owner when blocking began differs from the targeted operator
        bf = byref.get(b.target)
        blocked_owner = _owner_at(bf.intervals, tb)[0] if bf is not None else None
        if blocked_owner is None or login is None:
            cross = None                       # unattributed: listed, in neither the count nor the pooled times
        else:
            cross = _opkey(blocked_owner, op.mapping) != opk
        if res is not None:
            o["blocker_res"].append({"id": bid, "at": _d(res.at), "op": opk, "ambiguous_handoff": amb,
                                     "value": max(0, _sec(_d(res.at) - t0)), "cross_engineer": cross,
                                     "meta": meta})
        elif f.terminal is False and open_now and (nxt is None):
            queue["blockers"].append({"id": bid, "request": f.ref, "blocked": b.target,
                                      "op": op(_owner_at(f.intervals, cutoff)[0]), "blocking_at": b.at,
                                      "age": _sec(cutoff - t0), "acknowledged": ack_b is not None, "meta": meta,
                                      "acknowledged_at": ack_b.at if ack_b else None})
    # ---- schedule / reliability / panel
    sched = sorted((e for e in ev if e.event in ("scheduled", "rescheduled")), key=lambda e: _d(e.at))
    if open_now and req_at is not None and not f.terminal:
        if ack is None and completed is None:
            queue["never_responded"].append({"id": f.ref, "op": op(owner_now), "since": _s(req_at),
                                             "age": _sec(cutoff - req_at), "meta": meta})
        if f.request_type == "review":
            queue["reviews"].append({"id": f.ref, "op": op(owner_now), "since": _s(req_at),
                                     "age": _sec(cutoff - req_at), "kind": "request", "meta": meta})
    if not sched:
        if open_now and not f.terminal:
            queue["unscheduled"].append({"id": f.ref, "op": op(owner_now), "meta": meta})
        return
    structured = [e.original_target for e in sched if e.original_target]
    firsts = [e for e in sched if e.event == "scheduled"]
    original = structured[0] if structured else (firsts[0].when if firsts else None)
    current = sched[-1].when
    if original is None:
        # a rescheduled-only history never recorded the first target: say so, never guess it from the new one
        notes["original_target_unavailable"].append(f.ref)
        cur_dl = parse_deadline(current)
        if open_now and cur_dl is not None and cur_dl < cutoff:
            queue["overdue"].append({"id": f.ref, "op": op(owner_now), "target": current,
                                     "overdue_by": _sec(cutoff - cur_dl), "original_target": "unavailable",
                                     "meta": meta})
        return
    dl = parse_deadline(original)
    if dl is None:
        notes["unparseable_targets"].append(f.ref)
        return
    if f.terminal:
        o["excluded_cancelled"].append({"id": f.ref, "at": dl, "op": op(owner_now), "meta": meta})
        return
    login, _, _ = _attrib(f.intervals, req_at or dl, completed)
    done_on_time = completed is not None and completed <= dl
    o["reliability"].append({"id": f.ref, "at": dl, "op": op(login), "on_time": done_on_time,
                             "completed_at": _s(completed) if completed else None, "original_target": original,
                             "current_target": current, "rescheduled": len(sched) > 1 or original != current})
    cur_dl = parse_deadline(current)
    if open_now and cur_dl is not None and cur_dl < cutoff:
        queue["overdue"].append({"id": f.ref, "op": op(owner_now), "target": current,
                                 "overdue_by": _sec(cutoff - cur_dl), "original_target": original,
                                 "meta": meta})


# ------------------------------------------------------------------ usage ----------------------------------

def _norm_issue(v, default_repo):
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.fullmatch(r"#?\d+", s) and default_repo:
        return f"{default_repo}#{s.lstrip('#')}"
    return s


def _usage_rows(rows, cutoff, default_repo):
    """Deduplicate by request id; drop rows after the cutoff. A row with no request id or no parseable time
    cannot be deduplicated or placed in a window: it is kept as `unplaced` (listed, left out of every total)."""
    seen, out, unplaced = {}, [], []
    for k, r in enumerate(sorted(rows or [], key=lambda r: (str(r.get("at")), str(r.get("request_id"))))):
        rid = r.get("request_id")
        if rid in (None, ""):
            unplaced.append(f"(no request_id) #{k + 1} at {r.get('at')}")
            continue
        if rid in seen:
            continue
        seen[rid] = True
        t = us.parse_ts(r.get("at"))
        if t is None:
            unplaced.append(str(rid))
            continue
        if t > cutoff:
            continue
        toks = r.get("tokens") or {}
        out.append({"id": rid, "at": t, "op": r.get("operator") or UNKNOWN_OP, "bucket": r.get("bucket"),
                    "issue": _norm_issue(r.get("issue"), default_repo), "model": r.get("model") or "unknown",
                    "tokens": {c: int(toks.get(c) or 0) for c in CATEGORIES},
                    "cost": r.get("cost_usd_estimate"), "rate_basis": r.get("rate_basis") or "none"})
    return out, unplaced


def _tot(rows):
    t = {c: sum(r["tokens"][c] for r in rows) for c in CATEGORIES}
    t["total"] = sum(t[c] for c in CATEGORIES)
    return t


def _consumption(rows, s, e, op=None, keep=None):
    sel = [r for r in rows if s <= r["at"] < e and (op is None or r["op"] == op)
           and (keep is None or keep(r))]
    out = {"status": "ok", "requests": len(sel), "usage_ids": sorted(r["id"] for r in sel)}
    out.update(_tot(sel))
    out["categories"] = {c: sum(r["tokens"][c] for r in sel) for c in CATEGORIES}
    out["by_bucket"] = {b: _tot([r for r in sel if r["bucket"] == b])["total"]
                        for b in ("outcome", "lead-overhead", "unallocated")}
    out["by_outcome"] = {i: _tot([r for r in sel if r["issue"] == i and r["bucket"] == "outcome"])["total"]
                         for i in sorted({r["issue"] for r in sel if r["bucket"] == "outcome" and r["issue"]})}
    out["by_model"] = {m: _tot([r for r in sel if r["model"] == m])["total"]
                       for m in sorted({r["model"] for r in sel})}
    out["by_phase"] = "unavailable (usage records carry no phase mapping)"
    costed = [r for r in sel if isinstance(r["cost"], (int, float))]
    if not sel:
        out["estimated_cost"] = {"status": "unavailable", "reason": "no usage in the window"}
    elif not costed:
        out["estimated_cost"] = {"status": "unavailable", "reason": "no record carries a cost estimate"}
    else:
        out["estimated_cost"] = {"status": "partial" if len(costed) < len(sel) else "ok",
                                 "usd": round(sum(r["cost"] for r in costed), 6), "currency": "USD",
                                 "rate_basis": sorted({r["rate_basis"] for r in costed}),
                                 "effective_date": "per-request (native cost_usd at request time)",
                                 "records_with_estimate": len(costed), "records": len(sel),
                                 "note": "token-based estimate, not an invoice"}
    return out


# ------------------------------------------------------------------ DORA -----------------------------------

def _dora(deployments, windows, cutoff, services):
    if not deployments:
        return {"status": "unavailable",
                "reason": "no deployment or incident records supplied (production evidence required; "
                          "Ready-to-Done and PR merge are not substitutes)"}
    deps = [x for x in deployments if x.get("deployed_at") and _d(x["deployed_at"]) <= cutoff]
    svcs = sorted({x.get("service") or "unspecified" for x in deps})
    if services:
        svcs = [s for s in svcs if s in services]
    out = {"status": "ok", "services": {}}
    for sv in svcs:
        mine = [x for x in deps if (x.get("service") or "unspecified") == sv]
        per = {}
        for name, (s, e) in windows.items():
            w = [x for x in mine if s <= _d(x["deployed_at"]) < e]
            ids = sorted(str(x.get("id")) for x in w)
            n = len(w)
            ok_ = [x for x in w if x.get("status", "success") == "success" and x.get("commit_at")]
            lead = [{"id": str(x.get("id")), "value": _sec(_d(x["deployed_at"]) - _d(x["commit_at"]))}
                    for x in ok_]
            rec = [{"id": str(x.get("id")), "value": _sec(_d(x["recovered_at"]) - _d(x["deployed_at"]))}
                   for x in w if x.get("requires_intervention") and x.get("recovered_at")]
            interv = [x for x in w if x.get("requires_intervention")]
            unpl = [x for x in w if x.get("unplanned") or x.get("incident")]
            ratio = (lambda k: {"numerator": len(k), "denominator": n, "rate": _num(Fraction(len(k), n)),
                                "ids": sorted(str(x.get("id")) for x in k)} if n
                     else {"status": "unavailable", "reason": "no deployments in the window",
                           "numerator": 0, "denominator": 0})
            per[name] = {"deployment_frequency": {"deployments": n, "ids": ids},
                         "change_lead_time": dist(lead, source="commit timestamp"),
                         "failed_deployment_recovery_time": dist(rec, source="recovery timestamp"),
                         "change_fail_rate": ratio(interv), "deployment_rework_rate": ratio(unpl)}
        out["services"][sv] = per
    if not out["services"]:
        return {"status": "unavailable", "reason": "no deployment records for the selected service"}
    return out


# ------------------------------------------------------------------ the calculator -----------------------

def _in(t, s, e):
    return s <= t < e


def compute(inputs, as_of, filters=None, sort=None):
    """Pure. `inputs` = already-fetched data (see module docs); returns a JSON-able report."""
    filters = {k: v for k, v in (filters or {}).items() if v}
    cutoff = _d(as_of)
    scope = inputs.get("scope") or {}
    mapping = dict(scope.get("operators") or {})
    default_repo = scope.get("default_repo")
    facts = [f for f in (_facts(i, cutoff) for i in sorted(inputs.get("issues") or [], key=lambda i: i["ref"]))
             if f is not None]
    obs, wip, queue, notes, unmapped, parents = _observations(facts, cutoff, mapping)
    byref = {f.ref: f for f in facts}

    def keep_meta(m):
        return all(not filters.get(k) or m.get(k) == filters[k] for k in ("project", "work_type", "service"))

    opf = filters.get("operator")
    obs_filter_op = mapping.get(opf, opf) if opf else None

    def keep(x):
        if x.get("meta") is not None and not keep_meta(x["meta"]):
            return False
        return obs_filter_op is None or x.get("op") == obs_filter_op
    for k in obs:
        obs[k] = [x for x in obs[k] if keep(x)]
    wip = [x for x in wip if keep(x)]
    for k in queue:
        queue[k] = [x for x in queue[k] if keep(x)]

    ucov = inputs.get("usage_coverage")
    usage_present = inputs.get("usage") is not None
    urows, unplaced = _usage_rows(inputs.get("usage"), cutoff, default_repo)
    outcome_meta = {f.ref: {"project": f.project, "work_type": f.work_type, "service": f.service} for f in facts}
    meta_filtered = any(filters.get(k) for k in ("project", "work_type", "service"))

    def ukeep(r):
        if meta_filtered:
            m = outcome_meta.get(r["issue"]) if r["bucket"] == "outcome" else None
            if m is None or not keep_meta(m):
                return False
        return not obs_filter_op or r["op"] == obs_filter_op
    urows_f = [r for r in urows if ukeep(r)]

    # lifetime usage per outcome (cutoff-bounded, all operators, all time)
    life = {}
    for r in urows:
        if r["bucket"] == "outcome" and r["issue"]:
            L = life.setdefault(r["issue"], {"rows": []})
            L["rows"].append(r)
    for ref, L in life.items():
        rows = L["rows"]
        L.update(_tot(rows))
        L["by_operator"] = {o: _tot([r for r in rows if r["op"] == o])["total"]
                            for o in sorted({r["op"] for r in rows})}
        L["usage_ids"] = sorted(r["id"] for r in rows)
        L["first_at"] = min(r["at"] for r in rows)
        L["cost"] = [r["cost"] for r in rows]
        del L["rows"]
    earliest = None
    if ucov and ucov.get("earliest"):
        earliest = us.parse_ts(ucov["earliest"])
    elif urows:
        earliest = min(r["at"] for r in urows)
    silent = set((ucov or {}).get("operators_without_records") or [])

    def lifetime_coverage(acc):
        why = []
        if not usage_present or not ucov:
            why.append("no usage coverage record")
        firsts = [_d(v) for v in (acc["ready"], acc["created"], acc["start"]) if v]
        if not firsts:
            why.append("no Ready, created or Start timestamp to compare with the collection start")
        elif earliest is None or min(firsts) < earliest:
            why.append("usage collection began after the outcome was first ready, created or started")
        gone = sorted({mapping.get(x, x) for x in [acc["login"]] + acc["previous_owners"] if x} & silent)
        if gone:
            why.append("contributing operators with no usage records: " + ", ".join(gone))
        return ("complete", []) if not why else ("partial", why)

    # windows
    wins = {}
    for name, days in WINDOWS:
        e = cutoff
        s = e - timedelta(days=days)
        wins[name] = {"cur": (s, e), "prev": (s - timedelta(days=days), s)}

    def team_or_op(s, e, op):
        sel = lambda k: [x for x in obs[k] if _in(x["at"], s, e) and (op is None or x["op"] == op)]  # noqa: E731
        acc = sel("accepted")
        by_type = {}
        for x in acc:
            by_type.setdefault(x["meta"]["work_type"], []).append(x["id"])
        rolled = {}
        for x in acc:
            if x.get("parent"):
                rolled.setdefault(x["parent"], []).append(x["id"])
        accp = [a["id"] for a in acc]
        res = sel("blocker_res")
        attributed = [x for x in res if x["cross_engineer"] is not None]
        resolved = [x for x in attributed if x["cross_engineer"]]
        ta = {}
        for x in sel("turnaround"):
            ta.setdefault(x["rtype"], []).append(x)
        rel = sel("reliability")
        excl = sorted(x["id"] for x in sel("excluded_cancelled"))
        done_on_time = [x for x in rel if x["on_time"]]
        m = {
            "throughput": {"count": len(acc), "ids": sorted(accp),
                           "by_work_type": {k: {"count": len(v), "ids": sorted(v)} for k, v in sorted(by_type.items())},
                           "parent_rollups": {k: sorted(v) for k, v in sorted(rolled.items())},
                           "items": [{k: x[k] for k in ("id", "op", "owner_status", "handoff", "previous_owners",
                                                        "accepting_actor", "evidence", "start", "ready")}
                                     | {"accepted_at": _s(x["at"]), **x["meta"]}
                                     for x in sorted(acc, key=lambda x: (x["at"], x["id"]))]},
            "cycle_time": dist([{"id": x["id"], "value": x["cycle"]} for x in acc if x["cycle"] is not None],
                               population=accp, source="start event", empty_reason="no outcome first accepted in the window"),
            "ready_lead_time": dist([{"id": x["id"], "value": x["ready_lead"]} for x in acc
                                     if x["ready_lead"] is not None], population=accp,
                                    source="Ready timestamp", empty_reason="no outcome first accepted in the window"),
            "blockers_resolved": {"count": len(resolved), "ids": sorted(x["id"] for x in resolved),
                                  "excluded_not_cross_engineer": sorted(x["id"] for x in attributed
                                                                        if not x["cross_engineer"]),
                                  "unattributed": sorted(x["id"] for x in res if x["cross_engineer"] is None)},
            "blocker_ack_time": dist(sel("blocker_ack"), empty_reason="no blocker acknowledged in the window")
            | {"invalid_acks": sorted(x["id"] for x in sel("invalid_ack"))},
            "blocker_resolution_time": dist(attributed, empty_reason="no blocker resolved in the window")
            | {"unattributed_ids": sorted(x["id"] for x in res if x["cross_engineer"] is None)},
            "turnaround_by_request_type": {k: dist(v) for k, v in sorted(ta.items())} or
            {"status": "unavailable", "reason": "no request response completed in the window"},
            "scheduled_request_reliability": (
                {"status": "ok", "due_cohort": len(rel), "on_time": len(done_on_time),
                 "rate": _num(Fraction(len(done_on_time), len(rel))),
                 "on_time_ids": sorted(x["id"] for x in done_on_time),
                 "late_or_open_ids": sorted(x["id"] for x in rel if not x["on_time"]),
                 "rescheduled_ids": sorted(x["id"] for x in rel if x["rescheduled"]),
                 "label": "small sample" if len(rel) < SMALL_SAMPLE_N else None,
                 "excluded_cancelled_from_cohort": excl}
                if rel else {"status": "unavailable", "reason": "no scheduled request had its original target in the window",
                             "excluded_cancelled_from_cohort": excl}),
            "routine_requests_completed": {"count": len(sel("request_done")),
                                           "ids": sorted(x["id"] for x in sel("request_done"))},
            "quality_follow_up": {name: {"count": len(sel(k)), "ids": sorted(x["id"] for x in sel(k)),
                                         "affected_outcomes": sorted({x.get("affects") or x["id"] for x in sel(k)})}
                                  for k, name in (("reopen", "reopenings"), ("reaccept", "reacceptances"),
                                                  ("regression", "linked_regressions"),
                                                  ("revert", "linked_reverts"))},
        }
        # ---- tokens
        if not usage_present:
            m["tokens"] = {"status": "unavailable", "reason": "no usage dataset configured (gn-05)"}
            m["outcome_tokens"] = {"status": "unavailable", "reason": "no usage dataset configured (gn-05)"}
        else:
            m["tokens"] = _consumption(urows_f, s, e, op=op)
            m["outcome_tokens"] = _cohort(acc, life, lifetime_coverage, byref)
        return m

    def _cohort(acc, life, covfn, byref):
        comp, part, nouse = [], [], []
        for x in sorted(acc, key=lambda x: x["id"]):
            L = life.get(x["id"])
            if L is None:
                nouse.append(x["id"])
                continue
            status, why = covfn(x)
            row = {"id": x["id"], "lifetime_tokens": L["total"], "by_operator": L["by_operator"],
                   "usage_ids": L["usage_ids"], "coverage": status, "coverage_gaps": why,
                   "handoff": x["handoff"]}
            (comp if status == "complete" else part).append(row)

        def stats(rows):
            vals = sorted(r["lifetime_tokens"] for r in rows)
            n = len(vals)
            if not n:
                return {"status": "unavailable", "n": 0, "ids": [],
                        "reason": "no outcome in this coverage class"}
            tot = sum(vals)
            return {"status": "ok", "n": n, "total": tot, "mean": _num(Fraction(tot, n)), "median": median(vals),
                    "p90": nearest_rank(vals), "label": "small sample" if n < SMALL_SAMPLE_N else None,
                    "ids": [r["id"] for r in rows], "outcomes": rows,
                    "usage_ids": sorted({u for r in rows for u in r["usage_ids"]})}
        out = {"definition": "lifetime attributed tokens (all operators, all time up to the cutoff) of outcomes "
                             "first accepted in the window; never period tokens divided by period completions",
               "complete_coverage": stats(comp), "partial_coverage": stats(part),
               "no_usage_unavailable": nouse}
        if part:
            out["partial_coverage"]["note"] = "observed totals only; not comparable with the complete cohort"
        if not acc:
            out["status"] = "unavailable"
            out["reason"] = "no outcome first accepted in the window; period consumption is still shown"
        else:
            out["status"] = "ok"
        return out

    # operator universe
    ops = set(mapping.values())
    for k in obs:
        ops |= {x["op"] for x in obs[k]}
    ops |= {x["op"] for x in wip} | {r["op"] for r in urows_f}
    for k in queue:
        ops |= {x["op"] for x in queue[k]}
    if obs_filter_op:
        ops = {obs_filter_op}
    ops = sorted(ops, key=lambda x: (x == UNATTRIBUTED, x == UNKNOWN_OP, x))

    windows_out = {}
    for name, w in wins.items():
        entry = {"label": WINDOW_LABELS[name], "start": _s(w["cur"][0]), "end": _s(w["cur"][1]),
                 "previous_start": _s(w["prev"][0]), "previous_end": _s(w["prev"][1])}
        entry["team"] = team_or_op(*w["cur"], None)
        entry["team_previous"] = team_or_op(*w["prev"], None)
        entry["operators"], entry["trend"] = {}, {}
        for o in ops:
            cur = team_or_op(*w["cur"], o)
            prev = team_or_op(*w["prev"], o)
            entry["operators"][o] = cur
            entry["trend"][o] = _trend(cur, prev)
        windows_out[name] = entry

    # ---- queue panel (as of the cutoff; includes items older than any window)
    panel = {
        "as_of": _s(cutoff),
        "open_blockers": sorted(queue["blockers"], key=lambda x: (-x["age"], x["id"])),
        "unacknowledged_blockers": sorted((x for x in queue["blockers"] if not x["acknowledged"]),
                                          key=lambda x: (-x["age"], x["id"])),
        "oldest_blocker": None,
        "overdue_requests": sorted(queue["overdue"], key=lambda x: (-x["overdue_by"], x["id"])),
        "requests_never_responded": sorted(queue["never_responded"], key=lambda x: (-x["age"], x["id"])),
        "unscheduled_requests": sorted(queue["unscheduled"], key=lambda x: x["id"]),
        "active_work": sorted(wip, key=lambda x: (-x["age"], x["id"])),
        "outstanding_reviews": sorted(queue["reviews"], key=lambda x: (-x["age"], x["id"])),
        "work_item_age": dist([{"id": x["id"], "value": x["age"]} for x in wip],
                              empty_reason="no started-but-unfinished work at the cutoff"),
    }
    if panel["open_blockers"]:
        ob = panel["open_blockers"][0]
        panel["oldest_blocker"] = {"id": ob["id"], "age": ob["age"]}
    panel["by_operator"] = {o: {k: sum(1 for x in panel[k] if x["op"] == o) for k in (
        "open_blockers", "unacknowledged_blockers", "overdue_requests", "requests_never_responded",
        "active_work", "outstanding_reviews")} for o in ops}

    # ---- coverage / header
    reasons = list(inputs.get("incomplete_reasons") or [])
    for x in inputs.get("inaccessible") or []:
        reasons.append(f"inaccessible {x.get('source')}: {x.get('detail')}")
    if usage_present and ucov is not None and not ucov.get("complete", False):
        reasons += [f"usage: {r}" for r in ucov.get("incomplete_reasons") or ["incomplete"]]
    if not usage_present:
        reasons.append("usage: no dataset configured")
    if unmapped:
        reasons.append("assignees with no operator mapping: " + ", ".join(sorted(unmapped)))
    if unplaced:
        reasons.append(f"usage: {len(unplaced)} records with no request id or parseable timestamp "
                       "(unplaced, excluded from every total)")
    if notes["original_target_unavailable"]:
        reasons.append("requests rescheduled with no recorded original target (excluded from the reliability "
                       "cohort): " + ", ".join(sorted(notes["original_target_unavailable"])))
    if notes["unparseable_targets"]:
        reasons.append("requests with an unparseable schedule target: " + ", ".join(sorted(notes["unparseable_targets"])))
    reasons = sorted(set(reasons))
    tzname = scope.get("timezone") or "UTC"
    tz_note = None if scope.get("timezone") else "no timezone configured; UTC"
    try:
        from zoneinfo import ZoneInfo
        local = cutoff.astimezone(ZoneInfo(tzname)).isoformat()
    except Exception:  # noqa: BLE001 - unknown tz name: report UTC and say so
        local, tz_note = cutoff.isoformat(), f"unknown timezone {tzname!r}; UTC"
        tzname = "UTC"
    header = {
        "schema": SCHEMA, "cutoff": _s(cutoff), "cutoff_local": local, "timezone": tzname, "timezone_note": tz_note,
        "freshness": inputs.get("freshness") or {}, "collection_complete": not reasons,
        "incomplete_reasons": reasons, "filters": dict(sorted(filters.items())),
        "windows": {n: {"label": WINDOW_LABELS[n], "start": _s(w["cur"][0]), "end": _s(w["cur"][1]),
                        "previous": [_s(w["prev"][0]), _s(w["prev"][1])]} for n, w in wins.items()},
        "membership": "[start, end): at or after start, before end; one cutoff for every window",
        "issues_considered": len(facts), "operators_in_scope": ops,
        "overhead": {"note": "lead overhead and unallocated usage stay visible beside attributed usage; "
                             "nothing is allocated to outcomes"},
        "usage_coverage": ucov,
        "usage_unplaced": {"count": len(unplaced), "ids": sorted(unplaced)},
        "original_target_unavailable": sorted(notes["original_target_unavailable"]),
        "no_composite_score": True,
    }
    out = {"header": header, "windows": windows_out, "queue_panel": panel,
           "bottlenecks": _bottlenecks(panel, windows_out),
           "service_panel": _dora(inputs.get("deployments"), {n: w["cur"] for n, w in wins.items()}, cutoff,
                                  [filters["service"]] if filters.get("service") else None),
           "sort": None}
    out["operator_order"] = _order(ops, windows_out, sort, out)
    return out


def _trend(cur, prev):
    def pick(m):
        return {"throughput": m["throughput"]["count"],
                "cycle_median": m["cycle_time"]["median"],
                "blocker_ack_median": m["blocker_ack_time"]["median"],
                "blockers_resolved": m["blockers_resolved"]["count"],
                "tokens": m["tokens"].get("total") if m["tokens"].get("status") == "ok" else None}
    c, p = pick(cur), pick(prev)
    out = {}
    for k in sorted(c):
        both = c[k] is not None and p[k] is not None
        out[k] = {"current": c[k], "previous": p[k],
                  "change": _num(Fraction(c[k]) - Fraction(p[k])) if both else "unavailable"}
    return out


def _metric_value(m, key):
    if key == "throughput":
        return m["throughput"]["count"]
    if key == "cycle_median":
        return m["cycle_time"]["median"]
    if key == "ready_lead_median":
        return m["ready_lead_time"]["median"]
    if key == "blockers_resolved":
        return m["blockers_resolved"]["count"]
    if key == "blocker_ack_median":
        return m["blocker_ack_time"]["median"]
    if key == "blocker_resolution_median":
        return m["blocker_resolution_time"]["median"]
    if key == "tokens":
        return m["tokens"].get("total")
    return None


def _order(ops, windows, sort, report):
    if not sort:
        return list(ops)
    key, _, win = sort.partition("@")
    win = win or "1w"
    if key not in SORT_METRICS or win not in windows:
        raise ValueError(f"--sort must be one of {', '.join(SORT_METRICS)} (optionally @1d|@1w|@1m)")
    report["sort"] = {"metric": key, "window": win,
                      "note": "descriptive ordering of a table, not a performance ranking"}
    if key == "name":
        return sorted(ops)
    vals = {o: _metric_value(windows[win]["operators"][o], key) for o in ops}
    desc = key in ("throughput", "blockers_resolved", "tokens")
    have = sorted((o for o in ops if vals[o] is not None), key=lambda o: (-vals[o] if desc else vals[o], o))
    return have + sorted(o for o in ops if vals[o] is None)


def _bottlenecks(panel, windows):
    """Team-level things that need attention, as plain statements with their ids. No score."""
    out = []
    if panel["unacknowledged_blockers"]:
        u = panel["unacknowledged_blockers"]
        out.append({"kind": "unacknowledged_blockers", "count": len(u), "oldest_age": u[0]["age"],
                    "ids": [x["id"] for x in u]})
    if panel["overdue_requests"]:
        out.append({"kind": "overdue_requests", "count": len(panel["overdue_requests"]),
                    "ids": [x["id"] for x in panel["overdue_requests"]]})
    if panel["requests_never_responded"]:
        out.append({"kind": "requests_never_responded", "count": len(panel["requests_never_responded"]),
                    "ids": [x["id"] for x in panel["requests_never_responded"]]})
    if panel["outstanding_reviews"]:
        out.append({"kind": "outstanding_reviews", "count": len(panel["outstanding_reviews"]),
                    "oldest_age": panel["outstanding_reviews"][0]["age"],
                    "ids": [x["id"] for x in panel["outstanding_reviews"]]})
    if panel["active_work"]:
        a = panel["active_work"][0]
        out.append({"kind": "oldest_active_work", "id": a["id"], "age": a["age"], "ids": [a["id"]]})
    w = windows["1w"]
    for k, label in (("blocker_ack_time", "blocker acknowledgement time"),
                     ("blocker_resolution_time", "blocker resolution time")):
        c, p = w["team"][k], w["team_previous"][k]
        if c["median"] is not None and p["median"] is not None and c["median"] > p["median"]:
            out.append({"kind": f"{k}_slower_than_preceding_week", "current_median": c["median"],
                        "previous_median": p["median"], "n": c["n"], "ids": c["ids"]})
    return out


# ------------------------------------------------------------------ rendering ------------------------------

def _rd(d):
    if d.get("status") == "unavailable":
        return f"unavailable ({d.get('reason')}) n=0"
    s = f"n={d['n']} median {fmt_dur(d['median'])} p90 {fmt_dur(d['p90'])}"
    if d.get("label"):
        s += f" [{d['label']}]"
    if d.get("status") == "partial":
        s += f" [partial: {d['reason']}; missing {', '.join(d['missing_ids'])}]"
    return s + f" ids: {', '.join(d['ids'])}"


def _rtok(d):
    if d.get("status") == "unavailable":
        return f"unavailable ({d.get('reason')})"
    return (f"n={d['n']} total {d['total']:,} mean {d['mean']:,} median {d['median']:,} p90 {d['p90']:,}"
            + (f" [{d['label']}]" if d.get("label") else "") + f" ids: {', '.join(d['ids'])}")


def _ids(ids, cap=5):
    if len(ids) <= cap:
        return ", ".join(ids) or "-"
    return ", ".join(ids[:cap]) + f" (+{len(ids) - cap} more; --json lists all)"


def _scope_lines(m, ind):
    p = " " * ind
    L = []
    t = m["throughput"]
    L.append(f"{p}accepted throughput: {t['count']} ids: {', '.join(t['ids']) or '-'}")
    for k, v in t["by_work_type"].items():
        L.append(f"{p}  type {k}: {v['count']} ids: {', '.join(v['ids'])}")
    for pr, ch in t["parent_rollups"].items():
        L.append(f"{p}  rolls up into {pr}: {', '.join(ch)} (parent adds no count)")
    for it in t["items"]:
        if it["handoff"]:
            L.append(f"{p}  handoff on {it['id']}: previous owner(s) {', '.join(it['previous_owners']) or 'n/a'}"
                     f" -> {it['op']} (no usage transferred)")
    L.append(f"{p}cycle time: {_rd(m['cycle_time'])}")
    L.append(f"{p}ready lead time: {_rd(m['ready_lead_time'])}")
    b = m["blockers_resolved"]
    L.append(f"{p}blockers resolved: {b['count']} ids: {', '.join(b['ids']) or '-'}")
    if b["excluded_not_cross_engineer"]:
        L.append(f"{p}  excluded (not cross-engineer): {', '.join(b['excluded_not_cross_engineer'])}")
    if b["unattributed"]:
        L.append(f"{p}  unattributed (blocked issue or targeted operator has no single owner; "
                 f"not counted, not in the pooled times): {', '.join(b['unattributed'])}")
    L.append(f"{p}blocker acknowledgement time: {_rd(m['blocker_ack_time'])}")
    if m["blocker_ack_time"]["invalid_acks"]:
        L.append(f"{p}  invalid acknowledgements (before the request targeted anyone; excluded): "
                 f"{', '.join(m['blocker_ack_time']['invalid_acks'])}")
    L.append(f"{p}blocker resolution time: {_rd(m['blocker_resolution_time'])}")
    ta = m["turnaround_by_request_type"]
    if "status" in ta:
        L.append(f"{p}review/decision turnaround: unavailable ({ta['reason']})")
    else:
        for k, v in ta.items():
            L.append(f"{p}turnaround [{k}]: {_rd(v)}")
    r = m["scheduled_request_reliability"]
    if r.get("status") == "unavailable":
        L.append(f"{p}scheduled-request reliability: unavailable ({r['reason']})")
    else:
        L.append(f"{p}scheduled-request reliability (original-target cohort): {r['on_time']}/{r['due_cohort']} "
                 f"= {r['rate']}" + (" [small sample]" if r.get("label") else "")
                 + f" ids: {', '.join(r['on_time_ids'] + r['late_or_open_ids'])}")
    if r["excluded_cancelled_from_cohort"]:
        L.append(f"{p}  cancelled/duplicate requests excluded from the cohort: "
                 f"{', '.join(r['excluded_cancelled_from_cohort'])}")
    rq = m["routine_requests_completed"]
    L.append(f"{p}routine requests completed (not throughput): {rq['count']} ids: {', '.join(rq['ids']) or '-'}")
    q = m["quality_follow_up"]
    L.append(f"{p}quality follow-up: " + "; ".join(
        f"{k} {v['count']} [{', '.join(v['ids']) or '-'}]" for k, v in q.items()))
    tk = m["tokens"]
    if tk.get("status") == "unavailable":
        L.append(f"{p}tokens consumed: unavailable ({tk['reason']})")
    else:
        L.append(f"{p}tokens consumed in window: {tk['total']:,} over {tk['requests']} requests "
                 f"(input {tk['categories']['input']:,}, output {tk['categories']['output']:,}, "
                 f"cache_read {tk['categories']['cache_read']:,}, cache_creation {tk['categories']['cache_creation']:,})")
        bb = tk["by_bucket"]
        L.append(f"{p}  attributed to outcomes {bb['outcome']:,}; lead overhead {bb['lead-overhead']:,}; "
                 f"unallocated {bb['unallocated']:,}; provider-unobserved usage: unknown")
        L.append(f"{p}  by outcome: " + (", ".join(f"{k} {v:,}" for k, v in tk["by_outcome"].items()) or "-")
                 + "; by model: " + (", ".join(f"{k} {v:,}" for k, v in tk["by_model"].items()) or "-")
                 + "; by phase: " + tk["by_phase"])
        c = tk["estimated_cost"]
        L.append(f"{p}  estimated cost: " + (f"unavailable ({c['reason']})" if c["status"] == "unavailable" else
                 f"{c['usd']} {c['currency']} ({c['status']}; basis {', '.join(c['rate_basis'])}; "
                 f"effective {c['effective_date']}; {c['note']})"))
        L.append(f"{p}  usage ids: {_ids(tk['usage_ids'])}")
        ot = m["outcome_tokens"]
        if ot.get("status") == "unavailable":
            L.append(f"{p}lifetime tokens per accepted outcome: unavailable ({ot['reason']})")
        else:
            L.append(f"{p}lifetime tokens per accepted outcome, complete-coverage cohort: {_rtok(ot['complete_coverage'])}")
            pc = ot["partial_coverage"]
            if pc["status"] != "unavailable":
                L.append(f"{p}  partial-coverage outcomes (separate, not comparable): {_rtok(pc)}")
            if ot["no_usage_unavailable"]:
                L.append(f"{p}  no usage observed (unavailable, not zero): {', '.join(ot['no_usage_unavailable'])}")
    return L


def render_text(rep):
    h = rep["header"]
    L = ["# Leader board (read-only; descriptive statistics, not a score)",
         f"cutoff: {h['cutoff']} (local {h['cutoff_local']}) timezone: {h['timezone']}"
         + (f" [{h['timezone_note']}]" if h["timezone_note"] else ""),
         "source freshness: " + (", ".join(f"{k}={v}" for k, v in sorted(h["freshness"].items())) or "unavailable"),
         f"collection complete: {'yes' if h['collection_complete'] else 'no'}"]
    for r in h["incomplete_reasons"]:
        L.append(f"  - {r}")
    L.append("filters: " + (", ".join(f"{k}={v}" for k, v in h["filters"].items()) or "none"))
    L.append("windows share one cutoff; membership [start, end); views overlap and are not additive")
    L.append("")
    L.append("## Team bottlenecks")
    if not rep["bottlenecks"]:
        L.append("none observed")
    for b in rep["bottlenecks"]:
        extra = {k: v for k, v in b.items() if k not in ("kind", "ids")}
        L.append(f"- {b['kind']}: " + ", ".join(f"{k}={v}" for k, v in sorted(extra.items()))
                 + f" ids: {_ids([str(i) for i in b['ids']])}")
    pn = rep["queue_panel"]
    L += ["", f"## Queue panel (as of {pn['as_of']}; includes items older than any window)"]
    L.append(f"open blockers: {len(pn['open_blockers'])}; unacknowledged: {len(pn['unacknowledged_blockers'])}; "
             "oldest blocker age: " + (fmt_dur(pn["oldest_blocker"]["age"]) + f" ({pn['oldest_blocker']['id']})"
                                       if pn["oldest_blocker"] else "none"))
    for x in pn["open_blockers"]:
        L.append(f"  blocker {x['id']} operator {x['op']} age {fmt_dur(x['age'])} "
                 + ("acknowledged" if x["acknowledged"] else "UNACKNOWLEDGED"))
    for key, title in (("overdue_requests", "overdue requests"), ("requests_never_responded", "requests never responded to"),
                       ("unscheduled_requests", "unscheduled requests"), ("outstanding_reviews", "outstanding reviews")):
        L.append(f"{title}: {len(pn[key])}")
        for x in pn[key]:
            age = x.get("overdue_by", x.get("age"))
            L.append(f"  {x['id']} operator {x['op']}" + (f" age {fmt_dur(age)}" if age is not None else ""))
    L.append(f"active work: {len(pn['active_work'])}; work item age: {_rd(pn['work_item_age'])}")
    for x in pn["active_work"]:
        flags = [n for n, v in (("blocked", x["blocked"]), ("review", x["in_review"]), ("reopened", x["reopened"])) if v]
        L.append(f"  {x['id']} operator {x['op']} age {fmt_dur(x['age'])}" + (f" [{', '.join(flags)}]" if flags else ""))
    if rep["sort"]:
        L += ["", f"operator tables ordered by {rep['sort']['metric']}@{rep['sort']['window']} "
                  f"({rep['sort']['note']})"]
    for name in ("1d", "1w", "1m"):
        w = rep["windows"][name]
        L += ["", f"## Window {name}: {w['label']} [{w['start']}, {w['end']}) vs preceding [{w['previous_start']}, {w['previous_end']})",
              "### Team (pooled observations)"]
        L += _scope_lines(w["team"], 0)
        L.append("preceding window: throughput " + str(w["team_previous"]["throughput"]["count"])
                 + f"; cycle time {_rd(w['team_previous']['cycle_time'])}")
        for o in rep["operator_order"]:
            L += [f"### Operator {o}"] + _scope_lines(w["operators"][o], 0)
            L.append("trend vs preceding window: " + "; ".join(
                f"{k} {v['previous']} -> {v['current']} ({v['change']})" for k, v in w["trend"][o].items()))
    L += ["", "## Service panel (DORA)"]
    sp = rep["service_panel"]
    if sp["status"] == "unavailable":
        L.append(f"unavailable ({sp['reason']})")
    else:
        for sv, per in sp["services"].items():
            for n, m in per.items():
                L.append(f"{sv} {n}: deployments {m['deployment_frequency']['deployments']}; lead time "
                         f"{_rd(m['change_lead_time'])}; recovery {_rd(m['failed_deployment_recovery_time'])}; "
                         f"change fail rate {_ratio(m['change_fail_rate'])}; rework rate {_ratio(m['deployment_rework_rate'])}")
    L += ["", "## Coverage"]
    uc = h["usage_coverage"]
    if uc is None:
        L.append("usage: unavailable (no coverage record)")
    else:
        L.append(f"usage records {uc.get('records')}, attributed {uc.get('attributed_records')}, "
                 f"unknown-shape {((uc.get('unknown_shape') or {}).get('records'))} (tokens unknown), "
                 f"complete: {'yes' if uc.get('complete') else 'no'}")
    up = h["usage_unplaced"]
    L.append(f"usage records unplaced (no request id or parseable time; excluded from totals): {up['count']}"
             + (f" ids: {', '.join(up['ids'])}" if up["ids"] else ""))
    L.append("requests rescheduled with no recorded original target: "
             + (", ".join(h["original_target_unavailable"]) or "none"))
    L.append(h["overhead"]["note"])
    return "\n".join(L) + "\n"


def _ratio(r):
    if r.get("status") == "unavailable":
        return f"unavailable ({r['reason']})"
    return f"{r['numerator']}/{r['denominator']} = {r['rate']}"


# ------------------------------------------------------------------ the adapter (all I/O here) ---------------

class _RunnerAdapter(de.GhAdapter):
    """gn-04's adapter with its process-spawn seam replaced by the injected runner (read paths only)."""

    def __init__(self, runner, timeout):
        self.runner, self.timeout = runner, timeout

    def _run(self, args, stdin=None):
        r = self.runner(["gh", *args], self.timeout)
        if r.rc != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {(r.stderr or '').strip()[:300]}")
        return r.stdout


_REF_RE = r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)"


def _body_link(body, words):
    m = re.search(rf"(?im)^\s*(?:{words})\s*:?\s*{_REF_RE}", body or "")
    return m.group(1) if m else None


def _json_stream(text):
    """`gh api --paginate` prints one JSON array per page back to back: flatten them into one list."""
    dec, i, out, text = json.JSONDecoder(), 0, [], (text or "").strip()
    while i < len(text):
        val, i = dec.raw_decode(text, i)
        out += val if isinstance(val, list) else [val]
        while i < len(text) and text[i].isspace():
            i += 1
    return out


def _project_work_type(items):
    """The Project `Work type` field value, when the fetched project item carries it."""
    for it in items or []:
        if isinstance(it, dict):
            for k in ("Work type", "workType", "work_type"):
                v = _field_text(it.get(k))
                if v:
                    return v
    return None


def fetch_issues(cfg, runner, timeout, as_of):
    """-> (issues, inaccessible). Open issues + closed since the lookback. A list that reaches LIST_LIMIT
    is reported as truncated (so the header says `collection complete: no`)."""
    repos = [r for r in (cfg.get("repos") or []) if isinstance(r, str)] or [f"{cfg['owner']}/{cfg['planning_repo']}"]
    since = _s(as_of - timedelta(days=LOOKBACK_DAYS))[:10]
    adapter = _RunnerAdapter(runner, timeout)
    fields = "number,title,labels,author,createdAt,state,projectItems,body,assignees,url"
    issues, bad, seen = [], [], set()
    for repo in sorted(repos):
        for state, extra in (("open", []), ("closed", ["--search", f"closed:>={since}"])):
            res = runner(["gh", "issue", "list", "-R", repo, "--state", state, "--limit", str(LIST_LIMIT),
                          "--json", fields, *extra], timeout)
            try:
                if res.rc != 0:
                    raise RuntimeError((res.stderr or "").strip()[:200] or f"exit {res.rc}")
                rows = json.loads(res.stdout or "[]")
            except (RuntimeError, ValueError) as exc:
                bad.append({"source": f"{repo} {state} issues", "detail": str(exc)})
                continue
            if len(rows) >= LIST_LIMIT:
                bad.append({"source": f"{repo} {state} issues", "detail": f"list truncated at {LIST_LIMIT}; issues beyond it are not counted"})
            for r in rows:
                ref = f"{repo}#{r['number']}"
                if ref in seen:
                    continue
                seen.add(ref)
                try:
                    tl = adapter.timeline(ref)
                    cm = adapter.comments(ref)
                except (RuntimeError, ValueError) as exc:
                    bad.append({"source": ref, "detail": str(exc)[:200]})
                    continue
                kids = []
                sub = runner(["gh", "api", "--paginate", f"repos/{repo}/issues/{r['number']}/sub_issues?per_page=100"],
                             timeout)
                if sub.rc == 0:
                    try:
                        for c in _json_stream(sub.stdout):
                            cr = (c.get("repository_url") or "").split("repos/")[-1]
                            if cr and c.get("number"):
                                kids.append(f"{cr}#{c['number']}")
                    except (ValueError, AttributeError, TypeError):
                        bad.append({"source": f"{ref} sub-issues", "detail": "unparseable"})
                else:
                    bad.append({"source": f"{ref} sub-issues", "detail": "parent roll-up unavailable"})
                items = r.get("projectItems") or []
                proj = (items[0].get("title") if items and isinstance(items[0], dict) else None)
                body = r.get("body") or ""
                issues.append({"ref": ref, "title": r.get("title"), "created_at": r.get("createdAt"),
                               "author": (r.get("author") or {}).get("login"),
                               "labels": sorted(lb.get("name", "") for lb in r.get("labels") or []),
                               "project": proj, "work_type": _project_work_type(items),
                               "children": sorted(kids), "timeline": tl, "comments": cm,
                               "regression_of": _body_link(body, "regression of|regresses"),
                               "revert_of": _body_link(body, "reverts|revert of")})
    parent_of = {c: i["ref"] for i in issues for c in i["children"]}
    for i in issues:
        if i["ref"] in parent_of:
            i["parent"] = parent_of[i["ref"]]
    return sorted(issues, key=lambda i: i["ref"]), bad


def load_usage(project_dir, dataset, as_of):
    """-> (attributed rows with model/cost, coverage dict) or (None, None) when no dataset."""
    ds = us.dataset_dir(dataset, project_dir)
    if not ds or not os.path.isdir(ds):
        return None, None
    raw = us.read_jsonl(os.path.join(ds, us.USAGE))
    rows = us.attribute_records(raw, us.read_jsonl(os.path.join(ds, us.BINDINGS)))
    by_id = {r.get("request_id"): r for r in raw}
    for r in rows:
        src = by_id.get(r["request_id"]) or {}
        r["model"] = src.get("model")
        r["cost_usd_estimate"] = src.get("cost_usd_estimate")
        r["rate_basis"] = src.get("rate_basis")
    cov = us.coverage(ds, until=as_of, operators=us.configured_operators(project_dir), now=as_of)
    return rows, cov


def fetch_inputs(project_dir, as_of, runner=default_runner, timeout=30, dataset=None, deployments_path=None,
                 clock=None):
    manifest, cfg = load_scope(project_dir)
    if cfg is None:
        raise ValueError("no github_scope configured in .claude/foundry-project.json")
    lb = manifest.get("leader_board") if isinstance(manifest.get("leader_board"), dict) else {}
    issues, bad = fetch_issues(cfg, runner, timeout, as_of)
    rows, cov = load_usage(project_dir, dataset, as_of)
    deployments = None
    dpath = deployments_path or lb.get("deployments")
    if dpath:
        p = dpath if os.path.isabs(dpath) else os.path.join(project_dir, dpath)
        try:
            with open(p, encoding="utf-8") as f:
                deployments = json.load(f)
        except (OSError, ValueError):
            bad.append({"source": "deployments", "detail": f"unreadable {dpath}"})
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    return {"scope": {"operators": cfg.get("operators") if isinstance(cfg.get("operators"), dict) else {},
                      "timezone": cfg.get("timezone") or lb.get("timezone"),
                      "default_repo": f"{cfg['owner']}/{cfg['planning_repo']}"},
            "issues": issues, "inaccessible": bad, "usage": rows, "usage_coverage": cov,
            "deployments": deployments,
            "freshness": {"github": _s(now), "usage_latest_record": (cov or {}).get("latest")}}


def main(argv=None, runner=default_runner, clock=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of")
    ap.add_argument("--operator")
    ap.add_argument("--project")
    ap.add_argument("--work-type", dest="work_type")
    ap.add_argument("--service")
    ap.add_argument("--sort")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--inputs", help="already-fetched inputs (JSON); skips every network call")
    ap.add_argument("--deployments")
    ap.add_argument("--dataset")
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--timeout", type=float, default=30)
    a = ap.parse_args(argv)
    refused = _om.gate(a.project_dir, "foundry_leader_board.py")  # gn-17: before any gh call/read
    if refused is not None:
        return refused
    try:
        as_of = _d(a.as_of) if a.as_of else (clock or (lambda: datetime.now(timezone.utc)))()
        if a.inputs:
            with open(a.inputs, encoding="utf-8") as f:
                inputs = json.load(f)
        else:
            inputs = fetch_inputs(a.project_dir, as_of, runner, a.timeout, a.dataset, a.deployments, clock)
        rep = compute(inputs, _s(as_of), {"operator": a.operator, "project": a.project,
                                          "work_type": a.work_type, "service": a.service}, a.sort)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out.write((json.dumps(rep, indent=2, sort_keys=True) + "\n") if a.json else render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
