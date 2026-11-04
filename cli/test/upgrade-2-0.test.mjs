// upgrade-2-0.test.mjs — feat-foundry-adoption-npx-upgrade-2-0 (AC-NPX-2 ... AC-NPX-9, AC-NPX-11 ...
// AC-NPX-13). Every acceptance-contract checkpoint names ONE of these tests by its exact title, so each
// is a TOP-LEVEL `test(...)` (a nested describe would indent the TAP line the checkpoint matches).
//
// TEST ISOLATION, BINDING: every test drives an ISOLATED CLAUDE_CONFIG_DIR holding fabricated
// fixtures and an INJECTED STUB `claude` on PATH that records its argv. Nothing here touches the
// developer's real ~/.claude, the network, `claude` or `gh`.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { fileURLToPath } from 'node:url';
import { runUpdate } from '../src/update.mjs';
import { runCli } from '../src/run.mjs';
import { diffSettingsFile, REPORT_REL } from '../src/upgradeReport.mjs';
import { readCutoverState, CUTOVER_RECORD_REL, DIAGNOSIS_TABLE, makeRunId } from '../src/upgradeCompat.mjs';
import { loadPathClasses, classOf, parseRow, EFFECTS, WRITE_EFFECTS, PROTECTED_BASE } from '../src/pathClasses.mjs';
import { loadRetiredCatalogue, planRetiredArtifacts, applyRetiredArtifacts } from '../src/retiredArtifacts.mjs';
import { planAmendmentsBackfill, applyAmendmentsBackfill } from '../src/amendmentsBackfill.mjs';
import { planManifestTrueUp, applyManifestTrueUp } from '../src/manifestTrueUp.mjs';
import { DECLARED_PATH_SET } from '../src/scaffold.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const BIN_SRC = path.join(CLI_DIR, '..', 'cli-update', 'bin', 'update-agentic-workspace.mjs');
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const MARKETPLACE = PINS.marketplace_name;
const REPO = PINS.marketplace_repo;
const PLUGIN = PINS.plugin_name;
const KEY = `${PLUGIN}@${MARKETPLACE}`;
const V = PINS.plugin_version;
const PLANTED = 'PLANTED-7f3a';
const RUN_ID_RE = /^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$/;
const TABLE = loadPathClasses(CLI_DIR);

// ── fixture plumbing ────────────────────────────────────────────────────────────────────────────

const scratch = (prefix) => fs.realpathSync(fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), prefix)));
const writeJson = (p, obj) => { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, `${JSON.stringify(obj, null, 2)}\n`); };
const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf-8'));
const write = (p, text) => { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, text); };

const tagless = (repo = REPO) => ({ source: { source: 'github', repo }, autoUpdate: false });
const tagPinned = () => ({ source: { source: 'github', repo: REPO, ref: 'v1.5.0' }, autoUpdate: false });

function installDir(configDir, version) {
  const dir = path.join(configDir, 'plugins', 'cache', MARKETPLACE, PLUGIN, version);
  writeJson(path.join(dir, '.claude-plugin', 'plugin.json'), { name: PLUGIN, version });
  return dir;
}

/** A supported, complete install: project scope enabled (optionally user and local), the registry
 * naming a record per enabled scope at `versions`, every install directory carrying its manifest. */
function fixture({ user = false, local = false, registry = true, manifestVersion = V, versions = {}, projectEntry = tagless(), userEntry = tagless() } = {}) {
  const root = scratch('npx20-');
  const cwd = path.join(root, 'workspace');
  const configDir = path.join(root, 'config');
  fs.mkdirSync(cwd, { recursive: true });
  fs.mkdirSync(configDir, { recursive: true });
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: [], ask: [], deny: [] },
    extraKnownMarketplaces: { [MARKETPLACE]: projectEntry },
    enabledPlugins: { [KEY]: true },
  });
  if (user) {
    writeJson(path.join(configDir, 'settings.json'), {
      extraKnownMarketplaces: { [MARKETPLACE]: userEntry }, enabledPlugins: { [KEY]: true },
    });
  }
  if (local) writeJson(path.join(cwd, '.claude', 'settings.local.json'), { enabledPlugins: { [KEY]: true } });
  if (manifestVersion !== null) {
    writeJson(path.join(configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'), {
      name: MARKETPLACE, owner: { name: 'o' },
      plugins: [{ name: PLUGIN, source: { source: 'github', repo: REPO, sha: 'a'.repeat(40) }, version: manifestVersion }],
    });
  }
  if (registry) {
    const records = [{ scope: 'project', projectPath: cwd, version: versions.project || V, installPath: installDir(configDir, versions.project || V) }];
    if (user) records.push({ scope: 'user', version: versions.user || V, installPath: installDir(configDir, versions.user || V) });
    if (local) records.push({ scope: 'local', projectPath: cwd, version: versions.local || V, installPath: installDir(configDir, versions.local || V) });
    writeJson(path.join(configDir, 'plugins', 'installed_plugins.json'), { version: 2, plugins: { [KEY]: records } });
  }
  return { root, cwd, configDir };
}

function installClaudeStub(dir) {
  const stubPath = path.join(dir, 'claude');
  fs.writeFileSync(stubPath, `#!/usr/bin/env node
import fs from 'node:fs';
const argv = process.argv.slice(2);
if (process.env.CLAUDE_STUB_LOG) fs.appendFileSync(process.env.CLAUDE_STUB_LOG, JSON.stringify(argv) + '\\n');
const scopeOf = (a) => { const i = a.indexOf('--scope'); return i === -1 ? null : a[i + 1]; };
const readJ = (p) => JSON.parse(fs.readFileSync(p, 'utf-8'));
const writeJ = (p, o) => fs.writeFileSync(p, JSON.stringify(o, null, 2) + '\\n');
const scopes = process.env.CLAUDE_STUB_SCOPE_SETTINGS ? JSON.parse(process.env.CLAUDE_STUB_SCOPE_SETTINGS) : {};
const mp = process.env.CLAUDE_STUB_MARKETPLACE_NAME;
const p = scopes[scopeOf(argv)];
if (argv[2] === 'remove' && p && fs.existsSync(p)) {
  const o = readJ(p); if (o.extraKnownMarketplaces) delete o.extraKnownMarketplaces[mp]; o.enabledPlugins = {}; writeJ(p, o);
} else if (argv[2] === 'add' && p && fs.existsSync(p)) {
  const o = readJ(p); o.extraKnownMarketplaces = o.extraKnownMarketplaces || {};
  o.extraKnownMarketplaces[mp] = { source: { source: 'github', repo: argv[3] }, autoUpdate: false }; writeJ(p, o);
} else if (argv[1] === 'install' && p && fs.existsSync(p)) {
  const o = readJ(p); o.enabledPlugins = o.enabledPlugins || {}; o.enabledPlugins[argv[2]] = true; writeJ(p, o);
}
process.stdout.write('claude: ok (stub)\\n');
`);
  fs.chmodSync(stubPath, 0o755);
}

