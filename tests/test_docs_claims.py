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


# ------------------------------------------------------------ feat-foundry-docs-truth-reconciliation
# AC-DTR-1/-2/-3 — three verified untruths in shipped prose (spec's header comments locate each):
# (1) skills/id-sync/SKILL.md claimed never-force-sync MACHINE-enforced; nothing enforces it, so
#     every occurrence of the enforcement-claim literal must be a NEGATED claim, and the file must
#     carry zero occurrences of the case-sensitive literal `MACHINE-enforced`.
# (2) docs/troubleshooting.md misattributed a cache-move power to FOUNDRY_PLUGINS_DIR; it only
#     scopes fleet-doctor's installed_plugins.json lookup. Every mention anywhere in docs/**  +
#     README.md must co-occur, within its own SLICE (Terminology: fenced block -> table row ->
#     contiguous non-blank run), with both fleet-doctor scope literals.
# (3) eleven doc sites dropped the <product> segment the shipped `derive_area` projection indexes.
#
# Every check below computes its verdict from PARSED STRUCTURE (occurrence positions, the
# preceding-word test, segment-list lengths) -- never from substring presence in joined text
# (house lesson: "assert on structure, not substrings").

ID_SYNC = os.path.join(REPO_ROOT, "skills", "id-sync", "SKILL.md")
ADOPT_HOWTO = os.path.join(REPO_ROOT, "docs", "how-to", "adopt-on-an-existing-codebase.md")
RECOVER_HOWTO = os.path.join(REPO_ROOT, "docs", "how-to", "recover-from-a-failed-gate.md")

_ENFORCEMENT_RE = re.compile(r"machine-enforced", re.IGNORECASE)


def _preceding_word(text, pos):
    """The whitespace-delimited word immediately before position `pos` in `text`."""
    words = text[:pos].split()
    return words[-1] if words else ""


def _is_negating_word(word):
    """True if `word` IS (once markdown emphasis / punctuation decoration is stripped) the
    literal `not` (case-insensitive) -- so `**NOT`, `(not`, `not:` all still count."""
    stripped = word.strip("*_`()[]{}:;,.\"'“”‘’—-")
    return stripped.lower() == "not"


def _unnegated_enforcement_positions(text):
    """Positions of the case-insensitive `machine-enforced` literal NOT preceded by not/NOT."""
    return [m.start() for m in _ENFORCEMENT_RE.finditer(text)
            if not _is_negating_word(_preceding_word(text, m.start()))]


def _id_sync_unnegated_claim_failures(text):
    """AC-DTR-1 checkpoint 1: zero unnegated occurrences of the enforcement-claim literal
    ANYWHERE in the file (frontmatter description, every heading, every body line), and zero
    occurrences of the case-SENSITIVE literal `MACHINE-enforced` at all (negated or not --
    the honest replacement is spelled lowercase, matching the sibling id-* skills' house form)."""
    failures = []
    if "MACHINE-enforced" in text:
        failures.append("contains the case-sensitive literal 'MACHINE-enforced'")
    unnegated = _unnegated_enforcement_positions(text)
    if unnegated:
        failures.append(f"{len(unnegated)} unnegated occurrence(s) of 'machine-enforced' "
                         f"at position(s) {unnegated}")
    return failures


def test_id_sync_makes_no_unnegated_enforcement_claim():
    """AC-DTR-1 / DTR-1-NO-UNNEGATED-CLAIM-OK."""
    failures = _id_sync_unnegated_claim_failures(_read(ID_SYNC))
    assert not failures, f"skills/id-sync/SKILL.md: {failures}"


_NEVER_FORCE_SYNC_LITERALS = (
    "derive_realization_verdict",
    "machine-derived",
    "records",
    "not a merge block",
)


def _id_sync_section_failures(text):
    """AC-DTR-1 checkpoint 2: the ONE heading located by the stable `never-force-sync` substring
    (never by pre-change heading text) carries no unnegated enforcement claim in its OWN heading
    text, and its body names all four required literals."""
    failures = []
    headings = [m for m in _CP_HEADING_RE.finditer(text) if "never-force-sync" in m.group(2)]
    if len(headings) != 1:
        failures.append(f"expected exactly one heading containing 'never-force-sync'; "
                         f"found {len(headings)}")
        return failures
    heading_text = headings[0].group(2)
    if _unnegated_enforcement_positions(heading_text):
        failures.append(f"heading {heading_text!r} carries an unnegated enforcement claim")
    body = re.sub(r"\s+", " ", _cp_section_body(text, "never-force-sync"))
    for literal in _NEVER_FORCE_SYNC_LITERALS:
        if literal not in body:
            failures.append(f"never-force-sync section body missing literal {literal!r}")
    return failures


