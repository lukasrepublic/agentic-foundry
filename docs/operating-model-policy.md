# The operating-model policy

The operating rules of the factory (how the deck merges, fans out, reports, keeps cost and protects secrets) have one
policy file: `.foundry/operating-model.yaml`. It is the policy surface of the `factory` category in
[Foundry Governance](governance.md). The file is optional. A workspace with no file gets every default.

This page lists every key, whether it is a floor or a tunable, and the primitive that acts on it. The source of truth is
`schema/operating-model.schema.json`; the one reader is `scripts/foundry_operating_model.py`. Check your file and read the
reason for a key with:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_model.py" validate
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_model.py" status
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_model.py" explain deck.max_builders
```

The same verbs run as `/foundry:operating-model`. The seed file is `cli/templates/operating-model.yaml.tmpl`.

## Floor and tunable

- A **floor** key is a `const` in the schema. You can write it only as its enforced value. Any other value is refused with
  `floor-rule-disabled:<key>`. An unquoted YAML `off`, `no` or `false` on a floor key counts as another value.
- A **tunable** key carries a default and an allowed range. You may change it.
- An unknown key, a wrong type or a malformed file is refused with the key named. A broken file is never replaced by
  defaults.
- `secrets.forbidden_verbs` and `secrets.no_print_roots` are **add-only**: what you list is added to the built-in
  defaults, and a default can never be removed. A removal marker is refused with
  `floor-rule-disabled:secrets.forbidden_verbs.removal` or `floor-rule-disabled:secrets.no_print_roots.removal`.

## How a key acts

The "Acts through" column says how far the key reaches. Read it before you rely on a key.

| Value | Meaning |
|---|---|
| script | A script or hook reads the key when it runs. Changing the key changes the behavior. |
| prompt | The key is rendered into the command-deck tick prompt by `render-tick-section` and nowhere else. It is instruction text an agent follows. It is not a gate. |
| key only | The policy reader validates and shows the key. Nothing reads it, so changing it changes no behavior. |
| const floor | The schema fixes the value. The primitive in the last column enforces the rule whatever the file says; the key exists so that the file cannot switch the rule off. |

## Every key

| Key | Kind | Default or enforced value | Rule | Acts through | Primitive |
|---|---|---|---|---|---|
| `pr_strategy.granularity` | tunable | `coarse` | A1 | prompt | `skills/command-deck/tick-prompt.template.md` (section A1); clusters from `scripts/foundry_merge_plan.py` |
| `pr_strategy.one_pr_per_hot_file_per_round` | tunable | `true` | A1 | prompt | `skills/command-deck/tick-prompt.template.md` (section A1) |
| `merge.queue` | tunable | `auto` | A2 | script | `scripts/foundry_queue_land.py` (the git-discipline hook does not read this key) |
| `merge.requeue_budget_outside_diff` | floor | `1` | A2 | const floor | `scripts/foundry_queue_land.py` |
| `merge.full_suite_on_branch` | tunable | `shared-catalogue-change, after-queue-rejection` | A3 | script | `scripts/foundry_queue_land.py` |
| `merge.record_line_before_enqueue` | floor | `true` | A2 | const floor | `scripts/foundry_queue_land.py` posts the record line |
| `merge.human_review_required` | tunable | `false` | A4 | script | `scripts/foundry_queue_land.py` |
| `deck.max_builders` | tunable | `4` | B1 | script | `scripts/foundry_deck_resources.py`; section B1 and B8 of the tick prompt |
| `deck.max_agent_calls` | tunable | `150` | S5 | script | section S5 of the tick prompt (`render-tick-section S5`); every dispatch brief |
| `deck.max_wait_s` | tunable | `60` | S5 | script | section S5 of the tick prompt |
| `deck.note_max_tokens` | tunable | `2000` | S5 | script | section S5 of the tick prompt |
| `deck.stall_after_ticks` | tunable | `3` | S5 | script | `scripts/deck-cost-census.py --stall-check`; the `cost-without-progress` stall class in `scripts/foundry_blocker_check.py` |
| `deck.heavy_slots` | tunable | `auto` | B2 | script | `scripts/foundry_deck_resources.py`, `scripts/foundry-deck-heavy` |
| `deck.resource_check` | floor | `true` | B2 | const floor | `scripts/foundry_deck_resources.py`, run first in every tick |
| `deck.workflow_size_source` | floor | `"smallest"` | B8 | const floor | section B8 of the tick prompt (`resolve_workflow_size` in `scripts/foundry_operating_model.py`) |
| `decisions.research_first` | floor | `true` | B3 | const floor | `skills/research-first/SKILL.md`, `scripts/foundry_research_note.py` |
| `claims.enabled` | tunable | `true` | B5 | script | `scripts/foundry_ticket_store.py` (used by `scripts/foundry-ticket.py` and clause (m) of `hooks/foundry-git-discipline.sh`) |
| `claims.expires_after` | tunable | `2h` | B5 | script | `scripts/foundry_ticket_store.py` |
| `cost.weigh_in_every_decision` | floor | `true` | C1 | const floor | section C1 of the tick prompt |
| `cost.priced_ask_for_always_on` | floor | `true` | C1 | const floor | `scripts/foundry_blocker_check.py` (the `priced-ask` kind) |
| `report.format` | tunable | `four-block` | E1 | prompt | section E1 of the tick prompt; `scripts/foundry_deck_report.py` renders the blocks |
| `report.timezone` | tunable | `auto` | E1 | script | `scripts/foundry_deck_report.py` |
| `report.plain_language` | tunable | `true` | E1 | prompt | section E1 of the tick prompt |
| `report.max_lines` | tunable | `8` | E1 | script | `scripts/foundry_deck_report.py` |
| `report.include_ids` | tunable | `false` | E1 | script | `scripts/foundry_deck_report.py` |
| `communication.style` | tunable | `simplified-technical-english` | E2 | script | `scripts/foundry_brief.py` (the brief); `render-tick-section E2` renders the same text into the tick prompt |
| `communication.operator_style_file` | tunable | `` | E2 | prompt | `render-tick-section E2` |
| `models.builder` | tunable | `smaller` | B7 | prompt | section B7 of the tick prompt |
| `models.reviewer` | tunable | `smaller` | B7 | prompt | section B7 of the tick prompt |
| `models.security_reviewer` | tunable | `strongest` | B7 | prompt | section B7 of the tick prompt |
| `hygiene.teardown_required` | floor | `true` | F1 | const floor | `hooks/foundry-env-reap.sh`, `scripts/foundry-env-hygiene.py` |
| `secrets.value_reads_forbidden` | floor | `true` | D1 | const floor | `hooks/foundry-secret-read-guard.py` |
| `secrets.forbidden_verbs` | tunable | `(empty)` | D1 | script | `hooks/foundry-secret-read-guard.py` (add-only) |
| `secrets.no_print_roots` | tunable | `(empty)` | D1 | script | `hooks/foundry-secret-read-guard.py` (add-only) |
| `messaging.chat_mirror` | tunable | `off` | - | key only | none: `cli/templates/workflows/message-log-chat-mirror.yml` runs only when the repository variable `MESSAGE_LOG_CHAT_MIRROR` is `on`; it does not read this key |
| `messaging.open_change_minutes` | tunable | `120` | D2 | script | `scripts/foundry_message_log.py` (`audit`) |
| `messaging.cleanup_timeout_s` | tunable | `120` | D2 | script | `scripts/foundry_prod_step.py` |
| `governance.design_required_paths` | tunable | `(empty)` | - | script | `scripts/foundry-ticket.py` (`design-required`) |
| `governance.size_tag_required` | tunable | `L` | - | script | `scripts/foundry-ticket.py` (`design-required`) |

The rule letters are the operating-model rules the keys serve. The traceability table below maps every rule to the
acceptance criterion that checks it.

## Notes on the primitives

- **The production step (`scripts/foundry_prod_step.py`) runs non-interactive commands only.** It starts the command in its
  own session, so the command has no controlling terminal. A command that needs a prompt, such as a `sudo` password or an
  `ssh` multi-factor challenge, fails there. Authenticate first, or use a credential that needs no prompt.
- **Never persist an allow for the `foundry_prod_step.py` row.** The script runs whatever command it is given. An "always
  allow" for it would let any command run unprompted under the plugin's name. The permission floor tiers it `ask` and the
  plugin-scripts allow hook never grants it a silent allow. Approve each use.
- **Subagents and rule D2.** The tick prompt tells the deck to put the production-step rule in the brief of every subagent
  that can touch production. The brief renderer (`scripts/foundry_brief.py`) also ends every rendered brief with one D2
  line: state the step before, report it after, wrap it in `foundry_prod_step.py`, never persist an allow. A brief you
  write by hand must carry the same rule.
- **Time zones.** `report.timezone` takes `auto`, `UTC` or an IANA name. `auto` and `UTC` work on any host. A host with
  no time zone database (the Python standard library reads the system `tzdata`) refuses every IANA name, with a
  `bad-timezone` line from `scripts/foundry_deck_report.py`. Install the `tzdata` package, or set `auto` or `UTC`.
- **The shared message log.** `messaging.*` keys configure the log described in
  [the shared message log](how-to/shared-message-log.md), including how far its tamper evidence goes and which branch the
  queue-land script can create.
- **`communication.style: operator-defined`** reads a regular `.md` or `.txt` file under `.foundry/`. Its text is instruction
  text by design, because you wrote it.

## Traceability

Every rule of the operating model maps to at least one acceptance criterion in the programme's charters. The table is
generated by `scripts/foundry_om_trace.py` from the charter copy in `tests/fixtures/om-trace/charters/`. A test
regenerates it and fails when it differs from this page. Regenerate it with:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_om_trace.py" --charters "${CLAUDE_PLUGIN_ROOT}/tests/fixtures/om-trace/charters" --markdown
```

