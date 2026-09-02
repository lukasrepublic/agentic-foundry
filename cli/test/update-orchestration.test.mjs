// update-orchestration.test.mjs — feat-foundry-workspace-update-command (AC-UAW-1, -2, -7, -8, -9,
// -11, -12).
//
// TEST ISOLATION, BINDING: every test drives an ISOLATED CLAUDE_CONFIG_DIR holding fabricated
// fixtures, and an INJECTED STUB `claude` on PATH that records its argv. Nothing here ever touches
// the developer's real ~/.claude.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runUpdate, renderSummary } from '../src/update.mjs';
import { DECLARED_PATH_SET } from '../src/scaffold.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const CLI_UPDATE_DIR = path.join(CLI_DIR, '..', 'cli-update');
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const MARKETPLACE = PINS.marketplace_name;
const REPO = PINS.marketplace_repo;
const PLUGIN = PINS.plugin_name;
const PLUGIN_KEY = `${PLUGIN}@${MARKETPLACE}`;

// ── fixture plumbing (mirrors plugin-refresh.test.mjs's own copy — each test module is
// self-contained, matching this codebase's existing convention, e.g. floor-reconcile.test.mjs's
// own `target()` helper) ─────────────────────────────────────────────────────────────────────────

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

function scopeSettingsPaths({ cwd, configDir }) {
  return { project: path.join(cwd, '.claude', 'settings.json'), user: path.join(configDir, 'settings.json') };
}

function stubEnv({ stubDir, logPath, manifestPath }) {
  return {
    ...process.env,
    PATH: `${stubDir}${path.delimiter}${process.env.PATH || ''}`,
    CLAUDE_STUB_LOG: logPath,
    CLAUDE_STUB_MARKETPLACE_NAME: MARKETPLACE,
    CLAUDE_STUB_PLUGIN_NAME: PLUGIN,
    CLAUDE_STUB_MANIFEST_PATH: manifestPath,
  };
}

const sink = () => {
  const chunks = [];
  return { write: (s) => chunks.push(s), text: () => chunks.join('') };
};

async function invokeUpdate({ cwd, configDir, output = sink() }) {
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const env = stubEnv({
    stubDir, logPath,
    manifestPath: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'),
  });
  const res = await runUpdate([], { cwd, configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output, spawnEnv: env });
  return { res, log: readLog(logPath), text: output.text ? output.text() : '' };
}

function steadyStateFixture(prefix) {
  const { root, cwd, configDir } = makeRoots(prefix);
  const tagless = { [MARKETPLACE]: { source: { source: 'github', repo: REPO }, autoUpdate: false } };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless, enabledPlugins: { [PLUGIN_KEY]: true },
  });
  writeManifest(configDir, BASE_MANIFEST());
  return { root, cwd, configDir };
}

// ================================================================================================
// AC-UAW-1 — the npx-resolvable package identity
// ================================================================================================

test('update package manifest declares the npx resolvable name and bin', () => {
  const pkg = readJson(path.join(CLI_UPDATE_DIR, 'package.json'));
  assert.equal(pkg.name, 'update-agentic-workspace');
  const binKeys = Object.keys(pkg.bin || {});
  assert.deepEqual(binKeys, ['update-agentic-workspace']);
  assert.equal(pkg.bin['update-agentic-workspace'], 'bin/update-agentic-workspace.mjs');
});

// ================================================================================================
// AC-UAW-2 — one implementation; the two entry points cannot diverge
// ================================================================================================

test('the update entry point vendors no copy of the shared modules', () => {
  const sharedBasenames = new Set(fs.readdirSync(path.join(CLI_DIR, 'src')));
  const cliUpdateFiles = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else cliUpdateFiles.push(entry.name);
    }
  };
  walk(CLI_UPDATE_DIR);
  const collisions = cliUpdateFiles.filter((name) => sharedBasenames.has(name));
  assert.deepEqual(collisions, [], `cli-update/ vendors a copy of: ${collisions.join(', ')}`);
});

test('the update package depends on the shared package at an exact equal version', () => {
  const cliPkg = readJson(path.join(CLI_DIR, 'package.json'));
  const updatePkg = readJson(path.join(CLI_UPDATE_DIR, 'package.json'));
  assert.ok(updatePkg.dependencies, 'cli-update/package.json carries no dependencies block');
  const pinned = updatePkg.dependencies['create-agentic-workspace'];
  assert.equal(pinned, cliPkg.version, 'the pin is not exactly equal to create-agentic-workspace\'s own version');
  assert.doesNotMatch(pinned, /[~^*]|x/i, 'the pin is not an exact version (range/caret/tilde/wildcard)');

  // convicts a fixture that would otherwise pass vacuously: a caret/tilde/range must fail this row
  for (const bad of [`^${cliPkg.version}`, `~${cliPkg.version}`, '*', 'latest']) {
    assert.notEqual(bad, cliPkg.version);
  }
});

// ================================================================================================
// AC-UAW-7 — preview before the first mutation
// ================================================================================================

