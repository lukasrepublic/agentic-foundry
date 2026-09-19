// gitignoreReconcile.mjs — feat-gitignore-block-reconcile (ER #177, AC-GBR-1..5).
//
// `.gitignore` is one of scaffold.mjs's six template-derived managed files, so it already goes
// through the whole-file never-clobber plan in reconcile.mjs: absent -> `create`, byte-identical ->
// `unchanged`, anything else -> `drifted` and NEVER WRITTEN. That is correct for a file an adopter
// never touches, but `.gitignore` is NOT one of those — the template ships adopter-visible content
// (Risk #5's local-settings comment) around a FOUNDRY-RUNTIME-GITIGNORE-BEGIN/END block that is
// framework-owned. Once an adopter's `.gitignore` carries its own lines too, the whole-file compare
// reports `drifted` forever and the sentinel block — the one piece ER #169 needed every existing
// workspace to receive — never converges on an `--existing` reconcile or an upgrade run.
//
// This module is scoped to EXACTLY that block, the same way floorReconcile.mjs is scoped to the
// `permissions` key inside settings.json: converge the sentinel-delimited lines, leave every other
// line byte-identical, and never touch the file at all when the block already matches (AC-GBR-4).
// It deliberately does NOT port scripts/foundry-apply-runtime-gitignore.sh's Risk #9/#10
// deviation-line relocation — that machinery answers "does an adopter rule silently defeat a
// re-include", which is out of scope here (see the charter's Out of scope): this module only
// answers "does the managed block match the template", and reports converged either way.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin, RefusalError } from './util.mjs';

export const BEGIN_TOKEN = 'FOUNDRY-RUNTIME-GITIGNORE-BEGIN';
export const END_TOKEN = 'FOUNDRY-RUNTIME-GITIGNORE-END';

/** Split raw file text into a line array with NO trailing empty element for a final newline (a
 * file ending "a\nb\n" and one ending "a\nb" both split to ['a','b'] — the asymmetry is resolved on
 * WRITE, not on read: see writeGitignoreAtomically, which always terminates every line, mirroring
 * the shipped bash applier's own `write_output` (`printf '%s\n'` per line, unconditionally). A file
 * this module never writes (the `unchanged` action) is never re-serialized at all, so an original
 * file missing its final newline is left completely alone — this normalization only ever applies to
 * bytes this module itself is already rewriting. */
function splitLines(raw) {
  if (raw === '') return [];
  const body = raw.endsWith('\n') ? raw.slice(0, -1) : raw;
  return body.split('\n');
}

/** Scan a line array for the sentinel pair, exactly mirroring the shipped applier's
 * `scan_sentinels` malformed-state checks (AC-GBR-3), plus a stricter "at most one block" rule this
 * atom's own charter adds (the bash applier tolerates several closed blocks; the JS reconcile
 * refuses on the second one rather than silently acting on only the first). Returns
 * `{ ok: true, blocks: [{ start, end }] }` (0 or 1 entries) or `{ ok: false, reason }`; never
 * throws — callers decide whether a scan failure is a refusal or a bug (see loadDesiredBlock). */
export function scanSentinels(lines) {
  const blocks = [];
  let open = -1;
  for (let i = 0; i < lines.length; i += 1) {
    const ln = lines[i];
    const isBegin = ln.includes(BEGIN_TOKEN);
    const isEnd = ln.includes(END_TOKEN);
    if (isBegin && isEnd) {
      return { ok: false, reason: `line ${i + 1} carries both the BEGIN and END tokens: ${ln}` };
    }
    if (isBegin) {
      if (open >= 0) {
        return {
          ok: false,
          reason: `BEGIN sentinel at line ${open + 1} has no matching END sentinel (another BEGIN found at line ${i + 1} first)`,
        };
      }
      open = i;
    } else if (isEnd) {
      if (open < 0) {
        return { ok: false, reason: `END sentinel at line ${i + 1} precedes the first BEGIN sentinel` };
      }
      blocks.push({ start: open, end: i });
      open = -1;
    }
  }
  if (open >= 0) {
    return { ok: false, reason: `BEGIN sentinel at line ${open + 1} has no matching END sentinel` };
  }
  if (blocks.length > 1) {
    return { ok: false, reason: `${blocks.length} managed blocks found; expected at most one` };
  }
  return { ok: true, blocks };
}

