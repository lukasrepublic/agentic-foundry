export const meta = {
  name: 'foundry-release-wave',
  description: 'Implement + verify a wave of AUTHORIZED atoms via native fan-out (replaces the bespoke dispatch-queue fan-out)',
  phases: [
    { title: 'Implement', detail: 'one worktree-isolated worker per atom' },
    { title: 'Verify', detail: 'live-seam walk → merge-gate verdict per atom' },
  ],
}

// args: array of AUTHORIZED atom spec paths (each with a frozen acceptance-contract.yaml).
// Pipeline — each atom flows implement → verify independently (no barrier); native
// concurrency cap + journaling + resumeFromRunId replace the queue + fan-out hygiene.
// NOTE: requires a dispatcher context where worker writes are not hook-blocked (see
// /foundry:dispatch precondition 2); a plain operator session must run atoms inline.
//
// feat-foundry-dispatch-on-native-workflow (AC-DNW-1): THIS is the canonical multi-atom wave
// fan-out mechanism — the native Workflow tool's pipeline()/agent() primitives, not the bespoke
// dispatch-queue. It is the unconditional default; the bespoke process-spawn fallback (the
// deprecated `foundry-spawn-worker`/`foundry-fanout` scripts) does not ship — this workflow
// never shells out to anything.

const IMPL_SCHEMA = {
  type: 'object',
  required: ['branch', 'files_touched', 'status'],
  properties: {
    branch: { type: 'string' },
    pr_url: { type: ['string', 'null'] },
    files_touched: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    status: { type: 'string', enum: ['ready', 'failed'] },
    // Durable worker-learnings emission: the worker returns its learning records here so
    // they survive the worktree auto-clean. The PostToolUse(Agent|Workflow) hook captures them from
    // the returned wave result into .foundry/session-learnings/ — no skill step, no teardown race.
    learnings: { type: 'array', items: { type: 'object' } },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['verdict'],
  properties: {
    verdict: { type: 'string', enum: ['PASS', 'FAIL', 'EVIDENCE-MISSING', 'NOT-APPLICABLE'] },
    reasons: { type: 'array', items: { type: 'string' } },
    // feat-foundry-review-fanout-hardening (AC-RFH-1..-14): the CARRIER for the review fan-out's
    // raw findings — NOT a new reviewer, an optional array the existing review agent(s) may
    // populate alongside their verdict/reasons. `evidence`/`source` are optional per-finding; the
    // RFH-PURE block below consolidates + dispatches verification over exactly this array.
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'category', 'location', 'rationale'],
        properties: {
          severity: { type: 'string' },
          category: { type: 'string' },
          location: { type: 'string' },
          rationale: { type: 'string' },
          evidence: { type: 'string' },
          source: { type: 'string' },
        },
      },
    },
  },
}

// === RFH-PURE-BEGIN (review fan-out consolidation + verdict assembly — extracted verbatim by tests/test_review_fanout.py) ===
// feat-foundry-review-fanout-hardening (AC-RFH-1..-14). PURE, framework-authored compute — no
// worker discretion, the same posture as `normalizeSelfGateResult` above. This block MUST stay
// self-contained (nothing from the surrounding workflow scope — no `args`/`agent`/`pipeline`/
// `log`): the extraction harness evaluates it in isolation, so a dangling reference fails the
// test loudly rather than silently (the intended enforcement — see the spec's Clarifications §1).

// AC-RFH-1(a): whitespace normalization for the evidence component of the dedup key. Trims the
// ends, then collapses every maximal interior run of whitespace to one U+0020 SPACE. Every other
// character (including case) is preserved verbatim. Returns null when the input is not a usable
// string (absent, non-string, or normalizes to empty) — the sentinel `dedupKey` below checks for.
export function normalizeEvidence(s) {
  if (typeof s !== 'string') return null
  const trimmed = s.trim()
  if (!trimmed) return null
  return trimmed.replace(/\s+/g, ' ')
}

