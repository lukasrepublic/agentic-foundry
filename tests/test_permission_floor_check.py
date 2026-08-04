"""tests/test_permission_floor_check.py — feat-foundry-doctor-permission-floor-check (AC-DPF-1..8).

The live seam for the doctor's `permission-floor` probe. Drives `scripts/foundry_permission_floor.py`
(the pure comparison module) as a function of an explicit `(plugin_root, project_dir)` pair over
throwaway `tmp_path` fixture trees — never the real tree implicitly — and drives
`scripts/foundry-doctor.py` both directly (`check_permission_floor`) and as a real subprocess where
the CLI's own exit code / `--session-start` fail-open behaviour is the point.

Test names are the acceptance contract's `-k` selectors; each named case is its own test function —
`pytest -k` only proves "everything matched passed", never "each named case exists"
(assert-on-structure-not-substrings / anti-tautology discipline).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from conftest import REPO_ROOT, load_module

pf = load_module("scripts/foundry_permission_floor.py", "foundry_permission_floor")
doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor")

DOCTOR_CLI = os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py")
DEFAULT_GLOB = "~/.claude/plugins/cache/*/foundry/*"

_EXISTING_PROBE_NAMES = (
    "manifest", "hooks", "skills-frontmatter", "stack-profile-lock", "operator-registry",
    "control-plane",
)


# ================================================================================================ #
# fixture helpers
# ================================================================================================ #
def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def _perms(allow=None, ask=None, deny=None):
    return {"permissions": {"allow": list(allow or []), "ask": list(ask or []), "deny": list(deny or [])}}


def _project_dir(base, settings=None, settings_local=None):
    base = str(base)
    os.makedirs(base, exist_ok=True)
    if settings is not None:
        _write_json(os.path.join(base, ".claude", "settings.json"), settings)
    if settings_local is not None:
        _write_json(os.path.join(base, ".claude", "settings.local.json"), settings_local)
    return base


def _plugin_root(base, entries, glob_pat=DEFAULT_GLOB, not_invoked=None):
    base = str(base)
    doc = {
        "schema_version": 1,
        "plugin_root_glob": glob_pat,
        "generated_for_plugin_version": "1.1.0",
        "entries": entries,
        "not_invoked": not_invoked or [],
    }
    _write_json(os.path.join(base, "docs", "permission-floor.json"), doc)
    return base


def _functional_plugin_root(base, floor_doc=None, malformed_floor=False):
    """A LIGHT (not full-repo-copy) plugin tree with everything the doctor's OTHER probes need to
    pass trivially, plus the real scripts/ tree (so foundry_authz / foundry_control_plane /
    foundry_permission_floor all import), for tests that must drive the doctor CLI end-to-end."""
    base = str(base)
    os.makedirs(os.path.join(base, ".claude-plugin"), exist_ok=True)
    _write_json(os.path.join(base, ".claude-plugin", "plugin.json"), {"name": "foundry", "version": "0.0.0-test"})
    _write_json(os.path.join(base, "hooks", "hooks.json"), {})
    shutil.copytree(os.path.join(REPO_ROOT, "scripts"), os.path.join(base, "scripts"))
    os.makedirs(os.path.join(base, "docs"), exist_ok=True)
    if malformed_floor:
        with open(os.path.join(base, "docs", "permission-floor.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json at all")
    elif floor_doc is not None:
        _write_json(os.path.join(base, "docs", "permission-floor.json"), floor_doc)
    else:
        shutil.copyfile(
            os.path.join(REPO_ROOT, "docs", "permission-floor.json"),
            os.path.join(base, "docs", "permission-floor.json"),
        )
    return base


def _run_doctor(project_dir, *extra_args, plugin_root=None):
    e = dict(os.environ)
    e["CLAUDE_PROJECT_DIR"] = project_dir
    e["CLAUDE_PLUGIN_ROOT"] = plugin_root or REPO_ROOT
    return subprocess.run(
        [sys.executable, DOCTOR_CLI, *extra_args], capture_output=True, text=True, timeout=30, env=e,
    )


def _seed_minimal_operator_registry(project_dir):
    d = os.path.join(project_dir, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "foundry-operators.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "operators": {
            "op_test": {"name": "T", "github": "t", "added_at": "2026-01-01"}}}, f)


def _line_for(stdout, probe_name):
    for line in stdout.splitlines():
        if f"] {probe_name}:" in line:
            return line
    raise AssertionError(f"no line for probe {probe_name!r} in doctor output:\n{stdout}")


def _tree_snapshot(root):
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                snap[p] = hashlib.sha256(f.read()).hexdigest()
    return snap


def _entries_digest(entries):
    canon = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _seed_cache_dir(home, plugin="agentic-foundry", version="1.0.0", scripts=()):
    """Materializes a directory the DEFAULT_GLOB pattern actually expands to, so tests whose point
    is NOT stale-plugin-path don't spuriously trip the glob-expands-to-nothing finding."""
    d = os.path.join(home, ".claude", "plugins", "cache", plugin, "foundry", version, "scripts")
    os.makedirs(d, exist_ok=True)
    for name in scripts:
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n")
    return d


