"""tests/test_permission_floor_map.py — the live seam for feat-foundry-permission-floor-map.

Validates `docs/permission-floor.json`, the canonical three-tier allow/ask/deny map of every
command shape the plugin's workflow instructs (AC-PFM-1..7). Ground truth for the closed-world
coverage check (AC-PFM-2) is derived AT TEST TIME from the shipped `scripts/` tree — never from a
hand-copied list — so a new un-tiered invocable script fails closed.

Every coverage/subsumption check is exposed as a function of an explicit `(tree_root, parsed_map)`
pair (AC-PFM-5) so it can be driven both over the real shipped tree AND over throwaway fixture
trees for the negative controls, which prove the checks are not vacuous.
"""
from __future__ import annotations

import json
import os
import re
import shutil

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(REPO_ROOT, "docs", "permission-floor.json")

PLUGIN_ROOT_GLOB = "~/.claude/plugins/cache/*/foundry/*"

# --------------------------------------------------------------------------------------------- #
# shared helpers — each a function of an explicit (tree_root, parsed_map) pair (AC-PFM-5)
# --------------------------------------------------------------------------------------------- #


def load_map(map_path=MAP_PATH):
    with open(map_path, encoding="utf-8") as f:
        return json.load(f)


def ground_truth_basenames(tree_root):
    """The non-recursive set of basenames of files directly under scripts/ that are invocable
    shapes: every *.py file, plus every other regular file carrying the owner-execute bit
    (AC-PFM-2)."""
    scripts_dir = os.path.join(tree_root, "scripts")
    out = set()
    if not os.path.isdir(scripts_dir):
        return out
    for name in os.listdir(scripts_dir):
        path = os.path.join(scripts_dir, name)
        if not os.path.isfile(path):
            continue
        if name.endswith(".py"):
            out.add(name)
        elif os.access(path, os.X_OK) and (os.stat(path).st_mode & 0o100):
            out.add(name)
    return out


_RULE_RE = re.compile(r"^Bash\(([^()]+)\)$")
# a basename immediately after "scripts/", ended by a rule delimiter (":", space, or body end)
_SCRIPTS_BASENAME_RE = re.compile(r"scripts/([^\s:)]+)")


def rule_body(rule):
    m = _RULE_RE.match(rule)
    assert m, f"not a well-formed Bash() rule: {rule!r}"
    return m.group(1)


def basenames_named_by_rule(rule):
    """Every scripts/<basename> basename literally named inside a rule body."""
    body = rule_body(rule)
    return set(_SCRIPTS_BASENAME_RE.findall(body))


def tiered_basenames(parsed_map):
    """basename -> list of entries (rule, tier) that name it after scripts/."""
    out = {}
    for entry in parsed_map["entries"]:
        for name in basenames_named_by_rule(entry["rule"]):
            out.setdefault(name, []).append(entry)
    return out


def not_invoked_basenames(parsed_map):
    return {e["script"] for e in parsed_map["not_invoked"]}


# ---- AC-PFM-2 coverage -------------------------------------------------------------------- #


def coverage_violations(tree_root, parsed_map):
    """Returns a list of human-readable violation strings; empty means AC-PFM-2(a)/(b) coverage
    holds: every ground-truth basename is tiered XOR excluded, never both, never neither."""
    gt = ground_truth_basenames(tree_root)
    tiered = tiered_basenames(parsed_map)
    excluded = not_invoked_basenames(parsed_map)
    violations = []
    for name in sorted(gt):
        is_tiered = name in tiered
        is_excluded = name in excluded
        if is_tiered and is_excluded:
            violations.append(f"{name}: both tiered and excluded")
        elif not is_tiered and not is_excluded:
            violations.append(f"{name}: neither tiered nor excluded")
    return violations


def tier_coherence_violations(parsed_map):
    """AC-PFM-2: where a basename is tiered by more than one entry, those entries SHALL either
    all carry the same tier, or be pairwise reach-disjoint per AC-PFM-6."""
    tiered = tiered_basenames(parsed_map)
    violations = []
    for name, entries in tiered.items():
        if len(entries) < 2:
            continue
        tiers = {e["tier"] for e in entries}
        if len(tiers) == 1:
            continue
        # must be pairwise reach-disjoint (AC-PFM-6 sense)
        for i, a in enumerate(entries):
            for b in entries[i + 1:]:
                if a["tier"] == b["tier"]:
                    continue
                if _subsumes(a["rule"], b["rule"]) or _subsumes(b["rule"], a["rule"]):
                    violations.append(
                        f"{name}: {a['rule']!r} ({a['tier']}) and {b['rule']!r} ({b['tier']}) "
                        "are not reach-disjoint"
                    )
    return violations


