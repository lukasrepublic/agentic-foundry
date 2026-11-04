// pathClasses.mjs — the class lookup behind the updater's preview (feat-foundry-adoption-npx-upgrade-2-0,
// AC-NPX-4/-6). A pure module: it reads the shipped table `cli/path-classes.json` and answers "which of
// the three classes is this workspace path". It also owns the preview-row vocabulary (the closed effect
// set) so the writer, the parser the tests use, and the write/no-write split share one definition.
import fs from 'node:fs';
import path from 'node:path';

export const PATH_CLASSES_REL = 'path-classes.json';
export const CLASSES = Object.freeze(['framework-owned', 'project-customization', 'retained-history']);
export const EFFECTS = Object.freeze([
  'invoke', 'create', 'reconcile', 'keep', 'retire-rows', 'migrate', 'review',
  'legacy-backfill', 'legacy-true-up', 'quarantine', 'report',
]);
/** Every effect except these three is a write effect: its row names the path(s) the run may change. */
export const NON_WRITE_EFFECTS = Object.freeze(['invoke', 'keep', 'review']);
export const WRITE_EFFECTS = Object.freeze(EFFECTS.filter((e) => !NON_WRITE_EFFECTS.includes(e)));

/** The protected set (Terms): paths no run may change. The cutover record is added by the caller
 * (its path is a constant owned by upgradeCompat.mjs). */
export const PROTECTED_BASE = Object.freeze([
  '.claude/foundry-operators.json',
  '.claude/foundry-project.json',
  '.foundry/permissions.yaml',
  '.mcp.json',
]);

const normalize = (rel) => String(rel).replace(/\\/g, '/').replace(/^\.\//, '');

/** `*` stays inside one segment; `**` crosses segments; a leading `**\/` also matches zero directories. */
export function globToRegExp(pattern) {
  let re = '';
  let i = 0;
  while (i < pattern.length) {
    const c = pattern[i];
    if (c === '*') {
      if (pattern[i + 1] === '*') {
        if (pattern[i + 2] === '/') { re += '(?:.*/)?'; i += 3; } else { re += '.*'; i += 2; }
      } else { re += '[^/]*'; i += 1; }
    } else {
      re += c.replace(/[.+?^${}()|[\]\\]/g, '\\$&');
      i += 1;
    }
  }
  return new RegExp(`^${re}$`);
}

export function loadPathClasses(pkgDir) {
  const doc = JSON.parse(fs.readFileSync(path.join(pkgDir, PATH_CLASSES_REL), 'utf-8'));
  if (!doc || doc.schema_version !== 1 || !doc.classes || typeof doc.classes !== 'object') {
    throw new Error('path-classes.json: unrecognised schema');
  }
  if (!CLASSES.includes(doc.default_class)) throw new Error('path-classes.json: bad default_class');
  const compiled = [];
  for (const [cls, patterns] of Object.entries(doc.classes)) {
    if (!CLASSES.includes(cls) || cls === doc.default_class) throw new Error(`path-classes.json: bad class ${cls}`);
    if (!Array.isArray(patterns) || !patterns.every((p) => typeof p === 'string' && p && !path.isAbsolute(p) && !p.split('/').includes('..'))) {
      throw new Error(`path-classes.json: bad patterns for ${cls}`);
    }
    compiled.push({ cls, res: patterns.map(globToRegExp) });
  }
  // framework-owned first: a declared managed path wins over the history glob it also matches
  compiled.sort((a, b) => (a.cls === 'framework-owned' ? -1 : 0) - (b.cls === 'framework-owned' ? -1 : 0));
  return { defaultClass: doc.default_class, compiled };
}

/** The class of a workspace-relative path. `catalogueEntries` are the retired-artifact catalogue's
 * `path` values (a single `*` in the last segment is allowed); each, and everything beneath it, is
 * framework-owned. */
export function classOf(table, rel, { catalogueEntries = [] } = {}) {
  const p = normalize(rel);
  for (const { cls, res } of table.compiled) {
    if (res.some((re) => re.test(p))) return cls;
  }
  for (const e of catalogueEntries) {
    const re = globToRegExp(normalize(e));
    if (re.test(p) || (!e.includes('*') && p.startsWith(`${normalize(e)}/`))) return 'framework-owned';
  }
  return table.defaultClass;
}

export function isProtected(rel, extra = []) {
  const p = normalize(rel);
  return [...PROTECTED_BASE, ...extra].some((x) => p === x || p.startsWith(`${x}/`));
}

/** `[<effect>] <class> <target>` — two-space indent like every other updater row. */
export function formatRow(effect, cls, target, tail = '') {
  if (!EFFECTS.includes(effect)) throw new Error(`unknown effect ${effect}`);
  if (!CLASSES.includes(cls)) throw new Error(`unknown class ${cls}`);
  return `  [${effect}] ${cls} ${target}${tail}`;
}

const ROW_RE = new RegExp(`^\\s*\\[(${EFFECTS.join('|')})\\] (${CLASSES.join('|')}) (\\S.*)$`);
export function parseRow(line) {
  const m = ROW_RE.exec(line);
  return m ? { effect: m[1], cls: m[2], target: m[3] } : null;
}
