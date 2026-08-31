// permissionFloor.mjs — loads the bundled map, builds the written settings.json object (a
// verbatim declaration, never a grant — AC-BCL-4), renders the capability preview, and classifies
// drift between the map and a target's effective settings using the AC-DPF-8 vocabulary
// (AC-BCL-8). `covers()` agrees with tests/test_permission_floor_map.py::_subsumes on the shared
// 8-row table by construction (same prefix-subsumption rule).
import fs from 'node:fs';
import os from 'node:os';

/** The one map schema_version this build understands. A map declaring anything else is refused
 * rather than read optimistically — the tier field is load-bearing (it decides which effective tier
 * an entry is compared against, and which tier a consumer writes it into), so a shape this code
 * does not know is not a shape it may guess at. */
export const MAP_SCHEMA_VERSION = 1;

export const TIERS = Object.freeze(['allow', 'ask', 'deny']);

/** Thrown by loadMap for a structurally invalid bundled map. Mirrors the Python floor checker's
 * FloorMalformed, which is the one condition that reds the doctor rather than reporting advisory. */
export class MapMalformed extends Error {}

/** Load + VALIDATE the bundled map. Validation is new (AC-FDC-4): this used to parse and return,
 * with no schema_version check and no tier check — the Python twin already refused an out-of-enum
 * tier, so the two disagreed on what counted as a loadable map. On the create path a bad tier at
 * least crashed in buildSettings (`byTier[e.tier].push` on undefined); anything consuming findings
 * programmatically would instead act on it. */
export function loadMap(mapPath) {
  const text = fs.readFileSync(mapPath, 'utf-8');
  let doc;
  try {
    doc = JSON.parse(text);
  } catch (e) {
    throw new MapMalformed(`permission-floor map unparseable: ${e.message}`);
  }
  if (doc === null || typeof doc !== 'object' || Array.isArray(doc)) {
    throw new MapMalformed('permission-floor map is not a JSON object');
  }
  if (doc.schema_version !== MAP_SCHEMA_VERSION) {
    throw new MapMalformed(
      `permission-floor map schema_version ${JSON.stringify(doc.schema_version)} is not the ` +
        `${MAP_SCHEMA_VERSION} this build understands`,
    );
  }
  if (!Array.isArray(doc.entries) || doc.entries.length === 0) {
    throw new MapMalformed('permission-floor map missing entries');
  }
  for (const e of doc.entries) {
    if (e === null || typeof e !== 'object' || typeof e.rule !== 'string' || e.rule === '') {
      throw new MapMalformed('permission-floor map entry has a non-string/empty rule');
    }
    if (!TIERS.includes(e.tier)) {
      throw new MapMalformed(
        `permission-floor map entry has invalid tier ${JSON.stringify(e.tier)}`,
      );
    }
  }
  return doc;
}

const RULE_RE = /^([A-Za-z0-9_-]+)\((.*)\)$/s;

export function ruleBody(rule) {
  const m = RULE_RE.exec(rule);
  if (!m) return null;
  return m[2];
}

export function ruleTool(rule) {
  const m = RULE_RE.exec(rule);
  return m ? m[1] : null;
}

// --------------------------------------------------------------------------------------------- //
// canonicalization + the covers relation — AC-DPF-3's fold, ported from the Python floor checker
// so the two implementations decide coverage the same way (AC-FDC-6).
//
// WHY THIS REPLACED A RAW PREFIX MATCH. The previous `covers` compared rule bodies as literal
// strings. The Python twin canonicalizes first: it drops a leading interpreter word, expands `~/`
// and `$HOME/` against the real home, and folds away the marketplace/version segments beneath
// the plugin root. For the input that actually occurs in the wild — the harness's ask-to-allow persist,
// which writes an ABSOLUTE, version-resolved path into settings.local.json — the two disagreed:
//
//   effective  an ABSOLUTE, version-resolved path under the operator's real plugin root
//   map        the same script named through the map's `~` + wildcard form
//   python covers -> true          node covers -> false
//
// So the CLI reported `allow-absent` for rules the doctor could see were covered. Left alone, the
// consuming reconcile atom would write ~42 duplicates of rules already effectively present.
// --------------------------------------------------------------------------------------------- //

const INTERPRETER_WORDS = new Set(['python3', 'python', 'bash', 'sh']);

