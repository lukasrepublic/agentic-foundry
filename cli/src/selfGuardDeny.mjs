// selfGuardDeny.mjs — the two framework-owned deny rules that protect the operator's policy file
// (hotfix-v1.17.3, ER #232): `Edit(.foundry/permissions.yaml)` and `Write(.foundry/permissions.yaml)`.
//
// They are what `scripts/foundry-permissions-compile.py --write` derives for a policy with ZERO grants
// (its SELF_GUARD pair) — no operator judgement is in them, so every writer that seeds or keeps the
// policy file converges them too, and a fresh seed is in-sync instead of `policy drift (2)` until
// someone remembers the compiler. Grants stay the compiler's (operator-run) business.
//
// Discipline: add-if-absent onto `permissions.deny`, never reorder or remove anything, never touch
// another key; a settings object without a `permissions` block gains one with only `deny`.
import fs from 'node:fs';
import path from 'node:path';

export const POLICY_REL = '.foundry/permissions.yaml';
export const SELF_GUARD_DENY = Object.freeze([
  `Edit(${POLICY_REL})`,
  `Write(${POLICY_REL})`,
]);

/** Which of the two rules `settingsObj` still lacks. Pure. */
export function missingSelfGuardDeny(settingsObj) {
  const deny = (settingsObj && settingsObj.permissions && Array.isArray(settingsObj.permissions.deny))
    ? settingsObj.permissions.deny : [];
  return SELF_GUARD_DENY.filter((r) => !deny.includes(r));
}

/** A NEW settings object with the missing rules appended to `permissions.deny`. Pure. */
export function applySelfGuardDeny(settingsObj) {
  const missing = missingSelfGuardDeny(settingsObj);
  if (missing.length === 0) return settingsObj;
  const next = { ...(settingsObj || {}) };
  next.permissions = { ...(next.permissions || {}) };
  next.permissions.deny = [...(next.permissions.deny || []), ...missing];
  return next;
}

/** `true` when the policy file exists as a regular file under `physicalRoot` (seeded or kept) — the
 * rules guard a file, so they are written only when there is one. Never follows a symlink. */
export function policyPresent(physicalRoot) {
  try {
    return fs.lstatSync(path.join(physicalRoot, POLICY_REL)).isFile();
  } catch {
    return false;
  }
}

/** The one row every writer prints: `[permissions] self-guard deny rules added (N)` or
 * `already present`. `null` when there is no policy file to guard (nothing to say). */
export function renderSelfGuardRow(physicalRoot, addedCount) {
  if (!policyPresent(physicalRoot)) return null;
  return addedCount > 0
    ? `  [permissions] self-guard deny rules added (${addedCount}): Edit/Write on ${POLICY_REL}`
    : `  [permissions] self-guard deny rules already present`;
}
