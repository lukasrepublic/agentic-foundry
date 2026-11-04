#!/usr/bin/env python3
"""foundry_brief.py -- the shared brief preamble every builder and reviewer brief starts with.

  foundry_brief.py [--project-dir DIR] [--plugin-root DIR] render --ticket FILE.json --paths P [P ...]
                   [--blueprint ID] [--style on|off]

Sections, in order: always-on core (product charter by path if present, conduct rules), the blueprint's
level-1 rules, the ticket's cited rule ids with statements inlined, path-scoped rules, the communication
style block (unless `communication.style` is off), hygiene and shell-shape rules.

Ticket JSON: `{"title": str, "cites": ["FG-SEC-006", ...]}` (`cited_ids` is accepted as an alias).
The style setting comes from the operating-model policy loader (`foundry_operating_model`, a sibling
atom) when that module exists, else the default (on). `--style` overrides both.

Output stays under the dispatch inline cap: every blank-line-delimited paragraph is at most the cap
(`foundry_dispatch_lint.read_inline_cap`). Local files only; no network, no subprocess.
Exit 0 ok / 2 refused.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_dispatch_lint as _lint  # noqa: E402
import foundry_governance as _gov  # noqa: E402

STYLE_ON = "simplified-technical-english"
STYLE_BLOCK = """Communication style (simplified technical English, not strict ASD-STE100):
- Use short, direct sentences and active voice.
- Give one instruction per sentence.
- Use consistent terms for the same concept.
- State conditions and required actions explicitly.
- Avoid idioms, vague wording and unnecessary jargon.
- Keep necessary technical terms. Explain unfamiliar terms.
- Keep the articles and other words that make a sentence complete.
- Keep technical accuracy and necessary detail.
- Follow a different style when the operator asks for one."""
HYGIENE = """Hygiene:
- Write paper (specs, notes, reports) only when the ticket asks for it.
- Never print, copy or commit a secret value. Verify secrets by metadata only.
- Keep names of private organizations, people and accounts out of public repositories.
- Remove scratch files you created before you finish."""
SHELL_SHAPE = """Shell shape:
- Run one plain command per Bash call. Do not chain with ; && or |.
- Use absolute paths. Do not change directory.
- Write text that names a guarded command with Write or Edit, never a heredoc."""
CHARTER_CANDIDATES = (".foundry/product-charter.md", "PRODUCT-CHARTER.md", "docs/product-charter.md")


def communication_style(proj: Path, override=None) -> str:
    """'on' or 'off'. Policy loader when present, else on."""
    if override:
        return "off" if override == "off" else "on"
    try:
        om = importlib.import_module("foundry_operating_model")  # optional sibling module (gv-03)
    except ImportError:
        return "on"
    try:
        for name in ("communication_style", "style"):
            fn = getattr(om, name, None)
            if callable(fn):
                val = fn(str(proj))
                return "off" if str(val).strip().lower() == "off" else "on"
        res = om.resolve(str(proj)) if hasattr(om, "resolve") else om.load(str(proj))
        comm = res.get("communication") if isinstance(res, dict) else getattr(res, "communication", None)
        val = comm.get("style") if isinstance(comm, dict) else getattr(comm, "style", None)
        return "off" if str(val).strip().lower() == "off" else "on"
    except Exception:  # noqa: BLE001 -- a broken policy never blocks a brief; default on
        return "on"


def _line(r, short=False) -> str:
    return f"- {r['id']}: {r['statement'].strip()}"


def build_brief(proj: Path, root: Path, ticket: dict, paths: list, blueprint=None, style=None, cap=None) -> str:
    kits, local = _gov.load_kits(root), _gov.load_local(proj)
    rules = _gov._checked_effective(kits, local)
    active = [r for r in rules if r.get("status") == "active"]
    by_id = {r["id"]: r for r in rules}
    used = set()
    blocks = []

    core = ["Always-on core:"]
    charter = next((c for c in CHARTER_CANDIDATES if (proj / c).is_file()), None)
    if charter:
        core.append(f"- Product charter: read {charter} before you design.")
    conduct = [r for r in active if r["category"] == "conduct"]
    core += [_line(r) for r in conduct]
    used |= {r["id"] for r in conduct}
    blocks.append("\n".join(core))

    def bp_ok(r):
        b = r["applies_to"]["blueprints"]
        return "*" in b or (blueprint is not None and blueprint in b)

    cites = ticket.get("cites") or ticket.get("cited_ids") or []
    if not isinstance(cites, list):
        raise _gov.Refused("ticket-cites-must-be-a-list")
    cited = []
    for cid in cites:
        if cid not in by_id:
            raise _gov.Refused(f"cited-rule-unknown:{cid}")
        cited.append(by_id[cid])
    used |= {r["id"] for r in cited}

    lvl1 = [r for r in active if r["level"] == 1 and bp_ok(r) and r["id"] not in used]
    if lvl1:
        blocks.append("\n".join(["Level-1 rules for this blueprint:"] + [_line(r) for r in lvl1]))
        used |= {r["id"] for r in lvl1}
    if cited:
        blocks.append("\n".join(["Rules this ticket cites:"] + [_line(r) for r in cited]))

    scoped = [by_id[i] for i in _gov.applicable(rules, paths) if i not in used]
    if scoped:
        blocks.append("\n".join(["Rules for the paths in this change:"] + [_line(r) for r in scoped]))

    if communication_style(proj, style) != "off":
        blocks.append(STYLE_BLOCK)
    blocks += [HYGIENE, SHELL_SHAPE]

    cap = cap if cap is not None else _lint.read_inline_cap(str(proj))
    out = []
    for b in blocks:
        if len(b) > cap:  # keep every paragraph under the inline cap: split by line, truncate a lone long line
            piece = ""
            for ln in b.split("\n"):
                ln = ln if len(ln) <= cap else ln[: max(cap - 12, 0)] + " ...[more]"
                if piece and len(piece) + 1 + len(ln) > cap:
                    out.append(piece)
                    piece = ln
                else:
                    piece = f"{piece}\n{ln}" if piece else ln
            out.append(piece)
        else:
            out.append(b)
    return "\n\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_brief.py", description="Render the shared brief preamble.")
    ap.add_argument("--project-dir")
    ap.add_argument("--plugin-root")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("render")
    p.add_argument("--ticket", required=True)
    p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--blueprint")
    p.add_argument("--style", choices=("on", "off"))
    args = ap.parse_args(argv)
    try:
        try:
            ticket = json.loads(Path(args.ticket).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise _gov.Refused("ticket-unreadable")
        if not isinstance(ticket, dict):
            raise _gov.Refused("ticket-must-be-an-object")
        sys.stdout.write(build_brief(_gov.project_dir(args.project_dir), _gov.plugin_root(args.plugin_root),
                                     ticket, args.paths, args.blueprint, args.style))
        return 0
    except _gov.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
