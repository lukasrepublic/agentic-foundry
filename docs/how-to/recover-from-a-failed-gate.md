# How to recover from a failed gate

Every gate refusal in Foundry is written to be quotable and names its cause. This guide maps
each refusal to its recovery, in the order you'll meet them in the loop.

```
 where it failed          what refused              your move
 ──────────────           ────────────              ─────────
 edit a doc      ──▶      paper guard               add the path to the ticket's `Paper allowed`
 stop            ──▶      Stop hook                 `Done means` failed: fix the work (3 refusals max)
 push / PR       ──▶      git-discipline clause (j) run scripts/foundry-test.sh until green
 PR checks       ──▶      security-path-base        run the security reviewer, apply the label
 merge           ──▶      git-discipline hook       fix the red check; no bypass exists
 authorize       ──▶      a freeze floor            (opt-in spec lane) re-specify the contract
```

## 1. The paper guard refused a write

A write under `specs/`, `intake/`, `.foundry/`, `docs/`, `status-reports/` or `charters/` (or to a
contract / manifest file) needs the active ticket to allow it. Either the path belongs in the
ticket's `## Paper allowed` list (`scripts/foundry-ticket.py allow <path>`), or — usually — the
document should not be written at all. Code, infra, tests and config are never gated.

## 2. The Stop hook would not let the session end

The ticket's `Done means` command is failing. Read its output and fix the work. After three
refusals per ticket the session may end with the failure named, so a wrong `Done means` cannot
trap you; correct the ticket instead.

## 3. The push or the PR was refused

The git-discipline hook (clause (j)) refuses `git push` and a non-draft PR until
`scripts/foundry-test.sh` has recorded local-green for HEAD. Run it and fix what it reports; commit
again and the record is for the new HEAD. CI confirms; it does not discover.

## 4. The `security-path-base` check failed

The diff touches an auth / secrets / supply-chain path and carries no review label for this head.
Run the `security-reviewer` agent against the current diff, then apply the
`security-reviewed:<head12>-<base8>` label the check prints. Pushing a new commit invalidates the
old label by design.

## 5. The merge was refused

See the same section in [troubleshooting.md](../troubleshooting.md#the-merge-was-refused) —
short version: fix the red check; `--admin` has no supported path; a pending check means
wait.

## 6. `authorize` refused to freeze (opt-in spec lane)

A freeze floor failed → the output names it (empty `allowed_paths`, a checkpoint whose
locator can't bind, a bijection break between AC-IDs and checkpoints). The contract is
under-specified: re-author it. **The gate is never the thing to relax.**

## The one rule across all of them

**Fix the thing the gate named; never the gate.** Every refusal above is the tool doing its
job. If you believe a refusal is a false positive, that's a bug worth filing — with the
refusal text — rather than a reason to bypass.
