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

test('plan reports catalogued leftovers, refuses a link, the wrong kind and a wired hook, ignores operator files and the foundry- prefix', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry/context-snapshots'), { recursive: true });
  fs.writeFileSync(path.join(root, '.foundry/context-snapshots/one.json'), '{}');
  fs.mkdirSync(path.join(root, '.claude/hooks'), { recursive: true });
  fs.mkdirSync(path.join(root, '.claude/skills'), { recursive: true });
  fs.writeFileSync(path.join(root, '.foundry/wiring-hash.pin'), 'x');
  fs.writeFileSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh'), '#!/bin/sh\n');  // unwired: stale
  fs.writeFileSync(path.join(root, '.claude/hooks/aws-exec-guard.sh'), '#!/bin/sh\n');   // WIRED below: refused
  fs.writeFileSync(path.join(root, '.claude/hooks/foundry-cloud-cli-exec-guard.sh'), '#!/bin/sh\n');
  // PR #237 security review Risk 1: a hook a settings hook command still names is never stale
  fs.writeFileSync(path.join(root, '.claude/settings.json'), JSON.stringify({
    hooks: { PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: '"$CLAUDE_PROJECT_DIR"/.claude/hooks/aws-exec-guard.sh' }] }] },
  }));
  fs.writeFileSync(path.join(root, '.claude/skills/mine.md'), 'mine');
  fs.mkdirSync(path.join(root, '.claude/dispatcher-mode.json')); // catalogued as a FILE: wrong kind
  fs.symlinkSync('/etc/hosts', path.join(root, '.foundry/hasher-ref'));  // a link: refused
  const cat = loadRetiredCatalogue(CLI_DIR);
  const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: cat });
  const by = Object.fromEntries(plan.rows.map((r) => [r.relPath, r.state]));
  assert.equal(by['.foundry/wiring-hash.pin'], 'stale');
  assert.equal(by['.foundry/context-snapshots'], 'stale');
  assert.equal(by['.claude/hooks/zeta-exec-guard.sh'], 'stale');
  assert.equal(by['.claude/hooks/aws-exec-guard.sh'], 'refused');
  assert.equal(plan.rows.find((r) => r.relPath === '.claude/hooks/aws-exec-guard.sh').why, 'referenced');
  assert.equal(by['.claude/dispatcher-mode.json'], 'refused');
  assert.equal(by['.foundry/hasher-ref'], 'refused');
  assert.equal(by['.claude/hooks/foundry-cloud-cli-exec-guard.sh'], undefined);
  assert.equal(plan.present, 3);
  assert.equal(plan.refused, 3);
  assert.equal(plan.rows.find((r) => r.relPath === '.foundry/context-snapshots').entries, 1);
  // hotfix-v1.17.5: the --cleanup PREVIEW says what the run will do, never "NOT removed"
  const preview = renderRetiredArtifactRows(plan, { cleanup: true, phase: 'preview' });
  assert.ok(preview.some((l) => l.startsWith('  [stale] .foundry/wiring-hash.pin') && l.endsWith('; will be removed')));
  assert.ok(!preview.some((l) => l.includes('NOT removed')));
  assert.deepEqual(renderRetiredArtifactRows(plan, { cleanup: false, phase: 'preview' }), renderRetiredArtifactRows(plan, { cleanup: false }));
  const rows = renderRetiredArtifactRows(plan, { cleanup: false });
  assert.ok(rows.some((l) => l.startsWith('  [stale] .foundry/wiring-hash.pin') && l.endsWith('remove with --cleanup')));
  assert.ok(rows.some((l) => l.startsWith('  [stale] .foundry/context-snapshots [1 entry]')));
  assert.ok(rows.some((l) => l.startsWith('  [refused] .foundry/hasher-ref')));
  assert.ok(rows.some((l) => l.startsWith('  [refused] .claude/hooks/aws-exec-guard.sh — still named by a hook command')));
  // apply removes exactly the stale rows
  assert.equal(applyRetiredArtifacts(plan, root), 3);
  assert.equal(fs.existsSync(path.join(root, '.foundry/wiring-hash.pin')), false);
  assert.equal(fs.existsSync(path.join(root, '.foundry/context-snapshots')), false);
  assert.equal(fs.existsSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh')), false);
  assert.equal(fs.existsSync(path.join(root, '.claude/hooks/aws-exec-guard.sh')), true, 'a wired hook is never removed');
  assert.equal(fs.existsSync(path.join(root, '.claude/hooks/foundry-cloud-cli-exec-guard.sh')), true);
  assert.equal(fs.existsSync(path.join(root, '.claude/skills/mine.md')), true);
  assert.equal(fs.lstatSync(path.join(root, '.foundry/hasher-ref')).isSymbolicLink(), true);
  assert.equal(fs.existsSync('/etc/hosts'), true);
  assert.ok(renderRetiredArtifactRows(plan, { cleanup: true }).some((l) => l.startsWith('  [removed] .foundry/wiring-hash.pin')));
});

