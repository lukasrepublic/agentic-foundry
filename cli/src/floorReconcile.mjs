// floorReconcile.mjs — feat-foundry-adoption-permission-floor-reconcile.
//
// The wizard already computes the whole answer for an existing workspace and refuses to act on it:
// it ships the floor as a bundled constant, reads the target's effective rules, and names every
// missing rule — then reports and writes nothing, because settings.json goes through the whole-file
// never-clobber plan (exists and differs -> `drifted` -> untouched). Five of seven adopter
// handbooks are missing the entire floor as a result.
//
// This module converges the target's `permissions` block by ADDING the rules the classifier named.
// Nothing is reordered. The desired state is the shipped constant, the current state is what the
// classifier reads, and the write is the delta — recomputed every run, which is why no ledger is
// needed and why a second run is silent.
//
// AC-FRR-1 (ER #199, floor-retires-rows) adds the ONE narrow exception: a row shaped exactly like
// the floor's own root-glob rows, whose script name(+sub) the shipped map no longer declares, is
// removed from `allow`/`ask` — see `planRetirements`/`applyRetirements` below. Nothing else about
// the additive design above changes: an adopter-authored row of any other shape still survives
// forever, and `deny` is never touched by either side.
//
// THE WRITE IS THIS CLI'S FIRST TO A PATH THAT ALREADY EXISTS, and every anti-clobber control in
// the codebase is structurally unavailable to it. `applyPlan` opens O_EXCL, create-only, refusing
// by definition the case this module is entirely about. So:
//   - confinedJoin refuses a resolution that ESCAPES the root but PASSES an in-root symlink, and
//     `.claude/settings.json` symlinked to `.claude/foundry-operators.json` resolves inside it —
//     onto the file whose key membership alone mints an authorizer. statSync().isFile() returns
//     true through a symlink; only lstat sees it.
//   - truncate-then-write loses the operator's whole permissions block AND their install pin on a
//     ^C or ENOSPC. That is silent loss from a benign cause, which is never-clobber's substance
//     rather than its letter.
// One mechanism answers all three: confinement join + LINK-LEVEL stat + temp-in-.claude + rename.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { confinedJoin, RefusalError } from './util.mjs';
import { PROJECTED_TIERS, buildSettings, classifyDrift } from './permissionFloor.mjs';

/** The drift classes whose findings name a rule this module may ADD. Everything else the
 * classifier can emit is report-only: blanket-allow, ask-shadowed, ask-shadowed-ceremony and
 * tier-conflict each need a REMOVAL or a narrowing to close, which is a different and strictly
 * more dangerous capability than adding a rule from a shipped constant. An allowlist, not a
 * denylist — a class added to the vocabulary later defaults to not being written. */
export const ADDITIVE_CLASSES = Object.freeze(['allow-absent', 'ask-absent', 'deny-missing']);

const TIER_OF_CLASS = Object.freeze({
  'allow-absent': 'allow',
  'ask-absent': 'ask',
  'deny-missing': 'deny',
});

/** Read the target's TRACKED settings.json only. Deliberately not the union with
 * settings.local.json: a floor rule carried only in the untracked file reads as covered, so the
 * tracked file would stay incomplete while the report says converged — sharpest for `deny`, where
 * the repo then ships to every other clone and to CI without it. The union is still what the
 * REPORT is computed over; only the write set is narrowed. */
export function readTrackedRules(settingsObj) {
  const perms = (settingsObj && settingsObj.permissions) || {};
  const out = { allow: [], ask: [], deny: [] };
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const rule of perms[tier] || []) out[tier].push({ rule, origin: 'settings.json', tierKey: tier });
  }
  return out;
}

/** Does this target carry a foundry marketplace entry, and is it pinned?
 *
 * Load-bearing, and easy to miss. Every bundled `allow` rule is wildcarded across the plugin cache,
 * so the grant is bounded only by the pinned marketplace ref + autoUpdate:false that the create
 * path writes IN THE SAME FILE, IN THE SAME WRITE. Adding the allow rules alone would convert one
 * trust acceptance into a standing grant over whatever a future resolution drops into that cache
 * path. Six of the seven adopter handbooks carry no marketplace entry at all. */
