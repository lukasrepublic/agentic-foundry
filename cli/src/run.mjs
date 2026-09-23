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
  resolveTarget, readTarget, applyAdditions, planReconcile, writeTargetAtomically, renderPlan,
} from './floorReconcile.mjs';
import { reconcileGitignorePlan, applyGitignorePlan, renderGitignoreRow } from './gitignoreReconcile.mjs';
import { planAmendmentsBackfill, applyAmendmentsBackfill, renderAmendmentsRow } from './amendmentsBackfill.mjs';
import { planStatuslineWiring, applyStatuslineWiring, renderStatuslineRows } from './statuslineWiring.mjs';

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
  // ER #95 — STREAM, don't accumulate. This used to only push into `lines`, which bin printed
  // after runCli returned, so every prompt fired against a blank screen: "Write the workspace as
  // previewed above? [y/N]" was asked with nothing above it, and the preview — the file plan, the
  // machine-scope writes OUTSIDE the target root, and the 62 permission rules — scrolled past
  // afterwards. A confirmation whose subject is not yet on screen cannot be informed consent, and
  // its mere presence claims a review that did not happen.
  //
  // It became load-bearing with --reconcile-floor. R8 was answered live: reconciling an
  // already-trusted workspace fires NO trust dialog, so this prompt is the ONLY consent moment
  // there is. `lines` is still accumulated because runCli's return contract exposes it.
  const print = (s) => {
    lines.push(s);
    output.write(`${s}\n`);
  };

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

    // gitignore-block-reconcile (ER #177, AC-GBR-1/-2/-3): the generic managed-file row above
    // already covers `.gitignore` for the CREATE case (absent -> the full template lands verbatim,
    // already converged) and for the byte-identical case, but once an adopter's `.gitignore` also
    // carries its own lines it will never again match the template byte-for-byte, so that row reads
    // `drifted` forever and the never-clobber plan never writes it — which is exactly why the
    // sentinel-delimited block needs its OWN, narrower reconcile. Computed here (before the
    // dry-run return) so --dry-run reports the same action a real run would take; `null` for an
    // absent `.gitignore`, which is the CREATE path's business and prints nothing extra.
    const gitignorePlan = reconcileGitignorePlan({
      physicalRoot, templatesDir: path.join(pkgDir, 'templates'),
    });
    const gitignoreRow = renderGitignoreRow(gitignorePlan);
    if (gitignoreRow) {
      print('');
      print(gitignoreRow);
    }
    // amendments-backfill (ER #214, AC-AMB-1): every `specs/**/feat-*.md` with a normative region
    // and no `## Amendments` section after it gets the empty section `/foundry:amend` requires.
    // Planned here for the same reason as the gitignore block — --dry-run reports the same row a
    // real run would act on — and `null` when the workspace has no specs at all (a fresh scaffold).
    const amendmentsPlan = planAmendmentsBackfill({ physicalRoot });
    const amendmentsRow = renderAmendmentsRow(amendmentsPlan);
    if (amendmentsRow) {
      if (!gitignoreRow) print('');
      print(amendmentsRow);
    }
    // statusline-wiring (v1.17.0, AC-SLW-1/-2): ONLY on --existing --reconcile-floor. A plain
    // --existing run never touches .claude/settings.json (AC-BCL-9: an existing settings file is
    // reported drifted, left byte-identical, never merged); --reconcile-floor is the one opt-in
    // that already permits a narrow-key write to it, and this wiring is the same class of write.
    // The greenfield create path never wires it — feat-foundry-bootstrap-cli AC-BCL-4(c) closes
    // the pre-session key set, deliberately. planStatuslineWiring returns an empty plan when
    // settings.json is absent. The upgrader (update.mjs) always reconciles the floor, so it
    // always wires.
    const statuslinePlan = answers.existing && answers.reconcileFloor
      ? planStatuslineWiring({ physicalRoot, templatesDir: path.join(pkgDir, 'templates') })
      : null;
    for (const row of renderStatuslineRows(statuslinePlan)) print(row);

    // Resolved HERE — before the write phase and before the dry-run return — because the reconcile
    // below must know what it would add in order to decide whether to write at all, and --dry-run
    // must be able to report those rules. That requirement is carried by the TRACKED classification
    // a few lines down; the union classification the advisory report needs is derived after the
    // write instead, for the reason stated there.
    const pluginRootExpansion = expandPluginRootGlob(map.plugin_root_glob, homeDir);

    // The reconcile classifies against the TRACKED settings.json alone. A floor rule carried only
    // in the untracked settings.local.json reads as covered in the union report below, so the tracked file
    // would stay incomplete while the report said converged — and the repo would then ship to every
    // other clone and to CI without it.
    let floorPlan = null;
    let floorRetirementPlan = null;
    let floorTarget = null;
    if (answers.reconcileFloor) {
      floorTarget = resolveTarget(physicalRoot);
      if (floorTarget.present) {
        const settingsObj = readTarget(floorTarget.path);
        // AC-FRR-1 (ER #199, review round 1): retirement first, additions planned against the
        // POST-retirement rule set — never against the raw settingsObj directly. See
        // floorReconcile.mjs's own comment on planReconcile for why the other order loses a grant
        // for one cycle across a map restructure.
        const { additionsPlan, retirementPlan } = planReconcile({
          settingsObj, map, pins, pluginRootExpansion, unreadableOrigins: [], home: homeDir,
        });
        floorPlan = additionsPlan;
        floorRetirementPlan = retirementPlan;
        print('');
        for (const line of renderPlan(floorPlan, {
          applied: false, retirementPlan: floorRetirementPlan, mapEntryCount: map.entries.length,
        })) print(line);
      } else {
        // absent settings.json is the CREATE path's business, not this one's — the managed-file
        // plan above already writes the full floor for it, and racing that would duplicate it
        print('');
        print('permission-floor reconcile: .claude/settings.json absent — left to the create path.');
      }
    }

    // Refuse BEFORE anything is written. isYesMode is true whenever stdin is not a TTY, which also
    // waives the --existing basename ceremony, so a piped invocation would otherwise mutate the
    // permission floor unattended; --yes must be given EXPLICITLY. This sits above applyPlan
    // deliberately — a "refused" verdict printed after the scaffold write had already landed reads
    // as "nothing happened", which is the one thing it must not mean.
    const floorHasWork = Boolean(floorPlan)
      && (floorPlan.total > 0 || (floorRetirementPlan && floorRetirementPlan.total > 0));
    if (floorHasWork && !isTTY && answers.yes !== true) {
      throw new RefusalError(
        'refusing --reconcile-floor without a terminal: pass --yes explicitly to confirm the write',
        'reconcile-floor',
      );
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
    // A `refused` gitignorePlan is a no-op here — applyGitignorePlan only writes on `converged` /
    // `appended` — so a malformed managed block never blocks the rest of this run's writes; it is
    // reported (the row above, and the exit code below) rather than escalated to a hard refusal,
    // since it is a data-integrity issue local to one file, not the security-shaped case
    // --reconcile-floor's pre-write refusal exists for.
    applyGitignorePlan(gitignorePlan);
    // Same never-clobber posture as the gitignore block: only a spec classified `absent` is ever
    // written, and it is re-classified immediately before the atomic append (AC-AMB-2).
    applyAmendmentsBackfill(amendmentsPlan);

    if (floorHasWork) {
      // floorPlan.settingsObj is ALREADY post-retirement (planReconcile derived it that way) —
      // applyAdditions composes onto it directly; a second applyRetirements call here would be
      // retiring an object that was never given the rows back in the first place.
      writeTargetAtomically(floorTarget.path, applyAdditions(floorPlan.settingsObj, floorPlan, { map, pins }));
      print('');
      for (const line of renderPlan(floorPlan, {
        applied: true, retirementPlan: floorRetirementPlan, mapEntryCount: map.entries.length,
      })) print(line);
    }
    // AFTER the floor write above: that write serialises a settings object read before this
    // point, so wiring the statusLine keys first would have been overwritten by it. The wiring
    // re-reads settings.json itself and adds only the absent keys (AC-SLW-2).
    if (statuslinePlan) applyStatuslineWiring(statuslinePlan);

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

    // The advisory report describes the state the operator is LEFT in, so it is classified from the
    // files as they now stand — AFTER applyPlan and after any reconcile write. Classifying it up
    // front instead makes it name rules that exist by the time it prints: on the reconcile path
    // that is every rule just added, so the run reported `added allow=42, ask=16` and then listed
    // all 58 as absent, which reads as the write having silently failed. The create path had the
    // same defect whenever it wrote settings.json itself. Deriving it here cannot contradict the
    // write above it, at the cost of re-reading two small files on the write path.
    //
    // This is only the UNION (settings.json + settings.local.json) report. The reconcile's own
    // classification stays above the write and over the tracked file alone — consent has to be
    // informed by what WILL be written, which is a different question from what remains after.
    const { effective, unreadable } = readEffectiveRules(physicalRoot);
    const findings = classifyDrift(map, effective, {
      pluginRootExpansion, unreadableOrigins: unreadable, home: homeDir,
    });
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
    const gitignoreWrote = Boolean(
      gitignorePlan && (gitignorePlan.action === 'converged' || gitignorePlan.action === 'appended'),
    );
    print(TRUST_HANDOFF_TEXT(targetRoot, {
      isGitRepo,
      // only when a reconcile actually wrote — a dry run, a no-op second run, or a plain scaffold
      // all keep the standard hand-off. The gitignore-block-reconcile counts too: it is the SAME
      // kind of write to an already-trusted workspace floorPlan's own comment describes, just to a
      // different file.
      reconciledExisting: Boolean(floorPlan && floorPlan.total > 0) || gitignoreWrote
        || Boolean(amendmentsPlan && amendmentsPlan.applied && amendmentsPlan.written > 0),
    }));

    // A refused gitignore block joins the SAME non-zero bucket `drifted` files use (exit 2, "needs
    // the operator's attention") rather than exit 1's hard-refusal bucket — the rest of the run's
    // writes already landed, so "refused" here must not read as "nothing happened". exitCodeForPlan
    // only ever returns 0 or 2, so a refusal simply forces 2 rather than deferring to it.
    const gitignoreRefused = Boolean(gitignorePlan && gitignorePlan.action === 'refused');
    return {
      exitCode: gitignoreRefused ? 2 : exitCodeForPlan(plan),
      output: lines.join('\n'),
    };
  } catch (e) {
    if (e instanceof RefusalError) {
      print(`refused: ${e.message}`);
      return { exitCode: 1, output: lines.join('\n') };
    }
    print(`error: ${e.stack || e.message}`);
    return { exitCode: 1, output: lines.join('\n') };
  }
}
