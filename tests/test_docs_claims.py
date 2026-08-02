"""The anti-doc-rot lock (GP-3 Phase A docs pass).

The observed failure this gate prevents: user-facing docs drifted three ways at once — a
README status version, a different install pin, and a stale test-count claim — because
nothing machine-checked any of them. Quickstart drift is the #1 copy-paste killer in the
docs-benchmark research; this suite turns each load-bearing claim into an assertion.
"""
import json
import os
import re
import subprocess

from conftest import REPO_ROOT

README = os.path.join(REPO_ROOT, "README.md")
QUICKSTART = os.path.join(REPO_ROOT, "docs", "QUICKSTART.md")
TROUBLESHOOTING = os.path.join(REPO_ROOT, "docs", "troubleshooting.md")
CHANGELOG = os.path.join(REPO_ROOT, "CHANGELOG.md")
CONTROL_PLANE_HOWTO = os.path.join(REPO_ROOT, "docs", "how-to", "multi-repo-control-plane.md")
CONTROL_PLANE_HOWTO_RELPATH = "docs/how-to/multi-repo-control-plane.md"


def _read(p):
    return open(p, encoding="utf-8").read()


def _plugin_version():
    with open(os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        return json.load(fh)["version"]


def _marketplace():
    with open(os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
        return json.load(fh)["plugins"][0]


# ------------------------------------------------------------------ the version identity --
def test_manifests_agree():
    mp = _marketplace()
    assert mp["version"] == _plugin_version(), "plugin.json and marketplace.json disagree"
    assert mp["source"]["ref"] == "v" + _plugin_version(), "marketplace ref must be vVERSION"


def test_every_install_pin_matches_the_manifests():
    """Every `marketplace add …#vX.Y.Z` snippet in user-facing docs pins the shipped version.
    The exact drift that shipped once: README pinning one version while claiming another."""
    want = "#v" + _plugin_version()
    for doc in (README, QUICKSTART, TROUBLESHOOTING):
        pins = re.findall(r"marketplace add lukasrepublic/agentic-foundry(#v[\d.]+)", _read(doc))
        assert pins, f"{os.path.basename(doc)} has no install pin to check"
        for pin in pins:
            assert pin == want, f"{os.path.basename(doc)} pins {pin}, shipped is {want}"


def test_readme_status_matches_the_manifests():
    m = re.search(r"\*\*Status: v([\d.]+)\.\*\*", _read(README))
    assert m, "README must carry a **Status: vX.Y.Z.** line"
    assert m.group(1) == _plugin_version()


def test_changelog_has_a_section_for_the_shipped_version():
    assert f"## v{_plugin_version()}" in _read(CHANGELOG)


# ------------------------------------------------------------------ the evidence claims --
def test_test_count_claim_is_true():
    """README says 'More than 1000 pytest tests'. The claim is COLLECTION-true (parametrization
    expands ~911 test functions past 1,000 cases), so the lock measures a real collection —
    the same number an adopter's `pytest --collect-only` reports."""
    import subprocess, sys
    assert "More than 1000 pytest tests" in _read(README)
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
                        "-p", "no:cacheprovider"],
                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    m = re.search(r"(\d+) tests collected", r.stdout)
    assert m, f"could not read the collection count: {r.stdout[-300:]}"
    assert int(m.group(1)) >= 1000, f"the README claims >1,000 tests; collected {m.group(1)}"


def _quick_ref_roster():
    """(shipped, documented) verb name sets for VERBS-QUICK-REF.md."""
    ref = _read(os.path.join(REPO_ROOT, "docs", "VERBS-QUICK-REF.md"))
    shipped = {d for d in os.listdir(os.path.join(REPO_ROOT, "skills"))
               if os.path.isdir(os.path.join(REPO_ROOT, "skills", d))}
    documented = set(re.findall(r"`/foundry:([a-z0-9-]+)`", ref))
    return shipped, documented


def test_every_shipped_skill_is_in_the_verb_reference():
    """VERBS-QUICK-REF promises the full catalog; a skill missing from it is invisible."""
    shipped, documented = _quick_ref_roster()
    missing = sorted(shipped - documented)
    assert not missing, f"skills shipped but absent from VERBS-QUICK-REF.md: {missing}"


def test_the_verb_reference_lists_no_phantom_verbs():
    """The other direction, which went unchecked and is the worse failure: a documented verb that
    does not exist sends the reader to type a command that silently does nothing. A retired or
    renamed skill must be removed from the reference in the same change that retires it."""
    shipped, documented = _quick_ref_roster()
    phantom = sorted(documented - shipped)
    assert not phantom, (f"VERBS-QUICK-REF.md documents verbs with no skills/<verb>/ directory: "
                         f"{phantom}")


# ------------------------------------------------------------------ link + posture locks --
def test_relative_doc_links_resolve():
    """Every relative markdown link in the core user-facing docs points at a real file."""
    docs = [README, QUICKSTART, TROUBLESHOOTING,
            os.path.join(REPO_ROOT, "docs", "README.md"),
            os.path.join(REPO_ROOT, "docs", "DESIGN.md"),
            os.path.join(REPO_ROOT, "docs", "merge-floor.md"),
            os.path.join(REPO_ROOT, "docs", "faq.md"),
            os.path.join(REPO_ROOT, "llms.txt")]
    how_to = os.path.join(REPO_ROOT, "docs", "how-to")
    docs += [os.path.join(how_to, f) for f in os.listdir(how_to)]
    broken = []
    for doc in docs:
        base = os.path.dirname(doc)
        for text, target in re.findall(r"\[([^\]]+)\]\(([^)#]+)(?:#[^)]*)?\)", _read(doc)):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, target))):
                broken.append(f"{os.path.relpath(doc, REPO_ROOT)} → {target}")
    assert not broken, "broken relative links:\n" + "\n".join(broken)