export function classifyPin(settingsObj, pins) {
  const entry = ((settingsObj && settingsObj.extraKnownMarketplaces) || {})[pins.marketplace_name];
  if (entry === undefined) return { state: 'absent', ref: null, skew: false };
  const ref = entry && entry.source && entry.source.ref;
  // feat-foundry-installer-unpinning (AC-IUP-5). Both installers now register the marketplace
  // TAGLESS by default (AC-IUP-1/AC-IUP-3), so "no ref" must classify as pinned, not unpinned — an
  // ABSENT ref is well-formed (refAbsent), distinct from a malformed EXPLICIT one (empty string, or
  // carrying a wildcard, both still refused below). AC-IUP-4's Clarifications also widen the
  // autoUpdate check from `=== false` to `!== true`: the shell installer has no --autoUpdate flag
  // on `claude plugin marketplace add` and a verified isolated run wrote no such key at all, so an
  // ABSENT autoUpdate must read the same as an explicit `false` — never merely tolerate `true`.
  //
  // SECURITY REVIEW (PR #132): widening "no ref" to `pinned` must NOT also widen it to "any entry".
  // Property access on a primitive yields undefined rather than throwing, so `{}`, `"x"` and `[]`
  // all reach refAbsent -- and NOTHING here compared the entry's own repo to ours, so a tagless
  // entry naming a FOREIGN repository under our key would classify pinned and be granted the 42
  // wildcarded cache allow rules, with the report REASSURING the operator about a manifest that is
  // not ours. Before this atom that input hit the brake and warned. An entry must therefore be a
  // plain object naming OUR github source before "no ref" can mean anything. `autoUpdate` is
  // accepted only as absent-or-false: `!== true` admitted truthy coercions (1, "true") that the
  // platform may honour as auto-update-on, which is wider than AC-IUP-5's text.
  const isPlainObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);
  const src = isPlainObject(entry) && isPlainObject(entry.source) ? entry.source : null;
  // Both operands undefined must NOT compare equal: a pin block that lost marketplace_repo
  // would otherwise make every source carrying no repo key classify as ours.
  const ourRepo = typeof pins.marketplace_repo === 'string' && pins.marketplace_repo !== ''
    ? pins.marketplace_repo : null;
  // GitHub owner/repo is CASE-INSENSITIVE, so `LukasRepublic/agentic-foundry` is the SAME
  // repository. Comparing byte-exact would classify it unpinned and tell the operator "not ours",
  // which is false. Normalize both sides; the comparison stays exact-match otherwise.
  // Gate on the ASCII shape GitHub can actually issue BEFORE folding. toLowerCase() is
  // locale-independent but NOT injective: U+212A KELVIN SIGN folds to ASCII 'k', and this repo's
  // name contains one -- so "lu\u212Aasrepublic/agentic-foundry" would fold equal and read as
  // ours. No such string can name a real GitHub repository, so it buys a false reassurance rather
  // than attacker-controlled code; the shape gate closes the class outright, including whatever
  // confusable the next Unicode revision adds.
  const REPO_RE = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
  const sameRepo = typeof src?.repo === 'string' && REPO_RE.test(src.repo) && !!ourRepo
    && src.repo.toLowerCase() === ourRepo.toLowerCase();
  const isOurSource = !!src && sameRepo && src.source === 'github';
  const refAbsent = isOurSource && ref === undefined;
  const refWellFormed = isOurSource
    && (refAbsent || (typeof ref === 'string' && ref !== '' && !ref.includes('*')));
  const autoUpdateOk = entry && (entry.autoUpdate === undefined || entry.autoUpdate === false);
  const pinned = refWellFormed && autoUpdateOk;
  // A pin can be perfectly well-formed and still name a DIFFERENT plugin than the map these rules
  // came from. The 42 allow rules are wildcarded across the cache and their per-script rationales
  // were reviewed against THIS version's scripts; writing them over a workspace pinned at an older
  // one grants the same paths against different code. Surfaced rather than refused — the operator's
  // review of the plan is the control, and it can only work if the skew is on screen. AC-IUP-8: a
  // tagless (refAbsent) entry has nothing to compare against a version, so it can never be "skewed"
  // — computing `ref !== vX` against a null ref would otherwise fire the warning on every run.
  const skew = pinned && !refAbsent && ref !== `v${pins.plugin_version}`;
  // The foreign/malformed-source case must not render as an ordinary ref-shaped 'unpinned':
  // its remediation ("pin the marketplace") can NEVER clear it, because refWellFormed requires
  // isOurSource. Carry the reason so renderPlan can say the thing the operator can act on.
  const reason = pinned ? null : (!isOurSource ? 'source' : (!autoUpdateOk ? 'autoUpdate' : 'ref'));
  return { state: pinned ? 'pinned' : 'unpinned', ref: typeof ref === 'string' ? ref : null, skew, reason };
}

