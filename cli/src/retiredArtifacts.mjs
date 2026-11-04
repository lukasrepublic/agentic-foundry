// retiredArtifacts.mjs — the sweep for workspace-side files an earlier release wrote and no current
// release reads (hotfix-v1.17.4, ER #236). Driven by the shipped catalogue `cli/retired-artifacts.json`:
// every present entry is reported `[stale]` on every run; removal happens only under `--cleanup`,
// only for catalogued paths, only when the path is exactly the catalogued kind (a regular file, or a
// real directory) — a symlink or the other kind is reported `[refused]` and left alone, and so is a
// hook under `.claude/hooks/` that a hook command in `.claude/settings*.json` still names, and so is
// any path a workspace wiring file (CI, scripts, settings, root docs — the catalogue's reference_scan)
// still names by its full relative path (ER #241). Nothing
// outside the catalogue is ever a candidate: `.claude/skills`, `.claude/agents` and every operator
// file are invisible to this module by construction.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin } from './util.mjs';
import { makeRunId, RUN_ID_RE, safeRelPath, escapeForPreview } from './upgradeCompat.mjs';

export const CATALOGUE_REL = 'retired-artifacts.json';
export const RETIRED_ROOT_REL = '.foundry/retired';

/** The shipped retired-term list (feat-foundry-adoption-npx-upgrade-2-0, AC-NPX-2): terms naming
 * machinery earlier releases shipped. A missing or malformed list is empty — never a guess. */
export function retiredTerms(catalogue) {
  const t = catalogue && catalogue.retired_terms && catalogue.retired_terms.terms;
  return Array.isArray(t) ? t.filter((x) => typeof x === 'string' && x.length > 0 && x.length <= 80) : [];
}

/** Load and validate the catalogue shipped beside `src/` (the package root). Refuses a malformed
 * entry loudly rather than sweeping with a half-read list. */
