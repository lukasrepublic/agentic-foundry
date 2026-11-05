#!/usr/bin/env python3
"""governance-trace — the CI check that ties a PR to its ticket's Design (design §5.4, gv-16).

    governance-trace.py ticket-ref --pr-json FILE            prints `OWNER/NAME#N` (or `#N`) for the ticket the PR names
    governance-trace.py check --pr-json FILE --ticket-json FILE [--files-json FILE] [--repo OWNER/NAME]
                              [--project-dir PR_HEAD_DIR] [--base-dir BASE_REF_DIR] [--plugin-root DIR] [--today YYYY-MM-DD] [--json]

`--pr-json` is `gh pr view <n> --json number,body`, `--files-json` the PAGED changed-file list (`gh api --paginate
repos/O/R/pulls/N/files`, one `{"path": ...}` per file; without it `--pr-json` must carry `files`) and `--ticket-json` is
`gh issue view <n> --repo <the repo of the ticket ref> --json number,body,labels,author`. The script calls `gh` only to vet the
ticket author who is not a registered operator (`gh api user`, the collaborator permission); everything else is files.

TWO TREES. The policy, the locked blueprint, the declared trust boundaries and the operator list decide what is governed and who
is trusted, so they are read from `--base-dir` (a checkout of the PR's BASE ref): a PR cannot loosen the class that grades it.
`--project-dir` (the PR head) supplies only what the PR itself produces: `governance/rendered/` and the exceptions file.
It checks, and reports EVERY failing check (exit 1; exit 0 when all pass; exit 2 on bad input):

  ticket-named        the PR body names a ticket (`Ticket: #N`, `Closes|Fixes|Resolves|Refs #N`, `OWNER/NAME#N`)
  ticket-author       the ticket's author is trusted (a registered operator, or a write collaborator other than the agent)
  design-present      a diff that touches a design-required path has a `## Design` that cites at least one existing id
  cited-ids-exist     every id the Design cites exists in the effective rules (kits + governance/local)
  paths-in-design     every changed path falls within the Design's components (a file, a directory, or a glob);
                      `governance/rendered/` is generated and exempt; a component that matches everything (`**`) is refused. Without a Design, below the design-required
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
    r"(?im)^[ \t>*-]*(?:ticket|closes|fixes|resolves|refs?)[ \t]*:?[ \t]*(?:([\w.-]+/[\w.-]+))?#(\d+)\b")
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
    """(repo or None, number) of the first ticket the PR body names, or (None, None)."""
    m = PR_REF_RE.search(pr.get("body") or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _too_broad(component: str, g) -> bool:
    """A component that matches (nearly) any path says nothing about where the change is."""
    c = component.strip("/")
    if c in ("*", "**", "**/*", "*/**"):
        return True
    if g is None:
        return False
    try:
        rx = g.glob_re(component)
    except Exception:  # noqa: BLE001
        return False
    return all(rx.fullmatch(p) for p in ("x.py", "a/b.py", "a/b/c/d.txt", "docs/x/y/z.md"))


def _default_gh_json(args):
    import subprocess
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


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


def run_checks(pr: dict, ticket: dict, project: Path, plugin: Path, today: str | None, base: Path | None = None,
               gh_json=None, repo: str | None = None, files: list | None = None) -> list:
    ft = _load_ticket_module(plugin)
    base = base or project
    gh_json = gh_json or _default_gh_json
    findings = []

    def add(check, ok, detail, path=None):
        f = {"check": check, "ok": ok, "detail": detail}
        if path:
            f["path"] = path
        findings.append(f)

    ref_repo, n = ticket_ref(pr)
    add("ticket-named", n is not None,
        f"the PR names ticket {(ref_repo or '') + '#' + str(n)}" if n else "the PR body names no ticket (add `Ticket: #<n>`)")
    raw_files = files if files is not None else (pr.get("files") or [])
    changed = [str(f.get("path") if isinstance(f, dict) else f) for f in raw_files]
    changed = [f for f in changed if f and f != "None"]
    # the ticket author must be trusted: a Design from an untrusted account is not a Design
    login = ((ticket or {}).get("author") or {}).get("login") or ""
    trust_repo = ref_repo or repo
    ok_a, why_a = ft.author_trusted(trust_repo, login, gh_json, operators=ft.trusted_operator_logins(base))
    add("ticket-author", ok_a, why_a)
    body = (ticket or {}).get("body") or ""
    gv = ft.governance_context(base)               # policy, lock, boundaries and rules from the BASE ref
    view = ft.design_view(body, (ticket or {}).get("labels") or [], gv=gv, root=base)
    design = view["design"]
    governed = [f for f in changed if ft._matches_any(gv, f, gv["design_globs"]) or ft._matches_any(gv, f, gv["sec_globs"])]
    needs_design = bool(governed) or view["design_required"]
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
    scoped = [f for f in changed if not f.startswith(RENDERED_PREFIX)]
    g = gv.get("_gov")
    broad = [c for c in design["components"] if _too_broad(c, g)]
    if broad:
        add("paths-in-design", False, "the Design's component(s) " + ", ".join(broad) + " match every path, so they say nothing about "
            "where the change is; name the directories or files")
    elif design["present"] and design["components"]:
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
    # rendered-fresh, exceptions-live: what the PR itself produces, judged on the PR head (foundry_governance.py check)
    gp = ft.governance_context(project)
    gm = gp.get("_gov")
    if gm is None:
        add("rendered-fresh", False, f"governance module unreadable: {gp.get('error')}")
        add("exceptions-live", False, f"governance module unreadable: {gp.get('error')}")
    else:
        ns = argparse.Namespace(project_dir=str(project), plugin_root=str(plugin), today=today)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                gm.cmd_check(ns)
            lines = [ln for ln in buf.getvalue().splitlines() if ln and not ln.startswith("governance-check-ok") and "SKIP exception" not in ln]
        except gm.Refused as e:
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
    p.add_argument("--project-dir", help="the PR head checkout (rendered rules, exceptions)")
    p.add_argument("--base-dir", help="a checkout of the PR's BASE ref (policy, lock, boundaries, operators); default: --project-dir")
    p.add_argument("--files-json", help="the paged changed-file list: a JSON list of {\"path\": ...}")
    p.add_argument("--repo", help="OWNER/NAME of the repository the PR is in (the default repo of a bare #N ticket ref)")
    p.add_argument("--plugin-root")
    p.add_argument("--today")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        pr = json.loads(Path(a.pr_json).read_text(encoding="utf-8"))
        if a.verb == "ticket-ref":
            r, n = ticket_ref(pr)
            if n is None:
                print("ticket-named: the PR body names no ticket", file=sys.stderr)
                return 1
            print(f"{r or ''}#{n}")
            return 0
        ticket = json.loads(Path(a.ticket_json).read_text(encoding="utf-8"))
        project = Path(a.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        files = json.loads(Path(a.files_json).read_text(encoding="utf-8")) if a.files_json else None
        base = Path(a.base_dir).resolve() if a.base_dir else None
        findings = run_checks(pr, ticket, project, _plugin_root(a.plugin_root), a.today, base=base, repo=a.repo, files=files)
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
