#!/usr/bin/env python3
"""Deterministic host-side binder for the §8 audit engine (feat-foundry-audit-large-spec-binder, AC-ALB-2).

`scripts/foundry-audit-prepare.py <target>` is the path-based **Claim Check** loader the `/foundry:audit`
skill runs instead of hand-emitting the spec/contract bytes into `Workflow args`. The v0.9.1 decouple moved
fs I/O out of the fs-forbidden Workflow sandbox by requiring the skill main-loop (an LLM) to emit the FULL
spec text into `args.specText` — a fidelity wall at real spec sizes (ER #68: a 532-line/~39K-token spec drove
placeholder substitution + a garbage run). This binder removes that wall: pass a PATH; a DETERMINISTIC Python
read + `json.dumps` bind + round-trip verify produces a per-run copy of the shipped `workflows/spec-audit.js`
that is BYTE-IDENTICAL except a single injected `_args` binding, so the bytes never transit an LLM-emitted arg
or an agent return. The engine's control-plane is UNCHANGED — the binder pre-populates exactly the
`_args.specText`/`_args.contractText` the entrypoint already consumes; `workflows/spec-audit.js` is not edited.

Load-bearing properties (all four exercised by scripts/foundry_checks/audit-large-spec-binder.py):
  - Injection is ONE ADDED line, sentinel-delimited (`/*FOUNDRY-BIND-START*/…/*FOUNDRY-BIND-END*/`), of the
    form `Object.assign(_args, { … })`, inserted AFTER the engine's `const _args = …` declaration and BEFORE
    its `if (_args.__test)` branch (found by SEARCH, not a hardcoded line number). This parses (no duplicate
    `const _args`) and OVERRIDES any degenerate inline `args` a caller passes.
  - Data-not-code (JS-safe) escaping: values are `json.dumps(…, ensure_ascii=True)` double-quoted string
    literals — ALL non-ASCII escaped to `\\uXXXX` (U+2028/U+2029 too), so the JSON string literal is also a
    valid JS string literal; template-literal/backtick form is NEVER used, so `${…}` breakout is impossible.
  - Fail-closed by verifying the WRITTEN artifact: write to a temp name, RE-READ the written .js, re-extract
    the embedded specText/contractText literals, decode them, assert BYTE-FOR-BYTE equality against the
    on-disk source files — a Python-emit↔Python-decode identity check is tautological and would NOT catch a
    partial/truncated write, so the check reads the file on disk. Only on success: atomic rename into place +
    print scriptPath + exit 0. On ANY mismatch / unreadable target / write error: exit non-zero, no scriptPath.
  - The per-run script lives in a system-temp path OUTSIDE the repo tree (tempfile, per-run unique) — never a
    repo path (allowed_paths cannot add a .gitignore rule); concurrent audits never collide.

The round-trip/verify + the injection are exposed as importable functions so the drop-in selftest calls them
directly (no reliance on a CLI marker string).
"""
import json
import os
import re
import sys
import tempfile

# Atom B (#120, AC-ASG-4) — the system-grounding audit phase reuses Atom A's snapshot builder
# (`build_system_snapshot`) + Atom C's merged reconciliation (`system_grounding_errors`,
# `GroundingSourceError`), single source of truth = C (no re-implementation). Bootstrap THIS module's
# own directory onto sys.path before the sibling imports (mirrors `foundry_contract.py`'s own
# bootstrap of the SAME snapshot module) so the import resolves regardless of HOW
# foundry-audit-prepare.py itself was loaded (a plain `import` with scripts/ already on sys.path, OR a
# drop-in check's `importlib.util.spec_from_file_location` load, which does NOT put scripts/ on
# sys.path for us).
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)
from foundry_system_snapshot import build_system_snapshot as _build_system_snapshot  # noqa: E402
import foundry_contract as _fc  # noqa: E402
system_grounding_errors = _fc.system_grounding_errors      # re-exported for the drop-in check
GroundingSourceError = _fc.GroundingSourceError             # re-exported (Atom A's class, via C)
import foundry_audit_preconditions as _fap  # noqa: E402 -- feat-foundry-audit-preconditions (AC-APC-1..5)

# The keys the binder injects into the engine's `_args`, in a fixed emit order (AC-ALB-2 pinned form).
# `auditModel` is appended LAST of the original set (additive) so the positional decode in
# extract_bound_literals stays valid; Atom B (#120, AC-ASG-4) appends `systemGroundingFindings` +
# `systemState` LAST of all (additive again — the positional decode stays valid for BOTH generations).
# feat-foundry-audit-tier-caps (AC-ATC-1/-4/-6) appends `riskTier`, `watchdogTokenLimit`,
# `watchdogMsLimit`, `seedLedger` LAST of ALL (additive again — every prior generation's positional
# decode stays valid; `riskTier` is a DISTINCT field from `auditModel` — the risk tier T0-T3, never to
# be confused with the audit-model alias opus/sonnet/haiku/fable).
BIND_KEYS = ["target", "specText", "contractText", "max_pass", "reality_sampling", "auditModel",
            "systemGroundingFindings", "systemState",
            "riskTier", "watchdogTokenLimit", "watchdogMsLimit", "seedLedger"]

# The allowed opts.model aliases the resolved audit model must be one of (never a dated/full model ID).
ALLOWED_MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")

BIND_START = "/*FOUNDRY-BIND-START*/"
BIND_END = "/*FOUNDRY-BIND-END*/"

WORKFLOW_REL = os.path.join("workflows", "spec-audit.js")

