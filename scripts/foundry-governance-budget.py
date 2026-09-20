#!/usr/bin/env python3
"""foundry-governance-budget — the plugin's own governance surface, in five counted numbers
(subtraction-wave, AC-SUB-4, autonomy-continuation R4).

A pure, read-only reporter. Prints a table to stdout and exits 0 always (a report, never a gate)
-- writes nothing, gates nothing. `docs/GOVERNANCE-BUDGET.md` carries a before/after run of this
same script (before = the pre-wave tree, e.g. `git show <base-sha>:...` or a checkout of the
branch base; after = the shipped tree once the wave lands) so a deletion wave's own claimed
shrinkage is a number, not an assertion.

Five counts, each derived AT RUN TIME from the tree rooted at `--root` (default: this script's
own plugin root):

  1. skill prose lines   — the sum of every line in every shipped `skills/*/SKILL.md` (frontmatter
                            included; the same files the doctor's `skills-frontmatter` probe walks).
  2. shipped scripts      — the non-recursive count of invocable basenames directly under
                            `scripts/`: every `*.py` file, plus every other regular file carrying
                            the owner-execute bit (the SAME ground-truth shape
                            `tests/test_permission_floor_map.py::ground_truth_basenames` uses for
                            the floor's own closed-world coverage check).
  3. floor rows           — the length of `docs/permission-floor.json`'s top-level `entries` array.
  4. hook commands        — the total count of `{"type": "command", ...}` objects across every
                            event / matcher in `hooks/hooks.json`.
  5. pytest test count    — the number of `def test_*` FUNCTION DEFINITIONS across every
                            `tests/test_*.py` file (a regex count, never a live `--collect-only`
                            run -- this script must stay side-effect-free and importable-tree-
                            agnostic, so it can run cleanly against an OLDER checkout too). This
                            undercounts against `pytest --collect-only`'s own total whenever a
                            `@pytest.mark.parametrize` expands one definition into several
                            collected items -- an accepted, disclosed characteristic of the regex
                            approach (the charter names both `--collect-only` and a regex as
                            acceptable), not a defect.

Usage:
    foundry-governance-budget.py [--root DIR] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]*)\s*\(", re.MULTILINE)


def _default_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(here)


def count_skill_prose_lines(root: str) -> int:
    skills_dir = os.path.join(root, "skills")
    if not os.path.isdir(skills_dir):
        return 0
    total = 0
    for name in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                total += sum(1 for _ in fh)
        except OSError:
            continue
    return total


def count_shipped_scripts(root: str) -> int:
    """MIRRORS tests/test_permission_floor_map.py's `ground_truth_basenames` -- NOT imported
    (this CLI stays a standalone reporter with no cross-module coupling to a test file)."""
    scripts_dir = os.path.join(root, "scripts")
    if not os.path.isdir(scripts_dir):
        return 0
    count = 0
    for name in os.listdir(scripts_dir):
        path = os.path.join(scripts_dir, name)
        if not os.path.isfile(path):
            continue
        if name.endswith(".py"):
            count += 1
        elif os.access(path, os.X_OK) and (os.stat(path).st_mode & 0o100):
            count += 1
    return count


def count_floor_rows(root: str) -> int:
    path = os.path.join(root, "docs", "permission-floor.json")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return 0
    entries = doc.get("entries")
    return len(entries) if isinstance(entries, list) else 0


def _walk_hook_commands(node) -> int:
    """Recursive count of every `{"type": "command", ...}` object anywhere in the hooks.json
    document tree -- deliberately structural (walks any dict/list shape) rather than pinned to
    today's exact `hooks -> <event> -> [ {matcher, hooks: [...]} ]` nesting, so a future schema
    tweak cannot silently zero this count."""
    total = 0
    if isinstance(node, dict):
        if node.get("type") == "command" and "command" in node:
            total += 1
        for v in node.values():
            total += _walk_hook_commands(v)
    elif isinstance(node, list):
        for item in node:
            total += _walk_hook_commands(item)
    return total


def count_hook_commands(root: str) -> int:
    path = os.path.join(root, "hooks", "hooks.json")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return 0
    return _walk_hook_commands(doc.get("hooks", doc))


def count_pytest_tests(root: str) -> int:
    tests_dir = os.path.join(root, "tests")
    if not os.path.isdir(tests_dir):
        return 0
    total = 0
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(tests_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        total += len(TEST_DEF_RE.findall(text))
    return total


LABELS = (
    ("skill_prose_lines", "skill prose lines", count_skill_prose_lines),
    ("shipped_scripts", "shipped scripts", count_shipped_scripts),
    ("floor_rows", "floor rows", count_floor_rows),
    ("hook_commands", "hook commands", count_hook_commands),
    ("pytest_tests", "pytest test count", count_pytest_tests),
)


def budget(root: str) -> dict:
    return {key: fn(root) for key, _label, fn in LABELS}


def render_table(counts: dict) -> str:
    lines = ["governance budget:"]
    width = max(len(label) for _key, label, _fn in LABELS)
    for key, label, _fn in LABELS:
        lines.append(f"  {label.ljust(width)} : {counts[key]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="tree root to measure (default: the plugin root)")
    ap.add_argument("--json", action="store_true", help="print the counts as one JSON object")
    args = ap.parse_args()

    root = os.path.abspath(args.root) if args.root else _default_root()
    counts = budget(root)

    if args.json:
        print(json.dumps(counts, sort_keys=True))
    else:
        print(render_table(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