const readLog = (p) => (fs.existsSync(p) ? fs.readFileSync(p, 'utf-8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []);

/** A copy of the cli package's DATA (package.json, jsons, templates, src) with `mutate(pkg)` applied,
 * so a test can set `supported_from_min` / `supported_sources` / `plugin_version` to fixture values. */
function mkPkg(mutate) {
  const dir = scratch('npx20-pkg-');
  fs.cpSync(CLI_DIR, dir, {
    recursive: true,
    filter: (src) => {
      const rel = path.relative(CLI_DIR, src);
      return !(rel === 'test' || rel === 'node_modules' || rel.startsWith(`test${path.sep}`) || rel.startsWith(`node_modules${path.sep}`));
    },
  });
  const pkg = readJson(path.join(dir, 'package.json'));
  mutate(pkg);
  writeJson(path.join(dir, 'package.json'), pkg);
  return dir;
}

async function run(fx, {
  argv = [], pkgDir = CLI_DIR, failAfterPhase = null, env = {}, onWrite = null, updaterVersion = '0.2.5',
} = {}) {
  const stubDir = scratch('npx20-stub-');
  installClaudeStub(stubDir);
  const logPath = path.join(stubDir, 'log.jsonl');
  const chunks = [];
  const output = { write(s) { chunks.push(s); if (onWrite) onWrite(s, logPath); } };
  const spawnEnv = {
    ...process.env,
    PATH: `${stubDir}${path.delimiter}${process.env.PATH || ''}`,
    CLAUDE_STUB_LOG: logPath,
    FOUNDRY_PYTHON: '/nonexistent/python3',
    CLAUDE_STUB_MARKETPLACE_NAME: MARKETPLACE,
    CLAUDE_STUB_SCOPE_SETTINGS: JSON.stringify({ project: path.join(fx.cwd, '.claude', 'settings.json'), user: path.join(fx.configDir, 'settings.json') }),
    ...env,
  };
  const options = { cwd: fx.cwd, configDir: fx.configDir, homeDir: os.homedir(), pkgDir, output, spawnEnv, updaterVersion };
  if (failAfterPhase) options.failAfterPhase = failAfterPhase;
  const res = await runUpdate(argv, options);
  const reportPath = path.join(fx.cwd, REPORT_REL);
  const st = fs.lstatSync(reportPath, { throwIfNoEntry: false });
  const report = st && st.isFile() ? readJson(reportPath) : null;
  const text = chunks.join('');
  return { res, text, log: readLog(logPath), report, last: text.trimEnd().split('\n').pop() };
}

/** rel path -> content hash (or `->target` for a link), skipping `skip` prefixes. */
function snap(dir, skip = [REPORT_REL]) {
  const out = {};
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const abs = path.join(d, e.name);
      const rel = path.relative(dir, abs).split(path.sep).join('/');
      if (skip.some((s) => rel === s || rel.startsWith(`${s}/`))) continue;
      if (e.isSymbolicLink()) out[rel] = `->${fs.readlinkSync(abs)}`;
      else if (e.isDirectory()) { out[`${rel}/`] = 'dir'; walk(abs); } else out[rel] = createHash('sha256').update(fs.readFileSync(abs)).digest('hex');
    }
  };
  walk(dir);
  return out;
}
const changedPaths = (a, b) => [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((k) => a[k] !== b[k] && !k.endsWith('/'));

const stateOf = (rep) => ({ outcome: rep.outcome, codes: rep.compatibility.diagnoses.map((d) => d.code) });
const lastLineOf = (outcome, report) => `outcome: ${outcome} · next: ${report.next_action} · cutover: ${report.adoption.cutover}`;
const HANDOFF = 'run /foundry:post-upgrade in your next session (report: .foundry/upgrade-report.json)';
const SPEC_NORMATIVE = '# feat-old\n\n<!-- normative -->\n- **AC-OLD-1**: it works.\n<!-- /normative -->\n';

// ================================================================================================
// AC-NPX-2
// ================================================================================================

test('AC-NPX-2 the report records versions scopes sources session load and adoption', async () => {
  // a complete arm: project + user + local enabled, a record per scope
  const fx = fixture({ user: true, local: true });
  const r = await run(fx);
  assert.equal(r.res.exitCode, 0, r.text);
  const rep = r.report;
  assert.equal(rep.schema_version, 2);
  assert.match(rep.run_id, RUN_ID_RE);
  assert.equal(rep.outcome, 'complete');
  assert.equal(rep.next_action, HANDOFF);
  assert.equal(rep.failure, null);
  assert.equal(rep.previous_partial, null);
  assert.deepEqual(rep.cleanup, []);
  assert.ok(Array.isArray(rep.operator_review));
  const c = rep.compatibility;
  assert.equal(c.from_plugin_version, V);
  assert.equal(c.to_plugin_version, V);
  assert.equal(c.updater_version, '0.2.5');
  assert.equal(c.core_version, readJson(path.join(CLI_DIR, 'package.json')).version);
  assert.equal(c.updater_plugin_version, V);
  assert.equal(c.advertised_version, V);
  assert.deepEqual(c.enabled_scopes, ['user', 'project', 'local']);
  assert.deepEqual(c.diagnoses, []);
  const byLoc = Object.fromEntries(c.sources.map((s) => [s.location, s]));
  for (const loc of ['.claude/settings.json', '<config-dir>/settings.json']) {
    assert.deepEqual(byLoc[loc], { location: loc, scope: loc.startsWith('<') ? 'user' : 'project', source_type: 'github', repo_or_url: REPO, auto_update: false });
  }
  assert.deepEqual(c.session_load.map((s) => s.scope), ['user', 'project', 'local']);
  for (const s of c.session_load) {
    assert.equal(s.version, V);
    assert.match(s.install_path, /^<config-dir>\/plugins\/cache\//);
    assert.equal(s.directory_exists, true);
    assert.equal(s.manifest_declares_version, true);
  }
  assert.deepEqual(rep.adoption.mechanical_steps.map((s) => s.phase), ['marketplace-refresh', 'plugin-update', 'python-deps', 'cleanup', 'reinitialization']);
  assert.ok(rep.adoption.mechanical_steps.every((s) => s.verdict !== 'not-run'));
  assert.equal(rep.adoption.cutover, 'pending');
  assert.deepEqual(rep.adoption.instructions_pending, []);

  // an unreadable registry: to_plugin_version is null — never the package pin the v1 field falls back to
  const bare = fixture({ registry: false });
  const b = await run(bare);
  assert.equal(b.res.exitCode, 2, b.text);
  assert.equal(b.report.compatibility.from_plugin_version, null);
  assert.equal(b.report.compatibility.to_plugin_version, null);
  assert.equal(b.report.to_plugin_version, V, 'the v1 top-level field keeps its fallback');
  assert.deepEqual(stateOf(b.report).codes.sort(), ['registry-unreadable', 'source-version-unknown']);
  assert.equal(b.report.outcome, 'attention-needed');

  // a refused arm: everything the run did not reach is null / [] and its phases are `not-run`
  const refused = fixture({ projectEntry: { source: { source: 'github', repo: 'someone/else' } } });
  const f = await run(refused);
  assert.equal(f.res.exitCode, 1);
  const fr = f.report;
  assert.equal(fr.outcome, 'refused');
  assert.equal(fr.compatibility.to_plugin_version, null);
  assert.equal(fr.compatibility.advertised_version, null);
  assert.deepEqual(fr.compatibility.session_load, []);
  assert.deepEqual(fr.cleanup, []);
  assert.deepEqual(fr.operator_review, []);
  assert.deepEqual(fr.adoption.mechanical_steps.map((s) => s.verdict), ['not-run', 'not-run', 'not-run', 'not-run', 'not-run']);
  assert.deepEqual(fr.failure, { phase: 'preflight', code: 'foreign-marketplace-source' });
  assert.equal(fr.next_action, fr.compatibility.diagnoses[0].remedy);
});

// ================================================================================================
// AC-NPX-3
// ================================================================================================

const FOREIGN_URL = 'https://example.invalid/x.git';

test('AC-NPX-3 every diagnosis row records its code remedy and effect', async () => {
  const effectByCode = Object.fromEntries(DIAGNOSIS_TABLE.map((r) => [r.code, r.effect]));
  assert.equal(DIAGNOSIS_TABLE.length, 11);
  const seen = new Set();

  const stopPre = async (code, fx, opts = {}, remedy = /./) => {
    const before = snap(fx.cwd);
    const userBefore = fs.existsSync(path.join(fx.configDir, 'settings.json')) ? fs.readFileSync(path.join(fx.configDir, 'settings.json')) : null;
    const r = await run(fx, opts);
    assert.equal(r.res.exitCode, 1, `${code}: ${r.text}`);
    assert.equal(r.report.outcome, 'refused', code);
    const d = r.report.compatibility.diagnoses.find((x) => x.code === code);
    assert.ok(d, `${code} not recorded: ${JSON.stringify(r.report.compatibility.diagnoses)}`);
    assert.match(d.remedy, remedy, code);
    assert.equal(effectByCode[code], 'stop');
    assert.deepEqual(r.log, [], `${code}: a claude command was spawned before a pre stop`);
    assert.deepEqual(changedPaths(before, snap(fx.cwd)), [], `${code}: a workspace path changed`);
    if (userBefore) assert.deepEqual(fs.readFileSync(path.join(fx.configDir, 'settings.json')), userBefore);
    assert.match(r.last, /^outcome: refused · next: .+ · cutover: pending$/);
    seen.add(code);
    return r;
  };

  // 1 input-unreadable (user-scope file, then the project file)
  let fx = fixture({ user: true });
  fs.writeFileSync(path.join(fx.configDir, 'settings.json'), '{ not json');
  await stopPre('input-unreadable', fx, {}, /<config-dir>\/settings\.json/);
  fx = fixture();
  fs.writeFileSync(path.join(fx.cwd, '.claude', 'settings.json'), '[1, 2]');
  await stopPre('input-unreadable', fx, {}, /\.claude\/settings\.json/);

  // 1b the other rows are still evaluated when one input is unreadable: both are recorded, in table order
  fx = fixture({ user: true, versions: { project: '99.0.0' } });
  fs.writeFileSync(path.join(fx.configDir, 'settings.json'), '{ not json');
  let two = await run(fx);
  assert.deepEqual(two.report.compatibility.diagnoses.map((d) => d.code), ['input-unreadable', 'downgrade']);
  assert.equal(two.report.next_action, two.report.compatibility.diagnoses[0].remedy);
  assert.equal(two.res.exitCode, 1);

  // 2 foreign-marketplace-source: every fail-open arm fires
  fx = fixture();
  write(path.join(fx.configDir, 'plugins', 'known_marketplaces.json'), '{ not json');
  await stopPre('foreign-marketplace-source', fx, {}, /known_marketplaces\.json/);
  const kelvin = REPO.replace('k', 'K');
  assert.notEqual(kelvin, REPO);
  for (const entry of [
    { source: { source: 'url', url: FOREIGN_URL }, autoUpdate: false },
    { source: { source: 'git', url: FOREIGN_URL }, autoUpdate: false },
    { source: { source: 'github', repo: 'someone/else' } },
    { source: { source: 'github', repo: kelvin } },
    { source: { repo: REPO } }, // no `source` type
    { source: { source: 'github', repo: REPO, path: 'elsewhere' } }, // ours by repo, but with a key that fetches something else
    { source: { source: 'github', repo: REPO, sha: 'a'.repeat(40) } },
    { source: { source: 'github', repo: REPO, ref: '../../x' } },
  ]) {
    fx = fixture({ projectEntry: entry });
    await stopPre('foreign-marketplace-source', fx, {}, new RegExp(`claude plugin marketplace add ${REPO.replace('/', '\\/')} --scope project`));
  }
  // ...and the same url source listed in the shipped supported_sources does NOT fire
  fx = fixture({ projectEntry: { source: { source: 'url', url: FOREIGN_URL }, autoUpdate: false } });
  const supported = mkPkg((p) => { p.foundry.upgrade.supported_sources = [{ source: 'url', url: FOREIGN_URL }]; });
  let r = await run(fx, { pkgDir: supported });
  assert.ok(!r.report.compatibility.diagnoses.some((d) => d.code === 'foreign-marketplace-source'), JSON.stringify(r.report.compatibility.diagnoses));
  assert.notEqual(r.res.exitCode, 1, r.text);
  // a plain tag-pinned github source (the pre-1.7 shape the updater migrates) is still ours
  fx = fixture({ projectEntry: tagPinned() });
  r = await run(fx);
  assert.ok(!r.report.compatibility.diagnoses.some((d) => d.code === 'foreign-marketplace-source'));
  // case-insensitive ASCII match of OUR repo is ours
  fx = fixture({ projectEntry: tagless(REPO.toUpperCase()) });
  r = await run(fx);
  assert.ok(!r.report.compatibility.diagnoses.some((d) => d.code === 'foreign-marketplace-source'));

  // 3 unsupported-source-version (fixture bound, never the release value); an unusable bound fails closed
  fx = fixture({ versions: { project: '1.9.0' } });
  await stopPre('unsupported-source-version', fx, { pkgDir: mkPkg((p) => { p.foundry.upgrade.supported_from_min = '1.10.0'; }) }, /supports 1\.9\.0$/);
  fx = fixture();
  await stopPre('unsupported-source-version', fx, { pkgDir: mkPkg((p) => { p.foundry.upgrade.supported_from_min = 'soon'; }) });
  fx = fixture({ versions: { project: '1.10.0' } });
  r = await run(fx, { pkgDir: mkPkg((p) => { p.foundry.upgrade.supported_from_min = '1.10.0'; }) });
  assert.ok(!r.report.compatibility.diagnoses.some((d) => d.code === 'unsupported-source-version'), 'the bound itself is supported');

  // 4 downgrade
  fx = fixture({ versions: { project: '99.0.0' } });
  await stopPre('downgrade', fx, {}, /^npx update-agentic-workspace@latest$/);

  // 5 advertised-version-unreadable (post-refresh): manifest absent / non-strict / no entry / unparseable
  const stopPostRefresh = async (code, fx2, remedy = /./) => {
    const before = snap(fx2.cwd);
    const rr = await run(fx2);
    assert.equal(rr.res.exitCode, 1, `${code}: ${rr.text}`);
    assert.equal(rr.report.outcome, 'refused', code);
    assert.ok(rr.report.compatibility.diagnoses.some((d) => d.code === code && remedy.test(d.remedy)), JSON.stringify(rr.report.compatibility.diagnoses));
    assert.deepEqual(rr.log.map((a) => a.slice(0, 3).join(' ')), ['plugin marketplace update'], `${code}: only the marketplace refresh may have been spawned`);
    assert.deepEqual(changedPaths(before, snap(fx2.cwd)), []);
    assert.equal(effectByCode[code], 'stop');
    seen.add(code);
    return rr;
  };
  await stopPostRefresh('advertised-version-unreadable', fixture({ manifestVersion: null }), /^claude plugin marketplace update agentic-foundry, then rerun$/);
  await stopPostRefresh('advertised-version-unreadable', fixture({ manifestVersion: '1.2.3-rc.1' }));
  fx = fixture();
  writeJson(path.join(fx.configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'), { name: MARKETPLACE, plugins: [{ name: 'other', version: V }] });
  await stopPostRefresh('advertised-version-unreadable', fx);
  fx = fixture();
  fs.writeFileSync(path.join(fx.configDir, 'plugins', 'marketplaces', MARKETPLACE, '.claude-plugin', 'marketplace.json'), '{ nope');
  await stopPostRefresh('advertised-version-unreadable', fx);

  // 6 stale-updater
  await stopPostRefresh('stale-updater', fixture({ manifestVersion: '99.0.0' }), /^npx update-agentic-workspace@latest$/);

  // continue rows: the run reaches its end, attention-needed, exit 2
  const continues = async (code, fx2, opts = {}, remedy = /./) => {
    const rr = await run(fx2, opts);
    assert.equal(rr.res.exitCode, 2, `${code}: ${rr.text}`);
    assert.equal(rr.report.outcome, 'attention-needed', code);
    const d = rr.report.compatibility.diagnoses.find((x) => x.code === code);
    assert.ok(d, `${code} missing: ${JSON.stringify(rr.report.compatibility.diagnoses)}`);
    assert.match(d.remedy, remedy, code);
    assert.equal(effectByCode[code], 'continue');
    assert.ok(rr.log.some((a) => a[1] === 'update'), `${code}: the run did not reach the plugin update`);
    assert.equal(rr.report.adoption.mechanical_steps.at(-1).verdict !== 'not-run', true, `${code}: the run did not reach reinitialization`);
    assert.equal(rr.report.next_action, HANDOFF);
    seen.add(code);
    return rr;
  };
  // 7 source-version-unknown
  await continues('source-version-unknown', fixture({ registry: false }), {}, /^claude plugin list to confirm the installed version$/);
  // 8 input-skipped: an unparseable settings.local.json is left byte-identical and named
  fx = fixture();
  fs.writeFileSync(path.join(fx.cwd, '.claude', 'settings.local.json'), '{ broken');
  await continues('input-skipped', fx, {}, /\.claude\/settings\.local\.json \(does not parse\)/);
  assert.equal(fs.readFileSync(path.join(fx.cwd, '.claude', 'settings.local.json'), 'utf-8'), '{ broken');
  // 9 registry-unreadable (post-update): an unrecognised registry version
  fx = fixture();
  writeJson(path.join(fx.configDir, 'plugins', 'installed_plugins.json'), { version: 3, plugins: {} });
  r = await continues('registry-unreadable', fx, {}, /claude plugin install foundry@agentic-foundry --scope <scope>/);
  assert.equal(r.report.compatibility.to_plugin_version, null);
  // 10 mixed-scope-version: the user scope's record names another version
  fx = fixture({ user: true, versions: { user: '1.0.0' } });
  r = await continues('mixed-scope-version', fx, {}, /^claude plugin update foundry@agentic-foundry --scope user$/);
  assert.equal(r.report.compatibility.to_plugin_version, V);
  // 11 install-path-missing: a directory that is gone, then a manifest declaring another version
  fx = fixture();
  fs.rmSync(path.join(fx.configDir, 'plugins', 'cache', MARKETPLACE, PLUGIN, V), { recursive: true });
  r = await continues('install-path-missing', fx, {}, /^claude plugin install foundry@agentic-foundry --scope project$/);
  assert.equal(r.report.compatibility.session_load[0].directory_exists, false);
  fx = fixture();
  writeJson(path.join(fx.configDir, 'plugins', 'cache', MARKETPLACE, PLUGIN, V, '.claude-plugin', 'plugin.json'), { name: PLUGIN, version: '0.0.1' });
  await continues('install-path-missing', fx);

  assert.deepEqual([...seen].sort(), DIAGNOSIS_TABLE.map((x) => x.code).sort(), 'every one of the eleven rows was exercised');

  // precedence: two rows fire -> next_action is the remedy of the EARLIER table row
  fx = fixture({ projectEntry: { source: { source: 'git', url: FOREIGN_URL } }, versions: { project: '99.0.0' } });
  r = await run(fx);
  const codes = r.report.compatibility.diagnoses.map((d) => d.code);
  assert.deepEqual(codes, ['foreign-marketplace-source', 'downgrade'], 'diagnoses are in table order');
  assert.equal(r.report.next_action, r.report.compatibility.diagnoses[0].remedy);
  assert.match(r.last, /claude plugin marketplace add/);
});

// ================================================================================================
// the rich fixture (AC-NPX-4, -6, -12): every effect kind, protected paths, history, quarantine
// ================================================================================================

function richFixture({ cutover = null, user = false } = {}) {
  const fx = fixture({ user, projectEntry: tagPinned(), userEntry: tagPinned() });
  const { cwd } = fx;
  const subagent = { type: 'command', command: '$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-subagent-statusline.sh', padding: 0 };
  writeJson(path.join(cwd, '.claude', 'settings.json'), {
    permissions: { allow: ['Bash(/opt/mine/tool:*)'], ask: [], deny: ['Bash(rm -rf:*)'] },
    extraKnownMarketplaces: { [MARKETPLACE]: tagPinned() },
    enabledPlugins: { [KEY]: true },
    subagentStatusLine: subagent,
  });
  write(path.join(cwd, '.claude', 'hooks', 'foundry-subagent-statusline.sh'), '#!/bin/sh\n');
  writeJson(path.join(cwd, '.claude', 'settings.local.json'), { permissions: {
    allow: ['Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-doctor.py:*)', 'Bash(/opt/mine/other:*)'],
    ask: ['Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-authorize.py:*)'],
    deny: ['Write(.foundry/permissions.yaml)', 'Bash(rm -rf:*)'],
  } });
  write(path.join(cwd, 'CLAUDE.md'), '# my project\nWe run the live-seam gate here.\n');
  write(path.join(cwd, 'AGENTS.md'), '# agents\nall clean\n');
  write(path.join(cwd, 'specs', 'features', 'p', 'd', 'c', 'feat-old.md'), SPEC_NORMATIVE);
  write(path.join(cwd, 'specs', 'features', 'p', 'd', 'c', 'acceptance-contract.yaml'), 'spec_ref: x\nauthorized:\n  auth_seq: 1\n');
  write(path.join(cwd, '.foundry', 'releases', 'r1', 'release.yaml'), 'id: r1\nstate: released\natoms: []\n');
  write(path.join(cwd, '.foundry', 'authorized', 'a.yaml'), 'a: 1\n');
  write(path.join(cwd, '.foundry', 'decisions', 'd.md'), 'decided\n');
  writeJson(path.join(cwd, '.claude', 'foundry-project.json'), { schema_version: 1, repos: { app: { path: '../app' } } });
  writeJson(path.join(cwd, '.claude', 'foundry-operators.json'), { schema_version: 1, operators: { op_x: { github: 'x' } } });
  writeJson(path.join(cwd, '.mcp.json'), { mcpServers: {} });
  write(path.join(cwd, '.foundry', 'permissions.yaml'), '# operator policy\n');
  write(path.join(cwd, '.foundry', 'wiring-hash.pin'), 'stale');
  write(path.join(cwd, '.foundry', 'hasher-ref'), 'x');
  write(path.join(cwd, '.github', 'workflows', 'ci.yml'), 'run: test -f .foundry/hasher-ref\n');
  write(path.join(cwd, '.foundry', 'session-learnings', 'one.md'), 'learned');
  if (cutover) write(path.join(cwd, CUTOVER_RECORD_REL), cutover);
  return fx;
}

// ================================================================================================
// AC-NPX-4
// ================================================================================================

function previewRows(text) {
  const lines = text.split('\n');
  const a = lines.findIndex((l) => l.startsWith('planned effects'));
  const b = lines.findIndex((l) => l.startsWith('operator review'));
  const c = lines.findIndex((l) => l.startsWith('history maintenance:'));
  assert.ok(a !== -1 && b > a && c > b, `preview headings missing:\n${text}`);
  return { effects: lines.slice(a + 1, b), review: lines.slice(b + 1, c), lastIdx: c, lines };
}

test('AC-NPX-4 every run previews each effect with its class and dry run writes nothing', async () => {
  const args = ['--cleanup', '--legacy-maintenance'];
  // a REAL run: the preview is complete before the first spawn / first mutation
  const real = richFixture();
  const startTree = snap(real.cwd);
  let checked = false;
  const r = await run(real, {
    argv: args,
    onWrite: (s, logPath) => {
      if (!checked && s.includes('planned effects')) {
        checked = true;
        assert.deepEqual(readLog(logPath), [], 'a claude command ran before the preview printed');
        assert.deepEqual(snap(real.cwd), startTree, 'a workspace path changed before the preview printed');
        assert.ok(s.includes('operator review') && s.includes('history maintenance:'), 'the preview is one complete block');
      }
    },
  });
  assert.ok(checked, 'the preview never printed');
  assert.notEqual(r.res.exitCode, 1, r.text);
  const { effects, review } = previewRows(r.text);
  const parsed = effects.map((l) => ({ line: l, row: parseRow(l) }));
  for (const { line, row } of parsed) assert.ok(row, `not a [effect] class target row: ${JSON.stringify(line)}`);
  const kinds = new Set(parsed.map((p) => p.row.effect));
  for (const e of EFFECTS) assert.ok(kinds.has(e), `the fixture planned no [${e}] row`);
  for (const { row } of parsed) {
    if (!WRITE_EFFECTS.includes(row.effect)) continue;
    const toks = row.target.split(' ');
    assert.equal(toks.length, row.effect === 'quarantine' ? 2 : 1, `a write row must name ${row.effect === 'quarantine' ? 'two paths' : 'one path'}: ${row.target}`);
    assert.ok(row.effect === 'quarantine' ? toks[1].startsWith('.foundry/retired/') : true);
  }
  for (const l of review) assert.ok(l.trim() === '(none)' || parseRow(l)?.effect === 'review', l);
  assert.equal(parsed.at(-1).row.effect, 'report');

  // DRY RUN on an identical fixture: nothing written, nothing spawned, the two closing lines
  const dry = richFixture();
  const dryBefore = snap(dry.cwd, []);
  const d = await run(dry, { argv: [...args, '--dry-run'] });
  assert.equal(d.res.exitCode, 0, d.text);
  assert.deepEqual(snap(dry.cwd, []), dryBefore, 'the workspace changed under --dry-run');
  assert.equal(fs.existsSync(path.join(dry.cwd, REPORT_REL)), false, 'a dry run wrote the report');
  assert.deepEqual(d.log, [], 'a claude command ran under --dry-run');
  assert.match(d.text, /^post-update checks not evaluated$/m);
  assert.match(d.text, /^cutover: pending$/m);
  assert.match(d.text, /dry run: nothing was written and no claude command was run\./);
  const dryRows = previewRows(d.text);
  assert.equal(dryRows.effects.length, parsed.length, 'a dry run previews the same rows as the real run');

  // exit codes: 1 on a pre stop row, 2 on a pre continue row, 0 on a complete install
  const stop = fixture({ projectEntry: { source: { source: 'git', url: FOREIGN_URL } } });
  assert.equal((await run(stop, { argv: ['--dry-run'] })).res.exitCode, 1);
  const cont = fixture({ registry: false });
  const cr = await run(cont, { argv: ['--dry-run'] });
  assert.equal(cr.res.exitCode, 2);
  assert.equal(cr.log.length, 0);
  assert.equal(fs.existsSync(path.join(cont.cwd, REPORT_REL)), false);
  assert.equal((await run(fixture(), { argv: ['--dry-run'] })).res.exitCode, 0);
  // a drifted managed file (present, not a regular file) also exits 2
  const drifted = fixture();
  fs.mkdirSync(path.join(drifted.cwd, 'CLAUDE.md'));
  assert.equal((await run(drifted, { argv: ['--dry-run'] })).res.exitCode, 2);

  // a dry run with NO claude on PATH is a refusal that still writes nothing: no report, no .foundry/
  const nc = fixture();
  const ncBefore = snap(nc.cwd, []);
  const ncRun = await run(nc, { argv: ['--dry-run'], env: { PATH: scratch('npx20-nopath-') } });
  assert.equal(ncRun.res.exitCode, 1, ncRun.text);
  assert.equal(ncRun.report, null);
  assert.equal(fs.existsSync(path.join(nc.cwd, '.foundry')), false, 'a dry run created .foundry/');
  assert.deepEqual(snap(nc.cwd, []), ncBefore, 'the workspace changed under a refused --dry-run');
  assert.match(ncRun.text, /^refused: claude executable not found on PATH$/m);
  assert.match(ncRun.text, /^post-update checks not evaluated$/m);
  assert.match(ncRun.text, /^cutover: pending$/m);
  assert.deepEqual(ncRun.log, []);
  // ...and so does a dry run whose plan trips the protected-path guard (a hostile catalogue entry)
  const hostile = mkPkg((p) => { void p; });
  const cat = readJson(path.join(hostile, 'retired-artifacts.json'));
  cat.entries.push({ path: '.mcp.json', kind: 'file', retired_in: '9.9.9', reason: 'x' });
  writeJson(path.join(hostile, 'retired-artifacts.json'), cat);
  const pf = fixture();
  writeJson(path.join(pf.cwd, '.mcp.json'), { mcpServers: {} });
  const pfBefore = snap(pf.cwd, []);
  const pfRun = await run(pf, { argv: ['--dry-run', '--cleanup'], pkgDir: hostile });
  assert.equal(pfRun.res.exitCode, 1);
  assert.match(pfRun.text, /refused: refusing: a planned write targets the protected path \.mcp\.json/);
  assert.deepEqual(snap(pf.cwd, []), pfBefore);
  const pfReal = await run(pf, { argv: ['--cleanup'], pkgDir: hostile });
  assert.equal(pfReal.res.exitCode, 1);
  assert.equal(pfReal.report.outcome, 'refused');
  assert.ok(fs.existsSync(path.join(pf.cwd, '.mcp.json')), 'a protected path was quarantined');
  assert.deepEqual(pfReal.log, []);

  // --help names the 1/2/0 mapping
  const help = await run(fixture(), { argv: ['--help'] });
  assert.equal(help.res.exitCode, 0);
  assert.match(help.text, /Exits 1 if a pre-flight stop check fires, 2 if a pre-flight continue check fires/);
  assert.match(help.text, /else 0\./);
  assert.match(help.text, /0 complete, 2 attention-needed/);
});

// ================================================================================================
// AC-NPX-5
// ================================================================================================

/** An INDEPENDENT reading of "the parsed-JSON diff of a settings file": one string per change. */
function independentDiff(file, before, after) {
  const out = [];
  const b = before || {};
  const a = after || {};
  const kind = (x, y) => (x === undefined ? 'added' : y === undefined ? 'removed' : 'changed');
  for (const k of new Set([...Object.keys(b), ...Object.keys(a)])) {
    if (isDeepStrictEqual(b[k], a[k])) continue;
    if (k === 'permissions') {
      const bp = b[k] || {};
      const ap = a[k] || {};
      for (const tier of ['allow', 'ask', 'deny']) {
        for (const rule of ap[tier] || []) if (!(bp[tier] || []).includes(rule)) out.push(`${file}|permissions.${tier}|added|${rule}`);
        for (const rule of bp[tier] || []) if (!(ap[tier] || []).includes(rule)) out.push(`${file}|permissions.${tier}|removed|${rule}`);
      }
      for (const pk of new Set([...Object.keys(bp), ...Object.keys(ap)])) {
        if (['allow', 'ask', 'deny'].includes(pk) || isDeepStrictEqual(bp[pk], ap[pk])) continue;
        out.push(`${file}|permissions.${pk}|${kind(bp[pk], ap[pk])}|`);
      }
    } else if (k === 'enabledPlugins' || k === 'extraKnownMarketplaces') {
      const bo = b[k] || {};
      const ao = a[k] || {};
      for (const c of new Set([...Object.keys(bo), ...Object.keys(ao)])) {
        if (!isDeepStrictEqual(bo[c], ao[c])) out.push(`${file}|${k}.${c}|${kind(bo[c], ao[c])}|`);
      }
    } else {
      out.push(`${file}|${k}|${kind(b[k], a[k])}|`);
    }
  }
  return out;
}
const rowKey = (r) => `${r.file}|${r.key}|${r.change}|${r.rule || ''}`;

test('AC-NPX-5 every settings key change in all three files is listed with its impact', async () => {
  const fx = richFixture({ user: true });
  const files = {
    '.claude/settings.json': path.join(fx.cwd, '.claude', 'settings.json'),
    '.claude/settings.local.json': path.join(fx.cwd, '.claude', 'settings.local.json'),
    '<config-dir>/settings.json': path.join(fx.configDir, 'settings.json'),
  };
  const before = Object.fromEntries(Object.entries(files).map(([k, p]) => [k, readJson(p)]));
  assert.equal(before['<config-dir>/settings.json'].extraKnownMarketplaces[MARKETPLACE].source.ref, 'v1.5.0');
  const r = await run(fx);
  assert.notEqual(r.res.exitCode, 1, r.text);
  const after = Object.fromEntries(Object.entries(files).map(([k, p]) => [k, readJson(p)]));

  // the preview: one row per planned NON-claude change, under the heading, before the first mutation
  const { review } = previewRows(r.text);
  const rows = review.map((l) => parseRow(l));
  assert.ok(rows.every((x) => x && x.effect === 'review'), review.join('\n'));
  assert.equal(rows.length, 8, review.join('\n'));
  const want = [
    /\.claude\/settings\.json#permissions\.deny impact=narrows added tier=deny rule=Bash\(gh pr merge --admin:\*\)/,
    /\.claude\/settings\.json#permissions\.deny impact=narrows added tier=deny rule=Bash\(docker system prune:\*\)/,
    /\.claude\/settings\.json#permissions\.deny impact=narrows added tier=deny rule=Bash\(tofu destroy -auto-approve:\*\)/,
    /\.claude\/settings\.json#statusLine impact=widens added/,
    /\.claude\/settings\.json#subagentStatusLine impact=narrows removed/,
    /settings\.local\.json#permissions\.allow impact=narrows removed tier=allow/,
    /settings\.local\.json#permissions\.ask impact=widens removed tier=ask/,
    /settings\.local\.json#permissions\.deny impact=widens removed tier=deny rule=Write\(\.foundry\/permissions\.yaml\)/,
  ];
  for (const re of want) assert.equal(review.filter((l) => re.test(l)).length, 1, `${re}\n${review.join('\n')}`);
  assert.ok(!review.some((l) => /extraKnownMarketplaces/.test(l)), 'a change claude makes is previewed as its invoke row, not here');
  assert.ok(r.text.includes('claude plugin marketplace remove agentic-foundry --scope user'));

  // the report: SET-EQUAL to the parsed-JSON diff of all three files
  const expected = Object.keys(files).flatMap((k) => independentDiff(k, before[k], after[k])).sort();
  assert.deepEqual(r.report.operator_review.map(rowKey).sort(), expected);
  assert.ok(expected.some((e) => e.startsWith('<config-dir>/settings.json|extraKnownMarketplaces.agentic-foundry|changed')), 'the user-scope migration is reported');
  assert.ok(expected.some((e) => e.startsWith('.claude/settings.json|extraKnownMarketplaces.agentic-foundry|changed')), 'the project-scope migration is reported');

  // impact tags
  const pick = (file, key, rule) => r.report.operator_review.find((x) => x.file === file && x.key === key && (rule === undefined || x.rule === rule));
  assert.equal(pick('.claude/settings.json', 'permissions.deny', 'Bash(gh pr merge --admin:*)').impact, 'narrows');
  assert.equal(pick('.claude/settings.local.json', 'permissions.allow').impact, 'narrows');
  assert.equal(pick('.claude/settings.local.json', 'permissions.ask').impact, 'widens');
  assert.equal(pick('.claude/settings.local.json', 'permissions.deny').impact, 'widens');
  assert.equal(pick('.claude/settings.json', 'statusLine').impact, 'widens');
  assert.equal(pick('.claude/settings.json', 'subagentStatusLine').impact, 'narrows');
  assert.equal(pick('<config-dir>/settings.json', 'extraKnownMarketplaces.agentic-foundry').impact, 'widens');
  for (const x of r.report.operator_review) assert.ok(['widens', 'narrows', 'neutral'].includes(x.impact));

  // a //-prefixed comment key is neutral; removing an allow rule narrows; a plugin disabled narrows
  const unit = diffSettingsFile('f', { '//note': 'a', enabledPlugins: { x: true }, permissions: { allow: ['A'] } },
    { '//note': 'b', enabledPlugins: { x: false }, permissions: { allow: [] } });
  assert.deepEqual(unit.map((u) => [u.key, u.impact]).sort(), [['//note', 'neutral'], ['enabledPlugins.x', 'narrows'], ['permissions.allow', 'narrows']]);
});

// ================================================================================================
// AC-NPX-6
// ================================================================================================

test('AC-NPX-6 changed paths are a subset of previewed writes and exclude protected and history paths', async () => {
  const recorded = 'planning_authority: github\nrecorded_by: op_x\n';
  const arms = [
    { name: 'default', argv: [], cutover: null },
    { name: 'legacy+pending', argv: ['--legacy-maintenance'], cutover: null, expectLegacy: true },
    { name: 'cleanup', argv: ['--cleanup'], cutover: null, expectQuarantine: true },
    { name: 'legacy+cleanup', argv: ['--legacy-maintenance', '--cleanup'], cutover: null, expectLegacy: true, expectQuarantine: true },
    { name: 'legacy+recorded', argv: ['--legacy-maintenance', '--cleanup'], cutover: recorded, expectQuarantine: true },
    { name: 'absent managed paths', argv: ['--cleanup'], cutover: null, deleteFirst: ['.foundry/permissions.yaml', 'specs/features/README.md'], expectQuarantine: true },
  ];
  const catalogue = loadRetiredCatalogue(CLI_DIR).entries.map((e) => e.path);
  for (const arm of arms) {
    const fx = richFixture({ cutover: arm.cutover });
    for (const rel of arm.deleteFirst || []) fs.rmSync(path.join(fx.cwd, rel), { force: true });
    const before = snap(fx.cwd);
    const r = await run(fx, { argv: arm.argv });
    assert.notEqual(r.res.exitCode, 1, `${arm.name}: ${r.text}`);
    const changed = changedPaths(before, snap(fx.cwd));
    const rows = previewRows(r.text).effects.map(parseRow);
    const targets = rows.filter((x) => WRITE_EFFECTS.includes(x.effect)).flatMap((x) => x.target.split(' '));
    const legacyTargets = rows.filter((x) => x.effect.startsWith('legacy-')).map((x) => x.target);
    const covered = (p) => targets.some((t) => p === t || p.startsWith(`${t}/`));
    const protectedSet = [...PROTECTED_BASE, CUTOVER_RECORD_REL];
    for (const p of changed) {
      assert.ok(covered(p), `${arm.name}: ${p} changed but no previewed write row names it`);
      const absentDeclared = DECLARED_PATH_SET.includes(p) && !(p in before);
      const isProtected = protectedSet.some((x) => p === x || p.startsWith(`${x}/`));
      if (isProtected) assert.ok(absentDeclared, `${arm.name}: protected path ${p} changed`);
      if (classOf(TABLE, p, { catalogueEntries: catalogue }) === 'retained-history') {
        assert.ok(absentDeclared || legacyTargets.includes(p), `${arm.name}: retained-history path ${p} changed outside the two history writers`);
      }
    }
    // the outcome-level facts, on observed bytes
    const spec = 'specs/features/p/d/c/feat-old.md';
    const manifest = '.foundry/releases/r1/release.yaml';
    assert.equal(changed.includes(spec), Boolean(arm.expectLegacy), `${arm.name}: spec`);
    assert.equal(changed.includes(manifest), Boolean(arm.expectLegacy), `${arm.name}: manifest`);
    for (const p of ['specs/features/p/d/c/acceptance-contract.yaml', '.foundry/authorized/a.yaml', '.foundry/decisions/d.md',
      '.claude/foundry-project.json', '.claude/foundry-operators.json', '.mcp.json']) {
      assert.ok(!changed.includes(p), `${arm.name}: ${p} changed`);
    }
    assert.equal(changed.includes('.foundry/wiring-hash.pin'), Boolean(arm.expectQuarantine), `${arm.name}: quarantine`);
    if (arm.cutover) assert.equal(fs.readFileSync(path.join(fx.cwd, CUTOVER_RECORD_REL), 'utf-8'), arm.cutover);
    for (const rel of arm.deleteFirst || []) assert.ok(changed.includes(rel) && fs.existsSync(path.join(fx.cwd, rel)), `${arm.name}: ${rel} not re-created`);
    if (arm.deleteFirst) assert.ok(!changed.includes('.mcp.json'));
    assert.ok(r.report.adoption.cutover === (arm.cutover ? 'recorded' : 'pending'));
  }
});

// ================================================================================================
// AC-NPX-7
// ================================================================================================

const V2_FIELDS = ['run_id', 'outcome', 'next_action', 'failure', 'previous_partial', 'cleanup', 'operator_review', 'compatibility', 'adoption'];
const planted = (report) => JSON.stringify(V2_FIELDS.map((k) => report[k]));

test('AC-NPX-7 the report and last line carry only closed set values and no planted secret', async () => {
  // a user-scope registration whose source carries userinfo, a query and a fragment
  const url = `https://op:${PLANTED}@example.invalid/x.git?token=${PLANTED}#${PLANTED}`;
  const fx = fixture({ user: true, userEntry: { source: { source: 'url', url }, autoUpdate: false } });
  const r = await run(fx);
  assert.equal(r.res.exitCode, 1);
  assert.ok(r.report.compatibility.diagnoses.some((d) => d.code === 'foreign-marketplace-source'));
  const rec = r.report.compatibility.sources.find((s) => s.location === '<config-dir>/settings.json');
  assert.deepEqual(rec, { location: '<config-dir>/settings.json', scope: 'user', source_type: 'url', repo_or_url: 'https://example.invalid/x.git', auto_update: false });
  assert.ok(!planted(r.report).includes(PLANTED), 'the planted string reached a v2 field');
  assert.ok(!r.last.includes(PLANTED));
  assert.ok(!JSON.stringify(r.report).includes(PLANTED), 'the planted string reached the report');

  // a failure whose message is the secret: the error's code property is all that survives
  const fx2 = fixture();
  const f = await run(fx2, { failAfterPhase: { phase: 'plugin-update', message: PLANTED } });
  assert.equal(f.res.exitCode, 1);
  assert.ok(f.text.includes(PLANTED), 'the injected failure did not fire');
  assert.equal(f.report.outcome, 'partial');
  assert.deepEqual(f.report.failure, { phase: 'plugin-update', code: null });
  assert.ok(!planted(f.report).includes(PLANTED));
  assert.ok(!f.last.includes(PLANTED));
  assert.ok(!JSON.stringify(f.report).includes(PLANTED));

  // env / hooks values are digests, never the value; a registration value is URL-sanitised
  const rows = diffSettingsFile('.claude/settings.json', { env: { API_TOKEN: 'old-value' } }, { env: { API_TOKEN: PLANTED } });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].key, 'env');
  assert.match(rows[0].after, /^sha256:[0-9a-f]{64}$/);
  assert.match(rows[0].before, /^sha256:[0-9a-f]{64}$/);
  assert.ok(!JSON.stringify(rows).includes(PLANTED) && !JSON.stringify(rows).includes('old-value'));
  const hooks = diffSettingsFile('f', {}, { hooks: { PreToolUse: [{ command: `curl ${PLANTED}` }] } });
  assert.match(hooks[0].after, /^sha256:/);
  assert.ok(!JSON.stringify(hooks).includes(PLANTED));
  const reg = diffSettingsFile('f', {}, { extraKnownMarketplaces: { m: { source: { source: 'url', url } } } });
  assert.ok(!JSON.stringify(reg).includes(PLANTED), 'a registration value is URL-sanitised');
  const leaf = diffSettingsFile('f', {}, { extraKnownMarketplaces: { m: { source: { source: 'github', repo: 'a/b', ref: 'v1.0.0' }, note: PLANTED, autoUpdate: false } } });
  assert.ok(!JSON.stringify(leaf).includes(PLANTED), 'a non-URL string leaf outside source/repo/ref/path is a digest');
  assert.deepEqual(leaf[0].after.source, { ref: 'v1.0.0', repo: 'a/b', source: 'github' });
  assert.match(leaf[0].after.note, /^sha256:/);

  // every version in next_action and the last line is strict semver
  const strictOnly = (text) => {
    for (const v of text.match(/\d+\.\d+\.\d+[-+.\w]*/g) || []) assert.match(v, /^\d+\.\d+\.\d+$/, `non-strict version in ${text}`);
  };
  const unsup = await run(fixture({ versions: { project: '1.9.0' } }), { pkgDir: mkPkg((p) => { p.foundry.upgrade.supported_from_min = '1.10.0'; }) });
  assert.match(unsup.report.next_action, /1\.9\.0/);
  strictOnly(unsup.report.next_action);
  strictOnly(unsup.last);
  // a pre-release installed version is unreadable, and is never echoed
  const rc = await run(fixture({ versions: { project: '1.9.0-rc.1' } }), { pkgDir: mkPkg((p) => { p.foundry.upgrade.supported_from_min = '1.10.0'; }) });
  assert.equal(rc.report.compatibility.from_plugin_version, null);
  assert.ok(!JSON.stringify(V2_FIELDS.map((k) => rc.report[k])).includes('1.9.0-rc.1'));
  strictOnly(rc.last);

  // a planted previous report: an entry that fails the closed set is dropped
  const fx3 = fixture({ projectEntry: { source: { source: 'git', url: FOREIGN_URL } } });
  const rid = '20261101T101010Z-ab12';
  writeJson(path.join(fx3.cwd, REPORT_REL), {
    schema_version: 2, outcome: 'refused', run_id: '20261102T101010Z-cd34',
    previous_partial: {
      run_id: rid,
      phases: [{ name: 'plugin-update', verdict: 'changed' }, { name: PLANTED, verdict: 'changed' }, { name: 'cleanup', verdict: PLANTED }],
      written: [{ path: 'a.md', kind: 'managed' }, { path: 'b.md', kind: PLANTED }, { path: '/etc/passwd', kind: 'managed' }, { path: '../x', kind: 'managed' }],
      cleanup: [
        { path: '.foundry/x', referrers: ['ok.md', `${PLANTED} bad path!`], state: 'moved', restore_from: '.foundry/retired/r/x' },
        { path: 'q', referrers: [], state: PLANTED, restore_from: null },
      ],
    },
  });
  const pp = await run(fx3);
  assert.equal(pp.report.outcome, 'refused');
  assert.deepEqual(pp.report.previous_partial, {
    run_id: rid,
    phases: [{ name: 'plugin-update', verdict: 'changed' }],
    written: [{ path: 'a.md', kind: 'managed' }],
    cleanup: [{ path: '.foundry/x', referrers: ['ok.md'], state: 'moved', restore_from: '.foundry/retired/r/x' }],
  });
  assert.ok(!JSON.stringify(pp.report).includes(PLANTED));
});

// ================================================================================================
// AC-NPX-8
// ================================================================================================

test('AC-NPX-8 the cutover state is recorded pending or unknown and fails closed', async () => {
  const RECORDED = 'planning_authority: github\nrecorded_by: op_x\n';
  const specRel = 'specs/features/p/d/c/feat-old.md';
  const migDir = (cwd) => fs.mkdirSync(path.join(cwd, '.foundry', 'migration'), { recursive: true });
  const arms = [
    { name: 'absent', want: 'pending', setup: () => {} },
    { name: 'recorded', want: 'recorded', setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), RECORDED) },
    { name: 'recorded, quoted values and trailing comment', want: 'recorded',
      setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), 'schema: 1\nplanning_authority: "github"  # decided\nrecorded_by: \'op_x\'\n') },
    { name: 'empty file', want: 'unknown', setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), '') },
    { name: 'garbage bytes', want: 'unknown', setup: (cwd) => { migDir(cwd); fs.writeFileSync(path.join(cwd, CUTOVER_RECORD_REL), Buffer.from([0xff, 0xfe, 0x00, 0x9c, 0x01])); } },
    { name: 'planning_authority local', want: 'unknown', setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), 'planning_authority: local\nrecorded_by: op_x\n') },
    { name: 'empty recorded_by', want: 'unknown', setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), 'planning_authority: github\nrecorded_by:\n') },
    { name: 'comment-only recorded_by', want: 'unknown', setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), 'planning_authority: github\nrecorded_by: # nobody\n') },
    { name: 'indented (not top-level) keys', want: 'unknown', setup: (cwd) => write(path.join(cwd, CUTOVER_RECORD_REL), 'x:\n  planning_authority: github\n  recorded_by: op_x\n') },
    { name: 'a directory at the path', want: 'unknown', setup: (cwd) => fs.mkdirSync(path.join(cwd, CUTOVER_RECORD_REL), { recursive: true }) },
    { name: 'a symlinked record', want: 'unknown', setup: (cwd) => {
      write(path.join(cwd, 'elsewhere.yaml'), RECORDED);
      migDir(cwd);
      fs.symlinkSync(path.join(cwd, 'elsewhere.yaml'), path.join(cwd, CUTOVER_RECORD_REL));
    } },
    { name: 'a symlinked .foundry/migration ancestor', want: 'unknown', setup: (cwd) => {
      write(path.join(cwd, 'other-mig', 'authority.yaml'), RECORDED);
      fs.mkdirSync(path.join(cwd, '.foundry'), { recursive: true });
      fs.symlinkSync(path.join(cwd, 'other-mig'), path.join(cwd, '.foundry', 'migration'));
    } },
    { name: 'an unreadable (0o000) record', want: 'unknown', skipIfRoot: true, setup: (cwd) => {
      write(path.join(cwd, CUTOVER_RECORD_REL), RECORDED);
      fs.chmodSync(path.join(cwd, CUTOVER_RECORD_REL), 0o000);
    } },
  ];
  for (const arm of arms) {
    if (arm.skipIfRoot && process.getuid && process.getuid() === 0) continue;
    const fx = fixture();
    write(path.join(fx.cwd, specRel), SPEC_NORMATIVE);
    arm.setup(fx.cwd);
    assert.equal(readCutoverState(fx.cwd), arm.want, `${arm.name}: readCutoverState`);
    const r = await run(fx, { argv: ['--legacy-maintenance'] });
    assert.notEqual(r.res.exitCode, 1, `${arm.name}: ${r.text}`);
    assert.equal(r.report.adoption.cutover, arm.want, arm.name);
    assert.match(r.last, new RegExp(` · cutover: ${arm.want}$`), arm.name);
    const spec = fs.readFileSync(path.join(fx.cwd, specRel), 'utf-8');
    if (arm.want === 'pending') assert.ok(spec.includes('## Amendments'), `${arm.name}: the writers should run on a pending cutover`);
    else assert.equal(spec, SPEC_NORMATIVE, `${arm.name}: the legacy writers must stay off`);
  }

  // instructions_pending: a planted retired term with path+line+term; a clean AGENTS.md is not listed
  const fx = fixture();
  write(path.join(fx.cwd, 'CLAUDE.md'), '# p\n\nWe still run /foundry:command-deck here.\n');
  write(path.join(fx.cwd, 'AGENTS.md'), '# agents\nnothing retired\n');
  write(path.join(fx.cwd, '.claude', 'notes.md'), 'see the wiring-hash pin\n');
  const r = await run(fx);
  assert.deepEqual(r.report.adoption.instructions_pending, [
    { path: 'CLAUDE.md', line: 3, term: 'foundry:command-deck' },
    { path: '.claude/notes.md', line: 1, term: 'wiring-hash' },
  ]);
  assert.ok(!r.report.adoption.instructions_pending.some((x) => x.path === 'AGENTS.md'));
  assert.equal(r.res.exitCode, 0, 'a pending instruction is adoption work, not an attention trigger');
});

