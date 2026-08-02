---
name: relock
description: Re-lock the stack-profile lock after a trusted profile-version advance (/foundry:relock). When `/foundry:doctor` shows a `stack-profile` RED because a profile in `packs/` advanced (e.g. aws-eks-karpenter 0.3.0→0.4.0) and `.foundry/stack-profile.lock` still pins the old version/sha, this re-resolves the ALREADY-locked profiles against `packs/` and atomically re-writes the lock with their current {version, sha256, blueprints_sha256} — validate-before-write, refusing a downgrade / invalid / core-incompatible profile. Trigger after a `claude plugin update` bumped a locked profile, or when the operator says "/foundry:relock", "doctor stack-profile is red after updating", "re-lock the stack profile".
---

# /foundry:relock

The stack-profile re-lock operator surface (feat-foundry-stack-profile-relock). `.foundry/stack-profile.lock`
pins each adopted profile as `{id, version, sha256, blueprints_sha256}`. When the plugin ships a new
profile *version*, the locked `version`/`sha256` no longer match the profile now in `packs/` →
`resolve_lock` fail-closes → `/foundry:doctor`'s `stack-profile` row goes **DOCTOR-RED**. This verb is the
missing re-lock — the npm `update` / `terraform init -upgrade` / `/foundry:upgrade` (now a no-op pointer) "the
trusted action re-pins the lock" pattern, for the stack-profile lock.

**Refresh, not create — its sibling is `--lock` (`feat-foundry-stack-profile-lock-create`).**
`relock` re-resolves an id-set that is **already locked**; it refuses outright when no lock exists
("nothing to relock") and never adds/removes a profile. To **adopt** a profile for the first time —
when there is no `.foundry/stack-profile.lock` yet — run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --lock <id>[,<id>…]` instead (or
let `/foundry:init` drive it). The two verbs share the SAME entry-builder and the SAME
trusted-resolve guardrails (schema-valid, present in `packs/`, `requires_core` satisfied, no
core-plugin `skills/` bundle leak) so a lock either verb writes always resolves the same way; they
stay separate operations so `--lock` never silently re-points an adopter's existing lock and
`relock` never silently creates one that was never adopted.

## When to trigger

- `/foundry:doctor` shows `stack-profile … does not resolve` **and** the cause is a **known trusted
  profile-version advance** (you just ran `claude plugin update`, or bumped a profile's `requires_core`).
- Operator: "/foundry:relock", "re-lock the stack profile", "doctor stack-profile is red after updating".

## Procedure

1. **Inspect the drift first** — confirm it is a trusted advance, not an unexplained change:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py"            # see the stack-profile RED detail
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --validate <profile-id>
   ```
2. **Re-lock** (re-resolves the already-locked ids against `packs/`; validate-before-write; atomic):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --relock
   ```
   Output: `relocked: <id> <old>→<new>` per profile, then `stack-profile.lock re-locked (N profile(s))`.
   It **fail-closes (non-zero, no write)** if any locked profile is absent from `packs/`, schema-invalid,
   core-incompatible (`requires_core` excludes the running core), or a **downgrade** (resolved version <
   locked version).
3. **Confirm GREEN**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py"
   ```
   Expect `DOCTOR-GREEN`. If `--relock` refused, the drift is **not** a clean trusted advance — read the
   error (downgrade / core-incompatible / absent) and investigate before forcing anything.

## What it does NOT do

- **It re-locks only the profiles already in the lock** — it never adds or removes a profile. To adopt a
  *new* profile, run `--lock <id>` (a fresh workspace) or hand-edit + re-run for an id-set change; never
  `--relock`.
- **It never blindly copies a sha to silence a mismatch.** Each profile must load + schema-validate + admit
  the running core + not be a downgrade before *any* byte is written; otherwise it fail-closes with the lock
  untouched.
- **The only version guard is the downgrade refusal.** It also adopts a *same-version content drift* — a
  profile whose `version` string is unchanged but whose `stack-profile.yaml` / `blueprints/` bytes changed —
  by refreshing the `sha256`/`blueprints_sha256` (equal version ⇒ not a downgrade ⇒ re-pinned). That is the
  intended "re-pin to current trusted `packs/`" behavior under the mistake-catcher threat model, but it means
  step 1 (inspect the drift first) is load-bearing: only relock when the change to `packs/` is one you expect.

## Anti-patterns

- **Re-locking to make a RED go away without checking the drift is trusted** — `--relock` adopts whatever
  `packs/` now holds; only run it when the advance is one you expect (a plugin update / profile bump).
- **Treating it as a floor** — the stack-profile lock is a *mistake-catcher* (unpinned/typo'd/drifted/
  core-incompatible profile), not a gate. The enforcing floors (front-authorization, the merge floor — branch protection + CI) are elsewhere; this is the convenience re-lock for a trusted profile-version advance.
