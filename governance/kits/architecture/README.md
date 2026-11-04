# architecture kit

Layers and allowed dependency direction, cycle freedom, external allow/deny, model-as-code, ADRs, and
fitness functions in CI. Rules: `FG-ARCH-001` to `FG-ARCH-008` in `rules.yaml`.

- `checks/bindings.yaml`: tool per ecosystem (python import-linter, typescript/javascript dependency-cruiser,
  java ArchUnit, monorepo Nx boundaries). An ecosystem with no entry resolves to `no-check:<ecosystem>`
  (`checks/bindings.py ECOSYSTEM`); that is a reported gap, never a pass.
- `checks/adr_lint.py [DIR]`: fails on a missing `status`, a reused number, a broken `superseded-by`, or an
  accepted ADR without `confirmation.check_id`.
- `templates/`: starter tool configs, the baseline file shape (the file `foundry_governance.py baseline`
  writes under `governance/local/baselines/`), a CI gate, and `docs/decisions/0000-template.md`.

References: [MADR](https://adr.github.io/madr/), [C4](https://c4model.com/),
[Structurizr DSL](https://docs.structurizr.com/dsl), [import-linter](https://import-linter.readthedocs.io/),
[dependency-cruiser](https://github.com/sverweij/dependency-cruiser), [ArchUnit](https://www.archunit.org/),
[Nx boundaries](https://nx.dev/features/enforce-module-boundaries).
