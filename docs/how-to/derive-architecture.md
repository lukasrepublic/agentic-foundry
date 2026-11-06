# Derive an architecture draft for an existing codebase

`/foundry:intake existing` selects the governance kits, but the architecture kit (the FG-ARCH rules) needs layers to
check against. A brownfield tree has none declared. `scripts/foundry_architecture_derive.py` proposes them. Everything it
writes is a DRAFT that a person confirms. Nothing is enforced until then.

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_architecture_derive.py" --repo . --dry-run   # print the plan only
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_architecture_derive.py" --repo .             # write the drafts
```

Run it after `select` and before `baseline`. The tracked tree must be clean. Untracked files do not count.

Exit codes: zero is ok, one means a tool was missing or failed (a reported gap), two means refused (dirty tree, not a git repo, bad input).

## What it reads

The script parses no source code. It runs the ecosystem's own tool, or reads that tool's JSON output.

| Input | Tool | Flag for pre-computed output |
|---|---|---|
| Python imports | Grimp (`--python-package PKG`) | `--python-graph FILE` |
| JS and TS imports | `depcruise -T json` (`--js-source DIR`) | `--js-graph FILE` |
| OpenTofu | `tofu show -json <plan>` (`--tofu-plan`) | `--tofu-plan-json FILE` |
| Kubernetes | `helm template` (`--helm-chart`), `kustomize build` (`--kustomize`) | `--manifests PATH...` |

A tool that is not installed, or that fails, is reported as `could-not-look:<tool>`. The script then writes nothing for
that ecosystem. It never reads a missing tool as an empty graph. Java and Nx report `no-derive:java` and `no-derive:nx`.

## What it proposes

1. Each module is collapsed to its folder. Cycles (strongly connected components) are collapsed to one node and
   reported as a cycle finding. The members of a cycle always stay in one layer.
2. The condensed graph is levelled topologically. The result is an ordered layer list, top layer first, with the edge counts
   that justify it.

## What it writes (drafts)

- `.importlinter` (Python) or `.dependency-cruiser.cjs` (JS and TS): the layers contract, with the external packages each
  layer was seen to import. An existing file is kept; the draft goes to `<name>.derived`.
- `docs/architecture/workspace.dsl`: Structurizr DSL. Containers come from Dockerfiles, compose services, Helm charts and
  the code bases found. Components come from the layers. Deployment nodes come from the infra extraction. Every element is
  tagged `draft` and has a `source` property naming its evidence.
- `docs/decisions/NNNN-*.md`: retroactive MADR drafts only for evidenced decisions (a layer contract, a datastore in IaC).
  Each has `retroactive: true`, a `confidence` level, an `evidence` list and the rationale `TODO(human)`.
- `docs/architecture/DERIVE-CONFIRM.md`: what you must confirm.

It never writes a baseline and never applies infrastructure. The `baseline` step creates the tool's own baseline after you
confirm the drafts.

## What you confirm

Layer order and names. The module-to-layer mapping. The allowed external packages per layer. Which cycles you accept.
The container boundaries and external systems (these cannot be extracted). Each ADR's decision and rationale. Merging the
PR is the confirmation.

## The model lint

`governance/kits/architecture/checks/model_source_lint.py [FILE]` fails when a `workspace.dsl` element has no `source`
property. A model or a person may name and describe elements, but the extraction creates them. After you confirm, set the
workspace property `model-status` to `confirmed` and remove the `draft` tags. The lint then fails on any element still
tagged `draft`.
