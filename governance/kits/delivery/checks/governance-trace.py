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
  built-from          (dl-07) a PR in a product repo that a workspace governs carries `Built-from: <40-hex workspace sha>` in
                      its body or head commit (the commit read from --head-sha), all trailers agreeing, outside code fences and `>` quotes; the sha
                      must exist in the workspace repo AND be an ancestor of its default branch. Armed by the FOUNDRY_WORKSPACE_REPO
                      variable (OWNER/NAME; `unset` or absent means not set), else by the base ref's binding file. On the CLI a
                      set-but-empty or malformed variable, or an unreadable binding, fails (the CI template maps an empty one to `unset`). With neither source the rule skips with a named reason.
                      Workspace calls all go through `_workspace_gh`.

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
BUILT_FROM_RE = re.compile(r"(?im)^[ \t*-]*built-from[ \t]*:[ \t]*(\S*)[ \t]*$")
SHA40_RE = re.compile(r"[0-9a-f]{40}")


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


def _scope_repos(base: Path) -> set:
    """Lower-cased OWNER/NAME slugs the BASE ref's `.claude/foundry-project.json` lists: `github_scope.repos`, every explicit
    slug in `repos{}`, and `github_scope.planning_repo` (a bare name takes the scope's owner)."""
    out = set()
    try:
        doc = json.loads((base / ".claude" / "foundry-project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(doc, dict):
        return out
    scope = doc.get("github_scope") if isinstance(doc.get("github_scope"), dict) else {}
    owner = str(scope.get("owner") or "")
    def add(x):
        x = str(x or "").strip()
        if re.fullmatch(r"[\w.-]+/[\w.-]+", x):
            out.add(x.lower())
        elif x and owner and re.fullmatch(r"[\w.-]+", x):
            out.add(f"{owner}/{x}".lower())
    for x in (scope.get("repos") or []) if isinstance(scope.get("repos"), list) else []:
        add(x)
    add(scope.get("planning_repo"))
    for v in (doc.get("repos") or {}).values() if isinstance(doc.get("repos"), dict) else []:
        add(v if isinstance(v, str) else (v or {}).get("repo") or (v or {}).get("slug"))
    return out


SLUG_RE = re.compile(r"[\w.-]+/[\w.-]+")
WS_TOKEN_ENV = "FOUNDRY_WORKSPACE_READ_TOKEN"
WS_UNSET = "unset"      # what the job template passes when the repository variable is not defined (an Actions expression yields "" otherwise)


def _binding(base: Path):
    """The second source of the workspace: the BASE ref's `.claude/foundry-project.json`. Returns (state, workspace, scope):
    state is `absent` (no file), `unreadable` (a file that does not parse), or `ok`."""
    f = base / ".claude" / "foundry-project.json"
    if not f.is_file():
        return "absent", None, set()
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
        scope = doc.get("github_scope") or {}
        x = str(scope.get("planning_repo") or "").strip()
        owner = str(scope.get("owner") or "")
    except (OSError, ValueError, AttributeError):
        return "unreadable", None, set()
    ws = x.lower() if SLUG_RE.fullmatch(x) else (f"{owner}/{x}".lower() if x and owner and re.fullmatch(r"[\w.-]+", x) else None)
    return "ok", ws, _scope_repos(base)


def _strip_quoted(text: str) -> str:
    """Drop what is an example, not a claim: HTML comments, fenced code blocks (closed only by a run of the same character at
    least as long as the opener), 4-space or tab indented code, and `>` quote lines."""
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text or "", flags=re.S)
    out, fence = [], None
    for ln in text.splitlines():
        if fence:
            m = re.fullmatch(r" {0,3}(" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",})[ \t]*", ln)
            if m:
                fence = None
            continue
        m = re.match(r" {0,3}(`{3,}|~{3,})", ln)
        if m:
            fence = m.group(1)
            continue
        if ln.startswith(("    ", "\t")) or ln.lstrip().startswith(">"):
            continue
        out.append(ln)
    return "\n".join(out)


def _built_from_values(*texts) -> list:
    """Every `Built-from:` value in the given texts (PR body, head commit message), outside fenced blocks and quotes."""
    return [m.group(1) for t in texts for m in BUILT_FROM_RE.finditer(_strip_quoted(t))]


def _workspace_gh(gh_json, args):
    """The ONE function that reaches the workspace repo over the network. Fails closed: any error is None. The workspace read token
    (FOUNDRY_WORKSPACE_READ_TOKEN) replaces GH_TOKEN for this call only; it is never printed."""
    try:
        return _default_gh_json(args, WS_TOKEN_ENV) if gh_json is _default_gh_json else gh_json(args)
    except Exception:  # noqa: BLE001
        return None


def _verify_in_workspace(gh_json, ws: str, sha: str):
    """(ok, why). The sha must exist in the workspace AND be an ancestor of (or equal to) its default branch."""
    doc = _workspace_gh(gh_json, ["api", f"repos/{ws}/commits/{sha}"])
    if not (isinstance(doc, dict) and str(doc.get("sha") or "").lower() == sha):
        return False, f"Built-from {sha} does not resolve to a commit in the workspace repo {ws}"
    repo_doc = _workspace_gh(gh_json, ["api", f"repos/{ws}"])
    branch = str((repo_doc or {}).get("default_branch") or "") if isinstance(repo_doc, dict) else ""
    if not re.fullmatch(r"[\w./-]+", branch):
        return False, f"the default branch of the workspace repo {ws} could not be read, so Built-from {sha} cannot be verified"
    cmp_doc = _workspace_gh(gh_json, ["api", f"repos/{ws}/compare/{sha}...{branch}"])
    # compare/{sha}...{default}: `ahead` = the default branch is ahead of the sha (the sha is an ancestor); `behind`/`diverged` = it is not
    if not (isinstance(cmp_doc, dict) and cmp_doc.get("status") in ("ahead", "identical")
            and cmp_doc.get("behind_by", 0) == 0):
        return False, f"Built-from {sha} is not an ancestor of {ws}@{branch}"
    return True, f"Built-from {sha} is on {ws}@{branch}"


def _built_from_finding(pr: dict, base: Path, repo, gh_json, workspace_repo=None, head_sha=None):
    """(ok, detail) for the built-from check. Armed by the FOUNDRY_WORKSPACE_REPO variable (passed as `workspace_repo`), else by the
    base ref's binding file. On the CLI a variable that is set but empty or malformed, or a binding that cannot be read, FAILS: never a skip.
    (In CI the template maps an undefined or empty variable to `unset`, the skip path, before this function sees it.)"""
    if workspace_repo is not None and workspace_repo.strip() != WS_UNSET:
        ws = workspace_repo.strip().lower()
        if not SLUG_RE.fullmatch(ws):
            return False, "FOUNDRY_WORKSPACE_REPO is set but empty or malformed: it must be OWNER/NAME of the workspace repo"
        if repo and repo.lower() == ws:
            return True, "skipped: this repository is the workspace itself, so no Built-from trailer is required"
    else:
        state, ws, scope = _binding(base)
        if state == "unreadable":
            return False, "the base ref's .claude/foundry-project.json cannot be read, so it cannot be shown that no workspace governs this repo"
        if state == "absent" or not ws or not repo or repo.lower() == ws or repo.lower() not in scope:
            return True, ("skipped: single-repo (FOUNDRY_WORKSPACE_REPO is not set and the base ref's binding does not name a workspace "
                          "that governs this repository), so no Built-from trailer is required")
    texts = [pr.get("body") or ""]
    if not head_sha:
        print("built-from: note: no --head-sha was given, so the head commit message is not read (the PR body only)", file=sys.stderr)
    if head_sha:
        if not SHA40_RE.fullmatch(head_sha):
            return False, "--head-sha is not a 40-character hex sha"
        doc = gh_json(["api", f"repos/{repo}/commits/{head_sha}"]) if repo else None
        msg = (doc.get("commit") or {}).get("message") if isinstance(doc, dict) and isinstance(doc.get("commit"), dict) else None
        if not isinstance(msg, str):
            return False, f"the head commit {head_sha} could not be read, so its message cannot be checked for a Built-from trailer"
        texts.append(msg)
    vals = _built_from_values(*texts)
    if not vals:
        return False, f"the PR carries no `Built-from: <workspace sha>` trailer (body or head commit); {repo} is governed from {ws}"
    if any(not SHA40_RE.fullmatch(v) for v in vals):
        return False, "malformed Built-from trailer: the value must be a 40-character lower-case hex sha"
    if len(set(vals)) > 1:
        return False, "the Built-from trailers disagree: " + ", ".join(sorted(set(vals)))
    return _verify_in_workspace(gh_json, ws, vals[0])


def _default_gh_json(args, token_env: str | None = None):
    """The one place `gh` is run. `token_env` names an env var whose value replaces GH_TOKEN for this call (never printed)."""
    import subprocess
    env = dict(os.environ)
    if token_env and env.get(token_env):
        env["GH_TOKEN"] = env[token_env]
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, env=env)
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