def dangling_entry_violations(tree_root, parsed_map):
    """AC-PFM-2 reverse direction: every not_invoked[].script and every scripts/-named basename
    in a rule SHALL correspond to a file present in the shipped tree."""
    gt_all_files = set()
    scripts_dir = os.path.join(tree_root, "scripts")
    if os.path.isdir(scripts_dir):
        gt_all_files = {n for n in os.listdir(scripts_dir) if os.path.isfile(os.path.join(scripts_dir, n))}
    violations = []
    for name in not_invoked_basenames(parsed_map):
        if name not in gt_all_files:
            violations.append(f"not_invoked script {name!r} absent from shipped tree")
    for entry in parsed_map["entries"]:
        for name in basenames_named_by_rule(entry["rule"]):
            if name not in gt_all_files:
                violations.append(f"rule {entry['rule']!r} names absent script {name!r}")
    return violations


_INTERP_WORDS = {"bash", "sh", "python", "python3"}
_CMD_SPLIT_RE = re.compile(r"\|\||\||&&|;|\$\(|`")


def _fenced_code_blocks(text):
    return re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)


def _command_position_basenames(text):
    """Every basename that occurs in command position: the first word of a command (allowing an
    optional leading interpreter word and an optional directory prefix), commands delimited by
    line start, |, &&, ||, ;, $(, or a backtick."""
    found = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for part in _CMD_SPLIT_RE.split(line):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            if not tokens:
                continue
            first = tokens[0]
            if first in _INTERP_WORDS and len(tokens) > 1:
                first = tokens[1]
            first = first.strip("\"'")
            basename = os.path.basename(first)
            if basename:
                found.add(basename)
    return found


def commanded_basenames(tree_root):
    """Every basename appearing in command position in any skills/**/SKILL.md fenced code block
    or any non-comment line of hooks/** (AC-PFM-2's not_invoked truth-check)."""
    found = set()
    skills_dir = os.path.join(tree_root, "skills")
    if os.path.isdir(skills_dir):
        for dirpath, _dirnames, filenames in os.walk(skills_dir):
            for fn in filenames:
                if fn != "SKILL.md":
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    text = open(path, encoding="utf-8").read()
                except OSError:
                    continue
                for block in _fenced_code_blocks(text):
                    found |= _command_position_basenames(block)
    hooks_dir = os.path.join(tree_root, "hooks")
    if os.path.isdir(hooks_dir):
        for dirpath, _dirnames, filenames in os.walk(hooks_dir):
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                if not os.path.isfile(path):
                    continue
                try:
                    text = open(path, encoding="utf-8").read()
                except OSError:
                    continue
                found |= _command_position_basenames(text)
    return found


def not_invoked_truth_violations(tree_root, parsed_map):
    commanded = commanded_basenames(tree_root)
    violations = []
    for name in not_invoked_basenames(parsed_map):
        if name in commanded:
            violations.append(f"not_invoked script {name!r} appears in command position")
    return violations


# ---- AC-PFM-6 subsumption ------------------------------------------------------------------ #


def _subsumes(rule_a, rule_b):
    """True if rule A's reach (as a :* prefix rule) subsumes rule B's body."""
    body_a = rule_body(rule_a)
    if not body_a.endswith(":*"):
        return False
    s_a = body_a[: -len(":*")]
    body_b = rule_body(rule_b)
    s_b = body_b[: -len(":*")] if body_b.endswith(":*") else body_b
    return s_b.startswith(s_a)


def subsumption_violations(parsed_map):
    entries = parsed_map["entries"]
    violations = []
    for i, a in enumerate(entries):
        for j, b in enumerate(entries):
            if i == j:
                continue
            if a["tier"] == b["tier"]:
                continue
            if _subsumes(a["rule"], b["rule"]):
                violations.append(f"{a['rule']!r} ({a['tier']}) subsumes {b['rule']!r} ({b['tier']})")
    return violations


