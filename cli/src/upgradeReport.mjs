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
import { scopeRecordsFor } from './pluginRefresh.mjs';

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

/** Pure: assemble the report object. `installedBefore` is this workspace's installed version from
 * `installedVersionBefore` (or null); `afterEntry` is the marketplace manifest's plugin entry after
 * the refresh (or null); `filePlan` is the managed-file plan;
 * `amendmentsPlan` is the applied backfill plan (or null); `phases` is what renderSummary got. */
export function buildUpgradeReport({
  installedBefore = null, afterEntry, toPluginVersion, phases, filePlan, amendmentsPlan, now = new Date(),
  updaterVersion = null, coreVersion = null, updaterPluginVersion = null,
  retiredArtifacts = null, localRetired = 0,
}) {
  const seedRow = (filePlan || []).find((f) => f.seed);
  return {
    schema_version: REPORT_SCHEMA_VERSION,
    ran_at: now.toISOString(),
    // ER #222 (v1.17.1): THIS workspace's installed version from installed_plugins.json, captured
    // before any mutation — never the marketplace clone's advertised version, which is per machine
    // and already moved by the time Phase 1 reads it. null (no record, unreadable registry, a
    // first install) makes the skill list only the current version's CHANGELOG section.
    from_plugin_version: versionOrNull(installedBefore),
    to_plugin_version: (afterEntry && versionOrNull(afterEntry.version)) || versionOrNull(toPluginVersion),
    // ER #228 (v1.17.2): WHICH updater ran. A stale npx-cached updater was indistinguishable from a
    // broken walk; the skill refuses a report whose updater was built for another plugin version.
    updater_version: versionOrNull(updaterVersion),
    core_version: versionOrNull(coreVersion),
    updater_plugin_version: versionOrNull(updaterPluginVersion),
    phases: (phases || []).map((p) => ({ name: p.name, verdict: p.verdict, ...(p.reason ? { reason: p.reason } : {}) })),
    // `total` is the walk's denominator (ER #228): backfilled + present + skipped == total, so a
    // walk that never opened a file cannot read as a clean pass.
    amendments: amendmentsPlan
      ? { backfilled: amendmentsPlan.written ?? 0, present: amendmentsPlan.present, skipped: amendmentsPlan.skipped,
          total: amendmentsPlan.total ?? ((amendmentsPlan.written ?? 0) + amendmentsPlan.present + amendmentsPlan.skipped) }
      : { backfilled: 0, present: 0, skipped: 0, total: 0 },
    permissions_policy: seedRow ? (seedRow.action === 'create' ? 'created' : 'kept') : 'absent',
    drifted: (filePlan || []).filter((f) => f.action === 'drifted').map((f) => f.relPath),
    // hotfix-v1.17.4: what the sweep found (paths are workspace-relative catalogue entries, never free text)
    retired_artifacts: retiredArtifacts
      ? { present: retiredArtifacts.present, removed: retiredArtifacts.removed, refused: retiredArtifacts.refused }
      : { present: [], removed: 0, refused: 0 },
    settings_local_retired: localRetired,
  };
}

/** The version THIS workspace had installed before the run (ER #222): the project-scope record
 * whose `projectPath` is `cwd`, else the user-scope record (no `projectPath`), else null. Reads the
 * registry object `readInstalledPluginsRegistry` returns; `ok: false` is null, never a guess. */
export function installedVersionBefore(registry, pluginKey, cwd) {
  if (!registry || !registry.ok) return null;
  const records = scopeRecordsFor(registry.doc, pluginKey).filter((r) => r && typeof r === 'object');
  const project = records.find((r) => r.projectPath && path.resolve(String(r.projectPath)) === path.resolve(cwd));
  const user = records.find((r) => !r.projectPath);
  const hit = project || user;
  return hit ? versionOrNull(hit.version) : null;
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
