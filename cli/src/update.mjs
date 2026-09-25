// update.mjs — the `npx update-agentic-workspace` orchestrator: argv -> preflight -> preview ->
// Phase 1 (marketplace refresh + AC-UAW-4 migration) -> Phase 2 (plugin update) -> Phase 3
// (cleanup, opt-in) -> Phase 4 (reinitialization: managed-file + permission-floor reconcile) ->
// per-phase summary. No `claude` invocation lives in this file — every one goes through
// pluginRefresh.mjs's single allowlisted spawn site (AC-UAW-14); this file only decides WHICH
// invocations to make and WHEN, and performs Phase 4's file writes via the SAME never-clobber
// machinery run.mjs's create path already uses (cli/src/reconcile.mjs, cli/src/floorReconcile.mjs).
import fs from 'node:fs';
import path from 'node:path';
import { RefusalError, physicalResolve, confinedJoin } from './util.mjs';
import { loadMap, buildSettings } from './permissionFloor.mjs';
import { buildManagedFiles } from './scaffold.mjs';
import { planManagedFiles, applyPlan } from './reconcile.mjs';
import {
  resolveTarget, readTarget, applyAdditions, planReconcile, writeTargetAtomically, renderPlan,
} from './floorReconcile.mjs';
import { reconcileGitignorePlan, applyGitignorePlan, renderGitignoreRow } from './gitignoreReconcile.mjs';
import { planAmendmentsBackfill, applyAmendmentsBackfill, renderAmendmentsRow } from './amendmentsBackfill.mjs';
import { buildUpgradeReport, writeUpgradeReport, installedVersionBefore, versionOrNull, NEXT_LINE } from './upgradeReport.mjs';
import { planStatuslineWiring, applyStatuslineWiring, renderStatuslineRows, statuslineChanged } from './statuslineWiring.mjs';
import { policyPresent, missingSelfGuardDeny, applySelfGuardDeny, renderSelfGuardRow, selfGuardShapeOk } from './selfGuardDeny.mjs';
import { loadRetiredCatalogue, planRetiredArtifacts, applyRetiredArtifacts, renderRetiredArtifactRows } from './retiredArtifacts.mjs';
import { planRetirements, applyRetirements, askRootKeys } from './floorReconcile.mjs';
import {
  ALLOWED_CLAUDE_SUBCOMMANDS, resolveClaudeOnPath, runClaude,
  defaultScopes, snapshotScopes, classifyMigration, migrationActions, migrateScope,
  readMarketplaceManifest, pluginEntryOf, manifestRefreshed,
  enabledScopeNames, runPluginUpdate,
  readInstalledPluginsRegistry, scopeRecordsFor,
} from './pluginRefresh.mjs';
import { runCleanupPhase } from './cleanup.mjs';

export { ALLOWED_CLAUDE_SUBCOMMANDS };

/** The update entry point's OWN small flag table (Clarifications: "the update entry point carries
 * its own small flag table, disjoint from the wizard's") — deliberately NOT cli/src/argv.mjs +
 * QUESTION_TABLE, which is denied to the sibling cleanup atom and whose flag set is derived from
 * the wizard's prompts, not this command's. `--cleanup` is the cleanup atom's own opt-in. */
