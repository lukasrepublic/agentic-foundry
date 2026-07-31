#!/usr/bin/env python3
"""foundry_ctx_posture — read-only CTX posture probe + the EXECUTE/GENERATE/REFUSE resolver
(feat-foundry-ctx-posture).

`infra-delivery` runs INSIDE an operator-validated CTX session; **foundry gates the change, CTX gates
the environment.** This module is the posture PRIMITIVE — a *read-only* probe of CTX state plus a pure,
total, fail-closed decision: whether the framework may MUTATE infra (EXECUTE), must instead emit a
runbook for the operator to run in CTX (GENERATE), or must REFUSE. It reads `ctx status --json` and
NEVER mutates CTX (never `ctx activate`/`ctx off`/any mutating verb).

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). The operator owns the CTX
session; this is a fail-closed posture resolver — a mistake-preventer that defaults to the most
restrictive posture (never silently EXECUTE) when CTX is absent, errored, stale, or unparseable. It is
read-only; it does not itself apply anything — `id-apply` (the deferred sibling) acts on the posture.
SECURITY-REVIEW surface (floor #3): it governs whether a prod mutation runs.

GitOps posture is NOT derived here (CTX does not signal GitOps, snapshot §4): that classification is
`id-apply`'s, from the stack profile + the change target.
"""
import json
import re
import subprocess
from dataclasses import dataclass

# The read-only CTX read this probe is allowed to issue — the ONLY ctx call, and only the read-only
# `status` subcommand. The selftest's mutation-safety control asserts the recorded argv set ⊆ {this}.
CTX_STATUS_ARGV = ("ctx", "status", "--json")

# The closed posture enum (a total table maps every CtxState onto one of these three values).
EXECUTE = "EXECUTE"    # the framework may run the mutation directly (non-prod, or audited break-glass).
GENERATE = "GENERATE"  # emit a runbook for the operator to run in CTX, then verify read-only (guarded prod).
REFUSE = "REFUSE"      # the most-restrictive posture (unreachable / stale / unparseable / unmatched).

# Case-insensitive prod-indicating tokens for the (a') prod-label integrity cross-check. A
# `production_flag:false` env whose `active_env` matches one of these DISAGREES with the flag → treat
# as guarded prod (GENERATE), never EXECUTE. Matched as whole tokens so e.g. `staging` does NOT match.
_PROD_TOKENS = frozenset({"prod", "production", "prd"})


@dataclass(frozen=True)
class CtxState:
    """The parsed (or fail-closed sentinel) CTX session state.

    `reachable=False` is the fail-closed sentinel — emitted on EVERY probe failure mode (binary absent,
    non-zero exit, unparseable/non-JSON, missing required field, no active session). It is NOT an
    exception that crashes the caller and NOT a fabricated healthy state. When `reachable=False` the
    other fields are None and `resolve_posture` REFUSEs.
    """
    reachable: bool
    active_env: str | None = None
    production_flag: bool | None = None
    guard_state: str | None = None   # ∈ {"ON","OFF"} when reachable.
    stale_state: str | None = None   # ∈ {"OK","STALE"} when reachable.
    detail: str = ""


@dataclass(frozen=True)
class Posture:
    """The resolved posture decision. `decision` ∈ {EXECUTE, GENERATE, REFUSE}; `audited` is True ONLY
    for a break-glass EXECUTE (guard OFF in prod) so `id-apply` can stamp it into the audit ledger."""
    decision: str
    audited: bool = False
    reason: str = ""


def _default_runner(argv):
    """The real `ctx` runner — runs the read-only `ctx status --json` and returns its CompletedProcess.

    Raising (binary absent → FileNotFoundError, or any OSError) is caught by `probe_ctx` and mapped to
    the fail-closed sentinel; we deliberately do NOT check=True so a non-zero exit is observed (not
    raised) and likewise mapped. No shell; argv is a fixed list (no injection surface)."""
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, read-only `ctx status`.
        list(argv),
        capture_output=True,
        text=True,
        timeout=30,
    )