def _import_map_suite():
    path = os.path.join(REPO_ROOT, "tests", "test_permission_floor_map.py")
    spec = importlib.util.spec_from_file_location("_pfm_suite_for_conformance", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# the canonical-serialization SHA-256 of docs/permission-floor.json's `entries` array (AC-DPF-7's
# R8 tripwire). A legitimate change to `entries` MUST update this literal in the same reviewed diff
# — which is what this edit is.
#
# UPDATED for feat-foundry-wizard-attach-repo-flow (coordinator reconciliation, PR #62). The map
# grew by exactly TWO rows and lost none; the delta was enumerated entry-by-entry against
# origin/main before this digest was re-pinned (60 -> 62 entries):
#
#   + Bash(.../foundry_repo_attach.py attach:*)   tier `ask`
#   + Bash(.../foundry_repo_attach.py create:*)   tier `ask`
#
# `ask`, not `allow`, for both: attach writes the manifest + gitignore pairing and hands the new row
# to reconcile (clone/fetch egress), and create invokes `gh repo create` under the operator's
# identity — a workspace mutation with network reach and a remote-resource creation respectively, so
# each prompts per run and the preview is the consent surface. Nothing was removed and nothing
# re-tiered. `docs/permission-floor.json` is contract-DENIED to the atom's implementer (a model may
# not edit its own confinement), so both the map rows and this re-pin are coordinator work, the same
# shape as PR #59's `c3406e5`.
MERGE_BASE_ENTRIES_DIGEST = "32be6c67caf2c85288c68bdcd303bc25c935d941a5ae602a91a951c1ddad5dbd"


# ================================================================================================ #
# AC-DPF-1 — registration, and `advisory` is a distinct, non-failing, non-XX-bearing outcome
# ================================================================================================ #
_PROBE_RUN_RE = re.compile(r'_run\(\s*"([a-z0-9-]+)"')


def _doctor_source():
    with open(os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py"), encoding="utf-8") as f:
        return f.read()


def test_probe_is_registered_and_advisory_is_a_distinct_outcome(tmp_path):
    src = _doctor_source()
    ids = _PROBE_RUN_RE.findall(src)
    assert "permission-floor" in ids

    assert doctor.ADVISORY is not True
    assert doctor.ADVISORY is not False
    assert doctor.ADVISORY is not None

    proj = str(tmp_path / "proj")
    _seed_minimal_operator_registry(proj)  # no settings files at all -> the no-configuration advisory
    r = _run_doctor(proj, plugin_root=REPO_ROOT)
    assert r.returncode == 0
    assert "DOCTOR-GREEN" in r.stdout
    line = _line_for(r.stdout, "permission-floor")
    mark = line.strip().split("]", 1)[0].lstrip("[").strip()
    assert mark not in ("skip", "ok", "XX")
    assert "XX" not in line[: line.index("]") + 1]


def test_existing_probes_are_unchanged():
    src = _doctor_source()
    for name in _EXISTING_PROBE_NAMES:
        assert re.search(r'_run\(\s*"%s"' % re.escape(name), src), \
            f"registration literal changed/missing for {name!r}"

    # The real repo's own tree/project — exactly what the operator-invoked run drives — must still
    # be DOCTOR-GREEN, and none of the six pre-existing probes may render the new advisory mark.
    r = _run_doctor(REPO_ROOT, plugin_root=REPO_ROOT)
    assert "DOCTOR-GREEN" in r.stdout
    for name in _EXISTING_PROBE_NAMES:
        line = _line_for(r.stdout, name)
        mark = line.strip().split("]", 1)[0].lstrip("[").strip()
        assert mark in ("ok", "skip"), f"{name} probe outcome changed: {line!r}"
        assert mark != "adv"


def test_advisory_never_collapses_into_skip_or_ok(tmp_path):
    proj = str(tmp_path / "proj")
    _seed_minimal_operator_registry(proj)
    r = _run_doctor(proj, plugin_root=REPO_ROOT)
    line = _line_for(r.stdout, "permission-floor")
    mark = line.strip().split("]", 1)[0].lstrip("[").strip()
    assert mark not in ("skip", "ok", "XX")
    assert r.returncode == 0
    assert "DOCTOR-GREEN" in r.stdout


# ================================================================================================ #
# AC-DPF-2 — both settings files, read-only at the outcome level, VALUE-scoped non-disclosure
# ================================================================================================ #
def test_effective_config_unions_both_settings_files(tmp_path):
    proj = _project_dir(tmp_path / "proj", settings=_perms(allow=["Bash(a:*)"]), settings_local=_perms(ask=["Bash(b:*)"]))
    effective, unreadable, any_exists = pf._effective_config(proj)
    assert any_exists
    assert unreadable == []
    assert "Bash(a:*)" in {e["raw"] for e in effective["allow"]}
    assert "Bash(b:*)" in {e["raw"] for e in effective["ask"]}
    assert {e["label"] for e in effective["allow"]} == {os.path.join(".claude", "settings.json")}
    assert {e["label"] for e in effective["ask"]} == {os.path.join(".claude", "settings.local.json")}


def test_only_the_permissions_keys_are_consumed(tmp_path):
    secret_val = "sk-THISISASECRETVALUE1234567890"
    doc = {
        "permissions": {"allow": ["Bash(x:*)"], "ask": [], "deny": []},
        "env": {"OPENAI_API_KEY": secret_val},
        "apiKeyHelper": "some-helper-script-path",
        "enabledPlugins": {"foo": True},
    }
    proj = _project_dir(tmp_path / "proj", settings=doc)
    plugin_root = _plugin_root(tmp_path / "plugin", [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}])
    result = pf.run_check(plugin_root, proj, home=str(tmp_path / "home"))
    blob = result["summary"] + "\n".join(result["lines"])
    assert secret_val not in blob
    assert "apiKeyHelper" not in blob
    assert "some-helper-script-path" not in blob
    assert "enabledPlugins" not in blob


def test_no_settings_derived_string_escapes(tmp_path):
    cred_rule = "Read(token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789)"
    ansi_rule = "Write(\x1b[31mDANGER\x1b[0m/etc/passwd)"
    proj = _project_dir(tmp_path / "proj", settings=_perms(allow=[cred_rule, ansi_rule]))
    plugin_root = _plugin_root(tmp_path / "plugin", [{"rule": "Bash(anything:*)", "tier": "allow", "rationale": "r"}])
    result = pf.run_check(plugin_root, proj, home=str(tmp_path / "home"))
    blob = result["summary"] + "\n".join(result["lines"])
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in blob
    assert "\x1b" not in blob
    assert "DANGER" not in blob
    assert "/etc/passwd" not in blob
    assert any(l.startswith("unclassified:") for l in result["lines"])


def test_sanitize_strips_the_widened_zero_width_bidi_set(tmp_path):
    """R4 (PR #60 review): the AC-DPF-8 zero-width/bidi floor is widened, superset-only, to also
    neutralize the Arabic Letter Mark (U+061C) and the Unicode line/paragraph separators
    (U+2028/U+2029) — every code point AC-DPF-8's normative text enumerates is still stripped."""
    dirty = "a" + "\u061c" + "b" + "\u200b" + "c" + "\ufeff" + "d"
    assert pf.sanitize(dirty) == "abcd"


def test_the_module_never_writes(tmp_path):
    proj = _project_dir(
        tmp_path / "proj", settings=_perms(allow=["Bash(x:*)"]), settings_local=_perms(ask=["Bash(y:*)"])
    )
    plugin_root = _plugin_root(tmp_path / "plugin", [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}])
    before = _tree_snapshot(str(tmp_path))

    write_events = []

    def _hook(event, args):
        if event == "open" and len(args) >= 2:
            mode = args[1]
            path_arg = args[0]
            if isinstance(mode, str) and any(ch in mode for ch in ("w", "a", "x", "+")) and str(path_arg).startswith(str(tmp_path)):
                write_events.append((event, args))
        elif event in ("os.rename", "os.remove", "os.mkdir", "os.rmdir", "shutil.copyfile", "shutil.move", "shutil.rmtree"):
            if args and str(args[0]).startswith(str(tmp_path)):
                write_events.append((event, args))

    sys.addaudithook(_hook)
    pf.run_check(plugin_root, proj, home=str(tmp_path / "home"))
    after = _tree_snapshot(str(tmp_path))

    assert write_events == []
    assert before == after

    src = open(os.path.join(REPO_ROOT, "scripts", "foundry_permission_floor.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"subprocess", "socket", "http", "urllib", "requests"}
    assert not (imported & forbidden), f"forbidden import(s) in module closure: {imported & forbidden}"


def test_absent_or_unreadable_settings_are_tolerated(tmp_path):
    proj = str(tmp_path / "proj")
    os.makedirs(os.path.join(proj, ".claude"), exist_ok=True)
    with open(os.path.join(proj, ".claude", "settings.json"), "w", encoding="utf-8") as f:
        json.dump(_perms(allow=["Bash(x:*)"]), f)

    # settings.local.json genuinely absent — tolerated, no raise, not counted as unreadable
    effective, unreadable, any_exists = pf._effective_config(proj)
    assert unreadable == []
    assert any_exists

    # existing-but-malformed shapes never raise and are recorded as unreadable
    bad_docs = [
        '{"permissions": {"allow": "not-a-list"}}',
        '{"permissions": {"allow": {"a": 1}}}',
        '{"permissions": {"allow": null}}',
        '{"permissions": {"allow": ["Bash(x:*)", 7]}}',
        "{ not json at all",
    ]
    for i, text in enumerate(bad_docs):
        p = os.path.join(proj, ".claude", "settings.local.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        result = pf.load_settings_file(p)  # must never raise
        assert result["status"] == "unreadable", f"case {i}: {text!r} -> {result}"


# ================================================================================================ #
# AC-DPF-3 — canonicalization and the covers relation
# ================================================================================================ #
def test_canonicalization_folds_the_live_spellings():
    home = "/home/lsliwka"
    live_allow = (
        "Bash(python3 /home/lsliwka/.claude/plugins/cache/agentic-foundry/foundry/2.3.1/"
        "scripts/foundry-authorize.py *)"
    )
    map_ask = "Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-authorize.py:*)"
    assert pf.covers(live_allow, map_ask, home=home) is True

    home_var_allow = (
        "Bash($HOME/.claude/plugins/cache/agentic-foundry/foundry/2.3.1/"
        "scripts/foundry-authorize.py:*)"
    )
    assert pf.covers(home_var_allow, map_ask, home=home) is True

    unrelated = (
        "Bash(python3 /home/lsliwka/.claude/plugins/cache/agentic-foundry/foundry/2.3.1/"
        "scripts/foundry-fleet-doctor.py *)"
    )
    assert pf.covers(unrelated, map_ask, home=home) is False


def test_blanket_grants_are_detected():
    for r in ("Bash(*)", "Bash(python3 *)", "Bash(python3:*)"):
        assert pf.is_blanket_rule(r) is True, r
    assert pf.is_blanket_rule("Bash(scripts/foo.py:*)") is False
    assert pf.is_blanket_rule("Bash(gh pr merge:*)") is False


def test_blanket_allow_line_renders_the_folded_form_not_the_raw_prefix(tmp_path):
    """R1 (PR #60 review): an arbitrary free-text prefix ahead of the plugins/cache segment is
    reachable via the fold — the emitted blanket-allow line must carry the folded/canonicalized
    form, never the raw settings-file text (which could carry that free-text prefix verbatim)."""
    home = str(tmp_path / "home")
    G = DEFAULT_GLOB
    arbitrary_prefix = "ARBITRARY-FREE-TEXT-PREFIX-DO-NOT-LEAK"
    raw_rule = f"Bash({arbitrary_prefix} {home}/.claude/plugins/cache/agentic-foundry/foundry/1.0.0/*)"
    plugin_root = _plugin_root(
        tmp_path / "plugin",
        [{"rule": f"Bash({G}/scripts/foundry-authorize.py:*)", "tier": "ask", "rationale": "r"}],
        glob_pat=G,
    )
    proj = _project_dir(tmp_path / "proj", settings=_perms(allow=[raw_rule]))
    result = pf.run_check(plugin_root, proj, home=home)
    blanket_lines = [l for l in result["lines"] if l.startswith("blanket-allow:")]
    assert len(blanket_lines) == 1
    assert arbitrary_prefix not in blanket_lines[0]


def test_deny_coverage_requires_exact_reach_equality():
    map_deny = "Bash(gh pr merge --admin:*)"
    assert pf.deny_covers("Bash(gh pr merge --admin:*)", map_deny) is True
    # a BROADER effective deny does NOT count as coverage — fail-safe noise, never silence
    assert pf.deny_covers("Bash(gh pr merge:*)", map_deny) is False
    # the fold is refused on the deny direction: an interpreter-prefixed spelling does not reduce
    assert pf.deny_covers("Bash(bash gh pr merge --admin:*)", map_deny) is False


# ================================================================================================ #
# AC-DPF-8 — the report: classes, rank, origins, redaction, summary
# ================================================================================================ #
def test_the_finding_classes_are_reported_with_every_covering_origin(tmp_path):
    home = "/h"  # short and NOT tmp_path-derived: a tmp_path-length home would blow the AC-DPF-8
    # 200-char per-line render cap before the second origin ever appears in the line. This path
    # need not exist — the glob simply expands to nothing, contributing an unrelated
    # stale-plugin-path line this test does not assert on.
    G = DEFAULT_GLOB
    ceremony_rule = f"Bash({G}/scripts/foundry-authorize.py:*)"
    deny_rule = "Bash(gh pr merge --admin:*)"
    allow_rule = f"Bash({G}/scripts/foundry-index.py:*)"
    entries = [
        {"rule": ceremony_rule, "tier": "ask", "rationale": "r"},
        {"rule": deny_rule, "tier": "deny", "rationale": "r"},
        {"rule": allow_rule, "tier": "allow", "rationale": "r"},
    ]
    plugin_root = _plugin_root(tmp_path / "plugin", entries, glob_pat=G)
    covering = f"Bash(python3 {home}/.claude/plugins/cache/agentic-foundry/foundry/1.0.0/scripts/foundry-authorize.py *)"
    proj = _project_dir(tmp_path / "proj", settings=_perms(allow=[covering]), settings_local=_perms(allow=[covering]))
    result = pf.run_check(plugin_root, proj, home=home)
    assert result["outcome"] == "advisory"

    ceremony_lines = [l for l in result["lines"] if l.startswith("ask-shadowed-ceremony:")]
    assert len(ceremony_lines) == 1
    assert ".claude/settings.json" in ceremony_lines[0]
    assert ".claude/settings.local.json" in ceremony_lines[0]

    deny_lines = [l for l in result["lines"] if l.startswith("deny-missing:")]
    assert len(deny_lines) == 1 and "gh pr merge --admin" in deny_lines[0]

    absent_lines = [l for l in result["lines"] if l.startswith("allow-absent:")]
    assert len(absent_lines) == 1 and "foundry-index.py" in absent_lines[0]

    classes_seen = [l.split(":", 1)[0] for l in result["lines"]]
    assert classes_seen == sorted(classes_seen, key=pf.RANK.index)


def test_the_unclassified_bucket_redacts_rule_bodies(tmp_path):
    home = str(tmp_path / "home")
    # R9 (PR #60 review): credential-shaped-but-not-scanner-matching — 20 chars after `ghp_`
    # (a real GitHub PAT scanner rule is `ghp_[0-9A-Za-z]{36}`) so this fixture stays
    # credential-shaped for the test's purpose without exact-matching gitleaks/trufflehog (which
    # would otherwise fire on this repo's own prepublication leak scan at the GO-PUBLIC flip).
    secret_rule = "Read(ghp_ABCDEFGHIJKLMNOPQRST)"
    proj = _project_dir(tmp_path / "proj", settings=_perms(allow=[secret_rule]))
    plugin_root = _plugin_root(tmp_path / "plugin", [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}])
    result = pf.run_check(plugin_root, proj, home=home)
    unclassified_lines = [l for l in result["lines"] if l.startswith("unclassified:")]
    assert len(unclassified_lines) == 1
    line = unclassified_lines[0]
    assert "ghp_ABCDEFGHIJKLMNOPQRST" not in line
    assert "Read" in line
    assert ".claude/settings.json" in line
    assert "1" in line


def test_tool_prefix_never_emits_a_paren_less_secret_shaped_rule_body(tmp_path):
    """R2 (PR #60 review): a paren-less rule (e.g. an AWS-key-ID-shaped bare token) must not be
    emitted verbatim as a "tool prefix" — with no "(" there is nothing to extract, so it must
    always render as the withheld marker "?"."""
    home = str(tmp_path / "home")
    secret_body = "AKIAABCDEFGHIJKLMNOP"  # 20-char alnum, no "(" anywhere in the rule
    assert pf._tool_prefix(secret_body) == "?"
    proj = _project_dir(tmp_path / "proj", settings=_perms(allow=[secret_body]))
    plugin_root = _plugin_root(tmp_path / "plugin", [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}])
    result = pf.run_check(plugin_root, proj, home=home)
    unclassified_lines = [l for l in result["lines"] if l.startswith("unclassified:")]
    assert len(unclassified_lines) == 1
    assert secret_body not in unclassified_lines[0]
    assert "tool '?'" in unclassified_lines[0]


def test_tool_prefix_valid_re_rejects_a_trailing_newline():
    """R2 (PR #60 review): the anchor was widened from `$` to `\\Z` so a trailing newline can't
    smuggle a rule body past the tool-prefix validator."""
    assert pf._TOOL_PREFIX_VALID_RE.match("Bash") is not None
    assert pf._TOOL_PREFIX_VALID_RE.match("Bash\n") is None
    assert pf._TOOL_PREFIX_VALID_RE.match("Bash\nrm -rf /") is None


def test_the_summary_line_ranks_ceremony_shadowing_first(tmp_path):
    home = str(tmp_path / "home")
    G = DEFAULT_GLOB
    ceremony_rule = f"Bash({G}/scripts/foundry-authorize.py:*)"
    plugin_root = _plugin_root(tmp_path / "plugin", [{"rule": ceremony_rule, "tier": "ask", "rationale": "r"}], glob_pat=G)

    proj_a = _project_dir(tmp_path / "proj-a", settings=_perms(allow=["Bash(python3 *)"]))
    result_a = pf.run_check(plugin_root, proj_a, home=home)
    assert result_a["summary"].startswith(pf.CEREMONY_LEAD_LITERAL)

    covering = f"Bash({home}/.claude/plugins/cache/agentic-foundry/foundry/1.0.0/scripts/foundry-authorize.py:*)"
    proj_b = _project_dir(tmp_path / "proj-b", settings=_perms(allow=[covering]))
    result_b = pf.run_check(plugin_root, proj_b, home=home)
    assert result_b["summary"].startswith(pf.CEREMONY_LEAD_LITERAL)

    proj_c = _project_dir(tmp_path / "proj-c", settings=_perms())
    result_c = pf.run_check(plugin_root, proj_c, home=home)
    assert not result_c["summary"].startswith(pf.CEREMONY_LEAD_LITERAL)
    assert result_c["summary"].startswith("permission-floor:")
    assert re.search(
        r"blanket-allow=\d+, ask-shadowed-ceremony=\d+, ask-shadowed=\d+, deny-missing=\d+, "
        r"settings-unreadable=\d+, stale-plugin-path=\d+, allow-absent=\d+, unclassified=\d+",
        result_c["summary"],
    )


# ================================================================================================ #
# AC-DPF-4 — the honest tier + plugin_root_glob validation + session-start
# ================================================================================================ #
def test_outcome_tiering_is_advisory_except_a_malformed_floor(tmp_path):
    home = str(tmp_path / "home")
    _seed_cache_dir(home)  # so a genuinely clean config resolves to "ok", not a spurious
    # stale-plugin-path finding from an unrelated, unseeded glob expansion.
    entries = [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}]
    plugin_root = _plugin_root(tmp_path / "plugin", entries)

    proj_ok = _project_dir(tmp_path / "proj-ok", settings=_perms(allow=["Bash(x:*)"]))
    assert pf.run_check(plugin_root, proj_ok, home=home)["outcome"] == "ok"

    proj_adv = _project_dir(tmp_path / "proj-adv", settings=_perms())
    assert pf.run_check(plugin_root, proj_adv, home=home)["outcome"] == "advisory"

    proj_none = str(tmp_path / "proj-none")
    os.makedirs(proj_none, exist_ok=True)
    r_none = pf.run_check(plugin_root, proj_none, home=home)
    assert r_none["outcome"] == "advisory"
    assert "no-configuration" in r_none["summary"]

    empty_plugin_root = str(tmp_path / "no-floor-plugin")
    os.makedirs(empty_plugin_root, exist_ok=True)
    r_skip = pf.run_check(empty_plugin_root, proj_ok, home=home)
    assert r_skip["outcome"] == "skip"

    malformed_root = str(tmp_path / "malformed-plugin")
    os.makedirs(os.path.join(malformed_root, "docs"), exist_ok=True)
    with open(os.path.join(malformed_root, "docs", "permission-floor.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json")
    with pytest.raises(pf.FloorMalformed):
        pf.run_check(malformed_root, proj_ok, home=home)


def test_plugin_root_glob_is_validated_before_expansion(tmp_path):
    entries = [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}]
    bad_globs = [
        "/etc/plugins/cache/*/foundry/*",           # wrong prefix
        "~/.claude/plugins/cache/../*/foundry/*",   # .. segment
        "~/.claude/plugins/cache/**/foundry/*",     # **
        "~/.claude/plugins/cache/*/foundry/*/*/*",  # more than two *
    ]
    for i, g in enumerate(bad_globs):
        root = _plugin_root(tmp_path / f"bad-{i}", entries, glob_pat=g)
        with pytest.raises(pf.FloorMalformed):
            pf.load_permission_floor(root)

    good = _plugin_root(tmp_path / "good", entries, glob_pat=DEFAULT_GLOB)
    doc, status = pf.load_permission_floor(good)
    assert status is None and doc is not None


