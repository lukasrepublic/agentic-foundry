"""feat-foundry-config-drift-upgrade (v2.1) — AC-CFG-1..6.

Drives the REAL CLI over fixture trees as a subprocess and reads back BOTH the exit code and the on-disk
bytes. Test names are the contract's `-k` selectors; each anti-tautology case has its OWN checkpoint, because
`pytest -k` proves only "everything matched passed", never "each named case exists".
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO_ROOT, "scripts", "foundry-config.py")
OPS = ".claude/foundry-operators.json"
PROJ = ".claude/foundry-project.json"
BASELINE = ".claude/foundry-config-baseline.json"

GOOD_OPS = {"schema_version": 1,
            "operators": {"op_lukas": {"name": "Lukas", "github": "lukasrepublic", "added_at": "2026-06-12"}}}
GOOD_PROJ = {"schema_version": 1, "project": {"name": "demo"},
             "repos": {"app": {"path": "app", "datastores": ["postgres"]}}}


# ------------------------------------------------------------------ helpers --
def _write(root, rel, doc, raw=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(raw if raw is not None else json.dumps(doc, indent=2) + "\n")
    return p


def _seed(tmp_path, ops=None, proj=None):
    root = str(tmp_path)
    _write(root, OPS, ops if ops is not None else GOOD_OPS)
    _write(root, PROJ, proj if proj is not None else GOOD_PROJ)
    return root


def _run(root, *args, env=None):
    e = dict(os.environ)
    e.pop("CLAUDE_PROJECT_DIR", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, CLI, "--root", root] + list(args),
                          capture_output=True, text=True, timeout=60, env=e)


def _adopt(root):
    r = _run(root, "adopt", "--yes")
    assert r.returncode == 0, r.stderr
    return r


def _hash_tree(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _classes(root):
    r = _run(root, "check", "--json")
    return r, {(d["file"], d["pointer"]): d["class"] for d in json.loads(r.stdout)["drift"]}


# ------------------------------------------------------------- AC-CFG-1 ------
def test_baseline_record_shape_and_content(tmp_path):
    root = _seed(tmp_path)
    _adopt(root)
    with open(os.path.join(root, BASELINE), encoding="utf-8") as fh:
        b = json.load(fh)
    assert isinstance(b["schema_version"], int)
    assert b["foundry_version"] and b["recorded_at"].endswith("Z")
    assert set(b["files"]) == {OPS, PROJ}
    # the VERBATIM document is recorded, not a hash -- that is what makes per-key classification possible
    assert b["files"][OPS]["document"] == GOOD_OPS
    assert b["files"][PROJ]["schema_version"] == 1


# ------------------------------------------------------------- AC-CFG-2 ------
def test_classification_all_four_classes_and_arrays_atomic(tmp_path):
    root = _seed(tmp_path)
    _adopt(root)
    edited = json.loads(json.dumps(GOOD_PROJ))
    edited["project"]["name"] = "renamed"              # local-edit
    edited["extra_key"] = "added"                      # local-addition
    del edited["repos"]["app"]["path"]                 # locally-removed
    edited["repos"]["app"]["datastores"] = ["postgres", "redis"]   # array = ONE atomic leaf
    _write(root, PROJ, edited)
    r, cls = _classes(root)
    assert r.returncode == 2
    assert cls[(PROJ, "/project/name")] == "local-edit"
    assert cls[(PROJ, "/extra_key")] == "local-addition"
    assert cls[(PROJ, "/repos/app/path")] == "locally-removed"
    # the array reports at its OWN pointer, never per index
    assert cls[(PROJ, "/repos/app/datastores")] == "local-edit"
    assert not any(p.startswith("/repos/app/datastores/") for _f, p in cls)
    assert (PROJ, "/schema_version") not in cls        # untouched keys are clean, not reported


# ------------------------------------------------------------- AC-CFG-3 ------
def test_check_readonly_exitcodes(tmp_path):
    root = _seed(tmp_path)
    _adopt(root)
    assert _run(root, "check").returncode == 0                     # clean
    edited = json.loads(json.dumps(GOOD_PROJ)); edited["project"]["name"] = "x"
    _write(root, PROJ, edited)
    assert _run(root, "check").returncode == 2                     # findings
    _write(root, PROJ, None, raw="{not json")
    assert _run(root, "check").returncode == 1                     # error dominates
    # drift and schema violations are reported as DISTINCT sections
    root2 = _seed(tmp_path / "b", ops={"schema_version": 1, "operators": {"op_x": {"name": "n"}}})
    out = _run(root2, "check").stdout
    assert "schema violations" in out and "UNBASELINED" in out


def test_unbaselined_and_corrupt_baseline_exit_codes(tmp_path):
    """The v2.1 Blocks: unbaselined must never exit 0, and a corrupt baseline must never be read as absent."""
    root = _seed(tmp_path)
    r = _run(root, "check")
    assert r.returncode == 2, "a fresh install with no baseline must be a FINDING, never a clean pass"
    assert "UNBASELINED" in r.stdout
    payload = json.loads(_run(root, "check", "--json").stdout)
    assert payload["unbaselined"] == [OPS, PROJ] and payload["baseline_present"] is False
    assert payload["drift"] == [], "unbaselined must not be rendered as every key being drift"
    _adopt(root)
    _write(root, BASELINE, None, raw="{corrupt")
    r2 = _run(root, "check")
    assert r2.returncode == 1, "a present-but-unparseable baseline is an ERROR, never silently 'no baseline'"


# ------------------------------------------------------------- AC-CFG-4 ------
def test_adopt_records_and_gates(tmp_path):
    root = _seed(tmp_path)
    assert not os.path.exists(os.path.join(root, BASELINE))
    _adopt(root)
    assert os.path.exists(os.path.join(root, BASELINE))
    r = _run(root, "adopt", "--yes")                    # existing baseline, no --force
    assert r.returncode == 1 and "--force" in r.stderr
    # schema-invalid config is refused PRE-WRITE
    root2 = _seed(tmp_path / "c", ops={"schema_version": 1, "operators": {"op_x": {"name": "n"}}})
    r2 = _run(root2, "adopt", "--yes")
    assert r2.returncode == 1 and "refusing" in r2.stderr
    assert not os.path.exists(os.path.join(root2, BASELINE))


def test_adopt_refusals_exit_nonzero(tmp_path):
    """A refusal that prints a message and exits 0 is invisible to any CI gate."""
    root = _seed(tmp_path)
    assert _run(root, "adopt").returncode != 0                       # no --yes
    _adopt(root)
    assert _run(root, "adopt", "--yes").returncode != 0              # no --force
    _write(root, OPS, {"schema_version": 1, "operators": {"op_x": {"github": "g"}}})
    assert _run(root, "adopt", "--yes", "--force").returncode != 0   # schema-invalid


# ------------------------------------------------------------- AC-CFG-5 ------
def test_schema_operators_required_fields(tmp_path):
    root = _seed(tmp_path, ops={"schema_version": 1, "operators": {"op_x": {"name": "n", "github": "g"}}})
    out = _run(root, "check", "--json").stdout
    findings = " ".join(json.loads(out)["schema"])
    assert "added_at" in findings and "op_x" in findings
    root_ok = _seed(tmp_path / "ok")
    assert json.loads(_run(root_ok, "check", "--json").stdout)["schema"] == []


def test_schema_project_structural_spine(tmp_path):
    root = _seed(tmp_path, proj={"project": {"name": "no schema_version"}})
    findings = " ".join(json.loads(_run(root, "check", "--json").stdout)["schema"])
    assert "schema_version" in findings
    # the file is deliberately permissive: unknown adopter keys are NOT violations
    root2 = _seed(tmp_path / "p2", proj={"schema_version": 1, "totally_custom": {"a": 1}})
    assert json.loads(_run(root2, "check", "--json").stdout)["schema"] == []


# ------------------------------------------------- AC-CFG-6 anti-tautology ---
def test_antitaut_check_is_nonmutating_hash_all_files(tmp_path):
    root = _seed(tmp_path)
    _adopt(root)
    _write(root, PROJ, {"schema_version": 1, "changed": True})
    before = _hash_tree(root)
    _run(root, "check")
    _run(root, "check", "--json")
    assert _hash_tree(root) == before, "check must write NOTHING, baseline included"


def test_antitaut_two_classes_same_file(tmp_path):
    """DECISIVE: a per-file-hash implementation can only say 'this file changed'. Two distinct classes at
    distinct pointers in ONE file is the per-key claim, made falsifiable."""
    root = _seed(tmp_path)
    _adopt(root)
    edited = json.loads(json.dumps(GOOD_PROJ))
    edited["project"]["name"] = "renamed"     # local-edit
    edited["brand_new"] = 42                  # local-addition
    _write(root, PROJ, edited)
    _r, cls = _classes(root)
    same_file = {p: c for (f, p), c in cls.items() if f == PROJ}
    assert same_file["/project/name"] == "local-edit"
    assert same_file["/brand_new"] == "local-addition"
    assert len(set(same_file.values())) >= 2


def test_antitaut_formatting_only_is_clean(tmp_path):
    """DECISIVE: a textual/serialization comparison cannot pass this. Same values, different bytes."""
    root = _seed(tmp_path)
    _adopt(root)
    reordered = {"repos": {"app": {"datastores": ["postgres"], "path": "app"}},
                 "project": {"name": "demo"}, "schema_version": 1}
    raw = json.dumps(reordered, indent=8, sort_keys=False) + "\n\n"
    with open(os.path.join(root, PROJ), "rb") as fh:
        before_bytes = fh.read()
    _write(root, PROJ, None, raw=raw)
    with open(os.path.join(root, PROJ), "rb") as fh:
        assert fh.read() != before_bytes, "fixture must actually change the bytes"
    r, cls = _classes(root)
    assert [c for (f, _p), c in cls.items() if f == PROJ] == []
    assert r.returncode == 0, "a reformat is not drift"


def test_antitaut_adopt_refuses_ungated(tmp_path):
    root = _seed(tmp_path)
    r = _run(root, "adopt")
    assert r.returncode != 0 and not os.path.exists(os.path.join(root, BASELINE))
    _adopt(root)
    with open(os.path.join(root, BASELINE), "rb") as fh:
        before = fh.read()
    r2 = _run(root, "adopt", "--yes")
    assert r2.returncode != 0
    with open(os.path.join(root, BASELINE), "rb") as fh:
        assert fh.read() == before, "a refused adopt must leave the baseline byte-unchanged"


def test_antitaut_adopt_force_plans_before_blessing(tmp_path):
    root = _seed(tmp_path)
    _adopt(root)
    edited = json.loads(json.dumps(GOOD_PROJ)); edited["project"]["name"] = "renamed"
    _write(root, PROJ, edited)
    r = _run(root, "adopt", "--yes", "--force")
    assert r.returncode == 0
    assert "REPLACE" in r.stdout and "/project/name" in r.stdout, \
        "adopt --force must show what it is about to bless"
    assert r.stdout.index("/project/name") < r.stdout.index("recorded baseline"), "plan comes BEFORE the write"


def test_antitaut_atomic_write_interrupted_leaves_prior_intact(tmp_path):
    """Without this, a plain open(path,'w').write(...) satisfies every other case."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("fcfg", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    target = tmp_path / "b.json"
    target.write_text('{"original": true}\n', encoding="utf-8")
    class Boom(Exception):
        pass
    real_replace = os.replace
    def exploding_replace(*_a, **_k):
        raise Boom("interrupted mid-write")
    os.replace = exploding_replace
    try:
        with pytest.raises(Boom):
            mod.atomic_write(str(target), '{"new": true}\n')
    finally:
        os.replace = real_replace
    assert json.loads(target.read_text(encoding="utf-8")) == {"original": True}
    strays = [p for p in os.listdir(str(tmp_path)) if p.startswith(".foundry-config.")]
    assert strays == [], "no stray temp file may survive an interrupted write: %r" % strays


