// gitignore-reconcile.test.mjs — feat-gitignore-block-reconcile (ER #177, AC-GBR-1..5).
//
// `.gitignore` already goes through the whole-file never-clobber plan (reconcile.mjs): absent ->
// create, byte-identical -> unchanged, anything else -> drifted, never written. That correctly
// protects an adopter's own lines, but it also means the FOUNDRY-RUNTIME-GITIGNORE-BEGIN/END block
// the template ships never converges once the file carries anything else — which is every existing
// workspace. This module reconciles ONLY that sentinel-delimited block; these tests drive it exactly
// the way run.mjs / update.mjs do, over hermetic temp directories.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  BEGIN_TOKEN, END_TOKEN, scanSentinels, loadDesiredBlock, resolveGitignoreTarget,
  planGitignoreBlock, reconcileGitignorePlan, applyGitignorePlan, writeGitignoreAtomically,
  renderGitignoreRow,
} from '../src/gitignoreReconcile.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMPLATES_DIR = path.join(CLI_DIR, 'templates');
const DESIRED = loadDesiredBlock(TEMPLATES_DIR);

// realpath: macOS's /var -> /private/var symlink otherwise defeats confinedJoin's own root check,
// exactly as run-orchestration.test.mjs's own `scratch()` comment explains.
function target() {
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'gbr-')));
}

function writeGitignore(dir, text) {
  fs.writeFileSync(path.join(dir, '.gitignore'), text);
}

function readGitignore(dir) {
  return fs.readFileSync(path.join(dir, '.gitignore'), 'utf-8');
}

/** The whole pipeline as run.mjs/update.mjs drive it: plan, then apply. */
function reconcile(dir) {
  const plan = reconcileGitignorePlan({ physicalRoot: dir, templatesDir: TEMPLATES_DIR });
  applyGitignorePlan(plan);
  return plan;
}

// A pre-#170 stale block: the bash applier's OWN (longer, annotated) sentinel line text — proving
// the scan is substring-based, not an exact-line match — carrying only the first three re-includes
// and missing ER #169's releases/decisions/permissions.yaml trio. This is the exact shape the
// charter's motivating defect left every existing adopter workspace holding.
const STALE_BLOCK = [
  `# ${BEGIN_TOKEN} (managed by scripts/foundry-apply-runtime-gitignore.sh -- do not edit by hand)`,
  '# .foundry/ runtime partitions are ignored by default; the designed-tracked set is re-included.',
  '/.foundry/*',
  '!/.foundry/README.md',
  '!/.foundry/build-provenance.yaml',
  '!/.foundry/stack-profile.lock',
  `# ${END_TOKEN} (re-run the applier to converge; do not edit by hand)`,
];

// ================================================================================================
// AC-GBR-1 — converge: adopter lines above and below the block survive byte-for-byte
// ================================================================================================

test('converges a stale block, preserving adopter lines above and below byte-for-byte', () => {
  const dir = target();
  const above = ['node_modules/', '# my own notes', ''];
  const below = ['', 'dist/', '*.log'];
  writeGitignore(dir, [...above, ...STALE_BLOCK, ...below].map((l) => `${l}\n`).join(''));

  const plan = reconcile(dir);
  assert.equal(plan.action, 'converged');

  const lines = readGitignore(dir).split('\n');
  const hasFinalNewline = lines[lines.length - 1] === '';
  const body = hasFinalNewline ? lines.slice(0, -1) : lines;

  assert.deepEqual(body.slice(0, above.length), above, 'adopter lines above the block changed');
  assert.deepEqual(body.slice(above.length, above.length + DESIRED.length), DESIRED,
    'the block was not replaced with the template\'s own');
  assert.deepEqual(body.slice(above.length + DESIRED.length), below, 'adopter lines below the block changed');
});

test('an identical block reports unchanged and is reported as such', () => {
  const dir = target();
  const content = ['keep-me/', ...DESIRED, 'also-keep-me/'].map((l) => `${l}\n`).join('');
  writeGitignore(dir, content);

  const plan = reconcileGitignorePlan({ physicalRoot: dir, templatesDir: TEMPLATES_DIR });
  assert.equal(plan.action, 'unchanged');
  assert.equal(renderGitignoreRow(plan), '  [unchanged] .gitignore (managed block)');
});

// ================================================================================================
// AC-GBR-2 — append: no sentinel block present
// ================================================================================================

