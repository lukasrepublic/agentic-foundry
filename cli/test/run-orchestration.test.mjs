// run-orchestration.test.mjs — end-to-end coverage of runCli's ORDERING.
//
// The reconcile and drift primitives are unit-tested in floor-reconcile/floor-drift; what was
// untested is the sequence run.mjs assembles them into. That gap is why the advisory report
// shipped stale: every primitive was correct, and the orchestrator printed a pre-write
// classification after the write, so a run that had just added 58 rules listed all 58 as absent.
// An operator reading that output cannot distinguish it from a silently failed write.
//
// These drive runCli itself — real argv, real temp workspace, real files on disk.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCli } from '../src/run.mjs';
import { loadMap } from '../src/permissionFloor.mjs';
import { BEGIN_TOKEN, END_TOKEN, loadDesiredBlock } from '../src/gitignoreReconcile.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MAP = loadMap(path.join(CLI_DIR, 'permission-floor.json'));
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;

// realpath, not the raw tmpdir: on macOS /var is a symlink to /private/var, and the unresolved
// form makes the confinement join reject its own target root.
function scratch() {
  const dir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'run-orch-'));
  return dir;
}

const sink = () => {
  const chunks = [];
  return { write: (s) => chunks.push(s), text: () => chunks.join('') };
};

async function invoke(dir, extra = []) {
  const output = sink();
  const res = await runCli(
    ['--dir', dir, '--reconcile-floor', '--yes', '--gh-account', '', ...extra],
    {
      cwd: path.dirname(dir),
      isTTY: false,
      input: process.stdin,
      output,
      homeDir: os.homedir(),
      pkgDir: CLI_DIR,
    },
  );
  return { ...res, text: output.text() };
}

/** Every rule the report names, by drift class. */
function reported(text) {
  const out = {};
  for (const line of text.split('\n')) {
    const m = line.match(/^ {2}\[([a-z-]+)\] (\{.*\})$/);
    if (!m) continue;
    (out[m[1]] ||= []).push(JSON.parse(m[2]).rule);
  }
  return out;
}

const PIN = {
  [PINS.marketplace_name]: {
    source: { source: 'github', repo: PINS.marketplace_repo, ref: `v${PINS.plugin_version}` },
    autoUpdate: false,
  },
};

// ============================================================================================ //
// The defect: the advisory report contradicting the write it follows
// ============================================================================================ //

test('advisory_report_never_names_a_rule_the_same_run_just_added', async () => {
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  // A workspace that is pinned (so nothing is withheld) and carries the deny tier already, so the
  // run's whole job is the allow+ask tiers. Deny rules come from the map, never restated here.
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({
      extraKnownMarketplaces: PIN,
      permissions: { allow: [], ask: [], deny: MAP.entries.filter((e) => e.tier === 'deny').map((e) => e.rule) },
    }, null, 2)}\n`,
  );

  const { text } = await invoke(dir, ['--existing']);

  const added = text.match(/permission-floor reconcile: added allow=(\d+), ask=(\d+)/);
  assert.ok(added, 'the run did not report a completed reconcile');
  assert.ok(Number(added[1]) > 0, 'fixture added no allow rules — it proves nothing');

  const r = reported(text);
  assert.deepEqual(r['allow-absent'] ?? [], [],
    'the report names allow rules this same run added — it contradicts the write above it');
  assert.deepEqual(r['ask-absent'] ?? [], [],
    'the report names ask rules this same run added — it contradicts the write above it');

  // and the file really does carry them, so the assertions above are not passing vacuously
  const written = JSON.parse(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'));
  assert.equal(written.permissions.allow.length, MAP.entries.filter((e) => e.tier === 'allow').length);
});

test('create_path_report_reflects_the_settings_it_just_wrote', async () => {
  // The same defect reached the create path: applyPlan writes the full floor for a workspace with
  // no settings.json, and a report classified beforehand called every one of those rules absent.
  const dir = path.join(scratch(), 'fresh');
  const { text } = await invoke(dir);

  assert.ok(fs.existsSync(path.join(dir, '.claude', 'settings.json')), 'create path wrote no settings');
  const r = reported(text);
  assert.deepEqual(r['allow-absent'] ?? [], [], 'report calls the floor it just created absent');
  assert.deepEqual(r['ask-absent'] ?? [], [], 'report calls the floor it just created absent');
});

test('dry_run_still_reports_the_pre_write_state_and_writes_nothing', async () => {
  // The counterpart guard: --dry-run returns above the write, so its plan must still describe what
  // WOULD be added. Re-deriving the report post-write must not have moved that.
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({ extraKnownMarketplaces: PIN, permissions: { allow: [], ask: [], deny: [] } }, null, 2)}\n`,
  );
  const before = fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8');

  const { text } = await invoke(dir, ['--existing', '--dry-run']);

  assert.match(text, /permission-floor reconcile: would add allow=\d+/);
  assert.doesNotMatch(text, /permission-floor reconcile: added /);
  assert.equal(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'), before,
    'dry run mutated the target');
});