/** Compute the delta WITHOUT touching the filesystem. Returns
 * { additions: {allow,ask,deny}, total, pin, withheldAllow, blanket }.
 *
 * `findings` is classifyDrift's output over the TRACKED rules; `map` supplies the tier each rule
 * belongs in — never the finding, and never anything read from the target. */
export function planAdditions({ findings, map, settingsObj, pins }) {
  const tierOfRule = new Map(map.entries.map((e) => [e.rule, e.tier]));
  const additions = { allow: [], ask: [], deny: [] };

  const pin = classifyPin(settingsObj, pins);
  // An UNPINNED existing entry withholds the allow tier only. ask and deny are strengthening —
  // more prompting, more blocking — so holding them hostage to the pin would leave the floor worse
  // for no gain. An ABSENT entry is not a refusal: the pin is added alongside the grants, exactly
  // as the create path emits them together.
  const withheldAllow = pin.state === 'unpinned';

  for (const f of findings) {
    if (!ADDITIVE_CLASSES.includes(f.class)) continue;
    const tier = tierOfRule.get(f.rule);
    // v1.18.0: only the projected tier (deny) is ever written; script rows are a registry
    if (!PROJECTED_TIERS.includes(tier)) continue;
    // the tier comes from the map; a finding naming a rule the map does not declare is not ours
    if (tier === undefined || tier !== TIER_OF_CLASS[f.class]) continue;
    if (tier === 'allow' && withheldAllow) continue;
    if (!additions[tier].includes(f.rule)) additions[tier].push(f.rule);
  }

  // preserve bundled-map order so two runs over the same input produce the same diff
  for (const tier of ['allow', 'ask', 'deny']) {
    const order = map.entries.filter((e) => e.tier === tier).map((e) => e.rule);
    additions[tier].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  }

  const total = additions.allow.length + additions.ask.length + additions.deny.length;
  const blanket = findings.filter((f) => f.class === 'blanket-allow').map((f) => f.rule);
  return { additions, total, pin, withheldAllow, blanket, pinsVersion: pins.plugin_version };
}

// ── AC-FRR-1/-2 (floor-retires-rows, ER #199). ───────────────────────────────────────────────────
//
// The reconcile above only ever ADDS: every pre-existing rule survives forever, even the ones the
// shipped floor itself no longer declares. Concretely: v1.15.0 deleted four fleet scripts (#196)
// and dropped their four `allow` rows from cli/permission-floor.json, and every workspace scaffolded
// or reconciled at an earlier version keeps those four `allow` rules with nothing to remove them —
// a script with the same name landing later would silently inherit a grant nobody re-authorized.
//
// Retirement is deliberately NARROWER than the addition side's classifyDrift/covers() fold: it
// compares a row's LITERAL text against the floor's OWN `plugin_root_glob` text (never resolved
// against a real filesystem path, never folded across `~`/`$HOME`/an interpreter word), because the
// question here is "did the FLOOR ITSELF write this exact shape", not "does some broader rule cover
// the reach this map entry names". A row of any other shape — a different prefix, a hand-authored
// rule naming the same script through a bare path, a `deny` row — is never a retirement candidate
// (AC-FRR-2): only a row shaped exactly like the ones `buildSettings`/the reconcile itself would
// write is the floor's own to take back.

const ROOT_SHAPE_NAME_RE = '[A-Za-z0-9_.-]+';