test('appends the template block preceded by one blank line when no block exists', () => {
  const dir = target();
  const adopterLines = ['node_modules/', 'dist/'];
  writeGitignore(dir, adopterLines.map((l) => `${l}\n`).join(''));

  const plan = reconcile(dir);
  assert.equal(plan.action, 'appended');
  assert.equal(renderGitignoreRow(plan), '  [converged] .gitignore (managed block appended)');

  const lines = readGitignore(dir).split('\n');
  const body = lines[lines.length - 1] === '' ? lines.slice(0, -1) : lines;
  assert.deepEqual(body.slice(0, adopterLines.length), adopterLines, 'adopter lines changed');
  assert.equal(body[adopterLines.length], '', 'no blank line precedes the appended block');
  assert.deepEqual(body.slice(adopterLines.length + 1), DESIRED, 'the appended block does not match the template');
});

test('absent .gitignore is left entirely to the existing managed-file create path', () => {
  const dir = target();
  // no .gitignore written at all
  const plan = reconcileGitignorePlan({ physicalRoot: dir, templatesDir: TEMPLATES_DIR });
  assert.equal(plan, null, 'a null plan was expected for an absent .gitignore');
  assert.equal(renderGitignoreRow(plan), null, 'a null plan must render no row');
  assert.equal(fs.existsSync(path.join(dir, '.gitignore')), false, 'this module must not create the file itself');
});

// ================================================================================================
// AC-GBR-3 — malformed sentinel states, and symlink refusal, reported and NEVER written
// ================================================================================================

test('a line carrying both the BEGIN and END tokens is malformed', () => {
  const scan = scanSentinels([`# ${BEGIN_TOKEN} ${END_TOKEN}`]);
  assert.equal(scan.ok, false);
  assert.match(scan.reason, /carries both the BEGIN and END tokens/);
});

test('a BEGIN with no matching END is malformed', () => {
  const scan = scanSentinels([`# ${BEGIN_TOKEN}`, 'some line', 'no end in sight']);
  assert.equal(scan.ok, false);
  assert.match(scan.reason, /has no matching END sentinel/);
});

test('an END preceding the first BEGIN is malformed', () => {
  const scan = scanSentinels(['some line', `# ${END_TOKEN}`]);
  assert.equal(scan.ok, false);
  assert.match(scan.reason, /precedes the first BEGIN sentinel/);
});

test('two closed blocks is malformed', () => {
  const scan = scanSentinels([`# ${BEGIN_TOKEN}`, 'x', `# ${END_TOKEN}`, `# ${BEGIN_TOKEN}`, 'y', `# ${END_TOKEN}`]);
  assert.equal(scan.ok, false);
  assert.match(scan.reason, /2 managed blocks found/);
});

for (const [name, lines] of [
  ['both-tokens-one-line', [`# ${BEGIN_TOKEN} ${END_TOKEN}`]],
  ['begin-without-end', [`# ${BEGIN_TOKEN}`, 'trailing']],
  ['end-before-begin', ['leading', `# ${END_TOKEN}`]],
  ['two-blocks', [`# ${BEGIN_TOKEN}`, 'a', `# ${END_TOKEN}`, `# ${BEGIN_TOKEN}`, 'b', `# ${END_TOKEN}`]],
]) {
  test(`malformed .gitignore (${name}) is refused end-to-end and never written`, () => {
    const dir = target();
    const before = lines.map((l) => `${l}\n`).join('');
    writeGitignore(dir, before);
    const beforeBytes = fs.readFileSync(path.join(dir, '.gitignore'));

    const plan = reconcile(dir); // plan + apply — apply must be a no-op on refusal
    assert.equal(plan.action, 'refused');
    const row = renderGitignoreRow(plan);
    assert.match(row, /^ {2}\[refused] \.gitignore \(malformed managed block: .+\)$/);

    assert.deepEqual(fs.readFileSync(path.join(dir, '.gitignore')), beforeBytes,
      'a malformed .gitignore was written to despite the refusal');
  });
}

