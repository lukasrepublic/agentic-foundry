"""foundry_run_metrics — run-duration capture write boundary (feat-foundry-run-duration-capture,
AC-RDC-1..12). GATHERING ONLY: no dashboard, no gate, no merge authority (Out of scope in the
spec) — this module appends one row per (run_id, spec_ref) to `.foundry/run-metrics.jsonl` and
nothing else reads it to decide anything.

THE CENTRAL INVARIANT THIS MODULE EXISTS TO ENFORCE (narrowed per mandatory security review,
Risk 2 — the prior wording overclaimed): the FIDELITY QUARTET of a row — `spec_sha256`,
`auth_seq_at_dispatch`, `auth_seq_final` (all from the on-disk `acceptance-contract.yaml`) and
`dispatched_at`/`completed_at` (a host clock reading) — is a HOST-SIDE OBSERVATION by
`read_contract_fidelity()`/`_now_iso()`, components that are NEITHER the dispatched agent NOR the
workflow being measured. `spec_ref` and `outcome`, by contrast, ARE payload-carried (the payload
is the only channel that names which atom ran and what its consolidated verdict was) — `spec_ref`
is bounded by `_resolve_contract_path()`'s path-traversal/containment checks below PLUS a
cross-check against the loaded contract's own top-level `spec_ref:` key (a forged `atom` value
that does not match a real, in-bounds, self-consistent contract is refused, never silently
"trusted"); `outcome` is FRAMEWORK-COMPUTED from `review.verdict` (the consolidated verdict) but
that computed value still transits the untrusted wave-return channel to reach this module, so it
is a bounded/derived trust, not a host-side one. Where a wave-return payload ALSO carries a
same-named claim for a QUARTET field (a hostile or merely careless worker could put anything
there), the host-read/host-clock value is what gets written — `extract_atom_records()` /
`compose_row()` never even LOOK at a payload for one of those four fields.

WRITE BOUNDARY PATTERN — copied verbatim in shape from `scripts/foundry_audit_ledger.py`'s v2 row
(validate-and-refuse against a shipped JSON Schema at write time, append-only, `schema_version`-
stamped). DELIBERATE DIVERGENCE from that precedent: the audit ledger is fail-closed (an
authorize-time record that cannot be written stops the thing it records); run-metrics is a
NEVER-BLOCK OBSERVER (AC-RDC-3a/-3b) — a metrics module that turns a green run red is a worse
defect than a missing row. `write_row()`/`compose_row()`/`read_contract_fidelity()` DO raise on
refusal/failure (a caller needs to be able to detect and test that) — it is the CLI entrypoint
(`main()`, driven by `hooks/foundry-run-metrics.sh`) that catches everything and always exits 0.

TIMING SOURCE — the probe result (spec Clarifications, "Open — the timing source"). The shipped
seam is exactly ONE hook firing per wave: `PostToolUse(Agent|Workflow)`, wired here as ONE
additive entry LISTED AFTER `foundry-harvest-learnings.sh`'s entry in the SAME matcher's array —
this atom's allowed_paths budget is that one entry, which forecloses also wiring a
`PreToolUse(Agent|Workflow)` entry (there is none in this plugin's hooks.json today, for any
atom, and this atom may not add one). Array position is NOT an execution-order guarantee: Claude
Code may run same-matcher hooks concurrently, so "listed after" means exactly that and no more —
the two hooks are resource-disjoint (learnings writes under `.foundry/session-learnings/`, this
one under `.foundry/run-metrics*`), so concurrent execution is safe either way.
`workflows/release-wave.js`'s own survey (see the spec) is that it "does not currently read any
clock" and its return carries no per-agent timestamps. With no dispatch-time hook and no per-agent
timing in the return, the ONLY host-observed instant available to the shipped writer is the
single PostToolUse firing itself (the collect instant) — which cannot distinguish a
concurrency-slot wait from real execution for any agent in the wave. Per the spec's mandated
fallback, every row the shipped hook writes therefore records `measurement: "unobserved"`,
`unobserved_reason: "queue-indistinguishable"`, `active_seconds: null` — never elapsed-as-active.
`compose_row()` ITSELF stays fully general (it accepts real `execution_intervals` and computes
`measured` correctly, AC-RDC-4) so a future, better-instrumented caller can use the same write
boundary without a schema or API change.

SECURITY HARDENING (mandatory security review of PR #297, five Risks, all fixed on this branch):
  1. `_resolve_contract_path()` rejects an absolute `spec_ref`, a `spec_ref` with a `..` segment,
     and one whose `os.path.realpath` falls outside the project dir (symlink-assisted escape) —
     BEFORE the file is ever opened — plus `read_contract_fidelity()` cross-checks the loaded
     contract's own top-level `spec_ref:` key against the caller-supplied value. A forged/hostile
     `atom` payload value can no longer point the host-side read at an arbitrary readable file.
  2. `_default_run_id()` now derives from the PostToolUse payload's own `session_id` (falling
     back to `$CLAUDE_CODE_SESSION_ID`, then a freestanding uuid) rather than a fresh uuid every
     invocation, so two REAL hook firings for the same wave (the documented nested Agent+Workflow
     overlap) can actually collide on `(run_id, spec_ref)` — AC-RDC-12's dedup is now live in
     production, not provable only via an explicit `run_id=` override.
  3. Every refusal is persisted to `.foundry/run-metrics.loss.jsonl` (`_log_loss()`, mirrors the
     sibling learnings hook's loss-log pattern) — best-effort, never raising. stderr is NOT
     surfaced to the harness (the hook redirects it, unchanged) since this hook always exits 0
     and a would-be diagnostic reprs a payload-controlled `spec_ref` — a durable, non-model-visible
     record is the honest way to make "REFUSE, don't fabricate" (AC-RDC-3b) actually inspectable.
  4. `_existing_keys()` is read ONCE per hook invocation (not once per atom row) and a
     `MAX_ATOMS_PER_PAYLOAD` cap bounds how many atom records one payload can drive.
  5. This docstring, the CHANGELOG, and the schema no longer claim payload-carried fields
     (`spec_ref`, `outcome`) are host-side observations — only the fidelity quartet is.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import uuid

_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover — requirements-dev.txt pins PyYAML for the gate scripts
    yaml = None  # type: ignore


SCHEMA_VERSION = 1
MEASUREMENTS = ("measured", "unobserved", "backfill-proxy")
UNOBSERVED_REASONS = ("no-timing-source", "queue-indistinguishable", "self-reported-timing",
                      "clock-unavailable", "atom-unresolvable")
OUTCOMES = ("landed", "failed", "merged", "abandoned", "superseded", "in-progress")

_REQUIRED_FIELDS = ("schema_version", "run_id", "spec_ref", "spec_sha256", "dispatched_at",
                    "completed_at", "active_seconds", "measurement", "auth_seq_at_dispatch",
                    "auth_seq_final", "rounds", "outcome")

_ISO_RE_SRC = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"

# Security review Risk 5: a sane bound on how many atom records ONE PostToolUse payload can drive
# through the write boundary — a malformed or hostile payload with an unbounded array must not be
# able to make this never-block hook do unbounded work per invocation.
MAX_ATOMS_PER_PAYLOAD = 64


class RunMetricsWriteError(Exception):
    """Raised when a candidate row is REFUSED at the write boundary (AC-RDC-2/-12): it fails
    schema/structural validation, or its (run_id, spec_ref) pair is already recorded. The row is
    never written when this is raised. Callers that must never block the measured run catch this
    (see `main()` below) — the write boundary itself is honest about refusal, not fail-open."""


class ContractReadError(Exception):
    """Raised by `read_contract_fidelity()` when the atom's on-disk acceptance-contract.yaml
    cannot be read to a valid fidelity snapshot — missing file, unreadable, malformed YAML, no
    `authorized:` block, or a malformed auth_seq/spec_sha256 inside it. The message NAMES the
    read failure and the path (AC-RDC-3b)."""


# --------------------------------------------------------------------------- #
# Paths, clocks, ids — mirrors foundry_audit_ledger.py's conventions verbatim.
# --------------------------------------------------------------------------- #
def ledger_path(project_dir: str | None = None) -> str:
    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(base, ".foundry", "run-metrics.jsonl")


def loss_log_path(project_dir: str | None = None) -> str:
    """Security review Risk 3: a durable, NEVER-model-visible record of every refusal — the hook
    always exits 0 and its stderr is redirected (never surfaced to the harness), so without this
    a refused row's diagnostic is silent forever in production. Mirrors
    `hooks/foundry-harvest-learnings.sh`'s sibling `.harvest-log.jsonl` loss-log pattern."""
    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(base, ".foundry", "run-metrics.loss.jsonl")