// Terminology: a finding's *file path* is its `location` up to (excluding) the first `:`, the
// whole string when there is no `:`, with leading/trailing whitespace trimmed. Returns null when
// `location` is not a usable string or trims to empty.
function filePathOfLocation(location) {
  if (typeof location !== 'string') return null
  const idx = location.indexOf(':')
  const raw = idx === -1 ? location : location.slice(0, idx)
  const trimmed = raw.trim()
  return trimmed === '' ? null : trimmed
}

// Severity order: Block > Risk > Nit > Confirmed > (any other or absent label) — an unrecognized
// label ranks lowest so it can never win a merge and thereby weaken a real severity (Clarification 4).
function severityRank(sev) {
  if (sev === 'Block') return 4
  if (sev === 'Risk') return 3
  if (sev === 'Nit') return 2
  if (sev === 'Confirmed') return 1
  return 0
}

function isBlockingSeverity(sev) {
  return sev === 'Block'
}

// AC-RFH-1: the dedup key is the TRIPLE (normalized evidence, file path, category — category
// compared verbatim, no normalization). Returns null (unkeyable, AC-RFH-3) when evidence is
// absent/non-string/normalizes empty, OR file path is undeducible/empty, OR category is
// absent/non-string/empty-after-trim. `consolidateFindings` groups ONLY over non-null keys — the
// structural enforcement of "unkeyable never merges", not a scattered guard.
export function dedupKey(finding) {
  if (!finding || typeof finding !== 'object') return null
  const evidence = normalizeEvidence(finding.evidence)
  if (evidence === null) return null
  const filePath = filePathOfLocation(finding.location)
  if (filePath === null) return null
  const category = finding.category
  if (typeof category !== 'string' || category.trim() === '') return null
  return JSON.stringify([evidence, filePath, category])
}

function buildConsolidatedFinding(contributors, dispatchKey) {
  let strictest = contributors[0]
  let strictestRank = severityRank(strictest.severity)
  for (let i = 1; i < contributors.length; i += 1) {
    const rank = severityRank(contributors[i].severity)
    if (rank > strictestRank) {
      strictest = contributors[i]
      strictestRank = rank
    }
  }
  const locations = []
  const seenLocations = new Set()
  for (const c of contributors) {
    const ref = c && c.location
    if (!seenLocations.has(ref)) {
      seenLocations.add(ref)
      locations.push(ref)
    }
  }
  const sources = []
  for (const c of contributors) {
    if (c && typeof c.source === 'string' && c.source !== '') {
      sources.push({ source: c.source, rationale: c.rationale })
    }
  }
  return {
    severity: strictest.severity,
    category: strictest.category,
    rationale: strictest.rationale,
    locations,
    sources,
    dedupKey: dispatchKey,
    contributors,
  }
}

// AC-RFH-1/-2/-3: group findings by dedup key (first-seen order determines both group order and
// the tie-break order within a group), then reduce each group to one consolidated finding.
// Unkeyable findings (dedupKey === null) NEVER merge with anything — each gets its own singleton
// group, keyed by a synthetic-but-stable dispatch id so AC-RFH-14 still has a unique join target
// even though its true dedup key is absent.
export function consolidateFindings(findings) {
  const list = Array.isArray(findings) ? findings : []
  const order = []
  const byKey = new Map()
  let unkeyableSeq = 0
  for (const finding of list) {
    const key = dedupKey(finding)
    if (key === null) {
      order.push({ key: `__unkeyable_${unkeyableSeq}__`, contributors: [finding] })
      unkeyableSeq += 1
      continue
    }
    const existing = byKey.get(key)
    if (existing) {
      existing.contributors.push(finding)
    } else {
      const group = { key, contributors: [finding] }
      byKey.set(key, group)
      order.push(group)
    }
  }
  return order.map((group) => buildConsolidatedFinding(group.contributors, group.key))
}

function isWellFormedConfirmation(v) {
  return !!v && typeof v === 'object' && v.verified === true
}

function isWellFormedRefutation(v) {
  return !!v && typeof v === 'object' && v.verified === false
    && typeof v.reason === 'string' && v.reason !== ''
}

