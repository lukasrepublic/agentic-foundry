// statusline-wiring.test.mjs — statusline-wiring (v1.17.0, AC-SLW-1/-2/-5).
//
// The wrappers are FRAMEWORK-OWNED files (create / converge-on-marker / keep-without-marker) and
// the two settings keys are added only when absent; nothing runs unless .claude/settings.json is
// already present (post-trust). Driven over hermetic temp trees exactly as run.mjs / update.mjs do.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  MARKER, WRAPPERS, desiredSettingsValue, planStatuslineWiring, applyStatuslineWiring,
  renderStatuslineRows, statuslineChanged,
} from '../src/statuslineWiring.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMPLATES_DIR = path.join(CLI_DIR, 'templates');
const MAIN = WRAPPERS[0];

function scratch() {
  return fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'slw-'));
}
function withSettings(root, obj = { permissions: { allow: [] } }) {
  fs.mkdirSync(path.join(root, '.claude'), { recursive: true });
  fs.writeFileSync(path.join(root, '.claude', 'settings.json'), `${JSON.stringify(obj, null, 2)}\n`);
}
function settings(root) {
  return JSON.parse(fs.readFileSync(path.join(root, '.claude', 'settings.json'), 'utf-8'));
}

test('both templates carry the framework marker and are byte-identical to the shipped scripts/ wrappers', () => {
  for (const w of WRAPPERS) {
    const tpl = fs.readFileSync(path.join(TEMPLATES_DIR, w.template));
    assert.ok(tpl.toString('utf-8').includes(MARKER), `${w.template} lacks the marker`);
    const shipped = path.join(CLI_DIR, '..', 'scripts', w.template.replace('.sh', '-wrapper.sh'));
    assert.deepEqual(fs.readFileSync(shipped), tpl, `${shipped} differs from the template`);
  }
});

test('no settings.json -> empty plan, no rows, nothing written (the greenfield create path never wires)', () => {
  const root = scratch();
  const plan = planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR });
  assert.equal(plan.settingsPresent, false);
  assert.deepEqual(renderStatuslineRows(plan), []);
  applyStatuslineWiring(plan);
  assert.equal(fs.existsSync(path.join(root, '.claude', 'hooks')), false);
});

test('AC-SLW-1/-2: post-trust, absent wrappers are created 0755 and absent keys are wired; other keys untouched', () => {
  const root = scratch();
  withSettings(root, { permissions: { allow: ['Bash(git status:*)'] }, enabledPlugins: { 'foundry@agentic-foundry': true } });
  const plan = planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR });
  assert.deepEqual(plan.files.map((f) => f.action), ['create', 'create']);
  assert.deepEqual(plan.keys.map((k) => k.action), ['wired', 'wired']);
  const rows = renderStatuslineRows(plan);
  assert.ok(rows.some((r) => r === `  [create] ${MAIN.rel}`), rows.join('\n'));
  assert.ok(rows.some((r) => r === '  [statusline] wired statusLine, subagentStatusLine in .claude/settings.json'), rows.join('\n'));
  applyStatuslineWiring(plan);
  assert.ok(statuslineChanged(plan));
  for (const w of WRAPPERS) {
    const abs = path.join(root, w.rel);
    assert.deepEqual(fs.readFileSync(abs), fs.readFileSync(path.join(TEMPLATES_DIR, w.template)));
    assert.equal(fs.statSync(abs).mode & 0o777, 0o755);
  }
  const s = settings(root);
  assert.deepEqual(s.statusLine, desiredSettingsValue(MAIN.rel));
  assert.deepEqual(s.subagentStatusLine, desiredSettingsValue(WRAPPERS[1].rel));
  assert.deepEqual(s.permissions, { allow: ['Bash(git status:*)'] });
  assert.deepEqual(s.enabledPlugins, { 'foundry@agentic-foundry': true });
});

test('a second run: unchanged wrappers, already-wired keys, no write, not changed', () => {
  const root = scratch();
  withSettings(root);
  applyStatuslineWiring(planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR }));
  const before = fs.statSync(path.join(root, MAIN.rel)).mtimeMs;
  const sBefore = fs.readFileSync(path.join(root, '.claude', 'settings.json'));
  const plan = planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR });
  assert.deepEqual(plan.files.map((f) => f.action), ['unchanged', 'unchanged']);
  assert.deepEqual(plan.keys.map((k) => k.action), ['already-wired', 'already-wired']);
  applyStatuslineWiring(plan);
  assert.equal(statuslineChanged(plan), false);
  assert.equal(fs.statSync(path.join(root, MAIN.rel)).mtimeMs, before);
  assert.deepEqual(fs.readFileSync(path.join(root, '.claude', 'settings.json')), sBefore);
});

test('a stale wrapper WITH the marker is converged; one WITHOUT it is kept verbatim', () => {
  const root = scratch();
  withSettings(root);
  fs.mkdirSync(path.join(root, '.claude', 'hooks'), { recursive: true });
  fs.writeFileSync(path.join(root, MAIN.rel), `#!/usr/bin/env bash\n# old wrapper (${MARKER})\nexit 0\n`);
  const own = '#!/usr/bin/env bash\necho my-own-statusline\n';
  fs.writeFileSync(path.join(root, WRAPPERS[1].rel), own);
  const plan = planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR });
  assert.deepEqual(plan.files.map((f) => f.action), ['converged', 'kept']);
  const rows = renderStatuslineRows(plan);
  assert.ok(rows.some((r) => r.startsWith(`  [kept] ${WRAPPERS[1].rel} (operator-owned`)), rows.join('\n'));
  applyStatuslineWiring(plan);
  assert.deepEqual(fs.readFileSync(path.join(root, MAIN.rel)), fs.readFileSync(path.join(TEMPLATES_DIR, MAIN.template)));
  assert.equal(fs.readFileSync(path.join(root, WRAPPERS[1].rel), 'utf-8'), own);
});

test('an existing statusLine value pointing elsewhere is never overwritten', () => {
  const root = scratch();
  const foreign = { type: 'command', command: '/usr/local/bin/my-statusline' };
  withSettings(root, { permissions: {}, statusLine: foreign });
  const plan = planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR });
  assert.deepEqual(plan.keys.map((k) => k.action), ['already-wired', 'wired']);
  applyStatuslineWiring(plan);
  const s = settings(root);
  assert.deepEqual(s.statusLine, foreign);
  assert.deepEqual(s.subagentStatusLine, desiredSettingsValue(WRAPPERS[1].rel));
});

test('dry-run = plan without apply: rows rendered, nothing written', () => {
  const root = scratch();
  withSettings(root);
  const plan = planStatuslineWiring({ physicalRoot: root, templatesDir: TEMPLATES_DIR });
  assert.ok(renderStatuslineRows(plan).length >= 3);
  assert.equal(fs.existsSync(path.join(root, MAIN.rel)), false);
  assert.equal(settings(root).statusLine, undefined);
});