# The corpus is wider than markdown (AC-LPS-6, from the first-run audit's scan-gap lesson:
# a stale phrase survived the markdown-only sweep in a JSON comment, another in script
# output). JSON/YAML/shell are user-visible surfaces. Python stays out: comments there
# legitimately name retired mechanisms as retired, and convicting them would need an
# allowlist longer than the finding list. Any exception is an explicit entry with a
# reason, never a silent skip.
# .txt and .js were added after a pre-publication sweep found journey framing in both
# (requirements-dev.txt, workflows/release-wave.js) — the exact gap this gate exists to close.
# Python stays out per AC-LPS-6: comments there legitimately name retired mechanisms as retired.
_NARRATION_EXTS = (".md", ".json", ".yml", ".yaml", ".sh", ".txt", ".js")
_NARRATION_ALLOWLIST = {}  # relpath -> reason; empty today, and that is the point


# ------------------------------------------------------------ feat-foundry-control-plane-docs --
# AC-CPD-1/-2 — the shipped multi-repo control-plane how-to (structural presence + section
# placement). AC-CPD-3's behaviourally-derived `enforced` roster lives in tests/test_doc_claims.py
# (the COVERED_CLAIMS anti-doc-rot registry), per the spec's "extend, do not duplicate" design note.

_CP_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _cp_section_body(text, keyword):
    """The body of the ONE ATX heading (any level) whose text contains `keyword`
    (case-insensitive), extended through any nested/deeper headings so a subsection stays part
    of its parent's body, and stopping at the next heading of the SAME or a SHALLOWER level."""
    matches = list(_CP_HEADING_RE.finditer(text))
    found = [i for i, m in enumerate(matches) if keyword.lower() in m.group(2).lower()]
    assert len(found) == 1, (
        f"heading keyword {keyword!r} must occur in exactly one heading; found {len(found)} "
        f"(headings: {[m.group(2) for m in matches]})"
    )
    i = found[0]
    level = len(matches[i].group(1))
    body_start = matches[i].end()
    body_end = len(text)
    for j in range(i + 1, len(matches)):
        if len(matches[j].group(1)) <= level:
            body_end = matches[j].start()
            break
    return text[body_start:body_end]


