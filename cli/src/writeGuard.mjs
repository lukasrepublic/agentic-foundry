// writeGuard — ONE protected-path check, enforced inside every write helper (security review of #286).
//
// The updater used to refuse a protected write target only on the PREVIEW rows: a Phase-4 re-plan (the
// files re-planned fresh from disk after the preview) never passed through that check, so a re-plan that
// came out different could write `.claude/foundry-project.json` & co. The guard is installed for the
// duration of a run (`installWriteGuard`) and every helper that writes — applyPlan, writeTargetAtomically,
// writeGitignoreAtomically, applyStatuslineWiring, applyAmendmentsBackfill, applyManifestTrueUp,
// applyRetiredArtifacts and the quarantine manifest — calls `assertProtected(absPath)` first.
//
// The decision resolves symlinks (a link NAMED something harmless that points at a protected file is
// still the protected file) and keeps the preview rule's one exemption: a DECLARED managed path that does
// not exist yet may be CREATED (`creating: true`, never an overwrite).
import fs from 'node:fs';
import path from 'node:path';
import { isProtected } from './pathClasses.mjs';
import { DECLARED_PATH_SET } from './scaffold.mjs';
import { RefusalError } from './util.mjs';

let ctx = null;

/** Install the guard for `physicalRoot`; returns the function that removes it. */
export function installWriteGuard(physicalRoot, extra = []) {
  const prev = ctx;
  ctx = { root: realOrSelf(physicalRoot), extra: [...extra] };
  return () => { ctx = prev; };
}

function realOrSelf(p) {
  try { return fs.realpathSync(p); } catch { return path.resolve(p); }
}

/** Real location of `abs`: the realpath when it exists, else realpath(nearest existing ancestor) + the rest. */
function resolveReal(abs) {
  const full = path.resolve(abs);
  const tail = [];
  let cur = full;
  for (;;) {
    try {
      return path.join(fs.realpathSync(cur), ...tail.reverse());
    } catch {
      const parent = path.dirname(cur);
      if (parent === cur) return full;
      tail.push(path.basename(cur));
      cur = parent;
    }
  }
}

export function assertProtected(abs, { creating = false } = {}) {
  if (!ctx) return;
  const real = resolveReal(abs);
  const rel = path.relative(ctx.root, real).split(path.sep).join('/');
  if (rel === '' || rel.startsWith('..') || path.isAbsolute(rel)) return; // outside the workspace: not this guard's set
  if (!isProtected(rel, ctx.extra)) return;
  const absent = !fs.existsSync(real) && !fs.lstatSync(path.resolve(abs), { throwIfNoEntry: false });
  if (creating && absent && DECLARED_PATH_SET.includes(rel)) return;
  throw new RefusalError(`refusing: a write targets the protected path ${rel}`, 'protected');
}