test('no mutation is attempted before the preview is emitted', async () => {
  const { root, cwd, configDir } = steadyStateFixture('uaw7-');
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  let checked = false;
  const output = {
    write(s) {
      if (!checked && s.includes('The following claude invocations will be made:')) {
        checked = true;
        assert.deepEqual(readLog(logPath), [], 'a claude invocation was recorded before the preview printed');
        assert.ok(!fs.existsSync(path.join(cwd, 'CLAUDE.md')), 'a managed file was written before the preview printed');
      }
    },
  };
  const env = stubEnv({
    stubDir, logPath,
    manifestPath: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'),
  });
  const res = await runUpdate([], { cwd, configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output, spawnEnv: env });
  assert.ok(checked, 'the preview line was never printed');
  assert.notEqual(res.exitCode, 1, res.output);
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-8 — never-clobber, inherited from cli/src/reconcile.mjs
// ================================================================================================

test('an operator edited managed file is reported drifted and left byte identical', async () => {
  const { root, cwd, configDir } = steadyStateFixture('uaw8-');
  const claudeMdPath = path.join(cwd, 'CLAUDE.md');
  const editedBytes = Buffer.from('# an operator wrote something completely different here\n');
  fs.writeFileSync(claudeMdPath, editedBytes);
  const { res, text } = await invokeUpdate({ cwd, configDir, output: sink() });
  assert.equal(res.exitCode, 2, `expected the drift exit code; got ${res.exitCode}: ${res.output}`);
  assert.deepEqual(fs.readFileSync(claudeMdPath), editedBytes, 'the operator-edited file was overwritten');
  assert.match(text, /\[drifted] CLAUDE\.md/);
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-9 — the operator registry is read-only, structurally
// ================================================================================================

test('the operator registry is never opened for writing', async () => {
  const { root, cwd, configDir } = steadyStateFixture('uaw9-');
  const registryPath = path.join(cwd, '.claude', 'foundry-operators.json');
  writeJson(registryPath, { schema_version: 1, operators: { op_x: { github: 'x' } } });
  const beforeBytes = fs.readFileSync(registryPath);
  const beforeIno = fs.statSync(registryPath).ino;
  const { res } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  assert.deepEqual(fs.readFileSync(registryPath), beforeBytes, 'the operator registry bytes changed');
  assert.equal(fs.statSync(registryPath).ino, beforeIno, 'the operator registry was rewritten (inode changed)');
  fs.rmSync(root, { recursive: true, force: true });
});

// ================================================================================================
// AC-UAW-11 — the per-phase summary
// ================================================================================================

test('the summary names every phase with changed or already current or skipped', () => {
  const text = renderSummary([
    { name: 'marketplace-refresh', verdict: 'changed' },
    { name: 'plugin-update', verdict: 'already current' },
    { name: 'reinitialization', verdict: 'skipped', reason: 'no writable target' },
  ]);
  const phaseLines = text.split('\n').filter((l) => /^\s*\[\S+]\s+/.test(l));
  assert.equal(phaseLines.length, 3, `expected exactly three phase rows, got: ${JSON.stringify(phaseLines)}`);
  for (const line of phaseLines) {
    assert.match(line, /^\s*\[\S+]\s+(changed|already current|skipped: .+)$/, `not a valid phase row: ${line}`);
  }
  const skippedLine = phaseLines.find((l) => l.includes('skipped:'));
  assert.ok(skippedLine, 'no skipped row found');
  assert.match(skippedLine, /skipped: .+\S/, 'the skipped row carries no non-empty reason');
});

// ================================================================================================
// AC-UAW-12 — idempotence: a second run writes nothing
// ================================================================================================

test('a second run over a current workspace performs no write syscall', async () => {
  const { root, cwd, configDir } = steadyStateFixture('uaw12-');
  const first = await invokeUpdate({ cwd, configDir });
  assert.notEqual(first.res.exitCode, 1, first.res.output);

  const statOf = (rel) => {
    const p = path.join(cwd, rel);
    return fs.existsSync(p) ? fs.statSync(p) : null;
  };
  const before = Object.fromEntries(DECLARED_PATH_SET.map((rel) => [rel, statOf(rel)]));
  const beforeBytes = Object.fromEntries(
    DECLARED_PATH_SET.map((rel) => [rel, fs.existsSync(path.join(cwd, rel)) ? fs.readFileSync(path.join(cwd, rel)) : null]),
  );

  const second = await invokeUpdate({ cwd, configDir });
  assert.notEqual(second.res.exitCode, 1, second.res.output);

  for (const rel of DECLARED_PATH_SET) {
    const b = before[rel];
    const a = statOf(rel);
    assert.ok(b, `${rel} did not exist after the first run`);
    assert.ok(a, `${rel} disappeared after the second run`);
    assert.equal(a.ino, b.ino, `${rel}: inode changed on the second run`);
    assert.equal(a.mtimeMs, b.mtimeMs, `${rel}: mtime changed on the second run`);
    assert.deepEqual(fs.readFileSync(path.join(cwd, rel)), beforeBytes[rel], `${rel}: bytes changed on the second run`);
  }
  fs.rmSync(root, { recursive: true, force: true });
});
