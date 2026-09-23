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
import { confinedJoin } from './util.mjs';

export const REPORT_REL = '.foundry/upgrade-report.json';
export const REPORT_SCHEMA_VERSION = 1;
export const NEXT_LINE = `next: run /foundry:post-upgrade in your next session (report: ${REPORT_REL})`;

/** The plugin version strings come from the marketplace manifest — remote content refreshed by
 * `claude plugin marketplace update` — and land in a file an agent later reads and acts on. Only a
 * version-shaped string is copied (PR #218 security review, Risk 3); anything else is `null`. */
const VERSION_RE = /^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]{1,40})?$/;
export function versionOrNull(v) {
  return typeof v === 'string' && v.length <= 64 && VERSION_RE.test(v) ? v : null;
}

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
    from_plugin_version: beforeEntry ? versionOrNull(beforeEntry.version) : null,
    to_plugin_version: (afterEntry && versionOrNull(afterEntry.version)) || versionOrNull(toPluginVersion),
    phases: (phases || []).map((p) => ({ name: p.name, verdict: p.verdict, ...(p.reason ? { reason: p.reason } : {}) })),
    amendments: amendmentsPlan
      ? { backfilled: amendmentsPlan.written ?? 0, present: amendmentsPlan.present, skipped: amendmentsPlan.skipped }
      : { backfilled: 0, present: 0, skipped: 0 },
    permissions_policy: seedRow ? (seedRow.action === 'create' ? 'created' : 'kept') : 'absent',
    drifted: (filePlan || []).filter((f) => f.action === 'drifted').map((f) => f.relPath),
  };
}

/** Write the report under the workspace root, creating `.foundry/` if needed. Overwrites.
 * Confined the way every other Phase-4 writer is (PR #218 security review, Risk 4): the target is
 * joined through `confinedJoin`, a `.foundry` that is a symlink or a leaf that is not a regular
 * file is REFUSED (returns null, nothing written) — the write can never land outside the
 * physically-resolved root through a planted link. */
export function writeUpgradeReport(physicalRoot, report) {
  const abs = confinedJoin(physicalRoot, REPORT_REL);
  if (abs === null) return null;
  const dir = path.dirname(abs);
  const dst = fs.lstatSync(dir, { throwIfNoEntry: false });
  if (dst && !dst.isDirectory()) return null;          // `.foundry` is a symlink or a file
  const lst = fs.lstatSync(abs, { throwIfNoEntry: false });
  if (lst && !lst.isFile()) return null;               // the leaf is a symlink or special
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(abs, `${JSON.stringify(report, null, 2)}\n`, 'utf-8');
  return abs;
}
