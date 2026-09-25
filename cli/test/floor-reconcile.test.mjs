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
  planRetirements, applyRetirements, parseFloorRootShape, parseFloorPinnedShape, planReconcile,
  writeTargetAtomically, renderPlan, classifyPin, ADDITIVE_CLASSES,
} from '../src/floorReconcile.mjs';
import { RefusalError } from '../src/util.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MAP = loadMap(path.join(CLI_DIR, 'permission-floor.json'));
const PINS = JSON.parse(fs.readFileSync(path.join(CLI_DIR, 'package.json'), 'utf-8')).foundry;
const HOME = '/home/testuser';

const byTier = (t) => MAP.entries.filter((e) => e.tier === t).map((e) => e.rule);
// feat-foundry-installer-unpinning (AC-IUP-3): buildSettings no longer composes a `ref` — the
// registration (the INDEX) is tagless; the artifact stays pinned via the untouched
// `plugins[].source.sha`, denied to this atom. Kept in agreement with buildSettings's own output
// (never a second, independently-typed literal) so a regression in either drifts the other, not
// silently only one of them.
const PINNED = {
  [PINS.marketplace_name]: {
    source: { source: 'github', repo: PINS.marketplace_repo },
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

// ── feat-foundry-installer-unpinning (AC-IUP-5/AC-IUP-8) ──────────────────────────────────────

test('no_ref_autoupdate_false_entry_classifies_pinned_and_grants_allow', () => {
  // AC-IUP-5: both installers now register the marketplace TAGLESS by default (AC-IUP-1/AC-IUP-3)
  // — a no-ref, autoUpdate:false entry must classify PINNED and must NOT withhold the allow tier.
  const tagless = { [PINS.marketplace_name]: { source: { source: 'github', repo: PINS.marketplace_repo }, autoUpdate: false } };
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless });
  const { t, plan: p } = plan(root);
  assert.equal(p.pin.state, 'pinned');
  assert.equal(p.pin.ref, null);
  assert.equal(p.pin.skew, false, 'AC-IUP-8: a tagless entry must never report skew');
  assert.equal(p.withheldAllow, false);
  assert.equal(p.additions.allow.length, byTier('allow').length, 'the allow tier must be granted');
  commit(t, p);
  assert.equal(read(root).permissions.allow.length, byTier('allow').length);
});

test('no_ref_no_autoupdate_key_at_all_still_classifies_pinned', () => {
  // AC-IUP-4's Clarifications: `claude plugin marketplace add` has no --autoUpdate flag and a real
  // isolated run wrote NO autoUpdate key at all -- the shell installer's registration is exactly
  // this shape. The predicate must read an ABSENT key identically to an explicit `false`.
  const shellShaped = { [PINS.marketplace_name]: { source: { source: 'github', repo: PINS.marketplace_repo } } };
  assert.equal(classifyPin({ extraKnownMarketplaces: shellShaped }, PINS).state, 'pinned');
});

test('tagless_pinned_entry_renders_no_null_and_no_skew_warning', () => {
  // AC-IUP-8: renderPlan must never print "pinned at null", and the VERSION SKEW clause must never
  // fire for a tagless entry (skew is structurally false for it — see classifyPin above).
  const tagless = { [PINS.marketplace_name]: { source: { source: 'github', repo: PINS.marketplace_repo }, autoUpdate: false } };
  const root = target({ permissions: { allow: [], ask: [], deny: [] }, extraKnownMarketplaces: tagless });
  const { plan: p } = plan(root);
  const lines = renderPlan(p, { applied: false });
  const pinLine = lines.find((l) => l.includes('marketplace pinned at'));
  assert.ok(pinLine, 'expected a "marketplace pinned at ..." line');
  assert.doesNotMatch(pinLine, /pinned at null/);
  assert.doesNotMatch(pinLine, /VERSION SKEW/);
});

test('ref_present_but_wildcarded_still_unpinned', () => {
  // regression guard: AC-IUP-5 widens the predicate for an ABSENT ref only. An explicit but
  // malformed/wildcarded ref must still classify unpinned, exactly as before this atom.
  const wildcarded = { [PINS.marketplace_name]: { source: { source: 'github', repo: PINS.marketplace_repo, ref: '*' }, autoUpdate: false } };
  assert.equal(classifyPin({ extraKnownMarketplaces: wildcarded }, PINS).state, 'unpinned');
});