def _schema_path() -> str:
    # schemas/ ships beside scripts/ as a plugin artifact — resolve relative to THIS file so it
    # works regardless of CLAUDE_PROJECT_DIR (same pattern as foundry_audit_ledger._schema_path).
    return os.path.join(os.path.dirname(_HERE_DIR), "schemas", "run-metrics-row.schema.json")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_loss(reason: str, *, spec_ref=None, run_id=None, detail: str | None = None,
             project_dir: str | None = None) -> None:
    """Best-effort, NEVER-RAISING durable diagnostic (security review Risk 3): append one JSONL
    row to `.foundry/run-metrics.loss.jsonl` naming why a row was refused/skipped. Swallows every
    exception itself (an unwritable loss-log must never be a second failure on top of the first) —
    it is a recovery aid, not a floor. Also writes to stderr for direct-caller/test observability;
    the shipped hook redirects stderr (`>/dev/null 2>&1`), so nothing here reaches the harness or
    the model in production — see the module docstring."""
    row = {
        "ts": _now_iso(),
        "reason": reason,
        "spec_ref": spec_ref,
        "run_id": run_id,
        "detail": detail,
    }
    try:
        sys.stderr.write(f"foundry-run-metrics: {reason}"
                         + (f" spec_ref={spec_ref!r}" if spec_ref else "")
                         + (f": {detail}" if detail else "") + "\n")
    except Exception:  # noqa: BLE001 — stderr itself must never be the thing that raises
        pass
    try:
        p = loss_log_path(project_dir)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 — best-effort; never raises, never blocks the caller
        pass


