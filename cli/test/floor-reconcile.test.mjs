// floor-reconcile.test.mjs — feat-foundry-adoption-permission-floor-reconcile (AC-PFR-1..14).
//
// The wizard computes the whole answer for an existing workspace and refuses to act on it: the
// floor is a bundled constant, the classifier names every missing rule, and settings.json then goes
// through the whole-file never-clobber plan (exists and differs -> drifted -> untouched). Five of
// seven adopter handbooks are missing the entire floor as a result.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadMap, classifyDrift } from '../src/permissionFloor.mjs';
import {
  resolveTarget, readTarget, readTrackedRules, planAdditions, applyAdditions,
  writeTargetAtomically, renderPlan, classifyPin, ADDITIVE_CLASSES,
} from '../src/floorReconcile.mjs';
import { RefusalError } from '../src/util.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MAP = loadMap(path.join(CLI_DIR, 'permission-floor.json'));
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const HOME = '/home/testuser';

const byTier = (t) => MAP.entries.filter((e) => e.tier === t).map((e) => e.rule);
const PINNED = {
  [PINS.marketplace_name]: {
    source: { source: 'github', repo: PINS.marketplace_repo, ref: `v${PINS.plugin_version}` },
    autoUpdate: false,
  },
};

function target(settings) {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pfr-')));
  fs.mkdirSync(path.join(dir, '.claude'));
  if (settings !== null) {
    fs.writeFileSync(path.join(dir, '.claude', 'settings.json'), JSON.stringify(settings, null, 2) + '\n');
  }
  return dir;
}

/** the whole pipeline as run.mjs drives it, minus the terminal */
function plan(root) {
  const t = resolveTarget(root);
  if (!t.present) return { t, plan: null };
  const settingsObj = readTarget(t.path);
  const findings = classifyDrift(MAP, readTrackedRules(settingsObj), {
    pluginRootExpansion: ['x'], unreadableOrigins: [], home: HOME,
  });
  const p = planAdditions({ findings, map: MAP, settingsObj, pins: PINS });
  p.settingsObj = settingsObj;
  return { t, plan: p };
}
const commit = (t, p) => writeTargetAtomically(t.path, applyAdditions(p.settingsObj, p, { map: MAP, pins: PINS }));
const read = (root) => JSON.parse(fs.readFileSync(path.join(root, '.claude', 'settings.json'), 'utf-8'));

test('adds_every_absent_rule_to_declared_tier', () => {
  const projectRules = ['Bash(make build:*)', 'Bash(terraform plan:*)'];
  const root = target({ permissions: { allow: projectRules, ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const { t, plan: p } = plan(root);
  commit(t, p);
  const after = read(root);
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const rule of byTier(tier)) assert.ok(after.permissions[tier].includes(rule), `${tier}: ${rule} missing`);
  }
  // AC-PFR-3 — the project's own rules survive, in their tier, in their original relative order
  assert.deepEqual(after.permissions.allow.slice(0, projectRules.length), projectRules);
});

test('write_set_is_tracked_file_report_is_union', () => {
  // the union sees a rule carried only in settings.local.json as covered; the TRACKED file must
  // still receive it, or the repo ships to every other clone and to CI without it
  const denyRule = byTier('deny')[0];
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  fs.writeFileSync(path.join(root, '.claude', 'settings.local.json'),
    JSON.stringify({ permissions: { deny: [denyRule] } }));
  const { t, plan: p } = plan(root);
  assert.ok(p.additions.deny.includes(denyRule), 'the locally-satisfied deny was not added to the tracked file');
  commit(t, p);
  assert.ok(read(root).permissions.deny.includes(denyRule));
});

test('tier_conflict_reported_never_added', () => {
  const moved = byTier('allow')[0];
  const root = target({
    permissions: { allow: byTier('allow').slice(1), ask: [moved, ...byTier('ask')], deny: byTier('deny') },
    extraKnownMarketplaces: PINNED,
  });
  const { t, plan: p } = plan(root);
  assert.equal(p.total, 0, 'a deliberately-moved rule was queued for addition');
  commit(t, p);
  const after = read(root);
  const copies = ['allow', 'ask', 'deny'].reduce((n, tier) => n + after.permissions[tier].filter((r) => r === moved).length, 0);
  assert.equal(copies, 1, 'the rule now exists under two tiers');
});

test('preexisting_rules_survive_text_tier_position', () => {
  const mine = { allow: ['Bash(a:*)', 'Bash(b:*)'], ask: ['Bash(c:*)'], deny: ['Bash(d:*)'] };
  const root = target({ permissions: { ...mine }, extraKnownMarketplaces: PINNED });
  const { t, plan: p } = plan(root);
  commit(t, p);
  const after = read(root);
  for (const tier of ['allow', 'ask', 'deny']) {
    assert.deepEqual(after.permissions[tier].slice(0, mine[tier].length), mine[tier], `${tier} order changed`);
  }
});

test('unrelated_top_level_keys_unchanged', () => {
  const before = {
    permissions: { allow: [], ask: [], deny: [] },
    extraKnownMarketplaces: PINNED,
    enabledPlugins: { 'foundry@agentic-foundry': true },
    hooks: { SessionStart: [{ command: 'echo hi' }] },
    someKeyTheCliHasNeverHeardOf: { nested: [1, 2, 3] },
  };
  const root = target(before);
  const { t, plan: p } = plan(root);
  commit(t, p);
  const after = read(root);
  for (const k of ['extraKnownMarketplaces', 'enabledPlugins', 'hooks', 'someKeyTheCliHasNeverHeardOf']) {
    assert.deepEqual(after[k], before[k], `${k} changed`);
  }
});

test('absent_pin_added_matching_create_path', () => {
  // every bundled allow rule is wildcarded across the plugin cache and is bounded ONLY by the pin
  // the create path writes in the same file, in the same write. Six of seven adopter handbooks
  // carry no marketplace entry at all, so granting without it is a standing grant over whatever a
  // future resolution delivers.
  const root = target({ permissions: { allow: [], ask: [], deny: [] } });
  const { t, plan: p } = plan(root);
  assert.equal(p.pin.state, 'absent');
  assert.equal(p.withheldAllow, false, 'an absent pin must be ADDED, not used to withhold');
  commit(t, p);
  const after = read(root);
  assert.deepEqual(after.extraKnownMarketplaces, PINNED);
  assert.equal(after.permissions.allow.length, byTier('allow').length);
});

test('unpinned_marketplace_withholds_allow_tier_only', () => {
  const unpinned = { [PINS.marketplace_name]: { source: { source: 'github', repo: PINS.marketplace_repo }, autoUpdate: true } };
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: unpinned });
  const { t, plan: p } = plan(root);
  assert.equal(p.pin.state, 'unpinned');
  assert.equal(p.additions.allow.length, 0, 'allow rules granted against an unpinned marketplace');
  // ask and deny are STRENGTHENING — more prompting, more blocking — so they are not held hostage
  assert.equal(p.additions.ask.length, byTier('ask').length);
  assert.equal(p.additions.deny.length, byTier('deny').length);
  commit(t, p);
  assert.deepEqual(read(root).extraKnownMarketplaces, unpinned, 'the operator pin was modified');
  assert.ok(renderPlan(p, { applied: true }).some((l) => l.includes('WITHHELD')));
});

