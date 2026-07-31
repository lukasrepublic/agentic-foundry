"""foundry_audit_ledger — the §8 spec-audit EVIDENCE ledger (UL-0006), now LEDGER v2
(feat-foundry-audit-ledger-taxonomy, AC-ALT-1..6 — the ENABLING atom of the audit-economics
wave; downstream atoms consume this schema).

`/foundry:authorize` is supposed to require that the §8 adversarial spec-audit ran before it
freezes — but that was prose-only (an un-audited DRAFT once reached the freeze with zero audit and
nothing caught it). This ledger is the machine-checkable evidence: the audit producer records a row
bound to the spec's CONTENT hash, and authorize fail-closes unless a matching row exists.

THREAT MODEL (operator-chosen mechanism — signed-record-bound-to-spec-hash, NO keychain/MAC):
  • CLOSES the accidental/process-control gap (the incident: reaching authorize having run no audit).
  • BINDS evidence to the exact `spec_sha256` the operator signs — a post-audit edit to the spec
    changes the hash → the row no longer matches → a re-audit is forced. Cross-spec replay is
    impossible by content-addressing (different spec ⇒ different hash ⇒ no match).
  • MAKES audits auditable (a recorded, attributable trail).
  • Does NOT defend against an adversarial co-resident agent that fabricates a ledger row or uses the
    operator skip path — there is no human-held secret here. That containment is the DEFERRED
    keychain/hardware-MAC (§22.12), which can layer on top of this same lookup. Honest per the three
    prior §8 audits of UL-0006 (which killed the non-crypto designs as adversarial-agent-proof; this
    ships the proportionate, auditable fix the operator chose, with the limit stated, not hidden).

LEDGER v2 (this atom, TRUSTED-OPERATOR / non-security-flagged bookkeeping-fidelity threat model):
the single string `plateau` measured zero entropy across 153/153 real rows — a clean 1-round pass
and a 10-round contested outlier recorded identically, with no timestamp and no per-run identity.
v2 replaces it with a FLAT discriminating verdict enum (AC-ALT-1):

    converged | plateau-clean | plateau-security | needs-reground | needs-operator |
    killed | dedupe-skip | refused

plus an orthogonal `kill_reason ∈ {watchdog, limit, error}` field present IF AND ONLY IF
`verdict == "killed"` (the CI-conclusion / SARIF-result pattern: the discriminating detail rides
alongside the enum, never delimited inside it as a parameterized member string — a category error a
prior §8 pass of this very spec caught and re-grounded). Every v2 row additionally carries `ts`
(ISO-8601 UTC), `run_id`, `tier`, `rounds`, `findings` counts `{new, resolved, open}`, `spec_ref`,
`spec_sha256`, and a registry-bound `operator` (AC-ALT-2). The write boundary
(`record_audit_v2`) validates each row against the shipped JSON Schema
(`schemas/audit-ledger-row.schema.json`) AT WRITE TIME and enforces `run_id` uniqueness AT THE
WRITE — an invalid or duplicate row is REFUSED (raises `LedgerWriteError`), never written
(AC-ALT-3).

MIGRATION IS ADDITIVE (AC-ALT-4): v1 rows (the bare `{spec_ref, spec_sha256, rounds, operator,
verdict}` shape, `verdict` == the literal string `"plateau"`) are NEVER rewritten or deleted.
`record_audit` (below) still constructs that exact v1 shape — but no shipped v2 code path calls it
any more (`scripts/foundry-audit-record.py` writes v2 rows exclusively); it is kept only so v1-shaped
fixtures can still be built (tests, the append-only-preservation proof). `find_audit` — the
has-been-audited floor `foundry-authorize.py` reads — is UNCHANGED and already accepts both shapes:
a v1 row's `rounds`/`spec_sha256`/`verdict` keys mean the same thing in a v2 row, so the existing
lookup needs no modification to keep working for either (AC-ALT-4's "no retroactive fail-closed on
old atoms").
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

# Bootstrap THIS module's own directory onto sys.path so `import foundry_authz` resolves
# regardless of HOW this module was loaded (a plain `import` with scripts/ already on
# sys.path, OR a drop-in check's `importlib.util.spec_from_file_location` load, which does
# NOT put scripts/ on sys.path for us — mirrors foundry-audit-prepare.py's own bootstrap).
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

_NONPASS_VERDICTS = {"fail", "rejected", "abandoned"}   # v1 (legacy) vocabulary, untouched.

# ── v2 taxonomy (AC-ALT-1) ──────────────────────────────────────────────────────────────────────────
V2_VERDICTS = ("converged", "plateau-clean", "plateau-security", "needs-reground",
               "needs-operator", "killed", "dedupe-skip", "refused")
KILL_REASONS = ("watchdog", "limit", "error")

_V2_REQUIRED_FIELDS = ("schema_version", "ts", "run_id", "tier", "rounds", "findings",
                       "spec_ref", "spec_sha256", "operator", "verdict")


class LedgerWriteError(Exception):
    """Raised when a v2 row write is REFUSED at the write boundary (AC-ALT-3): the row fails
    schema validation, or its `run_id` is already recorded. The caller must surface this — a
    run that cannot write its row never silently under-records."""


def ledger_path(project_dir: str | None = None) -> str:
    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(base, ".foundry", "audit-ledger.jsonl")


def _schema_path() -> str:
    # schemas/ ships beside scripts/ as a plugin artifact (not project state) — resolve
    # relative to THIS file so it works regardless of CLAUDE_PROJECT_DIR.
    return os.path.join(os.path.dirname(_HERE_DIR), "schemas", "audit-ledger-row.schema.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_run_id() -> str:
    """Best-effort session correlation (residual): prefixed with $CLAUDE_CODE_SESSION_ID when
    present; a freestanding opaque id (run_id only, no correlation) otherwise."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    suffix = uuid.uuid4().hex[:12]
    return f"{sid}:{suffix}" if sid else suffix


