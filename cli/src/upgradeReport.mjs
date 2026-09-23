// upgradeReport.mjs — post-upgrade-skill (AC-PUS-1).
//
// An upgrade has two halves. The deterministic half is this package: file reconcile, the
// Amendments backfill, the permissions seed. The judgement half — standing grants into policy,
// `requires_capabilities` on unfrozen contracts, a truth pass over the adopter's own prose,
// branch garbage collection — cannot honestly be a script and lives in the plugin's
// `/foundry:post-upgrade` skill. The hand-off between the two is this report: written on every
// completed run (always overwritten — it is a report, not a managed file), read by the skill, and
// named in the updater's last output line. `.foundry/*` is gitignored by the runtime block, so the
// report never lands in the adopter's repo.
import fs from 'node:fs';
import path from 'node:path';

export const REPORT_REL = '.foundry/upgrade-report.json';
export const REPORT_SCHEMA_VERSION = 1;
export const NEXT_LINE = `next: run /foundry:post-upgrade in your next session (report: ${REPORT_REL})`;

/** Pure: assemble the report object. `beforeEntry`/`afterEntry` are the marketplace manifest's
 * plugin entries around the refresh (either may be null); `filePlan` is the managed-file plan;
 * `amendmentsPlan` is the applied backfill plan (or null); `phases` is what renderSummary got. */
export function buildUpgradeReport({
  beforeEntry, afterEntry, toPluginVersion, phases, filePlan, amendmentsPlan, now = new Date(),
}) {
  const seedRow = (filePlan || []).find((f) => f.seed);
  return {
    schema_version: REPORT_SCHEMA_VERSION,
    ran_at: now.toISOString(),
    // null when no manifest was readable before the refresh (a first install) — the skill then
    // lists only the current version's CHANGELOG section (AC-PUS-1, Out of scope).
    from_plugin_version: beforeEntry && typeof beforeEntry.version === 'string' ? beforeEntry.version : null,
    to_plugin_version: afterEntry && typeof afterEntry.version === 'string' ? afterEntry.version : toPluginVersion,
    phases: (phases || []).map((p) => ({ name: p.name, verdict: p.verdict, ...(p.reason ? { reason: p.reason } : {}) })),
    amendments: amendmentsPlan
      ? { backfilled: amendmentsPlan.written ?? 0, present: amendmentsPlan.present, skipped: amendmentsPlan.skipped }
      : { backfilled: 0, present: 0, skipped: 0 },
    permissions_policy: seedRow ? (seedRow.action === 'create' ? 'created' : 'kept') : 'absent',
    drifted: (filePlan || []).filter((f) => f.action === 'drifted').map((f) => f.relPath),
  };
}

/** Write the report under the workspace root, creating `.foundry/` if needed. Overwrites. */
export function writeUpgradeReport(physicalRoot, report) {
  const abs = path.join(physicalRoot, REPORT_REL);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, `${JSON.stringify(report, null, 2)}\n`, 'utf-8');
  return abs;
}
