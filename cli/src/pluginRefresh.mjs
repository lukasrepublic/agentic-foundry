// pluginRefresh.mjs — Phase 1 (marketplace refresh + the AC-UAW-4 one-time migration) and Phase 2
// (plugin update, AC-UAW-5/-6) of `npx update-agentic-workspace`. THE ONLY module in this atom that
// spawns `claude` — every process-spawning call site anywhere in the update path goes through
// `runClaude` below, gated by the ONE frozen allowlist (AC-UAW-14). cli/src/cleanup.mjs imports
// `runClaude` from here rather than declaring a second spawn site or a second copy of the allowlist.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { RefusalError } from './util.mjs';
import { writeTargetAtomically } from './floorReconcile.mjs';

// AC-UAW-14 — the closed six-pair allowlist, declared EXACTLY ONCE across this atom's modules.
// Every `claude` invocation anywhere in the update path is one of these pairs (optionally followed
// by more argv, e.g. a source/name and `--scope <scope>`) — never a bare `claude`, never anything
// that can start a session or reach the trust dialog. `plugin install` is the sixth pair, admitted
// on docs/troubleshooting.md:196-219's grounding: it is what re-establishes a plugin AC-UAW-4's own
// `marketplace remove` may have orphaned.
export const ALLOWED_CLAUDE_SUBCOMMANDS = Object.freeze([
  ['plugin', 'marketplace', 'update'],
  ['plugin', 'marketplace', 'add'],
  ['plugin', 'marketplace', 'remove'],
  ['plugin', 'update'],
  ['plugin', 'install'],
  ['plugin', 'list'],
]);

/** Prefix match against the frozen allowlist. Exact-token comparison at every position closes both
 * failure modes a looser check would admit: a NEAR-MISS in the same namespace (`plugin uninstall`,
 * `plugin marketplace list`) fails because some token differs, and a PREFIX-EXTENSION shape
 * (`plugin updatex`) fails for the same reason — `args[1] === 'update'` is false for `'updatex'`,
 * so a `startsWith`-style bug can never pass here because none is used. */
export function isAllowedInvocation(args) {
  return ALLOWED_CLAUDE_SUBCOMMANDS.some(
    (pair) => pair.length <= args.length && pair.every((tok, i) => args[i] === tok),
  );
}

/** Resolve `claude` on PATH WITHOUT spawning anything (AC-UAW-13's preflight). An executable
 * regular file or symlink named `claude` in one of PATH's directories; the first match wins, same
 * as a shell would resolve it. */
export function resolveClaudeOnPath(pathEnv) {
  for (const dir of String(pathEnv || '').split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, 'claude');
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // not here — keep looking
    }
  }
  return null;
}

/** THE spawn site (AC-UAW-14). Refuses — WITHOUT spawning anything — any argv not drawn from
 * ALLOWED_CLAUDE_SUBCOMMANDS. `env`/`cwd` are always supplied explicitly by the caller so a test
 * can drive an isolated CLAUDE_CONFIG_DIR and an injected stub `claude` on PATH without ever
 * touching the real one. `claudeBin`, when given, is the ABSOLUTE path resolveClaudeOnPath already
 * found — spawned directly rather than re-resolving the bare name `claude` against `env.PATH` a
 * second time, so the AC-UAW-13(a) preflight actually binds the executable the rest of the run
 * uses (R8) instead of merely proving SOME `claude` existed a moment earlier. Falls back to the
 * bare name only when no resolved path is given, e.g. from a unit test exercising this function on
 * its own. */
export function runClaude(args, { env, cwd, claudeBin }) {
  if (!isAllowedInvocation(args)) {
    throw new RefusalError(
      `refusing claude invocation outside the closed allowlist: claude ${args.join(' ')}`,
      'claude',
    );
  }
  return execFileSync(claudeBin || 'claude', args, { env, cwd, encoding: 'utf-8' });
}