def _default_run_id(session_id: str | None = None) -> str:
    """Security review Risk 4: derive from the PostToolUse payload's OWN `session_id` field when
    present — Claude Code supplies it in the hook payload — so that TWO REAL hook firings for the
    same wave (the documented nested Agent+Workflow overlap, AC-RDC-12's actual production
    scenario) share the SAME run_id and can genuinely collide on (run_id, spec_ref). The prior
    default (a fresh uuid4 slice per invocation) made the dedup structurally unreachable in
    production — AC-RDC-12's own fixture only proved it by passing `run_id=` explicitly. Falls
    back to `$CLAUDE_CODE_SESSION_ID`, then a freestanding opaque id, only when no session_id is
    available anywhere.

    RESIDUAL (disclosed, not hidden): within ONE Claude session, two SEPARATE wave dispatches of
    the SAME atom now also share this default run_id, so the second's row is refused as a
    duplicate. The spec's Residuals note a genuine re-dispatch wants a NEW run_id; a caller that
    must distinguish two real re-dispatches within one session should pass `run_id=` explicitly to
    `process_posttooluse_payload()` rather than rely on this default."""
    if isinstance(session_id, str) and session_id:
        return session_id
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        return sid
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# AC-RDC-11/-9 — the host-side contract read. Called TWICE per row (once for the "at dispatch"
# snapshot, once — fresh, never cached — for the "at collect" snapshot), so a contract re-freeze
# between the two calls is visible as two different auth_seq values on the SAME row.
#
# SECURITY REVIEW Risk 1 (path traversal / arbitrary-file-read): `spec_ref` is payload-carried
# (the `atom` field of a wave-return record). Before this fix, an absolute `spec_ref` silently
# DISCARDED the project-dir base (Python's `os.path.join` drops everything before an absolute
# component) and a `..`-laden `spec_ref` could walk the join outside the project dir — either way
# a forged payload could point the "host-side read" at an arbitrary readable file (attribution
# forgery, or an arbitrary-file-read via the resulting error message / accepted content). Fixed by
# `_resolve_contract_path()` below, which validates BEFORE ever touching the filesystem, PLUS a
# cross-check in `read_contract_fidelity()` of the loaded contract's own top-level `spec_ref:` key
# against the caller-supplied value (a contract reachable only via traversal AND one merely placed
# at the "right" path but describing a DIFFERENT atom are both refused).
# --------------------------------------------------------------------------- #
def contract_path_for_spec(spec_ref: str, project_dir: str | None = None) -> str:
    """The NOMINAL (non-validating) path join — used by fixture/test setup that needs to know
    where a contract WOULD live for a well-formed spec_ref. `read_contract_fidelity()` does NOT
    call this directly; it goes through `_resolve_contract_path()`, which validates first."""
    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(base, os.path.dirname(spec_ref), "acceptance-contract.yaml")


