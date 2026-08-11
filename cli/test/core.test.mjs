// core.test.mjs — node:test unit coverage of the CLI's own modules (AC-BCL-1..9, the "Both suites
// together" half that lives naturally at the unit level; the pytest shim in
// tests/test_bootstrap_cli.py drives the subprocess/integration half and the fourteen mutation
// negative controls). Run via `node --test cli/test/` (package.json's own `test` script; the
// pytest shim runs the same command as a subprocess — AC-BCL-10).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { QUESTION_TABLE, tableFlags } from '../src/questions.mjs';
import { parseArgv, renderHelp } from '../src/argv.mjs';
import { RefusalError, confinedJoin, physicalResolve } from '../src/util.mjs';
import { loadMap, buildSettings, covers, classifyDrift, DRIFT_CLASSES, isBlanketAllow } from '../src/permissionFloor.mjs';
import { buildManagedFiles, DECLARED_PATH_SET, TEMPLATE_ENTRIES } from '../src/scaffold.mjs';
import { planManagedFiles, applyPlan, exitCodeForPlan } from '../src/reconcile.mjs';
import { validateSlug } from '../src/identity.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CLI_DIR = path.resolve(__dirname, '..');

function tmpdir(prefix) {
  // physicalResolve, not the raw mkdtemp path. `confinedJoin` documents that it takes a
  // PHYSICALLY-RESOLVED root, and on macOS os.tmpdir() is /var/folders/… — a symlink to
  // /private/var/folders/…. Handing it the raw path made every join resolve outside the root it
  // was compared against, so confinedJoin returned null and buildManagedFiles refused its own
  // first template. That is the harness violating the function's contract, not the function
  // failing: run.mjs already physicalResolve()s the target root before it ever builds the plan.
  // Invisible in CI, which is ubuntu-only, where /tmp is not behind a symlink.
  return physicalResolve(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

test('AC-BCL-2: the argv parser accepts exactly the table flags, and --help renders one line per record', () => {
  const flags = tableFlags(QUESTION_TABLE);
  const help = renderHelp(QUESTION_TABLE);
  for (const f of flags) {
    assert.ok(help.includes(`--${f}`), `--help is missing --${f}`);
  }
  // every table flag parses without throwing
  const { provided } = parseArgv(['--dir', 'x', '--yes', '--stage-mode', 'scale'], QUESTION_TABLE);
  assert.ok(provided.has('dir') && provided.has('yes') && provided.has('stageMode'));
  assert.throws(() => parseArgv(['--bogus'], QUESTION_TABLE), RefusalError);
});

test('AC-BCL-2: an out-of-choices value refuses, naming the record and its permitted values', () => {
  assert.throws(
    () => parseArgv(['--stage-mode', 'nope'], QUESTION_TABLE),
    (err) => err instanceof RefusalError && /stage-mode/.test(err.message) && /lean/.test(err.message),
  );
});

test('AC-BCL-4: buildSettings is a set bijection onto the bundled map by tier', () => {
  const map = loadMap(path.join(CLI_DIR, 'permission-floor.json'));
  const pins = { marketplace_name: 'agentic-foundry', marketplace_repo: 'lukasrepublic/agentic-foundry', plugin_name: 'foundry' };
  const settings = buildSettings(map, pins);
  for (const tier of ['allow', 'ask', 'deny']) {
    const expected = new Set(map.entries.filter((e) => e.tier === tier).map((e) => e.rule));
    const actual = new Set(settings.permissions[tier]);
    assert.equal(actual.size, expected.size);
    for (const r of expected) assert.ok(actual.has(r), `missing ${tier} rule ${r}`);
  }
  assert.deepEqual(Object.keys(settings).sort(), ['enabledPlugins', 'extraKnownMarketplaces', 'permissions']);
  // AC-BCL-4(b), contract v1.2: `ref` is the pin. Derived from `pins.plugin_version` rather than
  // written as a second literal, so the assertion cannot drift from the marketplace manifest.
  assert.deepEqual(settings.extraKnownMarketplaces['agentic-foundry'], {
    source: {
      source: 'github',
      repo: 'lukasrepublic/agentic-foundry',
      ref: `v${pins.plugin_version}`,
    },
    autoUpdate: false,
  });
  assert.deepEqual(settings.enabledPlugins, { 'foundry@agentic-foundry': true });
});

test('AC-BCL-8: covers() matches the shared 8-row subsumption table', () => {
  const rows = [
    ['Bash(a/b:*)', 'Bash(a/b/c:*)', true],
    ['Bash(a/b/c:*)', 'Bash(a/b:*)', false],
    ['Bash(a/b:*)', 'Bash(a/b)', true],
    ['Bash(a/bc:*)', 'Bash(a/b:*)', false],
    ['Bash(a/b:*)', 'Bash(a/bc:*)', true],
    ['Bash(a/b:*)', 'Bash(a/b:*)', true],
    [
      'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/:*)',
      'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-authorize.py:*)',
      true,
    ],
    ['Bash(gh pr merge --admin:*)', 'Bash(gh pr merge:*)', false],
  ];
  for (const [a, b, expected] of rows) {
    assert.equal(covers(a, b), expected, `covers(${a}, ${b})`);
  }
});

test('AC-BCL-8: classifyDrift emits exactly the AC-DPF-8 vocabulary, never another class name', () => {
  const map = {
    plugin_root_glob: '~/.claude/plugins/cache/*/foundry/*',
    entries: [
      { rule: 'Bash(scripts/foundry-authorize.py:*)', tier: 'ask', rationale: 'ceremony' },
      { rule: 'Bash(claude plugin tag:*)', tier: 'ask', rationale: 'non-ceremony ask' },
      { rule: 'Bash(scripts/foundry-index.py:*)', tier: 'allow', rationale: 'read-only' },
      { rule: 'Bash(git push --force:*)', tier: 'deny', rationale: 'anti-pattern' },
    ],
  };

  // Scenario A: a blanket allow swallows everything (blanket-allow), plus unreadable/stale/unclassified.
  const effectiveBlanket = {
    allow: [
      { rule: 'Bash(*)', origin: 'settings.local.json', tierKey: 'allow' },
      { rule: 'weird tool call', origin: 'settings.json', tierKey: 'allow' },
    ],
    ask: [],
    deny: [],
  };
  const findingsA = classifyDrift(map, effectiveBlanket, {
    pluginRootExpansion: [],
    unreadableOrigins: ['settings.local.json'],
  });
  const seenA = new Set(findingsA.map((f) => f.class));
  assert.ok(seenA.has('blanket-allow'));
  assert.ok(seenA.has('settings-unreadable'));
  assert.ok(seenA.has('stale-plugin-path'));
  assert.ok(seenA.has('unclassified'));
  // swallowed entries never ALSO produce an individual ask-shadowed(-ceremony) line
  assert.ok(!seenA.has('ask-shadowed-ceremony'));
  assert.ok(!seenA.has('ask-shadowed'));

  // Scenario B: narrow (non-blanket) allow rules independently shadow both ask kinds; the map's
  // own allow entry and deny entry are both covered, so allow-absent/deny-missing do not fire here.
  const effectiveNarrow = {
    allow: [
      { rule: 'Bash(scripts/foundry-authorize.py:*)', origin: 'settings.json', tierKey: 'allow' },
      { rule: 'Bash(claude plugin tag:*)', origin: 'settings.json', tierKey: 'allow' },
      { rule: 'Bash(scripts/foundry-index.py:*)', origin: 'settings.json', tierKey: 'allow' },
    ],
    ask: [],
    deny: [{ rule: 'Bash(git push --force:*)', origin: 'settings.json', tierKey: 'deny' }],
  };
  const findingsB = classifyDrift(map, effectiveNarrow, { pluginRootExpansion: ['/some/dir'] });
  const seenB = new Set(findingsB.map((f) => f.class));
  assert.ok(seenB.has('ask-shadowed-ceremony'));
  assert.ok(seenB.has('ask-shadowed'));
  assert.ok(!seenB.has('deny-missing'));
  assert.ok(!seenB.has('stale-plugin-path'));

  // Scenario C: nothing covered at all -> deny-missing + allow-absent.
  const findingsC = classifyDrift(map, { allow: [], ask: [], deny: [] }, { pluginRootExpansion: ['/some/dir'] });
  const seenC = new Set(findingsC.map((f) => f.class));
  assert.ok(seenC.has('deny-missing'));
  assert.ok(seenC.has('allow-absent'));

  const everySeen = new Set([...seenA, ...seenB, ...seenC]);
  for (const cls of everySeen) assert.ok(DRIFT_CLASSES.includes(cls), `unexpected class ${cls}`);
  assert.deepEqual([...everySeen].sort(), [...DRIFT_CLASSES].sort());
});

test('AC-BCL-6: the greenfield scaffold declares exactly the seven closed paths', () => {
  const dir = tmpdir('bcl-scaffold-');
  const settingsBytes = Buffer.from('{}');
  const files = buildManagedFiles({
    templatesDir: path.join(CLI_DIR, 'templates'),
    physicalRoot: dir,
    projectName: 'demo',
    stageMode: 'lean',
    settingsBytes,
  });
  assert.deepEqual(files.map((f) => f.relPath).sort(), [...DECLARED_PATH_SET].sort());
});

test('AC-BCL-9: a traversing template entry is refused, naming the entry', () => {
  const dir = tmpdir('bcl-traverse-');
  const badEntries = [...TEMPLATE_ENTRIES, { template: 'CLAUDE.md.tmpl', target: '../evil.md' }];
  assert.throws(
    () =>
      buildManagedFiles({
        templatesDir: path.join(CLI_DIR, 'templates'),
        physicalRoot: dir,
        projectName: 'demo',
        stageMode: 'lean',
        settingsBytes: Buffer.from('{}'),
        entries: badEntries,
      }),
    (err) => err instanceof RefusalError && /evil\.md/.test(err.message),
  );
  assert.equal(confinedJoin(dir, '../escape'), null);
  assert.equal(confinedJoin(dir, '/etc/passwd'), null);
  assert.notEqual(confinedJoin(dir, 'ok/nested.txt'), null);
});

test('AC-BCL-7: --gh-account slug validation refuses out-of-charset and dot values', () => {
  assert.throws(() => validateSlug('bad slug'), RefusalError);
  assert.throws(() => validateSlug('.'), RefusalError);
  assert.throws(() => validateSlug('..'), RefusalError);
  assert.equal(validateSlug('demo-acct_9.x'), 'demo-acct_9.x');
});

test('AC-BCL-8: reconcile never overwrites a drifted file and computes the total exit matrix', () => {
  const dir = tmpdir('bcl-reconcile-');
  const target = path.join(dir, 'CLAUDE.md');
  fs.writeFileSync(target, 'ORIGINAL');
  const managed = [{ relPath: 'CLAUDE.md', absPath: target, bytes: Buffer.from('DESIRED') }];
  const plan = planManagedFiles(managed);
  assert.equal(plan[0].action, 'drifted');
  applyPlan(plan);
  assert.equal(fs.readFileSync(target, 'utf-8'), 'ORIGINAL');
  assert.equal(exitCodeForPlan(plan), 2);

  const dir2 = tmpdir('bcl-reconcile2-');
  const target2 = path.join(dir2, 'x.txt');
  const managed2 = [{ relPath: 'x.txt', absPath: target2, bytes: Buffer.from('DESIRED') }];
  const plan2 = planManagedFiles(managed2);
  assert.equal(plan2[0].action, 'create');
  applyPlan(plan2);
  assert.equal(fs.readFileSync(target2, 'utf-8'), 'DESIRED');
  assert.equal(exitCodeForPlan(plan2), 0);
});

test('AC-BCL-9: isBlanketAllow recognizes the named blanket spellings only', () => {
  assert.ok(isBlanketAllow('Bash(*)'));
  assert.ok(isBlanketAllow('Bash(python3 *)'));
  assert.ok(isBlanketAllow('Bash(python3:*)'));
  assert.ok(!isBlanketAllow('Bash(git push --force:*)'));
});

test('AC-BCL-5: the bundled map is byte-identical to the shipped map', () => {
  const bundled = fs.readFileSync(path.join(CLI_DIR, 'permission-floor.json'));
  const shipped = fs.readFileSync(path.join(CLI_DIR, '..', 'docs', 'permission-floor.json'));
  assert.ok(bundled.equals(shipped));
});

test('AC-BCL-9: physicalResolve resolves a not-yet-existing nested path onto its real ancestor', () => {
  const dir = tmpdir('bcl-physical-');
  const real = fs.realpathSync(dir);
  const resolved = physicalResolve(path.join(dir, 'a', 'b', 'c.txt'));
  assert.equal(resolved, path.join(real, 'a', 'b', 'c.txt'));
});
