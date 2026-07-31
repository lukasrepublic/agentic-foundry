#!/usr/bin/env python3
"""foundry-release-acceptance — pre-cut acceptance gate over the release artifact
(feat-foundry-release-acceptance-gate).

Validates a candidate plugin TREE the way a fresh client receives it, using the tool's OWN first-party
validators (NOT a bespoke re-implementation — that was the v1 category error):
  AC-RELACC-1  `claude plugin validate <tree> --strict` + `claude plugin tag <tree> --dry-run`  (manifest +
               marketplace + plugin<->marketplace version agreement + strict skill-frontmatter parsing)
  AC-RELACC-2  every hooks.json command script is present + EXECUTABLE (os.X_OK) — NEVER executes the hooks
               (they are fail-CLOSED, side-effecting gates; asserting an exit code is unsound + unsafe)
  AC-RELACC-3  the candidate tree's OWN scripts/foundry-doctor.py is DOCTOR-GREEN (read by TOKEN, not exit
               code; never --session-start) over a minimal synthetic CLAUDE_PROJECT_DIR

The verdict is DATA, bound to the artifact's resolved HEAD sha (so a caller cannot surface "passed" for a
different artifact than the one validated). Trusted-operator release mistake-catcher; a RELEASE (pre-cut) gate,
additive to the per-atom merge gate. It NEVER mutates and never executes hook scripts.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def _tree_sha(tree):
    r = subprocess.run(["git", "-C", tree, "rev-parse", "HEAD"], capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout.strip()
    h = hashlib.sha256()
    for root, _dirs, files in os.walk(tree):
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, tree).encode())
            try:
                with open(p, "rb") as fh:
                    h.update(fh.read())
            except OSError:
                pass
    return "sha256:" + h.hexdigest()


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _check_validators(tree):
    """AC-RELACC-1: the first-party validators, fail-closed. A missing `claude` CLI is a hard fail
    (the gate cannot validate the artifact without the tool's own validators), never a silent pass."""
    if shutil.which("claude") is None:
        return [{"name": "first-party-validators", "ok": False,
                 "detail": "`claude` CLI not on PATH — cannot run validate/tag (fail-closed)"}]
    out = []
    for label, cmd in (("claude plugin validate --strict", ["claude", "plugin", "validate", tree, "--strict"]),
                       ("claude plugin tag --dry-run", ["claude", "plugin", "tag", tree, "--dry-run"])):
        r = _run(cmd)
        out.append({"name": label, "ok": r.returncode == 0,
                    "detail": (r.stdout + r.stderr).strip()[-1500:]})
    return out


def _hook_script_paths(tree):
    """Resolve every hooks.json `command` to its repo-relative script path: strip surrounding quotes +
    the ${CLAUDE_PLUGIN_ROOT}/ prefix, then split off the trailing argv to isolate the script file."""
    hj = os.path.join(tree, "hooks", "hooks.json")
    if not os.path.isfile(hj):
        return []
    with open(hj, encoding="utf-8") as f:
        data = json.load(f)
    cmds = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "command" and isinstance(v, str):
                    cmds.add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    scripts = set()
    for c in cmds:
        first = c.strip().split()[0]            # split off trailing argv
        first = first.strip('"').strip("'")     # strip surrounding quotes
        first = re.sub(r'^"?\$\{CLAUDE_PLUGIN_ROOT\}"?/', "", first)  # strip the var prefix
        scripts.add(first)
    return sorted(scripts)


def _check_hooks_executable(tree):
    """AC-RELACC-2: every declared hook script present + executable. NEVER executes them."""
    checks = []
    for rel in _hook_script_paths(tree):
        p = os.path.join(tree, rel)
        present = os.path.isfile(p)
        execable = present and os.access(p, os.X_OK)
        checks.append({"name": f"hook executable: {rel}", "ok": execable,
                       "detail": "ok" if execable else ("MISSING" if not present else "NOT EXECUTABLE (mode lacks +x)")})
    if not checks:
        checks.append({"name": "hooks.json", "ok": True, "detail": "no hooks declared"})
    return checks


def _check_doctor(tree):
    """AC-RELACC-3: the CANDIDATE tree's own doctor is DOCTOR-GREEN (token-read, not --session-start)."""
    doctor = os.path.join(tree, "scripts", "foundry-doctor.py")
    if not os.path.isfile(doctor):
        return [{"name": "doctor", "ok": False, "detail": "scripts/foundry-doctor.py absent in candidate tree"}]
    with tempfile.TemporaryDirectory() as proj:
        # Seed exactly the project-side state the doctor's PLUGIN-INTRINSIC checks need (bounded):
        #  (1) a well-shaped operator registry (operators is a DICT keyed by id) so identity resolves.
        os.makedirs(os.path.join(proj, ".claude"), exist_ok=True)
        with open(os.path.join(proj, ".claude", "foundry-operators.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1,
                       "operators": {"op_release_gate": {"name": "release gate (synthetic)"}}}, f)
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=tree, CLAUDE_PROJECT_DIR=proj)
        r = _run(["python3", doctor], env=env)
    out = r.stdout + r.stderr
    green = ("DOCTOR-GREEN" in out) and ("DOCTOR-RED" not in out)
    reds = [ln.strip() for ln in out.splitlines() if "[XX" in ln]
    return [{"name": "foundry-doctor (candidate tree)", "ok": green,
             "detail": "DOCTOR-GREEN" if green else ("DOCTOR-RED: " + "; ".join(reds))[:1500]}]


def run_acceptance(*, tree, project_dir=None):
    """Validate the candidate plugin TREE; return a verdict DATA dict bound to the artifact sha.
    project_dir is accepted for signature symmetry; the doctor run uses its own synthetic project dir."""
    tree = os.path.abspath(tree)
    checks = _check_validators(tree) + _check_hooks_executable(tree) + _check_doctor(tree)
    failures = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    return {
        "verdict": "pass" if not failures else "fail",
        "artifact_sha": _tree_sha(tree),
        "tree": tree,
        "checks": checks,
        "failures": failures,
    }


def main():
    ap = argparse.ArgumentParser(description="Foundry release acceptance-gate over a candidate plugin tree.")
    ap.add_argument("--tree", required=True, help="path to the candidate plugin tree (a committed checkout)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = run_acceptance(tree=args.tree)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"release-acceptance: {res['verdict'].upper()}  artifact={res['artifact_sha'][:16]}")
        for c in res["checks"]:
            print(f"  [{'ok ' if c['ok'] else 'XX '}] {c['name']}: {c['detail'][:200]}")
    sys.exit(0 if res["verdict"] == "pass" else 2)


if __name__ == "__main__":
    main()
