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
import { BEGIN_TOKEN, END_TOKEN, loadDesiredInterior } from '../src/gitignoreReconcile.mjs';

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

async function invokeUpdate({ cwd, configDir, output = sink(), argv = [] }) {
  const stubDir = scratch('claude-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const env = stubEnv({
    stubDir, logPath,
    manifestPath: path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'),
  });
  const res = await runUpdate(argv, { cwd, configDir, homeDir: os.homedir(), pkgDir: CLI_DIR, output, spawnEnv: env });
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

// ================================================================================================
// gitignore-block-reconcile (ER #177, AC-GBR-1) — Phase 4's row and verdict
// ================================================================================================

// A pre-#170 stale block, the shape the bash applier itself once wrote (longer sentinel line text,
// proving the row's detection is substring-based) — same fixture shape gitignore-reconcile.test.mjs
// uses, kept local because this file is deliberately self-contained (this module's own convention).
const STALE_BLOCK = [
  `# ${BEGIN_TOKEN} (managed by scripts/foundry-apply-runtime-gitignore.sh -- do not edit by hand)`,
  '# .foundry/ runtime partitions are ignored by default; the designed-tracked set is re-included.',
  '/.foundry/*',
  '!/.foundry/README.md',
  '!/.foundry/build-provenance.yaml',
  '!/.foundry/stack-profile.lock',
  `# ${END_TOKEN} (re-run the applier to converge; do not edit by hand)`,
];

test('Phase 4 converges a stale gitignore block left over from an older workspace, and only that', async () => {
  const { root, cwd, configDir } = steadyStateFixture('gbr-uaw-');
  const first = await invokeUpdate({ cwd, configDir });
  assert.notEqual(first.res.exitCode, 1, first.res.output);
  // the fixture's own `reinitialization` phase settles to already-current once the floor is added —
  // a second run before touching .gitignore proves the workspace really is fully converged already,
  // so the NEXT run's `changed` verdict can only be attributed to this atom's gitignore reconcile.
  const settled = await invokeUpdate({ cwd, configDir });
  assert.match(settled.res.output, /\[reinitialization] already current/, settled.res.output);

  const statOf = (rel) => fs.statSync(path.join(cwd, rel));
  const beforeStats = Object.fromEntries(
    DECLARED_PATH_SET.filter((rel) => rel !== '.gitignore').map((rel) => [rel, statOf(rel)]),
  );

  // corrupt ONLY the managed block, preserving an adopter line on each side
  const gitignorePath = path.join(cwd, '.gitignore');
  fs.writeFileSync(
    gitignorePath,
    ['# adopter comment above', ...STALE_BLOCK, 'dist/'].map((l) => `${l}\n`).join(''),
  );

  const { res, text } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);

  // the preview names the row before the first write (AC-UAW-7's own ordering, reused here)
  assert.match(text, /\[converged] \.gitignore \(managed block\)/, text);
  // the phase this atom wires into reports `changed` — attributable to nothing but the gitignore
  // convergence, since every other declared path is about to be asserted byte/inode-stable below
  assert.match(text, /\[reinitialization] changed/, text);

  const converged = fs.readFileSync(gitignorePath, 'utf-8').split('\n');
  const body = converged[converged.length - 1] === '' ? converged.slice(0, -1) : converged;
  assert.equal(body[0], '# adopter comment above', 'the adopter line above the block was not preserved');
  assert.equal(body[body.length - 1], 'dist/', 'the adopter line below the block was not preserved');
  // PR #179 review: the EXISTING (annotated) sentinel lines are preserved verbatim — only the
  // interior converges onto the template.
  const desiredInterior = loadDesiredInterior(path.join(CLI_DIR, 'templates'));
  assert.equal(body[1], STALE_BLOCK[0], 'the existing annotated BEGIN line was rewritten');
  assert.deepEqual(body.slice(2, 2 + desiredInterior.length), desiredInterior,
    'the interior was not converged onto the template');
  assert.equal(body[2 + desiredInterior.length], STALE_BLOCK[STALE_BLOCK.length - 1],
    'the existing annotated END line was rewritten');

  for (const rel of Object.keys(beforeStats)) {
    const a = statOf(rel);
    assert.equal(a.ino, beforeStats[rel].ino, `${rel}: inode changed by a run that should only touch .gitignore`);
    assert.equal(a.mtimeMs, beforeStats[rel].mtimeMs, `${rel}: mtime changed by a run that should only touch .gitignore`);
  }
  fs.rmSync(root, { recursive: true, force: true });
});

