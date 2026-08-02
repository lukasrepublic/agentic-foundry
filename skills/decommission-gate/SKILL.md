---
name: decommission-gate
description: 'The governed turn-off primitive — no legacy component is severed until its replacement has PROVEN independence and the old side is provably safe, recorded in an append-only operator-bound validation ledger the gate RE-DERIVES from live re-checks at turn-off time (fail-closed, waiver-blind). Drives scripts/foundry-decommission.py: validate-register (class-aware register, GENERATED gate_status), record (append-only ledger; refuses incomplete VALIDATED rows incl. the custody real-operation proof), gate-check (GO only on latest-VALIDATED + live reverify/old-safe re-checks under a forced waiver-blind phase; else NO-GO exit 1). Turn-off is structurally the LAST wave. Trigger: "decommission", "turn off the legacy", "is it safe to delete", "sever the old side", "/foundry:decommission-gate".'
---

# decommission-gate — build-and-validate first, turn-off last, GO re-derived live

Turn-off is the one irreversible, highest-blast-radius action of a migration. This skill governs it
with three machine-checkable stages driven through `scripts/foundry-decommission.py`. **The gate
consumes a recorded design principle: a persisted "validated" flag alone NEVER authorizes a sever —
GO is re-derived from live re-checks at the moment of turn-off.**

## The register (one YAML, class-aware, generated status)

- **Enumerate every in-scope component** in `decommission-register.yaml`: stable `id`, the
  `legacy_identity` being turned off, the `replacement` and how it is reached, a `class`, a
  `parallel_name`, a `custody` flag, a `soak_window`, per-component `checks.reverify` /
  `checks.old_safe` slot commands (read-only by contract), and a **generated `gate_status`** —
  derived from the ledger by `validate-register --regen`, NEVER hand-edited (a hand edit that
  disagrees with the ledger fails validation closed).
- **Class semantics decide the old-side safety question** (they are OPPOSITES, not variants):
  - **endpoint-bearing (inbound)** — safe when ZERO consumers remain over the soak window (flow
    logs to the old interface / load-balancer request+connection counts == 0).
  - **headless (outbound worker: signer, poller, listener)** — the hazard is DUAL-OPERATION
    (double-spend, conflicting signatures, duplicated side effects), so safe means the old
    instance is QUIESCED: stopped + confirmed down AND no new outbound activity.
  - **decommission-only (no replacement)** — zero-traffic corroboration of the dead claim, so a
    live component cannot be hand-downgraded to "dead" to skip the gate.

## The independence policy (positive-leaning — no pass-by-omission)

- **The policy template denies on any known legacy marker AND denies any host-like endpoint ref
  NOT on the allow-set** — a dependency reached by an un-enumerated hostname cannot pass by
  omission. Evaluate it over the component's EFFECTIVE rendered config (the `checks.reverify`
  slot renders — e.g. `helm template` output + IaC source literals — and evaluates; a render
  error is FATAL, never skipped):

  ```rego
  package decommission.independence
  # deny: any configured legacy marker (substrings/regexes from allow-deny.yaml)
  deny contains msg if {
    some m in input.legacy_markers
    contains(input.effective_config, m)
    msg := sprintf("legacy marker %q present", [m])
  }
  # deny: any host-like ref NOT matched by the allow-set (positive-leaning — no pass-by-omission)
  deny contains msg if {
    some h in input.endpoint_refs
    not allowed_host(h)
    msg := sprintf("endpoint %q not on the allow-set", [h])
  }
  allowed_host(h) if { some a in input.allow_hosts; glob.match(a, [], h) }
  ```

- **Two phases:** `validate` (scoped, EXPIRING waivers honoured) and `reverify` (**waiver-blind**
  — a temporary migration-window waiver can never authorize a permanent turn-off). The gate FORCES
  `DECOM_PHASE=reverify` into the slot environment, so a validate-only pass is structurally NO-GO.
- **The typed slot-verdict contract** the gate enforces (the two hardening floors that proved
  load-bearing): the reverify slot must emit
  `{"verdict":"pass","canary_denied":true,"refs_scanned":N}` — **canary_denied must be true** (the
  policy was run on a known-dirty input and DENIED it, so a vacuous conftest pass on a load error
  can never read as "independent"), and **refs_scanned must be > 0** for an endpoint-bearing/
  headless component (zero host-like refs is a fail-closed setup error, never a silent PASS).

## The ledger + the gate (the only authorization path)

- **`record`** appends the single authorization event to `validation-ledger.jsonl` — bound to a
  REGISTERED operator (the existing operator registry; no invented dual-control), verdict
  `VALIDATED` / `REJECTED` (retained as evidence) / `TURNED_OFF` (the sever write-back), with an
  operator-supplied `--timestamp`. **The tool refuses an incomplete VALIDATED row**: independence
  pass + old-safe evidence per class, and for `custody: true` components a **real-operation
  proof** it will not let you omit.
- **`gate-check`** returns **GO only** when the latest row is a complete `VALIDATED` AND the live
  re-checks still hold (waiver-blind reverify + old-safe + custody proof). Anything else — no row,
  a REJECTED latest, a failing or missing re-check, a register inconsistency — is **NO-GO, exit 1,
  fail-closed**. Wire a sever atom's merge/execute step to this exit code.
- **Turn-off is structurally the LAST wave**: build-and-validate atoms for every component precede
  the program's single teardown wave, and the parallel-run endpoint stays live until then — the
  rollback for a bad sever is trivially "drop the parallel name; the old side never stopped".

## Anti-patterns

- **Severing on a spreadsheet / chat-thread "is it safe?"** — only a `gate-check` GO authorizes.
- **Trusting the persisted validation** at turn-off time — config drifts, consumers appear,
  waivers outlive their purpose; the gate re-derives live.
- **Ingress-only thinking for headless workers** — quiesce-first, or old and new dual-operate.
- **An allow-by-default independence policy** — deny anything not enumerated; omission is not
  independence.
- **Turn-off interleaved with build waves** — it is the last wave, once, with the ledger green.
- **On a harness denial** of a `record` or `gate-check` invocation, see `docs/harness-denial-fallback.md` and STOP: hand back the exact denied command; never retry it or route around it via another credential.
