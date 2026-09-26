// v1.18.2: the updater trues up release manifests — line-level, comment-preserving, idempotent.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { trueUpText, planManifestTrueUp, applyManifestTrueUp, renderManifestTrueUpRows } from '../src/manifestTrueUp.mjs';

test('a legacy state is rewritten, comments and other bytes untouched', () => {
  const src = '# keep me\nid: r1\ndescription: x\nstate: in_progress  # was wip\natoms:\n  - id: a\n    state: done\n';
  const { text, changes } = trueUpText(src);
  assert.deepEqual(changes, ['state in_progress -> active']);
  assert.equal(text, '# keep me\nid: r1\ndescription: x\nstate: active  # was wip\natoms:\n  - id: a\n    state: done\n');
});

test('a missing description is added after id; an existing one never is', () => {
  const { text, changes } = trueUpText('id: hotfix-v1.17.1\nstate: completed\natoms: []\n');
  assert.deepEqual(changes, ['description added']);
  assert.equal(text, 'id: hotfix-v1.17.1\ndescription: "hotfix-v1.17.1"\nstate: completed\natoms: []\n');
  assert.deepEqual(trueUpText('id: r\ndescription: d\nstate: active\n').changes, []);
});

test('current vocabulary and quoted states: no change; released/partially-* map', () => {
  assert.deepEqual(trueUpText('id: r\ndescription: d\nstate: active\n').changes, []);
  assert.deepEqual(trueUpText('id: r\ndescription: d\nstate: "released"\n').changes, ['state released -> completed']);
  assert.deepEqual(trueUpText('id: r\ndescription: d\nstate: partially-merged\n').changes, ['state partially-merged -> active']);
});

test('apply writes once, is idempotent, and never follows a symlinked manifest', () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'mtu-')));
  const d = path.join(root, '.foundry', 'releases', 'r1');
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, 'release.yaml'), 'id: r1\nstate: released\natoms: []\n');
  const d2 = path.join(root, '.foundry', 'releases', 'r2');
  fs.mkdirSync(d2, { recursive: true });
  const victim = path.join(root, 'victim.yaml');
  fs.writeFileSync(victim, 'id: v\nstate: released\n');
  fs.symlinkSync(victim, path.join(d2, 'release.yaml'));
  const plan = applyManifestTrueUp(planManifestTrueUp({ physicalRoot: root }));
  assert.deepEqual(plan.written, ['.foundry/releases/r1/release.yaml']);
  assert.match(renderManifestTrueUpRows(plan)[0], /\[trued-up\] .*state released -> completed; description added/);
  assert.equal(fs.readFileSync(victim, 'utf-8'), 'id: v\nstate: released\n');
  assert.equal(applyManifestTrueUp(planManifestTrueUp({ physicalRoot: root })).written.length, 0);
  fs.rmSync(root, { recursive: true, force: true });
});
