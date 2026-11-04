#!/usr/bin/env python3
"""foundry_usage — native usage collection, dispatch binding, attribution and coverage (gn-05).

Claude Code's documented usage surface is OTEL export. A per-operator exporter (USER-scope env; project
settings ignore OTEL keys) feeds an OpenTelemetry Collector whose `file` exporter writes OTLP/JSON lines.
This module turns that into one numeric usage record per provider request, joins records to dispatch
bindings, and reports what is missing. It stores ONLY numeric usage + correlation metadata: the ingest builds
each record from an allow-list of attributes, so prompts, tool inputs/results, headers and API bodies cannot
reach the dataset whatever the exporter sends.

Verbs:  ingest | bind | attribute | coverage | fallback-transcripts
Dataset: `.claude/foundry-project.json` -> `usage.dataset` (a directory), or `--dataset DIR`.
Files in the dataset: usage.jsonl, unknown_shape.jsonl, bindings.jsonl, attribution.jsonl,
ingest-state.json, export-status.json.

Stdlib only. Export failure is a coverage issue, never a merge or delivery gate: every verb exits 0 on a
data problem and reports it in its JSON output.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import math
import os
import sys

TOKEN_FIELDS = (  # (OTEL attribute, record key)
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_tokens", "cache_read"),
    ("cache_creation_tokens", "cache_creation"),
)
CATEGORIES = tuple(k for _, k in TOKEN_FIELDS)
REQUIRED_ATTRS = ("request_id", "session.id", "model") + tuple(a for a, _ in TOKEN_FIELDS)
EVENT_NAMES = ("api_request", "claude_code.api_request")
LINEAGE_MIN_VERSION = (2, 1, 310)  # agent_id / parent_agent_id appear from this CLI version
EXPORT_ENV_KEYS = (
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "OTEL_LOGS_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_RESOURCE_ATTRIBUTES",
)
FRESH_SECONDS = 24 * 3600

USAGE = "usage.jsonl"
UNKNOWN = "unknown_shape.jsonl"
BINDINGS = "bindings.jsonl"
ATTRIBUTION = "attribution.jsonl"
STATE = "ingest-state.json"
EXPORT_STATUS = "export-status.json"


# ---------------------------------------------------------------- helpers
def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    """ISO-8601 string -> aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def parse_version(v):
    try:
        return tuple(int(p) for p in str(v).split(".")[:3])
    except (ValueError, TypeError):
        return None


def project_root(start=None) -> str:
    cur = os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, ".claude", "foundry-project.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        cur = parent


def dataset_dir(explicit=None, start=None):
    """The dataset directory, or None when none is configured."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    root = project_root(start)
    try:
        with open(os.path.join(root, ".claude", "foundry-project.json"), encoding="utf-8") as f:
            ds = (json.load(f).get("usage") or {}).get("dataset")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(ds, str) or not ds:
        return None
    ds = os.path.expanduser(ds)
    return ds if os.path.isabs(ds) else os.path.join(root, ds)


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        pass
    return out


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(tmp, path)


def append_jsonl(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- OTLP/JSON parsing
def _attr_value(v):
    """OTLP AnyValue -> python scalar (strings stay strings; numbers become numbers)."""
    if not isinstance(v, dict):
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:  # OTLP/JSON encodes int64 as a string
        try:
            return int(v["intValue"])
        except (ValueError, TypeError):
            return None
    if "doubleValue" in v:
        try:
            return float(v["doubleValue"])
        except (ValueError, TypeError):
            return None
    if "boolValue" in v:
        return bool(v["boolValue"])
    return None  # arrays / kvlists are never needed and never copied


def attrs_to_dict(attrs):
    out = {}
    for kv in attrs or []:
        if isinstance(kv, dict) and isinstance(kv.get("key"), str):
            out[kv["key"]] = _attr_value(kv.get("value"))
    return out


def _as_count(v):
    """A non-negative integer token count, or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    try:
        if isinstance(v, float) and math.isfinite(v) and v >= 0 and v == int(v):
            return int(v)
        if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
            return int(v.strip())
    except (ValueError, OverflowError):  # huge digit strings, non-finite floats: unknown shape, never a crash
        pass
    return None