test('a settings file that does not parse makes every hook candidate refused (fail-closed), never stale', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.claude/hooks'), { recursive: true });
  fs.writeFileSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh'), '#!/bin/sh\n');
  fs.writeFileSync(path.join(root, '.claude/settings.local.json'), '{ not json');
  const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: loadRetiredCatalogue(CLI_DIR) });
  const row = plan.rows.find((r) => r.relPath === '.claude/hooks/zeta-exec-guard.sh');
  assert.equal(row.state, 'refused');
  assert.equal(row.why, 'settings-unreadable');
  assert.equal(applyRetiredArtifacts(plan, root), 0);
  assert.equal(fs.existsSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh')), true);
});

test('after apply, a row that changed since the plan reads NOT removed (changed since the plan)', () => {
  const plan = { rows: [{ relPath: '.foundry/wiring-hash.pin', kind: 'file', retired_in: '0.24.0', reason: 'r', state: 'stale' }] };
  assert.deepEqual(renderRetiredArtifactRows(plan, { cleanup: true }),
    ['  [stale] .foundry/wiring-hash.pin — retired in v0.24.0 (r) — NOT removed (changed since the plan)']);
});

test('a removal that fails reads "removal failed", never "not a regular file"', () => {
  const plan = { rows: [{ relPath: '.foundry/wiring-hash.pin', kind: 'file', retired_in: '0.24.0', reason: 'r', state: 'refused', why: 'remove-failed', error: 'EACCES' }] };
  assert.deepEqual(renderRetiredArtifactRows(plan, { cleanup: true }),
    ['  [refused] .foundry/wiring-hash.pin — removal failed (EACCES) — left in place']);
});

// ER #241: an adopter may re-adopt a retired path; a wiring file that names it keeps it.
test('a catalogued path a workspace wiring file names is refused; a mention outside the scan set is not', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry'), { recursive: true });
  fs.mkdirSync(path.join(root, '.github/workflows'), { recursive: true });
  fs.mkdirSync(path.join(root, 'specs'), { recursive: true });
  fs.writeFileSync(path.join(root, '.foundry/hasher-ref'), 'v1.2.3\n');
  fs.writeFileSync(path.join(root, '.foundry/wiring-hash.pin'), 'x');
  fs.writeFileSync(path.join(root, '.github/workflows/drift.yml'), 'run: test -f .foundry/hasher-ref || exit 1\n');
  fs.writeFileSync(path.join(root, 'specs/old.md'), 'historically wrote .foundry/wiring-hash.pin');
  const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: loadRetiredCatalogue(CLI_DIR) });
  const by = Object.fromEntries(plan.rows.map((r) => [r.relPath, r]));
  assert.equal(by['.foundry/hasher-ref'].state, 'refused');
  assert.equal(by['.foundry/hasher-ref'].why, 'referenced');
  assert.deepEqual(by['.foundry/hasher-ref'].refs, ['.github/workflows/drift.yml']);
  assert.equal(by['.foundry/wiring-hash.pin'].state, 'stale', 'a spec mention is not wiring');
  assert.ok(renderRetiredArtifactRows(plan, { cleanup: true, phase: 'preview' })
    .includes('  [refused] .foundry/hasher-ref — still referenced by .github/workflows/drift.yml — left alone'));
  assert.equal(applyRetiredArtifacts(plan, root), 1);
  assert.equal(fs.readFileSync(path.join(root, '.foundry/hasher-ref'), 'utf-8'), 'v1.2.3\n');
});

