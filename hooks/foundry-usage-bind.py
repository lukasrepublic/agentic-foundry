#!/usr/bin/env python3
"""foundry-usage-bind — SubagentStart hook: append one dispatch binding line (gn-05).

Reads the hook payload (session_id, agent_id, agent_type, cwd), looks for `<git-dir>/foundry-assignment.json`
in the cwd's worktree ({"operator": ..., "issue": ...}; absence is tolerated and yields a binding with no
issue), and appends one line to `<usage.dataset>/bindings.jsonl` (`.claude/foundry-project.json`).
Fail-open: any problem exits 0 silently; it never blocks a dispatch. Stdlib only, well under a second.
"""
import json
import os
import sys
from pathlib import Path


def _git_dir(cwd):
    cur = os.path.abspath(cwd)
    while True:
        g = os.path.join(cur, ".git")
        if os.path.isdir(g):
            return g, cur
        if os.path.isfile(g):
            try:
                with open(g, encoding="utf-8") as fh:
                    first = fh.readline().strip()
            except OSError:
                return None, cur
            if first.startswith("gitdir:"):
                d = first[len("gitdir:"):].strip()
                return (d if os.path.isabs(d) else os.path.join(cur, d)), cur
            return None, cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, None
        cur = parent


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import foundry_operating_mode as om
        import foundry_usage as fu

        cwd = payload.get("cwd") or os.getcwd()
        if om.resolve(fu.project_root(cwd)).name != om.TEAM:  # gn-17: no-op outside team mode
            return 0
        ds = fu.dataset_dir(None, start=cwd)
        sid = payload.get("session_id")
        if not ds or not isinstance(sid, str) or not sid:
            return 0
        gdir, wt = _git_dir(cwd)
        assign = fu.read_json(os.path.join(gdir, "foundry-assignment.json")) if gdir else {}
        fu.bind(ds, sid,
                operator=assign.get("operator") if isinstance(assign.get("operator"), str) else None,
                issue=assign.get("issue"),
                worktree=wt,
                agent_id=payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None)
    except Exception:  # fail-open by design
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
