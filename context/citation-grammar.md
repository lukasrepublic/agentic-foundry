# Citation grammar

Generic, keyword-prefixed references — resolved + graphed by the factory
(`/foundry:report-citation-graph` + the `foundry-graph` MCP). Plain-text files are the
source of truth; the graph is a derived cache.

| Form | Means |
|---|---|
| `[Doc: <repo-relative path>]` | a doc/spec in this workspace |
| `[Atom: <spec path>]` | another atomic spec |
| `[Design-asset: <path>]` | a design asset (load before UI work) |
| `[External: <url>]` | an external reference |

Plain markdown links (`[text](path.md)` shapes) are also edges. **Broken-reference sweeping is
agentic** (`/foundry:coherence-check`, source-filtered; advisory) — never a perpetually-red CI gate.