def _log_time(rec, attrs):
    nano = rec.get("timeUnixNano") or rec.get("observedTimeUnixNano")
    try:
        if nano and int(nano) > 0:
            return _iso(_dt.datetime.fromtimestamp(int(nano) / 1e9, _dt.timezone.utc))
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    ts = parse_ts(attrs.get("event.timestamp"))
    return _iso(ts) if ts else None


def iter_log_records(doc):
    """Yield (resource_attrs, log_attrs, log_record) from one OTLP/JSON export document."""
    for rl in (doc.get("resourceLogs") or []) if isinstance(doc, dict) else []:
        res = attrs_to_dict((rl.get("resource") or {}).get("attributes"))
        for sl in rl.get("scopeLogs") or []:
            for lr in sl.get("logRecords") or []:
                if isinstance(lr, dict):
                    yield res, attrs_to_dict(lr.get("attributes")), lr


def normalise_event(res, attrs, lr):
    """-> ("usage", record) | ("unknown", entry) | (None, None) for a non-api_request event.

    Allow-list construction: only the keys below are ever read out of the event.
    """
    name = attrs.get("event.name") or ""
    if name not in EVENT_NAMES:
        body = (lr.get("body") or {}).get("stringValue") if isinstance(lr.get("body"), dict) else None
        if body not in EVENT_NAMES:
            return None, None
    at = _log_time(lr, attrs)
    cli_version = res.get("service.version") if isinstance(res.get("service.version"), str) else None
    operator = res.get("foundry.operator") if isinstance(res.get("foundry.operator"), str) else None
    missing = [a for a in REQUIRED_ATTRS if attrs.get(a) in (None, "")]
    tokens = {}
    for attr, key in TOKEN_FIELDS:
        n = _as_count(attrs.get(attr))
        if n is None and attr not in missing:
            missing.append(attr)  # present but not a count: the shape is wrong, tokens are not inferred
        tokens[key] = n
    if at is None:
        missing.append("timestamp")
    rid = attrs.get("request_id") if isinstance(attrs.get("request_id"), str) else None
    sid = attrs.get("session.id") if isinstance(attrs.get("session.id"), str) else None
    if missing:
        return "unknown", {
            "request_id": rid,
            "session_id": sid,
            "at": at,
            "missing": sorted(set(missing)),
            "cli_version": cli_version,
            "operator": operator,
        }
    cost = attrs.get("cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return "usage", {
        "request_id": rid,
        "at": at,
        "session_id": sid,
        "agent_id": attrs.get("agent_id") or None,
        "parent_agent_id": attrs.get("parent_agent_id") or None,
        "model": attrs["model"],
        "provider": attrs.get("provider") if isinstance(attrs.get("provider"), str) else "anthropic",
        "operator": operator,
        "tokens": tokens,
        "cost_usd_estimate": cost,
        "rate_basis": "native-cost_usd" if cost is not None else "none",
        "source": "otel",
        "config": {"cli_version": cli_version, "exporter": "otlp-file"},
    }


# ---------------------------------------------------------------- ingest
def _export_files(collector_dir):
    files = []
    for pat in ("*.jsonl", "*.json", "*.ndjson"):
        files += glob.glob(os.path.join(collector_dir, "**", pat), recursive=True)
    return sorted(set(files))


