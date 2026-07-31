# Research artifact: <run-id> — <slug>

> Durable, claim-level evidence trail for a **deep-research-bearing** research-first gate. Authored FROM
> this template by the session that ran the gate — there is **no code emitter** and **no byte-identity /
> determinism requirement** (a prose narrative is not a machine attestation). Persist it at the
> **configured governance-record research location** — `governance.research_path` in
> `.claude/foundry-project.json` (defaults to **`.foundry/research/`**) — as `<run-id>-<slug>.md` (the
> `<run-id>-<slug>` is **path-safe** — `[a-z0-9._-]` only).
>
> **The one load-bearing rule:** the **`## Refuted / disconfirming evidence`** section is **ALWAYS
> present** (even if `(none)`) — the disconfirming / killed claims are **NEVER dropped**. A conclusion
> retains its disconfirming evidence (the scientific-provenance principle; Nygard ADR *Consequences*).

## Run

- run-id: `<wf_...>`
- date: `<YYYY-MM-DD>`

## Question

<the framed decision / fork this run researched — one paragraph>

## Stats

- sources: `<N>`
- claims: `<N>`
- verified: `<N>`
- confirmed: `<N>`
- refuted: `<N>`

## Verified claims

1. <claim> — <adversarial vote, e.g. 3/3> — <source ref(s)>
2. …

## Refuted / disconfirming evidence

<!-- REQUIRED — present even if "(none)". The refuted / killed / disconfirming claims are NEVER dropped:
     this section is the whole reason the artifact exists beyond the distilled decision record. -->

1. <refuted / killed claim> — <why refuted, e.g. 0/3, contradicted by …> — <source ref(s)>
2. … <!-- or a single line: "(none) — every researched claim was confirmed" -->

## Sources

| # | source | url |
|---|--------|-----|
| 1 | <publisher / doc> | <https://…> |
| 2 | … | … |

## Decision record

- Both-way link: this artifact ⇄ the ADR at the **configured governance-record decisions location** —
  `governance.decisions_path` in `.claude/foundry-project.json` (defaults to **`.foundry/decisions/`**) —
  as `<adr-slug>.md` (the distilled ADR carries its own `## Refuted / disconfirming evidence` section;
  this artifact carries the full claim-level trail).
