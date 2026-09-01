// cleanup.test.mjs — feat-foundry-workspace-update-cleanup (AC-UWC-1..9).
//
// TEST ISOLATION, BINDING: every test drives an ISOLATED CLAUDE_CONFIG_DIR holding FABRICATED
// plugins/cache/..., installed_plugins.json and known_marketplaces.json, and an INJECTED STUB
// `claude` on PATH. No test resolves the cache root from the OS home-directory lookup or the
// HOME environment variable — asserted by AC-UWC-3's third checkpoint (a static grep over this
// very file, which is why this note is worded to avoid the two literals it greps for).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  deriveLiveSet, planCachePrune, applyCachePrune, readKnownMarketplaces, resolveEnabledQualifiers,
  planRegistrationRemoval, runCleanupPhase,
} from '../src/cleanup.mjs';
import { RefusalError } from '../src/util.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const CLI_UPDATE_DIR = path.join(CLI_DIR, '..', 'cli-update');
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const MARKETPLACE = PINS.marketplace_name;
const REPO = PINS.marketplace_repo;
const PLUGIN = PINS.plugin_name;
const PLUGIN_KEY = `${PLUGIN}@${MARKETPLACE}`;

// ── fixture plumbing (isolated CLAUDE_CONFIG_DIR fixtures only; every path below is built from a
// freshly-made temp directory, never from the OS home-directory lookup or the HOME env var) ─────

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

function makeConfigDir(prefix) {
  const root = scratch(prefix);
  const configDir = path.join(root, 'config');
  fs.mkdirSync(configDir, { recursive: true });
  return { root, configDir };
}

function pluginCacheDirOf(configDir) {
  return path.join(configDir, 'plugins', 'cache', MARKETPLACE, PLUGIN);
}

function makeCacheVersions(configDir, versions, { sentinel = true } = {}) {
  const dir = pluginCacheDirOf(configDir);
  for (const v of versions) {
    const vDir = path.join(dir, v);
    fs.mkdirSync(vDir, { recursive: true });
    if (sentinel) fs.writeFileSync(path.join(vDir, 'marker.txt'), `sentinel for ${v}`);
  }
  return dir;
}

function writeInstalledPlugins(configDir, records) {
  writeJson(path.join(configDir, 'plugins', 'installed_plugins.json'), { version: 2, plugins: { [PLUGIN_KEY]: records } });
}

function writeManifest(configDir, version) {
  writeJson(path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'), {
    name: MARKETPLACE,
    plugins: [{ name: PLUGIN, source: { source: 'github', repo: REPO, sha: 'a'.repeat(40) }, version }],
  });
}

function writeKnownMarketplaces(configDir, doc) {
  writeJson(path.join(configDir, 'plugins', 'known_marketplaces.json'), doc);
}

function installClaudeStub(dir) {
  const stubPath = path.join(dir, 'claude');
  fs.writeFileSync(stubPath, `#!/usr/bin/env node
import fs from 'node:fs';
const argv = process.argv.slice(2);
const logPath = process.env.CLAUDE_STUB_LOG;
if (logPath) fs.appendFileSync(logPath, JSON.stringify(argv) + '\\n');
process.stdout.write('claude: ok (stub)\\n');
process.exit(0);
`);
  fs.chmodSync(stubPath, 0o755);
  return stubPath;
}

