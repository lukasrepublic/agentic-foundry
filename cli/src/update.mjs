// update.mjs — the `npx update-agentic-workspace` orchestrator: argv -> preflight -> preview ->
// Phase 1 (marketplace refresh + AC-UAW-4 migration) -> Phase 2 (plugin update) -> Phase 3
// (cleanup, opt-in) -> Phase 4 (reinitialization: managed-file + permission-floor reconcile) ->
// per-phase summary. No `claude` invocation lives in this file — every one goes through
// pluginRefresh.mjs's single allowlisted spawn site (AC-UAW-14); this file only decides WHICH
// invocations to make and WHEN, and performs Phase 4's file writes via the SAME never-clobber
// machinery run.mjs's create path already uses (cli/src/reconcile.mjs, cli/src/floorReconcile.mjs).
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { RefusalError, physicalResolve, confinedJoin } from './util.mjs';
import { loadMap, buildSettings } from './permissionFloor.mjs';
import { buildManagedFiles } from './scaffold.mjs';
import { planManagedFiles, applyPlan } from './reconcile.mjs';
import {
  resolveTarget, readTarget, applyAdditions, planReconcile, writeTargetAtomically, renderPlan,
} from './floorReconcile.mjs';
import { reconcileGitignorePlan, applyGitignorePlan, renderGitignoreRow } from './gitignoreReconcile.mjs';
import { planAmendmentsBackfill, applyAmendmentsBackfill, renderAmendmentsRow } from './amendmentsBackfill.mjs';
import { planManifestTrueUp, applyManifestTrueUp, renderManifestTrueUpRows } from './manifestTrueUp.mjs';
import {
  buildUpgradeReport, writeUpgradeReport, installedVersionBefore, versionOrNull, REPORT_REL,
  PHASE_NAMES, EXIT_CODE_OF, NEXT_ACTION_HANDOFF, NEXT_ACTION_REPORT_UNWRITABLE, formatLastLine,
  diffSettingsFile, readPreviousReport, previousPartialFrom, safeRelPath,
} from './upgradeReport.mjs';
import {
  planStatuslineWiring, applyStatuslineWiring, renderStatuslineRows, statuslineChanged, WRAPPERS, RETIRED_KEYS, desiredSettingsValue,
} from './statuslineWiring.mjs';
import {
  loadRetiredCatalogue, planRetiredArtifacts, applyRetiredArtifacts, renderRetiredArtifactRows, retiredTerms, RETIRED_ROOT_REL,
} from './retiredArtifacts.mjs';
import {
  CUTOVER_RECORD_REL, USER_SETTINGS_TOKEN, SCOPE_NAMES, makeRunId, strictVersion, readCutoverState, legacyOffCondition,
  collectSources, installedVersionOf, readSessionLoad, composeDiagnoses, effectOf,
  evalPreRows, evalPostRefreshRows, evalPostUpdateRows, inputReason, scanInstructionsPending,
} from './upgradeCompat.mjs';
import { loadPathClasses, classOf, formatRow } from './pathClasses.mjs';
import { planRetirements, applyRetirements, askRootKeys } from './floorReconcile.mjs';
import {
  ALLOWED_CLAUDE_SUBCOMMANDS, resolveClaudeOnPath, runClaude,
  defaultScopes, readScopeSettings, classifyMigration, migrationActions, migrateScope,
  readMarketplaceManifest, pluginEntryOf, manifestRefreshed,
  enabledScopeNames, runPluginUpdate,
  readInstalledPluginsRegistry, scopeRecordsFor,
} from './pluginRefresh.mjs';
import { runCleanupPhase } from './cleanup.mjs';
import { loadPythonRequirements, ensurePythonDeps, renderPythonDepsRow } from './pythonDeps.mjs';

export { ALLOWED_CLAUDE_SUBCOMMANDS };

/** v1.18.0 (AC-V118C-3): managed paths with their own reconciler on the update path. */
const SEPARATELY_RECONCILED = new Set(['.claude/settings.json', '.gitignore']);

/** The update entry point's OWN small flag table (Clarifications: "the update entry point carries
 * its own small flag table, disjoint from the wizard's") — deliberately NOT cli/src/argv.mjs +
 * QUESTION_TABLE, which is denied to the sibling cleanup atom and whose flag set is derived from
 * the wizard's prompts, not this command's. `--cleanup` is the cleanup atom's own opt-in. */
export function parseUpdateArgv(argv) {
  const values = { cleanup: false, help: false, dryRun: false, legacyMaintenance: false };
  for (const tok of argv) {
    if (tok === '--cleanup') values.cleanup = true;
    else if (tok === '--legacy-maintenance') values.legacyMaintenance = true;
    else if (tok === '--dry-run') values.dryRun = true;
    else if (tok === '--help') values.help = true;
    else throw new RefusalError(`unknown flag: ${tok}`, tok);
  }
  return values;
}

/** AC-UAW-11 — a pure formatter, unit-tested on its own with exactly the three phases this atom
 * itself defines (marketplace-refresh, plugin-update, reinitialization); the full orchestrator
 * below may print a fourth `cleanup` row once the sibling atom's phase is wired in, which is a
 * property of the RUNTIME composition, not of this formatter's own contract. */
/** hotfix-v1.17.4 (ER #236): the retirement-only plan over `.claude/settings.local.json`, which the
 * tracked-file reconcile never reads. Read-only; `{ rel, abs, plan, error }` — `plan` null when the
 * file is absent, not a regular file, or resolves outside the root; `error` set when it exists but
 * does not parse (reported, never guessed). A pinned `ask` row is retired only when the TRACKED file
 * carries the replacing wildcard row (`askCoveredBy`; PR #237 security review Risk 3). */
function planLocalRetirement({ physicalRoot, map, trackedSettingsObj }) {
  const rel = '.claude/settings.local.json';
  const abs = confinedJoin(physicalRoot, rel);
  if (!abs) return { rel, abs: null, plan: null, error: 'resolves outside the workspace' };
  try {
    if (!fs.lstatSync(abs).isFile()) return { rel, abs, plan: null, error: 'not a regular file' };
  } catch (e) {
    return { rel, abs, plan: null, error: e && e.code === 'ENOENT' ? null : e.message };
  }
  try {
    const settingsObj = readTarget(abs);
    const askCoveredBy = trackedSettingsObj ? askRootKeys(trackedSettingsObj, map) : new Set();
    return { rel, abs, settingsObj, plan: planRetirements({ settingsObj, map, askCoveredBy }), error: null };
  } catch (e) {
    return { rel, abs, plan: null, error: e.message };
  }
}

export function renderSummary(phases) {
  const lines = ['Summary:'];
  for (const p of phases) {
    const verdict = p.verdict === 'skipped' ? `skipped: ${p.reason}` : p.verdict;
    lines.push(`  [${p.name}] ${verdict}`);
  }
  return lines.join('\n');
}

