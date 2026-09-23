// statuslineWiring.mjs — statusline-wiring (v1.17.0, AC-SLW-1/-2).
//
// The token-budget status line (`⌂ <repo>:<branch> · tok ██████░░░░ 69% · ⚙️ factory`) is rendered
// by the plugin's shipped `scripts/foundry-statusline.sh`, reached through a thin wrapper at
// `.claude/hooks/foundry-statusline.sh` and a `statusLine` key in `.claude/settings.json`. Until
// v1.17.0 nothing shipped that wiring: /foundry:init only verified it, and the wrapper printed
// nothing when its one cache glob missed — on the operator's second machine the bar was simply
// absent, with no line saying which piece was missing.
//
// This module is the WRITER, and it runs only post-trust — from `update-agentic-workspace` and from
// `create-agentic-workspace --existing` on a workspace whose `.claude/settings.json` already exists.
// The greenfield create path never calls it: feat-foundry-bootstrap-cli AC-BCL-4(c) (a frozen
// security Block) closes the pre-session settings key set, and that reasoning stands.
//
// Two artifacts, two disciplines:
//   * the wrappers are FRAMEWORK-OWNED: absent -> create; present with the framework marker ->
//     converged onto the shipped bytes; present WITHOUT the marker -> kept (the operator wrote
//     their own; it is never touched). Mode 0755.
//   * the settings keys are ADDED ONLY WHEN ABSENT: an existing `statusLine`/`subagentStatusLine`
//     value, whatever it points at, is never overwritten. Same narrow-key discipline as the floor.
// Neither ever contributes to the exit-2 drift verdict.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin } from './util.mjs';
import { resolveTarget, readTarget, writeTargetAtomically } from './floorReconcile.mjs';

export const MARKER = 'feat-foundry-init-statusline-wrapper';
export const WRAPPERS = Object.freeze([
  { template: 'foundry-statusline.sh', rel: '.claude/hooks/foundry-statusline.sh', key: 'statusLine' },
  { template: 'foundry-subagent-statusline.sh', rel: '.claude/hooks/foundry-subagent-statusline.sh', key: 'subagentStatusLine' },
]);

export function desiredSettingsValue(rel) {
  return { type: 'command', command: `$CLAUDE_PROJECT_DIR/${rel}`, padding: 0 };
}

/** Plan only. `files[]` rows carry `{ rel, abs, action: create|converged|unchanged|kept|refused, bytes }`;
 * `keys[]` rows carry `{ key, action: wired|already-wired }`; `settingsPresent` says whether the
 * post-trust precondition held (when it did not, the plan is empty and renders nothing). */
export function planStatuslineWiring({ physicalRoot, templatesDir }) {
  const target = resolveTarget(physicalRoot);
  const plan = { settingsPresent: target.present, settingsPath: target.path, files: [], keys: [], applied: false };
  if (!target.present) return plan;
  for (const w of WRAPPERS) {
    const abs = confinedJoin(physicalRoot, w.rel);
    const bytes = fs.readFileSync(path.join(templatesDir, w.template));
    if (abs === null) { plan.files.push({ rel: w.rel, abs: null, action: 'refused', reason: 'path escapes the target root', bytes }); continue; }
    const st = fs.lstatSync(abs, { throwIfNoEntry: false });
    let action;
    if (!st) action = 'create';
    else if (!st.isFile()) action = 'refused';
    else {
      const cur = fs.readFileSync(abs);
      if (cur.equals(bytes)) action = 'unchanged';
      else if (cur.toString('utf-8').includes(MARKER)) action = 'converged';
      else action = 'kept';
    }
    plan.files.push({ rel: w.rel, abs, action, bytes, ...(action === 'refused' && st ? { reason: 'not a regular file' } : {}) });
  }
  const settings = readTarget(target.path);
  for (const w of WRAPPERS) {
    const present = Object.prototype.hasOwnProperty.call(settings, w.key);
    plan.keys.push({ key: w.key, action: present ? 'already-wired' : 'wired' });
  }
  return plan;
}

/** Apply: write create/converged wrappers atomically with mode 0755; add absent keys in one
 * atomic settings write, leaving every other key byte-for-byte as it was. */
export function applyStatuslineWiring(plan) {
  if (!plan.settingsPresent) return plan;
  for (const f of plan.files) {
    if (f.action !== 'create' && f.action !== 'converged') continue;
    fs.mkdirSync(path.dirname(f.abs), { recursive: true });
    const tmp = `${f.abs}.statusline.tmp`;
    fs.writeFileSync(tmp, f.bytes, { mode: 0o755 });
    fs.chmodSync(tmp, 0o755);
    fs.renameSync(tmp, f.abs);
  }
  const toAdd = plan.keys.filter((k) => k.action === 'wired');
  if (toAdd.length > 0) {
    const settings = readTarget(plan.settingsPath);
    for (const k of toAdd) {
      if (Object.prototype.hasOwnProperty.call(settings, k.key)) continue; // raced in since plan
      const w = WRAPPERS.find((x) => x.key === k.key);
      settings[k.key] = desiredSettingsValue(w.rel);
    }
    writeTargetAtomically(plan.settingsPath, settings);
  }
  plan.applied = true;
  return plan;
}

/** Rows for the preview / Phase 4 output. Empty when the post-trust precondition did not hold. */
export function renderStatuslineRows(plan) {
  if (!plan || !plan.settingsPresent) return [];
  const rows = [];
  for (const f of plan.files) {
    const suffix = f.action === 'kept' ? ' (operator-owned, no framework marker — never reconciled)'
      : f.action === 'refused' ? ` (refused: ${f.reason})` : '';
    rows.push(`  [${f.action}] ${f.rel}${suffix}`);
  }
  const wired = plan.keys.filter((k) => k.action === 'wired').map((k) => k.key);
  const already = plan.keys.filter((k) => k.action === 'already-wired').map((k) => k.key);
  if (wired.length) rows.push(`  [statusline] wired ${wired.join(', ')} in .claude/settings.json`);
  if (already.length) rows.push(`  [statusline] already wired: ${already.join(', ')} (existing value kept)`);
  return rows;
}

export function statuslineChanged(plan) {
  return Boolean(plan && plan.applied && (
    plan.files.some((f) => f.action === 'create' || f.action === 'converged')
    || plan.keys.some((k) => k.action === 'wired')
  ));
}
