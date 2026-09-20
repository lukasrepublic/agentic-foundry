#!/usr/bin/env python3
"""foundry_permission_floor — shared, read-only settings-reading and permission-rule-comparison
primitives (feat-foundry-doctor-permission-floor-check; AC-PFM-1..7, the sibling map).

A PURE, imported library — no argparse, no `main()`, no `__main__` block, never in command
position (see the corresponding `not_invoked` row this atom adds to `docs/permission-floor.json`).

r4-residuals AC-RES-3: this module's own top-level drift-comparison ENTRY POINT (`run_check`, plus
the rendering/effective-config glue only it used) was DEAD CODE — the doctor probe that called it
was already deleted by subtraction-wave (`scripts/foundry-doctor.py`'s own comment on
`_load_permission_floor_module` names the replacement:
`feat-foundry-authorization-capability-preflight-at-dispatch`, AC-CPD-4, which carries the drift
signal on the doctor's `permissions-policy` line instead). That entry point, and everything ONLY it
used, is deleted here. What remains, and who reuses it:
  * `load_settings_file` (the audited `permissions` read) — `foundry-capability-preflight.py` and
    `foundry-permissions-compile.py` both reuse it (AC-SGP-2), never re-implementing their own
    stat/size-cap/JSON-parse read.
  * `load_settings_env` (the sibling `env`-block read, AC-RES-3) — the doctor's `agent-teams`
    probe and `foundry_command_deck_watch`'s advisory-header gate both reuse it.
  * `sanitize` — `foundry-capability-preflight.py` reuses it as its own render floor.
  * `covers` / `canonicalize` / `canonicalize_identity` / `deny_covers` / `is_blanket_rule` / the
    `_classify` engine and its `RANK` vocabulary — the shared subsumption logic
    `tests/test_floor_drift_classification.py` runs differentially against the Node twin
    (`cli/src/permissionFloor.mjs`) over `tests/fixtures/floor-drift-corpus.json`; this is what
    keeps the two implementations' classification agreeing "by construction" rather than by
    comment.

Non-disclosure is VALUE-scoped: no settings-derived string reaches a caller except (i) a rule that
actually COVERS a map entry (rendered verbatim, sanitized), (ii) a settings file's own path/label,
(iii) a tier key from the closed set allow|ask|deny. An `unclassified` rule is reported by
tool-name prefix + origin + tier + count only — its body is never returned (AC-DPF-2(c), AC-DPF-8).
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
_GLOB_EXPANSION_CAP = 256

INTERPRETER_WORDS = frozenset({"python3", "python", "bash", "sh"})

_BASH_RULE_RE = re.compile(r"^Bash\((.+)\)$", re.DOTALL)
_PLUGIN_CACHE_FOLD_RE = re.compile(r"plugins/cache/[^/\s]+/foundry/[^/\s]+/")
_SCRIPTS_BASENAME_RE = re.compile(r"scripts/([^\s:)]+)")
_TOOL_PREFIX_VALID_RE = re.compile(r"\A[A-Za-z0-9_-]{1,32}\Z")

# AC-ROST-5 render floor (control/ANSI), extended per AC-DPF-8 to zero-width/bidi code points.
# AC-DPF-8's normative text is a floor ("extended to ... U+200B-U+200F, U+202A-U+202E,
# U+2066-U+2069, U+FEFF") \u2014 R4 (PR #60 review) widens this superset-only with the Arabic Letter
# Mark (U+061C, also bidi-class-affecting) and the Unicode line/paragraph separators (U+2028,
# U+2029), which are equally render-hostile and were not in the originally enumerated set. Strictly
# additive: every rule the AC-DPF-8 spec text requires stripped is still stripped.
_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f])")
_ZW_BIDI_RE = re.compile("[\u061C\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]")
_LINE_CAP = 200

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
    # AC-FDC-1 / AC-FDC-2, appended rather than inserted. Two reasons, and they agree:
    #
    # SUBSTANTIVE — both are INFORMATIONAL, on allow-absent's own reasoning. An absent `allow` is
    # informational because its effect is MORE prompting, never less. An absent `ask` is the same:
    # without the declaration the tool either falls to a broader allow — which is ask-shadowed, an
    # actionable class that already fires — or carries no grant at all and prompts or refuses.
    # Absent-ask alone opens nothing. A tier-conflict rule is PRESENT and deliberately placed by
    # the operator; nothing is open there either. Neither belongs in the actionable band that
    # reaches the session-start banner.
    #
    # MECHANICAL — the shipped suite pins the whole RANK order as one contiguous summary string
    # ("blanket-allow=N, … allow-absent=N, unclassified=N"), and that suite is this atom's
    # regression oracle and denied scope. Appending keeps _ACTIONABLE_RANKS byte-identical, so
    # session-start output is unchanged, and _INFORMATIONAL_RANKS picks these up by slice.
    "ask-absent",
    "tier-conflict",
)
_ACTIONABLE_RANKS = frozenset(RANK[:6])
_INFORMATIONAL_RANKS = frozenset(RANK[6:])


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


def load_settings_env(paths):
    """Bounded, exception-tolerant read of the top-level `env` object, folded across MULTIPLE
    settings files in ASCENDING PRECEDENCE order (`paths` is ordered least-specific to
    most-specific; a key present in a LATER file overwrites the same key from an earlier one) --
    the same read discipline `load_settings_file` uses (regular file, <= 1 MiB, UTF-8 JSON), but
    reading the `env` sibling key instead of `permissions`. NEVER raises: an absent, oversized, or
    unparseable file at ANY position in `paths` is simply skipped (not fatal to the fold, and not
    reported) -- this is a settings CONVENIENCE read for an advisory flag (AC-RES-3), not the
    audited `permissions` read above.

    AC-RES-3: the doctor's `agent-teams` probe and `foundry_command_deck_watch`'s advisory-header
    gate both call this instead of maintaining their own local copy of the same bounded read."""
    merged: dict = {}
    for path in paths:
        try:
            st = os.stat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_FILE_BYTES:
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            doc = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        env = doc.get("env")
        if isinstance(env, dict):
            merged.update(env)
    return merged


# --------------------------------------------------------------------------------------------- #
# plugin-root glob expansion (AC-DPF-4) — `_classify`'s own `stale-plugin-path` class still calls
# `expand_plugin_root_glob` against a `floor_doc["plugin_root_glob"]` the CALLER already validated
# (`tests/test_floor_drift_classification.py` loads `docs/permission-floor.json` directly via
# `json.load`, never through a schema-validating loader here — r4-residuals AC-RES-3 deleted this
# module's OWN schema-validating loader, `load_permission_floor`/`validate_plugin_root_glob`, along
# with `FloorMalformed` and `MAP_SCHEMA_VERSION` — those existed only to feed the dead `run_check`
# entry point; the map's schema is still validated live by the Node twin's own
# `cli/src/permissionFloor.mjs::loadMap`, unaffected by this deletion).
# --------------------------------------------------------------------------------------------- #


def expand_plugin_root_glob(glob_pat, home=None, cap=_GLOB_EXPANSION_CAP):
    """Non-recursive expansion, capped. Assumes `glob_pat` is already a well-formed
    `~/.claude/plugins/cache/...` glob (the caller's concern now — see the section note above)."""
    home = home or os.path.expanduser("~")
    pattern = home.rstrip("/") + glob_pat[1:]
    matches = sorted(glob.glob(pattern))
    dirs = [m for m in matches if os.path.isdir(m)]
    return dirs[:cap]


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
    # R2 (PR #60 review): a paren-less rule (e.g. a secret-shaped bare token) must NEVER be
    # emitted as a "tool prefix" — with no "(" there is no tool name to extract, so it is
    # unconditionally unclassified-body-withheld.
    idx = rule.find("(")
    if idx == -1:
        return "?"
    prefix = rule[:idx]
    if not _TOOL_PREFIX_VALID_RE.match(prefix):
        return "?"
    return prefix


# --------------------------------------------------------------------------------------------- #
# the comparison (AC-DPF-8) — `_classify` is the shared classification engine
# `tests/test_floor_drift_classification.py` runs differentially against the Node twin (AC-FDC-6);
# it takes an already-loaded `floor_doc` and an already-built `effective` config directly, rather
# than loading either itself, so a caller can feed it fixture data with no filesystem at all.
# `_effective_config` — the settings-file-reading glue that used to build `effective` for
# `run_check` specifically — was deleted alongside it (r4-residuals AC-RES-3): every current
# caller builds `effective` its own way (the differential test reads `tests/fixtures/floor-drift-
# corpus.json` directly).
# --------------------------------------------------------------------------------------------- #


def _classify(floor_doc, effective, unreadable_labels, home):
    entries = floor_doc["entries"]
    glob_pat = floor_doc["plugin_root_glob"]

    lines_by_class = {c: [] for c in RANK}
    counts = {c: 0 for c in RANK}
    # AC-FDC-6 — the structured surface the differential compares. Rendered lines are prose and
    # differ between the two implementations by design; the RULE SET named per class is the thing
    # that must agree. Holds MAP-derived rule text (plus, for blanket-allow, the same folded form
    # the line already renders), so it discloses nothing the value-scoped model does not already.
    rules_by_class = {c: [] for c in RANK}

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
            # R1 (PR #60 review): render the canonicalized/folded form, not the raw settings-file
            # text — the raw text can carry an arbitrary free-text prefix ahead of the
            # plugins/cache segment that the fold discards; the folded form is the same short
            # reach the blanket decision was actually made on (mirrors the ask-shadowed render
            # below).
            lines_by_class["blanket-allow"].append(
                f"blanket-allow: Bash({sanitize(c['folded'])}) ({c['_origin']['label']}) blankets "
                f"ask/deny: {names} — narrow this rule."
            )
            counts["blanket-allow"] += 1
            rules_by_class["blanket-allow"].append(f"Bash({c['folded']})")
        swallowed_ask_rules = {e["rule"] for e in ask_entries}

    # AC-FDC-2, computed BEFORE the absence classes because it suppresses them. A map entry whose
    # exact rule text sits in a tier other than the one the map declares is PRESENT — deliberately
    # placed — so reporting it absent would be false, and would invite a consumer to add a second
    # copy. Exact text only, matching the Node twin: covers() needs a ':*'-terminated body to match
    # at all, so a cross-tier duplicate of a non-wildcard rule is invisible to the shadowing tests
    # too, which is the gap this class closes. AC-FDC-3: the origin label is carried, because the
    # effective set unions the tracked settings.json with the untracked settings.local.json and a
    # consumer must be able to tell which one satisfied the rule.
    conflicted = set()
    for e in entries:
        for tier in ("allow", "ask", "deny"):
            if tier == e["tier"]:
                continue
            found = [eff for eff in effective[tier] if eff["raw"] == e["rule"]]
            if not found:
                continue
            conflicted.add(e["rule"])
            labels = ", ".join(sorted({eff["label"] for eff in found}))
            lines_by_class["tier-conflict"].append(
                f"tier-conflict: {sanitize(e['rule'])!r} is declared {e['tier']!r} by the floor "
                f"but present in {tier!r} ({labels}) — present and deliberate, so not reported "
                "absent; resolve which tier you want it in."
            )
            counts["tier-conflict"] += 1
            rules_by_class["tier-conflict"].append(e["rule"])

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
        rules_by_class[klass].append(e["rule"])

    # AC-FDC-1 — the exact mirror of deny-missing below, against the `ask` tier, which had no
    # absence class at all. Measured before this landed: an empty effective set yielded 46 findings
    # and named none of the 16 ask entries. Note what it does NOT consult: effective["allow"]. An
    # ask entry covered by a broad allow is already named by ask-shadowed, and whether that allow
    # defeats the ask at match time is the precedence question this module abstains from (R3) — so
    # absence and shadowing are reported as the two independent facts they are.
    for e in ask_entries:
        if e["rule"] in conflicted:
            continue
        covering = [
            eff
            for eff in effective["ask"]
            if eff["raw"] == e["rule"] or covers(eff["raw"], e["rule"], home=home)
        ]
        if covering:
            continue
        lines_by_class["ask-absent"].append(
            f"ask-absent: {sanitize(e['rule'])!r} has no covering effective ask rule "
            "(informational)."
        )
        counts["ask-absent"] += 1
        rules_by_class["ask-absent"].append(e["rule"])

    for e in deny_entries:
        if e["rule"] in conflicted:
            continue
        covering = [eff for eff in effective["deny"] if deny_covers(eff["raw"], e["rule"])]
        if covering:
            continue
        lines_by_class["deny-missing"].append(
            f"deny-missing: {sanitize(e['rule'])!r} has no covering effective deny rule — "
            "add it to .claude/settings.json permissions.deny."
        )
        counts["deny-missing"] += 1
        rules_by_class["deny-missing"].append(e["rule"])

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
        if e["rule"] in conflicted:
            continue
        mc = canonicalize(e["rule"], home=home)
        covering = [c for c in canon_allow if _covers_canon(c, mc)]
        if covering:
            continue
        lines_by_class["allow-absent"].append(
            f"allow-absent: {sanitize(e['rule'])!r} has no covering effective allow rule "
            "(informational)."
        )
        counts["allow-absent"] += 1
        rules_by_class["allow-absent"].append(e["rule"])

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

    # AC-FDC-7 — QUALIFIED, not suppressed. covers() returns false for a body that does not end
    # ':*', so under `Bash(*)` every map entry still classifies absent: a consumer could converge
    # all 62 rules and report success over a floor that rule defeats entirely. Suppressing the rest
    # would hide the gap; marking them names both facts at once — here is the gap, and here is why
    # closing it changes nothing until this rule is narrowed.
    if blanket:
        blanket_names = "; ".join(sorted({f"Bash({sanitize(c['folded'])})" for c in blanket}))
        for klass in RANK:
            if klass == "blanket-allow":
                continue
            lines_by_class[klass] = [
                f"{ln} [qualified by blanket allow {blanket_names}]"
                for ln in lines_by_class[klass]
            ]

    ceremony_shadowed = counts["ask-shadowed-ceremony"] > 0 or (
        bool(blanket) and any(is_ceremony_entry(e) for e in ask_entries)
    )

    rules_by_class = {k: sorted(v) for k, v in rules_by_class.items()}
    return lines_by_class, counts, ceremony_shadowed, rules_by_class