and paste the output between the two marker comments.

<!-- om-trace:begin -->
| Rule | Title | Charter | Acceptance criterion |
|---|---|---|---|
| A1 | Coarse-grained PRs and a merge plan every tick | `gv-13-deck-report` | `AC-OM-3` |
| A2 | Merge-queue discipline | `gv-10-merge-primitives` | `AC-OM-4` |
| A3 | Full suite only when required | `gv-10-merge-primitives` | `AC#3` |
| A4 | Agent review is the floor for small teams | `gv-16-ticket-review` | `AC#1` |
| A5 | Code first; largest cluster first; no silent stalls | `gv-13-deck-report` | `AC-OM-3` |
| A6 | A fix is done only when it is re-measured | `gv-13-deck-report` | `AC-OM-3` |
| B1 | Time to market through parallel fan-out | `gv-13-deck-report` | `AC-OM-3` |
| B2 | Bound parallelism by the machine | `gv-12-machine` | `AC-OM-6` |
| B3 | Research answers questions | `gv-14-blockers` | `AC#3` |
| B4 | "Needs the operator" is a claim that needs an attempt | `gv-14-blockers` | `AC-OM-8` |
| B5 | Work claims across machines | `gv-11-guards` | `AC#3` |
| B6 | Shell commands that raise no permission prompts | `gv-11-guards` | `AC-OM-5` |
| B7 | Token discipline | `gv-13-deck-report` | `Amendment 2026-11-05 New AC` |
| B8 | The smallest workflow size wins | `gv-13-deck-report` | `AC-OM-3` |
| B9 | An armed pause means start nothing new | `gv-13-deck-report` | `AC-OM-3` |
| B10 | Do not correct a running agent mid-task | `gv-13-deck-report` | `AC#4` |
| C1 | Cost is a decision input | `gv-13-deck-report` | `AC-OM-3` |
| C2 | Minimal CI; budgets are tools, not gates | `gv-16-ticket-review` | `AC#5` |
| C3 | Scale-to-zero for occasional privileged jobs | `gv-07-kits-cost-observability-data-ux` | `AC#5` |
| D1 | Never read a secret value | `gv-11-guards` | `AC#2` |
| D2 | Production is stated before and reported after | `gv-13-deck-report` | `AC-OM-3` |
| D3 | Every capability is reachable from every surface | `gv-16-ticket-review` | `AC#1` |
| E1 | A short four-block report | `gv-13-deck-report` | `AC-OM-3` |
| E2 | Keep communication simple and standardized | `gv-01-governance-core` | `AC-OM-11` |
| E2 | Keep communication simple and standardized | `gv-03-policy-file` | `AC-OM-11` |
| E2 | Keep communication simple and standardized | `gv-13-deck-report` | `AC-OM-3` |
| E3 | Operator hand-off files | `gv-14-blockers` | `AC#2` |
| F1 | Pair every start with a teardown | `gv-12-machine` | `AC#3` |
| F2 | Sweep the right disk each tick | `gv-12-machine` | `AC#3` |
| F3 | Push every head | `gv-12-machine` | `AC#3` |
| F4 | One worktree per builder; no shared stash | `gv-12-machine` | `AC#3` |
| G1 | No baseline before the first fully green run | `gv-15-intake-upgrade` | `AC#3` |
| G2 | A delivery record names who built it | `gv-16-ticket-review` | `AC#4` |
| G3 | Commit before you authorize | `gv-16-ticket-review` | `AC#3` |
<!-- om-trace:end -->

An `UNMAPPED` row would mean a rule with no acceptance criterion. The script exits non-zero in that case, so the gap is
visible to whoever runs it.
