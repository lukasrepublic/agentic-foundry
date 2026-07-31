---
name: research-capture
description: The durable research-evidence-trail discipline. At a deep-research-bearing research-first gate, persist the FULL claim-level trail (verified ∧ REFUTED) as <run-id>-<slug>.md at the configured governance.research_path (defaults to .foundry/research/) from the research-artifact template, and author the ADR at the configured governance.decisions_path (defaults to .foundry/decisions/) with a `## Refuted / disconfirming evidence` section — so a later auditor can check whether the claimed "industry consensus" was real. A discipline (a template + an author convention), NOT a code helper. Trigger after a deep-research run feeds a design decision.
---

# /foundry:research-capture — durably persist the research gate's full evidence trail

The research-first `record` phase writes a **distilled** decision record (at the configured
`governance.decisions_path`, defaults to `.foundry/decisions/`) — the *why*. But the **full claim-level evidence** behind a `deep-research` run (every verified claim, its
adversarial vote, the source table, and crucially the **refuted / killed** claims) lives only in an
**ephemeral** workflow-output file and is lost on cleanup. So a later auditor asking *"was this 'industry
consensus' actually real?"* has only the author's summary. This discipline closes that gap **the standard
way** — a durable **template** + an **author convention**, exactly as the ADR / decision-record ecosystem
does (adr-tools / log4brains / MADR ship `new`/`link` scaffolders + templates; **none** ship a
byte-identical emitter or a fail-closed completeness classifier).

## It is a DISCIPLINE, not a code helper (the deep-spec-audit re-ground)

The v1 design — a deterministic byte-identical *capture helper* + a fail-closed *completeness validator* —
was **novel machinery for a solved problem** (a deep-spec-audit category-error). Determinism / byte-identity has **no
prior-art basis** for a human-read prose doc (it governs machine-consumed attestations, not narratives).
The hand-written `.foundry/research/wf_c3c6df43-3b5-machinery-view.md` — complete, with a *Refuted/Killed*
section + both-way ADR links, authored with **zero** new code — is the decisive disproof. So capture is
**authoring from a template**; the lightweight drop-in wiring lint this line originally shipped
was removed with the drop-in-check registry and was not ported (see the note below) — capture
ships no code.

## The discipline

At a **deep-research-bearing** research-first gate (a multi-source run, not a mechanical/obvious-standard
decision):

1. **Persist the full trail.** Author `<run-id>-<slug>.md` at the **configured governance-record research
   location** — `governance.research_path` in `.claude/foundry-project.json` (defaults to
   **`.foundry/research/`**) — from `research-artifact-template.md` (this skill's sibling). The
   `<run-id>-<slug>` filename is **path-safe** (`[a-z0-9._-]`). Required sections: the **run-id**, the
   **question**, the **stats** (sources / claims / verified / confirmed / refuted), the **verified
   claims**, the **`## Refuted / disconfirming evidence`** section (**REQUIRED — present even if
   `(none)`**), the **sources** table, and a **both-way link** with the decision record.
2. **Retain the disconfirming evidence in the ADR too.** Author the ADR — at the **configured
   governance-record decisions location** (`governance.decisions_path` in `.claude/foundry-project.json`,
   defaults to **`.foundry/decisions/`**) — **with a `## Refuted / disconfirming evidence` section**
   (Nygard *Consequences*-style) so the killed/disconfirming claims are retained in the decision record
   itself as well as the linked artifact.
3. **Link both ways.** The ADR points at the artifact; the artifact points at the ADR.

**The one load-bearing rule:** the **Refuted / disconfirming evidence is NEVER dropped.** A conclusion
retains its disconfirming evidence — that is what lets a later auditor re-judge whether the consensus held.

This is woven into the research-first `record` phase (`skills/research-first/SKILL.md`) — capture is not a
separate step bolted on; it is *how* a deep-research-bearing gate records.

## Honest limitations (advisory, not enforced)

- **Advisory, not a hard gate.** The discipline instructs persistence at a research gate; per-run
  enforcement (a gate that rejects an un-captured run) is a deferred hook (touches `hooks/**`) — the same
  posture as `operator-status-protocol` / `research-first`.
- **Deep-research-bearing gates only.** A mechanical/obvious-standard decision that ran no multi-source
  research has no trail to persist; the discipline does not manufacture one.
- **Fidelity is the author's** (cooperating-operator). The discipline preserves the *shape* (incl. the
  Refuted section); it cannot verify the author transcribed the run faithfully — the same trusted-author
  bound every ADR convention rests on.

## Exercise

**Currently unverified by machine (named honestly).** The drop-in **wiring lint**
(a drop-in per-check selftest, AC-RCAP-1..3 — confirming
`research-artifact-template.md` carries its required `## Refuted` section and that
`skills/research-first/SKILL.md`'s `record` phase references this discipline) and its
`foundry-doctor.py --research-capture-selftest` registration were removed with the rest of the
drop-in-check registry (the v0.25.0 test-suite realignment, the doctor-thinning to a 5-check probe — see
`skills/doctor/SKILL.md`) and were **not** ported to the `tests/` pytest suite. The discipline
itself is unaffected (it was always advisory prose + a template, never a hard gate — see "Honest
limitations" above); what is gone is the cheap presence check that the template + the
`research-first` cross-reference haven't silently drifted apart. A future port belongs in
`tests/` (CONSTITUTION.md §8) as a plain file-presence/grep-shaped assertion, not a new drop-in CLI.