def test_id_sync_section_names_the_real_mechanism():
    """AC-DTR-1 / DTR-1-SECTION-NAMES-THE-REAL-MECHANISM-OK."""
    failures = _id_sync_section_failures(_read(ID_SYNC))
    assert not failures, f"skills/id-sync/SKILL.md 'never-force-sync' section: {failures}"


# --- AC-DTR-2 -- FOUNDRY_PLUGINS_DIR is described by what actually reads it ------------------
_FLEET_DOCTOR_SCOPE_LITERALS = ("foundry-fleet-doctor", "installed_plugins.json")
_FOUNDRY_PLUGINS_DIR = "FOUNDRY_PLUGINS_DIR"
_FENCE_RE = re.compile(r"```.*?```", re.S)


def _slice_at(text, pos):
    """The slice (spec Terminology) containing the occurrence at `pos`:
    (i) inside a fenced code block -> that block's full contents;
    (ii) else on a table-row line (first non-ws char `|`) -> that single line;
    (iii) else the maximal run of consecutive non-blank, non-table-row lines containing it.
    A slice never spans a blank line, and never spans two table rows."""
    for m in _FENCE_RE.finditer(text):
        if m.start() <= pos < m.end():
            return text[m.start():m.end()]

    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    if line.lstrip().startswith("|"):
        return line

    def _is_blank(l):
        return l.strip() == ""

    def _is_table_row(l):
        return l.lstrip().startswith("|")

    lines = text.split("\n")
    idx = text.count("\n", 0, line_start)
    start = idx
    while start > 0 and not _is_blank(lines[start - 1]) and not _is_table_row(lines[start - 1]):
        start -= 1
    end = idx
    while (end < len(lines) - 1 and not _is_blank(lines[end + 1])
           and not _is_table_row(lines[end + 1])):
        end += 1
    return "\n".join(lines[start:end + 1])


def _docs_and_readme_texts():
    """{relpath: text} for README.md + every file under docs/** (spec's `docs/**` + README.md)."""
    texts = {"README.md": _read(README)}
    docs_dir = os.path.join(REPO_ROOT, "docs")
    for root, _dirs, files in os.walk(docs_dir):
        for f in files:
            p = os.path.join(root, f)
            try:
                texts[os.path.relpath(p, REPO_ROOT)] = _read(p)
            except UnicodeDecodeError:
                continue
    return texts


def _plugins_dir_scope_failures(file_texts):
    """AC-DTR-2 checkpoint 1: every FOUNDRY_PLUGINS_DIR occurrence's own SLICE contains BOTH
    fleet-doctor scope literals."""
    failures = []
    for relpath, text in file_texts.items():
        for m in re.finditer(re.escape(_FOUNDRY_PLUGINS_DIR), text):
            sl = _slice_at(text, m.start())
            missing = [lit for lit in _FLEET_DOCTOR_SCOPE_LITERALS if lit not in sl]
            if missing:
                failures.append(f"{relpath}@{m.start()}: slice missing {missing}")
    return failures


def _troubleshooting_cache_clear_failures(text):
    """AC-DTR-2 checkpoint 1's second half: the slice carrying the cache-clear command names
    zero occurrences of FOUNDRY_PLUGINS_DIR -- the variable does not move the cache."""
    idx = text.find("rm -rf ~/.claude/plugins/cache/")
    if idx == -1:
        return ["docs/troubleshooting.md no longer carries the cache-clear command at all"]
    sl = _slice_at(text, idx)
    if _FOUNDRY_PLUGINS_DIR in sl:
        return ["the cache-clear command's slice still mentions FOUNDRY_PLUGINS_DIR"]
    return []


def test_plugins_dir_env_var_is_described_by_its_reader():
    """AC-DTR-2 / DTR-2-ENV-VAR-SCOPED-OK."""
    failures = _plugins_dir_scope_failures(_docs_and_readme_texts())
    failures += _troubleshooting_cache_clear_failures(_read(TROUBLESHOOTING))
    assert not failures, "; ".join(failures)


