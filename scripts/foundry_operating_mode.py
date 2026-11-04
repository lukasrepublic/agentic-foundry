#!/usr/bin/env python3
"""foundry_operating_mode.py — the one resolver for the workspace operating mode (gn-17).

Two modes, persisted as `operating_mode` in `.claude/foundry-project.json`:
  single-operator  the DEFAULT: no team features, no GitHub team surfaces
  team             the GitHub-native model (opt-in, from plugin 2.0.0); needs a `github_scope`

Absent or malformed -> single-operator (fail to the default, with the reason). `team` without a
well-formed `github_scope` also resolves single-operator. This is NOT the session posture
(`/foundry:mode` factory|noninteractive|interactive), which is a different axis.

Importable: `resolve(project_dir) -> Mode`, `require_team(project_dir, verb) -> None | str`.

CLI:  foundry_operating_mode.py [--project-dir DIR] [--plugin-root DIR] status [--json]
      foundry_operating_mode.py ... enable-team | disable-team
Exit 0 ok / 2 refused / 1 error. Writes are atomic (temp + rename in the same dir), preserve every
other key and its order, and are idempotent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SINGLE = "single-operator"
TEAM = "team"
MODES = (SINGLE, TEAM)
MIN_TEAM_VERSION = (2, 0, 0)
KEY = "operating_mode"
ENABLE_CMD = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_mode.py" enable-team'
HERE = Path(__file__).resolve().parent


@dataclass
class Mode:
    name: str                 # "single-operator" | "team"
    source: str               # "default" | "explicit" | "malformed" | "team-without-scope"
    reason: str
    github_scope_present: bool

    @property
    def line(self) -> str:
        """The doctor's one-line rendering."""
        if self.name == TEAM:
            return TEAM
        if self.source == "default":
            return "single-operator (default)"
        if self.source == "explicit":
            return "single-operator (explicit)"
        return f"single-operator ({self.reason})"


def manifest_path(project_dir) -> str:
    return os.path.join(str(project_dir), ".claude", "foundry-project.json")


def _read(project_dir):
    """(manifest dict | None, error | None). A missing file is ({}, None)."""
    p = manifest_path(project_dir)
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return None, f"cannot read {p}: {e}"
    try:
        doc = json.loads(raw)
    except ValueError as e:
        return None, f"{p} is not valid JSON: {e}"
    if not isinstance(doc, dict):
        return None, f"{p} is not a JSON object"
    return doc, None


def _ghc():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import foundry_github_capabilities as ghc  # lazy: that module imports this one
    return ghc


def scope_problem(scope) -> str | None:
    """The reason `github_scope` is not well-formed, else None. The validity predicate is the one
    `foundry_github_capabilities.load_scope` applies (owner AND planning_repo); this only adds the
    reason text and the optional-field shape checks used by enable-team."""
    if scope is None:
        return "github_scope is absent"
    if not isinstance(scope, dict):
        return "github_scope is not an object"
    for k in ("owner", "planning_repo"):
        if not scope.get(k):
            return f"github_scope.{k} is missing or empty"
    pn = scope.get("project_number")
    if pn is not None and (isinstance(pn, bool) or not isinstance(pn, int) or pn < 1):
        return "github_scope.project_number is not a positive integer"
    ops = scope.get("operators")
    if ops is not None and not isinstance(ops, dict):
        return "github_scope.operators is not an object"
    return None


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=True) if not isinstance(v, str) else v
    s = re.sub(r"[^\x20-\x7e]", "?", s)
    return s[:40]


def resolve(project_dir) -> Mode:
    doc, err = _read(project_dir)
    if doc is None:
        return Mode(SINGLE, "default", f"unreadable manifest ({err}); defaulting", False)
    scope_ok = _ghc().load_scope(str(project_dir))[1] is not None   # the ONE predicate (gn-03)
    present = "github_scope" in doc
    if KEY not in doc:
        return Mode(SINGLE, "default", f"{KEY} absent", present)
    v = doc[KEY]
    if v == SINGLE:
        return Mode(SINGLE, "explicit", f"{KEY} is {SINGLE}", present)
    if v == TEAM:
        if scope_ok:
            return Mode(TEAM, "explicit", f"{KEY} is {TEAM}", True)
        return Mode(SINGLE, "team-without-scope",
                    f"{KEY} is team but {scope_problem(doc.get('github_scope'))}", present)
    return Mode(SINGLE, "malformed", f'malformed value "{_short(v)}"', present)