def test_session_start_stays_fail_open(tmp_path):
    fixture_root = _functional_plugin_root(tmp_path / "plugin-copy", malformed_floor=True)
    proj = str(tmp_path / "proj")
    _seed_minimal_operator_registry(proj)

    t0 = time.monotonic()
    r = _run_doctor(proj, "--session-start", plugin_root=fixture_root)
    elapsed = time.monotonic() - t0
    assert r.returncode == 0
    assert elapsed < 5.0

    # the SAME malformed floor, operator-invoked (no --session-start), IS RED
    r2 = _run_doctor(proj, plugin_root=fixture_root)
    assert r2.returncode != 0
    assert "DOCTOR-RED" in r2.stdout


def test_session_start_prints_only_the_actionable_lines(tmp_path):
    home = str(tmp_path / "home")
    G = DEFAULT_GLOB
    entries = [
        {"rule": f"Bash({G}/scripts/foundry-authorize.py:*)", "tier": "ask", "rationale": "r"},
        {"rule": f"Bash({G}/scripts/foundry-index.py:*)", "tier": "allow", "rationale": "r"},
    ]
    plugin_root = _plugin_root(tmp_path / "plugin", entries, glob_pat=G)
    covering = f"Bash({home}/.claude/plugins/cache/agentic-foundry/foundry/1.0.0/scripts/foundry-authorize.py:*)"
    proj = _project_dir(
        tmp_path / "proj",
        settings=_perms(allow=[covering, "Read(sk-ABCDEFGHIJKLMNOPQRSTUV1234567890)"]),
    )
    full = pf.run_check(plugin_root, proj, home=home, for_session_start=False)
    filtered = pf.run_check(plugin_root, proj, home=home, for_session_start=True)

    assert any(l.startswith("ask-shadowed") for l in filtered["lines"])
    assert not any(l.startswith("allow-absent:") for l in filtered["lines"])
    assert not any(l.startswith("unclassified:") for l in filtered["lines"])
    info_lines = [l for l in filtered["lines"] if "informational finding" in l]
    assert len(info_lines) == 1

    assert any(l.startswith("allow-absent:") for l in full["lines"])
    assert any(l.startswith("unclassified:") for l in full["lines"])


