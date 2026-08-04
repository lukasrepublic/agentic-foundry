// identity.mjs — the out-of-session identity half (AC-BCL-7), proved differentially equal to
// `scripts/foundry-bootstrap.sh`. Every git-config write goes through `git config` in an argv
// position — never composed/templated text — so git's own writer, quoting and locking apply, and
// the CLI's output is byte-identical to the shell script's for the same inputs.
import { execFileSync, execFile as execFileCb } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { RefusalError, physicalResolve } from './util.mjs';

const execFileAsync = promisify(execFileCb);

const SLUG_RE = /^[A-Za-z0-9._-]+$/;

/** Validate a --gh-account value against the closed charset, refusing '.'/'..' (AC-BCL-7). */
export function validateSlug(raw) {
  if (raw === '.' || raw === '..' || !SLUG_RE.test(raw)) {
    throw new RefusalError(
      `invalid --gh-account slug (need ^[A-Za-z0-9._-]+$, not '.'/'..'): ${raw}`,
      'gh-account',
    );
  }
  return raw;
}

const STRIPPED_ENV_VARS = ['GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_HOST'];

/** At most ONE `gh api user` invocation, timeout-bounded, jailed under its own GH_CONFIG_DIR, with
 * the five higher-precedence token/host variables stripped from the child environment. Compares
 * the returned `.login` against the declared slug (ASCII case-insensitive) and discards the probe
 * WHOLE on any mismatch, parse failure, timeout, or absent `gh` — never a partial adopt. No probe
 * output (in whole or in part) is ever written to a file, logged, or printed by this function. */
export async function probeGhIdentity(slug, { homeDir, timeoutMs = 5000, env = process.env } = {}) {
  // A STRING LITERAL, never an env-supplied name (PR #61 security review Risk 1). This previously
  // read `env.FOUNDRY_TEST_GH_BIN || 'gh'`, which made the spec's "the child-process set is closed
  // to {git, gh}, so `claude` is never spawned" literally false: anything able to set that variable
  // — a direnv .envrc in a freshly cloned repo, an exported var, CI — chose the binary this line
  // spawns, `claude` included. It was also DEAD: the only reference in the tree was its own
  // definition, since the test suites inject their `gh` stub via PATH. Removed rather than guarded;
  // an unused escape hatch is all cost.
  const gh = 'gh';
  const jail = path.join(homeDir, '.config', `gh-${slug}`);
  fs.mkdirSync(jail, { recursive: true });
  const childEnv = { ...env, GH_CONFIG_DIR: jail };
  for (const v of STRIPPED_ENV_VARS) delete childEnv[v];

  let stdout;
  try {
    const result = await execFileAsync(gh, ['api', 'user'], { env: childEnv, timeout: timeoutMs });
    stdout = result.stdout;
  } catch {
    return null; // absent gh, non-zero exit, or timeout — degrade, no further network call
  }
  let data;
  try {
    data = JSON.parse(stdout);
  } catch {
    return null;
  }
  const login = typeof data.login === 'string' ? data.login : '';
  if (login.toLowerCase() !== slug.toLowerCase()) return null; // whole discard, never partial
  const name = typeof data.name === 'string' ? data.name : '';
  const email = typeof data.email === 'string' ? data.email : '';
  if (!name || !email) return null;
  return { name, email };
}

function parseGitAuthor(raw) {
  const m = /^(.*) <([^<>]+)>$/.exec(raw);
  if (!m) {
    throw new RefusalError(`malformed --git-author (expected 'Name <email>'): ${raw}`, 'git-author');
  }
  return { name: m[1], email: m[2] };
}

/** Resolve the declared "Name <email>" (AC-BCL-7 order): (1) --git-author, (2) the bounded gh
 * probe, (3) an interactive TTY prompt, (4) fail closed. */
