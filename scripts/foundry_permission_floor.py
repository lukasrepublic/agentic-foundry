#!/usr/bin/env python3
"""foundry_permission_floor — the doctor's `permission-floor` drift comparison
(feat-foundry-doctor-permission-floor-check).

A PURE, imported library — no argparse, no `main()`, no `__main__` block, never in command
position (see the corresponding `not_invoked` row this atom adds to `docs/permission-floor.json`).
`scripts/foundry-doctor.py` is the only caller.

Compares the workspace's EFFECTIVE permission configuration — the union of `permissions.allow`,
`permissions.ask` and `permissions.deny` read from BOTH `<project_dir>/.claude/settings.json` and
`<project_dir>/.claude/settings.local.json`, origin-tracked — against the shipped
`docs/permission-floor.json` (AC-PFM-1..7, the sibling map). Discharges R6 of
`[[feat-foundry-permission-floor-map]]`: the harness's ask-to-allow persist option writes into
`.claude/settings.local.json` with no second trust dialog, silently converting a declared ceremony
`ask` into a standing local grant that never appears in a reviewed `settings.json` diff — this
module is what notices.

READ-ONLY, asserted at the OUTCOME level by the test suite (byte-identical fixture tree, a
`sys.addaudithook` write-event witness, and this module's own import closure carrying no
`subprocess`/`socket`/`http`/`urllib`/`requests`) rather than by enumerating forbidden call
shapes. Every settings file is `stat`-ed and read only if it is a regular file of at most 1 MiB;
ANY exception while resolving/reading/parsing/validating a settings file is caught and recorded as
a `settings-unreadable` finding for that file, never propagated (AC-DPF-2).

Non-disclosure is VALUE-scoped: no settings-derived string reaches a caller except (i) a rule that
actually COVERS a map entry (rendered verbatim, sanitized), (ii) a settings file's own path/label,
(iii) a tier key from the closed set allow|ask|deny. An `unclassified` rule is reported by
tool-name prefix + origin + tier + count only — its body is never returned (AC-DPF-2(c), AC-DPF-8).

A mismatch is ADVISORY, never RED. RED (raised as `FloorMalformed`) fires ONLY on a schema-invalid
`docs/permission-floor.json` — the one broken-install case (AC-DPF-4).
"""
from __future__ import annotations

import glob
import json
import os
import re
import stat

# --------------------------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------------------------- #

_MAX_FILE_BYTES = 1024 * 1024  # 1 MiB, both the settings files and the floor file itself
_PINNED_PLUGIN_ROOT_PREFIX = "~/.claude/plugins/cache/"
_GLOB_EXPANSION_CAP = 256

INTERPRETER_WORDS = frozenset({"python3", "python", "bash", "sh"})

_BASH_RULE_RE = re.compile(r"^Bash\((.+)\)$", re.DOTALL)
_PLUGIN_CACHE_FOLD_RE = re.compile(r"plugins/cache/[^/\s]+/foundry/[^/\s]+/")
_SCRIPTS_BASENAME_RE = re.compile(r"scripts/([^\s:)]+)")
_TOOL_PREFIX_VALID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# AC-ROST-5 render floor (control/ANSI), extended per AC-DPF-8 to zero-width/bidi code points.
_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f])")
_ZW_BIDI_RE = re.compile("[\u200B-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]")
_LINE_CAP = 200
_MAX_LINES_PER_CLASS = 50

SETTINGS_RELATIVE_PATHS = (
    os.path.join(".claude", "settings.json"),
    os.path.join(".claude", "settings.local.json"),
)

RANK = (
    "blanket-allow",
    "ask-shadowed-ceremony",
    "ask-shadowed",
    "deny-missing",
    "settings-unreadable",
    "stale-plugin-path",
    "allow-absent",
    "unclassified",
)
_ACTIONABLE_RANKS = frozenset(RANK[:6])
_INFORMATIONAL_RANKS = frozenset(RANK[6:])

CEREMONY_LEAD_LITERAL = "the front-authorization prompt is not firing"


class FloorMalformed(Exception):
    """Raised only for a schema-validation failure of docs/permission-floor.json (AC-DPF-4)."""


# --------------------------------------------------------------------------------------------- #
# render floor
# --------------------------------------------------------------------------------------------- #


def sanitize(s, cap=_LINE_CAP):
    """AC-ROST-5 extended to zero-width/bidi, plus a length cap (AC-DPF-8)."""
    if not isinstance(s, str):
        return s
    s = _CTRL_RE.sub("", s)
    s = _ZW_BIDI_RE.sub("", s)
    if len(s) > cap:
        s = s[:cap]
    return s