def ingest(collector_dir, dataset, now=None):
    collector_dir = os.path.abspath(os.path.expanduser(collector_dir))
    usage = read_jsonl(os.path.join(dataset, USAGE))
    unknown = read_jsonl(os.path.join(dataset, UNKNOWN))
    # request_id -> index in `usage`. Source precedence: an OTEL record replaces a transcript-fallback record
    # with the same id (never the reverse). ASSUMPTION: transcript `requestId` and OTEL `request_id` share the
    # provider request id format; if they do not, the same request is double-counted (coverage reports
    # `fallback-overlap-unverified` whenever both sources contribute).
    seen = {r["request_id"]: i for i, r in enumerate(usage) if r.get("request_id")}
    unknown_keys = {(u.get("request_id"), u.get("at"), tuple(u.get("missing") or ())) for u in unknown}
    new = dup = new_unknown = events = replaced = 0
    files = _export_files(collector_dir)
    for path in files:
        for doc in read_jsonl(path):
            for res, attrs, lr in iter_log_records(doc):
                kind, rec = normalise_event(res, attrs, lr)
                if kind is None:
                    continue
                events += 1
                if kind == "usage":
                    idx = seen.get(rec["request_id"])
                    if idx is not None:
                        if usage[idx].get("source") == "transcript-fallback":
                            usage[idx] = rec
                            replaced += 1
                        else:
                            dup += 1
                        continue
                    seen[rec["request_id"]] = len(usage)
                    usage.append(rec)
                    new += 1
                else:
                    key = (rec["request_id"], rec["at"], tuple(rec["missing"]))
                    if key in unknown_keys:
                        continue
                    unknown_keys.add(key)
                    unknown.append(rec)
                    new_unknown += 1
    if new or replaced:
        usage.sort(key=lambda r: (r.get("at") or "", r.get("request_id") or ""))
        write_jsonl(os.path.join(dataset, USAGE), usage)
    if new_unknown:
        write_jsonl(os.path.join(dataset, UNKNOWN), unknown)
    state = read_json(os.path.join(dataset, STATE))
    state["collector_dir"] = collector_dir
    state["last_ingest"] = _iso(now or _now())
    write_json(os.path.join(dataset, STATE), state)
    status = export_status(dataset, now=now)
    return {
        "files_read": len(files),
        "events_seen": events,
        "records_new": new,
        "fallback_replaced": replaced,
        "duplicates_skipped": dup,
        "unknown_shape_new": new_unknown,
        "records_total": len(usage),
        "export": status["export"],
    }


# ---------------------------------------------------------------- export status
def _user_env(home=None):
    env = {}
    path = os.path.join(home or os.path.expanduser("~"), ".claude", "settings.json")
    data = read_json(path)
    if isinstance(data.get("env"), dict):
        env.update({k: str(v) for k, v in data["env"].items()})
    return env


def export_status(dataset, now=None, home=None, environ=None):
    """`export: arriving|not-arriving`; written to <dataset>/export-status.json so gn-03 reads the same answer."""
    now = now or _now()
    env = _user_env(home)
    env.update({k: v for k, v in (os.environ if environ is None else environ).items() if k in EXPORT_ENV_KEYS})
    configured = env.get("CLAUDE_CODE_ENABLE_TELEMETRY") == "1" and bool(
        env.get("OTEL_LOGS_EXPORTER") or env.get("OTEL_METRICS_EXPORTER")
    )
    state = read_json(os.path.join(dataset, STATE))
    newest = None
    cdir = state.get("collector_dir")
    if cdir:
        for p in _export_files(cdir):
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            newest = m if newest is None or m > newest else newest
    fresh = newest is not None and (now.timestamp() - newest) <= FRESH_SECONDS
    reasons = []
    if not configured:
        reasons.append("user-scope exporter not configured")
    if not fresh:
        reasons.append("no collector file written in the last 24h")
    out = {
        "export": "arriving" if configured and fresh else "not-arriving",
        "reasons": reasons,
        "checked_at": _iso(now),
        "newest_collector_file": _iso(_dt.datetime.fromtimestamp(newest, _dt.timezone.utc)) if newest else None,
    }
    if out["export"] == "not-arriving":
        out["check_user_scope_env_keys"] = [k for k in EXPORT_ENV_KEYS if k not in env]
        out["user_scope_env_keys"] = list(EXPORT_ENV_KEYS)
    write_json(os.path.join(dataset, EXPORT_STATUS), out)
    return out


# ---------------------------------------------------------------- bind
def bind(dataset, session_id, operator=None, issue=None, worktree=None, agent_id=None, at=None):
    rec = {
        "session_id": session_id,
        "agent_id": agent_id or None,
        "operator": operator,
        "issue": str(issue) if issue not in (None, "") else None,
        "worktree": worktree,
        "at": at or _iso(_now()),
    }
    append_jsonl(os.path.join(dataset, BINDINGS), rec)
    return rec


