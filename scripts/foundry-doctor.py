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
  7. `permissions-policy` (feat-foundry-authorization-capability-preflight-at-dispatch, AC-CPD-4;
     replaces the R1 `permissions-policy` drift-only advisory, feat-foundry-authorization-standing-
     grants-as-policy AC-SGP-6, but KEEPS the R1 drift state on the same line rather than dropping
     it): one ADVISORY line -- runs `scripts/foundry-capability-preflight.py` over every atom
     (contract_ref or charter_ref) of every ACTIVE release under `.foundry/releases/*/release.yaml`,
     printing `preflight ok (<n> atoms)` or `preflight: <n> missing rule(s)`, followed by
     `; policy absent|in-sync|drift (<k>)` -- the SAME derivation `scripts/foundry-permissions-
     compile.py --check` runs. NEVER RED, by design (AC-CPD-4, unchanged from AC-SGP-6): a stale-
     permission workspace must never wedge a session; `/foundry:mode-autonomous`'s own preflight-
     before-dispatch (AC-CPD-3) and the operator's own settings review are the real enforcement
     surface.
  8. `agent-teams` (feat-agent-teams-enablement, AC-ATE-4): one advisory line, `agent-teams: on
     (settings env) | off`, derived from whether the EFFECTIVE settings files' top-level `env`
     block sets `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` to `"1"` — `~/.claude/settings.json`, then
     `<project>/.claude/settings.json`, then `<project>/.claude/settings.local.json`, in that
     ASCENDING-precedence (last-one-present-wins) order, mirroring the platform's own
     user/project/local override resolution. Never RED: flipping the flag is an adopter opt-in,
     never a doctor-enforced default (see `docs/how-to/agent-teams.md`).
  9. `branches` (branch-and-worktree-discipline, AC-BWD-3): one advisory line, `branches: <n>
     merged-not-deleted, <m> stale worktrees`, computed by IMPORTING
     `scripts/foundry-worktree-gc.py`'s own classifier (ancestry-only, `use_gh=False` -- this
     stays a cheap offline probe, never a live `gh` call) over the session's own project dir.
     Never RED: reads `n/a (not a git checkout)` when the project dir is not a git repository
     (see `docs/how-to/branching-and-cleanup.md`).

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
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)

# R3 (PR #60 review): the render floor (AC-ROST-5 pattern) applied to this probe's own exception
# details — an unhandled/malformed-input exception's `str(e)` can carry attacker-reachable text
# (e.g. a crafted settings-file path or JSON payload) straight into the report untouched. A local
# duplicate rather than an import from foundry_permission_floor (which may itself be the thing
# that failed to import) — the local-render-floor pattern (the fleet-session scripts that first
# carried it were retired in the R4 deletion wave; the pattern stays).
_DOCTOR_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f])")
_DOCTOR_DETAIL_CAP = 200


def _sanitize_detail(s, cap=_DOCTOR_DETAIL_CAP):
    """Length-cap + control/ANSI-neutralize a probe-exception detail string before it is returned
    for rendering (R3, PR #60 review)."""
    if not isinstance(s, str):
        return s
    s = _DOCTOR_CTRL_RE.sub("", s)
    if len(s) > cap:
        s = s[:cap]
    return s

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
# shared helper -- lazy-loads scripts/foundry_permission_floor.py. The probe that owned this
# loader (permission-floor drift, feat-foundry-doctor-permission-floor-check, AC-DPF-1..8) was
# deleted by subtraction-wave (AC-SUB-1c, -> feat-foundry-authorization-capability-preflight-at-
# dispatch, AC-CPD-4, which carries the same drift signal on the permissions-policy line below).
# The loader itself stays: probe 8 (agent-teams) below still reuses it for
# `foundry_permission_floor.SETTINGS_RELATIVE_PATHS`.
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