/** Escape every regex metacharacter in `glob`, INCLUDING `*` — the floor's own glob text is matched
 * LITERALLY here (an actual `*` character in the permission rule's text), never expanded against a
 * filesystem. Sibling of foldRegexFromGlob in permissionFloor.mjs, which instead treats `*` as a
 * wildcard for the addition-side coverage fold; the two escape functions look alike and answer two
 * different questions on purpose. */
function escapeLiteral(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Build the regex that recognizes the floor's own row shape over ITS OWN `plugin_root_glob`:
 * `Bash(<plugin_root_glob>/scripts/<name>[ <sub>]:*)`. Derived from the map's own glob text, never
 * a second hardcoded copy of it — the same one-source-of-truth reasoning foldRegexFromGlob already
 * documents for the addition side. */
function floorRootShapeRe(pluginRootGlob) {
  // v1.18.0: the `:*` suffix is optional — earlier releases also wrote a bare row (the doctor's
  // `Bash(<glob>/scripts/foundry-doctor.py)`), and retirement must take that back too.
  return new RegExp(`^Bash\\(${escapeLiteral(pluginRootGlob)}/scripts/(${ROOT_SHAPE_NAME_RE})(?: (.+?))?(?::\\*)?\\)$`);
}

/** Parse `rule` against the floor's own root-glob shape (the same shape `buildSettings` writes).
 * Returns `{ name, sub }` (`sub` is `null` for a bare `<name>:*` row) when `rule` is of exactly this
 * shape, `null` for any other shape at all — including the map's own bare, non-`:*` exception
 * (`foundry-doctor.py)` with no trailing marker) and every adopter-authored rule that merely NAMES a
 * plugin script through a different prefix or with no plugin-root glob (AC-FRR-2). Exported for the
 * differential/fixture tests that need to assert the shape gate directly. */
export function parseFloorRootShape(rule, pluginRootGlob) {
  if (typeof pluginRootGlob !== 'string' || pluginRootGlob === '') return null;
  const m = floorRootShapeRe(pluginRootGlob).exec(rule);
  if (!m) return null;
  return { name: m[1], sub: m[2] ?? null };
}

/** The floor's root-glob shape with every `*` segment of the glob allowed to be a CONCRETE
 * segment instead (`cache/agentic-foundry/foundry/1.9.1/scripts/<name>[ <sub>]:*`) — the shape an
 * init before installer-unpinning (v1.7.0) wrote, with the marketplace directory and the plugin
 * version spelled out. hotfix-v1.17.3: such rows are stale by construction (the floor has written
 * only version-wildcarded rows since; the wildcard row the reconcile adds in the same pass covers
 * the script), and because they never matched the exact-shape regex above, nothing ever retired
 * them — an adopter carried `1.9.1` allow rows across eight releases. `*` in the glob becomes
 * `([^/]+)`; a row whose captured segments are ALL literal `*` is the exact shape (handled above),
 * so this parser reports `pinned: true` only when at least one segment is concrete. */
function floorPinnedShapeRe(pluginRootGlob) {
  // PR #233 security review Risk 2: every `*` but the last (the marketplace directory) may be a
  // plain name (no dots — so `..` and an operator's partial glob like `1.*` never qualify) or the
  // literal `*`; the LAST `*` (the plugin version) may be a semver-shaped segment or `*`. A rule
  // from a foreign marketplace still matches by shape (the name is not the floor's to know here),
  // but only for the tiers and conditions planRetirements allows.
  const stars = (pluginRootGlob.match(/\*/g) || []).length;
  let seen = 0;
  const src = escapeLiteral(pluginRootGlob).replace(/\\\*/g, () => {
    seen += 1;
    return seen === stars ? '(\\*|\\d+\\.\\d+\\.\\d+[A-Za-z0-9.+-]*)' : '(\\*|[A-Za-z0-9_-]+)';
  });
  return new RegExp(`^Bash\\(${src}/scripts/(${ROOT_SHAPE_NAME_RE})(?: (.+?))?(?::\\*)?\\)$`);
}

/** Parse `rule` as a version-/marketplace-PINNED variant of the floor's own row shape. Returns
 * `{ name, sub, pinned: true }` when at least one glob segment is concrete in the row, `null` for
 * the exact wildcard shape (parseFloorRootShape's business) and for every other shape. Exported for
 * the fixture tests. */
export function parseFloorPinnedShape(rule, pluginRootGlob) {
  if (typeof pluginRootGlob !== 'string' || pluginRootGlob === '') return null;
  const stars = (pluginRootGlob.match(/\*/g) || []).length;
  if (stars === 0) return null;
  const m = floorPinnedShapeRe(pluginRootGlob).exec(rule);
  if (!m) return null;
  const segs = m.slice(1, 1 + stars);
  if (segs.every((x) => x === '*')) return null;
  return { name: m[1 + stars], sub: m[2 + stars] ?? null, pinned: true };
}

/** A collision-free key for the `(name, sub)` pair — `JSON.stringify` of a 2-tuple rather than a
 * string concatenation with a hand-picked separator, which a `sub` containing that exact separator
 * (an unlikely but not-impossible flag value) could otherwise fold into a DIFFERENT pair's key. */
function rootNameKey(parsed) {
  return JSON.stringify([parsed.name, parsed.sub]);
}

/** The (name, sub) pairs the SHIPPED map itself declares, each parsed through the exact same shape
 * matcher used to recognize a target row — never a second, hand-written enumeration of script
 * names, which is precisely how the two could drift apart. A map entry of another shape (there is
 * none today, but the parser must not silently assume one) contributes nothing here. */
function shippedRootNames(map) {
  const set = new Set();
  for (const e of map.entries) {
    const parsed = parseFloorRootShape(e.rule, map.plugin_root_glob);
    if (parsed) set.add(rootNameKey(parsed));
  }
  return set;
}

/** The `(name, sub)` keys of every wildcard-shaped `ask` row a settings object carries — what
 * `planRetirements({ askCoveredBy })` takes for the local file (hotfix-v1.17.4). */
export function askRootKeys(settingsObj, map) {
  const keys = new Set();
  const perms = (settingsObj && settingsObj.permissions) || {};
  for (const rule of perms.ask || []) {
    const parsed = typeof rule === 'string' ? parseFloorRootShape(rule, map.plugin_root_glob) : null;
    if (parsed) keys.add(rootNameKey(parsed));
  }
  return keys;
}

/** Compute the rows the reconcile SHALL remove (AC-FRR-1), WITHOUT touching the filesystem. Every
 * `allow`/`ask` row shaped exactly like the floor's own root-glob rows, whose `(name, sub)` pair the
 * shipped map no longer declares, is queued for removal. `deny` is never a candidate (AC-FRR-2) —
 * retirement only narrows a grant or a prompt, and a deny row read back as "extra" is, if anything,
 * a reason to leave it exactly where the operator (or an earlier release) put it. Returns
 * `{ retirements: { allow: [...], ask: [...] }, total }`. */
/** Rows an earlier release of the floor wrote verbatim and v1.18.0 takes back (AC-V118A-2/-4): the
 * broad force-push deny (the git-discipline hook is the floor — it refuses protected targets and
 * allows feature branches, which this row wrongly refused), the release-ceremony ask row, and the
 * policy file's self-guard deny pair (operator decision 2026-09-25). Literal text only. */
export const RETIRED_FLOOR_LITERALS = Object.freeze({
  allow: Object.freeze([]),
  ask: Object.freeze(['Bash(claude plugin tag:*)']),
  deny: Object.freeze([
    'Bash(git push --force:*)',
    'Edit(.foundry/permissions.yaml)',
    'Write(.foundry/permissions.yaml)',
  ]),
});

/** v1.18.0 (AC-V118A-2): every `allow`/`ask` row shaped exactly like the floor's own script rows —
 * wildcard (`<plugin_root_glob>/scripts/<x>`) or version-/marketplace-pinned — is retired, whether
 * or not the script still ships: those rows never matched a real invocation (measured), and the
 * plugin's scripts are allowed by the PreToolUse hook instead. Plus the RETIRED_FLOOR_LITERALS.
 * Any other shape — an operator's own rule, a different prefix, a hand-written bare path — is never
 * touched (AC-FRR-2). Retiring an `ask` row cannot turn a prompt into a grant the operator did not
 * choose: the scripts it named are allowed by design (operator decision: no foundry script prompts).
 * `askCoveredBy` is accepted for call-site compatibility and ignored. */
export function planRetirements({ settingsObj, map, askCoveredBy = null }) { // eslint-disable-line no-unused-vars
  const retirements = { allow: [], ask: [], deny: [] };
  const perms = (settingsObj && settingsObj.permissions) || {};
  for (const tier of ['allow', 'ask']) {
    for (const rule of perms[tier] || []) {
      if (typeof rule !== 'string') continue;
      if (parseFloorRootShape(rule, map.plugin_root_glob) || parseFloorPinnedShape(rule, map.plugin_root_glob)) {
        retirements[tier].push(rule);
      }
    }
  }
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const rule of perms[tier] || []) {
      if (RETIRED_FLOOR_LITERALS[tier].includes(rule) && !retirements[tier].includes(rule)) retirements[tier].push(rule);
    }
  }
  const total = retirements.allow.length + retirements.ask.length + retirements.deny.length;
  return { retirements, total };
}

