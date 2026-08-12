// run.mjs — the orchestrator. Wires argv -> answers -> preview -> confirm -> reconcile -> identity
// -> drift report -> trust hand-off. No `import` of a network module anywhere in this closure
// (AC-BCL-9); the only reachable egress is identity.mjs's bounded `gh api user` probe.
import fs from 'node:fs';
import path from 'node:path';
import { createInterface } from 'node:readline/promises';
import { execFileSync } from 'node:child_process';
import { QUESTION_TABLE } from './questions.mjs';
import { parseArgv, renderHelp } from './argv.mjs';
import { resolveAnswers, isYesMode } from './answers.mjs';
import { RefusalError, physicalResolve, isNonEmptyDir } from './util.mjs';
import { loadMap, buildSettings, classifyDrift } from './permissionFloor.mjs';
import { buildManagedFiles, DECLARED_PATH_SET } from './scaffold.mjs';
import { planManagedFiles, applyPlan, exitCodeForPlan } from './reconcile.mjs';
import { renderPreview, TRUST_HANDOFF_TEXT } from './preview.mjs';
import { validateSlug, resolveIdentity, wireIdentity, plannedMachineScopeWrites } from './identity.mjs';
import {
  resolveTarget, readTarget, readTrackedRules, planAdditions, applyAdditions,
  writeTargetAtomically, renderPlan,
} from './floorReconcile.mjs';

export { DECLARED_PATH_SET };

function loadPins(pkgDir) {
  const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf-8'));
  return pkg.foundry;
}

function ensureGitRepo(physicalRoot) {
  if (!fs.existsSync(path.join(physicalRoot, '.git'))) {
    fs.mkdirSync(physicalRoot, { recursive: true });
    execFileSync('git', ['init', '--quiet', physicalRoot]);
  }
}

/** Read the target's effective permission rules (settings.json unioned with settings.local.json,
 * origin-tracked) for the drift report. Never throws on absence; reports unreadable JSON. */
function readEffectiveRules(physicalRoot) {
  const effective = { allow: [], ask: [], deny: [] };
  const unreadable = [];
  for (const name of ['settings.json', 'settings.local.json']) {
    const p = path.join(physicalRoot, '.claude', name);
    if (!fs.existsSync(p)) continue;
    try {
      const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
      const perms = data.permissions || {};
      for (const tierKey of ['allow', 'ask', 'deny']) {
        for (const rule of perms[tierKey] || []) {
          effective[tierKey].push({ rule, origin: name, tierKey });
        }
      }
    } catch {
      unreadable.push(name);
    }
  }
  return { effective, unreadable };
}

