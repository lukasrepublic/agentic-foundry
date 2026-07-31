# Quickstart — zero to your first governed merge

Every command below is copy-paste-runnable against **v1.0.0**. If a command here ever
drifts from the shipped CLI, that's a bug — file it (a CI doc-drift test locks the pins
on our side).

What you'll produce, artifact by artifact:

```
 you type                      what appears in your repo
 ────────                      ─────────────────────────
 /foundry:init            ──▶  .claude/foundry-operators.json   (who may authorize)
                               .claude/foundry-project.json     (project config)

 /foundry:intake "…"      ──▶  specs/features/<domain>/<cap>/
                                 ├─ feat-<cap>.md               (atomic spec, stable AC-IDs)
                                 └─ acceptance-contract.yaml    (observable checkpoints)

 /foundry:spec-review     ──▶  review recorded, content-bound to the spec's hash

 /foundry:authorize       ──▶  acceptance-contract.yaml gains a frozen, signed
                                 `authorized:` block             (the point of no drift)

 /foundry:dispatch        ──▶  an isolated worktree → a PR → YOUR checks decide the merge

 /foundry:certify-local   ──▶  per-atom pass/fail against one real running instance

 /foundry:release accept  ──▶  your sign-off recorded            (a note, not a gate)
```

## Prerequisites

- **Claude Code** (CLI or desktop).
- **python3** with `pyyaml`, `jsonschema` (`pip install -r requirements.txt`, runtime deps) and
  `pytest` (`pip install -r requirements-dev.txt`, dev deps).
- **node 22+** — for the Workflow templates and Playwright journeys (the graph MCP server is Python).
- A repo you own, on GitHub, with CI you trust (the floor derives from YOUR checks).

## The minimal path

The six-verb core loop, on one repo, with nothing else read first. The full governance model —
the hook layer, the merge-floor tiers, certification detail — is covered afterward in
**the full install**, below.

## 0. Install

```bash
claude plugin marketplace add lukasrepublic/agentic-foundry#v1.0.0
claude plugin install foundry@agentic-foundry
```

Confirm inside a session in your repo:

```
/foundry:doctor        # → DOCTOR-GREEN (5 probes: manifest, hooks, skills, profile lock, operators)
```

## 1. Wire your repo (once)

```
/foundry:init
```

This seeds the **operator registry** (`.claude/foundry-operators.json` — add yourself) and the
project config (`.claude/foundry-project.json`). Then set up your merge floor: run
`scripts/foundry_tier_preflight.py --repo <owner>/<repo> --context <your-gate-checks> --apply` to
apply the shipped ruleset template and print the honest tier (`TIER-A`, `TIER-B` with its cause,
or `PREFLIGHT-ERROR`) — see [merge-floor.md](merge-floor.md) for what each verdict means and why
a created ruleset is not, on its own, evidence of enforcement. (`init` does not apply branch
protection itself; `foundry_tier_preflight.py` is the command that does.)

**Existing codebase?** Run `/foundry:extract-spec` first — it surveys the code and promotes a
chosen capability into a candidate spec, which then rides the exact same loop below.

## 2. From fuzzy ask to reviewed spec

```
/foundry:intake "users can export their data as CSV"
```

Interactive discovery → an **atomic spec** (stable AC-IDs, a delimited normative region,
prior-art grounding) + a sibling **acceptance contract** declaring observable checkpoints.
Specs are capped — hard — at 14 acceptance criteria / 8,000 words; oversize means decompose,
there is no override flag.

```
/foundry:spec-review specs/features/export/csv/feat-export-csv.md
```

Deterministic pre-lints first (size ceiling, reference closure — zero tokens), then three
fresh-context reviewer questions (prior-art; steel-man + adversarial; per-AC rubric), one
remediation round, and the review is recorded content-bound to the spec's hash.

## 3. Authorize (the front gate)

```
/foundry:authorize specs/features/export/csv/feat-export-csv.md
```

You see every checkpoint; you confirm; the spec + contract hashes are frozen and signed with
your operator id. **An unauthorized spec cannot reach `main` through the factory — there is
no skip.** (Authorization is yours; the tool only does the freezing.)

## 4. Build

```
/foundry:dispatch feat-export-csv
```

An implementer persona builds the atom in an **isolated git worktree** against the frozen
contract and opens a PR. Merges wait on your repo's own checks — details in **the full
install**, below.