# ---------------------------------------------------------------- attribute
def attribute_records(usage, bindings):
    """Join order: exact session+agent -> session with a single bound outcome -> lead-overhead
    (lead record, several outcomes bound) -> unallocated. Never inferred from commits or a mutable ticket."""
    by_session = {}
    for b in bindings:
        if b.get("session_id") and b.get("issue"):
            by_session.setdefault(b["session_id"], []).append(b)
    rows = []
    for u in usage:
        sid, aid = u.get("session_id"), u.get("agent_id")
        all_bs = by_session.get(sid, [])
        ts = parse_ts(u.get("at"))
        # time guard: only bindings that existed at request time (a binding with no parseable time is kept);
        # a later binding therefore never re-attributes an earlier request
        bs = [b for b in all_bs if ts is None or (parse_ts(b.get("at")) or ts) <= ts]
        bucket, issue, basis = "unallocated", None, "no-binding"
        exact = [b for b in bs if aid and b.get("agent_id") == aid]
        if exact:
            chosen = sorted(exact, key=lambda b: b.get("at") or "")[-1]
            bucket, issue, basis = "outcome", chosen["issue"], "session+agent"
        elif all_bs and not bs:
            bucket, basis = "lead-overhead", "before-first-binding"
        else:
            issues = sorted({b["issue"] for b in bs})
            if len(issues) == 1:
                bucket, issue, basis = "outcome", issues[0], "session-single-binding"
            elif len(issues) > 1 and not aid:
                bucket, basis = "lead-overhead", "lead-multi-outcome"
            elif len(issues) > 1:
                basis = "agent-unbound-multi-outcome"
        rows.append({
            "request_id": u["request_id"],
            "at": u.get("at"),
            "operator": u.get("operator"),
            "session_id": sid,
            "agent_id": aid,
            "bucket": bucket,
            "issue": issue,
            "basis": basis,
            "tokens": u["tokens"],
        })
    return rows


def _sum_tokens(rows):
    t = {c: 0 for c in CATEGORIES}
    for r in rows:
        for c in CATEGORIES:
            t[c] += int((r.get("tokens") or {}).get(c) or 0)
    t["total"] = sum(t[c] for c in CATEGORIES)
    return t


def in_window(at, since=None, until=None):
    ts = parse_ts(at)
    if ts is None:
        return since is None and until is None
    if since and ts < since:
        return False
    if until and ts >= until:
        return False
    return True


def attribute(dataset, since=None, until=None):
    usage = read_jsonl(os.path.join(dataset, USAGE))
    rows = attribute_records(usage, read_jsonl(os.path.join(dataset, BINDINGS)))
    write_jsonl(os.path.join(dataset, ATTRIBUTION), rows)
    return summarise(rows, since, until)


def summarise(rows, since=None, until=None):
    rows = [r for r in rows if in_window(r.get("at"), since, until)]
    outcomes = {}
    for issue in sorted({r["issue"] for r in rows if r["bucket"] == "outcome"}):
        sub = [r for r in rows if r["bucket"] == "outcome" and r["issue"] == issue]
        outcomes[issue] = {
            "tokens": _sum_tokens(sub),
            "requests": len(sub),
            "by_operator": {
                (op or "unknown"): _sum_tokens([r for r in sub if r.get("operator") == op])["total"]
                for op in sorted({r.get("operator") for r in sub}, key=lambda x: x or "")
            },
        }
    lead = [r for r in rows if r["bucket"] == "lead-overhead"]
    unalloc = [r for r in rows if r["bucket"] == "unallocated"]
    return {
        "outcomes": outcomes,
        "lead_overhead": {"tokens": _sum_tokens(lead), "requests": len(lead)},
        "unallocated": {"tokens": _sum_tokens(unalloc), "requests": len(unalloc)},
        "total": _sum_tokens(rows),
        "requests": len(rows),
    }


# ---------------------------------------------------------------- coverage
def configured_operators(root=None):
    data = read_json(os.path.join(project_root(root), ".claude", "foundry-operators.json"))
    ops = data.get("operators")
    return sorted(ops) if isinstance(ops, dict) else []


