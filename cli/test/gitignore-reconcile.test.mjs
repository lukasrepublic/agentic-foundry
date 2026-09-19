// gitignore-reconcile.test.mjs — feat-gitignore-block-reconcile (ER #177, AC-GBR-1..5).
//
// `.gitignore` already goes through the whole-file never-clobber plan (reconcile.mjs): absent ->
// create, byte-identical -> unchanged, anything else -> drifted, never written. That correctly
// protects an adopter's own lines, but it also means the FOUNDRY-RUNTIME-GITIGNORE-BEGIN/END block
// the template ships never converges once the file carries anything else — which is every existing
// workspace. This module reconciles ONLY that sentinel-delimited block's INTERIOR; these tests drive
// it exactly the way run.mjs / update.mjs do, over hermetic temp directories.
//
// PR #179 review, load-bearing for most of this file: `cli/templates/gitignore.tmpl` ships BARE
// sentinel lines while `scripts/foundry-apply-runtime-gitignore.sh` writes ANNOTATED ones, and every
// bootstrapped workspace in practice carries the annotated form. Comparing/replacing whole lines
// (sentinels included) made the two tools flip a workspace's sentinel text back and forth on
// alternating runs. The fix — and what most of the tests below exist to pin — is: compare/replace
// only the interior, preserve whichever sentinel text a found block already has verbatim, and use
// the bash applier's own annotated form only when APPENDING a block that had no prior sentinel.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  BEGIN_TOKEN, END_TOKEN, ANNOTATED_BEGIN_LINE, ANNOTATED_END_LINE, scanSentinels,
  loadDesiredBlock, loadDesiredInterior, resolveGitignoreTarget, planGitignoreBlock,
  reconcileGitignorePlan, applyGitignorePlan, writeGitignoreAtomically, renderGitignoreRow,
} from '../src/gitignoreReconcile.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMPLATES_DIR = path.join(CLI_DIR, 'templates');
const DESIRED_INTERIOR = loadDesiredInterior(TEMPLATES_DIR);
// The template's OWN sentinel line text — bare, no annotation. Exactly what a fresh scaffold
// writes (scaffold.mjs materializes gitignore.tmpl verbatim), so this is the shape a brand-new
// workspace's block starts in, before any applier or reconcile run has touched it.
const TEMPLATE_BLOCK = loadDesiredBlock(TEMPLATES_DIR);
const BARE_BEGIN_LINE = TEMPLATE_BLOCK[0];
const BARE_END_LINE = TEMPLATE_BLOCK[TEMPLATE_BLOCK.length - 1];

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

/** Strip the file's own trailing-newline artifact from a `split('\n')` result. */
function body(lines) {
  return lines[lines.length - 1] === '' ? lines.slice(0, -1) : lines;
}

// A pre-#170 stale block carrying the bash applier's OWN annotated sentinel line text — proving
// the scan is substring-based, not an exact-line match — and only the first three re-includes,
// missing ER #169's releases/decisions/permissions.yaml trio. This is the exact shape the
// charter's motivating defect left every existing adopter workspace holding (PR #179 review: this
// annotated form, not the template's bare one, is what a workspace that ever ran the bash applier
// actually carries).
const STALE_INTERIOR = [
  '# .foundry/ runtime partitions are ignored by default; the designed-tracked set is re-included.',
  '/.foundry/*',
  '!/.foundry/README.md',
  '!/.foundry/build-provenance.yaml',
  '!/.foundry/stack-profile.lock',
];
const STALE_BLOCK_ANNOTATED = [ANNOTATED_BEGIN_LINE, ...STALE_INTERIOR, ANNOTATED_END_LINE];
const STALE_BLOCK_BARE = [BARE_BEGIN_LINE, ...STALE_INTERIOR, BARE_END_LINE];

// ================================================================================================
// AC-GBR-1 — converge: adopter lines survive byte-for-byte; the EXISTING sentinel line text is
// preserved verbatim (PR #179 review) regardless of whether it was annotated or bare
// ================================================================================================

