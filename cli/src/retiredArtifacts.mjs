// retiredArtifacts.mjs — the sweep for workspace-side files an earlier release wrote and no current
// release reads (hotfix-v1.17.4, ER #236). Driven by the shipped catalogue `cli/retired-artifacts.json`:
// every present entry is reported `[stale]` on every run; removal happens only under `--cleanup`,
// only for catalogued paths, only when the path is exactly the catalogued kind (a regular file, or a
// real directory) — a symlink or the other kind is reported `[refused]` and left alone. Nothing
// outside the catalogue is ever a candidate: `.claude/skills`, `.claude/agents` and every operator
// file are invisible to this module by construction.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin } from './util.mjs';

export const CATALOGUE_REL = 'retired-artifacts.json';

/** Load and validate the catalogue shipped beside `src/` (the package root). Refuses a malformed
 * entry loudly rather than sweeping with a half-read list. */
export function loadRetiredCatalogue(pkgDir) {
  const doc = JSON.parse(fs.readFileSync(path.join(pkgDir, CATALOGUE_REL), 'utf-8'));
  if (!doc || doc.schema_version !== 1 || !Array.isArray(doc.entries)) {
    throw new Error('retired-artifacts.json: unrecognised schema');
  }
  for (const e of doc.entries) {
    if (typeof e.path !== 'string' || path.isAbsolute(e.path) || e.path.split(/[\\/]+/).includes('..')) {
      throw new Error(`retired-artifacts.json: bad path ${JSON.stringify(e.path)}`);
    }
    if (e.kind !== 'file' && e.kind !== 'dir') throw new Error(`retired-artifacts.json: bad kind for ${e.path}`);
    const star = e.path.indexOf('*');
    if (star !== -1 && (e.path.lastIndexOf('*') !== star || e.path.slice(star).includes('/'))) {
      throw new Error(`retired-artifacts.json: only one '*' in the last segment is allowed (${e.path})`);
    }
  }
  return doc;
}

/** Expand an entry to concrete relative paths present on disk: the path itself, or, for a
 * basename glob, every directory entry that matches (names starting with `exclude_prefix` skipped). */
function candidates(physicalRoot, entry) {
  if (!entry.path.includes('*')) return [entry.path];
  const dirRel = path.posix.dirname(entry.path);
  const pattern = path.posix.basename(entry.path);
  const [pre, post] = pattern.split('*');
  const dirAbs = confinedJoin(physicalRoot, dirRel);
  if (!dirAbs) return [];
  let names;
  try {
    names = fs.readdirSync(dirAbs);
  } catch {
    return [];
  }
  return names
    .filter((n) => n.startsWith(pre) && n.endsWith(post) && n.length >= pre.length + post.length)
    .filter((n) => !(entry.exclude_prefix && n.startsWith(entry.exclude_prefix)))
    .map((n) => path.posix.join(dirRel, n));
}

/** Plan: `{ rows: [{ relPath, kind, retired_in, reason, state }], present, refused }` where `state`
 * is `stale` (present and of the catalogued kind), `refused` (present but a symlink or the other
 * kind), and absent paths are omitted. Pure over the filesystem — nothing is written. */
export function planRetiredArtifacts({ physicalRoot, catalogue }) {
  const rows = [];
  for (const entry of catalogue.entries) {
    for (const relPath of candidates(physicalRoot, entry)) {
      const abs = confinedJoin(physicalRoot, relPath);
      let st = null;
      if (abs) {
        try {
          st = fs.lstatSync(abs);
        } catch {
          continue; // absent: nothing to say
        }
      } else {
        // the path resolves OUTSIDE the workspace (a symlink escaping the root): present, reported,
        // never touched
        try {
          st = fs.lstatSync(path.join(physicalRoot, relPath));
        } catch {
          continue;
        }
      }
      const kindOk = abs && !st.isSymbolicLink() && (entry.kind === 'dir' ? st.isDirectory() : st.isFile());
      rows.push({ relPath, kind: entry.kind, retired_in: entry.retired_in, reason: entry.reason, state: kindOk ? 'stale' : 'refused' });
    }
  }
  return {
    rows,
    present: rows.filter((r) => r.state === 'stale').length,
    refused: rows.filter((r) => r.state === 'refused').length,
  };
}

/** Remove every `stale` row (re-checked with lstat immediately before each removal). Returns the
 * count removed. A row whose state changed between plan and apply is skipped, never forced. */
export function applyRetiredArtifacts(plan, physicalRoot) {
  let removed = 0;
  for (const row of plan.rows) {
    if (row.state !== 'stale') continue;
    const abs = confinedJoin(physicalRoot, row.relPath);
    if (!abs) continue;
    let st;
    try {
      st = fs.lstatSync(abs);
    } catch {
      continue;
    }
    if (st.isSymbolicLink()) continue;
    if (row.kind === 'dir' ? !st.isDirectory() : !st.isFile()) continue;
    try {
      if (row.kind === 'dir') fs.rmSync(abs, { recursive: true, force: false });
      else fs.unlinkSync(abs);
      removed += 1;
      row.state = 'removed';
    } catch {
      row.state = 'refused';
    }
  }
  plan.removed = removed;
  return removed;
}

/** The rows every writer prints. `cleanup` false → `[stale]` (nothing removed); true → `[removed]`
 * for what went, `[refused]` for what did not. Empty when nothing catalogued is present. */
export function renderRetiredArtifactRows(plan, { cleanup }) {
  const out = [];
  for (const r of plan.rows) {
    if (r.state === 'stale') {
      out.push(cleanup
        ? `  [stale] ${r.relPath} — retired in v${r.retired_in} (${r.reason}) — NOT removed`
        : `  [stale] ${r.relPath} — retired in v${r.retired_in} (${r.reason}); remove with --cleanup`);
    } else if (r.state === 'removed') {
      out.push(`  [removed] ${r.relPath} — retired in v${r.retired_in}`);
    } else {
      out.push(`  [refused] ${r.relPath} — present but not a regular ${r.kind} (a link, or the other kind) — left alone`);
    }
  }
  return out;
}
