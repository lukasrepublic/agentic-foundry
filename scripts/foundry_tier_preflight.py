#!/usr/bin/env python3
"""foundry_tier_preflight.py — apply the shipped create-shape merge-floor ruleset and answer,
from evidence alone, whether it is actually enforced (feat-foundry-tier-a-ruleset-as-code).

THE LOAD-BEARING PROPERTY: the answer is EVIDENCE, not inference. GitHub lets a private repo on
a plan without ruleset enforcement *create and store* a ruleset — the create call can return 201,
the ruleset can be listed with `enforcement: active`, and NOTHING is protected. This CLI never
treats a create response, a listed `active` status, an owner-plan read, or a repository's public
visibility as Tier A evidence. The ONLY thing it ever accepts as TIER-A evidence is the post-apply
branch-rules probe — `GET repos/{owner}/{repo}/rules/branches/{default_branch}` — the endpoint
that returns the rules actually enforced on that branch right now (it excludes `evaluate` and
`disabled` rulesets by the vendor's own documented semantics). Absent a superset match there, the
run resolves TIER-B (a supported, honestly-labeled advisory mode) with exactly one named cause, or
PREFLIGHT-ERROR when the question could not even be answered (any API failure or a hang).

  foundry_tier_preflight.py --repo <owner>/<repo> --context <name> [--context <name> ...]
                             [--apply] [--template <path>] [--timeout <seconds>]

Exit codes (from the ONE verdict->exit mapping function `resolve_verdict_line_and_exit`, so the
printed token and the exit code can never drift apart):
  0  TIER-A            — the required contexts are actively enforced on the default branch.
  10 TIER-B (<cause>)  — honestly-labeled advisory mode; not an error.
  1  PREFLIGHT-ERROR   — the question could not be answered; no tier is claimed.

Least-privilege token: a READ-ONLY token suffices for the preflight (no --apply — see --help).
Administration (write) is needed only for --apply.

Read-only unless --apply: without --apply this CLI issues only default-GET `gh api` requests —
no `-X POST`/`-X PUT`/`-X PATCH`/`-X DELETE` is ever sent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from urllib.parse import quote
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ---- pinned invocation parameters (single-literal invariants, AC-TARC-11) ------------------- #

# The REST API version header pinned to every `gh api` call this CLI issues (latest stable at the
# 2026-07-28 research pass; its breaking-change list touches neither rulesets, branch protection,
# nor required status checks — docs/research/2026-07-28-github-rulesets-as-code.md).
API_VERSION = "2026-03-10"

# Default per-call subprocess timeout in seconds. `gh` exposes no request deadline of its own, so
# the caller bounds every call itself; a run's `--timeout` overrides this constant for every call
# that run issues (not per-endpoint — one bound in effect for the whole run).
GH_TIMEOUT_SECONDS = 30

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_PATH = SCRIPT_DIR.parent / "rulesets" / "tier-a-merge-floor.json"

# The plan-limitation literal set (DECLARED POLICY, case-insensitive). A bare "upgrade" is
# forbidden as too broad — a vendor rewording degrades a run to PREFLIGHT-ERROR (loud), never to
# a false TIER-A, because the Tier A claim is never reachable through the 403 path at all.
PLAN_LIMITATION_LITERALS = (
    "make this repository public",
    "upgrade this organization",
    "upgrade your plan",
)

# Per-cause remediation lines (the Tier B cause table, closed set, spec-declared wording).
REMEDIATION_PLAN_LIMITATION = (
    "plan-limitation: server-side enforcement requires a public repository or a GitHub Pro, "
    "Team, or Enterprise plan."
)
REMEDIATION_ENABLE_ENFORCEMENT = (
    "enable enforcement in settings: set this ruleset's enforcement to Active."
)
REMEDIATION_CONTEXT_MISSING = (
    "check the context name: the enforced and required context sets are printed for comparison."
)
REMEDIATION_PULL_REQUEST_MISSING = (
    "the ruleset enforces the required status checks but carries no enforced pull_request rule — "
    "add one: status checks gate a pull request, they do not gate a direct push."
)
REMEDIATION_NO_RULESET = (
    "no ruleset carrying the template's name exists — re-run with --apply."
)

REMEDIATION_CLASSIC_PROTECTION = (
    "no ruleset exists, BUT the default branch is protected by CLASSIC branch protection, which the "
    "branch-rules probe cannot see. This is NOT an unprotected branch — the verdict is 'unverifiable "
    "from a read-only token', not 'advisory'. Reading which contexts classic protection requires "
    "needs the admin-only /branches/{branch}/protection endpoint, and this tool is deliberately "
    "read-only. Either re-run with --apply to migrate to a ruleset (recommended — rulesets are "
    "GitHub's forward path and are introspectable), or confirm the required contexts by hand."
)

# The verdict vocabulary (closed set) and its exit codes.
_VERDICT_EXIT = {"TIER-A": 0, "TIER-B": 10, "PREFLIGHT-ERROR": 1}
_CAUSE_TOKEN_RE = re.compile(r"^[a-z-]+$")
_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")


# ------------------------------- the one verdict/exit mapping -------------------------------- #

def resolve_verdict_line_and_exit(verdict: str, cause: str | None = None) -> tuple[str, int]:
    """THE single module-level function mapping a resolved verdict (+ Tier B cause) to its
    (verdict-line prefix, process exit code) pair (AC-TARC-10). Every code path in this module
    routes its verdict through this function, so the printed token and the exit code can never
    disagree; a test may import this module and call it directly over the whole vocabulary."""
    if verdict not in _VERDICT_EXIT:
        raise ValueError(f"unrecognized verdict: {verdict!r}")
    if verdict == "TIER-B":
        if not cause or not _CAUSE_TOKEN_RE.match(cause):
            raise ValueError(f"TIER-B requires a lowercase-hyphen cause token, got {cause!r}")
        return f"TIER-B ({cause})", _VERDICT_EXIT[verdict]
    return verdict, _VERDICT_EXIT[verdict]


# ------------------------------------ gh subprocess layer ------------------------------------ #

@dataclass
class GhResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    not_resolvable: bool = False
    timeout: float = 0.0


def run_gh(path: str, *, method: str | None = None, paginate: bool = False,
           input_path: str | None = None, timeout: float) -> GhResult:
    """Issue exactly one `gh api` call, bounded by `timeout`, carrying the pinned API-version
    header. `method` is only ever "POST" here (the apply path); every other call is the tool's
    default GET — no `-X` at all, which is what makes the read-only criterion observable from the
    invocation journal rather than from this code."""
    args = ["gh", "api"]
    if paginate:
        args.append("--paginate")
    if method:
        args += ["-X", method]
    args += ["-H", f"X-GitHub-Api-Version: {API_VERSION}"]
    if input_path:
        args += ["--input", input_path]
    args.append(path)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return GhResult(timed_out=True, timeout=timeout)
    except FileNotFoundError:
        return GhResult(not_resolvable=True)
    return GhResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def extract_status(stderr: str) -> int | None:
    m = _HTTP_STATUS_RE.search(stderr or "")
    return int(m.group(1)) if m else None


def classify_generic_failure(res: GhResult, label: str) -> tuple[str, str] | None:
    """Fail-closed classification shared by every call site (AC-TARC-7): anything that is not a
    clean exit-0 response becomes a (label, detail) pair a caller turns into PREFLIGHT-ERROR.
    Returns None when the call succeeded (still needs its body parsed by the caller)."""
    if res.not_resolvable:
        return (label, "gh executable not found on PATH")
    if res.timed_out:
        return (label, f"timed out after {res.timeout}s (no response within the declared bound)")
    if res.exit_code != 0:
        status = extract_status(res.stderr)
        status_txt = f"HTTP {status}" if status else "an unclassified error"
        body_line = next((ln for ln in (res.stderr or res.stdout or "").splitlines() if ln.strip()), "")
        return (label, f"{status_txt} — {body_line}".strip(" —"))
    return None


# ------------------------------------- pure decision logic ------------------------------------ #

def expand_template(template: dict, contexts: list[str]) -> dict:
    """Replace the template's single placeholder `required_status_checks` entry with one entry
    per supplied --context value, in the supplied order (AC-TARC-3)."""
    body = json.loads(json.dumps(template))  # deep copy, preserves shape exactly
    touched = False
    for rule in body.get("rules", []):
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            params = rule.setdefault("parameters", {})
            params["required_status_checks"] = [{"context": c} for c in contexts]
            touched = True
    if not touched:
        raise ValueError("template carries no required_status_checks rule to expand")
    return body


def find_entry(rulesets: list, name: str) -> dict | None:
    for item in rulesets or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def extract_contexts(probe_rules: list) -> set[str]:
    """Byte-exact context set carried by every `required_status_checks` rule in the probe
    response (DECLARED POLICY: no case-folding, no trimming, no normalization of any kind)."""
    ctx: set[str] = set()
    for rule in probe_rules or []:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            params = rule.get("parameters") or {}
            for entry in params.get("required_status_checks") or []:
                if isinstance(entry, dict) and "context" in entry:
                    ctx.add(entry["context"])
    return ctx


def probe_has_rsc_rule(probe_rules: list) -> bool:
    return any(isinstance(r, dict) and r.get("type") == "required_status_checks" for r in probe_rules or [])


def resolve_cause(create_plan_gated: bool, found_entry: dict | None, has_rsc_rule: bool,
                  contexts_satisfied: bool = False, has_pr_rule: bool = True,
                  classic_protected: bool = False) -> tuple[str, str]:
    """The Tier B cause table (closed set), evaluated in DECLARED PRECEDENCE ORDER — first match
    wins, a strict partition of every input that reaches Tier B (AC-TARC-6).

    `classic_protected` splits the terminal `no-ruleset` case in two. Before it, a branch protected
    the CLASSIC way reported `no-ruleset` — indistinguishable from a branch with no protection at
    all, because the branch-rules endpoint reports rulesets only and returns `[]` for classic
    protection. That was a FALSE NEGATIVE about the merge floor, and this tool is what decides
    whether the gates get called load-bearing: it told an adopter with a genuinely enforcing Tier-A
    floor that their gates were advisory. Observed on this repo 2026-08-04, where `main` was
    `protected=true` with six required contexts while the probe returned an empty rule list.

    The new cause is still TIER-B, deliberately. Read-only, we can learn THAT the branch is
    protected but not WHICH contexts it requires, and claiming TIER-A on the strength of "something
    is protecting this branch" would be exactly the overclaim the tool exists to prevent."""
    if create_plan_gated:
        return "plan-gated", REMEDIATION_PLAN_LIMITATION
    if found_entry is not None:
        enforcement = found_entry.get("enforcement")
        if enforcement == "evaluate":
            return "evaluate", REMEDIATION_ENABLE_ENFORCEMENT
        if enforcement == "disabled":
            return "disabled", REMEDIATION_ENABLE_ENFORCEMENT
        if enforcement == "active":
            return "created-not-enforced", REMEDIATION_PLAN_LIMITATION
    if has_rsc_rule and not contexts_satisfied:
        return "context-missing", REMEDIATION_CONTEXT_MISSING
    if contexts_satisfied and not has_pr_rule:
        return "pull-request-missing", REMEDIATION_PULL_REQUEST_MISSING
    if has_rsc_rule:
        return "context-missing", REMEDIATION_CONTEXT_MISSING
    if classic_protected:
        return "classic-protection", REMEDIATION_CLASSIC_PROTECTION
    return "no-ruleset", REMEDIATION_NO_RULESET


