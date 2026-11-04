// upgradeCompat.mjs — the READ-ONLY half of the 2.0 upgrade path (feat-foundry-adoption-npx-upgrade-2-0,
// AC-NPX-2/-3/-8). It reads settings files, the platform's registry files and the workspace, and
// decides: versions and scopes, the registrations that bound the permission floor, what a new
// session would load, the cutover state, and which diagnosis rows fire. It spawns nothing and writes
// nothing: the single process-spawning module of the update path stays pluginRefresh.mjs, and the
// permission-floor write path stays floorReconcile.mjs.
//
// Everything this module returns that can reach the upgrade report or the run's last line is already
// reduced to the closed value set AC-NPX-7 names (versions, scope names, workspace-relative paths,
// shipped constants, URLs without userinfo/query/fragment). Free text from a file never leaves it.
import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';

/** The registry records for one plugin key (same shape the update path's other readers use). Kept here
 * so this module, which the bootstrap also imports, never pulls in the module that spawns `claude`. */
function scopeRecordsFor(doc, pluginKey) {
  const plugins = (doc && doc.plugins) || {};
  return Array.isArray(plugins[pluginKey]) ? plugins[pluginKey] : [];
}

/** The cutover record: the one path gn-07 defines and gn-13 only READS. Both entry points import this. */
export const CUTOVER_RECORD_REL = '.foundry/migration/authority.yaml';
export const CUTOVER_STATES = Object.freeze(['pending', 'recorded', 'unknown']);
export const SCOPE_NAMES = Object.freeze(['user', 'project', 'local']);
export const USER_SETTINGS_TOKEN = '<config-dir>/settings.json';
export const KNOWN_MARKETPLACES_TOKEN = '<config-dir>/plugins/known_marketplaces.json';

// ── names that reach the report or the preview ─────────────────────────────────────────────────

/** A workspace-relative path that is safe to put in the report and in a `[effect] class target` row: not
 * absolute, no `..`, and only letters, digits and `._@+/-` (no space, so a row's target tokens stay
 * unambiguous; no control character or backslash). A name read from a directory listing that fails this
 * is never echoed — it is reported as refused `unsafe-name`. */
export function safeRelPath(p) {
  return typeof p === 'string' && p.length > 0 && p.length <= 300 && !p.startsWith('/')
    && /^[A-Za-z0-9._@+/-]+$/.test(p) && !p.split('/').includes('..');
}

/** A string for the preview with every control character shown as `\xNN` (never raw). */
export function escapeForPreview(s) {
  return String(s).replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, (c) => `\\x${c.charCodeAt(0).toString(16).padStart(2, '0')}`);
}

// ── versions and run ids ────────────────────────────────────────────────────────────────────────

const STRICT_SEMVER_RE = /^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$/;

/** `MAJOR.MINOR.PATCH`, nothing else — a pre-release/build suffix, a prefix or a non-string is null. */
export function strictVersion(v) {
  return typeof v === 'string' && STRICT_SEMVER_RE.test(v) ? v : null;
}

/** -1 / 0 / 1 over two strict semvers; null when either is not one. */
export function compareSemver(a, b) {
  if (!strictVersion(a) || !strictVersion(b)) return null;
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < 3; i += 1) if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  return 0;
}

export const RUN_ID_RE = /^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$/;

/** `<UTC yyyymmddThhmmssZ>-<4 lowercase hex>` — a safe single path segment. */
export function makeRunId(now = new Date(), hex = randomBytes(2).toString('hex')) {
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.[0-9]+Z$/, 'Z');
  return `${stamp}-${hex}`;
}

// ── the cutover state (AC-NPX-8) ───────────────────────────────────────────────────────────────

