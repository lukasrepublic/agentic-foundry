// floorReconcile.mjs — feat-foundry-adoption-permission-floor-reconcile.
//
// The wizard already computes the whole answer for an existing workspace and refuses to act on it:
// it ships the floor as a bundled constant, reads the target's effective rules, and names every
// missing rule — then reports and writes nothing, because settings.json goes through the whole-file
// never-clobber plan (exists and differs -> `drifted` -> untouched). Five of seven adopter
// handbooks are missing the entire floor as a result.
//
// This module converges the target's `permissions` block by ADDING the rules the classifier named.
// Nothing is removed, nothing is reordered. The desired state is the shipped constant, the current
// state is what the classifier reads, and the write is the delta — recomputed every run, which is
// why no ledger is needed and why a second run is silent.
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
import path from 'node:path';
import { confinedJoin, RefusalError } from './util.mjs';
import { buildSettings } from './permissionFloor.mjs';

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
  if (entry === undefined) return { state: 'absent' };
  const ref = entry && entry.source && entry.source.ref;
  const pinned = typeof ref === 'string' && ref !== '' && !ref.includes('*') && entry.autoUpdate === false;
  return { state: pinned ? 'pinned' : 'unpinned', ref: typeof ref === 'string' ? ref : null };
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
  return { additions, total, pin, withheldAllow, blanket };
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
  const fd = fs.openSync(tmp, 'wx');
  try {
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
 * the two cannot describe the same plan differently. */
export function renderPlan(plan, { applied }) {
  const lines = [];
  const verb = applied ? 'added' : 'would add';
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const rule of plan.additions[tier]) lines.push(`  [${tier}] ${rule}`);
  }
  lines.push(
    `permission-floor reconcile: ${verb} ` +
      ['allow', 'ask', 'deny'].map((t) => `${t}=${plan.additions[t].length}`).join(', '),
  );
  if (plan.pin.state === 'absent') {
    lines.push(`  + marketplace pin added — the bundled allow rules are wildcarded across the plugin cache and are bounded only by it`);
  } else if (plan.withheldAllow) {
    lines.push(
      `  ! marketplace entry present but unpinned (ref=${plan.pin.ref === null ? 'none' : plan.pin.ref})` +
        ' — allow-tier rules WITHHELD; ask and deny still applied. Pin the marketplace, then re-run.',
    );
  }
  for (const rule of plan.blanket) {
    lines.push(`  ! qualified by blanket allow ${rule} — this rule defeats the floor until it is narrowed`);
  }
  return lines;
}
