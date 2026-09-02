// update.mjs — the `npx update-agentic-workspace` orchestrator: argv -> preflight -> preview ->
// Phase 1 (marketplace refresh + AC-UAW-4 migration) -> Phase 2 (plugin update) -> Phase 3
// (cleanup, opt-in) -> Phase 4 (reinitialization: managed-file + permission-floor reconcile) ->
// per-phase summary. No `claude` invocation lives in this file — every one goes through
// pluginRefresh.mjs's single allowlisted spawn site (AC-UAW-14); this file only decides WHICH
// invocations to make and WHEN, and performs Phase 4's file writes via the SAME never-clobber
// machinery run.mjs's create path already uses (cli/src/reconcile.mjs, cli/src/floorReconcile.mjs).
import fs from 'node:fs';
import path from 'node:path';
import { RefusalError, physicalResolve } from './util.mjs';
import { loadMap, buildSettings, classifyDrift } from './permissionFloor.mjs';
import { buildManagedFiles } from './scaffold.mjs';
import { planManagedFiles, applyPlan } from './reconcile.mjs';
import {
  resolveTarget, readTarget, readTrackedRules, planAdditions, applyAdditions, writeTargetAtomically,
} from './floorReconcile.mjs';
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
export async function runUpdate(argv, { cwd, configDir, homeDir, pkgDir, output, spawnEnv = process.env }) {
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

    const pins = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf-8')).foundry;
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
    const previewFloorPlan = floorTarget.present
      ? planAdditions({
        findings: classifyDrift(map, readTrackedRules(readTarget(floorTarget.path)), {
          pluginRootExpansion: [], unreadableOrigins: [], home: homeDir,
        }),
        map, settingsObj: readTarget(floorTarget.path), pins,
      })
      : null;

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
    } else {
      previewLines.push('  [permission-floor] .claude/settings.json absent — left to the create path');
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
    const freshFloorTarget = resolveTarget(physicalRoot);
    if (freshFloorTarget.present) {
      const settingsObj = readTarget(freshFloorTarget.path);
      const findings = classifyDrift(map, readTrackedRules(settingsObj), {
        pluginRootExpansion: [], unreadableOrigins: [], home: homeDir,
      });
      floorPlan = planAdditions({ findings, map, settingsObj, pins });
      floorPlan.settingsObj = settingsObj;
      if (floorPlan.total > 0) {
        writeTargetAtomically(freshFloorTarget.path, applyAdditions(settingsObj, floorPlan, { map, pins }));
      }
    }
    const anyCreated = filePlan.some((f) => f.action === 'create');
    const anyFloorAdded = Boolean(floorPlan && floorPlan.total > 0);
    phases.push({ name: 'reinitialization', verdict: anyCreated || anyFloorAdded ? 'changed' : 'already current' });

    print('');
    print(renderSummary(phases));

    const anyDrifted = filePlan.some((f) => f.action === 'drifted');
    return { exitCode: anyDrifted ? 2 : 0, output: lines.join('\n') };
  } catch (e) {
    if (e instanceof RefusalError) {
      print(`refused: ${e.message}`);
      return { exitCode: 1, output: lines.join('\n') };
    }
    print(`error: ${e.stack || e.message}`);
    return { exitCode: 1, output: lines.join('\n') };
  }
}