function readLog(logPath) {
  if (!fs.existsSync(logPath)) return [];
  return fs.readFileSync(logPath, 'utf-8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

/** The env every `claude` invocation this phase makes is spawned with — CLAUDE_CONFIG_DIR pinned
 * to the isolated fixture, exactly as cli/src/pluginRefresh.mjs's runClaude does it, so no
 * invocation this phase makes can ever reach the developer's real ~/.claude. */
function claudeEnv(stubDir, logPath, configDir) {
  return {
    ...process.env,
    PATH: `${stubDir}${path.delimiter}${process.env.PATH || ''}`,
    CLAUDE_STUB_LOG: logPath,
    CLAUDE_CONFIG_DIR: configDir,
  };
}

function makePrint() {
  const lines = [];
  return { print: (s) => lines.push(s), lines };
}

// ================================================================================================
// AC-UWC-1 — the live set is READ from the platform's state files, never sorted
// ================================================================================================

test('the live set is read from every scope record in the installed plugin registry', () => {
  // The exact observed shape: user->1.6.0, project(piiq-handbook)->1.6.0, project(agentic-workspace)->1.5.0
  const registry = { ok: true, doc: { version: 2, plugins: { [PLUGIN_KEY]: [
    { installPath: '/x/1.6.0', version: '1.6.0' },
    { installPath: '/y/1.6.0', version: '1.6.0', projectPath: '/y' },
    { installPath: '/z/1.5.0', version: '1.5.0', projectPath: '/z' },
  ] } } };
  const result = deriveLiveSet({ registry, manifestVersion: '1.6.0', pluginKey: PLUGIN_KEY });
  assert.ok(result.ok);
  assert.ok(result.versions.has('1.5.0'), 'the older, still-live scope record was dropped');
  assert.ok(result.versions.has('1.6.0'));
});

test('the newest on disk version is pruned when the registry names an older one as live', () => {
  const { root, configDir } = makeConfigDir('uwc1-notsorted-');
  const dir = makeCacheVersions(configDir, ['1.5.0', '1.6.0']);
  const liveVersions = new Set(['1.5.0']); // both registry AND manifest name 1.5.0; 1.6.0 is abandoned
  const candidates = planCachePrune({ pluginCacheDir: dir, liveVersions });
  assert.deepEqual(candidates, ['1.6.0']);
  assert.ok(fs.existsSync(path.join(dir, '1.5.0')), '1.5.0 must not have been touched by planning alone');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UWC-2 — the candidate set is confined to one pinned root
// ================================================================================================

test('no entry outside the pinned marketplace and plugin cache root is ever a candidate', () => {
  const { root, configDir } = makeConfigDir('uwc2-confined-');
  const dir = makeCacheVersions(configDir, ['1.6.0']);
  // a stray file sitting BESIDE the plugin directory (marketplace level), a second plugin under
  // the SAME marketplace, and an entirely SEPARATE marketplace's cache — none inside pluginCacheDir.
  fs.writeFileSync(path.join(configDir, 'plugins', 'cache', MARKETPLACE, 'stray.txt'), 'not a plugin');
  fs.mkdirSync(path.join(configDir, 'plugins', 'cache', MARKETPLACE, 'other-plugin', '2.0.0'), { recursive: true });
  fs.mkdirSync(path.join(configDir, 'plugins', 'cache', 'claude-plugins-official', PLUGIN, '1.0.0'), { recursive: true });

  const candidates = planCachePrune({ pluginCacheDir: dir, liveVersions: new Set(['1.6.0']) });
  assert.deepEqual(candidates, [], 'an unrelated entry was treated as a candidate');
  assert.ok(fs.existsSync(path.join(configDir, 'plugins', 'cache', MARKETPLACE, 'stray.txt')));
  assert.ok(fs.existsSync(path.join(configDir, 'plugins', 'cache', MARKETPLACE, 'other-plugin', '2.0.0')));
  assert.ok(fs.existsSync(path.join(configDir, 'plugins', 'cache', 'claude-plugins-official', PLUGIN, '1.0.0')));
  fs.rmSync(root, { recursive: true, force: true });
});

test('a marketplace or plugin pin that is not a safe path component refuses to derive a cache root', () => {
  // R1: marketplaceName/pluginName come from this package's own bundled pins, never adopter
  // input, but `path.join` normalizes `..` regardless of provenance — a corrupted pin naming
  // `../..` would otherwise walk the derived root outside plugins/cache/ entirely, past every
  // symlink/realpath guard planCachePrune has (those guard the LEAVES of the ALREADY-DERIVED
  // root, not this join). A genuinely superseded directory sits under the REAL, safe root and
  // must survive untouched — this is not the same directory the bad pin would have reached.
  const { root, configDir } = makeConfigDir('r1-traversal-');
  const safeDir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  const { print } = makePrint();

  for (const badName of ['../..', '..', 'a/../../b', '/etc']) {
    const result = runCleanupPhase({
      cleanupFlag: true, configDir, marketplaceName: badName, marketplaceRepo: REPO,
      pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: process.env, cwd: root, print,
    });
    assert.equal(result.verdict, 'skipped', `a path-traversal-shaped marketplace name (${badName}) was not refused`);
    assert.deepEqual(result.prunedVersions, []);
  }
  // the REAL cache root (a sibling of whatever a traversal pin would have reached) is untouched
  assert.ok(fs.existsSync(path.join(safeDir, '1.4.1')));
  assert.ok(fs.existsSync(path.join(safeDir, '1.6.0')));
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UWC-3 — the live version survives
// ================================================================================================

test('every live version directory survives an opted in prune', () => {
  const { root, configDir } = makeConfigDir('uwc3-survive-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.5.0', '1.6.0']);
  writeInstalledPlugins(configDir, [
    { installPath: '/x', version: '1.6.0' },
    { installPath: '/y', version: '1.6.0', projectPath: '/y' },
    { installPath: '/z', version: '1.5.0', projectPath: '/z' },
  ]);
  writeManifest(configDir, '1.6.0');
  const beforeMarker15 = fs.readFileSync(path.join(dir, '1.5.0', 'marker.txt'));
  const beforeMarker16 = fs.readFileSync(path.join(dir, '1.6.0', 'marker.txt'));

  const { print } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: process.env, cwd: root, print,
  });
  assert.deepEqual(result.prunedVersions, ['1.4.1']);
  assert.ok(fs.existsSync(path.join(dir, '1.5.0')));
  assert.ok(fs.existsSync(path.join(dir, '1.6.0')));
  assert.ok(!fs.existsSync(path.join(dir, '1.4.1')));
  assert.deepEqual(fs.readFileSync(path.join(dir, '1.5.0', 'marker.txt')), beforeMarker15);
  assert.deepEqual(fs.readFileSync(path.join(dir, '1.6.0', 'marker.txt')), beforeMarker16);
  fs.rmSync(root, { recursive: true, force: true });
});

test('a cache in which every on disk version is live removes nothing', () => {
  const { root, configDir } = makeConfigDir('uwc3-alllive-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.5.0', '1.6.0']);
  writeInstalledPlugins(configDir, [
    { installPath: '/a', version: '1.4.1' },
    { installPath: '/b', version: '1.5.0', projectPath: '/b' },
    { installPath: '/c', version: '1.6.0', projectPath: '/c' },
  ]);
  writeManifest(configDir, '1.6.0');
  const { print } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: process.env, cwd: root, print,
  });
  assert.deepEqual(result.prunedVersions, []);
  for (const v of ['1.4.1', '1.5.0', '1.6.0']) assert.ok(fs.existsSync(path.join(dir, v)), `${v} missing`);
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UWC-4 — indeterminate live set or an unreadable scope: SKIP, say why, delete NOTHING
// ================================================================================================

test('an unreadable or unrecognised registry skips the prune with a reason and removes nothing', () => {
  const cases = [];

  // (a) registry absent
  {
    const { root, configDir } = makeConfigDir('uwc4-absent-');
    const dir = makeCacheVersions(configDir, ['1.5.0']);
    cases.push({ root, configDir, dir });
  }
  // (b) registry invalid JSON
  {
    const { root, configDir } = makeConfigDir('uwc4-invalid-');
    const dir = makeCacheVersions(configDir, ['1.5.0']);
    fs.mkdirSync(path.dirname(path.join(configDir, 'plugins', 'installed_plugins.json')), { recursive: true });
    fs.writeFileSync(path.join(configDir, 'plugins', 'installed_plugins.json'), '{ not json');
    cases.push({ root, configDir, dir });
  }
  // (c) unrecognised schema version
  {
    const { root, configDir } = makeConfigDir('uwc4-schema-');
    const dir = makeCacheVersions(configDir, ['1.5.0']);
    writeJson(path.join(configDir, 'plugins', 'installed_plugins.json'), { version: 3, plugins: {} });
    cases.push({ root, configDir, dir });
  }
  // (d) registry parses fine but holds no record for this plugin
  {
    const { root, configDir } = makeConfigDir('uwc4-norecord-');
    const dir = makeCacheVersions(configDir, ['1.5.0']);
    writeInstalledPlugins(configDir, []);
    cases.push({ root, configDir, dir });
  }
  // (e) B1: records PRESENT but none carries a usable version string, and no manifest to fall
  // back on — an EMPTY live set must never read as "everything is safe to prune" (it would
  // otherwise make every on-disk version, including the live one, a candidate).
  {
    const { root, configDir } = makeConfigDir('uwc4-noversion-');
    const dir = makeCacheVersions(configDir, ['1.5.0']);
    writeInstalledPlugins(configDir, [
      { installPath: '/a' }, // version key entirely absent
      { installPath: '/b', version: null },
      { installPath: '/c', version: 1.5 }, // numeric, not a string
    ]);
    cases.push({ root, configDir, dir, skipManifest: true });
  }

  for (const { root, configDir, dir, skipManifest } of cases) {
    if (!skipManifest) writeManifest(configDir, '1.5.0');
    const before = fs.readdirSync(dir).sort();
    const { print } = makePrint();
    const result = runCleanupPhase({
      cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
      pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: process.env, cwd: root, print,
    });
    assert.equal(result.verdict, 'skipped', `expected skipped for ${configDir}`);
    assert.ok(result.reason && result.reason.length > 0, `no reason given for ${configDir}`);
    assert.ok(fs.existsSync(path.join(dir, '1.5.0')), `cache path removed despite indeterminate input (${configDir})`);
    assert.deepEqual(fs.readdirSync(dir).sort(), before, `the cache directory's own contents changed (${configDir})`);
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// ================================================================================================
// AC-UWC-5 — nothing is removed that the preview did not name
// ================================================================================================

test('every removed path was named in the preview emitted before the first removal', () => {
  const { root, configDir } = makeConfigDir('uwc5-preview-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  const events = [];
  const print = (s) => events.push({ type: 'preview', s });
  const result = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: process.env, cwd: root, print,
  });
  assert.deepEqual(result.prunedVersions, ['1.4.1']);
  const previewText = events.map((e) => e.s).join('\n');
  assert.ok(previewText.includes(path.join(dir, '1.4.1')), 'the removed path was never named in the preview');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UWC-6 — deletion is opt-in; the default run is report-only
// ================================================================================================

test('without the cleanup flag the phase reports candidates and removes nothing', () => {
  const { root, configDir } = makeConfigDir('uwc6-reportonly-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  const { print, lines } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: false, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: process.env, cwd: root, print,
  });
  assert.deepEqual(result.prunedVersions, [], 'a flagless run pruned something');
  assert.ok(fs.existsSync(path.join(dir, '1.4.1')), 'the superseded directory was removed without --cleanup');
  assert.ok(lines.join('\n').includes(path.join(dir, '1.4.1')), 'the candidate was never reported');
  fs.rmSync(root, { recursive: true, force: true });
});

test('a flagless run issues zero claude invocations', () => {
  // Non-vacuous: the fixture ALSO carries a stale registration that WOULD be removed (and would
  // therefore spawn `claude plugin marketplace remove`) if cleanupFlag were not correctly gating
  // it — proving this is a real gate, not merely "nothing happened to need one".
  const { root, configDir } = makeConfigDir('uwc6-noclaude-');
  makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  writeKnownMarketplaces(configDir, {
    [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE), lastUpdated: '2026-09-01T00:00:00Z' },
    [`${MARKETPLACE}-old`]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'nonexistent'), lastUpdated: '2026-01-01T00:00:00Z' },
  });
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const { print } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: false, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [],
    env: claudeEnv(stubDir, logPath, configDir), cwd: root, print,
  });
  assert.deepEqual(result.removedRegistrations, [], 'a flagless run removed a registration');
  assert.deepEqual(readLog(logPath), [], 'a claude invocation was recorded on a flagless run');
  fs.rmSync(root, { recursive: true, force: true });
});