test('absent_settings_defers_to_create_path', () => {
  const root = target(null);
  const { t, plan: p } = plan(root);
  assert.equal(t.present, false);
  assert.equal(p, null, 'the reconcile planned a write for a file the create path owns');
});

test('classification_precedes_write_and_dryrun_return', () => {
  // the plan is fully computed with the file untouched — which is what lets --dry-run report each
  // rule it would add, something impossible while the only classifyDrift call sat after applyPlan
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const p0 = fs.readFileSync(path.join(root, '.claude', 'settings.json'));
  const { plan: p } = plan(root);
  assert.equal(p.total, MAP.entries.length);
  assert.deepEqual(fs.readFileSync(path.join(root, '.claude', 'settings.json')), p0, 'planning wrote to the target');
});

test('dry_run_names_each_rule_and_tier', () => {
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const { plan: p } = plan(root);
  const lines = renderPlan(p, { applied: false });
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const rule of byTier(tier)) {
      assert.ok(lines.some((l) => l.includes(`[${tier}] ${rule}`)), `${rule} not named with its tier`);
    }
  }
  assert.ok(lines.some((l) => l.includes('would add')));
});

test('writes_only_when_additions_pending_and_not_dry_run', () => {
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const { t, plan: p } = plan(root);
  commit(t, p);
  const first = fs.readFileSync(t.path);
  const st1 = fs.statSync(t.path);

  // second run: the delta is recomputed and comes back empty — idempotence falls out of the
  // computation, with nothing recorded between runs
  const { plan: p2 } = plan(root);
  assert.equal(p2.total, 0);
  assert.deepEqual(fs.readFileSync(t.path), first, 'the second run changed the bytes');
  assert.equal(fs.statSync(t.path).mtimeMs, st1.mtimeMs, 'the second run rewrote the file');
});

test('in_root_symlink_refused_target_untouched', () => {
  // confinedJoin refuses a resolution that ESCAPES the root but passes an IN-ROOT symlink, and
  // .claude/foundry-operators.json is the file whose key membership alone mints an authorizer.
  // statSync().isFile() returns true through a symlink; only lstat sees it.
  const root = target(null);
  const registry = path.join(root, '.claude', 'foundry-operators.json');
  const registryBody = JSON.stringify({ schema_version: 1, operators: { op_x: { github: 'x' } } }, null, 2);
  fs.writeFileSync(registry, registryBody);
  fs.symlinkSync(registry, path.join(root, '.claude', 'settings.json'));
  assert.throws(() => resolveTarget(root), RefusalError);
  assert.equal(fs.readFileSync(registry, 'utf-8'), registryBody, 'the operator registry was written through the link');
});

