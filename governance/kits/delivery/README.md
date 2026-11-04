# delivery kit

Branches, commits, merge and release. Rules: `rules.yaml` (FG-DLV-001..010). Floors: branch discipline
(FG-DLV-001), merge-queue discipline (FG-DLV-004) and never editing CI to pass (FG-DLV-010).

Templates and snippets: a commitlint config, a release-please config and a CODEOWNERS template. The merge
queue, merge plan and paper guard are shipped by the factory scripts and hooks named in each rule's `check.ref`.
