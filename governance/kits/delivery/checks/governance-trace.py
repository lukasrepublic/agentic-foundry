#!/usr/bin/env python3
"""governance-trace — the CI check that ties a PR to its ticket's Design (design §5.4, gv-16).

    governance-trace.py ticket-ref --pr-json FILE
    governance-trace.py check --pr-json FILE --ticket-json FILE [--project-dir DIR] [--plugin-root DIR] [--today YYYY-MM-DD] [--json]

`--pr-json` is `gh pr view <n> --json number,body,files` and `--ticket-json` is `gh issue view <n> --json number,body,labels`;
this script never calls `gh` or the network itself, so the job template runs the two reads and hands the files over.
It checks, and reports EVERY failing check (exit 1; exit 0 when all pass; exit 2 on bad input):

  ticket-named        the PR body names a ticket (`Ticket: #N`, `Closes|Fixes|Resolves|Refs #N`, `OWNER/NAME#N`)
  design-present      a diff that touches a design-required path has a `## Design` that cites at least one existing id
  cited-ids-exist     every id the Design cites exists in the effective rules (kits + governance/local)
  paths-in-design     every changed path falls within the Design's components (a file, a directory, or a glob);
                      `governance/rendered/` is generated and exempt. Without a Design, below the design-required
                      class, the check passes with a note (the sections are a checklist there).
  rendered-fresh      `governance/rendered/` matches what `foundry_governance.py render` would write
  exceptions-live     no exception in `governance/local/exceptions.yaml` has expired

The Design parser and the design-required class are the ticket verb's own (`scripts/foundry-ticket.py`), loaded from the
plugin root; the freshness and exception checks are `foundry_governance.py check`'s. Nothing is re-implemented here.
Run it from a checkout of the PR's head with the plugin at the LOCKED version (the job template does both); the PR's own
files are only READ, never executed.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path

PR_REF_RE = re.compile(
    r"(?im)^[ \t>*-]*(?:ticket|closes|fixes|resolves|refs?)[ \t]*:?[ \t]*(?:[\w.-]+/[\w.-]+)?#(\d+)\b")
RENDERED_PREFIX = "governance/rendered/"


def _plugin_root(arg) -> Path:
    if arg:
        return Path(arg).resolve()
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]      # governance/kits/delivery/checks/<this file> inside the plugin


def _load_ticket_module(plugin: Path):
    scripts = plugin / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("foundry_ticket_for_trace", scripts / "foundry-ticket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ticket_ref(pr: dict):
    m = PR_REF_RE.search(pr.get("body") or "")
    return int(m.group(1)) if m else None


def _within(path: str, components: list, gv: dict, ft) -> bool:
    g = gv.get("_gov")
    for c in components:
        if path == c or path.startswith(c.rstrip("/") + "/"):
            return True
        if g is not None and any(ch in c for ch in "*?["):
            try:
                if g.glob_re(c).fullmatch(path):
                    return True
            except Exception:  # noqa: BLE001 - a malformed glob matches nothing
                pass
    return False


def run_checks(pr: dict, ticket: dict, project: Path, plugin: Path, today: str | None) -> list:
    ft = _load_ticket_module(plugin)
    findings = []

    def add(check, ok, detail, path=None):
        f = {"check": check, "ok": ok, "detail": detail}
        if path:
            f["path"] = path
        findings.append(f)

    n = ticket_ref(pr)
    add("ticket-named", n is not None,
        f"the PR names ticket #{n}" if n else "the PR body names no ticket (add `Ticket: #<n>`)")
    files = [str(f.get("path") if isinstance(f, dict) else f) for f in (pr.get("files") or [])]
    files = [f for f in files if f and f != "None"]
    body = (ticket or {}).get("body") or ""
    gv = ft.governance_context(project)
    view = ft.design_view(body, (ticket or {}).get("labels") or [], gv=gv, root=project)
    design = view["design"]
    governed = [f for f in files if ft._matches_any(gv, f, gv["design_globs"]) or ft._matches_any(gv, f, gv["sec_globs"])]
    needs_design = bool(governed) or view["design_required"]
    # design-present + cited-ids-exist
    if needs_design:
        defects = ft.design_defects(ft.parse_sections(body), gv)
        add("design-present", not any(d.startswith(("no `## Design`", "`## Design` cites no")) for d in defects),
            ("a governed path has no Design citing a rule id: " + (governed[0] if governed else "size-tag class")
             + "; " + "; ".join(defects)) if defects else "the Design cites at least one rule id")
        unknown = [i for i in design["cites"] if i not in gv["rules"]] if not gv.get("error") else []
        add("cited-ids-exist", not unknown and not gv.get("error"),
            ("unknown cited id(s): " + ", ".join(unknown)) if unknown else
            (f"governance rules unreadable: {gv['error']}" if gv.get("error") else "every cited id exists"))
    else:
        unknown = [i for i in design["cites"] if i not in gv["rules"]] if (design["present"] and not gv.get("error")) else []
        add("design-present", True, "below the design-required class: the sections are a checklist")
        add("cited-ids-exist", not unknown, ("unknown cited id(s): " + ", ".join(unknown)) if unknown else "every cited id exists")
    # paths-in-design
    scoped = [f for f in files if not f.startswith(RENDERED_PREFIX)]
    if design["present"] and design["components"]:
        outside = [f for f in scoped if not _within(f, design["components"], gv, ft)]
        if outside:
            for f in outside:
                add("paths-in-design", False, f"{f} is outside the Design's components ({', '.join(design['components'])})", f)
        else:
            add("paths-in-design", True, "every changed path is inside the Design's components")
    elif needs_design:
        add("paths-in-design", False, "the Design names no components, so no changed path can be shown to be inside it")
    else:
        add("paths-in-design", True, "no Design components declared; below the design-required class")
    # rendered-fresh, exceptions-live (foundry_governance.py check, in-process)
    g = gv.get("_gov")
    if g is None:
        add("rendered-fresh", False, f"governance module unreadable: {gv.get('error')}")
        add("exceptions-live", False, f"governance module unreadable: {gv.get('error')}")
    else:
        ns = argparse.Namespace(project_dir=str(project), plugin_root=str(plugin), today=today)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                g.cmd_check(ns)
            lines = [ln for ln in buf.getvalue().splitlines() if ln and not ln.startswith("governance-check-ok") and "SKIP exception" not in ln]
        except g.Refused as e:
            lines = [f"refused: {e}"]
        stale = [ln for ln in lines if ln.startswith(("rendered-stale", "lock-"))]
        exc = [ln for ln in lines if ln not in stale]
        add("rendered-fresh", not stale, "; ".join(stale) if stale else "governance/rendered/ matches the rules")
        add("exceptions-live", not exc, "; ".join(exc) if exc else "no exception has expired")
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="governance-trace.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("ticket-ref")
    p.add_argument("--pr-json", required=True)
    p = sub.add_parser("check")
    p.add_argument("--pr-json", required=True)
    p.add_argument("--ticket-json", required=True)
    p.add_argument("--project-dir")
    p.add_argument("--plugin-root")
    p.add_argument("--today")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        pr = json.loads(Path(a.pr_json).read_text(encoding="utf-8"))
        if a.verb == "ticket-ref":
            n = ticket_ref(pr)
            if n is None:
                print("ticket-named: the PR body names no ticket", file=sys.stderr)
                return 1
            print(n)
            return 0
        ticket = json.loads(Path(a.ticket_json).read_text(encoding="utf-8"))
        project = Path(a.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        findings = run_checks(pr, ticket, project, _plugin_root(a.plugin_root), a.today)
    except (OSError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    bad = [f for f in findings if not f["ok"]]
    if a.json:
        print(json.dumps({"ok": not bad, "findings": findings}, indent=2))
    else:
        for f in findings:
            print(("PASS " if f["ok"] else "FAIL ") + f"{f['check']}: {f['detail']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