/** Apply a retirement plan to a parsed settings object, returning a NEW object. Pure — no I/O.
 * Removes exactly the named rows from `allow`/`ask`, preserving every surviving rule's text and
 * relative order; `deny` and every other top-level key pass through untouched. Composed with
 * `applyAdditions` at the call site — the two touch disjoint rule sets by construction (a row
 * cannot simultaneously be absent-from-target-and-in-the-map, which is what `applyAdditions` adds,
 * and present-in-target-and-absent-from-the-map, which is what this removes). */
export function applyRetirements(settingsObj, retirementPlan) {
  const next = { ...settingsObj };
  const perms = { ...(settingsObj.permissions || {}) };
  for (const tier of ['allow', 'ask', 'deny']) {
    const toRemove = new Set(retirementPlan.retirements[tier] || []);
    if (toRemove.size === 0) continue;
    const existing = Array.isArray(perms[tier]) ? perms[tier] : [];
    perms[tier] = existing.filter((rule) => !toRemove.has(rule));
  }
  next.permissions = perms;
  return next;
}

/** Apply a plan to a parsed settings object, returning a NEW object. Pure — no I/O.
 *
 * Additive by construction: every pre-existing rule keeps its text, its tier and its position
 * relative to the other pre-existing rules of that tier, because new rules are appended and the
 * prior array is copied in order. No input can express a removal — the rules come from the bundled
 * map and the only operation is append. */