export function parseUpdateArgv(argv) {
  const values = { cleanup: false, help: false };
  for (const tok of argv) {
    if (tok === '--cleanup') values.cleanup = true;
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

/** Run the update command end to end. Never throws — every failure path is caught and turned into
 * a refusal-shaped exit 1 (or, for a bug, exit 1 with the error message), matching run.mjs's own
 * contract. */
export async function runUpdate(argv, { cwd, configDir, homeDir, pkgDir, output, spawnEnv = process.env, updaterVersion = null }) {
  const lines = [];
  const print = (s) => {
    lines.push(s);
    output.write(`${s}\n`);
  };

  try {
    let flags;
    try {
      flags = parseUpdateArgv(argv);
    } catch (e) {
      if (e instanceof RefusalError) {
        print(`refused: ${e.message}`);
        return { exitCode: 1, output: lines.join('\n') };
      }
      throw e;
    }

    if (flags.help) {
      print([
        'Usage: update-agentic-workspace [--cleanup] [--help]',
        '',
        '  --cleanup   Also prune superseded plugin-cache versions and remove a stale or',
        '              duplicate marketplace registration (previewed either way; only',
        '              removed under this flag). Off by default.',
        '  --help      Show this help and exit.',
      ].join('\n'));
      return { exitCode: 0, output: lines.join('\n') };
    }

    const corePkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf-8'));
    const pins = corePkg.foundry;
    // ER #228: say WHICH updater this is, first, every run — a stale npx-cached updater looked
    // exactly like a broken backfill until the report and the log carried the version.
    print(`update-agentic-workspace ${versionOrNull(updaterVersion) || 'unknown'} (core create-agentic-workspace ${versionOrNull(corePkg.version) || 'unknown'}, built for plugin ${versionOrNull(pins.plugin_version) || 'unknown'})`);
    const marketplaceName = pins.marketplace_name;
    const marketplaceRepo = pins.marketplace_repo;
    const pluginKey = `${pins.plugin_name}@${pins.marketplace_name}`;

    // ── AC-UAW-13(a): the claude executable must resolve on PATH before anything else runs ──────
    // R8: the RESOLVED path is what the rest of the run spawns — never the bare name re-resolved
    // against env.PATH a second time, which would leave this preflight proving something the
    // actual spawn calls do not rely on.
    const claudeBin = resolveClaudeOnPath(spawnEnv.PATH);
    if (!claudeBin) {
      throw new RefusalError('claude executable not found on PATH');
    }

    // ── AC-UAW-15(b): snapshot every scope BEFORE any mutation; AC-UAW-13(b) refuses here too ───
    const scopes = defaultScopes({ cwd, configDir });
    const snapshot = snapshotScopes(scopes);
    const enabledScopes = enabledScopeNames(snapshot, pluginKey);

    const registry = readInstalledPluginsRegistry(configDir);
    const isInstalled = isInstalledInScopeFactory(registry, pluginKey, cwd);
    // ER #222: the report's `from` is what THIS workspace had installed, read before any mutation.
    const installedBefore = installedVersionBefore(registry, pluginKey, cwd);

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
    const physicalRoot = physicalResolve(cwd);
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

    // This is a PREVIEW-ONLY computation: `.claude/settings.json` is also `project` scope's
    // settings file, and Phase 1's migration (below) may write to that SAME path. Applying THIS
    // captured plan verbatim in Phase 4 would silently clobber whatever Phase 1 just wrote —
    // Phase 4 therefore re-reads and recomputes the floor plan fresh, right before it writes.
    const floorTarget = resolveTarget(physicalRoot);
    // AC-FRR-1 (ER #199, review round 1): retirement first, additions planned against the
    // POST-retirement rule set — planReconcile is the ONLY entry point either the preview here or
    // Phase 4 below should use; see its own comment in floorReconcile.mjs. PREVIEW-ONLY: Phase 4
    // re-reads and recomputes fresh right before it writes, for the migration-clobber reason above.
    const previewReconcile = floorTarget.present
      ? planReconcile({
        settingsObj: readTarget(floorTarget.path), map, pins,
        pluginRootExpansion: [], unreadableOrigins: [], home: homeDir,
      })
      : null;
    const previewFloorPlan = previewReconcile ? previewReconcile.additionsPlan : null;
    const previewRetirementPlan = previewReconcile ? previewReconcile.retirementPlan : null;

    // gitignore-block-reconcile (ER #177, AC-GBR-1): PREVIEW-ONLY, same caveat as previewFloorPlan
    // above — `.gitignore` is not a migration target, but Phase 4 recomputes fresh from disk anyway,
    // for the same "never apply a stale pre-migration plan" reason.
    const templatesDir = path.join(pkgDir, 'templates');
    const previewGitignorePlan = reconcileGitignorePlan({ physicalRoot, templatesDir });

    // ── AC-UAW-7: the preview, before the first `claude` invocation and the first write ─────────
    const previewLines = ['The following claude invocations will be made:'];
    for (const { scopeSnap, trigger } of migrations) {
      for (const args of migrationActions(trigger, {
        scope: scopeSnap.name, marketplaceName, marketplaceRepo, pluginKey,
      })) {
        previewLines.push(`  claude ${args.join(' ')}`);
      }
    }
    previewLines.push(`  claude plugin marketplace update ${marketplaceName}`);
    for (const scopeName of enabledScopes) {
      previewLines.push(`  claude plugin update ${pluginKey} --scope ${scopeName}`);
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
      previewLines.push(`  [permission-floor] would add allow=${previewFloorPlan.additions.allow.length}, ask=${previewFloorPlan.additions.ask.length}, deny=${previewFloorPlan.additions.deny.length}`);
      if (previewRetirementPlan && previewRetirementPlan.total > 0) {
        previewLines.push(`  [permission-floor] would retire allow=${previewRetirementPlan.retirements.allow.length}, ask=${previewRetirementPlan.retirements.ask.length}`);
      }
      // hotfix-v1.17.3 (PR #233 review Risk 4): the self-guard pair is previewed like the floor.
      if (policyPresent(physicalRoot) || filePlan.some((f) => f.seed && f.action === 'create')) {
        const cur0 = readTarget(floorTarget.path);
        const prow = renderSelfGuardRow(physicalRoot, missingSelfGuardDeny(cur0).length, { shapeOk: selfGuardShapeOk(cur0), applied: false });
        if (prow) previewLines.push(prow);
        else previewLines.push('  [permissions] would add self-guard deny rules (2): Edit/Write on .foundry/permissions.yaml (with the seed)');
      }
    } else {
      previewLines.push('  [permission-floor] .claude/settings.json absent — left to the create path');
    }
    const previewGitignoreRow = renderGitignoreRow(previewGitignorePlan);
    if (previewGitignoreRow) previewLines.push(previewGitignoreRow);
    // amendments-backfill (ER #214, AC-AMB-1): PREVIEW-ONLY like the two rows above; Phase 4
    // re-plans fresh from disk before it writes.
    const previewAmendmentsRow = renderAmendmentsRow(planAmendmentsBackfill({ physicalRoot }));
    if (previewAmendmentsRow) previewLines.push(previewAmendmentsRow);
    // statusline-wiring (AC-SLW-1/-2): PREVIEW-ONLY rows; Phase 4 re-plans fresh from disk.
    previewLines.push(...renderStatuslineRows(planStatuslineWiring({ physicalRoot, templatesDir })));
    // retired-artifacts (hotfix-v1.17.4, ER #236): PREVIEW-ONLY rows; Phase 4 re-plans fresh from disk.
    const retiredCatalogue = loadRetiredCatalogue(pkgDir);
    previewLines.push(...renderRetiredArtifactRows(planRetiredArtifacts({ physicalRoot, catalogue: retiredCatalogue }), { cleanup: flags.cleanup, phase: 'preview' }));
    // settings.local.json retirement (hotfix-v1.17.4): PREVIEW-ONLY row (PR #237 review Risk 2 —
    // every Phase 4 write is announced before the first write happens); Phase 4 re-plans fresh.
    {
      const lp = planLocalRetirement({
        physicalRoot, map, trackedSettingsObj: floorTarget.present ? readTarget(floorTarget.path) : null,
      });
      if (lp.plan && lp.plan.total > 0) previewLines.push(`  [permission-floor] ${lp.rel}: would retire allow=${lp.plan.retirements.allow.length}, ask=${lp.plan.retirements.ask.length} (never adds)`);
      else if (lp.error) previewLines.push(`  [permission-floor] ${lp.rel}: would not be reconciled (${lp.error})`);
    }
    print(previewLines.join('\n'));

    const env = { ...spawnEnv, CLAUDE_CONFIG_DIR: configDir };
    const phases = [];

    // ── Phase 1: marketplace refresh ─────────────────────────────────────────────────────────────
    let anyMigrated = false;
    for (const { scopeSnap, trigger } of migrations) {
      migrateScope({ scopeSnap, trigger, marketplaceName, marketplaceRepo, pluginKey, env, cwd, claudeBin });
      anyMigrated = true;
    }
    const manifestBefore = readMarketplaceManifest(configDir, marketplaceName);
    const beforeEntry = manifestBefore.present ? pluginEntryOf(manifestBefore.doc, pins.plugin_name) : null;
    runClaude(['plugin', 'marketplace', 'update', marketplaceName], { env, cwd, claudeBin });
    const manifestAfter = readMarketplaceManifest(configDir, marketplaceName);
    const afterEntry = manifestAfter.present ? pluginEntryOf(manifestAfter.doc, pins.plugin_name) : null;
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

    // ── Phase 2: plugin update, once per PRE-migration-snapshot enabled scope (AC-UAW-15b) ──────
    for (const scopeName of enabledScopes) {
      runPluginUpdate({ scope: scopeName, pluginKey, env, cwd, claudeBin });
    }
    phases.push({ name: 'plugin-update', verdict: refreshed ? 'changed' : 'already current' });

    // ── Phase 3: cleanup (sibling atom; always previewed, only acts under --cleanup) ────────────
    const cleanupScopeDescriptors = scopes; // same {name, settingsPath} pairs, unresolved-required
    const cleanupResult = runCleanupPhase({
      cleanupFlag: flags.cleanup,
      configDir, marketplaceName, marketplaceRepo, pluginName: pins.plugin_name, pluginKey,
      scopeDescriptors: cleanupScopeDescriptors, env, cwd, print, claudeBin,
    });
    phases.push({ name: 'cleanup', verdict: cleanupResult.verdict, reason: cleanupResult.reason });

    // ── Phase 4: reinitialization — managed files, then the additive floor reconcile ───────────
    applyPlan(filePlan);
    // Recomputed FRESH from disk — never the preview-time `previewFloorPlan` — because Phase 1's
    // migration may have just rewritten this exact file (project scope's settings.json IS the
    // floor-reconcile target). Applying a stale pre-migration plan here would silently clobber it.
    let floorPlan = null;
    let floorRetirementPlan = null;
    const freshFloorTarget = resolveTarget(physicalRoot);
    if (freshFloorTarget.present) {
      const settingsObj = readTarget(freshFloorTarget.path);
      // AC-FRR-1 (ER #199, review round 1): the upgrader's own reconcile path — retirement first,
      // additions planned against the POST-retirement rule set (planReconcile; see its comment in
      // floorReconcile.mjs). `floorPlan.settingsObj` comes back already post-retirement, so the
      // write below composes onto it directly — never a second `applyRetirements` call.
      const { additionsPlan, retirementPlan } = planReconcile({
        settingsObj, map, pins, pluginRootExpansion: [], unreadableOrigins: [], home: homeDir,
      });
      floorPlan = additionsPlan;
      floorRetirementPlan = retirementPlan;
      if (floorPlan.total > 0 || floorRetirementPlan.total > 0) {
        writeTargetAtomically(freshFloorTarget.path, applyAdditions(floorPlan.settingsObj, floorPlan, { map, pins }));
        for (const line of renderPlan(floorPlan, {
          applied: true, retirementPlan: floorRetirementPlan, mapEntryCount: map.entries.length,
        })) print(line);
      }
      // hotfix-v1.17.3 (ER #232): the policy file's self-guard deny pair is framework-owned — converge
      // it here, fresh from disk after the floor write, whenever a policy file exists (seeded above or
      // kept). Grants stay the compiler's (operator-run) business.
      if (policyPresent(physicalRoot)) {
        const cur = readTarget(freshFloorTarget.path);
        const shapeOk = selfGuardShapeOk(cur);
        const missing = missingSelfGuardDeny(cur);
        if (shapeOk && missing.length > 0) writeTargetAtomically(freshFloorTarget.path, applySelfGuardDeny(cur));
        const row = renderSelfGuardRow(physicalRoot, missing.length, { shapeOk });
        if (row) print(row);
      }
    }
    // Recomputed FRESH from disk, same reasoning as floorPlan just above: never apply a plan
    // captured before Phases 1-3 ran, even though `.gitignore` is not itself a migration target.
    // Skipped entirely when THIS run's own filePlan just CREATED `.gitignore` — AC-GBR-2 leaves the
    // absent case to the create path, and re-planning immediately after applyPlan would otherwise
    // find the just-written file already converged and print a redundant `[unchanged]` row for a
    // file that never existed before this run.
    const gitignoreFileAction = filePlan.find((f) => f.relPath === '.gitignore')?.action;
    let freshGitignorePlan = null;
    if (gitignoreFileAction !== 'create') {
      freshGitignorePlan = reconcileGitignorePlan({ physicalRoot, templatesDir });
      applyGitignorePlan(freshGitignorePlan);
      const gitignoreRow = renderGitignoreRow(freshGitignorePlan);
      if (gitignoreRow) print(gitignoreRow);
    }

    // amendments-backfill (ER #214, AC-AMB-1/-2): re-planned FRESH from disk like the two blocks
    // above, applied with the module's own re-classify-before-append guard.
    const amendmentsPlan = planAmendmentsBackfill({ physicalRoot });
    applyAmendmentsBackfill(amendmentsPlan);
    const amendmentsRow = renderAmendmentsRow(amendmentsPlan);
    if (amendmentsRow) print(amendmentsRow);

    const anyCreated = filePlan.some((f) => f.action === 'create');
    const anyFloorAdded = Boolean(floorPlan && floorPlan.total > 0)
      || Boolean(floorRetirementPlan && floorRetirementPlan.total > 0);
    const anyGitignoreChanged = Boolean(
      freshGitignorePlan && (freshGitignorePlan.action === 'converged' || freshGitignorePlan.action === 'appended'),
    );
    const anyAmendmentsBackfilled = amendmentsPlan.written > 0;
    // statusline-wiring (v1.17.0, AC-SLW-1/-2): the updater is the post-trust writer of the
    // wrapper files and the two settings keys (added only when absent). Planned fresh from disk
    // here, after the floor write above, so the settings read is the current one.
    const statuslinePlan = planStatuslineWiring({ physicalRoot, templatesDir });
    applyStatuslineWiring(statuslinePlan);
    for (const row of renderStatuslineRows(statuslinePlan)) print(row);
    // retired-artifacts (hotfix-v1.17.4, ER #236): re-planned FRESH from disk; reported every run,
    // removed only under --cleanup, only catalogued paths of the catalogued kind, never a hook a
    // settings hook command still names.
    const retiredPlan = planRetiredArtifacts({ physicalRoot, catalogue: retiredCatalogue });
    let retiredRemoved = 0;
    if (flags.cleanup && retiredPlan.present > 0) retiredRemoved = applyRetiredArtifacts(retiredPlan, physicalRoot);
    for (const row of renderRetiredArtifactRows(retiredPlan, { cleanup: flags.cleanup })) print(row);

    // settings.local.json (hotfix-v1.17.4): the tracked-file reconcile never reads it, so a
    // version-pinned floor row an old init left there survived every upgrade. Only RETIREMENT is
    // applied here (never additions — the local file is the operator's), with the same planner and
    // the same never-clobber, mode-preserving write as the tracked file. The tracked file is re-read
    // AFTER its own write above, so an `ask` row is retired only when the wildcard row that replaces
    // it is really there (Risk 3); confined like every other path (Risk 4).
    let localRetired = 0;
    {
      const lp = planLocalRetirement({
        physicalRoot, map,
        trackedSettingsObj: freshFloorTarget.present ? readTarget(freshFloorTarget.path) : null,
      });
      if (lp.plan && lp.plan.total > 0) {
        writeTargetAtomically(lp.abs, applyRetirements(lp.settingsObj, lp.plan));
        localRetired = lp.plan.total;
        for (const tier of ['allow', 'ask']) for (const r of lp.plan.retirements[tier]) print(`  [retired] ${lp.rel}: ${r}`);
        print(`  [permission-floor] ${lp.rel}: retired ${localRetired} version-pinned/gone row(s)`);
      } else if (lp.error) {
        print(`  [permission-floor] ${lp.rel}: not reconciled (${lp.error})`);
      }
    }

    phases.push({
      name: 'reinitialization',
      verdict: anyCreated || anyFloorAdded || anyGitignoreChanged || anyAmendmentsBackfilled
        || statuslineChanged(statuslinePlan) || retiredRemoved > 0 || localRetired > 0 ? 'changed' : 'already current',
    });

    print('');
    print(renderSummary(phases));

    // post-upgrade-skill (AC-PUS-1): the hand-off to the judgement half. Written on every
    // completed run (overwritten — a report, not a managed file; `.foundry/*` is gitignored), and
    // named in the LAST line so the operator's next step is never a guess.
    const report = buildUpgradeReport({
      installedBefore, afterEntry, toPluginVersion: pins.plugin_version, phases, filePlan, amendmentsPlan,
      updaterVersion, coreVersion: corePkg.version, updaterPluginVersion: pins.plugin_version,
      retiredArtifacts: { present: retiredPlan.rows.filter((r) => r.state === 'stale').map((r) => r.relPath), removed: retiredRemoved, refused: retiredPlan.refused },
      localRetired,
    });
    const reportPath = writeUpgradeReport(physicalRoot, report);
    print('');
    if (reportPath === null) {
      // PR #218 review round 2: never hand off to a report that was not written — a planted link
      // at that path would otherwise be what the skill reads.
      print('  [refused] .foundry/upgrade-report.json (.foundry is not a directory, or the report path is not a regular file — NOT written)');
      print('next: make .foundry/upgrade-report.json a regular path and re-run — do not run /foundry:post-upgrade until this run writes its report');
    } else {
      print(NEXT_LINE);
    }

    const anyDrifted = filePlan.some((f) => f.action === 'drifted');
    // Same bucket a `drifted` managed file uses (exit 2), not the hard-refusal exit 1 — Phases 1-4
    // already ran and wrote what they could; a malformed gitignore block is reported, not escalated
    // into "the update failed" for the whole run (run.mjs makes the identical choice; see its own
    // comment on `gitignoreRefused`).
    const gitignoreRefused = Boolean(freshGitignorePlan && freshGitignorePlan.action === 'refused');
    return { exitCode: anyDrifted || gitignoreRefused ? 2 : 0, output: lines.join('\n') };
  } catch (e) {
    if (e instanceof RefusalError) {
      print(`refused: ${e.message}`);
      return { exitCode: 1, output: lines.join('\n') };
    }
    print(`error: ${e.stack || e.message}`);
    return { exitCode: 1, output: lines.join('\n') };
  }
}
