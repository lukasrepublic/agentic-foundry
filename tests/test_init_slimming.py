"""tests/test_init_slimming.py — feat-foundry-init-slimming (AC-INS-1..8).

Verifies `skills/init/SKILL.md`'s four settings-writing steps (plugin/marketplace enablement,
git-identity, statusline, sandbox) became delimited VERIFY-ONLY regions carrying the correct
per-artifact disposition (REFUSE where `[[feat-foundry-bootstrap-cli]]` owns the write, REPORT-ONLY
+ `no shipped writer` where nothing does), that the closing hello-loop step 14 names its five
stages in order with the one-keypress explanation and a cleanup instruction while staying inside a
tight negative scope, that the honest tiering section carries all five rows with real owners, that
the numbering/inherited-anchor/grep-lock surface survives, that the untouched step slices
{1,3,5,6,7,8,9,10,13} are byte-identical to the merge-base blob (supplied via `INIT_SLIMMING_BASE`
by the checkpoint locator — this case FAILS, never skips, when that var is unset/unreadable), that
the six release-atomicity literals are gone at FILE scope, and that all of the above checks are
non-vacuous (a negative control per lock class, over mutated in-memory copies).

Pure text-locator machinery over the shipped skill file — no product code changes; this is test
machinery only (spec Design note).
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

from conftest import REPO_ROOT

SKILL_PATH = os.path.join(REPO_ROOT, "skills", "init", "SKILL.md")

# ----------------------------------------------------------------------------- frozen literals ---

REGION_SLUGS = ("plugin-enablement", "git-identity", "statusline", "sandbox")

# W — the write-prescription literals (Terminology). Zero occurrences REQUIRED inside each region.
W_LITERALS = [
    "load-modify-write",
    "install -m ",
    "chmod ",
    "gh auth login",
    "git config --global",
    "--insecure-storage",
    "settings.local.json",
    "set `sandbox.enabled`",
    "set `statusLine.command`",
    "set `subagentStatusLine.command`",
    "write `.claude/gh-identity`",
    'cp "${CLAUDE_PLUGIN_ROOT}',
]

# F — the release-atomicity literals (Terminology). Zero occurrences REQUIRED at FILE scope.
F_LITERALS = [
    "<!-- foundry:statusline-prescribed-wiring",
    "load-modify-write",
    "install -m 0755",
    "gh auth login",
    "git config --global",
    "--insecure-storage",
]

# the pointer triple (Terminology).
POINTER_TRIPLE = [
    "pre-session bootstrap",
    "${CLAUDE_PLUGIN_ROOT}/docs/QUICKSTART.md",
    "https://github.com/lukasrepublic/agentic-foundry/blob/main/docs/QUICKSTART.md",
]

TIERING_KEYS = ("status lines", "native Bash sandbox", "git identity", "plugin enablement", "permission floor")
UNTOUCHED_STEPS = (1, 3, 5, 6, 7, 8, 9, 10, 13)

BYPASS_TOKENS = ["--yes", "--dangerously", "bypassPermissions", "acceptEdits", "auto-approve", "allow rule"]
HELLO_VERBS_IN_ORDER = ["/foundry:intake", "/foundry:spec-review", "/foundry:authorize", "/foundry:dispatch", "merge"]
ALLOWED_HELLO_VERBS = {"/foundry:intake", "/foundry:spec-review", "/foundry:authorize", "/foundry:dispatch"}
FOREIGN_PATH_PREFIXES = (".claude/", "scripts/", "docs/", "hooks/", "schema/", ".github/", ".foundry/", "packs/", "skills/")
# Word-boundary patterns (never naive substrings — "gh " is a substring of "through ").
FOREIGN_COMMAND_PATTERNS = [re.compile(r"\b%s\b" % tok) for tok in
                            ("git", "gh", "python3", "pytest", "bash", "sh")]

INHERITED_LOCKS = [
    "GitHub identity isolation", "GH_CONFIG_DIR", "dormant", "SSH host alias",
    "local-seed", "branch-protection", "wiring-hash",
]

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
STEP_HEADING_RE = re.compile(r"^(\d+)\. \*\*", re.MULTILINE)
TIERING_ROW_RE = re.compile(r"^- \*\*(.+?)\*\* —", re.MULTILINE)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _skill_text():
    return _read(SKILL_PATH)


# --------------------------------------------------------------------------- generic locators ---

def _section_body(text, heading):
    """Body of the ONE ATX heading with EXACT text `heading`, from just after it to the next
    heading at the same or a shallower level (or EOF)."""
    matches = list(HEADING_RE.finditer(text))
    found = [i for i, m in enumerate(matches) if m.group(2) == heading]
    assert len(found) == 1, f"heading {heading!r} must occur exactly once; found {len(found)}"
    i = found[0]
    level = len(matches[i].group(1))
    start = matches[i].end()
    end = len(text)
    for j in range(i + 1, len(matches)):
        if len(matches[j].group(1)) <= level:
            end = matches[j].start()
            break
    return text[start:end]


def region_slice(text, slug):
    """The body of the ONE matched verify-only-region delimiter pair for `slug`."""
    open_marker = f"<!-- foundry:init-verify-only:{slug} v1 -->"
    close_marker = f"<!-- /foundry:init-verify-only:{slug} -->"
    assert text.count(open_marker) == 1, f"{slug}: expected exactly one open marker, found {text.count(open_marker)}"
    assert text.count(close_marker) == 1, f"{slug}: expected exactly one close marker, found {text.count(close_marker)}"
    start = text.index(open_marker) + len(open_marker)
    end = text.index(close_marker)
    assert start < end, f"{slug}: open marker must precede close marker"
    return text[start:end]


def _procedure_section(text):
    return _section_body(text, "Procedure (fail-closed at the end)")


def _step_slices(text):
    """{step_number: slice_text} over the numbered steps inside '## Procedure'."""
    proc = _procedure_section(text)
    matches = list(STEP_HEADING_RE.finditer(proc))
    out = {}
    for i, m in enumerate(matches):
        n = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(proc)
        out[n] = proc[start:end]
    return out


def _step14_slice(text):
    marker = "Guided first atom (the hello-loop)"
    idx = text.index(marker)
    line_start = text.rfind("\n", 0, idx) + 1
    m = re.search(r"^## ", text[idx:], re.MULTILINE)
    assert m, "no closing '## ' heading found after step 14 — locator drifted"
    end = idx + m.start()
    return text[line_start:end]


def _tiering_section(text):
    return _section_body(text, "What init verifies — and what it no longer does")


def _tiering_rows(section_text):
    starts = [m.start() for m in TIERING_ROW_RE.finditer(section_text)]
    assert starts, "no tiering rows found — locator drifted"
    ends = starts[1:] + [len(section_text)]
    rows = {}
    for s, e in zip(starts, ends):
        key = TIERING_ROW_RE.match(section_text[s:]).group(1)
        rows[key] = section_text[s:e]
    return rows


# --------------------------------------------------------------------------------- checkers -----
# Pure functions (raise AssertionError on failure) — shared between the positive tests (run on the
# real shipped file, expect no exception) and the negative controls (run on a mutated in-memory
# copy, expect AssertionError — AC-INS-8's non-vacuousness requirement).

def check_region_pointer_and_zero_w(text, slug):
    region = region_slice(text, slug)
    assert "VERIFY-ONLY" in region, f"{slug} region missing VERIFY-ONLY"
    assert "init does not write" in region, f"{slug} region missing 'init does not write'"
    for lit in POINTER_TRIPLE:
        assert lit in region, f"{slug} region missing pointer literal {lit!r}"
    for w in W_LITERALS:
        assert w not in region, f"{slug} region contains forbidden W literal {w!r}"
    return region


def check_no_release_atomicity_literal_at_file_scope(text):
    for lit in F_LITERALS:
        assert lit not in text, f"file-scope release-atomicity literal survived: {lit!r}"


def check_tiering_rows_present(text):
    section = _tiering_section(text)
    rows = _tiering_rows(section)
    assert set(rows.keys()) == set(TIERING_KEYS), f"tiering rows drifted: {sorted(rows.keys())}"
    for key, body in rows.items():
        for label in ("verifies:", "no longer does:", "owner:"):
            assert label in body, f"row {key!r} missing label {label!r}"
    return section, rows


def check_tiering_owners(text):
    _section, rows = check_tiering_rows_present(text)
    for key in ("git identity", "plugin enablement", "permission floor"):
        assert "pre-session bootstrap" in rows[key], f"{key} row owner is not pre-session bootstrap"
    for key in ("status lines", "native Bash sandbox"):
        assert "no shipped writer" in rows[key], f"{key} row owner is not no shipped writer"
    pf = rows["permission floor"]
    assert "/foundry:doctor" in pf, "permission floor row missing /foundry:doctor"
    assert "reports" in pf, "permission floor row missing 'reports'"
    assert "reconcile" not in pf, "permission floor row must not carry 'reconcile'"


def check_hello_loop_stages_in_order_and_cleanup(text):
    slice_ = _step14_slice(text)
    assert "Guided first atom (the hello-loop)" in slice_
    positions = [slice_.index(s) for s in HELLO_VERBS_IN_ORDER]
    assert positions == sorted(positions), f"hello-loop stages out of order: {positions}"
    assert "one-keypress" in slice_
    assert "the operator answers it" in slice_
    assert "never answers it on the operator's behalf" in slice_
    assert "scratch branch" in slice_
    assert "delete it" in slice_
    return slice_


def check_hello_loop_no_bypass_or_foreign_path(text):
    slice_ = _step14_slice(text)
    for tok in BYPASS_TOKENS:
        assert tok not in slice_, f"step 14 slice carries forbidden literal {tok!r}"
    for prefix in FOREIGN_PATH_PREFIXES:
        assert prefix not in slice_, f"step 14 slice names a path outside specs/**: {prefix!r}"
    assert "specs/**" in slice_, "step 14 slice never names specs/**"
    found_verbs = set(re.findall(r"/foundry:[a-z-]+", slice_))
    assert found_verbs <= ALLOWED_HELLO_VERBS, f"step 14 names an unexpected verb: {found_verbs - ALLOWED_HELLO_VERBS}"
    for pat in FOREIGN_COMMAND_PATTERNS:
        assert not pat.search(slice_), f"step 14 slice names another command matching {pat.pattern!r}"


def check_untouched_steps_match(current_text, base_text):
    current_slices = _step_slices(current_text)
    base_slices = _step_slices(base_text)
    for n in UNTOUCHED_STEPS:
        assert n in base_slices, f"step {n} not found in the merge-base blob"
        assert n in current_slices, f"step {n} not found in the current file"
        assert current_slices[n] == base_slices[n], f"step {n} is not byte-identical to the merge-base version"


# ============================================================================== AC-INS-1 =========

def test_plugin_enablement_region_is_verify_only_and_refuses():
    text = _skill_text()
    region = check_region_pointer_and_zero_w(text, "plugin-enablement")
    assert "REFUSE" in region
    assert "REPORT-ONLY" not in region


def test_statusline_region_is_verify_only_and_reports():
    text = _skill_text()
    region = check_region_pointer_and_zero_w(text, "statusline")
    assert "REPORT-ONLY" in region
    assert "REFUSE" not in region
    assert "no shipped writer" in region


def test_sandbox_region_is_verify_only_and_reports():
    text = _skill_text()
    region = check_region_pointer_and_zero_w(text, "sandbox")
    assert "REPORT-ONLY" in region
    assert "REFUSE" not in region
    assert "no shipped writer" in region


# ============================================================================== AC-INS-2 =========

def test_git_identity_region_keeps_only_the_read_only_proof():
    text = _skill_text()
    region = check_region_pointer_and_zero_w(text, "git-identity")
    assert "scripts/foundry-bootstrap.sh" in region
    assert "GH_CONFIG_DIR=~/.config/gh-<account> gh api user --jq .login" in region
    assert "git -C <target> config user.email" in region


def test_git_identity_rows_carry_the_right_disposition():
    text = _skill_text()
    region = region_slice(text, "git-identity")
    table_rows = [
        ln for ln in region.split("\n")
        if ln.strip().startswith("|") and "---" not in ln and "Artifact" not in ln
    ]
    assert len(table_rows) == 3, f"expected exactly 3 disposition rows, found {len(table_rows)}: {table_rows}"
    assert "REFUSE" in table_rows[0] and "AC-BCL-7" in table_rows[0]
    assert "REPORT-ONLY" in table_rows[1] and "no shipped writer" in table_rows[1]
    assert "REPORT-ONLY" in table_rows[2] and "no shipped writer" in table_rows[2]
    assert "docs/identity-isolation.md" in table_rows[2]


# ============================================================================== AC-INS-3 =========

def test_hello_loop_names_the_five_stages_in_order_and_cleans_up():
    check_hello_loop_stages_in_order_and_cleanup(_skill_text())


def test_hello_loop_slice_carries_no_bypass_token_or_foreign_path():
    check_hello_loop_no_bypass_or_foreign_path(_skill_text())


# ============================================================================== AC-INS-4 =========

def test_tiering_section_has_five_rows_and_the_still_writes_line():
    text = _skill_text()
    section, _rows = check_tiering_rows_present(text)
    assert "init still writes" in section
    for artifact in (".claude/foundry-operators.json", ".foundry/stack-profile.lock", ".gitignore"):
        assert artifact in section, f"closing line missing artifact {artifact!r}"


def test_each_tiering_row_names_its_real_owner():
    check_tiering_owners(_skill_text())


# ============================================================================== AC-INS-5 =========

def test_fourteen_steps_in_order_with_their_inherited_anchors():
    text = _skill_text()
    proc = _procedure_section(text)
    nums = [int(m.group(1)) for m in STEP_HEADING_RE.finditer(proc)]
    assert nums == list(range(1, 15)), f"step numbering drifted: {nums}"

    slices = _step_slices(text)
    assert "foundry_control_plane.py" in slices[1]
    create_idx = text.index("Create `.claude/foundry-operators.json`")
    step1_idx = text.index("foundry_control_plane.py")
    assert step1_idx < create_idx, "step 1 no longer precedes the operator-registry create literal"

    assert "<!-- foundry:stack-profile-lock-create-prescribed" in slices[9]
    assert "--lock <id>" in slices[9]

    assert "/foundry:doctor" in slices[10]
    assert "DOCTOR-GREEN" in slices[10]

    assert "scripts/foundry-apply-runtime-gitignore.sh <repo-root>" in slices[13]


def test_inherited_grep_locks_survive_and_the_antipattern_cites_step_nine():
    text = _skill_text()
    for lit in INHERITED_LOCKS:
        assert lit in text, f"file lost the inherited literal {lit!r}"
    m = re.search(r"scripted `--lock` path \(step\s*\n?\s*(\d+)\)", text)
    assert m, "anti-pattern's step citation locator drifted"
    assert m.group(1) == "9", f"anti-pattern still cites the stale step {m.group(1)}, expected step 9"


def _resolve_ins_base_text():
    """Baseline for the AC-INS-5 minimal-diff comparison. Prefers the checkpoint locator's
    INIT_SLIMMING_BASE; falls back to resolving `git merge-base HEAD origin/main` itself so a bare
    `pytest tests/ -q` works. FAILS (never skips) when NEITHER source resolves — a missing baseline
    stays RED, never a silent pass, exactly as AC-INS-5 requires."""
    base_path = os.environ.get("INIT_SLIMMING_BASE")
    if base_path:
        assert os.path.isfile(base_path), (
            f"INIT_SLIMMING_BASE={base_path!r} does not resolve to a readable file"
        )
        return _read(base_path)

    mb = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO_ROOT,
                        capture_output=True, text=True, timeout=30)
    assert mb.returncode == 0 and mb.stdout.strip(), (
        "INIT_SLIMMING_BASE is unset, and `git merge-base HEAD origin/main` could not resolve one "
        f"either (needs a full-history checkout): {mb.stderr.strip()}"
    )
    blob = subprocess.run(["git", "show", f"{mb.stdout.strip()}:skills/init/SKILL.md"],
                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    assert blob.returncode == 0, (
        f"INIT_SLIMMING_BASE is unset, and reading the merge-base blob failed: {blob.stderr.strip()}"
    )
    return blob.stdout


def test_untouched_steps_are_byte_identical_to_the_merge_base():
    """AC-INS-5's minimal-diff guarantee, in TWO REGIMES decided structurally from the baseline —
    never by a skip.

    A merge-base-anchored minimal-diff check is inherently atom-scoped: it can only compare against
    a baseline that still predates the atom. Once this atom lands, every later branch's merge base
    IS post-slimming main, so the comparison would be text-against-itself — passing while proving
    nothing. (Its sibling in tests/test_workflow_export_shape.py hit the harsher form of this and
    turned main red on merge; same root cause, handled the same way here.)

      PRE-LANDING  (baseline has no verify-only regions)
          -> the original claim: the nine untouched step slices are byte-identical to the baseline.
      POST-LANDING (baseline already carries them)
          -> the minimal diff is history; assert the OUTCOME still holds — every one of the four
             regions is still present and still verify-only, and the nine untouched steps still
             carry their inherited literals (the checks the sibling cases below own).
    """
    base_text = _resolve_ins_base_text()

    if "<!-- foundry:init-verify-only:" in base_text:
        current = _skill_text()
        for slug in REGION_SLUGS:
            assert f"<!-- foundry:init-verify-only:{slug} v1 -->" in current, (
                f"post-landing regression: the {slug!r} verify-only region is gone from "
                "skills/init/SKILL.md"
            )
        for lit in INHERITED_LOCKS:
            assert lit in current, f"post-landing regression: lost the inherited literal {lit!r}"
        return

    check_untouched_steps_match(_skill_text(), base_text)


# ============================================================================== AC-INS-6 (see also
# tests/test_docs_claims.py — the QUICKSTART/troubleshooting locks live there)

# ============================================================================== AC-INS-7 is a CLI
# checkpoint (`foundry-doctor.py`), not a pytest case here.

# ============================================================================== AC-INS-8 =========

def test_no_release_atomicity_literal_survives_at_file_scope():
    check_no_release_atomicity_literal_at_file_scope(_skill_text())


def test_negative_controls_all_fire():
    """One negative control per lock class (AC-INS-8): each mutated in-memory copy must FAIL the
    corresponding check, proving the positive assertions above are not vacuous."""
    text = _skill_text()

    # (a) an F literal re-inserted OUTSIDE every region (appended at end of file).
    mutated_a = text + "\n<!-- negative control --> gh auth login\n"
    with pytest.raises(AssertionError):
        check_no_release_atomicity_literal_at_file_scope(mutated_a)

    # (b) a W literal inserted inside EACH of the four regions.
    for slug in REGION_SLUGS:
        open_marker = f"<!-- foundry:init-verify-only:{slug} v1 -->"
        idx = text.index(open_marker) + len(open_marker)
        mutated_b = text[:idx] + "\nchmod 0700 something\n" + text[idx:]
        with pytest.raises(AssertionError):
            check_region_pointer_and_zero_w(mutated_b, slug)

    # (c) a fifth tiering row's owner: value emptied (permission floor).
    mutated_c = text.replace("owner: pre-session bootstrap.\n", "owner: .\n")
    assert mutated_c != text, "mutation (c) locator drifted — nothing was replaced"
    with pytest.raises(AssertionError):
        check_tiering_owners(mutated_c)

    # (d) a step-14 slice carrying bypassPermissions.
    marker = "Guided first atom (the hello-loop)."
    idx = text.index(marker)
    mutated_d = text[:idx] + marker + " bypassPermissions" + text[idx + len(marker):]
    with pytest.raises(AssertionError):
        check_hello_loop_no_bypass_or_foreign_path(mutated_d)

    # (e) a byte-mutated slice of an untouched step (step 5).
    slices = _step_slices(text)
    step5 = slices[5]
    mutated_step5 = step5.replace("Tier B", "Tier X", 1)
    assert mutated_step5 != step5, "mutation (e) locator drifted — nothing was replaced"
    mutated_e = text.replace(step5, mutated_step5, 1)
    with pytest.raises(AssertionError):
        check_untouched_steps_match(mutated_e, text)