def test_control_plane_how_to_is_shipped_and_linked():
    """AC-CPD-1: the doc ships at the exact path the preflight sibling's contract references, is
    git-tracked (an untracked file does not ship in the packaged plugin), and is reachable by a
    resolving relative link from BOTH docs/README.md and llms.txt."""
    tracked = subprocess.run(
        ["git", "ls-files", CONTROL_PLANE_HOWTO_RELPATH],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tracked == CONTROL_PLANE_HOWTO_RELPATH, (
        f"{CONTROL_PLANE_HOWTO_RELPATH} is not git-tracked (git ls-files returned {tracked!r})"
    )
    assert os.path.isfile(CONTROL_PLANE_HOWTO)

    for doc in (os.path.join(REPO_ROOT, "docs", "README.md"), os.path.join(REPO_ROOT, "llms.txt")):
        text = _read(doc)
        base = os.path.dirname(doc)
        resolved = [
            os.path.normpath(os.path.join(base, target))
            for _text, target in re.findall(r"\[([^\]]+)\]\(([^)#]+)(?:#[^)]*)?\)", text)
            if not target.startswith(("http://", "https://", "mailto:"))
        ]
        assert os.path.normpath(CONTROL_PLANE_HOWTO) in resolved, (
            f"{os.path.relpath(doc, REPO_ROOT)} carries no resolving relative link to "
            f"{CONTROL_PLANE_HOWTO_RELPATH}"
        )


def test_control_plane_how_to_covers_the_pattern():
    """AC-CPD-2: the six required subjects each land in their own keyword-matched ATX section,
    each carrying its pinned literal token(s) inside that section's OWN body."""
    text = _read(CONTROL_PLANE_HOWTO)

    # (1) pattern / submodule
    pattern = _cp_section_body(text, "pattern")
    assert "submodule" in pattern.lower(), "'pattern' section carries no 'submodule' token"

    # (2) registry / repos{}, path, foundry-wt, schema_version — plus schema validation + inert
    #     field marking + the foundry-wt-resolves-`workspace`-itself statement.
    registry = _cp_section_body(text, "registry")
    for tok in ("repos{}", "path", "foundry-wt", "schema_version"):
        assert tok in registry, f"'registry' section missing token {tok!r}"
    m = re.search(r"```json\n(.*?)```", registry, re.S)
    assert m, "'registry' section carries no fenced json example"
    example = json.loads(m.group(1))
    import jsonschema  # declared dependency (requirements.txt); see foundry_contract's own use
    with open(os.path.join(REPO_ROOT, "schema", "foundry-project.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.validate(example, schema)
    assert "inert" in registry.lower(), "'registry' section does not mark any field inert"
    for field in ("kind", "role"):
        assert field in registry, f"'registry' section's inert-field ledger omits {field!r}"
    assert "foundry-wt" in registry and "workspace" in registry.lower(), (
        "'registry' section does not state that `workspace` is resolved by foundry-wt itself"
    )

    # (3) pairing rule / .gitignore, repos{}, spill — both the DANGLE and SPILL directions
    pairing = _cp_section_body(text, "pairing rule")
    for tok in (".gitignore", "repos{}", "spill"):
        assert tok in pairing, f"'pairing rule' section missing token {tok!r}"
    assert "dangl" in pairing.lower(), "'pairing rule' section does not name the DANGLE direction"

    # (4) session / --add-dir, blast radius, permissions.deny
    session = _cp_section_body(text, "session")
    for tok in ("--add-dir", "blast radius", "permissions.deny"):
        assert tok in session, f"'session' section missing token {tok!r}"

    # (5) target_repo / target_repo, .foundry/build-provenance.yaml
    target_repo_section = _cp_section_body(text, "target_repo")
    for tok in ("target_repo", ".foundry/build-provenance.yaml"):
        assert tok in target_repo_section, f"'target_repo' section missing token {tok!r}"

    # (6) enforced — governed fully by AC-CPD-3 (tests/test_doc_claims.py); just confirm the
    #     section exists exactly once and forward-references target_repo freeze.
    enforced = _cp_section_body(text, "enforced")
    assert "target_repo freeze" in enforced


def test_no_journey_narration_in_shipped_docs():
    """The public tree describes what IS, not what was deleted or how it got here. These
    markers are the narration classes scrubbed in the pre-v1 content pass; reintroducing
    one is a regression, not a style choice."""
    forbidden = re.compile(r"the subtraction|41,?365|back-to-basics|\b0/17\b|BTB Phase", re.I)
    # The corpus is the TRACKED set (R5, PR #308 review): what ships is what git tracks. An
    # os.walk would also convict gitignored local cruft (a vendored .venv, runtime .foundry
    # artifacts) and error on any non-UTF-8 bystander -- red gates about the machine, not
    # the tree.
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True,
                             check=True).stdout.decode("utf-8", "surrogateescape").split("\0")
    offenders = []
    for rel in tracked:
        if not rel or not rel.endswith(_NARRATION_EXTS) or rel.startswith("tests/"):
            continue
        if rel in _NARRATION_ALLOWLIST:
            continue
        if forbidden.search(_read(os.path.join(REPO_ROOT, rel))):
            offenders.append(rel)
    assert not offenders, f"journey narration reintroduced in: {offenders}"
