// v1.18.2: the updater installs the plugin's missing Python runtime deps — only what is missing,
// --user outside a venv, one PEP 668 retry with --break-system-packages, never throws.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { loadPythonRequirements, probePythonDeps, pipArgs, ensurePythonDeps, renderPythonDepsRow } from '../src/pythonDeps.mjs';

const CLI_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REQS = loadPythonRequirements(CLI_DIR);

/** A fake spawnSync over a mutable set of importable modules; pip calls "install" by default. */
function fakePython({ importable = [], venv = false, pip = 'ok' } = {}) {
  const have = new Set(importable);
  const calls = [];
  const opts = [];
  const spawn = (bin, args, o) => {
    calls.push([bin, ...args]);
    opts.push(o);
    if (args[0] === '-c') {
      const mods = args.slice(2);
      return { status: 0, stdout: JSON.stringify({ python: '/usr/bin/python3', version: '3.12', venv, missing: mods.filter((m) => !have.has(m)) }) + '\n', stderr: '' };
    }
    if (args[0] === '-m' && args[1] === 'pip') {
      const breakSystem = args.includes('--break-system-packages');
      if (pip === 'pep668' && !breakSystem) return { status: 1, stdout: '', stderr: 'error: externally-managed-environment\n' };
      if (pip === 'nopip') return { status: 1, stdout: '', stderr: '/usr/bin/python3: No module named pip\n' };
      if (pip === 'fail') return { status: 1, stdout: '', stderr: 'ERROR: network unreachable\n' };
      for (const q of REQS) if (args.includes(q.requirement)) have.add(q.module);
      return { status: 0, stdout: '', stderr: '' };
    }
    return { status: 127, stdout: '', stderr: '' };
  };
  return { spawn, calls, opts };
}

test('requirements: the two modules the scripts import, exactly pinned', () => {
  assert.deepEqual(REQS.map((q) => q.module).sort(), ['jsonschema', 'yaml']);
  for (const q of REQS) assert.match(q.requirement, /^[A-Za-z0-9_.-]+==\d+(\.\d+)+$/);
});

test('all present: nothing installed, no pip call', () => {
  const f = fakePython({ importable: ['yaml', 'jsonschema'] });
  const r = ensurePythonDeps(REQS, { spawn: f.spawn });
  assert.equal(r.verdict, 'already current');
  assert.equal(f.calls.filter((c) => c[1] === '-m').length, 0);
});

test('one missing: only that one is installed, --user outside a venv', () => {
  const f = fakePython({ importable: ['yaml'] });
  const r = ensurePythonDeps(REQS, { spawn: f.spawn });
  assert.equal(r.verdict, 'changed');
  assert.deepEqual(r.installed, ['jsonschema']);
  const pip = f.calls.find((c) => c[1] === '-m');
  assert.ok(pip.includes('--user'));
  assert.ok(pip.includes('jsonschema==4.25.0'));
  assert.ok(!pip.some((a) => a.startsWith('PyYAML')), 'an importable module is never reinstalled');
});

test('inside a venv: no --user', () => {
  assert.ok(!pipArgs(REQS, { venv: true }).includes('--user'));
  assert.ok(pipArgs(REQS, { venv: false }).includes('--user'));
});

test('PEP 668: one retry with --break-system-packages (still --user)', () => {
  const f = fakePython({ pip: 'pep668' });
  const r = ensurePythonDeps(REQS, { spawn: f.spawn });
  assert.equal(r.verdict, 'changed');
  const pips = f.calls.filter((c) => c[1] === '-m');
  assert.equal(pips.length, 2);
  assert.ok(pips[1].includes('--break-system-packages') && pips[1].includes('--user'));
});

test('no pip / pip fails: failed, names what is missing and why, never throws', () => {
  const nopip = ensurePythonDeps(REQS, { spawn: fakePython({ pip: 'nopip' }).spawn });
  assert.equal(nopip.verdict, 'failed');
  assert.deepEqual(nopip.missing.sort(), ['jsonschema', 'yaml']);
  assert.match(nopip.reason, /pip is not available/);
  const fail = ensurePythonDeps(REQS, { spawn: fakePython({ pip: 'fail' }).spawn });
  assert.equal(fail.verdict, 'failed');
  assert.match(fail.reason, /network unreachable/);
  assert.match(renderPythonDepsRow(fail, REQS), /^ {2}\[failed\] python deps: still missing/);
});

test('python3 not runnable: skipped, no pip call', () => {
  const r = ensurePythonDeps(REQS, { spawn: () => ({ error: Object.assign(new Error('x'), { code: 'ENOENT' }) }) });
  assert.equal(r.verdict, 'skipped');
  assert.match(r.reason, /not runnable \(ENOENT\)/);
});

test('dry run: probes only, never installs', () => {
  const f = fakePython({ importable: [] });
  const r = ensurePythonDeps(REQS, { spawn: f.spawn, dryRun: true });
  assert.equal(r.verdict, 'would change');
  assert.equal(f.calls.filter((c) => c[1] === '-m').length, 0);
  assert.match(renderPythonDepsRow(r, REQS), /\[would install\]/);
});

test('probe output unreadable: not ok', () => {
  assert.equal(probePythonDeps(REQS, { spawn: () => ({ status: 0, stdout: 'garbage' }) }).ok, false);
});

test('security review: python never runs in the workspace; exact transitive pins; wheels only', () => {
  const f = fakePython({ importable: ['yaml'] });
  ensurePythonDeps(REQS, { spawn: f.spawn });
  for (const o of f.opts) {
    assert.equal(o.cwd, os.tmpdir(), 'probe and pip run from a neutral cwd');
    assert.equal(o.env.PYTHONSAFEPATH, '1');
  }
  const pip = f.calls.find((c) => c[1] === '-m');
  assert.ok(pip.includes('--only-binary=:all:'));
  const ci = pip.indexOf('-c');
  assert.ok(ci > 0 && /constraints\.txt$/.test(pip[ci + 1]), 'constraints file passed with -c');
  assert.ok(REQS.constraints.length >= 4 && REQS.constraints.every((c) => /^[A-Za-z0-9_.-]+==[\d.]+$/.test(c)));
});

test('the PEP 668 override is named in the row', () => {
  const r = ensurePythonDeps(REQS, { spawn: fakePython({ pip: 'pep668' }).spawn });
  assert.match(renderPythonDepsRow(r, REQS), /PEP 668 overridden/);
});
