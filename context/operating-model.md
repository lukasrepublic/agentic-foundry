# The operating model — one page

**One artifact per change: a ticket with a runnable "Done means". The platform is the gate.**

## The unit of work is a ticket

A GitHub issue (or the forge's equivalent), one screen at most:

```
## What
Repoint beta DNS to the new ALB.
## Why
The old ALB is being decommissioned Friday.
## Done means
```
dig +short beta.example.test | grep -q 203.0.113.10 && curl -fsS https://beta.example.test/healthz
```
## Paper allowed
docs/runbooks/dns.md
```

- `Done means` is a **command**, not a sentence. It is what the agent runs before it stops, what
  the reviewer runs, and what the operator runs for sign-off.
- `Paper allowed` lists the documents this change may touch. Nothing else under `specs/`,
  `.foundry/`, `docs/`, `status-reports/`, `charters/` may be written for this ticket.
- Assignment of the ticket **is** the authorization. The operator's standing grant is recorded
  once in `.claude/foundry-operators.json` (`standing_authorization: true`), not re-asked per change.

Start it in the session: `python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py" start <issue>`.

## The loop

1. Branch from `main`: `git switch -c <ticket>-<slug>`.
2. Build on the branch. Run `Done means` and the repo's own tests **locally** —
   `bash "$CLAUDE_PLUGIN_ROOT/scripts/foundry-test.sh"` — until green. In a container this is
   the real stack (compose / DinD), not a mock.
3. Push once and open **one** PR per ticket (draft until local-green). The push and the
   non-draft PR are refused by the git-discipline hook until `foundry-test.sh` has recorded
   local-green for HEAD. CI confirms; it does not discover.
4. One review pass by a reviewer that is not the author: correctness and requirement gaps only,
   one round. Security-reviewer in addition on auth / secrets / custody / production-data paths.
5. Merge when the platform says green. Deploy on promote (tag or label), not on every merge.
6. Close the ticket with the `Done means` output. That is the record.

## The floor (never relaxed)

- Branch protection + required CI checks on `main`; PR-then-merge; no force-push.
- Security review (separate context) on any auth / secrets / supply-chain / custody change.
- The leak guard for anything public.
- The operator's own walkthrough before a release is called delivered.

## What is deliberately absent

No acceptance contracts, hash freezes, per-atom authorization, audit ledgers, spec-review
rounds, release manifests, `state.yaml`, provenance markers, status reports, charters or
command-deck ticks on the default path. A spec is **opt-in, chosen by the operator** — for
`security: true` work or a design too large for a ticket — never chosen by the agent.

## The agent's conduct

- Do not stop on reversible work. The Stop hook runs `Done means`; while it fails the session
  continues (three refusals, then it may end with the ticket still open and the failure named).
- Do not ask what prior art plus sensible defaults can answer. Ask only on an irreversible,
  security or authorization-adjacent fork, naming the exact decision.
- **Proposing a hook, gate, ledger, sign-off, status report, "commentary PR" or new programme
  is a defect.** The ticket and its check are the only artifacts the agent creates unasked.
- Measure, don't report: `python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-delivery-metrics.py"`
  prints one line per repo per week — paper:code, PRs merged, lead time. If paper:code rises
  above 0.3 in a workspace, the next ticket is "delete paper", not "write more".