def coverage(dataset, since=None, until=None, operators=None, now=None):
    usage = [r for r in read_jsonl(os.path.join(dataset, USAGE)) if in_window(r.get("at"), since, until)]
    unknown = [r for r in read_jsonl(os.path.join(dataset, UNKNOWN)) if in_window(r.get("at"), since, until)]
    attr = attribute_records(read_jsonl(os.path.join(dataset, USAGE)), read_jsonl(os.path.join(dataset, BINDINGS)))
    ids = {r["request_id"] for r in usage}
    attr = [a for a in attr if a["request_id"] in ids]
    by_bucket = {b: [a for a in attr if a["bucket"] == b] for b in ("outcome", "lead-overhead", "unallocated")}
    times = sorted(r["at"] for r in usage if r.get("at"))
    fallback = [r for r in usage if r.get("source") == "transcript-fallback"]
    configs = {}
    for r in usage:
        c = r.get("config") or {}
        key = "{}|{}".format(r.get("source"), c.get("cli_version"))
        configs[key] = configs.get(key, 0) + 1
    seen_ops = {r.get("operator") for r in usage}
    ops = list(operators or [])
    silent = sorted(o for o in ops if o not in seen_ops)
    versions = {(r.get("config") or {}).get("cli_version") for r in usage if r.get("source") == "otel"}
    lineage_missing = sorted(v for v in versions if v and (parse_version(v) or (0,)) < LINEAGE_MIN_VERSION)
    reasons = []
    export = export_status(dataset, now=now)
    if not usage:
        reasons.append("no-records-in-window")
    if export.get("export") == "not-arriving":
        reasons.append("export-not-arriving")
    if any(r.get("operator") in (None, "") for r in usage):
        reasons.append("records-with-null-operator")
    contributing = sorted(v for v in lineage_missing
                          if any(r.get("source") == "otel" and (r.get("config") or {}).get("cli_version") == v
                                 for r in usage))
    if contributing:
        reasons.append("agent-lineage-unavailable-cli-versions: " + ", ".join(contributing))
    if fallback:
        reasons.append("transcript-fallback records present")
    if fallback and any(r.get("source") == "otel" for r in usage):
        reasons.append("fallback-overlap-unverified")
    if unknown:
        reasons.append("unknown_shape records present")
    if silent:
        reasons.append("configured operators with zero records: " + ", ".join(silent))
    out = {
        "window": {"since": _iso(since) if since else None, "until": _iso(until) if until else None},
        "observed": _sum_tokens(usage),
        "attributed": _sum_tokens(by_bucket["outcome"]),
        "lead_overhead": _sum_tokens(by_bucket["lead-overhead"]),
        "unallocated": _sum_tokens(by_bucket["unallocated"]),
        "unknown_shape": {"records": len(unknown), "tokens": "unknown",
                          "missing_fields": sorted({f for u in unknown for f in u.get("missing", [])})},
        "records": len(usage),
        "attributed_records": len(by_bucket["outcome"]),
        "sessions": len({r.get("session_id") for r in usage}),
        "agents": len({r.get("agent_id") for r in usage if r.get("agent_id")}),
        "earliest": times[0] if times else None,
        "latest": times[-1] if times else None,
        "configurations": configs,
        "operators_without_records": silent,
        "agent_lineage_unavailable_cli_versions": lineage_missing,
        "transcript_fallback_records": len(fallback),
        "complete": not reasons,
        "incomplete_reasons": reasons,
        "export": export,
    }
    return out