// ================================================================================================
// AC-NPX-9
// ================================================================================================

function historyFixture() {
  const fx = fixture();
  write(path.join(fx.cwd, 'specs', 'features', 'p', 'd', 'c', 'feat-old.md'), SPEC_NORMATIVE);
  write(path.join(fx.cwd, '.foundry', 'releases', 'r1', 'release.yaml'), 'id: r1\nstate: released\natoms: []\n');
  return fx;
}
const HISTORY = ['specs/features/p/d/c/feat-old.md', '.foundry/releases/r1/release.yaml'];
const bytesOf = (cwd) => Object.fromEntries(HISTORY.map((rel) => [rel, fs.readFileSync(path.join(cwd, rel), 'utf-8')]));

test('AC-NPX-9 history maintenance runs only under the flag and before cutover', async () => {
  const RECORDED = 'planning_authority: github\nrecorded_by: op_x\n';
  // the denied modules' own apply, on a copy
  const expectedFor = (fx, { trueUp }) => {
    const copy = scratch('npx20-copy-');
    fs.cpSync(fx.cwd, copy, { recursive: true });
    applyAmendmentsBackfill(planAmendmentsBackfill({ physicalRoot: copy }));
    if (trueUp) applyManifestTrueUp(planManifestTrueUp({ physicalRoot: copy }));
    return bytesOf(copy);
  };

  // updater: flag + pending -> both writers, byte-equal to the modules' own output
  let fx = historyFixture();
  const original = bytesOf(fx.cwd);
  const expected = expectedFor(fx, { trueUp: true });
  assert.notDeepEqual(expected, original, 'the fixture gives the writers work');
  let r = await run(fx, { argv: ['--legacy-maintenance'] });
  assert.notEqual(r.res.exitCode, 1, r.text);
  assert.deepEqual(bytesOf(fx.cwd), expected);
  assert.ok(r.report.written.some((w) => w.kind === 'amendments-backfill') && r.report.written.some((w) => w.kind === 'manifest-true-up'));

  // updater: each way of being off leaves every byte alone and the preview names the condition
  const offArms = [
    { name: 'default run', argv: [], cutover: null, condition: 'flag absent' },
    { name: 'flag + recorded', argv: ['--legacy-maintenance'], cutover: RECORDED, condition: 'cutover recorded' },
    { name: 'flag + unknown', argv: ['--legacy-maintenance'], cutover: '', condition: 'cutover unknown' },
  ];
  for (const arm of offArms) {
    fx = historyFixture();
    if (arm.cutover !== null) write(path.join(fx.cwd, CUTOVER_RECORD_REL), arm.cutover);
    r = await run(fx, { argv: arm.argv });
    assert.notEqual(r.res.exitCode, 1, `${arm.name}: ${r.text}`);
    assert.deepEqual(bytesOf(fx.cwd), original, `${arm.name}: history bytes changed`);
    const preview = r.text.slice(0, r.text.indexOf('planned effects'));
    assert.ok(preview.includes(`[legacy-maintenance] off — ${arm.condition}`), `${arm.name}: the preview must name "${arm.condition}"`);
    assert.ok(previewRows(r.text).lines.some((l) => l === `history maintenance: off — ${arm.condition}`), arm.name);
    assert.ok(!r.report.written.some((w) => ['amendments-backfill', 'manifest-true-up'].includes(w.kind)), arm.name);
  }

  // bootstrap (`--existing`): the Amendments backfill only, behind the same gate
  const bootstrap = async (fx2, extra) => {
    const chunks = [];
    const res = await runCli(['--dir', fx2.cwd, '--existing', '--yes', '--gh-account', '', ...extra], {
      cwd: path.dirname(fx2.cwd), isTTY: false, input: process.stdin, output: { write: (s) => chunks.push(s) }, homeDir: os.homedir(), pkgDir: CLI_DIR,
    });
    return { ...res, text: chunks.join('') };
  };
  fx = historyFixture();
  const bexpected = expectedFor(fx, { trueUp: false });
  let b = await bootstrap(fx, ['--legacy-maintenance']);
  assert.notEqual(b.exitCode, 1, b.text);
  assert.deepEqual(bytesOf(fx.cwd), bexpected);
  assert.notEqual(bexpected[HISTORY[0]], original[HISTORY[0]]);
  assert.equal(bexpected[HISTORY[1]], original[HISTORY[1]], 'the bootstrap never trues up manifests');
  for (const arm of offArms) {
    fx = historyFixture();
    if (arm.cutover !== null) write(path.join(fx.cwd, CUTOVER_RECORD_REL), arm.cutover);
    b = await bootstrap(fx, arm.argv);
    assert.notEqual(b.exitCode, 1, `${arm.name}: ${b.text}`);
    assert.deepEqual(bytesOf(fx.cwd), original, `bootstrap ${arm.name}`);
    assert.ok(b.text.includes(`Amendments backfill not run: ${arm.condition}`), `bootstrap ${arm.name}: ${b.text}`);
  }
});

