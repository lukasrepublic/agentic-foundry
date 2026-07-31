export const meta = {
  name: 'foundry-spec-audit',
  description: 'Adversarial §8 spec audit on the native Workflow tool — a PHASED remediate-between-passes engine (guarded auto-default): one PASS = an ordered sequential sweep prior-art → requirement-quality → steel-man → adversarial → red-team, each phase AUDIT → REMEDIATE → next, with a DETERMINISTIC control-plane (ledger, verify-after-correct on the NEXT sweep, append-only normative integrity + snapshot/rollback, confined reviser allowlist, blind re-verifying critic, severity pinning, fail-safe blast-radius escalation, draft-only precondition) and injectable LLM adapters. Layered termini CONVERGED | MAX_PASS(10)-REACHED | NEEDS-REGROUND | NEEDS-OPERATOR.',
  phases: [{ title: 'Audit', detail: 'ordered phased sweep, remediate-between, looped to a layered terminus' }],
}

// GUARDED-AUTO CONTRACT (supersedes the prior header that framed this workflow as audit-only with a
// downstream-applied remediation step).
// ----------------------------------------------------------------------------------------------
// This workflow is NOT audit-only and remediation is NOT a downstream step: it runs the §8 audit as a
// PHASED remediate-between-passes engine whose DEFAULT mode is GUARDED-AUTO. A PASS is ONE ordered,
// sequential sweep through the five phases (prior-art → requirement-quality → steel-man → adversarial
// → red-team); after each phase's findings a SEPARATE fresh-context reviser (critic != reviser) edits
// ONLY the target spec, append-only, under the foundry-cwd-enforce.sh write-jail. Every guard is
// DETERMINISTIC orchestrator logic (NOT model judgment) — an unguarded auto loop is reward-hackable
// (the §8 audits of this very spec proved both gross exploits and subtler guard bypasses):
//   - verify-after-correct on the NEXT sweep (a fix is `pending-fix`, promoted to `fixed-verified`
//     only after a later sweep's INDEPENDENT BLIND critic does not re-raise it);
//   - append-only normative integrity (modify/shrink/delimiter-move/dup-AC-ID/foreign-write => rollback
//     from a whole-tree pre-edit snapshot => NEEDS-OPERATOR);
//   - confined reviser (allowlist = the target spec only);
//   - critic-owned + pinned severity (no severity-laundering); no auto self-accept of Crit/High/Med;
//   - reopen-on-re-raise (incl. accepted-residual); blind critic status filter;
//   - fail-safe blast-radius escalation over BOTH findings AND the reviser diff (ambiguous => escalate);
//   - draft-only precondition (refuse a spec whose sibling acceptance-contract.yaml carries the trailer).
// The control-plane below is deterministic + node-executable + agent-INJECTABLE: the critic / reviser /
// matcher are injected adapters (production wraps agent(); the live-seam injects STUBS). The operator
// reviews the full auto-remediation diff downstream at /foundry:authorize (the human root of trust);
// auto-remediation bypasses nothing. Operator-STEER (dispositioning the NEEDS-OPERATOR halt + Crit/High/
// Med residuals + existing-normative-text changes) is the SIBLING atom.

// Shared REPORTING RULE — appended to every phase. A phase reports ONLY genuine gaps; affirmations are
// NOT findings. Load-bearing for convergence: a phase that emits its positive verdict as a finding would
// keep a blocking class non-zero forever and force a false MAX_PASS. A clean phase returns an EMPTY array.
const REPORTING_RULE = ` REPORTING RULE (load-bearing): report ONLY genuine, located gaps/defects/concerns as findings. Do NOT emit affirmations, restatements, summaries, or a positive verdict (e.g. "ALIGNED", "no issue", "looks correct", "no new mechanism") as a finding — if your phase finds nothing, return an EMPTY findings array. Reserve "Info" severity strictly for genuinely non-actionable notes (Info never blocks convergence). Never invent a finding to look thorough.`

// ORDERED, SEQUENTIAL phases (replaces the parallel LENSES fan-out). The order is FIXED and canonical:
// prior-art FIRST (a category-error halts before downstream effort), red-team LAST (on the most-hardened
// artifact). Each phase carries the SAME lens prompt it had + the shared ${REPORTING_RULE} marker.
const PHASES = [
  // PRIOR-ART (1st — research-first criteria embedded inline; the workflow has no skill-load): is this the
  // approach the best shops use, or are we building what the industry does not build (a CATEGORY ERROR)?
  { key: 'prior-art', prompt: (t) => `PRIOR-ART lens on ${t}: research-first — is the target's approach the one the best agentic-engineering / platform / domain shops actually use? Compare against the established industry standard for this problem (the proven pattern, the dominant design). If the approach is novel / non-standard, is the novelty RESEARCH-JUSTIFIED (a deliberate, defended departure), or is it a CATEGORY ERROR — building what the industry does not build, re-inventing a solved problem with a bespoke mechanism? A category-error finding is class:"category-error" and DOMINATES the implementation findings: it is resolved by RE-GROUNDING on the standard (research-first -> adopt the standard pattern -> re-spec), NOT by patching the bespoke approach. For any category-error name the industry-standard approach the target should re-ground on. If the approach IS aligned with the industry standard, that is the CLEAN case — return an EMPTY findings array; do NOT emit the "ALIGNED" verdict as a finding.${REPORTING_RULE}` },
  // REQUIREMENT-QUALITY (2nd — ADVISORY; the inverse of the live-seam merge gate). Grades the PROSE QUALITY
  // of each requirement, NOT whether the implementation works. Its findings ride a distinct
  // `requirement-quality` class that the convergence predicate NEVER gates on (advisory, no merge authority).
  { key: 'requirement-quality', prompt: (t) => `REQUIREMENT-QUALITY lens on ${t}: score EACH acceptance criterion in the spec's \`<!-- normative -->\` region against this small fixed rubric — COMPLETENESS (no missing precondition, edge case, or output), CLARITY (no ambiguity, no undefined term), MEASURABILITY (a concrete, testable pass condition — flag vague adjectives such as "gracefully" / "correctly" / "robust" / "handles errors" that lack a measurable criterion, and unresolved placeholders such as TBD / TODO / ???), CONSISTENCY (no contradiction with another AC or the spec body). BOUNDARY (load-bearing, verbatim): evaluate the requirement, not the code. You are FORBIDDEN from grading implementation behavior, runtime output, or whether the system "displays correctly" / "works correctly" — that is the live-seam merge gate's job, and crossing this boundary would duplicate the gate. Emit each finding with class:"requirement-quality" and a \`location\` that names the AC-ID it concerns (e.g. "AC-RQ-2"), so the finding is traceable to the exact requirement it sharpens. This lens is ADVISORY: it sharpens the spec pre-authorize; it NEVER blocks plateau and adds NO merge authority.${REPORTING_RULE}` },
  // STEEL-MAN (3rd) — assume the design is right; find where it is under-specified.
  { key: 'steelman', prompt: (t) => `STEEL-MAN lens on ${t}: assume the design is right and find where it is UNDER-specified — missing mechanisms, unstated preconditions, gaps a faithful implementer would hit. Report concrete, located findings.${REPORTING_RULE}` },
  // ADVERSARIAL (4th) — assume an adversary; find vacuous-pass holes, fail-open defaults.
  { key: 'adversarial', prompt: (t) => `ADVERSARIAL lens on ${t}: assume an adversary will exploit it — find vacuous-pass holes, fail-open defaults, unguarded edges, claims asserted-not-demonstrated. Report concrete, located findings with the exploit.${REPORTING_RULE}` },
  // RED-TEAM (5th, LAST — on the most-hardened artifact) — attack the security/trust floor.
  { key: 'redteam', prompt: (t) => `RED-TEAM lens on ${t}: attack the security/trust floor — auth/identity/secrets/merge-authority/privilege escalation, forgeable evidence, co-residence. Report concrete, located findings with impact.${REPORTING_RULE}` },
]

// The canonical phase order, machine-assertable by the drop-in check (AC-APR-1). Atom B (#120,
// AC-ASG-1) prepends 'system-grounding' as Pass-0 — the reality-oracle phase, evaluated BEFORE
// prior-art (a live-system contradiction halts before any downstream audit effort, mirroring why
// prior-art itself runs first). 'system-grounding' has NO entry in the PHASES prompt array above
// (it is deterministic, never an agent() critic — see the dispatch loop in runEngine, AC-ASG-3).
const PHASE_ORDER = ['system-grounding', 'prior-art', 'requirement-quality', 'steelman', 'adversarial', 'redteam']

const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'severity', 'class'],
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['Critical', 'High', 'Medium', 'Low', 'Info'] },
          // finding-CLASS drives convergence judgment. `category-error` (the prior-art phase) is a
          // first-class class that DOMINATES: any open one HALTS the run (NEEDS-REGROUND).
          // `requirement-quality` is an ADVISORY class: traceable + reported but NEVER gated on.
          // `reality-divergence` (the system-grounding phase, Atom B #120, AC-ASG-1) is a SECOND
          // dominant class that mirrors `category-error` exactly (severity-independent HALT to
          // NEEDS-REGROUND; see the dispatch loop + isConvergedSweep below).
          class: { type: 'string', enum: ['new-design', 'epistemic', 'residual', 'meta-regress', 'category-error', 'requirement-quality', 'reality-divergence'] },
          location: { type: 'string' },
          detail: { type: 'string' },
        },
      },
    },
  },
}