export function applyAdditions(settingsObj, plan, { map, pins }) {
  const next = { ...settingsObj };
  const perms = { ...(settingsObj.permissions || {}) };
  for (const tier of ['allow', 'ask', 'deny']) {
    const existing = Array.isArray(perms[tier]) ? perms[tier] : [];
    perms[tier] = plan.additions[tier].length > 0 ? [...existing, ...plan.additions[tier]] : existing;
  }
  next.permissions = perms;

  if (plan.pin.state === 'absent') {
    // Taken from buildSettings — the SAME function the create path uses, over the same map and the
    // same pins — rather than re-spelling the entry here. A second copy of that literal is how the
    // pin drifts from what a fresh scaffold writes. Only its marketplace block is used; the
    // permissions it also builds are irrelevant here and discarded.
    const created = buildSettings(map, pins);
    next.extraKnownMarketplaces = {
      ...(settingsObj.extraKnownMarketplaces || {}),
      ...created.extraKnownMarketplaces,
    };
  }
  return next;
}

// ── AC-FRR-1 review round 1: additions MUST be planned against the POST-retirement rule set ────
//
// Computing `planAdditions` over the raw (pre-retirement) tracked rules is a real, one-cycle
// defect, not a hypothetical: the addition side's `covers()` is a PREFIX fold — a `:*`-suffixed
// effective rule covers every narrower reach beneath it — so a bare `Bash(<glob>/scripts/foo:*)`
// row a workspace still carries covers BOTH `Bash(<glob>/scripts/foo --a:*)` and
// `Bash(<glob>/scripts/foo --b:*)` map entries a map restructure might split it into. Planned in
// that order, a SINGLE reconcile pass would retire the bare row (its exact `(foo, null)` pair is
// no longer in the shipped map) while adding NEITHER split row (the pre-retirement classification
// still sees the bare row "covering" them) — the grant for that script is gone until a SECOND run
// notices the split rows are now genuinely absent. `planReconcile` closes the gap by re-deriving
// the tracked rules from the ALREADY-RETIRED settings object before classifying what to add, so
// the two sides compose into one correct delta in one pass. This is the ONLY entry point either
// call site (run.mjs's `--existing` path, update.mjs's Phase 4) should use from here on — never
// `planAdditions`/`planRetirements` called separately against the same raw settingsObj.

