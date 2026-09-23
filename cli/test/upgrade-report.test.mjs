// upgrade-report.test.mjs — post-upgrade-skill (AC-PUS-1, AC-PUS-5): the report's shape and the
// updater's last line. The orchestration test in update-orchestration.test.mjs proves the wiring
// through a real run; these pin the pure builder and the writer.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  REPORT_REL, REPORT_SCHEMA_VERSION, NEXT_LINE, buildUpgradeReport, writeUpgradeReport,
} from '../src/upgradeReport.mjs';

const NOW = new Date('2026-09-23T12:00:00Z');

test('the last line names the skill and the report path, verbatim', () => {
  assert.equal(NEXT_LINE, 'next: run /foundry:post-upgrade in your next session (report: .foundry/upgrade-report.json)');
  assert.equal(REPORT_REL, '.foundry/upgrade-report.json');
});

test('buildUpgradeReport: every field, from a full run', () => {
  const report = buildUpgradeReport({
    beforeEntry: { name: 'foundry', version: '1.16.1' },
    afterEntry: { name: 'foundry', version: '1.17.0' },
    toPluginVersion: '1.17.0',
    phases: [
      { name: 'marketplace-refresh', verdict: 'changed' },
      { name: 'cleanup', verdict: 'skipped', reason: 'report-only' },
    ],
    filePlan: [
      { relPath: 'CLAUDE.md', action: 'drifted' },
      { relPath: '.foundry/permissions.yaml', action: 'create', seed: true },
    ],
    amendmentsPlan: { written: 3, present: 4, skipped: 1, applied: true },
    now: NOW,
  });
  assert.deepEqual(report, {
    schema_version: REPORT_SCHEMA_VERSION,
    ran_at: '2026-09-23T12:00:00.000Z',
    from_plugin_version: '1.16.1',
    to_plugin_version: '1.17.0',
    phases: [
      { name: 'marketplace-refresh', verdict: 'changed' },
      { name: 'cleanup', verdict: 'skipped', reason: 'report-only' },
    ],
    amendments: { backfilled: 3, present: 4, skipped: 1 },
    permissions_policy: 'created',
    drifted: ['CLAUDE.md'],
  });
});

test('buildUpgradeReport: a first install has no from-version; a kept seed reports kept; no manifest after falls back to the package pin', () => {
  const report = buildUpgradeReport({
    beforeEntry: null, afterEntry: null, toPluginVersion: '1.17.0', phases: [],
    filePlan: [{ relPath: '.foundry/permissions.yaml', action: 'kept', seed: true }],
    amendmentsPlan: null, now: NOW,
  });
  assert.equal(report.from_plugin_version, null);
  assert.equal(report.to_plugin_version, '1.17.0');
  assert.equal(report.permissions_policy, 'kept');
  assert.deepEqual(report.amendments, { backfilled: 0, present: 0, skipped: 0 });
  assert.deepEqual(report.drifted, []);
});

test('writeUpgradeReport: creates .foundry/, writes pretty JSON with a trailing newline, overwrites', () => {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'upr-'));
  const abs = writeUpgradeReport(root, { schema_version: 1, a: 1 });
  assert.equal(abs, path.join(root, REPORT_REL));
  assert.equal(fs.readFileSync(abs, 'utf-8'), '{\n  "schema_version": 1,\n  "a": 1\n}\n');
  writeUpgradeReport(root, { schema_version: 1, a: 2 });
  assert.equal(JSON.parse(fs.readFileSync(abs, 'utf-8')).a, 2);
});