test('the update entry point accepts and forwards the cleanup flag', async () => {
  // Proves the REAL npm-packaging shape: cli-update/bin resolves 'create-agentic-workspace' as a
  // package dependency, never a relative import. A transient node_modules symlink (removed in the
  // `finally` below) is the standard way to prove that resolution without an actual `npm install`.
  //
  // Driven WITHOUT --help (C2): the `--help` branch in update.mjs returns before runCleanupPhase
  // is ever reached, so a run that only exercised `--cleanup --help` proves merely that the flag
  // is not rejected as unknown — never that it actually reaches the cleanup phase. A mutant that
  // hardcoded `cleanupFlag: false` at that call site would still pass such a test. Here the SAME
  // fixture — carrying a stale registration that only an OPTED-IN run may remove — is run twice,
  // once with `--cleanup` and once without, and the two runs must differ exactly the way the flag
  // predicts: the invocation happens only when the flag is forwarded as true.
  const nodeModulesDir = path.join(CLI_UPDATE_DIR, 'node_modules');
  const linkPath = path.join(nodeModulesDir, 'create-agentic-workspace');
  const nodeModulesDirAlreadyExisted = fs.existsSync(nodeModulesDir);
  fs.mkdirSync(nodeModulesDir, { recursive: true });
  const alreadyExisted = fs.existsSync(linkPath);
  if (!alreadyExisted) fs.symlinkSync(CLI_DIR, linkPath, 'dir');
  try {
    const { spawnSync } = await import('node:child_process');

    function fixture(prefix) {
      const { root, configDir } = makeConfigDir(prefix);
      const cwd = path.join(root, 'workspace');
      fs.mkdirSync(cwd, { recursive: true });
      const tagless = { [MARKETPLACE]: { source: { source: 'github', repo: REPO }, autoUpdate: false } };
      writeJson(path.join(cwd, '.claude', 'settings.json'), {
        permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true },
      });
      writeManifest(configDir, PINS.plugin_version);
      makeCacheVersions(configDir, [PINS.plugin_version]);
      writeInstalledPlugins(configDir, [{ installPath: '/a', version: PINS.plugin_version }]);
      writeKnownMarketplaces(configDir, {
        [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE), lastUpdated: '2026-09-01T00:00:00Z' },
        [`${MARKETPLACE}-old`]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'nonexistent'), lastUpdated: '2026-01-01T00:00:00Z' },
      });
      return { root, cwd, configDir };
    }

    function runBin(extraArgs, { cwd, configDir }) {
      const stubDir = scratch('bin-claude-stub-');
      installClaudeStub(stubDir);
      const logPath = path.join(stubDir, 'log.jsonl');
      // spawnSync, not execFileSync: the run's own exit code is meaningless to this test (it may
      // legitimately be 0 or the drift code 2) and execFileSync THROWS on any non-zero exit,
      // which would abort the assertion before it ever inspects the recorded argv.
      const result = spawnSync(
        process.execPath,
        [path.join(CLI_UPDATE_DIR, 'bin', 'update-agentic-workspace.mjs'), ...extraArgs],
        {
          cwd,
          encoding: 'utf-8',
          env: {
            ...process.env,
            CLAUDE_CONFIG_DIR: configDir,
            PATH: `${stubDir}${path.delimiter}${process.env.PATH || ''}`,
            CLAUDE_STUB_LOG: logPath,
          },
        },
      );
      return { out: `${result.stdout || ''}${result.stderr || ''}`, log: readLog(logPath) };
    }

    const without = fixture('bin-cleanup-flag-without-');
    const withoutResult = runBin([], without);
    const removedWithout = withoutResult.log.some((a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'remove');
    assert.equal(removedWithout, false, `a flagless bin run removed a registration: ${JSON.stringify(withoutResult.log)}`);

    const withF = fixture('bin-cleanup-flag-with-');
    const withResult = runBin(['--cleanup'], withF);
    const removedWith = withResult.log.some(
      (a) => a[0] === 'plugin' && a[1] === 'marketplace' && a[2] === 'remove' && a.includes(`${MARKETPLACE}-old`),
    );
    assert.ok(removedWith, `--cleanup did not reach the phase: ${withResult.out}\nlog: ${JSON.stringify(withResult.log)}`);
  } finally {
    if (!alreadyExisted) fs.rmSync(linkPath, { force: true });
    if (!nodeModulesDirAlreadyExisted) fs.rmSync(nodeModulesDir, { recursive: true, force: true });
  }
});