// Hard cap (AC-APR-4): the loop never exceeds 10 passes (scale ceiling); the MAX_PASS terminus names it.
const MAX_PASS_CAP = 10

// ============================================================================================
// DETERMINISTIC CONTROL-PLANE — plain functions/objects, NO agent() calls inside. Everything here
// is node-executable + machine-testable; the LLM touchpoints are the INJECTED adapters only.
// ============================================================================================

const SEVERITY_RANK = { Critical: 5, High: 4, Medium: 3, Low: 2, Info: 1 }
const BLOCKING = new Set(['Critical', 'High', 'Medium'])

function isBlockingSeverity(sev) { return BLOCKING.has(sev) }
function higherSeverity(a, b) { return (SEVERITY_RANK[a] || 0) >= (SEVERITY_RANK[b] || 0) ? a : b }

// Ledger identity = finding CLASS + LOCATION (not the title — a paraphrased title must not mint a twin).
function findingKey(f) { return `${f.class}::${f.location || ''}` }

// Fail-safe blast-radius classifier: security/auth/secrets/supply-chain. Evaluated over a finding's text
// AND over the reviser's diff. Ambiguous => escalate (an explicit `blast: 'ambiguous'` hint, or the
// keyword set). Over-escalation is the tuned-for cost; under-escalation is the hazard.
const _SECURITY_RE = /\b(auth|authn|authz|authenticat\w*|authoriz\w*|secret|credential|password|passwd|token|api[- ]?key|private[- ]?key|iam|rbac|privilege|escalat\w*|supply[- ]?chain|dependenc\w*|signing|sigstore|in-toto|slsa)\b/i

function blastRadius(text, hint) {
  if (hint === 'ambiguous') return true            // fail-safe: ambiguous => escalate
  if (typeof text !== 'string') return true        // fail-safe: cannot classify => escalate
  return _SECURITY_RE.test(text)
}

// ============================================================================================
// RISK-TIERED CEREMONY + PER-CRITIC WATCHDOG + FINDINGS-CARRY (feat-foundry-audit-tier-caps,
// AC-ATC-1..6). Named `riskTier` throughout — deliberately DISTINCT from `auditModel`'s own
// "§8 audit tier: critic + reviser on '<alias>'" narrator line (feat-foundry-audit-model-escalation),
// an unrelated pre-existing concept this atom does not touch or rename.
// ============================================================================================
const VALID_RISK_TIERS = ['T0', 'T1', 'T2', 'T3']
// T3 is excluded here — its cap is the existing MAX_PASS(maxPass) computation (unchanged), never a
// fixed hard number; T0/T1/T2 are HARD caps independent of `maxPass` (AC-ATC-2).
const TIER_ROUND_CAP = { T0: 0, T1: 1, T2: 2 }

function resolveRiskTier(requested) {
  // AC-ATC-1/-2 fail-closed: an unrecognized/absent risk tier resolves to the fullest ceremony, T3.
  return VALID_RISK_TIERS.includes(requested) ? requested : 'T3'
}

// Per-critic watchdog defaults (AC-ATC-4): corpus-derived from the measured yield curve (healthy
// critics complete in single-digit minutes; the named runaways burned ~30 min per dead attempt, up to
// 4.75h). Configurable via opts.watchdog; either bound may be overridden independently.
const WATCHDOG_DEFAULTS = { tokenLimit: 200000, msLimit: 10 * 60 * 1000 }

function resolveWatchdog(w) {
  const tokenLimit = (w && Number.isFinite(w.tokenLimit) && w.tokenLimit > 0) ? w.tokenLimit : WATCHDOG_DEFAULTS.tokenLimit
  const msLimit = (w && Number.isFinite(w.msLimit) && w.msLimit > 0) ? w.msLimit : WATCHDOG_DEFAULTS.msLimit
  return { tokenLimit, msLimit }
}

// A per-critic watchdog (AC-ATC-4/-5): races the injected critic call against a wall-time threshold via
// `setTimeout` + `Promise.race` — NEVER a read of the current wall-clock time (the native Workflow
// runtime forbids that class of call as a resume-journal hazard, AC-AWD-1's blanket ban over this whole
// file — `Promise.race` only needs to know WHICH promise settles first, never an elapsed duration) —
// and checks a returned `outputTokens` count against a token threshold when the adapter supplies one
// (the mechanism is wired + configurable; the production agent() wrapper does not currently surface a
// real token count, so that arm activates once/if a caller supplies `outputTokens` — never a fabricated
// metric). The adapter MAY also self-report a death directly (`{ killed: true, kill_reason }`) for a
// `max_output_tokens` truncation / session-usage-limit exhaustion the production wrapper detects; a
// plain rejection defaults to `kill_reason: 'error'` (the ledger-taxonomy 3-value enum: watchdog |
// limit | error).
async function withWatchdog(fn, watchdog) {
  const { tokenLimit, msLimit } = watchdog
  let timer = null
  const timeoutP = new Promise((resolve) => {
    timer = setTimeout(() => resolve({ __timeout: true }), msLimit)
  })
  const invokedP = (async () => {
    try {
      const r = await fn()
      return { __ok: true, r }
    } catch (e) {
      return { __err: true, e }
    }
  })()
  const outcome = await Promise.race([invokedP, timeoutP])
  if (timer) clearTimeout(timer)
  if (outcome.__timeout) {
    return { killed: true, kill_reason: 'watchdog', detail: `wall-time exceeded ${msLimit}ms` }
  }
  if (outcome.__err) {
    const e = outcome.e
    return { killed: true, kill_reason: (e && e.kill_reason) || 'error', detail: String((e && e.message) || e) }
  }
  const r = outcome.r
  if (r && r.killed === true) {
    return { killed: true, kill_reason: r.kill_reason || 'watchdog', detail: r.detail }
  }
  const outputTokens = (r && typeof r.outputTokens === 'number') ? r.outputTokens : 0
  if (outputTokens > tokenLimit) {
    return { killed: true, kill_reason: 'watchdog', outputTokens, detail: `output tokens ${outputTokens} exceeded ${tokenLimit}` }
  }
  return { killed: false, findings: (r && r.findings) || [], outputTokens }
}

// findings-carry (AC-ATC-6): new/resolved/still-open deltas against a SEEDED prior-run ledger — the
// ledger-taxonomy row's own `findings: {new, resolved, open}` shape (SARIF-baseline-diffing
// incremental-analysis memoization). `new` = a key absent from the seed (raised fresh this run, any
// status); `resolved` = a SEEDED key that WAS open/pending-fix and is NOT any more; `open` = every key
// still open/pending-fix now (seeded or new) — three independent counts, not a strict partition
// (mirrors the shipped row schema, where a same-run new-and-resolved entry is representable).
function findingsDelta(seedLedger, finalLedger) {
  const seedKeys = new Set((seedLedger || []).map(findingKey))
  const seedByKey = {}
  for (const e of (seedLedger || [])) seedByKey[findingKey(e)] = e
  let fresh = 0
  let openNow = 0
  for (const [key, e] of Object.entries(finalLedger)) {
    const isOpen = e.status === 'open' || e.status === 'pending-fix'
    if (!seedKeys.has(key)) fresh++
    if (isOpen) openNow++
  }
  let resolved = 0
  for (const [key, seed] of Object.entries(seedByKey)) {
    const wasOpen = seed.status === 'open' || seed.status === 'pending-fix'
    const final = finalLedger[key]
    const isOpenNow = final && (final.status === 'open' || final.status === 'pending-fix')
    if (wasOpen && !isOpenNow) resolved++
  }
  return { new: fresh, resolved, open: openNow }
}

// The diff a reviser edit introduces = the lines present in newText but not in oldText (append-only delta).
function addedText(oldText, newText) {
  const oldLines = new Set(String(oldText).split('\n'))
  return String(newText).split('\n').filter((l) => !oldLines.has(l)).join('\n')
}

