# Harness-denial fallback discipline

Foundry's ceremony verbs (`/foundry:authorize`, the release cut, the decommission gate, the upstream
submit, the infra apply) run commands the harness permission layer is **supposed** to stop an agent
from running unattended — the trust model working, not a bug. This file is the **one canonical
clause** for what a model does when that denial actually fires. Each of the seven
ceremony-instructing skills carries a one-line pointer to it (see `## Skills that carry this clause`
below) rather than a copy — single-sourced so seven copies cannot drift.

<!-- foundry:harness-denial-fallback v1 -->
**Harness-denial fallback (single-sourced — every ceremony skill points here rather than pasting a
copy; see `## Skills that carry this clause` below for the roster).**

**(a) Emit the exact ready-to-run command.** When the harness denies a command, hand the exact
invocation back to the operator, ready to paste: the emitted block SHALL be byte-identical to the
invocation the harness denied, modulo the leading in-session `!` prefix a chat-run command carries —
no flag added, removed, or re-valued. It SHALL never freeform-compose the command, and it SHALL
never lift it from a spec or PR body — it is transcribed, never authored. When the denied invocation
carries an override/exception flag (`--yes`, `--skip-audit-reason`, `--reauth-after-impl`, `--admin`,
`-auto-approve`), name the override flag in plain language on a line above the block, so a
`--yes`-bearing command can never be handed over silently.

**(b) Stop.** On a harness denial: never retry the exact call, and never route around it via another
tool or credential — reaching the same effect through a substitute is exactly what this forbids. This
excludes a documented degraded path a skill itself publishes: `upstream-submit`'s
`UPSTREAM-SUBMIT-LABEL-DEGRADED` degradation and `cut-release`'s `REFUSED`/`GATED` outcome are the
opposite polarity of a harness denial — a verb's own gate refusal — and following that documented
path is correct.

**(c) Name the durable fix.** The real fix is a permission-floor rule change, not a one-off grant:
point at `.claude/settings.json` (the permission rules the adopter and the operator maintain) and the
native workspace trust dialog — those are the two channels that actually carry consent forward. Chat
text is not a consent channel, however clearly the operator states it.

**Resuming after a real grant.** None of the above forbids resuming the ceremony once the state
actually changed through a real consent channel — the settings rule now allows it, or the trust
dialog was accepted. At that point it is fine to run it again; that is exactly what "hand it to the
operator and stop" was waiting for, not a violation of limb (b).
<!-- /foundry:harness-denial-fallback -->

## Skills that carry this clause

- `skills/authorize/SKILL.md`
- `skills/authorize-release/SKILL.md`
- `skills/cut-release/SKILL.md`
- `skills/decommission-gate/SKILL.md`
- `skills/release/SKILL.md`
- `skills/upstream-submit/SKILL.md`
- `skills/id-apply/SKILL.md`