# ------------------------------------------- output --------------------------------------------#

def finish(lines: list[str], verdict: str, cause: str | None, message: str) -> int:
    """Print every collected line, then the ONE final verdict line (AC-TARC-10) — nothing is
    written after it, to stdout or stderr (gh's own stderr is captured into a variable above,
    never re-emitted)."""
    prefix, code = resolve_verdict_line_and_exit(verdict, cause)
    for line in lines:
        print(line)
    print(f"{prefix}: {message}")
    return code


def fail_closed(lines: list[str], label: str, detail: str) -> int:
    """Every PREFLIGHT-ERROR path routes through here: the line immediately BEFORE the final
    verdict line names the failing call and the observed status/error/timeout (AC-TARC-7)."""
    lines.append(f"{label}: {detail}")
    return finish(lines, "PREFLIGHT-ERROR", None,
                  "the preflight could not resolve a verdict — see the line above.")


def _write_temp_json(obj: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json", prefix="foundry-tier-ruleset-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


# --------------------------------------------- run ---------------------------------------------#

def run(args: argparse.Namespace) -> int:
    lines: list[str] = []
    timeout = args.timeout if args.timeout is not None else GH_TIMEOUT_SECONDS
    template_path = args.template or str(DEFAULT_TEMPLATE_PATH)

    if shutil.which("gh") is None:
        lines.append("gh: executable not found on PATH — install GitHub CLI (gh) and retry.")
        return finish(lines, "PREFLIGHT-ERROR", None, "gh is not resolvable on PATH.")

    try:
        with open(template_path, encoding="utf-8") as fh:
            template = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return fail_closed(lines, f"template ({template_path})", f"could not be read/parsed — {exc}")
    template_name = template.get("name")

    create_plan_gated = False

    if args.apply:
        existence_label = f"repos/{args.repo}/rulesets (existence check)"
        eres = run_gh(f"repos/{args.repo}/rulesets", paginate=True, timeout=timeout)
        fail = classify_generic_failure(eres, existence_label)
        if fail:
            return fail_closed(lines, *fail)
        try:
            existing = json.loads(eres.stdout)
        except json.JSONDecodeError as exc:
            return fail_closed(lines, existence_label, f"response body did not parse as JSON ({exc})")
        existing_entry = find_entry(existing, template_name)
        if existing_entry is not None:
            lines.append(f"apply: skipped (ruleset already present) — {template_name!r} already exists on {args.repo}.")
        else:
            expanded = expand_template(template, args.context)
            tf_path = _write_temp_json(expanded)
            create_label = f"repos/{args.repo}/rulesets (create)"
            try:
                cres = run_gh(f"repos/{args.repo}/rulesets", method="POST", input_path=tf_path, timeout=timeout)
            finally:
                os.unlink(tf_path)
            if cres.not_resolvable or cres.timed_out:
                fail = classify_generic_failure(cres, create_label)
                return fail_closed(lines, *fail)
            if cres.exit_code != 0:
                status = extract_status(cres.stderr)
                if status == 403:
                    text = f"{cres.stdout}\n{cres.stderr}".lower()
                    if any(lit in text for lit in PLAN_LIMITATION_LITERALS):
                        create_plan_gated = True
                        lines.append("apply: create returned HTTP 403 (plan-limitation wording "
                                      "matched) — continuing to the branch-rules probe.")
                    else:
                        lines.append(
                            f"{create_label}: HTTP 403 — grant the fine-grained Administration "
                            "(write) repository permission (or a classic token's `repo` scope "
                            "plus repository-admin role) and retry --apply."
                        )
                        return finish(lines, "PREFLIGHT-ERROR", None,
                                      "the create call was refused and its body did not match the "
                                      "plan-limitation wording.")
                else:
                    fail = classify_generic_failure(cres, create_label)
                    return fail_closed(lines, *fail)
            else:
                lines.append(f"apply: created ruleset {template_name!r} on {args.repo}.")

    repo_label = f"repos/{args.repo}"
    rres = run_gh(repo_label, timeout=timeout)
    fail = classify_generic_failure(rres, repo_label)
    if fail:
        return fail_closed(lines, *fail)
    try:
        repo_obj = json.loads(rres.stdout)
    except json.JSONDecodeError as exc:
        return fail_closed(lines, repo_label, f"response body did not parse as JSON ({exc})")
    default_branch = repo_obj.get("default_branch")
    if not default_branch:
        return fail_closed(lines, repo_label, "response carried no default_branch")

    # Security review (PR #282): default_branch is server-supplied and a legal git ref may
    # contain characters (notably `#`) that would truncate or retarget the URL — encode it,
    # so the probe can only ever read the branch we resolved.
    probe_label = f"repos/{args.repo}/rules/branches/{quote(default_branch, safe='')}"
    pres = run_gh(probe_label, timeout=timeout)
    fail = classify_generic_failure(pres, probe_label)
    if fail:
        return fail_closed(lines, *fail)
    try:
        probe_rules = json.loads(pres.stdout)
    except json.JSONDecodeError as exc:
        return fail_closed(lines, probe_label, f"response body did not parse as JSON ({exc})")
    if not isinstance(probe_rules, list):
        return fail_closed(lines, probe_label, "response was not a JSON array of rules")

    enforced = extract_contexts(probe_rules)
    required = set(args.context)
    lines.append(f"probe: {args.repo}@{default_branch} — required={sorted(required)} enforced={sorted(enforced)}")

    # Security review (PR #282): the shipped template also carries pull_request,
    # deletion, and non_fast_forward rules. Confirming only status-check contexts would
    # let TIER-A be claimed for a branch whose PR requirement is absent, so require the
    # pull_request rule to be present in the ENFORCED set too.
    rule_types = {r.get("type") for r in probe_rules if isinstance(r, dict)}
    if required and required <= enforced and "pull_request" in rule_types:
        return finish(lines, "TIER-A", None,
                      f"required status checks and the pull-request rule are actively "
                      f"enforced on {args.repo}@{default_branch}.")
    if required and required <= enforced:
        lines.append("probe: status-check contexts enforced, but no enforced pull_request rule — "
                     "not TIER-A (a merge could bypass review entirely).")

    cause_label = f"repos/{args.repo}/rulesets (cause read)"
    cres2 = run_gh(f"repos/{args.repo}/rulesets", paginate=True, timeout=timeout)
    fail = classify_generic_failure(cres2, cause_label)
    if fail:
        return fail_closed(lines, *fail)
    try:
        rulesets_list = json.loads(cres2.stdout)
    except json.JSONDecodeError as exc:
        return fail_closed(lines, cause_label, f"response body did not parse as JSON ({exc})")
    found_entry = find_entry(rulesets_list, template_name)
    has_rsc_rule = probe_has_rsc_rule(probe_rules)

    # Only asked when it can change the answer — i.e. when we are otherwise about to report
    # `no-ruleset`. `/branches/{branch}` is READABLE WITH A READ-ONLY TOKEN (unlike
    # `/branches/{branch}/protection`, which is admin-only), so this preserves the tool's
    # least-privilege promise. A failure here is not fatal: it degrades to the previous
    # `no-ruleset` answer rather than turning a Tier-B report into a PREFLIGHT-ERROR.
    classic_protected = False
    if found_entry is None and not has_rsc_rule and not create_plan_gated:
        blabel = f"repos/{args.repo}/branches/{quote(default_branch, safe='')}"
        bres = run_gh(blabel, timeout=timeout)
        if classify_generic_failure(bres, blabel) is None:
            try:
                classic_protected = bool(json.loads(bres.stdout).get("protected"))
            except (json.JSONDecodeError, AttributeError):
                classic_protected = False
        if classic_protected:
            lines.append(f"note: {default_branch} IS protected by classic branch protection "
                         f"(the branch-rules probe reports rulesets only, so it read empty)")

    cause, remediation = resolve_cause(create_plan_gated, found_entry, has_rsc_rule,
                                       contexts_satisfied=bool(required and required <= enforced),
                                       has_pr_rule=("pull_request" in rule_types),
                                       classic_protected=classic_protected)
    lines.append(remediation)
    if cause == "context-missing":
        lines.append(f"required contexts: {sorted(required)}")
        lines.append(f"enforced contexts: {sorted(enforced)}")
    lines.append("the merge gate continues to run advisory-only in the meantime.")
    stored = "stored but " if found_entry is not None else ""
    if cause == "classic-protection":
        # "ruleset NOT enforced" is true but reads as "nothing is enforced", which is the exact
        # misreading this cause exists to prevent.
        detail = (f"no ruleset on {args.repo}@{default_branch}; the branch IS protected by classic "
                  f"branch protection, whose required contexts a read-only token cannot enumerate.")
    else:
        detail = f"ruleset {stored}NOT enforced on {args.repo}@{default_branch}."
    return finish(lines, "TIER-B", cause, detail)


# ------------------------------------------- CLI glue -------------------------------------------#

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="foundry_tier_preflight.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Applies the shipped create-shape merge-floor ruleset template and answers, from the "
            "post-apply branch-rules probe alone, whether the supplied gate contexts are actively "
            "enforced on the repository's default branch (TIER-A), stored-but-not-enforced "
            "(TIER-B, with a named cause), or unanswerable (PREFLIGHT-ERROR)."
        ),
        epilog=(
            "Least-privilege token: a READ-ONLY token suffices for the preflight (default, no "
            "--apply) — it only reads. The fine-grained Administration (write) repository "
            "permission (or a classic token's repo scope plus repository-admin role) is required "
            "ONLY for --apply."
        ),
    )
    ap.add_argument("--repo", required=True, metavar="OWNER/REPO", help="target repository, e.g. acme/widgets")
    ap.add_argument("--context", action="append", default=[], required=True, metavar="NAME",
                     help="a required gate check context; repeat for more than one")
    ap.add_argument("--apply", action="store_true",
                     help="apply the shipped template first (needs Administration (write)); default is read-only")
    ap.add_argument("--template", default=None, metavar="PATH",
                     help=f"path to the ruleset template (default: {DEFAULT_TEMPLATE_PATH})")
    ap.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                     help=f"per-call timeout in seconds, applied to every gh call this run issues (default: {GH_TIMEOUT_SECONDS})")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    # Security review (PR #282): OWNER/REPO reaches an `-X POST` path and every read path.
    # An unvalidated value can carry `..` segments that retarget the call, so validate the
    # shape before any network call and fail closed on anything else.
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", args.repo or ""):
        print("PREFLIGHT-ERROR")
        print(f"reason: --repo {args.repo!r} is not a bare OWNER/REPO (refusing before any API call).")
        return 1
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