// docs/troubleshooting.md and its pytest regression guard are verified separately (static grep +
// `python3 -m pytest tests/test_docs_claims.py`), per the contract's own surface split.

// ================================================================================================
// AC-UWC-7 — the stale / duplicate marketplace registration
// ================================================================================================

test('a stale registration that no scope enables is removed by name', () => {
  const { root, configDir } = makeConfigDir('uwc7-stale-');
  makeCacheVersions(configDir, ['1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  writeKnownMarketplaces(configDir, {
    [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE), lastUpdated: '2026-09-01T00:00:00Z' },
    [`${MARKETPLACE}-old`]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'nonexistent'), lastUpdated: '2026-01-01T00:00:00Z' },
  });
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const { print } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env: claudeEnv(stubDir, logPath, configDir), cwd: root, print,
  });
  assert.deepEqual(result.removedRegistrations, [`${MARKETPLACE}-old`]);
  const log = readLog(logPath);
  assert.equal(log.length, 1);
  assert.deepEqual(log[0], ['plugin', 'marketplace', 'remove', `${MARKETPLACE}-old`]);
  fs.rmSync(root, { recursive: true, force: true });
});

test('a registration enabled by any settings scope is never removed', () => {
  const { root, configDir } = makeConfigDir('uwc7-enabled-');
  makeCacheVersions(configDir, ['1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  const dupName = `${MARKETPLACE}-old`;
  writeKnownMarketplaces(configDir, {
    [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE), lastUpdated: '2026-09-01T00:00:00Z' },
    [dupName]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'nonexistent'), lastUpdated: '2026-01-01T00:00:00Z' },
  });
  const scopeSettingsPath = path.join(root, 'project-settings.json');
  writeJson(scopeSettingsPath, { enabledPlugins: { [`${PLUGIN}@${dupName}`]: true } });
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const { print } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY,
    scopeDescriptors: [{ name: 'project', settingsPath: scopeSettingsPath }],
    env: claudeEnv(stubDir, logPath, configDir), cwd: root, print,
  });
  assert.deepEqual(result.removedRegistrations, [], 'a still-enabled duplicate registration was removed');
  assert.deepEqual(readLog(logPath), [], 'a claude invocation was made against an enabled registration');
  fs.rmSync(root, { recursive: true, force: true });
});