test('Phase 4 leaves an already-converged gitignore block untouched (no write syscall)', async () => {
  const { root, cwd, configDir } = steadyStateFixture('gbr-uaw-stable-');
  await invokeUpdate({ cwd, configDir }); // creates .gitignore already matching the template

  const gitignorePath = path.join(cwd, '.gitignore');
  const before = fs.readFileSync(gitignorePath);
  const beforeIno = fs.statSync(gitignorePath).ino;

  const { text } = await invokeUpdate({ cwd, configDir });
  assert.match(text, /\[unchanged] \.gitignore \(managed block\)/, text);
  assert.deepEqual(fs.readFileSync(gitignorePath), before, 'an already-converged .gitignore was rewritten');
  assert.equal(fs.statSync(gitignorePath).ino, beforeIno, 'an already-converged .gitignore was rewritten');
  fs.rmSync(root, { recursive: true, force: true });
});

// amendments-backfill (ER #214, AC-AMB-1/-2/-5) — Phase 4's row and verdict through the real
// upgrader: a spec that predates /foundry:amend gains the empty section, the phase reports
// `changed`, and the very next run reports `backfilled 0` and `already current`.
test('Phase 4 backfills the Amendments section on a pre-amend spec, then settles', async () => {
  const { cwd, configDir } = steadyStateFixture('amb-uaw-');
  const first = await invokeUpdate({ cwd, configDir });
  assert.notEqual(first.res.exitCode, 1, first.res.output);
  const settled = await invokeUpdate({ cwd, configDir });
  assert.match(settled.res.output, /\[reinitialization] already current/, settled.res.output);

  const specPath = path.join(cwd, 'specs', 'features', 'p', 'd', 'c', 'feat-old.md');
  fs.mkdirSync(path.dirname(specPath), { recursive: true });
  const normative = '# feat-old\n\n<!-- normative -->\n- **AC-OLD-1**: it works.\n<!-- /normative -->\n';
  fs.writeFileSync(specPath, normative);

  const { res, text } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  assert.match(text, /\[amendments] backfilled 1 of 3 specs \(0 already present, 2 skipped: no normative region\)/, text);
  // ER #228 (v1.17.2): the run says WHICH updater ran, first; the report carries the denominator
  assert.match(text.split('\n')[0], /^update-agentic-workspace (unknown|\d+\.\d+\.\d+) \(core create-agentic-workspace \d+\.\d+\.\d+, built for plugin \d+\.\d+\.\d+\)$/, text.split('\n')[0]);
  assert.match(text, /\[reinitialization] changed/, text);
  const after = fs.readFileSync(specPath, 'utf-8');
  assert.ok(after.startsWith(normative), 'the spec body above the section was not preserved');
  assert.ok(after.endsWith('## Amendments\n\n| date | what changed | why reality required it | auth_seq |\n|---|---|---|---|\n'));

  const again = await invokeUpdate({ cwd, configDir });
  assert.match(again.text, /\[amendments] backfilled 0 of 3 specs \(1 already present, 2 skipped: no normative region\)/, again.text);
  assert.match(again.res.output, /\[reinitialization] already current/, again.res.output);
  assert.equal(fs.readFileSync(specPath, 'utf-8'), after);
});

// post-upgrade-skill (AC-PUS-1) — the report is written on every completed run and the LAST
// line names the skill; through the real upgrader, over the steady-state fixture.
test('every completed run writes .foundry/upgrade-report.json and ends with the post-upgrade hand-off line', async () => {
  const { cwd, configDir } = steadyStateFixture('upr-uaw-');
  const { res, text } = await invokeUpdate({ cwd, configDir });
  assert.notEqual(res.exitCode, 1, res.output);
  const lines = res.output.trimEnd().split('\n');
  assert.equal(lines[lines.length - 1], 'next: run /foundry:post-upgrade in your next session (report: .foundry/upgrade-report.json)', text);
  const report = JSON.parse(fs.readFileSync(path.join(cwd, '.foundry', 'upgrade-report.json'), 'utf-8'));
  assert.equal(report.schema_version, 1);
  assert.equal(report.to_plugin_version, PINS.plugin_version);
  assert.ok(Array.isArray(report.phases) && report.phases.some((p) => p.name === 'reinitialization'));
  assert.ok(['created', 'kept'].includes(report.permissions_policy), report.permissions_policy);
  assert.deepEqual(Object.keys(report.amendments).sort(), ['backfilled', 'present', 'skipped', 'total']);
  assert.equal(report.amendments.backfilled + report.amendments.present + report.amendments.skipped, report.amendments.total);
  // ER #228: the report names the updater's core and the plugin it was built for
  assert.match(report.core_version, /^\d+\.\d+\.\d+$/);
  assert.equal(report.updater_plugin_version, report.to_plugin_version);
});