function yamlScalar(rest) {
  let v = rest.trim();
  if (v.startsWith('#')) return '';
  v = v.replace(/\s+#.*$/, '').trim();
  if (v.length >= 2 && ((v[0] === '"' && v[v.length - 1] === '"') || (v[0] === "'" && v[v.length - 1] === "'"))) {
    v = v.slice(1, -1).trim();
  }
  return v;
}

/** `recorded` | `pending` | `unknown`. Fails CLOSED: anything but "no entry at the path" or a clean
 * record is `unknown`, and only `pending` lets the history writers run. No YAML dependency: two
 * column-0 lines are read, each of which must occur exactly once. */
export function readCutoverState(physicalRoot) {
  const parts = CUTOVER_RECORD_REL.split('/');
  let cur = physicalRoot;
  for (let i = 0; i < parts.length; i += 1) {
    cur = path.join(cur, parts[i]);
    let st;
    try {
      st = fs.lstatSync(cur);
    } catch (e) {
      return e && e.code === 'ENOENT' ? 'pending' : 'unknown';
    }
    if (st.isSymbolicLink()) return 'unknown';
    if (i < parts.length - 1 && !st.isDirectory()) return 'unknown';
    if (i === parts.length - 1 && !st.isFile()) return 'unknown';
    if (i === parts.length - 1 && st.size > 65536) return 'unknown';
  }
  let text;
  try {
    text = fs.readFileSync(cur, 'utf-8');
  } catch {
    return 'unknown';
  }
  const authority = [];
  const recordedBy = [];
  for (const line of text.split(/\r?\n/)) {
    let m = /^planning_authority:(.*)$/.exec(line);
    if (m) authority.push(yamlScalar(m[1]));
    m = /^recorded_by:(.*)$/.exec(line);
    if (m) recordedBy.push(yamlScalar(m[1]));
  }
  if (authority.length === 1 && authority[0] === 'github' && recordedBy.length === 1 && recordedBy[0] !== '') return 'recorded';
  return 'unknown';
}

// ── sources (AC-NPX-2 `sources`, AC-NPX-3 foreign-marketplace-source) ───────────────────────────

const REPO_RE = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
const asciiLower = (s) => s.replace(/[A-Z]/g, (c) => c.toLowerCase());
const isPlainObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);

/** A URL reduced to `scheme://host[:port]/path` — userinfo, query and fragment dropped. Anything that
 * does not parse as an absolute URL is null (never echoed). */
export function sanitizeUrl(value) {
  if (typeof value !== 'string' || value.length > 2048) return null;
  let u;
  try {
    u = new URL(value);
  } catch {
    return null;
  }
  if (!u.host) return null;
  return `${u.protocol}//${u.host}${u.pathname}`;
}