test('converges an annotated-sentinel block with a stale interior; the sentinel lines are byte-identical afterwards', () => {
  const dir = target();
  const above = ['node_modules/', '# my own notes', ''];
  const below = ['', 'dist/', '*.log'];
  writeGitignore(dir, [...above, ...STALE_BLOCK_ANNOTATED, ...below].map((l) => `${l}\n`).join(''));

  const plan = reconcile(dir);
  assert.equal(plan.action, 'converged');

  const lines = body(readGitignore(dir).split('\n'));
  assert.deepEqual(lines.slice(0, above.length), above, 'adopter lines above the block changed');

  const blockStart = above.length;
  const blockEnd = blockStart + 1 + DESIRED_INTERIOR.length; // index of the (preserved) END line
  assert.equal(lines[blockStart], ANNOTATED_BEGIN_LINE, 'the existing annotated BEGIN line was rewritten');
  assert.deepEqual(lines.slice(blockStart + 1, blockEnd), DESIRED_INTERIOR, 'the interior did not converge onto the template');
  assert.equal(lines[blockEnd], ANNOTATED_END_LINE, 'the existing annotated END line was rewritten');
  assert.deepEqual(lines.slice(blockEnd + 1), below, 'adopter lines below the block changed');
});

test('converges a bare-sentinel block with a stale interior; the sentinel lines are byte-identical afterwards', () => {
  const dir = target();
  const above = ['# adopter comment'];
  const below = ['dist/'];
  writeGitignore(dir, [...above, ...STALE_BLOCK_BARE, ...below].map((l) => `${l}\n`).join(''));

  const plan = reconcile(dir);
  assert.equal(plan.action, 'converged');

  const lines = body(readGitignore(dir).split('\n'));
  const blockStart = above.length;
  const blockEnd = blockStart + 1 + DESIRED_INTERIOR.length;
  assert.equal(lines[blockStart], BARE_BEGIN_LINE, 'the existing bare BEGIN line was rewritten');
  assert.deepEqual(lines.slice(blockStart + 1, blockEnd), DESIRED_INTERIOR, 'the interior did not converge onto the template');
  assert.equal(lines[blockEnd], BARE_END_LINE, 'the existing bare END line was rewritten');
  assert.deepEqual(lines.slice(blockEnd + 1), below, 'adopter lines below the block changed');
});

test('an identical interior reports unchanged, regardless of the sentinel line text carried', () => {
  for (const [label, beginLine, endLine] of [
    ['bare (fresh scaffold shape)', BARE_BEGIN_LINE, BARE_END_LINE],
    ['annotated (bash-applier shape)', ANNOTATED_BEGIN_LINE, ANNOTATED_END_LINE],
  ]) {
    const dir = target();
    const content = ['keep-me/', beginLine, ...DESIRED_INTERIOR, endLine, 'also-keep-me/']
      .map((l) => `${l}\n`).join('');
    writeGitignore(dir, content);

    const plan = reconcileGitignorePlan({ physicalRoot: dir, templatesDir: TEMPLATES_DIR });
    assert.equal(plan.action, 'unchanged', `${label}: expected unchanged`);
    assert.equal(renderGitignoreRow(plan), '  [unchanged] .gitignore (managed block)', label);
  }
});

test('a second run after the bash applier itself converges the same file is unchanged', () => {
  // Exactly what scripts/foundry-apply-runtime-gitignore.sh would have just written: its own
  // annotated sentinel lines wrapping the SAME interior this module's template block carries.
  // This is the regression PR #179 caught: before the fix, this run rewrote the annotated
  // sentinels to bare, and running the bash applier again would have flipped them straight back.
  const dir = target();
  writeGitignore(
    dir,
    ['# adopter line', ANNOTATED_BEGIN_LINE, ...DESIRED_INTERIOR, ANNOTATED_END_LINE, 'dist/']
      .map((l) => `${l}\n`).join(''),
  );
  const before = fs.readFileSync(path.join(dir, '.gitignore'));

  const plan = reconcile(dir);
  assert.equal(plan.action, 'unchanged');
  assert.deepEqual(fs.readFileSync(path.join(dir, '.gitignore')), before,
    'a file already converged by the bash applier was rewritten');
});

// ================================================================================================
// AC-GBR-2 — append: no sentinel block present; the NEW block uses the applier's annotated form
// ================================================================================================