// AC-RFH-5..-10, -14: assemble the per-atom review result. `verifications` maps a consolidated
// finding's `dedupKey` (see buildConsolidatedFinding) to its verification outcome — a Map or a
// plain object are both accepted. AC-RFH-14 (verdict scope) holds structurally: each consolidated
// finding looks up ONLY its own `dedupKey` in `verifications`, so a verdict dispatched for one key
// can never be applied under another.
export function assembleReviewResult(consolidated, verifications) {
  const list = Array.isArray(consolidated) ? consolidated : []
  const verMap = verifications instanceof Map ? verifications : new Map(Object.entries(verifications || {}))
  const blocking = []
  const nonblocking = []
  const incomplete = []
  for (const finding of list) {
    if (!isBlockingSeverity(finding.severity)) {
      nonblocking.push(finding)
      continue
    }
    const outcome = verMap.get(finding.dedupKey)
    if (isWellFormedRefutation(outcome)) {
      // AC-RFH-5/-14: a well-formed refutation demotes the WHOLE consolidated finding (all its
      // contributors together) — never a partial demotion, never a different key.
      nonblocking.push({ ...finding, refutation_reason: outcome.reason })
      continue
    }
    if (isWellFormedConfirmation(outcome)) {
      blocking.push(finding)
      continue
    }
    // AC-RFH-6/-7: neither a well-formed confirmation nor refutation — a failed/thrown/timed-out/
    // garbage verification result. Fail-closed: stays blocking, tagged, and surfaced in incomplete[].
    const reason = (outcome && typeof outcome === 'object' && typeof outcome.reason === 'string' && outcome.reason)
      || 'verification agent failed, threw, timed out, or returned a result that was neither a well-formed confirmation nor a well-formed refutation'
    const tagged = { ...finding, tag: 'could-not-be-verified', reason }
    blocking.push(tagged)
    incomplete.push(tagged)
  }
  // AC-RFH-8: incomplete non-empty forces FAIL; this is subsumed by `blocking.length > 0` since
  // incomplete is always a subset of blocking (built above).
  const verdict = blocking.length > 0 ? 'FAIL' : 'PASS'
  return { verdict, incomplete, blocking, nonblocking }
}
// === RFH-PURE-END ===

// feat-foundry-worker-dod-self-gate (AC-WDG-1..4): the worker definition-of-done SELF-GATE result
// schema — the structured return of the framework-authored self-gate probe below. `ran: false` means
// the gate could NOT be driven to a verdict (script errored/absent/crashed — DISTINCT from `ran: true,
// verdict: 'FAIL'`, a gate that ran and found >=1 locally-computable check failing).
const SELF_GATE_SCHEMA = {
  type: 'object',
  required: ['ran', 'verdict'],
  properties: {
    ran: { type: 'boolean' },
    verdict: { type: 'string', enum: ['PASS', 'FAIL', 'gate-unrunnable'] },
    failing_checks: { type: 'array', items: { type: 'string', enum: ['allowed-paths', 'checkpoint', 'base-sha'] } },
    reason: { type: 'string' },
  },
}

// AC-WDG-4: normalize the self-gate sub-agent's structured return to a well-formed verdict — PURE,
// framework-authored compute (no worker discretion). Any shape that is not an explicit `ran: true` +
// a PASS/FAIL verdict + (on FAIL) a NON-EMPTY typed `failing_checks` set is treated as
// `gate-unrunnable` — a malformed/absent/never-produced result is fail-closed, never silently accepted
// as a pass. This is a SECOND, independent floor beneath the self-gate script's own fail-closed exit
// code: even a sub-agent that mis-transcribes the script's JSON is caught here.
// (floor-#3 review R4) The normalized gate/verdict this function (and the Verify-stage
// agent below) produce are OBSERVABILITY, not merge authority — a 'ready'/PASS result never itself
// merges, approves, or authorizes a merge; the operator, or `hooks/foundry-git-discipline.sh`'s
// deterministic `gh` clause, decides whether a PR actually merges.
function normalizeSelfGateResult(gateRaw) {
  const wellFormed = gateRaw && typeof gateRaw === 'object' && gateRaw.ran === true
    && (gateRaw.verdict === 'PASS' || gateRaw.verdict === 'FAIL')
    && Array.isArray(gateRaw.failing_checks || [])
  if (!wellFormed) {
    return {
      ran: false, verdict: 'gate-unrunnable', failing_checks: [],
      reason: (gateRaw && typeof gateRaw.reason === 'string' && gateRaw.reason)
        || 'self-gate did not return a well-formed ran/verdict result (absent, malformed, or never invoked — fail-closed, AC-WDG-4)',
    }
  }
  if (gateRaw.verdict === 'FAIL' && (gateRaw.failing_checks || []).length === 0) {
    // AC-WDG-2: a FAIL verdict MUST name >=1 failing check id; an untyped FAIL is itself untrustworthy.
    return {
      ran: false, verdict: 'gate-unrunnable', failing_checks: [],
      reason: 'self-gate reported FAIL with an empty failing_checks set (untyped failure — fail-closed, AC-WDG-4)',
    }
  }
  return gateRaw
}