test('a reference scan that cannot finish refuses every row (fail-closed)', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry'), { recursive: true });
  fs.writeFileSync(path.join(root, '.foundry/wiring-hash.pin'), 'x');
  fs.writeFileSync(path.join(root, 'README.md'), 'hello');
  const cat = loadRetiredCatalogue(CLI_DIR);
  const tight = { ...cat, reference_scan: { ...cat.reference_scan, max_bytes: 2 } };
  const plan = planRetiredArtifacts({ physicalRoot: root, catalogue: tight });
  assert.equal(plan.rows[0].state, 'refused');
  assert.equal(plan.rows[0].why, 'scan-incomplete');
  assert.match(renderRetiredArtifactRows(plan, { cleanup: true, phase: 'preview' })[0], /reference scan could not finish \(README\.md is larger than 2 bytes\)/);
  assert.equal(applyRetiredArtifacts(plan, root), 0);
});

test('the catalogue refuses a missing or malformed reference_scan', () => {
  const dir = scratch();
  const doc = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'retired-artifacts.json'), 'utf-8'));
  delete doc.reference_scan;
  fs.writeFileSync(path.join(dir, 'retired-artifacts.json'), JSON.stringify(doc));
  assert.throws(() => loadRetiredCatalogue(dir), /reference_scan/);
  doc.reference_scan = { files: ['../x'], dirs: [], root_suffixes: ['.md'], max_files: 1, max_bytes: 1 };
  fs.writeFileSync(path.join(dir, 'retired-artifacts.json'), JSON.stringify(doc));
  assert.throws(() => loadRetiredCatalogue(dir), /reference_scan/);
});

// PR #242 security review: the scan's own gaps.
test('reference scan: nested build/ dirs are read, a symlinked wiring file refuses all, a hook sourced by basename is kept', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.foundry'), { recursive: true });
  fs.mkdirSync(path.join(root, 'scripts/build'), { recursive: true });
  fs.mkdirSync(path.join(root, '.claude/hooks'), { recursive: true });
  fs.writeFileSync(path.join(root, '.foundry/hasher-ref'), 'x');
  fs.writeFileSync(path.join(root, '.foundry/wiring-hash.pin'), 'x');
  fs.writeFileSync(path.join(root, 'scripts/build/ci.sh'), 'cat ./.foundry/hasher-ref\n');           // nested build/, ./ prefix
  fs.writeFileSync(path.join(root, '.claude/hooks/zeta-exec-guard.sh'), '#!/bin/sh\n');
  fs.writeFileSync(path.join(root, '.claude/hooks/live.sh'), '. "$(dirname "$0")/zeta-exec-guard.sh"\n'); // sourced by basename
  const cat = loadRetiredCatalogue(CLI_DIR);
  let by = Object.fromEntries(planRetiredArtifacts({ physicalRoot: root, catalogue: cat }).rows.map((r) => [r.relPath, r]));
  assert.deepEqual(by['.foundry/hasher-ref'].refs, ['scripts/build/ci.sh']);
  assert.deepEqual(by['.claude/hooks/zeta-exec-guard.sh'].refs, ['.claude/hooks/live.sh']);
  assert.equal(by['.foundry/wiring-hash.pin'].state, 'stale');
  // a symlinked wiring file cannot be read without following it -> incomplete -> every row refused
  fs.symlinkSync('/etc/hosts', path.join(root, 'Makefile'));
  by = Object.fromEntries(planRetiredArtifacts({ physicalRoot: root, catalogue: cat }).rows.map((r) => [r.relPath, r]));
  assert.equal(by['.foundry/wiring-hash.pin'].why, 'scan-incomplete');
  assert.match(by['.foundry/wiring-hash.pin'].detail, /^Makefile (is a symlink|resolves outside the workspace)/);
});

test('reference scan: a directory row named through a child path is kept; the dir budget fails closed', () => {
  const root = scratch();
  fs.mkdirSync(path.join(root, '.claude/logs'), { recursive: true });
  fs.writeFileSync(path.join(root, 'CLAUDE.md'), 'tail .claude/logs/today.log');
  const cat = loadRetiredCatalogue(CLI_DIR);
  const row = planRetiredArtifacts({ physicalRoot: root, catalogue: cat }).rows.find((r) => r.relPath === '.claude/logs');
  assert.equal(row.why, 'referenced');
  fs.writeFileSync(path.join(root, 'CLAUDE.md'), 'nothing');
  fs.mkdirSync(path.join(root, 'scripts/a/b'), { recursive: true });
  const tight = { ...cat, reference_scan: { ...cat.reference_scan, max_dirs: 2 } };
  const r2 = planRetiredArtifacts({ physicalRoot: root, catalogue: tight }).rows.find((r) => r.relPath === '.claude/logs');
  assert.equal(r2.why, 'scan-incomplete');
});
