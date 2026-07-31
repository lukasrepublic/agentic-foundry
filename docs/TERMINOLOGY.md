# Foundry Terminology Standard

> The canonical, industry-grounded vocabulary and trigger grammar for agentic-foundry. Every **skill**,
> **agent**, and **playbook** conforms to this. Terms are rooted in authoritative sources (Anthropic
> *Building Effective Agents* / Agent Skills, Claude Code, the Model Context Protocol spec, SLSA/in-toto,
> the Google SRE workbook, GitOps/ArgoCD, kubectl); foundry coinages are kept only where they carry a
> precise referent, which is glossed inline. See [Sources](#sources).

## 1. Why this exists

A shared vocabulary is load-bearing for an agentic factory: the **`description` field of a skill is the
only surface the model matches a request against**, so imprecise or non-standard terms degrade triggering
and onboarding. This standard fixes (a) what each primitive *is*, (b) how the operator *targets* it, and
(c) how new machinery is *named* — so the library stays guessable and the triggers stay unambiguous.

## 2. The lexicon

Legend — **ADOPT**: foundry already uses the standard term correctly · **GLOSS**: a foundry coinage kept
for its precise referent (named here) · **RESERVED/RENAMED**: usage corrected to remove a collision.

| Term | Definition (industry-grounded) | Boundary vs nearest sibling | Status |
|---|---|---|---|
| **skill** | A `SKILL.md` (YAML frontmatter `name` + `description` + instructions) packaging a procedure the model loads on demand; the `description` is pre-loaded so the model knows *when* to use it (progressive disclosure). | A **skill** is *procedural knowledge the model pulls in*; a **tool** is *an executable function it calls*; a **subagent** is *a separate context that runs it*. | ADOPT |
| **agent** | A system where the LLM *dynamically directs its own process and tool use* in a loop on environmental feedback. | An **agent** decides its own path; a **workflow** follows a predefined one. | ADOPT |
| **subagent** | A specialized agent instance running in its **own context window**; only its summary returns to the main thread. | A **subagent** is an *isolated-context delegate* (foundry's `pr-reviewer`, `security-reviewer`); a **skill** is the *instructions* it may run. | ADOPT |
| **tool** | An executable, model-invoked function that acts or fetches data. In MCP, one of three server primitives. | A **tool** *does* (side effects); an MCP **resource** *reads* (no side effects); a **skill** *instructs*. | ADOPT |
| **workflow** | "LLMs and tools orchestrated through **predefined code paths**" (patterns: prompt-chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer). | A **workflow** = fixed orchestration code (foundry's `release-wave` fan-out); an **agent** = model-directed. | ADOPT — **reserved** for the predefined-path sense |
| **playbook** | A **task-triggered, model-invoked skill that loops to an EXIT CRITERION**, with governance floors orthogonal. The artifact is a `SKILL.md`; the *pattern* is what SRE calls a **playbook** (judgment-bearing procedural guidance) vs a **runbook** (fixed steps). | A **playbook** is *judgment-bearing + looping* (foundry's `cut-release`); a **workflow** is *a fixed code path*; a plain **skill** need not loop. | **NEW** (replaces "micro-workflow") |
| **command / slash-command** | User-invoked control: `/<name>` (or `/<plugin>:<name>`) explicitly runs a skill. The *user* decides, vs the model deciding by `description`. | Same artifact as a **skill**, two trigger modes: explicit (`/cut-release`) vs model-matched. | ADOPT |
| **verb** | The action token of a command (`kubectl <verb> <resource>`, `git <verb>`) — the learnable CLI shape. | A **verb** is the *intent*; the **object/resource** is the *target*. | ADOPT |
| **hook** | A command/prompt that fires **deterministically on a lifecycle event** (edit, tool call, session start, stop). | A **hook** is *event-triggered + deterministic*; a **skill/playbook** is *task-triggered + model-mediated*. | ADOPT |
| **MCP** | Open protocol exposing server **primitives** — tools (executable), resources (read-only data), prompts (templates). | An MCP **resource** ≠ a **tool**: resource = read, no side effects; tool = action. | ADOPT |
| **primitive** | A first-class platform building block (MCP: tools/resources/prompts; Claude Code: skills/subagents/commands/hooks). | A **primitive** is a *capability type*; an **atom** is a *unit of work built from them*. | ADOPT |
| **gate** | A **quality gate**: a checkpoint enforcing a codified threshold before progression ("standards made automatic"). | A **gate** *blocks progression on a check* (a required status check on `main`); a **floor** is *which invariants it enforces*. | ADOPT |
| **floor** | An **always-on, non-relaxable invariant/control** (front-authorization, the merge floor, security review, typed contracts). Industry referent: **guardrail / baseline control**. | A **floor** is the *standing invariant*; a **gate** is the *enforcement point* where a floor is re-derived. | GLOSS — "always-on guardrail" |
| **exit-gate** | The **loop-termination condition (exit criteria / stop condition)** of a playbook. | An **exit-gate** *ends a loop*; the **merge floor** *blocks a merge*. | GLOSS — "exit criteria / stop condition" |
| **engine** | A **pure, total, fail-closed decision function** (`decide_apply`, `classify_gitops`). Industry referent: **decision / policy engine** (cf. OPA). | An **engine** is *deterministic computation over inputs*; a **gate** *uses* its verdict to block. | GLOSS — "decision/policy engine" |
| **atom** | The **atomic unit of delivered change** — one authorized spec, built to merge in one PR. Industry referents: **work item / change unit / increment**. | An **atom** is a *unit of change*; a **primitive** is a *capability type*; a **floor** is an *invariant over atoms*. | GLOSS — "atomic work-item" |
| **live-seam** | The **runtime verification surface** (run the app/infra, observe behavior) that certification exercises — journeys against the deployed release. Industry referents: **acceptance / smoke / end-to-end verification**; Feathers' **"seam"**. | The **live-seam** is *the surface*; **certify-local** is *the verb that exercises it*. "live" = against a running system, not static analysis. | GLOSS — "runtime acceptance-verification seam" |
| **provenance** | Verifiable record of where/when/how an artifact was produced; SLSA provenance in the in-toto attestation format. | **Provenance** = *the committed record* (foundry's `build-provenance.yaml`); an **attestation** = *the envelope*. | ADOPT |

## 3. The trigger grammar

All four authoritative trigger models converge on the **imperative verb-object** form — the CLI
`<verb> <object>` (kubectl/git/docker), the NLU **intent (verb) + slot (object)** model, and the Agent
Skills **`description`-match** surface.

**Canonical shape:** `<verb> <object> [<qualifier>]`
- **verb** — from the standard palette (§5), the *intent*.
- **object** — a named artifact: `spec`, `atom`, `release`, `feature`, `PR`, `drift`, `incident`, `doctor-red`.
- **qualifier** — the disambiguator: a path, an id, a version, an env.

**The description rule (mandatory for every skill/playbook):** because the `description` is the only
surface the model matches on, every `description` MUST enumerate **(1) the canonical verb-object** and
**(2) 2–3 natural-language trigger phrases**. Docs always show **both** so the operator learns the
verb-object form and the model learns the natural-language aliases.

## 4. Example prompts per machinery type

| Machinery | How it triggers | Explicit / slash form | Natural-language trigger (lives in the `description`) |
|---|---|---|---|
| **Playbook** (guarded looping skill) | Model-invoked by `description` match; loops to its exit criteria | `/foundry:cut-release v0.7.0` | "cut the v0.7.0 release" · "ship the release" |
| **Skill** (model-invoked) | Model reads `description`, decides to load | `/foundry:intake` (also user-invocable) | "turn this PRD into a spec" → `intake` · "capture this session's learnings" → `learn-capture` |
| **Slash-command** (explicit) | The **user** decides; deterministic | `/foundry:authorize <spec-path>` | "authorize the spec at `<path>`" (the natural-language alias of the same skill) |
| **Subagent** (delegated) | Dispatched into an isolated context; summary returns | (via the dispatch/Agent path) | "run a security review on the PR #51 diff" → `security-reviewer` · "give me a fresh code-review pass" → `pr-reviewer` |

**Verb-object ⟷ natural-language, side by side** (the unprefixed names below illustrate the
naming SHAPE for playbooks an adopter authors — only `/foundry:`-prefixed names are shipped
verbs; `implement-feature`, `drift-reconcile`, `diagnose-doctor-red`, `respond-to-incident`
are naming-standard examples, not shipped commands):

```
INTENT(verb)  OBJECT(slot)   QUALIFIER     canonical                  natural-language variant
authorize     spec           <path>        /foundry:authorize <path>  "authorize the spec at <path>"
cut           release        <version>     cut-release v0.7.0         "cut the v0.7.0 release"
implement     feature|atom   <id>          implement-feature #50      "build atom #50"
reconcile     drift          <env>         drift-reconcile staging    "fix the drift in staging"
diagnose      doctor-red     —             diagnose-doctor-red        "doctor is red — figure out why"
respond       incident       <ref>         respond-to-incident <ref>  "we have an incident on <svc>"
```

## 5. Library naming standard

**Convention:** `verb-object`, hyphenated imperative; **verb from the standard palette**, object a
recognized artifact — the kubectl verb-noun shape, so the library is *guessable*.

**Standard verb palette** (sourced, recognizable): `specify · plan · implement · test · review · verify ·
audit · authorize · cut · deploy · observe · diagnose · reconcile · revert · document · postmortem ·
respond`.

| Proposed playbook name (naming review — these are naming verdicts, not shipped verbs) | Verdict | Grounding |
|---|---|---|
| `specify` (← was `develop-spec`) | **RENAME** | SDLC "specification" phase; "develop" was vague (cf. Spec Kit `/specify`). |
| `cut-release` | KEEP | "cut a release" — canonical release-engineering idiom. |
| `implement-feature` | KEEP | "implement" — standard SDLC build verb. |
| `debug-fix` / `diagnose-fix` | KEEP | "debug" standard; "diagnose" matches SRE triage. |
| `diagnose-doctor-red` | KEEP | "diagnose/triage" SRE-standard; "doctor" = the named health check (proper noun). |
| `drift-reconcile` | KEEP | exact ArgoCD/Flux terms (desired vs live state). |
| `deploy-verify` | KEEP | "deploy" + "verify" both standard; matches the observe-only deploy posture. |
| `respond-to-incident` | KEEP | "incident response" — exact SRE term. |

## 6. The playbook authoring convention

A **playbook** is a `SKILL.md` that declares the four-part shape (this is the template extracted from the
first playbook, `cut-release`):

- **TRIGGER** — "when the operator is doing ___" (description-matched; carries the verb-object + NL phrases per §3).
- **ENGINE** — the reusable pattern it runs (e.g. `deterministic-seam-drive`, `disposition-loop`, `plateau-audit`).
- **EXIT CRITERIA** (exit-gate) — one of: **deterministic-seam** (a machine verdict flips PASS) · **convergence-plateau** (K rounds no-new) · **disposition-complete** (every item routed) · **completeness-bar** (a threshold met) · **advisory** (surfaces, doesn't block).
- **FLOORS TOUCHED** — which always-on guardrails its *actions* trip (authorize? merge floor? security?). Floors are **orthogonal** — declared, never inlined as steps.

## Sources

Anthropic *Building Effective Agents* (workflow-vs-agent; the five patterns) · Anthropic *Agent Skills*
(`SKILL.md`, `description` as trigger, progressive disclosure) · Claude Code docs (skills / subagents /
slash-commands / hooks) · Model Context Protocol spec (tools / resources / prompts) · OpenAI Agents SDK
(handoffs, guardrails, run/stop) · NLU intent/utterance/slot (Microsoft, Rasa) · kubectl verb-noun
reference · Google SRE workbook (SLO/SLI/error-budget/toil/runbook/playbook/postmortem) · ArgoCD/Flux
(drift/reconciliation) · Sonar/Perforce quality-gates · SLSA + in-toto (provenance/attestation) ·
M. Feathers *Working Effectively with Legacy Code* ("seam").