function selfGateFailReason(gate) {
  return gate.verdict === 'gate-unrunnable'
    ? `dod-self-gate-unrunnable (fail-closed, never accepted as ready): ${gate.reason}`
    : `dod-self-gate FAIL — failing checks: [${(gate.failing_checks || []).join(', ')}] — ${gate.reason}`
}

// Normalize args: the native Workflow named-invocation path can deliver `args` JSON-stringified;
// accept both the stringified and the verbatim-array form (defensive shim, backward-compatible).
const _args = (typeof args === 'string') ? (() => { try { return JSON.parse(args) } catch { return [] } })() : args

// Security finding 3 (injection guard) + the wave-plan id-vs-path BLOCK (both PR #271 reviews):
// `foundry-wave-plan.py`'s JSON carries atom IDS in `waves`, never spec paths — the caller MUST
// map every id through the plan's `atoms[id].spec_ref` before handing it here (see
// `skills/release/SKILL.md` step 3). Every dispatch element below is interpolated directly into an
// `agent()` prompt template literal (`${atom}`), so it must ALSO be validated as a real,
// path-shaped, non-traversing string BEFORE dispatch — never a bare id, never anything else. Fail
// CLOSED: reject (drop + log the offending element) anything that doesn't match, in BOTH the flat
// and wave-list shapes; a rejected element is never silently interpolated into a worker prompt.
const SPEC_PATH_CHARSET_RE = /^[A-Za-z0-9._/-]+$/

function isValidSpecPath(x) {
  if (typeof x !== 'string' || !x) return false
  if (!SPEC_PATH_CHARSET_RE.test(x)) return false          // charset: alnum, '.', '_', '/', '-' only
  if (!x.includes('/')) return false                        // a path, not a bare id
  if (!x.endsWith('.md')) return false                      // a spec file, not a directory/other type
  if (x.split('/').some((seg) => seg === '..')) return false // no '..' traversal segment
  return true
}

function filterValidSpecPaths(list, label) {
  const valid = []
  for (const x of list) {
    if (isValidSpecPath(x)) {
      valid.push(x)
    } else {
      log(`foundry-release-wave: REJECTED non-spec-path dispatch element in ${label}: ` +
          `${JSON.stringify(x)} (must be a path-shaped string — charset ^[A-Za-z0-9._/-]+$, ` +
          `contains '/', ends '.md', no '..' segment — never a bare atom id)`)
    }
  }
  return valid
}

// (feat-foundry-wave-plan): accept an EXPLICIT wave list — `{ waves: [[specPath, ...],
// ...] }` (the CALLER maps the wave plan's atom ids to spec_ref paths before calling — see
// skills/release/SKILL.md) — alongside the original flat-array shape. Dispatch behavior is
// unchanged (one fan-out, no barrier, for the flat shape); the spec-path VALIDATION above is new
// for both shapes (a security hardening, not a behavior change for any already-valid caller). Wave
// mode adds ONE barrier property the flat shape never had: every atom in wave N completes (impl ->
// self-gate -> verify) before wave N+1 starts, so a later wave's worker never races a same-path
// earlier-wave worker still landing its PR. Within a wave, atoms still fan out via the SAME
// `pipeline()` call the flat shape used — no new fan-out mechanism, just a sequential barrier
// between wave-scoped pipeline() calls.
const _waveListsRaw = (_args && !Array.isArray(_args) && Array.isArray(_args.waves)) ? _args.waves : null
const _waveLists = _waveListsRaw
  ? _waveListsRaw.map((wave, i) => filterValidSpecPaths(wave, `wave ${i}`))
  : null
