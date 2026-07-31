"""tests/support_statusline_wiring.py — the `evaluate()` predicate ported from the (now-deleted,
now-deleted) `scripts/foundry_checks/statusline-wiring-live.py`.

A pure settings.json / plugin-cache reader (never production runtime code), so it lives alongside
the test that is its only consumer now.
"""
from __future__ import annotations

import json
import os
import re

_VER_SEG_RE = re.compile(r"/cache/([^/]+)/foundry/(\d+\.\d+\.\d+)/")
_FIX = ("re-wire statusLine through the shipped version-agnostic wrapper "
        "(.claude/hooks/foundry-statusline.sh — /foundry:init §9 / AC-SLW)")


def _pad(v):
    return tuple(int(x) for x in v.split("."))


def _settings_command(ws_root):
    for name in ("settings.local.json", "settings.json"):
        p = os.path.join(ws_root, ".claude", name)
        if not os.path.isfile(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        sl = data.get("statusLine")
        if isinstance(sl, dict) and isinstance(sl.get("command"), str) and sl["command"].strip():
            return sl["command"].strip(), p
    return None, None


def _script_path(command, ws_root):
    for tok in command.replace('"', " ").replace("'", " ").split():
        cand = tok.replace("${CLAUDE_PROJECT_DIR}", ws_root).replace("$CLAUDE_PROJECT_DIR", ws_root)
        cand = os.path.expanduser(cand)
        if "/" in cand:
            return cand
    return None


def _highest_installed(cache_root):
    import glob as g
    best = None
    for d in g.glob(os.path.join(cache_root, "*", "foundry", "*")):
        v = os.path.basename(d)
        if re.match(r"^\d+\.\d+\.\d+$", v) and os.path.isdir(d):
            if best is None or _pad(v) > _pad(best):
                best = v
    return best


def evaluate(ws_root, cache_root):
    command, src = _settings_command(ws_root)
    if command is None:
        return None, "no statusLine configured — not applicable"
    path = _script_path(command, ws_root)
    if not path:
        return False, f"statusLine command has no resolvable script path ({src})"
    m = _VER_SEG_RE.search(path)
    if m:
        pinned = m.group(2)
        highest = _highest_installed(cache_root)
        if highest and _pad(pinned) < _pad(highest):
            return False, (f"STALE WIRING: statusLine pins plugin-cache version {pinned} but "
                           f"{highest} is installed — the pinned dir is gone/going; {_FIX}")
    if not os.path.isfile(path):
        return False, f"configured statusLine script does not resolve: {path} ({src}); {_FIX}"
    if not os.access(path, os.X_OK):
        return False, f"configured statusLine script is not executable: {path}"
    detail = f"statusLine resolves ({path})"
    if m:
        detail += (f" — NOTE: version-keyed cache path ({m.group(2)}) will stale on the next "
                   f"upgrade; {_FIX}")
    return True, detail
