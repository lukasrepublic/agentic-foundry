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
    "spec-author": "opus",
    "pr-reviewer": "sonnet",
    "spec-reviewer": "sonnet",
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

NEW_AGENTS = {"app-engineer", "infra-engineer", "framework-engineer", "qa-engineer", "spec-author"}
EXISTING_AGENTS = {"pr-reviewer", "security-reviewer"}
NEW_REVIEWERS = {"spec-reviewer"}
CANONICAL_AGENTS = NEW_AGENTS | EXISTING_AGENTS | NEW_REVIEWERS

ROLE_TOOLS = {
    "pr-reviewer": frozenset({"Read", "Grep", "Glob"}),
    "security-reviewer": frozenset({"Read", "Grep", "Glob"}),
    "spec-reviewer": frozenset({"Read", "Grep", "Glob"}),
    "spec-author": frozenset({"Read", "Grep", "Glob", "Write", "Edit"}),
    "app-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "infra-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "framework-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "qa-engineer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
}


# ==================================================== workflow-agent-model-pins ==== #

ALLOWED_WORKFLOW_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable"})
KNOWN_FANOUT = ("audit:", "remediate:", "impl:", "verify:")

_LABEL_RE = re.compile(r"\blabel\s*:")
_MODEL_EXPR_RE = re.compile(r"\bmodel\s*:\s*([^,}\n]+)")
_QUOTED_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _workflow_files(base):
    return sorted(glob.glob(os.path.join(base, "workflows", "*.js")))


def evaluate_workflow_agent_model_pins(root):
    res = {"ac1": False, "ac2": False, "detail": ""}
    files = _workflow_files(root)
    if not files:
        res["detail"] = f"no workflows/*.js under {root}"
        return res

    ac1, ac2 = [], []
    for path in files:
        rel = os.path.relpath(path, root)
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if _LABEL_RE.search(line):
                m = _MODEL_EXPR_RE.search(line)
                if not m:
                    ac1.append(f"{rel}:{i}: agent() opts (label:) has no model: pin")
                else:
                    lits = _QUOTED_RE.findall(m.group(1))
                    if not lits:
                        ac1.append(f"{rel}:{i}: model: expr has no quoted alias literal ({m.group(1).strip()!r})")
                    elif any(l not in ALLOWED_WORKFLOW_ALIASES for l in lits):
                        ac1.append(f"{rel}:{i}: model: expr has a non-alias literal ({lits!r})")
            for mm in _MODEL_EXPR_RE.finditer(line):
                for val in _QUOTED_RE.findall(mm.group(1)):
                    if val in ALLOWED_WORKFLOW_ALIASES:
                        continue
                    why = ("dated/full model id" if _DATED_ID_RE.match(val)
                           else "inherit-all '*'" if val == "*" else "non-alias value")
                    ac2.append(f"{rel}:{i}: {why}: {val!r}")

    res["ac1"] = not ac1
    res["ac2"] = not ac2
    res["detail"] = "; ".join(ac1 + ac2) or "ok"
    return res