# ================================================================================================ #
# AC-DPF-5(a) — class negative controls (a)-(k)
# ================================================================================================ #
def test_negative_controls_all_fire(tmp_path):
    G = DEFAULT_GLOB

    # (a) the live shadow spelling: python3 interpreter, absolute home path, globbed version
    # segment, trailing bare `*` — without this the shadow check is vacuous against the exact
    # spelling it exists to catch.
    home_a = str(tmp_path / "home-a")
    ceremony = f"Bash({G}/scripts/foundry-authorize.py:*)"
    plugin_a = _plugin_root(tmp_path / "plugin-a", [{"rule": ceremony, "tier": "ask", "rationale": "r"}], glob_pat=G)
    shadow_rule = f"Bash(python3 {home_a}/.claude/plugins/cache/agentic-foundry/foundry/9.9.9/scripts/foundry-authorize.py *)"
    proj_a = _project_dir(tmp_path / "proj-a", settings_local=_perms(allow=[shadow_rule]))
    result_a = pf.run_check(plugin_a, proj_a, home=home_a)
    assert any(l.startswith("ask-shadowed-ceremony:") for l in result_a["lines"]), "(a) live shadow spelling did not fire"

    # (b) a missing map deny
    plugin_b = _plugin_root(tmp_path / "plugin-b", [{"rule": "Bash(gh pr merge --admin:*)", "tier": "deny", "rationale": "r"}])
    proj_b = _project_dir(tmp_path / "proj-b", settings=_perms())
    result_b = pf.run_check(plugin_b, proj_b, home=str(tmp_path / "home-b"))
    assert any(l.startswith("deny-missing:") for l in result_b["lines"]), "(b) missing deny did not fire"

    # (c) an absent map allow
    plugin_c = _plugin_root(tmp_path / "plugin-c", [{"rule": f"Bash({G}/scripts/foundry-index.py:*)", "tier": "allow", "rationale": "r"}], glob_pat=G)
    proj_c = _project_dir(tmp_path / "proj-c", settings=_perms())
    result_c = pf.run_check(plugin_c, proj_c, home=str(tmp_path / "home-c"))
    assert any(l.startswith("allow-absent:") for l in result_c["lines"]), "(c) absent allow did not fire"

    # (d) plugin_root_glob expanding to no directory
    plugin_d = _plugin_root(tmp_path / "plugin-d", [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}], glob_pat=G)
    proj_d = _project_dir(tmp_path / "proj-d", settings=_perms())
    result_d = pf.run_check(plugin_d, proj_d, home=str(tmp_path / "home-d-empty"))
    assert any(
        l.startswith("stale-plugin-path:") and "expands to no directory" in l for l in result_d["lines"]
    ), "(d) empty glob expansion did not fire"

    # (e) a malformed floor file -> RED at the check-function level. Needs the real
    # scripts/foundry_permission_floor.py present so the doctor's module loader can import it at
    # all (a plugin_root with no scripts/ tree yields "not applicable", not RED).
    plugin_e = _functional_plugin_root(tmp_path / "plugin-e", malformed_floor=True)
    ok_e, _detail_e = doctor.check_permission_floor(plugin_root=plugin_e, project_dir=str(tmp_path / "proj-e"))
    assert ok_e is False, "(e) malformed floor did not RED"

    # (f) a clean configuration -> ok, no findings
    home_f = str(tmp_path / "home-f")
    script_dir = os.path.join(home_f, ".claude", "plugins", "cache", "agentic-foundry", "foundry", "1.0.0", "scripts")
    os.makedirs(script_dir, exist_ok=True)
    with open(os.path.join(script_dir, "foundry-index.py"), "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env python3\n")
    plugin_f = _plugin_root(tmp_path / "plugin-f", [{"rule": f"Bash({G}/scripts/foundry-index.py:*)", "tier": "allow", "rationale": "r"}], glob_pat=G)
    covering_f = f"Bash({home_f}/.claude/plugins/cache/agentic-foundry/foundry/1.0.0/scripts/foundry-index.py:*)"
    proj_f = _project_dir(tmp_path / "proj-f", settings=_perms(allow=[covering_f]))
    result_f = pf.run_check(plugin_f, proj_f, home=home_f)
    assert result_f["outcome"] == "ok", f"(f) clean config not ok: {result_f}"
    assert result_f["lines"] == []

    # (g)(h)(i) Bash(*), Bash(python3 *), Bash(python3:*) -> blanket-allow, naming the ceremony
    for idx, blanket_rule in enumerate(("Bash(*)", "Bash(python3 *)", "Bash(python3:*)")):
        plugin_x = _plugin_root(tmp_path / f"plugin-blanket-{idx}", [{"rule": ceremony, "tier": "ask", "rationale": "r"}], glob_pat=G)
        proj_x = _project_dir(tmp_path / f"proj-blanket-{idx}", settings=_perms(allow=[blanket_rule]))
        result_x = pf.run_check(plugin_x, proj_x, home=str(tmp_path / f"home-blanket-{idx}"))
        blanket_lines = [l for l in result_x["lines"] if l.startswith("blanket-allow:")]
        assert len(blanket_lines) == 1, f"({blanket_rule}) did not produce exactly one blanket-allow line"
        assert "foundry-authorize.py" in blanket_lines[0]
        assert not any(l.startswith("ask-shadowed") for l in result_x["lines"]), \
            f"({blanket_rule}) also produced an individual ask-shadowed line"

    # (j) a credential-shaped body + an ANSI-bearing body -> both land in unclassified, nothing leaks
    cred_j = "Read(token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789)"
    ansi_j = "Write(\x1b[31mDANGER\x1b[0m)"
    plugin_j = _plugin_root(tmp_path / "plugin-j", [{"rule": "Bash(x:*)", "tier": "allow", "rationale": "r"}])
    proj_j = _project_dir(tmp_path / "proj-j", settings=_perms(allow=[cred_j, ansi_j]))
    result_j = pf.run_check(plugin_j, proj_j, home=str(tmp_path / "home-j"))
    blob_j = result_j["summary"] + "".join(result_j["lines"])
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in blob_j
    assert "\x1b" not in blob_j
    assert "DANGER" not in blob_j
    assert sum(1 for l in result_j["lines"] if l.startswith("unclassified:")) >= 1

    # (k) the same map entry covered by rules in BOTH settings files -> one line, both origins
    home_k = str(tmp_path / "home-k")
    entry_k = f"Bash({G}/scripts/foundry-index.py:*)"
    plugin_k = _plugin_root(tmp_path / "plugin-k", [{"rule": entry_k, "tier": "ask", "rationale": "r"}], glob_pat=G)
    cov_k = f"Bash({home_k}/.claude/plugins/cache/agentic-foundry/foundry/1.0.0/scripts/foundry-index.py:*)"
    proj_k = _project_dir(tmp_path / "proj-k", settings=_perms(allow=[cov_k]), settings_local=_perms(allow=[cov_k]))
    result_k = pf.run_check(plugin_k, proj_k, home=home_k)
    ask_lines_k = [l for l in result_k["lines"] if l.startswith("ask-shadowed")]
    assert len(ask_lines_k) == 1
    assert ".claude/settings.json" in ask_lines_k[0] and ".claude/settings.local.json" in ask_lines_k[0]