function isInstalledInScopeFactory(registry, pluginKey, cwd) {
  return (scopeName) => {
    // `null` = cannot be determined. NEVER folded into `true` or `false` silently — an unreadable
    // registry must not be read as "installed everywhere" (which would silently suppress a real
    // orphaned-install healing) nor as "installed nowhere" (which would force an unneeded
    // reinstall on every run); classifyMigration surfaces this as its own reported trigger kind.
    if (!registry.ok) return null;
    const records = scopeRecordsFor(registry.doc, pluginKey);
    if (scopeName === 'user') return records.some((r) => r && !r.projectPath);
    return records.some((r) => r && r.projectPath && path.resolve(r.projectPath) === path.resolve(cwd));
  };
}

// ── 2.0 upgrade path (feat-foundry-adoption-npx-upgrade-2-0) ────────────────────────────────────
const NEXT_FIX = 'fix the cause named above, then rerun npx update-agentic-workspace';
const NEXT_PARTIAL = 'fix the cause named in failure, then rerun npx update-agentic-workspace';
const NEXT_NO_CLAUDE = 'put the claude CLI on PATH, then rerun npx update-agentic-workspace';
const NEXT_NOT_WORKSPACE = 'run npx update-agentic-workspace from inside an installed workspace';
const ERROR_CODE_RE = /^[A-Z][A-Z0-9_]{1,40}$/;
const PROJECT_SETTINGS_REL = '.claude/settings.json';
const LOCAL_SETTINGS_REL = '.claude/settings.local.json';

function readJsonObjectOrNull(abs) {
  // absent -> {} (nothing configured); present but unreadable / not a JSON object -> null
  let raw;
  try {
    raw = fs.readFileSync(abs, 'utf-8');
  } catch (e) {
    return e && e.code === 'ENOENT' ? {} : null;
  }
  try {
    const obj = JSON.parse(raw);
    return obj !== null && typeof obj === 'object' && !Array.isArray(obj) ? obj : null;
  } catch {
    return null;
  }
}

/** The report's `cleanup[]` and the preview's catalogue rows share this reading of a retired plan. */
function cleanupEntriesOf(plan) {
  return plan.rows.map((r) => ({
    path: r.relPath,
    referrers: (r.referrers || []).filter(safeRelPath),
    state: r.state === 'removed' ? 'moved' : r.state === 'stale' ? 'stale' : r.why === 'referenced' ? 'referenced' : 'refused',
    restore_from: r.state === 'removed' && r.restore_from ? r.restore_from : null,
  }));
}

/** Run the update command end to end. Never throws — every failure path is caught and turned into
 * an outcome (AC-NPX-11): `refused` when the run stopped before any mutation, `partial` after one;
 * both exit 1 and both write the report where a report can be written.
 *
 * `failAfterPhase: {phase, message}` is the AC-NPX-12 failure-injection seam: the options object
 * only — never an environment variable, and the published bin does not pass it. */
