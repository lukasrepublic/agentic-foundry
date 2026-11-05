---
name: upstream-submit
description: 'Guard an upstream contribution (/foundry:upstream-submit). Before an adopter sends a change to the public Foundry repository (a kit rule, a blueprint, a fix), this runs the governance leak guard over exactly the files in the submission and REFUSES if any file is under governance/local/, carries an adopter rule id (anything but FG-), sits on a path the governance lock marks adopter-owned, or contains a product literal. Trigger when the operator says "/foundry:upstream-submit", "send this upstream", "contribute this kit rule", or before opening any pull request against the framework repository from a workspace.'
---

# /foundry:upstream-submit

Adopters contribute to the framework from a workspace that holds private material: the adopter layer
(`governance/local/`), rule ids with their own prefix, names of their products. None of it may leave. This skill
puts one mechanical check in front of the submission (design 5.6): **it refuses; it does not redact.**

## What is refused (each is a finding naming the path and the kind, never the matched text)

| Finding | Meaning |
|---|---|
| `governance-local-path:<path>` | any file under `governance/local/` |
| `adopter-owned-path:<path>` | a path the lock marks adopter-owned: `governance/rendered/**`, `.foundry/governance.lock`, `.foundry/governance-intake.json`, and every glob in the lock's `declarations.trust_boundaries` |
| `private-rule-id:<path>:<line>` | a rule id whose prefix is not `FG-` (an `<ORG>-` id). `FG-` ids pass |
| `product-literal:<path>` / `product-id-pattern:<path>` | a string from the workspace's product-literal list, or a domain-id shape the blueprint HOW/WHAT guard already denies |
| `unreadable:<path>` | the file could not be read (never treated as clean) |

## Procedure

1. **List exactly the files the submission would send** (the diff against the upstream base, not the working
   tree): `git diff --name-only <upstream-base>...HEAD`.
2. **Run the guard.** The product-literal list is the workspace's private list, one literal per line, kept
   outside the repository you are contributing to.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" leak-check --files <path> [<path> ...] [--literals <file>]
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-prepublication-leak-scan.py" --governance-submission --root . --files <path> [<path> ...] [--literals <file>]
   ```

   Exit 0 and `GOVERNANCE-SUBMISSION-CLEAN` is the only pass. Exit 1 / `GOVERNANCE-SUBMISSION-REFUSED` lists
   the findings: **stop**. Remove the file from the submission or move the content to `governance/local/`
   (where it stays private); do not edit the finding away by renaming an id.
3. Only after a clean result, open the pull request against the upstream repository.

## The kit-fix ticket (input from the learned distill)

At programme close, `scripts/foundry_learned_distill.py propose <programme> --from-json <comments.json> --out <dir>`
(dl-10) turns the `## Learned` comments of the programme's atoms into proposals. A lesson that names an `FG-` rule
id, or carries `[upstream]`, becomes a kit-fix ticket body, `<dir>/upstream/kit-fix-<n>.md`, with exactly these
sections: `## Kit` (the category), `## Rule` (the `FG-` id, or `none (new rule proposed)`), `## Lesson`,
`## Proposed edit`, `## Evidence`. A ticket carries no atom id, no issue URL and no adopter text: those stay in the
private `<dir>/proposals.json`.

The distill runs the leak guard over every ticket itself. A lesson that names a private rule id, or fails the
guard, is kept private and goes to the `governance/local` pull request instead. It never reaches an upstream file.
Run the guard in step 2 over the ticket files again before you file them (`gh issue create --body-file`). The
distill opens nothing unless you pass `--open-tickets` (and `--open-prs` for the private pull request); a
maintainer reviews every ticket.

## Never

- Never submit a file because "the matched text is harmless": the operator decides what is public, and the
  guard has no override.
- Never run the guard over the whole working tree and ignore the findings that are not in the diff - the
  findings that matter are the ones in the files that leave.
- Never echo a refused id or literal into the pull request, the issue or the chat; cite the path and line.

This guard is a mistake-catcher for the common copy-the-private-thing error. The operator's own review of the
public pull request remains the final check before anything is published.