Prefer hands-on? `/foundry:mode-interactive` is the zero-ceremony lane: plain Claude Code,
you implement and review yourself. Small changes deserve small process.

## 5. Certify against the real thing

```
/foundry:certify-local export-v1
```

Deploys the release **once** locally (your stack profile's boot recipe), then runs every
atom's tagged Playwright journeys against that single instance — per-atom pass/fail with the
runner's own output as the evidence. No journeys or no boot recipe → it **refuses** and names
what's missing; it never passes vacuously.

## 6. Sign off — you, not the machine

```
/foundry:release accept export-v1 --operator <you> --verdict accepted --note "tested it myself"
```

This records a practice note in the release manifest. It is deliberately **not** a gate: the
automation's job ends at making problems visible; the judgment is yours.

---

**The whole loop:** `intake → spec-review → authorize → dispatch → floor → certify-local →
accept`. Six verbs plus your own CI. Everything else in the catalog is optional — see
[docs/VERBS-QUICK-REF.md](VERBS-QUICK-REF.md) for the full list.

## The full install

The minimal path above is the whole discipline. This section states plainly what enabling the
plugin does to every session in every repo where it's installed, what the merge floor actually
enforces, and what certification checks — none of it is new machinery beyond the minimal path;
it's the same six verbs, explained in full.

### The hook layer — not optional today

**The hook layer is not optional today.** Installing and enabling the plugin wires its hooks
into every session in every repo on your machine — there is no supported way to disable it, no
environment variable, no config flag, and no reduced-ceremony install that turns it off. At
minimum, three guards are wired:

- **`hooks/foundry-git-discipline.sh`** — a `PreToolUse` guard on `gh pr merge`: refuses
  `--admin` (a server-side-check bypass) outright, and admits a plain merge only after a live
  `gh pr checks` query returns all-green. Fail-closed on any error, pending row, or unknown
  state.
- **`hooks/foundry-cloud-cli-exec-guard.sh`** — blocks a bare invocation of a guarded cloud/IaC
  CLI (`aws`, `kubectl`, `tofu`, `terraform`, `helm`, `argocd`) at any command position unless
  it's routed through a configured wrapper; it activates once you set one in
  `.claude/foundry-project.json` and stays inert (never blocks) until you do.
- **`hooks/foundry-cwd-enforce.sh`** — inside a dispatched worktree, canonicalizes every
  write-tool target and hard-stops (fail-closed) any write resolving outside that worktree's
  root, closing the gap native worktree isolation leaves open.

An environment-gated off-switch for this layer has been proposed and is **parked pending an
operator decision** — it does not ship today, and this document promises nothing about it.

### The merge floor, in full

The PR merges when **your repo's checks** are green, per your tier:

- **Tier A** (rulesets available): required status checks on `main` — server-enforced.
- **Tier B** (plans without rulesets): the same checks always-reporting, plus the
  git-discipline guard above.

Details + exact hook behavior: [merge-floor.md](merge-floor.md).

### Certification, in full

`/foundry:certify-local` deploys once and runs the real journey suite (the Certify step above);
nothing further to configure for the minimal path. A shared staging deployment is
`/foundry:certify-staging`, part of the optional catalog.

## Pick one install path — do not stack

There are two ways to get this plugin into a session: the marketplace install (`claude plugin
marketplace add` + `claude plugin install`, as in **Install** above) and a directory-sourced
local plugin (`claude --plugin-dir ./agentic-foundry`, for a private or vendored checkout).
**Use exactly one.** Plugin hook commands are additive with whatever else is wired into a repo,
and are de-duplicated by command string — but each install source expands
`${CLAUDE_PLUGIN_ROOT}` to a different root, so the two sources produce two different command
strings and de-duplication does not save you. Running both against the same repo means every
hook fires twice, with two possibly-different plugin versions disagreeing about what's current.
Pick one source per repo and stay on it.

## Something red?

A red doctor, a wedged install, a refused merge, a stale plugin version — every recovery
runbook is in **[troubleshooting.md](troubleshooting.md)**, symptom-first.

## Where things live

| Artifact | Path |
|---|---|
| Specs + contracts | `specs/features/<domain>/<capability>/` |
| Operator registry / project config | `.claude/foundry-operators.json` / `.claude/foundry-project.json` |
| Review + release records | `.foundry/` (gitignored evidence) + your git history (the ledger) |
| Stack profiles | `packs/stack-profiles/` (node-web, aws-eks-karpenter, python-uv-lib, python-uv-service) |