// ================================================================================================
// AC-NPX-11
// ================================================================================================

/** A fake interpreter: the dependency probe always reports both modules missing and pip always fails. */
function fakePython() {
  const dir = scratch('npx20-py-');
  const p = path.join(dir, 'python3');
  fs.writeFileSync(p, `#!/bin/sh
case "$1" in
  -c) echo '{"python":"/fake/python3","version":"3.12","venv":true,"missing":["yaml","jsonschema"]}'; exit 0;;
  *) echo "pip failed" >&2; exit 1;;
esac
`);
  fs.chmodSync(p, 0o755);
  return p;
}

test('AC-NPX-11 each outcome rule has one exit code and the last line names next action and cutover', async () => {
  const lastOf = (outcome, next, cutover = 'pending') => `outcome: ${outcome} · next: ${next} · cutover: ${cutover}`;

  // refused: a pre stop row — zero workspace changes, yet the report IS written
  let fx = fixture({ projectEntry: { source: { source: 'git', url: FOREIGN_URL } } });
  let before = snap(fx.cwd);
  let r = await run(fx);
  assert.equal(r.res.exitCode, 1);
  assert.equal(r.report.outcome, 'refused');
  assert.deepEqual(changedPaths(before, snap(fx.cwd)), []);
  assert.equal(r.last, lastOf('refused', r.report.next_action));
  assert.match(r.report.next_action, /^remove the marketplace registration at \.claude\/settings\.json \(project\)/);
  assert.deepEqual(r.report.failure, { phase: 'preflight', code: 'foreign-marketplace-source' });
  assert.ok(r.text.indexOf('planned effects') !== -1 && r.text.indexOf('planned effects') < r.text.indexOf('refused: foreign-marketplace-source'),
    'a refused run still shows the plan before the stop');

  // refused: a stale updater with no migration (the marketplace refresh alone is not a mutation)
  fx = fixture({ manifestVersion: '99.0.0' });
  before = snap(fx.cwd);
  r = await run(fx);
  assert.equal(r.res.exitCode, 1);
  assert.equal(r.report.outcome, 'refused');
  assert.deepEqual(changedPaths(before, snap(fx.cwd)), []);
  assert.equal(r.last, lastOf('refused', 'npx update-agentic-workspace@latest'));
  assert.deepEqual(r.log.map((a) => a.slice(0, 3).join(' ')), ['plugin marketplace update']);
  assert.deepEqual(r.report.failure, { phase: 'plugin-update', code: 'stale-updater' });

  // partial: an injected stop after plugin-update — completed phases and failure {phase, code}
  fx = fixture();
  r = await run(fx, { failAfterPhase: { phase: 'plugin-update', message: 'boom' } });
  assert.equal(r.res.exitCode, 1);
  assert.equal(r.report.outcome, 'partial');
  assert.deepEqual(r.report.phases.map((p) => p.name), ['marketplace-refresh', 'plugin-update']);
  assert.deepEqual(r.report.failure, { phase: 'plugin-update', code: null });
  assert.deepEqual(r.report.adoption.mechanical_steps.map((s) => s.verdict !== 'not-run'), [true, true, false, false, false]);
  assert.equal(r.last, lastOf('partial', 'fix the cause named in failure, then rerun npx update-agentic-workspace'));
  assert.equal(r.report.previous_partial, null);

  // partial: a stale-updater stop AFTER a project-scope migration changed the settings file
  fx = fixture({ projectEntry: tagPinned(), manifestVersion: '99.0.0' });
  r = await run(fx);
  assert.equal(r.res.exitCode, 1);
  assert.equal(r.report.outcome, 'partial');
  assert.ok(r.log.some((a) => a[2] === 'remove'), 'the migration ran');
  assert.ok(!r.log.some((a) => a[1] === 'update'), 'no plugin update after the stop');
  assert.equal(r.last, lastOf('partial', 'npx update-agentic-workspace@latest'));
  assert.deepEqual(r.report.failure, { phase: 'plugin-update', code: 'stale-updater' });
  assert.equal(r.report.previous_partial, null, 'only a refused run carries previous_partial');

  // attention-needed: each existing exit-2 trigger, separately
  const attention = async (name, fx2, opts = {}) => {
    const rr = await run(fx2, opts);
    assert.equal(rr.res.exitCode, 2, `${name}: ${rr.text}`);
    assert.equal(rr.report.outcome, 'attention-needed', name);
    assert.equal(rr.last, lastOf('attention-needed', rr.report.next_action), name);
    assert.ok(rr.report.adoption.mechanical_steps.every((s) => s.verdict !== 'not-run'), name);
    return rr;
  };
  await attention('a continue row', fixture({ registry: false }));
  fx = fixture();
  fs.mkdirSync(path.join(fx.cwd, 'CLAUDE.md'));
  r = await attention('a drifted managed file', fx);
  assert.deepEqual(r.report.drifted, ['CLAUDE.md']);
  assert.equal(r.report.next_action, `fix CLAUDE.md (not a regular file) and rerun npx update-agentic-workspace; then ${HANDOFF}`);
  // a symlinked managed file is named the same way (never an unexplained permanent exit 2)
  fx = fixture();
  write(path.join(fx.cwd, 'elsewhere.md'), 'x');
  fs.symlinkSync(path.join(fx.cwd, 'elsewhere.md'), path.join(fx.cwd, 'CLAUDE.md'));
  r = await attention('a symlinked managed file', fx);
  assert.match(r.last, /next: fix CLAUDE\.md \(not a regular file\) and rerun/);
  assert.equal(fs.readFileSync(path.join(fx.cwd, 'elsewhere.md'), 'utf-8'), 'x');
  assert.equal(r.report.compatibility.diagnoses.length, 0, 'no continue row fired: drift alone makes the run attention-needed');
  fx = fixture();
  write(path.join(fx.cwd, '.gitignore'), '# FOUNDRY-RUNTIME-GITIGNORE-BEGIN\nnode_modules\n');
  r = await attention('a refused gitignore block', fx);
  assert.match(r.text, /\[refused] \.gitignore/);
  fx = fixture();
  r = await attention('python-deps failed', fx, { env: { FOUNDRY_PYTHON: fakePython() } });
  assert.equal(r.report.python_deps.verdict, 'failed');
  assert.equal(r.report.compatibility.diagnoses.length, 0);

  // not a workspace: typed refusal, nothing created, no report
  const bare = scratch('npx20-bare-');
  const bareCfg = scratch('npx20-barecfg-');
  const nw = await run({ cwd: bare, configDir: bareCfg });
  assert.equal(nw.res.exitCode, 1);
  assert.equal(nw.report, null);
  assert.deepEqual(fs.readdirSync(bare), []);
  assert.match(nw.last, /^outcome: refused · next: run npx update-agentic-workspace from inside an installed workspace · cutover: pending$/);

  // complete
  fx = fixture();
  r = await run(fx);
  assert.equal(r.res.exitCode, 0, r.text);
  assert.equal(r.report.outcome, 'complete');
  assert.equal(r.last, lastOf('complete', HANDOFF));
  assert.equal(r.report.failure, null);

  // previous_partial is carried across TWO successive refused reruns after a partial
  fx = fixture();
  const first = await run(fx, { failAfterPhase: { phase: 'reinitialization', message: 'boom' } });
  assert.equal(first.report.outcome, 'partial');
  assert.ok(first.report.written.length > 0);
  writeJson(path.join(fx.cwd, '.claude', 'settings.json'), {
    ...readJson(path.join(fx.cwd, '.claude', 'settings.json')),
    extraKnownMarketplaces: { [MARKETPLACE]: { source: { source: 'url', url: FOREIGN_URL } } },
  });
  const second = await run(fx);
  assert.equal(second.report.outcome, 'refused');
  assert.equal(second.report.previous_partial.run_id, first.report.run_id);
  assert.deepEqual(second.report.previous_partial.phases.map((p) => p.name), first.report.phases.map((p) => p.name));
  assert.deepEqual(second.report.previous_partial.written, first.report.written);
  assert.notEqual(second.report.run_id, first.report.run_id);
  const third = await run(fx);
  assert.equal(third.report.outcome, 'refused');
  assert.deepEqual(third.report.previous_partial, second.report.previous_partial);

  // a symlinked .foundry pointing outside the root: no report, nothing written outside, refused (no mutation)
  fx = fixture();
  const outside = scratch('npx20-outside-');
  fs.symlinkSync(outside, path.join(fx.cwd, '.foundry'));
  r = await run(fx);
  assert.equal(r.res.exitCode, 1);
  assert.equal(r.report, null);
  assert.deepEqual(fs.readdirSync(outside), []);
  assert.equal(r.last, lastOf('refused', 'make .foundry/upgrade-report.json a regular path and re-run — do not run /foundry:post-upgrade until this run writes its report', 'unknown'));
  // ...and one resolving inside the root: the run reaches its end, mutated, and cannot write its report
  fx = fixture();
  fs.mkdirSync(path.join(fx.cwd, 'foundry-real'));
  fs.symlinkSync(path.join(fx.cwd, 'foundry-real'), path.join(fx.cwd, '.foundry'));
  r = await run(fx);
  assert.equal(r.res.exitCode, 1);
  assert.equal(r.report, null);
  assert.match(r.last, /^outcome: partial · next: make \.foundry\/upgrade-report\.json a regular path/);
  assert.match(r.last, /cutover: unknown$/, 'a symlinked .foundry is an unknown cutover');
});