def test_antitaut_schema_distinct_and_validator_absent_skip(tmp_path):
    root = _seed(tmp_path, ops={"schema_version": 1, "operators": {"op_x": {"name": "n", "github": "g"}}})
    payload = json.loads(_run(root, "check", "--json").stdout)
    assert payload["schema"] and payload["drift"] == [], "schema findings are DISTINCT from drift"
    # Simulate no jsonschema: the dependency-free fallback must STILL convict the missing field.
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "jsonschema.py").write_text("raise ImportError('blocked')\n", encoding="utf-8")
    r = _run(root, "check", "--json", env={"PYTHONPATH": str(blocker)})
    p2 = json.loads(r.stdout)
    assert any("added_at" in s for s in p2["schema"]), "the identity shape check must not evaporate"
    assert p2["skips"], "a skipped richer check must be REPORTED, never a silent false PASS"
    assert r.returncode == 2


def test_antitaut_managed_files_never_written(tmp_path):
    """Dynamic sweep (happy AND error branches) + a STATIC ALLOWLIST of write primitives.

    A sample cannot prove a universal, and a denylist of one spelling is trivially evaded (a direct
    `os.replace`, `Path.write_text`, `shutil.copyfile`, `subprocess`, or one variable hop all slip past).
    So the static half enumerates every write-capable primitive in the module and requires each to be either
    absent or inside `atomic_write`, whose sole call site must target the baseline.
    """
    root = _seed(tmp_path)
    before = {rel: open(os.path.join(root, rel), "rb").read() for rel in (OPS, PROJ)}
    combos = [("check",), ("check", "--json"), ("adopt",), ("adopt", "--yes"), ("adopt", "--yes", "--force")]
    for args in combos:
        _run(root, *args)
    # ...and again over ERROR branches, where an "any error path" regression would hide.
    # (the combos above already recorded a baseline via `adopt --yes`; corrupt it in place)
    _write(root, BASELINE, None, raw="{corrupt")
    for args in combos:
        _run(root, *args)
    _write(root, OPS, {"schema_version": 1, "operators": {"bad": {}}})   # schema-invalid
    before[OPS] = open(os.path.join(root, OPS), "rb").read()
    for args in combos:
        _run(root, *args)
    for rel, b in before.items():
        with open(os.path.join(root, rel), "rb") as fh:
            assert fh.read() == b, "%s was written by some verb/flag/error combination" % rel

    import re
    src = open(CLI, encoding="utf-8").read()
    # Span arithmetic, NOT substring containment: `"os.replace(" in body_of_atomic` is true for EVERY
    # os.replace in the file, since atomic_write's own body contains one. The check must be positional.
    a_start = src.index("def atomic_write(")
    a_end = src.index("\ndef ", a_start + 1)
    # NB: the file-mode class is spelled as an ALTERNATION rather than a regex character class. The
    # bracketed form of these three mode letters spells a term the pre-publication leak scanner
    # denylists; the gate is fail-closed and correct, so the regex bends, not the denylist. (Do not
    # 'simplify' this back to a character class -- and do not write the offending form in a comment
    # either, which is how this was caught the second time.)
    WRITE_PRIMITIVES = [r"open\([^)]*['\"](?:w|a|x)", r"os\.replace\(", r"os\.rename\(", r"\.write_text\(",
                        r"\.write_bytes\(", r"shutil\.", r"subprocess\.", r"os\.open\(", r"os\.symlink\("]
    for pat in WRITE_PRIMITIVES:
        for m in re.finditer(pat, src):
            assert a_start <= m.start() < a_end, (
                "write primitive %r at offset %d is OUTSIDE atomic_write (span %d-%d) -- the no-write "
                "guarantee is only as strong as this allowlist" % (m.group(0), m.start(), a_start, a_end))
    calls = re.findall(r"(?<!def )atomic_write\(([^)]*)\)", src)
    assert calls, "expected the single write helper to be used"
    for call in calls:
        assert "BASELINE_REL" in call, "the only write target may be the baseline, found: %r" % call


def test_antitaut_skill_surface_names_both_verbs(tmp_path):
    """A correct engine that ships undiscoverable delivers nothing."""
    skill = os.path.join(REPO_ROOT, "skills", "upgrade", "SKILL.md")
    text = open(skill, encoding="utf-8").read()
    assert "foundry-config.py" in text
    for verb in ("check", "adopt"):
        assert verb in text, "the operator-facing skill must name the `%s` verb" % verb
    assert "nothing left for this skill to orchestrate" not in text, "the tombstone text must be replaced"
