#!/usr/bin/env bash
# foundry-compact-reinject — the SessionStart pinned-context re-injection hook
# (feat-foundry-compact-reinjection, AC-CRI-1..6).
#
# Fires ONLY on a SessionStart event whose `source` is `compact` or `fork` (checked inline, defense-in-depth
# against the `hooks.json` matcher, which admits the other sources too). Emits a <=2048-UTF-8-byte
# pinned-context manifest to stdout — the session posture (`foundry_session_mode.resolve`) and the
# active atom's contract path (from the `.agent/assignment.json` dispatch/work marker) — every field
# re-derived at FIRE TIME (no cache, no prior-emission read). Advisory + fail-open: a broken/absent
# resolver never blocks or delays a session; this hook ALWAYS exits 0, and prints NOTHING unless a
# component genuinely resolves (posture-resolution failure or ANY unhandled error => print nothing).
#
# A thin bash dispatcher around an inline python body (portable; no plugin-shipped python module).
set -uo pipefail   # fail-open: never abort/wedge the session

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$HERE/.." 2>/dev/null && pwd || echo "$HERE/..")}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

_fire() {
  command -v python3 >/dev/null 2>&1 || exit 0
  local payload out rc
  payload="$(cat 2>/dev/null || true)"
  out="$(_CRI_PAYLOAD="$payload" _CRI_PROJECT_DIR="$PROJECT_DIR" _CRI_PLUGIN_ROOT="$PLUGIN_ROOT" \
         python3 - <<'PY' 2>/dev/null
import json
import os
import sys

CEILING = 2048
MARK = "…[truncated]"
HEADER = "[foundry:compact-reinject] pinned context re-injected after compaction"


def _payload():
    raw = os.environ.get("_CRI_PAYLOAD", "")
    try:
        d = json.loads(raw) if raw.strip() else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _fits(t):
    return len(t.encode("utf-8")) <= CEILING


def _render(posture_line, contract_line):
    lines = [HEADER, posture_line]
    if contract_line:
        lines.append("active-atom-contract: " + contract_line)
    return "\n".join(lines) + "\n"


def _assemble(posture_line, contract_line):
    """AC-CRI-2 deterministic truncation: when the manifest exceeds the ceiling the contract path
    is dropped first; the posture line is NEVER dropped."""
    text = _render(posture_line, contract_line)
    if _fits(text):
        return text
    return _render(posture_line, None)


def main():
    payload = _payload()
    # `fork` (a forked background session) receives the same re-inject as `compact`: it starts from a
    # copy of the parent's context and must be told the state it inherits.
    if payload.get("source") not in ("compact", "fork"):
        return   # startup/resume/clear: nothing to re-inject; the matcher is wider than this scope
    project_dir = os.environ.get("_CRI_PROJECT_DIR") or os.getcwd()
    plugin_root = os.environ.get("_CRI_PLUGIN_ROOT") or project_dir
    scripts_dir = os.path.join(plugin_root, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    session_id = payload.get("session_id") or None

    # ── posture — resolved at fire time. AC-CRI-4: a posture-resolution failure suppresses the
    # ENTIRE manifest.
    try:
        import foundry_session_mode as fsm
        posture = fsm.resolve(project_dir, session_id=session_id)
        if posture not in fsm.MODES:
            raise ValueError("resolver returned a value outside the closed mode set")
    except Exception:
        return

    # ── the active atom's contract path, from the dispatch/work marker the WorktreeCreate
    # redirect leaves at `<worktree>/.agent/assignment.json` — independent + non-fatal.
    contract_line = None
    try:
        cwd = payload.get("cwd") or project_dir
        p = os.path.join(cwd, ".agent", "assignment.json")
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        cr = d.get("contract_ref")
        if isinstance(cr, str) and cr.strip():
            contract_line = cr.strip()
    except Exception:
        contract_line = None

    # ── AC-CRI-5: default posture (`factory`) and no active atom -> emit NOTHING.
    if posture == "factory" and contract_line is None:
        return

    sys.stdout.write(_assemble("posture: " + posture, contract_line))


try:
    main()
except Exception:
    pass   # AC-CRI-4: any unhandled script error -> emit nothing (fail-open, never wedge).
PY
)"
  rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$out" ]; then
    printf '%s' "$out"
  fi
  exit 0   # AC-CRI-4: always exit 0 — a broken re-injection never blocks or delays a session.
}

_fire