// ================================================================================================
// AC-NPX-12
// ================================================================================================

function retiredFiles(cwd) {
  const out = [];
  const root = path.join(cwd, '.foundry', 'retired');
  if (!fs.existsSync(root)) return out;
  for (const id of fs.readdirSync(root)) {
    const walk = (d) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const abs = path.join(d, e.name);
        if (e.isDirectory()) walk(abs);
        else if (!(d === path.join(root, id) && e.name === 'manifest.json')) out.push({ id, rel: path.relative(path.join(root, id), abs) });
      }
    };
    walk(path.join(root, id));
  }
  return out;
}

test('AC-NPX-12 a rerun after any stop converges to the uninterrupted result', async () => {
  const flags = ['--cleanup'];
  const skip = [REPORT_REL, '.foundry/retired'];
  const clean = richFixture();
  const a = await run(clean, { argv: flags });
  assert.notEqual(a.res.exitCode, 1, a.text);
  const reference = snap(clean.cwd, skip);
  assert.ok(!('.foundry/wiring-hash.pin' in reference), 'the reference run quarantined the stale entry');
  const retiredDirs = [retiredFiles(clean.cwd)];

  for (const phase of ['marketplace-refresh', 'plugin-update', 'python-deps', 'cleanup', 'reinitialization']) {
    const fx = richFixture();
    const interrupted = await run(fx, { argv: flags, failAfterPhase: { phase, message: 'injected stop' } });
    assert.equal(interrupted.res.exitCode, 1, `${phase}: ${interrupted.text}`);
    // the project scope enables the plugin, so the plugin update was spawned (a mutation) by every phase
    assert.equal(interrupted.report.outcome, 'partial', phase);
    assert.equal(interrupted.report.failure.phase, phase);
    const rerun = await run(fx, { argv: flags });
    assert.notEqual(rerun.res.exitCode, 1, `${phase}: ${rerun.text}`);
    assert.deepEqual(snap(fx.cwd, skip), reference, `${phase}: the rerun did not converge`);
    retiredDirs.push(retiredFiles(fx.cwd));
  }

  // a pre-mutation refusal, then the cause removed (the registry lives outside the workspace)
  const fx = richFixture();
  const registryPath = path.join(fx.configDir, 'plugins', 'installed_plugins.json');
  const good = readJson(registryPath);
  const bad = JSON.parse(JSON.stringify(good));
  bad.plugins[KEY][0].version = '99.0.0';
  writeJson(registryPath, bad);
  const startTree = snap(fx.cwd, skip);
  const refused = await run(fx, { argv: flags });
  assert.equal(refused.report.outcome, 'refused');
  assert.deepEqual(snap(fx.cwd, skip), startTree);
  writeJson(registryPath, good);
  const rerun = await run(fx, { argv: flags });
  assert.notEqual(rerun.res.exitCode, 1, rerun.text);
  assert.deepEqual(snap(fx.cwd, skip), reference, 'the rerun after a refusal did not converge');

  // no relative path appears under two run directories — over every workspace above
  for (const files of retiredDirs) {
    const seen = new Map();
    for (const f of files) {
      assert.ok(!seen.has(f.rel) || seen.get(f.rel) === f.id, `${f.rel} is under two run directories`);
      seen.set(f.rel, f.id);
    }
    assert.ok(files.length > 0);
  }
  // an interrupted-then-rerun workspace holds the entry under exactly ONE run directory
  const interruptedAfterQuarantine = richFixture();
  await run(interruptedAfterQuarantine, { argv: flags, failAfterPhase: { phase: 'reinitialization', message: 'x' } });
  await run(interruptedAfterQuarantine, { argv: flags });
  assert.equal(fs.readdirSync(path.join(interruptedAfterQuarantine.cwd, '.foundry', 'retired')).length, 1);

  // no enabled user/project scope: nothing but the (non-mutating) marketplace refresh ran, so an
  // injected stop there is `refused`, and the rerun still converges
  const idle = fixture();
  writeJson(path.join(idle.cwd, '.claude', 'settings.json'), { ...readJson(path.join(idle.cwd, '.claude', 'settings.json')), enabledPlugins: {} });
  const idleStop = await run(idle, { failAfterPhase: { phase: 'marketplace-refresh', message: 'x' } });
  assert.equal(idleStop.res.exitCode, 1);
  assert.equal(idleStop.report.outcome, 'refused');
  assert.deepEqual(idleStop.report.failure, { phase: 'marketplace-refresh', code: null });
  assert.ok(!idleStop.log.some((a) => a[1] === 'update'));
  // ...but pip installing IS a mutation: the same stop after python-deps is `partial`
  const pyDir = scratch('npx20-pyinst-');
  const marker = path.join(pyDir, 'installed');
  const py = path.join(pyDir, 'python3');
  fs.writeFileSync(py, `#!/bin/sh
case "$1" in
  -c) if [ -f "${marker}" ]; then echo '{"python":"/fake/python3","version":"3.12","venv":true,"missing":[]}'; else echo '{"python":"/fake/python3","version":"3.12","venv":true,"missing":["yaml","jsonschema"]}'; fi; exit 0;;
  *) touch "${marker}"; exit 0;;
esac
`);
  fs.chmodSync(py, 0o755);
  const idle2 = fixture();
  writeJson(path.join(idle2.cwd, '.claude', 'settings.json'), { ...readJson(path.join(idle2.cwd, '.claude', 'settings.json')), enabledPlugins: {} });
  const pipStop = await run(idle2, { env: { FOUNDRY_PYTHON: py }, failAfterPhase: { phase: 'python-deps', message: 'x' } });
  assert.equal(pipStop.report.outcome, 'partial');
  assert.deepEqual(pipStop.report.python_deps.installed.sort(), ['jsonschema', 'yaml']);
  assert.ok(!pipStop.log.some((a) => a[1] === 'update'));
  const idleRerun = await run(idle, {});
  assert.notEqual(idleRerun.res.exitCode, 1, idleRerun.text);

  // a settled rerun: nothing written, nothing moved. A catalogue entry something still wires stays
  // LISTED (AC-NPX-13 lists every present entry), as `referenced` with its referrers and no restore_from
  const idem = richFixture();
  const one = await run(idem, { argv: flags });
  assert.equal(one.res.exitCode, 0, one.text);
  assert.ok(one.report.written.length > 0 && one.report.cleanup.some((c) => c.state === 'moved'));
  const treeBetween = snap(idem.cwd, skip);
  const two = await run(idem, { argv: flags });
  assert.equal(two.res.exitCode, 0, two.text);
  assert.deepEqual(two.report.written, []);
  assert.deepEqual(two.report.cleanup, [
    { path: '.foundry/hasher-ref', referrers: ['.github/workflows/ci.yml'], state: 'referenced', restore_from: null },
  ]);
  assert.equal(two.report.cleanup.filter((c) => c.state === 'moved').length, 0);
  assert.deepEqual(snap(idem.cwd, skip), treeBetween);
  // with nothing wired at all the list is empty
  fs.rmSync(path.join(idem.cwd, '.foundry', 'hasher-ref'));
  fs.rmSync(path.join(idem.cwd, '.github'), { recursive: true });
  const three = await run(idem, { argv: flags });
  assert.deepEqual(three.report.cleanup, []);

  // the published bin cannot pass the failure-injection seam: its runUpdate options are exactly seven keys
  const src = fs.readFileSync(BIN_SRC, 'utf-8');
  const call = /runUpdate\(rawArgv,\s*\{([\s\S]*?)\n\}\);/.exec(src);
  assert.ok(call, 'the bin no longer calls runUpdate(rawArgv, { ... })');
  const keys = [...call[1].matchAll(/^ {2}([A-Za-z]+)\s*[:,]/gm)].map((m) => m[1]).sort();
  assert.deepEqual(keys, ['configDir', 'cwd', 'homeDir', 'output', 'pkgDir', 'spawnEnv', 'updaterVersion']);
  assert.ok(!/failAfterPhase/.test(src));
});

