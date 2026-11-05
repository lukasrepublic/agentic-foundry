// governance-upgrade.test.mjs — gv-15: the updater runs `governance upgrade` after the plugin refresh and
// names /foundry:post-upgrade; the lock and the adopter layer are in the never-overwrite set.
//
// TEST ISOLATION: the governance script is a STUB python file that records its argv; the `claude` binary is a
// stub on PATH; nothing here touches a real ~/.claude, a real git remote or a real `gh`.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runUpdate } from '../src/update.mjs';
import { loadPathClasses, classOf } from '../src/pathClasses.mjs';
import {
  runGovernanceUpgrade, resolveInstallPath, renderGovernanceUpgradeRow, GOVERNANCE_LOCK_REL,
} from '../src/governanceUpgrade.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const MARKETPLACE = PINS.marketplace_name;
const PLUGIN = PINS.plugin_name;
const PLUGIN_KEY = `${PLUGIN}@${MARKETPLACE}`;

const scratch = (p) => fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), p));
const writeJson = (p, o) => { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, `${JSON.stringify(o, null, 2)}\n`); };

/** A plugin root whose governance script is a stub: records argv, prints `stdout`, exits `code`. */
function stubPlugin({ code = 0, stdout = 'governance-upgrade: already current' } = {}) {
  const root = scratch('gov-plugin-');
  const log = path.join(root, 'argv.json');
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.writeFileSync(path.join(root, 'scripts', 'foundry_governance.py'),
    `import json, sys\nopen(${JSON.stringify(log)}, 'w').write(json.dumps(sys.argv))\nprint(${JSON.stringify(stdout)})\nsys.exit(${code})\n`);
  return { root, log };
}

function workspaceWithLock() {
  const cwd = scratch('gov-ws-');
  fs.mkdirSync(path.join(cwd, '.foundry'), { recursive: true });
  fs.writeFileSync(path.join(cwd, GOVERNANCE_LOCK_REL), 'blueprint: {}\n');
  return cwd;
}

// ── never-overwrite set ─────────────────────────────────────────────────────────────────────────

test('the lock and the adopter layer are in the never-overwrite set', () => {
  const table = loadPathClasses(CLI_DIR);
  for (const rel of ['.foundry/governance.lock', 'governance/local/rules.yaml', 'governance/local/baselines/x.json', 'governance/local/adr/0001.md']) {
    assert.equal(classOf(table, rel), 'retained-history', rel);
  }
  // control: the generated render is not adopter-owned
  assert.notEqual(classOf(table, 'governance/rendered/security.md'), 'retained-history');
});

// ── the step itself ─────────────────────────────────────────────────────────────────────────────

test('no governance lock: the step is skipped and the script is never spawned', () => {
  const cwd = scratch('gov-ws-');
  const { root, log } = stubPlugin();
  const r = runGovernanceUpgrade({ cwd, pluginRoot: root });
  assert.equal(r.verdict, 'skipped');
  assert.match(r.reason, /no governance lock/);
  assert.ok(!fs.existsSync(log));
});

test('with a lock: runs `upgrade --pr` against the workspace and names /foundry:post-upgrade', () => {
  const cwd = workspaceWithLock();
  const { root, log } = stubPlugin({ stdout: 'governance-upgrade: 2 files changed' });
  const r = runGovernanceUpgrade({ cwd, pluginRoot: root });
  assert.equal(r.verdict, 'changed');
  const argv = JSON.parse(fs.readFileSync(log, 'utf-8'));
  assert.deepEqual(argv.slice(1), ['--project-dir', cwd, 'upgrade', '--pr']);
  assert.match(renderGovernanceUpgradeRow(r), /\/foundry:post-upgrade/);
});

test('an already-current lock is reported as such and does not ask for a review', () => {
  const cwd = workspaceWithLock();
  const { root } = stubPlugin();
  const r = runGovernanceUpgrade({ cwd, pluginRoot: root });
  assert.equal(r.verdict, 'already current');
  assert.doesNotMatch(renderGovernanceUpgradeRow(r), /post-upgrade/);
});

test('exit 1 (conflict markers left) is attention, exit 2 (refused) is failed; both name the review skill', () => {
  for (const [code, verdict] of [[1, 'attention'], [2, 'failed']]) {
    const cwd = workspaceWithLock();
    const { root } = stubPlugin({ code, stdout: 'governance-upgrade: stopped' });
    const r = runGovernanceUpgrade({ cwd, pluginRoot: root });
    assert.equal(r.verdict, verdict);
    assert.match(renderGovernanceUpgradeRow(r), /\/foundry:post-upgrade/);
  }
});

test('a dry run plans the step and spawns nothing', () => {
  const cwd = workspaceWithLock();
  const { root, log } = stubPlugin();
  const r = runGovernanceUpgrade({ cwd, pluginRoot: root, dryRun: true });
  assert.equal(r.verdict, 'would run');
  assert.ok(!fs.existsSync(log));
});

