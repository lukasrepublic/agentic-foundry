// governanceUpgrade.mjs — gv-15: the updater's governance step. After the plugin refresh, when the workspace
// carries `.foundry/governance.lock`, run `foundry_governance.py upgrade --pr` from the freshly updated plugin and
// report one row; a row that changed anything names /foundry:post-upgrade, whose skill reviews the pull request.
//
// The step is deliberately thin: the three-way merge, the dirty-tree refusal, the migrations and the pull request
// all live in the Python script (one implementation, testable on its own). This module decides only WHETHER and
// WHERE to run it and how to read the exit code. One spawn site (runPython below) with a fixed argv.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { scopeRecordsFor } from './pluginRefresh.mjs';

export const GOVERNANCE_LOCK_REL = '.foundry/governance.lock';
const SCRIPT_REL = path.join('scripts', 'foundry_governance.py');
const REVIEW = '/foundry:post-upgrade';

/** The installed plugin's directory for this workspace: the project-scope record first, then the user-scope one. */
export function resolveInstallPath(registry, pluginKey, cwd) {
  if (!registry || !registry.ok) return null;
  const records = scopeRecordsFor(registry.doc, pluginKey).filter((r) => r && typeof r === 'object');
  const project = records.find((r) => r.projectPath && path.resolve(String(r.projectPath)) === path.resolve(cwd));
  const user = records.find((r) => !r.projectPath);
  const hit = project || user;
  return hit && typeof hit.installPath === 'string' && hit.installPath.trim() ? hit.installPath : null;
}

export function hasGovernanceLock(cwd) {
  const st = fs.lstatSync(path.join(cwd, GOVERNANCE_LOCK_REL), { throwIfNoEntry: false });
  return Boolean(st && st.isFile());
}

/** A spawnSync-shaped result; never throws. */
function runPython(python, argv, opts) {
  try {
    return { status: 0, stdout: String(execFileSync(python, argv, { ...opts, stdio: ['ignore', 'pipe', 'pipe'] })), stderr: '' };
  } catch (e) {
    if (typeof e.status === 'number') return { status: e.status, stdout: String(e.stdout || ''), stderr: String(e.stderr || '') };
    return { error: e, status: null, stdout: '', stderr: '' };
  }
}

/**
 * Run the step. `{ verdict, reason?, detail? }` with verdict one of:
 *   skipped | would run | already current | changed | attention (exit 1: conflict markers left) | failed.
 * Never throws: a missing plugin root or an unrunnable python is `failed`, never a silent pass.
 */
export function runGovernanceUpgrade({ cwd, pluginRoot, python = 'python3', env = process.env, dryRun = false }) {
  if (!hasGovernanceLock(cwd)) return { verdict: 'skipped', reason: 'no governance lock in this workspace' };
  if (dryRun) return { verdict: 'would run', detail: 'foundry_governance.py upgrade --pr after the plugin refresh' };
  if (!pluginRoot || !fs.existsSync(path.join(pluginRoot, SCRIPT_REL))) {
    return { verdict: 'failed', reason: 'the installed plugin has no scripts/foundry_governance.py (cannot find its install directory)' };
  }
  // Never run python with the workspace as cwd: -m / -c would put it first on sys.path. The script takes the
  // project explicitly. PYTHONSAFEPATH (3.11+) keeps the script directory off sys.path ahead of the stdlib.
  const argv = [path.join(pluginRoot, SCRIPT_REL), '--project-dir', cwd, 'upgrade', '--pr'];
  const r = runPython(python, argv, { env: { ...env, PYTHONSAFEPATH: '1' }, cwd: os.tmpdir(), encoding: 'utf-8', timeout: 600000 });
  if (r.error) return { verdict: 'failed', reason: `${python} not runnable (${r.error.code || r.error.message})` };
  const lines = `${r.stdout}\n${r.stderr}`.split('\n').map((l) => l.trim()).filter(Boolean);
  const tail = lines.slice(-1)[0] || `exit ${r.status}`;
  if (r.status === 0) {
    return /already current/.test(r.stdout) ? { verdict: 'already current' } : { verdict: 'changed', detail: tail };
  }
  if (r.status === 1) return { verdict: 'attention', reason: 'merge conflicts are marked inline in the pull request', detail: tail };
  return { verdict: 'failed', reason: tail };
}

/** One output row in the updater's `[verdict] subject — detail` shape. */
export function renderGovernanceUpgradeRow(result) {
  const subject = 'governance-upgrade';
  switch (result.verdict) {
    case 'skipped': return `  [skipped] ${subject}: ${result.reason}`;
    case 'would run': return `  [would run] ${subject}: ${result.detail}`;
    case 'already current': return `  [ok] ${subject}: governance lock is current with the installed plugin`;
    case 'changed': return `  [changed] ${subject}: ${result.detail} — review the pull request with ${REVIEW}`;
    case 'attention': return `  [attention] ${subject}: ${result.reason} — resolve them with ${REVIEW}`;
    default: return `  [failed] ${subject}: ${result.reason} — fix the cause, rerun the updater, then ${REVIEW}`;
  }
}