export function loadRetiredCatalogue(pkgDir) {
  const doc = JSON.parse(fs.readFileSync(path.join(pkgDir, CATALOGUE_REL), 'utf-8'));
  if (!doc || doc.schema_version !== 1 || !Array.isArray(doc.entries)) {
    throw new Error('retired-artifacts.json: unrecognised schema');
  }
  const rs = doc.reference_scan;
  const relOk = (x) => typeof x === 'string' && x.length > 0 && !path.isAbsolute(x) && !x.split(/[\\/]+/).includes('..');
  if (!rs || !Array.isArray(rs.files) || !Array.isArray(rs.dirs) || !Array.isArray(rs.root_suffixes)
    || !rs.files.every(relOk) || !rs.dirs.every(relOk) || !rs.root_suffixes.every((x) => typeof x === 'string' && x.startsWith('.'))
    || !['max_files', 'max_dirs', 'max_bytes', 'max_total_bytes'].every((k) => Number.isInteger(rs[k]) && rs[k] >= 1)) {
    throw new Error('retired-artifacts.json: reference_scan missing or malformed');
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

const HOOKS_DIR_REL = '.claude/hooks/';
const SETTINGS_FILES = ['.claude/settings.json', '.claude/settings.local.json'];

/** Every `command` string under `hooks` in the two workspace settings files, joined — the text a
 * hook candidate's basename is checked against (PR #237 security review Risk 1: a basename glob
 * must never take a hook the operator still wires). `unreadable` is true when a settings file
 * exists but does not parse: then nothing under `.claude/hooks/` is planned removable (fail-closed). */
function hookCommandText(physicalRoot) {
  const parts = [];
  const perFile = [];
  let unreadable = false;
  for (const rel of SETTINGS_FILES) {
    const abs = confinedJoin(physicalRoot, rel);
    if (!abs) continue;
    let raw;
    try {
      if (!fs.lstatSync(abs).isFile()) continue;
      raw = fs.readFileSync(abs, 'utf-8');
    } catch {
      continue; // absent: nothing wired there
    }
    let obj;
    try {
      obj = JSON.parse(raw);
    } catch {
      unreadable = true;
      continue;
    }
    const own = [];
    const walk = (v) => {
      if (Array.isArray(v)) { for (const x of v) walk(x); return; }
      if (v && typeof v === 'object') {
        if (typeof v.command === 'string') { parts.push(v.command); own.push(v.command); }
        for (const x of Object.values(v)) walk(x);
      }
    };
    if (obj && typeof obj === 'object' && obj.hooks) walk(obj.hooks);
    perFile.push([rel, own.join('\n')]);
  }
  return { text: parts.join('\n'), unreadable, perFile };
}

// Skipped at ANY depth: version-control internals and installed dependencies only (PR #242 review
// Risk 1 — `build`/`dist`/`venv` inside a wiring dir may hold wiring, so they are read).
const SKIP_DIRS = new Set(['.git', 'node_modules']);
const ABSENT = new Set(['ENOENT', 'ENOTDIR']);

/** ER #241: read every file the catalogue's `reference_scan` names (explicit files, the tree under each
 * listed dir, and workspace-root files with a listed suffix). Returns `{ texts: [[relPath, text]],
 * incomplete: null | '<why>' }`. Links are never followed and never skipped silently: a symlink, a
 * path escaping the root, a non-ENOENT stat error, an unreadable entry, or any budget exceeded
 * (`max_files`, `max_dirs`, `max_bytes` per file, `max_total_bytes`) makes the scan `incomplete`, and
 * the caller then refuses every row (PR #242 review Risks 2 and 5). Iterative, so depth cannot throw. */
export function scanReferenceTexts(physicalRoot, rs) {
  const texts = [];
  let files = 0;
  let dirs = 0;
  let bytes = 0;
  let incomplete = null;
  const fail = (why) => { if (!incomplete) incomplete = why; };
  const statOf = (rel) => {
    const abs = confinedJoin(physicalRoot, rel);
    if (!abs) { fail(`${rel} resolves outside the workspace`); return null; }
    try {
      return { abs, st: fs.lstatSync(abs) };
    } catch (e) {
      if (!ABSENT.has(e.code)) fail(`${rel} unreadable (${e.code || 'error'})`);
      return null; // absent: nothing wired there
    }
  };
  const readFile = (rel, abs, st) => {
    files += 1;
    if (files > rs.max_files) return fail(`more than ${rs.max_files} files to scan`);
    if (st.size > rs.max_bytes) return fail(`${rel} is larger than ${rs.max_bytes} bytes`);
    bytes += st.size;
    if (bytes > rs.max_total_bytes) return fail(`more than ${rs.max_total_bytes} bytes to scan`);
    try { texts.push([rel, fs.readFileSync(abs, 'utf-8')]); } catch (e) { fail(`${rel} unreadable (${e.code || 'error'})`); }
  };
  const visit = (rel, { treeRoot }) => {
    const hit = statOf(rel);
    if (!hit) return [];
    const { abs, st } = hit;
    if (st.isSymbolicLink()) { fail(`${rel} is a symlink (not followed)`); return []; }
    if (st.isFile()) { readFile(rel, abs, st); return []; }
    if (!st.isDirectory() || !treeRoot) return [];
    dirs += 1;
    if (dirs > rs.max_dirs) { fail(`more than ${rs.max_dirs} directories to scan`); return []; }
    let names;
    try { names = fs.readdirSync(abs).sort(); } catch (e) { fail(`${rel} unreadable (${e.code || 'error'})`); return []; }
    return names.filter((n) => !SKIP_DIRS.has(n)).map((n) => path.posix.join(rel, n));
  };
  for (const f of rs.files) { visit(f, { treeRoot: false }); if (incomplete) return { texts, incomplete }; }
  for (const d of rs.dirs) {
    const stack = [d];
    while (stack.length > 0 && !incomplete) stack.push(...visit(stack.pop(), { treeRoot: true }).reverse());
    if (incomplete) return { texts, incomplete };
  }
  let rootNames = [];
  try { rootNames = fs.readdirSync(physicalRoot).sort(); } catch (e) { fail(`workspace root unreadable (${e.code || 'error'})`); }
  const explicit = new Set(rs.files);
  for (const n of rootNames) {
    if (incomplete) break;
    if (explicit.has(n) || !rs.root_suffixes.some((suf) => n.endsWith(suf))) continue;
    visit(n, { treeRoot: false });
  }
  return { texts, incomplete };
}

/** Plan: `{ rows: [{ relPath, kind, retired_in, reason, state, why?, entries? }], present, refused }`
 * where `state` is `stale` (present, of the catalogued kind, and not wired) or `refused` (present
 * but a symlink, the other kind, escaping the root, or — for a hook — still named by a hook command
 * in `.claude/settings*.json`, or those files unreadable); absent paths are omitted. A `dir` row
 * carries `entries` (its direct entry count) so the operator sees what a removal takes. Pure over
 * the filesystem — nothing is written. */
export function planRetiredArtifacts({ physicalRoot, catalogue }) {
  const rows = [];
  let hooks = null; // read lazily, once, only when a hook candidate is present
  let refs = null;  // ER #241: the reference scan, read lazily, once, only when a stale candidate exists
  for (const entry of catalogue.entries) {
    for (const relPath of candidates(physicalRoot, entry)) {
      // a name read from a directory listing that is not a safe path is never echoed or acted on
      if (!safeRelPath(relPath)) {
        rows.push({ relPath, kind: entry.kind, retired_in: entry.retired_in, reason: entry.reason, state: 'refused', why: 'unsafe-name', referrers: [] });
        continue;
      }
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
      const row = { relPath, kind: entry.kind, retired_in: entry.retired_in, reason: entry.reason, state: kindOk ? 'stale' : 'refused', why: kindOk ? null : 'kind', referrers: [] };
      if (row.state === 'stale' && relPath.startsWith(HOOKS_DIR_REL)) {
        if (hooks === null) hooks = hookCommandText(physicalRoot);
        if (hooks.unreadable) { row.state = 'refused'; row.why = 'settings-unreadable'; }
        else if (hooks.text.includes(path.posix.basename(relPath))) {
          row.state = 'refused'; row.why = 'referenced';
          row.referrers = hooks.perFile.filter(([, t]) => t.includes(path.posix.basename(relPath))).map(([rel]) => rel);
        }
      }
      // AC-NPX-13: a symbolic link (or non-directory) anywhere above the entry means a move could
      // cross into another class of path, so the row is refused at plan time too.
      if (row.state === 'stale' && ancestorProblem(physicalRoot, relPath)) { row.state = 'refused'; row.why = 'link-in-path'; }
      if (row.state === 'stale') {
        // ER #241: an adopter may re-adopt a retired path for their own CI or tooling. A path named
        // (full relative path) by any file the catalogue's reference_scan lists is refused, never
        // removed; a scan that could not finish refuses every row (fail-closed).
        if (refs === null) refs = scanReferenceTexts(physicalRoot, catalogue.reference_scan);
        if (refs.incomplete) { row.state = 'refused'; row.why = 'scan-incomplete'; row.detail = refs.incomplete; }
        else {
          // full relative path anywhere in the scan set; for a hook, also its basename in any OTHER
          // hook script (a live hook that sources `$(dirname "$0")/<name>` — PR #242 review Risk 3)
          const base = relPath.startsWith(HOOKS_DIR_REL) ? path.posix.basename(relPath) : null;
          const by = refs.texts.filter(([rel, text]) => rel !== relPath
            && (text.includes(relPath) || (base && rel.startsWith(HOOKS_DIR_REL) && text.includes(base)))).map(([rel]) => rel);
          if (by.length > 0) { row.state = 'refused'; row.why = 'referenced'; row.refs = by.slice(0, 3); row.referrers = by.slice(0, 20); }
        }
      }
      if (row.state === 'stale' && entry.kind === 'dir') {
        try { row.entries = fs.readdirSync(abs).length; } catch { row.entries = null; }
      }
      rows.push(row);
    }
  }
  return {
    rows,
    present: rows.filter((r) => r.state === 'stale').length,
    refused: rows.filter((r) => r.state === 'refused').length,
  };
}

/** First non-directory-or-symlink component ABOVE `relPath`'s leaf, or null (AC-NPX-13). `lstat`
 * from the workspace root down: a component that is missing is not a link (the leaf's own lstat
 * decides presence); one that is a symbolic link or any non-directory is a problem. */
function ancestorProblem(physicalRoot, relPath) {
  const parts = relPath.split('/').filter(Boolean);
  let cur = physicalRoot;
  for (let i = 0; i < parts.length - 1; i += 1) {
    cur = path.join(cur, parts[i]);
    const st = fs.lstatSync(cur, { throwIfNoEntry: false });
    if (!st) return null;
    if (st.isSymbolicLink() || !st.isDirectory()) return parts.slice(0, i + 1).join('/');
  }
  return null;
}

/** True while `created` (outermost first) is a chain of directories each holding nothing but the next one. */
function emptyChain(created) {
  for (let i = 0; i < created.length; i += 1) {
    let names;
    try { names = fs.readdirSync(created[i]); } catch { return false; }
    const next = created[i + 1] ? path.basename(created[i + 1]) : null;
    if (names.length > (next ? 1 : 0) || (names.length === 1 && names[0] !== next)) return false;
  }
  return true;
}

/** Create `.foundry/retired/<runId>/<dirname(rel)>` one component at a time, each `lstat`ed first:
 * an existing component must be a real directory (a symbolic link or file is a refusal), a missing
 * one is created with a plain `mkdir` and pushed onto `created`. Returns null, or the refusal reason.
 * The caller removes `created` again when the rename that follows does not happen. */
function prepareDestination(physicalRoot, destRel, created) {
  const parts = destRel.split('/').filter(Boolean);
  let cur = physicalRoot;
  for (let i = 0; i < parts.length - 1; i += 1) {
    cur = path.join(cur, parts[i]);
    const st = fs.lstatSync(cur, { throwIfNoEntry: false });
    if (!st) {
      fs.mkdirSync(cur, { mode: 0o755 });
      created.push(cur);
    } else if (st.isSymbolicLink() || !st.isDirectory()) {
      return 'link-in-path';
    }
  }
  return null;
}

/** The per-run record of what was moved (AC-NPX-13): `.foundry/retired/<runId>/manifest.json`. */
function writeQuarantineManifest(physicalRoot, runId, moved) {
  if (moved.length === 0) return;
  const abs = path.join(physicalRoot, RETIRED_ROOT_REL, runId, 'manifest.json');
  const doc = { schema_version: 1, run_id: runId, entries: moved.map((m) => ({ path: m.path, restore_from: m.restore_from })) };
  fs.writeFileSync(abs, `${JSON.stringify(doc, null, 2)}\n`, { encoding: 'utf-8', flag: 'wx' });
}

/** Quarantine every `stale` row: ONE `rename` of the entry to `.foundry/retired/<runId>/<rel>`.
 * Nothing is unlinked, copied or deleted — the bytes stay and a restore is the same move back. Every
 * component of the source (above the leaf) and of the destination is `lstat`ed from the root down; a
 * symbolic link or non-directory anywhere, an existing destination, or a failing rename (EXDEV
 * included — there is no copy fallback) leaves the entry in place and the row `refused` with a
 * reason. The only recursive removal left in this module takes back the empty directories THIS call
 * created for a destination whose rename did not happen. Returns the count moved; `runId` is derived
 * when none is given, so the two-argument form still works. */
export function applyRetiredArtifacts(plan, physicalRoot, opts) {
  const runId = (opts && opts.runId) || makeRunId();
  if (!RUN_ID_RE.test(runId)) throw new Error('applyRetiredArtifacts: runId is not a safe single path segment');
  let removed = 0;
  const moved = [];
  try {
    for (const row of plan.rows) {
      // one row failing for ANY reason refuses that row only; the rows already moved stay recorded
      try {
        if (row.state !== 'stale') continue;
        const abs = confinedJoin(physicalRoot, row.relPath);
        if (!abs) continue;
        const destRel = `${RETIRED_ROOT_REL}/${runId}/${row.relPath}`;
        const absDest = confinedJoin(physicalRoot, destRel);
        if (!absDest) { row.state = 'refused'; row.why = 'link-in-path'; continue; }
        let st;
        try {
          st = fs.lstatSync(abs);
        } catch {
          continue;
        }
        if (st.isSymbolicLink()) continue;
        if (row.kind === 'dir' ? !st.isDirectory() : !st.isFile()) continue;
        if (ancestorProblem(physicalRoot, row.relPath)) { row.state = 'refused'; row.why = 'link-in-path'; continue; }
        if (fs.lstatSync(absDest, { throwIfNoEntry: false })) { row.state = 'refused'; row.why = 'destination-exists'; continue; }
        const created = [];
        let failure = null;
        try {
          const why = prepareDestination(physicalRoot, destRel, created);
          if (why) failure = { why };
          else fs.renameSync(abs, absDest);
        } catch (e) {
          failure = { why: 'remove-failed', error: e && e.code ? e.code : 'error' };
        }
        if (failure) {
          // nothing moved: take back ONLY the directories THIS call created for the destination, and only
          // while they still form an empty chain (a directory that existed before is never in `created`)
          if (created.length > 0 && emptyChain(created)) fs.rmSync(created[0], { recursive: true, force: false });
          row.state = 'refused';
          row.why = failure.why;
          if (failure.error) row.error = failure.error;
          continue;
        }
        removed += 1;
        row.state = 'removed';
        row.restore_from = destRel;
        moved.push({ path: row.relPath, restore_from: destRel });
      } catch (e) {
        if (row.state !== 'removed') { row.state = 'refused'; row.why = 'remove-failed'; row.error = e && e.code ? e.code : 'error'; }
      }
    }
  } finally {
    // whatever moved is recorded, however the loop ended
    writeQuarantineManifest(physicalRoot, runId, moved);
    plan.removed = removed;
    plan.runId = runId;
    plan.present = plan.rows.filter((r) => r.state === 'stale').length;
    plan.refused = plan.rows.filter((r) => r.state === 'refused').length;
  }
  return removed;
}

/** The rows every writer prints. `phase` is `'preview'` (before the first write) or `'result'`
 * (after apply; the default). `cleanup` false → `[stale] …; remove with --cleanup` in both phases.
 * `cleanup` true → the preview says `; will be removed`; after apply, `[removed]` for what went,
 * `[refused]` for what was never removable, and `— NOT removed (changed since the plan)` only for a
 * row apply skipped because it changed between plan and apply (hotfix-v1.17.5: the preview used to
 * print that last wording, contradicting the `[removed]` row the same run printed next). Empty when
 * nothing catalogued is present. */
export function renderRetiredArtifactRows(plan, { cleanup, phase = 'result', runDir = null } = {}) {
  const out = [];
  for (const r0 of plan.rows) {
    const r = { ...r0, relPath: escapeForPreview(r0.relPath), refs: r0.refs ? r0.refs.map(escapeForPreview) : r0.refs };
    const size = r.kind === 'dir' && typeof r.entries === 'number' ? ` [${r.entries} entr${r.entries === 1 ? 'y' : 'ies'}]` : '';
    if (r.state === 'stale') {
      const tail = !cleanup ? '; remove with --cleanup'
        : phase === 'preview' ? `; will be removed${runDir ? `; quarantined to ${runDir}` : ''}`
          : ' — NOT removed (changed since the plan)';
      out.push(`  [stale] ${r.relPath}${size} — retired in v${r.retired_in} (${r.reason})${tail}`);
    } else if (r.state === 'removed') {
      out.push(`  [removed] ${r.relPath}${size} — retired in v${r.retired_in}${r.restore_from ? `; restore from ${r.restore_from}` : ''}`);
    } else if (r.why === 'referenced' && r.refs) {
      out.push(`  [refused] ${r.relPath} — still referenced by ${r.refs.join(', ')} — left alone`);
    } else if (r.why === 'referenced') {
      out.push(`  [refused] ${r.relPath} — still named by a hook command in .claude/settings*.json — left alone`);
    } else if (r.why === 'scan-incomplete') {
      out.push(`  [refused] ${r.relPath} — the reference scan could not finish (${r.detail}) — left alone`);
    } else if (r.why === 'remove-failed') {
      out.push(`  [refused] ${r.relPath} — removal failed (${r.error}) — left in place`);
    } else if (r.why === 'unsafe-name') {
      out.push(`  [refused] ${r.relPath} — the file name is not a safe path (unsafe-name) — left alone`);
    } else if (r.why === 'link-in-path') {
      out.push(`  [refused] ${r.relPath} — a component of its path or of its quarantine destination is a symbolic link or not a directory — left in place`);
    } else if (r.why === 'destination-exists') {
      out.push(`  [refused] ${r.relPath} — its quarantine destination already exists — left in place`);
    } else if (r.why === 'settings-unreadable') {
      out.push(`  [refused] ${r.relPath} — .claude/settings*.json does not parse, so its hook wiring is unknown — left alone`);
    } else {
      out.push(`  [refused] ${r.relPath} — present but not a regular ${r.kind} (a link, or the other kind) — left alone`);
    }
  }
  return out;
}