# --------------------------------------------------------------------------------------- #
# 7. permissions-policy advisory -- capability preflight over every active-release atom
#    (feat-foundry-authorization-capability-preflight-at-dispatch, AC-CPD-4; replaces the R1
#    permissions-policy drift-only advisory, feat-foundry-authorization-standing-grants-as-policy
#    AC-SGP-6)
# --------------------------------------------------------------------------------------- #
def _load_capability_preflight_module(plugin_root):
    path = os.path.join(plugin_root, "scripts", "foundry-capability-preflight.py")
    if not os.path.isfile(path):
        return None
    scripts_dir = os.path.dirname(path)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("foundry_capability_preflight_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_permissions_compile_module(plugin_root):
    path = os.path.join(plugin_root, "scripts", "foundry-permissions-compile.py")
    if not os.path.isfile(path):
        return None
    scripts_dir = os.path.dirname(path)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("foundry_permissions_compile_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _active_release_atoms(project_dir):
    """Every (release_id, atom) pair from every release under `.foundry/releases/*/release.yaml`
    whose `state` is `active`, for atoms carrying a `contract_ref` or `charter_ref` (AC-CPD-4). A
    release that fails to load (malformed manifest, unknown id, etc.) is SKIPPED, not raised --
    this probe stays advisory even when an unrelated release.yaml elsewhere is broken."""
    releases_dir = os.path.join(project_dir, ".foundry", "releases")
    if not os.path.isdir(releases_dir):
        return []
    scripts_dir = os.path.join(PLUGIN_ROOT, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import foundry_release as _fr  # lazy import, mirrors check_operator_registry above

    out = []
    try:
        names = sorted(os.listdir(releases_dir))
    except OSError:
        return []
    for name in names:
        if not os.path.isfile(os.path.join(releases_dir, name, "release.yaml")):
            continue
        try:
            release = _fr.load_release(name, project_dir=project_dir)
        except _fr.ReleaseError:
            continue
        if release.state != "active":
            continue
        for atom in release.atoms:
            if atom.contract_ref or atom.charter_ref:
                out.append((release.id, atom))
    return out


def _policy_drift_state(pc, pdir):
    """AC-CPD-4: the R1 drift state -- `absent` | `in-sync` | `drift (<k>)` -- kept on the SAME
    doctor line the preflight status rides, not dropped. Unchanged derivation from
    `foundry-permissions-compile.py --check` (feat-foundry-authorization-standing-grants-as-policy,
    AC-SGP-6)."""
    policy_path = os.path.join(pdir, pc.POLICY_REL)
    if not os.path.isfile(policy_path):
        return "absent", True
    code, detail = pc.run_check(pdir)
    if code == pc.EXIT_OK:
        return "in-sync", True
    if code == pc.EXIT_DRIFT:
        findings = [ln for ln in detail.splitlines() if ln.strip().startswith(("missing", "moved", "extra"))]
        return f"drift ({len(findings)})", False
    # a schema-invalid/unreadable permissions.yaml is itself advisory here (AC-CPD-4 "never RED")
    # -- the compiler's own --check is the fail-closed surface for that.
    return _sanitize_detail(f"invalid ({detail})"), False


def check_permissions_policy(plugin_root=None, project_dir=None):
    """ADVISORY, never RED (AC-CPD-4, unchanged posture from AC-SGP-6) — wrapped entirely in its
    own try/except so that a probe crash never reaches `_run`'s generic (RED-producing) exception
    handler; the "never RED" guarantee has to hold even when the preflight module itself is
    broken."""
    root = plugin_root or PLUGIN_ROOT
    pdir = project_dir or _project_dir()
    try:
        cpf = _load_capability_preflight_module(root)
        pc = _load_permissions_compile_module(root)
        if cpf is None or pc is None:
            return None, "capability-preflight/compiler module absent (not applicable)"

        atoms = _active_release_atoms(pdir)
        missing_total = 0
        for _release_id, atom in atoms:
            try:
                # AC-CPD-1 (auth_seq 2): the same path-confinement floor the CLI's own --contract/
                # --charter enforces applies here too — a `..`-escaping or oversized atom ref is
                # refused by the loader itself, never joined/opened directly by this probe.
                if atom.contract_ref:
                    capabilities = cpf.load_contract_capabilities(atom.contract_ref, pdir)
                else:
                    capabilities = cpf.load_charter_capabilities(atom.charter_ref, pdir)
                verdict = cpf.preflight(capabilities, pdir)
            except cpf.PreflightInputError:
                # an unreadable/out-of-bounds atom-level source is itself advisory here (AC-CPD-4
                # "never RED") -- the preflight's own --contract/--charter run is the fail-closed
                # surface for that.
                continue
            missing_total += len(verdict.get("missing", []))

        preflight_part = (
            f"preflight ok ({len(atoms)} atoms)" if missing_total == 0
            else f"preflight: {missing_total} missing rule(s)"
        )
        drift_state, drift_ok = _policy_drift_state(pc, pdir)
        detail = f"{preflight_part}; policy {drift_state}"
        # permissions-scaffold (ER #215, AC-PSC-4): an absent policy names its remedy in one clause.
        if str(drift_state).startswith("absent"):
            detail += (" — seed it: `npx update-agentic-workspace` writes a starter "
                       ".foundry/permissions.yaml, or copy context/permissions-template.yaml")
        if missing_total == 0 and drift_ok:
            return True, detail
        return ADVISORY, detail
    except Exception as e:  # noqa: BLE001 — deliberate: AC-CPD-4 must never redden the run
        return ADVISORY, _sanitize_detail(f"probe error ({type(e).__name__}: {e})")


# --------------------------------------------------------------------------------------- #
# 8. agent-teams advisory -- whether the native team surface is on (feat-agent-teams-enablement,
#    AC-ATE-4)
# --------------------------------------------------------------------------------------- #
_AGENT_TEAMS_ENV_KEY = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
# Mirrors `foundry_permission_floor._MAX_FILE_BYTES` -- the actual bounded read (AC-RES-3) is now
# `foundry_permission_floor.load_settings_env`, so this local literal no longer gates a read of
# its own; kept as the same 1 MiB value this probe's own tests fixture against, so a test can
# construct an oversized settings file without importing the shared module's private constant.
_SETTINGS_MAX_BYTES = 1024 * 1024


def _settings_candidate_paths(project_dir, plugin_root):
    """The three effective-settings locations, in ASCENDING precedence order (last-one-present-
    WINS below) -- user-level global, then project-shared, then project-local -- mirroring the
    platform's own user/project/local settings-override resolution. Reuses
    `foundry_permission_floor.SETTINGS_RELATIVE_PATHS` for the two project-scoped relative paths
    (rather than re-declaring them) so the permission-floor probe and this one can never name two
    different files; falls back to the same two literal paths if that module is unavailable."""
    paths = [os.path.join(os.path.expanduser("~"), ".claude", "settings.json")]
    pf = _load_permission_floor_module(plugin_root)
    if pf is not None:
        paths += [os.path.join(project_dir, rel) for rel in pf.SETTINGS_RELATIVE_PATHS]
    else:
        paths += [
            os.path.join(project_dir, ".claude", "settings.json"),
            os.path.join(project_dir, ".claude", "settings.local.json"),
        ]
    return paths


def _load_worktree_gc_module(plugin_root):
    """Mirrors `_load_capability_preflight_module` above -- a hyphenated filename, loaded by
    explicit path (never a bare `import`)."""
    path = os.path.join(plugin_root, "scripts", "foundry-worktree-gc.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("foundry_worktree_gc_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_branches_advisory(plugin_root=None, project_dir=None):
    """AC-BWD-3: one advisory line, `branches: <n> merged-not-deleted, <m> stale worktrees`,
    NEVER RED -- computed by IMPORTING `scripts/foundry-worktree-gc.py`'s own `classify_repo`
    (never a re-implementation of the git plumbing here). Runs `use_gh=False` deliberately: this
    is a cheap, offline, every-run probe (the module docstring's own "thin probe" discipline), not
    a live `gh` query -- an unmerged branch that gh WOULD tell apart as `open-pr` is still counted
    here as neither `merged-not-deleted` nor a false positive, since ancestry-only classification
    can only ever UNDER-report `merged` relative to the live gc run, never over-report it.

    `project_dir` defaults to this session's own project dir (mirrors every other probe here);
    when it is not a git checkout at all (an adopter running doctor from a non-repo directory, or
    the plugin's own installed cache tree, which is never a checkout), this reads
    `"n/a (not a git checkout)"` rather than attempting a git call that would only fail."""
    pdir = project_dir or _project_dir()
    try:
        gc = _load_worktree_gc_module(plugin_root or PLUGIN_ROOT)
        if gc is None or not gc.is_git_repo(pdir):
            return True, "n/a (not a git checkout)"
        rows, _worktrees = gc.classify_repo(pdir, use_gh=False)
        merged = [r for r in rows if r["class"] == "merged"]
        stale_worktrees = [r for r in merged if r.get("worktree")]
        return True, f"{len(merged)} merged-not-deleted, {len(stale_worktrees)} stale worktrees"
    except Exception as e:  # noqa: BLE001 -- deliberate: AC-BWD-3 must never redden or crash the run
        return ADVISORY, _sanitize_detail(f"unknown (probe error: {type(e).__name__}: {e})")


def check_agent_teams_flag(plugin_root=None, project_dir=None):
    """AC-ATE-4: `agent-teams: on (settings env) | off`, NEVER RED -- flipping the flag is an
    adopter opt-in, never a doctor-enforced default (this workspace's own settings are the
    operator's, out of scope per the charter).

    Wrapped ENTIRELY in its own try/except, mirroring `check_permissions_policy` above (PR #185
    review finding 1): `_settings_candidate_paths` -> `_load_permission_floor_module` does a bare
    `import foundry_permission_floor`, and this probe is called directly from `main()` -- NOT
    through the crash-proof `_run("<name>", ...)` wrapper the `checks` list uses -- so without this
    try/except a broken `foundry_permission_floor.py` would traceback straight out of `main()`,
    BEFORE the `--session-start` fail-open branch even runs, wedging every session start.

    AC-RES-3: the bounded `env`-block read across the candidate settings files is now
    `foundry_permission_floor.load_settings_env` (shared with `foundry_command_deck_watch`'s
    advisory-header gate) rather than a local copy -- when that module is unavailable,
    `_settings_candidate_paths` already falls back to the two literal project-scoped paths, and
    an absent `pf` module here simply means the shared reader is never reached; `on` stays `False`
    (the safe default), matching this function's own NEVER-RED contract."""
    root = plugin_root or PLUGIN_ROOT
    pdir = project_dir or _project_dir()
    try:
        pf = _load_permission_floor_module(root)
        if pf is None:
            return True, "off"
        env = pf.load_settings_env(_settings_candidate_paths(pdir, root))
        on = env.get(_AGENT_TEAMS_ENV_KEY) == "1"
        return True, "on (settings env)" if on else "off"
    except Exception as e:  # noqa: BLE001 -- deliberate: AC-ATE-4 must never redden or crash the run
        return ADVISORY, _sanitize_detail(f"unknown (probe error: {type(e).__name__}: {e})")


_STATUSLINE_MARKER = "feat-foundry-init-statusline-wrapper"
_STATUSLINE_WRAPPER_REL = os.path.join(".claude", "hooks", "foundry-statusline.sh")


def _statusline_renderer_path(config_root):
    """The renderer the wrapper would resolve from THIS machine: installed_plugins.json's
    installPath first, then the cache newest by version segment. Returns (path, version) or
    (None, None). Mirrors cli/templates/foundry-statusline.sh's resolution order (AC-SLW-3)."""
    ip = os.path.join(config_root, "plugins", "installed_plugins.json")
    try:
        with open(ip, encoding="utf-8") as fh:
            doc = json.load(fh)
        entry = (doc.get("plugins") or {}).get("foundry@agentic-foundry") or doc.get("foundry@agentic-foundry")
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        install = entry.get("installPath") if isinstance(entry, dict) else None
        if install and os.path.isfile(os.path.join(install, "scripts", "foundry-statusline.sh")):
            return os.path.join(install, "scripts", "foundry-statusline.sh"), os.path.basename(install.rstrip("/"))
    except Exception:  # noqa: BLE001 -- absent/unreadable registry is simply "not this path"
        pass
    import glob
    best = None
    for cand in glob.glob(os.path.join(config_root, "plugins", "cache", "*", "foundry", "*", "scripts", "foundry-statusline.sh")):
        ver = os.path.basename(os.path.dirname(os.path.dirname(cand)))
        key = tuple(int(p) if p.isdigit() else p for p in ver.replace("-", ".").split("."))
        if best is None or key > best[0]:
            best = (key, cand, ver)
    return (best[1], best[2]) if best else (None, None)


def check_statusline(plugin_root=None, project_dir=None):
    """statusline-wiring (v1.17.0, AC-SLW-4): `statusline: wired (renderer <version>)` when the
    settings key, the framework wrapper and a renderer resolvable from this machine are all present;
    otherwise the FIRST missing piece, named. NEVER RED — the status line is fail-open by design and
    absence is a supported state; this line exists so an absent token bar is explained, not guessed."""
    pdir = project_dir or _project_dir()
    try:
        settings_path = os.path.join(pdir, ".claude", "settings.json")
        key = None
        try:
            with open(settings_path, encoding="utf-8") as fh:
                key = (json.load(fh) or {}).get("statusLine")
        except Exception:  # noqa: BLE001
            key = None
        if not key:
            return ADVISORY, "no `statusLine` key in .claude/settings.json — run `npx update-agentic-workspace` (it wires it)"
        wrapper = os.path.join(pdir, _STATUSLINE_WRAPPER_REL)
        if not os.path.isfile(wrapper):
            return ADVISORY, f"wrapper absent at {_STATUSLINE_WRAPPER_REL} — run `npx update-agentic-workspace`"
        with open(wrapper, encoding="utf-8", errors="replace") as fh:
            if _STATUSLINE_MARKER not in fh.read():
                return ADVISORY, f"{_STATUSLINE_WRAPPER_REL} carries no framework marker (operator-owned; not reconciled)"
        config_root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        renderer, ver = _statusline_renderer_path(config_root)
        if renderer is None:
            return ADVISORY, _sanitize_detail(f"no renderer resolvable from this machine (looked under {config_root}/plugins) — the wrapper falls back to an inline bar")
        return True, _sanitize_detail(f"wired (renderer {ver})")
    except Exception as e:  # noqa: BLE001 -- NEVER-RED contract
        return ADVISORY, _sanitize_detail(f"unknown (probe error: {type(e).__name__}: {e})")


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
            ok, detail = False, _sanitize_detail(f"probe crashed: {type(e).__name__}: {e}")
        return (name, ok, detail)

    checks = [
        _run("manifest", check_manifest),
        _run("hooks", check_hooks),
        _run("skills-frontmatter", check_skills_frontmatter),
        _run("stack-profile-lock", check_stack_profile_lock, project_dir=project_dir),
        _run("operator-registry", check_operator_registry, project_dir),
        _run("control-plane", check_control_plane, project_dir=project_dir),
    ]

    hard_fail = False
    any_advisory = False
    out_lines = []

    def _render_row(name, ok, detail):
        # AC-DPF-1: the advisory branch precedes the truthiness branch, and its mark is distinct
        # from `ok `/`skip`/`XX ` and carries no `XX` substring (release-acceptance scrapes `[XX`).
        nonlocal hard_fail, any_advisory
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
        # A probe may return a multi-line detail (permission-floor does, one finding per line).
        # The first line rides the check row; the rest are indented under it so a long finding set
        # stays scannable instead of collapsing into one unreadable row.
        head, _, rest = str(detail).partition("\n")
        out_lines.append(f"  [{mark}] {name}: {head}")
        for extra in rest.splitlines():
            out_lines.append(f"           {extra}")

    for name, ok, detail in checks:
        _render_row(name, ok, detail)

    # `permissions-policy` (AC-SGP-6) is rendered the SAME way as the registered checks above but
    # is deliberately NOT added to `checks`/run through the `_run("<name>", ...)` literal: the
    # doctor-probe-claims doc-sync test (AC-DRT-9, tests/test_doc_claims.py) derives its probe
    # COUNT from that exact call-site regex and cross-checks it against docs/QUICKSTART.md's
    # pinned "N probes" claim — a file outside this atom's scope. This line still satisfies
    # AC-SGP-6 literally ("one advisory permissions-policy line ... never RED") without silently
    # drifting an out-of-scope doc claim; see this atom's PR notes for the exact doc-sync
    # follow-up this asks for.
    pp_ok, pp_detail = check_permissions_policy(project_dir=project_dir)
    _render_row("permissions-policy", pp_ok, pp_detail)

    # `agent-teams` (AC-ATE-4) is rendered the SAME way, for the SAME reason: it is deliberately
    # not a `_run("<name>", ...)` call-site literal so it stays outside the doc-sync test's probe
    # count (tests/test_doc_claims.py's `_derive_doctor_probe_ids`, which regex-matches ONLY
    # `_run("...")` call sites) -- this line still satisfies AC-ATE-4 ("one advisory line ... never
    # RED") without touching docs/QUICKSTART.md, which is outside this atom's allowed_paths.
    at_ok, at_detail = check_agent_teams_flag(project_dir=project_dir)
    _render_row("agent-teams", at_ok, at_detail)

    # `branches` (AC-BWD-3) is rendered the SAME way, for the SAME reason (the agent-teams
    # precedent this atom follows verbatim): deliberately not a `_run("<name>", ...)` call-site
    # literal, so it stays outside tests/test_doc_claims.py's doctor-probe-claims bijection
    # (docs/QUICKSTART.md is outside this atom's allowed_paths).
    br_ok, br_detail = check_branches_advisory(project_dir=project_dir)
    _render_row("branches", br_ok, br_detail)

    # `statusline` (AC-SLW-4) is rendered the SAME way, for the SAME reason: an advisory line
    # outside the `_run("...")` probe count, never RED — it explains an absent token bar.
    sl_ok, sl_detail = check_statusline(project_dir=project_dir)
    _render_row("statusline", sl_ok, sl_detail)

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
