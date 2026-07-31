# Architecture — Workspace + Factory

An agentic project has **two components**, and the distinction is load-bearing:

> **The workspace holds the _WHAT_ to build. The plugin (factory) determines _HOW_ it gets built. The plugin is wired _into_ the workspace.**

- **Workspace** (a *handbook* repo) — the project's organizing hub. It holds the
  **agentic SDLC artifacts**: the instruction set of *what* to build, plus the
  governance, corpus, and orchestration around it. Project-specific.
- **Factory** (the **Foundry plugin**) — the *verbs*: the process + the trust model +
  the floor discipline that turn the instruction set into merged code. Generic,
  versioned, shared. **Wired into** the workspace.

Neither replaces the other. The factory is wired into the workspace and operates on the
workspace's artifacts.

## 1. The workspace holds the WHAT — the SDLC artifact ladder

Intent is progressively refined, in the workspace, from human language into a
machine-executable instruction set. Each rung is more deterministic than the last:

```
human-readable specs            ← intent: PRDs, user stories, requirements (human ↔ human)
        │  (intake · spec-author · spec-review)
        ▼
LLM-friendly functional specs   ← deterministic atomic specs with stable AC-IDs +
   + designs                       designs/design-assets — the precise instruction set
        │  (authorize: freeze + sign)         "what to build", written FOR the agent
        ▼
frozen acceptance contracts     ← content-hashed, operator-signed: observable checkpoints
                                   {surface, locator, expect} — the binding definition of done
```

The workspace also holds everything project-specific and *accumulated* around that
ladder: governance posture (stage mode, the both-modes floor), the spec/release
taxonomy + lifecycle, the **citation-graph corpus**, cross-repo coordination
(spec-repo ↔ code-repo), the operator's command center (dispatcher session, audit log,
memory), and status/learnings history.

**The human spec is the source of intent; the generated functional spec + frozen
contract are the instruction set the factory builds against.** That generation +
freezing happens *in the workspace*, using the factory's verbs.

## 2. The plugin determines the HOW — the factory

The plugin contributes none of the *what*. It contributes the **process and the trust
model**: how intent becomes a functional spec, how it is authorized, how it is built and
proven, and how it reaches `main`.

```
intake → spec-review → AUTHORIZE → wave-plan → implement → MERGE FLOOR → certify → sign-off
  (how to refine)  (how to gate the front)   (how to build)   (how to prove + admit)
```

Every "how" names what enforces it: front-authorization (no skip — the operator freezes
the contract), the honestly-tiered merge floor (your branch protection + CI, see
[merge-floor.md](merge-floor.md)), certification against the real running release, and the
operator's terminal sign-off. The factory is the same for every project; only the *what*
(the workspace's artifacts) differs.

## 3. The wiring contract — how the factory plugs into the workspace

The plugin is **wired into** the workspace via native Claude Code mechanisms:

| Seam | Wiring |
|---|---|
| **Plugin load** | the workspace loads the plugin (`--plugin-dir` / marketplace / `enabledPlugins`) → `/foundry:*` skills, agents, and the MCP become available in the workspace's sessions. |
| **Hooks** | the plugin's `hooks/hooks.json` composes into `PreToolUse`/`SessionStart`/… (additive; deduped) — the gate fires because the plugin's hooks are wired into the workspace's tool events. |
| **MCP** | the plugin's `.mcp.json` merges into the workspace's MCP config (the `foundry-graph` retrieval source). |
| **Data flow** | **`${CLAUDE_PLUGIN_ROOT}`** = the factory's own files (scripts, schemas, templates); **`CLAUDE_PROJECT_DIR`** = the workspace (specs, the operator registry, the graph corpus). The factory's scripts read the workspace's artifacts. |

That last row is the crux: **`${CLAUDE_PLUGIN_ROOT}` (the wired-in factory) ↔
`CLAUDE_PROJECT_DIR` (the host workspace).** The factory brings the verbs; it reads the
workspace's nouns. `/foundry:doctor` health-checks the wiring (manifest, hooks, skills,
profile lock, operator registry) in under a second.

## 4. Two reusable deliverables

Because the two components are cleanly separated, both are reusable:

- **The factory** → the `agentic-foundry` **plugin** (install via marketplace). Shared across N projects; bump the version to upgrade all of them.
- **The workspace** → the separate [`agentic-handbook`](https://github.com/lukasrepublic/agentic-handbook) **GitHub _template_ repo** (the organizing skeleton — the spec/release taxonomy, the stage-mode `CLAUDE.md`/`WORKFLOW.md` scaffolding, the dispatcher conventions, the directory layout, example specs/contracts), de-businessed and **pre-wired to install the plugin**.

**These are two repos by design, because a plugin and a workspace are different _kinds_
of artifact** — a plugin is a **dependency** you *install/wire* (versioned, shared, stays
generic, never diverges per project); a workspace is a **repo you own** that you
*instantiate from a template* and then *evolve* (your specs, governance, and corpus grow
in it). You can't "install" a workspace, and the factory should not spray a repo skeleton
into your dir — keeping them separate lets each version on its own cadence. They meet at
the **wiring contract** (the template exposes the corpus/config at the paths the plugin's
hooks/scripts expect).

A **new agentic project** has two entry points:

```
GREENFIELD:  "Use this template" (agentic-handbook)  →  author specs  →  build via the factory
              (the WHAT structure, pre-wired to the plugin)   (fill the WHAT)    (the HOW)

EXISTING REPO:  /foundry:init wires the plugin in  →  author specs  →  build via the factory
                 (load + hooks + MCP + corpus paths)
```

So `/foundry:init` handles the **wiring** (into a template-or-existing repo); the
**template repo** provides the **workspace structure**. Different jobs, no overlap.

The **reference workspace** the template is distilled from becomes adopter #0 — a full
handbook that *wires in* the factory plugin instead of in-lining the machinery.

## One-liner

**Workspace = the WHAT (the SDLC instruction set + its organization). Plugin = the HOW
(the factory + trust model). The wiring (`${CLAUDE_PLUGIN_ROOT}` ↔ `CLAUDE_PROJECT_DIR`,
hook composition, MCP merge) plugs the factory into the workspace.**