test('an unreadable settings scope routes to the skip branch not to unenabled', () => {
  const { root, configDir } = makeConfigDir('uwc7-unreadable-scope-');
  const dir = makeCacheVersions(configDir, ['1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  writeKnownMarketplaces(configDir, {
    [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE), lastUpdated: '2026-09-01T00:00:00Z' },
    [`${MARKETPLACE}-old`]: { source: { source: 'github', repo: REPO }, installLocation: path.join(configDir, 'nonexistent'), lastUpdated: '2026-01-01T00:00:00Z' },
  });
  const badScopePath = path.join(root, 'broken-settings.json');
  fs.writeFileSync(badScopePath, '{ not json');
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const { print } = makePrint();
  const result = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY,
    scopeDescriptors: [{ name: 'broken', settingsPath: badScopePath }],
    env: claudeEnv(stubDir, logPath, configDir), cwd: root, print,
  });
  assert.equal(result.verdict, 'skipped', 'an unreadable scope must route to the skip branch');
  assert.ok(result.reason && result.reason.length > 0);
  assert.deepEqual(readLog(logPath), [], 'a claude invocation was made despite the indeterminate scope');
  assert.ok(fs.existsSync(path.join(dir, '1.6.0')), 'the live cache directory was affected');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UWC-8 — idempotence: the second run finds nothing to do
