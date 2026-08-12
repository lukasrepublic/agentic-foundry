// floor-drift.test.mjs — feat-foundry-onboarding-floor-drift-classification (AC-FDC-1..4, -7).
//
// The drift classifier used to check `ask` entries only for being SHADOWED, never for being
// ABSENT: there was no ask-absent class in either implementation. Measured against a real adopter
// workspace, the tools reported 46 findings and were silent about 16 more — every ceremony rule
// gone, with the floor reading clean on that dimension.
//
// The cross-implementation differential (AC-FDC-5/-6) lives on the Python side, where it can drive
// both classifiers over the shared corpus in one process.
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  loadMap,
  classifyDrift,
  covers,
  denyCovers,
  isBlanketAllow,
  DRIFT_CLASSES,
  MapMalformed,
  foldRegexFromGlob,
} from '../src/permissionFloor.mjs';

const CLI_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MAP = loadMap(path.join(CLI_DIR, 'permission-floor.json'));
const HOME = '/home/testuser';
// the fold pattern is derived from the map's own plugin_root_glob, so a caller reaching for the
// coverage relation directly supplies the same one classifyDrift computes internally
const FOLD = foldRegexFromGlob(MAP.plugin_root_glob);

const byTier = (tier) => MAP.entries.filter((e) => e.tier === tier).map((e) => e.rule);
const eff = ({ allow = [], ask = [], deny = [] }, origin = 'settings.json') => ({
  allow: allow.map((rule) => ({ rule, origin, tierKey: 'allow' })),
  ask: ask.map((rule) => ({ rule, origin, tierKey: 'ask' })),
  deny: deny.map((rule) => ({ rule, origin, tierKey: 'deny' })),
});
const classify = (effective) =>
  classifyDrift(MAP, effective, { pluginRootExpansion: ['x'], home: HOME });
const countBy = (findings) =>
  findings.reduce((acc, f) => ({ ...acc, [f.class]: (acc[f.class] || 0) + 1 }), {});

test('empty_effective_set_yields_every_absence', () => {
  const counts = countBy(classify(eff({})));
  // The whole floor, named. Before AC-FDC-1 this was 46 findings with the ask tier silent.
  assert.equal(counts['allow-absent'], byTier('allow').length);
  assert.equal(counts['ask-absent'], byTier('ask').length);
  assert.equal(counts['deny-missing'], byTier('deny').length);
  assert.equal(
    counts['allow-absent'] + counts['ask-absent'] + counts['deny-missing'],
    MAP.entries.length,
  );
});

test('ask_present_yields_no_ask_absent', () => {
  // The negative control. Without it the class could fire unconditionally and still satisfy the
  // case above — the assertion "62 findings" alone cannot tell a working class from a broken one.
  const findings = classify(eff({ ask: byTier('ask') }));
  assert.equal(countBy(findings)['ask-absent'], undefined);
  // and it is genuinely still classifying: the other two tiers are untouched by this target
  assert.equal(countBy(findings)['allow-absent'], byTier('allow').length);
});

test('cross_tier_rule_conflicts_not_absent', () => {
  const moved = byTier('allow')[0];
  const findings = classify(eff({ allow: byTier('allow').slice(1), ask: [moved] }));
  const conflicts = findings.filter((f) => f.class === 'tier-conflict');
  assert.equal(conflicts.length, 1);
  assert.equal(conflicts[0].rule, moved);
  assert.equal(conflicts[0].declaredTier, 'allow');
  assert.equal(conflicts[0].foundTier, 'ask');
  // and the false absence is suppressed — reporting it absent would invite a consumer to add a
  // second copy, leaving one capability declared twice under two tiers
  assert.ok(!findings.some((f) => f.class === 'allow-absent' && f.rule === moved));
});

test('findings_carry_origin_file', () => {
  const moved = byTier('deny')[0];
  const e = eff({ allow: byTier('allow'), ask: byTier('ask') });
  e.allow.push({ rule: moved, origin: 'settings.local.json', tierKey: 'allow' });
  const conflict = classify(e).find((f) => f.class === 'tier-conflict');
  // AC-FDC-3 — the effective set unions the tracked settings.json with the untracked
  // settings.local.json, so a consumer scoping its writes to the tracked file needs to know which
  // one actually satisfied the rule.
  assert.deepEqual(conflict.origins, ['settings.local.json']);
});