function expandPluginRootGlob(glob, homeDir) {
  // Non-recursive expansion of ~/.claude/plugins/cache/*/foundry/* against homeDir.
  const rel = glob.replace(/^~\//, '');
  const parts = rel.split('/');
  let dirs = [homeDir];
  for (const part of parts) {
    const next = [];
    for (const d of dirs) {
      if (part === '*') {
        // statSync via throwIfNoEntry:false, never a bare statSync (PR #61 security review Risk 4).
        // This walk runs AFTER applyPlan, over the operator's own home directory, purely to derive
        // the ADVISORY `stale-plugin-path` finding. A broken symlink inside the plugin cache — not
        // the CLI's business and not something it can prevent — made the bare statSync throw into
        // run.mjs's catch-all and turned a SUCCESSFUL scaffold into exit 1 plus a stack trace. An
        // advisory probe must never be able to fail the run that already did its work.
        const dStat = fs.statSync(d, { throwIfNoEntry: false });
        if (dStat && dStat.isDirectory()) {
          for (const child of fs.readdirSync(d)) {
            const full = path.join(d, child);
            const childStat = fs.statSync(full, { throwIfNoEntry: false });
            if (childStat && childStat.isDirectory()) next.push(full);
          }
        }
      } else {
        const full = path.join(d, part);
        if (fs.existsSync(full)) next.push(full);
      }
    }
    dirs = next;
  }
  return dirs;
}

/** Run the CLI end to end. Returns { exitCode, output }. Never throws — every failure path is
 * caught and turned into a refusal-shaped exit 1 (or, for a bug, exit 1 with the error message). */
export async function runCli(argv, { cwd, isTTY, input, output, homeDir, pkgDir }) {
  const lines = [];
  const print = (s) => lines.push(s);

  try {
    let parsed;
    try {
      parsed = parseArgv(argv, QUESTION_TABLE);
    } catch (e) {
      if (e instanceof RefusalError) {
        print(`refused: ${e.message}`);
        return { exitCode: 1, output: lines.join('\n') };
      }
      throw e;
    }

    if (parsed.values.help === true) {
      print(renderHelp(QUESTION_TABLE));
      return { exitCode: 0, output: lines.join('\n') };
    }

    const yesMode = isYesMode(parsed.values, isTTY);
    const answers = await resolveAnswers(QUESTION_TABLE, parsed, { yesMode, input, output });

    const targetRoot = path.resolve(cwd, answers.dir);
    const nonEmpty = isNonEmptyDir(targetRoot);
    if (nonEmpty && !answers.existing) {
      throw new RefusalError(
        `${targetRoot} exists and is non-empty; use --existing to scaffold into it`,
        'existing',
      );
    }
    if (nonEmpty && answers.existing && !yesMode) {
      const rl = createInterface({ input, output });
      const typed = (await rl.question(`Type the directory's basename to confirm ('${path.basename(targetRoot)}'): `)).trim();
      rl.close();
      if (typed !== path.basename(targetRoot)) {
        throw new RefusalError('basename confirmation did not match; refusing', 'existing');
      }
    }

    let slug = '';
    if (answers.ghAccount) {
      slug = validateSlug(answers.ghAccount);
    }

    const physicalRoot = physicalResolve(targetRoot);
    const map = loadMap(path.join(pkgDir, 'permission-floor.json'));
    const pins = loadPins(pkgDir);
    const settingsObj = buildSettings(map, pins);
    const settingsBytes = Buffer.from(`${JSON.stringify(settingsObj, null, 2)}\n`, 'utf-8');

    const managedFiles = buildManagedFiles({
      templatesDir: path.join(pkgDir, 'templates'),
      physicalRoot,
      projectName: path.basename(targetRoot),
      stageMode: answers.stageMode,
      settingsBytes,
    });
    const plan = planManagedFiles(managedFiles);
    const machineScopeWrites = slug ? plannedMachineScopeWrites({ slug, targetRoot, homeDir }) : [];

    print(renderPreview({ plan, machineScopeWrites, map }));

    // Drift is classified HERE — before the write phase and before the dry-run return — not after
    // applyPlan where the advisory report used to compute it. Two things depend on the move: the
    // reconcile must know what it would add in order to decide whether to write at all, and
    // --dry-run must be able to report those rules, which it never could before because it returned
    // above the only classifyDrift call in the file.
    const { effective, unreadable } = readEffectiveRules(physicalRoot);
    const pluginRootExpansion = expandPluginRootGlob(map.plugin_root_glob, homeDir);
    const findings = classifyDrift(map, effective, {
      pluginRootExpansion, unreadableOrigins: unreadable, home: homeDir,
    });

    // The reconcile classifies against the TRACKED settings.json alone. A floor rule carried only
    // in the untracked settings.local.json reads as covered in the union above, so the tracked file
    // would stay incomplete while the report said converged — and the repo would then ship to every
    // other clone and to CI without it.
    let floorPlan = null;
    let floorTarget = null;
    if (answers.reconcileFloor) {
      floorTarget = resolveTarget(physicalRoot);
      if (floorTarget.present) {
        const settingsObj = readTarget(floorTarget.path);
        const trackedFindings = classifyDrift(map, readTrackedRules(settingsObj), {
          pluginRootExpansion, unreadableOrigins: [], home: homeDir,
        });
        floorPlan = planAdditions({ findings: trackedFindings, map, settingsObj, pins });
        floorPlan.settingsObj = settingsObj;
        print('');
        for (const line of renderPlan(floorPlan, { applied: false })) print(line);
      } else {
        // absent settings.json is the CREATE path's business, not this one's — the managed-file
        // plan above already writes the full floor for it, and racing that would duplicate it
        print('');
        print('permission-floor reconcile: .claude/settings.json absent — left to the create path.');
      }
    }

    if (answers.dryRun) {
      print('(dry-run: no write, no side effect, zero child processes spawned)');
      return { exitCode: 0, output: lines.join('\n') };
    }

    if (!yesMode) {
      const rl = createInterface({ input, output });
      const confirm = (await rl.question('Write the workspace as previewed above? [y/N]: ')).trim().toLowerCase();
      rl.close();
      if (confirm !== 'y' && confirm !== 'yes') {
        throw new RefusalError('write-phase confirmation declined; writing nothing');
      }
    }

    applyPlan(plan);

    if (floorPlan && floorPlan.total > 0) {
      // isYesMode is true whenever stdin is not a TTY, which also waives the --existing basename
      // ceremony — so a piped invocation would otherwise mutate the permission floor unattended.
      // The reconcile requires --yes to have been given EXPLICITLY, keeping the automation path
      // available and deliberate rather than inferred from the absence of a terminal.
      if (!isTTY && answers.yes !== true) {
        throw new RefusalError(
          'refusing --reconcile-floor without a terminal: pass --yes explicitly to confirm the write',
          'reconcile-floor',
        );
      }
      writeTargetAtomically(floorTarget.path, applyAdditions(floorPlan.settingsObj, floorPlan, { map, pins }));
      print('');
      for (const line of renderPlan(floorPlan, { applied: true })) print(line);
    }

    if (slug) {
      ensureGitRepo(physicalRoot);
      const identity = await resolveIdentity(slug, {
        gitAuthor: answers.gitAuthor,
        homeDir,
        isTTY,
        promptFn: isTTY
          ? async (q) => {
              const rl = createInterface({ input, output });
              const a = await rl.question(q);
              rl.close();
              return a;
            }
          : null,
      });
      wireIdentity({ slug, name: identity.name, email: identity.email, targetRoot: physicalRoot, homeDir });
    }

    if (findings.length > 0) {
      print('');
      print(`Permission-floor report (advisory, ${findings.length} finding(s)):`);
      for (const f of findings) print(`  [${f.class}] ${JSON.stringify(f)}`);
    }

    // Probed here, not inside preview.mjs, which renders text and never touches the filesystem.
    // `.git` is a DIRECTORY in a normal clone and a FILE in a worktree/submodule, so `existsSync`
    // on the path is the check that covers both — a directory-only test would tell a worktree user
    // to re-init a repository they already have.
    const isGitRepo = fs.existsSync(path.join(targetRoot, '.git'));
    print(TRUST_HANDOFF_TEXT(targetRoot, { isGitRepo }));

    return { exitCode: exitCodeForPlan(plan), output: lines.join('\n') };
  } catch (e) {
    if (e instanceof RefusalError) {
      print(`refused: ${e.message}`);
      return { exitCode: 1, output: lines.join('\n') };
    }
    print(`error: ${e.stack || e.message}`);
    return { exitCode: 1, output: lines.join('\n') };
  }
}
