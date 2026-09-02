// plugin-refresh.test.mjs — feat-foundry-workspace-update-command (AC-UAW-3..6, -13, -14, -15).
//
// TEST ISOLATION, BINDING (see the spec): every test drives an ISOLATED CLAUDE_CONFIG_DIR holding
// fabricated fixtures, and an INJECTED STUB `claude` on PATH that records its argv. Nothing here
// ever touches the developer's real ~/.claude.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ALLOWED_CLAUDE_SUBCOMMANDS, isAllowedInvocation, resolveClaudeOnPath, runClaude,
  defaultScopes, snapshotScopes, classifyMigration, migrationActions, migrateScope,
  repairScopeSettings, readMarketplaceManifest, pluginEntryOf, manifestRefreshed,
  enabledScopeNames,
} from '../src/pluginRefresh.mjs';
import { runUpdate } from '../src/update.mjs';
import { RefusalError } from '../src/util.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const MARKETPLACE = PINS.marketplace_name;
const REPO = PINS.marketplace_repo;
const PLUGIN = PINS.plugin_name;
const PLUGIN_KEY = `${PLUGIN}@${MARKETPLACE}`;

// ── fixture plumbing ──────────────────────────────────────────────────────────────────────────

function scratch(prefix) {
  return fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), prefix));
}

function writeJson(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, `${JSON.stringify(obj, null, 2)}\n`);
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, 'utf-8'));
}

/** A workspace root ('project' scope) and an isolated CLAUDE_CONFIG_DIR ('user' scope + the
 * platform's own state files), both fabricated fresh per test. */
function makeRoots(prefix) {
  const root = scratch(prefix);
  const cwd = path.join(root, 'workspace');
  const configDir = path.join(root, 'config');
  fs.mkdirSync(cwd, { recursive: true });
  fs.mkdirSync(configDir, { recursive: true });
  return { root, cwd, configDir };
}

function writeManifest(configDir, doc) {
  const p = path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json');
  writeJson(p, doc);
  return p;
}

const BASE_MANIFEST = () => ({
  name: MARKETPLACE,
  owner: { name: 'lukasrepublic' },
  plugins: [{
    name: PLUGIN,
    source: { source: 'github', repo: REPO, ref: `v${PINS.plugin_version}`, sha: 'a'.repeat(40) },
    version: PINS.plugin_version,
  }],
});

/** Write an executable Node stub named `claude` on PATH. Every invocation's argv is logged (one
 * JSON array per line) to CLAUDE_STUB_LOG. Every other behaviour is scripted entirely through env
 * vars, so one stub implementation serves every test scenario. */