function canonicalJson(v) {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(',')}]`;
  if (isPlainObject(v)) return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonicalJson(v[k])}`).join(',')}}`;
  return JSON.stringify(v);
}

/** AC-NPX-3 "ours": type exactly `github`, the repo shape-gated BEFORE ASCII case-folding (U+212A folds
 * to `k` under toLowerCase, so the gate must come first) and equal to `marketplaceRepo`; or the whole
 * source object equals an entry of the shipped `supported_sources`. */
// A plain release tag (v1.2.3 / 1.2.3) or a SLASH-FREE branch name: `refs/pull/N/head`, `pull/N/merge` and any
// other path-shaped ref (an attacker-controlled PR ref) are never "ours" (security review of #286).
const REF_RE = /^(?:v?[0-9]+\.[0-9]+\.[0-9]+|[A-Za-z0-9][A-Za-z0-9._-]{0,99})$/;
export function isOurSource(src, marketplaceRepo, supportedSources = []) {
  // Fail closed (a decision the spec leaves open): a github source is ours only when it carries NOTHING
  // beyond `source` and `repo`, plus at most a plain tag/branch `ref` (the pre-1.7 tag pin the updater
  // migrates). Any other key — a `path`, a `sha`, anything a future platform reads — is a different
  // thing to fetch, so it must equal a shipped `supported_sources` entry exactly.
  const keys = isPlainObject(src) ? Object.keys(src) : [];
  const plainKeys = keys.every((k) => k === 'source' || k === 'repo' || k === 'ref');
  const refOk = !keys.includes('ref') || (typeof src.ref === 'string' && REF_RE.test(src.ref) && !src.ref.includes('..'));
  if (isPlainObject(src) && plainKeys && refOk && src.source === 'github' && typeof src.repo === 'string' && REPO_RE.test(src.repo)
    && typeof marketplaceRepo === 'string' && marketplaceRepo !== '' && asciiLower(src.repo) === asciiLower(marketplaceRepo)) {
    return true;
  }
  if (!isPlainObject(src)) return false;
  const mine = canonicalJson(src);
  return (Array.isArray(supportedSources) ? supportedSources : []).some((s) => isPlainObject(s) && canonicalJson(s) === mine);
}

function describeSource(location, scope, entry) {
  const src = isPlainObject(entry) && isPlainObject(entry.source) ? entry.source : null;
  const type = src && typeof src.source === 'string' && /^[a-z][a-z0-9-]{0,15}$/.test(src.source) ? src.source : null;
  let where = null;
  if (src) {
    if (type === 'github') where = typeof src.repo === 'string' && REPO_RE.test(src.repo) ? src.repo : null;
    else where = sanitizeUrl(src.url) || sanitizeUrl(src.repo);
  }
  return {
    location, scope, source_type: type, repo_or_url: where,
    auto_update: isPlainObject(entry) && typeof entry.autoUpdate === 'boolean' ? entry.autoUpdate : null,
  };
}

/** Inspect every registration under the foundry marketplace name: `known_marketplaces.json` (when it is
 * present but does not parse, that itself is a foreign finding) and `extraKnownMarketplaces` of each
 * settings file. `settingsFiles` is `[{ location, scope, obj }]` (obj null when absent/unparseable).
 * Returns `{ sources, foreign: [{ location, scope }] }`. */
export function collectSources({ configDir, marketplaceName, marketplaceRepo, supportedSources, settingsFiles }) {
  const sources = [];
  const foreign = [];
  const check = (location, scope, entry) => {
    sources.push(describeSource(location, scope, entry));
    const src = isPlainObject(entry) && isPlainObject(entry.source) ? entry.source : null;
    if (!isOurSource(src, marketplaceRepo, supportedSources)) foreign.push({ location, scope });
  };
  const kmPath = path.join(configDir, 'plugins', 'known_marketplaces.json');
  let raw = null;
  try {
    raw = fs.readFileSync(kmPath, 'utf-8');
  } catch (e) {
    if (e && e.code !== 'ENOENT') foreign.push({ location: KNOWN_MARKETPLACES_TOKEN, scope: 'user' });
  }
  if (raw !== null) {
    let doc = null;
    try { doc = JSON.parse(raw); } catch { doc = null; }
    if (!isPlainObject(doc)) {
      foreign.push({ location: KNOWN_MARKETPLACES_TOKEN, scope: 'user' });
    } else if (Object.prototype.hasOwnProperty.call(doc, marketplaceName)) {
      check(KNOWN_MARKETPLACES_TOKEN, 'user', doc[marketplaceName]);
    }
  }
  for (const f of settingsFiles) {
    const reg = f.obj && isPlainObject(f.obj.extraKnownMarketplaces) ? f.obj.extraKnownMarketplaces : null;
    if (reg && Object.prototype.hasOwnProperty.call(reg, marketplaceName)) check(f.location, f.scope, reg[marketplaceName]);
  }
  return { sources, foreign };
}

// ── the registry: versions, scopes and what a new session loads ─────────────────────────────────

/** The registry record for each scope. user = no `projectPath`; project/local = `projectPath` is the
 * workspace (a record whose `scope` is `local` is the local one). */
export function recordsByScope(registryDoc, pluginKey, cwd) {
  const records = scopeRecordsFor(registryDoc, pluginKey).filter((r) => isPlainObject(r));
  const here = path.resolve(cwd);
  const mine = records.filter((r) => r.projectPath && path.resolve(String(r.projectPath)) === here);
  return {
    user: records.find((r) => !r.projectPath) || null,
    project: mine.find((r) => r.scope !== 'local') || null,
    local: mine.find((r) => r.scope === 'local') || null,
  };
}

/** The installed version for THIS workspace (Terms): the project-scope record, else the user-scope
 * record. Strict semver or null — a record that is there but unreadable is null, not the other scope. */
export function installedVersionOf(registry, pluginKey, cwd) {
  if (!registry || !registry.ok) return null;
  const recs = recordsByScope(registry.doc, pluginKey, cwd);
  const hit = recs.project || recs.user;
  return hit ? strictVersion(hit.version) : null;
}

/** For each enabled scope: the version and install directory the registry names, and whether that
 * directory exists and carries `.claude-plugin/plugin.json` declaring that version. */
export function readSessionLoad({ registry, pluginKey, cwd, configDir, enabledScopes }) {
  const recs = registry && registry.ok ? recordsByScope(registry.doc, pluginKey, cwd) : { user: null, project: null, local: null };
  const cfg = path.resolve(configDir);
  return enabledScopes.map((scope) => {
    const rec = recs[scope];
    if (!rec) return { scope, version: null, install_path: null, directory_exists: false, manifest_declares_version: false };
    const version = strictVersion(rec.version);
    let exists = false;
    let manifestOk = false;
    let shown = null;
    if (typeof rec.installPath === 'string' && rec.installPath !== '') {
      const dir = path.resolve(rec.installPath);
      // shown only in the platform's own layout (<cache>/<marketplace>/<plugin>/<strict version>) under the
      // config dir — any other directory name is the registry's free text and stays out of the report
      const [plugin, marketplace] = String(pluginKey).split('@');
      const rel = dir.startsWith(cfg + path.sep) ? path.relative(cfg, dir).split(path.sep) : [];
      if (rel.length === 5 && rel[0] === 'plugins' && rel[1] === 'cache' && rel[2] === marketplace && rel[3] === plugin && strictVersion(rel[4])) {
        shown = `<config-dir>/${rel.join('/')}`;
      }
      try {
        exists = fs.statSync(dir).isDirectory();
      } catch {
        exists = false;
      }
      if (exists) {
        try {
          const manifest = JSON.parse(fs.readFileSync(path.join(dir, '.claude-plugin', 'plugin.json'), 'utf-8'));
          manifestOk = version !== null && manifest && manifest.version === version;
        } catch {
          manifestOk = false;
        }
      }
    }
    return { scope, version, install_path: shown, directory_exists: exists, manifest_declares_version: manifestOk };
  });
}

// ── the diagnosis table (AC-NPX-3) ──────────────────────────────────────────────────────────────

/** Table order IS precedence: `next_action` takes the remedy of the first fired row in this order. */
export const DIAGNOSIS_TABLE = Object.freeze([
  { code: 'input-unreadable', when: 'pre', effect: 'stop' },
  { code: 'foreign-marketplace-source', when: 'pre', effect: 'stop' },
  { code: 'unsupported-source-version', when: 'pre', effect: 'stop' },
  { code: 'downgrade', when: 'pre', effect: 'stop' },
  { code: 'advertised-version-unreadable', when: 'post-refresh', effect: 'stop' },
  { code: 'stale-updater', when: 'post-refresh', effect: 'stop' },
  { code: 'source-version-unknown', when: 'pre', effect: 'continue' },
  { code: 'input-skipped', when: 'pre', effect: 'continue' },
  { code: 'registry-unreadable', when: 'post-update', effect: 'continue' },
  { code: 'mixed-scope-version', when: 'post-update', effect: 'continue' },
  { code: 'install-path-missing', when: 'post-update', effect: 'continue' },
]);
const TABLE_INDEX = new Map(DIAGNOSIS_TABLE.map((r, i) => [r.code, i]));
export const effectOf = (code) => DIAGNOSIS_TABLE[TABLE_INDEX.get(code)].effect;

const SAFE_PATH_RE = /^[A-Za-z0-9._@<>/+ -]{1,200}$/;
const safeName = (s) => (typeof s === 'string' && SAFE_PATH_RE.test(s) && !s.split('/').includes('..') ? s : null);
const uniq = (a) => [...new Set(a)];

/** The remedy text for a fired row. Interpolates only closed-set values (versions, scopes, workspace
 * paths, the marketplace name/repo, the plugin key). */
export function remedyFor(code, v = {}) {
  const scopesOf = (list) => uniq((list || []).map((x) => (typeof x === 'string' ? x : x.scope)).filter((s) => SCOPE_NAMES.includes(s)));
  switch (code) {
    case 'input-unreadable':
      return `fix ${(v.files || []).map((f) => `${safeName(f.file) || 'a settings file'} (${f.reason})`).join(', ')}, then rerun`;
    case 'foreign-marketplace-source': {
      const regs = (v.regs || []).map((r) => `${safeName(r.location) || 'a registration'} (${r.scope})`).join(', ');
      const adds = uniq((v.regs || []).map((r) => r.scope)).filter((s) => SCOPE_NAMES.includes(s))
        .map((s) => `claude plugin marketplace add ${v.marketplaceRepo} --scope ${s}`).join('; ');
      return `remove the marketplace registration at ${regs}; then run: ${adds}`;
    }
    case 'unsupported-source-version':
      return `upgrade first with an updater release that supports ${strictVersion(v.installed) || 'the installed version'}`;
    case 'downgrade':
    case 'stale-updater':
      return 'npx update-agentic-workspace@latest';
    case 'advertised-version-unreadable':
      return `claude plugin marketplace update ${v.marketplaceName}, then rerun`;
    case 'source-version-unknown':
      return 'claude plugin list to confirm the installed version';
    case 'input-skipped':
      return `fix ${(v.files || []).map((f) => `${safeName(f.file) || 'a workspace file'} (${f.reason})`).join(', ')}, then rerun`;
    case 'registry-unreadable':
      return `claude plugin list; then claude plugin install ${v.pluginKey} --scope <scope> where the plugin is missing`;
    case 'mixed-scope-version':
      return scopesOf(v.scopes).map((s) => `claude plugin update ${v.pluginKey} --scope ${s}`).join('; ');
    case 'install-path-missing':
      return scopesOf(v.scopes).map((s) => `claude plugin install ${v.pluginKey} --scope ${s}`).join('; ');
    default:
      throw new Error(`unknown diagnosis code ${code}`);
  }
}

/** Merge raw `{code, vars}` firings into one `{code, remedy}` entry per fired row, in table order. */
export function composeDiagnoses(firings) {
  const merged = new Map();
  for (const f of firings) {
    const cur = merged.get(f.code) || { code: f.code, vars: {} };
    for (const [k, val] of Object.entries(f.vars || {})) {
      cur.vars[k] = Array.isArray(val) ? [...(cur.vars[k] || []), ...val] : val;
    }
    merged.set(f.code, cur);
  }
  return [...merged.values()]
    .sort((a, b) => TABLE_INDEX.get(a.code) - TABLE_INDEX.get(b.code))
    .map((m) => ({ code: m.code, remedy: remedyFor(m.code, m.vars) }));
}

/** Pre rows 1-4 and the two `pre` continue rows that need only what is already read. */
export function evalPreRows({
  unreadable, foreign, installedBefore, builtFor, supportedFromMin, skipped, marketplaceRepo, pluginKey,
}) {
  const out = [];
  if (unreadable.length > 0) out.push({ code: 'input-unreadable', vars: { files: unreadable } });
  if (foreign.length > 0) out.push({ code: 'foreign-marketplace-source', vars: { regs: foreign, marketplaceRepo } });
  if (strictVersion(supportedFromMin) === null
    || (installedBefore !== null && compareSemver(installedBefore, supportedFromMin) === -1)) {
    out.push({ code: 'unsupported-source-version', vars: { installed: installedBefore } });
  }
  if (installedBefore !== null && strictVersion(builtFor) && compareSemver(installedBefore, builtFor) === 1) {
    out.push({ code: 'downgrade', vars: {} });
  }
  if (installedBefore === null) out.push({ code: 'source-version-unknown', vars: { pluginKey } });
  if (skipped.length > 0) out.push({ code: 'input-skipped', vars: { files: skipped } });
  return out;
}

/** Post-refresh rows: the advertised version of the plugin in the marketplace manifest the platform holds. */
export function evalPostRefreshRows({ advertised, builtFor, marketplaceName }) {
  if (advertised === null) return [{ code: 'advertised-version-unreadable', vars: { marketplaceName } }];
  if (advertised !== builtFor) return [{ code: 'stale-updater', vars: {} }];
  return [];
}

/** Post-update rows: the registry after the plugin-update phase, per enabled scope. */
export function evalPostUpdateRows({ registry, toVersion, enabledScopes, sessionLoad, cwd, pluginKey }) {
  if (!registry.ok) return [{ code: 'registry-unreadable', vars: { pluginKey } }];
  const out = [];
  const recs = recordsByScope(registry.doc, pluginKey, cwd);
  const mixed = [];
  const missing = [];
  for (const scope of enabledScopes) {
    const rec = recs[scope];
    const load = sessionLoad.find((s) => s.scope === scope);
    if (!rec || !load || !load.directory_exists || !load.manifest_declares_version) { missing.push(scope); continue; }
    if (toVersion !== null && strictVersion(rec.version) !== toVersion) mixed.push(scope);
  }
  if (mixed.length > 0) out.push({ code: 'mixed-scope-version', vars: { scopes: mixed, pluginKey } });
  if (missing.length > 0) out.push({ code: 'install-path-missing', vars: { scopes: missing, pluginKey } });
  return out;
}

/** Reason words for a planned-write input that cannot be used (a closed set). */
export function inputReason(message) {
  const m = String(message || '');
  if (/outside/.test(m)) return 'resolves outside the workspace';
  if (/regular|symlink/.test(m)) return 'not a regular file';
  if (/JSON|parse|Unexpected|object/i.test(m)) return 'does not parse';
  return 'unreadable';
}

// ── instructions pending (AC-NPX-2 adoption.instructions_pending) ───────────────────────────────

/** Each occurrence of a retired term in CLAUDE.md, AGENTS.md, WORKFLOW.md and `.claude/*.md`:
 * `{ pending: [{ path, line, term }], unsafe }` — `unsafe` counts `.claude/*.md` files whose name is not a
 * safe path and which were therefore skipped. Links and non-regular files are skipped; nothing is written. */
