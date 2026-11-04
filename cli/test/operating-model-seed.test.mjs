// operating-model-seed.test.mjs -- gv-03: `.foundry/operating-model.yaml` is a SEED entry.
// Absent -> created from the template (and the file validates); present -> `kept`, byte-for-byte,
// so an adopter's tunable edit survives every reconcile. The path is classed framework-owned.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buildManagedFiles, TEMPLATE_ENTRIES, DECLARED_PATH_SET } from '../src/scaffold.mjs';
import { planManagedFiles, applyPlan, exitCodeForPlan } from '../src/reconcile.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMPLATES_DIR = path.join(CLI_DIR, 'templates');
const VALIDATOR = path.join(CLI_DIR, '..', 'scripts', 'foundry_operating_model.py');
const REL = '.foundry/operating-model.yaml';

function scratch() {
  return fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'omseed-'));
}
function managed(root) {
  return buildManagedFiles({
    templatesDir: TEMPLATES_DIR, physicalRoot: root, projectName: 'demo', stageMode: 'lean',
    settingsBytes: Buffer.from('{}\n'),
  });
}
function validate(root) {
  return spawnSync('python3', [VALIDATOR, '--project-dir', root, 'validate'], { encoding: 'utf-8' });
}

test('the manifest carries the entry as a seed and the declared set names it', () => {
  const entry = TEMPLATE_ENTRIES.find((e) => e.target === REL);
  assert.ok(entry);
  assert.equal(entry.seed, true);
  assert.equal(entry.template, 'operating-model.yaml.tmpl');
  assert.ok(DECLARED_PATH_SET.includes(REL));
});

test('scaffold writes the default file verbatim and it validates', () => {
  const root = scratch();
  applyPlan(planManagedFiles(managed(root)));
  assert.equal(
    fs.readFileSync(path.join(root, REL), 'utf-8'),
    fs.readFileSync(path.join(TEMPLATES_DIR, 'operating-model.yaml.tmpl'), 'utf-8'),
  );
  const r = validate(root);
  assert.equal(r.status, 0, r.stdout + r.stderr);
});

test('reconcile keeps an adopter tunable edit byte-for-byte', () => {
  const root = scratch();
  applyPlan(planManagedFiles(managed(root)));
  const abs = path.join(root, REL);
  const edited = fs.readFileSync(abs, 'utf-8').replace('max_builders: 4', 'max_builders: 2  # my edit');
  fs.writeFileSync(abs, edited);
  const before = fs.readFileSync(abs);
  const plan = planManagedFiles(managed(root));
  assert.equal(plan.find((f) => f.relPath === REL).action, 'kept');
  assert.equal(exitCodeForPlan(plan), 0);
  applyPlan(plan);
  assert.deepEqual(fs.readFileSync(abs), before);
  assert.equal(validate(root).status, 0);
});

test('a floor key disabled in the file is kept (not overwritten) and validate refuses it', () => {
  const root = scratch();
  applyPlan(planManagedFiles(managed(root)));
  const abs = path.join(root, REL);
  fs.writeFileSync(abs, fs.readFileSync(abs, 'utf-8').replace('teardown_required: true', 'teardown_required: false'));
  applyPlan(planManagedFiles(managed(root)));
  const r = validate(root);
  assert.equal(r.status, 2);
  assert.match(r.stdout, /floor-rule-disabled:hygiene\.teardown_required/);
});

test('path-classes lists the file as framework-owned', () => {
  const pc = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'path-classes.json'), 'utf-8'));
  assert.ok(pc.classes['framework-owned'].includes(REL));
});
