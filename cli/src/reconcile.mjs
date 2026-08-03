// reconcile.mjs — the ONE write path: terraform-init reconcile shape, shared by a greenfield
// create, an --existing scaffold-into, and an idempotent re-run (AC-BCL-8/AC-BCL-9). A managed
// path that is absent is created; one that exists and matches is left untouched (no write
// syscall at all — bytes/inode/mtime survive); one that exists and differs is reported `drifted`
// and NEVER written, on any path, in any mode (never-clobber is unconditional).
import { ensureDirFor, fileBytesEqual } from './util.mjs';
import fs from 'node:fs';

/** Compute the action for each managed file WITHOUT writing anything. */
export function planManagedFiles(managedFiles) {
  return managedFiles.map((f) => {
    const { present, equal, notRegular } = fileBytesEqual(f.absPath, f.bytes);
    let action;
    if (!present) action = 'create';
    else if (notRegular) action = 'drifted';
    else action = equal ? 'unchanged' : 'drifted';
    return { ...f, action };
  });
}

/** Apply a plan: writes ONLY the `create` rows. `unchanged`/`drifted` rows are never touched —
 * this is what makes never-clobber unconditional and re-runs byte/inode/mtime-stable. */
export function applyPlan(plan) {
  for (const f of plan) {
    if (f.action !== 'create') continue;
    ensureDirFor(f.absPath);
    fs.writeFileSync(f.absPath, f.bytes);
  }
  return plan;
}

/** The total exit-code matrix (AC-BCL-8): 0 unless >=1 row is `drifted`, in which case 2. Refusals
 * are a distinct path (exit 1) handled by the caller before any plan is computed. */
export function exitCodeForPlan(plan) {
  return plan.some((f) => f.action === 'drifted') ? 2 : 0;
}