export async function resolveIdentity(slug, { gitAuthor, homeDir, isTTY, promptFn } = {}) {
  if (gitAuthor) {
    return parseGitAuthor(gitAuthor);
  }
  const probed = await probeGhIdentity(slug, { homeDir });
  if (probed) return probed;
  if (isTTY && promptFn) {
    const name = (await promptFn(`commit-identity name for account ${slug}: `)).trim();
    const email = (await promptFn(`commit-identity email for account ${slug}: `)).trim();
    if (name && email) return { name, email };
  }
  throw new RefusalError(
    `could not resolve a commit identity for '${slug}' (give --git-author 'Name <email>', authenticate gh, or run interactively)`,
    'git-author',
  );
}

function validateIdentityValue(kind, val) {
  if (!val) throw new RefusalError(`commit-identity ${kind} is empty (refusing to write a fabricated identity)`);
  if (val.includes('\n')) throw new RefusalError(`commit-identity ${kind} is not single-line (refusing to write)`);
  if (kind === 'email' && !val.includes('@')) {
    throw new RefusalError(`commit-identity email lacks '@': ${val} (refusing to write)`);
  }
}

/** Physically-resolved target root with a trailing separator (the <canon> Terminology term). Works
 * even when the target does not yet exist (preview time), by resolving the longest existing
 * ancestor and appending the missing segments (see util.physicalResolve). */
export function canonicalTargetWithSlash(targetRoot) {
  const real = physicalResolve(targetRoot);
  return real.endsWith(path.sep) ? real : real + path.sep;
}

/** Describe (never execute) the three machine-scope writes wireIdentity would make — used by the
 * preview/dry-run path (AC-BCL-3), which SHALL spawn zero child processes. */
export function plannedMachineScopeWrites({ slug, targetRoot, homeDir }) {
  const canon = canonicalTargetWithSlash(targetRoot);
  const matchPrefix = os.platform() === 'darwin' ? 'gitdir/i:' : 'gitdir:';
  const inc = path.join(homeDir, '.config', 'git', `identity-${slug}`);
  return [
    { scope: 'global git config (machine-wide)', description: `includeIf.${matchPrefix}${canon}.git.path -> ${inc}` },
    { scope: 'global git config (machine-wide)', description: `includeIf.${matchPrefix}${canon}.git/.path -> ${inc}` },
    { scope: 'per-account include file (machine-wide)', description: inc },
  ];
}

/** Wire the three machine-scope artifacts + the repo-local useConfigOnly + the .claude/gh-identity
 * marker, EXACTLY as `scripts/foundry-bootstrap.sh`'s seed_commit_identity/seed_gh_identity do:
 * every git-config write goes through `git config` in an argv position (AC-BCL-7). Returns the
 * paths written, for the caller's machine-scope-write accounting (AC-BCL-9). */
export function wireIdentity({ slug, name, email, targetRoot, homeDir }) {
  validateIdentityValue('name', name);
  validateIdentityValue('email', email);

  const inc = path.join(homeDir, '.config', 'git', `identity-${slug}`);
  fs.mkdirSync(path.dirname(inc), { recursive: true });
  execFileSync('git', ['config', '--file', inc, 'user.name', name]);
  execFileSync('git', ['config', '--file', inc, 'user.email', email]);

  const canon = canonicalTargetWithSlash(targetRoot);
  const matchPrefix = os.platform() === 'darwin' ? 'gitdir/i:' : 'gitdir:';
  const narrow1 = `${matchPrefix}${canon}.git`;
  const narrow2 = `${matchPrefix}${canon}.git/`;
  const gitConfigEnv = { ...process.env, HOME: homeDir };
  execFileSync('git', ['config', '--global', `includeIf.${narrow1}.path`, inc], { env: gitConfigEnv });
  execFileSync('git', ['config', '--global', `includeIf.${narrow2}.path`, inc], { env: gitConfigEnv });

  const dotGitConfig = path.join(targetRoot, '.git', 'config');
  execFileSync('git', ['config', '--file', dotGitConfig, 'user.useConfigOnly', 'true']);

  const claudeDir = path.join(targetRoot, '.claude');
  fs.mkdirSync(claudeDir, { recursive: true });
  const marker = path.join(claudeDir, 'gh-identity');
  fs.writeFileSync(marker, `${slug}\n`);

  return { includeFile: inc, narrow1, narrow2, dotGitConfig, marker };
}
