// amendmentsBackfill.mjs — amendments-backfill (ER #214, AC-AMB-1..5).
//
// `/foundry:amend` (v1.11.0) refuses, by design and by test, when a spec has no `## Amendments`
// heading after its LAST `<!-- /normative -->` marker (outside fenced code) — see
// scripts/foundry-amend.py `amendments_section_ok`, whose rule this module mirrors byte for byte
// (tests/test_amendments_backfill.py cross-checks the two over one fixture set). Nothing added that
// section to a spec written before the verb existed, so on an upgraded corpus the verb never fires:
// 0 of 204 specs on one adopter, 7 of 360 on the self-hosting workspace (2026-09-22/23).
//
// The section sits OUTSIDE the hashed normative region, so appending it moves no `spec_sha256`
// and no authorization (AC-AMB-4 pins that with the real hashing function). This module therefore
// runs on the `--existing` reconcile and the upgrader's Phase 4 like the gitignore block does:
// idempotent, never-clobber in the only sense that matters (a spec that already has the section is
// never written), atomic writes, and a plan/apply split so `--dry-run` prints the same row.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin } from './util.mjs';

export const NORMATIVE_CLOSE = '<!-- /normative -->';
export const AMENDMENTS_HEADING = '## Amendments';
/** Exactly what is appended (AC-AMB-1); the leading blank line is added only when the file does
 * not already end with one, so a spec ending "...\n" gets "\n## Amendments..." and one ending
 * "...\n\n" gets the heading directly. Header columns are `foundry-amend.py`'s own row shape. */
export const AMENDMENTS_BLOCK = `${AMENDMENTS_HEADING}\n\n| date | what changed | why reality required it | auth_seq |\n|---|---|---|---|\n`;

// ER #223 (v1.17.1): EVERY `*.md` under specs/, not only `feat-*.md` — foundry-amend.py has no
// filename rule (it classifies by the normative region), and an adopter's delivery atoms are named
// `spec-*.md`. A README or template without a normative region lands in `skipped`, never written.
const SPEC_BASENAME_RE = /\.md$/;
const CODE_FENCE_RE = /```[\s\S]*?```/g;

/** The verdict `foundry-amend.py`'s amendments_section_ok gives: `present` when a `## Amendments`
 * heading appears after the LAST normative close marker and outside any fenced code block;
 * `absent` when it does not; `no-marker` when the spec has no close marker at all (amend's own
 * whole-body fallback applies there, and this module never writes such a file). Fenced blocks are
 * masked with same-length filler so the heading's offset stays comparable to the marker's, exactly
 * as the Python does; CRLF is tolerated because neither search depends on line structure. */
export function classifySpec(text) {
  const closeIdx = text.lastIndexOf(NORMATIVE_CLOSE);
  if (closeIdx === -1) return 'no-marker';
  const masked = text.replace(CODE_FENCE_RE, (m) => '\0'.repeat(m.length));
  const idx = masked.indexOf(AMENDMENTS_HEADING);
  if (idx !== -1 && idx > closeIdx) return 'present';
  // A heading BEFORE the marker does not count for amend either — the section must follow the
  // normative region — so it is `absent` and the block is appended at the end of the file.
  return 'absent';
}

/** Every regular `*.md` under `<root>/specs` (any basename — ER #223), depth-first, with symlinked FILES reported
 * separately (never followed, never written — AC-AMB-2) and symlinked DIRECTORIES not descended
 * (the same confinement instinct as the scaffold's confinedJoin: nothing outside the workspace
 * root is ever touched). Absent `specs/` yields an empty walk, not an error. */
export function walkSpecs(physicalRoot) {
  const files = [];
  const symlinks = [];
  // v1.17.1 security review Risk 1: the ROOT is confined like every entry under it — a `specs`
  // that is itself a symlink (or resolves outside the workspace) is never descended, never written.
  const specsRoot = confinedJoin(physicalRoot, 'specs');
  if (!specsRoot) return { files, symlinks };
  const rootStat = fs.lstatSync(specsRoot, { throwIfNoEntry: false });
  if (!rootStat || rootStat.isSymbolicLink() || !rootStat.isDirectory()) return { files, symlinks };
  const stack = [specsRoot];
  while (stack.length > 0) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      const abs = path.join(dir, ent.name);
      if (ent.isSymbolicLink()) {
        if (SPEC_BASENAME_RE.test(ent.name)) symlinks.push(abs);
        continue; // never descend a symlinked directory, never write a symlinked file
      }
      if (ent.isDirectory()) stack.push(abs);
      else if (ent.isFile() && SPEC_BASENAME_RE.test(ent.name)) files.push(abs);
    }
  }
  files.sort();
  symlinks.sort();
  return { files, symlinks };
}

