// v1.18.2 — the upgrade TRUES UP release manifests to the current contract (operator directive,
// 2026-09-26: "whatever artifacts are no longer compatible with a new version must be fixed by the
// updater … not dumped on a user as a surprise"). 41 of 84 real `.foundry/releases/*/release.yaml`
// failed the loader. The loader now reads every harmless legacy shape (bookkeeping fields, dotted
// ids, legacy state names, a missing description); this module rewrites the two that carry MEANING on
// disk, so the file says what the machinery reads:
//   * a legacy `state:` value -> the current vocabulary (in_progress/partially-* -> active, released/
//     done -> completed);
//   * a missing `description:` -> one line naming the release id, inserted after `id:`.
// Edits are LINE-LEVEL on top-level keys only (column 0), so every comment and every other byte is
// preserved (some manifests carry over 1,000 comment lines; a YAML round-trip would drop them all).
// Idempotent; symlinks are never followed; anything it cannot rewrite safely is left alone and listed.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin } from './util.mjs';

export const STATE_TRUE_UP = Object.freeze({
  in_progress: 'active', 'in-progress': 'active', 'partially-released': 'active', 'partially-merged': 'active',
  released: 'completed', done: 'completed',
});

const STATE_LINE = /^state:[ \t]*(["']?)([A-Za-z_-]+)\1([ \t]*(?:#.*)?)$/m;
const ID_LINE = /^id:[ \t]*(["']?)([a-z0-9.-]+)\1[ \t]*(?:#.*)?$/m;
const DESCRIPTION_LINE = /^description:/m;

/** The new text for one manifest, and what changed; `changes` empty when nothing applies. */
export function trueUpText(text) {
  let out = text;
  const changes = [];
  const sm = STATE_LINE.exec(out);
  if (sm && Object.prototype.hasOwnProperty.call(STATE_TRUE_UP, sm[2])) {
    const to = STATE_TRUE_UP[sm[2]];
    out = out.replace(STATE_LINE, `state: ${to}${sm[3]}`);
    changes.push(`state ${sm[2]} -> ${to}`);
  }
  const im = ID_LINE.exec(out);
  if (im && !DESCRIPTION_LINE.test(out)) {
    const at = im.index + im[0].length;
    out = `${out.slice(0, at)}\ndescription: "${im[2]}"${out.slice(at)}`;
    changes.push('description added');
  }
  return { text: out, changes };
}

/** Plan over every `.foundry/releases/<id>/release.yaml` of the workspace. */
export function planManifestTrueUp({ physicalRoot }) {
  const rows = [];
  const dir = confinedJoin(physicalRoot, '.foundry/releases');
  const st = dir ? fs.lstatSync(dir, { throwIfNoEntry: false }) : null;
  if (!st || !st.isDirectory() || st.isSymbolicLink()) return { rows, applied: false };
  for (const name of fs.readdirSync(dir).sort()) {
    const rel = path.posix.join('.foundry/releases', name, 'release.yaml');
    const abs = confinedJoin(physicalRoot, rel);
    if (!abs) continue;
    const fst = fs.lstatSync(abs, { throwIfNoEntry: false });
    if (!fst || !fst.isFile()) continue; // a symlink or a missing manifest is never touched
    let text;
    try { text = fs.readFileSync(abs, 'utf-8'); } catch { continue; }
    const { text: next, changes } = trueUpText(text);
    if (changes.length > 0) rows.push({ rel, abs, changes, next, before: text });
  }
  return { rows, applied: false };
}

/** Atomic per-file write (temp + rename), re-checking the bytes did not change since the plan. */
export function applyManifestTrueUp(plan) {
  const written = [];
  for (const r of plan.rows) {
    let cur;
    try { cur = fs.readFileSync(r.abs, 'utf-8'); } catch { continue; }
    if (cur !== r.before) continue; // changed since the plan: left alone
    const tmp = path.join(path.dirname(r.abs), `.release.yaml.${process.pid}.tmp`);
    const fd = fs.openSync(tmp, 'wx', 0o644);
    try { fs.writeFileSync(fd, r.next); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    try {
      fs.renameSync(tmp, r.abs);
      written.push(r.rel);
    } catch {
      try { fs.unlinkSync(tmp); } catch { /* best effort */ }
    }
  }
  plan.applied = true;
  plan.written = written;
  return plan;
}

export function renderManifestTrueUpRows(plan) {
  return plan.rows.map((r) => `  [${plan.applied ? 'trued-up' : 'would true up'}] ${r.rel} — ${r.changes.join('; ')}`);
}
