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
import { BEGIN_TOKEN, END_TOKEN, loadDesiredInterior } from '../src/gitignoreReconcile.mjs';

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

const reEsc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const DENY = MAP.entries.filter((e) => e.tier === 'deny').map((e) => e.rule);
const ALLOW = MAP.entries.filter((e) => e.tier === 'allow').map((e) => e.rule);
const ASK = MAP.entries.filter((e) => e.tier === 'ask').map((e) => e.rule);
const SELF_GUARD_PAIR = ['Edit(.foundry/permissions.yaml)', 'Write(.foundry/permissions.yaml)'];
const OPERATOR = { allow: ['Bash(/opt/mine/tool:*)', 'Bash(mine:*)'], ask: ['Bash(mine-ask:*)'], deny: ['Bash(rm -rf:*)'] };
// v1.18.0 (AC-V118A-2): the drift report still classifies the map's allow/ask registry rows, which
// the floor no longer writes — see the two `todo` tests below (a SOURCE defect, reported, not patched).
const SOURCE_BUG_REGISTRY_ABSENT = 'SOURCE DEFECT (v1.18.0): run.mjs\'s advisory report classifies the map\'s allow/ask registry rows, which buildSettings no longer writes, so every run lists them all as allow-absent/ask-absent';

test('advisory_report_never_names_a_deny_rule_the_same_run_just_added', async () => {
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  // pinned (nothing withheld), no deny rows yet: the run's whole additive job is the deny tier
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({ extraKnownMarketplaces: PIN, permissions: { ...OPERATOR } }, null, 2)}\n`,
  );

  const { text } = await invoke(dir, ['--existing']);

  const added = text.match(/permission-floor reconcile \(\.claude\/settings\.json\): added allow=(\d+), ask=(\d+), deny=(\d+)/);
  assert.ok(added, `the run did not report a completed reconcile:\n${text}`);
  assert.equal(Number(added[1]), 0, 'the floor added an allow row');
  assert.equal(Number(added[2]), 0, 'the floor added an ask row');
  assert.equal(Number(added[3]), DENY.length);
  for (const r of DENY) assert.ok(text.includes(`  [added] .claude/settings.json deny: ${r}`), `${r} not named with file and tier`);

  const r = reported(text);
  assert.deepEqual(r['deny-missing'] ?? [], [],
    'the report names deny rules this same run added — it contradicts the write above it');

  const written = JSON.parse(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'));
  assert.deepEqual(written.permissions.deny, [...OPERATOR.deny, ...DENY]);
  assert.deepEqual(written.permissions.allow, OPERATOR.allow, 'operator allow rows changed or a floor row was added');
  assert.deepEqual(written.permissions.ask, OPERATOR.ask, 'operator ask rows changed or a floor row was added');
});

test('advisory_report_never_names_a_registry_row_the_floor_does_not_write', async () => {
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({ extraKnownMarketplaces: PIN, permissions: { allow: [], ask: [], deny: [] } }, null, 2)}\n`,
  );
  const { text } = await invoke(dir, ['--existing']);
  const r = reported(text);
  assert.deepEqual(r['allow-absent'] ?? [], [], 'the report asks for allow rows the floor deliberately never writes');
  assert.deepEqual(r['ask-absent'] ?? [], [], 'the report asks for ask rows the floor deliberately never writes');
});

test('create_path_writes_only_the_deny_floor_and_its_report_reflects_it', async () => {
  // applyPlan writes the floor for a workspace with no settings.json; a report classified
  // beforehand called every one of those rules absent.
  const dir = path.join(scratch(), 'fresh');
  const { text } = await invoke(dir);

  const p = path.join(dir, '.claude', 'settings.json');
  assert.ok(fs.existsSync(p), 'create path wrote no settings');
  const written = JSON.parse(fs.readFileSync(p, 'utf-8'));
  assert.deepEqual(written.permissions, { allow: [], ask: [], deny: DENY });
  for (const x of SELF_GUARD_PAIR) assert.ok(!written.permissions.deny.includes(x), `self-guard ${x} written on create`);
  const r = reported(text);
  assert.deepEqual(r['deny-missing'] ?? [], [], 'report calls the deny floor it just created missing');
});

test('create_path_report_names_no_registry_row_absent', async () => {
  const dir = path.join(scratch(), 'fresh');
  const { text } = await invoke(dir);
  const r = reported(text);
  assert.deepEqual(r['allow-absent'] ?? [], [], 'report calls the floor it just created absent');
  assert.deepEqual(r['ask-absent'] ?? [], [], 'report calls the floor it just created absent');
  // and the preview must not claim allow/ask rules are being declared
  assert.doesNotMatch(text, /\[allow\] \([1-9]\d* rules\)/);
  assert.doesNotMatch(text, /\[ask\] \([1-9]\d* rules\)/);
});

test('dry_run_still_reports_the_pre_write_state_and_writes_nothing', async () => {
  // --dry-run returns above the write, so its plan must still describe what WOULD be added.
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({ extraKnownMarketplaces: PIN, permissions: { allow: [], ask: [], deny: [] } }, null, 2)}\n`,
  );
  const before = fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8');

  const { text } = await invoke(dir, ['--existing', '--dry-run']);

  assert.match(text, new RegExp(`permission-floor reconcile \\(\\.claude/settings\\.json\\): would add allow=0, ask=0, deny=${DENY.length}`));
  assert.ok(text.includes(`  [would add] .claude/settings.json deny: ${DENY[0]}`));
  assert.doesNotMatch(text, /permission-floor reconcile \(\.claude\/settings\.json\): added /);
  assert.equal(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'), before,
    'dry run mutated the target');
});

// ============================================================================================ //
// floor-retires-rows (AC-FRR-1/-5, ER #199; v1.18.0 AC-V118A-2/-4) — the --existing reconcile
// drives the real runCli path
// ============================================================================================ //

function legacyWorkspace(dir) {
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  const staleRow = `Bash(${MAP.plugin_root_glob}/scripts/foundry-fleet-doctor.py:*)`;
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({
      extraKnownMarketplaces: PIN,
      permissions: {
        allow: [staleRow, ...ALLOW, ...OPERATOR.allow],
        ask: [...ASK, 'Bash(claude plugin tag:*)', ...OPERATOR.ask],
        deny: [...DENY, 'Bash(git push --force:*)', ...SELF_GUARD_PAIR, ...OPERATOR.deny],
      },
    }, null, 2)}\n`,
  );
  return staleRow;
}