const atoms = _waveLists
  ? _waveLists.flat()
  : (Array.isArray(_args) ? filterValidSpecPaths(_args, 'flat args') : [])
if (!atoms.length) {
  log('foundry-release-wave: no atoms in args (pass an array of authorized spec paths, or ' +
      '{ waves: [[specPath, ...], ...] } for an explicit wave-barriered run) — or every element ' +
      'given was rejected as non-spec-path-shaped, see the REJECTED lines above')
  return []
}
if (_waveLists) {
  log(`foundry-release-wave: wave mode — ${_waveLists.length} wave(s), ${atoms.length} atom(s) total`)
}

// Surface the model DECISION natively (the Workflow narrator): the fan-out impl + verify workers run on
// the cost-conscious 'sonnet' alias (down-pinned from the inherited session model via opts.model).
log(`foundry-release-wave: impl + verify workers on 'sonnet'`)

// feat-foundry-worker-dod-self-gate (AC-WDG-3): capture the DISPATCH-RECORDED base sha ONCE, up
// front — via a framework-authored (fixed-prompt, not worker-instructed) probe run BEFORE any
// implement worker starts, so it cannot be influenced by a worker's own report. Every atom in this
// wave is dispatched against the same base; the self-gate step below compares each worker's ACTUAL
// worktree base against this recorded value with a purely LOCAL git comparison (no network).
const _BASE_SHA_SCHEMA = { type: 'object', required: ['base_sha'], properties: { base_sha: { type: 'string' } } }
const _baseShaProbe = await agent(
  `Run exactly: git rev-parse HEAD  — in the session repo root ($CLAUDE_PROJECT_DIR). Return only the ` +
  `resulting commit sha in 'base_sha', nothing else.`,
  { label: 'dod-self-gate:dispatch-base-sha', phase: 'Implement', model: 'sonnet', schema: _BASE_SHA_SCHEMA },
)
const dispatchBaseSha = (_baseShaProbe && typeof _baseShaProbe.base_sha === 'string' && _baseShaProbe.base_sha.trim())
  ? _baseShaProbe.base_sha.trim() : null
log(`foundry-release-wave: dispatch-recorded base sha = ${dispatchBaseSha || '<UNRESOLVED — every self-gate step fails closed>'}`)

// the impl/verify functions are named consts (not inlined into the `pipeline()` call)
// so the SAME pair drives either a single flat fan-out (no wave barrier — the original shape) or a
// sequential per-wave fan-out (the barrier described above) without duplicating the agent prompts.
const implFn = (atom) => agent(
    `Implement the AUTHORIZED atom ${atom} against its frozen acceptance-contract.yaml. ` +
    `Do NOT weaken its checkpoints. Run the live-seam walk against each frozen checkpoint locator, ` +
    `then cut the PR via 'gh pr create'. ` +
    `Return the structured result, INCLUDING your learning records in 'learnings[]' (the durable ` +
    `worker-emission channel; do NOT leave learnings only in the worktree, which is auto-cleaned).`,
    { label: `impl:${atom}`, phase: 'Implement', model: 'sonnet', isolation: 'worktree', schema: IMPL_SCHEMA },
  )

