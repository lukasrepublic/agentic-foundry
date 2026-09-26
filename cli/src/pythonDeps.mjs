// v1.18.2: the plugin's scripts import two third-party Python packages (jsonschema, PyYAML), and
// nothing ever put them on the machine: the plugin declared no runtime dependency, the updater
// never checked, and a fresh agent container (whose image did not carry them) failed its doctor and
// every contract script. This phase makes the interpreter the scripts run under (`python3` on PATH)
// able to import every declared module, installing ONLY what is missing — an existing install is
// never upgraded or replaced — at the exact versions CI tests (`python-requirements.json`, kept equal
// to the plugin's `requirements.txt` by test).
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

/** The declared runtime requirements shipped with this package: [{ module, requirement }]. */
export function loadPythonRequirements(pkgDir) {
  const doc = JSON.parse(fs.readFileSync(path.join(pkgDir, 'python-requirements.json'), 'utf-8'));
  return doc.requirements;
}

// Prints JSON: the interpreter, whether it is a venv, and which declared modules cannot be found.
// find_spec (no import) so a broken package's import-time side effects never run here.
const PROBE = [
  'import importlib.util, json, sys',
  'mods = sys.argv[1:]',
  'print(json.dumps({"python": sys.executable, "version": "%d.%d" % sys.version_info[:2],',
  '  "venv": sys.prefix != sys.base_prefix,',
  '  "missing": [m for m in mods if importlib.util.find_spec(m) is None]}))',
].join('\n');

/** Probe `python3` for the declared modules. `{ ok:false, reason }` when python3 cannot run. */
export function probePythonDeps(requirements, { env = process.env, python = 'python3', spawn = spawnSync } = {}) {
  const r = spawn(python, ['-c', PROBE, ...requirements.map((q) => q.module)], { env, encoding: 'utf-8', timeout: 30000 });
  if (r.error || r.status !== 0) {
    return { ok: false, reason: r.error ? `${python} not runnable (${r.error.code || r.error.message})` : `${python} probe exited ${r.status}` };
  }
  try {
    const out = JSON.parse(String(r.stdout).trim().split('\n').pop());
    const missing = requirements.filter((q) => out.missing.includes(q.module));
    return { ok: true, python: out.python, version: out.version, venv: out.venv, missing };
  } catch {
    return { ok: false, reason: `${python} probe output unreadable` };
  }
}

/** The pip argv for installing `missing`: `--user` outside a venv (a venv has no user site). */
export function pipArgs(missing, { venv, breakSystem = false }) {
  const args = ['-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--no-input'];
  if (!venv) args.push('--user');
  if (breakSystem) args.push('--break-system-packages');
  return [...args, ...missing.map((q) => q.requirement)];
}

/**
 * Install what is missing, then re-probe. A PEP 668 "externally-managed-environment" refusal is
 * retried once with --break-system-packages: with --user that writes only the user site (~/.local
 * or ~/Library/Python), never the system interpreter's own packages. Never throws.
 * Returns { verdict: 'already current'|'changed'|'failed'|'skipped', installed, missing, reason }.
 */
export function ensurePythonDeps(requirements, { env = process.env, python = 'python3', spawn = spawnSync, dryRun = false } = {}) {
  const before = probePythonDeps(requirements, { env, python, spawn });
  if (!before.ok) return { verdict: 'skipped', installed: [], missing: requirements.map((q) => q.module), reason: before.reason };
  if (before.missing.length === 0) return { verdict: 'already current', installed: [], missing: [], python: before.python };
  if (dryRun) {
    return { verdict: 'would change', installed: [], missing: before.missing.map((q) => q.module), python: before.python };
  }
  let r = spawn(python, pipArgs(before.missing, { venv: before.venv }), { env, encoding: 'utf-8', timeout: 600000 });
  const out = `${r.stdout || ''}${r.stderr || ''}`;
  if (r.status !== 0 && /externally-managed-environment/.test(out)) {
    r = spawn(python, pipArgs(before.missing, { venv: before.venv, breakSystem: true }), { env, encoding: 'utf-8', timeout: 600000 });
  }
  const after = probePythonDeps(requirements, { env, python, spawn });
  const still = after.ok ? after.missing.map((q) => q.module) : before.missing.map((q) => q.module);
  const installed = before.missing.map((q) => q.module).filter((m) => !still.includes(m));
  if (still.length === 0) return { verdict: 'changed', installed, missing: [], python: before.python };
  const tail = `${r.stdout || ''}${r.stderr || ''}`.trim().split('\n').slice(-1)[0] || `exit ${r.status}`;
  const noPip = /No module named pip/.test(`${r.stdout || ''}${r.stderr || ''}`);
  return {
    verdict: 'failed', installed, missing: still, python: before.python,
    reason: noPip
      ? `pip is not available for ${before.python} — install it (Debian/Ubuntu: apt-get install python3-pip, or python3-${still.includes('yaml') ? 'yaml' : 'jsonschema'} directly), then re-run`
      : `pip install failed: ${tail}`,
  };
}

/** One output row per outcome, in the updater's `[verdict] subject — detail` shape. */
export function renderPythonDepsRow(result, requirements) {
  const want = requirements.map((q) => q.requirement).join(', ');
  switch (result.verdict) {
    case 'already current': return `  [ok] python deps: ${requirements.map((q) => q.module).join(', ')} importable (${result.python})`;
    case 'would change': return `  [would install] python deps: ${result.missing.join(', ')} for ${result.python} (${want}; user site, missing only)`;
    case 'changed': return `  [installed] python deps: ${result.installed.join(', ')} for ${result.python} (user site)`;
    case 'skipped': return `  [skipped] python deps: ${result.reason}`;
    default: return `  [failed] python deps: still missing ${result.missing.join(', ')} — ${result.reason}`;
  }
}
