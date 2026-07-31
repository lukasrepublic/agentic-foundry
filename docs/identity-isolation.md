# GitHub identity isolation — what `--gh-account` wires, and how to enforce it

Canonical reference for the gh-token isolation layer `scripts/foundry-bootstrap.sh --gh-account
<name>` scaffolds. This document ships in the foundry plugin's own tree; every path below is
relative to that tree unless the text says otherwise.

`--gh-account <name>` writes two files into the target project — `.claude/gh-identity` (the
declared account marker) and an `.envrc` direnv stanza — and wires git-native commit-identity
isolation. Together they build **three layers**, and this document states, for each, what makes
that layer active — including the one layer this plugin does not enforce on its own.

## The three layers

### 1. Commit-identity isolation (`includeIf`)

Wired with no adopter action needed: a global git `includeIf` binding plus repo-local `useConfigOnly=true`, so a commit under this project resolves the declared account's identity and FAILS rather than silently falling back to an unrelated one.

### 2. `GH_CONFIG_DIR` (the direnv layer)

Active only where direnv is installed, hooked into the adopter's shell, and `direnv allow` has been run for this project; inert and not exported otherwise — a shell without direnv wired, or a project where `direnv allow` has not yet been run, exports nothing, so `gh` falls back to the ambient account (the commit-identity layer's `includeIf` + repo-local `useConfigOnly` still holds for `git`, which fails closed rather than falling back — see the commit-identity layer above).

If `use foundry_gh` looks inert (no `GH_CONFIG_DIR` in your shell after `cd`-ing into the project), check the machine-scope lib direnv dispatches to: `~/.config/direnv/lib/foundry.sh`. A missing or stale copy there is a common reason the dispatch verb resolves to nothing — re-run `foundry-bootstrap.sh toolchain-install` or `project-scaffold` to reinstall it.

### 3. Marker enforcement (`PreToolUse` guard) — opt-in, unshipped by this plugin

Active only where a `PreToolUse(Bash)` guard reading `.claude/gh-identity` is wired in
`.claude/settings.json`. This plugin ships no such guard itself: no `PreToolUse` entry in
`hooks/hooks.json` reads the marker, and no workspace-template `gh-account-guard.sh` is vendored
into `hooks/`.

The `PreToolUse(Bash)` guard is expected to be already wired if your target was cloned from the default workspace template — confirm it in your own clone rather than trust this document:

```
grep -n gh-account-guard .claude/settings.json && ls .claude/hooks/gh-account-guard.sh   # workspace-template guard check
```

On `--existing` or a custom `--template`, none of that is present in the target — you wire it
yourself, below.

## Wiring the guard yourself (`--existing` / custom `--template`)

1. Copy `.claude/hooks/gh-account-guard.sh` from the default workspace template
   (`lukasrepublic/agentic-handbook`) into your target's `.claude/hooks/`. Before wiring it, compare
   your copy against this plugin's byte-pinned in-tree copy of the workspace-template guard,
   `tests/fixtures/gh-account-guard.pinned.sh` (a test fixture, not enforcement — it is not wired
   into this plugin's own `hooks/hooks.json`; its own header names the exact upstream commit it
   was pinned from, `ac923eadbd8a8c476d2f0f0bf125a10b642cf63e`, so the comparison is a provenance
   statement, not just a sanity check).

   The pinned fixture carries an added provenance header the upstream guard itself does not have — a whole-file diff is never empty; it differs by that header, so compare only below the provenance header. Derive the offset from the PINNED fixture itself (the trusted side), anchored on its own header text — never from your own guard copy's length: a prefix-truncated or otherwise shortened copy would slide the two sides into false alignment and read as clean instead of drifted (a `bash` recipe; it uses process substitution, and both paths are quoted since an unquoted path would word-split on an embedded space):

   ```
   guard=".claude/hooks/gh-account-guard.sh"                          # your copy, from the workspace template
   pinned="<this-plugin>/tests/fixtures/gh-account-guard.pinned.sh"   # the workspace-template guard, byte-pinned
   start=$(grep -n 'nothing below is edited' "$pinned" | cut -d: -f1)   # the header's own closing line, in the TRUSTED file
   diff <(tail -n +2 "$guard") <(tail -n +$((start + 2)) "$pinned")     # +2 skips the trailing "#" separator onto the fixture's first body line
   ```

   **This below-the-provenance-header comparison must be EMPTY before you wire the guard** — a non-empty below-the-provenance-header comparison means your copy has drifted from the pinned, reviewed baseline. A genuine trailing-newline difference between the two files still surfaces, correctly, as `diff`'s own "No newline at end of file" remark rather than as silence.

