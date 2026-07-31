---
name: data-tier-cutover
description: 'The stateful-tier migration PROCEDURE skill — cut an app''s SQL database + cache over from self-managed instances to a managed serverless target. A read-only procedure the generic agent runs alongside the id-* infra-delivery steps: it drives the fail-closed cutover-mechanism DECISION TREE (snapshot-restore vs CDC-replicate + fast-promote), the per-app connection-pool INVENTORY read from application source (never the deploy layer), the live-vs-committed VERIFICATION GATE (committed IaC defaults are NOT live truth — offline-green ≠ live-parity), fills one of the two parameterized RUNBOOK shapes (each with an explicit rollback column), and routes a BLOCKING security-reviewer pass for custodial tiers (per-store key-policy isolation). It authors plan artifacts and runbooks; it NEVER applies, restores, promotes, or mutates — every apply step in the emitted runbook is operator-run through the id-apply posture discipline. Trigger: "cut over the database", "migrate the data tier", "snapshot restore vs CDC", "/foundry:data-tier-cutover".'
---

# data-tier-cutover — the stateful-tier decision model, inventory, gate, runbooks, and review hook

Migrating the stateful tier is the hardest step of a platform migration and is categorically
different from the stateless `id-*` flow. This skill encodes the decision model and the four
evidence-backed traps so they are driven, not re-derived. **Read-only / never-apply:** this skill
authors the plan artifact + runbook; every mutating step in the emitted runbook is operator-run
(the `id-apply` posture discipline). The merge floor — the adopter's branch protection + CI
checks (see docs/merge-floor.md) — stays the merge authority.

## Stage A — the cutover-mechanism decision tree (fail-closed)

- **Inputs (all three required):** `downtime_tolerance` (`window_ok` | `near_zero`),
  `source_shared_with_prod` (bool), `source_is_replicable` (bool). A missing input is a STOP —
  never assume tolerance.
- **`window_ok` AND not shared ⇒ snapshot-restore** (the simple path): a snapshot restore
  PRESERVES the source DB users and passwords — the app keeps its credentials and only the host
  string changes.
- **`near_zero` OR shared-with-prod ⇒ CDC-replicate + fast-promote**, and the plan artifact MUST
  carry the three required CDC caveat items: **sequences are NOT replicated by logical CDC —
  resync them at promote**; **the cache cannot be slaved cross-environment — provision it FRESH
  and warm it**; **define a replication-lag SLA before promote** (stale reads until promote).
- **Record the chosen mechanism + rationale into the per-app cutover plan artifact** so the
  operator and the PR reviewers see WHY, not just WHAT.

## Stage B — connection-pool inventory (from application source, never the deploy layer)

- **Read the APPLICATION SOURCE for the data-access topology** and record a matrix row per app:
  `{pool_shape: single | dual, ro_endpoint_key, rw_endpoint_key, sticky_read_your_writes: bool,
  orm/driver}`. The **deploy/gitops layer is an UNRELIABLE source** for this — a dual read/write
  pool or a sticky read-your-writes default is only visible in code, and guessing from the deploy
  layer produced a wrong matrix on the evidence engagement.
- **The matrix drives feasibility**: dual-pool apps can take a phased read-first cutover; a
  single-pool app cannot. **Missing/ambiguous topology ⇒ default-deny — STOP and ask; never guess**
  (a wrong guess silently reads stale data or connects to the wrong endpoint).

## Stage C — the live-vs-committed verification gate (committed defaults are NOT truth)

- **Before authoring any restore/replicate IaC, diff every committed IaC default the pre-merge
  plan reads against the LIVE source resource**: engine MAJOR version; the at-rest encryption flag
  AND key; the in-transit TLS/auth posture; and the gitignored-varfile blind spot (when the repo
  gitignores local var files, the COMMITTED defaults are the only truth CI reads — surface this
  loudly). Offline-green ≠ live-parity — a distinction worth stating explicitly.
- **Any discrepancy that would cause a destructive replace or a connect failure is a BLOCKING plan
  item** the operator reconciles before restore. The recurring instances: an unencrypted source
  restored with a null key comes up plaintext and the IaC then wants an encrypt-REPLACE — **supply
  the customer-managed key AT RESTORE TIME** so the target comes up encrypted in one shot; a
  plaintext-in-transit source breaks on a TLS-forced target — **transit encryption is opt-in at
  PARITY**, tracked as an explicit post-cutover hardening with coordinated app-client changes;
  differing engine majors — **restore at the SOURCE major, then a separate apply for the upgrade**.

## Stage D — the two runbook shapes (parameterized; both carry a rollback column)

- **Snapshot-restore runbook:** snapshot → **restore-at-source-major with the key wired from the
  start** → separate major-upgrade apply → converge drift to empty → **repoint host fields only**
  (the writer endpoint serves RO when single-writer) → force secret-sync → rolling restart →
  verify (TCP probe DB + cache from the pod, health endpoint, app-log "connected") → commit on a
  branch → PR-then-merge.
- **CDC + fast-promote runbook:** provision target + FRESH cache → start full-load+CDC → drain lag
  to the SLA → quiesce writes → **promote** → resync sequences → repoint → verify → soak →
  decommission the source (through the decommission gate, never ad hoc).
- **Every runbook carries an explicit ROLLBACK column** per step (the pre-repoint host string is
  the universal fallback until the soak completes).
- **Data-tier egress reminder:** a managed store living inside the workload's own network CIDR
  still needs an explicit NetworkPolicy allow on the DB/cache ports — a self-healing GitOps
  controller will otherwise leave the app reaching only the legacy tier, a boot-time connect
  failure that masquerades as a data-plane bug.

## Stage E — the custodial key-isolation review hook (BLOCKING when flagged)

- **When the tier is custodial/sensitive (or touches a root-of-trust secret), route a BLOCKING
  security-reviewer pass** whose checklist includes the specific per-store key-policy isolation
  assertion: **the encryption key's root statement grants key-ADMIN verbs only and carries NO
  unconditioned data-plane verbs (encrypt/decrypt/re-encrypt/generate-data-key); data-plane verbs
  exist ONLY under the service-scoped (ViaService) statement; verified LIVE, not just in code.**
  (The evidence finding: an unconditioned root statement let any key-IAM principal decrypt custody
  data, bypassing the ViaService constraint on the second statement.)
- **Parity-window items** (in-transit encryption deferred, pod egress scoped) are TRACKED
  post-cutover hardening, not blockers. NO-BLOCK ⇒ proceed; Block ⇒ hold + document.

## Anti-patterns

- **Choosing the mechanism by habit** (always-snapshot or always-CDC) instead of the Stage-A
  inputs — a shared still-serving source makes snapshot-restore a production outage.
- **Reading pool topology from the gitops layer** — only application source is authoritative.
- **Trusting committed IaC defaults** as live truth — the most expensive trap in the program.
- **Flipping the target to match the code** (forcing TLS, new engine major at restore) instead of
  restoring at parity and hardening as a tracked follow-up.
- **Applying anything from this skill's session** — it authors; the operator applies (id-apply).