# ================================================================================================ #
# AC-DPF-5(b) — fail-open controls
# ================================================================================================ #
def _assert_unreadable(path):
    result = pf.load_settings_file(path)  # must never raise
    assert result["status"] == "unreadable", f"expected unreadable for {path}: {result}"


def test_fail_open_negative_controls_all_exit_zero(tmp_path):
    base = tmp_path / "settings-cases"
    os.makedirs(str(base), exist_ok=True)

    # (1) a JSON document nested past the interpreter's recursion limit
    p = str(base / "deep.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"permissions": {"allow": ' + "[" * 5000 + "]" * 5000 + "}}")
    _assert_unreadable(p)

    # (2)(3)(4) permissions.allow as a string, as a dict, and as null
    for i, bad in enumerate(['"not-a-list"', '{"x": 1}', "null"]):
        p = str(base / f"bad-shape-{i}.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"permissions": {"allow": %s}}' % bad)
        _assert_unreadable(p)

    # (5) a tier array containing an integer element
    p = str(base / "int-elem.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"permissions": {"allow": ["Bash(x:*)", 7]}}')
    _assert_unreadable(p)

    # (6) a settings path raising PermissionError
    p = str(base / "no-perm.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"permissions": {"allow": []}}')
    os.chmod(p, 0o000)
    try:
        _assert_unreadable(p)
    finally:
        os.chmod(p, 0o644)

    # (7) a settings path that is a FIFO
    p = str(base / "a-fifo")
    os.mkfifo(p)
    _assert_unreadable(p)

    # (8) a settings path that is a symlink to a non-regular file (a directory)
    target_dir = str(base / "a-dir")
    os.makedirs(target_dir, exist_ok=True)
    p = str(base / "symlink-to-dir")
    os.symlink(target_dir, p)
    _assert_unreadable(p)

    # (9) a settings file whose bytes are not valid UTF-8
    p = str(base / "bad-utf8.json")
    with open(p, "wb") as f:
        f.write(b'{"permissions": {"allow": ["\xff\xfe"]}}')
    _assert_unreadable(p)

    # Integration: bundle two pathological settings files into a real project dir and drive the
    # doctor CLI with --session-start, proving the module's tolerance holds end-to-end too.
    proj2 = str(tmp_path / "proj-integration")
    os.makedirs(os.path.join(proj2, ".claude"), exist_ok=True)
    with open(os.path.join(proj2, ".claude", "settings.json"), "w", encoding="utf-8") as f:
        f.write('{"permissions": {"allow": "not-a-list"}}')
    os.mkfifo(os.path.join(proj2, ".claude", "settings.local.json"))
    plugin_root = _functional_plugin_root(tmp_path / "plugin-integration")
    t0 = time.monotonic()
    r = _run_doctor(proj2, "--session-start", plugin_root=plugin_root)
    elapsed = time.monotonic() - t0
    assert r.returncode == 0
    assert elapsed < 5.0

    # (10, MANDATORY) the malformed-floor case, at --session-start: exit 0 within 5s.
    proj3 = str(tmp_path / "proj-malformed-floor")
    _seed_minimal_operator_registry(proj3)
    malformed_root = _functional_plugin_root(tmp_path / "plugin-malformed-floor", malformed_floor=True)
    t1 = time.monotonic()
    r3 = _run_doctor(proj3, "--session-start", plugin_root=malformed_root)
    elapsed3 = time.monotonic() - t1
    assert r3.returncode == 0
    assert elapsed3 < 5.0


# ================================================================================================ #
# AC-DPF-5(c) — cross-atom matcher conformance
# ================================================================================================ #
SHARED_TABLE = [
    ("Bash(a/b:*)", "Bash(a/b/c:*)", True),
    ("Bash(a/b/c:*)", "Bash(a/b:*)", False),
    ("Bash(a/b:*)", "Bash(a/b)", True),
    ("Bash(a/bc:*)", "Bash(a/b:*)", False),
    ("Bash(a/b:*)", "Bash(a/bc:*)", True),
    ("Bash(a/b:*)", "Bash(a/b:*)", True),
    (
        "Bash(~/.claude/plugins/cache/*/foundry/*/scripts/:*)",
        "Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-authorize.py:*)",
        True,
    ),
    ("Bash(gh pr merge --admin:*)", "Bash(gh pr merge:*)", False),
]


def test_covers_agrees_with_the_map_suite_on_the_shared_table():
    try:
        map_suite = _import_map_suite()
    except Exception as e:  # pragma: no cover — proves the FAIL-not-skip discipline
        pytest.fail(f"sibling map suite tests/test_permission_floor_map.py could not be imported: {e}")

    for a, b, expected in SHARED_TABLE:
        got_subsumes = map_suite._subsumes(a, b)
        got_covers = pf.covers(a, b)
        assert got_subsumes == expected, (a, b, expected, "map suite _subsumes disagreed", got_subsumes)
        assert got_covers == expected, (a, b, expected, "this module's covers() disagreed", got_covers)


def test_declared_extension_spellings_beyond_the_shared_table():
    """AC-DPF-5(c): rows outside the shared vocabulary (bare `*`, ` *`, `/*`, interpreter, `~`/
    `$HOME`-expanded spellings) are THIS atom's declared extension, asserted here only."""
    home = "/home/x"
    assert pf.covers("Bash(*)", "Bash(a/b:*)", home=home) is True
    assert pf.covers("Bash(python3 *)", "Bash(a/b:*)", home=home) is True
    assert pf.covers("Bash(~/scripts/x:*)", "Bash($HOME/scripts/x/y:*)", home=home) is True
    assert pf.covers("Bash(a/b *)", "Bash(a/bc:*)", home=home) is True
    assert pf.covers("Bash(a/b/*)", "Bash(a/b/c:*)", home=home) is True


# ================================================================================================ #
# AC-DPF-6 — troubleshooting.md documents the advisory tier
# ================================================================================================ #
def test_troubleshooting_documents_the_advisory_tier():
    with open(os.path.join(REPO_ROOT, "docs", "troubleshooting.md"), encoding="utf-8") as f:
        text = f.read()
    for literal in ("permission-floor", "advisory", ".claude/settings.local.json"):
        assert literal in text, f"docs/troubleshooting.md missing required literal {literal!r}"


# ================================================================================================ #
# AC-DPF-7 — the sibling map's closed world is preserved, `entries` provably untouched
# ================================================================================================ #
def test_the_map_records_the_new_module_as_not_invoked():
    with open(os.path.join(REPO_ROOT, "docs", "permission-floor.json"), encoding="utf-8") as f:
        doc = json.load(f)
    scripts = {e["script"] for e in doc["not_invoked"]}
    assert "foundry_permission_floor.py" in scripts
    row = next(e for e in doc["not_invoked"] if e["script"] == "foundry_permission_floor.py")
    assert row["rationale"]


def test_the_map_entries_array_is_unchanged():
    with open(os.path.join(REPO_ROOT, "docs", "permission-floor.json"), encoding="utf-8") as f:
        doc = json.load(f)
    assert _entries_digest(doc["entries"]) == MERGE_BASE_ENTRIES_DIGEST, (
        "docs/permission-floor.json `entries` changed — if this is a legitimate map edit, update "
        "MERGE_BASE_ENTRIES_DIGEST in the SAME reviewed diff (R8)"
    )
