// prompt-disclosure.test.mjs — node:test coverage for feat-foundry-wizard-prompt-disclosure
// (ER #88). The wizard's prompts must EXPLAIN themselves: a description above every question,
// one line per enumerated choice, and the default rendered ALONGSIDE the choices rather than
// shadowed by them. The pytest shim in tests/test_wizard_prompt_disclosure.py drives the
// mutation negative controls; this file is the in-process unit half.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { QUESTION_TABLE } from '../src/questions.mjs';
import { renderHelp } from '../src/argv.mjs';
import { defaultToken, promptSuffix, renderPromptBlock } from '../src/answers.mjs';

const byId = (id) => QUESTION_TABLE.find((r) => r.id === id);

test('AC-WPD-1: every record declares a non-empty description', () => {
  for (const rec of QUESTION_TABLE) {
    assert.equal(typeof rec.description, 'string', `${rec.id} has no description`);
    assert.ok(rec.description.trim().length > 0, `${rec.id} description is blank`);
  }
});

test('AC-WPD-2: the description renders above the prompt line', () => {
  const rec = byId('stageMode');
  const block = renderPromptBlock(rec);
  assert.ok(block.includes('How much process ceremony this workspace enforces.'));
  // the block is what precedes the prompt line; the prompt itself is never inside it
  assert.ok(!block.includes(`${rec.prompt}${promptSuffix(rec)}: `));
});

test('AC-WPD-3: one indented line per choice, between the description and the prompt', () => {
  const rec = byId('stageMode');
  const lines = renderPromptBlock(rec).split('\n');
  const descIdx = lines.findIndex((l) => l.includes('How much process ceremony'));
  const leanIdx = lines.findIndex((l) => l.trimStart().startsWith('lean'));
  const scaleIdx = lines.findIndex((l) => l.trimStart().startsWith('scale'));
  assert.ok(leanIdx > descIdx, 'choice lines must follow the description');
  assert.ok(scaleIdx > leanIdx, 'choices render in declared order');
  for (const i of [leanIdx, scaleIdx]) assert.match(lines[i], /^ {2}\S/, 'choice lines are indented');
  assert.match(lines[leanIdx], /development loop with lighter ceremony/);
  assert.match(lines[scaleIdx], /higher ceremony loop enforced end to end/);
});

// THE ER #88 REGRESSION. Asserted as a PROPERTY over the whole table, not as one literal string:
// the defect was a single-line mutually-exclusive ternary that reads correct at a glance.
test('AC-WPD-4: a record with a default renders it even when it declares choices', () => {
  for (const rec of QUESTION_TABLE) {
    if (rec.default === undefined) continue;
    const suffix = promptSuffix(rec);
    assert.ok(
      suffix.includes(defaultToken(rec)),
      `${rec.id}: default ${JSON.stringify(rec.default)} is missing from suffix ${JSON.stringify(suffix)}`,
    );
    if (rec.choices) {
      assert.ok(suffix.includes(`(${rec.choices.join('/')})`), `${rec.id}: choices missing from suffix`);
    }
  }
  assert.equal(promptSuffix(byId('stageMode')), ' (lean/scale) [lean]');
});

test('AC-WPD-5: a non-empty string default renders in brackets', () => {
  assert.equal(defaultToken(byId('stageMode')), '[lean]');
});

test('AC-WPD-6: an empty-string default renders [blank], never [] and never nothing', () => {
  for (const id of ['ghAccount', 'gitAuthor']) {
    assert.equal(defaultToken(byId(id)), '[blank]', `${id} must advertise that Enter is safe`);
  }
  assert.ok(promptSuffix(byId('ghAccount')).includes('[blank]'));
});

test('AC-WPD-7: a boolean record renders (y/n) and a letter default, never [false]', () => {
  const suffix = promptSuffix(byId('existing'));
  assert.equal(suffix, ' (y/n) [n]');
  assert.ok(!suffix.includes('false'), 'a yes/no question must not answer in booleans');
});

// The scope half is load-bearing: naming the artifacts WITHOUT stating that the effect is confined
// to this folder is what a live operator misread as "this changes my global identity".
test('AC-WPD-8: an out-of-project record states the scope of its effect', () => {
  const desc = byId('ghAccount').description;
  assert.match(desc, /global git identity is NOT changed/);
  assert.match(desc, /ONLY inside this folder/);
  assert.match(desc, /Leave blank to skip\. Nothing outside this folder is written\./);
});

test('AC-WPD-14: an out-of-project record names each artifact written outside the root', () => {
  const desc = byId('ghAccount').description;
  assert.match(desc, /~\/\.config\/git\/identity-<slug>/, 'the per-account identity file');
  assert.match(desc, /~\/\.gitconfig/, 'the global config the includeIf rules land in');
  assert.match(desc, /two "only inside this folder" rules/, 'both includeIf entries');
});

test('AC-WPD-9: the ghAccount record declares writesOutsideProject', () => {
  assert.equal(byId('ghAccount').writesOutsideProject, true);
  const others = QUESTION_TABLE.filter((r) => r.id !== 'ghAccount' && r.writesOutsideProject);
  assert.deepEqual(others, [], 'no other record claims out-of-project reach');
});

test('AC-WPD-10: --help renders each description as an indented continuation', () => {
  const help = renderHelp(QUESTION_TABLE);
  for (const rec of QUESTION_TABLE) {
    for (const line of rec.description.split('\n')) {
      if (!line.trim()) continue;
      assert.ok(help.includes(line.trim()), `${rec.id}: help is missing description line: ${line.trim()}`);
    }
  }
  // multi-line descriptions stay multi-line rather than being collapsed onto the flag line
  const flagLine = help.split('\n').find((l) => l.includes('--stage-mode'));
  assert.ok(!flagLine.includes('How much process ceremony'), 'description is not jammed onto the flag line');
});

test('AC-WPD-11: --help renders one line per choice', () => {
  const help = renderHelp(QUESTION_TABLE);
  const lines = help.split('\n');
  const flagIdx = lines.findIndex((l) => l.includes('--stage-mode'));
  const leanIdx = lines.findIndex((l, i) => i > flagIdx && /^\s+lean\s/.test(l));
  const scaleIdx = lines.findIndex((l, i) => i > flagIdx && /^\s+scale\s/.test(l));
  assert.ok(leanIdx > flagIdx && scaleIdx > leanIdx);
  assert.match(lines[leanIdx], /development loop with lighter ceremony/);
});

test('AC-WPD-12: every rendered line derives from the question table, not a second list', () => {
  const help = renderHelp(QUESTION_TABLE);
  // a table-only edit must propagate to BOTH surfaces with no other file touched
  const probe = QUESTION_TABLE.map((r) =>
    r.id === 'stageMode' ? { ...r, description: 'PROBE-SENTINEL-TEXT' } : r);
  assert.ok(renderHelp(probe).includes('PROBE-SENTINEL-TEXT'), '--help must read the table');
  assert.ok(renderPromptBlock(probe[2]).includes('PROBE-SENTINEL-TEXT'), 'the prompt must read the table');
  assert.ok(!help.includes('PROBE-SENTINEL-TEXT'), 'the real render is unaffected by the probe');
});