test('a symlinked .gitignore is refused the same way, and the link target is untouched', () => {
  const dir = target();
  const secret = path.join(dir, 'elsewhere-ignore-body');
  const secretBytes = Buffer.from('# not a foundry-managed file\n');
  fs.writeFileSync(secret, secretBytes);
  fs.symlinkSync(secret, path.join(dir, '.gitignore'));

  const target1 = resolveGitignoreTarget(dir);
  assert.equal(target1.present, true);
  assert.equal(target1.notRegular, true);

  const plan = reconcile(dir);
  assert.equal(plan.action, 'refused');
  assert.match(plan.reason, /not a regular file/);
  assert.equal(renderGitignoreRow(plan), '  [refused] .gitignore (malformed managed block: not a regular file (symlink or special file))');
  assert.deepEqual(fs.readFileSync(secret), secretBytes, 'the write followed the symlink to its target');
});

// ================================================================================================
// AC-GBR-4 — dry-run prints and writes nothing; a converged second run is byte-stable
// ================================================================================================

test('planning alone (the --dry-run shape) never writes', () => {
  const dir = target();
  writeGitignore(dir, [...STALE_BLOCK].map((l) => `${l}\n`).join(''));
  const before = fs.readFileSync(path.join(dir, '.gitignore'));
  const beforeStat = fs.statSync(path.join(dir, '.gitignore'));

  const plan = reconcileGitignorePlan({ physicalRoot: dir, templatesDir: TEMPLATES_DIR });
  assert.equal(plan.action, 'converged', 'fixture must actually need a converge for this test to prove anything');

  assert.deepEqual(fs.readFileSync(path.join(dir, '.gitignore')), before, 'planning wrote to the target');
  assert.equal(fs.statSync(path.join(dir, '.gitignore')).mtimeMs, beforeStat.mtimeMs, 'planning touched the file');
});

test('a second run over an already-converged file performs no write syscall (byte/inode-stable)', () => {
  const dir = target();
  writeGitignore(dir, [...STALE_BLOCK].map((l) => `${l}\n`).join(''));
  const first = reconcile(dir);
  assert.equal(first.action, 'converged');

  const afterFirstBytes = fs.readFileSync(path.join(dir, '.gitignore'));
  const afterFirstIno = fs.statSync(path.join(dir, '.gitignore')).ino;

  const second = reconcile(dir);
  assert.equal(second.action, 'unchanged', 'the second run did not see its own prior write as converged');

  assert.deepEqual(fs.readFileSync(path.join(dir, '.gitignore')), afterFirstBytes, 'the second run changed the bytes');
  assert.equal(fs.statSync(path.join(dir, '.gitignore')).ino, afterFirstIno, 'the second run rewrote the file');
});

test('a converge leaves no temp residue behind', () => {
  const dir = target();
  writeGitignore(dir, [...STALE_BLOCK].map((l) => `${l}\n`).join(''));
  reconcile(dir);
  const strays = fs.readdirSync(dir).filter((f) => f.includes('.tmp'));
  assert.deepEqual(strays, [], `temp residue left behind: ${strays}`);
});

// ================================================================================================
// planGitignoreBlock — the pure computation, isolated from the filesystem
// ================================================================================================

test('planGitignoreBlock is pure and mirrors the end-to-end action for each shape', () => {
  assert.equal(planGitignoreBlock({ currentLines: [...DESIRED], desiredBlock: DESIRED }).action, 'unchanged');
  assert.equal(planGitignoreBlock({ currentLines: ['x'], desiredBlock: DESIRED }).action, 'appended');
  assert.equal(planGitignoreBlock({ currentLines: [], desiredBlock: DESIRED }).action, 'appended');
  assert.deepEqual(planGitignoreBlock({ currentLines: [], desiredBlock: DESIRED }).nextLines, DESIRED,
    'appending to a truly empty file must not open it with a leading blank line');
  assert.equal(planGitignoreBlock({ currentLines: STALE_BLOCK, desiredBlock: DESIRED }).action, 'converged');
});

test('writeGitignoreAtomically replaces by rename, not in-place truncation', () => {
  const dir = target();
  writeGitignore(dir, 'a\nb\n');
  const gitignorePath = path.join(dir, '.gitignore');
  const inoBefore = fs.statSync(gitignorePath).ino;
  writeGitignoreAtomically(gitignorePath, ['a', 'b', 'c']);
  assert.notEqual(fs.statSync(gitignorePath).ino, inoBefore, 'the file was rewritten in place, not replaced');
  assert.equal(fs.readFileSync(gitignorePath, 'utf-8'), 'a\nb\nc\n');
});
