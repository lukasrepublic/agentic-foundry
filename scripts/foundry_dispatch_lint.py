#!/usr/bin/env python3
"""foundry_dispatch_lint — the deterministic dispatch-prompt claim-check lint
(feat-foundry-worker-context-diet, AC-WCD-3/-6).

Universal claim-check I/O rule for the dispatch path (AC-WCD-1): a dispatch prompt references its
atom's spec/contract/evidence artifacts BY PATH (worker Reads them in its own context) rather than
inlining their bodies. This module is the deterministic, standalone-or-composed lint that catches a
violation of that rule in an ALREADY-ASSEMBLED prompt string:

  1. **embedded-spec-body** — the prompt contains a `<!-- normative -->` ... `<!-- /normative -->`
     fenced span (a spec's normative region copied in whole, rather than referenced by path).
  2. **embedded-contract-body** — the prompt contains >= 2 of the `acceptance-contract.yaml`
     structural markers (`spec_ref:`, `checkpoints:`, `authorized:`, `contract_sha256:`) together
     (a short quoted checkpoint line is fine — AC-WCD-1's design note; the whole contract shape is
     not).
  3. **over-cap-inline-block** — any single blank-line-delimited paragraph exceeding the per-artifact
     inline cap (chars). The cap is PER ARTIFACT/BLOCK, not per-prompt (a charter legitimately quotes
     several short checkpoints across several short paragraphs).

Every finding names its offending SPAN (`start`/`end` char offsets into the prompt) plus a `detail`
string, so a caller can point straight at the violating text.

The cap (AC-WCD-1) is read from `<project_dir>/.foundry/dispatch-inline-cap` — a single positive
integer, characters, surrounding whitespace tolerated; absent or invalid (non-integer, non-positive)
falls back to the `DEFAULT_CAP` (2048).

Invocable standalone:
    python3 scripts/foundry_dispatch_lint.py <prompt-file> [--strict] [--cap N] [--project-dir DIR]
    <assembled-prompt> | python3 scripts/foundry_dispatch_lint.py [--strict]   # via stdin

Advisory fail-open at dispatch time: prints findings and exits 0 by default (never blocks a spawn on
its own); `--strict` makes a non-empty finding list exit 1 (for a CI/preflight gate that wants to
enforce it). Also composed into the dispatch preflight — `scripts/foundry-spawn-worker` imports
`lint_prompt()` directly and prints an advisory (fail-open, never blocking) over its assembled prompt
before spawning (AC-WCD-3 "invocable standalone and from the dispatch preflight").

Threat model — TRUSTED OPERATOR; non-security-flagged (spec threat model, feat-foundry-worker-
context-diet). A missed lint wastes tokens, not a safety hole; the merge gate is untouched
(AC-WCD-5).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

DEFAULT_CAP = 2048

NORMATIVE_OPEN = "<!-- normative -->"
NORMATIVE_CLOSE = "<!-- /normative -->"

CONTRACT_MARKERS = ("spec_ref:", "checkpoints:", "authorized:", "contract_sha256:")

_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def resolve_project_dir(project_dir=None):
    """Precedence: explicit `project_dir` -> `$CLAUDE_PROJECT_DIR` -> `git rev-parse --show-toplevel`
    -> cwd. Never raises."""
    if project_dir:
        return os.path.abspath(project_dir)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return os.path.abspath(env)
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                              timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def read_inline_cap(project_dir=None):
    """AC-WCD-1: read the per-artifact inline cap from `<root>/.foundry/dispatch-inline-cap` — a
    single positive integer (chars), surrounding whitespace tolerated. Absent or invalid (not an
    int, or <= 0) -> `DEFAULT_CAP`. Never raises."""
    root = resolve_project_dir(project_dir)
    path = os.path.join(root, ".foundry", "dispatch-inline-cap")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError:
        return DEFAULT_CAP
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_CAP
    if val <= 0:
        return DEFAULT_CAP
    return val


def _paragraphs(text):
    """Blank-line-delimited paragraphs, each as (start, end, text) char-offset spans into `text`."""
    out = []
    pos = 0
    for part in _BLANK_LINE_RE.split(text):
        if part.strip():
            idx = text.find(part, pos)
            if idx == -1:
                idx = pos
            out.append((idx, idx + len(part), part))
            pos = idx + len(part)
        else:
            pos += len(part)
    return out


def lint_prompt(text, cap=None):
    """Return a list of finding dicts `{class, start, end, detail}` (`[]` == clean). `cap` defaults
    to `DEFAULT_CAP` when omitted."""
    cap = cap if cap is not None else DEFAULT_CAP
    findings = []

    # 1. embedded-spec-body — a full `<!-- normative -->`..`<!-- /normative -->` fenced span.
    start = text.find(NORMATIVE_OPEN)
    if start != -1:
        close = text.find(NORMATIVE_CLOSE, start)
        end = (close + len(NORMATIVE_CLOSE)) if close != -1 else len(text)
        span_len = end - start
        findings.append({
            "class": "embedded-spec-body",
            "start": start, "end": end,
            "detail": (f"prompt embeds a spec normative region ({span_len} chars) between "
                       f"{NORMATIVE_OPEN!r} and {NORMATIVE_CLOSE!r} at offset {start}-{end}; "
                       "reference the spec BY PATH instead (rooted at $CLAUDE_PROJECT_DIR) — the "
                       "worker Reads it in its own context"),
        })

    # 2. embedded-contract-body — >= 2 of the acceptance-contract.yaml structural markers together.
    hits = [m for m in CONTRACT_MARKERS if m in text]
    if len(hits) >= 2:
        offsets = [text.find(m) for m in hits]
        c_start, c_end = min(offsets), max(o + len(hits[i]) for i, o in enumerate(offsets))
        findings.append({
            "class": "embedded-contract-body",
            "start": c_start, "end": c_end,
            "detail": (f"prompt embeds acceptance-contract.yaml structure (markers {hits}) at "
                       f"offset {c_start}-{c_end}; reference the contract BY PATH instead (rooted "
                       "at $CLAUDE_PROJECT_DIR) — the worker Reads it in its own context"),
        })

    # 3. over-cap-inline-block — any single paragraph exceeding the per-artifact cap.
    for p_start, p_end, block in _paragraphs(text):
        if len(block) > cap:
            findings.append({
                "class": "over-cap-inline-block",
                "start": p_start, "end": p_end,
                "detail": (f"inline block of {len(block)} chars exceeds the per-artifact cap "
                           f"({cap} chars) at offset {p_start}-{p_end}; reference the artifact BY "
                           "PATH instead of inlining its body"),
            })

    return findings


def _format_findings(findings, cap):
    if not findings:
        return f"foundry-dispatch-lint: clean (cap={cap} chars)"
    lines = [f"foundry-dispatch-lint: {len(findings)} finding(s) (cap={cap} chars):"]
    for f in findings:
        lines.append(f"  [{f['class']}] offset {f['start']}-{f['end']}: {f['detail']}")
    return "\n".join(lines)


def _main(argv=None):
    ap = argparse.ArgumentParser(
        prog="foundry_dispatch_lint",
        description="Flag an assembled dispatch prompt that exceeds the per-artifact inline cap or "
                     "embeds a spec/contract body. Advisory fail-open by default; --strict exits "
                     "non-zero on any finding.",
    )
    ap.add_argument("prompt_file", nargs="?", default=None,
                     help="path to a file holding the assembled prompt (default: read stdin)")
    ap.add_argument("--strict", action="store_true", help="non-zero exit on any finding")
    ap.add_argument("--cap", type=int, default=None,
                     help="override the inline cap (chars); else read from .foundry/dispatch-inline-cap")
    ap.add_argument("--project-dir", dest="project_dir", default=None,
                     help="root for resolving .foundry/dispatch-inline-cap (default $CLAUDE_PROJECT_DIR/cwd)")
    args = ap.parse_args(argv)

    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    cap = args.cap if args.cap is not None else read_inline_cap(args.project_dir)
    findings = lint_prompt(text, cap=cap)
    print(_format_findings(findings, cap))
    if findings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
