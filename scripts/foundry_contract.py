"""foundry_contract — acceptance-contract.yaml parsing, byte-canonical hashing, and
freeze-gate semantic checks. The shared library behind /foundry:authorize (0b),
foundry-emit-provenance (0c), and foundry-merge-gate.py (0d).

Design refs: §22.4b (schema, freeze gates, byte-canonical hashing, normative-region
spec hash), §22.6 (authorization state machine, monotonic baseline). Two build-time
resolutions of design self-references are documented inline:

  1. contract_sha256 self-reference. §22.4b says the `authorized:` block is committed
     INSIDE acceptance-contract.yaml AND that contract_sha256 hashes the contract —
     circular. Resolved by a sentinel split: contract_sha256 hashes only the
     contract-proper region (bytes above the FOUNDRY-AUTHORIZED-TRAILER sentinel);
     the `authorized:` attestation lives below it and is excluded. The whole file
     still parses as one YAML document (the sentinel is a YAML comment).

  2. Newline canonicalization. For the gate's raw[:sentinel_offset] to byte-match the
     bytes /foundry:authorize hashed, the contract-proper is normalized to end in
     exactly one '\\n' before hashing + before the sentinel is appended.

This module imports PyYAML (present in every adopter env that runs the live-seam walk).
jsonschema is imported opportunistically here, BUT its absence is no longer silent: it is a
declared dependency (requirements.txt + QUICKSTART) and /foundry:doctor fails closed on the
`schema-validator` check when it is missing, so the contract JSON-Schema floor cannot silently
degrade to the structural fallback (UL-0011). See _jsonschema_available + foundry-doctor.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import shlex
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("foundry_contract: PyYAML is required (pip install pyyaml)\n")
    raise

# Atom C (#121) — the system-grounding freeze floor consumes Atom A's snapshot builder
# (`foundry_system_snapshot.build_system_snapshot`). Bootstrap THIS module's own directory onto
# sys.path before the sibling import so the import resolves regardless of HOW foundry_contract.py
# itself was loaded (a plain `import foundry_contract` with scripts/ already on sys.path, OR a
# drop-in check's `importlib.util.spec_from_file_location` load, which does NOT put scripts/ on
# sys.path for us). `GroundingSourceError` is re-exported at module level (AC-SGC-7) so callers can
# catch `foundry_contract.GroundingSourceError` without importing the snapshot module directly.
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)
from foundry_system_snapshot import (  # noqa: E402
    build_system_snapshot as _build_system_snapshot,
    GroundingSourceError,
)

SENTINEL = "# === FOUNDRY-AUTHORIZED-TRAILER (excluded from contract_sha256) ==="
_SENTINEL_B = SENTINEL.encode("utf-8")

# Always-true regex bodies rejected as vacuous `matches` operands (§22.4b floor 2).
_VACUOUS_MATCHES = {".*", "^.*$", "(.*)", "^.*", ".*$", "", ".+", "^.+$"}

# Unbounded whole-repo globs rejected as an `allowed_paths` operand (§22.4c, F-B1). A
# lone `**` (or equivalent) makes diff-scope containment vacuous — EVERY changed file is
# "attributed". Interim bound until the full §22.4c footprint cross-check lands: the
# operator must name the atom's actual path prefixes, never a bare whole-repo wildcard.
_UNBOUNDED_GLOBS = {"**", "*", "**/*", "**/**", "/**", "/*", ".", "./**", "**/", "/", ""}


# --------------------------------------------------------------------------- #
# Byte-canonical hashing
# --------------------------------------------------------------------------- #
def _sentinel_offset(raw: bytes) -> int:
    """Byte offset of the sentinel line, or -1 if absent. The sentinel only counts
    when it starts a line (offset 0 or preceded by '\\n')."""
    start = 0
    while True:
        j = raw.find(_SENTINEL_B, start)
        if j == -1:
            return -1
        if j == 0 or raw[j - 1:j] == b"\n":
            return j
        start = j + 1


def split_contract_bytes(raw: bytes) -> tuple[bytes, bytes]:
    """(contract_proper_bytes, trailer_bytes). On an unauthorized contract (no
    sentinel) the whole file is the contract-proper and the trailer is empty."""
    idx = _sentinel_offset(raw)
    if idx == -1:
        return raw, b""
    return raw[:idx], raw[idx:]


def canonicalize_proper(proper: bytes) -> bytes:
    """Normalize the contract-proper to end in exactly one '\\n' so the hash is
    stable across the freeze transition regardless of the draft's trailing
    whitespace."""
    return proper.rstrip(b"\n") + b"\n"


def contract_sha256_bytes(raw: bytes) -> str:
    """contract_sha256 over the contract-proper region (sentinel-excluded).

    CANONICALIZES before hashing, exactly as `freeze_proper_and_trailer` does. The two MUST agree:
    `authorize()` hashes the draft here, writes the frozen bytes there, then asserts the recorded
    hash equals `contract_sha256_bytes(frozen)`. While only the writer canonicalized, any contract
    whose proper region ended in more than one newline failed that assertion with
    "internal: contract_sha256 unstable across freeze (newline canonicalization bug)" — a refusal
    that named a real bug, but located it in the caller's file rather than in this asymmetry.

    A FIRST authorize was immune: with no sentinel yet the whole file is the proper region, and its
    single trailing newline is already canonical. It took a RE-authorization — where a blank line
    left before the sentinel by an edit falls INSIDE the proper region — to expose it.

    No existing authorization is invalidated, and that holds BY CONSTRUCTION rather than by census:
    `freeze_proper_and_trailer` canonicalizes before appending the sentinel, so every contract this
    writer has ever frozen already has a canonical proper region, and `canonicalize_proper` is
    idempotent. The hash can therefore only move for a contract hand-edited after freezing to leave
    blank lines before the sentinel — which previously reported as a hash MISMATCH and now verifies
    clean. That is a deliberate widening of what a signature tolerates, not a neutral refactor.

    Sound only while trailing newlines carry no meaning. YAML's keep-chomping indicators (`|+`,
    `>+`) would break that, so `validate_contract_bytes` refuses them outright."""
    proper, _ = split_contract_bytes(raw)
    return hashlib.sha256(canonicalize_proper(proper)).hexdigest()


def contract_sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return contract_sha256_bytes(fh.read())


def freeze_proper_and_trailer(raw: bytes, trailer_yaml: str) -> bytes:
    """Produce the frozen file bytes: canonical contract-proper + sentinel + the
    authorized trailer. Used by /foundry:authorize (0b). The contract_sha256 the
    operator signs == contract_sha256_bytes(result)."""
    proper, _ = split_contract_bytes(raw)
    proper = canonicalize_proper(proper)
    body = trailer_yaml if trailer_yaml.endswith("\n") else trailer_yaml + "\n"
    return proper + _SENTINEL_B + b"\n" + body.encode("utf-8")


# --------------------------------------------------------------------------- #
# Spec normative-region hash (§22.4b — excludes §Changelog / cosmetic regions)
# --------------------------------------------------------------------------- #
_FENCE_OPEN = b"<!-- normative -->"
_FENCE_CLOSE = b"<!-- /normative -->"
_CHANGELOG_HEADING = re.compile(rb"(?m)^##\s+(?:\d+\.\s+)?Changelog\s*$")


def spec_normative_bytes(spec_path: str) -> bytes:
    raw = open(spec_path, "rb").read()
    # Preferred: explicit <!-- normative --> … <!-- /normative --> fences (concatenated).
    if _FENCE_OPEN in raw and _FENCE_CLOSE in raw:
        regions = []
        pos = 0
        while True:
            o = raw.find(_FENCE_OPEN, pos)
            if o == -1:
                break
            c = raw.find(_FENCE_CLOSE, o)
            if c == -1:
                break
            regions.append(raw[o + len(_FENCE_OPEN):c])
            pos = c + len(_FENCE_CLOSE)
        if regions:
            return b"\n".join(r.strip(b"\n") for r in regions) + b"\n"
    # Fallback: everything EXCEPT the Changelog section (heading → next top-level
    # '## ' heading at column 0, or EOF).
    m = _CHANGELOG_HEADING.search(raw)
    if not m:
        return raw
    nxt = re.compile(rb"(?m)^##\s").search(raw, m.end())
    end = nxt.start() if nxt else len(raw)
    return (raw[:m.start()] + raw[end:])


def spec_sha256(spec_path: str) -> str:
    return hashlib.sha256(spec_normative_bytes(spec_path)).hexdigest()


# --------------------------------------------------------------------------- #
# Parse + validate
# --------------------------------------------------------------------------- #
class ContractError(Exception):
    pass


def _temporal_to_iso(obj):
    """Recursively coerce datetime/date values to ISO-8601 strings (UL-0010). PyYAML
    auto-parses an UNQUOTED YAML timestamp (e.g. the `authorized_at: 2026-…T…Z` the
    authorize writer emits) into a Python datetime, which then violates the schema's
    `type: string` for authorized_at. We normalize at the load boundary so timestamps
    round-trip as strings regardless of how they were serialized. NOTE: authorized_at
    lives BELOW the FOUNDRY-AUTHORIZED-TRAILER sentinel and is excluded from
    contract_sha256 (and there is no timestamp field in the hashed contract-proper), so
    this normalization changes NO hash — it only affects the parsed dict used for
    schema/structural validation."""
    if isinstance(obj, dict):
        return {k: _temporal_to_iso(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_temporal_to_iso(v) for v in obj]
    # bool is an int subclass but not temporal; datetime is a subclass of date — order ok.
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    return obj


def _jsonschema_available() -> bool:
    try:
        import jsonschema  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def load_contract(path: str) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ContractError(f"YAML parse error: {e}")
    if not isinstance(data, dict):
        raise ContractError("contract root must be a mapping")
    return _temporal_to_iso(data)


def _ac_ids_of(cp: dict) -> list[str]:
    v = cp.get("ac_id")
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _schema_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema",
                        "acceptance-contract.schema.json")


def _jsonschema_check(data: dict) -> list[str]:
    """Opportunistic structural validation against the pinned JSON Schema. Returns
    [] when jsonschema is unavailable (the explicit checks below still run)."""
    try:
        import json
        import jsonschema  # type: ignore
    except ImportError:
        return []
    try:
        with open(_schema_path()) as fh:
            schema = json.load(fh)
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:  # type: ignore
        return [f"schema: {e.message} (at {'/'.join(str(p) for p in e.absolute_path)})"]
    except Exception as e:  # pragma: no cover
        return [f"schema: could not validate ({e})"]
    return []


def validate_contract(path: str, spec_ac_ids: list[str] | None = None) -> tuple[bool, list[str], list[str]]:
    """Validate one acceptance-contract.yaml at `path`. Thin reader over
    validate_contract_bytes (so the merge gate can re-validate pinned bytes — F-F1)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return False, [f"cannot read contract: {e}"], []
    return validate_contract_bytes(raw, spec_ac_ids)