/** Build the cache-fold pattern from the map's OWN `plugin_root_glob` rather than from a second
 * hardcoded copy of that path. One source of truth: a map that moves its plugin root cannot leave
 * a stale fold behind here. Each `*` becomes one non-empty, slash-free segment.
 *
 * Absent a glob there is no fold — a caller that does not know where the plugin root lives has no
 * business guessing at it. classifyDrift always has the map, so the real path always folds. */
export function foldRegexFromGlob(glob) {
  if (typeof glob !== 'string' || glob === '') return null;
  const tail = glob.replace(/^~\//, '').replace(/^\.claude\//, '');
  const escaped = tail
    .split('*')
    .map((seg) => seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('[^/\\s]+');
  return new RegExp(escaped + '/');
}

function foldBody(body, home, foldRe) {
  body = body.trim();
  const m = /^(\S+)\s+([\s\S]+)$/.exec(body);
  if (m && INTERPRETER_WORDS.has(m[1])) body = m[2];
  if (body.startsWith('~/')) body = home.replace(/\/+$/, '') + body.slice(1);
  else if (body.startsWith('$HOME/')) body = home.replace(/\/+$/, '') + body.slice('$HOME'.length);
  if (foldRe) {
    const f = foldRe.exec(body);
    if (f) body = body.slice(f.index + f[0].length);
  }
  return body;
}

/** Returns { reach, isPrefix }. */
function splitMarker(body) {
  if (body === '*') return { reach: '', isPrefix: true };
  if (body.endsWith(':*')) return { reach: body.slice(0, -2), isPrefix: true };
  if (body.length >= 2 && body.endsWith('*') && (body[body.length - 2] === ' ' || body[body.length - 2] === '/')) {
    return { reach: body.slice(0, -2), isPrefix: true };
  }
  return { reach: body, isPrefix: false };
}

/** AC-DPF-3's ask/allow-direction fold. Returns null for a rule that does not participate. */
export function canonicalize(rule, home = os.homedir(), foldRe = null) {
  const body = ruleBody(rule);
  if (body === null) return null;
  const folded = foldBody(body, home, foldRe);
  const { reach, isPrefix } = splitMarker(folded);
  const isBlanket = isPrefix && (reach === '' || INTERPRETER_WORDS.has(reach));
  return { raw: rule, folded, reach, isPrefix, isBlanket, coverageReach: isBlanket ? '' : reach };
}

function coversCanon(effectiveC, mapC) {
  if (effectiveC === null || mapC === null) return false;
  if (effectiveC.isPrefix) return mapC.reach.startsWith(effectiveC.coverageReach);
  return effectiveC.reach === mapC.reach;
}

/** Does `effectiveRule` (the broad rule) cover `mapRule` under the ask/allow fold? */
export function covers(effectiveRule, mapRule, home = os.homedir(), foldRe = null) {
  return coversCanon(canonicalize(effectiveRule, home, foldRe), canonicalize(mapRule, home, foldRe));
}

/** AC-DPF-3(b), the DENY direction: no interpreter drop, no plugin-cache fold, and coverage
 * requires EXACT reach equality rather than a prefix. A deny is a promise about a specific thing;
 * folding one rule into another would let a broader-looking deny silently stand in for a narrower
 * one the map actually requires. The Node side previously reused the allow-direction `covers` here,
 * which is strictly more permissive than the twin. */
export function canonicalizeIdentity(rule) {
  const body = ruleBody(rule);
  if (body === null) return null;
  return splitMarker(body.trim());
}

export function denyCovers(effectiveRule, mapRule) {
  const a = canonicalizeIdentity(effectiveRule);
  const b = canonicalizeIdentity(mapRule);
  if (a === null || b === null) return false;
  return a.reach === b.reach;
}

/** A blanket effective allow rule: one whose reach swallows the whole map. Derived from the fold
 * rather than from an enumerated set of spellings — the old hardcoded trio (`Bash(*)`,
 * `Bash(python3 *)`, `Bash(python3:*)`) missed every other interpreter word the twin folds. */
export function isBlanketAllow(rule, home = os.homedir(), foldRe = null) {
  const c = canonicalize(rule, home, foldRe);
  return c !== null && c.isBlanket;
}

const SCRIPTS_BASENAME_RE = /scripts\/([A-Za-z0-9_.-]+)/;

/** A "ceremony" map entry (AC-DPF-8 rank 2): tier `ask` and its rule body names a scripts/
 * basename — derived structurally, no second source of truth. */
export function isCeremonyEntry(entry) {
  return entry.tier === 'ask' && SCRIPTS_BASENAME_RE.test(ruleBody(entry.rule) || '');
}

export const DRIFT_CLASSES = Object.freeze([
  'blanket-allow',
  'ask-shadowed-ceremony',
  'ask-shadowed',
  'deny-missing',
  // AC-FDC-1. The `ask` tier was the one absence this vocabulary could not name: ask entries were
  // checked ONLY for being shadowed, never for being missing. Measured against a real target
  // a real adopter workspace, the tools reported 46 findings and were silent about 16 more — every
  // ceremony rule gone, with the floor reading clean on that dimension.
  'ask-absent',
  // AC-FDC-2. Every absence test is tier-scoped: it consults only the effective tier matching the
  // map's declared tier. A map rule the operator deliberately placed in ANOTHER tier was therefore
  // reported absent while plainly present — and a consumer acting on that would add a second copy,
  // leaving one capability declared twice under two tiers. Which of the two governs is a
  // precedence question this tooling deliberately does not model (the shipped
  // doctor-permission-floor-check residual R3), so the double declaration is REPORTED, never
  // resolved.
  'tier-conflict',
  'settings-unreadable',
  'stale-plugin-path',
  'allow-absent',
  'unclassified',
]);

/** Build the settings.json object the CLI writes, verbatim from the bundled map plus the
 * marketplace/plugin pins (AC-BCL-4). */
export function buildSettings(map, pins) {
  const byTier = { allow: [], ask: [], deny: [] };
  for (const e of map.entries) {
    byTier[e.tier].push(e.rule);
  }
  // THE PINNED LITERAL — SUPERSEDED (feat-foundry-installer-unpinning, AC-IUP-3). This block used
  // to read (AC-BCL-4(b), contract v1.2 — PR #61 security review Block 1), verbatim:
  //
  //   "THE PINNED LITERAL (AC-BCL-4(b), contract v1.2 — PR #61 security review Block 1). `ref` is
  //   load-bearing, not decoration: without it the adopter's FIRST marketplace resolution floats
  //   to whatever the default branch points at, and because the floor's allow rules are
  //   version-wildcarded (`cache/*/foundry/*/scripts/...`), the operator's single trust acceptance
  //   becomes a standing grant over whatever code that resolution delivered. That is the same
  //   floating-pin defect `autoUpdate: false` guards the LATER fetches against, left open on the
  //   first one — and strictly weaker than the manual path docs/QUICKSTART.md documents (the
  //   documented `marketplace add …#<tag>` install line). Single-sourced from the `foundry` pin
  //   block, so it cannot drift from the marketplace manifest."
  //
  // That reasoning was CORRECT at the time and is not dismissed — it is superseded, on grounds
  // re-derived from the LIVE repository configuration (not a shipped adopter template, which an
  // earlier draft of the supersession wrongly cited and then withdrew), recorded in full in
  // specs/features/foundry/onboarding/installer-unpinning/feat-foundry-installer-unpinning.md
  // ("This SUPERSEDES a prior security-review Block"). Summary of the three grounds:
  //   (i)   Ref integrity does not distinguish "pinned" from "tagless" on THIS repository: `main`
  //         is Tier A (6 required status contexts, enforce_admins, strict) and `refs/tags/v*` is
  //         doubly ruleset-protected (immutable, one ruleset with bypass_actors: []). The Block's
  //         threat model — an index that "floats to whatever the default branch points at" — is
  //         not a tampering threat here; both refs are equally tamper-resistant.
  //   (ii)  The index pin controls CADENCE, not integrity, and that cost is paid three other ways:
  //         AC-IUP-4 makes BOTH installers register with an explicit-or-effective `autoUpdate:
  //         false` (this one keeps writing the literal `false`; the shell installer's Clarified
  //         resolution is that an ABSENT key reads identically, per floorReconcile.mjs's `pinned`
  //         predicate below); the untouched, content-addressed `plugins[].source.sha` in
  //         `.claude-plugin/marketplace.json` fixes exactly what commit is fetched regardless of
  //         which ref the index names; and `.github/workflows/btb-gates-base.yml`'s
  //         `^\.claude-plugin/` security-path rule gates what may enter the catalogue at all.
  //   (iii) The Block's own mechanism never implemented the Block's stated threat: this same
  //         floorReconcile.mjs already classified `ref: "main"` (a branch) as PINNED before this
  //         atom, and `scripts/foundry-bootstrap.sh --channel edge` already shipped exactly that
  //         floating-ref state as a supported, if opt-in, option. And the withholding branch this
  //         Block relied on (`floorReconcile.mjs`'s `withheldAllow`) only runs on the RECONCILE
  //         path against an ALREADY-EXISTING entry — never on this create path, which the Block's
  //         own "the adopter's FIRST marketplace resolution" language describes.
  // AC-IUP-5 is a NECESSARY CONSEQUENCE of removing `ref` here, not a mitigation invented to excuse
  // it: without floorReconcile.mjs's predicate change below, EVERY adopter's reconcile run would
  // silently drop the allow tier. The residual this supersession leaves OPEN, not closed: nothing
  // in this atom bounds what may land on `main` beyond the security-path label — the sibling
  // default-branch catalogue gate (not delivered here) is what closes that gap. Single-sourced
  // from the `foundry` pin block, so `repo` cannot drift from the marketplace manifest.
  const marketplaceEntry = {
    source: { source: 'github', repo: pins.marketplace_repo },
    autoUpdate: false,
  };
  return {
    permissions: { allow: byTier.allow, ask: byTier.ask, deny: byTier.deny },
    extraKnownMarketplaces: { [pins.marketplace_name]: marketplaceEntry },
    enabledPlugins: { [`${pins.plugin_name}@${pins.marketplace_name}`]: true },
  };
}

/** One capability-preview line per bundled-map entry, grouped by tier, carrying rule/tier/
 * rationale (AC-BCL-3). */
export function renderCapabilityLines(map) {
  const lines = [];
  for (const tier of ['allow', 'ask', 'deny']) {
    const entries = map.entries.filter((e) => e.tier === tier);
    if (entries.length === 0) continue;
    lines.push(`  [${tier}] (${entries.length} rules)`);
    for (const e of entries) {
      lines.push(`    - ${e.rule}  — ${e.rationale}`);
    }
  }
  return lines;
}

/** Classify drift between the bundled map and a target's effective permission configuration.
 * `effective` is { allow: [{rule, origin, tierKey}], ask: [...], deny: [...] } drawn from
 * settings.json unioned with settings.local.json (origin-tracked, AC-BCL-8). `readable` names
 * whether each origin file that exists parsed successfully. `pluginRootExpansion` is the list of
 * directories `plugin_root_glob` expanded to on disk (empty => stale-plugin-path). Returns an
 * array of finding objects `{class, ...}` using exactly the AC-DPF-8 vocabulary — no other class
 * name is ever emitted. */
export function classifyDrift(map, effective, { pluginRootExpansion = [], unreadableOrigins = [], home = os.homedir() } = {}) {
  const findings = [];
  const foldRe = foldRegexFromGlob(map.plugin_root_glob);

  for (const origin of unreadableOrigins) {
    findings.push({ class: 'settings-unreadable', origin });
  }

  const shadowedByBlanket = new Set();
  for (const a of effective.allow) {
    if (isBlanketAllow(a.rule, home, foldRe)) {
      const swallowed = map.entries.filter((e) => e.tier !== 'allow');
      findings.push({
        class: 'blanket-allow',
        rule: a.rule,
        // the folded reach the blanket decision was actually made on — the same short form the
        // Python twin renders, so the two name this class identically (AC-FDC-6)
        folded: canonicalize(a.rule, home, foldRe).folded,
        origin: a.origin,
        swallows: swallowed.map((e) => e.rule),
      });
      for (const e of swallowed) shadowedByBlanket.add(e.rule);
    }
  }

  // AC-FDC-2, computed FIRST because it suppresses the absence findings below. A map entry whose
  // exact rule text sits in a tier other than the one the map declares is PRESENT — deliberately
  // placed — so reporting it absent would be false, and would invite a consumer to add a duplicate.
  // Exact text only: `covers()` needs a `:*`-terminated body to match at all, so a cross-tier
  // duplicate of a non-wildcard rule is invisible to the shadowing tests too, which is precisely
  // the gap this class closes.
  const conflicted = new Set();
  for (const entry of map.entries) {
    for (const tierKey of TIERS) {
      if (tierKey === entry.tier) continue;
      const found = effective[tierKey].filter((e) => e.rule === entry.rule);
      if (found.length === 0) continue;
      conflicted.add(entry.rule);
      findings.push({
        class: 'tier-conflict',
        rule: entry.rule,
        declaredTier: entry.tier,
        foundTier: tierKey,
        // AC-FDC-3 — the origin is what lets a consumer separate the tracked settings.json from the
        // untracked settings.local.json, which the effective set unions together.
        origins: found.map((e) => e.origin),
      });
    }
  }

  for (const entry of map.entries.filter((e) => e.tier === 'ask')) {
    if (shadowedByBlanket.has(entry.rule)) continue;
    const coveringAllow = effective.allow.filter((a) => covers(a.rule, entry.rule, home, foldRe));
    if (coveringAllow.length > 0) {
      findings.push({
        class: isCeremonyEntry(entry) ? 'ask-shadowed-ceremony' : 'ask-shadowed',
        rule: entry.rule,
        coveredBy: coveringAllow.map((a) => ({ rule: a.rule, origin: a.origin, tierKey: a.tierKey })),
      });
    }
  }

  for (const entry of map.entries.filter((e) => e.tier === 'deny')) {
    if (conflicted.has(entry.rule)) continue;
    // denyCovers, not covers: the deny direction refuses the fold and requires exact reach
    // equality (AC-DPF-3(b)). Reusing the allow-direction relation here was strictly more
    // permissive than the Python twin — a broad deny would have stood in for a narrower one.
    const coveringDeny = effective.deny.filter((d) => denyCovers(d.rule, entry.rule));
    if (coveringDeny.length === 0) {
      findings.push({ class: 'deny-missing', rule: entry.rule });
    }
  }

  // AC-FDC-1 — the exact mirror of deny-missing above, against the `ask` tier. Note what it does
  // NOT consult: effective.allow. An ask entry covered by a broad allow is already named by
  // ask-shadowed, and whether that allow actually defeats the ask at match time is the precedence
  // question this tooling abstains from — so absence and shadowing are reported as the two
  // independent facts they are, rather than one being folded into the other.
  for (const entry of map.entries.filter((e) => e.tier === 'ask')) {
    if (conflicted.has(entry.rule)) continue;
    const coveringAsk = effective.ask.filter((a) => covers(a.rule, entry.rule, home, foldRe));
    if (coveringAsk.length === 0) {
      findings.push({ class: 'ask-absent', rule: entry.rule });
    }
  }

  if (pluginRootExpansion.length === 0) {
    findings.push({ class: 'stale-plugin-path', glob: map.plugin_root_glob });
  }

  for (const entry of map.entries.filter((e) => e.tier === 'allow')) {
    if (conflicted.has(entry.rule)) continue;
    const coveringAllow = effective.allow.filter((a) => covers(a.rule, entry.rule, home, foldRe));
    if (coveringAllow.length === 0) {
      findings.push({ class: 'allow-absent', rule: entry.rule });
    }
  }

  for (const tierKey of ['allow', 'ask', 'deny']) {
    for (const eff of effective[tierKey]) {
      if (ruleBody(eff.rule) === null) {
        const prefix = (eff.rule.split('(')[0] || '?').replace(/[^A-Za-z0-9_-]/g, '') || '?';
        findings.push({ class: 'unclassified', toolPrefix: prefix.slice(0, 32), origin: eff.origin, tierKey });
      }
    }
  }

  // AC-FDC-7 — QUALIFIED, not suppressed. `covers()` returns false for a body that does not end
  // `:*`, so under `Bash(*)` every map entry still classifies absent and a consumer could converge
  // all 62 rules and report success over a floor that rule defeats entirely. Suppressing the other
  // findings would hide the gap; marking them names both facts at once — here is the gap, and here
  // is why closing it changes nothing until this rule is narrowed.
  const blanketRules = findings.filter((f) => f.class === 'blanket-allow').map((f) => f.rule);
  if (blanketRules.length > 0) {
    for (const f of findings) {
      if (f.class === 'blanket-allow') continue;
      f.qualifiedBy = [...blanketRules];
    }
  }

  return findings;
}
