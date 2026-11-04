// security review of #286: the protected-path set is enforced inside every write helper, so a Phase-4
// re-plan (which never passes the preview-row check) cannot write a protected path.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { installWriteGuard, assertProtected } from '../src/writeGuard.mjs';
import { applyPlan } from '../src/reconcile.mjs';
import { writeTargetAtomically } from '../src/floorReconcile.mjs';
import { writeGitignoreAtomically } from '../src/gitignoreReconcile.mjs';
import { applyStatuslineWiring } from '../src/statuslineWiring.mjs';
import { applyManifestTrueUp } from '../src/manifestTrueUp.mjs';
import { applyAmendmentsBackfill } from '../src/amendmentsBackfill.mjs';
import { applyRetiredArtifacts } from '../src/retiredArtifacts.mjs';
import { RefusalError } from '../src/util.mjs';
import { makeRunId } from '../src/upgradeCompat.mjs';

const RUN = makeRunId();

const scratch = () => fs.realpathSync(fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'wg-')));
const refused = (fn) => assert.throws(fn, (e) => e instanceof RefusalError && e.flag === 'protected');

test('applyPlan: a create row aimed at a protected, undeclared path is refused; a declared absent one may be created', () => {
  const root = scratch();
  const release = installWriteGuard(root, ['.foundry/migration/cutover.json']);
  try {
    refused(() => applyPlan([{ action: 'create', relPath: '.mcp.json', absPath: path.join(root, '.mcp.json'), bytes: Buffer.from('{}') }]));
    assert.equal(fs.existsSync(path.join(root, '.mcp.json')), false);
    applyPlan([{ action: 'create', relPath: '.foundry/permissions.yaml', absPath: path.join(root, '.foundry/permissions.yaml'), bytes: Buffer.from('x') }]);
    assert.equal(fs.readFileSync(path.join(root, '.foundry/permissions.yaml'), 'utf-8'), 'x');
    // ...but only ever CREATED: with the file present the same row is refused
    refused(() => applyPlan([{ action: 'create', relPath: '.foundry/permissions.yaml', absPath: path.join(root, '.foundry/permissions.yaml'), bytes: Buffer.from('y') }]));
    // the cutover record (extra protected) is refused even though nothing else declares it
    refused(() => applyPlan([{ action: 'create', relPath: '.foundry/migration/cutover.json', absPath: path.join(root, '.foundry/migration/cutover.json'), bytes: Buffer.from('{}') }]));
  } finally { release(); }
});

test('every atomic writer refuses a protected target; a symlink alias resolves to the protected file', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.claude'));
  const prot = path.join(root, '.claude', 'foundry-project.json');
  fs.writeFileSync(prot, '{"keep":true}');
  fs.symlinkSync(prot, path.join(root, 'innocent.json'));
  const release = installWriteGuard(root);
  try {
    refused(() => writeTargetAtomically(prot, { a: 1 }));
    refused(() => writeTargetAtomically(path.join(root, 'innocent.json'), { a: 1 }));
    refused(() => writeGitignoreAtomically(path.join(root, '.mcp.json'), ['x']));
    refused(() => applyAmendmentsBackfill({ toAppend: [prot], present: 0, physicalRoot: root }));
    refused(() => applyManifestTrueUp({ rows: [{ abs: prot, rel: '.claude/foundry-project.json', before: '', next: 'x' }] }));
    refused(() => applyStatuslineWiring({ settingsPresent: true, files: [{ action: 'create', abs: path.join(root, '.mcp.json'), bytes: Buffer.from('x') }], keys: [] }));
    assert.equal(fs.readFileSync(prot, 'utf-8'), '{"keep":true}');
    // a settings target that is NOT protected still writes
    writeTargetAtomically(path.join(root, '.claude', 'settings.json'), { ok: 1 });
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root, '.claude', 'settings.json'), 'utf-8')), { ok: 1 });
  } finally { release(); }
});

test('the guard is scoped: released, the same helper is unguarded again; paths outside the workspace are not its set', () => {
  const root = scratch();
  const outside = scratch();
  const release = installWriteGuard(root);
  assertProtected(path.join(outside, '.mcp.json'));   // outside the root: not decided here
  release();
  assertProtected(path.join(root, '.mcp.json'));      // no guard installed: a no-op
});

test('quarantine: a protected row is refused, never moved; a failing manifest write is recorded on the plan', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry'), { recursive: true });
  fs.writeFileSync(path.join(root, '.mcp.json'), '{}');
  const release = installWriteGuard(root);
  try {
    const plan = { rows: [{ state: 'stale', kind: 'file', relPath: '.mcp.json', retired_in: '1.0.0', reason: 'x' }] };
    applyRetiredArtifacts(plan, root, { runId: RUN });
    assert.equal(plan.rows[0].state, 'refused');
    assert.equal(fs.existsSync(path.join(root, '.mcp.json')), true);
  } finally { release(); }
  // manifest destination parent is a symlink out of the tree -> the manifest is NOT written through it
  const root2 = scratch();
  const elsewhere = scratch();
  fs.mkdirSync(path.join(root2, '.foundry'), { recursive: true });
  fs.writeFileSync(path.join(root2, 'old.txt'), 'x');
  const plan2 = { rows: [{ state: 'stale', kind: 'file', relPath: 'old.txt', retired_in: '1.0.0', reason: 'x' }] };
  fs.mkdirSync(path.join(root2, '.foundry', 'retired'), { recursive: true });
  applyRetiredArtifacts(plan2, root2, { runId: RUN });
  assert.equal(plan2.manifestWriteFailed, null);
  assert.equal(fs.existsSync(path.join(root2, '.foundry', 'retired', RUN, 'manifest.json')), true);
  // a second run into the same run dir: the manifest already exists, so the write fails -> recorded on the plan
  fs.writeFileSync(path.join(root2, 'old2.txt'), 'x');
  const plan3 = { rows: [{ state: 'stale', kind: 'file', relPath: 'old2.txt', retired_in: '1.0.0', reason: 'x' }] };
  applyRetiredArtifacts(plan3, root2, { runId: RUN });
  assert.equal(plan3.rows[0].state, 'removed');
  assert.equal(plan3.manifestWriteFailed, 'EEXIST');
  // a link planted at the run dir is never written through (the move refuses, nothing lands elsewhere)
  const RUN4 = makeRunId();
  fs.symlinkSync(elsewhere, path.join(root2, '.foundry', 'retired', RUN4));
  fs.writeFileSync(path.join(root2, 'old4.txt'), 'x');
  const plan4 = { rows: [{ state: 'stale', kind: 'file', relPath: 'old4.txt', retired_in: '1.0.0', reason: 'x' }] };
  applyRetiredArtifacts(plan4, root2, { runId: RUN4 });
  assert.equal(fs.readdirSync(elsewhere).length, 0, 'nothing written through the planted link');
  assert.equal(plan4.rows[0].state, 'refused');
});
