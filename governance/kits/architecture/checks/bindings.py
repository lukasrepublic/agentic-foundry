#!/usr/bin/env python3
"""Resolve the architecture tool for an ecosystem.

  bindings.py ECOSYSTEM [--table PATH]

Prints the tool name, or `no-check:<ecosystem>` when the table has no binding. Exit 0 when bound, 1 when not.
An unbound ecosystem is a reported state, never a silent pass.
"""
import sys
from pathlib import Path

import yaml

DEFAULT = Path(__file__).resolve().parent / "bindings.yaml"


def resolve(ecosystem, table=DEFAULT):
    data = yaml.safe_load(Path(table).read_text(encoding="utf-8")) or {}
    entry = (data.get("ecosystems") or {}).get(str(ecosystem).strip().lower())
    if isinstance(entry, dict) and entry.get("tool"):
        return entry["tool"]
    return f"no-check:{ecosystem}"


def main(argv):
    args = list(argv)
    table = DEFAULT
    if "--table" in args:
        i = args.index("--table")
        table = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: bindings.py ECOSYSTEM [--table PATH]", file=sys.stderr)
        return 2
    out = resolve(args[0], table)
    print(out)
    return 1 if out.startswith("no-check:") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