export function scanInstructionsPending(physicalRoot, terms) {
  const files = ['CLAUDE.md', 'AGENTS.md', 'WORKFLOW.md'];
  let unsafe = 0;
  const claudeDir = path.join(physicalRoot, '.claude');
  const dst = fs.lstatSync(claudeDir, { throwIfNoEntry: false });
  if (dst && dst.isDirectory() && !dst.isSymbolicLink()) {
    let names = [];
    try { names = fs.readdirSync(claudeDir).sort(); } catch { names = []; }
    for (const n of names) {
      if (!n.endsWith('.md')) continue;
      if (safeRelPath(`.claude/${n}`)) files.push(`.claude/${n}`);
      else unsafe += 1; // never echoed: counted so the caller can say a file was skipped
    }
  }
  const lowered = terms.map((t) => [t, t.toLowerCase()]);
  const found = [];
  for (const rel of files) {
    const abs = path.join(physicalRoot, rel);
    const st = fs.lstatSync(abs, { throwIfNoEntry: false });
    if (!st || !st.isFile() || st.size > 1048576) continue;
    let text;
    try { text = fs.readFileSync(abs, 'utf-8'); } catch { continue; }
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i += 1) {
      const low = lines[i].toLowerCase();
      for (const [term, lt] of lowered) if (low.includes(lt)) found.push({ path: rel, line: i + 1, term });
    }
  }
  return { pending: found, unsafe };
}

/** AC-NPX-9: why the two history writers are off for this run, or null when they run. The flag is
 * checked first; an unreadable or recorded cutover keeps them off (only `pending` lets them run). */
export function legacyOffCondition({ flag, cutover }) {
  if (flag !== true) return 'flag absent';
  if (cutover === 'recorded') return 'cutover recorded';
  if (cutover !== 'pending') return 'cutover unknown';
  return null;
}
