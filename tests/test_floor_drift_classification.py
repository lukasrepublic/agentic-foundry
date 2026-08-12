"""feat-foundry-onboarding-floor-drift-classification — AC-FDC-5, -6, -8.

The two implementations of the drift classifier — cli/src/permissionFloor.mjs and
scripts/foundry_permission_floor.py — have always claimed to agree "by construction" on the shared
subsumption rule. That claim was a comment. AC-FDC-6 makes it a DIFFERENTIAL: every case in
tests/fixtures/floor-drift-corpus.json is run through BOTH classifiers against the shipped map, and
the two must name the identical set of rules for every drift class.

It found a real divergence on the first comparison. For an absolute, version-resolved rule — what
the harness's ask-to-allow persist writes into settings.local.json — Python folded and Node did not,
so the CLI reported 42 rules absent that the doctor could see were covered. The consuming reconcile
atom writes what allow-absent names, so it would have written 42 duplicates into the permission
floor. The corpus case `full-floor-home-expanded` is what pins that shut.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import foundry_permission_floor as pf  # noqa: E402

CORPUS_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "floor-drift-corpus.json")
MAP_PATH = os.path.join(REPO_ROOT, "docs", "permission-floor.json")
CLI_DIR = os.path.join(REPO_ROOT, "cli")

# stale-plugin-path is an ENVIRONMENT PROBE, not a classification of the target's rules: Python
# expands the glob against the real filesystem while the Node side takes an injected expansion.
# Comparing it would compare the two harnesses, not the two classifiers. Excluded by name, stated
# rather than silently dropped.
ENVIRONMENT_CLASSES = frozenset({"stale-plugin-path"})
COMPARED_CLASSES = tuple(c for c in pf.RANK if c not in ENVIRONMENT_CLASSES)

# The classes that NAME a map rule, and so have a rule set to compare and to require coverage over.
# settings-unreadable and unclassified deliberately name none — unclassified reports a tool prefix
# and a count, never a body (AC-DPF-2(c)) — so they are compared as empty on both sides rather than
# being held to a "must fire with rules" bar they can never meet.
RULE_NAMING_CLASSES = tuple(
    c for c in COMPARED_CLASSES if c not in ("settings-unreadable", "unclassified")
)


def _corpus():
    with open(CORPUS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _python_rules(case, home):
    with open(MAP_PATH, encoding="utf-8") as fh:
        floor_doc = json.load(fh)
    effective = {
        tier: [{"raw": r["rule"], "label": r["origin"]} for r in case["effective"].get(tier, [])]
        for tier in ("allow", "ask", "deny")
    }
    _lines, _counts, _ceremony, rules = pf._classify(floor_doc, effective, [], home)
    return {k: sorted(v) for k, v in rules.items() if k not in ENVIRONMENT_CLASSES}


_NODE_DRIVER = r"""
import { readFileSync } from 'node:fs';
// the module is imported by ABSOLUTE url so this driver can live outside the repo tree — writing a
// scratch file into cli/ would leave a stray artifact behind whenever a run is interrupted
const { loadMap, classifyDrift } = await import(process.argv[3]);
const corpus = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const map = loadMap('./permission-floor.json');
const out = {};
for (const c of corpus.cases) {
  const effective = { allow: [], ask: [], deny: [] };
  for (const tier of ['allow', 'ask', 'deny']) {
    for (const r of (c.effective[tier] || [])) {
      effective[tier].push({ rule: r.rule, origin: r.origin, tierKey: tier });
    }
  }
  // pluginRootExpansion is injected non-empty so the environment probe never fires; the Python
  // side's own expansion is excluded from the comparison for the same reason.
  const findings = classifyDrift(map, effective, {
    pluginRootExpansion: ['injected'], home: corpus.home,
  });
  const byClass = {};
  for (const f of findings) {
    // the rule TEXT each class names — blanket-allow names the folded reach the decision was made
    // on, matching what the Python twin records; the classes that name no map rule contribute none
    let rule = null;
    if (f.class === 'blanket-allow') rule = `Bash(${f.folded})`;
    else if (['settings-unreadable', 'stale-plugin-path', 'unclassified'].includes(f.class)) rule = null;
    else rule = f.rule;
    if (rule === null) continue;
    (byClass[f.class] ||= []).push(rule);
  }
  out[c.name] = Object.fromEntries(Object.entries(byClass).map(([k, v]) => [k, v.sort()]));
}
process.stdout.write(JSON.stringify(out));
"""


def _node_rules_all_cases(tmp_path):
    """Drive the Node classifier over the whole corpus in one child process."""
    driver = tmp_path / "floor-drift-driver.mjs"
    driver.write_text(_NODE_DRIVER, encoding="utf-8")
    module_url = pathlib.Path(CLI_DIR, "src", "permissionFloor.mjs").as_uri()
    # cwd is cli/ so the driver's relative map load resolves; the module itself comes in by url
    proc = subprocess.run(
        ["node", str(driver), CORPUS_PATH, module_url],
        capture_output=True, text=True, cwd=CLI_DIR,
    )
    if proc.returncode != 0:
        pytest.fail(f"node driver failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


# ============================================================================================== #
# AC-FDC-5 — the vocabulary carries the new classes, in BOTH implementations
# ============================================================================================== #


def test_vocabulary_carries_new_classes_both_sides():
    for cls in ("ask-absent", "tier-conflict"):
        assert cls in pf.RANK, f"{cls} missing from the Python vocabulary"

    proc = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {DRIFT_CLASSES} from './src/permissionFloor.mjs';"
         "process.stdout.write(JSON.stringify(DRIFT_CLASSES));"],
        capture_output=True, text=True, cwd=CLI_DIR,
    )
    assert proc.returncode == 0, proc.stderr
    node_classes = json.loads(proc.stdout)
    for cls in ("ask-absent", "tier-conflict"):
        assert cls in node_classes, f"{cls} missing from the Node vocabulary"

    # asserted against each module's OWN exported list, so the two vocabularies cannot drift apart
    # behind a third restated copy in this file
    assert set(node_classes) == set(pf.RANK)


def test_the_two_new_classes_are_informational():
    # An absent `allow` is informational because its effect is MORE prompting, never less. An
    # absent `ask` is the same — it opens nothing on its own — and a tier-conflict rule is present
    # and deliberately placed. Neither belongs in the actionable band that reaches the
    # session-start banner, and _ACTIONABLE_RANKS must be unchanged by this atom.
    assert "ask-absent" in pf._INFORMATIONAL_RANKS
    assert "tier-conflict" in pf._INFORMATIONAL_RANKS
    assert pf._ACTIONABLE_RANKS == frozenset(
        ("blanket-allow", "ask-shadowed-ceremony", "ask-shadowed",
         "deny-missing", "settings-unreadable", "stale-plugin-path")
    )


# ============================================================================================== #
# AC-FDC-6 — THE DIFFERENTIAL
# ============================================================================================== #


def test_node_and_python_agree_on_corpus(tmp_path):
    corpus = _corpus()
    node_all = _node_rules_all_cases(tmp_path)
    mismatches = []
    for case in corpus["cases"]:
        py = _python_rules(case, corpus["home"])
        node = node_all[case["name"]]
        for cls in COMPARED_CLASSES:
            p = sorted(py.get(cls, []))
            n = sorted(node.get(cls, []))
            if p != n:
                mismatches.append(
                    f"case {case['name']!r} class {cls!r}:\n"
                    f"    python-only: {sorted(set(p) - set(n))[:3]}\n"
                    f"    node-only:   {sorted(set(n) - set(p))[:3]}\n"
                    f"    (python {len(p)} rules, node {len(n)} rules)"
                )
    assert not mismatches, (
        "the two classifiers named different rule sets:\n" + "\n".join(mismatches)
    )


def test_corpus_covers_every_drift_class():
    # The differential passes trivially over an empty or one-sided corpus. This is what keeps it
    # honest: every compared class must be exercised, and the absence classes must appear BOTH
    # firing and not firing, so a class that fired unconditionally could not survive.
    corpus = _corpus()
    fired = {c: 0 for c in COMPARED_CLASSES}
    silent = {c: 0 for c in COMPARED_CLASSES}
    for case in corpus["cases"]:
        py = _python_rules(case, corpus["home"])
        for cls in COMPARED_CLASSES:
            if py.get(cls):
                fired[cls] += 1
            else:
                silent[cls] += 1

    never_fired = [c for c in RULE_NAMING_CLASSES if fired[c] == 0]
    assert not never_fired, f"corpus never exercises: {never_fired}"

    for cls in ("allow-absent", "ask-absent", "deny-missing", "tier-conflict"):
        assert silent[cls] > 0, f"{cls} fires in every case — the corpus cannot detect an always-on class"

    # and the canonicalization case is present by name: it is the one that found the divergence
    names = {c["name"] for c in corpus["cases"]}
    assert "full-floor-home-expanded" in names


def test_the_home_expanded_case_is_genuinely_covered():
    # Guards the corpus itself. `full-floor-home-expanded` only proves anything if those rules are
    # actually recognised as covering — if the expansion were wrong, the case would report 42
    # allow-absent on BOTH sides and the differential would pass while proving nothing.
    corpus = _corpus()
    case = next(c for c in corpus["cases"] if c["name"] == "full-floor-home-expanded")
    py = _python_rules(case, corpus["home"])
    assert py.get("allow-absent", []) == [], "the home-expanded allow rules were not seen as covering"
    assert any("plugins/cache/agentic-foundry/foundry/" in r["rule"]
               for r in case["effective"]["allow"]), "the case is not actually home-expanded"


# ============================================================================================== #
# AC-FDC-8 — read-only, asserted at the OUTCOME level
# ============================================================================================== #


def test_classification_emits_no_write_event(tmp_path):
    # The standard the shipped suite already sets: a sys.addaudithook write-event witness over a
    # real run, never an enumeration of forbidden call shapes — an implementation can satisfy an
    # enumeration while writing through a shape the enumeration missed.
    witness = tmp_path / "witness.py"
    witness.write_text(
        "import sys, json, os\n"
        "sys.path.insert(0, %r)\n" % os.path.join(REPO_ROOT, "scripts") +
        "violations = []\n"
        "def hook(event, args):\n"
        "    if event in ('open',) and len(args) > 1 and args[1] and any(m in str(args[1]) for m in ('w','a','+','x')):\n"
        "        violations.append((event, str(args[0])))\n"
        "    elif event in ('os.remove','os.rename','os.mkdir','os.rmdir','shutil.copyfile'):\n"
        "        violations.append((event, str(args[:1])))\n"
        "import foundry_permission_floor as pf\n"
        "corpus = json.load(open(%r))\n" % CORPUS_PATH +
        "floor = json.load(open(%r))\n" % MAP_PATH +
        "sys.addaudithook(hook)\n"
        "for case in corpus['cases']:\n"
        "    eff = {t: [{'raw': r['rule'], 'label': r['origin']} for r in case['effective'].get(t, [])]\n"
        "           for t in ('allow','ask','deny')}\n"
        "    pf._classify(floor, eff, [], corpus['home'])\n"
        "print(json.dumps(violations))\n"
    )
    proc = subprocess.run([sys.executable, str(witness)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [], f"classification performed a write: {proc.stdout}"