/** The desired block, read fresh from the shipped template every call — never a copy of its
 * content vendored into this module, which is exactly the drift `cli/templates/gitignore.tmpl`
 * being a denied path (this atom may read it, never edit it) is designed to prevent. Returns the
 * BEGIN line through the END line, inclusive, exactly as `scaffold.mjs` writes them into a fresh
 * `.gitignore` — no re-templating, no substitution (gitignore.tmpl carries none). A malformed
 * shipped template is a packaging bug, not an adopter's data problem, so it throws a plain Error
 * rather than a RefusalError (nothing here has offered a refusal-shaped report for the template
 * itself to the CLI's caller). */
export function loadDesiredBlock(templatesDir) {
  const raw = fs.readFileSync(path.join(templatesDir, 'gitignore.tmpl'), 'utf-8');
  const lines = splitLines(raw);
  const scan = scanSentinels(lines);
  if (!scan.ok || scan.blocks.length !== 1) {
    throw new Error(
      `cli/templates/gitignore.tmpl carries no single well-formed managed block: ${scan.ok ? 'no block found' : scan.reason}`,
    );
  }
  const { start, end } = scan.blocks[0];
  return Object.freeze(lines.slice(start, end + 1));
}

/** Resolve `.gitignore` inside the physically-resolved target root, refusing (RefusalError) any
 * resolution that escapes it — same confinement discipline as every other managed path. lstat, not
 * stat: a symlinked `.gitignore` must be seen as such (AC-GBR-3's "a symlinked .gitignore is
 * refused the same way"), not silently followed. */
export function resolveGitignoreTarget(physicalRoot) {
  const joined = confinedJoin(physicalRoot, '.gitignore');
  if (joined === null) {
    throw new RefusalError('refusing .gitignore: path escapes the target root', '.gitignore');
  }
  const st = fs.lstatSync(joined, { throwIfNoEntry: false });
  if (!st) return { path: joined, present: false };
  if (!st.isFile()) return { path: joined, present: true, notRegular: true };
  return { path: joined, present: true };
}

/** Compute the reconcile action for an EXISTING, regular-file `.gitignore` WITHOUT touching the
 * filesystem beyond the one read already implied by `targetPath` — this is what lets --dry-run
 * report the same action a real run would take (AC-GBR-4). Returns one of:
 *   { action: 'unchanged' }
 *   { action: 'converged', nextLines }   -- a single block found, differs from the template
 *   { action: 'appended',  nextLines }   -- no block found; the template's block is appended
 *   { action: 'refused',   reason }      -- a malformed sentinel state (AC-GBR-3)
 * `desiredBlock` is the array `loadDesiredBlock` returns. */
export function planGitignoreBlock({ currentLines, desiredBlock }) {
  const scan = scanSentinels(currentLines);
  if (!scan.ok) {
    return { action: 'refused', reason: scan.reason };
  }
  if (scan.blocks.length === 0) {
    // AC-GBR-2: append, preceded by exactly one blank line. An empty (0-line) file has nothing to
    // precede, so the block is written on its own rather than opening the file with a blank line.
    const nextLines = currentLines.length > 0
      ? [...currentLines, '', ...desiredBlock]
      : [...desiredBlock];
    return { action: 'appended', nextLines };
  }
  const { start, end } = scan.blocks[0];
  const currentBlock = currentLines.slice(start, end + 1);
  const equal = currentBlock.length === desiredBlock.length
    && currentBlock.every((ln, i) => ln === desiredBlock[i]);
  if (equal) return { action: 'unchanged' };
  // AC-GBR-1: replace EXACTLY the sentinel-delimited lines; every line outside them, on either
  // side, is copied through untouched and in order.
  const nextLines = [...currentLines.slice(0, start), ...desiredBlock, ...currentLines.slice(end + 1)];
  return { action: 'converged', nextLines };
}