test('existing_reconcile_retires_every_floor_script_row_the_retired_literals_and_the_self_guard_pair_end_to_end', async () => {
  const dir = scratch();
  const staleRow = legacyWorkspace(dir);

  const { text } = await invoke(dir, ['--existing']);
  assert.match(text, new RegExp(`\\[retired\\] \\.claude/settings\\.json allow: ${reEsc(staleRow)}`));
  assert.match(text, new RegExp(`\\[retired\\] \\.claude/settings\\.json ask: ${reEsc(ASK[0])}`));
  assert.match(text, /\[retired\] \.claude\/settings\.json ask: Bash\(claude plugin tag:\*\)/);
  assert.match(text, /\[retired\] \.claude\/settings\.json deny: Bash\(git push --force:\*\)/);
  assert.match(text, /\[retired\] \.claude\/settings\.json deny: Edit\(\.foundry\/permissions\.yaml\)/);
  assert.match(text, new RegExp(
    `permission-floor reconcile \\(\\.claude/settings\\.json\\): added allow=0, ask=0, deny=0; retired allow=\\d+, ask=${ASK.length + 1}, deny=3`));

  const written = JSON.parse(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'));
  assert.ok(!written.permissions.allow.includes(staleRow), 'the stale row survived the real reconcile path');
  // what survives: exactly the operator's rows, plus the map's deny tier (and the bare doctor row —
  // see the report: a non-`:*` floor row is not recognized by the retirement parser)
  assert.deepEqual(written.permissions.allow.filter((x) => !ALLOW.includes(x)), OPERATOR.allow);
  assert.deepEqual(written.permissions.ask, OPERATOR.ask);
  assert.deepEqual(written.permissions.deny, [...DENY, ...OPERATOR.deny]);
});

test('existing_reconcile_leaves_no_floor_row_in_the_allow_tier', async () => {
  const dir = scratch();
  legacyWorkspace(dir);
  await invoke(dir, ['--existing']);
  const written = JSON.parse(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'));
  assert.deepEqual(written.permissions.allow, OPERATOR.allow);
});

test('dry_run_reports_retirement_and_writes_nothing', async () => {
  const dir = scratch();
  const staleRow = legacyWorkspace(dir);
  const before = fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8');

  const { text } = await invoke(dir, ['--existing', '--dry-run']);
  assert.match(text, new RegExp(`\\[would retire\\] \\.claude/settings\\.json allow: ${reEsc(staleRow)}`));
  assert.match(text, /\[would retire\] \.claude\/settings\.json deny: Write\(\.foundry\/permissions\.yaml\)/);
  assert.equal(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf-8'), before,
    'dry run retired a row on disk');
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
  // PR #179 review: the EXISTING (annotated) sentinel lines are preserved verbatim — only the
  // interior converges. Rewriting the sentinel line itself would have made this run and a later
  // bash-applier run flip it back and forth forever.
  const desiredInterior = loadDesiredInterior(path.join(CLI_DIR, 'templates'));
  assert.equal(body[1], STALE_GITIGNORE_BLOCK[0], 'the existing annotated BEGIN line was rewritten');
  assert.deepEqual(body.slice(2, 2 + desiredInterior.length), desiredInterior,
    'the interior did not converge onto the template');
  assert.equal(body[2 + desiredInterior.length], STALE_GITIGNORE_BLOCK[STALE_GITIGNORE_BLOCK.length - 1],
    'the existing annotated END line was rewritten');
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

test('a_gitignore_only_convergence_still_reports_the_already_trusted_hand_off', async () => {
  const dir = scratch();
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  // a COMPLETE floor (every tier, fully pinned) — floorPlan.total will be 0, so if
  // `reconciledExisting` were still gated on the floor alone, this run would wrongly get the
  // standard (not-yet-trusted) hand-off despite having just written to .gitignore.
  fs.writeFileSync(
    path.join(dir, '.claude', 'settings.json'),
    `${JSON.stringify({
      extraKnownMarketplaces: PIN,
      permissions: {
        allow: MAP.entries.filter((e) => e.tier === 'allow').map((e) => e.rule),
        ask: MAP.entries.filter((e) => e.tier === 'ask').map((e) => e.rule),
        deny: MAP.entries.filter((e) => e.tier === 'deny').map((e) => e.rule),
      },
    }, null, 2)}\n`,
  );
  fs.writeFileSync(
    path.join(dir, '.gitignore'),
    ['# adopter line above', ...STALE_GITIGNORE_BLOCK, 'dist/'].map((l) => `${l}\n`).join(''),
  );

  const { text } = await invoke(dir, ['--existing']);
  assert.doesNotMatch(text, /permission-floor reconcile: added /, 'the floor fixture was not actually complete');
  assert.match(text, /^ {2}\[converged] \.gitignore \(managed block\)$/m, text);
  assert.match(text, /ALREADY TRUSTED/, 'a gitignore-only write did not get the already-trusted hand-off');
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
