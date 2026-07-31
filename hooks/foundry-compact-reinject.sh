#!/usr/bin/env bash
# foundry-compact-reinject — the SessionStart:compact pinned-context re-injection hook
# (feat-foundry-compact-reinjection, AC-CRI-1..6).
#
# Fires ONLY on a SessionStart event whose `source` is `compact` (scoped by the `hooks.json`
# matcher; the `source` field is ALSO checked inline as defense-in-depth). Emits a <=2048-UTF-8-
# byte pinned-context manifest to stdout — the active release id + its run-state `--summary`
# digest, the session posture (`foundry_session_mode.resolve`), and the active atom's contract
# path (from the `.agent/assignment.json` dispatch/work marker) — every field re-derived at FIRE
# TIME (no cache, no prior-emission read). Advisory + fail-open: a broken/absent resolver never
# blocks or delays a session; this hook ALWAYS exits 0, and prints NOTHING unless a component
# genuinely resolves (posture-resolution failure or ANY unhandled error => print nothing).
#
# Sibling shape to hooks/foundry-session-learnings.sh: a thin bash dispatcher around an inline
# python body (portable; no new plugin-shipped python module — the logic lives HERE, in the one
# allowed_paths file).
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


def _render(release_line, digest_text, posture_line, contract_line):
    lines = [HEADER]
    if release_line:
        lines.append(release_line)
    if digest_text is not None:
        lines.append("run-state:")
        for l in digest_text.split("\n"):
            lines.append("  " + l)
    lines.append(posture_line)
    if contract_line:
        lines.append("active-atom-contract: " + contract_line)
    return "\n".join(lines) + "\n"


def _assemble(release_line, digest_text, posture_line, contract_line):
    """AC-CRI-2 deterministic priority truncation (lowest-value shed first), re-checked after
    EVERY shedding step so the smallest amount of content is ever dropped: (1) the run-state
    digest body is truncated on a whole-character boundary, trailing MARK appended, kept as long
    as possible; (2) once the digest is fully exhausted (or never existed) and it STILL doesn't
    fit, the active-atom contract path (c) is dropped entirely; (3) if it STILL doesn't fit, the
    run-state section is dropped ENTIRELY (not just truncated). The posture line and the release
    id(s) are NEVER dropped at any step."""
    text = _render(release_line, digest_text, posture_line, contract_line)
    if _fits(text):
        return text

    # Step 1: shrink the digest body on a whole-character boundary (prefix kept, tail dropped —
    # mirrors the front-loaded truncation of render_summary itself), contract (c) still kept.
    if digest_text:
        n = len(digest_text)
        for k in range(n - 1, -1, -1):
            cand = (digest_text[:k] + MARK) if k > 0 else MARK
            t = _render(release_line, cand, posture_line, contract_line)
            if _fits(t):
                return t

    # Step 2: (c) dropped. The digest stays at its most-truncated form (a bare MARK) if one
    # existed at all; nothing left to shrink further short of removing it outright (step 3).
    minimal_digest = MARK if digest_text else None
    t = _render(release_line, minimal_digest, posture_line, None)
    if _fits(t):
        return t

    # Step 3: the run-state section is dropped ENTIRELY (not just truncated). (c) already gone.
    # Best-effort floor: the posture line + release id(s) are never dropped by this function.
    return _render(release_line, None, posture_line, None)


def main():
    payload = _payload()
    if payload.get("source") != "compact":
        return   # defense-in-depth; the hooks.json matcher already scopes firing to `compact`

    project_dir = os.environ.get("_CRI_PROJECT_DIR") or os.getcwd()
    plugin_root = os.environ.get("_CRI_PLUGIN_ROOT") or project_dir
    scripts_dir = os.path.join(plugin_root, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    session_id = payload.get("session_id") or None

    # ── (b) posture — resolved FIRST, at fire time. AC-CRI-4: a posture-resolution failure
    # suppresses the ENTIRE manifest (its own dedicated clause, stronger than per-component omit).
    try:
        import foundry_session_mode as fsm
        posture = fsm.resolve(project_dir, session_id=session_id)
        if posture not in fsm.MODES:
            raise ValueError("resolver returned a value outside the closed mode set")
    except Exception:
        return

    # ── (a) active release id + its run-state --summary digest — independent + non-fatal: any
    # failure here OMITS (a) only, never suppresses (b)/(c) (AC-CRI-4 per-component degradation).
    # Every field is re-derived HERE, at fire time — never a cached/prior emission (AC-CRI-3).
    release_line = None
    digest_text = None
    try:
        base = os.path.join(project_dir, ".foundry", "releases")
        active_ids = []
        if os.path.isdir(base):
            import yaml
            for name in sorted(os.listdir(base)):
                p = os.path.join(base, name, "release.yaml")
                if not os.path.isfile(p):
                    continue
                try:
                    with open(p, encoding="utf-8") as fh:
                        doc = yaml.safe_load(fh)
                except Exception:
                    continue   # one malformed manifest never sinks the whole scan
                if isinstance(doc, dict) and doc.get("state") == "active" and doc.get("id") == name:
                    active_ids.append(name)
        if len(active_ids) == 1:
            release_line = "release: " + active_ids[0]
            try:
                import foundry_release as fr
                rel = fr.load_release(active_ids[0], project_dir=project_dir)
                rows = fr.derive_run_state(rel, project_dir=project_dir)
                digest_text = fr.render_summary(rel, rows).rstrip("\n")
            except Exception:
                digest_text = None   # release id still shown; only the digest is lost
        elif len(active_ids) > 1:
            # AC-CRI-2: ambiguity is surfaced (every active id named), never guessed — no digest.
            release_line = "releases (ambiguous, active): " + ", ".join(active_ids)
            digest_text = None
    except Exception:
        release_line = None
        digest_text = None

    # ── (c) the active atom's contract path, from the dispatch/work marker the WorktreeCreate
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

    # ── AC-CRI-5: no active release AND default posture (`factory`) AND no active atom
    # resolvable -> emit NOTHING (overrides (b)'s otherwise-unconditional inclusion; no noise on
    # an unrelated session).
    if release_line is None and posture == "factory" and contract_line is None:
        return

    manifest_text = _assemble(release_line, digest_text, "posture: " + posture, contract_line)

    sys.stdout.write(manifest_text)


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