def _resolve_contract_path(spec_ref: str, project_dir: str | None = None) -> str:
    """Validate `spec_ref` and return its nominal contract path — WITHOUT ever opening the
    filesystem for anything other than `os.path.realpath` (which does not require the target to
    exist). Raises ContractReadError, naming the specific reason and the offending spec_ref,
    BEFORE any file is read, on any of: `spec_ref` missing/not a string, an absolute path, a path
    containing a `..` segment, or one whose realpath (symlink-resolved) falls outside the
    realpath of the project dir."""
    if not isinstance(spec_ref, str) or not spec_ref:
        raise ContractReadError(f"spec_ref is missing or not a non-empty string: {spec_ref!r}")
    if os.path.isabs(spec_ref):
        raise ContractReadError(
            f"spec_ref must be a project-relative path, got an absolute path — refused: {spec_ref!r}")
    if any(seg == ".." for seg in spec_ref.replace("\\", "/").split("/")):
        raise ContractReadError(f"spec_ref must not contain a '..' path segment — refused: {spec_ref!r}")

    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    base_real = os.path.realpath(base)
    candidate = contract_path_for_spec(spec_ref, project_dir)
    candidate_real = os.path.realpath(candidate)
    if candidate_real != base_real and not candidate_real.startswith(base_real + os.sep):
        raise ContractReadError(
            f"spec_ref resolves outside the project dir — refused (spec_ref={spec_ref!r}, "
            f"resolved={candidate_real!r}, project_dir={base_real!r})")
    return candidate


def read_contract_fidelity(spec_ref: str, project_dir: str | None = None) -> dict:
    """Fresh, uncached host-side read of the atom's on-disk acceptance-contract.yaml `authorized:`
    block. Returns {"spec_sha256": <64-hex str>, "auth_seq": <positive int>}. Raises
    ContractReadError (naming the failure and the path, AC-RDC-3b) on any of: an unsafe spec_ref
    (see `_resolve_contract_path`, security review Risk 1), missing file, unreadable file,
    malformed YAML, non-mapping root, absent/non-mapping `authorized:` block, a missing/malformed
    `auth_seq`, a missing/malformed `spec_sha256`, or a contract whose OWN top-level `spec_ref:`
    key disagrees with the caller-supplied `spec_ref` (a self-consistency cross-check — closes the
    same Risk 1 for a contract merely misplaced at, or symlinked to, the expected path). NEVER
    reads a wave-return payload for the fidelity fields themselves — this function's only inputs
    are the spec_ref path and the on-disk file (AC-RDC-11)."""
    path = _resolve_contract_path(spec_ref, project_dir)
    if not os.path.isfile(path):
        raise ContractReadError(f"acceptance-contract.yaml not found at {path!r} (spec_ref={spec_ref!r})")
    if yaml is None:
        raise ContractReadError(f"PyYAML is not importable — cannot read {path!r}")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        raise ContractReadError(f"acceptance-contract.yaml unreadable at {path!r}: {e}") from e
    try:
        data = yaml.safe_load(raw)
    except Exception as e:  # noqa: BLE001 — any YAML parse failure is a read failure here
        raise ContractReadError(f"acceptance-contract.yaml YAML parse error at {path!r}: {e}") from e
    if not isinstance(data, dict):
        raise ContractReadError(f"acceptance-contract.yaml root is not a mapping at {path!r}")
    declared_ref = data.get("spec_ref")
    if declared_ref != spec_ref:
        raise ContractReadError(
            f"acceptance-contract.yaml at {path!r} declares spec_ref={declared_ref!r}, which does "
            f"not match the requested spec_ref={spec_ref!r} — refused (self-consistency cross-check, "
            "security review Risk 1)")
    blk = data.get("authorized")
    if not isinstance(blk, dict):
        raise ContractReadError(f"acceptance-contract.yaml has no 'authorized:' block at {path!r}")
    auth_seq = blk.get("auth_seq")
    if not isinstance(auth_seq, int) or isinstance(auth_seq, bool) or auth_seq < 1:
        raise ContractReadError(
            f"acceptance-contract.yaml authorized.auth_seq is missing or not a positive integer at {path!r}")
    spec_sha256 = blk.get("spec_sha256")
    if (not isinstance(spec_sha256, str) or len(spec_sha256) != 64
            or any(c not in "0123456789abcdef" for c in spec_sha256.lower())):
        raise ContractReadError(
            f"acceptance-contract.yaml authorized.spec_sha256 is missing or not 64-hex at {path!r}")
    return {"spec_sha256": spec_sha256.lower(), "auth_seq": auth_seq}