// ── Scope model (AC-UAW-5/-13/-15) ──────────────────────────────────────────────────────────────
//
// The only two scopes grounded anywhere in this repository (docs/troubleshooting.md:196-228,
// skills/cut-release/SKILL.md:189-197): `user` (`<config-dir>/settings.json`) and `project`
// (`<cwd>/.claude/settings.json` — this command is run FROM WITHIN an already-scaffolded workspace,
// exactly like the reconcile path in run.mjs). `project` is therefore REQUIRED: its absence means
// this was not run inside a real workspace, which AC-UAW-13 treats as an unresolvable precondition.
// `user` is OPTIONAL — its absence just means nothing is configured at that scope (the common case
// on a fresh machine) — but a PRESENT-and-broken file at EITHER scope is still a refusal: an I/O
// failure must never be read as "not configured here", which is the failure R4/AC-UWC-4(ii) name as
// the single most dangerous mis-implementation on the sibling cleanup atom's side of this same seam.

export function defaultScopes({ cwd, configDir }) {
  return [
    { name: 'project', settingsPath: path.join(cwd, '.claude', 'settings.json'), required: true },
    { name: 'user', settingsPath: path.join(configDir, 'settings.json'), required: false },
  ];
}

/** Read one scope's settings file. Throws RefusalError (AC-UAW-13) for a REQUIRED scope that is
 * absent, or for ANY scope that exists but is unreadable or does not parse as a JSON object.
 * Absence of an OPTIONAL scope is not an error — the caller filters it out. */
export function readScopeSettings(scope) {
  let raw;
  try {
    raw = fs.readFileSync(scope.settingsPath, 'utf-8');
  } catch (e) {
    if (e.code === 'ENOENT') {
      if (scope.required) {
        throw new RefusalError(
          `refusing: ${scope.name} scope settings file is absent (${scope.settingsPath})`,
          scope.name,
        );
      }
      return { ...scope, present: false, obj: null };
    }
    throw new RefusalError(
      `refusing: ${scope.name} scope settings file is unreadable (${scope.settingsPath}): ${e.message}`,
      scope.name,
    );
  }
  let obj;
  try {
    obj = JSON.parse(raw);
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('not an object');
  } catch (e) {
    throw new RefusalError(
      `refusing: ${scope.name} scope settings file does not parse as a JSON object (${scope.settingsPath}): ${e.message}`,
      scope.name,
    );
  }
  return { ...scope, present: true, obj };
}

/** Read every scope BEFORE any mutation — the snapshot AC-UAW-15(b) requires the rest of the run to
 * be derived from. Throws on the first unresolvable scope (AC-UAW-13); returns only the scopes that
 * are actually present. */
export function snapshotScopes(scopes) {
  return scopes.map(readScopeSettings).filter((s) => s.present);
}

function marketplaceEntryOf(settingsObj, marketplaceName) {
  return (settingsObj.extraKnownMarketplaces || {})[marketplaceName];
}

function isEnabled(settingsObj, pluginKey) {
  return Boolean((settingsObj.enabledPlugins || {})[pluginKey]);
}

function entryHasRef(entry) {
  const src = entry && typeof entry === 'object' && !Array.isArray(entry) ? entry.source : undefined;
  return Boolean(src && typeof src === 'object' && Object.prototype.hasOwnProperty.call(src, 'ref'));
}

// ── AC-UAW-4 — the migration to the tagless steady state ───────────────────────────────────────

/** Classify ONE scope's migration trigger. Returns null for a scope already in the tagless steady
 * state (or one carrying no marketplace/plugin state at all). `isInstalled(scopeName)` is the
 * installedness oracle for the THIRD disjunct — a run killed between `marketplace add` and
 * `plugin install` leaves a tagless, enabled entry whose plugin is not actually installed there,
 * which the tagless-entry shape alone would read as healthy. `isInstalled` returns `true`, `false`,
 * or `null` (cannot be determined, e.g. an unreadable installed_plugins.json) — a `null` is NEVER
 * silently folded into either boolean: it is surfaced as its own `indeterminate-installedness`
 * trigger so the caller can report it rather than either skip a genuine orphan or force an
 * unneeded reinstall silently. */