// ── PR #132 security review: widening "no ref" must not widen to "any entry" ───────────────────
// Each case below classified `unpinned` (and so hit the withheld-allow brake) BEFORE this atom.
// The tagless widening must not silently promote them to `pinned` — that would turn a warning
// into a reassurance, which is the specific regression the review caught.
const pinOf = (entry) => classifyPin({ extraKnownMarketplaces: { [PINS.marketplace_name]: entry } }, PINS).state;

test('a_tagless_entry_naming_a_foreign_repo_is_not_pinned', () => {
  assert.equal(pinOf({ source: { source: 'github', repo: 'attacker/lookalike' } }), 'unpinned');
  // and the genuine one still is, so the check is not simply refusing everything
  assert.equal(pinOf({ source: { source: 'github', repo: PINS.marketplace_repo } }), 'pinned');
});

test('a_structurally_malformed_entry_is_not_pinned', () => {
  for (const bad of [{}, 'x', [], null, 42, { source: 'github' }, { source: null }]) {
    assert.equal(pinOf(bad), 'unpinned', `malformed entry classified pinned: ${JSON.stringify(bad)}`);
  }
});

test('a_non_github_source_is_not_pinned', () => {
  assert.equal(pinOf({ source: { source: 'git', repo: PINS.marketplace_repo } }), 'unpinned');
  assert.equal(pinOf({ source: { source: 'url', repo: PINS.marketplace_repo } }), 'unpinned');
});

test('a_truthy_non_true_autoUpdate_is_not_pinned', () => {
  const ours = { source: { source: 'github', repo: PINS.marketplace_repo } };
  for (const au of [1, 'true', {}, []]) {
    assert.equal(pinOf({ ...ours, autoUpdate: au }), 'unpinned', `autoUpdate ${JSON.stringify(au)} classified pinned`);
  }
  // absent and explicit-false remain pinned (AC-IUP-4/AC-IUP-5)
  assert.equal(pinOf(ours), 'pinned');
  assert.equal(pinOf({ ...ours, autoUpdate: false }), 'pinned');
});

// ── PR #132 final review R1: the reason field and its render branch were UNASSERTED ────────────
// The whole point of carrying `reason` is that the foreign-source refusal must NOT advise
// "Pin the marketplace, then re-run" -- pinning can never clear it. Nothing read the field, so a
// refactor dropping it would silently restore the un-actionable message with the suite green.
test('classifyPin_reports_why_it_refused', () => {
  const ours = { source: { source: 'github', repo: PINS.marketplace_repo } };
  assert.equal(classifyPin({ extraKnownMarketplaces: { [PINS.marketplace_name]: ours } }, PINS).reason, null);
  const reasonOf = (e) => classifyPin({ extraKnownMarketplaces: { [PINS.marketplace_name]: e } }, PINS).reason;
  assert.equal(reasonOf({ source: { source: 'github', repo: 'attacker/lookalike' } }), 'source');
  assert.equal(reasonOf({}), 'source');
  assert.equal(reasonOf({ source: { source: 'git', repo: PINS.marketplace_repo } }), 'source');
  assert.equal(reasonOf({ ...ours, autoUpdate: 1 }), 'autoUpdate');
  assert.equal(reasonOf({ source: { source: 'github', repo: PINS.marketplace_repo, ref: '' } }), 'ref');
});