const verifyFn = (impl, atom) => {
    // feat-foundry-run-duration-capture (AC-RDC-1/-7): `review` is carried EXPLICITLY as `null`
    // (never simply omitted) on both short-circuit branches below, so a run-shaped consumer
    // (the run-metrics persistence hook) can read `r.review` uniformly without needing to
    // distinguish "absent because JS dropped an undefined key" from "explicitly no review" — an
    // absent/null consolidated verdict is `failed`, never `landed` (AC-RDC-7).
    if (!impl || impl.status !== 'ready') return { atom, impl, gate: null, verdict: { verdict: 'FAIL', reasons: ['impl not ready'] }, review: null }
    // The bespoke worker-dod-self-gate script + the merge-gate live-seam
    // verdict it re-derived are both retired. The floor those controls carried moves to the native
    // CI battery (.github/workflows/ci.yml: pytest + graph selftests + doctor) plus the
    // always-reporting btb-gates lane signal (spec-link/security-path) — the framework RETURN PATH
    // still runs a SEPARATE, framework-authored (not worker-instructed) probe before accepting this
    // "ready" impl as ready, so a worker cannot silently skip it.
    return agent(
      `Run the CI command battery for AUTHORIZED atom ${atom} (branch '${impl.branch}', ` +
      `PR ${impl.pr_url || '<none>'}) LOCALLY against a worktree/checkout of that branch: every command ` +
      `.github/workflows/ci.yml invokes (the pytest suite, graph selftests, doctor), plus the ` +
      `frozen checkpoint locators from the atom's acceptance-contract.yaml (BY PATH, sibling to the atom's ` +
      `spec), run against the candidate branch AND RED-on-base (base sha ${dispatchBaseSha || '<UNRESOLVED>'}). ` +
      `Also confirm the PR carries its btb-gates lane signal (spec-link/security-path, honestly labeled ` +
      `Tier B advisory). Do not hand-wave any command's exit status. Return a structured result ` +
      `(ran/verdict/failing_checks/reason). If any command cannot be run at all, return exactly ` +
      `{"ran": false, "verdict": "gate-unrunnable", "failing_checks": [], "reason": "<the error>"} — ` +
      `never fabricate a PASS.`,
      { label: `dod-self-gate:${atom}`, phase: 'Implement', model: 'sonnet', schema: SELF_GATE_SCHEMA },
    ).then((gateRaw) => {
      const gate = normalizeSelfGateResult(gateRaw)
      if (gate.verdict !== 'PASS') {
        // AC-WDG-2 / AC-WDG-4: fail-closed — a self-gate that ran-and-failed (typed failing_checks)
        // OR could not be run to a verdict (gate-unrunnable) overrides 'ready' to 'failed' and SKIPS
        // the Verify stage entirely; this impl is NEVER reported as success.
        return { atom, impl: { ...impl, status: 'failed' }, gate, verdict: { verdict: 'FAIL', reasons: [selfGateFailReason(gate)] }, review: null }
      }
      return agent(
        `Run the native merge floor for atom ${atom}'s PR ${impl.pr_url || impl.branch}: confirm the ` +
        `.github/workflows/ci.yml command battery (the pytest suite, graph selftests, doctor) ` +
        `is GREEN on the candidate branch, and the PR carries its btb-gates lane signal (the always-` +
        `reporting spec-link/security-path checks, honestly labeled Tier B advisory — never a blocking ` +
        `merge-gate verdict). This verdict is OBSERVABILITY for the operator/native floor, not merge ` +
        `authority — it does not itself merge, approve, or authorize a merge of this PR. Also run (or ` +
        `re-use, if already run in this pass) the PR's review fan-out and return every Block/Risk/Nit/ ` +
        `Confirmed finding in 'findings[]' ({severity, category, location, rationale, evidence?, ` +
        `source?} each) — do NOT dedup or pre-filter them yourself, the workflow consolidates. Return ` +
        `the verdict.`,
        { label: `verify:${atom}`, phase: 'Verify', model: 'sonnet', schema: VERDICT_SCHEMA },
      ).then((v) => {
        // feat-foundry-review-fanout-hardening (AC-RFH-1..-14): dedup-before-verify + unverifiable-
        // stays-blocking, over the review fan-out's raw findings[] (the VERDICT_SCHEMA carrier
        // above). Consolidation happens ONCE per atom, on the RFH-PURE block's pure functions;
        // the dispatch list is exactly `consolidateFindings(...)`'s blocking output, which is what
        // makes the AC-RFH-4 dispatch-count invariant hold structurally rather than by convention.
        const rawFindings = Array.isArray(v && v.findings) ? v.findings : []
        const consolidated = consolidateFindings(rawFindings)
        const blockingFindings = consolidated.filter((f) => f.severity === 'Block')
        if (!blockingFindings.length) {
          const review = assembleReviewResult(consolidated, new Map())
          return { atom, impl, gate, verdict: v, review }
        }
        const VERIFICATION_SCHEMA = {
          type: 'object',
          required: ['verified'],
          properties: { verified: { type: 'boolean' }, reason: { type: 'string' } },
        }
        const dispatches = blockingFindings.map((f) => agent(
          `Verify (or refute) this consolidated Block finding for atom ${atom}: category ` +
          `'${f.category}', location(s) ${JSON.stringify(f.locations)}, rationale: ${f.rationale}. ` +
          `Return exactly {"verified": true} if you confirm this is a real, currently-present defect ` +
          `at the cited location(s). Return exactly {"verified": false, "reason": "<why it does not ` +
          `hold>"} ONLY for a well-formed, evidence-backed refutation — a non-empty 'reason' is ` +
          `required. If you cannot reach a verdict (no access, ambiguous, cannot reproduce), say so ` +
          `honestly in 'reason' rather than fabricating confidence either way.`,
          { label: `review-verify:${atom}:${f.dedupKey}`, phase: 'Verify', model: 'sonnet', schema: VERIFICATION_SCHEMA },
        ).then((result) => ({ key: f.dedupKey, result }))
          .catch((err) => ({ key: f.dedupKey, result: { __error: String((err && err.message) || err), reason: String((err && err.message) || err) } })))
        return Promise.all(dispatches).then((settled) => {
          const verificationMap = new Map(settled.map((s) => [s.key, s.result]))
          const review = assembleReviewResult(consolidated, verificationMap)
          return { atom, impl, gate, verdict: v, review }
        })
      })
    })
}