def concrete_script_violations(tree_root, parsed_map):
    gt_all_files = set()
    scripts_dir = os.path.join(tree_root, "scripts")
    if os.path.isdir(scripts_dir):
        gt_all_files = ground_truth_basenames(tree_root)
    violations = []
    for entry in parsed_map["entries"]:
        if "/scripts/" not in entry["rule"]:
            continue
        for name in basenames_named_by_rule(entry["rule"]):
            if not name or name not in gt_all_files:
                violations.append(f"{entry['rule']!r} does not name a concrete ground-truth script ({name!r})")
    return violations


# --------------------------------------------------------------------------------------------- #
# AC-PFM-1 — schema
# --------------------------------------------------------------------------------------------- #


def test_map_schema_is_wellformed():
    doc = load_map()
    assert set(doc.keys()) == {
        "schema_version",
        "plugin_root_glob",
        "generated_for_plugin_version",
        "entries",
        "not_invoked",
    }
    assert doc["schema_version"] == 1
    assert isinstance(doc["plugin_root_glob"], str) and doc["plugin_root_glob"]
    gpv = doc["generated_for_plugin_version"]
    assert isinstance(gpv, str) and gpv and "\n" not in gpv and gpv == gpv.strip()

    entries = doc["entries"]
    assert isinstance(entries, list) and entries
    seen_rules = set()
    for entry in entries:
        assert set(entry.keys()) == {"rule", "tier", "rationale"}
        rule, tier, rationale = entry["rule"], entry["tier"], entry["rationale"]
        for field_name, value in (("rule", rule), ("rationale", rationale)):
            assert isinstance(value, str) and value, f"{field_name} must be non-empty: {entry!r}"
            assert "\n" not in value, f"{field_name} must be single-line: {entry!r}"
            assert value == value.strip(), f"{field_name} must have no leading/trailing whitespace: {entry!r}"
        assert tier in ("allow", "ask", "deny"), f"bad tier {tier!r} in {entry!r}"
        assert rule not in seen_rules, f"duplicate rule: {rule!r}"
        seen_rules.add(rule)

    not_invoked = doc["not_invoked"]
    assert isinstance(not_invoked, list)
    seen_scripts = set()
    for entry in not_invoked:
        assert set(entry.keys()) == {"script", "rationale"}
        script, rationale = entry["script"], entry["rationale"]
        assert isinstance(script, str) and script and "/" not in script
        assert isinstance(rationale, str) and rationale and "\n" not in rationale
        assert rationale == rationale.strip()
        assert script not in seen_scripts, f"duplicate not_invoked script: {script!r}"
        seen_scripts.add(script)


def test_plugin_root_glob_is_the_pinned_cache_shape():
    doc = load_map()
    assert doc["plugin_root_glob"] == PLUGIN_ROOT_GLOB


# --------------------------------------------------------------------------------------------- #
# AC-PFM-2 — closed-world coverage
# --------------------------------------------------------------------------------------------- #


def test_every_script_is_tiered_exactly_once():
    doc = load_map()
    violations = coverage_violations(REPO_ROOT, doc)
    assert not violations, "coverage violations:\n" + "\n".join(violations)


def test_tiers_per_script_are_coherent():
    doc = load_map()
    violations = tier_coherence_violations(doc)
    assert not violations, "tier-coherence violations:\n" + "\n".join(violations)


def test_no_dangling_map_entries():
    doc = load_map()
    violations = dangling_entry_violations(REPO_ROOT, doc)
    assert not violations, "dangling-entry violations:\n" + "\n".join(violations)


def test_not_invoked_scripts_are_never_commanded():
    doc = load_map()
    violations = not_invoked_truth_violations(REPO_ROOT, doc)
    assert not violations, "not_invoked truth-check violations:\n" + "\n".join(violations)


# --------------------------------------------------------------------------------------------- #
# AC-PFM-3 — ceremonies + anti-patterns pinned verbatim
# --------------------------------------------------------------------------------------------- #


