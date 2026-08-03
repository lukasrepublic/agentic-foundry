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
  if (!fs.existsSync(existingPath)) return { present: false, equal: false };
  const stat = fs.lstatSync(existingPath);
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