test('a python that cannot run is failed, never a silent pass', () => {
  const cwd = workspaceWithLock();
  const { root } = stubPlugin();
  const r = runGovernanceUpgrade({ cwd, pluginRoot: root, python: '/nonexistent/python3' });
  assert.equal(r.verdict, 'failed');
});

test('the plugin root comes from the installed-plugins registry: project record first, then user', () => {
  const doc = { plugins: { [PLUGIN_KEY]: [
    { scope: 'user', installPath: '/u/path' },
    { scope: 'project', projectPath: '/ws', installPath: '/p/path' },
  ] } };
  assert.equal(resolveInstallPath({ ok: true, doc }, PLUGIN_KEY, '/ws'), '/p/path');
  assert.equal(resolveInstallPath({ ok: true, doc }, PLUGIN_KEY, '/other'), '/u/path');
  assert.equal(resolveInstallPath({ ok: false }, PLUGIN_KEY, '/ws'), null);
});

// ── wired into the update run ───────────────────────────────────────────────────────────────────

function installClaudeStub(dir) {
  const p = path.join(dir, 'claude');
  fs.writeFileSync(p, '#!/usr/bin/env node\nprocess.stdout.write("claude: ok (stub)\\n");\n');
  fs.chmodSync(p, 0o755);
}

function updateFixture({ withLock, scriptStdout, scriptCode = 0 }) {
  const root = scratch('gov-upd-');
  const cwd = path.join(root, 'workspace');
  const configDir = path.join(root, 'config');
  fs.mkdirSync(cwd, { recursive: true });
  fs.mkdirSync(configDir, { recursive: true });
  const tagless = { [MARKETPLACE]: { source: { source: 'github', repo: PINS.marketplace_repo }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeJson(path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'), {
    name: MARKETPLACE, owner: { name: 'x' },
    plugins: [{ name: PLUGIN, source: { source: 'github', repo: PINS.marketplace_repo, ref: `v${PINS.plugin_version}`, sha: 'a'.repeat(40) }, version: PINS.plugin_version }],
  });
  const installPath = path.join(configDir, 'plugins', 'cache', MARKETPLACE, PLUGIN, PINS.plugin_version);
  writeJson(path.join(installPath, '.claude-plugin', 'plugin.json'), { name: PLUGIN, version: PINS.plugin_version });
  writeJson(path.join(configDir, 'plugins', 'installed_plugins.json'), {
    version: 2, plugins: { [PLUGIN_KEY]: [{ scope: 'project', projectPath: cwd, version: PINS.plugin_version, installPath }] },
  });
  const log = path.join(installPath, 'argv.json');
  fs.mkdirSync(path.join(installPath, 'scripts'), { recursive: true });
  fs.writeFileSync(path.join(installPath, 'scripts', 'foundry_governance.py'),
    `import json, sys\nopen(${JSON.stringify(log)}, 'w').write(json.dumps(sys.argv))\nprint(${JSON.stringify(scriptStdout || 'governance-upgrade: already current')})\nsys.exit(${scriptCode})\n`);
  if (withLock) { fs.mkdirSync(path.join(cwd, '.foundry'), { recursive: true }); fs.writeFileSync(path.join(cwd, GOVERNANCE_LOCK_REL), 'blueprint: {}\n'); }
  return { cwd, configDir, log };
}

async function update(fx, argv = []) {
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const chunks = [];
  const env = {
    ...process.env, PATH: `${stubDir}${path.delimiter}${process.env.PATH || ''}`,
    FOUNDRY_PYTHON: '/nonexistent/python3', FOUNDRY_GOVERNANCE_PYTHON: 'python3',
  };
  const res = await runUpdate(argv, { cwd: fx.cwd, configDir: fx.configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output: { write: (s) => chunks.push(s) }, spawnEnv: env });
  return { res, text: chunks.join('') };
}

test('update runs `governance upgrade` after the plugin refresh and names /foundry:post-upgrade', async () => {
  const fx = updateFixture({ withLock: true, scriptStdout: 'governance-upgrade: 3 files changed' });
  const { text } = await update(fx);
  const argv = JSON.parse(fs.readFileSync(fx.log, 'utf-8'));
  assert.deepEqual(argv.slice(1), ['--project-dir', fs.realpathSync(fx.cwd), 'upgrade', '--pr']);
  const summary = text.slice(text.indexOf('Summary:'));
  const iPlugin = summary.indexOf('[plugin-update]');
  const iGov = summary.indexOf('[governance-upgrade]');
  assert.ok(iPlugin >= 0 && iGov > iPlugin, 'governance upgrade runs after the plugin update');
  assert.match(text, /\/foundry:post-upgrade/);
});

test('update without a governance lock does not run the step and prints no governance row', async () => {
  const fx = updateFixture({ withLock: false });
  const { text } = await update(fx);
  assert.ok(!fs.existsSync(fx.log));
  assert.doesNotMatch(text, /governance-upgrade/);
});

test('a dry-run update spawns nothing but previews the step when a lock exists', async () => {
  const fx = updateFixture({ withLock: true });
  const { text } = await update(fx, ['--dry-run']);
  assert.ok(!fs.existsSync(fx.log));
  assert.match(text, /governance-upgrade/);
});