def refusal_text(verb: str) -> str:
    return (f"{verb} is a team-mode verb; this workspace is in single-operator mode (default). "
            f"Enable it with: {ENABLE_CMD}")


def require_team(project_dir, verb: str) -> str | None:
    """None when the workspace is in team mode, else the one-line refusal text."""
    return None if resolve(project_dir).name == TEAM else refusal_text(verb)


def gate(project_dir, verb: str) -> int | None:
    """For a team-only entry point: print the refusal to stderr and return 2, or None to proceed."""
    msg = require_team(project_dir, verb)
    if msg is None:
        return None
    print(msg, file=sys.stderr)
    return 2


# ------------------------------------------------------------------ version
def parse_version(text) -> tuple | None:
    # exactly MAJOR.MINOR.PATCH: a pre-release or suffix (2.0.0-rc.1, 2.0.0.dev) is NOT >= 2.0.0
    m = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", str(text))
    return tuple(int(x) for x in m.groups()) if m else None


def plugin_version(plugin_root=None) -> tuple[str | None, str | None]:
    """(version string, error). Root: arg, else CLAUDE_PLUGIN_ROOT, else this script's repo root."""
    root = plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT") or str(HERE.parent)
    p = os.path.join(root, ".claude-plugin", "plugin.json")
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f).get("version")
    except (OSError, ValueError, AttributeError) as e:
        return None, f"cannot read plugin version from {p}: {e}"
    if not isinstance(v, str) or parse_version(v) is None:
        return None, f"plugin version in {p} is not a plain MAJOR.MINOR.PATCH release version (pre-releases cannot enable team mode)"
    return v, None