/** Compute both plans, correctly composed: retirement first, additions against the resulting
 * (post-retirement) rule set. Returns `{ additionsPlan, retirementPlan }`; `additionsPlan.settingsObj`
 * is the POST-retirement object — the one `applyAdditions` must be called against, so the caller
 * never needs to call `applyRetirements` a second time on top of it. */
export function planReconcile({
  settingsObj, map, pins, pluginRootExpansion = [], unreadableOrigins = [], home = os.homedir(),
}) {
  const retirementPlan = planRetirements({ settingsObj, map });
  const postRetirementSettingsObj = applyRetirements(settingsObj, retirementPlan);
  const findings = classifyDrift(map, readTrackedRules(postRetirementSettingsObj), {
    pluginRootExpansion, unreadableOrigins, home,
  });
  const additionsPlan = planAdditions({ findings, map, settingsObj: postRetirementSettingsObj, pins });
  additionsPlan.settingsObj = postRetirementSettingsObj;
  return { additionsPlan, retirementPlan };
}

/** Resolve the target settings path, refusing anything that is not a regular file inside the root.
 * lstat, NOT stat: statSync().isFile() follows a symlink, so an in-root link would pass. */
export function resolveTarget(physicalRoot) {
  const joined = confinedJoin(physicalRoot, path.join('.claude', 'settings.json'));
  if (joined === null) {
    throw new RefusalError('refusing .claude/settings.json: path escapes the target root', '.claude/settings.json');
  }
  const st = fs.lstatSync(joined, { throwIfNoEntry: false });
  if (!st) return { path: joined, present: false };
  if (!st.isFile()) {
    throw new RefusalError(
      `refusing ${joined}: not a regular file (symlink or special file)`,
      '.claude/settings.json',
    );
  }
  return { path: joined, present: true };
}

/** Parse the tracked settings file, refusing rather than treating unparseable as empty. Classifying
 * against an empty rule set and then writing into a file whose keys could not be read would make
 * the preserve-every-other-key guarantee unimplementable. */
export function readTarget(targetPath) {
  const raw = fs.readFileSync(targetPath, 'utf-8');
  try {
    const doc = JSON.parse(raw);
    if (doc === null || typeof doc !== 'object' || Array.isArray(doc)) throw new Error('not an object');
    return doc;
  } catch (e) {
    throw new RefusalError(
      `refusing ${targetPath}: does not parse as a JSON object (${e.message})`,
      '.claude/settings.json',
    );
  }
}

/** Install new content by rename. Temp file in the TARGET'S OWN directory so the rename is
 * same-filesystem and therefore atomic; rename neither follows a symlink at the destination nor
 * can leave a truncated file, which closes the interrupted-write loss and the classify-to-write
 * race together — the same argument applyPlan's O_EXCL comment already makes for the create path. */