def _declared_agent_logins(base: Path) -> list:
    """`github_scope.agent_login` (a string or a list of strings) from the BASE ref's project file; [] when absent/unreadable.
    Malformed entries are kept so author_trusted fails closed on them rather than silently dropping them."""
    try:
        gs = json.loads((Path(base) / ".claude" / "foundry-project.json").read_text(encoding="utf-8")).get("github_scope") or {}
        v = gs.get("agent_login")
    except (OSError, ValueError, AttributeError):
        return []
    if isinstance(v, str):
        return [v]
    return list(v) if isinstance(v, list) else []


def _snippet_findings(base: Path, project: Path):
    """None when the BASE ref has no lock file at all (nothing to compare; the caller keeps the head's own findings). Otherwise
    the list of `snippet-tampered:<path>` / `snippet-missing:<path>` for every installed snippet whose hash in the BASE lock the PR
    head no longer matches (an edit of the file, an edit together with the PR's own lock, or a deletion). A base lock that exists but
    cannot be read (bad YAML, no PyYAML, a malformed `snippets`) is a `lock-unreadable` finding, never "nothing to compare"."""
    import hashlib
    lock_file = Path(base) / ".foundry" / "governance.lock"
    if not lock_file.is_file():
        return None
    try:
        import yaml
        lock = yaml.safe_load(lock_file.read_text(encoding="utf-8")) or {}
        snippets = lock.get("snippets") or {}
        if not isinstance(snippets, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in snippets.items()):
            raise ValueError("snippets is not a mapping of path to sha256")
        items = sorted(snippets.items())
    except Exception as exc:  # noqa: BLE001 - any failure to read the base lock is a finding
        return [f"lock-unreadable:{type(exc).__name__}"]
    out = []
    for dest, want in items:
        f = Path(project) / dest
        if f.is_symlink() or (f.exists() and not f.is_file()):
            out.append(f"snippet-tampered:{dest}")
        elif not f.exists():
            out.append(f"snippet-missing:{dest}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != want:
            out.append(f"snippet-tampered:{dest}")
    return out


def run_checks(pr: dict, ticket: dict, project: Path, plugin: Path, today: str | None, base: Path | None = None,
               gh_json=None, repo: str | None = None, files: list | None = None, agent_login: str | None = None,
               workspace_repo: str | None = None, head_sha: str | None = None) -> list:
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
    listed = n is not None and (not ref_repo or (repo and ref_repo.lower() == repo.lower())
                                or ref_repo.lower() in _scope_repos(base))
    add("ticket-named", n is not None and listed,
        ("the PR names ticket " + (ref_repo or '') + '#' + str(n)) if (n and listed) else (
            f"the PR names ticket {ref_repo}#{n}, but {ref_repo} is not this repository and is not listed in the base ref's "
            "github_scope (repos / planning_repo)" if n else "the PR body names no ticket (add `Ticket: #<n>`)"))
    raw_files = files if files is not None else (pr.get("files") or [])
    changed = [str(f.get("path") if isinstance(f, dict) else f) for f in raw_files]
    changed = [f for f in changed if f and f != "None"]
    # the ticket author must be trusted: a Design from an untrusted account is not a Design
    login = ((ticket or {}).get("author") or {}).get("login") or ""
    # the author is vetted against the PR's own repository, never against the repo a ref names (that repo is the PR author's choice)
    # gv-20: the agent accounts are the one DECLARED on the base ref (github_scope.agent_login) plus the workflow actor as an
    # addition -- never the actor alone (github.actor varies with the event that triggered the run).
    accounts = _declared_agent_logins(base) + ([agent_login] if agent_login else [])
    ok_a, why_a = ft.author_trusted(repo, login, gh_json, operators=ft.trusted_operator_logins(base),
                                    agent_login=accounts or None)
    add("ticket-author", ok_a, why_a)
    body = (ticket or {}).get("body") or ""
    gv = ft.governance_context(base)               # policy, lock, boundaries and rules from the BASE ref
    view = ft.design_view(body, (ticket or {}).get("labels") or [], gv=gv, root=base)
    design = view["design"]
    governed = [f for f in changed if ft._matches_any(gv, f, gv["design_globs"]) or ft._matches_any(gv, f, gv["sec_globs"])]
    needs_design = bool(governed) or view["ready_check_required"]
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
    ok_b, why_b = _built_from_finding(pr, base, repo, gh_json, workspace_repo, head_sha)
    add("built-from", ok_b, why_b)
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
        # the head's own snippet findings compare with the head's own lock, which a PR can edit together with the snippet;
        # the judgement is made against the BASE ref's lock hashes below
        from_base = _snippet_findings(base, project)
        if from_base is not None:           # no lock in the base: the head's own snippet findings stand
            lines = [ln for ln in lines if not ln.startswith("snippet-")] + from_base
        stale = [ln for ln in lines if ln.startswith(("rendered-stale", "lock-", "snippet-"))]
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
    p.add_argument("--base-dir", help="a checkout of the PR's BASE ref (policy, lock, boundaries, operators); required")
    p.add_argument("--files-json", help="the paged changed-file list: a JSON list of {\"path\": ...}")
    p.add_argument("--repo", help="OWNER/NAME of the repository the PR is in (the default repo of a bare #N ticket ref)")
    p.add_argument("--agent-login", help="the account the agent acts as (CI: the workflow actor). `github.token` cannot call "
                   "`gh api user`, so in CI the self-identity rule uses this login instead of a lookup")
    p.add_argument("--head-sha", help="the PR head commit sha (CI: github.event.pull_request.head.sha); its message is read for a Built-from trailer")
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
        if not a.base_dir:
            print("refused: --base-dir is required (a checkout of the PR's BASE ref); comparing the PR with its own lock and "
                  "policy would let it vouch for itself", file=sys.stderr)
            return 2
        base = Path(a.base_dir).resolve()
        findings = run_checks(pr, ticket, project, _plugin_root(a.plugin_root), a.today, base=base, repo=a.repo, files=files,
                              agent_login=a.agent_login, workspace_repo=os.environ.get("FOUNDRY_WORKSPACE_REPO"),
                              head_sha=a.head_sha)
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
