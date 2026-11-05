# How to use the shared message log

Several sessions on several machines work on one workspace at once and cannot see each other's terminals. The
shared message log is how they tell each other what they do, what they need, what they found, and, above all,
which production changes they are about to make and have made. It has two channels:

| Channel | For | Where |
|---|---|---|
| Machine channel | sessions and scripts | an append-only log on the protected branch `foundry/messages` |
| People channel | people | one issue per production change, request or decision |

Operator-facing reports call it "the shared message log" and name the channel in plain words.

## Why a git branch and not one big issue

One shared issue as a message board hit GitHub's `2500`-comment limit, had no structure, no addressing and no
threads, could be edited or deleted after the fact, was hard to query, and made every reader rescan the whole
stream. A log on a protected branch has no size limit, keeps full history, is tamper-evident, and can be
queried: "every production write this week" is one `audit` call.

Options considered and rejected: repository-dispatch or workflow events (each message starts a CI job and
leaves no readable log), Projects (a status board, not a stream), and commit statuses or check runs (built for
gates on a commit, not conversation). Prior art for the chosen shape: git-bug, git-annex's data branch,
git-appraise, and Flux's write-back retry loop.

## The record

One JSON object per line in `messages/YYYY-MM.jsonl` on the orphan branch `foundry/messages`. The branch
carries `.gitattributes` with `*.jsonl merge=union`, so a human merge of two writers keeps both lines.

| Field | Meaning |
|---|---|
| `v` | format version, `1` |
| `id` | a ULID; the record's identity. Readers dedupe on it |
| `time` | RFC `3339` UTC; informational. Order is commit order |
| `from` / `to` | a session, machine or person id; `to` may be `all` |
| `kind` | one of the closed set below |
| `corr` | joins a production step's `PROD-BEFORE`, `PROD-AFTER` and `STOP` |
| `refs` | PRs, specs, run ids, issue links |
| `body` | the text |

Kinds: `FINDING`, `CHALLENGE` (need an evidence line: an `evidence:` line, a URL, a path, or captured command
output), `NEEDS-INTERFACE`, `HANDOFF` (needs a fenced json block with `cwd`, `command`, `why`, `expect`),
`TICK` (body `<programme> <UTC-stamp>`), `PROD-BEFORE`, `PROD-AFTER`, and `STOP` (a lane stopped; the body is
the reason). The three production kinds need a `corr`.

## The verbs

All verbs are `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_message_log.py" [--repo DIR] [--remote NAME] <verb>`.

**`post --to <id|all> --kind <K> [--corr <id>] [--refs R ...] --body-file <file|-> [--from <me>]`**. Lints the
record (`foundry_message_kind.py`), then appends it and pushes. Your work tree and index are never touched: the
commit is built with git plumbing on top of the fetched tip. If the push loses a race (non-fast-forward), it
refetches, re-appends to the new tip and retries up to `5` times: `2` seconds, doubling, with `50` % jitter, about
two minutes in all. It never forces. Any other refusal, and running out of retries, is a failure: exit `1` and the
reason on stderr, which the tick reports. After a push it reads the tip back and confirms the id is there. A
lint refusal exits `3` and writes nothing.

**`read --to <me> [--since <commit>] [--kind K ...]`**. Fetches the branch alone and returns the records added
after the cursor that are addressed to you or to `all`, deduped by id. Malformed lines are skipped and listed
under `malformed`. The cursor is the last processed commit, kept in your git dir
(`git rev-parse --git-path foundry-message-cursor`), never in the work tree. Without `--since` the stored cursor
is used and then advanced. `--session <name>` keeps a separate cursor, `--no-advance` leaves it alone.

**`audit [--open-minutes N]`**. Replays the log and reports a `PROD-AFTER` or `STOP` with no earlier
`PROD-BEFORE` for its `corr`; a `PROD-BEFORE` with no `PROD-AFTER` or `STOP` after N minutes (default: the
policy's `messaging.open_change_minutes`, `120`); and a production record with no `corr`. Exit `3` when anything is
flagged. Anyone may close a pair with a `STOP` that gives its reason. The `120`-minute default is a tunable
without an industry value behind it.

**`import <issue-url>`**. One-time migration of an existing issue's comments into the log. Paginated (the
listing ceiling is `30000` comments, past which nothing is imported); the kind comes from `TO:`, `BEFORE` and
`AFTER` lines, else `FINDING`; each record's `refs` carries the comment anchor, which makes a second run write
nothing. An `AFTER` is paired with the nearest earlier unpaired `BEFORE`.

**`ruleset-template`**. Prints the branch-ruleset JSON for `foundry/messages`: block force-push, restrict
deletion. Apply it once per repository (see below).

## Protecting the branch

The log is only tamper-evident if the branch is protected. Apply the template as a repository ruleset:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_message_log.py" ruleset-template > ruleset.json
gh api -X POST repos/<owner>/<repo>/rulesets --input ruleset.json
```

Rulesets on a plan that only creates them without enforcing them protect nothing. Check, on a throwaway
branch name or a sandbox repository, that a force-push is actually refused before relying on it. The test
suite reports `unverifiable:tier-b` rather than a pass when it cannot observe a real rejection. Signed commits
on this branch are optional: there is no consensus for bot writers.

## The people channel

One issue per production change, cross-team request or decision. Label it, mention the person, close it when
done. The machine record links to it in `refs`, so a person reads a short relevant thread instead of the whole
stream. The operator inbox already reads request issues; there is no new reader.

## Optional chat mirror

`cli/templates/workflows/message-log-chat-mirror.yml` mirrors only `PROD-BEFORE` and `PROD-AFTER` lines to a
chat webhook every `15` minutes. It is off by default and costs runner minutes. To turn it on: set
`messaging.chat_mirror: on` in the policy file, set the repository variable `MESSAGE_LOG_CHAT_MIRROR` to `on`,
and add the secret `MESSAGE_LOG_CHAT_WEBHOOK`.

## Permissions

`post` and `import` are tiered `ask` in the permission floor (they write to a remote or read an issue under
your identity); `read`, `audit` and `ruleset-template` run without a prompt, as does the kind lint. The
plugin-scripts allow hook does not grant `post` or `import` a silent allow.

## Policy keys

```yaml
messaging:
  machine_channel: git-log
  people_channel: issue-per-topic
  chat_mirror: off
  open_change_minutes: 120
```