test('a_foreign_source_renders_advice_that_pinning_cannot_clear', () => {
  // drive the real pipeline, exactly as plan() does, with a FOREIGN marketplace source
  const root = target({
    permissions: { allow: [], ask: [], deny: [] },
    extraKnownMarketplaces: { [PINS.marketplace_name]: { source: { source: 'github', repo: 'attacker/lookalike' } } },
  });
  const { plan: p } = plan(root);
  const out = renderPlan(p, { applied: false }).join('\n');
  assert.match(out, /does not name this marketplace's github source/);
  assert.match(out, /Pinning will NOT clear this/);
  assert.doesNotMatch(out, /Pin the marketplace, then re-run/);
});

test('the_same_repo_in_different_case_is_still_ours', () => {
  const upper = PINS.marketplace_repo.replace(/^./, (c) => c.toUpperCase());
  assert.notEqual(upper, PINS.marketplace_repo, 'the case variant must actually differ');
  const state = classifyPin(
    { extraKnownMarketplaces: { [PINS.marketplace_name]: { source: { source: 'github', repo: upper } } } }, PINS).state;
  assert.equal(state, 'pinned', 'GitHub owner/repo is case-insensitive -- the same repo must not read as foreign');
});

test('a_unicode_confusable_repo_does_not_fold_into_ours', () => {
  // U+212A KELVIN SIGN lowercases to ASCII 'k'. Without an ASCII shape gate this folds equal to
  // lukasrepublic/... and reads as pinned. No real GitHub name can contain it, so the damage is a
  // false reassurance rather than a live path -- but it is a class the shape gate closes outright.
  const confusable = PINS.marketplace_repo.replace('k', 'K');
  assert.notEqual(confusable, PINS.marketplace_repo, 'the fixture must actually differ');
  assert.equal(confusable.toLowerCase(), PINS.marketplace_repo.toLowerCase(), 'and must fold equal');
  const pin = classifyPin(
    { extraKnownMarketplaces: { [PINS.marketplace_name]: { source: { source: 'github', repo: confusable } } } }, PINS);
  assert.equal(pin.state, 'unpinned');
  assert.equal(pin.reason, 'source');
});

test('version_skew_fires_for_a_well_formed_ref_naming_another_version', () => {
  // skew was asserted only NEGATIVELY -- replacing it with a constant false passed the whole suite,
  // the same unasserted-field defect just fixed for `reason`.
  const ours = (ref) => ({ source: { source: 'github', repo: PINS.marketplace_repo, ref }, autoUpdate: false });
  const at = (ref) => classifyPin({ extraKnownMarketplaces: { [PINS.marketplace_name]: ours(ref) } }, PINS);
  const stale = at('v0.9.0');
  assert.equal(stale.state, 'pinned');
  assert.equal(stale.skew, true, 'a ref naming another version must be reported as skewed');
  const current = at(`v${PINS.plugin_version}`);
  assert.equal(current.state, 'pinned');
  assert.equal(current.skew, false, 'the matching ref must not be reported as skewed');
});

test('an_empty_pin_block_repo_cannot_match_an_empty_entry_repo', () => {
  const brokenPins = { ...PINS, marketplace_repo: '' };
  const entry = { source: { source: 'github', repo: '' } };
  const pin = classifyPin({ extraKnownMarketplaces: { [PINS.marketplace_name]: entry } }, brokenPins);
  assert.equal(pin.state, 'unpinned', 'a broken pin block must fail closed, never match by degeneracy');
});

// ── floor-retires-rows (AC-FRR-1/-2/-4, ER #199) ─────────────────────────────────────────────────
// The reconcile above only ever ADDED: the four fleet scripts v1.15.0 deleted (#196) kept `allow`
// rules in every upgraded workspace forever, and nothing reported it. These drive the retirement
// side directly (parseFloorRootShape/planRetirements/applyRetirements), the same pipeline shape
// `plan()`/`commit()` above drive for additions.

const RETIRED_ROW = `Bash(${MAP.plugin_root_glob}/scripts/foundry-fleet-doctor.py:*)`;
const RETIRED_ROW_WITH_SUB = `Bash(${MAP.plugin_root_glob}/scripts/foundry-fleet-roster.py list:*)`;

test('parse_floor_root_shape_extracts_name_and_sub_or_returns_null', () => {
  assert.deepEqual(parseFloorRootShape(RETIRED_ROW, MAP.plugin_root_glob),
    { name: 'foundry-fleet-doctor.py', sub: null });
  assert.deepEqual(parseFloorRootShape(RETIRED_ROW_WITH_SUB, MAP.plugin_root_glob),
    { name: 'foundry-fleet-roster.py', sub: 'list' });
  // a real shipped row with a subcommand parses the same way the shape it was written in demands
  const shippedWithSub = byTier('allow').find((r) => r.includes('foundry_repo_fleet.py status'));
  assert.deepEqual(parseFloorRootShape(shippedWithSub, MAP.plugin_root_glob),
    { name: 'foundry_repo_fleet.py', sub: 'status' });
  // AC-FRR-2: any OTHER shape — no plugin-root glob, a different prefix, the map's own bare
  // (non-`:*`) doctor exception — is never a retirement candidate
  assert.equal(parseFloorRootShape('Bash(scripts/foundry-fleet-doctor.py:*)', MAP.plugin_root_glob), null);
  assert.equal(parseFloorRootShape('Bash(make build:*)', MAP.plugin_root_glob), null);
  const bareDoctor = byTier('allow').find((r) => r.includes('foundry-doctor.py)'));
  assert.equal(parseFloorRootShape(bareDoctor, MAP.plugin_root_glob), null);
});

test('retirement_removes_a_row_the_shipped_map_no_longer_declares', () => {
  const root = target({
    permissions: { allow: [RETIRED_ROW, ...byTier('allow')], ask: byTier('ask'), deny: byTier('deny') },
    extraKnownMarketplaces: PINNED,
  });
  const settingsObj = readTarget(resolveTarget(root).path);
  const r = planRetirements({ settingsObj, map: MAP });
  assert.deepEqual(r.retirements.allow, [RETIRED_ROW]);
  assert.deepEqual(r.retirements.ask, []);
  assert.equal(r.total, 1);

  const after = applyRetirements(settingsObj, r);
  assert.ok(!after.permissions.allow.includes(RETIRED_ROW), 'the retired row survived');
  // every rule the map still declares survives, untouched
  for (const rule of byTier('allow')) assert.ok(after.permissions.allow.includes(rule));
  assert.deepEqual(after.permissions.ask, settingsObj.permissions.ask);
  assert.deepEqual(after.permissions.deny, settingsObj.permissions.deny);
});

test('retirement_leaves_an_adopter_row_of_another_shape_alone', () => {
  const adopterRow = 'Bash(scripts/foundry-fleet-doctor.py:*)'; // names the same script, no plugin-root glob
  const root = target({
    permissions: { allow: [adopterRow, ...byTier('allow')], ask: byTier('ask'), deny: byTier('deny') },
    extraKnownMarketplaces: PINNED,
  });
  const settingsObj = readTarget(resolveTarget(root).path);
  const r = planRetirements({ settingsObj, map: MAP });
  assert.equal(r.total, 0, 'an adopter-authored row of another shape was queued for retirement');
  const after = applyRetirements(settingsObj, r);
  assert.ok(after.permissions.allow.includes(adopterRow), 'the adopter row was removed');
});

test('retirement_never_touches_deny', () => {
  const retiredDenyShaped = `Bash(${MAP.plugin_root_glob}/scripts/foundry-fleet-doctor.py:*)`;
  const root = target({
    permissions: { allow: byTier('allow'), ask: byTier('ask'), deny: [retiredDenyShaped, ...byTier('deny')] },
    extraKnownMarketplaces: PINNED,
  });
  const settingsObj = readTarget(resolveTarget(root).path);
  const r = planRetirements({ settingsObj, map: MAP });
  assert.equal(r.total, 0, 'a deny-tier row of the retired shape was queued for removal');
  const after = applyRetirements(settingsObj, r);
  assert.deepEqual(after.permissions.deny, settingsObj.permissions.deny);
});

test('a_present_map_name_with_a_different_sub_still_retires_the_stale_sub', () => {
  // AC-FRR-1: the check is on the (name, sub) PAIR, not the name alone — retiring `status` must not
  // depend on whether `validate` (a different sub of the SAME script) is still declared.
  const stillDeclaredName = byTier('allow').find((r) => r.includes('foundry_repo_fleet.py status'));
  const staleSub = stillDeclaredName.replace(' status:*', ' no-longer-shipped-sub:*');
  const root = target({
    permissions: { allow: [staleSub, ...byTier('allow')], ask: byTier('ask'), deny: byTier('deny') },
    extraKnownMarketplaces: PINNED,
  });
  const settingsObj = readTarget(resolveTarget(root).path);
  const r = planRetirements({ settingsObj, map: MAP });
  assert.deepEqual(r.retirements.allow, [staleSub]);
  const after = applyRetirements(settingsObj, r);
  assert.ok(after.permissions.allow.includes(stillDeclaredName), 'the still-shipped sub was removed too');
});

test('reconcile_pipeline_reports_retired_rows_and_the_composite_summary_line', () => {
  const root = target({
    permissions: { allow: [RETIRED_ROW, ...byTier('allow')], ask: byTier('ask'), deny: byTier('deny') },
    extraKnownMarketplaces: PINNED,
  });
  const settingsObj = readTarget(resolveTarget(root).path);
  // planReconcile — the single entry point either call site uses (review round 1): never
  // planAdditions/planRetirements invoked separately against the same raw settingsObj from here on.
  const { additionsPlan, retirementPlan } = planReconcile({
    settingsObj, map: MAP, pins: PINS, pluginRootExpansion: ['x'], unreadableOrigins: [], home: HOME,
  });
  const lines = renderPlan(additionsPlan, {
    applied: true, retirementPlan, mapEntryCount: MAP.entries.length,
  });
  assert.ok(lines.some((l) => l === `  [retired] ${RETIRED_ROW}`), 'the retired row was not printed');
  assert.ok(
    lines.some((l) => l.includes(`0 added, ${retirementPlan.total} retired, ${MAP.entries.length} unchanged`)),
    `summary line missing the composite counts: ${lines.join('\n')}`,
  );
});

// ── review round 1 (PR #201): additions MUST be planned against the POST-retirement rule set ────

test('a_bare_row_split_into_two_subs_by_a_map_restructure_retires_and_adds_in_ONE_pass', () => {
  // The defect the pre-fix ordering had: covers() is a PREFIX fold, so a bare `<name>:*` row a
  // workspace still carries reads as ALREADY COVERING both split entries a map restructure might
  // introduce — planning additions against the PRE-retirement settings would therefore queue
  // NEITHER split row in the SAME pass that retires the bare one, losing the grant for one cycle.
  const glob = MAP.plugin_root_glob;
  const bareRow = `Bash(${glob}/scripts/foo.py:*)`;
  const splitA = `Bash(${glob}/scripts/foo.py --a:*)`;
  const splitB = `Bash(${glob}/scripts/foo.py --b:*)`;
  // A minimal, self-contained map (never the real 71-entry one) so the ONLY thing this fixture
  // exercises is the bare-to-split restructure, not any other entry's absence/presence.
  const restructuredMap = {
    schema_version: 1,
    plugin_root_glob: glob,
    entries: [
      { rule: splitA, tier: 'allow', rationale: 'split a' },
      { rule: splitB, tier: 'allow', rationale: 'split b' },
    ],
  };
  const root = target({
    permissions: { allow: [bareRow], ask: [], deny: [] },
    extraKnownMarketplaces: PINNED,
  });
  const settingsObj = readTarget(resolveTarget(root).path);

  // (a) the OLD, unsafe order — additions planned against the RAW (pre-retirement) settingsObj —
  // reproduces the loss: neither split row is queued, because the bare row still "covers" both.
  const unsafeRetirement = planRetirements({ settingsObj, map: restructuredMap });
  const unsafeFindings = classifyDrift(restructuredMap, readTrackedRules(settingsObj), {
    pluginRootExpansion: ['x'], unreadableOrigins: [], home: HOME,
  });
  const unsafeAdditions = planAdditions({
    findings: unsafeFindings, map: restructuredMap, settingsObj, pins: PINS,
  });
  assert.deepEqual(unsafeRetirement.retirements.allow, [bareRow]);
  assert.equal(unsafeAdditions.total, 0,
    'sanity check: the OLD ordering must reproduce the one-cycle loss, or this test proves nothing');

  // (b) planReconcile — the FIX — retires the bare row AND adds both split rows in the SAME pass.
  const { additionsPlan, retirementPlan } = planReconcile({
    settingsObj, map: restructuredMap, pins: PINS,
    pluginRootExpansion: ['x'], unreadableOrigins: [], home: HOME,
  });
  assert.deepEqual(retirementPlan.retirements.allow, [bareRow]);
  assert.deepEqual(additionsPlan.additions.allow.slice().sort(), [splitA, splitB].sort());

  const written = applyAdditions(additionsPlan.settingsObj, additionsPlan, { map: restructuredMap, pins: PINS });
  assert.ok(!written.permissions.allow.includes(bareRow), 'the bare row survived');
  assert.ok(written.permissions.allow.includes(splitA), 'split row a was never added');
  assert.ok(written.permissions.allow.includes(splitB), 'split row b was never added');
});

test('full_pipeline_idempotence_through_the_real_entry_point_second_run_is_silent', () => {
  // Additions AND retirements composed through planReconcile, driven twice over the SAME on-disk
  // target the way run.mjs actually drives it (plan -> commit -> re-plan): the second pass must
  // report zero of each and must not touch the file at all.
  const root = target({
    permissions: { allow: [RETIRED_ROW, ...byTier('allow')], ask: byTier('ask'), deny: byTier('deny') },
    extraKnownMarketplaces: PINNED,
  });
  const t = resolveTarget(root);

  const first = planReconcile({
    settingsObj: readTarget(t.path), map: MAP, pins: PINS,
    pluginRootExpansion: ['x'], unreadableOrigins: [], home: HOME,
  });
  assert.equal(first.retirementPlan.total, 1, 'the fixture did not actually seed a retirement candidate');
  writeTargetAtomically(t.path, applyAdditions(first.additionsPlan.settingsObj, first.additionsPlan, { map: MAP, pins: PINS }));

  const bytesAfterFirst = fs.readFileSync(t.path);
  const inoAfterFirst = fs.statSync(t.path).ino;

  const second = planReconcile({
    settingsObj: readTarget(t.path), map: MAP, pins: PINS,
    pluginRootExpansion: ['x'], unreadableOrigins: [], home: HOME,
  });
  assert.equal(second.additionsPlan.total, 0, 'a second run over its own output queued an addition');
  assert.equal(second.retirementPlan.total, 0, 'a second run over its own output queued a retirement');

  // never actually written — the second plan is inert, but assert the FILE too, not only the plan,
  // exactly as the pre-existing additive-only idempotence test does
  assert.deepEqual(fs.readFileSync(t.path), bytesAfterFirst, 'a no-op second plan changed the bytes');
  assert.equal(fs.statSync(t.path).ino, inoAfterFirst, 'a no-op second plan rewrote the file');
});


// hotfix-v1.17.3: version-/marketplace-pinned variants of the floor's own rows are retired.
test('retirement_takes_back_version_pinned_variants_of_the_floors_own_rows', () => {
  const map = { plugin_root_glob: '~/.claude/plugins/cache/*/foundry/*', entries: [
    { rule: 'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-doctor.py:*)', tier: 'allow' },
  ] };
  const settingsObj = { permissions: { allow: [
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-doctor.py:*)',   // shipped script, stale pin
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-gone.py:*)',     // gone script, stale pin
    'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-doctor.py:*)',                      // the floor's own row: kept
    'Bash(/opt/foundry/scripts/foundry-doctor.py:*)',                                            // adopter shape: never touched
  ], ask: [], deny: [
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-doctor.py:*)',   // deny is never a candidate
  ] } };
  const plan = planRetirements({ settingsObj, map });
  assert.deepEqual(plan.retirements.allow, [
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-doctor.py:*)',
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-gone.py:*)',
  ]);
  assert.equal(plan.total, 2);
  const next = applyRetirements(settingsObj, plan);
  assert.deepEqual(next.permissions.allow, [
    'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-doctor.py:*)',
    'Bash(/opt/foundry/scripts/foundry-doctor.py:*)',
  ]);
  assert.deepEqual(next.permissions.deny, settingsObj.permissions.deny);
});


