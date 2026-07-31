# `karpenter.rego` policy fixtures

Executable evidence that the Karpenter policy pack **is alive** — and adopter-facing documentation of
what it flags, what it hard-denies, and what it deliberately leaves alone.

These ship with the profile on purpose. They are co-located with the policy they exercise, following the
OPA ecosystem's convention: `gatekeeper-library` ships `example_allowed.yaml` / `example_disallowed.yaml`
under `library/<policy-name>/samples/`, and OPA/conftest keep policy tests beside the Rego. No prior art
hides policy fixtures inside the tooling that runs them.

## Why these exist

The pack was **inert**. Every rule opened with `some obj in input`, assuming `input` was an ARRAY of
manifests. The shipped invocation has no `--combine`, so conftest evaluates each document separately and
`input` is a single manifest **object** — `some obj in input` iterated that object's *values*, none of
which has a `.kind`, so every rule body died at its first expression. The pack emitted **nothing on every
manifest**, and its own test suite greened because its one positive control was *mocked in the array shape
conftest never produces*.

A mocked positive control is not a live one. These fixtures are driven through the **real shipped command**
by `scripts/foundry_checks/aws-eks-karpenter-profile.py`, which asserts the pack actually **fires**.

## The three classes

| Directory / file | Drives | Must produce |
|---|---|---|
| `should-flag/rendered-manifests.yaml` | AC-KPP-4 — the live **positive** control | `warnings[]` for `karpenter_uncapped_percentage_budget` **and** `karpenter_disruption_change` (plus `karpenter_expire_after`, `karpenter_capacity_type`, `karpenter_ami_change`) |
| `hard-deny/rendered-manifests.yaml` | AC-KPP-5 — the live **hard-deny** control | `failures[]` for `karpenter_unbounded_disruption` (un-ackable) |
| `should-not-flag/*.yaml` | AC-KPP-6 — the non-flagging guarantee (AC-KDB-2) | **zero** `karpenter_uncapped_percentage_budget` entries, each file independently |

A result reporting only `successes` for `should-flag/` or `hard-deny/` means **the pack has gone dead
again** — that is the regression these fixtures exist to catch. The `should-not-flag/` fixtures cannot
catch it: a dead pack passes every one of them trivially. Never read them alone.

## Running them yourself

```sh
# the positive control — the SHIPPED invocation, verbatim (no --combine)
conftest test -o json --policy packs/stack-profiles/aws-eks-karpenter/policy \
  packs/stack-profiles/aws-eks-karpenter/policy/testdata/should-flag/rendered-manifests.yaml

# the whole thing, as the doctor check drives it
python3 scripts/foundry_checks/aws-eks-karpenter-profile.py
```

`should-not-flag/no-budgets.yaml` also trips the `karpenter_unbounded_disruption` deny — that is correct.
Its assertion is scoped to the uncapped-percentage rule, not to an empty result.

## Adding a rule

Add a `should-flag` case that the rule **fires** on, before (or alongside) any `should-not-flag` case. A
rule proven only by non-flagging fixtures is not proven at all — that is precisely how this pack stayed
dead through a release. The same standard the fleet's `gate-canary-mutation-harness` applies: no gate is
trusted until it is shown to convict a known-bad input.
