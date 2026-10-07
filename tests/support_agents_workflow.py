"""tests/support_agents_workflow.py — pure functions ported from the (now-deleted)
`scripts/foundry_checks/{persona-model-selection,reference-agents,workflow-agent-model-pins}.py`.

These were pure, self-contained parsers/predicates over `.claude-plugin/plugin.json` +
`agents/*.md` + `workflows/*.js` — NOT production runtime code (unlike
`scripts/foundry_env_isolation.py`, which was relocated because real hooks/skills consume it at
runtime). Nothing in the shipped plugin imports these at runtime, so they live alongside the
tests that are their only consumer now, rather than in `scripts/`.
"""
from __future__ import annotations

import glob
import json
import os
import re

# ==================================================== persona-model-selection ==== #

ROLE_MODEL = {
    "security-reviewer": "opus",
    "pr-reviewer": "sonnet",
    "app-engineer": "sonnet",
    "framework-engineer": "sonnet",
    "infra-engineer": "sonnet",
    "qa-engineer": "sonnet",
}

ALLOWED_MODEL_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "inherit"})
_DATED_ID_RE = re.compile(r"^(us\.)?(anthropic\.)?claude-[a-z0-9]+-[0-9].*$", re.IGNORECASE)


def _fm_scalar(fm, key):
    m = re.search(rf"(?m)^{re.escape(key)}:\s*(.*)$", fm)
    return m.group(1).strip() if m else None


def parse_model(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return None, False
    val = _fm_scalar(m.group(1), "model")
    if val is not None:
        val = val.strip().strip("'\"")
    return val, True


def manifest_agent_names(manifest_obj):
    out = []
    for entry in manifest_obj.get("agents") or []:
        b = os.path.basename(str(entry))
        if b.endswith(".md"):
            b = b[:-3]
        out.append((b, str(entry)))
    return out


def alias_hygiene(model):
    if model is None:
        return True, ""
    if model in ALLOWED_MODEL_ALIASES:
        return True, ""
    if _DATED_ID_RE.match(model):
        return False, f"dated/full model id: {model!r} (use a family alias)"
    if model in ("*", "'*'", '"*"'):
        return False, "inherit-all '*' is not an allowed model alias"
    return False, f"non-alias model value: {model!r}"


def evaluate_persona_model_selection(root):
    res = {"ac1": False, "ac2": False, "detail": ""}
    manifest = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.isfile(manifest):
        res["detail"] = f"manifest absent under {root}"
        return res
    try:
        obj = json.load(open(manifest, encoding="utf-8"))
    except Exception as e:
        res["detail"] = f"manifest unreadable: {e}"
        return res

    ac1, ac2, seen = [], [], set()
    for name, entry in manifest_agent_names(obj):
        seen.add(name)
        p = os.path.normpath(os.path.join(root, entry))
        if not os.path.isfile(p):
            ac1.append(f"{name}: registered file missing ({entry})")
            continue
        model, _ = parse_model(open(p, encoding="utf-8").read())
        ok2, why2 = alias_hygiene(model)
        if not ok2:
            ac2.append(f"{name}: {why2}")
        expected = ROLE_MODEL.get(name)
        if expected is None:
            ac1.append(f"{name}: registered agent outside the known role map")
        elif model != expected:
            ac1.append(f"{name}: model={model!r} expected {expected!r}")
    for name in ROLE_MODEL:
        if name not in seen:
            ac1.append(f"{name}: mapped persona not registered")

    res["ac1"] = not ac1
    res["ac2"] = not ac2
    res["detail"] = "; ".join(ac1 + ac2) or "ok"
    return res


# ==================================================== reference-agents ==== #

NEW_AGENTS = {"app-engineer", "infra-engineer", "framework-engineer", "qa-engineer"}
EXISTING_AGENTS = {"pr-reviewer", "security-reviewer"}
CANONICAL_AGENTS = NEW_AGENTS | EXISTING_AGENTS

ROLE_TOOLS = {
    "pr-reviewer": frozenset({"Read", "Grep", "Glob"}),
    "security-reviewer": frozenset({"Read", "Grep", "Glob"}),
    "app-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "infra-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "framework-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "qa-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
}
