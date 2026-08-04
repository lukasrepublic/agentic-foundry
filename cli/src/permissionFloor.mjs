// permissionFloor.mjs — loads the bundled map, builds the written settings.json object (a
// verbatim declaration, never a grant — AC-BCL-4), renders the capability preview, and classifies
// drift between the map and a target's effective settings using the AC-DPF-8 vocabulary
// (AC-BCL-8). `covers()` agrees with tests/test_permission_floor_map.py::_subsumes on the shared
// 8-row table by construction (same prefix-subsumption rule).
import fs from 'node:fs';

export function loadMap(mapPath) {
  const text = fs.readFileSync(mapPath, 'utf-8');
  return JSON.parse(text);
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

/** covers(A, B): true if the broad rule A's reach (a `:*`-terminated prefix) subsumes rule B's
 * body. Mirrors tests/test_permission_floor_map.py::_subsumes exactly (same prefix rule). */
export function covers(ruleA, ruleB) {
  const bodyA = ruleBody(ruleA);
  if (bodyA === null || !bodyA.endsWith(':*')) return false;
  const sA = bodyA.slice(0, -2);
  const bodyB = ruleBody(ruleB);
  if (bodyB === null) return false;
  const sB = bodyB.endsWith(':*') ? bodyB.slice(0, -2) : bodyB;
  return sB.startsWith(sA);
}

const BLANKET_BODIES = new Set(['*', 'python3 *', 'python3:*']);

/** A blanket effective allow rule: one whose reach swallows the whole map (AC-DPF-3(a)'s named
 * spellings this atom's own controls (g)(h)(i) exercise: `Bash(*)`, `Bash(python3 *)`,
 * `Bash(python3:*)`). */
export function isBlanketAllow(rule) {
  const body = ruleBody(rule);
  if (body === null) return false;
  const stripped = body.endsWith(':*') ? body.slice(0, -2) : body;
  return BLANKET_BODIES.has(body) || stripped === '' || stripped === '*';
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
  // THE PINNED LITERAL (AC-BCL-4(b), contract v1.2 — PR #61 security review Block 1). `ref` is
  // load-bearing, not decoration: without it the adopter's FIRST marketplace resolution floats to
  // whatever the default branch points at, and because the floor's allow rules are
  // version-wildcarded (`cache/*/foundry/*/scripts/...`), the operator's single trust acceptance
  // becomes a standing grant over whatever code that resolution delivered. That is the same
  // floating-pin defect `autoUpdate: false` guards the LATER fetches against, left open on the
  // first one — and strictly weaker than the manual path docs/QUICKSTART.md documents
  // (the documented `marketplace add …#<tag>` install line). Single-sourced from
  // the `foundry` pin block, so it cannot drift from the marketplace manifest.
  const marketplaceEntry = {
    source: { source: 'github', repo: pins.marketplace_repo, ref: `v${pins.plugin_version}` },
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
export function classifyDrift(map, effective, { pluginRootExpansion = [], unreadableOrigins = [] } = {}) {
  const findings = [];

  for (const origin of unreadableOrigins) {
    findings.push({ class: 'settings-unreadable', origin });
  }

  const shadowedByBlanket = new Set();
  for (const a of effective.allow) {
    if (isBlanketAllow(a.rule)) {
      const swallowed = map.entries.filter((e) => e.tier !== 'allow');
      findings.push({
        class: 'blanket-allow',
        rule: a.rule,
        origin: a.origin,
        swallows: swallowed.map((e) => e.rule),
      });
      for (const e of swallowed) shadowedByBlanket.add(e.rule);
    }
  }

  for (const entry of map.entries.filter((e) => e.tier === 'ask')) {
    if (shadowedByBlanket.has(entry.rule)) continue;
    const coveringAllow = effective.allow.filter((a) => covers(a.rule, entry.rule));
    if (coveringAllow.length > 0) {
      findings.push({
        class: isCeremonyEntry(entry) ? 'ask-shadowed-ceremony' : 'ask-shadowed',
        rule: entry.rule,
        coveredBy: coveringAllow.map((a) => ({ rule: a.rule, origin: a.origin, tierKey: a.tierKey })),
      });
    }
  }

  for (const entry of map.entries.filter((e) => e.tier === 'deny')) {
    const coveringDeny = effective.deny.filter((d) => covers(d.rule, entry.rule) || d.rule === entry.rule);
    if (coveringDeny.length === 0) {
      findings.push({ class: 'deny-missing', rule: entry.rule });
    }
  }

  if (pluginRootExpansion.length === 0) {
    findings.push({ class: 'stale-plugin-path', glob: map.plugin_root_glob });
  }

  for (const entry of map.entries.filter((e) => e.tier === 'allow')) {
    const coveringAllow = effective.allow.filter((a) => covers(a.rule, entry.rule) || a.rule === entry.rule);
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

  return findings;
}
