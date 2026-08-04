// util.mjs — small shared helpers. No third-party deps; node: builtins and relative imports only.
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

/** A refusal: the CLI writes nothing and names the offending flag/path/entry. */
export class RefusalError extends Error {
  constructor(message, flag) {
    super(message);
    this.name = 'RefusalError';
    this.flag = flag;
  }
}

export function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

/** Physically resolve a path (symlinks followed), even if it does not yet exist — resolves the
 * longest existing ancestor and joins the remaining (non-existent) segments onto it. */
export function physicalResolve(targetPath) {
  const abs = path.resolve(targetPath);
  const parts = abs.split(path.sep);
  let base = path.sep;
  let i = 1;
  // find the longest existing ancestor
  let existing = abs;
  const missing = [];
  while (existing !== path.sep && !fs.existsSync(existing)) {
    missing.unshift(path.basename(existing));
    existing = path.dirname(existing);
  }
  const realBase = fs.existsSync(existing) ? fs.realpathSync(existing) : existing;
  return missing.length ? path.join(realBase, ...missing) : realBase;
}

/** Join a template-relative path onto a physically-resolved target root, refusing (returns null)
 * any resolution that escapes the root — an absolute template path, a `..` segment, or a
 * symlinked ancestor that resolves outside. Never throws; callers decide how to report. */
export function confinedJoin(physicalRoot, relPath) {
  if (path.isAbsolute(relPath)) return null;
  const segments = relPath.split(/[\\/]+/);
  if (segments.some((s) => s === '..')) return null;
  const joined = path.join(physicalRoot, relPath);
  const resolved = physicalResolve(joined);
  const rootWithSep = physicalRoot.endsWith(path.sep) ? physicalRoot : physicalRoot + path.sep;
  if (resolved !== physicalRoot && !resolved.startsWith(rootWithSep)) return null;
  return joined;
}

export function ensureDirFor(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

export function fileBytesEqual(existingPath, desiredBytes) {
  // lstat, NOT existsSync (PR #61 security review Block 2). existsSync FOLLOWS symlinks, so a
  // DANGLING symlink at a managed path reported `false` -> the row was classified `create` ->
  // writeFileSync followed the link and landed the write at the link's target, OUTSIDE the
  // physically-resolved target root (AC-BCL-9) and reachable at $HOME/.claude/settings.json
  // (AC-BCL-4(d)) — a user-scope settings file is NOT gated by the workspace trust dialog, so on
  // that one path the CLI stopped declaring and started granting. lstat does not follow the final
  // component, so ANY existing entry — dangling symlink included — is seen and falls to the
  // `notRegular` arm below, i.e. `drifted`: reported, never written. Symlinks whose targets EXIST
  // were already caught (realpath resolves them out of root), as were symlinked ancestors; this
  // closes the dangling-leaf case, the only one that escaped.
  const stat = fs.lstatSync(existingPath, { throwIfNoEntry: false });
  if (!stat) return { present: false, equal: false };
  if (!stat.isFile()) return { present: true, equal: false, notRegular: true };
  const actual = fs.readFileSync(existingPath);
  return { present: true, equal: Buffer.compare(actual, Buffer.from(desiredBytes)) === 0 };
}

export function isNonEmptyDir(dirPath) {
  if (!fs.existsSync(dirPath)) return false;
  const stat = fs.lstatSync(dirPath);
  if (!stat.isDirectory()) return true; // exists as a non-directory: treat as "occupied"
  return fs.readdirSync(dirPath).length > 0;
}