def _quickstart_plugins_dir_note_failures(text):
    """AC-DTR-2 checkpoint 2: QUICKSTART carries >=1 FOUNDRY_PLUGINS_DIR mention that both
    satisfies the co-occurrence rule AND names the default `~/.claude/plugins`."""
    matches = list(re.finditer(re.escape(_FOUNDRY_PLUGINS_DIR), text))
    if not matches:
        return ["docs/QUICKSTART.md carries no FOUNDRY_PLUGINS_DIR mention"]
    for m in matches:
        sl = _slice_at(text, m.start())
        if all(lit in sl for lit in _FLEET_DOCTOR_SCOPE_LITERALS) and "~/.claude/plugins" in sl:
            return []
    return ["no FOUNDRY_PLUGINS_DIR mention in docs/QUICKSTART.md is both co-scoped with the "
            "fleet-doctor literals and names the default ~/.claude/plugins"]


def test_quickstart_documents_the_plugins_dir_override():
    """AC-DTR-2 / DTR-2-QUICKSTART-NOTE-OK."""
    failures = _quickstart_plugins_dir_note_failures(_read(QUICKSTART))
    assert not failures, "; ".join(failures)


# --- AC-DTR-3 -- every concrete spec-path mention in the reconciled doc set is 3+ segments ----
_SPEC_PATH_RUN_RE = re.compile(r"specs/features/([A-Za-z0-9._<>-]+(?:/[A-Za-z0-9._<>-]+)*)")
_TRUNCATED_FIRST_SEGMENTS = ("…", "...")  # the spec's two truncation spellings


def _spec_path_segment_lists(text):
    """Every spec-path mention's segment list (Terminology: the run split on `/`, trailing
    `feat-*.md` element removed when present)."""
    out = []
    for m in _SPEC_PATH_RUN_RE.finditer(text):
        segments = m.group(1).split("/")
        if segments and re.match(r"^feat-.*\.md$", segments[-1]):
            segments = segments[:-1]
        out.append(segments)
    return out


def _reconciled_docs_failures(doc_texts):
    """AC-DTR-3: every non-truncated, non-empty spec-path mention has segment-list length >= 3."""
    failures = []
    for relpath, text in doc_texts.items():
        for segments in _spec_path_segment_lists(text):
            if not segments:
                continue
            if segments[0] in _TRUNCATED_FIRST_SEGMENTS:
                continue
            if len(segments) < 3:
                failures.append(f"{relpath}: spec-path mention {segments} has < 3 segments")
    return failures


_RECONCILED_DOC_SET_PATHS = {
    "README.md": README,
    "docs/QUICKSTART.md": QUICKSTART,
    "docs/how-to/adopt-on-an-existing-codebase.md": ADOPT_HOWTO,
    "docs/how-to/recover-from-a-failed-gate.md": RECOVER_HOWTO,
}


def test_reconciled_docs_carry_the_product_segment():
    """AC-DTR-3 / DTR-3-PATH-SHAPE-OK."""
    doc_texts = {rel: _read(p) for rel, p in _RECONCILED_DOC_SET_PATHS.items()}
    failures = _reconciled_docs_failures(doc_texts)
    assert not failures, "; ".join(failures)


# --- AC-DTR-4 -- the meta-test: four negative controls prove none of the above is vacuous -----
def test_docs_truth_negative_controls_all_fire():
    """AC-DTR-4 / DTR-4-CONTROLS-OK. Four mutated in-memory copies, each of which the
    corresponding checker above MUST report as a FAILURE."""
    # (a) the enforcement-claim literal re-inserted unnegated.
    mutated_a = _read(ID_SYNC) + "\n\nAppendix: this discipline is machine-enforced by policy.\n"
    assert _id_sync_unnegated_claim_failures(mutated_a), "control (a) did not fire"

    # (b) a FOUNDRY_PLUGINS_DIR mention stripped of the fleet-doctor scope literals.
    stripped_row = "| lookup root | override with `FOUNDRY_PLUGINS_DIR` if you need to |\n"
    assert _plugins_dir_scope_failures({"synthetic-b.md": stripped_row}), "control (b) did not fire"

    # (c) a spec-path mention shortened by one segment.
    original = _read(QUICKSTART)
    shortened = original.replace(
        "specs/features/app/export/csv/feat-export-csv.md",
        "specs/features/app/feat-export-csv.md",
        1,
    )
    assert shortened != original, "control (c) setup found nothing to shorten"
    assert _reconciled_docs_failures({"docs/QUICKSTART.md": shortened}), "control (c) did not fire"

    # (d) THE SLICE-SCOPING CONTROL (review B1): both fleet-doctor scope literals present in the
    #     file but OUTSIDE the mention's own slice -- a different table row, and beyond a blank
    #     line -- must still report FAILURE, proving slice-scoping is enforced, not assumed.
    synthetic_d = (
        "| root override | uses `FOUNDRY_PLUGINS_DIR` |\n"
        "| unrelated row | nothing to see here |\n"
        "\n"
        "| doctor lookup | `foundry-fleet-doctor` reads `installed_plugins.json` |\n"
    )
    assert _plugins_dir_scope_failures({"synthetic-d.md": synthetic_d}), "control (d) did not fire"


