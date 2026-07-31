#!/usr/bin/env python3
"""foundry-graph-mcp — the first Foundry MCP server (§21.7). Serves the citation graph
(graph.json, built by foundry-build-citation-graph) over the native MCP stdio transport,
so the MODEL retrieves "what cites X" / "what does X cite" natively. Backlinks are O(1)
(delta-1 materialized reverse index).

Dependency-free: a minimal newline-delimited JSON-RPC 2.0 stdio server implementing the
MCP methods initialize / tools/list / tools/call. Endpoint trust (§22.7): stdio is
loopback by construction (no network surface); the served graph is pinned to its
source_signature for staleness.

LOAD-ONCE CACHING (UL-0005, AC-MCPGRAPH-4): the graph is read from disk EXACTLY ONCE at
server start (in serve()) and cached in-process for the life of the connection. A graph
REBUILD (foundry-build-citation-graph) does NOT refresh a running server — you MUST restart
the MCP/session to pick up the new graph. A wrong/empty load is therefore sticky until
restart, which is why graph-path resolution is hardened below and the missing-file branch
is LOUD rather than silent.

  FOUNDRY_GRAPH_JSON=<path> foundry-graph-mcp.py          # serve (stdio)
  foundry-graph-mcp.py --selftest                          # in-process protocol smoke test
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_graph as fg  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "foundry-graph-mcp", "version": "0.1.0"}

TOOLS = [
    {"name": "graph_backlinks",
     "description": "O(1) 'what cites this node' — the materialized reverse index (delta-1).",
     "inputSchema": {"type": "object", "required": ["node"],
                     "properties": {"node": {"type": "string", "description": "repo-relative node path"}}}},
    {"name": "graph_neighbors",
     "description": "Forward edges: what this node cites.",
     "inputSchema": {"type": "object", "required": ["node"],
                     "properties": {"node": {"type": "string"}}}},
    {"name": "graph_node",
     "description": "Node info (content hash, in/out degree) + graph source_signature.",
     "inputSchema": {"type": "object", "required": ["node"],
                     "properties": {"node": {"type": "string"}}}},
]


# A documented, greppable marker for the load-once caching contract (AC-MCPGRAPH-4). The
# doctor --graph-selftest imports this to assert the behavior is documented at the
# resolution surface (rebuild ⇒ restart to refresh).
CACHING_NOTE = ("load-once: the graph is read and cached ONCE at server start; "
                "a graph rebuild requires an MCP/session restart to refresh.")


def _is_unexpanded(v) -> bool:
    """A literal '${...}' that the harness never expanded is NOT a usable path."""
    return isinstance(v, str) and "${" in v


def _graph_path() -> str:
    """Resolve the graph path with a FIXED precedence (UL-0005): an explicit, EXPANDED
    FOUNDRY_GRAPH_JSON → CLAUDE_PROJECT_DIR/.foundry/graph.json → a deterministic
    cwd/.foundry/graph.json. An unexpanded '${...}' literal is treated as unset (applies to
    both vars), never as a path.

    No cwd-ANCESTOR walk-up (audit remediation, AC-MCPGRAPH-1): in a multi-repo workspace a
    walk-up from a wrong cwd would climb to the umbrella's (or an attacker-planted ancestor's)
    .foundry/graph.json and serve a CONFIDENTLY-WRONG graph with a valid non-null signature.
    Better to resolve deterministically and FAIL LOUD (AC-MCPGRAPH-3) on a missing file than to
    fail open onto a foreign graph."""
    env = os.environ.get("FOUNDRY_GRAPH_JSON")
    if env and not _is_unexpanded(env):
        return env
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj and not _is_unexpanded(proj):
        return os.path.join(proj, ".foundry", "graph.json")
    return os.path.join(os.getcwd(), ".foundry", "graph.json")


def _emit_missing_diagnostic(path: str) -> None:
    """LOUD stderr banner (UL-0005, AC-MCPGRAPH-3) — the missing-graph branch must never be
    silent. Pairs with the per-response `warning` field surfaced in _call_tool."""
    bar = "!" * 72
    print(
        f"\n{bar}\n"
        "foundry-graph-mcp: GRAPH FILE NOT FOUND — serving an EMPTY graph.\n"
        f"  resolved path     : {path}\n"
        f"  FOUNDRY_GRAPH_JSON: {os.environ.get('FOUNDRY_GRAPH_JSON')!r}\n"
        f"  CLAUDE_PROJECT_DIR: {os.environ.get('CLAUDE_PROJECT_DIR')!r}\n"
        f"  cwd               : {os.getcwd()!r}\n"
        "  → backlinks/neighbors will be [] and source_signature null until fixed.\n"
        "  → pin FOUNDRY_GRAPH_JSON in .mcp.json env, or build the graph "
        "(foundry-build-citation-graph).\n"
        f"{bar}",
        file=sys.stderr, flush=True)


def _missing_warning(graph: dict):
    m = graph.get("_missing")
    if m:
        return (f"EMPTY-GRAPH: no graph file at {m} — source_signature is null and "
                "results are NOT authoritative (see stderr diagnostic).")
    return None


def _load_graph() -> dict:
    p = _graph_path()
    if not os.path.isfile(p):
        _emit_missing_diagnostic(p)
        return {"nodes": {}, "edges": [], "backlinks": {}, "source_signature": None, "_missing": p}
    with open(p) as fh:
        return json.load(fh)


def _call_tool(name: str, args: dict, graph: dict) -> dict:
    node = (args or {}).get("node", "")
    # AC-MCPGRAPH-3: surface the empty-graph state in EVERY response (not just stderr) so a
    # consumer/agent never mistakes an empty fallthrough for a genuinely edgeless node.
    warn = _missing_warning(graph)
    if name == "graph_backlinks":
        out = {"node": node, "backlinks": fg.backlinks_of(graph, node), "source_signature": graph.get("source_signature")}
    elif name == "graph_neighbors":
        out = {"node": node, "neighbors": fg.neighbors_of(graph, node), "source_signature": graph.get("source_signature")}
    elif name == "graph_node":
        out = {"node": node, "info": graph.get("nodes", {}).get(node),
               "in_degree": len(fg.backlinks_of(graph, node)), "out_degree": len(fg.neighbors_of(graph, node)),
               "source_signature": graph.get("source_signature")}
    else:
        raise ValueError(f"unknown tool: {name}")
    if warn:
        out["warning"] = warn
    return out


def handle(req: dict, graph: dict):
    """Return a JSON-RPC response dict, or None for a notification (no response)."""
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params", {})
        try:
            out = _call_tool(params.get("name"), params.get("arguments", {}), graph)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(out)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(e)}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def serve():
    graph = _load_graph()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req, graph)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def _selftest() -> int:
    # In-process protocol smoke test over a synthetic graph.
    graph = {"nodes": {"docs/b.md": {"content_sha256": "x"}}, "edges": [{"src": "docs/a.md", "dst": "docs/b.md"}],
             "backlinks": {"docs/b.md": ["docs/a.md"]}, "source_signature": "sig"}
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, graph)
    lst = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, graph)
    call = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "graph_backlinks", "arguments": {"node": "docs/b.md"}}}, graph)
    notif = handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, graph)
    payload = json.loads(call["result"]["content"][0]["text"])
    checks = [
        ("initialize protocolVersion", init["result"]["protocolVersion"] == PROTOCOL_VERSION),
        ("tools/list has 3 tools", len(lst["result"]["tools"]) == 3),
        ("graph_backlinks O(1) result", payload["backlinks"] == ["docs/a.md"]),
        ("notification → no response", notif is None),
        ("unknown method → error", handle({"jsonrpc": "2.0", "id": 9, "method": "bogus"}, graph).get("error") is not None),
    ]
    ok = all(v for _, v in checks)
    for n, v in checks:
        print(f"  [{'ok ' if v else 'XX '}] {n}")
    print("SELFTEST-GREEN" if ok else "SELFTEST-RED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    serve()