# ------------------------------------------------------------------ write
def _write_key(project_dir, value) -> bool:
    """Set operating_mode atomically. Returns True when the file changed.

    The file is RE-SERIALISED as 2-space JSON (so a 4-space or compact input is reformatted); every key
    and value is preserved and key order is kept, but the bytes are not guaranteed identical. A symlinked
    foundry-project.json has its TARGET rewritten (the link stays a link). The temp file and then the
    directory are fsynced around the rename so the change survives a crash."""
    # the `.claude` DIRECTORY must stay inside the workspace (a symlinked .claude that leaves it is refused);
    # a symlinked manifest FILE is a documented, tested feature (a shared file) and keeps its target rewritten
    cdir = os.path.dirname(manifest_path(project_dir))
    rr, rc = os.path.realpath(str(project_dir)), os.path.realpath(cdir)
    if rc != rr and not rc.startswith(rr + os.sep):
        raise OSError(f"refusing to write: {cdir} resolves outside the workspace")
    p = os.path.realpath(manifest_path(project_dir))
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    doc = json.loads(raw)
    if doc.get(KEY) == value:
        return False
    doc[KEY] = value  # replaces in place, or appends at the end: other keys keep their order
    text = json.dumps(doc, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") or not raw else "")
    d = os.path.dirname(p)
    fd, tmp = tempfile.mkstemp(prefix=".foundry-project.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, os.stat(p).st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, p)
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


# ------------------------------------------------------------------ commands
def _eligibility(project_dir, plugin_root):
    """(ok, reason) for the two offline preconditions, in the charter's order."""
    ver, err = plugin_version(plugin_root)
    if err:
        return False, err
    if parse_version(ver) < MIN_TEAM_VERSION:
        return False, (f"installed plugin version is {ver}; team mode needs >= "
                       f"{'.'.join(map(str, MIN_TEAM_VERSION))} (upgrade with: "
                       f"npx update-agentic-workspace@latest)")
    doc, err = _read(project_dir)
    if doc is None:
        return False, err
    prob = scope_problem(doc.get("github_scope"))
    if prob:
        return False, f"{prob} in .claude/foundry-project.json (team mode needs a github_scope)"
    return True, "eligible"


def capability_verdict(project_dir, runner=None, timeout=30):
    """(verdict in {full, partial, none}, uncovered names, detail). `none` = unreachable too."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import foundry_github_capabilities as ghc
    res = ghc.collect(project_dir, runner or ghc.default_runner, timeout)
    if res is None:
        return "none", [], "github_scope not readable"
    by = {r["name"]: r for r in res["sources"]}
    for n in ("gh", "planning_repo"):
        if by.get(n, {}).get("status") != "ok":
            r = by.get(n, {})
            return "none", [], f"{n}: {r.get('status', 'missing')}: {r.get('detail', '')}"
    bad = [n for n in ghc.INBOX_ROWS if by.get(n, {}).get("status") != "ok"]
    if len(bad) == len(ghc.INBOX_ROWS):
        return "none", bad, "no request source is covered"
    return ("full" if not bad else "partial"), bad, res["summary"]


def cmd_status(project_dir, plugin_root, as_json, out=None):
    out = out or sys.stdout
    m = resolve(project_dir)
    ver, verr = plugin_version(plugin_root)
    ok, why = _eligibility(project_dir, plugin_root)
    if m.name == TEAM:
        elig = "n/a (already team)"
    else:
        elig = "eligible (the capability check runs at enable-team)" if ok else f"not eligible: {why}"
    if as_json:
        out.write(json.dumps({"mode": m.name, "source": m.source, "reason": m.reason,
                              "github_scope_present": m.github_scope_present,
                              "plugin_version": ver, "enable_eligible": ok,
                              "enable_detail": why}, indent=2) + "\n")
    else:
        out.write(f"mode: {m.name}\nsource: {m.source} ({m.reason})\n"
                  f"github_scope present: {'yes' if m.github_scope_present else 'no'}\n"
                  f"plugin version: {ver or 'unknown (' + str(verr) + ')'}\n"
                  f"enable-team: {elig}\n")
    return 0


ENABLE_NOW = """team mode enabled. Now active: capability check, deck events, usage collection, owned queue
(foundry-ticket next|claim|reconcile|lead), operator inbox, slim command deck, issue-bound dispatch,
leader-board, and the deck-events and usage command-line tools. Revert any time with: foundry_operating_mode.py disable-team"""

DISABLE_NOW = """single-operator mode set. Now stopped: owned queue, operator inbox, issue-bound dispatch,
usage attribution, leader-board, the deck-events and usage CLIs and the GitHub capability check. Kept: github_scope, runtime files and
history. Re-enable any time with: foundry_operating_mode.py enable-team"""


def cmd_enable(project_dir, plugin_root, runner=None, timeout=30):
    doc, _ = _read(project_dir)
    if resolve(project_dir).name == TEAM and isinstance(doc, dict) and doc.get(KEY) == TEAM:
        print("already in team mode; nothing changed")   # no probe, no network, no write
        return 0
    ok, why = _eligibility(project_dir, plugin_root)
    if not ok:
        print(f"enable-team refused: {why}", file=sys.stderr)
        return 2
    try:
        verdict, uncovered, detail = capability_verdict(project_dir, runner, timeout)
    except Exception as e:  # noqa: BLE001 -- unreachable is a refusal, not a traceback
        print(f"enable-team refused: capability check unreachable ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 2
    if verdict == "none":
        print(f"enable-team refused: capability check is none/unreachable ({detail}); fix GitHub "
              f"access first (see scripts/foundry_github_capabilities.py)", file=sys.stderr)
        return 2
    if verdict == "partial":
        print("capability check is partial; uncovered: " + ", ".join(uncovered))
    try:
        changed = _write_key(project_dir, TEAM)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(ENABLE_NOW if changed else "already in team mode; nothing changed")
    return 0


def cmd_disable(project_dir):
    if not os.path.isfile(manifest_path(project_dir)):
        print("already single-operator (default); no manifest, nothing changed")
        return 0
    doc, err = _read(project_dir)
    if doc is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        changed = _write_key(project_dir, SINGLE)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(DISABLE_NOW if changed else "already single-operator; nothing changed")
    return 0


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser(description="Show or change the workspace operating mode.")
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--plugin-root", default=None,
                    help="plugin root whose .claude-plugin/plugin.json gives the version (tests)")
    ap.add_argument("--timeout", type=float, default=30)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    sub.add_parser("enable-team")
    sub.add_parser("disable-team")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "status":
            return cmd_status(a.project_dir, a.plugin_root, a.json)
        if a.cmd == "enable-team":
            return cmd_enable(a.project_dir, a.plugin_root, runner, a.timeout)
        return cmd_disable(a.project_dir)
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
