#!/usr/bin/env python3
"""foundry-done-when-backfill — derive a top-level `done_when` for every acceptance-contract.yaml
that lacks one (subtraction-wave, AC-SUB-3, autonomy-continuation R4).

For every `specs/features/foundry/**/acceptance-contract.yaml` under a WORKSPACE root (the
consumer's own spec corpus — this is a standalone CLI, never scoped to this plugin repo's own
`specs/`) that has no top-level `done_when` key, derives one `test:<file>` locator per DISTINCT
test file named by that contract's `checkpoints[].surface: "test:..."` entries (a checkpoint
surface may carry `::test_name` granularity; this backfill derives at FILE granularity only, one
locator per distinct file, sorted for determinism).

A contract whose checkpoints name no `test:` surface at all is SKIPPED and reported by name — it
is not given an empty/absent-derivation `done_when` (the schema requires `minItems: 1` anyway).
A contract that already carries a top-level `done_when` is also skipped (never overwritten).

THE FROZEN-HASH DISCIPLINE (load-bearing). `done_when` lives in the contract-proper region
(sibling of `scope`/`checkpoints`, hash-covered into `contract_sha256` per
schema/acceptance-contract.schema.json) — inserting it necessarily changes what that hash covers.
This script does ONE thing: insert the `done_when:` block as literal text immediately BEFORE the
`# === FOUNDRY-AUTHORIZED-TRAILER ...` sentinel line, preserving every other byte of the file
exactly (no YAML re-serialization — a full parse/re-dump would silently reflow comments, key
order, and quoting on every one of ~300 files). It NEVER edits the trailer itself (the
`authorized:` block, including the now-stale `contract_sha256`) and never recomputes a hash. Re-
freezing the trailer to match the new contract-proper content is the operator-proxy's own
follow-up step, in its own commit, narrated -- exactly the same two-step discipline
`/foundry:amend` already uses for a non-widening amendment (re-derive, then re-freeze, never in
the same silent motion).

A contract with NO sentinel at all (a DRAFT/never-authorized contract) has no trailer to protect;
the `done_when:` block is appended at the end of the file instead.

Usage:
    foundry-done-when-backfill.py --dry-run <workspace-root>
    foundry-done-when-backfill.py --apply   <workspace-root>

--dry-run (the default-safe mode): writes NOTHING. Prints one line per contract: `BACKFILL`,
    `SKIP (already has done_when)`, or `SKIP (no test: surface)`, plus a final summary count, and
    exits 0 always (a report, not a gate).
--apply: writes the derived `done_when:` block into every contract this would BACKFILL. Exits 0
    on success; exits 1 if any write failed (a per-file report still prints for every file).

Neither mode ever touches a contract outside `specs/features/foundry/**` under the given root.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import yaml

# MIRRORS scripts/foundry_contract.py's SENTINEL literal -- NOT imported, deliberately: this CLI
# runs against an ARBITRARY consumer workspace root, never against this plugin's own scripts/, so
# it carries no import-time coupling to a sibling plugin module.
SENTINEL = "# === FOUNDRY-AUTHORIZED-TRAILER (excluded from contract_sha256) ==="

CONTRACT_GLOB = os.path.join("specs", "features", "foundry", "**", "acceptance-contract.yaml")


def find_contracts(workspace_root: str) -> list:
    """Every `specs/features/foundry/**/acceptance-contract.yaml` under `workspace_root`, sorted
    for a deterministic report order."""
    pattern = os.path.join(workspace_root, CONTRACT_GLOB)
    return sorted(glob.glob(pattern, recursive=True))


def _has_done_when(doc) -> bool:
    return isinstance(doc, dict) and "done_when" in doc


def _test_surfaces(doc) -> list:
    """Every DISTINCT `test:<file>` locator this contract's checkpoints already name, sorted.
    A `surface` value of `"test:tests/x.py::test_name"` derives the FILE `tests/x.py` only --
    this backfill is file-granular by design (AC-SUB-3), coarser than a hand-authored per-test
    done_when may later choose to be."""
    if not isinstance(doc, dict):
        return []
    checkpoints = doc.get("checkpoints")
    if not isinstance(checkpoints, list):
        return []
    files = set()
    for cp in checkpoints:
        if not isinstance(cp, dict):
            continue
        surface = cp.get("surface")
        if not isinstance(surface, str) or not surface.startswith("test:"):
            continue
        locator = surface[len("test:"):]
        file_only = locator.split("::", 1)[0].strip()
        if file_only:
            files.add(file_only)
    return sorted(f"test:{f}" for f in files)


class ContractPlan:
    """The derived plan for one contract file: exactly one of BACKFILL / SKIP-HAS / SKIP-NO-TEST,
    never a fourth silent state."""

    def __init__(self, path, action, done_when=None):
        self.path = path
        self.action = action  # "backfill" | "skip-has" | "skip-no-test" | "skip-unreadable"
        self.done_when = done_when or []

    def report_line(self, root) -> str:
        rel = os.path.relpath(self.path, root)
        if self.action == "backfill":
            return f"BACKFILL {rel}: " + ", ".join(self.done_when)
        if self.action == "skip-has":
            return f"SKIP (already has done_when) {rel}"
        if self.action == "skip-no-test":
            return f"SKIP (no test: surface) {rel}"
        return f"SKIP (unreadable) {rel}"


def plan_for(path: str) -> ContractPlan:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ContractPlan(path, "skip-unreadable")
    # Parse ONLY the contract-proper region (before the sentinel, if any) -- the trailer's
    # `authorized:` block is intentionally never fed to the YAML parser here; a malformed trailer
    # must never block deriving/reporting the proper region's own done_when.
    proper_text = text.split(SENTINEL, 1)[0]
    try:
        doc = yaml.safe_load(proper_text) or {}
    except yaml.YAMLError:
        return ContractPlan(path, "skip-unreadable")
    if _has_done_when(doc):
        return ContractPlan(path, "skip-has")
    locators = _test_surfaces(doc)
    if not locators:
        return ContractPlan(path, "skip-no-test")
    return ContractPlan(path, "backfill", locators)


def render_done_when_block(locators: list) -> str:
    lines = ["done_when:"]
    for loc in locators:
        lines.append(f'  - "{loc}"')
    return "\n".join(lines) + "\n"


def apply_backfill(path: str, locators: list) -> None:
    """Insert the rendered `done_when:` block immediately BEFORE the sentinel line, preserving
    every other byte of the file exactly. Appends at end-of-file when no sentinel is present."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    block = render_done_when_block(locators)
    if SENTINEL in text:
        before, sep, after = text.partition(SENTINEL)
        new_text = before + block + sep + after
    else:
        # No trailer to protect (a DRAFT/never-authorized contract) -- append, preserving a
        # single trailing newline boundary rather than gluing onto a possibly-newline-less EOF.
        new_text = text if text.endswith("\n") else text + "\n"
        new_text += block
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="report only; writes nothing")
    mode.add_argument("--apply", action="store_true", help="write the derived done_when blocks")
    ap.add_argument("workspace_root", help="the consumer workspace root (holds specs/features/foundry/**)")
    args = ap.parse_args()

    root = os.path.abspath(args.workspace_root)
    if not os.path.isdir(root):
        print(f"error: not a directory: {args.workspace_root}", file=sys.stderr)
        return 2

    contracts = find_contracts(root)
    plans = [plan_for(p) for p in contracts]

    backfill = [p for p in plans if p.action == "backfill"]
    skip_has = [p for p in plans if p.action == "skip-has"]
    skip_no_test = [p for p in plans if p.action == "skip-no-test"]
    skip_unreadable = [p for p in plans if p.action == "skip-unreadable"]

    for plan in plans:
        print(plan.report_line(root))

    print(
        f"\n{len(contracts)} contract(s) scanned: "
        f"{len(backfill)} to backfill, {len(skip_has)} already declared, "
        f"{len(skip_no_test)} skipped (no test: surface), "
        f"{len(skip_unreadable)} unreadable/malformed."
    )

    if args.dry_run:
        return 0

    failures = 0
    for plan in backfill:
        try:
            apply_backfill(plan.path, plan.done_when)
        except OSError as e:
            failures += 1
            print(f"WRITE-FAILED {os.path.relpath(plan.path, root)}: {e}", file=sys.stderr)
    if failures:
        print(f"\n{failures} write failure(s).", file=sys.stderr)
        return 1
    print(f"\napplied {len(backfill)} backfill(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