2. Make the copied script executable — a copied guard that is not executable fails open the same
   way a missing one does (see the note on hook-failure semantics below):

   ```
   chmod +x .claude/hooks/<the guard script you copied in step 1>
   ```

3. Add the `PreToolUse(Bash)` entry to `.claude/settings.json`, using the exact env-anchored,
   quoted command form the workspace template itself uses. **This form matters, not just the
   filename:** a *bare relative* command (`.claude/hooks/<script>`) resolves against the hook
   process's working directory, not your project root, so it can miss with exit `127` depending on
   where the enclosing session was started from — and a `PreToolUse` hook that exits anything other
   than `2` is **non-blocking**, so the `gh` call proceeds anyway. An adopter who wired the guard
   this way believes it enforces and it is silently, intermittently failing open. The safe,
   env-anchored form (placeholder script name; see the copy step above for the real one):

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/<guard-script-from-step-1>\""}]
         }
       ]
     }
   }
   ```

   The placeholder above and the literal example below are split on purpose, so this plugin's own
   anti-doc-rot line-scoped locator has one qualified line to check rather than a JSON value edited
   to carry documentation prose (which would corrupt the command a reader copy-pastes). The
   literal, copy-paste-ready form for the workspace template's own guard
   (`lukasrepublic/agentic-handbook`): `"command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/gh-account-guard.sh\""`

4. Add the session-scoped `GH_CONFIG_DIR` entry to the gitignored (never committed)
   `.claude/settings.local.json`, so Claude Code sessions inherit the jail and not just your
   terminal. **Use your actual absolute home directory, not the literal string `$HOME`** — whether
   the settings loader shell-expands `$HOME` inside an env value is unverified, and an unexpanded
   `$HOME` makes `gh` create a literal directory named `$HOME` *inside your repo tree*, with a
   token in its `hosts.yml`, un-gitignored:

   ```json
   {"env": {"GH_CONFIG_DIR": "/home/YOUR-USERNAME/.config/gh-<name>"}}
   ```

   (machine-specific — substitute your own absolute home path; on macOS this is typically
   `/Users/YOUR-USERNAME/...`)

## Scope of the marker-enforcement layer (what it does not do)

The marker-enforcement layer is a **best-effort mistake-catcher for the operator's own Claude Code
sessions — not a containment boundary**. Its match is a word-boundary command-name check, so it
does not catch an absolute invocation such as `/usr/bin/gh`; it compares the running
`GH_CONFIG_DIR` value against a string, it authenticates nothing, and it only ever sees tool calls
Claude Code itself issues — a terminal window opened outside a Claude Code session is not covered
by it at all. Treat it as a guard rail against your own mistake, not as a security boundary against
an adversary who already has a shell.

## The canonical source

The only official source of the workspace template is `lukasrepublic/agentic-handbook` — treat any other namespace as untrusted.

`lukasrepublic/agentic-handbook` is not yet public; until it is, obtain the template through the access route your operator gives you. (Once it is public this section should gain a real link — see the atom's own disclosed residual on this point.)

## What this plugin does not ship

No `PreToolUse` entry in `hooks/hooks.json`, and no workspace-template `gh-account-guard.sh`
vendored into `hooks/`. The gh-token isolation layer above is a documented manual opt-in until a
follow-up, security-reviewed atom ships marker enforcement from the plugin itself — see this
repository's `CHANGELOG.md` and issue tracker for status.
