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
import { createHash } from 'node:crypto';
import { confinedJoin } from './util.mjs';
import { scopeRecordsFor } from './pluginRefresh.mjs';
import { strictVersion, sanitizeUrl, RUN_ID_RE } from './upgradeCompat.mjs';

export const REPORT_REL = '.foundry/upgrade-report.json';
// v2 (feat-foundry-adoption-npx-upgrade-2-0): every v1 field keeps its v1 meaning; run_id, outcome,
// next_action, failure, previous_partial, cleanup, operator_review, compatibility and adoption are added.
export const REPORT_SCHEMA_VERSION = 2;
export const NEXT_LINE = `next: run /foundry:post-upgrade in your next session (report: ${REPORT_REL})`;
/** `NEXT_LINE` without its `next: ` prefix — the `complete` / `attention-needed` next_action. */
export const NEXT_ACTION_HANDOFF = NEXT_LINE.slice('next: '.length);
export const NEXT_ACTION_REPORT_UNWRITABLE = `make ${REPORT_REL} a regular path and re-run — do not run /foundry:post-upgrade until this run writes its report`;

export const OUTCOMES = Object.freeze(['complete', 'attention-needed', 'refused', 'partial']);
export const EXIT_CODE_OF = Object.freeze({ complete: 0, 'attention-needed': 2, refused: 1, partial: 1 });
export const PHASE_NAMES = Object.freeze(['marketplace-refresh', 'plugin-update', 'python-deps', 'cleanup', 'reinitialization']);
const PHASE_VERDICTS = Object.freeze(['changed', 'already current', 'skipped', 'failed', 'not-run']);
const WRITE_KINDS = Object.freeze([
  'managed', 'seed', 'permission-floor', 'marketplace-migration', 'managed-block', 'amendments-backfill',
  'statusline', 'local-retirement', 'manifest-true-up',
]);
const CLEANUP_STATES = Object.freeze(['moved', 'stale', 'referenced', 'refused']);

