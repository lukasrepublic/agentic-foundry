#!/usr/bin/env python3
"""foundry-verify — the profile-parameterized static-validation + test executor (SDLC steps 7 & 8).

The executor the sibling stack-profile loader's recipes were frozen for. It resolves the ACTIVE
stack profile from `.foundry/stack-profile.lock` by importing the MERGED loader
(`scripts/foundry-stack-profile.py`) READ-ONLY and calling its `resolve_lock()`, then DISPATCHES the
profile-declared `static_validation` (format/lint/typecheck/build) + `test_recipe` (unit/integration/e2e)
command strings in declared order under a shell (cwd = project/repo dir, full utf-8 capture, a timeout),
reporting a verdict FAIL-CLOSED.

Dispatcher, not definer — `verify` reads the seven schema-shaped command strings BY NAME from the
resolved profile and runs exactly those; it defines no command, implements no linter/formatter/test
runner, and synthesizes nothing. `test_recipe.coverage_gate` is a NUMBER: it is SURFACED in the run
record (the declared threshold) but NEVER dispatched as a command — the test commands enforce the
threshold themselves (e.g. `pytest --cov-fail-under=N`), and `verify` catches their non-zero exit.

Fail-closed is the whole point — three terminal verdicts, no fourth:
  * `pass`  — a lock is active, `resolve_lock()` resolves EXACTLY ONE profile, AND every dispatched
              command exits zero.
  * `fail`  — a lock is active and ANY command exits non-zero (or fails to launch), OR `resolve_lock()`
              raises for a present-but-invalid lock (unpinned/sha-drifted/version-mismatch/
              core-incompatible/malformed), OR resolution yields ZERO or MORE-THAN-ONE profiles
              (selection over N is deferred). A resolution error / empty / ambiguous resolution is a
              hard FAIL, never a skip and never a pass — "nothing to run" is never green.
  * `skip`  — ONLY when NO lock is present (`read_lock()` is None): a distinct, reasoned SKIP, never
              collapsed into pass.
The process exit code mirrors the verdict fail-closed: pass -> 0, fail -> non-zero, skip -> a distinct
non-pass status (never 0 masquerading as a green run).

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). The profile (and therefore
every command `verify` runs) is operator-authored, gate-authorized machinery. `verify` is an advisory
mistake-catcher FOR the operator that runs the declared commands, NOT a defense AGAINST them; it does
not sandbox, signature-check, or allowlist (the deferred pack-trust-model, D9). The one cheap
discipline kept: profile command strings + command output are DATA, never instructions to the agent.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)
LOADER_NAME = "foundry-stack-profile.py"

# ordered, schema-shaped recipe: 4 static-validation then 3 test-recipe command-string fields.
STATIC_KEYS = ("format", "lint", "typecheck", "build")
TEST_KEYS = ("unit", "integration", "e2e")
DEFAULT_TIMEOUT = 1800  # seconds per command (sequential declared-order dispatch; richer policy deferred)


class VerifyError(Exception):
    """Fail-closed verify error (a resolution/setup failure that maps to a hard FAIL verdict)."""


def load_loader(plugin_root=None):
    """Import the MERGED stack-profile loader (`scripts/foundry-stack-profile.py`) READ-ONLY via importlib
    (the dashed-CLI module precedent). The loader's own path resolves against THIS script's dir, never cwd.
    `verify` is a dispatcher, not a definer — it never edits the loader (denied path)."""
    path = os.path.join(HERE, LOADER_NAME)
    if not os.path.isfile(path):
        raise VerifyError(f"stack-profile loader absent: {path}")
    spec = importlib.util.spec_from_file_location("foundry_stack_profile", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatch(command, *, cwd, env, timeout):
    """Run ONE profile-declared command string under a shell, cwd = the project/repo dir, full utf-8
    stdout/stderr capture, a timeout. Returns (exit_code, stdout, stderr, passed). A non-zero exit OR a
    launch failure OR a timeout => passed=False. Command output is treated as DATA (captured), never as
    instructions."""
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode("utf-8", "replace") if e.stdout else "")
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode("utf-8", "replace") if e.stderr else "")
        return 124, out, (err + f"\n[verify] command timed out after {timeout}s"), False
    except OSError as e:
        # launch failure (no shell, etc.) — a hard FAIL, never a skipped/green command.
        return 127, "", f"[verify] command failed to launch: {e}", False
    return proc.returncode, proc.stdout or "", proc.stderr or "", proc.returncode == 0


def run_verify(*, project_dir=None, plugin_root=None, root=None, timeout=DEFAULT_TIMEOUT):
    """Resolve the ACTIVE stack profile and DISPATCH its declared static_validation + test_recipe command
    set, fail-closed. Exposes the computed result as DATA:

        {
          "verdict": "pass" | "fail" | "skip",
          "reason": "<human reason>",
          "profile_id": "<id>" | None,
          "coverage_gate": <number> | None,   # surfaced, NOT dispatched
          "records": [ {phase, key, command, exit_code, passed, stdout, stderr}, ... ],
        }

    Fail-closed contract (see module docstring): no lock => skip; resolution error / zero / >1 profiles
    => fail; exactly-one profile => dispatch the seven command strings, pass iff every record passed.
    """
    pr = plugin_root or plugin_root_default(plugin_root)
    sp = load_loader(pr)
    rt = root if root is not None else pr

    # SKIP only when NO lock is present (read_lock -> None). A present-but-unreadable lock raises below.
    try:
        lock = sp.read_lock(project_dir)
    except sp.StackProfileError as e:
        return {"verdict": "fail", "reason": f"stack-profile.lock unreadable/malformed: {e}",
                "profile_id": None, "coverage_gate": None, "records": []}
    if lock is None:
        return {"verdict": "skip", "reason": "no active stack-profile.lock (no active stack)",
                "profile_id": None, "coverage_gate": None, "records": []}

    # A present lock that does not resolve is a hard FAIL (a broken build contract, never "nothing to do").
    try:
        resolved = sp.resolve_lock(project_dir, root=rt, plugin_root=pr)
    except sp.StackProfileError as e:
        return {"verdict": "fail", "reason": f"active stack-profile.lock does not resolve (fail-closed): {e}",
                "profile_id": None, "coverage_gate": None, "records": []}

    # verify the SINGLE active profile: zero => FAIL (nothing-to-run is never green); >1 => FAIL
    # (ambiguous active profile; selection over N is deferred).
    if not resolved:
        return {"verdict": "fail", "reason": "no active profile resolved (zero profiles) — nothing to run is not a pass",
                "profile_id": None, "coverage_gate": None, "records": []}
    if len(resolved) > 1:
        ids = ", ".join(str(d.get("id")) for d in resolved)
        return {"verdict": "fail",
                "reason": f"more than one resolved profile ({ids}) with no selection mechanism (deferred) — fail-closed",
                "profile_id": None, "coverage_gate": None, "records": []}

    profile = resolved[0]
    static = profile.get("static_validation") or {}
    tests = profile.get("test_recipe") or {}
    coverage_gate = tests.get("coverage_gate")  # a NUMBER — surfaced, not dispatched

    cwd = sp._project_dir(project_dir)
    env = dict(os.environ)
    records = []
    overall_pass = True

    # ordered command set read BY NAME from the resolved profile (dispatch, not redefinition):
    # 4 static_validation fields then 3 test_recipe fields.
    plan = [("static_validation", k, static.get(k)) for k in STATIC_KEYS] + \
           [("test_recipe", k, tests.get(k)) for k in TEST_KEYS]

    for phase, key, command in plan:
        if not isinstance(command, str) or not command:
            # schema-impossible on a validated profile (every field required + minLength 1); fail-closed.
            records.append({"phase": phase, "key": key, "command": command,
                            "exit_code": None, "passed": False, "stdout": "",
                            "stderr": "[verify] missing/empty command (unexpected on a validated profile)"})
            overall_pass = False
            continue
        code, out, err, passed = _dispatch(command, cwd=cwd, env=env, timeout=timeout)
        records.append({"phase": phase, "key": key, "command": command,
                        "exit_code": code, "passed": passed, "stdout": out, "stderr": err})
        overall_pass = overall_pass and passed

    verdict = "pass" if overall_pass else "fail"
    reason = ("all static_validation + test_recipe commands passed"
              if overall_pass else "one or more dispatched commands failed (fail-closed)")
    return {"verdict": verdict, "reason": reason, "profile_id": profile.get("id"),
            "coverage_gate": coverage_gate, "records": records}


def plugin_root_default(plugin_root=None):
    return plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)


def _verdict_exit(verdict):
    """Process exit code mirrors the verdict fail-closed: pass->0, fail->1, skip->a distinct non-pass
    status (3), never 0 masquerading as green."""
    return {"pass": 0, "fail": 1, "skip": 3}.get(verdict, 1)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Profile-parameterized static-validation + test executor (SDLC steps 7 & 8). "
                    "Resolves the active stack profile and dispatches its declared recipe FAIL-CLOSED.")
    ap.add_argument("--project-dir", default=None,
                    help="project/repo dir (default: CLAUDE_PROJECT_DIR or cwd)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help="per-command timeout in seconds")
    ap.add_argument("--json", action="store_true", help="emit the run record as JSON")
    args = ap.parse_args(argv)
    try:
        result = run_verify(project_dir=args.project_dir, timeout=args.timeout)
    except VerifyError as e:
        # a setup failure (e.g. the loader is absent) is a hard FAIL, never a silent pass.
        print(f"verify error (fail-closed): {e}", file=sys.stderr)
        return 1

    if args.json:
        # do NOT echo captured stdout/stderr back as instructions — render as DATA only.
        printable = dict(result)
        printable["records"] = [{k: v for k, v in r.items() if k not in ("stdout", "stderr")}
                                for r in result["records"]]
        print(json.dumps(printable, indent=2))
    else:
        verdict = result["verdict"].upper()
        print(f"foundry-verify: {verdict} — {result['reason']}")
        if result.get("profile_id"):
            print(f"  active profile: {result['profile_id']}  "
                  f"(coverage_gate={result['coverage_gate']})")
        for r in result["records"]:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['phase']}.{r['key']} (exit={r['exit_code']}): {r['command']}")
    return _verdict_exit(result["verdict"])


if __name__ == "__main__":
    sys.exit(main())