function installClaudeStub(dir) {
  const stubPath = path.join(dir, 'claude');
  fs.writeFileSync(stubPath, `#!/usr/bin/env node
import fs from 'node:fs';
const argv = process.argv.slice(2);
const logPath = process.env.CLAUDE_STUB_LOG;
if (logPath) fs.appendFileSync(logPath, JSON.stringify(argv) + '\\n');

const failPrefix = process.env.CLAUDE_STUB_FAIL_PREFIX ? JSON.parse(process.env.CLAUDE_STUB_FAIL_PREFIX) : null;
if (failPrefix && failPrefix.every((tok, i) => argv[i] === tok)) {
  process.stderr.write('claude: stub-injected failure\\n');
  process.exit(1);
}

function scopeOf(a) {
  const i = a.indexOf('--scope');
  return i === -1 ? null : a[i + 1];
}
function readJ(p) { return JSON.parse(fs.readFileSync(p, 'utf-8')); }
function writeJ(p, obj) { fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\\n'); }

const scopeSettings = process.env.CLAUDE_STUB_SCOPE_SETTINGS ? JSON.parse(process.env.CLAUDE_STUB_SCOPE_SETTINGS) : {};
const mpName = process.env.CLAUDE_STUB_MARKETPLACE_NAME;

if (argv[0] === 'plugin' && argv[1] === 'marketplace' && argv[2] === 'remove') {
  const p = scopeSettings[scopeOf(argv)];
  if (p && fs.existsSync(p)) {
    const obj = readJ(p);
    if (obj.extraKnownMarketplaces) delete obj.extraKnownMarketplaces[mpName];
    obj.enabledPlugins = {}; // OBSERVED platform side effect (AC-UAW-15) — this atom repairs it
    writeJ(p, obj);
  }
} else if (argv[0] === 'plugin' && argv[1] === 'marketplace' && argv[2] === 'add') {
  const p = scopeSettings[scopeOf(argv)];
  const source = argv[3];
  if (p && fs.existsSync(p)) {
    const obj = readJ(p);
    obj.extraKnownMarketplaces = obj.extraKnownMarketplaces || {};
    obj.extraKnownMarketplaces[mpName] = { source: { source: 'github', repo: source }, autoUpdate: false };
    writeJ(p, obj);
  }
} else if (argv[0] === 'plugin' && argv[1] === 'install') {
  const p = scopeSettings[scopeOf(argv)];
  if (p && fs.existsSync(p)) {
    const obj = readJ(p);
    obj.enabledPlugins = obj.enabledPlugins || {};
    obj.enabledPlugins[argv[2]] = true;
    writeJ(p, obj);
  }
} else if (argv[0] === 'plugin' && argv[1] === 'marketplace' && argv[2] === 'update') {
  const manifestPath = process.env.CLAUDE_STUB_MANIFEST_PATH;
  const newVersion = process.env.CLAUDE_STUB_MANIFEST_NEW_VERSION;
  const newSha = process.env.CLAUDE_STUB_MANIFEST_NEW_SHA;
  const pluginName = process.env.CLAUDE_STUB_PLUGIN_NAME;
  if (manifestPath && newVersion && newSha && fs.existsSync(manifestPath)) {
    const doc = readJ(manifestPath);
    const entry = (doc.plugins || []).find((p) => p.name === pluginName);
    if (entry) { entry.version = newVersion; entry.source.sha = newSha; }
    writeJ(manifestPath, doc);
  }
}

process.stdout.write('claude: ok (stub, plausible success line)\\n');
process.exit(Number(process.env.CLAUDE_STUB_EXIT || '0'));
`);
  fs.chmodSync(stubPath, 0o755);
  return stubPath;
}

function readLog(logPath) {
  if (!fs.existsSync(logPath)) return [];
  return fs.readFileSync(logPath, 'utf-8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

/** A full env for the stub: PATH prepended with the stub dir, CLAUDE_STUB_* wiring, and no
 * mutation of process.env itself. */
function stubEnv({ stubDir, logPath, scopeSettings = {}, manifestPath, manifestNewVersion, manifestNewSha, failPrefix }) {
  const env = {
    ...process.env,
    PATH: `${stubDir}${path.delimiter}${process.env.PATH || ''}`,
    CLAUDE_STUB_LOG: logPath,
    CLAUDE_STUB_MARKETPLACE_NAME: MARKETPLACE,
    CLAUDE_STUB_SCOPE_SETTINGS: JSON.stringify(scopeSettings),
    CLAUDE_STUB_PLUGIN_NAME: PLUGIN,
  };
  if (manifestPath) env.CLAUDE_STUB_MANIFEST_PATH = manifestPath;
  if (manifestNewVersion) env.CLAUDE_STUB_MANIFEST_NEW_VERSION = manifestNewVersion;
  if (manifestNewSha) env.CLAUDE_STUB_MANIFEST_NEW_SHA = manifestNewSha;
  if (failPrefix) env.CLAUDE_STUB_FAIL_PREFIX = JSON.stringify(failPrefix);
  return env;
}

const sink = () => {
  const chunks = [];
  return { write: (s) => chunks.push(s), text: () => chunks.join('') };
};

function scopeSettingsPaths({ cwd, configDir }) {
  return {
    project: path.join(cwd, '.claude', 'settings.json'),
    user: path.join(configDir, 'settings.json'),
  };
}

/** Drive the full orchestrator with a fresh stub dir + log, returning { res, log, paths }. */
async function invokeUpdate({ cwd, configDir, extraEnv = {} }) {
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const paths = scopeSettingsPaths({ cwd, configDir });
  const env = stubEnv({
    stubDir, logPath, scopeSettings: paths,
    manifestPath: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'),
    ...extraEnv,
  });
  const output = sink();
  const res = await runUpdate([], {
    cwd, configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output, spawnEnv: env,
  });
  return { res, log: readLog(logPath), text: output.text(), paths, env };
}

// ================================================================================================
// AC-UAW-3 — catalogue refresh precedes the first plugin update
// ================================================================================================

test('marketplace update precedes the first plugin update', async () => {
  const { root, cwd, configDir } = makeRoots('uaw3-');
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] },
    extraKnownMarketplaces: { [MARKETPLACE]: { source: { source: 'github', repo: REPO }, autoUpdate: false } },
    enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const mpUpdateIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'update');
  const pluginUpdateIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'update');
  assert.notEqual(mpUpdateIdx, -1, 'no marketplace update invocation recorded');
  assert.notEqual(pluginUpdateIdx, -1, 'no plugin update invocation recorded');
  assert.ok(mpUpdateIdx < pluginUpdateIdx, `marketplace update (${mpUpdateIdx}) did not precede plugin update (${pluginUpdateIdx})`);
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-4 — the migration to the tagless steady state
// ================================================================================================