export function classifyMigration(scopeSnap, { marketplaceName, pluginKey, isInstalled }) {
  const entry = marketplaceEntryOf(scopeSnap.obj, marketplaceName);
  const enabled = isEnabled(scopeSnap.obj, pluginKey);
  if (entry !== undefined) {
    if (entryHasRef(entry)) return { kind: 'tag-pinned' };
    if (enabled) {
      const installed = isInstalled(scopeSnap.name);
      if (installed === null) return { kind: 'indeterminate-installedness' };
      if (!installed) return { kind: 'orphaned-install' };
    }
    return null;
  }
  // entry absent — the shape a run SIGKILLed between its own `marketplace remove` and
  // `marketplace add` leaves behind. Only relevant if the scope still believes the plugin enabled;
  // otherwise this scope simply never had the marketplace registered, which is not this atom's job.
  if (enabled) return { kind: 'interrupted-remove' };
  return null;
}

/** The AC-UAW-4 action sequence for one trigger, PER SCOPE with that scope named explicitly on
 * every action. NEVER emits `marketplace remove` when there is no entry to remove (interrupted-
 * remove), and NEVER re-adds an entry that is already tagless (orphaned-install needs only
 * `plugin install`). */
export function migrationActions(trigger, { scope, marketplaceName, marketplaceRepo, pluginKey }) {
  const actions = [];
  // Nothing is actionable on uncertain data — surfaced to the caller as its own trigger kind
  // instead (see classifyMigration), never silently resolved to an action here.
  if (trigger.kind === 'indeterminate-installedness') return actions;
  if (trigger.kind === 'tag-pinned') {
    actions.push(['plugin', 'marketplace', 'remove', marketplaceName, '--scope', scope]);
  }
  if (trigger.kind === 'tag-pinned' || trigger.kind === 'interrupted-remove') {
    // source argument carries NO `#` — the tagless steady state (AC-UAW-4's own text).
    actions.push(['plugin', 'marketplace', 'add', marketplaceRepo, '--scope', scope]);
  }
  actions.push(['plugin', 'install', pluginKey, '--scope', scope]);
  return actions;
}

/** AC-UAW-15(a) — restore what the platform's own `marketplace remove` is OBSERVED to blank.
 * `before` is the scope's settings object captured BEFORE the first migration mutation; `after` is
 * a fresh read taken once every migration action for this scope has run. Every top-level key
 * `before` carried survives with its ORIGINAL value except `extraKnownMarketplaces` — the one key
 * the migration exists to change — and `enabledPlugins` is unconditionally guaranteed to still name
 * the plugin, regardless of what either side carried. */
export function repairScopeSettings(before, after, pluginKey) {
  const repaired = { ...after };
  for (const key of Object.keys(before)) {
    if (key === 'extraKnownMarketplaces') continue;
    repaired[key] = before[key];
  }
  repaired.enabledPlugins = { ...(repaired.enabledPlugins || {}), [pluginKey]: true };
  return repaired;
}

/** Run one scope's migration end to end: the actions, a fresh read-back, the repair, and the
 * atomic write (reusing floorReconcile.mjs's temp-in-directory + rename — the same mechanism the
 * reconcile path already trusts for exactly this hazard). Returns the argv arrays actually
 * invoked, for the caller's preview/summary. */
export function migrateScope({ scopeSnap, trigger, marketplaceName, marketplaceRepo, pluginKey, env, cwd, claudeBin }) {
  const actions = migrationActions(trigger, {
    scope: scopeSnap.name, marketplaceName, marketplaceRepo, pluginKey,
  });
  // R6: if an action throws PARTWAY (offline, a non-zero exit, ^C) after `marketplace remove` has
  // already blanked this scope's settings, the repair below must STILL run before the failure
  // propagates — otherwise the adopter is left both unregistered/disabled AND with the `before`
  // snapshot this run captured now unused and lost. Whatever landed on disk (even a partial
  // blank) is read back and repaired the same way a fully-successful run would; the original
  // error is then rethrown so the caller still reports the run as failed.
  let caught = null;
  for (const args of actions) {
    try {
      runClaude(args, { env, cwd, claudeBin });
    } catch (e) {
      caught = e;
      break;
    }
  }
  if (fs.existsSync(scopeSnap.settingsPath)) {
    const after = readScopeSettings(scopeSnap).obj;
    const repaired = repairScopeSettings(scopeSnap.obj, after, pluginKey);
    writeTargetAtomically(scopeSnap.settingsPath, repaired);
  }
  if (caught) throw caught;
  return actions;
}

