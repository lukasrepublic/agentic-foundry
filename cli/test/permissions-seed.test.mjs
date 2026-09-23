// permissions-seed.test.mjs — permissions-scaffold (ER #215, AC-PSC-2/-5).
//
// `.foundry/permissions.yaml` is a SEED entry in the scaffold manifest: absent -> `create` like any
// managed file; present -> `kept`, whatever its bytes, never compared, never written, and never
// part of the exit-2 drift verdict. The other seven entries keep the whole-file never-clobber
// contract exactly as before.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildManagedFiles, TEMPLATE_ENTRIES, DECLARED_PATH_SET } from '../src/scaffold.mjs';
import { planManagedFiles, applyPlan, exitCodeForPlan } from '../src/reconcile.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMPLATES_DIR = path.join(CLI_DIR, 'templates');
const SEED_REL = '.foundry/permissions.yaml';

function scratch() {
  return fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'pseed-'));
}

function managed(root) {
  return buildManagedFiles({
    templatesDir: TEMPLATES_DIR, physicalRoot: root, projectName: 'demo', stageMode: 'lean',
    settingsBytes: Buffer.from('{}\n'),
  });
}

test('the manifest carries the seed entry, flagged, and the declared set names it', () => {
  const entry = TEMPLATE_ENTRIES.find((e) => e.target === SEED_REL);
  assert.ok(entry, 'seed entry missing from TEMPLATE_ENTRIES');
  assert.equal(entry.seed, true);
  assert.equal(entry.template, 'permissions.yaml.tmpl');
  assert.ok(DECLARED_PATH_SET.includes(SEED_REL));
  const seeds = TEMPLATE_ENTRIES.filter((e) => e.seed);
  assert.deepEqual(seeds.map((e) => e.target), [SEED_REL], 'exactly one seed entry');
});

test('absent -> create; the written bytes are the template, verbatim', () => {
  const root = scratch();
  const plan = planManagedFiles(managed(root));
  const row = plan.find((f) => f.relPath === SEED_REL);
  assert.equal(row.action, 'create');
  assert.equal(row.seed, true);
  applyPlan(plan);
  assert.equal(
    fs.readFileSync(path.join(root, SEED_REL), 'utf-8'),
    fs.readFileSync(path.join(TEMPLATES_DIR, 'permissions.yaml.tmpl'), 'utf-8'),
  );
});

test('present -> kept, whatever the bytes: never written, never drifted, exit 0', () => {
  const root = scratch();
  applyPlan(planManagedFiles(managed(root)));
  const abs = path.join(root, SEED_REL);
  fs.writeFileSync(abs, 'schema_version: 1\ngrants:\n  - id: x\n    tool: Bash\n    pattern: "gh pr merge:*"\n    mode: automatic\n');
  const before = fs.statSync(abs);
  const bytes = fs.readFileSync(abs);
  const plan = planManagedFiles(managed(root));
  const row = plan.find((f) => f.relPath === SEED_REL);
  assert.equal(row.action, 'kept');
  assert.equal(exitCodeForPlan(plan), 0);
  applyPlan(plan);
  assert.deepEqual(fs.readFileSync(abs), bytes);
  assert.equal(fs.statSync(abs).mtimeMs, before.mtimeMs);
});

test('a symlinked or otherwise irregular seed path is still kept (never followed, never written)', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry'), { recursive: true });
  const elsewhere = path.join(root, 'elsewhere.yaml');
  fs.writeFileSync(elsewhere, 'schema_version: 1\ngrants: []\n');
  fs.symlinkSync(elsewhere, path.join(root, SEED_REL));
  const plan = planManagedFiles(managed(root));
  assert.equal(plan.find((f) => f.relPath === SEED_REL).action, 'kept');
  assert.equal(exitCodeForPlan(plan), 0);
});

test('the other entries keep the never-clobber contract: an edited framework file is drifted, exit 2', () => {
  const root = scratch();
  applyPlan(planManagedFiles(managed(root)));
  fs.appendFileSync(path.join(root, '.foundry/README.md'), '\nlocal edit\n');
  const plan = planManagedFiles(managed(root));
  assert.equal(plan.find((f) => f.relPath === '.foundry/README.md').action, 'drifted');
  assert.equal(plan.find((f) => f.relPath === SEED_REL).action, 'kept');
  assert.equal(exitCodeForPlan(plan), 2);
});
