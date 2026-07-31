#!/usr/bin/env python3
"""foundry-fleet-doctor — one-command health sweep across ALL Foundry adopter handbooks on a machine.

A single operator typically runs several handbooks (control center + per-project), each wiring the
Foundry plugin. "Are they all current?" otherwise means visiting each repo and running
`/foundry:doctor` by hand — and it's easy to miss that an adopter is on a DIFFERENT/stale plugin
source (e.g. a vendored directory copy) that `claude plugin update` never touches. This sweep
discovers adopters, runs each one's doctor against the plugin IT actually uses, and reports a fleet
table: source · installed version/sha · version-drift · DOCTOR verdict.

  foundry-fleet-doctor.py --root ~/work --root ~/personal      # scan these roots for adopters
  foundry-fleet-doctor.py --root ~/work --strict               # exit non-zero if ANY adopter is RED
  foundry-fleet-doctor.py --selftest                           # hermetic proof (AC-FLEET-1..4)

Advisory by default (exit 0 with the report); --strict fails closed on any RED for cron/CI use.
Adopters are dirs containing .claude/foundry-operators.json (the registry marker). Project-scoped
installs in ~/.claude/plugins/installed_plugins.json are folded in so a vendored-source adopter is
never silently assumed to be on the marketplace.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _plugins_dir():
    return os.environ.get("FOUNDRY_PLUGINS_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "plugins")


def discover_adopters(roots, max_depth=4, extra=None):
    """Return the sorted, de-duped set of adopter dirs: every dir under `roots` (bounded depth)
    that contains .claude/foundry-operators.json, plus any `extra` paths that exist."""
    found = set()
    for root in roots or []:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, _ in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
            if os.path.isfile(os.path.join(dirpath, ".claude", "foundry-operators.json")):
                found.add(dirpath)
                dirnames[:] = []  # don't descend into a discovered adopter
    for p in (extra or []):
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isfile(os.path.join(p, ".claude", "foundry-operators.json")):
            found.add(p)
    return sorted(found)


def adopter_source(adopter):
    """The plugin source key the adopter wires (first foundry@* in .claude/settings.json
    enabledPlugins), or None."""
    try:
        cfg = json.load(open(os.path.join(adopter, ".claude", "settings.json")))
    except (OSError, json.JSONDecodeError):
        return None
    for k in (cfg.get("enabledPlugins") or {}):
        if k.startswith("foundry@"):
            return k
    return None


def installed_index(plugins_dir=None):
    """{source_key: {installPath, version, sha}} from installed_plugins.json."""
    p = os.path.join(plugins_dir or _plugins_dir(), "installed_plugins.json")
    out = {}
    try:
        d = json.load(open(p))
    except (OSError, json.JSONDecodeError):
        return out
    for key, recs in (d.get("plugins") or {}).items():
        for r in recs or []:
            out[key] = {"installPath": r.get("installPath"), "version": r.get("version"),
                        "sha": (r.get("gitCommitSha") or "")[:8]}
            break
    return out


def _run_doctor(adopter, install_path):
    """Run the adopter's actual plugin doctor; return (verdict, drift_line)."""
    doctor = os.path.join(install_path or "", "scripts", "foundry-doctor.py")
    if not (install_path and os.path.isfile(doctor)):
        return "NO-PLUGIN", "installed plugin path missing"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": adopter, "CLAUDE_PLUGIN_ROOT": install_path}
    p = subprocess.run([sys.executable, doctor], capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr
    verdict = "GREEN" if "DOCTOR-GREEN" in out else ("RED" if "DOCTOR-RED" in out else "UNKNOWN")
    drift = next((ln.strip() for ln in out.splitlines() if "version-drift" in ln), "")
    return verdict, drift


def adopter_status(adopter, idx, runner=_run_doctor):
    src = adopter_source(adopter)
    info = idx.get(src or "", {})
    verdict, drift = runner(adopter, info.get("installPath"))
    return {"adopter": adopter, "source": src or "(none)",
            "version": info.get("version") or "?", "sha": info.get("sha") or "?",
            "verdict": verdict, "drift": drift}


def render_report(statuses):
    lines = ["foundry fleet doctor:", ""]
    lines.append(f"  {'ADOPTER':<44} {'SOURCE':<26} {'VER':<7} {'VERDICT'}")
    for s in statuses:
        adopter = s["adopter"]
        disp = ("…" + adopter[-43:]) if len(adopter) > 44 else adopter
        lines.append(f"  {disp:<44} {s['source']:<26} {s['version']:<7} {s['verdict']}")
    greens = sum(1 for s in statuses if s["verdict"] == "GREEN")
    lines += ["", f"  {greens}/{len(statuses)} DOCTOR-GREEN"]
    nonmkt = [s for s in statuses if s["source"] not in ("(none)",) and not s["source"].endswith("@agentic-foundry")]
    if nonmkt:
        lines.append(f"  ⚠ {len(nonmkt)} adopter(s) on a non-marketplace source "
                     "(NOT updated by `claude plugin update foundry@agentic-foundry`): "
                     + ", ".join(s["source"] for s in nonmkt))
    return "\n".join(lines)


def fleet_verdict(statuses, strict):
    """Exit code: --strict → non-zero if any adopter is not GREEN; advisory → always 0."""
    if strict and any(s["verdict"] != "GREEN" for s in statuses):
        return 1
    return 0


def _selftest():
    import tempfile
    lines, ok = [], True

    def emit(t, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        lines.append(f"{t}: {'PASS' if passed else 'FAIL'}{(' — ' + detail) if detail else ''}")

    with tempfile.TemporaryDirectory() as tmp:
        def mk(rel, source_key):
            d = os.path.join(tmp, rel)
            os.makedirs(os.path.join(d, ".claude"))
            json.dump({"operators": {"op_x": {}}}, open(os.path.join(d, ".claude", "foundry-operators.json"), "w"))
            if source_key:
                json.dump({"enabledPlugins": {source_key: True}},
                          open(os.path.join(d, ".claude", "settings.json"), "w"))
            return d
        a1 = mk("a/hb1", "foundry@agentic-foundry")
        a2 = mk("a/nested/hb2", "foundry@foundry-local")     # divergent source
        os.makedirs(os.path.join(tmp, "a", "not-an-adopter"))  # no marker → must be ignored

        # AC-FLEET-1: discovery finds exactly the two adopters, ignores the non-adopter.
        found = discover_adopters([tmp])
        emit("AC-FLEET-1 discovers-adopters",
             set(found) == {a1, a2}, f"found {len(found)}")

        # AC-FLEET-2: per-adopter report carries source + version + verdict (stub runner).
        idx = {"foundry@agentic-foundry": {"installPath": "/x", "version": "0.1.3", "sha": "abc"},
               "foundry@foundry-local": {"installPath": "/y", "version": "0.1.0", "sha": "old"}}
        fake = {a1: ("GREEN", "version-drift: current"), a2: ("RED", "version-drift: BEHIND")}
        statuses = [adopter_status(a, idx, runner=lambda ad, ip: fake[ad]) for a in found]
        rep = render_report(statuses)
        emit("AC-FLEET-2 reports-per-adopter-verdict-and-source",
             "foundry@agentic-foundry" in rep and "0.1.3" in rep and "GREEN" in rep and "RED" in rep,
             "report rendered")

        # AC-FLEET-3: --strict fails on any RED; advisory passes.
        emit("AC-FLEET-3 strict-fails-on-any-red",
             fleet_verdict(statuses, strict=True) == 1 and fleet_verdict(statuses, strict=False) == 0)

        # AC-FLEET-4: a divergent (non-marketplace) source is flagged, not silently assumed current.
        emit("AC-FLEET-4 flags-divergent-source",
             "non-marketplace source" in rep and "foundry@foundry-local" in rep)

    print("foundry-fleet-doctor self-test:")
    for l in lines:
        print("  " + l)
    print("FLEET-DOCTOR-SELFTEST-GREEN" if ok else "FLEET-DOCTOR-SELFTEST-RED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=[], help="root dir to scan for adopters (repeatable)")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any adopter is not DOCTOR-GREEN")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())

    idx = installed_index()
    project_paths = []
    p = os.path.join(_plugins_dir(), "installed_plugins.json")
    try:
        d = json.load(open(p))
        for recs in (d.get("plugins") or {}).values():
            for r in recs or []:
                if r.get("projectPath"):
                    project_paths.append(r["projectPath"])
    except (OSError, json.JSONDecodeError):
        pass

    adopters = discover_adopters(args.root or [os.getcwd()], extra=project_paths)
    if not adopters:
        print("foundry fleet doctor: no adopters found (pass --root <dir>)")
        sys.exit(0)
    statuses = [adopter_status(a, idx) for a in adopters]
    print(render_report(statuses))
    sys.exit(fleet_verdict(statuses, args.strict))


if __name__ == "__main__":
    main()