// ============================================================================================ //
// gitignore-block-reconcile (ER #177, AC-GBR-1/-4) — wired beside floorReconcile's own rows
// ============================================================================================ //

const STALE_GITIGNORE_BLOCK = [
  `# ${BEGIN_TOKEN} (managed by scripts/foundry-apply-runtime-gitignore.sh -- do not edit by hand)`,
  '# .foundry/ runtime partitions are ignored by default; the designed-tracked set is re-included.',
  '/.foundry/*',
  '!/.foundry/README.md',
  '!/.foundry/build-provenance.yaml',
  '!/.foundry/stack-profile.lock',
  `# ${END_TOKEN} (re-run the applier to converge; do not edit by hand)`,
];

function existingWorkspaceWithStaleGitignore(dir) {
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({
      extraKnownMarketplaces: PIN,
      permissions: { allow: [], ask: [], deny: MAP.entries.filter((e) => e.tier === 'deny').map((e) => e.rule) },
    }, null, 2)}\n`,
  );
  fs.writeFileSync(
    path.join(dir, '.gitignore'),
    ['# adopter line above', ...STALE_GITIGNORE_BLOCK, 'dist/'].map((l) => `${l}\n`).join(''),
  );
}

test('existing_workspace_reconcile_converges_a_stale_gitignore_block', async () => {
  const dir = scratch();
  existingWorkspaceWithStaleGitignore(dir);

  const { text } = await invoke(dir, ['--existing']);
  assert.match(text, /^ {2}\[converged] \.gitignore \(managed block\)$/m, text);

  const lines = fs.readFileSync(path.join(dir, '.gitignore'), 'utf-8').split('\n');
  const body = lines[lines.length - 1] === '' ? lines.slice(0, -1) : lines;
  assert.equal(body[0], '# adopter line above', 'the adopter line above the block was not preserved');
  assert.equal(body[body.length - 1], 'dist/', 'the adopter line below the block was not preserved');
  const desired = loadDesiredBlock(path.join(CLI_DIR, 'templates'));
  assert.deepEqual(body.slice(1, 1 + desired.length), desired, 'the block did not converge onto the template');
});

test('dry_run_reports_the_gitignore_row_and_writes_nothing', async () => {
  const dir = scratch();
  existingWorkspaceWithStaleGitignore(dir);
  const before = fs.readFileSync(path.join(dir, '.gitignore'));

  const { text } = await invoke(dir, ['--existing', '--dry-run']);
  assert.match(text, /^ {2}\[converged] \.gitignore \(managed block\)$/m, text);
  assert.deepEqual(fs.readFileSync(path.join(dir, '.gitignore')), before, 'dry run mutated .gitignore');
});

test('a_second_existing_reconcile_over_an_already_converged_block_is_unchanged_and_byte_stable', async () => {
  const dir = scratch();
  existingWorkspaceWithStaleGitignore(dir);
  await invoke(dir, ['--existing']);

  const gitignorePath = path.join(dir, '.gitignore');
  const beforeBytes = fs.readFileSync(gitignorePath);
  const beforeIno = fs.statSync(gitignorePath).ino;

  const { text } = await invoke(dir, ['--existing']);
  assert.match(text, /^ {2}\[unchanged] \.gitignore \(managed block\)$/m, text);
  assert.deepEqual(fs.readFileSync(gitignorePath), beforeBytes, 'a converged .gitignore was rewritten');
  assert.equal(fs.statSync(gitignorePath).ino, beforeIno, 'a converged .gitignore was rewritten');
});

test('a_malformed_gitignore_block_is_refused_and_the_rest_of_the_scaffold_still_lands', async () => {
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({ extraKnownMarketplaces: PIN, permissions: { allow: [], ask: [], deny: [] } }, null, 2)}\n`,
  );
  // an END with no BEGIN at all — malformed (AC-GBR-3)
  const before = `# adopter\n# ${END_TOKEN}\n`;
  fs.writeFileSync(path.join(dir, '.gitignore'), before);

  const { text, exitCode } = await invoke(dir, ['--existing']);
  assert.match(text, /^ {2}\[refused] \.gitignore \(malformed managed block: .+\)$/m, text);
  assert.equal(fs.readFileSync(path.join(dir, '.gitignore'), 'utf-8'), before, 'a malformed .gitignore was written to');
  // the refusal is reported, not escalated to a hard failure that blocks everything else this run
  // would otherwise do (CLAUDE.md still lands, permission-floor reconcile still runs)
  assert.notEqual(exitCode, 1, text);
  assert.ok(fs.existsSync(path.join(dir, 'CLAUDE.md')), 'the rest of the scaffold did not land');
});