// ================================================================================================
// AC-NPX-13
// ================================================================================================

function quarantineRoot() {
  const root = scratch('npx20-q-');
  const sub = (...p) => path.join(root, ...p);
  return { root, sub };
}
const catalogue = () => loadRetiredCatalogue(CLI_DIR);

test('AC-NPX-13 cleanup quarantines only unreferenced entries and refuses any symlinked path component', async () => {
  // ── a real run: an unreferenced directory is MOVED (same inode), a referenced entry stays ───────
  const fx = fixture();
  write(path.join(fx.cwd, '.foundry', 'session-learnings', 'one.md'), 'recovery context\n');
  write(path.join(fx.cwd, '.claude', 'resume', 'two.md'), 'resume me\n');
  write(path.join(fx.cwd, '.foundry', 'hasher-ref'), 'x');
  write(path.join(fx.cwd, '.github', 'workflows', 'ci.yml'), 'run: test -f .foundry/hasher-ref\n');
  const ino = fs.statSync(path.join(fx.cwd, '.foundry', 'session-learnings')).ino;
  const r = await run(fx, { argv: ['--cleanup'] });
  assert.notEqual(r.res.exitCode, 1, r.text);
  const runDir = path.join(fx.cwd, '.foundry', 'retired', r.report.run_id);
  assert.equal(fs.statSync(path.join(runDir, '.foundry', 'session-learnings')).ino, ino, 'the entry was copied, not renamed');
  assert.equal(fs.readFileSync(path.join(runDir, '.foundry', 'session-learnings', 'one.md'), 'utf-8'), 'recovery context\n');
  assert.equal(fs.readFileSync(path.join(runDir, '.claude', 'resume', 'two.md'), 'utf-8'), 'resume me\n');
  assert.ok(!fs.existsSync(path.join(fx.cwd, '.foundry', 'session-learnings')));
  const byPath = Object.fromEntries(r.report.cleanup.map((c) => [c.path, c]));
  const dest = (rel) => `.foundry/retired/${r.report.run_id}/${rel}`;
  assert.deepEqual(byPath['.foundry/session-learnings'], { path: '.foundry/session-learnings', referrers: [], state: 'moved', restore_from: dest('.foundry/session-learnings') });
  assert.deepEqual(byPath['.claude/resume'], { path: '.claude/resume', referrers: [], state: 'moved', restore_from: dest('.claude/resume') });
  assert.deepEqual(byPath['.foundry/hasher-ref'], { path: '.foundry/hasher-ref', referrers: ['.github/workflows/ci.yml'], state: 'referenced', restore_from: null });
  assert.equal(fs.readFileSync(path.join(fx.cwd, '.foundry', 'hasher-ref'), 'utf-8'), 'x', 'a referenced entry stays');
  const manifest = readJson(path.join(runDir, 'manifest.json'));
  assert.equal(manifest.run_id, r.report.run_id);
  assert.deepEqual(manifest.entries.map((e) => e.restore_from).sort(), [dest('.claude/resume'), dest('.foundry/session-learnings')]);
  assert.match(r.text, /\[removed] \.foundry\/session-learnings \[1 entry] — retired in v2\.0\.0; restore from /);
  assert.match(r.text, /\[stale] \.foundry\/session-learnings .*; will be removed; quarantined to \.foundry\/retired\//);
  // restoring is the same move back
  fs.renameSync(path.join(runDir, '.foundry', 'session-learnings'), path.join(fx.cwd, '.foundry', 'session-learnings'));
  assert.equal(fs.statSync(path.join(fx.cwd, '.foundry', 'session-learnings')).ino, ino);

  // ── refusal arms: the entry stays, nothing is written, nothing crosses a class boundary ─────────
  const RID = makeRunId();
  const arm = (name, setup, { expectWhy, replan = false }) => {
    const { root, sub } = quarantineRoot();
    const outside = scratch('npx20-out-');
    fs.mkdirSync(sub('specs', 'x'), { recursive: true });
    fs.mkdirSync(sub('specs', 'y'), { recursive: true });
    fs.mkdirSync(sub('specs', 'z'), { recursive: true });
    const ctx = setup({ root, sub, outside });
    const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: catalogue() });
    const entryRow = plan.rows.find((x) => x.relPath === ctx.entry);
    assert.ok(entryRow, `${name}: no plan row for ${ctx.entry}`);
    if (ctx.afterPlan) ctx.afterPlan();
    const beforeTree = snap(root, []);
    const moved = applyRetiredArtifacts(plan, root, { runId: RID });
    assert.equal(moved, 0, `${name}: something was moved`);
    assert.notEqual(entryRow.state, 'removed', name);
    if (expectWhy) assert.equal(entryRow.why, expectWhy, `${name}: why`);
    assert.deepEqual(snap(root, []), beforeTree, `${name}: the tree changed`);
    assert.deepEqual(fs.readdirSync(outside), [], `${name}: something was written outside the root`);
    assert.deepEqual(fs.readdirSync(sub('specs', 'y')), ctx.yContents || [], `${name}: a class boundary was crossed (specs/y)`);
    assert.deepEqual(fs.readdirSync(sub('specs', 'z')), ctx.zContents || [], `${name}: a class boundary was crossed (specs/z)`);
    if (replan) {
      const again = planRetiredArtifacts({ physicalRoot: root, catalogue: catalogue() }).rows.find((x) => x.relPath === ctx.entry);
      assert.equal(again.state, 'refused', `${name}: a fresh plan must refuse it too`);
    }
    return entryRow;
  };

  // a symlinked entry
  arm('symlinked entry', ({ sub }) => {
    fs.mkdirSync(sub('.claude'), { recursive: true });
    fs.symlinkSync(sub('specs', 'x'), sub('.claude', 'resume'));
    return { entry: '.claude/resume' };
  }, { expectWhy: 'kind' });

  // an in-root symlink planted at the destination
  arm('symlink at the destination', ({ sub }) => {
    write(sub('.foundry', 'session-learnings', 'a.md'), 'a');
    fs.mkdirSync(sub('.foundry', 'retired', RID, '.foundry'), { recursive: true });
    fs.symlinkSync(sub('specs', 'z'), sub('.foundry', 'retired', RID, '.foundry', 'session-learnings'));
    return { entry: '.foundry/session-learnings' };
  }, { expectWhy: 'destination-exists' });

  // an existing destination
  arm('existing destination', ({ sub }) => {
    write(sub('.foundry', 'session-learnings', 'a.md'), 'a');
    write(sub('.foundry', 'retired', RID, '.foundry', 'session-learnings', 'old.md'), 'old');
    return { entry: '.foundry/session-learnings' };
  }, { expectWhy: 'destination-exists' });

  // a symlinked ANCESTOR of the source: `.claude` -> an in-root specs/x dir holding resume/
  const ancestor = arm('symlinked ancestor of the source (swapped after the plan)', ({ root, sub }) => {
    write(sub('.claude', 'resume', 'f.md'), 'f');
    return {
      entry: '.claude/resume',
      afterPlan: () => { fs.rmSync(sub('specs', 'x'), { recursive: true }); fs.renameSync(sub('.claude'), sub('specs', 'x')); fs.symlinkSync(sub('specs', 'x'), sub('.claude')); void root; },
    };
  }, { expectWhy: undefined });
  assert.equal(ancestor.state, 'refused');
  assert.equal(ancestor.why, 'link-in-path');
  // ...and the same shape is refused at plan time
  arm('symlinked ancestor of the source (at plan time)', ({ sub }) => {
    write(sub('specs', 'x', 'resume', 'f.md'), 'f');
    fs.symlinkSync(sub('specs', 'x'), sub('.claude'));
    return { entry: '.claude/resume' };
  }, { expectWhy: 'link-in-path' });

  // a symlinked ANCESTOR of the destination: `.foundry/retired` -> an in-root specs/y
  const destAncestor = arm('symlinked ancestor of the destination', ({ sub }) => {
    write(sub('.foundry', 'session-learnings', 'a.md'), 'a');
    fs.symlinkSync(sub('specs', 'y'), sub('.foundry', 'retired'));
    return { entry: '.foundry/session-learnings' };
  }, { expectWhy: 'link-in-path' });
  assert.equal(destAncestor.state, 'refused');

  const isRoot = process.getuid && process.getuid() === 0;
  if (!isRoot) {
    // one row failing does not lose the rows already moved: the manifest and restore_from still cover them
    const q = quarantineRoot();
    write(q.sub('.claude', 'resume', 'a.md'), 'a');
    write(q.sub('.foundry', 'session-learnings', 'b.md'), 'b');
    const rid = makeRunId();
    fs.mkdirSync(q.sub('.foundry', 'retired', rid, '.foundry'), { recursive: true });
    fs.chmodSync(q.sub('.foundry', 'retired', rid, '.foundry'), 0o555); // the second rename cannot land
    const qplan = planRetiredArtifacts({ physicalRoot: q.root, catalogue: catalogue() });
    const moved = applyRetiredArtifacts(qplan, q.root, { runId: rid });
    fs.chmodSync(q.sub('.foundry', 'retired', rid, '.foundry'), 0o755);
    assert.equal(moved, 1);
    const byRel = Object.fromEntries(qplan.rows.map((x) => [x.relPath, x]));
    assert.equal(byRel['.claude/resume'].state, 'removed');
    assert.equal(byRel['.foundry/session-learnings'].state, 'refused');
    assert.equal(byRel['.foundry/session-learnings'].why, 'remove-failed');
    assert.ok(fs.existsSync(q.sub('.foundry', 'session-learnings', 'b.md')), 'the refused entry stays');
    assert.deepEqual(readJson(q.sub('.foundry', 'retired', rid, 'manifest.json')).entries,
      [{ path: '.claude/resume', restore_from: `.foundry/retired/${rid}/.claude/resume` }]);
    assert.equal(qplan.present, 0);
    assert.equal(qplan.refused, 1);

    // the rollback after a failed rename removes only what THIS call created; a pre-existing directory
    // and everything in it survives
    const r2 = quarantineRoot();
    write(r2.sub('.claude', 'resume', 'a.md'), 'a');
    write(r2.sub('.foundry', 'keep.txt'), 'pre-existing');
    const rp = planRetiredArtifacts({ physicalRoot: r2.root, catalogue: catalogue() });
    fs.chmodSync(r2.sub('.claude'), 0o555); // the source cannot be renamed out of its parent
    const rid2 = makeRunId();
    const n = applyRetiredArtifacts(rp, r2.root, { runId: rid2 });
    fs.chmodSync(r2.sub('.claude'), 0o755);
    assert.equal(n, 0);
    assert.equal(rp.rows.find((x) => x.relPath === '.claude/resume').state, 'refused');
    assert.ok(!fs.existsSync(r2.sub('.foundry', 'retired')), 'the directories this call created were taken back');
    assert.equal(fs.readFileSync(r2.sub('.foundry', 'keep.txt'), 'utf-8'), 'pre-existing');
    assert.equal(fs.readFileSync(r2.sub('.claude', 'resume', 'a.md'), 'utf-8'), 'a');
    // ...and a pre-existing .foundry/retired with another run's content is never touched
    const r3 = quarantineRoot();
    write(r3.sub('.claude', 'resume', 'a.md'), 'a');
    write(r3.sub('.foundry', 'retired', '20260101T000000Z-aaaa', 'old.txt'), 'older run');
    const rp3 = planRetiredArtifacts({ physicalRoot: r3.root, catalogue: catalogue() });
    fs.chmodSync(r3.sub('.claude'), 0o555);
    applyRetiredArtifacts(rp3, r3.root, { runId: makeRunId() });
    fs.chmodSync(r3.sub('.claude'), 0o755);
    assert.equal(fs.readFileSync(r3.sub('.foundry', 'retired', '20260101T000000Z-aaaa', 'old.txt'), 'utf-8'), 'older run');
    assert.deepEqual(fs.readdirSync(r3.sub('.foundry', 'retired')), ['20260101T000000Z-aaaa']);
  }

  // a name from a directory listing that is not a safe path is refused `unsafe-name`, escaped in the
  // preview and never put in the report
  const u = quarantineRoot();
  fs.mkdirSync(u.sub('.claude', 'hooks'), { recursive: true });
  const weird = 'bad\u001bname-exec-guard.sh';
  fs.writeFileSync(path.join(u.root, '.claude', 'hooks', weird), '#!/bin/sh\n');
  const up = planRetiredArtifacts({ physicalRoot: u.root, catalogue: catalogue() });
  const urow = up.rows.find((x) => x.relPath.includes('bad'));
  assert.equal(urow.state, 'refused');
  assert.equal(urow.why, 'unsafe-name');
  assert.equal(applyRetiredArtifacts(up, u.root, { runId: makeRunId() }), 0);
  assert.ok(fs.existsSync(path.join(u.root, '.claude', 'hooks', weird)));
  const ufx = fixture();
  fs.mkdirSync(path.join(ufx.cwd, '.claude', 'hooks'), { recursive: true });
  fs.writeFileSync(path.join(ufx.cwd, '.claude', 'hooks', weird), '#!/bin/sh\n');
  fs.writeFileSync(path.join(ufx.cwd, '.claude', 'bad\u001b.md'), 'live-seam\n');
  const ur = await run(ufx, { argv: ['--cleanup'] });
  assert.ok(!ur.text.includes('\u001b'), 'a control character reached the output');
  assert.ok(ur.text.includes('bad\\x1bname-exec-guard.sh'));
  assert.ok(!JSON.stringify(ur.report).includes('\u001b') && !JSON.stringify(ur.report).includes('bad\\u001b'));
  assert.ok(ur.report.cleanup.some((c) => c.path === '<unsafe-name>' && c.state === 'refused'));
  assert.ok(ur.report.compatibility.diagnoses.some((d) => d.code === 'input-skipped' && d.remedy.includes('.claude/<unsafe-name>')));
  assert.deepEqual(ur.report.adoption.instructions_pending, []);

  // the two-argument form still works: a run id is derived and the catalogued entry is moved
  const { root, sub } = quarantineRoot();
  write(sub('.foundry', 'wiring-hash.pin'), 'pin');
  const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: catalogue() });
  assert.equal(applyRetiredArtifacts(plan, root), 1);
  assert.ok(!fs.existsSync(sub('.foundry', 'wiring-hash.pin')));
  const ids = fs.readdirSync(sub('.foundry', 'retired'));
  assert.equal(ids.length, 1);
  assert.match(ids[0], RUN_ID_RE);
  assert.equal(fs.readFileSync(sub('.foundry', 'retired', ids[0], '.foundry', 'wiring-hash.pin'), 'utf-8'), 'pin');
});