test('interrupted_write_leaves_original_intact', () => {
  // truncate-then-write would lose the operator's whole permissions block AND their install pin on
  // a ^C or ENOSPC — silent loss from a benign cause, which is never-clobber's substance rather
  // than its letter.
  const root = target({ permissions: { allow: ['Bash(mine:*)'], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const p0 = fs.readFileSync(path.join(root, '.claude', 'settings.json'));
  const t = resolveTarget(root);

  // (a) THE REPLACE IS A RENAME, not an in-place rewrite — asserted by the observable signature
  // rather than by inspecting the implementation: truncate-then-write keeps the file's inode,
  // replace-by-rename does not. This is the assertion that actually rules out the lossy shape.
  const inoBefore = fs.statSync(t.path).ino;
  writeTargetAtomically(t.path, { permissions: { allow: ['Bash(mine:*)', 'Bash(new:*)'] } });
  assert.notEqual(fs.statSync(t.path).ino, inoBefore, 'the file was rewritten in place, not replaced');
  assert.ok(JSON.parse(fs.readFileSync(t.path, 'utf-8')).permissions.allow.includes('Bash(new:*)'));

  // (b) a serialization failure cannot damage the target — it happens before anything is opened,
  // which is itself the ordering that matters
  const restore = target({ permissions: { allow: ['Bash(mine:*)'], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const t2 = resolveTarget(restore);
  const circular = {}; circular.self = circular;
  assert.throws(() => writeTargetAtomically(t2.path, circular));
  assert.deepEqual(fs.readFileSync(t2.path), p0, 'the original was damaged');
  const strays = fs.readdirSync(path.join(restore, '.claude')).filter((f) => f.includes('.tmp'));
  assert.deepEqual(strays, [], `temp residue left behind: ${strays}`);
});

test('refuses_nonregular_escaping_or_unparseable', () => {
  const bad = target(null);
  fs.writeFileSync(path.join(bad, '.claude', 'settings.json'), '{ not json');
  const t = resolveTarget(bad);
  // an unparseable TRACKED file is a refusal, not a finding: classifying against an empty rule set
  // and then writing into a file whose keys could not be read cannot preserve those keys
  assert.throws(() => readTarget(t.path), RefusalError);

  const dir = target(null);
  fs.rmSync(path.join(dir, '.claude', 'settings.json'), { force: true });
  fs.mkdirSync(path.join(dir, '.claude', 'settings.json'));
  assert.throws(() => resolveTarget(dir), RefusalError);
});

test('reports_per_tier_counts_and_names_blanket_allow', () => {
  const root = target({ permissions: { allow: ['Bash(*)'], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const { plan: p } = plan(root);
  const lines = renderPlan(p, { applied: true });
  assert.ok(lines.some((l) => /allow=\d+, ask=\d+, deny=\d+/.test(l)), 'per-tier counts absent');
  assert.ok(lines.some((l) => l.includes('blanket allow') && l.includes('Bash(*)')),
    'a converged floor was reported without naming the rule that defeats it');
});

test('additive_classes_are_a_closed_allowlist', () => {
  // an allowlist, not a denylist: a class added to the vocabulary later defaults to NOT being
  // written, which is the direction that fails closed
  assert.deepEqual([...ADDITIVE_CLASSES], ['allow-absent', 'ask-absent', 'deny-missing']);
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: PINNED });
  const { plan: p } = plan(root);
  const fake = [{ class: 'tier-conflict', rule: byTier('allow')[0] },
                { class: 'ask-shadowed', rule: byTier('ask')[0] },
                { class: 'unclassified', rule: 'Bash(anything:*)' }];
  const p2 = planAdditions({ findings: fake, map: MAP, settingsObj: p.settingsObj, pins: PINS });
  assert.equal(p2.total, 0);
});

test('classify_pin_reads_only_the_foundry_entry', () => {
  const other = { 'someone-elses-marketplace': { source: { source: 'github', repo: 'x/y' }, autoUpdate: true } };
  assert.equal(classifyPin({ extraKnownMarketplaces: other }, PINS).state, 'absent');
  assert.equal(classifyPin({ extraKnownMarketplaces: PINNED }, PINS).state, 'pinned');
});

test('trust_handoff_tells_the_truth_on_the_reconcile_path', async () => {
  // R8, answered live 2026-08-12: reconciling an ALREADY-TRUSTED workspace and restarting gave NO
  // trust dialog — the 42 allow rules simply took effect. The standard hand-off says the dialog is
  // the consent ceremony and the rules wait for it, which on that path is false and points the
  // operator at a review that will not happen.
  const { TRUST_HANDOFF_TEXT } = await import('../src/preview.mjs');
  const scaffold = TRUST_HANDOFF_TEXT('/tmp/x', { isGitRepo: true });
  const reconciled = TRUST_HANDOFF_TEXT('/tmp/x', { isGitRepo: true, reconciledExisting: true });

  // the standard path still points at the dialog, because there it is genuinely the grant
  assert.match(scaffold, /trust dialog is the consent ceremony/);
  assert.match(scaffold, /take effect only after/);

  // the reconcile path must NOT, and must say where consent actually happened
  assert.doesNotMatch(reconciled, /take effect only after/);
  assert.match(reconciled, /ALREADY TRUSTED/);
  assert.match(reconciled, /no second consent ceremony/);
});