// ================================================================================================

test('a second opted in run reports already current and removes nothing', () => {
  const { root, configDir } = makeConfigDir('uwc8-idempotent-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  writeInstalledPlugins(configDir, [{ installPath: '/a', version: '1.6.0' }]);
  writeManifest(configDir, '1.6.0');
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const env = claudeEnv(stubDir, logPath, configDir);

  const first = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env, cwd: root, print: () => {},
  });
  assert.deepEqual(first.prunedVersions, ['1.4.1']);
  const statBefore = fs.statSync(path.join(dir, '1.6.0'));

  const second = runCleanupPhase({
    cleanupFlag: true, configDir, marketplaceName: MARKETPLACE, marketplaceRepo: REPO,
    pluginName: PLUGIN, pluginKey: PLUGIN_KEY, scopeDescriptors: [], env, cwd: root, print: () => {},
  });
  assert.equal(second.verdict, 'already current');
  assert.deepEqual(second.prunedVersions, []);
  assert.deepEqual(readLog(logPath), [], 'no live registration was ever removable, so no claude call should exist');
  const statAfter = fs.statSync(path.join(dir, '1.6.0'));
  assert.equal(statAfter.ino, statBefore.ino);
  assert.equal(statAfter.mtimeMs, statBefore.mtimeMs);
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UWC-9 — fail-closed: an unexpected entry refuses and removes NOTHING AT ALL
// ================================================================================================

test('a symlinked or escaping cache entry refuses before removing anything', () => {
  const { root, configDir } = makeConfigDir('uwc9-rollup-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']); // 1.4.1 superseded, 1.6.0 live
  const outside = scratch('uwc9-outside-');
  fs.symlinkSync(outside, path.join(dir, 'bad-link'));
  assert.throws(
    () => planCachePrune({ pluginCacheDir: dir, liveVersions: new Set(['1.6.0']) }),
    RefusalError,
  );
  assert.ok(fs.existsSync(path.join(dir, '1.4.1')), 'a superseded directory was removed despite the refusal');
  assert.ok(fs.existsSync(path.join(dir, '1.6.0')));
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(outside, { recursive: true, force: true });
});