/** The whole read-plan step for a target known to be PRESENT: resolve, refuse a non-regular file
 * the same way a malformed sentinel state is refused, read, scan, plan. Returns `null` only when
 * the caller should not have invoked this at all (kept as a defensive assertion, not a normal
 * path — `reconcileGitignore` below is what decides absence is the create path's business). */
function planFromTarget(target, desiredBlock) {
  if (target.notRegular) {
    return { action: 'refused', reason: 'not a regular file (symlink or special file)', path: target.path };
  }
  const raw = fs.readFileSync(target.path, 'utf-8');
  const currentLines = splitLines(raw);
  const plan = planGitignoreBlock({ currentLines, desiredBlock });
  return { ...plan, path: target.path };
}

/** The orchestration entry point run.mjs / update.mjs call: resolve + plan, WITHOUT writing
 * anything (that is `applyGitignorePlan`'s job, called only after the same confirm/dry-run gate
 * every other write in this CLI goes through). Returns `null` when `.gitignore` is absent — AC-GBR-2
 * leaves that case to the existing managed-file create path untouched, so there is nothing for this
 * module to plan or report; the caller must not print a row for a `null` result. */
export function reconcileGitignorePlan({ physicalRoot, templatesDir }) {
  const target = resolveGitignoreTarget(physicalRoot);
  if (!target.present) return null;
  const desiredBlock = loadDesiredBlock(templatesDir);
  return planFromTarget(target, desiredBlock);
}

/** Install new `.gitignore` bytes by rename, same-directory temp opened O_EXCL then fsync then
 * rename — the identical pattern floorReconcile.mjs's `writeTargetAtomically` uses for settings.json
 * and for the identical reason: this is a rewrite of bytes belonging to an EXISTING regular file
 * already lstat-verified not to be a symlink, so plain reconcile.mjs's create-only O_EXCL write
 * (which requires the path NOT exist) cannot apply here — a same-dir rename is what makes the
 * replace atomic (same filesystem) and closes the same interrupted-write / classify-to-write-race
 * loss floorReconcile.mjs's own comment documents, without ever truncating the file in place. */
export function writeGitignoreAtomically(targetPath, nextLines) {
  const dir = path.dirname(targetPath);
  const tmp = path.join(dir, `.gitignore.${process.pid}.tmp`);
  const bytes = Buffer.from(nextLines.map((ln) => `${ln}\n`).join(''), 'utf-8');
  const fd = fs.openSync(tmp, 'wx');
  try {
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  try {
    fs.renameSync(tmp, targetPath);
  } catch (e) {
    fs.rmSync(tmp, { force: true });
    throw e;
  }
}

/** Apply a plan computed by `reconcileGitignorePlan`. A no-op for every action except `converged`
 * and `appended` — `unchanged` and `refused` both leave the file byte-for-byte as it was, which is
 * what makes AC-GBR-4's no-write-when-already-converged guarantee hold: there is no code path from
 * `unchanged` to a write syscall at all, not merely one that happens to produce identical bytes. */
export function applyGitignorePlan(plan) {
  if (!plan) return;
  if (plan.action === 'converged' || plan.action === 'appended') {
    writeGitignoreAtomically(plan.path, plan.nextLines);
  }
}

/** Render the single report row for a plan, in the SAME `  [action] path (detail)` shape
 * preview.mjs's own rows use. Returns `null` for a `null` plan (absent `.gitignore` — nothing to
 * print; the generic managed-file row already covers that case). The four non-null strings are
 * exactly AC-GBR-1/-2/-3's own report text. */
export function renderGitignoreRow(plan) {
  if (!plan) return null;
  switch (plan.action) {
    case 'converged':
      return '  [converged] .gitignore (managed block)';
    case 'appended':
      return '  [converged] .gitignore (managed block appended)';
    case 'unchanged':
      return '  [unchanged] .gitignore (managed block)';
    case 'refused':
      return `  [refused] .gitignore (malformed managed block: ${plan.reason})`;
    default:
      throw new Error(`unknown gitignore reconcile action: ${plan.action}`);
  }
}