// The barrier: wave mode runs one `pipeline()` call PER WAVE, sequentially awaited — every atom in
// wave N (impl -> self-gate -> verify) settles before wave N+1's `pipeline()` call starts. Flat mode
// (no `waves` key in args) is BYTE-FOR-BYTE the original single fan-out — one `pipeline()` call over
// every atom, no barrier.
let results
if (_waveLists) {
  results = []
  for (let w = 0; w < _waveLists.length; w += 1) {
    const waveAtoms = _waveLists[w]
    if (!waveAtoms.length) continue
    log(`foundry-release-wave: wave ${w} — dispatching ${waveAtoms.length} atom(s): ${waveAtoms.join(', ')}`)
    // eslint-disable-next-line no-await-in-loop -- the barrier IS the point: wave N+1 must not start
    // until wave N's fan-out (impl -> self-gate -> verify, for every atom in the wave) has settled.
    const waveResults = await pipeline(waveAtoms, implFn, verifyFn)
    results.push(...waveResults)
  }
} else {
  results = await pipeline(atoms, implFn, verifyFn)
}

// The run-ledger persistence this journal line used to feed has been
// retired. The narration stays as plain per-atom dispatch
// observability — the native Workflow tool has no fs/import()/node: access (a pure-compute
// sandbox, see audit-workflow-runtime-decouple.py), so this workflow computes + narrates a
// per-atom dispatch map rather than writing a journal file itself. A 'done'/'failed' dispatch is
// definitive here (this IS the terminus); a wave that never reaches a given atom leaves it absent
// from the map.
const _dispatchOf = (impl, verdict) => (impl && impl.status === 'ready' && verdict && verdict.verdict === 'PASS') ? 'done' : 'failed'
const confirmed = results.filter(Boolean).map((r) => ({ ...r, dispatch: _dispatchOf(r.impl, r.verdict) }))
log(`foundry-run-ledger:journal ${JSON.stringify({ atoms: Object.fromEntries(confirmed.map((r) => [r.atom, { dispatch: r.dispatch }])) })}`)
log(`foundry-release-wave: ${confirmed.filter((r) => r.verdict?.verdict === 'PASS').length}/${atoms.length} atoms PASS`)
return confirmed