test('a symlinked cache entry refuses before removing anything', () => {
  const { root, configDir } = makeConfigDir('uwc9-symlink-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  const outside = scratch('uwc9-symlink-target-');
  fs.symlinkSync(outside, path.join(dir, 'linked-version'));
  assert.throws(
    () => planCachePrune({ pluginCacheDir: dir, liveVersions: new Set(['1.6.0']) }),
    (e) => e instanceof RefusalError && /symbolic link/.test(e.message),
  );
  assert.ok(fs.existsSync(path.join(dir, '1.4.1')), 'the superseded directory was removed despite the refusal');
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(outside, { recursive: true, force: true });
});

test('a non directory cache entry refuses before removing anything', () => {
  const { root, configDir } = makeConfigDir('uwc9-nondir-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  fs.writeFileSync(path.join(dir, 'not-a-version-dir.txt'), 'oops');
  assert.throws(
    () => planCachePrune({ pluginCacheDir: dir, liveVersions: new Set(['1.6.0']) }),
    (e) => e instanceof RefusalError && /not a directory/.test(e.message),
  );
  assert.ok(fs.existsSync(path.join(dir, '1.4.1')), 'the superseded directory was removed despite the refusal');
  fs.rmSync(root, { recursive: true, force: true });
});

test('a cache entry whose real path escapes the root refuses before removing anything', () => {
  const { root, configDir } = makeConfigDir('uwc9-escape-');
  // The REAL versions live elsewhere; `plugins/cache/<marketplace>` itself is a symlinked
  // ancestor, so the leaf entries are PLAIN directories (lstat sees no symlink) whose realpath
  // nonetheless resolves outside their own nominal join.
  const realElsewhere = scratch('uwc9-real-elsewhere-');
  const realPluginDir = path.join(realElsewhere, MARKETPLACE, PLUGIN);
  fs.mkdirSync(path.join(realPluginDir, '1.4.1'), { recursive: true });
  fs.mkdirSync(path.join(realPluginDir, '1.6.0'), { recursive: true });
  fs.mkdirSync(path.join(configDir, 'plugins'), { recursive: true });
  fs.symlinkSync(realElsewhere, path.join(configDir, 'plugins', 'cache'));
  const nominalDir = pluginCacheDirOf(configDir); // NOT realpath'd — the nominal join used by the phase
  assert.throws(
    () => planCachePrune({ pluginCacheDir: nominalDir, liveVersions: new Set(['1.6.0']) }),
    (e) => e instanceof RefusalError && /escapes the pinned root/.test(e.message),
  );
  assert.ok(fs.existsSync(path.join(realPluginDir, '1.4.1')), 'the superseded directory was removed despite the refusal');
  assert.ok(fs.existsSync(path.join(realPluginDir, '1.6.0')));
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(realElsewhere, { recursive: true, force: true });
});

// ── unit coverage over the primitives the composed tests above depend on ────────────────────────

test('readKnownMarketplaces and planRegistrationRemoval agree on stale vs duplicate', () => {
  const { root, configDir } = makeConfigDir('primitives-known-mp-');
  const liveInstallLocation = path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE);
  fs.mkdirSync(liveInstallLocation, { recursive: true });
  writeKnownMarketplaces(configDir, {
    [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: liveInstallLocation },
    'dup-name': { source: { source: 'github', repo: REPO }, installLocation: liveInstallLocation },
    'other-repo-entirely': { source: { source: 'github', repo: 'someone/else' }, installLocation: '/nowhere' },
  });
  const { ok, doc } = readKnownMarketplaces(configDir);
  assert.ok(ok);
  const removable = planRegistrationRemoval({
    knownMarketplacesDoc: doc, marketplaceRepo: REPO, canonicalName: MARKETPLACE, enabledQualifiers: new Set(),
  });
  assert.deepEqual(removable, ['dup-name']);
  fs.rmSync(root, { recursive: true, force: true });
});

test('a registration name outside the safe argv charset is never made removable', () => {
  // R7: `name` is a JSON OBJECT KEY read from the adopter's own known_marketplaces.json — a key
  // beginning with `-` would reach the platform CLI's own option parser as a flag once it becomes
  // an argv element (`claude plugin marketplace remove <name>`). Excluded here, BEFORE anything
  // downstream could ever pass it to runClaude.
  const { root, configDir } = makeConfigDir('r7-unsafe-name-');
  const liveInstallLocation = path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE);
  fs.mkdirSync(liveInstallLocation, { recursive: true });
  writeKnownMarketplaces(configDir, {
    [MARKETPLACE]: { source: { source: 'github', repo: REPO }, installLocation: liveInstallLocation },
    '--scope': { source: { source: 'github', repo: REPO }, installLocation: '/nowhere-real' },
    '-f': { source: { source: 'github', repo: REPO }, installLocation: '/also-nowhere-real' },
  });
  const { ok, doc } = readKnownMarketplaces(configDir);
  assert.ok(ok);
  const removable = planRegistrationRemoval({
    knownMarketplacesDoc: doc, marketplaceRepo: REPO, canonicalName: MARKETPLACE, enabledQualifiers: new Set(),
  });
  assert.deepEqual(removable, [], 'a flag-shaped registration name was proposed for removal');
  fs.rmSync(root, { recursive: true, force: true });
});