# ---------------------------------------------------------------- transcript fallback
def fallback_transcripts(dataset, cli_version, projects_dir=None, now=None):
    """Best-effort, version-pinned import of `~/.claude/projects/**/*.jsonl`. Transcripts are an internal,
    version-unstable format: only lines stamped with the pinned CLI version are read, only numeric fields are
    taken, and the records are marked source=transcript-fallback so coverage can never be `complete`."""
    projects_dir = os.path.expanduser(projects_dir or "~/.claude/projects")
    usage = read_jsonl(os.path.join(dataset, USAGE))
    seen = {r["request_id"] for r in usage if r.get("request_id")}
    new = skipped_version = skipped_shape = dup = 0
    for path in sorted(glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True)):
        for line in read_jsonl(path):
            if line.get("type") != "assistant":
                continue
            if line.get("version") != cli_version:
                skipped_version += 1
                continue
            msg = line.get("message") if isinstance(line.get("message"), dict) else {}
            u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            rid = line.get("requestId") or msg.get("id")
            tokens = {
                "input": _as_count(u.get("input_tokens")),
                "output": _as_count(u.get("output_tokens")),
                "cache_read": _as_count(u.get("cache_read_input_tokens")),
                "cache_creation": _as_count(u.get("cache_creation_input_tokens")),
            }
            ts = parse_ts(line.get("timestamp"))
            sid = line.get("sessionId")
            if (not isinstance(rid, str) or not rid or not isinstance(sid, str) or ts is None
                    or not isinstance(msg.get("model"), str) or any(v is None for v in tokens.values())):
                skipped_shape += 1
                continue
            if rid in seen:
                dup += 1
                continue
            seen.add(rid)
            usage.append({
                "request_id": rid,
                "at": _iso(ts),
                "session_id": sid,
                "agent_id": line.get("agentId") if isinstance(line.get("agentId"), str) else None,
                "parent_agent_id": None,
                "model": msg["model"],
                "provider": "anthropic",
                "operator": None,
                "tokens": tokens,
                "cost_usd_estimate": None,
                "rate_basis": "none",
                "source": "transcript-fallback",
                "config": {"cli_version": cli_version, "exporter": "transcript-jsonl"},
            })
            new += 1
    usage.sort(key=lambda r: (r.get("at") or "", r.get("request_id") or ""))
    write_jsonl(os.path.join(dataset, USAGE), usage)
    return {"label": "best-effort; never raises coverage to complete", "records_new": new,
            "duplicates_skipped": dup, "skipped_other_cli_version": skipped_version,
            "skipped_unexpected_shape": skipped_shape}


# ---------------------------------------------------------------- CLI
def _window(args):
    return parse_ts(getattr(args, "since", None)), parse_ts(getattr(args, "until", None))


def main(argv=None):
    p = argparse.ArgumentParser(prog="foundry_usage", description=__doc__.splitlines()[0])
    p.add_argument("--dataset", help="dataset directory (default: foundry-project.json usage.dataset)")
    sub = p.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("ingest")
    s.add_argument("collector_dir")
    s = sub.add_parser("bind")
    s.add_argument("--session", required=True)
    s.add_argument("--agent")
    s.add_argument("--operator")
    s.add_argument("--issue", required=True)
    s.add_argument("--worktree")
    s.add_argument("--at", help="binding time, ISO-8601 (default: now); requests before it are not attributed to it")
    for name in ("attribute", "coverage"):
        s = sub.add_parser(name)
        s.add_argument("--since")
        s.add_argument("--until")
        if name == "coverage":
            s.add_argument("--operator", action="append", default=None,
                           help="configured operator (repeatable; default: .claude/foundry-operators.json)")
    s = sub.add_parser("fallback-transcripts")
    s.add_argument("--cli-version", required=True)
    s.add_argument("--projects-dir")
    args = p.parse_args(argv)
    import foundry_operating_mode as _om  # gn-17: the CLI is a team-mode verb (module imports stay ungated)
    refused = _om.gate(project_root(), f"foundry_usage.py {args.verb}")
    if refused is not None:
        return refused
    ds = dataset_dir(args.dataset)
    if not ds:
        print(json.dumps({"error": "no usage.dataset configured (.claude/foundry-project.json) and no --dataset"}))
        return 2
    if args.verb == "ingest":
        out = ingest(args.collector_dir, ds)
    elif args.verb == "bind":
        out = bind(ds, args.session, args.operator, args.issue, args.worktree, args.agent, at=args.at)
    elif args.verb == "attribute":
        out = attribute(ds, *_window(args))
    elif args.verb == "coverage":
        out = coverage(ds, *_window(args), operators=args.operator if args.operator is not None else configured_operators())
    else:
        out = fallback_transcripts(ds, args.cli_version, args.projects_dir)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