test('every scope carrying a stale registration is migrated with its scope named explicitly', async () => {
  // OBSERVED on the operator's own machine: user AND project both tag-pinned.
  const { root, cwd, configDir } = makeRoots('uaw4-perscope-');
  const tagPinned = { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagPinned, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeJson(path.join(configDir, 'settings.json'), {
    extraKnownMarketplaces: tagPinned, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);

  for (const scope of ['user', 'project']) {
    const removeIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'remove' && a.includes(scope));
    const addIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'add' && a.includes(scope));
    const installIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'install' && a.includes(scope));
    assert.notEqual(removeIdx, -1, `${scope} scope: no marketplace remove --scope ${scope}`);
    assert.notEqual(addIdx, -1, `${scope} scope: no marketplace add --scope ${scope}`);
    assert.notEqual(installIdx, -1, `${scope} scope: no plugin install --scope ${scope}`);
    assert.ok(removeIdx < addIdx && addIdx < installIdx, `${scope} scope: actions out of order`);
    assert.ok(!log[addIdx].join(' ').includes('#'), `${scope} scope: add source carried a #`);
  }
  fs.rmSync(root, { recursive: true, force: true });
});

test('a tagless enabled entry whose plugin is not installed is reinstalled', async () => {
  const { root, cwd, configDir } = makeRoots('uaw4-orphan-');
  const tagless = { [MARKETPLACE]: { source: { source: 'github', repo: REPO }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  // installed_plugins.json carries NO record for this scope's project path -> not installed here.
  writeJson(path.join(configDir, 'plugins', 'installed_plugins.json'), { version: 2, plugins: {} });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const removes = log.filter((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'remove');
  const adds = log.filter((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'add');
  const installs = log.filter((a) => a[0] === 'plugin' && a[1] === 'install' && a.includes('project'));
  assert.equal(removes.length, 0, 'a steady-state tagless entry must never be removed');
  assert.equal(adds.length, 0, 'a steady-state tagless entry must never be re-added');
  assert.equal(installs.length, 1, 'the orphaned plugin was not reinstalled');
  fs.rmSync(root, { recursive: true, force: true });
});

test('a tag pinned registration is removed re added tagless and reinstalled before the refresh', async () => {
  const { root, cwd, configDir } = makeRoots('uaw4-legacy-');
  const tagPinned = { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagPinned, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const removeIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'remove' && a.includes('project'));
  const addIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'add' && a.includes('project'));
  const installIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'install' && a.includes('project'));
  const refreshIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'update');
  assert.ok(removeIdx !== -1 && addIdx !== -1 && installIdx !== -1 && refreshIdx !== -1, `missing a step: ${JSON.stringify(log)}`);
  assert.ok(removeIdx < addIdx, 'remove did not precede add');
  assert.ok(addIdx < installIdx, 'add did not precede install');
  assert.ok(installIdx < refreshIdx, 'install did not precede the catalogue refresh');
  assert.ok(!log[addIdx].includes('#') && !log[addIdx].some((tok) => tok.includes('#')), 'add source carried a #');
  fs.rmSync(root, { recursive: true, force: true });
});

test('an absent marketplace entry with the plugin still enabled is re added and reinstalled', async () => {
  // Shape a run SIGKILLed between its own `marketplace remove` and `marketplace add` leaves: no
  // extraKnownMarketplaces entry at all, but enabledPlugins still names the plugin.
  const { root, cwd, configDir } = makeRoots('uaw4-interrupted-');
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const removes = log.filter((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'remove');
  assert.equal(removes.length, 0, 'nothing was there to remove; a remove must not be issued');
  const addIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'add' && a.includes('project'));
  const installIdx = log.findIndex((a) => a[0] === 'plugin' && a[1] === 'install' && a.includes('project'));
  assert.notEqual(addIdx, -1, 'no marketplace add for the recovering scope');
  assert.notEqual(installIdx, -1, 'no plugin install for the recovering scope');
  assert.ok(addIdx < installIdx, 'add did not precede install');
  assert.ok(!log[addIdx].some((tok) => tok.includes('#')), 'add source carried a #');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-15 — the migration must not destroy state the later phases depend on
// ================================================================================================

test('a migrated scope keeps enabledPlugins and every other settings key', () => {
  // Driven at the Phase-1 unit level (migrateScope), not through the full orchestrator: Phase 4's
  // OWN, later, ADDITIVE floor-reconcile legitimately appends further allow/ask/deny rules to this
  // same file once the marketplace is pinned — a real and separate effect this row is not about.
  // AC-UAW-15(a) is specifically that Phase 1's OWN migration does not itself destroy state.
  const { root, cwd, configDir } = makeRoots('uaw15-preserve-');
  const tagPinned = { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false } };
  const before = {
    permissions: { allow: ['Bash(mine:*)'], ask: [], deny: [] },
    extraKnownMarketplaces: tagPinned,
    enabledPlugins: { [PLUGIN_KEY]: true },
    someKeyTheCliHasNeverHeardOf: { nested: [1, 2, 3] },
  };
  const settingsPath = path.join(cwd, '.claude', 'settings.json');
  writeJson(settingsPath, before);

  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const env = stubEnv({ stubDir, logPath, scopeSettings: { project: settingsPath } });

  const scopeSnap = { name: 'project', settingsPath, obj: before, required: true, present: true };
  const trigger = classifyMigration(scopeSnap, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: () => true });
  assert.equal(trigger.kind, 'tag-pinned');
  migrateScope({ scopeSnap, trigger, marketplaceName: MARKETPLACE, marketplaceRepo: REPO, pluginKey: PLUGIN_KEY, env, cwd });

  const after = readJson(settingsPath);
  assert.equal(after.enabledPlugins[PLUGIN_KEY], true, 'the plugin is no longer enabled after migration');
  assert.deepEqual(after.someKeyTheCliHasNeverHeardOf, before.someKeyTheCliHasNeverHeardOf, 'a sentinel key did not survive');
  assert.deepEqual(after.permissions, before.permissions, 'unrelated permissions were altered');
  assert.notDeepEqual(after.extraKnownMarketplaces[MARKETPLACE], tagPinned[MARKETPLACE], 'the tag-pinned entry was not migrated');
  fs.rmSync(root, { recursive: true, force: true });
});

test('a migration action that fails partway still repairs and writes before rethrowing', () => {
  // R6: `marketplace remove` succeeds (blanking enabledPlugins, per the observed platform side
  // effect) and THEN `marketplace add` fails (offline, 5xx, ^C). Without a repair-on-failure path,
  // the adopter is left unregistered AND disabled, with the pre-migration snapshot captured in
  // memory never used. The repair must still land on disk, and the original error must still
  // propagate so the run is honestly reported as failed.
  const { root, cwd, configDir } = makeRoots('r6-rollback-');
  const tagPinned = { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false } };
  const before = {
    permissions: { allow: ['Bash(mine:*)'], ask: [], deny: [] },
    extraKnownMarketplaces: tagPinned,
    enabledPlugins: { [PLUGIN_KEY]: true },
    sentinel: 'kept',
  };
  const settingsPath = path.join(cwd, '.claude', 'settings.json');
  writeJson(settingsPath, before);

  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const env = stubEnv({
    stubDir, logPath, scopeSettings: { project: settingsPath },
    failPrefix: ['plugin', 'marketplace', 'add'],
  });

  const scopeSnap = { name: 'project', settingsPath, obj: before, required: true, present: true };
  const trigger = classifyMigration(scopeSnap, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: () => true });
  assert.equal(trigger.kind, 'tag-pinned');

  assert.throws(
    () => migrateScope({ scopeSnap, trigger, marketplaceName: MARKETPLACE, marketplaceRepo: REPO, pluginKey: PLUGIN_KEY, env, cwd }),
    'the failed action did not propagate',
  );

  const after = readJson(settingsPath);
  assert.equal(after.enabledPlugins[PLUGIN_KEY], true, 'the plugin was left disabled after the failed migration');
  assert.equal(after.sentinel, 'kept', 'an unrelated key was lost on the failure path');
  fs.rmSync(root, { recursive: true, force: true });
});