test('malformed_map_refuses', async () => {
  const fs = await import('node:fs');
  const osm = await import('node:os');
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(osm.tmpdir(), 'fdc-map-')));
  const write = (obj) => {
    const p = path.join(dir, `m${Math.abs(JSON.stringify(obj).length)}.json`);
    fs.writeFileSync(p, JSON.stringify(obj));
    return p;
  };
  const good = { schema_version: 1, plugin_root_glob: 'x', entries: [{ rule: 'Bash(a)', tier: 'allow' }] };
  assert.ok(loadMap(write(good)));
  // an out-of-enum tier: the field decides which effective tier an entry is compared against and
  // which tier a consumer writes it into, so it is refused rather than guessed at
  assert.throws(() => loadMap(write({ ...good, entries: [{ rule: 'Bash(a)', tier: 'allowed' }] })), MapMalformed);
  // a shape this build does not know
  assert.throws(() => loadMap(write({ ...good, schema_version: 2 })), MapMalformed);
  assert.throws(() => loadMap(write({ ...good, schema_version: undefined })), MapMalformed);
  assert.throws(() => loadMap(write({ ...good, entries: [] })), MapMalformed);
  assert.throws(() => loadMap(write({ ...good, entries: [{ rule: '', tier: 'allow' }] })), MapMalformed);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('blanket_allow_qualifies_every_finding', () => {
  const findings = classify(eff({ allow: ['Bash(*)'] }));
  const blanket = findings.filter((f) => f.class === 'blanket-allow');
  assert.equal(blanket.length, 1);
  // Under the fold a blanket rule's coverage reach is the empty string, so it covers the whole
  // allow tier: allow-absent goes to zero and the ask entries are swallowed. What survives is
  // ask-absent (those entries are genuinely missing from the ask tier, blanket or not) and
  // deny-missing (the deny direction refuses the fold). Those are exactly the findings a consumer
  // would act on, so they are the ones that must carry the qualifier — converging them reports a
  // closed floor while Bash(*) still defeats it. Qualified, not suppressed: suppressing hides the gap.
  const rest = findings.filter((f) => f.class !== 'blanket-allow');
  assert.equal(countBy(findings)['allow-absent'], undefined);
  assert.equal(countBy(findings)['ask-absent'], byTier('ask').length);
  assert.equal(countBy(findings)['deny-missing'], byTier('deny').length);
  assert.equal(rest.length, byTier('ask').length + byTier('deny').length);
  assert.ok(rest.every((f) => Array.isArray(f.qualifiedBy) && f.qualifiedBy.includes('Bash(*)')));
});

test('canonicalized_coverage_matches_the_deployed_rule_shape', () => {
  // The divergence this atom closed. The harness's ask-to-allow persist writes an ABSOLUTE,
  // version-resolved path into settings.local.json; the map declares `~` + `*` segments. Node
  // compared them as literal strings and reported 42 rules absent that the doctor could see were
  // covered — and the consuming reconcile would then have written 42 duplicates.
  const mapRule = byTier('allow').find((r) => r.includes('foundry-doctor.py'));
  const deployed = `Bash(${HOME}/.claude/plugins/cache/agentic-foundry/foundry/1.3.1/scripts/foundry-doctor.py:*)`;
  assert.equal(covers(deployed, mapRule, HOME, FOLD), true);
  assert.equal(countBy(classify(eff({ allow: [deployed] })))['allow-absent'], byTier('allow').length - 1);
  // the interpreter-word fold, which the old enumerated three-spelling set reached by coincidence
  assert.equal(isBlanketAllow('Bash(bash *)', HOME, FOLD), true);
  assert.equal(isBlanketAllow('Bash(sh:*)', HOME, FOLD), true);
});

test('deny_direction_refuses_the_fold', () => {
  // AC-DPF-3(b): deny coverage is EXACT reach equality. Node previously reused the allow-direction
  // relation here, which is strictly more permissive than the Python twin — a broad deny would
  // have stood in for the narrower one the map actually requires.
  const mapDeny = byTier('deny')[0];
  assert.equal(denyCovers('Bash(git:*)', mapDeny), false);
  assert.equal(denyCovers(mapDeny, mapDeny), true);
  const findings = classify(eff({ deny: ['Bash(git:*)'] }));
  assert.equal(countBy(findings)['deny-missing'], byTier('deny').length);
});

test('vocabulary_is_closed_over_emitted_classes', () => {
  const seen = new Set();
  for (const e of [eff({}), eff({ allow: ['Bash(*)'] }), eff({ allow: ['Read(x)'] })]) {
    for (const f of classify(e)) seen.add(f.class);
  }
  for (const c of seen) assert.ok(DRIFT_CLASSES.includes(c), `unexpected class ${c}`);
  assert.ok(DRIFT_CLASSES.includes('ask-absent'));
  assert.ok(DRIFT_CLASSES.includes('tier-conflict'));
});