// hotfix-v1.17.4 (ER #236): the retired-artifacts sweep and the settings.local.json retirement.
test('Phase 4 reports a retired artifact every run, removes it only under --cleanup, and retires a pinned row in settings.local.json', async () => {
  const { cwd, configDir } = steadyStateFixture('ra-uaw-');
  await invokeUpdate({ cwd, configDir });
  fs.mkdirSync(path.join(cwd, '.foundry'), { recursive: true });
  fs.writeFileSync(path.join(cwd, '.foundry', 'wiring-hash.pin'), 'stale');
  fs.mkdirSync(path.join(cwd, '.claude', 'skills'), { recursive: true });
  fs.writeFileSync(path.join(cwd, '.claude', 'skills', 'mine.md'), 'operator');
  // a pinned `ask` row for a pair the tracked file's wildcard ask row covers (retired), and one for
  // a pair the map declares at ask but the tracked file does NOT carry (kept — Risk 3)
  const trackedAsk = readJson(path.join(cwd, '.claude', 'settings.json')).permissions.ask
    .filter((r) => /^Bash\(~\/\.claude\/plugins\/cache\/\*\/foundry\/\*\/scripts\//.test(r));
  assert.ok(trackedAsk.length >= 1, 'fixture: the tracked file carries at least one wildcard ask row');
  const pinnedAsk = trackedAsk[0].replace('~/.claude/plugins/cache/*/foundry/*/', '~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/');
  writeJson(path.join(cwd, '.claude', 'settings.local.json'), { permissions: { allow: [
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-doctor.py:*)',
    'Bash(/opt/mine/tool:*)',
  ], ask: [pinnedAsk, 'Bash(/opt/mine/dangerous:*)'] } });
  fs.chmodSync(path.join(cwd, '.claude', 'settings.local.json'), 0o600);

  const first = await invokeUpdate({ cwd, configDir });
  assert.notEqual(first.res.exitCode, 1, first.res.output);
  // Risk 2: the preview announces the local-file write BEFORE the first write happens
  const previewIdx = first.text.indexOf('[permission-floor] .claude/settings.local.json: would retire allow=1, ask=1 (never adds)');
  const writeIdx = first.text.indexOf('[permission-floor] .claude/settings.local.json: retired 2');
  assert.ok(previewIdx !== -1 && writeIdx !== -1 && previewIdx < writeIdx, `preview row precedes the write row:\n${first.text}`);
  assert.match(first.text, /\[stale] \.foundry\/wiring-hash\.pin — retired in v0\.24\.0 .*; remove with --cleanup/, first.text);
  assert.equal(fs.existsSync(path.join(cwd, '.foundry', 'wiring-hash.pin')), true, 'nothing removed without --cleanup');
  assert.match(first.text, /\[permission-floor] \.claude\/settings\.local\.json: retired 2 version-pinned\/gone row\(s\)/, first.text);
  assert.match(first.text, /\[reinitialization] changed/, 'the local retirement counts toward the phase verdict');
  const local = readJson(path.join(cwd, '.claude', 'settings.local.json'));
  assert.deepEqual(local.permissions.allow, ['Bash(/opt/mine/tool:*)']);
  assert.deepEqual(local.permissions.ask, ['Bash(/opt/mine/dangerous:*)']);
  assert.equal(fs.statSync(path.join(cwd, '.claude', 'settings.local.json')).mode & 0o777, 0o600, 'Risk 5: mode preserved');
  const report = readJson(path.join(cwd, '.foundry', 'upgrade-report.json'));
  assert.deepEqual(report.retired_artifacts, { present: ['.foundry/wiring-hash.pin'], removed: 0, refused: 0 });
  assert.equal(report.settings_local_retired, 2);

  const cleaned = await invokeUpdate({ cwd, configDir, argv: ['--cleanup'] });
  assert.notEqual(cleaned.res.exitCode, 1, cleaned.res.output);
  assert.match(cleaned.text, /\[removed] \.foundry\/wiring-hash\.pin — retired in v0\.24\.0/, cleaned.text);
  assert.equal(fs.existsSync(path.join(cwd, '.foundry', 'wiring-hash.pin')), false);
  assert.equal(fs.existsSync(path.join(cwd, '.claude', 'skills', 'mine.md')), true, 'operator files are invisible to the sweep');
  const report2 = readJson(path.join(cwd, '.foundry', 'upgrade-report.json'));
  assert.equal(report2.retired_artifacts.removed, 1);
});