# --- AC-DTR-4 -- additive-only: every pre-existing case still defined, byte-identical body -----
_PREEXISTING_DOCS_CLAIMS_CASES = (
    "test_manifests_agree",
    "test_every_install_pin_matches_the_manifests",
    "test_readme_status_matches_the_manifests",
    "test_changelog_has_a_section_for_the_shipped_version",
    "test_test_count_claim_is_true",
    "test_every_shipped_skill_is_in_the_verb_reference",
    "test_the_verb_reference_lists_no_phantom_verbs",
    "test_relative_doc_links_resolve",
    "test_control_plane_how_to_is_shipped_and_linked",
    "test_control_plane_how_to_covers_the_pattern",
    "test_no_journey_narration_in_shipped_docs",
)


def _top_level_function_bodies(source):
    """{name: exact source text} for every top-level `def` in `source` (via ast, never a
    substring/line-count heuristic, so a reformat that preserves meaning still convicts)."""
    import ast
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _resolve_docs_claims_base():
    """The merge-base blob of THIS file, as (source_text, error).

    Preferred source: DTR_DOCS_CLAIMS_BASE (the AC-DTR-4 checkpoint's own locator pre-resolves
    `git merge-base HEAD origin/main` and exports the blob to a temp path -- required in a
    SHALLOW checkout, e.g. ci.yml's pytest job, where `git merge-base` cannot resolve `origin/main`
    at all). Falling back to resolving it locally here as well is deliberate and reconciles two
    otherwise-conflicting frozen clauses: AC-DTR-4 says this case "SHALL fail, never skip, when
    [DTR_DOCS_CLAIMS_BASE] is unset or unreadable", while AC-DTR-5 separately requires a BARE
    `python3 -m pytest tests/ -q` (no env var set by anyone) to stay green -- which every ordinary
    (non-shallow) invocation of this suite is. The fail-never-skip guarantee is preserved for the
    case neither source resolves (a shallow clone with no locator-supplied var)."""
    env_path = os.environ.get("DTR_DOCS_CLAIMS_BASE")
    if env_path:
        try:
            with open(env_path, encoding="utf-8") as fh:
                return fh.read(), None
        except OSError as exc:
            return None, f"DTR_DOCS_CLAIMS_BASE={env_path!r} is unreadable: {exc}"

    try:
        mb = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=30)
        if mb.returncode != 0 or not mb.stdout.strip():
            return None, (f"DTR_DOCS_CLAIMS_BASE is unset, and `git merge-base HEAD origin/main` "
                           f"could not resolve it either: {mb.stderr.strip()}")
        sha = mb.stdout.strip()
        show = subprocess.run(["git", "show", f"{sha}:tests/test_docs_claims.py"], cwd=REPO_ROOT,
                               capture_output=True, text=True, timeout=30)
        if show.returncode != 0:
            return None, (f"DTR_DOCS_CLAIMS_BASE is unset, and `git show {sha}:"
                           f"tests/test_docs_claims.py` failed: {show.stderr.strip()}")
        return show.stdout, None
    except OSError as exc:
        return None, f"DTR_DOCS_CLAIMS_BASE is unset, and local git resolution errored: {exc}"


def test_preexisting_docs_claims_cases_are_unweakened():
    """AC-DTR-4 / DTR-4-ADDITIVE-ONLY-OK. FAILS (never skips) when no merge-base baseline can be
    resolved -- neither via DTR_DOCS_CLAIMS_BASE (the checkpoint's own locator) nor by this file
    resolving it locally itself (see `_resolve_docs_claims_base`)."""
    base_source, error = _resolve_docs_claims_base()
    assert base_source is not None, error

    base_bodies = _top_level_function_bodies(base_source)
    current_bodies = _top_level_function_bodies(_read(__file__))

    missing = [n for n in _PREEXISTING_DOCS_CLAIMS_CASES if n not in current_bodies]
    assert not missing, f"pre-existing case(s) no longer defined in this file: {missing}"

    changed = [n for n in _PREEXISTING_DOCS_CLAIMS_CASES
               if n in base_bodies and base_bodies[n] != current_bodies[n]]
    assert not changed, f"pre-existing case(s) diverged from their merge-base body: {changed}"


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