export async function runUpdate(argv, {
  cwd, configDir, homeDir, pkgDir, output, spawnEnv = process.env, updaterVersion = null, failAfterPhase = null,
}) {
  const lines = [];
  const print = (s) => {
    lines.push(s);
    output.write(`${s}\n`);
  };

  let flags;
  try {
    flags = parseUpdateArgv(argv);
  } catch (e) {
    print(e instanceof RefusalError ? `refused: ${e.message}` : `error: ${e.stack || e.message}`);
    return { exitCode: 1, output: lines.join('\n') };
  }

  if (flags.help) {
    print([
      'Usage: update-agentic-workspace [--dry-run] [--cleanup] [--legacy-maintenance] [--help]',
      '',
      '  --cleanup   Also quarantine retired workspace artifacts (moved, never deleted, under',
      '              .foundry/retired/<run-id>/ with their restore path recorded) and remove a stale',
      '              or duplicate marketplace registration. Previewed either way; only acted on under',
      '              this flag. Off by default.',
      '  --legacy-maintenance',
      '              Also append the empty "## Amendments" section to old specs and true up release',
      '              manifests. Edits retained history, so it runs only with this flag AND while no',
      '              cutover record exists at .foundry/migration/authority.yaml. Off by default.',
      '  --dry-run   Plan and print everything; write nothing and run no claude command.',
      '              Exits 1 if a pre-flight stop check fires, 2 if a pre-flight continue check fires',
      '              or the plan holds a drifted managed file or a refused .gitignore block, else 0.',
      '  --help      Show this help and exit.',
      '',
      'Exit codes: 0 complete, 2 attention-needed (finished; read compatibility.diagnoses in',
      '.foundry/upgrade-report.json), 1 refused (stopped, nothing changed) or partial (stopped',
      'after a change). The last line names the outcome, the next action and the cutover state;',
      'a zero exit does not mean the project has moved to 2.0 or that shared work was cut over.',
    ].join('\n'));
    return { exitCode: 0, output: lines.join('\n') };
  }

  const physicalRoot = physicalResolve(cwd);
  const st = {
    runId: makeRunId(), phase: 'preflight', mutated: false, stopped: false, error: null, reportable: true,
    nextOverride: null, firings: [], phases: [], written: [], removed: [],
    compat: { from: null, to: null, advertised: null, enabled: [], sources: [], sessionLoad: [] },
    cutover: 'unknown', instructions: [], cleanupEntries: [], reviewBefore: null,
    filePlan: null, amendmentsPlan: null, pythonDeps: null, retiredPlan: null, retiredRemoved: 0, localRetired: 0,
    gitignoreRefused: false, installedBefore: null, installedAfter: null, afterEntry: null, previousReport: null,
    corePkg: null, pins: null,
  };

  const afterPhase = (name) => {
    if (failAfterPhase && failAfterPhase.phase === name) throw new Error(String(failAfterPhase.message));
  };
  const wrote = (relPath, kind) => { if (!st.written.some((w) => w.path === relPath)) st.written.push({ path: relPath, kind }); };

  /** Everything up to and including the run's last phase; returns `{ dry: {exitCode} }` for a dry run. */
  const body = async () => {
    st.corePkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf-8'));
    const corePkg = st.corePkg;
    const pins = corePkg.foundry;
    st.pins = pins;
    const upgradeCfg = (pins.upgrade && typeof pins.upgrade === 'object') ? pins.upgrade : {};
    // ER #228: say WHICH updater this is, first, every run — a stale npx-cached updater looked
    // exactly like a broken backfill until the report and the log carried the version.
    print(`update-agentic-workspace ${versionOrNull(updaterVersion) || 'unknown'} (core create-agentic-workspace ${versionOrNull(corePkg.version) || 'unknown'}, built for plugin ${versionOrNull(pins.plugin_version) || 'unknown'})`);
    const marketplaceName = pins.marketplace_name;
    const marketplaceRepo = pins.marketplace_repo;
    const pluginKey = `${pins.plugin_name}@${pins.marketplace_name}`;
    const builtFor = strictVersion(pins.plugin_version);

    st.cutover = readCutoverState(physicalRoot);
    st.previousReport = readPreviousReport(physicalRoot);

    // ── AC-UAW-13(a): the claude executable must resolve on PATH before anything else runs ──────
    // R8: the RESOLVED path is what the rest of the run spawns — never the bare name re-resolved
    // against env.PATH a second time, which would leave this preflight proving something the
    // actual spawn calls do not rely on.
    const claudeBin = resolveClaudeOnPath(spawnEnv.PATH);
    if (!claudeBin) {
      st.nextOverride = NEXT_NO_CLAUDE;
      throw new RefusalError('claude executable not found on PATH');
    }

    // ── AC-UAW-15(b) / AC-NPX-3: read every scope BEFORE any mutation ───────────────────────────
    const scopes = defaultScopes({ cwd, configDir });
    const snapshot = [];
    const unreadable = [];
    for (const scope of scopes) {
      try {
        const read = readScopeSettings(scope);
        if (read.present) snapshot.push(read);
      } catch (e) {
        if (!(e instanceof RefusalError)) throw e;
        if (scope.required && /is absent/.test(e.message)) {
          st.reportable = false; // not a workspace: nothing to write a report into
          st.nextOverride = NEXT_NOT_WORKSPACE;
          throw e;
        }
        unreadable.push({
          file: scope.name === 'project' ? PROJECT_SETTINGS_REL : USER_SETTINGS_TOKEN,
          reason: /does not parse/.test(e.message) ? 'does not parse as a JSON object' : 'unreadable',
        });
      }
    }
    // AC-NPX-5: the parsed content of all three settings files before the first mutation
    st.reviewBefore = [
      { file: PROJECT_SETTINGS_REL, abs: path.join(cwd, '.claude', 'settings.json') },
      { file: LOCAL_SETTINGS_REL, abs: path.join(cwd, '.claude', 'settings.local.json') },
      { file: USER_SETTINGS_TOKEN, abs: path.join(configDir, 'settings.json') },
    ].map((f) => ({ ...f, obj: readJsonObjectOrNull(f.abs) }));

    if (unreadable.length > 0) {
      st.firings.push({ code: 'input-unreadable', vars: { files: unreadable } });
      return finishPreStop(flags.dryRun ? 'dry' : 'real', null);
    }

    const enabledOld = enabledScopeNames(snapshot, pluginKey);
    const registry = readInstalledPluginsRegistry(configDir);
    const isInstalled = isInstalledInScopeFactory(registry, pluginKey, cwd);
    // ER #222: the report's `from` is what THIS workspace had installed, read before any mutation.
    st.installedBefore = installedVersionBefore(registry, pluginKey, cwd);
    st.compat.from = installedVersionOf(registry, pluginKey, cwd);

    const migrations = [];
    const indeterminateInstalledness = [];
    for (const scopeSnap of snapshot) {
      const trigger = classifyMigration(scopeSnap, { marketplaceName, pluginKey, isInstalled });
      if (!trigger) continue;
      if (trigger.kind === 'indeterminate-installedness') {
        indeterminateInstalledness.push(scopeSnap.name);
      } else {
        migrations.push({ scopeSnap, trigger });
      }
    }

    // ── Phase 4's plan, computed but NOT applied yet — needed for the preview below ─────────────
    const map = loadMap(path.join(pkgDir, 'permission-floor.json'));
    const shippedSettings = buildSettings(map, pins);
    const settingsBytes = Buffer.from(`${JSON.stringify(shippedSettings, null, 2)}\n`, 'utf-8');
    const managedFiles = buildManagedFiles({
      templatesDir: path.join(pkgDir, 'templates'),
      physicalRoot,
      projectName: path.basename(physicalRoot),
      stageMode: 'lean',
      settingsBytes,
    });
    const filePlan = planManagedFiles(managedFiles);
    st.filePlan = filePlan;
    // v1.18.0 (AC-V118C-3): on the UPDATE path, a present file is never "drifted" just because it
    // is not byte-identical to a fresh scaffold. `.claude/settings.json` and `.gitignore` each have
    // their own reconciler below (the whole-file compare could never pass: it has no statusLine key
    // and no operator rows, so every run exited 2); CLAUDE.md and foundry-project.json are
    // operator-owned after creation; the framework READMEs are create-only and reported `kept`.
    // What STAYS `drifted` is a managed path that is present but not a regular file (a link, a
    // directory): nothing can be reconciled there, and the run says so (AC-NPX-11 attention).
    for (const f of filePlan) {
      if (f.action !== 'drifted') continue;
      if (SEPARATELY_RECONCILED.has(f.relPath)) { f.action = 'reconciled'; continue; }
      const lst = fs.lstatSync(f.absPath, { throwIfNoEntry: false });
      f.action = lst && lst.isFile() ? 'kept' : 'drifted';
    }

    // This is a PREVIEW-ONLY computation: `.claude/settings.json` is also `project` scope's
    // settings file, and Phase 1's migration (below) may write to that SAME path. Applying THIS
    // captured plan verbatim in Phase 4 would silently clobber whatever Phase 1 just wrote —
    // Phase 4 therefore re-reads and recomputes the floor plan fresh, right before it writes.
    const floorTarget = resolveTarget(physicalRoot);
    const trackedObj = floorTarget.present ? readTarget(floorTarget.path) : null;
    // AC-FRR-1 (ER #199, review round 1): retirement first, additions planned against the
    // POST-retirement rule set — planReconcile is the ONLY entry point either the preview here or
    // Phase 4 below should use; see its own comment in floorReconcile.mjs.
    const previewReconcile = floorTarget.present
      ? planReconcile({
        settingsObj: trackedObj, map, pins,
        pluginRootExpansion: [], unreadableOrigins: [], home: homeDir,
      })
      : null;
    const previewFloorPlan = previewReconcile ? previewReconcile.additionsPlan : null;
    const previewRetirementPlan = previewReconcile ? previewReconcile.retirementPlan : null;

    // gitignore-block-reconcile (ER #177, AC-GBR-1): PREVIEW-ONLY, same caveat as previewFloorPlan.
    const templatesDir = path.join(pkgDir, 'templates');
    const previewGitignorePlan = reconcileGitignorePlan({ physicalRoot, templatesDir });
    const retiredCatalogue = loadRetiredCatalogue(pkgDir);
    const pathTable = loadPathClasses(pkgDir);
    const classFor = (rel) => classOf(pathTable, rel, { catalogueEntries: retiredCatalogue.entries.map((e) => e.path) });
    const lp = planLocalRetirement({ physicalRoot, map, trackedSettingsObj: trackedObj });
    const previewStatusline = planStatuslineWiring({ physicalRoot, templatesDir });
    const previewRetired = planRetiredArtifacts({ physicalRoot, catalogue: retiredCatalogue });
    // Phase 4 unwires the retired `subagentStatusLine` key BEFORE it re-plans the sweep, so the one
    // catalogue entry that key names (and nothing else names) is `stale` by then although the plan
    // read from disk right now still sees it referenced. The preview must show what will happen.
    for (const k of previewStatusline.keys || []) {
      if (k.action !== 'unwired') continue;
      const rel = (RETIRED_KEYS.find((x) => x.key === k.key) || {}).rel;
      const row = previewRetired.rows.find((r) => r.relPath === rel);
      if (row && row.state === 'refused' && row.why === 'referenced' && row.referrers.length === 1 && row.referrers[0] === PROJECT_SETTINGS_REL) {
        row.state = 'stale'; row.why = null; row.refs = undefined; row.referrers = [];
        previewRetired.present += 1; previewRetired.refused -= 1;
      }
    }

    // ── AC-NPX-2/-3: scopes, registrations, versions — then the pre rows ─────────────────────────
    const userSnap = snapshot.find((s) => s.name === 'user');
    const projectSnap = snapshot.find((s) => s.name === 'project');
    const enabledIn = (obj) => Boolean(obj && obj.enabledPlugins && obj.enabledPlugins[pluginKey]);
    st.compat.enabled = SCOPE_NAMES.filter((n) => (n === 'user' ? enabledIn(userSnap && userSnap.obj)
      : n === 'project' ? enabledIn(projectSnap && projectSnap.obj) : enabledIn(lp.settingsObj)));
    const { sources, foreign } = collectSources({
      configDir, marketplaceName, marketplaceRepo, supportedSources: upgradeCfg.supported_sources,
      settingsFiles: [
        { location: PROJECT_SETTINGS_REL, scope: 'project', obj: projectSnap ? projectSnap.obj : null },
        { location: USER_SETTINGS_TOKEN, scope: 'user', obj: userSnap ? userSnap.obj : null },
        { location: LOCAL_SETTINGS_REL, scope: 'local', obj: lp.settingsObj || null },
      ],
    });
    st.compat.sources = sources;
    const skipped = [];
    if (lp.error) skipped.push({ file: LOCAL_SETTINGS_REL, reason: inputReason(lp.error) });
    for (const r of previewRetired.rows) {
      if (r.why !== 'scan-incomplete') continue;
      const first = String(r.detail || '').split(' ')[0];
      if (safeRelPath(first) && !skipped.some((x) => x.file === first)) skipped.push({ file: first, reason: inputReason(r.detail) });
    }
    st.firings.push(...evalPreRows({
      unreadable: [], foreign, installedBefore: st.compat.from, builtFor, supportedFromMin: upgradeCfg.supported_from_min,
      skipped, marketplaceRepo, pluginKey,
    }));
    st.instructions = scanInstructionsPending(physicalRoot, retiredTerms(retiredCatalogue));

    const legacyOff = legacyOffCondition({ flag: flags.legacyMaintenance, cutover: st.cutover });
    const legacyRun = legacyOff === null;
    const previewAmendments = legacyRun ? planAmendmentsBackfill({ physicalRoot }) : null;
    const previewTrueUp = legacyRun ? planManifestTrueUp({ physicalRoot }) : null;

    // ── AC-UAW-7 / AC-NPX-4: the preview, before the first `claude` invocation and the first write
    const runDir = `${RETIRED_ROOT_REL}/${st.runId}`;
    const previewLines = [
      flags.dryRun ? 'DRY RUN — the plan below is printed; nothing will be written and no claude command will run.'
        : 'PLAN — printed before anything is written (rows below describe what the run will do):',
      'The following claude invocations will be made:',
    ];
    const invokeRows = [];
    for (const { scopeSnap, trigger } of migrations) {
      for (const args of migrationActions(trigger, {
        scope: scopeSnap.name, marketplaceName, marketplaceRepo, pluginKey,
      })) {
        previewLines.push(`  claude ${args.join(' ')}`);
        invokeRows.push(`claude ${args.join(' ')}`);
      }
    }
    previewLines.push(`  claude plugin marketplace update ${marketplaceName}`);
    invokeRows.push(`claude plugin marketplace update ${marketplaceName}`);
    for (const scopeName of enabledOld) {
      previewLines.push(`  claude plugin update ${pluginKey} --scope ${scopeName}`);
      invokeRows.push(`claude plugin update ${pluginKey} --scope ${scopeName}`);
    }
    // C3 / AC-UAW-7: the concrete cleanup candidate list cannot be known until Phases 1-2 have
    // refreshed the manifest, so it cannot be named here by path — but the SHAPE of what it may
    // do, and whether this run can act on it at all, is known up front and disclosed here rather
    // than only in the phase's own later, post-mutation block.
    const cleanupCacheRoot = path.join(configDir, 'plugins', 'cache', marketplaceName, pins.plugin_name);
    previewLines.push(
      `  cleanup phase (${flags.cleanup ? 'will remove what it finds' : 'report-only — nothing removed without --cleanup'}):`,
    );
    previewLines.push(`    may prune superseded versions under ${cleanupCacheRoot}`);
    previewLines.push(`    may invoke claude plugin marketplace remove <name> --scope <scope> for a stale/duplicate registration`);
    previewLines.push('The following workspace paths will be reconciled (never-clobber):');
    for (const f of filePlan) previewLines.push(`  [${f.action}] ${f.relPath}`);
    if (previewFloorPlan) {
      previewLines.push(`  [permission-floor] .claude/settings.json: would add deny=${previewFloorPlan.additions.deny.length} (the floor writes only deny rows; foundry scripts are allowed by the plugin's PreToolUse hook)`);
      if (previewRetirementPlan && previewRetirementPlan.total > 0) {
        const r = previewRetirementPlan.retirements;
        previewLines.push(`  [permission-floor] .claude/settings.json: would retire allow=${r.allow.length}, ask=${r.ask.length}, deny=${(r.deny || []).length}`);
      }
    } else {
      previewLines.push('  [permission-floor] .claude/settings.json absent — left to the create path');
    }
    const previewGitignoreRow = renderGitignoreRow(previewGitignorePlan);
    if (previewGitignoreRow) previewLines.push(previewGitignoreRow);
    // amendments-backfill (ER #214, AC-AMB-1) and the manifest true-up: PREVIEW-ONLY like the rows
    // above; Phase 4 re-plans fresh from disk. AC-NPX-9: planned at all only under the flag while
    // the cutover is pending — otherwise the preview names the condition that kept them off.
    if (legacyRun) {
      const previewAmendmentsRow = renderAmendmentsRow(previewAmendments);
      if (previewAmendmentsRow) previewLines.push(previewAmendmentsRow);
      previewLines.push(...renderManifestTrueUpRows(previewTrueUp));
    } else {
      previewLines.push(`  [legacy-maintenance] off — ${legacyOff}: the Amendments backfill and the release-manifest true-up do not run (cutover record: ${CUTOVER_RECORD_REL})`);
    }
    // statusline-wiring (AC-SLW-1/-2): PREVIEW-ONLY rows; Phase 4 re-plans fresh from disk.
    previewLines.push(...renderStatuslineRows(previewStatusline));
    // retired-artifacts (hotfix-v1.17.4, ER #236): PREVIEW-ONLY rows; Phase 4 re-plans fresh from disk.
    previewLines.push(...renderRetiredArtifactRows(previewRetired, { cleanup: flags.cleanup, phase: 'preview', runDir }));
    // settings.local.json retirement (hotfix-v1.17.4): PREVIEW-ONLY row (PR #237 review Risk 2 —
    // every Phase 4 write is announced before the first write happens); Phase 4 re-plans fresh.
    if (lp.plan && lp.plan.total > 0) previewLines.push(`  [permission-floor] ${lp.rel}: would retire allow=${lp.plan.retirements.allow.length}, ask=${lp.plan.retirements.ask.length}, deny=${(lp.plan.retirements.deny || []).length} (never adds)`);
    else if (lp.error) previewLines.push(`  [permission-floor] ${lp.rel}: would not be reconciled (${lp.error})`);
    // v1.18.2: python deps — the preview only probes (nothing installed before the first write).
    const pythonRequirements = loadPythonRequirements(pkgDir);
    previewLines.push(renderPythonDepsRow(ensurePythonDeps(pythonRequirements, { env: spawnEnv, python: spawnEnv.FOUNDRY_PYTHON || 'python3', dryRun: true }), pythonRequirements));

    // ── AC-NPX-4/-5: one `[<effect>] <class> <target>` row per planned effect ───────────────────
    const effectRows = [];
    for (const target of invokeRows) effectRows.push(formatRow('invoke', 'framework-owned', target));
    for (const { scopeSnap } of migrations) {
      effectRows.push(formatRow('migrate', 'framework-owned', scopeSnap.name === 'project' ? PROJECT_SETTINGS_REL : USER_SETTINGS_TOKEN));
    }
    const statuslineKeysChange = previewStatusline.settingsPresent
      && previewStatusline.keys.some((k) => k.action === 'wired' || k.action === 'unwired');
    const settingsRewrite = Boolean(previewFloorPlan && (previewFloorPlan.total > 0 || (previewRetirementPlan && previewRetirementPlan.total > 0)))
      || statuslineKeysChange;
    for (const f of filePlan) {
      if (f.relPath === PROJECT_SETTINGS_REL && f.action === 'reconciled') {
        effectRows.push(formatRow(settingsRewrite ? 'reconcile' : 'keep', classFor(f.relPath), f.relPath));
      } else if (f.relPath === '.gitignore' && f.action === 'reconciled') {
        const gi = previewGitignorePlan && (previewGitignorePlan.action === 'converged' || previewGitignorePlan.action === 'appended');
        effectRows.push(formatRow(gi ? 'reconcile' : 'keep', classFor(f.relPath), f.relPath));
      } else {
        effectRows.push(formatRow(f.action === 'create' ? 'create' : 'keep', classFor(f.relPath), f.relPath));
      }
    }
    if (!floorTarget.present && settingsRewrite) effectRows.push(formatRow('reconcile', classFor(PROJECT_SETTINGS_REL), PROJECT_SETTINGS_REL));
    if (lp.plan && lp.plan.total > 0) effectRows.push(formatRow('retire-rows', classFor(lp.rel), lp.rel));
    for (const f of previewStatusline.files || []) {
      if (f.action === 'create') effectRows.push(formatRow('create', classFor(f.rel), f.rel));
      else if (f.action === 'converged') effectRows.push(formatRow('reconcile', classFor(f.rel), f.rel));
      else effectRows.push(formatRow('keep', classFor(f.rel), f.rel));
    }
    if (legacyRun) {
      for (const abs of previewAmendments.toAppend) {
        const rel = path.relative(physicalRoot, abs).split(path.sep).join('/');
        effectRows.push(formatRow('legacy-backfill', classFor(rel), rel));
      }
      for (const row of previewTrueUp.rows) effectRows.push(formatRow('legacy-true-up', classFor(row.rel), row.rel));
    }
    const quarantining = flags.cleanup && previewRetired.rows.some((r) => r.state === 'stale');
    for (const r of previewRetired.rows) {
      const cls = classFor(r.relPath);
      if (r.state === 'stale' && flags.cleanup) {
        effectRows.push(formatRow('quarantine', cls, `${r.relPath} ${runDir}/${r.relPath}`));
      } else {
        const tail = r.state === 'stale' ? ' — stale; remove with --cleanup'
          : ` — ${r.why === 'referenced' ? 'referenced' : 'refused'}${(r.referrers || []).length ? ` by ${r.referrers.join(', ')}` : ''}`;
        effectRows.push(formatRow('keep', cls, r.relPath, tail));
      }
    }
    if (quarantining) effectRows.push(formatRow('create', 'framework-owned', `${runDir}/manifest.json`));
    for (const i of st.instructions) effectRows.push(formatRow('review', classFor(i.path), `${i.path}:${i.line}`, ` ${i.term}`));
    effectRows.push(formatRow('report', classFor(REPORT_REL), REPORT_REL));

    // operator review: every planned non-claude change to a key of a settings file, one row each
    const reviewRows = [];
    const pushReview = (file, before, after) => {
      for (const d of diffSettingsFile(file, before, after)) {
        const detail = d.tier ? ` tier=${d.tier} rule=${d.rule}`
          : ` before=${JSON.stringify(d.before)} after=${JSON.stringify(d.after)}`;
        reviewRows.push(formatRow('review', classFor(file), `${file}#${d.key}`, ` impact=${d.impact} ${d.change}${detail}`));
      }
    };
    if (floorTarget.present) {
      let simulated = previewReconcile.additionsPlan.settingsObj;
      if (previewFloorPlan.total > 0 || previewRetirementPlan.total > 0) {
        simulated = applyAdditions(previewFloorPlan.settingsObj, previewFloorPlan, { map, pins });
      }
      simulated = { ...simulated };
      for (const k of previewStatusline.keys || []) {
        if (k.action === 'wired') simulated[k.key] = desiredSettingsValue(WRAPPERS.find((w) => w.key === k.key).rel);
        if (k.action === 'unwired') delete simulated[k.key];
      }
      pushReview(PROJECT_SETTINGS_REL, trackedObj, simulated);
    }
    if (lp.plan && lp.plan.total > 0) pushReview(LOCAL_SETTINGS_REL, lp.settingsObj, applyRetirements(lp.settingsObj, lp.plan));

    previewLines.push('planned effects ([<effect>] <class> <target>):');
    previewLines.push(...effectRows);
    previewLines.push('operator review (settings keys this run would change, with impact):');
    previewLines.push(...(reviewRows.length > 0 ? reviewRows : ['  (none)']));
    previewLines.push(`history maintenance: ${legacyRun ? 'on (--legacy-maintenance, cutover pending)' : `off — ${legacyOff}`}`);

    const diagnosesNow = composeDiagnoses(st.firings);
    const preStop = diagnosesNow.some((d) => effectOf(d.code) === 'stop');
    const gitignoreRefusedPreview = Boolean(previewGitignorePlan && previewGitignorePlan.action === 'refused');

    if (flags.dryRun) {
      print(previewLines.join('\n'));
      for (const d of diagnosesNow) print(`  [${effectOf(d.code)}] ${d.code}: ${d.remedy}`);
      print('');
      print('dry run: nothing was written and no claude command was run.');
      print('post-update checks not evaluated');
      print(`cutover: ${st.cutover}`);
      const anyContinue = diagnosesNow.some((d) => effectOf(d.code) === 'continue');
      const code = preStop ? 1 : (anyContinue || filePlan.some((f) => f.action === 'drifted') || gitignoreRefusedPreview ? 2 : 0);
      return { dry: { exitCode: code } };
    }
    if (preStop) return finishPreStop('real', diagnosesNow);

    print(previewLines.join('\n'));

    const env = { ...spawnEnv, CLAUDE_CONFIG_DIR: configDir };
    const phases = st.phases;

    // ── Phase 1: marketplace refresh ─────────────────────────────────────────────────────────────
    st.phase = 'marketplace-refresh';
    let anyMigrated = false;
    for (const { scopeSnap, trigger } of migrations) {
      st.mutated = true; // `claude plugin marketplace remove/add/install` + the settings write-back
      migrateScope({ scopeSnap, trigger, marketplaceName, marketplaceRepo, pluginKey, env, cwd, claudeBin });
      anyMigrated = true;
      if (scopeSnap.name === 'project') wrote(PROJECT_SETTINGS_REL, 'marketplace-migration');
    }
    const manifestBefore = readMarketplaceManifest(configDir, marketplaceName);
    const beforeEntry = manifestBefore.present ? pluginEntryOf(manifestBefore.doc, pins.plugin_name) : null;
    runClaude(['plugin', 'marketplace', 'update', marketplaceName], { env, cwd, claudeBin });
    const manifestAfter = readMarketplaceManifest(configDir, marketplaceName);
    const afterEntry = manifestAfter.present ? pluginEntryOf(manifestAfter.doc, pins.plugin_name) : null;
    st.afterEntry = afterEntry;
    const refreshed = manifestRefreshed(beforeEntry, afterEntry);
    // R (risk, not block): an unreadable installed_plugins.json must not silently suppress the
    // orphaned-install trigger for a tagless, enabled scope — surfaced here rather than folded
    // into a plain 'changed'/'already current' verdict.
    const marketplaceRefreshVerdict = anyMigrated || refreshed ? 'changed' : 'already current';
    phases.push(
      indeterminateInstalledness.length > 0
        ? {
          name: 'marketplace-refresh',
          verdict: 'skipped',
          reason: `installedness could not be determined for scope(s) ${indeterminateInstalledness.join(', ')} (installed plugin registry unreadable) — the orphaned-install trigger was not evaluated there; every other migration still ran (${marketplaceRefreshVerdict})`,
        }
        : { name: 'marketplace-refresh', verdict: marketplaceRefreshVerdict },
    );
    afterPhase('marketplace-refresh');

    // ── AC-NPX-3 (post-refresh): the advertised version, BEFORE the plugin-update phase ──────────
    st.phase = 'plugin-update';
    st.compat.advertised = afterEntry ? strictVersion(afterEntry.version) : null;
    st.firings.push(...evalPostRefreshRows({ advertised: st.compat.advertised, builtFor, marketplaceName }));
    if (composeDiagnoses(st.firings).some((d) => effectOf(d.code) === 'stop')) {
      st.stopped = true;
      return undefined;
    }

    // ── Phase 2: plugin update, once per PRE-migration-snapshot enabled scope (AC-UAW-15b) ──────
    for (const scopeName of enabledOld) {
      st.mutated = true;
      runPluginUpdate({ scope: scopeName, pluginKey, env, cwd, claudeBin });
    }
    // v1.18.0 (AC-V118C-5): the verdict is what THIS workspace has installed, read back from the
    // platform's own registry — never whether the marketplace clone moved in this run (a machine's
    // second workspace read "already current" whatever it installed).
    const registryAfter = readInstalledPluginsRegistry(configDir);
    st.installedAfter = installedVersionBefore(registryAfter, pluginKey, cwd);
    st.compat.to = installedVersionOf(registryAfter, pluginKey, cwd);
    phases.push({
      name: 'plugin-update',
      verdict: st.installedAfter && st.installedAfter !== st.installedBefore ? 'changed' : 'already current',
      ...(st.installedAfter ? {} : { reason: 'installed version unreadable from installed_plugins.json' }),
    });
    st.compat.sessionLoad = readSessionLoad({ registry: registryAfter, pluginKey, cwd, configDir, enabledScopes: st.compat.enabled });
    st.firings.push(...evalPostUpdateRows({
      registry: registryAfter, toVersion: st.compat.to, enabledScopes: st.compat.enabled,
      sessionLoad: st.compat.sessionLoad, cwd, pluginKey,
    }));
    afterPhase('plugin-update');

    // ── Phase 2b (v1.18.2): the plugin's Python runtime deps — install only what is missing ──────
    // FOUNDRY_PYTHON names another interpreter (a venv's), and lets tests keep pip off the machine.
    st.phase = 'python-deps';
    const pythonDeps = ensurePythonDeps(pythonRequirements, { env: spawnEnv, python: spawnEnv.FOUNDRY_PYTHON || 'python3' });
    st.pythonDeps = pythonDeps;
    print(renderPythonDepsRow(pythonDeps, pythonRequirements));
    phases.push({ name: 'python-deps', verdict: pythonDeps.verdict, ...(pythonDeps.reason ? { reason: pythonDeps.reason } : {}) });
    afterPhase('python-deps');

    // ── Phase 3: cleanup (sibling atom; always previewed, only acts under --cleanup) ────────────
    st.phase = 'cleanup';
    if (flags.cleanup) st.mutated = true; // may `claude plugin marketplace remove` a stale registration
    const cleanupResult = runCleanupPhase({
      cleanupFlag: flags.cleanup,
      configDir, marketplaceName, marketplaceRepo, pluginName: pins.plugin_name, pluginKey,
      scopeDescriptors: scopes, env, cwd, print, claudeBin,
    });
    phases.push({ name: 'cleanup', verdict: cleanupResult.verdict, reason: cleanupResult.reason });
    afterPhase('cleanup');

    // ── Phase 4: reinitialization — managed files, then the additive floor reconcile ───────────
    st.phase = 'reinitialization';
    // Conservative: any write the preview planned makes this a run that has mutated.
    if (filePlan.some((f) => f.action === 'create') || settingsRewrite
      || (previewGitignorePlan && (previewGitignorePlan.action === 'converged' || previewGitignorePlan.action === 'appended'))
      || (previewStatusline.files || []).some((f) => f.action === 'create' || f.action === 'converged')
      || (flags.cleanup && previewRetired.present > 0) || (lp.plan && lp.plan.total > 0)
      || (legacyRun && (previewAmendments.toAppend.length > 0 || previewTrueUp.rows.length > 0))) {
      st.mutated = true;
    }
    applyPlan(filePlan);
    for (const f of filePlan) if (f.action === 'create') wrote(f.relPath, f.seed ? 'seed' : 'managed');
    // Recomputed FRESH from disk — never the preview-time `previewFloorPlan` — because Phase 1's
    // migration may have just rewritten this exact file (project scope's settings.json IS the
    // floor-reconcile target). Applying a stale pre-migration plan here would silently clobber it.
    let floorPlan = null;
    let floorRetirementPlan = null;
    const freshFloorTarget = resolveTarget(physicalRoot);
    if (freshFloorTarget.present) {
      const settingsObj = readTarget(freshFloorTarget.path);
      // AC-FRR-1: the upgrader's own reconcile path — retirement first, additions planned against the
      // POST-retirement rule set. `floorPlan.settingsObj` comes back already post-retirement, so the
      // write below composes onto it directly — never a second `applyRetirements` call.
      const { additionsPlan, retirementPlan } = planReconcile({
        settingsObj, map, pins, pluginRootExpansion: [], unreadableOrigins: [], home: homeDir,
      });
      floorPlan = additionsPlan;
      floorRetirementPlan = retirementPlan;
      if (floorPlan.total > 0 || floorRetirementPlan.total > 0) {
        writeTargetAtomically(freshFloorTarget.path, applyAdditions(floorPlan.settingsObj, floorPlan, { map, pins }));
        wrote(PROJECT_SETTINGS_REL, 'permission-floor');
        for (const line of renderPlan(floorPlan, {
          applied: true, retirementPlan: floorRetirementPlan, mapEntryCount: map.entries.length,
        })) print(line);
      }
    }
    // Recomputed FRESH from disk, same reasoning as floorPlan just above. Skipped entirely when THIS
    // run's own filePlan just CREATED `.gitignore` — AC-GBR-2 leaves the absent case to the create path.
    const gitignoreFileAction = filePlan.find((f) => f.relPath === '.gitignore')?.action;
    let freshGitignorePlan = null;
    if (gitignoreFileAction !== 'create') {
      freshGitignorePlan = reconcileGitignorePlan({ physicalRoot, templatesDir });
      applyGitignorePlan(freshGitignorePlan);
      if (freshGitignorePlan.action === 'converged' || freshGitignorePlan.action === 'appended') wrote('.gitignore', 'managed-block');
      const gitignoreRow = renderGitignoreRow(freshGitignorePlan);
      if (gitignoreRow) print(gitignoreRow);
    }
    st.gitignoreRefused = Boolean(freshGitignorePlan && freshGitignorePlan.action === 'refused');

    // The two history writers (AC-NPX-9): only under --legacy-maintenance while the cutover is
    // pending; re-planned FRESH from disk and applied with the modules' own re-classify guards.
    let amendmentsPlan = null;
    let trueUpPlan = { written: [], rows: [], applied: true };
    if (legacyRun) {
      amendmentsPlan = planAmendmentsBackfill({ physicalRoot });
      applyAmendmentsBackfill(amendmentsPlan);
      for (const rel of amendmentsPlan.writtenPaths || []) wrote(rel, 'amendments-backfill');
      const amendmentsRow = renderAmendmentsRow(amendmentsPlan);
      if (amendmentsRow) print(amendmentsRow);
      trueUpPlan = applyManifestTrueUp(planManifestTrueUp({ physicalRoot }));
      for (const rel of trueUpPlan.written || []) wrote(rel, 'manifest-true-up');
      for (const row of renderManifestTrueUpRows(trueUpPlan)) print(row);
    } else {
      print(`  [legacy-maintenance] off — ${legacyOff}: the Amendments backfill and the release-manifest true-up did not run (cutover record: ${CUTOVER_RECORD_REL})`);
    }
    st.amendmentsPlan = amendmentsPlan;

    const anyCreated = filePlan.some((f) => f.action === 'create');
    const anyFloorAdded = Boolean(floorPlan && floorPlan.total > 0)
      || Boolean(floorRetirementPlan && floorRetirementPlan.total > 0);
    const anyGitignoreChanged = Boolean(
      freshGitignorePlan && (freshGitignorePlan.action === 'converged' || freshGitignorePlan.action === 'appended'),
    );
    const anyAmendmentsBackfilled = Boolean(amendmentsPlan && amendmentsPlan.written > 0);
    // statusline-wiring (v1.17.0, AC-SLW-1/-2): the updater is the post-trust writer of the
    // wrapper files and the two settings keys (added only when absent). Planned fresh from disk
    // here, after the floor write above, so the settings read is the current one.
    const statuslinePlan = planStatuslineWiring({ physicalRoot, templatesDir });
    applyStatuslineWiring(statuslinePlan);
    for (const f of statuslinePlan.files || []) if (f.action === 'create' || f.action === 'converged') wrote(f.rel, 'statusline');
    if ((statuslinePlan.keys || []).some((k) => k.action === 'wired' || k.action === 'unwired')) wrote(PROJECT_SETTINGS_REL, 'statusline');
    for (const row of renderStatuslineRows(statuslinePlan)) print(row);
    // retired-artifacts (hotfix-v1.17.4, ER #236 / AC-NPX-13): re-planned FRESH from disk; reported
    // every run, QUARANTINED (moved, never deleted) only under --cleanup, only catalogued paths of the
    // catalogued kind, never a hook a settings hook command still names, never through a link.
    const retiredPlan = planRetiredArtifacts({ physicalRoot, catalogue: retiredCatalogue });
    let retiredRemoved = 0;
    if (flags.cleanup && retiredPlan.present > 0) {
      st.mutated = true;
      retiredRemoved = applyRetiredArtifacts(retiredPlan, physicalRoot, { runId: st.runId });
    }
    for (const r of retiredPlan.rows) if (r.state === 'removed') st.removed.push(r.relPath);
    st.retiredPlan = retiredPlan;
    st.retiredRemoved = retiredRemoved;
    st.cleanupEntries = cleanupEntriesOf(retiredPlan);
    for (const row of renderRetiredArtifactRows(retiredPlan, { cleanup: flags.cleanup })) print(row);

    // settings.local.json (hotfix-v1.17.4): the tracked-file reconcile never reads it, so a
    // version-pinned floor row an old init left there survived every upgrade. Only RETIREMENT is
    // applied here (never additions — the local file is the operator's), with the same planner and
    // the same never-clobber, mode-preserving write as the tracked file. The tracked file is re-read
    // AFTER its own write above, so an `ask` row is retired only when the wildcard row that replaces
    // it is really there (Risk 3); confined like every other path (Risk 4).
    let localRetired = 0;
    {
      const fresh = planLocalRetirement({
        physicalRoot, map,
        trackedSettingsObj: freshFloorTarget.present ? readTarget(freshFloorTarget.path) : null,
      });
      if (fresh.plan && fresh.plan.total > 0) {
        writeTargetAtomically(fresh.abs, applyRetirements(fresh.settingsObj, fresh.plan));
        wrote(fresh.rel, 'local-retirement');
        localRetired = fresh.plan.total;
        for (const tier of ['allow', 'ask', 'deny']) for (const r of fresh.plan.retirements[tier] || []) print(`  [retired] ${fresh.rel} ${tier}: ${r}`);
        print(`  [permission-floor] ${fresh.rel}: retired ${localRetired} version-pinned/gone row(s)`);
      } else if (fresh.error) {
        print(`  [permission-floor] ${fresh.rel}: not reconciled (${fresh.error})`);
      }
    }
    st.localRetired = localRetired;

    phases.push({
      name: 'reinitialization',
      verdict: anyCreated || anyFloorAdded || anyGitignoreChanged || anyAmendmentsBackfilled || (trueUpPlan.written || []).length > 0
        || statuslineChanged(statuslinePlan) || retiredRemoved > 0 || localRetired > 0 ? 'changed' : 'already current',
    });
    afterPhase('reinitialization');

    print('');
    print(renderSummary(phases));
    return undefined;
  };

  /** A stop before the first mutation (or the input-unreadable stop that precedes the plan). */
  const finishPreStop = (mode, diagnoses) => {
    const d = diagnoses || composeDiagnoses(st.firings);
    if (mode === 'dry') {
      for (const x of d) print(`  [${effectOf(x.code)}] ${x.code}: ${x.remedy}`);
      print('');
      print('dry run: nothing was written and no claude command was run.');
      print('post-update checks not evaluated');
      print(`cutover: ${st.cutover}`);
      return { dry: { exitCode: 1 } };
    }
    st.stopped = true;
    return undefined;
  };

  let early;
  try {
    early = await body();
  } catch (e) {
    st.error = e;
  }
  if (early && early.dry) return { exitCode: early.dry.exitCode, output: lines.join('\n') };

  // ── the end: outcome (AC-NPX-11), the report (AC-NPX-2), the last line ─────────────────────────
  if (st.error) {
    if (st.error instanceof RefusalError) print(`refused: ${st.error.message}`);
    else print(`error: ${st.error.stack || st.error.message}`);
  }
  const diagnoses = composeDiagnoses(st.firings);
  for (const d of diagnoses) {
    if (effectOf(d.code) === 'stop') print(`refused: ${d.code}: ${d.remedy}`);
    else print(`  [attention] ${d.code}: ${d.remedy}`);
  }
  const stopDiag = diagnoses.find((d) => effectOf(d.code) === 'stop');
  const mutated = st.mutated || st.written.length > 0 || st.removed.length > 0;
  const stoppedEarly = Boolean(st.error) || st.stopped;
  const anyDrifted = (st.filePlan || []).some((f) => f.action === 'drifted');
  const attention = diagnoses.some((d) => effectOf(d.code) === 'continue') || anyDrifted || st.gitignoreRefused
    || Boolean(st.pythonDeps && st.pythonDeps.verdict === 'failed');
  let outcome = stoppedEarly ? (mutated ? 'partial' : 'refused') : (attention ? 'attention-needed' : 'complete');
  let nextAction;
  let failure = null;
  if (stoppedEarly) {
    if (st.error) {
      const code = st.error && typeof st.error.code === 'string' && ERROR_CODE_RE.test(st.error.code) ? st.error.code : null;
      failure = { phase: st.phase, code };
      nextAction = st.nextOverride || (outcome === 'partial' ? NEXT_PARTIAL : NEXT_FIX);
    } else {
      failure = { phase: st.phase, code: stopDiag ? stopDiag.code : null };
      nextAction = stopDiag ? stopDiag.remedy : NEXT_FIX;
    }
  } else {
    nextAction = NEXT_ACTION_HANDOFF;
  }

  let reportPath = null;
  if (st.reportable && st.pins) {
    // AC-NPX-5: the diff between each settings file's parsed content before the first mutation and now
    const operatorReview = [];
    for (const f of st.reviewBefore || []) {
      const after = readJsonObjectOrNull(f.abs);
      if (f.obj === null || after === null) continue;
      operatorReview.push(...diffSettingsFile(f.file, f.obj, after));
    }
    const steps = PHASE_NAMES.map((phase) => {
      const p = st.phases.find((x) => x.name === phase);
      return { phase, verdict: p ? p.verdict : 'not-run' };
    });
    const report = buildUpgradeReport({
      installedBefore: st.installedBefore, installedAfter: st.installedAfter, afterEntry: st.afterEntry,
      toPluginVersion: st.pins.plugin_version, phases: st.phases, filePlan: st.filePlan, amendmentsPlan: st.amendmentsPlan,
      written: st.written, removed: st.removed, configDir, hostname: os.hostname(), pythonDeps: st.pythonDeps,
      updaterVersion, coreVersion: st.corePkg ? st.corePkg.version : null, updaterPluginVersion: st.pins.plugin_version,
      retiredArtifacts: st.retiredPlan
        ? { present: st.retiredPlan.rows.filter((r) => r.state === 'stale').map((r) => r.relPath), removed: st.retiredRemoved, refused: st.retiredPlan.refused }
        : null,
      localRetired: st.localRetired,
      v2: {
        runId: st.runId, outcome, nextAction, failure,
        previousPartial: outcome === 'refused' ? previousPartialFrom(st.previousReport) : null,
        cleanup: st.cleanupEntries, operatorReview,
        compatibility: {
          from_plugin_version: st.compat.from, to_plugin_version: st.compat.to, advertised_version: st.compat.advertised,
          enabled_scopes: st.compat.enabled, sources: st.compat.sources, session_load: st.compat.sessionLoad, diagnoses,
        },
        adoption: { mechanical_steps: steps, instructions_pending: st.instructions, cutover: st.cutover },
      },
    });
    reportPath = writeUpgradeReport(physicalRoot, report);
    if (reportPath === null) {
      // PR #218 review round 2: never hand off to a report that was not written — a planted link
      // at that path would otherwise be what the skill reads. A report that cannot be written is a
      // stop (AC-NPX-11): the outcome falls back to refused / partial by mutation.
      print('');
      print('  [refused] .foundry/upgrade-report.json (.foundry is not a directory, or the report path is not a regular file — NOT written)');
      outcome = mutated ? 'partial' : 'refused';
      nextAction = NEXT_ACTION_REPORT_UNWRITABLE;
    }
  } else if (st.reportable === false) {
    outcome = mutated ? 'partial' : 'refused';
  }
  print('');
  print(formatLastLine({ outcome, nextAction, cutover: st.cutover }));
  return { exitCode: EXIT_CODE_OF[outcome], output: lines.join('\n') };
}