test('a_pinned_ask_row_is_retired_only_when_the_map_declares_the_same_pair_at_ask', () => {
  const map = { plugin_root_glob: '~/.claude/plugins/cache/*/foundry/*', entries: [
    { rule: 'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-x.py:*)', tier: 'allow' },
    { rule: 'Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-y.py push:*)', tier: 'ask' },
  ] };
  const settingsObj = { permissions: { allow: [], ask: [
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.17.3/scripts/foundry-x.py push:*)', // operator prompt under a broader allow: KEPT
    'Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-y.py push:*)',  // the map's own ask pair, pinned: retired
  ], deny: [] } };
  const plan = planRetirements({ settingsObj, map });
  assert.deepEqual(plan.retirements.ask, ['Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.9.1/scripts/foundry-y.py push:*)']);
  assert.deepEqual(plan.retirements.allow, []);
});

test('pinned_shape_rejects_dotted_marketplace_segments_partial_globs_and_non_semver_versions', () => {
  const g = '~/.claude/plugins/cache/*/foundry/*';
  assert.equal(parseFloorPinnedShape('Bash(~/.claude/plugins/cache/../foundry/1.9.1/scripts/x.py:*)', g), null);
  assert.equal(parseFloorPinnedShape('Bash(~/.claude/plugins/cache/agentic-foundry/foundry/1.*/scripts/x.py:*)', g), null);
  assert.equal(parseFloorPinnedShape('Bash(~/.claude/plugins/cache/agentic-foundry/foundry/latest/scripts/x.py:*)', g), null);
  assert.deepEqual(parseFloorPinnedShape('Bash(~/.claude/plugins/cache/*/foundry/1.9.1/scripts/x.py:*)', g), { name: 'x.py', sub: null, pinned: true });
});