# --------------------------------------------------------------------------- #
# v1 (legacy) — kept ONLY for fixture/backward-compat construction, never called by v2 code.
# --------------------------------------------------------------------------- #
def record_audit(spec_ref: str, spec_sha256: str, rounds: int, operator: str,
                 verdict: str = "plateau", project_dir: str | None = None) -> dict:
    """Append a v1 (legacy, bare-verdict-string) audit-evidence row. NOT called by any shipped
    v2 code path — `scripts/foundry-audit-record.py` writes v2 rows exclusively (AC-ALT-1: the
    bare string `plateau` shall not be writable by v2 code). Retained so v1-shaped rows can
    still be constructed for the append-only / both-shapes-read proofs. `spec_sha256` is the
    binding key; `spec_ref` is recorded for human audit. Append-only (never rewrites prior
    rows)."""
    p = ledger_path(project_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    row = {"spec_ref": spec_ref, "spec_sha256": spec_sha256, "rounds": int(rounds),
           "operator": operator, "verdict": verdict}
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def find_audit(spec_sha256: str, project_dir: str | None = None, min_rounds: int = 1):
    """Return the most-recent audit row whose `spec_sha256` matches AND rounds>=min_rounds AND the
    verdict is not a non-pass — else None. Hash-keyed: this is what binds the evidence to the exact
    spec content authorize is about to sign (no cross-spec replay, no post-audit mutation).

    BOTH-SHAPES READ (AC-ALT-4): a v1 row (`verdict="plateau"`) and a v2 row (`verdict` one of
    `V2_VERDICTS`) carry the SAME `spec_sha256`/`rounds`/`verdict` keys with the same meaning, so
    this lookup needs no shape-specific branching to accept either — a v1 `plateau` row keeps
    satisfying this floor exactly as before v2 shipped."""
    p = ledger_path(project_dir)
    if not os.path.isfile(p):
        return None
    hit = None
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (r.get("spec_sha256") == spec_sha256
                    and int(r.get("rounds", 0)) >= min_rounds
                    and r.get("verdict") not in _NONPASS_VERDICTS):
                hit = r  # keep last → most recent
    except OSError:
        return None
    return hit


# --------------------------------------------------------------------------- #
# v2 — schema-validated write boundary (AC-ALT-1..3).
# --------------------------------------------------------------------------- #
def _load_schema() -> dict:
    with open(_schema_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _structural_errors(row: dict) -> list[str]:
    """Hand-rolled structural + semantic floor for a v2 row — ALWAYS runs (in addition to the
    real JSON-Schema check when `jsonschema` is importable), so the write boundary never
    silently degrades to a no-op when the optional dependency is absent (mirrors
    foundry_contract.py's UL-0011 pattern)."""
    if not isinstance(row, dict):
        return ["row is not a JSON object"]
    errs: list[str] = []
    for k in _V2_REQUIRED_FIELDS:
        if k not in row:
            errs.append(f"missing required field {k!r}")
    if row.get("schema_version") != 2:
        errs.append(f"schema_version must be 2, got {row.get('schema_version')!r}")
    verdict = row.get("verdict")
    if verdict not in V2_VERDICTS:
        errs.append(f"verdict {verdict!r} not in {V2_VERDICTS}")
    is_killed = verdict == "killed"
    has_kr = "kill_reason" in row
    if is_killed and not has_kr:
        errs.append("verdict=killed requires kill_reason (AC-ALT-1)")
    if is_killed and has_kr and row.get("kill_reason") not in KILL_REASONS:
        errs.append(f"kill_reason {row.get('kill_reason')!r} not in {KILL_REASONS}")
    if not is_killed and has_kr:
        errs.append("kill_reason present but verdict != killed (AC-ALT-1 iff-killed violation)")
    findings = row.get("findings")
    if not isinstance(findings, dict) or any(k not in findings for k in ("new", "resolved", "open")):
        errs.append("findings must be an object with new/resolved/open")
    else:
        for k in ("new", "resolved", "open"):
            v = findings.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                errs.append(f"findings.{k} must be a non-negative integer")
    rounds = row.get("rounds")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 0:
        errs.append("rounds must be a non-negative integer")
    for k in ("run_id", "tier", "spec_ref", "operator"):
        if k in row and (not isinstance(row[k], str) or not row[k]):
            errs.append(f"{k} must be a non-empty string")
    sha = row.get("spec_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
        errs.append("spec_sha256 must be a 64-hex-char string")
    ts = row.get("ts")
    if not isinstance(ts, str) or not ts.endswith("Z") or "T" not in ts:
        errs.append("ts must be an ISO-8601 UTC string ending in 'Z'")
    return errs


def validate_row_v2(row: dict) -> list[str]:
    """Validate a candidate v2 row. Uses `jsonschema` against the shipped
    `schemas/audit-ledger-row.schema.json` when the optional dependency is importable, PLUS the
    hand-rolled `_structural_errors` floor — which always runs regardless (never a silent
    no-op degrade). Returns a list of error strings; `[]` means valid."""
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


def _existing_run_ids(project_dir: str | None = None) -> set[str]:
    p = ledger_path(project_dir)
    ids: set[str] = set()
    if not os.path.isfile(p):
        return ids
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = r.get("run_id")
            if isinstance(rid, str):
                ids.add(rid)
    except OSError:
        pass
    return ids


def record_audit_v2(*, spec_ref: str, spec_sha256: str, rounds: int, operator: str,
                    tier: str, verdict: str, findings: dict | None = None,
                    kill_reason: str | None = None, run_id: str | None = None,
                    ts: str | None = None, project_dir: str | None = None) -> dict:
    """Write EXACTLY ONE v2 JSONL row at the engine-run write boundary (AC-ALT-3): every
    invocation — converged, plateau, needs-reground/operator, killed, dedupe-skip, refused —
    produces exactly one row here. The row is validated against the shipped JSON Schema AT
    WRITE TIME; an invalid row (bad verdict, a kill_reason iff-killed violation, a missing
    field) is REFUSED and never written — raises `LedgerWriteError`, never silently dropped.
    `run_id` uniqueness is enforced AT THE WRITE — a second write bearing an already-recorded
    `run_id` is refused the same way, preventing duplicates at the cause rather than merely
    linting them after. Append-only: never rewrites or removes any prior row (v1 or v2)."""
    row = {
        "schema_version": 2,
        "ts": ts or _now_iso(),
        "run_id": run_id or _default_run_id(),
        "tier": tier,
        "rounds": int(rounds),
        "findings": findings if findings is not None else {"new": 0, "resolved": 0, "open": 0},
        "spec_ref": spec_ref,
        "spec_sha256": spec_sha256,
        "operator": operator,
        "verdict": verdict,
    }
    if kill_reason is not None:
        row["kill_reason"] = kill_reason

    errors = validate_row_v2(row)
    if errors:
        raise LedgerWriteError("v2 row REFUSED (not written): " + "; ".join(errors))

    if row["run_id"] in _existing_run_ids(project_dir):
        raise LedgerWriteError(
            f"run_id {row['run_id']!r} already recorded — refused (run_id uniqueness is "
            "enforced at the write boundary, AC-ALT-3; duplicates are prevented at the cause)")

    p = ledger_path(project_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def resolve_operator_for_ledger(explicit: str | None, project_dir: str | None = None) -> str:
    """Resolve `explicit` against `.claude/foundry-operators.json` (AC-ALT-2). Unresolvable —
    no registry, an unregistered id, or nothing supplied at all — records as
    `agent:<identity>`, NEVER silently coerced to a registered alias. `identity` is the best
    available raw hint: the explicit value passed, else `$FOUNDRY_OPERATOR`, else `$USER`,
    else the literal `unknown`."""
    identity = explicit or os.environ.get("FOUNDRY_OPERATOR") or os.environ.get("USER") or "unknown"
    try:
        import foundry_authz as az  # noqa: E402  (sys.path bootstrapped above)
    except ImportError:
        return f"agent:{identity}"
    try:
        return az.resolve_operator(explicit, project_dir)
    except az.AuthzError:
        return f"agent:{identity}"
