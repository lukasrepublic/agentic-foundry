// amendments-backfill.test.mjs — amendments-backfill (ER #214, AC-AMB-1..5).
//
// `/foundry:amend` refuses when a spec has no `## Amendments` section after its LAST
// `<!-- /normative -->` marker outside fenced code. This module appends the empty section to every
// such spec on the `--existing` reconcile and the upgrader's Phase 4, never touching a spec that
// already has one, has no marker, or is a symlink. tests/test_amendments_backfill.py cross-checks
// `classifySpec` against scripts/foundry-amend.py's own `amendments_section_ok`; these tests pin
// the module's behaviour over hermetic temp trees.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  AMENDMENTS_BLOCK, classifySpec, walkSpecs, planAmendmentsBackfill, applyAmendmentsBackfill,
  appendBytesFor, renderAmendmentsRow,
} from '../src/amendmentsBackfill.mjs';

function scratch() {
  return fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'amb-'));
}

const NORMATIVE = '# feat-x\n\n<!-- normative -->\n- **AC-X-1**: it works.\n<!-- /normative -->\n';
const WITH_SECTION = `${NORMATIVE}\n${AMENDMENTS_BLOCK}`;

function writeSpec(root, rel, text) {
  const abs = path.join(root, rel);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, text, 'utf-8');
  return abs;
}

// ---------------------------------------------------------------- classifySpec (AC-AMB-3 shape)
test('classifySpec: absent / present / no-marker, heading before the marker does not count', () => {
  assert.equal(classifySpec(NORMATIVE), 'absent');
  assert.equal(classifySpec(WITH_SECTION), 'present');
  assert.equal(classifySpec('# spec with no fences\n\n## Amendments\n'), 'no-marker');
  assert.equal(classifySpec('## Amendments\n\n<!-- normative -->\nx\n<!-- /normative -->\n'), 'absent');
});

test('classifySpec: a heading inside a fenced code block after the marker is not a section', () => {
  const fenced = `${NORMATIVE}\n\`\`\`md\n## Amendments\n\`\`\`\n`;
  assert.equal(classifySpec(fenced), 'absent');
  assert.equal(classifySpec(`${fenced}\n${AMENDMENTS_BLOCK}`), 'present');
});

test('classifySpec: only a heading after the LAST marker counts; CRLF tolerated', () => {
  const twoRegions = '<!-- normative -->\na\n<!-- /normative -->\n\n## Amendments\n\n<!-- normative -->\nb\n<!-- /normative -->\n';
  assert.equal(classifySpec(twoRegions), 'absent');
  assert.equal(classifySpec(NORMATIVE.replaceAll('\n', '\r\n')), 'absent');
  assert.equal(classifySpec(WITH_SECTION.replaceAll('\n', '\r\n')), 'present');
});

// ---------------------------------------------------------------- appendBytesFor
test('appendBytesFor: exactly one blank line separates the body from the heading', () => {
  assert.equal(appendBytesFor('x\n'), `\n${AMENDMENTS_BLOCK}`);
  assert.equal(appendBytesFor('x\n\n'), AMENDMENTS_BLOCK);
  assert.equal(appendBytesFor('x'), `\n\n${AMENDMENTS_BLOCK}`);
  assert.equal(appendBytesFor(''), AMENDMENTS_BLOCK);
});

// ---------------------------------------------------------------- walk + plan + apply
test('AC-AMB-1: append to every absent spec under specs/, count present and skipped, one row', () => {
  const root = scratch();
  const a = writeSpec(root, 'specs/features/p/d/c/feat-a.md', NORMATIVE);
  writeSpec(root, 'specs/features/p/d/c/feat-b.md', WITH_SECTION);
  writeSpec(root, 'specs/lifecycle/feat-c.md', '# no marker here\n');
  // ER #223: an adopter's delivery atoms are `spec-*.md`; any basename with a normative region counts
  const d = writeSpec(root, 'specs/delivery/spec-d.md', NORMATIVE);
  writeSpec(root, 'specs/README.md', '# index, no normative region\n');
  writeSpec(root, 'specs/features/p/d/c/acceptance-contract.yaml', 'not a spec\n');
  writeSpec(root, 'docs/feat-outside.md', NORMATIVE); // outside specs/: never seen
  const plan = planAmendmentsBackfill({ physicalRoot: root });
  assert.deepEqual(plan.toAppend.sort(), [a, d].sort());
  assert.equal(plan.present, 1);
  assert.equal(plan.skipped, 2);
  assert.equal(plan.total, 5);
  assert.equal(
    renderAmendmentsRow(plan),
    '  [amendments] backfilled 2 of 5 specs (1 already present, 2 skipped: no normative region)',
  );
  assert.equal(applyAmendmentsBackfill(plan), 2);
  assert.equal(fs.readFileSync(a, 'utf-8'), `${NORMATIVE}\n${AMENDMENTS_BLOCK}`);
  assert.equal(fs.readFileSync(d, 'utf-8'), `${NORMATIVE}\n${AMENDMENTS_BLOCK}`);
  assert.equal(fs.readFileSync(path.join(root, 'specs/README.md'), 'utf-8'), '# index, no normative region\n');
  assert.equal(classifySpec(fs.readFileSync(a, 'utf-8')), 'present');
  assert.equal(fs.readFileSync(path.join(root, 'docs/feat-outside.md'), 'utf-8'), NORMATIVE);
});

