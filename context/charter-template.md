# Charter template — the noninteractive-mode unit of work

A **charter** is the one-page spec for a single atom delivered in `noninteractive` posture. It is
deliberately NOT an atomic spec + frozen acceptance-contract: no deep-audit ceremony, no hash
freeze. The charter is committed to the workspace before the build starts — the commit IS the
record (git history is the ledger; the retired light-authorize verb + provenance marker this
template originally named were removed). Its **Scope** section
names the paths the build may touch. Requirements changed mid-build? Edit the charter and
commit again — a fresh history entry.

Copy the block below; keep it under a page. Delete guidance comments.

```markdown
# Charter: <imperative one-line goal>

**Date:** <YYYY-MM-DD> · **Mode:** noninteractive · **Repo:** <target repo>

## Goal
<2-4 sentences: what exists when this is done, and why now.>

## Acceptance criteria
<!-- EARS-phrased, singular, testable. 3-7 criteria; more means this is not one atom -->
- WHEN <trigger>, THE SYSTEM SHALL <measurable behavior>.
- WHILE <state>, THE SYSTEM SHALL <invariant>.
- IF <unwanted condition>, THEN THE SYSTEM SHALL <required response>.

## Done when
<!-- >=1 machine-checkable locator: test:<path>[::name] | cli:<command> | file:<path> |
     checkpoint:AC-<TOKEN>-<n>. The driver (deck tick / mode-autonomous) yields on these — a
     stop with none met and no escalate_when hit is a silent yield, not a finish. -->
- <locator>

## Escalate when
<!-- closed set only: external-provisioning | credential-step | no-consensus-after-research |
     security-widening | irreversible-action. Omit a member that does not apply; add none new. -->
- <member>

## Requires capabilities
<!-- OPTIONAL: what this build needs (network egress, a credential, a cloud CLI). A later change
     here is a boundary widening (AC-AMND-1(b)). -->
- <capability>

## Out of scope
<!-- what a reasonable implementer might assume is included, but is not -->
- <explicitly excluded item>

## Scope (write boundary)
<!-- literal paths only — no globs; these become the light marker's allowed/denied paths -->
allowed_paths:
  - <path/to/file-or-dir>
denied_paths: []          # security surfaces are refused by the lane itself; list extras here

## Verification
<!-- how "done" is demonstrated: the test/command a reviewer or CI runs -->
- <command or test name> → <expected outcome>

## Merge
operator-merges            # or: auto-merge-on-green (explicit opt-in, per PR)

## Amendments
<!-- The charter is a living document, adjusted during implementation when reality requires it.
Leave the table empty (header only) until the first amendment lands. -->
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
```

## Rules of the lane (unchanged by this template)

- Valid ONLY in `noninteractive` / `interactive` posture — `factory` mode routes to the full
  spec + `/foundry:authorize` flow instead.
- Security surfaces (auth / secrets / floor machinery) can never ride a charter — route them
  through the full spec flow, and the `security-path` CI gate flags the realized diff.
- Build in an isolated worktree (native Agent `isolation: worktree`); PR-then-merge; CI green +
  a fresh-context `pr-reviewer` pass before merge.

## Native permission-mode pairing (guidance, per posture)

The session posture and Claude Code's native permission mode are **separate controls that pair**
— the framework cannot set the native mode (hooks receive `permission_mode` read-only; only
settings `permissions.defaultMode`, the CLI flag, or the operator's gesture change it):

| posture          | recommended native mode        | rationale                                  |
|------------------|--------------------------------|--------------------------------------------|
| `factory`        | `plan` → `default`             | plan-first; edits gated while specs settle |
| `noninteractive` | `acceptEdits` (or `auto`)      | autonomous single-atom delivery            |
| `interactive`    | `acceptEdits`                  | fast vibe/debug loop, operator present     |

Set a workspace default in `.claude/settings.json` → `permissions.defaultMode`; flip per-session
with the native gesture (Shift+Tab) — the pairing is a recommendation, never enforced.