/** Plan only — no write. `{ toAppend: [abs...], present, skipped, symlinks, total }` where `total`
 * counts every regular spec seen (the "of <m>" in the row) and `skipped` is the no-marker count. */
export function planAmendmentsBackfill({ physicalRoot }) {
  const { files, symlinks } = walkSpecs(physicalRoot);
  const toAppend = [];
  let present = 0;
  let skipped = 0;
  for (const abs of files) {
    let text;
    try {
      text = fs.readFileSync(abs, 'utf-8');
    } catch {
      skipped += 1;
      continue;
    }
    const verdict = classifySpec(text);
    if (verdict === 'present') present += 1;
    else if (verdict === 'no-marker') skipped += 1;
    else toAppend.push(abs);
  }
  return { physicalRoot, toAppend, present, skipped, symlinks, total: files.length, applied: false };
}

/** The bytes to append for a given current text: the block, preceded by one newline when the text
 * does not already end with a blank line, and by TWO when it does not end with a newline at all. */
export function appendBytesFor(text) {
  if (text === '' || text.endsWith('\n\n') || text.endsWith('\r\n\r\n')) return AMENDMENTS_BLOCK;
  if (text.endsWith('\n')) return `\n${AMENDMENTS_BLOCK}`;
  return `\n\n${AMENDMENTS_BLOCK}`;
}

/** Atomic append: read, compose, write a sibling temp file, rename over the original (the same
 * discipline as gitignoreReconcile's writer). Re-classifies right before writing so a file that
 * gained the section between plan and apply is left alone. Returns the count actually written. */
export function applyAmendmentsBackfill(plan) {
  let written = 0;
  // v1.18.0 (AC-V118C-8): every planned spec lands in exactly one bucket — written, gained the
  // section since the plan (present), or failed — so backfilled + present + skipped + failed ==
  // total and the post-upgrade arithmetic check can never refuse on an uncounted file.
  let failed = 0;
  const writtenPaths = [];
  for (const abs of plan.toAppend) {
    let text;
    try {
      text = fs.readFileSync(abs, 'utf-8');
    } catch {
      failed += 1;
      continue;
    }
    if (classifySpec(text) !== 'absent') { plan.present += 1; continue; }
    const tmp = `${abs}.amendments-backfill.tmp`;
    // v1.17.1 security review Risk 2: `wx` refuses to write through a planted sibling — a symlink
    // or a leftover file at the temp path means this spec is skipped, never written elsewhere.
    try {
      fs.writeFileSync(tmp, text + appendBytesFor(text), { encoding: 'utf-8', flag: 'wx' });
    } catch {
      failed += 1;
      continue;
    }
    try {
      fs.renameSync(tmp, abs);
    } catch {
      fs.rmSync(tmp, { force: true });
      failed += 1;
      continue;
    }
    written += 1;
    if (plan.physicalRoot) writtenPaths.push(path.relative(plan.physicalRoot, abs).split(path.sep).join('/'));
  }
  plan.applied = true;
  plan.written = written;
  plan.failed = failed;
  plan.writtenPaths = writtenPaths;
  return written;
}

/** The one row (AC-AMB-1). `null` when the workspace has no specs at all, so a fresh scaffold's
 * output is unchanged. Under dry-run the count is what WOULD be backfilled; after apply it is what
 * was. Symlinked spec files are named so the operator sees what was deliberately not touched. */
export function renderAmendmentsRow(plan) {
  if (!plan || (plan.total === 0 && plan.symlinks.length === 0)) return null;
  const n = plan.applied ? plan.written : plan.toAppend.length;
  const verb = plan.applied ? 'backfilled' : 'would backfill';
  let row = `  [amendments] ${verb} ${n} of ${plan.total} specs (${plan.present} already present, ${plan.skipped} skipped: no normative region${plan.failed ? `, ${plan.failed} FAILED to write` : ''})`;
  if (plan.symlinks.length > 0) {
    row += `; ${plan.symlinks.length} symlinked spec file(s) not touched: ${plan.symlinks.map((p) => path.basename(p)).join(', ')}`;
  }
  return row;
}
