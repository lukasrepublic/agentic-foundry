# How to run builders across machines (`foundry_fleet.py`)

A **programme** can have builders on more than one machine. Each builder is one worker session. A worker claims
one **batch** of the execution plan, builds it, opens one PR and records `review_ready`. This page covers the
claim, the lease, the worker id, the deck slots and the fleet capacity.

## The claim

A claim is the creation of the ref `refs/heads/claim/<programme>/<batch>`. GitHub makes the create atomic:
the create status `201` means you won the batch, and `422` means somebody else holds it (report `lost`).

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_fleet.py" claim <owner>/<repo> <programme> <batch>
```

The ref points at a commit. The commit message holds the claim record: worker id, state, heartbeat time.
The programme and batch names are checked before any ref name is built. Only refs under `claim/<programme>/<batch>`
are ever written.

## Heartbeat, transitions and takeover

- `heartbeat` and `transition` move the ref with the GraphQL mutation `updateRefs` and a `beforeOid`. If the tip
  moved since the read, the call reads the ref again and reports `conflict`. It never overwrites the new tip.
- States are `claimed`, `building`, `review_ready` and `stopped`. `transition ... review_ready --pr <url>` records the PR.
- The lease lasts the policy value `claims.expires_after` (default two hours) after the last heartbeat. While the lease
  is live, another worker gets `lost`. After it expires, another worker can take the batch over (the record keeps
  `taken_over_from`). The old holder then gets `not_holder`.

## The worker id

`<op_id>@<machine12>/<container12>/<session>` is built from the operators file, `/etc/machine-id`, the container id
in the cgroup and `CLAUDE_CODE_SESSION_ID`.

- If a part is missing or unreadable, the script refuses with a named reason (`session-missing`, `machine-id-unreadable`,
  `container-id-missing`, `operator-unregistered` and so on). There is no per-clone session fallback.
- A host without a container shows no container id in the cgroup. Set `FOUNDRY_CONTAINER_ID` (a hex string)
  on purpose for such a host. The script never invents one.
- `trailers` prints the commit trailers `Co-authored-by`, `Session-Id` and `Worker-Host`. `marker` prints the hidden PR-body
  marker `<!-- foundry-worker: <id> -->`. Pass each trailer to `git commit` as its own `--trailer` argument.

## The token

Each worker session uses one GitHub App installation token. The script reads it from the file named by
`FOUNDRY_FLEET_TOKEN_FILE` (owner-only permissions). The token goes only into the environment of the `gh` child process and is
removed from every message. This script does not create, register or configure the App. If no token is provisioned,
the script stops with `external-provisioning`. The operator holds the App identity.

## Deck slots and capacity

- `arm-deck` takes one of two slots of this machine. A deck claim title starts with `deck:`. When both slots hold a
  live claim, it reports `deck-slot-full` and the deck does not arm.
- `capacity --workers 2,2 --reviewers 3` prints `min(sum of max_concurrent_per_worker, reviewer capacity)`. The open
  group of the plan never runs more batches than this value or the plan's `max_concurrent`.

## Builder mode

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_fleet.py" build-next <owner>/<repo> <programme> --plan-ref --workers 2,2 --reviewers 3
```

It reads the plan, skips held atoms, claims one batch of the `open_wave` inside the capacity and prints it. It
never re-plans and never edits the plan. Results: `claimed`, `held` (you already hold one), `at-capacity` and `none`.
After the build and the PR, run `transition ... review_ready --pr <url>`.

The operator's own test pass stays the last step of delivery. This script gives visibility of who builds what. It is
not a control that replaces that pass.