/** The last output line of every run that reaches an outcome (AC-NPX-11). */
export function formatLastLine({ outcome, nextAction, cutover }) {
  return `outcome: ${outcome} · next: ${nextAction} · cutover: ${cutover}`;
}

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
  installedBefore = null, installedAfter = null, afterEntry, toPluginVersion, phases, filePlan, amendmentsPlan, now = new Date(),
  updaterVersion = null, coreVersion = null, updaterPluginVersion = null,
  retiredArtifacts = null, localRetired = 0, written = [], removed = [], configDir = null, hostname = null,
  pythonDeps = null, v2 = null,
}) {
  const seedRow = (filePlan || []).find((f) => f.seed);
  const c = (v2 && v2.compatibility) || {};
  const a = (v2 && v2.adoption) || {};
  return {
    schema_version: REPORT_SCHEMA_VERSION,
    // ── v2 (feat-foundry-adoption-npx-upgrade-2-0, AC-NPX-2). A field the run did not reach is null,
    // or an empty list for a list field — never a substitute value.
    run_id: v2 && v2.runId ? v2.runId : null,
    outcome: v2 && v2.outcome ? v2.outcome : null,
    next_action: v2 && typeof v2.nextAction === 'string' ? v2.nextAction : null,
    failure: v2 && v2.failure ? v2.failure : null,
    previous_partial: v2 && v2.previousPartial ? v2.previousPartial : null,
    cleanup: v2 && Array.isArray(v2.cleanup) ? v2.cleanup : [],
    operator_review: v2 && Array.isArray(v2.operatorReview) ? v2.operatorReview : [],
    compatibility: {
      from_plugin_version: c.from_plugin_version !== undefined ? c.from_plugin_version : strictVersion(installedBefore),
      to_plugin_version: c.to_plugin_version !== undefined ? c.to_plugin_version : strictVersion(installedAfter),
      updater_version: strictVersion(updaterVersion),
      core_version: strictVersion(coreVersion),
      updater_plugin_version: strictVersion(updaterPluginVersion),
      advertised_version: c.advertised_version !== undefined ? c.advertised_version : null,
      enabled_scopes: c.enabled_scopes || [],
      sources: c.sources || [],
      session_load: c.session_load || [],
      diagnoses: c.diagnoses || [],
    },
    adoption: {
      mechanical_steps: a.mechanical_steps || PHASE_NAMES.map((phase) => ({ phase, verdict: 'not-run' })),
      instructions_pending: a.instructions_pending || [],
      cutover: a.cutover !== undefined ? a.cutover : null,
    },
    ran_at: now.toISOString(),
    // ER #222 (v1.17.1): THIS workspace's installed version from installed_plugins.json, captured
    // before any mutation — never the marketplace clone's advertised version, which is per machine
    // and already moved by the time Phase 1 reads it. null (no record, unreadable registry, a
    // first install) makes the skill list only the current version's CHANGELOG section.
    from_plugin_version: versionOrNull(installedBefore),
    // v1.18.0 (AC-V118C-5): what this workspace has INSTALLED after the run, from the platform's own
    // registry; the marketplace's advertised version only when the registry could not be read.
    to_plugin_version: versionOrNull(installedAfter) || (afterEntry && versionOrNull(afterEntry.version)) || versionOrNull(toPluginVersion),
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
          failed: amendmentsPlan.failed ?? 0,
          total: amendmentsPlan.total ?? ((amendmentsPlan.written ?? 0) + amendmentsPlan.present + amendmentsPlan.skipped) }
      : { backfilled: 0, present: 0, skipped: 0, failed: 0, total: 0 },
    // v1.18.2: whether python3 can import every module the plugin's scripts need, and what this run
    // installed. null when the phase did not run (a dry run records nothing).
    python_deps: pythonDeps
      ? { verdict: pythonDeps.verdict, installed: pythonDeps.installed || [], missing: pythonDeps.missing || [],
          ...(pythonDeps.python ? { python: pythonDeps.python } : {}), ...(pythonDeps.reason ? { reason: pythonDeps.reason } : {}) }
      : null,
    permissions_policy: seedRow ? (seedRow.action === 'create' ? 'created' : 'kept') : 'absent',
    drifted: (filePlan || []).filter((f) => f.action === 'drifted').map((f) => f.relPath),
    // hotfix-v1.17.4: what the sweep found (paths are workspace-relative catalogue entries, never free text)
    retired_artifacts: retiredArtifacts
      ? { present: retiredArtifacts.present, removed: retiredArtifacts.removed, refused: retiredArtifacts.refused }
      : { present: [], removed: 0, refused: 0 },
    settings_local_retired: localRetired,
    // v1.18.0 (AC-V118C-1/-2): every path this run wrote or removed — `/foundry:post-upgrade`
    // commits exactly the tracked ones as the first commit of its PR, so nothing the updater writes
    // stays uncommitted; `config_dir`/`hostname` say WHICH environment the versions describe (a
    // container's ~/.claude is its own volume), so a report from another environment is refused.
    written: (written || []).map((w) => ({ path: String(w.path), kind: String(w.kind) })),
    removed: (removed || []).map(String),
    config_dir: typeof configDir === 'string' ? configDir : null,
    hostname: typeof hostname === 'string' ? hostname : null,
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

// ── the settings diff behind operator_review (AC-NPX-5, AC-NPX-7) ───────────────────────────────

const isPlainObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);
const TIERS = ['allow', 'ask', 'deny'];
/** Keys whose before/after values are shown as themselves; every other value is `sha256:<hex>` of its JSON. */
const VALUE_KEYS = new Set(['enabledPlugins', 'extraKnownMarketplaces', 'permissions', 'statusLine', 'subagentStatusLine']);
const REMOVAL_NARROWS = new Set(['statusLine', 'subagentStatusLine', 'hooks', 'env']);