test('AC-AMB-2: a second run backfills 0 and issues no write (byte-stable, mtime untouched)', () => {
  const root = scratch();
  const a = writeSpec(root, 'specs/f/feat-a.md', NORMATIVE);
  applyAmendmentsBackfill(planAmendmentsBackfill({ physicalRoot: root }));
  const before = fs.statSync(a);
  const bytes = fs.readFileSync(a);
  const again = planAmendmentsBackfill({ physicalRoot: root });
  assert.deepEqual(again.toAppend, []);
  assert.equal(applyAmendmentsBackfill(again), 0);
  assert.equal(renderAmendmentsRow(again), '  [amendments] backfilled 0 of 1 specs (1 already present, 0 skipped: no normative region)');
  assert.deepEqual(fs.readFileSync(a), bytes);
  assert.equal(fs.statSync(a).mtimeMs, before.mtimeMs);
});

test('AC-AMB-2: dry-run (plan without apply) writes nothing and reports the would-be count', () => {
  const root = scratch();
  const a = writeSpec(root, 'specs/f/feat-a.md', NORMATIVE);
  const plan = planAmendmentsBackfill({ physicalRoot: root });
  assert.equal(renderAmendmentsRow(plan), '  [amendments] backfilled 1 of 1 specs (0 already present, 0 skipped: no normative region)');
  assert.equal(fs.readFileSync(a, 'utf-8'), NORMATIVE);
});

test('AC-AMB-2: a symlinked spec file is never written and is named in the row; a symlinked dir is not descended', () => {
  const root = scratch();
  const real = writeSpec(root, 'elsewhere/feat-real.md', NORMATIVE);
  fs.mkdirSync(path.join(root, 'specs/f'), { recursive: true });
  fs.symlinkSync(real, path.join(root, 'specs/f/feat-link.md'));
  fs.symlinkSync(path.join(root, 'elsewhere'), path.join(root, 'specs/linked-dir'));
  const plan = planAmendmentsBackfill({ physicalRoot: root });
  assert.equal(plan.total, 0);
  assert.equal(plan.symlinks.length, 1);
  applyAmendmentsBackfill(plan);
  assert.equal(fs.readFileSync(real, 'utf-8'), NORMATIVE);
  assert.match(renderAmendmentsRow(plan), /1 symlinked spec file\(s\) not touched: feat-link\.md$/);
});

test('AC-AMB-2: apply re-classifies right before writing, so a spec that gained the section between plan and apply is left alone', () => {
  const root = scratch();
  const a = writeSpec(root, 'specs/f/feat-a.md', NORMATIVE);
  const plan = planAmendmentsBackfill({ physicalRoot: root });
  fs.writeFileSync(a, WITH_SECTION, 'utf-8');
  assert.equal(applyAmendmentsBackfill(plan), 0);
  assert.equal(fs.readFileSync(a, 'utf-8'), WITH_SECTION);
});

test('CRLF spec: the appended block keeps one blank line before the heading', () => {
  const root = scratch();
  const a = writeSpec(root, 'specs/f/feat-a.md', NORMATIVE.replaceAll('\n', '\r\n'));
  applyAmendmentsBackfill(planAmendmentsBackfill({ physicalRoot: root }));
  const out = fs.readFileSync(a, 'utf-8');
  assert.ok(out.endsWith(`\r\n\n${AMENDMENTS_BLOCK}`));
  assert.equal(classifySpec(out), 'present');
});

test('no specs/ at all: an empty walk, no row (a fresh scaffold prints nothing extra)', () => {
  const root = scratch();
  assert.deepEqual(walkSpecs(root), { files: [], symlinks: [] });
  assert.equal(renderAmendmentsRow(planAmendmentsBackfill({ physicalRoot: root })), null);
});

// v1.17.1 security review Risks 1 and 2: the root is confined; the temp leaf is never a symlink.
test('a symlinked specs/ root is never walked; a planted temp-path symlink is never written through', () => {
  const root = scratch();
  const elsewhere = writeSpec(root, 'elsewhere/shared/feat-shared.md', NORMATIVE);
  fs.symlinkSync(path.join(root, 'elsewhere/shared'), path.join(root, 'specs'));
  const plan = planAmendmentsBackfill({ physicalRoot: root });
  assert.deepEqual(plan.toAppend, []);
  assert.equal(plan.total, 0);
  assert.equal(fs.readFileSync(elsewhere, 'utf-8'), NORMATIVE);

  const root2 = scratch();
  const a = writeSpec(root2, 'specs/f/feat-a.md', NORMATIVE);
  const target = path.join(root2, 'outside.md');
  fs.symlinkSync(target, `${a}.amendments-backfill.tmp`);
  const plan2 = planAmendmentsBackfill({ physicalRoot: root2 });
  assert.deepEqual(plan2.toAppend, [a]);
  assert.equal(applyAmendmentsBackfill(plan2), 0);
  assert.equal(fs.readFileSync(a, 'utf-8'), NORMATIVE);
  assert.equal(fs.existsSync(target), false);
  assert.equal(fs.lstatSync(`${a}.amendments-backfill.tmp`).isSymbolicLink(), true);
});