test('appends a new block preceded by one blank line, using the bash applier\'s own annotated sentinel form', () => {
  const dir = target();
  const adopterLines = ['node_modules/', 'dist/'];
  writeGitignore(dir, adopterLines.map((l) => `${l}\n`).join(''));

  const plan = reconcile(dir);
  assert.equal(plan.action, 'appended');
  assert.equal(renderGitignoreRow(plan), '  [converged] .gitignore (managed block appended)');

  const lines = body(readGitignore(dir).split('\n'));
  assert.deepEqual(lines.slice(0, adopterLines.length), adopterLines, 'adopter lines changed');
  assert.equal(lines[adopterLines.length], '', 'no blank line precedes the appended block');
  assert.equal(lines[adopterLines.length + 1], ANNOTATED_BEGIN_LINE,
    'the appended block used the template\'s bare sentinel instead of the applier\'s annotated one');
  assert.deepEqual(
    lines.slice(adopterLines.length + 2, adopterLines.length + 2 + DESIRED_INTERIOR.length),
    DESIRED_INTERIOR,
    'the appended interior does not match the template',
  );
  assert.equal(
    lines[adopterLines.length + 2 + DESIRED_INTERIOR.length],
    ANNOTATED_END_LINE,
    'the appended block used the template\'s bare sentinel instead of the applier\'s annotated one',
  );
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
  writeGitignore(dir, [...STALE_BLOCK_ANNOTATED].map((l) => `${l}\n`).join(''));
  const before = fs.readFileSync(path.join(dir, '.gitignore'));
  const beforeStat = fs.statSync(path.join(dir, '.gitignore'));

  const plan = reconcileGitignorePlan({ physicalRoot: dir, templatesDir: TEMPLATES_DIR });
  assert.equal(plan.action, 'converged', 'fixture must actually need a converge for this test to prove anything');

  assert.deepEqual(fs.readFileSync(path.join(dir, '.gitignore')), before, 'planning wrote to the target');
  assert.equal(fs.statSync(path.join(dir, '.gitignore')).mtimeMs, beforeStat.mtimeMs, 'planning touched the file');
});

test('a second run over an already-converged file performs no write syscall (byte/inode-stable)', () => {
  const dir = target();
  writeGitignore(dir, [...STALE_BLOCK_ANNOTATED].map((l) => `${l}\n`).join(''));
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
  writeGitignore(dir, [...STALE_BLOCK_ANNOTATED].map((l) => `${l}\n`).join(''));
  reconcile(dir);
  const strays = fs.readdirSync(dir).filter((f) => f.includes('.tmp'));
  assert.deepEqual(strays, [], `temp residue left behind: ${strays}`);
});

// ================================================================================================
// planGitignoreBlock — the pure computation, isolated from the filesystem
// ================================================================================================

test('planGitignoreBlock is pure and mirrors the end-to-end action for each shape', () => {
  assert.equal(
    planGitignoreBlock({
      currentLines: [BARE_BEGIN_LINE, ...DESIRED_INTERIOR, BARE_END_LINE],
      desiredInterior: DESIRED_INTERIOR,
    }).action,
    'unchanged',
  );
  assert.equal(planGitignoreBlock({ currentLines: ['x'], desiredInterior: DESIRED_INTERIOR }).action, 'appended');
  assert.equal(planGitignoreBlock({ currentLines: [], desiredInterior: DESIRED_INTERIOR }).action, 'appended');
  assert.deepEqual(
    planGitignoreBlock({ currentLines: [], desiredInterior: DESIRED_INTERIOR }).nextLines,
    [ANNOTATED_BEGIN_LINE, ...DESIRED_INTERIOR, ANNOTATED_END_LINE],
    'appending to a truly empty file must not open it with a leading blank line, and must use the annotated sentinel form',
  );

  const converged = planGitignoreBlock({ currentLines: STALE_BLOCK_ANNOTATED, desiredInterior: DESIRED_INTERIOR });
  assert.equal(converged.action, 'converged');
  assert.equal(converged.nextLines[0], ANNOTATED_BEGIN_LINE, 'the existing sentinel line was not preserved verbatim');
  assert.equal(converged.nextLines[converged.nextLines.length - 1], ANNOTATED_END_LINE,
    'the existing sentinel line was not preserved verbatim');
  assert.deepEqual(converged.nextLines.slice(1, -1), DESIRED_INTERIOR, 'the interior did not converge');
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
