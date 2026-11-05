#!/usr/bin/env python3
"""foundry_om_trace — the operating-model traceability table (AC-OM-10, gv-16).

    foundry_om_trace.py --charters DIR [--prompt FILE] [--json] [--strict]

Maps every rule of the operating-model port prompt (A1 .. G3) to the acceptance criteria of the programme's charters
and exits 1, naming each rule that maps to none. READ-ONLY: it reads the charter files (and, with --prompt, the
prompt's own rule headings) and prints a table; it writes nothing and runs no network call.

A charter MAPS a rule when the rule id appears in the charter's `## Goal` section or in one of its
`## Acceptance criteria` bullets, and the charter has at least one acceptance criterion. A mention inside an
acceptance-criterion bullet is the strong form (the row names the bullet's `AC-...` id when it has one); a mention
in the Goal is the weak form (the charter's acceptance criteria collectively realise what its Goal declares). With
`--strict` only the strong form counts. A single-letter range such as `A1-A4` or `F1-F4` expands; a cross-letter
span written `A1..G3` is NOT a range and maps nothing (it would map every rule to the line that names the span).

`--prompt FILE` checks the embedded rule list against the prompt's own `A1. Title ...` headings and exits 2 on
a difference (the list in this file is then out of date).

Exit: 0 every rule mapped, 1 at least one rule unmapped, 2 bad input (no charters, unreadable file, prompt drift).
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The rule ids and short titles of the port prompt, in order. Floors and tunables are the policy file's business.
RULES = (
    ("A1", "Coarse-grained PRs and a merge plan every tick"),
    ("A2", "Merge-queue discipline"),
    ("A3", "Full suite only when required"),
    ("A4", "Agent review is the floor for small teams"),
    ("A5", "Code first; largest cluster first; no silent stalls"),
    ("A6", "A fix is done only when it is re-measured"),
    ("B1", "Time to market through parallel fan-out"),
    ("B2", "Bound parallelism by the machine"),
    ("B3", "Research answers questions"),
    ("B4", "\"Needs the operator\" is a claim that needs an attempt"),
    ("B5", "Work claims across machines"),
    ("B6", "Shell commands that raise no permission prompts"),
    ("B7", "Token discipline"),
    ("B8", "The smallest workflow size wins"),
    ("B9", "An armed pause means start nothing new"),
    ("B10", "Do not correct a running agent mid-task"),
    ("C1", "Cost is a decision input"),
    ("C2", "Minimal CI; budgets are tools, not gates"),
    ("C3", "Scale-to-zero for occasional privileged jobs"),
    ("D1", "Never read a secret value"),
    ("D2", "Production is stated before and reported after"),
    ("D3", "Every capability is reachable from every surface"),
    ("E1", "A short four-block report"),
    ("E2", "Keep communication simple and standardized"),
    ("E3", "Operator hand-off files"),
    ("F1", "Pair every start with a teardown"),
    ("F2", "Sweep the right disk each tick"),
    ("F3", "Push every head"),
    ("F4", "One worktree per builder; no shared stash"),
    ("G1", "No baseline before the first fully green run"),
    ("G2", "A delivery record names who built it"),
    ("G3", "Commit before you authorize"),
)
RULE_IDS = tuple(r for r, _ in RULES)

_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])[ \t]+(.*)$")
_AC_ID = re.compile(r"\bAC-[A-Z]+-\d+\b")
# `A1`, `A1-A4`, `A1–4`; never part of a longer word, never an endpoint of an `A1..G3` span.
_TOKEN = re.compile(r"(?<![\w.])(?<!\.\.)([A-G])(\d{1,2})(?:[ \t]*[–—-][ \t]*(?:\1)?(\d{1,2}))?(?![\w])(?!\.\.)")


def _sections(text: str) -> dict:
    marks = [(m.start(), m.end(), m.group(1).strip().lower()) for m in _HEADING.finditer(text)]
    out = {}
    for i, (_, end, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.setdefault(name, text[end:stop])
    return out


def rule_mentions(text: str) -> set:
    """Rule ids named in `text` (single ids, and same-letter ranges expanded). Only ids in RULE_IDS count."""
    found = set()
    for m in _TOKEN.finditer(text):
        letter, lo, hi = m.group(1), int(m.group(2)), m.group(3)
        top = int(hi) if hi else lo
        if top < lo or top - lo > 12:
            continue
        for n in range(lo, top + 1):
            rid = f"{letter}{n}"
            if rid in RULE_IDS:
                found.add(rid)
    return found


def charter_facts(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    secs = _sections(text)
    goal = next((v for k, v in secs.items() if k == "goal"), "")
    ac_text = next((v for k, v in secs.items() if k.startswith("acceptance criteria")), "")
    acs, cur = [], None
    for line in ac_text.split("\n"):
        m = _BULLET.match(line)
        if m:
            cur = {"text": m.group(1), "id": None}
            acs.append(cur)
        elif cur is not None and line.strip():
            cur["text"] += " " + line.strip()
    for ac in acs:
        m = _AC_ID.search(ac["text"])
        ac["id"] = m.group(0) if m else None
    return {"charter": path.stem, "goal": goal, "acs": acs}


def trace(charters_dir: Path, strict: bool = False) -> dict:
    files = sorted(charters_dir.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"no charter files (*.md) in {charters_dir}")
    facts = [charter_facts(f) for f in files]
    rows = {rid: [] for rid in RULE_IDS}
    for f in facts:
        if not f["acs"]:
            continue                      # a charter with no acceptance criterion maps nothing
        for i, ac in enumerate(f["acs"], 1):
            for rid in sorted(rule_mentions(ac["text"])):
                rows[rid].append({"charter": f["charter"], "via": "ac", "ac": ac["id"] or f"AC#{i}"})
        if not strict:
            seen_ac = {rid for rid, hits in rows.items() if any(h["charter"] == f["charter"] for h in hits)}
            for rid in sorted(rule_mentions(f["goal"]) - seen_ac):
                rows[rid].append({"charter": f["charter"], "via": "goal", "ac": None})
    unmapped = [rid for rid in RULE_IDS if not rows[rid]]
    return {"rules": [{"rule": rid, "title": dict(RULES)[rid], "maps": rows[rid]} for rid in RULE_IDS],
            "unmapped": unmapped, "charters": [f["charter"] for f in facts], "strict": strict}


def render_table(result: dict) -> str:
    lines = ["| Rule | Title | Charter | Acceptance criterion |", "|---|---|---|---|"]
    for r in result["rules"]:
        if not r["maps"]:
            lines.append(f"| {r['rule']} | {r['title']} | UNMAPPED | - |")
            continue
        for m in r["maps"]:
            ac = m["ac"] if m["via"] == "ac" else "(the charter's Goal)"
            lines.append(f"| {r['rule']} | {r['title']} | {m['charter']} | {ac} |")
    return "\n".join(lines)


def prompt_rule_ids(path: Path) -> list:
    return re.findall(r"^([A-G]\d{1,2})\.[ \t]", path.read_text(encoding="utf-8"), re.M)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_om_trace.py", description=__doc__.split("\n")[0])
    ap.add_argument("--charters", required=True, help="directory holding the programme's charter files")
    ap.add_argument("--prompt", help="the port prompt; its rule headings must equal the embedded list")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="count only a mention inside an acceptance-criterion bullet")
    a = ap.parse_args(argv)
    try:
        if a.prompt:
            have = prompt_rule_ids(Path(a.prompt))
            if sorted(have) != sorted(RULE_IDS):
                diff = sorted(set(have) ^ set(RULE_IDS))
                print(f"prompt-drift: the prompt's rule ids differ from the embedded list: {', '.join(diff)}", file=sys.stderr)
                return 2
        result = trace(Path(a.charters), strict=a.strict)
    except (OSError, UnicodeDecodeError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_table(result))
        if result["unmapped"]:
            print("\nunmapped rule(s): " + ", ".join(result["unmapped"]))
        else:
            print(f"\nall {len(RULE_IDS)} rules map to at least one acceptance criterion")
    return 1 if result["unmapped"] else 0


if __name__ == "__main__":
    sys.exit(main())
