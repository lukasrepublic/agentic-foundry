#!/usr/bin/env python3
"""foundry-build-citation-graph — build the citation graph cache (graph.json) with
materialized backlinks (delta-1). The graph is a derived, reproducible cache; plain-text
files are the source of truth.

  foundry-build-citation-graph.py [--root <dir>] [--subdir docs --subdir specs] [--out <path>]
  foundry-build-citation-graph.py --selftest
  foundry-build-citation-graph.py --check-stale   # content-based staleness gate
"""
import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_graph as fg  # noqa: E402


def _selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "docs"))
        open(os.path.join(d, "docs", "a.md"), "w").write("# A\nSee [Doc: docs/b.md] and [link](docs/b.md).\n")
        open(os.path.join(d, "docs", "b.md"), "w").write("# B\nNo citations.\n")
        open(os.path.join(d, "docs", "c.md"), "w").write("# C\nCites [Doc: docs/a.md].\n")
        g = fg.build_graph(d)
        checks = [
            ("nodes==3", g["node_count"] == 3),
            ("edge a->b (dedup link+cite)", any(e == {"src": "docs/a.md", "dst": "docs/b.md"} for e in g["edges"])),
            ("a->b counted once", sum(1 for e in g["edges"] if e["src"] == "docs/a.md" and e["dst"] == "docs/b.md") == 1),
            ("backlinks[b] O(1) == [a]", fg.backlinks_of(g, "docs/b.md") == ["docs/a.md"]),
            ("backlinks[a] == [c]", fg.backlinks_of(g, "docs/a.md") == ["docs/c.md"]),
            ("neighbors[a] == [b]", fg.neighbors_of(g, "docs/a.md") == ["docs/b.md"]),
            ("source_signature stable", fg.build_graph(d)["source_signature"] == g["source_signature"]),
        ]
        ok = all(v for _, v in checks)
        for name, v in checks:
            print(f"  [{'ok ' if v else 'XX '}] {name}")
        print("SELFTEST-GREEN" if ok else "SELFTEST-RED")
        return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--subdir", action="append", dest="subdirs")
    ap.add_argument("--out", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check-stale", dest="check_stale", action="store_true",
                    help="exit 0 if .foundry/graph.json matches the corpus; non-zero (STALE) otherwise")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.check_stale:
        graph_path = args.out or os.path.join(args.root, ".foundry", "graph.json")
        stale, reason = fg.is_stale(args.root, graph_path)
        print(f"{'STALE' if stale else 'CURRENT'} ({reason}) — {graph_path}")
        return 2 if stale else 0
    out = args.out or os.path.join(args.root, ".foundry", "graph.json")
    g = fg.build_graph(args.root, args.subdirs)
    fg.write_graph(g, out)
    print(f"wrote {out}: {g['node_count']} nodes, {g['edge_count']} edges, "
          f"{len(g['backlinks'])} backlinked nodes, sig {g['source_signature'][:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