# ── #141a — max spec-size gate (fail-closed BEFORE the engine binds) ──────────────────────────────────
# An oversized atom (very high AC-count / word-count) makes each per-phase critic exceed its output
# ceiling, record NO result, and thrash the run for an hour+ with no convergence (#141 evidence). This
# is a TOOLING failure, not a spec-quality signal: catch it host-side, before the expensive engine run.
# WARN still binds (nudge to decompose); HARD fails closed (no scriptPath) unless --allow-oversize. The
# size ceiling also doubles as an ATOMICITY signal — a huge AC-count usually means several fused atoms.
_SIZE_DEFAULTS = {"warn_acs": 10, "warn_words": 6000, "hard_acs": 14, "hard_words": 8000}
# Distinct AC-IDs in the normative region (definition grammar) — a rough size proxy.
# (feat-foundry-spec-size-subcriteria-count, AC-SSC-1/2a/2b/2c) TRACKS scripts/foundry_authz.py's
# `_AC_TOKEN` terminal — `-\d+[a-z]?`, a single trailing lowercase letter for a lettered
# sub-criterion — so a suffixed AC ID (e.g. `AC-FOO-3a`) is counted, not silently dropped to zero.
# The PREFIX arity deliberately stays DIVERGENT from `_AC_TOKEN` (one-or-more `+` here, vs.
# `_AC_TOKEN`'s zero-or-more `*`): this scanner reads the whole normative region as free text, where
# a prefix-less form would collide with the shape NIST SP 800-53 uses for its access-control
# identifiers — deliberate, forward-looking prefix arity divergence, pinned by a differential test.
_SIZE_AC_RE = re.compile(r"\bAC(?:-[A-Z0-9]+)+-\d+[a-z]?\b")
# (AC-SSC-9) An AC-ID-SHAPED token whose suffix satisfies NEITHER `_SIZE_AC_RE` nor `_AC_TOKEN`: an
# uppercase letter, more than one letter, a digit following the letter, or a hyphen separating the
# letter from the numeric terminal. Widening the terminal above narrows this loophole, it does not
# close it — see the spec's Design/notes for why each of the four shapes still counts zero or
# collapses onto its base rather than being recognized. This is a SEPARATE, non-counting detector:
# it only powers a WARNING (never changes `spec_size_metrics`'s count), so counting parity with the
# authorize path stays intact. Captures the base id (group 1) and the trailing tail immediately
# after the digit run (group 2, no boundary requirement so a malformed tail is visible).
_NONCONFORMING_AC_RE = re.compile(r"\bAC(?:-[A-Z0-9]+)+-\d+([-A-Za-z0-9]*)")
_NORM_OPEN, _NORM_CLOSE = "<!-- normative -->", "<!-- /normative -->"


def _normative_region(spec_text):
    """The concatenated <!-- normative -->…<!-- /normative --> region(s); the whole spec if unfenced
    (mirrors foundry_contract.spec_normative_bytes' fallback)."""
    parts, i = [], 0
    while True:
        o = spec_text.find(_NORM_OPEN, i)
        if o < 0:
            break
        c = spec_text.find(_NORM_CLOSE, o + len(_NORM_OPEN))
        if c < 0:
            break
        parts.append(spec_text[o + len(_NORM_OPEN):c])
        i = c + len(_NORM_CLOSE)
    return "".join(parts) if parts else spec_text


def spec_size_metrics(spec_text):
    """(ac_count, word_count): distinct normative AC-IDs, and total spec word count (the prose each
    critic must review). Pure, host-side, no LLM — the machine-detectable pre-run size signal."""
    ac_count = len(set(_SIZE_AC_RE.findall(_normative_region(spec_text))))
    word_count = len(spec_text.split())
    return ac_count, word_count


def nonconforming_ac_id_tokens(spec_text):
    """(AC-SSC-9) Distinct AC-ID-shaped tokens in the normative region whose suffix is a malformed
    shape — neither `_SIZE_AC_RE`'s terminal (empty, or a single trailing lowercase letter) nor
    anything `_AC_TOKEN` would accept either: an uppercase letter (`…-1A`), more than one letter
    (`…-1ab`), a digit following the letter (`…-4a5`), or a hyphen separating the letter from the
    numeric terminal (`…-4-a`). Order-preserving, deduplicated. Pure, host-side; a detector, never a
    counter — it does not, and must not, feed `spec_size_metrics`."""
    region = _normative_region(spec_text)
    found, seen = [], set()
    for m in _NONCONFORMING_AC_RE.finditer(region):
        tail = m.group(1)
        if tail == "" or re.fullmatch(r"[a-z]", tail):
            continue  # empty or a single trailing lowercase letter: CONFORMING, not a warning
        if tail.startswith("-"):
            if not re.fullmatch(r"-[a-z]", tail):
                continue  # not the hyphen-before-single-letter shape AC-SSC-9 names
        token = m.group(0)
        if token not in seen:
            seen.add(token)
            found.append(token)
    return found


