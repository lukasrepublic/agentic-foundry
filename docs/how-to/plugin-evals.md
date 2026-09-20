# How to run the plugin's native evals

The plugin's behavior is certified two ways today: the pytest suite (`python3 -m pytest tests/
-q`, the scripts) and the operator's own walkthrough (the skills). Native `claude plugin eval`
(the primary doc, "Test plugins with evals") adds a third path — realistic prompts run against
this plugin in an empty workspace, scored by graders, with a no-plugin baseline so the report
shows what the plugin actually contributes.

**This is never run in CI by this atom.** Every run, and every `llm`/`baseline` grader, is a real
model call billed against your plan or API key. `.github/workflows/` gains nothing here — this
page is the recipe for the operator to enable it, on their own machine or in their own CI, when
they choose to.

**What actually loads.** Each run starts a fresh, isolated session with this plugin's own
**skills AND hooks** loaded and running exactly as they would in a real session — only *your*
personal and project-level settings, hooks, and `CLAUDE.md` are excluded (the primary doc, "How
runs are isolated"). This plugin's own `SessionStart`/`PreToolUse` hooks fire in every run; a
case is never testing skills in a vacuum with the hooks silently absent.

## Requirements

- Claude Code v2.1.269 or later (`claude --version`, `claude update` if older).
- The plugin's own credentials/model provider — the same ones your normal `claude` sessions use.

## Run it locally

From this repo's root (the plugin root, where `.claude-plugin/plugin.json` lives):

```bash
claude plugin eval . --case keep-going --runs 1 --ablation none --trust-plugin
```

This runs one case, once, with no no-plugin baseline — the cheapest way to check a single case
still behaves before trusting the full suite. Drop `--case`/`--runs`/`--ablation` to run every
case in `evals/` at the default three runs per arm (six runs per case: three with the plugin,
three without).

Two of the six shipped cases need flags this quick command doesn't pass:

- **`merge-waits-with-primitive`** needs `--allow-tools Bash` (the read-only default tool set
  excludes `Bash`) and `--scaffold` (its `case.yaml` names a `scaffold_script` that stubs `gh` on
  `PATH`; without `--scaffold` the script never runs and the case has no `gh` to call).
- **`state-read-first`** needs `--scaffold` for the same reason — its `scaffold_script` writes
  the release's `state.yaml` into the empty workspace before Claude starts.
- **`message-kind-lint`** needs `--allow-tools Bash` (it invokes
  `scripts/foundry_message_kind.py`).

The full local run, covering every case:

```bash
claude plugin eval . --trust-plugin --scaffold --allow-tools Bash
```

## Read the result

A summary table prints as each run finishes (`WITH`, `W/OUT`, `Δ` columns in two-arm mode), then
a report path:

```text
CASE        WITH  W/OUT Δ      RUNS COST    NOTES
keep-going  1.00  0.50  +0.50  6    $0.41

1 case(s) · mean Δ +0.50 · 41s · $0.41
Report: /path/to/agentic-foundry/evals/results/<timestamp>/report.html
```

Open the report for each grader's verdict per run. A case whose `Δ` sits at or below zero across
two consecutive runs is a subtraction candidate — see `skills/certify-local/SKILL.md`'s "Native
plugin eval" section.

## Run it in CI (operator opt-in; not shipped)

The primary doc's own CI recipe, quoted verbatim (pin both models so a model rollout is never
mistaken for a plugin regression):

```bash
claude plugin eval . \
  --trust-plugin \
  --json results.json \
  --threshold 0.8 \
  --model claude-sonnet-5 \
  --judge-model claude-haiku-4-5 \
  --no-publish \
  --max-cost-usd 20
```

Exit codes: `0` every case at/above threshold, `1` a case below threshold or a load failure,
`2` partial (cost ceiling hit or credential rejected), `130` interrupted, `143` terminated (e.g.
a CI timeout). A CI runner needs a Claude Code install and credentials in the environment (such
as `ANTHROPIC_API_KEY`) — `--trust-plugin` is required so the job never waits at the first-run
trust prompt.

Wiring this into `.github/workflows/` — a required check, a scheduled job, whichever cadence
fits the bill you're willing to pay — is the operator's call, not something this plugin ships
turned on.

## The six shipped cases

Each case under `evals/` targets one autonomy-continuation mining fixture (R1–R3):

| Case | What it checks | Release |
| :--- | :-------------- | :------ |
| `keep-going` | finishes a multi-step task without stopping to ask a directive question at a natural pause | v1.12.0 |
| `blocker-with-evidence` | reports an impossible step as a shaped blocker (`claim`/`evidence`/`attempted`/`why_operator`/`handoff`), not a prose excuse | v1.12.0 |
| `merge-waits-with-primitive` | reaches for `foundry-merge-when-green.py` instead of a hand-rolled `sleep`-then-poll loop | v1.13.0 |
| `unauthorized-claim-refused` | refuses to merge an atom whose contract carries no `authorized` trailer, and names `/foundry:authorize` | v1.14.0 |
| `message-kind-lint` | validates an outgoing cross-session message with `scripts/foundry_message_kind.py` before claiming it's ready to send | v1.14.0 |
| `state-read-first` | reads a release's `state.yaml` and names its `next_action` before doing anything else | v1.14.0 |

`tests/test_plugin_eval_suite.py` validates every case's shape (frontmatter keys, grader types)
without running any of them — that test is free and always green; this page is for the billed
run.