def probe_ctx(*, runner=_default_runner) -> CtxState:
    """Read `ctx status --json` (read-only) and parse the documented contract fields, fail-closed.

    Parses (snapshot §4): `active_env`, `production_flag` (bool), `guard_state` ∈ {"ON","OFF"},
    `stale_state` ∈ {"OK","STALE"}. The ONLY ctx invocation, and only the read-only `status` subcommand
    — NEVER `ctx activate`/`ctx off`/any mutating verb.

    Fail-closed on EVERY failure mode → `CtxState(reachable=False, …)`:
      * `ctx` binary absent / runner raises (FileNotFoundError, OSError, timeout, …),
      * non-zero exit,
      * unparseable / non-JSON stdout,
      * a missing required field,
      * no active session (identity fields null).

    `runner(argv)` is injectable so the selftest drives synthetic JSON without a real `ctx`; it must
    return an object with `.returncode` and `.stdout` (a `subprocess.CompletedProcess`-shaped value).
    """
    try:
        proc = runner(list(CTX_STATUS_ARGV))
    except Exception as e:  # binary absent / OSError / timeout / any runner failure → fail-closed.
        return CtxState(reachable=False, detail=f"ctx probe failed to run: {e}")

    rc = getattr(proc, "returncode", None)
    if rc != 0:
        return CtxState(reachable=False, detail=f"ctx status exited non-zero (rc={rc})")

    raw = getattr(proc, "stdout", None)
    if not isinstance(raw, str) or not raw.strip():
        return CtxState(reachable=False, detail="ctx status produced no stdout")

    try:
        doc = json.loads(raw)
    except (ValueError, TypeError) as e:
        return CtxState(reachable=False, detail=f"ctx status output is not parseable JSON: {e}")

    if not isinstance(doc, dict):
        return CtxState(reachable=False, detail="ctx status JSON is not an object")

    # No active session: the documented identity fields are null/absent → fail-closed sentinel (a
    # desynced/absent session must never drive a mutation), NOT a fabricated healthy state.
    active_env = doc.get("active_env")
    production_flag = doc.get("production_flag")
    guard_state = doc.get("guard_state")
    stale_state = doc.get("stale_state")

    if active_env is None:
        return CtxState(reachable=False, detail="no active CTX session (active_env is null)")

    # Required fields must be present + correctly typed; any missing/mistyped field → fail-closed
    # (schema-drift fail-closes to REFUSE rather than mis-deciding).
    if not isinstance(production_flag, bool):
        return CtxState(reachable=False, detail="ctx status missing/invalid production_flag (bool)")
    if not isinstance(guard_state, str):
        return CtxState(reachable=False, detail="ctx status missing/invalid guard_state")
    if not isinstance(stale_state, str):
        return CtxState(reachable=False, detail="ctx status missing/invalid stale_state")

    return CtxState(
        reachable=True,
        active_env=active_env,
        production_flag=production_flag,
        guard_state=guard_state,
        stale_state=stale_state,
        detail="ctx status parsed",
    )


def _is_prod_token(active_env) -> bool:
    """True iff `active_env` matches a case-insensitive prod-indicating WHOLE token (prod/production/prd).
    Tokenized on non-alphanumeric boundaries so `staging`/`dev` never match, but `prod-us`/`PRD` do."""
    if not isinstance(active_env, str):
        return False
    tokens = (t for t in re.split(r"[^a-z0-9]+", active_env.lower()) if t)
    return any(t in _PROD_TOKENS for t in tokens)


def resolve_posture(state: CtxState) -> Posture:
    """Map a CtxState to a Posture by a TOTAL, fail-closed table. Pure — NO I/O. NEVER calls a mutating
    ctx verb (it calls nothing).

    The table (REFUSE dominates):
      (a)  reachable=False OR stale_state != "OK"  → REFUSE
           (positive "OK" ALLOWLIST: STALE, an unknown value, or schema-drift all REFUSE; never a
            fail-open on a non-OK/non-STALE value — a desynced/absent session must never mutate.)
      (a') prod-label integrity cross-check — production_flag==False BUT active_env is a prod token →
           the labels DISAGREE → resolve as guarded prod (GENERATE), never EXECUTE (a mislabelled-prod
           env cannot silently drive a direct mutation; production_flag is no longer the sole EXECUTE
           determinant — mirrors software-delivery's re-derive-don't-trust).
      (b)  production_flag==False AND active_env NOT a prod token (dev/staging) → EXECUTE.
      (c)  production_flag==True AND guard_state=="ON" (guarded prod, the DEFAULT) → GENERATE.
      (d)  production_flag==True AND guard_state=="OFF" (break-glass) → EXECUTE audited=True.
      else → REFUSE (fail-closed default; the table is total, never a silent EXECUTE — including an
             unrecognized guard_state value).
    """
    # (a) unreachable / stale dominate — REFUSE (positive-OK allowlist, no fail-open).
    if not state.reachable:
        return Posture(REFUSE, reason=f"CTX unreachable: {state.detail}")
    if state.stale_state != "OK":
        return Posture(REFUSE, reason=f"CTX state not OK (stale_state={state.stale_state!r}); refuse")

    prod_flag = state.production_flag
    prod_token = _is_prod_token(state.active_env)

    if prod_flag is False:
        # (a') mislabelled-prod cross-check — labels disagree → treat as guarded prod (GENERATE).
        if prod_token:
            return Posture(
                GENERATE,
                reason=(f"prod-label mismatch: production_flag=False but active_env="
                        f"{state.active_env!r} is prod-indicating; treat as guarded prod (GENERATE)"),
            )
        # (b) genuine non-prod (dev/staging) → EXECUTE.
        return Posture(EXECUTE, reason=f"non-prod env {state.active_env!r}; framework may execute")

    if prod_flag is True:
        if state.guard_state == "ON":
            # (c) guarded prod (the default) → GENERATE a runbook for the operator.
            return Posture(GENERATE, reason="guarded prod (guard ON); generate runbook for operator")
        if state.guard_state == "OFF":
            # (d) break-glass — the operator disabled CTX's guard out-of-band → honor EXECUTE but flag
            #     audited so id-apply records it. The framework never ENGAGES break-glass; it reflects it.
            return Posture(EXECUTE, audited=True,
                           reason="break-glass (prod, guard OFF): execute audited")
        # unrecognized guard_state in prod → fail-closed.
        return Posture(REFUSE, reason=f"prod with unrecognized guard_state={state.guard_state!r}; refuse")

    # production_flag was neither True nor False (mistyped past the probe) → fail-closed default.
    return Posture(REFUSE, reason=f"unmatched CtxState (production_flag={prod_flag!r}); refuse")