# --------------------------------------------------------------------------- #
# AC-RDC-4 — measured-mode arithmetic. Pure; no I/O. Sums per-agent EXECUTION intervals, excluding
# BY CONSTRUCTION any wall-clock gap between them (idle time, slot-wait before the first interval,
# time between one agent finishing and the next starting) — never `completed_at - dispatched_at`.
# --------------------------------------------------------------------------- #
def _parse_iso(ts: str) -> _dt.datetime:
    if not isinstance(ts, str):
        raise ValueError(f"timestamp must be a string, got {ts!r}")
    s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = _dt.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def sum_execution_intervals(intervals) -> int:
    """intervals: an iterable of (start_iso, end_iso) pairs, each a host-observed EXECUTION
    boundary for one agent (impl / dod-self-gate / verify / review-verify) within the atom's run.
    Returns the sum of (end - start) in whole seconds, per interval — the gap BETWEEN intervals
    (idle time, a concurrency-slot wait before the first one) is excluded by construction because
    it is simply never summed. Raises ValueError on an empty list or any end < start interval (a
    malformed observation must not silently produce a wrong number)."""
    pairs = list(intervals)
    if not pairs:
        raise ValueError("sum_execution_intervals: no execution intervals given")
    total = 0.0
    for start, end in pairs:
        s, e = _parse_iso(start), _parse_iso(end)
        if e < s:
            raise ValueError(f"execution interval end {end!r} precedes start {start!r}")
        total += (e - s).total_seconds()
    return int(round(total))


# --------------------------------------------------------------------------- #
# Row composition — the ONLY place fidelity fields are set, and they are ALWAYS host-read here,
# never accepted as trusted parameters representing a payload's claim (AC-RDC-11).
# --------------------------------------------------------------------------- #
def compose_row(*, spec_ref: str, run_id: str, dispatched_at: str, completed_at: str,
                outcome: str, rounds: int = 1, project_dir: str | None = None,
                dispatch_fidelity: dict | None = None,
                execution_intervals=None,
                unobserved_reason: str | None = None, unobserved_detail: str | None = None,
                schema_version: int = SCHEMA_VERSION) -> dict:
    """Build one candidate row (not yet written — call `write_row()` for that).

    `dispatch_fidelity`, when given, MUST itself be the product of an earlier host-side
    `read_contract_fidelity()` call (never derived from a payload) — this is what lets a genuinely
    two-instant caller show a mid-run re-freeze (AC-RDC-9). When omitted, this function performs
    that "at dispatch" read itself, immediately before its own "at collect" read — both are real,
    independent, uncached disk reads; a caller with no earlier observation point (the shipped
    hook's situation, see the probe result in the module docstring) gets two back-to-back-but-
    genuine reads rather than one read copied into both fields.

    Exactly one of `execution_intervals` (-> measurement="measured") or `unobserved_reason`
    (-> measurement="unobserved", active_seconds=None) must be given."""
    if execution_intervals is not None and unobserved_reason is not None:
        raise ValueError("compose_row: give execution_intervals OR unobserved_reason, not both")
    if execution_intervals is None and unobserved_reason is None:
        raise ValueError("compose_row: one of execution_intervals or unobserved_reason is required")

    fid_dispatch = dispatch_fidelity if dispatch_fidelity is not None else read_contract_fidelity(spec_ref, project_dir)
    fid_collect = read_contract_fidelity(spec_ref, project_dir)  # ALWAYS a fresh read, never cached

    row: dict = {
        "schema_version": schema_version,
        "run_id": run_id,
        "spec_ref": spec_ref,
        "spec_sha256": fid_dispatch["spec_sha256"],
        "dispatched_at": dispatched_at,
        "completed_at": completed_at,
        "auth_seq_at_dispatch": fid_dispatch["auth_seq"],
        "auth_seq_final": fid_collect["auth_seq"],
        "rounds": int(rounds),
        "outcome": outcome,
    }

    if execution_intervals is not None:
        row["measurement"] = "measured"
        row["active_seconds"] = sum_execution_intervals(execution_intervals)
    else:
        if unobserved_reason not in UNOBSERVED_REASONS:
            raise ValueError(f"unobserved_reason {unobserved_reason!r} not in {UNOBSERVED_REASONS}")
        row["measurement"] = "unobserved"
        row["active_seconds"] = None
        row["unobserved_reason"] = unobserved_reason
        if unobserved_detail:
            row["unobserved_detail"] = unobserved_detail

    return row


def build_backfill_proxy_row(*, spec_ref: str, run_id: str, dispatched_at: str, completed_at: str,
                             outcome: str, rounds: int = 1,
                             auth_seq_at_dispatch: int | None = None,
                             auth_seq_final: int | None = None,
                             spec_sha256: str = "0" * 64,
                             active_seconds: int | None = None,
                             schema_version: int = SCHEMA_VERSION) -> dict:
    """Construct a well-formed `measurement: "backfill-proxy"` row (AC-RDC-6) — no on-disk contract
    read (a historical backfill's atom may predate this atom or no longer have a live contract).
    This atom writes none of these (Out of scope) — this constructor exists so the write boundary's
    forward-compat acceptance is provable at schema_version 1, for a later backfill writer."""
    return {
        "schema_version": schema_version,
        "run_id": run_id,
        "spec_ref": spec_ref,
        "spec_sha256": spec_sha256,
        "dispatched_at": dispatched_at,
        "completed_at": completed_at,
        "active_seconds": active_seconds,
        "measurement": "backfill-proxy",
        "auth_seq_at_dispatch": auth_seq_at_dispatch,
        "auth_seq_final": auth_seq_final,
        "rounds": int(rounds),
        "outcome": outcome,
    }