// ── AC-UAW-6 — the verdict comes from the manifest read-back, never from stdout ─────────────────

export function readMarketplaceManifest(configDir, marketplaceName) {
  const manifestPath = path.join(
    configDir, 'plugins', 'marketplaces', marketplaceName, '.claude-plugin', 'marketplace.json',
  );
  try {
    const doc = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
    return { present: true, path: manifestPath, doc };
  } catch {
    return { present: false, path: manifestPath, doc: null };
  }
}

export function pluginEntryOf(manifestDoc, pluginName) {
  const plugins = Array.isArray(manifestDoc && manifestDoc.plugins) ? manifestDoc.plugins : [];
  return plugins.find((p) => p && p.name === pluginName) || null;
}

/** Both `version` AND `source.sha` must move for the run to be reported `refreshed` — the v1.4.1
 * scar made mechanical. The invoked CLI's own stdout carries no verdict; it is never consulted.
 * A manifest that APPEARED where none existed before (a fresh CLAUDE_CONFIG_DIR's first resolve)
 * counts as refreshed too — `before === null` must not read as "nothing moved" when in fact
 * everything just did. Only "still nothing after, as before" is genuinely unrefreshed. */
export function manifestRefreshed(before, after) {
  if (!after) return false;
  if (!before) return true;
  const versionMoved = before.version !== after.version;
  const shaMoved = (before.source && before.source.sha) !== (after.source && after.source.sha);
  return versionMoved && shaMoved;
}

// ── AC-UAW-5 — every enabled scope, once, from the pre-migration snapshot ───────────────────────

export function enabledScopeNames(snapshot, pluginKey) {
  return snapshot.filter((s) => isEnabled(s.obj, pluginKey)).map((s) => s.name);
}

export function runPluginUpdate({ scope, pluginKey, env, cwd, claudeBin }) {
  return runClaude(['plugin', 'update', pluginKey, '--scope', scope], { env, cwd, claudeBin });
}

// ── shared with cli/src/cleanup.mjs — the platform's own per-scope install registry ─────────────

/** `<config-dir>/plugins/installed_plugins.json` — schema READ LIVE on the operator's own machine
 * (see the sibling workspace-update-cleanup spec): `{"version": 2, "plugins": {"<key>": [record,
 * ...]}}`, one record per scope, each carrying `installPath`/`version` and — for a project-scope
 * record — a `projectPath`. No published schema contract; an unrecognised `version` (including a
 * future 3) is treated as indeterminate by every caller, never parsed optimistically. */
export function readInstalledPluginsRegistry(configDir) {
  const registryPath = path.join(configDir, 'plugins', 'installed_plugins.json');
  let raw;
  try {
    raw = fs.readFileSync(registryPath, 'utf-8');
  } catch {
    return { ok: false, reason: 'installed plugin registry is absent', doc: null };
  }
  let doc;
  try {
    doc = JSON.parse(raw);
  } catch {
    return { ok: false, reason: 'installed plugin registry does not parse as JSON', doc: null };
  }
  if (!doc || typeof doc !== 'object' || doc.version !== 2) {
    return { ok: false, reason: 'installed plugin registry carries an unrecognised schema version', doc: null };
  }
  return { ok: true, reason: null, doc };
}

export function scopeRecordsFor(doc, pluginKey) {
  const plugins = (doc && doc.plugins) || {};
  return Array.isArray(plugins[pluginKey]) ? plugins[pluginKey] : [];
}