test('resolveEnabledQualifiers is fine with an absent scope and fails on a broken one', () => {
  const { root } = makeConfigDir('primitives-qualifiers-');
  const missing = path.join(root, 'absent.json');
  const ok = resolveEnabledQualifiers([{ name: 'x', settingsPath: missing }]);
  assert.deepEqual([...ok.qualifiers], []);
  assert.equal(ok.ok, true);

  const broken = path.join(root, 'broken.json');
  fs.writeFileSync(broken, 'not json');
  const bad = resolveEnabledQualifiers([{ name: 'y', settingsPath: broken }]);
  assert.equal(bad.ok, false);
  fs.rmSync(root, { recursive: true, force: true });
});

test('applyCachePrune removes only the given candidates', () => {
  const { root, configDir } = makeConfigDir('primitives-apply-');
  const dir = makeCacheVersions(configDir, ['1.4.1', '1.6.0']);
  applyCachePrune(dir, ['1.4.1']);
  assert.ok(!fs.existsSync(path.join(dir, '1.4.1')));
  assert.ok(fs.existsSync(path.join(dir, '1.6.0')));
  fs.rmSync(root, { recursive: true, force: true });
});

test('every claude invocation this phase makes is already in the sibling closed allowlist', async () => {
  const { isAllowedInvocation } = await import('../src/pluginRefresh.mjs');
  assert.ok(isAllowedInvocation(['plugin', 'marketplace', 'remove', 'x']));
});
