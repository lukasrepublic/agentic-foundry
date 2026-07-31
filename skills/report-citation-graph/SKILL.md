---
name: report-citation-graph
description: Build/refresh the citation-graph cache + expose it natively via MCP (/foundry:report-citation-graph). Rebuilds .foundry/graph.json (materialized backlinks = delta-1) and serves it through the foundry-graph MCP server. Trigger to refresh the graph or query "what cites X" / "what does X cite".
---

# /foundry:report-citation-graph

The graph layer — a WRAP over native MCP. Plain-text files are the source of
truth; the graph is a derived, reproducible cache.

## Procedure

1. **Build/refresh** the cache:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-build-citation-graph.py" --subdir docs --subdir specs --subdir foundry
   ```
   Writes `.foundry/graph.json` (nodes + forward edges + **materialized backlinks** — the
   O(1) "what cites X" reverse index, delta-1) pinned to a `source_signature` (staleness).
2. **Query natively via MCP.** The `foundry-graph` MCP server (`.mcp.json`, auto-discovered)
   serves three tools the MODEL calls directly: `graph_backlinks(node)` (O(1)),
   `graph_neighbors(node)`, `graph_node(node)`. This is the "make the graph native
   via MCP" realization — the first Foundry MCP server.
3. **Enforcement is agentic, never CI**: broken-reference checks live in
   `/foundry:coherence-check` (source-filtered, advisory), not a perpetually-red CI gate.

## FRESH-FALLBACK CONTRACT (coherence-substrate)

The committed `.foundry/graph.json` is a derived cache with **no auto-rebuild hook** — it can be
stale relative to the corpus. Every consumer that reads the cache (its `broken_edges`, `backlinks`,
`out_of_scope`) MUST first run the **content-based staleness gate**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-build-citation-graph.py" --check-stale
```

- **exit 0 (CURRENT)** → the cache matches the corpus over its recorded `build_scope`; trust it.
- **non-zero (STALE)** → a tracked doc changed, the builder version skewed, or the graph is
  absent/pre-substrate. **Do NOT trust the cache** — **re-walk fresh** (rebuild via step 1) and use
  the freshly-built graph. Never read a stale graph's `broken_edges`/backlinks.

(The doctor staleness line this sentence originally named was retired with the drop-in registry; staleness is read via `foundry_graph.is_stale` by the MCP server, and it never
flips `DOCTOR-RED`). The blocking merge-gate consumer of staleness this line originally named was retired; staleness is now surfaced advisorily (`foundry_graph.is_stale`, read by `foundry-graph-mcp.py`).
The staleness signature is content-based (stable across checkouts), not mtime.

## Inputs / Outputs

- In: the corpus (`docs/`, `specs/`, `foundry/` markdown). Out: `.foundry/graph.json` (cache) + the live `foundry-graph` MCP tools.

## Endpoint trust

The MCP server is stdio (loopback by construction — no network surface); enumerated in
`.mcp.json` (allowlist, no auto-discovery of external servers); the served graph is
pinned to its `source_signature`.

## Anti-patterns

- **Treating graph.json as the source of truth** — it's a derived cache; rebuild from the files.
- **A broken-edge CI gate** — broken-edge sweeping is agentic (`/foundry:coherence-check`, advisory), not CI.
- **An unauthenticated network MCP variant** — stdio/loopback only at v1; an HTTP variant needs token/mTLS + pinned identity.