export function writeTargetAtomically(targetPath, obj) {
  const dir = path.dirname(targetPath);
  const tmp = path.join(dir, `.settings.json.${process.pid}.tmp`);
  const bytes = Buffer.from(`${JSON.stringify(obj, null, 2)}\n`, 'utf-8');
  // hotfix-v1.17.4 (PR #237 security review Risk 5): a rename-install would otherwise reset the
  // target's mode to the umask default — an operator's 0600 `settings.local.json` (it often holds
  // `env`) must come back 0600. The original's permission bits are copied onto the temp file
  // before the rename; a target that does not exist yet keeps the default.
  let mode = null;
  try {
    mode = fs.statSync(targetPath).mode & 0o777;
  } catch {
    mode = null;
  }
  const fd = fs.openSync(tmp, 'wx');
  try {
    if (mode !== null) fs.fchmodSync(fd, mode);
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  try {
    fs.renameSync(tmp, targetPath);
  } catch (e) {
    fs.rmSync(tmp, { force: true });
    throw e;
  }
}

/** Render the plan for the operator: every rule that would be added, with its tier, plus the
 * per-tier counts and any qualifier. Used for both the dry-run report and the post-write one, so
 * the two cannot describe the same plan differently.
 *
 * AC-FRR-1: when `retirementPlan` is supplied (both call sites in this codebase always supply
 * one), every row it names prints as `[retired] <row>` — same tag whether this is a dry-run
 * preview or a just-applied report, because the row itself does not become "more retired" for
 * having actually been removed — and the summary line gains `N added, M retired, K unchanged` in
 * the SAME line as the existing per-tier addition counts, never a second, separately-findable
 * report. `K unchanged` is the shipped floor's own row count minus what this run added — the rows
 * that needed no action at all, additions and retirements both being actions. */
export function renderPlan(plan, { applied, retirementPlan = null, mapEntryCount = null }) {
  const lines = [];
  const verb = applied ? 'added' : 'would add';
  const rverb = applied ? 'retired' : 'would retire';
  // v1.18.0 (AC-V118A-7): every row names its file and its tier, so no reader can mistake which
  // file holds which tier (the 2026-09-25 misread: an ask row relayed as a deny).
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const rule of plan.additions[tier]) lines.push(`  [${applied ? 'added' : 'would add'}] .claude/settings.json ${tier}: ${rule}`);
  }
  if (retirementPlan) {
    for (const tier of ['allow', 'ask', 'deny']) {
      for (const rule of retirementPlan.retirements[tier] || []) lines.push(`  [${rverb}] .claude/settings.json ${tier}: ${rule}`);
    }
  }
  let summary = `permission-floor reconcile (.claude/settings.json): ${verb} ` +
      ['allow', 'ask', 'deny'].map((t) => `${t}=${plan.additions[t].length}`).join(', ');
  if (retirementPlan) {
    summary += `; ${rverb} ` + ['allow', 'ask', 'deny'].map((t) => `${t}=${(retirementPlan.retirements[t] || []).length}`).join(', ');
  }
  lines.push(summary);
  if (plan.pin.state === 'absent') {
    lines.push(`  + marketplace pin added — the bundled allow rules are wildcarded across the plugin cache and are bounded only by it`);
  } else if (plan.pin.state === 'pinned') {
    // printed on EVERY pinned run, not only the skewed one: an operator cannot notice a mismatch
    // that is never shown, and this is the branch where 42 grants are written without comment.
    // AC-IUP-8: a TAGLESS entry (plan.pin.ref === null, the default registration as of
    // feat-foundry-installer-unpinning) must never render as "pinned at null" — say what actually
    // bounds it instead. classifyPin's `skew` is already forced false for this case, so the
    // trailing VERSION SKEW clause never appends here.
    const refDisplay = plan.pin.ref === null
      ? 'the default catalogue (tagless index; the artifact commit stays fixed by the manifest\'s source.sha)'
      : plan.pin.ref;
    lines.push(
      `  · marketplace pinned at ${refDisplay}; these rules come from the floor generated for v${plan.pinsVersion}` +
        (plan.pin.skew ? ' — VERSION SKEW: the same wildcarded paths will resolve to that pin\'s scripts, not this one\'s' : ''),
    );
  } else if (plan.withheldAllow) {
    lines.push(
      plan.pin.reason === 'source' || plan.pin.reason === 'autoUpdate'
        ? `  ! the marketplace entry ${plan.pin.reason === 'autoUpdate' ? 'sets autoUpdate to something other than false' : "does not name this marketplace's github source"}` +
          ' — allow-tier rules WITHHELD; ask and deny still applied. Pinning will NOT clear this:' +
          ' the registered source itself is not ours. Inspect the entry before re-running.'
        : `  ! marketplace entry present but unpinned (ref=${plan.pin.ref === null ? 'none' : plan.pin.ref})` +
          ' — allow-tier rules WITHHELD; ask and deny still applied. Pin the marketplace, then re-run.',
    );
  }
  for (const rule of plan.blanket) {
    lines.push(`  ! qualified by blanket allow ${rule} — this rule defeats the floor until it is narrowed`);
  }
  return lines;
}