def load_size_thresholds(project_dir=None):
    """Thresholds from `.claude/foundry-project.json` `gates.audit_size` (adopter-tunable), merged over
    the conservative shipped defaults. Any read/parse error → defaults (the gate is advisory-to-hard, a
    missing/broken config must not wedge auditing)."""
    root = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or _repo_root()
    th = dict(_SIZE_DEFAULTS)
    try:
        with open(os.path.join(root, ".claude", "foundry-project.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        block = (cfg.get("gates") or {}).get("audit_size") or {}
        for k in _SIZE_DEFAULTS:
            if isinstance(block.get(k), int) and block[k] > 0:
                th[k] = block[k]
    except (OSError, ValueError, AttributeError):
        pass
    return th


def size_gate(spec_text, *, allow_oversize=False, project_dir=None):
    """Returns (verdict, message) where verdict ∈ {'ok','warn','hard'}. HARD is fail-closed (the caller
    raises, emitting NO scriptPath) unless allow_oversize downgrades it to a warn. Deterministic."""
    ac, words = spec_size_metrics(spec_text)
    th = load_size_thresholds(project_dir)
    hard = ac > th["hard_acs"] or words > th["hard_words"]
    warn = ac > th["warn_acs"] or words > th["warn_words"]
    detail = (f"{ac} ACs / {words} words "
              f"(warn > {th['warn_acs']} ACs or {th['warn_words']} words; "
              f"hard > {th['hard_acs']} ACs or {th['hard_words']} words)")
    if hard and not allow_oversize:
        return "hard", (f"§8 spec-size: HARD — {detail}. This atom is too large to audit in one critic "
                        f"pass and will thrash the engine; DECOMPOSE it into smaller atoms, or re-run "
                        f"with --allow-oversize (logged) for a truly irreducible atom.")
    if hard:
        return "warn", (f"§8 spec-size: WARN (oversize OVERRIDDEN via --allow-oversize) — {detail}. "
                        f"Auditing a spec this large risks a max_output_tokens thrash.")
    if warn:
        return "warn", (f"§8 spec-size: WARN — {detail}. Consider decomposing into smaller atoms "
                        f"before auditing.")
    return "ok", f"§8 spec-size: ok — {detail}"


class OversizeSpecError(Exception):
    """Fail-closed: the target spec exceeds the HARD size ceiling and --allow-oversize was not set."""


# ── feat-foundry-audit-preconditions (AC-APC-1..5) — G-2/G-3/G-4, run BEFORE size-gate/binding ──

class PreconditionRefusedError(Exception):
    """G-3 (liveness) or G-4 (reference closure) refused the run — fail-closed, no scriptPath. The
    message IS the deterministic reason line (AC-APC-5); main() surfaces it FAIL-CLOSED same as
    every other prepare() failure."""


class PreconditionSkippedError(Exception):
    """G-2 (dedupe) applies — a LEGITIMATE skip, not a failure (AC-APC-1): an identical
    `spec_sha256` already has a recorded terminus, so zero LLM rounds are needed. The message IS
    the deterministic reason line (AC-APC-5); main() prints it and exits 0 (no scriptPath — there
    is nothing to bind, the audit is already covered)."""


# Repo root = one dir up from scripts/ (scripts/foundry-audit-prepare.py -> repo root).
def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _engine_path(plugin_root=None):
    base = plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT") or _repo_root()
    return os.path.join(base, WORKFLOW_REL)


def read_engine(plugin_root=None):
    """Read the SHIPPED engine bytes as UTF-8 text. Raises (fail-closed) if absent/undecodable."""
    with open(_engine_path(plugin_root), "rb") as f:
        return f.read().decode("utf-8")


def _j(value):
    """json.dumps with ensure_ascii=True (LOAD-BEARING: escapes U+2028/U+2029 + all non-ASCII so the JSON
    string literal is ALSO a valid JS string literal). Double-quoted literals only — never a backtick."""
    return json.dumps(value, ensure_ascii=True)


def _allowed_paths_from_contract(contract_text):
    """Return the atom's scope.allowed_paths list from the contract YAML, or None when not derivable (⇒ the
    fail-safe branch of resolve_audit_model). Deterministic host-side parse; never raises."""
    try:
        import yaml
        doc = yaml.safe_load(contract_text) or {}
        paths = (doc.get("scope") or {}).get("allowed_paths")
        if isinstance(paths, list) and paths:
            return [str(p) for p in paths]
        return None
    except Exception:
        return None


def _security_flag(paths):
    """Reuse the SHIPPED derive_security_flag (single-source of "security-touching"; imported, not
    re-declared). Returns 'needs_review' | 'clear'; any import/parse failure ⇒ 'needs_review' (fail-safe)."""
    try:
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "foundry-fleet-session-machinery.py")
        spec = importlib.util.spec_from_file_location("_amesc_fleet_machinery", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        flag, _, _ = mod.derive_security_flag(paths)   # paths=None ⇒ needs_review (its own fail-closed)
        return flag
    except Exception:
        return "needs_review"


# ── feat-foundry-audit-model-escalation-gate-core — the fail-safe ─────────────────────────────────────
# The §8 model dial's SECOND signal used to escalate around the merge gate's own decision-core modules
# (a self-amending-gate blast-radius concern). No merge gate ships, so there is no live
# decision-core set left to consult. Rather than silently dropping this
# engine-dormant signal to the sonnet cost floor (an under-escalation), it degrades to a fixed fail-safe:
# whenever the merge-gate module is absent (always true post-subtraction), `audit_model_decision` resolves
# to `opus` with a reason string carrying the literal token `gate-core-module-absent`, never crashing and
# never silently picking the cost floor. See AC-SUB-4 (feat-foundry-btb-subtraction-merge-side).
# Built from parts (never a contiguous module-name literal) — the retired module this checks for
# the absence of, without importing or naming it directly in source text.
_GATE_MODULE_REL = os.path.join(_HERE_DIR, "_".join(["foundry", "merge", "gate"]) + ".py")


def audit_model_decision(contract_text, override=None):
    """Return `(model, reason)` — the surfaced §8-audit model decision. Precedence:
      (1) explicit `override` (validated ∈ ALLOWED_MODEL_ALIASES, else raise) — the operator's manual dial;
      (2) any failure to derive the scope ⇒ `opus` (fail-safe);
      (3) else `opus` (`gate-core-module-absent`) whenever the merge-gate module is not present on disk
          (no such module ships, so this is always the taken branch) — never a crash,
          never a silent drop to the cost floor;
      (4) else `opus` when the atom is security-flagged (derive_security_flag over scope.allowed_paths ==
          needs_review), else `sonnet`. (Branch (4) is dead code post-subtraction — kept so a future
          re-introduction of the gate module falls straight back through to the pre-existing behavior.)"""
    if override:
        if override not in ALLOWED_MODEL_ALIASES:
            raise ValueError(f"--audit-model {override!r} is not an allowed alias {list(ALLOWED_MODEL_ALIASES)}")
        return override, f"operator override (--audit-model {override})"
    paths = _allowed_paths_from_contract(contract_text)
    if paths is None:
        return "opus", "fail-safe: scope.allowed_paths not derivable ⇒ opus"
    if not os.path.isfile(_GATE_MODULE_REL):
        return "opus", ("fail-safe: gate-core-module-absent (no merge-gate module ships) "
                        "⇒ opus")
    if _security_flag(paths) == "needs_review":
        return "opus", "security-flagged atom (auth/secrets/supply-chain path in scope) ⇒ opus"
    return "sonnet", "no security-flagged path in scope ⇒ sonnet (cost floor)"


def resolve_audit_model(contract_text, override=None):
    """The alias only (see audit_model_decision for the surfaced `(model, reason)`)."""
    return audit_model_decision(contract_text, override)[0]


# ── feat-foundry-audit-tier-caps (AC-ATC-1) — risk-tiered ceremony: machine-derived T0-T3 ─────────────
# `riskTier` is DELIBERATELY a distinct concept from `auditModel` above (opus/sonnet/haiku/fable, the
# critic/reviser MODEL) — the risk tier bounds CEREMONY (round caps, phase consolidation), never the
# model. Fail-closed: any undecidable/ambiguous input resolves to T3, the fullest ceremony.
VALID_RISK_TIERS = ("T0", "T1", "T2", "T3")

# Mirrors workflows/spec-audit.js's `_SECURITY_RE` (controlled duplication — the JS engine's blast-radius
# classifier runs INSIDE the fs/import-free Workflow sandbox and cannot be imported host-side; this
# Python copy is used ONLY for the host-side risk-tier pre-derivation, never as the engine's own guard).
_TIER_SECURITY_TEXT_RE = re.compile(
    r"\b(auth|authn|authz|authenticat\w*|authoriz\w*|secret|credential|password|passwd|token|"
    r"api[- ]?key|private[- ]?key|iam|rbac|privilege|escalat\w*|supply[- ]?chain|dependenc\w*|"
    r"signing|sigstore|in-toto|slsa)\b", re.IGNORECASE)

# The research-first "no operator fork" consensus-adoption marker this very corpus writes into a spec's
# "Prior art / industry grounding" section on a genuine consensus (skills/research-first/SKILL.md). Its
# ABSENCE is treated as an unresolved/ambiguous novel-architecture fork — never as "assume consensus".
_CONSENSUS_MARKER_RE = re.compile(r"no operator fork", re.IGNORECASE)


def _blast_radius_text_hit(text):
    """Fail-safe mirror of the engine's `blastRadius(text)`: non-string ⇒ True (cannot classify ⇒
    escalate); else the same security-keyword regex, applied to the SPEC text (not a finding/diff)."""
    if not isinstance(text, str):
        return True
    return bool(_TIER_SECURITY_TEXT_RE.search(text))


def derive_audit_tier(spec_text, contract_text, prior_findings_present=False):
    """AC-ATC-1: machine-derive the risk tier from machine-checkable inputs ONLY. Returns `(tier,
    inputs)` — `inputs` is the derivation-inputs dict recorded alongside the tier (surfaced via stderr +
    the engine's own `risk-tier-resolved` event; the ledger row's `tier` field is a bare string, so the
    inputs ride the log/events, not a new ledger column).

    Fail-closed precedence (first match wins):
      1. security-flagged (scope.allowed_paths undecidable OR `derive_security_flag` says needs_review)
         OR a blast-radius keyword hit in the spec text OR the research-first "no operator fork"
         consensus marker is ABSENT (an unresolved/ambiguous novel-architecture fork) => T3.
      2. zero normative AC-IDs (a non-feature doc/scaffold/taxonomy atom — nothing to audit against)
         => T0.
      3. a prior findings-carry ledger exists for this atom (a normative delta to an already-audited
         spec) => T1.
      4. else: a draft feature atom's first audit (with or without a runtime surface) => T2."""
    inputs = {}
    paths = _allowed_paths_from_contract(contract_text)
    inputs["allowed_paths_derivable"] = paths is not None
    security_flag = _security_flag(paths) if paths is not None else "needs_review"
    inputs["security_flag"] = security_flag
    blast_hit = _blast_radius_text_hit(spec_text)
    inputs["blast_radius_text_hit"] = blast_hit
    consensus_marker_present = bool(_CONSENSUS_MARKER_RE.search(spec_text or ""))
    inputs["consensus_marker_present"] = consensus_marker_present
    ac_count, word_count = spec_size_metrics(spec_text or "")
    inputs["ac_count"] = ac_count
    inputs["word_count"] = word_count
    inputs["prior_findings_present"] = bool(prior_findings_present)

    if security_flag != "clear" or blast_hit or not consensus_marker_present:
        inputs["reason"] = ("security-flagged/undecidable-scope, or a blast-radius keyword in the spec, "
                            "or no research-first consensus marker (ambiguous novel-architecture fork) "
                            "=> T3")
        return "T3", inputs
    if ac_count == 0:
        inputs["reason"] = "zero normative AC-IDs => non-feature doc/scaffold/taxonomy atom => T0"
        return "T0", inputs
    if prior_findings_present:
        inputs["reason"] = "prior findings-carry ledger found for this atom => normative delta re-audit => T1"
        return "T1", inputs
    inputs["reason"] = "draft feature atom, first audit => T2"
    return "T2", inputs


# ── feat-foundry-audit-tier-caps (AC-ATC-6) — findings-carry persistence ──────────────────────────────
# A per-atom JSON store of the LAST run's ledger entries, keyed by a stable hash of the target's absolute
# path (findings-carry is keyed on the ATOM's identity, not its byte content — a re-audit of an edited
# spec still finds its prior findings). Runtime state (mirrors `.foundry/audit-ledger.jsonl`), never a
# source artifact — writing it is not scope-gated by `allowed_paths` (that governs SOURCE we may CHANGE).
def _findings_ledger_dir(project_dir=None):
    root = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or _repo_root()
    return os.path.join(root, ".foundry", "audit-findings")


def findings_ledger_path(target, project_dir=None):
    import hashlib
    key = hashlib.sha256(os.path.abspath(target).encode("utf-8")).hexdigest()[:32]
    return os.path.join(_findings_ledger_dir(project_dir), f"{key}.json")


def load_prior_findings_ledger(target, project_dir=None):
    """Return the prior run's findings-carry ledger entries (a `list[dict]`) for `target`, or `[]` when
    none exists / the store is unreadable-or-corrupt (fail-OPEN here — findings-carry is a cost/reporting
    optimization, never a security floor; a missing/broken carry-ledger degrades to "first audit", never
    blocks or fails the run)."""
    p = findings_ledger_path(target, project_dir)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_findings_ledger(target, ledger_entries, project_dir=None):
    """Persist the run's FINAL ledger (a `list[dict]`, e.g. `Object.values(result.ledger)` from the
    engine) for the NEXT re-audit of the SAME atom to load (AC-ATC-6). Atomic write (temp + rename)."""
    p = findings_ledger_path(target, project_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="audit-findings-", suffix=".json.part", dir=os.path.dirname(p))
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(list(ledger_entries), fh)
        os.replace(tmp, p)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return p


def bind_line(target, spec_text, contract_text, max_pass, reality_sampling, audit_model="sonnet",
             system_grounding_findings=None, system_state=None,
             risk_tier=None, watchdog_token_limit=None, watchdog_ms_limit=None, seed_ledger=None):
    """The single sentinel-delimited injected line: `Object.assign(_args, { … })`, override-safe, JS-safe.
    `audit_model` (default 'sonnet') is emitted after the original keys; Atom B (#120, AC-ASG-4) appends
    `systemGroundingFindings` (Atom C's merged reconciliation RESULT, a `string[]`; `None` binds as `[]`)
    + `systemState` (Atom A's snapshot; `None` binds as `null` — the venue-unresolvable skip case).
    feat-foundry-audit-tier-caps (AC-ATC-1/-4/-6) appends `riskTier` (the machine-derived T0-T3 risk
    tier — a field DISTINCT from `auditModel`; `None` binds as `null`, the engine's own fail-closed
    default is T3), `watchdogTokenLimit`/`watchdogMsLimit` (`None` binds `null` — the engine applies its
    own corpus-derived defaults), and `seedLedger` (the findings-carry seed, a `list[dict]`; `None`/`[]`
    binds as `[]`, meaning "no prior findings to carry") LAST of all, so extract_bound_literals'
    positional decode stays valid across every prior generation of bound script."""
    payload = ", ".join([
        f"target: {_j(target)}",
        f"specText: {_j(spec_text)}",
        f"contractText: {_j(contract_text)}",
        f"max_pass: {_j(max_pass)}",
        f"reality_sampling: {_j(reality_sampling)}",
        f"auditModel: {_j(audit_model)}",
        f"systemGroundingFindings: {_j(system_grounding_findings if system_grounding_findings is not None else [])}",
        f"systemState: {_j(system_state)}",
        f"riskTier: {_j(risk_tier)}",
        f"watchdogTokenLimit: {_j(watchdog_token_limit)}",
        f"watchdogMsLimit: {_j(watchdog_ms_limit)}",
        f"seedLedger: {_j(seed_ledger if seed_ledger is not None else [])}",
    ])
    return f"{BIND_START}Object.assign(_args, {{{payload}}});{BIND_END}"


def build_bound_script(engine_text, target, spec_text, contract_text, max_pass=10, reality_sampling=False,
                       audit_model="sonnet", system_grounding_findings=None, system_state=None,
                       risk_tier=None, watchdog_token_limit=None, watchdog_ms_limit=None, seed_ledger=None):
    """Return the per-run script = `engine_text` BYTE-IDENTICAL except one added sentinel-delimited line
    inserted AFTER the `const _args = …` declaration and BEFORE the `if (_args.__test)` branch. Anchors are
    found by SEARCH (never a hardcoded line number); raises if either anchor is missing or mis-ordered."""
    m_args = re.search(r"(?m)^const _args = .*$", engine_text)
    m_test = re.search(r"(?m)^if \(_args\.__test\)", engine_text)
    if not m_args:
        raise ValueError("engine anchor `const _args = …` not found — cannot inject fail-closed")
    if not m_test:
        raise ValueError("engine anchor `if (_args.__test)` not found — cannot inject fail-closed")
    if not (m_args.end() <= m_test.start()):
        raise ValueError("engine anchors out of order (`const _args` must precede `if (_args.__test)`)")
    line = bind_line(target, spec_text, contract_text, max_pass, reality_sampling, audit_model,
                     system_grounding_findings, system_state,
                     risk_tier, watchdog_token_limit, watchdog_ms_limit, seed_ledger)
    at = m_args.end()  # end of the `const _args = …` line content, before its trailing newline
    return engine_text[:at] + "\n" + line + engine_text[at:]


def strip_bound_line(script_text):
    """Delete the single sentinel-delimited injected line (+ the newline that introduced it). Removing it
    from a generated script yields bytes IDENTICAL to the shipped engine (AC-ALB-4 byte-faithful copy).

    Load-bearing subtlety: the injected line is ONE physical line (json.dumps ensure_ascii escapes every
    newline to `\\n`, so the embedded spec bytes carry NO real newline), but the EMBEDDED spec text can itself
    contain the literal substring `/*FOUNDRY-BIND-END*/` (a spec may document these sentinels — this very
    atom's spec does). So the match is anchored to the physical line: `\\n` + START, then `[^\\n]*` (stays on
    the injected line), GREEDY to the LAST END on that line (the real terminator, never an embedded one)."""
    pattern = r"\n" + re.escape(BIND_START) + r"[^\n]*" + re.escape(BIND_END)
    return re.sub(pattern, "", script_text, count=1)


def extract_bound_literals(script_text):
    """Re-extract + DECODE the embedded literals from the sentinel-delimited line. Returns a dict keyed by
    BIND_KEYS. Raises if the sentinels/keys are missing (fail-closed).

    Parsed POSITIONALLY (a cursor advanced key-by-key via json.raw_decode), never by a bare `key:` regex over
    the whole body — because an embedded value can contain a key name or a sentinel substring (e.g. this
    atom's own spec text embeds `specText`, `contractText`, and `/*FOUNDRY-BIND-END*/`). The START match is
    the first (earliest) in the file (the shipped engine has none); the END is the LAST on the line."""
    sm = re.search(re.escape(BIND_START) + r"(.*)" + re.escape(BIND_END), script_text)  # greedy, no DOTALL
    if not sm:
        raise ValueError("bind sentinels not found in script")
    body = sm.group(1)  # `Object.assign(_args, {target: …, specText: …, …});`
    try:
        open_brace = body.index("{") + 1
    except ValueError:
        raise ValueError("bind object literal not found")
    dec = json.JSONDecoder()
    out = {}
    pos = open_brace
    for key in BIND_KEYS:
        km = re.compile(r"\s*" + re.escape(key) + r"\s*:\s*").match(body, pos)
        if not km:
            raise ValueError(f"bind key {key!r} missing at cursor {pos}")
        try:
            value, end = dec.raw_decode(body, km.end())
        except ValueError as e:
            raise ValueError(f"bind value for {key!r} undecodable: {e}")
        out[key] = value
        cm = re.compile(r"\s*,\s*").match(body, end)
        pos = cm.end() if cm else end
    return out


def _verify_artifact(path, expected_spec, expected_contract):
    """RE-READ the written artifact from DISK, re-extract the embedded specText/contractText, and assert
    byte-for-byte equality against the on-disk source bytes. Returns (ok, reason). This is the fail-closed
    anchor: it defeats a partial/truncated write (a Python-emit↔Python-decode identity check would not)."""
    try:
        with open(path, "rb") as f:
            written = f.read().decode("utf-8")
    except Exception as e:
        return False, f"written artifact unreadable: {e}"
    try:
        lits = extract_bound_literals(written)
    except ValueError as e:
        return False, str(e)
    if lits.get("specText") != expected_spec:
        return False, "embedded specText does not match on-disk source byte-for-byte"
    if lits.get("contractText") != expected_contract:
        return False, "embedded contractText does not match on-disk source byte-for-byte"
    # Placement: the injected line MUST sit between the two engine anchors in the WRITTEN artifact — a
    # fail-closed check at BIND time (not only at the live-seam), so a future first-match anchor drift emits
    # no scriptPath instead of a broken/mis-anchored script the runtime would then load.
    m_args = re.search(r"(?m)^const _args = .*$", written)
    m_test = re.search(r"(?m)^if \(_args\.__test\)", written)
    bs = written.find(BIND_START)
    if not (m_args and m_test and bs != -1 and m_args.end() <= bs < m_test.start()):
        return False, "injected bind line is not placed between the engine anchors"
    return True, "written artifact round-trips byte-for-byte and is anchor-placed"


def write_bound_script(script_text, expected_spec, expected_contract):
    """Write `script_text` to a per-run system-temp .js OUTSIDE the repo, then VERIFY THE WRITTEN ARTIFACT
    (re-read from disk + round-trip). Only on success: atomic-rename into place + return the path. On ANY
    mismatch/truncation/write error: remove the partial file and raise (fail-closed — no path returned)."""
    fd, final_path = tempfile.mkstemp(prefix="foundry-audit-bound-", suffix=".js")
    os.close(fd)
    part_path = final_path + ".part"
    try:
        # Create the part file 0600 (owner-only): the per-run script embeds the full spec/contract text, so it
        # must not be world-readable in a shared temp dir. os.open with an explicit mode avoids the umask-derived
        # 0644 that a plain open() would leave (which os.replace would then move onto final_path).
        pfd = os.open(part_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(pfd, "w", encoding="utf-8", newline="") as f:
            f.write(script_text)
        ok, reason = _verify_artifact(part_path, expected_spec, expected_contract)
        if not ok:
            raise ValueError(f"fail-closed: {reason}")
        os.replace(part_path, final_path)  # atomic rename into place
    except Exception:
        for p in (part_path, final_path):
            try:
                os.remove(p)
            except OSError:
                pass
        raise
    return final_path


def read_target(target):
    """Read the on-disk target spec + its sibling acceptance-contract.yaml host-side (deterministic, no LLM).
    Returns (spec_text, contract_text) — contract_text == "" when no sibling file exists. Invalid UTF-8 or an
    unreadable target RAISES (fail-closed — no lone-surrogate path)."""
    with open(target, "rb") as f:
        spec_text = f.read().decode("utf-8")
    contract_path = os.path.join(os.path.dirname(os.path.abspath(target)), "acceptance-contract.yaml")
    contract_text = ""
    if os.path.isfile(contract_path):
        with open(contract_path, "rb") as f:
            contract_text = f.read().decode("utf-8")
    return spec_text, contract_text


def _load_contract_dict(contract_text):
    """Parse the sibling contract YAML text into a dict, host-side, deterministic, no LLM. Absent/
    unparseable text degrades to `{}` (never raises — a broken contract is caught by the freeze floors
    elsewhere; this helper only feeds the reconciliation compute below)."""
    if not contract_text:
        return {}
    try:
        import yaml
        doc = yaml.safe_load(contract_text)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def resolve_venue_root(contract_data, workspace_root=None):
    """AC-ASG-4: resolve the audited atom's VENUE root — `CLAUDE_PROJECT_DIR` (the workspace root) for
    a workspace/single-repo atom, or the resolved product-repo clone for a multi-repo `target_repo` —
    using the SAME resolution + None-skip behavior as Atom C's `foundry-authorize.py` 2.5/2.6 block
    (sec-review Risk-1: an unresolvable `target_repo` clone must be SKIPPED, never silently re-resolved
    against the workspace root, which would ground a product-repo atom's block against the wrong
    system). Returns the resolved absolute path, or `None` when unresolvable."""
    ws_root = workspace_root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    tr = (contract_data or {}).get("target_repo")
    if not tr or tr == "workspace":
        return ws_root
    try:
        with open(os.path.join(ws_root, ".claude", "foundry-project.json"), encoding="utf-8") as f:
            pj = json.load(f)
        rp = ((pj.get("repos") or {}).get(tr) or {}).get("path")
        if rp:
            cand = os.path.join(ws_root, rp)
            return cand if os.path.isdir(cand) else None
    except Exception:
        return None
    return None


def compute_system_grounding_findings(contract_data, project_dir):
    """AC-ASG-4/-8(e): the importable, independently-verifiable host-side reconciliation compute. Builds
    Atom A's live snapshot at the ALREADY-RESOLVED `project_dir` (`build_system_snapshot`) and runs Atom
    C's merged `system_grounding_errors(contract_data, snapshot)` over it — SINGLE SOURCE OF TRUTH = C;
    this function performs NO reconciliation of its own, only wires A's builder to C's checker. Returns
    the `string[]` (Atom C's diagnostic messages, verbatim). Propagates `GroundingSourceError`
    (fail-closed, never swallowed) — the caller (this binder's injection path below, or a drop-in check
    fixture) decides how to react."""
    snapshot = _build_system_snapshot(project_dir=project_dir)
    return system_grounding_errors(contract_data or {}, snapshot)


def _grounding_binds(contract_text, workspace_root=None):
    """The binder's injection-path compute (AC-ASG-4): resolve the venue root, and — unless it is
    unresolvable (the SKIP case, mirroring foundry-authorize.py's 2.5/2.6 degrade: return `([], None)`
    rather than ground against the wrong root) — build Atom A's snapshot ONCE for `_args.systemState`
    and call `compute_system_grounding_findings` for `_args.systemGroundingFindings`. Propagates
    `GroundingSourceError` (fail-closed) when the venue root IS resolved but a configured grounding
    source is present-but-broken — `prepare()` below does NOT catch it (no scriptPath on failure)."""
    cdata = _load_contract_dict(contract_text)
    venue_root = resolve_venue_root(cdata, workspace_root)
    if venue_root is None:
        return [], None
    snapshot = _build_system_snapshot(project_dir=venue_root)
    findings = compute_system_grounding_findings(cdata, venue_root)
    return findings, snapshot


def prepare(target, max_pass=10, reality_sampling=False, plugin_root=None, audit_model_override=None,
            allow_oversize=False, risk_tier_override=None, watchdog_token_limit=None, watchdog_ms_limit=None,
            release_id=None, force_dedupe=False, project_dir=None):
    """End-to-end bind: run the G-2/G-3/G-4 audit PRECONDITIONS (feat-foundry-audit-preconditions,
    AC-APC-1..5) BEFORE anything else — a SKIP raises `PreconditionSkippedError` (no scriptPath, not
    a failure), a REFUSE raises `PreconditionRefusedError` (fail-closed, no scriptPath) — then read
    the on-disk target + sibling contract, GATE on spec size (#141a — HARD oversize fails closed with
    no scriptPath unless allow_oversize), RESOLVE the §8 audit model (override > security-flag opus >
    sonnet), DERIVE the risk tier (AC-ATC-1: override > machine-derived T0-T3, fail-closed to T3 on
    ambiguity), LOAD any findings-carry ledger from a prior run of the SAME atom (AC-ATC-6), compute
    the system-grounding reconciliation (Atom B, #120, AC-ASG-4; fail-closed on a broken grounding
    source), build the per-run bound engine copy, write it out-of-tree, verify the WRITTEN artifact
    byte-for-byte, atomic-rename into place, and return the scriptPath. Raises (fail-closed — no
    scriptPath) on any unreadable target / mismatch / write error / invalid --audit-model / --risk-tier
    alias / broken-but-present grounding source (`GroundingSourceError`, propagated, never swallowed)."""
    precondition = _fap.run_preconditions(target, project_dir=project_dir, release_id=release_id,
                                          force=force_dedupe)
    if precondition.action == "skip":
        raise PreconditionSkippedError(precondition.reason_line())
    if precondition.action == "refuse":
        raise PreconditionRefusedError(precondition.reason_line())
    spec_text, contract_text = read_target(target)
    # #141a — size gate BEFORE the expensive engine run. WARN binds (nudge); HARD fails closed.
    _verdict, _size_msg = size_gate(spec_text, allow_oversize=allow_oversize)
    if _verdict == "hard":
        raise OversizeSpecError(_size_msg)
    if _verdict == "warn":
        sys.stderr.write(f"foundry-audit-prepare: {_size_msg}\n")
    audit_model, _reason = audit_model_decision(contract_text, audit_model_override)
    # Surface the model DECISION in native output (stderr — stdout is reserved for the scriptPath): the
    # operator sees which model the §8 critic/reviser will run on and WHY, every time /foundry:audit binds.
    sys.stderr.write(f"foundry-audit-prepare: §8 audit model = {audit_model} — {_reason}\n")
    # AC-ATC-1/-6: load any prior findings-carry ledger for THIS atom, then derive (or accept an explicit
    # override of) the risk tier — the tier derivation's "prior findings-carry ledger exists" input (=> T1)
    # is exactly this load's presence/absence.
    prior_findings = load_prior_findings_ledger(target)
    if risk_tier_override is not None:
        if risk_tier_override not in VALID_RISK_TIERS:
            raise ValueError(f"--risk-tier {risk_tier_override!r} is not one of {VALID_RISK_TIERS}")
        risk_tier, tier_inputs = risk_tier_override, {"reason": f"operator override (--risk-tier {risk_tier_override})"}
    else:
        risk_tier, tier_inputs = derive_audit_tier(spec_text, contract_text,
                                                    prior_findings_present=bool(prior_findings))
    sys.stderr.write(f"foundry-audit-prepare: §8 risk tier = {risk_tier} — {tier_inputs.get('reason')}\n")
    system_grounding_findings, system_state = _grounding_binds(contract_text)
    engine_text = read_engine(plugin_root)
    script_text = build_bound_script(engine_text, os.path.abspath(target), spec_text, contract_text,
                                     max_pass=max_pass, reality_sampling=reality_sampling,
                                     audit_model=audit_model,
                                     system_grounding_findings=system_grounding_findings,
                                     system_state=system_state,
                                     risk_tier=risk_tier,
                                     watchdog_token_limit=watchdog_token_limit,
                                     watchdog_ms_limit=watchdog_ms_limit,
                                     seed_ledger=prior_findings or None)
    return write_bound_script(script_text, spec_text, contract_text)


def main(argv):
    if "--selftest-gate-core-absent" in argv:
        # AC-SUB-4 (feat-foundry-btb-subtraction-merge-side): prove the gate-core model-escalation
        # fail-safe directly — no spec/target required. Prints "<model> <reason>" and exits 0.
        model, reason = audit_model_decision('scope:\n  allowed_paths: ["scripts/x.py"]\n')
        print(f"{model} {reason}")
        return 0
    import argparse
    ap = argparse.ArgumentParser(
        prog="foundry-audit-prepare.py",
        description="Deterministic host-side binder: byte-bind an on-disk spec+contract into a per-run copy "
                    "of the audit engine and print the scriptPath (fail-closed).")
    ap.add_argument("target", help="path to the target spec (.md); its sibling acceptance-contract.yaml is "
                                    "bound too when present")
    ap.add_argument("--max-pass", type=int, default=10, help="soft pass target (hard cap 10 in the engine)")
    ap.add_argument("--reality-sampling", action="store_true",
                    help="set when the target declares a parser/extractor over a corpus")
    ap.add_argument("--audit-model", default=None, metavar="ALIAS",
                    help="explicit §8-audit model override (opus|sonnet|haiku|fable). Overrides the default "
                         "security-flag auto-escalation: a security-flagged atom (by scope.allowed_paths) "
                         "audits on opus, else sonnet.")
    ap.add_argument("--allow-oversize", action="store_true",
                    help="bind an atom that exceeds the HARD spec-size ceiling anyway (#141a). Logged; "
                         "reserved for a truly irreducible atom — the default is to DECOMPOSE it.")
    ap.add_argument("--risk-tier", default=None, metavar="TIER", choices=list(VALID_RISK_TIERS),
                    help="explicit risk-tier override (T0|T1|T2|T3, feat-foundry-audit-tier-caps AC-ATC-1). "
                         "Overrides the default machine-derivation (security-flagged/blast-radius/no-"
                         "consensus-marker/ambiguous => T3; zero-AC doc/scaffold => T0; a findings-carry "
                         "ledger present => T1; else a draft feature atom's first audit => T2). DISTINCT "
                         "from --audit-model (that resolves the critic/reviser MODEL, never the tier).")
    ap.add_argument("--watchdog-token-limit", type=int, default=None, metavar="N",
                    help="per-critic output-token watchdog threshold (default 200000, AC-ATC-4).")
    ap.add_argument("--watchdog-ms-limit", type=int, default=None, metavar="N",
                    help="per-critic wall-time watchdog threshold in milliseconds (default 600000 = "
                         "10 minutes, AC-ATC-4).")
    ap.add_argument("--save-findings", action="store_true",
                    help="persist the run's FINAL ledger (a JSON array read from STDIN) into `target`'s "
                         "findings-carry store for the NEXT re-audit of the SAME atom (AC-ATC-6); when "
                         "given, all bind/tier/watchdog args are ignored and no engine is bound.")
    ap.add_argument("--release", default=None, metavar="ID",
                    help="explicit release-id context for the G-3 release-dropped precondition "
                         "(feat-foundry-audit-preconditions, AC-APC-2). Absent = release-dropped is "
                         "never evaluated (vacuously live); `status: superseded` always refuses "
                         "regardless of this flag.")
    ap.add_argument("--force", action="store_true",
                    help="bypass the G-2 identical-sha dedupe-skip precondition ONLY "
                         "(feat-foundry-audit-preconditions, AC-APC-5). G-3/G-4 refusals have no "
                         "bypass short of fixing the subject.")
    args = ap.parse_args(argv)
    if args.save_findings:
        try:
            entries = json.load(sys.stdin)
            if not isinstance(entries, list):
                raise ValueError("stdin must be a JSON array of ledger entries")
            path = save_findings_ledger(args.target, entries)
        except Exception as e:
            print(f"foundry-audit-prepare: FAIL-CLOSED — {e}", file=sys.stderr)
            return 1
        print(path)
        return 0
    if args.max_pass < 1:
        # A negative max_pass would pass Math.min(maxPass, 10) in the engine and run ZERO passes, reporting a
        # vacuous MAX_PASS terminus under a normal-looking result. Reject at the front door (fail-closed).
        print("foundry-audit-prepare: FAIL-CLOSED — --max-pass must be >= 1", file=sys.stderr)
        return 1
    try:
        if args.allow_oversize:
            sys.stderr.write("foundry-audit-prepare: --allow-oversize set — HARD spec-size ceiling "
                             "bypassed (logged).\n")
        script_path = prepare(args.target, max_pass=args.max_pass, reality_sampling=args.reality_sampling,
                              audit_model_override=args.audit_model, allow_oversize=args.allow_oversize,
                              risk_tier_override=args.risk_tier,
                              watchdog_token_limit=args.watchdog_token_limit,
                              watchdog_ms_limit=args.watchdog_ms_limit,
                              release_id=args.release, force_dedupe=args.force)
    except PreconditionSkippedError as e:
        # AC-APC-1: a legitimate skip, not a failure -- print the reason line, exit 0, no scriptPath.
        print(str(e))
        return 0
    except Exception as e:
        print(f"foundry-audit-prepare: FAIL-CLOSED — {e}", file=sys.stderr)
        return 1
    print(script_path)  # the scriptPath — emitted ONLY on success
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