// Normative-region extraction + the append-only normative-integrity guard.
function normativeRegion(text) {
  const m = String(text).match(/<!--\s*normative\s*-->([\s\S]*?)<!--\s*\/normative\s*-->/)
  return m ? m[1] : null
}
function acIds(regionText) {
  return (String(regionText).match(/AC-[A-Z0-9]+(?:-[A-Z0-9]+)*/g) || [])
}
// Returns { ok, reason }. A reviser may APPEND new ACs/clarifications, but must NOT modify or weaken
// existing normative text, shrink the AC-ID set, move a delimiter, or duplicate an AC-ID.
function normativeIntegrity(oldText, newText) {
  const oldRegion = normativeRegion(oldText)
  const newRegion = normativeRegion(newText)
  if (oldRegion === null) return { ok: true, reason: 'no normative region in base (nothing to protect)' }
  if (newRegion === null) return { ok: false, reason: 'normative delimiter removed/moved' }
  // Append-only: the entire existing normative content must survive verbatim as a substring of the new
  // region (any in-place modification/weakening breaks the substring; appends extend it).
  if (!newRegion.includes(oldRegion.replace(/\s+$/, '')) && !newRegion.includes(oldRegion)) {
    return { ok: false, reason: 'existing normative text modified/weakened (not append-only)' }
  }
  const oldIds = acIds(oldRegion)
  const newIds = acIds(newRegion)
  for (const id of new Set(oldIds)) {
    if (!newIds.includes(id)) return { ok: false, reason: `normative AC-ID set shrank (lost ${id})` }
  }
  const counts = {}
  for (const id of newIds) counts[id] = (counts[id] || 0) + 1
  for (const [id, n] of Object.entries(counts)) {
    if (n > (oldIds.filter((x) => x === id).length || 0) + 0 && n > 1 && !oldIds.includes(id)) {
      return { ok: false, reason: `duplicate AC-ID introduced (${id})` }
    }
    if (n > 1 && oldIds.filter((x) => x === id).length <= 1) {
      return { ok: false, reason: `duplicate AC-ID introduced (${id})` }
    }
  }
  return { ok: true, reason: 'append-only normative integrity preserved' }
}

// ============================================================================================
// REMEDIATION MODEL (feat-foundry-audit-remediation-model, AC-ARM-1..6): propose-diff -> blind
// verify -> anchored apply. Replaces the old append-only "hand back the whole document" reviser
// contract with a BOUNDED DIFF PROPOSAL against the round's anchored base text, gated by a
// fresh-context blind verifier, applied IN PLACE with a one-line changelog entry. A `{ newText }`
// reviser reply (no `.proposal`) is a LEGACY shape kept for exact backward-compat byte-for-byte
// (the append-only floors below, `normativeIntegrity` included, are UNCHANGED for that shape); the
// NEW `{ proposal }` shape is what the production reviser adapter emits (see `adapters.reviser`
// near the bottom of this file) and is what AC-ARM-1..6 govern.
// ----------------------------------------------------------------------------------------------
// Diff-proposal shape: { findingsAddressed: [key,...], findingsDeferred: [key,...],
//   edits: [{ span: <AC-ID this edit targets>, find: <exact substring within that AC-block>,
//             replace: <its in-place replacement> }], changelog: <one-line summary> }
// NEVER a full-document rewrite (no bare `newText`) and NEVER an appended amendment/reground block
// (an edit only ever replaces text WITHIN an existing AC-ID's own block bounds).
const _DELIM_RE = /<!--\s*\/?normative\s*-->/

// Locate the `<!-- normative --> ... <!-- /normative -->` inner-text bounds (absolute offsets into
// `text`), so an edit's span can be resolved + spliced back precisely (never a regex-replace over
// the whole document, which could touch an unrelated match elsewhere).
function normativeBounds(text) {
  const s = String(text)
  const om = /<!--\s*normative\s*-->/.exec(s)
  if (!om) return null
  const innerStart = om.index + om[0].length
  const cm = /<!--\s*\/normative\s*-->/.exec(s.slice(innerStart))
  if (!cm) return null
  return { innerStart, innerEnd: innerStart + cm.index, inner: s.slice(innerStart, innerStart + cm.index) }
}

// feat-foundry-audit-span-locator-coverage (AC-ASL-1..8, ER #190): a critic's finding `location` is
// routinely COMPOUND / parenthetical / multi-section — `§3.5 (crash recovery)`,
// `§3.4 (…) / §3.6 (…)` — never a single exact block key. `locationComponents` tokenizes ANY such
// string (a bare token included — it is then a 1-element list, byte-identical prior behavior) into its
// component section references: split on the ` / ` multi-section separator — PAREN-DEPTH-AWARE, so a
// `/` INSIDE a parenthetical qualifier (e.g. `§3.4 (I/O consistency)`) is never a split point — then
// strip each component's own trailing parenthetical `(…)` qualifier (nested-parens aware: the qualifier
// stripped is the BALANCED group anchored to the component's own end, never a naive first-`)` match).
// Reused by both span guards below so tokenization is defined exactly once (AC-ASL-1).
function _splitTopLevel(s) {
  const parts = []
  let depth = 0
  let cur = ''
  for (const ch of s) {
    if (ch === '(') depth++
    else if (ch === ')') depth = Math.max(0, depth - 1)
    if (ch === '/' && depth === 0) { parts.push(cur); cur = '' } else { cur += ch }
  }
  parts.push(cur)
  return parts
}
function _stripTrailingParenthetical(s) {
  const t = String(s).trim()
  if (!t.endsWith(')')) return t
  let depth = 0
  for (let i = t.length - 1; i >= 0; i--) {
    const ch = t[i]
    if (ch === ')') depth++
    else if (ch === '(') {
      depth--
      if (depth === 0) return t.slice(0, i).trimEnd()
    }
  }
  return t   // unbalanced parens — fail-safe: never mangle, leave as-is.
}
function locationComponents(location) {
  return _splitTopLevel(String(location == null ? '' : location))
    .map((s) => _stripTrailingParenthetical(s).trim())
    .filter((s) => s.length > 0)
}