# --------------------------------------------------------------------------------------------- #
# settings-file reads (AC-DPF-2)
# --------------------------------------------------------------------------------------------- #


def load_settings_file(path):
    """Bounded, exception-tolerant read of one settings file.

    Returns a dict {"status": "absent"} | {"status": "unreadable"} |
    {"status": "ok", "rules": {"allow": [...], "ask": [...], "deny": [...]}}.
    NEVER raises.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {"status": "absent"}
    except Exception:
        return {"status": "unreadable"}
    try:
        if not stat.S_ISREG(st.st_mode):
            return {"status": "unreadable"}
        if st.st_size > _MAX_FILE_BYTES:
            return {"status": "unreadable"}
        with open(path, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8")
        doc = json.loads(text)
        if not isinstance(doc, dict):
            return {"status": "unreadable"}
        perms = doc.get("permissions", {})
        if perms is None:
            perms = {}
        if not isinstance(perms, dict):
            return {"status": "unreadable"}
        rules = {}
        for tier in ("allow", "ask", "deny"):
            if tier not in perms:
                # AC-DPF-2(b): a MISSING tier key is an empty tier, not a failure.
                rules[tier] = []
                continue
            val = perms[tier]
            # AC-DPF-2(b): a PRESENT-but-wrong-shaped value (string, dict, null, or an array
            # containing a non-string element) is settings-unreadable — only absence is tolerated.
            if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
                return {"status": "unreadable"}
            rules[tier] = val
        return {"status": "ok", "rules": rules}
    except Exception:
        return {"status": "unreadable"}


# --------------------------------------------------------------------------------------------- #
# floor-file load + schema validation (AC-DPF-4)
# --------------------------------------------------------------------------------------------- #


def validate_plugin_root_glob(glob_pat):
    """Validated BEFORE it is ever expanded. Raises FloorMalformed on any violation."""
    if not isinstance(glob_pat, str) or not glob_pat.startswith(_PINNED_PLUGIN_ROOT_PREFIX):
        raise FloorMalformed(
            f"plugin_root_glob must begin with {_PINNED_PLUGIN_ROOT_PREFIX!r}: {glob_pat!r}"
        )
    if any(seg == ".." for seg in glob_pat.split("/")):
        raise FloorMalformed(f"plugin_root_glob must not contain a .. segment: {glob_pat!r}")
    if "**" in glob_pat:
        raise FloorMalformed(f"plugin_root_glob must not contain **: {glob_pat!r}")
    if glob_pat.count("*") > 2:
        raise FloorMalformed(f"plugin_root_glob must contain at most two * characters: {glob_pat!r}")


def expand_plugin_root_glob(glob_pat, home=None, cap=_GLOB_EXPANSION_CAP):
    """Non-recursive expansion, capped. Assumes `glob_pat` already passed validation."""
    home = home or os.path.expanduser("~")
    pattern = home.rstrip("/") + glob_pat[1:]
    matches = sorted(glob.glob(pattern))
    dirs = [m for m in matches if os.path.isdir(m)]
    return dirs[:cap]


def load_permission_floor(plugin_root):
    """Returns (doc, status) where status is None (loaded) or "absent" (skip case).
    Raises FloorMalformed for any schema-validation failure of a PRESENT file."""
    path = os.path.join(plugin_root, "docs", "permission-floor.json")
    try:
        st = os.stat(path)
    except Exception:
        return None, "absent"
    if not stat.S_ISREG(st.st_mode):
        return None, "absent"
    if st.st_size > _MAX_FILE_BYTES:
        raise FloorMalformed("permission-floor.json exceeds 1 MiB")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise FloorMalformed(f"permission-floor.json unparseable: {type(e).__name__}: {e}") from e
    if not isinstance(doc, dict):
        raise FloorMalformed("permission-floor.json is not a JSON object")
    glob_pat = doc.get("plugin_root_glob")
    entries = doc.get("entries")
    if not isinstance(glob_pat, str) or not glob_pat:
        raise FloorMalformed("permission-floor.json missing plugin_root_glob")
    if not isinstance(entries, list) or not entries:
        raise FloorMalformed("permission-floor.json missing entries")
    for e in entries:
        if not isinstance(e, dict) or "rule" not in e or "tier" not in e:
            raise FloorMalformed("permission-floor.json entry missing rule/tier")
        if not isinstance(e["rule"], str) or not e["rule"]:
            raise FloorMalformed("permission-floor.json entry has a non-string/empty rule")
        if e["tier"] not in ("allow", "ask", "deny"):
            raise FloorMalformed(f"permission-floor.json entry has invalid tier {e['tier']!r}")
    validate_plugin_root_glob(glob_pat)
    return doc, None


# --------------------------------------------------------------------------------------------- #
# canonicalization + the covers relation (AC-DPF-3)
# --------------------------------------------------------------------------------------------- #


def _fold_body(body, home):
    body = body.strip()
    tokens = body.split(None, 1)
    if tokens and tokens[0] in INTERPRETER_WORDS and len(tokens) > 1:
        body = tokens[1]
    if body.startswith("~/"):
        body = home.rstrip("/") + body[1:]
    elif body.startswith("$HOME/"):
        body = home.rstrip("/") + body[len("$HOME"):]
    m = _PLUGIN_CACHE_FOLD_RE.search(body)
    if m:
        body = body[m.end():]
    return body


def _split_marker(body):
    """Returns (reach_prefix, is_prefix_rule)."""
    if body == "*":
        return "", True
    if body.endswith(":*"):
        return body[:-2], True
    if len(body) >= 2 and body[-1] == "*" and body[-2] in (" ", "/"):
        return body[:-2], True
    return body, False


def canonicalize(rule, home=None):
    """AC-DPF-3's ask/allow-direction fold. Returns None if the rule does not participate
    (does not match ^Bash\\((.+)\\)$)."""
    m = _BASH_RULE_RE.match(rule)
    if not m:
        return None
    home = home or os.path.expanduser("~")
    body = m.group(1)
    folded = _fold_body(body, home)
    reach, is_prefix = _split_marker(folded)
    is_blanket = is_prefix and (reach == "" or reach in INTERPRETER_WORDS)
    coverage_reach = "" if is_blanket else reach
    return {
        "raw": rule,
        "folded": folded,
        "reach": reach,
        "is_prefix": is_prefix,
        "is_blanket": is_blanket,
        "coverage_reach": coverage_reach,
    }


def _covers_canon(effective_c, map_c):
    if effective_c is None or map_c is None:
        return False
    if effective_c["is_prefix"]:
        return map_c["reach"].startswith(effective_c["coverage_reach"])
    return effective_c["reach"] == map_c["reach"]


def covers(effective_rule, map_rule, home=None):
    """Public conformance-tested API: does `effective_rule` (the broad rule) cover `map_rule`
    (the map entry) under AC-DPF-3's ask/allow fold? Proved equal to the sibling map suite's
    `_subsumes` on the shared table (AC-DPF-5(c))."""
    ca = canonicalize(effective_rule, home=home)
    cb = canonicalize(map_rule, home=home)
    return _covers_canon(ca, cb)


def canonicalize_identity(rule):
    """AC-DPF-3(b): the deny direction. No interpreter drop, no plugin-cache fold — only
    surrounding-whitespace normalization and the prefix-marker split."""
    m = _BASH_RULE_RE.match(rule)
    if not m:
        return None
    body = m.group(1).strip()
    reach, is_prefix = _split_marker(body)
    return {"raw": rule, "reach": reach, "is_prefix": is_prefix}


def deny_covers(effective_rule, map_rule):
    """AC-DPF-3(b): deny coverage requires EXACT reach-prefix equality; the fold is refused."""
    ca = canonicalize_identity(effective_rule)
    cb = canonicalize_identity(map_rule)
    if ca is None or cb is None:
        return False
    return ca["reach"] == cb["reach"]


def is_blanket_rule(rule, home=None):
    c = canonicalize(rule, home=home)
    return bool(c and c["is_blanket"])


# --------------------------------------------------------------------------------------------- #
# ceremony derivation (structural, no schema change — AC-DPF-8)
# --------------------------------------------------------------------------------------------- #


def is_ceremony_entry(map_entry):
    return map_entry["tier"] == "ask" and bool(_SCRIPTS_BASENAME_RE.search(map_entry["rule"]))


def _tool_prefix(rule):
    idx = rule.find("(")
    prefix = rule[:idx] if idx != -1 else rule
    if not _TOOL_PREFIX_VALID_RE.match(prefix):
        return "?"
    return prefix


# --------------------------------------------------------------------------------------------- #
# the comparison (AC-DPF-8)
# --------------------------------------------------------------------------------------------- #


def _effective_config(project_dir):
    """Returns (effective, unreadable_labels, any_file_exists) where `effective` is
    {"allow": [...], "ask": [...], "deny": [...]}, each element {"raw": rule, "label": file_label,
    "tier": tier}. `label` is one of SETTINGS_RELATIVE_PATHS — the only settings-derived path
    ever surfaced (AC-DPF-2(c))."""
    effective = {"allow": [], "ask": [], "deny": []}
    unreadable_labels = []
    any_exists = False
    for label in SETTINGS_RELATIVE_PATHS:
        path = os.path.join(project_dir, label)
        result = load_settings_file(path)
        if result["status"] == "absent":
            continue
        any_exists = True
        if result["status"] == "unreadable":
            unreadable_labels.append(label)
            continue
        for tier in ("allow", "ask", "deny"):
            for raw in result["rules"][tier]:
                effective[tier].append({"raw": raw, "label": label, "tier": tier})
    return effective, unreadable_labels, any_exists


def _classify(floor_doc, effective, unreadable_labels, home):
    entries = floor_doc["entries"]
    glob_pat = floor_doc["plugin_root_glob"]

    lines_by_class = {c: [] for c in RANK}
    counts = {c: 0 for c in RANK}

    ask_entries = [e for e in entries if e["tier"] == "ask"]
    allow_entries = [e for e in entries if e["tier"] == "allow"]
    deny_entries = [e for e in entries if e["tier"] == "deny"]

    canon_allow = []
    for eff in effective["allow"]:
        c = canonicalize(eff["raw"], home=home)
        if c is None:
            continue
        c = dict(c)
        c["_origin"] = eff
        canon_allow.append(c)

    blanket = [c for c in canon_allow if c["is_blanket"]]
    swallowed_ask_rules = set()
    if blanket:
        swallowed = ask_entries + deny_entries
        names = "; ".join(sanitize(e["rule"]) for e in swallowed)
        for c in blanket:
            lines_by_class["blanket-allow"].append(
                f"blanket-allow: {sanitize(c['raw'])!r} ({c['_origin']['label']}) blankets "
                f"ask/deny: {names} — narrow this rule."
            )
            counts["blanket-allow"] += 1
        swallowed_ask_rules = {e["rule"] for e in ask_entries}

    for e in ask_entries:
        if e["rule"] in swallowed_ask_rules:
            continue
        mc = canonicalize(e["rule"], home=home)
        covering = [c for c in canon_allow if _covers_canon(c, mc)]
        if not covering:
            continue
        klass = "ask-shadowed-ceremony" if is_ceremony_entry(e) else "ask-shadowed"
        # Render the CANONICALIZED (folded) form, not the raw settings-file text: a real effective
        # rule carries a machine-specific absolute home path + plugin-cache version segments that
        # can run well past the AC-DPF-8 200-char cap on its own; the fold collapses that noise to
        # the same short reach the covers() decision was actually made on ("a canonicalized rule
        # that covers a map entry", AC-DPF-2(c)(i)) — strictly less disclosure, not more. Grouped
        # by identical canonical value so a rule present in both settings files is named ONCE with
        # both origins, rather than repeating the text per origin.
        by_rule = {}
        for c in covering:
            by_rule.setdefault(c["folded"], set()).add(c["_origin"]["label"])
        origins = sorted(
            f"Bash({sanitize(folded)}) ({', '.join(sorted(labels))})"
            for folded, labels in by_rule.items()
        )
        lines_by_class[klass].append(
            f"{klass}: {sanitize(e['rule'])!r} shadowed by {'; '.join(origins)} — "
            "tighten/remove the covering allow rule."
        )
        counts[klass] += 1

    for e in deny_entries:
        covering = [eff for eff in effective["deny"] if deny_covers(eff["raw"], e["rule"])]
        if covering:
            continue
        lines_by_class["deny-missing"].append(
            f"deny-missing: {sanitize(e['rule'])!r} has no covering effective deny rule — "
            "add it to .claude/settings.json permissions.deny."
        )
        counts["deny-missing"] += 1

    for label in unreadable_labels:
        lines_by_class["settings-unreadable"].append(
            f"settings-unreadable: {label} failed to parse/validate — fix or remove it."
        )
        counts["settings-unreadable"] += 1

    try:
        dirs = expand_plugin_root_glob(glob_pat, home=home)
    except Exception:
        dirs = []
    if not dirs:
        lines_by_class["stale-plugin-path"].append(
            f"stale-plugin-path: glob {sanitize(glob_pat)!r} expands to no directory — "
            "reinstall/update the plugin."
        )
        counts["stale-plugin-path"] += 1
    else:
        wanted = set()
        for e in entries:
            wanted |= set(_SCRIPTS_BASENAME_RE.findall(e["rule"]))
        for name in sorted(wanted):
            if not any(os.path.isfile(os.path.join(d, "scripts", name)) for d in dirs):
                lines_by_class["stale-plugin-path"].append(
                    f"stale-plugin-path: scripts/{sanitize(name)} not found in any expansion "
                    "— reinstall/update the plugin."
                )
                counts["stale-plugin-path"] += 1

    for e in allow_entries:
        mc = canonicalize(e["rule"], home=home)
        covering = [c for c in canon_allow if _covers_canon(c, mc)]
        if covering:
            continue
        lines_by_class["allow-absent"].append(
            f"allow-absent: {sanitize(e['rule'])!r} has no covering effective allow rule "
            "(informational)."
        )
        counts["allow-absent"] += 1

    unclassified_counter = {}
    for tier in ("allow", "ask", "deny"):
        for eff in effective[tier]:
            if _BASH_RULE_RE.match(eff["raw"]):
                continue
            key = (_tool_prefix(eff["raw"]), eff["label"], tier)
            unclassified_counter[key] = unclassified_counter.get(key, 0) + 1
    for (prefix, label, tier), n in sorted(unclassified_counter.items()):
        lines_by_class["unclassified"].append(
            f"unclassified: {n} rule(s), tool {sanitize(prefix)!r}, {label} ({tier}) — "
            "body withheld (informational)."
        )
        counts["unclassified"] += n

    ceremony_shadowed = counts["ask-shadowed-ceremony"] > 0 or (
        bool(blanket) and any(is_ceremony_entry(e) for e in ask_entries)
    )

    return lines_by_class, counts, ceremony_shadowed


def _render(lines_by_class, counts, ceremony_shadowed, for_session_start):
    count_str = ", ".join(f"{k}={counts[k]}" for k in RANK)
    summary = f"permission-floor: {count_str}"
    if ceremony_shadowed:
        summary = f"{CEREMONY_LEAD_LITERAL} — {summary}"
    summary = sanitize(summary)

    lines = []
    for klass in RANK:
        if for_session_start and klass not in _ACTIONABLE_RANKS:
            continue
        cls_lines = lines_by_class[klass]
        shown = cls_lines[:_MAX_LINES_PER_CLASS]
        remainder = len(cls_lines) - len(shown)
        for ln in shown:
            lines.append(sanitize(ln))
        if remainder > 0:
            lines.append(sanitize(f"{klass}: +{remainder} more finding(s) truncated"))

    if for_session_start:
        info_count = counts["allow-absent"] + counts["unclassified"]
        lines.append(sanitize(
            f"{info_count} informational finding(s) (allow-absent, unclassified) — run "
            "`/foundry:doctor` (no --session-start) for detail"
        ))

    return summary, lines


def run_check(plugin_root, project_dir, home=None, for_session_start=False):
    """The whole comparison. Returns a dict:
    {"outcome": "skip"|"ok"|"advisory", "summary": str, "lines": [str, ...], "counts": {...}}.
    Raises FloorMalformed only for a schema-invalid, PRESENT docs/permission-floor.json."""
    home = home or os.path.expanduser("~")
    floor_doc, status = load_permission_floor(plugin_root)
    if status == "absent":
        return {
            "outcome": "skip",
            "summary": "permission-floor.json absent from the plugin tree (not applicable)",
            "lines": [],
            "counts": {},
        }

    effective, unreadable_labels, any_exists = _effective_config(project_dir)

    if not any_exists:
        line = sanitize(
            "no-configuration: neither .claude/settings.json nor .claude/settings.local.json "
            "exists. Remedy: run the pre-session bootstrap CLI to apply the permission floor."
        )
        return {"outcome": "advisory", "summary": line, "lines": [], "counts": {}}

    lines_by_class, counts, ceremony_shadowed = _classify(
        floor_doc, effective, unreadable_labels, home
    )
    summary, lines = _render(lines_by_class, counts, ceremony_shadowed, for_session_start)
    total = sum(counts.values())
    outcome = "ok" if total == 0 else "advisory"
    return {"outcome": outcome, "summary": summary, "lines": lines, "counts": dict(counts)}