# --------------------------------------------------------------------------- #
# AC-RDC-2 — validate-and-refuse. A hand-rolled structural floor ALWAYS runs (never a silent
# no-op degrade when `jsonschema` is absent, mirrors foundry_contract.py/foundry_audit_ledger.py's
# UL-0011 pattern), plus the real JSON-Schema check when `jsonschema` is importable.
# --------------------------------------------------------------------------- #
def _load_schema() -> dict:
    with open(_schema_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _iso_ok(ts) -> bool:
    if not isinstance(ts, str) or not ts.endswith("Z") or "T" not in ts:
        return False
    try:
        _parse_iso(ts)
        return True
    except ValueError:
        return False


def _structural_errors(row: dict) -> list[str]:
    if not isinstance(row, dict):
        return ["row is not a JSON object"]
    errs: list[str] = []
    for k in _REQUIRED_FIELDS:
        if k not in row:
            errs.append(f"missing required field {k!r}")
    if errs:
        return errs  # further checks would just KeyError-chase; the caller already refuses

    if row.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION}, got {row.get('schema_version')!r}")
    for k in ("run_id", "spec_ref"):
        if not isinstance(row.get(k), str) or not row[k]:
            errs.append(f"{k} must be a non-empty string")
    sha = row.get("spec_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
        errs.append("spec_sha256 must be a 64-hex-char string")
    for k in ("dispatched_at", "completed_at"):
        if not _iso_ok(row.get(k)):
            errs.append(f"{k} must be an ISO-8601 UTC string ending in 'Z'")

    measurement = row.get("measurement")
    if measurement not in MEASUREMENTS:
        errs.append(f"measurement {measurement!r} not in {MEASUREMENTS}")

    active_seconds = row.get("active_seconds")
    has_ur = "unobserved_reason" in row
    if measurement == "unobserved":
        if active_seconds is not None:
            errs.append("active_seconds must be null when measurement='unobserved'")
        if not has_ur:
            errs.append("measurement='unobserved' requires unobserved_reason (AC-RDC-5)")
        elif row.get("unobserved_reason") not in UNOBSERVED_REASONS:
            errs.append(f"unobserved_reason {row.get('unobserved_reason')!r} not in {UNOBSERVED_REASONS}")
    else:
        if has_ur:
            errs.append("unobserved_reason present but measurement != 'unobserved' (iff violation)")
        if "unobserved_detail" in row:
            errs.append("unobserved_detail present but measurement != 'unobserved' (iff violation)")
        if not isinstance(active_seconds, int) or isinstance(active_seconds, bool) or active_seconds < 0:
            errs.append("active_seconds must be a non-negative integer unless measurement='unobserved'")

    for k in ("auth_seq_at_dispatch", "auth_seq_final"):
        v = row.get(k)
        if v is None:
            if measurement != "backfill-proxy":
                errs.append(f"{k} may be null only when measurement='backfill-proxy'")
        elif not isinstance(v, int) or isinstance(v, bool) or v < 1:
            errs.append(f"{k} must be a positive integer or null (iff backfill-proxy)")

    rounds = row.get("rounds")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        errs.append("rounds must be a positive integer")

    if row.get("outcome") not in OUTCOMES:
        errs.append(f"outcome {row.get('outcome')!r} not in {OUTCOMES}")

    extra = set(row) - set(_REQUIRED_FIELDS) - {"unobserved_reason", "unobserved_detail"}
    if extra:
        errs.append(f"unexpected field(s): {sorted(extra)}")

    return errs


def validate_row(row: dict) -> list[str]:
    """Validate a candidate row. Uses `jsonschema` against the shipped
    `schemas/run-metrics-row.schema.json` when importable, PLUS the hand-rolled
    `_structural_errors` floor, which always runs regardless. Returns a list of error strings;
    `[]` means valid."""
    errors: list[str] = []
    try:
        import jsonschema  # type: ignore
    except ImportError:
        jsonschema = None  # type: ignore

    if jsonschema is not None:
        try:
            jsonschema.validate(row, _load_schema())
        except jsonschema.ValidationError as e:  # type: ignore[attr-defined]
            errors.append(f"schema: {e.message}")
        except OSError as e:
            errors.append(f"schema file unreadable: {e}")

    errors.extend(_structural_errors(row))
    return errors


# --------------------------------------------------------------------------- #
# AC-RDC-8/-12 — the append-only, idempotent write.
# --------------------------------------------------------------------------- #
def _existing_keys(project_dir: str | None = None) -> set:
    p = ledger_path(project_dir)
    keys: set = set()
    if not os.path.isfile(p):
        return keys
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid, sref = r.get("run_id"), r.get("spec_ref")
            if isinstance(rid, str) and isinstance(sref, str):
                keys.add((rid, sref))
    except OSError:
        pass
    return keys


def write_row(row: dict, project_dir: str | None = None, *, existing_keys: set | None = None) -> dict:
    """Append EXACTLY ONE row to the ledger (AC-RDC-1/-8). Validated against the shipped schema
    at write time (AC-RDC-2); an invalid row is REFUSED (raises RunMetricsWriteError, never
    written). A row whose (run_id, spec_ref) pair is already recorded is likewise refused, naming
    the duplicate key (AC-RDC-12). Append-only: never rewrites or removes a prior row.

    `existing_keys`, when given, is a MUTABLE set the caller owns and pre-populated via
    `_existing_keys()` — security review Risk 5: a multi-row caller (`process_posttooluse_payload`)
    hoists that full-ledger scan to ONCE per invocation rather than once per atom row. This
    function still adds the just-written key to it, so a second atom in the SAME loop sees the
    first's key without a second disk read. Omitted (the default), it reads the ledger itself —
    unchanged behavior for a single direct `write_row()` call."""
    errors = validate_row(row)
    if errors:
        raise RunMetricsWriteError("row REFUSED (not written): " + "; ".join(errors))

    key = (row["run_id"], row["spec_ref"])
    keys = existing_keys if existing_keys is not None else _existing_keys(project_dir)
    if key in keys:
        raise RunMetricsWriteError(
            f"duplicate (run_id, spec_ref) key {key!r} already recorded — refused "
            "(AC-RDC-12: at most one row per (run_id, spec_ref) pair)")

    p = ledger_path(project_dir)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as e:
        raise RunMetricsWriteError(f"ledger unwritable at {p!r}: {e}") from e
    keys.add(key)
    return row


# --------------------------------------------------------------------------- #
# AC-RDC-7 — outcome from the CONSOLIDATED review verdict, never the raw one.
# --------------------------------------------------------------------------- #
def outcome_from_wave_atom(atom_record: dict) -> str:
    """`atom_record` is one element of release-wave.js's return array:
    {atom, impl, gate, verdict, review, dispatch}. `landed` iff impl.status == 'ready' AND
    review.verdict == 'PASS' (the CONSOLIDATED verdict — assembleReviewResult's output); `failed`
    in every other case, including a record that carries NO 'review' key at all (the self-gate
    short-circuit, workflows/release-wave.js:414) and one whose raw `verdict.verdict` disagrees
    with the consolidated `review.verdict` (the consolidated one governs, AC-RDC-7)."""
    impl = atom_record.get("impl") if isinstance(atom_record, dict) else None
    status = impl.get("status") if isinstance(impl, dict) else None
    review = atom_record.get("review") if isinstance(atom_record, dict) else None
    consolidated_verdict = review.get("verdict") if isinstance(review, dict) else None
    return "landed" if (status == "ready" and consolidated_verdict == "PASS") else "failed"


# --------------------------------------------------------------------------- #
# The PostToolUse(Agent|Workflow) payload seam. Structurally handles BOTH the single-object
# ("Agent") shape and the array ("Workflow") shape — mirrors hooks/foundry-harvest-learnings.sh's
# own dual-shape handling of the SAME matcher, which needed it for the documented nested fan-out
# overlap (AC-RDC-12's replay proof drives exactly this).
# --------------------------------------------------------------------------- #
def _as_obj(x):
    if isinstance(x, dict) and set(x.keys()) <= {"type", "text"} and "text" in x:
        x = x["text"]
    if isinstance(x, str):
        try:
            return json.loads(x)
        except (TypeError, ValueError):
            return None
    return x


def extract_atom_records(payload: dict) -> list:
    """Return the list of atom-shaped dicts (has a string 'atom' key and a dict-like 'impl') found
    in a PostToolUse(Agent|Workflow) payload — whether they arrive as a single object at the root
    (a hypothetical 'Agent' shape) or as an array (release-wave.js's 'Workflow' shape), unwrapping
    a {"type":"text","text":"<json>"} content-block wrapper or a JSON-string body at any level."""
    res = None
    for k in ("tool_output", "tool_response", "tool_result"):
        if isinstance(payload, dict) and k in payload:
            res = payload[k]
            break

    out: list = []

    def collect(o):
        o = _as_obj(o)
        if isinstance(o, dict):
            if isinstance(o.get("atom"), str) and o["atom"] and isinstance(o.get("impl"), (dict, type(None))):
                out.append(o)
        elif isinstance(o, list):
            for it in o:
                collect(it)

    collect(res)
    return out


def process_posttooluse_payload(payload: dict, *, run_id: str | None = None,
                                project_dir: str | None = None, now: str | None = None) -> list:
    """The top-level, NEVER-RAISING entrypoint driven by `hooks/foundry-run-metrics.sh`
    (AC-RDC-3a/-3b): extracts every atom record from the payload, composes + writes one row per
    atom (AC-RDC-1), and reports each outcome. A single atom's contract-read failure or write
    refusal is caught PER ATOM — persisted to the durable loss-log (`_log_loss()`, security
    review Risk 3), never surfaced to the harness — and never aborts the others or propagates to
    the caller; the measured run's own outcome is never touched by anything in this function
    (AC-RDC-3b).

    TIMING (the probe result, module docstring): the shipped seam has exactly one host-observed
    instant per wave firing — `now` — so every row lands `measurement: "unobserved"`,
    `unobserved_reason: "queue-indistinguishable"`, `dispatched_at == completed_at == now`.
    `run_id` defaults to the payload's OWN `session_id` field (security review Risk 4 —
    `_default_run_id()`) unless the caller supplies one explicitly (tests replaying the same run
    across two payload shapes, AC-RDC-12), so every atom in this SAME payload shares it.

    `_existing_keys()` is read ONCE here (security review Risk 5), not once per atom, and the
    extracted atom-record list is capped at `MAX_ATOMS_PER_PAYLOAD` — an oversized/hostile array
    cannot make this never-block hook do unbounded work; a truncation is itself loss-logged."""
    results: list = []
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    rid = run_id or _default_run_id(session_id)
    ts = now or _now_iso()
    keys_cache = _existing_keys(project_dir)

    atom_records = extract_atom_records(payload)
    if len(atom_records) > MAX_ATOMS_PER_PAYLOAD:
        _log_loss("payload-truncated", run_id=rid, project_dir=project_dir,
                  detail=f"{len(atom_records)} atom records found, capped at {MAX_ATOMS_PER_PAYLOAD}")
        atom_records = atom_records[:MAX_ATOMS_PER_PAYLOAD]

    for atom_record in atom_records:
        spec_ref = atom_record.get("atom")
        try:
            dispatch_fidelity = read_contract_fidelity(spec_ref, project_dir)
            row = compose_row(
                spec_ref=spec_ref, run_id=rid, dispatched_at=ts, completed_at=ts,
                outcome=outcome_from_wave_atom(atom_record), rounds=1, project_dir=project_dir,
                dispatch_fidelity=dispatch_fidelity,
                unobserved_reason="queue-indistinguishable",
                unobserved_detail=(
                    "hooks/foundry-run-metrics.sh: only PostToolUse(Agent|Workflow) is wired "
                    "(no PreToolUse dispatch-time hook in this atom's allowed scope) — the "
                    "per-agent execution boundary cannot be distinguished from a concurrency-"
                    "slot wait, so the honest value is unobserved, never elapsed-as-active"),
            )
            written = write_row(row, project_dir, existing_keys=keys_cache)
            results.append(written)
        except (ContractReadError, RunMetricsWriteError, ValueError) as e:
            _log_loss("row-refused", spec_ref=spec_ref, run_id=rid, detail=str(e), project_dir=project_dir)
            results.append(None)
    return results


# --------------------------------------------------------------------------- #
# CLI — the hook's entrypoint. ALWAYS exits 0 (AC-RDC-3a): a metrics module that turns a green run
# red is a worse defect than a missing row.
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] != "--posttooluse":
        sys.stderr.write("usage: foundry_run_metrics.py --posttooluse   (stdin: PostToolUse JSON payload)\n")
        return 0
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            _log_loss("malformed-payload", detail=str(e))
            return 0
        process_posttooluse_payload(payload)
    except Exception as e:  # noqa: BLE001 — the hook contract is ALWAYS exit 0 (AC-RDC-3a)
        _log_loss("unexpected-error", detail=str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