// AC-ASL-7: resolve a `§N.M` section-heading component token to the section's block — the span from
// the matched `§N.M` HEADING line to the next heading of the SAME-OR-HIGHER level (or end of the
// region). The marker is ANCHORED to the heading's OWN leading number (immediately after the ATX
// `#`..`######` hashes + whitespace) — never a substring match anywhere in the heading line — so a
// DECOY heading whose title merely mentions another section in prose (e.g. `## Overview (see §3.5)`,
// which does NOT itself lead with `§3.5`) can never be mistaken for the real `### §3.5 …` heading.
// `token` is already a normalized `locationComponents` output (parenthetical-free).
function _sectionHeadingNum(token) {
  const m = /^§(\d+(?:\.\d+)*)$/.exec(String(token).trim())
  return m ? m[1] : null
}
function sectionHeadingBounds(innerText, token) {
  const num = _sectionHeadingNum(token)
  if (!num) return null
  const esc = num.replace(/\./g, '\\.')
  const markerRe = new RegExp('^#{1,6}[ \\t]+§' + esc + '(?!\\.?\\d)')
  const headingRe = /^(#{1,6})[ \t]+.*$/gm
  const heads = []
  let m
  while ((m = headingRe.exec(innerText))) heads.push({ level: m[1].length, idx: m.index, line: m[0] })
  const hi = heads.findIndex((h) => markerRe.test(h.line))
  if (hi === -1) return null
  const level = heads[hi].level
  let end = innerText.length
  for (let j = hi + 1; j < heads.length; j++) {
    if (heads[j].level <= level) { end = heads[j].idx; break }
  }
  return { begin: heads[hi].idx, end }
}

// Locate a single component token's block bounds (offsets into `innerText`). A `§N.M` token resolves
// via `sectionHeadingBounds` (AC-ASL-7); otherwise the v1 grammar — a `- **AC-ID**` bullet marker up to
// the next bullet marker (or end of the normative region) — is unchanged (see the spec's Residuals
// note: block granularity, never a sub-block character range, is preserved either way).
function acBlockBounds(innerText, acId) {
  if (typeof acId === 'string' && acId.trim().startsWith('§')) {
    return sectionHeadingBounds(innerText, acId.trim())
  }
  const re = /(^|\n)(-\s+\*\*([A-Z][A-Z0-9-]*)\*\*)/g
  const starts = []
  let m
  while ((m = re.exec(innerText))) starts.push({ id: m[3], idx: m.index + m[1].length })
  const i = starts.findIndex((x) => x.id === acId)
  if (i === -1) return null
  return { begin: starts[i].idx, end: (i + 1 < starts.length) ? starts[i + 1].idx : innerText.length }
}

// Anchored apply: splice each edit's `replace` in for its `find` — located EXACTLY ONCE within the
// declared span's OWN block bounds (never a document-wide replace) — against the round's base text.
// Fails closed (ok:false) on anything unlocatable/ambiguous/malformed; never partially applies.
function applyProposal(baseText, proposal) {
  const bounds = normativeBounds(baseText)
  if (!bounds) return { ok: false, reason: 'no normative region in base' }
  const edits = (proposal && proposal.edits) || []
  if (!edits.length) return { ok: false, reason: 'empty proposal (no edits) — not a valid diff' }
  let inner = bounds.inner
  const touchedSpans = new Set()
  for (const e of edits) {
    if (!e || typeof e.span !== 'string' || typeof e.find !== 'string' || typeof e.replace !== 'string') {
      return { ok: false, reason: 'malformed edit (span/find/replace required)' }
    }
    if (_DELIM_RE.test(e.find) || _DELIM_RE.test(e.replace)) {
      return { ok: false, reason: 'edit touches the normative delimiter (rejected)' }
    }
    // AC-ASL-1 / AC-ASL-3: tokenize a compound/parenthetical/multi-section `span` into its component
    // section refs and resolve EACH to a block; the edit's permitted region is the UNION of every
    // component that resolves (a bare, non-compound span tokenizes to itself — byte-identical prior
    // behavior for the v1 single-AC-ID case).
    const tokens = locationComponents(e.span)
    const blocks = []
    for (const tok of tokens) {
      const blk = acBlockBounds(inner, tok)
      if (blk) blocks.push(blk)
    }
    if (!blocks.length) return { ok: false, reason: `edit span not located: ${e.span}` }
    let target = null
    let totalOcc = 0
    for (const blk of blocks) {
      const t = inner.slice(blk.begin, blk.end)
      const occ = e.find ? t.split(e.find).length - 1 : 0
      if (occ > 0) { target = blk; totalOcc += occ }
    }
    if (totalOcc !== 1) {
      return { ok: false, reason: `edit.find not located exactly once within span ${e.span} (occurrences=${totalOcc})` }
    }
    const blockText = inner.slice(target.begin, target.end)
    inner = inner.slice(0, target.begin) + blockText.replace(e.find, e.replace) + inner.slice(target.end)
    touchedSpans.add(e.span)
    for (const tok of tokens) touchedSpans.add(tok)
  }
  return { ok: true, newText: baseText.slice(0, bounds.innerStart) + inner + baseText.slice(bounds.innerEnd), touchedSpans }
}

// AC-ARM-4 (defense-in-depth, span-scoped successor of the append-only `normativeIntegrity` for the
// NEW diff shape): delimiters intact, the AC-ID set unchanged (no shrink/dup/foreign AC-ID), and
// every AC-block OUTSIDE the proposal's actually-touched spans is BYTE-IDENTICAL to the anchored
// base — computed structurally (never trusting the reviser's self-declared `edit.span` alone).
function spanScopedIntegrity(oldText, newText, touchedSpans) {
  const oldBounds = normativeBounds(oldText)
  const newBounds = normativeBounds(newText)
  if (!oldBounds) return { ok: true, reason: 'no normative region in base (nothing to protect)' }
  if (!newBounds) return { ok: false, reason: 'normative delimiter removed/moved' }
  const oldIds = acIds(oldBounds.inner)
  const newIds = acIds(newBounds.inner)
  const oldSet = new Set(oldIds)
  for (const id of oldSet) {
    if (!newIds.includes(id)) return { ok: false, reason: `normative AC-ID set shrank (lost ${id})` }
  }
  if (newIds.length !== oldIds.length) {
    return { ok: false, reason: 'normative AC-ID count changed (duplicate/foreign AC-ID introduced)' }
  }
  for (const id of oldSet) {
    if (touchedSpans && touchedSpans.has(id)) continue
    const ob = acBlockBounds(oldBounds.inner, id)
    const nb = acBlockBounds(newBounds.inner, id)
    if (!ob || !nb || oldBounds.inner.slice(ob.begin, ob.end) !== newBounds.inner.slice(nb.begin, nb.end)) {
      return { ok: false, reason: `out-of-span normative delta: untouched block ${id} changed` }
    }
  }
  return { ok: true, reason: 'span-scoped normative integrity preserved' }
}

// AC-ARM-2: the fresh-context BLIND VERIFIER. `adapters.verifier` is an INJECTED adapter — production
// wraps a FRESH-CONTEXT agent() call (reusing operator-steer's mechanized reviser-check dispatch
// shape; see `adapters.verifier` below), the live-seam stubs it deterministically — and it receives
// ONLY the base text + the proposed edits + the named finding's IDENTITY (class/location/severity/
// title): NEVER the reviser's reasoning/detail. It judges (a) finding-resolution. (b) the no-
// out-of-span-delta guard is DETERMINISTIC engine logic below — never delegated to model judgment,
// matching this engine's existing guard philosophy — checked declaratively here (every edit's `span`
// must equal the named finding's OWN location) and again structurally post-apply (spanScopedIntegrity).
async function blindVerifyProposal(adapters, before, proposal, finding) {
  const key = findingKey(finding)
  const addressed = new Set((proposal && proposal.findingsAddressed) || [])
  const deferred = new Set((proposal && proposal.findingsDeferred) || [])
  if (!addressed.has(key) && !deferred.has(key)) {
    return { ok: false, reason: `named finding ${key} neither addressed nor deferred` }
  }
  if (deferred.has(key) && !addressed.has(key)) return { ok: true, deferred: true }
  if (proposal && proposal.newText !== undefined) {
    return { ok: false, reason: 'proposal smuggles a full-document newText — not a bounded diff' }
  }
  const edits = (proposal && proposal.edits) || []
  if (!edits.length) return { ok: false, reason: 'proposal claims resolution with zero edits' }
  // AC-ASL-2 / AC-ASL-4: the region-membership test. A finding's `location` may be compound /
  // parenthetical / multi-section (`§3.4 (…) / §3.6 (…)`); the guard compares the edit's `span` against
  // the NORMALIZED component tokens produced by `locationComponents` (separator-split, parenthetical-
  // stripped) — NEVER the raw pre-strip `location` substring. An edit's `span` is accepted iff EVERY
  // token it tokenizes to is a member of the named finding's own component set (so a bare single
  // component, e.g. '§3.5', or the full raw compound string, or any subset thereof, is in-region) — an
  // edit resolving to a section/AC-block that is NOT one of the finding's own cited components (an
  // UNcited section) is still rejected: the acceptable set widens to exactly the location's own
  // components and nothing else, never the whole document (the load-bearing negative invariant).
  const namedSpan = finding.location
  const namedComponents = locationComponents(namedSpan)
  for (const e of edits) {
    const editComponents = e ? locationComponents(e.span) : []
    const allCited = editComponents.length > 0 && editComponents.every((c) => namedComponents.includes(c))
    if (!allCited) {
      return { ok: false, reason: `out-of-span delta: edit targets '${e && e.span}', outside the named finding's span '${namedSpan}'` }
    }
  }
  const blindFindingView = { class: finding.class, location: finding.location, severity: finding.severity, title: finding.title }
  const verdict = adapters.verifier ? ((await adapters.verifier(before, edits, blindFindingView)) || {}) : { resolved: true }
  if (!verdict.resolved) {
    return { ok: false, reason: `blind verifier: finding not resolved${verdict.reason ? ' — ' + verdict.reason : ''}` }
  }
  return { ok: true, deferred: false }
}

// AC-ARM-3: the deterministic, ENGINE-authored one-line changelog entry (never reviser-authored free
// text spliced into the document) naming the round + the resolved finding — inserted OUTSIDE the
// normative region, never an appended amendment/reground BLOCK.
function changelogLine(pass, finding, summary) {
  const key = findingKey(finding)
  const oneLine = String(summary || finding.title || key).replace(/\s+/g, ' ').trim()
  return `- remediation (pass ${pass}) — resolved ${key}: ${oneLine}`
}
function appendChangelogLine(text, line) {
  const s = String(text)
  const m = /^##\s+Changelog\s*\n/m.exec(s)
  if (m) {
    let at = m.index + m[0].length
    if (s.slice(at, at + 1) === '\n') at += 1
    return s.slice(0, at) + line + '\n' + s.slice(at)
  }
  return s + (s.endsWith('\n') ? '' : '\n') + '\n## Changelog\n\n' + line + '\n'
}

// The BLIND, status-filtered critic view: suppress ONLY fixed-verified + accepted-residual entries.
// `open` and `pending-fix` remain re-auditable and are NOT labeled "fixed" (no priming). The reviser
// gets the FULL ledger; the critic gets this blind view.
function blindView(ledger) {
  return Object.values(ledger)
    .filter((e) => e.status === 'open' || e.status === 'pending-fix')
    .map((e) => ({ class: e.class, location: e.location, severity: e.severity }))   // NO status field => blind
}
function fullLedger(ledger) {
  return Object.values(ledger).map((e) => ({ ...e }))
}

// Independent matcher: class+location identity by default; the (non-reviser) matcher adapter adjudicates
// an ambiguous cross-location match. Fail-safe: an ambiguous match is the SAME open finding at the HIGHER
// severity; a fresh finding is NEVER absorbed into a lower-severity fixed-verified/accepted-residual.
function matchFinding(f, ledger, matcherAdapter) {
  const key = findingKey(f)
  if (ledger[key]) return { key, entry: ledger[key] }
  if (matcherAdapter) {
    const adj = matcherAdapter(f, fullLedger(ledger))
    if (adj && adj.ambiguous && adj.key && ledger[adj.key]) {
      return { key: adj.key, entry: ledger[adj.key], ambiguous: true }
    }
  }
  return null
}

function countClass(ledger, cls) {
  return Object.values(ledger).filter((e) => e.class === cls && e.status !== 'fixed-verified' && e.status !== 'accepted-residual').length
}
function hasOpenOrPendingBlocking(ledger) {
  // AC-ATC-3: `requirement-quality` is a NON-GATING advisory class — an open/pending-fix RQ finding,
  // at ANY severity, never counts as a convergence blocker. Deliberately kept in THIS function (never
  // inside isConvergedSweep's own body) — audit-requirement-quality-lens.py's w5 structural guard reds
  // out on ANY 'requirement-quality' substring inside isConvergedSweep specifically.
  return Object.values(ledger).some((e) => (e.status === 'open' || e.status === 'pending-fix')
    && e.class !== 'requirement-quality' && isBlockingSeverity(e.severity))
}

// AC-ATC-2: "reaching a cap with material (High+) findings still open SHALL terminate as needs-operator".
// AC-ATC-3: requirement-quality is non-gating EVERYWHERE (never escalates either) — same exclusion as
// hasOpenOrPendingBlocking, kept in this separate function for the same structural-check reason above.
function hasOpenHighPlus(ledger) {
  return Object.values(ledger).some((e) => (e.status === 'open' || e.status === 'pending-fix')
    && e.class !== 'requirement-quality' && (e.severity === 'Critical' || e.severity === 'High'))
}
// The convergence predicate (AC-APR-4, supersedes the old `new-design==0 AND category-error==0` plateau):
// a CLEAN VERIFICATION SWEEP raised ZERO fresh-or-reopened findings, leaves NO open/pending Critical/High/
// Medium, AND has category-error == 0. No "no-change => CONVERGED" path: a stalled sweep with an open
// blocker is MAX_PASS, never CONVERGED.
// AC-ASG-7 (Atom B, #120): `reality-divergence` is a SECOND dominant class alongside `category-error` — a
// sweep with any open/seeded `reality-divergence` entry in the ledger CANNOT return CONVERGED, exactly
// mirroring the category-error dominance (severity-independent; countClass already excludes
// fixed-verified/accepted-residual).
function isConvergedSweep(freshOrReopened, ledger) {
  const categoryErrors = countClass(ledger, 'category-error')
  const realityDivergences = countClass(ledger, 'reality-divergence')
  return freshOrReopened === 0 && !hasOpenOrPendingBlocking(ledger) && categoryErrors === 0 && realityDivergences === 0
}

// AC-ASG-3 (Atom B, #120): the system-grounding phase's DETERMINISTIC surfacing function — NO agent()
// call, NO prose parsing, NO re-implementation of Atom C's reconciliation. `injected` is the host-side
// binder's computed `_args.systemGroundingFindings` (Atom C's merged
// `foundry_contract.system_grounding_errors(contract_data, snapshot)` result — a `string[]`; absent/
// non-array degrades to zero findings, AC-ASG-5). Emits EXACTLY ONE `reality-divergence` finding per
// array element, using the string VERBATIM as the finding's title/detail (never paraphrased), with a
// FIXED `location` (a stable locator naming the audited atom's declared block) and a FIXED `severity`
// of `High` (deterministic for the ledger/report; the HALT itself is severity-independent per AC-ASG-2).
const SYSTEM_GROUNDING_LOCATION = 'system_grounding (acceptance-contract.yaml)'
function systemGroundingPhaseFindings(injected) {
  const list = Array.isArray(injected) ? injected : []
  return list.map((msg) => ({
    class: 'reality-divergence',
    severity: 'High',
    location: SYSTEM_GROUNDING_LOCATION,
    title: msg,
    detail: msg,
  }))
}

// Draft-only precondition (AC-APR-4): authorization is recorded on the SIBLING acceptance-contract.yaml's
// FOUNDRY-AUTHORIZED-TRAILER (NOT the spec .md). The engine reads the CONTRACT trailer and refuses to
// auto-remediate an authorized/frozen atom (front-authorization floor #1).
function contractHasAuthorizedTrailer(contractText) {
  if (typeof contractText !== 'string') return false
  return /FOUNDRY-AUTHORIZED-TRAILER/.test(contractText) ||
    /^authorized:\s*$/m.test(contractText) && /\bauth_seq:/.test(contractText)
}

// ----------------------------------------------------------------------------------------------
// The engine. Deterministic loop; LLM touchpoints are the injected adapters only:
//   adapters.critic(phaseKey, specText, blindView)  -> { findings: [...] }
//   adapters.reviser(specText, finding, fullLedger) -> { newText, writes:[paths], severityOverride?, acceptResidual? }
//   adapters.matcher(finding, fullLedger)           -> { key, ambiguous } | null   (optional)
// io abstraction (production = an IN-MEMORY STRING-backed adapter built from the injected args strings —
// no filesystem, no dynamic import; the live-seam drives the SAME production string-io factory):
//   io.read(path), io.write(path, text), io.exists(path)->bool, io.allowlist:[specPath],
//   io.inAllowlist(path), io.listGuarded()->[paths], io.snapshot(paths)->snap, io.restore(snap)
//   (io.exists is load-bearing: the draft-only precondition calls it — a re-implemented io MUST provide it)
// Returns { terminus, passes, ledger, events, specText, finalSpecText } (specText == finalSpecText == the
// in-memory buffer at the terminus: accepted appends included, rolled-back edits excluded).
// ----------------------------------------------------------------------------------------------
async function runEngine(opts) {
  const { specPath, contractPath, io, adapters, maxPass, systemGroundingFindings } = opts
  const riskTier = resolveRiskTier(opts.riskTier)
  const watchdog = resolveWatchdog(opts.watchdog)
  // T3: the existing MAX_PASS(maxPass) computation, UNCHANGED. T0/T1/T2: HARD caps (AC-ATC-2),
  // independent of `maxPass`.
  const cap = riskTier === 'T3' ? Math.min(maxPass || MAX_PASS_CAP, MAX_PASS_CAP) : TIER_ROUND_CAP[riskTier]
  const events = []
  const ledger = {}
  if (opts.seedLedger) {
    for (const e of opts.seedLedger) ledger[findingKey(e)] = { ...e }
  }

  // phaseCoverage (feat-foundry-audit-advisory-phase-nonhalting, AC-APN-3): a TOTAL, mutually-exclusive
  // map of each of the five LLM phase names to 'ran' | 'skipped-by-tier' | 'skipped-by-halt' |
  // 'skipped-other', reflecting the state as of the TERMINATING pass. Reset at the top of every pass
  // (only the terminating pass's snapshot is carried out via `done`); `markHaltCoverage` backfills every
  // still-default ('skipped-other') LATER phase to 'skipped-by-halt' at every halting return.
  const LLM_PHASES = ['prior-art', 'requirement-quality', 'steelman', 'adversarial', 'redteam']
  const initialPhaseCoverage = () => {
    const c = {}
    for (const p of LLM_PHASES) c[p] = 'skipped-other'
    return c
  }
  const markHaltCoverage = (coverage, haltedAtPhaseKey) => {
    const idx = LLM_PHASES.indexOf(haltedAtPhaseKey)
    const start = idx === -1 ? 0 : idx + 1   // system-grounding (idx -1) halts before every LLM phase
    for (let i = start; i < LLM_PHASES.length; i++) {
      if (coverage[LLM_PHASES[i]] === 'skipped-other') coverage[LLM_PHASES[i]] = 'skipped-by-halt'
    }
  }
  // REFUSED-AUTHORIZED-TRAILER returns before any tier/pass logic runs — all five phases correctly stay
  // the default 'skipped-other' (AC-APN-3: "a REFUSED return records all five phases skipped-other").
  let phaseCoverage = initialPhaseCoverage()

  const done = (terminus, passes) => {
    const _final = io.read(specPath)
    return {
      terminus, passes, ledger, events, specText: _final, finalSpecText: _final,
      riskTier, findingsDelta: findingsDelta(opts.seedLedger, ledger),
      phaseCoverage,
    }
  }

  // Draft-only precondition (front-auth floor #1): refuse an authorized/frozen atom.
  const contractText = contractPath && io.exists(contractPath) ? io.read(contractPath) : null
  if (contractHasAuthorizedTrailer(contractText)) {
    events.push({ type: 'refuse-authorized-trailer', contractPath })
    return done('REFUSED-AUTHORIZED-TRAILER', 0)
  }

  events.push({ type: 'risk-tier-resolved', riskTier, requested: opts.riskTier, cap, watchdog })

  // T0 (AC-ATC-2): 0 LLM rounds — deterministic lint only, the system-grounding phase alone (no
  // agent() dispatch, AC-ASG-3). A non-feature doc/scaffold/taxonomy atom never pays a critic round.
  if (riskTier === 'T0') {
    // AC-APN-3: "a T0 deterministic-only return records all five LLM phases skipped-by-tier" —
    // regardless of the terminus (CONVERGED or NEEDS-REGROUND), T0 never dispatches an LLM phase.
    for (const p of LLM_PHASES) phaseCoverage[p] = 'skipped-by-tier'
    const findings = systemGroundingPhaseFindings(systemGroundingFindings)
    events.push({ type: 'system-grounding-eval', pass: 0, count: findings.length, findings })
    for (const f of findings) {
      if (f.class === 'reality-divergence') {
        events.push({ type: 'reground', key: findingKey(f), class: 'reality-divergence' })
        return done('NEEDS-REGROUND', 0)
      }
    }
    events.push({ type: 'converged', pass: 0, freshOrReopened: 0 })
    return done('CONVERGED', 0)
  }

  let deathUsed = false   // AC-ATC-5: the ONE retry has already been consumed by an earlier critic death.

  for (let pass = 1; pass <= cap; pass++) {
    let freshOrReopened = 0
    const reopenedThisPass = new Set()
    let passDied = false
    let dieReason = null
    let deadPhaseKeyThisPass = null

    // phaseCoverage reflects the state in the TERMINATING pass (AC-APN-3) — reset fresh each pass.
    phaseCoverage = initialPhaseCoverage()
    if (riskTier === 'T2') {
      // AC-APN-3 / AC-ATC-3: T2's consolidated dispatch runs the adversarial-family CONTENT under the
      // 'steelman' slot — 'adversarial'/'redteam' are ALWAYS 'skipped-by-tier' for a T2 run (a tier
      // property, independent of where/whether a halt later occurs this pass).
      phaseCoverage.adversarial = 'skipped-by-tier'
      phaseCoverage.redteam = 'skipped-by-tier'
    }

    for (const phaseKey of PHASE_ORDER) {
      // AC-ATC-3: for T2, the adversarial-family (steel-man/adversarial/red-team) runs as ONE
      // consolidated pass — dispatched once at the 'steelman' slot; the 'adversarial'/'redteam' slots
      // are skipped. T1/T3 keep the full separated PHASE_ORDER untouched.
      if (riskTier === 'T2' && (phaseKey === 'adversarial' || phaseKey === 'redteam')) continue

      const specText = io.read(specPath)
      let findings
      if (phaseKey === 'system-grounding') {
        // AC-ASG-3: Pass-0 is evaluated by the DETERMINISTIC function above — NO adapters.critic
        // dispatch, hence NO agent() call for this phase (the other five phases are byte-unchanged
        // below). Every injected string surfaces as exactly one reality-divergence finding.
        findings = systemGroundingPhaseFindings(systemGroundingFindings)
        events.push({ type: 'system-grounding-eval', pass, count: findings.length, findings })
      } else {
        // AC-ATC-3: T2's consolidated adversarial-family dispatch key (see above) — the 'steelman' slot
        // dispatches under 'adversarial-consolidated' for T2; every other phase dispatches under its own key.
        const dispatchKey = (riskTier === 'T2' && phaseKey === 'steelman') ? 'adversarial-consolidated' : phaseKey
        const wd = await withWatchdog(() => adapters.critic(dispatchKey, specText, blindView(ledger)), watchdog)
        events.push({ type: 'critic-call', pass, phase: dispatchKey, view: blindView(ledger) })
        if (wd.killed) {
          // AC-ATC-4/-5: critic-death-fails-the-round — never a vacuous pass. Stop dispatching further
          // phases this pass (the round is voided); the retry-vs-terminate disposition is decided below,
          // after the phase loop, so a mid-pass death never reaches the promotion/convergence check.
          events.push({ type: 'critic-death', pass, phase: dispatchKey, kill_reason: wd.kill_reason, detail: wd.detail })
          passDied = true
          dieReason = wd.kill_reason
          // AC-APN-3: "a KILLED return records the dead phase skipped-other" — phaseKey stays at its
          // default (never marked 'ran' below, since we break before reaching it).
          deadPhaseKeyThisPass = phaseKey
          break
        }
        findings = wd.findings
        phaseCoverage[phaseKey] = 'ran'
      }

      for (const f of findings) {
        // Deterministic HALT termini over the CRITIC finding (fail-safe blast-radius FIRST).
        // AC-APN-1/-2 (feat-foundry-audit-advisory-phase-nonhalting): the advisory (requirement-quality)
        // phase is structurally incapable of a run-halting terminus — guarded on the PHASE (robust to a
        // mislabeled finding), never on the finding's own class. Every NON-advisory phase's halt is
        // BYTE-IDENTICAL to before (AC-APN-2 anti-regression) — `blastRadius` itself is untouched.
        if (phaseKey !== 'requirement-quality') {
          if (blastRadius(`${f.title || ''} ${f.detail || ''}`, f.blast)) {
            events.push({ type: 'escalate-operator', source: 'finding', key: findingKey(f) })
            markHaltCoverage(phaseCoverage, phaseKey)
            return done('NEEDS-OPERATOR', pass)
          }
          if (f.class === 'category-error') {
            events.push({ type: 'reground', key: findingKey(f) })
            markHaltCoverage(phaseCoverage, phaseKey)
            return done('NEEDS-REGROUND', pass)
          }
          // AC-ASG-2 (Atom B, #120): reality-divergence is a SECOND dominant, severity-independent HALT
          // that mirrors category-error exactly — deterministic terminus NEEDS-REGROUND, and the reviser
          // below is NEVER reached for this finding (re-grounding is a re-spec on the live shape, never
          // an in-place patch).
          if (f.class === 'reality-divergence') {
            events.push({ type: 'reground', key: findingKey(f), class: 'reality-divergence' })
            markHaltCoverage(phaseCoverage, phaseKey)
            return done('NEEDS-REGROUND', pass)
          }
        }

        // Ledger identity + reopen/raise (severity critic-owned + pinned afresh on reopen).
        const m = matchFinding(f, ledger, adapters.matcher)
        let key
        if (m && (m.entry.status === 'pending-fix' || m.entry.status === 'fixed-verified' || m.entry.status === 'accepted-residual')) {
          key = m.key
          const reopenSev = m.ambiguous ? higherSeverity(f.severity, m.entry.severity) : f.severity
          ledger[key] = { ...m.entry, status: 'open', severity: reopenSev }   // re-pinned, never inherits stale
          reopenedThisPass.add(key)
          freshOrReopened++
          events.push({ type: 'reopen', key, from: m.entry.status, severity: reopenSev })
        } else if (m && m.entry.status === 'open') {
          key = m.key
          ledger[key].severity = m.ambiguous ? higherSeverity(f.severity, ledger[key].severity) : f.severity
          freshOrReopened++
        } else {
          key = findingKey(f)
          ledger[key] = { class: f.class, location: f.location, severity: f.severity, status: 'open', title: f.title }
          freshOrReopened++
        }

        // Remediate an OPEN finding through the confined reviser (feat-foundry-audit-remediation-model,
        // AC-ARM-1..5): propose-diff -> blind verify -> anchored apply. A round = this finding's own
        // remediation cycle; AT MOST ONE proposal is APPLIED per round, budgeted at the initial reviser
        // reply plus ONE post-rejection revision. A `{ newText }` reply (no `.proposal`) is the LEGACY
        // shape — routed through the UNCHANGED append-only floor for exact backward-compat; a `{
        // proposal }` reply is the NEW diff-proposal shape AC-ARM-1..6 govern.
        // AC-APN-1(b): a requirement-quality-class finding is RECORDED (above) but NEVER auto-remediated
        // in-band — the reviser is not dispatched for it, so the reviser-DIFF blast-radius halt below is
        // structurally never reached for an advisory finding (the operator dispositions it at
        // /foundry:authorize, as it is non-gating). Every non-advisory finding's remediation path
        // (write-scope / normative-integrity / budget-exhaustion guards) is UNCHANGED.
        if (ledger[key].status === 'open' && ledger[key].class !== 'requirement-quality') {
          const guarded = io.listGuarded()
          const snap = io.snapshot(guarded)
          const before = io.read(specPath)
          const namedFinding = { ...ledger[key], key }
          const firstReply = (await adapters.reviser(before, namedFinding, fullLedger(ledger))) || {}

          if (firstReply.newText !== undefined && !firstReply.proposal) {
            // ---- LEGACY single-shot path (pre-ARM shape; byte-for-byte unchanged behavior). ----
            const edit = firstReply
            const newText = edit.newText
            const writes = edit.writes || [specPath]

            // (1) Blast-radius over the reviser DIFF — checked FIRST, independent of the integrity branch.
            if (blastRadius(addedText(before, newText))) {
              io.restore(snap)
              events.push({ type: 'escalate-operator', source: 'diff', key })
              markHaltCoverage(phaseCoverage, phaseKey)
              return done('NEEDS-OPERATOR', pass)
            }
            // (2) Severity-laundering + no-blocking-self-accept — reviser cannot relabel or self-accept.
            if (edit.severityOverride && edit.severityOverride !== ledger[key].severity) {
              io.restore(snap)
              events.push({ type: 'reject-severity-laundering', key, attempted: edit.severityOverride, pinned: ledger[key].severity })
              continue
            }
            if (edit.acceptResidual && isBlockingSeverity(ledger[key].severity)) {
              io.restore(snap)
              events.push({ type: 'reject-self-accept', key, severity: ledger[key].severity })
              continue
            }
            if (edit.acceptResidual && !isBlockingSeverity(ledger[key].severity)) {
              ledger[key].status = 'accepted-residual'
              events.push({ type: 'accept-residual', key, severity: ledger[key].severity })
              continue
            }
            // (3) Write-scope allowlist (confined reviser) — only the target spec.
            const foreign = writes.find((p) => !io.inAllowlist(p))
            if (foreign) {
              io.restore(snap)
              events.push({ type: 'reject-write-scope', key, path: foreign })
              markHaltCoverage(phaseCoverage, phaseKey)
              return done('NEEDS-OPERATOR', pass)
            }
            // (4) Append-only normative integrity — modify/shrink/delimiter/dup => rollback + NEEDS-OPERATOR.
            const integrity = normativeIntegrity(before, newText)
            if (!integrity.ok) {
              io.restore(snap)
              events.push({ type: 'reject-integrity', key, reason: integrity.reason })
              markHaltCoverage(phaseCoverage, phaseKey)
              return done('NEEDS-OPERATOR', pass)
            }
            io.write(specPath, newText)
            ledger[key].status = 'pending-fix'
            ledger[key].pending_since = pass
            events.push({ type: 'pending-fix', key, pending_since: pass })
          } else {
            // ---- AC-ARM-1..5: propose-diff -> blind-verify -> anchored-apply. ----
            let applied = false
            let lastRejected = null
            let reply = firstReply
            for (let attempt = 1; attempt <= 2 && !applied; attempt++) {
              if (attempt > 1) {
                reply = (await adapters.reviser(before, namedFinding, fullLedger(ledger), { attempt, priorRejection: lastRejected })) || {}
              }
              const proposal = reply.proposal || {}
              events.push({ type: 'propose-diff', key, attempt, spans: (proposal.edits || []).map((e) => e && e.span) })

              const verdict = await blindVerifyProposal(adapters, before, proposal, namedFinding)
              if (!verdict.ok) {
                lastRejected = { attempt, proposal, reason: verdict.reason }
                events.push({ type: 'reject-verify', key, attempt, reason: verdict.reason })
                continue
              }
              if (verdict.deferred) {
                events.push({ type: 'defer', key, attempt })
                applied = 'deferred'
                break
              }

              const built = applyProposal(before, proposal)
              if (!built.ok) {
                lastRejected = { attempt, proposal, reason: built.reason }
                events.push({ type: 'reject-apply', key, attempt, reason: built.reason })
                continue
              }
              let newText = built.newText
              const writes = reply.writes || [specPath]

              // AC-ARM-4 floors, preserved + consumed (not bypassed): blast-radius over the diff first.
              if (blastRadius(addedText(before, newText))) {
                io.restore(snap)
                events.push({ type: 'escalate-operator', source: 'diff', key })
                markHaltCoverage(phaseCoverage, phaseKey)
                return done('NEEDS-OPERATOR', pass)
              }
              // Confined reviser allowlist — only the target spec.
              const foreign = writes.find((p) => !io.inAllowlist(p))
              if (foreign) {
                io.restore(snap)
                events.push({ type: 'reject-write-scope', key, path: foreign })
                markHaltCoverage(phaseCoverage, phaseKey)
                return done('NEEDS-OPERATOR', pass)
              }
              // Span-scoped normative integrity (defense-in-depth successor of the append-only guard).
              const integrity = spanScopedIntegrity(before, newText, built.touchedSpans)
              if (!integrity.ok) {
                io.restore(snap)
                events.push({ type: 'reject-integrity', key, reason: integrity.reason })
                markHaltCoverage(phaseCoverage, phaseKey)
                return done('NEEDS-OPERATOR', pass)
              }

              // AC-ARM-3: ANCHORED APPLY — in place, with a one-line changelog entry naming the finding
              // resolved; the applied result becomes the NEXT round's anchored base.
              newText = appendChangelogLine(newText, changelogLine(pass, namedFinding, proposal.changelog))
              io.write(specPath, newText)
              ledger[key].status = 'pending-fix'
              ledger[key].pending_since = pass
              events.push({ type: 'pending-fix', key, pending_since: pass, attempt })
              applied = true
            }
            if (!applied) {
              // AC-ARM-5: budget exhausted (reviser failure or verifier rejection x2) => NEEDS-OPERATOR
              // with the open findings (already in `ledger`, status untouched) + the last rejected
              // proposal attached — remediation never force-applies, never silently drops a finding.
              io.restore(snap)
              events.push({ type: 'escalate-operator', source: 'remediation-budget', key, lastRejected })
              markHaltCoverage(phaseCoverage, phaseKey)
              return done('NEEDS-OPERATOR', pass)
            }
          }
        }
      }
    }

    if (passDied) {
      // AC-ATC-5: a round in which any critic died is NEVER eligible for promotion or convergence —
      // skip straight to the retry-vs-terminate disposition (never a vacuous pass).
      if (deathUsed || pass >= cap) {
        events.push({ type: 'killed', pass, kill_reason: dieReason })
        // AC-APN-3: "a KILLED return records the dead phase skipped-other and any later phases
        // skipped-by-halt" — deadPhaseKeyThisPass itself stays at its untouched default 'skipped-other'.
        markHaltCoverage(phaseCoverage, deadPhaseKeyThisPass)
        return done('KILLED', pass)
      }
      deathUsed = true
      events.push({ type: 'retry-round', pass, kill_reason: dieReason })
      continue   // the ONE retry consumes the NEXT capped round (pass+1)
    }

    // Verify-after-correct on the NEXT sweep: promote pending-fix => fixed-verified ONLY if it was raised
    // in an EARLIER pass (pending_since < pass) AND was not reopened this pass. A same-pass fix never
    // verifies here — it must survive the NEXT full ordered sweep's blind critic.
    for (const [key, e] of Object.entries(ledger)) {
      if (e.status === 'pending-fix' && e.pending_since < pass && !reopenedThisPass.has(key)) {
        e.status = 'fixed-verified'
        events.push({ type: 'promote', key, verified_at: pass })
      } else if (e.status === 'pending-fix' && e.pending_since === pass) {
        events.push({ type: 'no-promote-same-pass', key, pending_since: e.pending_since })
      }
    }

    if (isConvergedSweep(freshOrReopened, ledger)) {
      events.push({ type: 'converged', pass, freshOrReopened })
      return done('CONVERGED', pass)
    }
  }

  // AC-ATC-2: reaching a tier's cap with a material (High+) finding still open is NEVER a silent pass
  // and NEVER an uncapped continuation — it terminates NEEDS-OPERATOR. T3 keeps its prior unconditional
  // MAX_PASS(10)-REACHED terminus (its own cap is the existing ceiling, unchanged by this atom).
  if (riskTier !== 'T3') {
    events.push({ type: 'cap-reached', riskTier, cap })
    if (hasOpenHighPlus(ledger)) {
      events.push({ type: 'escalate-operator', source: 'cap', riskTier })
      return done('NEEDS-OPERATOR', cap)
    }
    return done(`MAX_PASS(${cap})-REACHED`, cap)
  }
  events.push({ type: 'max-pass', cap })
  return done(`MAX_PASS(${MAX_PASS_CAP})-REACHED`, cap)
}

// ============================================================================================
// Production io + adapters (an IN-MEMORY string-backed io + agent()). The control-plane above is
// unaware of agent(); the io is a pure string buffer, so the workflow opens NO file handle.
// ============================================================================================
// makeStringIo — the DEPENDENCY-INJECTED sibling of a filesystem io: an in-memory STRING-backed
// adapter over the injected spec/contract TEXT. `read(specPath)` serves the current in-memory buffer;
// `write(specPath, t)` mutates that buffer (the ONLY mutable cell — the contract cell is immutable);
// `snapshot()` captures the buffer + the immutable contractText; `restore()` reverts the buffer. No
// filesystem, no dynamic import — it conforms to the native Workflow runtime contract (bytes cross the
// sandbox boundary via `args` in + the top-level return value out). This SUPERSEDES the fs-coupled io.
function makeStringIo(specPath, specText, contractText) {
  const contractPath = specPath.replace(/[^/]+$/, 'acceptance-contract.yaml')
  const hasContract = typeof contractText === 'string' && contractText.length > 0
  const store = {}
  store[specPath] = typeof specText === 'string' ? specText : ''
  if (hasContract) store[contractPath] = contractText
  const guarded = hasContract ? [specPath, contractPath] : [specPath]
  return {
    allowlist: [specPath],
    contractPath,
    inAllowlist(p) { return p === specPath },
    listGuarded() { return guarded.slice() },
    exists(p) { return Object.prototype.hasOwnProperty.call(store, p) },
    read(p) { return Object.prototype.hasOwnProperty.call(store, p) ? store[p] : '' },
    write(p, t) { if (p === specPath) store[specPath] = t },   // buffer write only; contract is immutable
    snapshot(paths) {
      const snap = {}
      for (const p of paths) snap[p] = Object.prototype.hasOwnProperty.call(store, p) ? store[p] : null
      return snap
    },
    restore(snap) {
      for (const p of Object.keys(snap)) { if (snap[p] !== null) store[p] = snap[p] }
    },
  }
}

// ============================================================================================
// Workflow entrypoint. The native Workflow runtime injects agent/parallel/pipeline/log/args and
// captures the top-level return. The live-seam drives the deterministic control-plane through the
// `__test` hook with STUB adapters (no real agent() calls).
// ============================================================================================
const _args = (typeof args === 'string') ? (() => { try { return JSON.parse(args) } catch { return {} } })() : (args || {})

// LIVE-SEAM TEST HOOK (AC-APR-5): scripts/foundry_checks/audit-phased-remediation.py loads this module
// body, injects stub adapters + a temp-dir io, and drives runEngine here. Production never sets __test.
if (_args.__test) {
  // LIVE-SEAM: expose the PRODUCTION string-io factory so the drop-in check builds `io` from the SAME
  // function the entrypoint calls (AC-AWD-4 closes the `__test`-bypass false-GREEN); else drive runEngine.
  if (_args.__test.__exposeFactory) return { makeStringIo }
  return await runEngine(_args.__test)
}

const target = _args.target || null
if (!target) { log('foundry-spec-audit: args.target required'); return { error: 'no target' } }
const MAX_PASS = Math.min(_args.max_pass || MAX_PASS_CAP, MAX_PASS_CAP)
if (_args.reality_sampling) {
  log('Pass-0 reality sampling: the target declares a parser/extractor over a corpus — sample real inputs before auditing claims.')
}

// The io is an IN-MEMORY string buffer built from the injected `args` strings — the /foundry:audit skill
// main-loop (which has Read) reads the target spec + its sibling acceptance-contract.yaml itself and passes
// their TEXT via args {target, specText, contractText, ...}. The draft-only precondition reads the trailer
// from the injected `_args.contractText` (not a file); the skill applies the returned finalSpecText.
const _contractPath = target.replace(/[^/]+$/, 'acceptance-contract.yaml')
const io = makeStringIo(target, _args.specText, _args.contractText)

// Production adapters wrap agent(). Critic = a fresh-context audit of one phase, BLIND-status-filtered,
// receiving the CURRENT buffer text INLINE (never an on-disk read — a disk read would be STALE after the
// first in-memory edit). Reviser (feat-foundry-audit-remediation-model, AC-ARM-1) = a SEPARATE fresh-context
// agent that returns a BOUNDED DIFF `proposal` ONLY (never a full-document `newText`, never an appended
// amendment/reground block) and performs NO file write — its sole output channel is the proposal, verified
// (blind verify + span-scoped integrity + blast-radius) before the control-plane anchors it into the buffer.
// Verifier (AC-ARM-2) = a THIRD, independently fresh-context agent (reuses operator-steer's mechanized
// reviser-check dispatch shape) that sees ONLY the base text + the proposed edits + the named finding's bare
// identity — never the reviser's reasoning — and judges finding-resolution; the no-out-of-span-delta guard is
// separately DETERMINISTIC engine logic (never delegated to model judgment). This is a STRICTLY STRONGER
// confinement than a write-jail on a file-writing reviser (a sandboxed reviser has no write capability at
// all). Matcher defaults to the deterministic class+location identity. The control-plane's allowlist +
// snapshot/rollback over the string buffer are the deterministic backstop.
const REVISER_SCHEMA = {
  type: 'object', required: ['proposal'],
  properties: {
    proposal: {
      type: 'object', required: ['findingsAddressed', 'edits'],
      properties: {
        findingsAddressed: { type: 'array', items: { type: 'string' } },
        findingsDeferred: { type: 'array', items: { type: 'string' } },
        edits: {
          type: 'array',
          items: {
            type: 'object', required: ['span', 'find', 'replace'],
            properties: { span: { type: 'string' }, find: { type: 'string' }, replace: { type: 'string' } },
          },
        },
        changelog: { type: 'string' },
      },
    },
  },
}
const VERIFIER_SCHEMA = {
  type: 'object', required: ['resolved'],
  properties: { resolved: { type: 'boolean' }, reason: { type: 'string' } },
}
const adapters = {
  critic: async (phaseKey, specText, view) => {
    // AC-ATC-3: T2's 'adversarial-consolidated' dispatch key is NOT a PHASES entry (PHASES stays the
    // exact 5-entry shape the phased-engine grammar checks parse) — its prompt is the three individual
    // adversarial-family prompts (steel-man + adversarial + red-team), concatenated, in ONE critic call.
    const promptText = phaseKey === 'adversarial-consolidated'
      ? ['steelman', 'adversarial', 'redteam'].map((k) => PHASES.find((p) => p.key === k).prompt(target)).join('\n\n')
      : PHASES.find((p) => p.key === phaseKey).prompt(target)
    const hint = view.length ? ` Known still-open/located concerns (re-audit blindly; do NOT assume any are resolved): ${JSON.stringify(view)}.` : ''
    const r = await agent(
      promptText +
        ` Audit the CURRENT spec text provided INLINE below — do NOT read ${target} from disk (a disk read would be STALE after an in-memory edit). CURRENT SPEC TEXT:\n<<<SPEC\n${specText}\nSPEC` + hint,
      { label: `audit:${phaseKey}`, phase: 'Audit', model: _args.auditModel || 'sonnet', schema: FINDINGS_SCHEMA },
    )
    // `outputTokens` is left undefined here — the production agent() wrapper does not currently surface
    // a real per-call token count to this script (AC-ATC-4's watchdog token-threshold arm is wired +
    // configurable but activates only once/if a caller supplies one; the wall-time arm is fully live).
    return { findings: (r && r.findings) || [] }
  },
  reviser: async (specText, finding, ledgerSnapshot, ctx) => {
    const revisionHint = (ctx && ctx.priorRejection)
      ? ` Your PRIOR proposal was REJECTED: ${ctx.priorRejection.reason}. Revise it (still within the finding's OWN span).`
      : ''
    const r = await agent(
      `You are a SEPARATE remediation agent (fresh context; you are NOT the critic). Resolve this §8 finding on the spec ${target} with a BOUNDED DIFF PROPOSAL — NOT a full-document rewrite, NOT an appended amendment/reground block. Propose one or more in-place span edits ONLY within a COMPONENT of this finding's OWN location '${finding.location}' — if the location is compound/multi-section (e.g. 'X / Y') or carries a parenthetical qualifier (e.g. '§3.5 (crash recovery)'), set each edit's 'span' to ONE cited component, stripped of its parenthetical (e.g. '§3.5' or 'AC-ID'); an edit outside every cited component is rejected. Each edit is { span, find, replace } where 'find' is an EXACT substring located within that component's own resolved block (an AC-ID bullet block, or a '§N.M' section's block) in the CURRENT spec text (provided inline) and 'replace' is its in-place replacement. List the finding's key in findingsAddressed if you fix it now, or in findingsDeferred if you cannot resolve it yet (never silently drop it). Return ONLY the structured 'proposal' — do NOT read or write any file; your ONLY output channel is the proposal.${revisionHint} CURRENT SPEC TEXT:\n<<<SPEC\n${specText}\nSPEC Finding: ${JSON.stringify(finding)}.`,
      { label: `remediate:${finding.key}`, phase: 'Audit', model: _args.auditModel || 'sonnet', schema: REVISER_SCHEMA },
    )
    return { proposal: (r && r.proposal) || { findingsAddressed: [], findingsDeferred: [], edits: [] } }
  },
  verifier: async (baseText, edits, blindFinding) => {
    const r = await agent(
      `You are a FRESH-CONTEXT BLIND VERIFIER (you are NOT the reviser and have NO access to its reasoning). Given the BASE spec text, a bounded diff proposal's edits (in-place span edits only), and the NAMED finding it claims to resolve — identity only (class/location/severity/title), no reviser detail — judge: does applying this diff resolve the named finding? Return { resolved, reason }. BASE TEXT:\n<<<BASE\n${baseText}\nBASE PROPOSED EDITS: ${JSON.stringify(edits)}\nNAMED FINDING: ${JSON.stringify(blindFinding)}.`,
      { label: `verify:${blindFinding.location}`, phase: 'Audit', model: _args.auditModel || 'sonnet', schema: VERIFIER_SCHEMA },
    )
    return { resolved: !!(r && r.resolved), reason: r && r.reason }
  },
}

// Surface the model DECISION natively (the Workflow narrator): the operator sees which model the §8
// critic + reviser run on. The host binder resolved it (operator override > security-flag opus > sonnet).
log(`§8 audit tier: critic + reviser on '${_args.auditModel || 'sonnet'}'`)
// Atom B (#120, AC-ASG-3/-4): the host-side binder (scripts/foundry-audit-prepare.py) injects the
// RESULT of Atom C's merged reconciliation as _args.systemGroundingFindings (a string[]); the engine
// never re-derives it. _args.systemState (the raw snapshot) is also injected per the host-side-
// injection invariant (ER #68) but is not consumed by this deterministic phase.
// feat-foundry-audit-tier-caps (AC-ATC-1): the host binder ALSO injects the machine-derived RISK TIER
// (`_args.riskTier`, T0-T3 — deliberately NOT the same field as the `auditModel` narrated above),
// watchdog thresholds, and any findings-carry seed ledger from a prior run of the SAME atom.
log(`§8 risk-tier: ${_args.riskTier || 'T3'} (round cap per AC-ATC-2; fail-closed default T3)`)
const result = await runEngine({
  specPath: target, contractPath: _contractPath, io, adapters, maxPass: MAX_PASS,
  systemGroundingFindings: _args.systemGroundingFindings,
  riskTier: _args.riskTier,
  watchdog: { tokenLimit: _args.watchdogTokenLimit, msLimit: _args.watchdogMsLimit },
  seedLedger: _args.seedLedger,
})
log(`terminus: ${result.terminus} after ${result.passes} pass(es); ledger entries: ${Object.keys(result.ledger).length}; risk-tier: ${result.riskTier}; findings delta: ${JSON.stringify(result.findingsDelta)}`)
return {
  target, terminus: result.terminus, passes: result.passes, ledger: result.ledger, events: result.events,
  finalSpecText: result.finalSpecText, riskTier: result.riskTier, findingsDelta: result.findingsDelta,
  // AC-APN-3: the production /foundry:audit consumer reads THIS entrypoint return, not runEngine's
  // internal result directly — phaseCoverage must be carried here too or the distinction is invisible.
  phaseCoverage: result.phaseCoverage,
}