def validate_contract_bytes(raw: bytes, spec_ac_ids: list[str] | None = None) -> tuple[bool, list[str], list[str]]:
    """Validate one acceptance-contract.yaml from its raw committed bytes. Returns
    (ok, errors, warnings).

    Runs (a) opportunistic JSON-Schema structural validation, then (b) the freeze
    gates §22.4b floors (1)-(4) + the §22.4c scope floor that a schema cannot express.
    Floor (5) AUTO-HARD classification needs spec/impl complexity signals and is
    enforced at the merge gate (0d), not here — noted as a warning so it is never
    silently assumed done.

    Operating on bytes (not a path) lets the merge gate re-run the SAME freeze +
    integrity floors over the pinned contract bytes it already resolved (F-F1) — the
    gate is then not merely a hash-consistency checker but an independent re-validator
    of contract well-formedness.

    `spec_ac_ids` (the spec's declared AC IDs) enables the bidirectional bijection
    floor (3). When omitted (e.g. at the merge gate, which has the contract but not the
    resolved spec), bijection is checked only for internal consistency and a warning is
    emitted — never silently assumed satisfied.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return False, [f"YAML parse error: {e}"], []
    if not isinstance(data, dict):
        return False, ["contract root must be a mapping"], []
    data = _temporal_to_iso(data)  # UL-0010: unquoted YAML timestamps → ISO strings before schema check

    errors += _jsonschema_check(data)
    # UL-0011: the JSON-Schema floor is OPPORTUNISTIC (skipped when jsonschema is absent).
    # That silent degradation masked UL-0010 locally (jsonschema absent in the operator env,
    # present in CI). Make the reduced-coverage state LOUD so a vacuous schema floor can never
    # again pass unnoticed — the hand-rolled freeze floors 1-4 below still run regardless.
    if not _jsonschema_available():
        warnings.append("schema floor in structural-fallback mode: jsonschema not installed "
                        "(`pip install jsonschema`) — full JSON-Schema validation skipped; "
                        "hand-rolled freeze floors 1-4 still enforced (UL-0011)")

    # INTEGRITY (authorization-sentinel injection guard). contract_sha256 hashes only
    # the contract-proper region (bytes above the sentinel). A sentinel injected early
    # in the body would exclude later checkpoints/scope from the hash while YAML still
    # parses + displays them — a silent integrity bypass. So every normative field MUST
    # live entirely within the hashed region: compare the full-doc fields against the
    # fields parsed from the contract-proper region alone.
    _proper, _ = split_contract_bytes(raw)
    try:
        _proper_data = yaml.safe_load(_proper)
    except yaml.YAMLError:
        _proper_data = None
    if isinstance(_proper_data, dict):
        _proper_data = _temporal_to_iso(_proper_data)  # UL-0010: match the normalized full-doc data
    if not isinstance(_proper_data, dict):
        errors.append("integrity: contract-proper region (above the authorization "
                      "sentinel) is not a valid YAML mapping")
    else:
        if "authorized" in _proper_data:
            errors.append("integrity: an 'authorized:' block appears ABOVE the sentinel "
                          "(it must live only in the excluded trailer)")
        for _k in ("spec_ref", "spec_sha256", "target_repo", "scope", "checkpoints", "system_grounding",
                   "preconditions", "build_gates", "post_apply_checks", "mandatory_review",
                   "drift_policy"):
            if data.get(_k) != _proper_data.get(_k):
                errors.append(f"integrity: field '{_k}' is not fully within the hashed "
                              f"contract-proper region (authorization-sentinel injection?)")

    # `contract_sha256` canonicalizes the contract-proper region's TRAILING NEWLINES away
    # (canonicalize_proper), which is sound only while trailing newlines are semantics-free. In YAML
    # they are not: a KEEP-CHOMPED block scalar (`|+` / `>+`) makes trailing empty lines part of the
    # scalar's VALUE. A contract ending in such a scalar could therefore differ in a frozen field's
    # real value while hashing identically — a collision inside the signed region, invisible to the
    # sentinel-injection check above (both parses see the same mutated value).
    #
    # Closed here rather than disclosed as a bound: a chomping indicator has no legitimate use in an
    # acceptance contract, and refusing it restores "trailing newlines carry no meaning" as a fact
    # about this file format rather than an assumption about its authors. Zero contracts in the
    # corpus use one, so this refuses nothing that exists.
    _proper_text = _proper.decode("utf-8", errors="replace")
    for _lineno, _line in enumerate(_proper_text.splitlines(), start=1):
        _uncommented = _line.split("#", 1)[0]
        if re.search(r"[|>]\+", _uncommented):
            errors.append(
                f"integrity: line {_lineno} uses a keep-chomped block scalar ('|+' or '>+'), whose "
                "trailing blank lines are part of the value. contract_sha256 normalizes trailing "
                "newlines away, so this would let two contracts with different frozen values share "
                "one signature. Use '|' or '>' instead."
            )

    # AC-SGC-1 (system-grounding block shape). UNCONDITIONAL: runs regardless of any snapshot —
    # this function is byte-pure and is also re-run at the merge gate, so a malformed
    # `system_grounding` block must never slip through under an unconfigured/None snapshot. Only
    # the CONSISTENCY floor (system_grounding_errors, below) is gated on grounding_configured.
    errors += _system_grounding_structural_errors(data)

    # Minimal hand-rolled structural floor (so the gate works without jsonschema).
    for key in ("spec_ref", "spec_sha256", "scope", "checkpoints"):
        if key not in data:
            errors.append(f"missing required field: {key}")
    scope = data.get("scope") or {}
    allowed = scope.get("allowed_paths") if isinstance(scope, dict) else None
    if not (isinstance(allowed, list) and len(allowed) >= 1):
        errors.append("scope.allowed_paths must be a non-empty list (§22.4c)")
    else:
        # F-B1: reject a bare whole-repo glob — it makes diff-scope containment vacuous.
        unbounded = [g for g in allowed if isinstance(g, str) and g.strip() in _UNBOUNDED_GLOBS]
        if unbounded:
            errors.append(f"scope.allowed_paths floor (§22.4c): unbounded whole-repo glob(s) "
                          f"{unbounded} — scope must be bounded (no bare '**'/'*'); name the "
                          f"atom's actual path prefixes")

    checkpoints = data.get("checkpoints")
    if not (isinstance(checkpoints, list) and len(checkpoints) >= 1):
        errors.append("freeze floor 1: checkpoints must be NON-EMPTY (§22.4b)")
        return False, errors, warnings  # nothing further to check

    # Floor 1 (cont.): spec carries >=1 AC id.
    if spec_ac_ids is not None and len(spec_ac_ids) < 1:
        errors.append("freeze floor 1: spec declares zero AC IDs — not implementable-with-evidence (§22.4b)")

    cp_ac_union: set[str] = set()
    has_pre_change = False
    for i, cp in enumerate(checkpoints):
        if not isinstance(cp, dict):
            errors.append(f"checkpoint[{i}] must be a mapping")
            continue
        ids = _ac_ids_of(cp)
        if not ids:
            errors.append(f"checkpoint[{i}] declares no ac_id (coverage floor 3)")
        cp_ac_union.update(ids)

        expect = cp.get("expect")
        if not isinstance(expect, dict):
            errors.append(f"checkpoint[{i}].expect missing/invalid")
            continue
        op = expect.get("op")
        val = expect.get("value")
        baseline = expect.get("baseline")
        # Floor 2: non-trivial POSITIVE lower bound.
        if op == "count_gte":
            if not (isinstance(val, int) and val >= 1):
                errors.append(f"checkpoint[{i}] freeze floor 2: count_gte must be >=1, got {val!r} (no count_gte:0)")
        elif op == "matches":
            if not isinstance(val, str) or val.strip() in _VACUOUS_MATCHES:
                errors.append(f"checkpoint[{i}] freeze floor 2: matches operand is vacuous/always-true ({val!r})")
        elif op == "equals":
            if val is None or (isinstance(val, str) and val == ""):
                errors.append(f"checkpoint[{i}] freeze floor 2: equals requires a non-empty value")
        elif op == "non-empty":
            pass  # inherently a positive lower bound
        else:
            errors.append(f"checkpoint[{i}] expect.op invalid: {op!r}")
        if baseline == "pre-change":
            has_pre_change = True
        elif baseline != "none":
            errors.append(f"checkpoint[{i}] expect.baseline must be pre-change|none, got {baseline!r}")

    # Floor 4: at least one attributable (pre-change) checkpoint.
    if not has_pre_change:
        errors.append("freeze floor 4: no checkpoint asserts change-attributable output (baseline: pre-change) (§22.4b)")

    # Floor 3: bidirectional AC-ID coverage.
    if spec_ac_ids is not None:
        spec_set = set(spec_ac_ids)
        uncovered = spec_set - cp_ac_union
        orphan = cp_ac_union - spec_set
        if uncovered:
            errors.append(f"freeze floor 3: spec AC IDs with no checkpoint: {sorted(uncovered)}")
        if orphan:
            errors.append(f"freeze floor 3: checkpoint ac_ids not in spec: {sorted(orphan)} (padding/orphan)")
    else:
        warnings.append("freeze floor 3 (bijection) not fully checked: spec AC IDs not provided "
                        "(pass --spec to resolve)")

    warnings.append("freeze floor 5 (AUTO-HARD classification) is enforced at the merge floor, "
                    "not at standalone validation")

    return (len(errors) == 0), errors, warnings


# ── Atom C (#121) — system-grounding contract block + authorize freeze floor ───────────────────────
#
# The DECLARED side of the reality-grounding gate: an optional, frozen, hash-covered
# `system_grounding` block (per-artifact `exists|alter|net-new` + a live `identifier`) validated
# against Atom A's system-state snapshot (`foundry_system_snapshot.build_system_snapshot`) at
# `/foundry:authorize` FREEZE time. Two independently-gated floors:
#   - AC-SGC-1 structural validation (`_system_grounding_structural_errors`) is UNCONDITIONAL —
#     called from `validate_contract_bytes` above, byte-pure, re-run at the merge gate.
#   - AC-SGC-3/-4/-5/-6/-8 consistency validation (`system_grounding_errors`) is a FREEZE-TIME-ONLY
#     floor gated on `grounding_configured` (AC-SGC-6) — it needs the live snapshot, so it is
#     deliberately NOT called from `validate_contract_bytes` (mirrors ER #77's surface⊆scope floor,
#     which is also freeze-only for the identical reason: the merge gate's re-validation stays
#     byte-pure, no live read). `foundry-authorize.py`'s "2.6" block calls it directly.

_SG_KINDS = {"table", "column", "fk", "module", "queue", "event", "resource"}
_SG_GROUNDED_KINDS = {"table", "column", "module"}
_SG_CLASSIFICATIONS = {"exists", "alter", "net-new"}


def _system_grounding_structural_errors(data: dict) -> list[str]:
    """AC-SGC-1: structural validation of a PRESENT `system_grounding` block. Returns [] when the
    block is absent (it is optional). UNCONDITIONAL — the caller (`validate_contract_bytes`) runs
    this regardless of `grounding_configured`/snapshot; a malformed block is always rejected."""
    errors: list[str] = []
    sg = (data or {}).get("system_grounding")
    if sg is None:
        return errors
    if not isinstance(sg, dict) or "artifacts" not in sg:
        errors.append("system_grounding: block present but omits required 'artifacts' key (AC-SGC-1)")
        return errors
    artifacts = sg.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append(f"system_grounding.artifacts must be a list, got {type(artifacts).__name__} (AC-SGC-1)")
        return errors

    seen_pairs: set[tuple] = set()
    for i, art in enumerate(artifacts):
        if not isinstance(art, dict):
            errors.append(f"system_grounding.artifacts[{i}] must be a mapping (AC-SGC-1)")
            continue

        for key in ("kind", "identifier", "classification"):
            if key not in art:
                errors.append(f"system_grounding.artifacts[{i}] missing required key {key!r} (AC-SGC-1)")

        kind = art.get("kind")
        identifier = art.get("identifier")
        classification = art.get("classification")

        if "kind" in art and kind not in _SG_KINDS:
            errors.append(f"system_grounding.artifacts[{i}] unknown kind {kind!r} (AC-SGC-1)")
        if "classification" in art and classification not in _SG_CLASSIFICATIONS:
            errors.append(f"system_grounding.artifacts[{i}] unknown classification {classification!r} (AC-SGC-1)")
        if "identifier" in art and (not isinstance(identifier, str) or not identifier.strip()):
            errors.append(f"system_grounding.artifacts[{i}] identifier must be a non-empty string, "
                          f"got {identifier!r} (AC-SGC-1)")
        if "source_ref" in art:
            sref = art.get("source_ref")
            if not isinstance(sref, str) or not sref.strip():
                errors.append(f"system_grounding.artifacts[{i}] source_ref present but "
                              f"empty/non-string: {sref!r} (AC-SGC-1)")
        if kind == "column" and isinstance(identifier, str) and identifier.count("::") != 1:
            errors.append(f"system_grounding.artifacts[{i}] column identifier {identifier!r} must "
                          f"contain exactly one '::' separator (<entity>::<column>) (AC-SGC-1)")

        if isinstance(kind, str) and isinstance(identifier, str) and identifier.strip():
            pair = (kind, identifier)
            if pair in seen_pairs:
                errors.append(f"system_grounding.artifacts[{i}] duplicate (kind, identifier) pair "
                              f"{pair!r} — already declared elsewhere in the block (AC-SGC-1)")
            else:
                seen_pairs.add(pair)

    return errors


def _sg_identifier_matches(kind, identifier: str, entities: dict, module_set: set) -> bool:
    """The NORMATIVE identifier-match predicate (spec §Acceptance-criteria, "Identifier-match
    predicate"). `table` -> key of entities; `module` -> exact member of modules; `column` ->
    `<entity>::<col>` where entity is a key AND col is in that entity's columns. Ungrounded kinds
    (fk/queue/event/resource) are never evaluated here (their sole exposure is the AC-SGC-3(b)
    cross-dimension collision catch, applied by the caller)."""
    if kind == "table":
        return identifier in entities
    if kind == "module":
        return identifier in module_set
    if kind == "column":
        if identifier.count("::") != 1:
            return False
        entity, col = identifier.split("::", 1)
        return entity in entities and col in (entities.get(entity, {}).get("columns") or [])
    return False


def system_grounding_errors(contract_data: dict, snapshot: "dict | None") -> list[str]:
    """AC-SGC-3/-4/-5/-6/-8: the CONSISTENCY floor — validates a `system_grounding` block against
    Atom A's live snapshot. Freeze-time only (needs the snapshot); NOT byte-pure, NOT called from
    `validate_contract_bytes`.

    AC-SGC-6: `snapshot is None` or `grounding_configured` falsy -> inert no-op, `[]` (structural
    validation already ran unconditionally in `validate_contract_bytes`).
    AC-SGC-5: grounding configured + no `system_grounding` block -> one error (author must declare
    intent; an empty `artifacts: []` block passes).
    AC-SGC-3/-4/-8: per-artifact reconciliation against the identifier-match predicate above.
    """
    if snapshot is None or not snapshot.get("grounding_configured"):
        return []

    errors: list[str] = []
    sg = (contract_data or {}).get("system_grounding")
    if sg is None:
        errors.append("system_grounding: grounding is configured (a schema/module source is live) "
                      "but the contract carries no system_grounding block (AC-SGC-5) — declare "
                      "intent for every touched artifact (an empty 'artifacts: []' block is valid "
                      "when the atom touches no grounded artifact)")
        return errors
    if not isinstance(sg, dict):
        return errors  # structural floor already rejects this shape
    artifacts = sg.get("artifacts")
    if not isinstance(artifacts, list):
        return errors  # structural floor already rejects this shape

    entities = snapshot.get("entities") or {}
    modules = snapshot.get("modules") or []
    entity_keys = set(entities.keys())
    module_set = set(modules)

    for art in artifacts:
        if not isinstance(art, dict):
            continue
        kind = art.get("kind")
        identifier = art.get("identifier")
        classification = art.get("classification")
        if not isinstance(identifier, str) or not identifier.strip():
            continue  # structural floor already rejects this artifact

        matched = _sg_identifier_matches(kind, identifier, entities, module_set)
        # AC-SGC-3(b): cross-dimension mislabel catch — kind-independent, whole-string collision.
        cross = identifier in entity_keys or identifier in module_set

        if classification == "net-new":
            if matched or cross:
                errors.append(
                    f"system_grounding: artifact kind={kind!r} identifier={identifier!r} declared "
                    f"net-new but is already live in the snapshot (re-ground to 'alter' or 'exists') "
                    f"(AC-SGC-3)"
                )
        elif classification in ("exists", "alter"):
            if kind in _SG_GROUNDED_KINDS and not matched:
                errors.append(
                    f"system_grounding: artifact kind={kind!r} identifier={identifier!r} declared "
                    f"{classification!r} but is absent from the live snapshot (AC-SGC-4)"
                )
            # ungrounded kinds (fk/queue/event/resource): predicate not evaluated (AC-SGC-8).

    return errors


def resolve_grounding_snapshot(project_dir: "str | None"):
    """AC-SGC-7 fail-closed seam. Delegates to Atom A's `build_system_snapshot(project_dir)` and
    PROPAGATES `GroundingSourceError` (never swallowed) — `foundry-authorize.py`'s 2.6 block
    delegates to THIS helper (rather than inlining the try/except) so the drop-in check can drive
    the fail-closed path directly (AC-SGC-9(g)) without invoking authorize.py's `main()`."""
    return _build_system_snapshot(project_dir=project_dir)


# ── ER #77 — authorize-time surface⊆scope consistency (feat-foundry-contract-surface-scope) ──────────
#
# A checkpoint surface `test:<p>` / `file:<p>` whose path-shaped <p> does NOT exist under the resolved
# venue root — i.e. the BUILD must create it — must fall within scope.allowed_paths, or the contract is
# internally inconsistent (the build would touch a file the atom does not permit; for consumer repos the
# merge gate short-circuits and nothing else catches it). EXISTENCE-AWARE by design: a surface may
# legitimately reference a PRE-EXISTING test file the atom runs but does not modify (95 such surfaces in
# the corpus), and a `test:<key>` without a `/` is a surfaces-map SUITE KEY, not a path — both pass.
# Enforced at the foundry-authorize FREEZE (pre-build, venue-independent); deliberately NOT added to
# validate_contract_bytes (the gate's post-build 1b re-validation would see the file existing).

def _scope_glob_to_re(glob: str) -> "re.Pattern":
    """The merge gate's glob semantics (foundry_merge_gate._glob_to_re), mirrored so freeze-time
    containment and gate-time diff-scope agree byte-for-byte. Kept local — the gate imports THIS
    module, so importing it back would be circular."""
    out, i, n = [], 0, len(glob)
    while i < n:
        c = glob[i]
        if glob[i:i + 3] == "**/":
            out.append(r"(?:.*/)?"); i += 3; continue
        if glob[i:i + 2] == "**":
            out.append(r".*"); i += 2; continue
        if c == "*":
            out.append(r"[^/]*")
        elif c == "?":
            out.append(r"[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


_PATH_SURFACE_RE = re.compile(r"^(test|file):(.+)$")


def surface_scope_errors(data: dict, repo_root: str | None) -> list[str]:
    """AC-CSSF-1/-2 (ER #77): one clear error per checkpoint whose surface names a NEW path-typed
    artifact outside the atom's own scope. Pure — reads the contract dict + stats files under
    repo_root; executes nothing. Returns [] when repo_root is None (existence unknowable — the
    CALLER degrades to a warning, never a block on a missing multi-repo clone)."""
    if repo_root is None:
        return []
    errors: list[str] = []
    scope = (data or {}).get("scope") or {}
    allowed = [g for g in (scope.get("allowed_paths") or []) if isinstance(g, str)]
    allowed_res = [_scope_glob_to_re(g) for g in allowed]
    for i, cp in enumerate((data or {}).get("checkpoints") or []):
        surf = (cp or {}).get("surface")
        if not isinstance(surf, str):
            continue
        m = _PATH_SURFACE_RE.match(surf.strip())
        if not m:
            continue
        p = m.group(2).strip()
        if "/" not in p:
            continue  # a surfaces-map suite KEY (e.g. test:auth), not a path
        if p.startswith("/") or ".." in p.split("/"):
            errors.append(f"checkpoint[{i}] surface {surf!r}: absolute/traversal path — rejected")
            continue
        if os.path.exists(os.path.join(repo_root, p)):
            continue  # pre-existing file: the checkpoint RUNS it, the build does not create it
        if not any(rx.match(p) for rx in allowed_res):
            errors.append(
                f"checkpoint[{i}] (ac_id {cp.get('ac_id')!r}) surface {surf!r}: names a NEW file "
                f"absent from the venue root that is OUTSIDE scope.allowed_paths — the build could "
                f"not create it within the atom's own scope (ER #77); seed {p!r} into allowed_paths "
                f"(and re-check) or fix the surface")
    return errors


# ── ER #178 fix 1 — authorize-time doctor-row-baseline consistency (feat-foundry-offline-cli-
# walk-partition, AC-OCW-4) ────────────────────────────────────────────────────────────────────
#
# An atom that EXTENDS an existing drop-in check tends to freeze a `cli:foundry-doctor` `[ok ] <x>`
# checkpoint at `baseline: pre-change` for a check <x> its own scope.allowed_paths EDITS. That
# doctor row PRE-EXISTS (the check is already registered, already GREEN on the merge-base) so it
# can NEVER be RED-on-base — freezing it at `baseline: pre-change` is a structurally non-
# attributable freeze (the walk-verdict's per-AC any-flip floor, AC-OCW-2/-3, would then need a
# SIBLING locator to flip; this floor catches the mistake earlier, at freeze time). Modelled on
# `surface_scope_errors` immediately above: existence-aware, pure (reads the contract dict + the
# repo_root filesystem only), returns [] when repo_root is None (existence unknowable — the CALLER
# degrades to a warning, never a block on a missing multi-repo clone — mirrors ER #77 exactly).

_DOCTOR_OK_ROW_RE = re.compile(r"^\[ok \]\s*([a-z0-9-]+)")


def doctor_row_baseline_errors(data: dict, repo_root: str | None) -> list[str]:
    """AC-OCW-4: one clear error per checkpoint that freezes a `cli:foundry-doctor` `[ok ] <x>` row
    at `baseline: pre-change` for a check <x> whose backing `scripts/foundry_checks/<x>.py` (a)
    pre-exists under the venue root AND (b) is named in the atom's OWN `scope.allowed_paths` (i.e.
    the atom EDITS it, not merely observes an unrelated pre-existing check). Both conditions must
    hold — a net-new check file (absent pre-change) is NOT subject to this rejection, and a
    pre-existing check the atom's scope does NOT touch is out of this floor's scope (a different
    atom editing it is not this atom's freeze mistake). Pure — reads the contract dict + repo_root
    filesystem; executes nothing. Returns [] when repo_root is None (existence unknowable — the
    CALLER degrades to a warning, mirroring `surface_scope_errors` / ER #77)."""
    if repo_root is None:
        return []
    errors: list[str] = []
    scope = (data or {}).get("scope") or {}
    allowed = [g for g in (scope.get("allowed_paths") or []) if isinstance(g, str)]
    allowed_res = [_scope_glob_to_re(g) for g in allowed]
    for i, cp in enumerate((data or {}).get("checkpoints") or []):
        surf = (cp or {}).get("surface")
        if surf != "cli:foundry-doctor":
            continue
        exp = (cp or {}).get("expect") or {}
        if exp.get("baseline") != "pre-change":
            continue
        val = exp.get("value")
        if not isinstance(val, str):
            continue
        m = _DOCTOR_OK_ROW_RE.match(val.strip())
        if not m:
            continue  # not an `[ok ] <x>` doctor-row assertion — out of this floor's scope
        check_name = m.group(1)
        rel_path = f"scripts/foundry_checks/{check_name}.py"
        if not any(rx.match(rel_path) for rx in allowed_res):
            continue  # the atom's own scope does not edit this check file — not this atom's mistake
        if os.path.exists(os.path.join(repo_root, rel_path)):
            errors.append(
                f"checkpoint[{i}] (ac_id {cp.get('ac_id')!r}) surface {surf!r}: freezes doctor row "
                f"'[ok ] {check_name}' at baseline: pre-change, but {rel_path} pre-exists under the "
                f"venue root and is inside this atom's scope.allowed_paths (the atom EDITS an "
                f"existing check) — the row is GREEN-on-both-sides by construction and can never be "
                f"RED on the merge-base (ER #178); use baseline: none, or an expect.value that "
                f"appears only post-change")
    return errors


# ── ER #179 — authorize-time allowed_paths reality-grounding (feat-foundry-authorize-allowed-paths-
# ground) ───────────────────────────────────────────────────────────────────────────────────────
#
# A frozen `scope.allowed_paths` entry is the one contract field the merge gate actually enforces
# set-containment against (CHECK-4, diff-scope containment). A stale prefix (`bin/…` where the real
# path is `scripts/…`) or a typo in that field was previously undetectable at authorize time and
# surfaced only at merge, after the build was complete. This floor grounds every `allowed_paths`
# entry against the resolved venue root AT FREEZE TIME, mirroring `surface_scope_errors` (ER #77)
# and `system_grounding_errors` (Atom C / #121) exactly: pure, read-only, executes nothing, and
# degrades to `[]` (the CALLER prints a non-fatal warning) when `repo_root` is None — never wedging
# authorization on a missing multi-repo clone.

_GLOB_META_RE = re.compile(r"[*?]")


def _checkpoint_path_surfaces(data: dict) -> set:
    """The set of path-shaped checkpoint surface targets (`test:<p>`/`file:<p>`, `<p>` containing a
    '/', absolute/traversal excluded) — the SAME shape `surface_scope_errors` (ER #77) parses.
    Reused here for AC-APG-3's infer-from-checkpoint tolerance: a `scope.allowed_paths` entry absent
    from the venue root is admitted as declared-new ONLY when its exact path is named by one of
    these surfaces."""
    paths: set = set()
    for cp in (data or {}).get("checkpoints") or []:
        surf = (cp or {}).get("surface")
        if not isinstance(surf, str):
            continue
        m = _PATH_SURFACE_RE.match(surf.strip())
        if not m:
            continue
        p = m.group(2).strip()
        if "/" not in p:
            continue  # a surfaces-map suite KEY (e.g. test:auth), not a path
        if p.startswith("/") or ".." in p.split("/"):
            continue  # absolute/traversal-shaped surface — never admitted as a declared-new path
        paths.add(p)
    return paths


def _glob_has_match(glob_entry: str, repo_root: str) -> bool:
    """True iff `glob_entry` (containing `*`/`?`) matches >=1 path under repo_root, using the SAME
    glob semantics (`_scope_glob_to_re`) the merge gate enforces byte-for-byte. Walks the tree once
    per glob entry (bounded by an atom's small allowed_paths list); skips .git."""
    rx = _scope_glob_to_re(glob_entry)
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel_dir = os.path.relpath(dirpath, repo_root).replace(os.sep, "/")
        if rel_dir != "." and rx.match(rel_dir):
            return True
        for fn in filenames:
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            if rx.match(rel):
                return True
    return False


def _allowed_path_exists(entry: str, repo_root: str) -> bool:
    """AC-APG-1: EXISTS classification — a literal path (file or directory) via `os.path.exists`, or
    a glob (contains `*`/`?`) with >=1 filesystem match under repo_root via `_glob_has_match`.

    sec-review hardening: an absolute or `..`-traversal LITERAL entry is never EXISTS-classified —
    `os.path.join(repo_root, entry)` silently discards `repo_root` for an absolute `entry` (Python's
    documented `os.path.join` semantics), and a `..`-segment resolves outside `repo_root` either way;
    both would otherwise stat a path OUTSIDE the venue and could falsely admit it. Mirrors the exact
    absolute/traversal predicate `_checkpoint_path_surfaces` / `surface_scope_errors` already use —
    such an entry falls through to the caller's fail-closed branch (AC-APG-2). The glob branch is
    already traversal-safe (`_glob_has_match` only ever tests `os.walk`-derived relpaths, which never
    start with `/` or contain `..`)."""
    if _GLOB_META_RE.search(entry):
        return _glob_has_match(entry, repo_root)
    if entry.startswith("/") or ".." in entry.split("/"):
        return False  # absolute/traversal literal — never EXISTS-classified (fails closed upstream)
    return os.path.exists(os.path.join(repo_root, entry))


def allowed_paths_grounding_errors(data: dict, repo_root: "str | None") -> list[str]:
    """AC-APG-1..3 (#179): reality-ground every `scope.allowed_paths` entry against repo_root. Pure,
    read-only, executes nothing. Returns [] when repo_root is None (AC-APG-4 — the CALLER degrades
    to a non-fatal warning, mirroring `surface_scope_errors` / `system_grounding_errors`).

    An entry is admitted when it EXISTS (AC-APG-1: a literal path/dir, or a glob matching >=1 path
    under repo_root) OR is named by one of the atom's own path-shaped checkpoint surfaces (AC-APG-3,
    the infer-from-checkpoint tolerance — NOT a soundness guarantee; see the spec Residuals +
    ER #184 for the uncaught both-places-consistent-typo class). Any other entry fails CLOSED
    (AC-APG-2), naming the offending entry."""
    if repo_root is None:
        return []
    errors: list[str] = []
    scope = (data or {}).get("scope") or {}
    allowed = [g for g in (scope.get("allowed_paths") or []) if isinstance(g, str)]
    checkpoint_paths = _checkpoint_path_surfaces(data)
    for entry in allowed:
        if _allowed_path_exists(entry, repo_root):
            continue  # AC-APG-1
        if entry in checkpoint_paths:
            continue  # AC-APG-3: checkpoint-named declared-new tolerance
        errors.append(
            f"scope.allowed_paths entry {entry!r}: matches ZERO paths under the venue root and is "
            f"not named by any of the atom's own path-shaped checkpoint surfaces (file:/test:) — a "
            f"stale path prefix or typo (ER #179); fix the path, or seed a checkpoint surface naming "
            f"it (declared-new) and re-check"
        )
    return errors


# ── feat-foundry-walk-locator-executability (AC-WLE-4/-5) — authorize-time LOCATOR sanity ──────
#
# The FOURTH sibling floor. `surface_scope_errors` (ER #77) and `doctor_row_baseline_errors`
# (ER #178) validate a checkpoint's `surface`; `allowed_paths_grounding_errors` (ER #179) validates
# `scope.allowed_paths`. NOTHING validated the `locator` field — so a typo'd
# `python3 scripts/foundry_checks/<typo>.py --selftest` froze into the contract unchecked and only
# manifested at the walk, where (per the sibling runtime half in `worker-dod-self-gate.py`) a Python
# interpreter that cannot open its script file exits 2 — NOT 127 — and 2 is deliberately OUTSIDE the
# UNEXECUTABLE set. The two halves are therefore COMPLEMENTARY, not redundant: the runtime rule
# catches the dead-COMMAND class (the shell's 127) this floor cannot see (it grounds script PATHS,
# not bare command names); this floor catches the typo'd-script-PATH class the runtime rule cannot
# see. Shift-left, exactly as GitHub Actions rejects an unknown `uses:` at parse and `terraform
# validate` grounds references without applying.
#
# Same signature, same purity ("executes nothing" — tokenization + path existence ONLY), and the same
# `repo_root is None -> []` degrade (the SOLE fail-open: existence is unknowable on a missing
# multi-repo clone; the CALLER prints a visible non-fatal warning) as all three siblings.

_CLI_SURFACE_PREFIX = "cli:"


def _is_locator_script_ref(token: str) -> bool:
    """LOCATOR-SCRIPT-REF classification: a `shlex` token confidently classifiable as an IN-REPO
    script path — it (a) contains a '/', (b) is neither absolute nor carries a '..' segment, and
    (c) ends in '.py' or '.sh'.

    The absolute/traversal predicate is the SAME one `surface_scope_errors` / `_allowed_path_exists`
    already apply: such a token is never classified as an in-repo ref, so this floor never stats a
    path OUTSIDE the venue root (`os.path.join(repo_root, '/abs')` silently discards repo_root).
    A bare command name (`pytest`, `python3`, `mytypo`) is NOT a LOCATOR-SCRIPT-REF and asserts
    nothing here — that class is covered at RUNTIME by the 127 rule (spec Residuals)."""
    if not isinstance(token, str) or "/" not in token:
        return False
    if token.startswith("/") or ".." in token.split("/"):
        return False
    return token.endswith(".py") or token.endswith(".sh")


def locator_grounding_errors(data: dict, repo_root: "str | None") -> list[str]:
    """AC-WLE-4/-5: one clear error per `cli:*` checkpoint whose `locator` (a) carries a
    LOCATOR-SCRIPT-REF that neither EXISTS under repo_root NOR is admitted by any
    `scope.allowed_paths` entry of the SAME contract (the declared-new case, using the SAME
    `_scope_glob_to_re` glob semantics the merge gate enforces), or (b) cannot be tokenized at all
    (`shlex` raises on an unbalanced quote — an unknown shape CONVICTS, never exonerates).

    Pure: tokenizes strings and stats paths under repo_root; EXECUTES NOTHING (no subprocess, no
    shell, no locator). Returns [] when repo_root is None (AC-WLE-5, the SOLE fail-open — the CALLER
    degrades to a visible non-fatal warning, mirroring `surface_scope_errors` / ER #77,
    `doctor_row_baseline_errors` / ER #178, `allowed_paths_grounding_errors` / ER #179)."""
    if repo_root is None:
        return []
    errors: list[str] = []
    scope = (data or {}).get("scope") or {}
    allowed = [g for g in (scope.get("allowed_paths") or []) if isinstance(g, str)]
    allowed_res = [_scope_glob_to_re(g) for g in allowed]
    for i, cp in enumerate((data or {}).get("checkpoints") or []):
        surf = (cp or {}).get("surface")
        if not isinstance(surf, str) or not surf.strip().startswith(_CLI_SURFACE_PREFIX):
            continue  # only `cli:*` checkpoints carry a shell locator this floor can ground
        locator = (cp or {}).get("locator")
        if not isinstance(locator, str) or not locator.strip():
            continue  # a missing/blank locator is the schema's + the runtime `checkpoint-malformed`
                      # branch's business, not this floor's
        try:
            tokens = shlex.split(locator)
        except ValueError as e:
            errors.append(
                f"checkpoint[{i}] (ac_id {cp.get('ac_id')!r}) locator {locator!r}: CANNOT BE "
                f"TOKENIZED ({e}) — an unparseable shell string (e.g. an unbalanced quote); an "
                f"unknown shape fails CLOSED (AC-WLE-4). Fix the locator and re-check")
            continue
        for tok in tokens:
            if not _is_locator_script_ref(tok):
                continue
            if os.path.exists(os.path.join(repo_root, tok)):
                continue  # the script pre-exists under the venue root — the checkpoint RUNS it
            if any(rx.match(tok) for rx in allowed_res):
                continue  # declared-new: the build creates it within the atom's OWN scope
            errors.append(
                f"checkpoint[{i}] (ac_id {cp.get('ac_id')!r}) locator {locator!r}: script path "
                f"token {tok!r} does NOT exist under the venue root and is NOT admitted by any "
                f"scope.allowed_paths entry — a typo'd or stale locator script path would freeze "
                f"into the contract and fail only at the walk (AC-WLE-4); fix the path, or add it "
                f"to allowed_paths (declared-new) and re-check")
    return errors