def test_ceremony_and_denial_rules_are_pinned():
    doc = load_map()
    G = doc["plugin_root_glob"]
    rule_tier = {}
    rule_count = {}
    for e in doc["entries"]:
        rule_tier[e["rule"]] = e["tier"]
        rule_count[e["rule"]] = rule_count.get(e["rule"], 0) + 1

    ask_pins = [
        f"Bash({G}/scripts/foundry-authorize.py:*)",
        f"Bash({G}/scripts/foundry-decommission.py record:*)",
        f"Bash({G}/scripts/foundry-decommission.py gate-check:*)",
        f"Bash({G}/scripts/foundry_release.py accept:*)",
        f"Bash({G}/scripts/foundry-upstream-submit.py:*)",
        f"Bash({G}/scripts/foundry-cut-release.py:*)",
        f"Bash({G}/scripts/foundry-project-sync.py:*)",
        f"Bash({G}/scripts/foundry_tier_preflight.py:*)",
        f"Bash({G}/scripts/foundry-doctor.py --heal:*)",
        f"Bash({G}/scripts/foundry-stack-profile.py --relock:*)",
        f"Bash({G}/scripts/foundry-bootstrap.sh:*)",
        "Bash(claude plugin tag:*)",
    ]
    deny_pins = [
        "Bash(gh pr merge --admin:*)",
        "Bash(git push --force:*)",
        "Bash(tofu destroy -auto-approve:*)",
        "Bash(docker system prune:*)",
    ]
    for pin in ask_pins:
        assert pin in rule_tier, f"missing pinned ask rule: {pin!r}"
        assert rule_tier[pin] == "ask", f"pinned rule not ask: {pin!r} is {rule_tier[pin]!r}"
        assert rule_count[pin] == 1, f"pinned rule not exactly once: {pin!r}"
    for pin in deny_pins:
        assert pin in rule_tier, f"missing pinned deny rule: {pin!r}"
        assert rule_tier[pin] == "deny", f"pinned rule not deny: {pin!r} is {rule_tier[pin]!r}"
        assert rule_count[pin] == 1, f"pinned rule not exactly once: {pin!r}"

    # no bare `git push` at any tier
    for e in doc["entries"]:
        body = rule_body(e["rule"])
        if body == "git push" or body.startswith("git push:") or body.startswith("git push "):
            rest = body[len("git push"):].lstrip()
            rest = rest[:-2] if rest.endswith(":*") else rest
            assert rest, f"bare `git push` rule found at any tier: {e['rule']!r}"


# --------------------------------------------------------------------------------------------- #
# AC-PFM-4 — syntactically valid, prefix-anchored Bash() rules
# --------------------------------------------------------------------------------------------- #


def test_rules_are_prefix_anchored_bash_rules():
    doc = load_map()
    G = doc["plugin_root_glob"]
    for entry in doc["entries"]:
        rule = entry["rule"]
        assert re.match(r"^Bash\([^()]+\)$", rule), f"not a well-formed Bash() rule: {rule!r}"
        body = rule_body(rule)
        assert body == body.strip(), f"rule body has leading/trailing whitespace: {rule!r}"
        # prefix-anchored: exact literal, or ends in :*
        is_trailing_star = body.endswith(":*")
        # wildcard position: only within the leading plugin_root_glob prefix, or as the trailing :*
        star_positions = [i for i, c in enumerate(body) if c == "*"]
        for pos in star_positions:
            in_glob_prefix = body.startswith(G) and pos < len(G)
            is_the_trailing_star = is_trailing_star and pos == len(body) - 1
            assert in_glob_prefix or is_the_trailing_star, (
                f"mid-argument wildcard in rule {rule!r} at position {pos}"
            )
        if "/scripts/" in body:
            assert body.startswith(f"{G}/scripts/"), (
                f"rule naming a plugin script must begin with plugin_root_glob + /scripts/: {rule!r}"
            )


# --------------------------------------------------------------------------------------------- #
# AC-PFM-6 — no silent subsumption across tiers, no blanket scripts-dir grant
# --------------------------------------------------------------------------------------------- #


def test_no_rule_subsumes_a_different_tier():
    doc = load_map()
    violations = subsumption_violations(doc)
    assert not violations, "subsumption violations:\n" + "\n".join(violations)

    # no bare <glob>/scripts/:* rule at any tier
    G = doc["plugin_root_glob"]
    blanket = f"Bash({G}/scripts/:*)"
    rules = {e["rule"] for e in doc["entries"]}
    assert blanket not in rules, "a blanket scripts-dir grant is not a legal rule at any tier"


def test_scripts_dir_rules_name_a_concrete_script():
    doc = load_map()
    violations = concrete_script_violations(REPO_ROOT, doc)
    assert not violations, "concrete-script violations:\n" + "\n".join(violations)


