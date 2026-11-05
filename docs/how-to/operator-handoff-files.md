# Operator hand-off files

Operating-model rule E3. When the operator must do a step that the agent cannot (a credential step, a provisioning step, a
priced decision), the agent writes step-by-step instructions for it as a file. Where that file goes is a policy key.

## The key

`report.handoff_dir` in `.foundry/operating-model.yaml` names the directory for operator hand-off files. The default is
`~/Documents/<workspace-name>`; replace the placeholder with your workspace name. The directory is outside the repository
on purpose. Hand-off files for one step are noise in a pull request.

```yaml
report:
  handoff_dir: ~/Documents/my-workspace
```

The key is validated (no control or invisible characters) and shown by `status`. Nothing writes to the directory
automatically: no program reads the key today. The agent reads the policy and puts the file there. See
[the operating-model policy](../operating-model-policy.md) for the "key only" meaning.

## What goes where

- **Durable instructions stay in the repository as Markdown.** A runbook that others will need again belongs in the repo,
  for example under `docs/runbooks/`. It is reviewed, versioned and searchable. A ticket can allow the path in its
  `## Paper allowed` list.
- **A hand-off file is a one-off.** It names the exact command, the working directory, why it is needed and what a good
  result looks like. The blocker candidate has the same four fields (`cwd`, `command`, `why`, `expect`); see
  `scripts/foundry_blocker_check.py`.
- **Generated PDFs go next to the hand-off files**, in the same directory, never in the repository. A PDF is a copy for
  reading on paper or on a phone. The Markdown is the source.

## No PDF skill in 2.0

Foundry 2.0 ships no PDF export skill. The port plan named one; it was not built. To make a PDF, convert the Markdown with
a tool you already have (for example `pandoc`) and save the result in `report.handoff_dir`. Do not commit it. If a PDF
skill is added later, this page will say so.

## Reports and secrets

A hand-off file must not contain a secret value. Name the secret and where to find it; see
[never read a secret value](secrets-never-read.md).