function canonical(v) {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  if (isPlainObject(v)) return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}`;
  return JSON.stringify(v === undefined ? null : v);
}

/** `sha256:` + hex of the value's JSON — what a value outside the shown keys is reduced to. */
export function digestOf(value) {
  return `sha256:${createHash('sha256').update(canonical(value)).digest('hex')}`;
}

const SAFE_LEAF_RE = /^[A-Za-z0-9._@:/+-]{1,200}$/;
function shownMarketplaceValue(v) {
  if (typeof v === 'string') {
    if (/^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(v)) return sanitizeUrl(v);
    return SAFE_LEAF_RE.test(v) ? v : digestOf(v);
  }
  if (Array.isArray(v)) return v.map(shownMarketplaceValue);
  if (isPlainObject(v)) return Object.fromEntries(Object.keys(v).sort().map((k) => [k, shownMarketplaceValue(v[k])]));
  return v === undefined ? null : v;
}

function shown(top, value) {
  if (value === undefined) return null;
  if (top === 'extraKnownMarketplaces') return shownMarketplaceValue(value);
  return VALUE_KEYS.has(top) ? value : digestOf(value);
}

const sameValue = (x, y) => canonical(x) === canonical(y);

function impactFor({ top, child, change, after }) {
  if (top.startsWith('//') || (child && String(child).startsWith('//'))) return 'neutral';
  if (top === 'enabledPlugins') return change === 'removed' || !after ? 'narrows' : 'widens';
  if (top === 'extraKnownMarketplaces') return change === 'removed' ? 'narrows' : 'widens';
  if (REMOVAL_NARROWS.has(top)) return change === 'removed' ? 'narrows' : 'widens';
  return 'widens';
}

/** The difference between two PARSED settings files, as operator_review rows
 * `{ file, key, impact, change, tier?, rule?, before?, after? }`. Granularity: each permission rule is
 * its own row (`key: permissions.<tier>`, `tier`, `rule`); each child of `enabledPlugins` and
 * `extraKnownMarketplaces` is its own row; every other top-level key is one row. An `env` or `hooks`
 * value, and any value outside the shown keys, is a `sha256:` digest — never the value. */
export function diffSettingsFile(file, before, after) {
  const b = isPlainObject(before) ? before : {};
  const a = isPlainObject(after) ? after : {};
  const rows = [];
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])].sort();
  for (const top of keys) {
    const bv = b[top];
    const av = a[top];
    if (sameValue(bv, av)) continue;
    if (top === 'permissions' && (bv === undefined || isPlainObject(bv)) && (av === undefined || isPlainObject(av))) {
      const bp = bv || {};
      const ap = av || {};
      for (const tier of TIERS) {
        const bl = Array.isArray(bp[tier]) ? bp[tier].filter((r) => typeof r === 'string') : [];
        const al = Array.isArray(ap[tier]) ? ap[tier].filter((r) => typeof r === 'string') : [];
        for (const rule of al.filter((r) => !bl.includes(r))) {
          rows.push({ file, key: `permissions.${tier}`, impact: tier === 'allow' ? 'widens' : 'narrows', change: 'added', tier, rule });
        }
        for (const rule of bl.filter((r) => !al.includes(r))) {
          rows.push({ file, key: `permissions.${tier}`, impact: tier === 'allow' ? 'narrows' : 'widens', change: 'removed', tier, rule });
        }
      }
      for (const k of [...new Set([...Object.keys(bp), ...Object.keys(ap)])].sort()) {
        if (TIERS.includes(k) || sameValue(bp[k], ap[k])) continue;
        const change = bp[k] === undefined ? 'added' : ap[k] === undefined ? 'removed' : 'changed';
        rows.push({ file, key: `permissions.${k}`, impact: 'widens', change, before: shown('permissions', bp[k]), after: shown('permissions', ap[k]) });
      }
    } else if ((top === 'enabledPlugins' || top === 'extraKnownMarketplaces')
      && (bv === undefined || isPlainObject(bv)) && (av === undefined || isPlainObject(av))) {
      const bo = bv || {};
      const ao = av || {};
      for (const k of [...new Set([...Object.keys(bo), ...Object.keys(ao)])].sort()) {
        if (sameValue(bo[k], ao[k])) continue;
        const change = bo[k] === undefined ? 'added' : ao[k] === undefined ? 'removed' : 'changed';
        rows.push({
          file, key: `${top}.${k}`, impact: impactFor({ top, child: k, change, after: ao[k] }), change,
          before: shown(top, bo[k]), after: shown(top, ao[k]),
        });
      }
    } else {
      const change = bv === undefined ? 'added' : av === undefined ? 'removed' : 'changed';
      rows.push({ file, key: top, impact: impactFor({ top, change, after: av }), change, before: shown(top, bv), after: shown(top, av) });
    }
  }
  return rows;
}

// ── previous_partial: evidence a refused rerun carries forward (AC-NPX-11, AC-NPX-7) ────────────

/** A workspace-relative path: not absolute, no `..`, no backslash or control character. */
export function safeRelPath(p) {
  return typeof p === 'string' && p.length > 0 && p.length <= 300 && !p.startsWith('/')
    && /^[A-Za-z0-9._@+/ -]+$/.test(p) && !p.split('/').includes('..');
}

/** Re-validate a `previous_partial` read from disk against the closed value set; an entry that fails
 * is DROPPED (never repaired). `null` when the run id itself is not a run id. */
export function sanitizePartial(pp) {
  if (!isPlainObject(pp) || typeof pp.run_id !== 'string' || !RUN_ID_RE.test(pp.run_id)) return null;
  const phases = (Array.isArray(pp.phases) ? pp.phases : [])
    .filter((x) => isPlainObject(x) && PHASE_NAMES.includes(x.name ?? x.phase) && PHASE_VERDICTS.includes(x.verdict))
    .map((x) => ({ name: x.name ?? x.phase, verdict: x.verdict }));
  const written = (Array.isArray(pp.written) ? pp.written : [])
    .filter((x) => isPlainObject(x) && safeRelPath(x.path) && WRITE_KINDS.includes(x.kind))
    .map((x) => ({ path: x.path, kind: x.kind }));
  const cleanup = (Array.isArray(pp.cleanup) ? pp.cleanup : [])
    .filter((x) => isPlainObject(x) && safeRelPath(x.path) && CLEANUP_STATES.includes(x.state)
      && (x.restore_from === null || x.restore_from === undefined || safeRelPath(x.restore_from)))
    .map((x) => ({
      path: x.path,
      referrers: (Array.isArray(x.referrers) ? x.referrers : []).filter(safeRelPath),
      state: x.state,
      restore_from: x.restore_from ?? null,
    }));
  return { run_id: pp.run_id, phases, written, cleanup };
}

/** The report this run replaces, or null (absent, not a regular file, too big, unparseable). */
export function readPreviousReport(physicalRoot) {
  const abs = confinedJoin(physicalRoot, REPORT_REL);
  if (abs === null) return null;
  const st = fs.lstatSync(abs, { throwIfNoEntry: false });
  if (!st || !st.isFile() || st.size > 4 * 1024 * 1024) return null;
  try {
    const doc = JSON.parse(fs.readFileSync(abs, 'utf-8'));
    return isPlainObject(doc) ? doc : null;
  } catch {
    return null;
  }
}

/** previous_partial for a refused run: from a `partial` report its run_id, phases, written and
 * cleanup; from a `refused` one its own previous_partial; otherwise null. */
export function previousPartialFrom(prev) {
  if (!isPlainObject(prev) || prev.schema_version !== 2) return null;
  if (prev.outcome === 'partial') {
    return sanitizePartial({ run_id: prev.run_id, phases: prev.phases, written: prev.written, cleanup: prev.cleanup });
  }
  if (prev.outcome === 'refused') return sanitizePartial(prev.previous_partial);
  return null;
}