# --------------------------------------------------------------------------------------------- #
# AC-PFM-5 — negative controls (the checks are not vacuous)
# --------------------------------------------------------------------------------------------- #


def _make_fixture_tree(tmp_path, extra_script=None, extra_mode=None, extra_content=b"#!/bin/sh\necho hi\n"):
    """Copy the real scripts/, skills/, hooks/ trees into a throwaway root, optionally adding one
    extra file under scripts/."""
    root = tmp_path / "fixture-tree"
    root.mkdir(parents=True)
    shutil.copytree(os.path.join(REPO_ROOT, "scripts"), root / "scripts")
    if extra_script is not None:
        p = root / "scripts" / extra_script
        p.write_bytes(extra_content)
        if extra_mode is not None:
            os.chmod(p, extra_mode)
    return str(root)


def test_coverage_assertion_is_not_vacuous(tmp_path):
    """(a) a tree copy carrying one extra, un-tiered scripts/*.py file must FAIL coverage."""
    tree = _make_fixture_tree(tmp_path, extra_script="foundry-brand-new-tool.py", extra_mode=0o644,
                               extra_content=b"#!/usr/bin/env python3\nprint('new')\n")
    doc = load_map()
    violations = coverage_violations(tree, doc)
    assert any("foundry-brand-new-tool.py" in v for v in violations), (
        "coverage check did not fire on an extra un-tiered .py file"
    )


def test_negative_controls_all_fire(tmp_path):
    doc = load_map()

    # (b) an extra un-tiered scripts/*.sh file (owner-executable)
    tree_b = _make_fixture_tree(
        tmp_path / "b", extra_script="foundry-brand-new-tool.sh", extra_mode=0o755,
        extra_content=b"#!/bin/sh\necho hi\n",
    )
    violations_b = coverage_violations(tree_b, doc)
    assert any("foundry-brand-new-tool.sh" in v for v in violations_b), (
        "coverage check did not fire on an extra un-tiered owner-executable .sh file"
    )

    # (c) an extra un-tiered extensionless owner-executable file
    tree_c = _make_fixture_tree(
        tmp_path / "c", extra_script="foundry-brand-new-tool", extra_mode=0o755,
        extra_content=b"#!/bin/sh\necho hi\n",
    )
    violations_c = coverage_violations(tree_c, doc)
    assert any("foundry-brand-new-tool" in v for v in violations_c), (
        "coverage check did not fire on an extra un-tiered extensionless owner-executable file"
    )

    # (d) a map naming a scripts/ basename absent from the tree (reverse-direction/dangling check)
    tree_d = _make_fixture_tree(tmp_path / "d")
    doc_d = json.loads(json.dumps(doc))
    doc_d["entries"].append(
        {
            "rule": f"Bash({doc['plugin_root_glob']}/scripts/foundry-does-not-exist.py:*)",
            "tier": "allow",
            "rationale": "fixture: a rule naming a script absent from the tree",
        }
    )
    violations_d = dangling_entry_violations(tree_d, doc_d)
    assert any("foundry-does-not-exist.py" in v for v in violations_d), (
        "dangling-entry check did not fire on a rule naming an absent script"
    )

    # (e) a map carrying a blanket allow beneath an ask pin (the subsumption check)
    tree_e = _make_fixture_tree(tmp_path / "e")
    doc_e = json.loads(json.dumps(doc))
    G = doc["plugin_root_glob"]
    doc_e["entries"].append(
        {
            "rule": f"Bash({G}/scripts/:*)",
            "tier": "allow",
            "rationale": "fixture: a blanket scripts-dir allow beneath an ask pin",
        }
    )
    violations_e = subsumption_violations(doc_e)
    assert violations_e, "subsumption check did not fire on a blanket scripts-dir allow beneath an ask pin"


# --------------------------------------------------------------------------------------------- #
# AC-PFM-7 — the repo's own health gate is unchanged (belt-and-braces; the contract also runs the
# CLI directly)
# --------------------------------------------------------------------------------------------- #


def test_doctor_green_regression():
    import subprocess

    result = subprocess.run(
        ["python3", os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert "DOCTOR-GREEN" in result.stdout, f"doctor did not report GREEN:\n{result.stdout}\n{result.stderr}"
