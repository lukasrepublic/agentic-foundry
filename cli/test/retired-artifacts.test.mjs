import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadRetiredCatalogue, planRetiredArtifacts, applyRetiredArtifacts, renderRetiredArtifactRows } from '../src/retiredArtifacts.mjs';

const CLI_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scratch = () => fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ra-'))); // physicalRoot is always a real path

test('the shipped catalogue loads and every entry is well-formed', () => {
  const cat = loadRetiredCatalogue(CLI_DIR);
  assert.ok(cat.entries.length >= 10);
  for (const e of cat.entries) {
    assert.ok(!path.isAbsolute(e.path) && !e.path.includes('..'));
    assert.ok(['file', 'dir'].includes(e.kind));
    assert.match(e.retired_in, /^\d+\.\d+\.\d+$/);
    assert.ok(!e.path.startsWith('.claude/skills') && !e.path.startsWith('.claude/agents'));
  }
});

test('plan reports catalogued leftovers, refuses a link and the wrong kind, ignores operator files and the foundry- prefix', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry/context-snapshots'), { recursive: true });
  fs.mkdirSync(path.join(root, '.claude/hooks'), { recursive: true });
  fs.mkdirSync(path.join(root, '.claude/skills'), { recursive: true });
  fs.writeFileSync(path.join(root, '.foundry/wiring-hash.pin'), 'x');
  fs.writeFileSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh'), '#!/bin/sh\n');
  fs.writeFileSync(path.join(root, '.claude/hooks/foundry-cloud-cli-exec-guard.sh'), '#!/bin/sh\n');
  fs.writeFileSync(path.join(root, '.claude/skills/mine.md'), 'mine');
  fs.mkdirSync(path.join(root, '.claude/dispatcher-mode.json')); // catalogued as a FILE: wrong kind
  fs.symlinkSync('/etc/hosts', path.join(root, '.foundry/hasher-ref'));  // a link: refused
  const cat = loadRetiredCatalogue(CLI_DIR);
  const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: cat });
  const by = Object.fromEntries(plan.rows.map((r) => [r.relPath, r.state]));
  assert.equal(by['.foundry/wiring-hash.pin'], 'stale');
  assert.equal(by['.foundry/context-snapshots'], 'stale');
  assert.equal(by['.claude/hooks/zeta-exec-guard.sh'], 'stale');
  assert.equal(by['.claude/dispatcher-mode.json'], 'refused');
  assert.equal(by['.foundry/hasher-ref'], 'refused');
  assert.equal(by['.claude/hooks/foundry-cloud-cli-exec-guard.sh'], undefined);
  assert.equal(plan.present, 3);
  assert.equal(plan.refused, 2);
  const rows = renderRetiredArtifactRows(plan, { cleanup: false });
  assert.ok(rows.some((l) => l.startsWith('  [stale] .foundry/wiring-hash.pin') && l.endsWith('remove with --cleanup')));
  assert.ok(rows.some((l) => l.startsWith('  [refused] .foundry/hasher-ref')));
  // apply removes exactly the stale rows
  assert.equal(applyRetiredArtifacts(plan, root), 3);
  assert.equal(fs.existsSync(path.join(root, '.foundry/wiring-hash.pin')), false);
  assert.equal(fs.existsSync(path.join(root, '.foundry/context-snapshots')), false);
  assert.equal(fs.existsSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh')), false);
  assert.equal(fs.existsSync(path.join(root, '.claude/hooks/foundry-cloud-cli-exec-guard.sh')), true);
  assert.equal(fs.existsSync(path.join(root, '.claude/skills/mine.md')), true);
  assert.equal(fs.lstatSync(path.join(root, '.foundry/hasher-ref')).isSymbolicLink(), true);
  assert.equal(fs.existsSync('/etc/hosts'), true);
  assert.ok(renderRetiredArtifactRows(plan, { cleanup: true }).some((l) => l.startsWith('  [removed] .foundry/wiring-hash.pin')));
});
