#!/usr/bin/env python3
"""foundry-doctor — thin health probe (the v0.25.0 test-suite realignment).

This is a thin probe, not a drop-in-check registry. One real pytest suite (`tests/`) carries
the load-bearing behavioral assertions — `scripts/foundry_checks/` and its drop-in
discovery machinery do not ship.

What this probe checks, every run, cheaply:
  1. The plugin manifest (`.claude-plugin/plugin.json`) loads as JSON and carries a `version`.
  2. `hooks/hooks.json` parses as JSON and every referenced hook command script exists on disk.
  3. Every shipped `skills/*/SKILL.md` frontmatter YAML-parses (a defect class that is cheap to
     catch here and expensive to discover live — a colon-space in a plain scalar broke YAML and
     reached a release candidate before this class of check existed).
  4. `.foundry/stack-profile.lock` (if present) resolves against the shipped `packs/` tree.
  5. The operator registry (`.claude/foundry-operators.json`) resolves.
  6. Control-plane preflight (feat-foundry-control-plane-preflight, AC-CPP-1/-2/-3/-3b): no
     dangling `repos{}` path in THIS project's own manifest, and no ancestor
     `.claude/foundry-project.json` already names (or merely governs) this project directory as a
     hosted repo. A MISTAKE-CATCHER for the operator, not a floor — see
     `scripts/foundry_control_plane.py`'s module docstring. `--session-start` still fails open
     (AC-CPP-7); the operator-invoked exit code is this check's only enforcement.
  7. Permission-floor drift (feat-foundry-doctor-permission-floor-check, AC-DPF-1..8): compares
     the workspace's EFFECTIVE permission configuration (`.claude/settings.json` AND
     `.claude/settings.local.json`, unioned) against the shipped `docs/permission-floor.json`.
     ADVISORY-only — a mismatch never reddens the run — because the ask-to-allow harness persist
     option writes into `.claude/settings.local.json` with no second trust dialog, and this is the
     one probe that watches that file. See `scripts/foundry_permission_floor.py`'s module
     docstring. RED only on a malformed `docs/permission-floor.json`.

Fails CLOSED for the operator-invoked check (exit non-zero on any hard failure). The
--session-start cadence is ADVISORY (exits 0 so it never wedges a session) — the real merge-side
enforcement is the native floor (`ci.yml` + `btb-gates`, Tier B advisory) plus
`hooks/foundry-git-discipline.sh`'s deterministic `gh` clause. --heal stays the Phase-2b no-op
(the SessionStart hook still calls it unconditionally; the wiring auto-heal machinery it used to
back is retired).

  foundry-doctor.py                  # operator check, fail-closed
  foundry-doctor.py --session-start  # advisory cadence, fail-open
  foundry-doctor.py --heal           # no-op (retired machinery), always exits 0
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)

# AC-DPF-1: a fourth, non-failing outcome distinct from True/False/None. The shared renderer below
# reads `ok is None` as `skip`, so reusing `None` would silently collapse an advisory into a skip;
# `object()` guarantees `ADVISORY is not True and ADVISORY is not False and ADVISORY is not None`.
ADVISORY = object()


def _project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


# --------------------------------------------------------------------------------------- #
# 1. plugin manifest
# --------------------------------------------------------------------------------------- #
def check_manifest(plugin_root=None):
    root = plugin_root or PLUGIN_ROOT
    path = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.isfile(path):
        return False, f"plugin manifest absent: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        return False, f"plugin manifest invalid JSON: {e}"
    version = doc.get("version")
    if not version:
        return False, "plugin manifest carries no version"
    return True, f"plugin manifest loads (version {version})"


# --------------------------------------------------------------------------------------- #
# 2. hooks.json parses + every referenced hook command script exists
# --------------------------------------------------------------------------------------- #
def _hook_command_scripts(data, plugin_root):
    """Walk the hooks.json doc and resolve every `command` field to a repo-relative script
    path, expanding ${CLAUDE_PLUGIN_ROOT}/$CLAUDE_PLUGIN_ROOT."""
    scripts = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "command" and isinstance(v, str):
                    first = v.strip().split()[0] if v.strip() else ""
                    first = first.replace('"', "").replace("'", "")
                    first = first.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
                    first = first.replace("$CLAUDE_PLUGIN_ROOT", plugin_root)
                    if first:
                        scripts.add(first)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    return sorted(scripts)


def check_hooks(plugin_root=None):
    root = plugin_root or PLUGIN_ROOT
    path = os.path.join(root, "hooks", "hooks.json")
    if not os.path.isfile(path):
        return False, f"hooks.json absent: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"hooks.json invalid JSON: {e}"
    missing = [s for s in _hook_command_scripts(data, root) if not os.path.isfile(s)]
    if missing:
        return False, f"hooks.json references missing script(s): {missing}"
    return True, "hooks.json parses; every referenced hook command script exists"


# --------------------------------------------------------------------------------------- #
# 3. every skills/*/SKILL.md frontmatter YAML-parses
# --------------------------------------------------------------------------------------- #
def _skill_paths(plugin_root):
    d = os.path.join(plugin_root, "skills")
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, name, "SKILL.md")
        for name in os.listdir(d)
        if os.path.isfile(os.path.join(d, name, "SKILL.md"))
    )


def check_skills_frontmatter(plugin_root=None):
    root = plugin_root or PLUGIN_ROOT
    paths = _skill_paths(root)
    if not paths:
        return None, f"no skills/*/SKILL.md under {root} (not applicable)"
    broken = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            broken.append(f"{p}: unreadable ({e})")
            continue
        if not text.startswith("---"):
            broken.append(f"{p}: no `---` frontmatter fence at line 1")
            continue
        end = text.find("\n---", 3)
        if end == -1:
            broken.append(f"{p}: unterminated frontmatter fence")
            continue
        fm_text = text[3:end]
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError as e:
            broken.append(f"{p}: frontmatter YAML parse error: {e}")
            continue
        if not isinstance(fm, dict) or "name" not in fm or "description" not in fm:
            broken.append(f"{p}: frontmatter missing name/description")
    if broken:
        return False, f"{len(broken)} skill(s) with broken frontmatter: {broken[:5]}"
    return True, f"{len(paths)} skill(s) frontmatter YAML-parses cleanly"


# --------------------------------------------------------------------------------------- #
# 4. stack-profile lock (if present) resolves
# --------------------------------------------------------------------------------------- #
def _load_stack_profile_module(plugin_root):
    path = os.path.join(plugin_root, "scripts", "foundry-stack-profile.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("foundry_stack_profile_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_stack_profile_lock(plugin_root=None, project_dir=None):
    root = plugin_root or PLUGIN_ROOT
    try:
        sp = _load_stack_profile_module(root)
    except Exception as e:
        return False, f"stack-profile loader unimportable: {e}"
    if sp is None:
        return None, "stack-profile loader absent (not applicable)"
    lpath = sp.lock_path(project_dir)
    if not os.path.isfile(lpath):
        return True, "no active stack-profile.lock (not applicable)"
    try:
        resolved = sp.resolve_lock(project_dir, root=root, plugin_root=root)
    except sp.StackProfileError as e:
        return False, (f"active stack-profile.lock does not resolve: {e} — run "
                       "`/foundry:relock` if this is a trusted profile-version advance")
    return True, f"stack-profile lock resolves ({len(resolved)} profile(s) pinned)"


# --------------------------------------------------------------------------------------- #
# 5. operator registry resolvable
# --------------------------------------------------------------------------------------- #
def check_operator_registry(project_dir=None):
    scripts_dir = os.path.join(PLUGIN_ROOT, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import foundry_authz as az
        ops = az.load_operators(project_dir)
        return True, f"operator registry resolves ({len(ops)} operator(s))"
    except Exception as e:
        return False, f"operator registry fail-closed: {e}"


# --------------------------------------------------------------------------------------- #
# 6. control-plane preflight (feat-foundry-control-plane-preflight, AC-CPP-1/-2/-3/-3b)
# --------------------------------------------------------------------------------------- #
def _load_control_plane_module(plugin_root):
    path = os.path.join(plugin_root, "scripts", "foundry_control_plane.py")
    if not os.path.isfile(path):
        return None
    scripts_dir = os.path.dirname(path)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import foundry_control_plane as cp  # lazy import, mirrors check_operator_registry above
    return cp


def check_control_plane(plugin_root=None, project_dir=None):
    root = plugin_root or PLUGIN_ROOT
    pdir = project_dir or _project_dir()
    try:
        cp = _load_control_plane_module(root)
    except Exception as e:
        return False, f"control-plane preflight module unimportable: {e}"
    if cp is None:
        return None, "control-plane preflight module absent (not applicable)"

    findings = []

    # AC-CPP-1 — a dangling repos{} path in THIS project's own manifest.
    for key, resolved in sorted(cp.dangling_repo_paths(pdir).items()):
        findings.append(cp.format_dangling(key, resolved))

    # AC-CPP-2 / AC-CPP-3 / AC-CPP-3b — an ancestor manifest already governs this project dir.
    ancestor_finding = cp.find_ancestor_control_plane(pdir)
    if ancestor_finding is not None:
        findings.append(cp.format_ancestor_finding(ancestor_finding, pdir))

    if findings:
        return False, "; ".join(findings)
    return True, "control-plane: no dangling repos{} path; session is correctly rooted"


# --------------------------------------------------------------------------------------- #
# 7. permission-floor drift (feat-foundry-doctor-permission-floor-check, AC-DPF-1..8)
# --------------------------------------------------------------------------------------- #
def _load_permission_floor_module(plugin_root):
    path = os.path.join(plugin_root, "scripts", "foundry_permission_floor.py")
    if not os.path.isfile(path):
        return None
    scripts_dir = os.path.dirname(path)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import foundry_permission_floor as pf  # lazy import, mirrors check_control_plane above
    return pf


def check_permission_floor(plugin_root=None, project_dir=None, session_start=False):
    root = plugin_root or PLUGIN_ROOT
    pdir = project_dir or _project_dir()
    try:
        pf = _load_permission_floor_module(root)
    except Exception as e:
        return False, f"permission-floor module unimportable: {e}"
    if pf is None:
        return None, "permission-floor module absent (not applicable)"
    try:
        result = pf.run_check(root, pdir, for_session_start=session_start)
    except pf.FloorMalformed as e:
        return False, f"permission-floor.json malformed: {e}"
    detail = result["summary"]
    if result["lines"]:
        detail = detail + "; " + "; ".join(result["lines"])
    if result["outcome"] == "skip":
        return None, detail
    if result["outcome"] == "ok":
        return True, detail
    return ADVISORY, detail


# --------------------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="foundry-doctor — thin health probe")
    ap.add_argument("--session-start", action="store_true",
                    help="advisory cadence: fail-open, never wedges a session")
    ap.add_argument("--heal", action="store_true",
                    help="no-op (the wiring auto-heal this backed no longer ships)")
    ap.add_argument("--repo", default=None, help="accepted for back-compat; unused")
    args = ap.parse_args()

    if args.heal:
        print("foundry doctor --heal: no-op (wiring auto-heal retired in v0.24.0)")
        sys.exit(0)

    project_dir = _project_dir()

    def _run(name, fn, *a, **kw):
        # Each probe is individually crash-proof: an unexpected exception is a RED result,
        # never a traceback — the --session-start fail-open guarantee below must be reachable
        # regardless of any probe's internals (PR-#270 review finding 4).
        try:
            ok, detail = fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 — deliberate: a probe crash is itself the finding
            ok, detail = False, f"probe crashed: {type(e).__name__}: {e}"
        return (name, ok, detail)

    checks = [
        _run("manifest", check_manifest),
        _run("hooks", check_hooks),
        _run("skills-frontmatter", check_skills_frontmatter),
        _run("stack-profile-lock", check_stack_profile_lock, project_dir=project_dir),
        _run("operator-registry", check_operator_registry, project_dir),
        _run("control-plane", check_control_plane, project_dir=project_dir),
        _run("permission-floor", check_permission_floor, project_dir=project_dir,
             session_start=args.session_start),
    ]

    hard_fail = False
    any_advisory = False
    out_lines = []
    for name, ok, detail in checks:
        # AC-DPF-1: the advisory branch precedes the truthiness branch, and its mark is distinct
        # from `ok `/`skip`/`XX ` and carries no `XX` substring (release-acceptance scrapes `[XX`).
        if ok is None:
            mark = "skip"
        elif ok is ADVISORY:
            mark = "adv "
            any_advisory = True
        elif ok:
            mark = "ok "
        else:
            mark = "XX "
        if ok is False:
            hard_fail = True
        out_lines.append(f"  [{mark}] {name}: {detail}")

    header = "foundry doctor" + (" (session-start advisory)" if args.session_start else "")
    body = header + "\n" + "\n".join(out_lines)

    if args.session_start:
        # Fail-open: never wedge a session. Warn prominently if anything is off — extended (never
        # weakened) to also warn on an advisory-only run (AC-DPF-4): today's hard_fail-only banner
        # would otherwise print nothing when the only signal is a permission-floor advisory.
        if hard_fail or any_advisory:
            print("WARNING: foundry doctor found problems (advisory; the real merge-side floor "
                  "is ci.yml + btb-gates + hooks/foundry-git-discipline.sh):", file=sys.stderr)
            print(body, file=sys.stderr)
        sys.exit(0)

    print(body)
    print("DOCTOR-GREEN" if not hard_fail else "DOCTOR-RED")
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