test('the scope set is snapshotted before the first migration mutation', async () => {
  // THE COMPOSED CASE: Phase 1 blanks enabledPlugins (via the stub, simulating the observed
  // platform behaviour) before Phase 1's own repair step runs; if Phase 2 derived its scope set by
  // a LIVE re-read taken after Phase 1, it would see the (transiently) blanked file and skip the
  // very scope that was just migrated.
  const { root, cwd, configDir } = makeRoots('uaw15-snapshot-');
  const tagPinned = { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagPinned, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const pluginUpdate = log.find((a) => a[0] === 'plugin' && a[1] === 'update' && a.includes('project'));
  assert.ok(pluginUpdate, 'the migrated scope was not updated — the scope set was read live, post-blank');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-5 — every enabled scope, once, from the pre-migration snapshot
// ================================================================================================

test('plugin update runs once per scope that enables the plugin', async () => {
  const { root, cwd, configDir } = makeRoots('uaw5-every-');
  const tagless = { [MARKETPLACE]: { source: { source: 'github', repo: REPO }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeJson(path.join(configDir, 'settings.json'), { extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true } });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  for (const scope of ['user', 'project']) {
    const updates = log.filter((a) => a[0] === 'plugin' && a[1] === 'update' && a.includes(scope));
    assert.equal(updates.length, 1, `expected exactly one plugin update for ${scope}, got ${updates.length}`);
  }
  fs.rmSync(root, { recursive: true, force: true });
});

test('no plugin update is issued for a scope that does not enable the plugin', () => {
  const snapshot = [
    { name: 'user', obj: { enabledPlugins: { [PLUGIN_KEY]: true } } },
    { name: 'project', obj: { enabledPlugins: { [PLUGIN_KEY]: true } } },
    { name: 'third-project', obj: { enabledPlugins: {} } },
  ];
  const names = enabledScopeNames(snapshot, PLUGIN_KEY);
  assert.deepEqual(names.sort(), ['project', 'user']);
  assert.ok(!names.includes('third-project'), 'a scope that does not enable the plugin was included');
});

// ================================================================================================
// AC-UAW-6 — the verdict comes from the manifest read-back, never from stdout
// ================================================================================================

test('a success line over an unchanged manifest is reported as not refreshed', () => {
  const before = { version: '1.8.0', source: { sha: 'a'.repeat(40) } };
  const after = { version: '1.8.0', source: { sha: 'a'.repeat(40) } };
  assert.equal(manifestRefreshed(before, after), false);
});

test('a manifest whose version and sha both move is reported as refreshed', () => {
  const before = { version: '1.8.0', source: { sha: 'a'.repeat(40) } };
  const after = { version: '1.9.0', source: { sha: 'b'.repeat(40) } };
  assert.equal(manifestRefreshed(before, after), true);
});

test('a first resolve on a fresh config dir where no manifest existed before is reported as refreshed', () => {
  // Every other fixture in this file pre-seeds BASE_MANIFEST(), which made this branch invisible:
  // `before === null` must not read as "nothing moved" when in fact a manifest appeared where
  // there was none.
  const after = { version: '1.8.0', source: { sha: 'a'.repeat(40) } };
  assert.equal(manifestRefreshed(null, after), true);
  assert.equal(manifestRefreshed(null, null), false, 'still nothing after a refresh must stay unrefreshed');
});

// ================================================================================================
// AC-UAW-13 — fail-closed refusal
// ================================================================================================

test('an absent claude executable refuses before any mutation', async () => {
  const { root, cwd, configDir } = makeRoots('uaw13-noclaude-');
  writeJson(path.join(cwd, '.claude', 'settings.json'), { permissions: { allow: [], ask: [], deny: [] } });
  writeManifest(configDir, BASE_MANIFEST());
  const before = readJson(path.join(cwd, '.claude', 'settings.json'));
  const emptyPathDir = scratch('empty-path-');
  const output = sink();
  const res = await runUpdate([], {
    cwd, configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output,
    spawnEnv: { ...process.env, PATH: emptyPathDir },
  });
  assert.equal(res.exitCode, 1);
  assert.match(output.text(), /^refused:/m);
  assert.deepEqual(readJson(path.join(cwd, '.claude', 'settings.json')), before, 'the settings file was mutated');
  fs.rmSync(root, { recursive: true, force: true });
});

test('an unresolvable scope settings file refuses before any mutation', async () => {
  const { root, cwd, configDir } = makeRoots('uaw13-badscope-');
  writeJson(path.join(cwd, '.claude', 'settings.json'), { permissions: { allow: [], ask: [], deny: [] } });
  // `user` scope EXISTS but is not parseable JSON — "cannot be resolved", distinct from absent.
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(path.join(configDir, 'settings.json'), '{ this is not json');
  writeManifest(configDir, BASE_MANIFEST());
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const before = readJson(path.join(cwd, '.claude', 'settings.json'));
  const output = sink();
  const res = await runUpdate([], {
    cwd, configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output,
    spawnEnv: stubEnv({ stubDir, logPath, scopeSettings: scopeSettingsPaths({ cwd, configDir }) }),
  });
  assert.equal(res.exitCode, 1);
  assert.match(output.text(), /^refused:/m);
  assert.deepEqual(readLog(logPath), [], 'a claude invocation was made despite the refusal');
  assert.deepEqual(readJson(path.join(cwd, '.claude', 'settings.json')), before, 'the project settings file was mutated');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-14 — the closed `claude` subcommand allowlist
// ================================================================================================

test('every claude invocation is drawn from the closed subcommand allowlist', async () => {
  const { root, cwd, configDir } = makeRoots('uaw14-closed-');
  const tagPinned = { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagPinned, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  assert.ok(log.length > 0, 'no claude invocations were recorded — the fixture proves nothing');
  const used = new Set();
  for (const args of log) {
    assert.ok(isAllowedInvocation(args), `out-of-allowlist invocation recorded: claude ${args.join(' ')}`);
    const pair = ALLOWED_CLAUDE_SUBCOMMANDS.find((p) => p.every((tok, i) => args[i] === tok));
    used.add(pair.join(' '));
  }
  for (const pair of used) {
    assert.ok(ALLOWED_CLAUDE_SUBCOMMANDS.some((p) => p.join(' ') === pair), `${pair} is not a member of the frozen allowlist`);
  }
  fs.rmSync(root, { recursive: true, force: true });
});

test('an invocation outside the closed allowlist is refused and never spawned', () => {
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const env = stubEnv({ stubDir, logPath, scopeSettings: {} });
  const cwd = scratch('cwd-');

  const outOfSet = [
    [], // bare `claude`
    ['--print'],
    ['plugin', 'uninstall', PLUGIN_KEY],
    ['plugin', 'marketplace', 'list'],
    ['plugin', 'updatex', PLUGIN_KEY], // prefix-extension shape
  ];
  for (const args of outOfSet) {
    assert.throws(() => runClaude(args, { env, cwd }), RefusalError, `claude ${args.join(' ')} was not refused`);
  }
  assert.deepEqual(readLog(logPath), [], 'a refused invocation still spawned the stub');
});

// ── unit coverage over the primitives (not separately checkpointed, but exercised for the
// migration/repair machinery the composed tests above depend on) ─────────────────────────────────

test('classifyMigration and migrationActions agree on every trigger shape', () => {
  const isInstalledTrue = () => true;
  const isInstalledFalse = () => false;

  const tagPinned = { obj: { extraKnownMarketplaces: { [MARKETPLACE]: { source: { source: 'github', repo: REPO, ref: 'v1.5.0' } } }, enabledPlugins: { [PLUGIN_KEY]: true } }, name: 'project' };
  const t1 = classifyMigration(tagPinned, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: isInstalledTrue });
  assert.equal(t1.kind, 'tag-pinned');
  assert.equal(migrationActions(t1, { scope: 'project', marketplaceName: MARKETPLACE, marketplaceRepo: REPO, pluginKey: PLUGIN_KEY }).length, 3);

  const steady = { obj: { extraKnownMarketplaces: { [MARKETPLACE]: { source: { source: 'github', repo: REPO } } }, enabledPlugins: { [PLUGIN_KEY]: true } }, name: 'project' };
  assert.equal(classifyMigration(steady, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: isInstalledTrue }), null);

  const orphan = classifyMigration(steady, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: isInstalledFalse });
  assert.equal(orphan.kind, 'orphaned-install');
  assert.equal(migrationActions(orphan, { scope: 'project', marketplaceName: MARKETPLACE, marketplaceRepo: REPO, pluginKey: PLUGIN_KEY }).length, 1);

  const absent = { obj: { enabledPlugins: { [PLUGIN_KEY]: true } }, name: 'project' };
  const t3 = classifyMigration(absent, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: isInstalledTrue });
  assert.equal(t3.kind, 'interrupted-remove');
  assert.equal(migrationActions(t3, { scope: 'project', marketplaceName: MARKETPLACE, marketplaceRepo: REPO, pluginKey: PLUGIN_KEY }).length, 2);

  // an unreadable installed_plugins.json must surface as its OWN kind — never silently folded
  // into "installed" (which would suppress a genuine orphan) or "not installed" (which would
  // force an unneeded reinstall on every run) — and must produce NO actions.
  const isInstalledUnknown = () => null;
  const unknown = classifyMigration(steady, { marketplaceName: MARKETPLACE, pluginKey: PLUGIN_KEY, isInstalled: isInstalledUnknown });
  assert.equal(unknown.kind, 'indeterminate-installedness');
  assert.deepEqual(migrationActions(unknown, { scope: 'project', marketplaceName: MARKETPLACE, marketplaceRepo: REPO, pluginKey: PLUGIN_KEY }), []);
});

test('repairScopeSettings restores every other key and forces enabledPlugins', () => {
  const before = { permissions: { allow: ['x'] }, extraKnownMarketplaces: { old: true }, sentinel: 42 };
  const after = { permissions: { allow: ['x'] }, extraKnownMarketplaces: { fresh: true }, enabledPlugins: {} };
  const repaired = repairScopeSettings(before, after, PLUGIN_KEY);
  assert.deepEqual(repaired.extraKnownMarketplaces, { fresh: true }, 'the fresh migration result must survive');
  assert.equal(repaired.sentinel, 42);
  assert.deepEqual(repaired.permissions, { allow: ['x'] });
  assert.equal(repaired.enabledPlugins[PLUGIN_KEY], true);
});

test('resolveClaudeOnPath finds an executable and null when absent', () => {
  const dir = scratch('resolve-claude-');
  assert.equal(resolveClaudeOnPath(dir), null);
  installClaudeStub(dir);
  assert.equal(resolveClaudeOnPath(dir), path.join(dir, 'claude'));
});

test('snapshotScopes filters absent optional scopes and reads present ones', () => {
  const { root, cwd, configDir } = makeRoots('snapshot-');
  writeJson(path.join(cwd, '.claude', 'settings.json'), { enabledPlugins: {} });
  const scopes = defaultScopes({ cwd, configDir });
  const snap = snapshotScopes(scopes);
  assert.equal(snap.length, 1, 'the absent optional user scope should be filtered out');
  assert.equal(snap[0].name, 'project');
  fs.rmSync(root, { recursive: true, force: true });
});

test('readMarketplaceManifest and pluginEntryOf read the plugin entry', () => {
  const { root, configDir } = makeRoots('manifest-');
  writeManifest(configDir, BASE_MANIFEST());
  const { present, doc } = readMarketplaceManifest(configDir, MARKETPLACE);
  assert.ok(present);
  const entry = pluginEntryOf(doc, PLUGIN);
  assert.equal(entry.version, PINS.plugin_version);
  fs.rmSync(root, { recursive: true, force: true });
});

test('an unreadable installed plugin registry is reported rather than silently resolved either way', async () => {
  const { root, cwd, configDir } = makeRoots('indeterminate-installed-');
  const tagless = { [MARKETPLACE]: { source: { source: 'github', repo: REPO }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  // deliberately NO installed_plugins.json written -> readInstalledPluginsRegistry reports !ok
  writeManifest(configDir, BASE_MANIFEST());
  const { res, log, text } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const installs = log.filter((a) => a[0] === 'plugin' && a[1] === 'install');
  assert.equal(installs.length, 0, 'an unneeded reinstall was forced on indeterminate installedness');
  assert.match(text, /installedness could not be determined/, 'the limitation was not surfaced in the summary');
  fs.rmSync(root, { recursive: true, force: true });
});
