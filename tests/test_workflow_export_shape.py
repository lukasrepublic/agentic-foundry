"""tests/test_workflow_export_shape.py — feat-foundry-release-wave-workflow-syntax (AC-RWS-1..-4).

`workflows/release-wave.js` violated the Workflow-runtime script contract: the runtime extracts
`export const meta` and wraps the REMAINING body in an async function, so `export const meta` MUST
be the sole export — a stray `export function` inside that wrapper is a SyntaxError, while a
top-level `return` is legal (an ordinary function return once wrapped). This module carries two
independent locks:

  1. **The wrapper-simulation oracle** (AC-RWS-2) — reproduces the runtime's own transform (excise
     `export const meta` by a balanced-brace scan over an ELIDED VIEW, embed the remainder in
     `(async () => { ... })`) and checks the TRANSFORMED artifact with a real parser
     (`node --check --input-type=module`), asserting on exit status only. `workflows/spec-audit.js`
     is the POSITIVE CONTROL — the same simulation over it must pass before and after this atom,
     which is what proves the oracle tests the export rule rather than merely rejecting every
     workflow file (it keeps a top-level `return` and still passes under the wrap).
  2. **The Node-free structural sole-export scan** (AC-RWS-3) — computes an elided view (comments
     and string/template/regex-literal CONTENTS replaced by the filler character, newlines
     preserved), collects every whole-word `export` TOKEN by position, and asserts the collected
     list has length exactly 1 and that its single member is `export const meta`. Six negative
     controls (four FAIL, two PASS) prove the scan is not vacuous — see
     `test_export_shape_negative_controls_all_fire`.

AC-RWS-2's two cases are `pytest.mark.skipif`-gated on `node` PER FUNCTION (not a module-level
`pytestmark`) because AC-RWS-3's cases live in this same module and must never be skip-gated — the
lock that actually catches a re-introduced `export function` runs everywhere, Node or not.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT

WORKFLOWS_DIR = os.path.join(REPO_ROOT, "workflows")
RELEASE_WAVE_JS = os.path.join(WORKFLOWS_DIR, "release-wave.js")
SPEC_AUDIT_JS = os.path.join(WORKFLOWS_DIR, "spec-audit.js")

BEGIN_SENTINEL = "// === RFH-PURE-BEGIN"
END_SENTINEL = "// === RFH-PURE-END ==="

FOUR_FUNCTIONS = ["normalizeEvidence", "dedupKey", "consolidateFindings", "assembleReviewResult"]

NODE = shutil.which("node")

FILLER = " "
_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$"
)


# ============================================================================================
# THE ELIDER — a minimal (not a full lexer, see the spec's R2) structural scan that replaces
# comment text, string/template-literal contents, and regex-literal contents with the filler
# character, preserving every newline so line/column offsets in the elided view are identical to
# offsets in the original text (the Terminology's own invariant).
# ============================================================================================

def _is_regex_context(prev1, prev2):
    """A `/` opens a regex literal exactly when the preceding SIGNIFICANT (non-whitespace,
    non-comment) character is NOT an identifier character, a digit, `)`, `]`, `}`, or the second
    character of `++`/`--`; otherwise it is division (Terminology: "an elided view")."""
    if prev1 is None:
        return True
    if prev1 in _IDENTIFIER_CHARS:
        return False
    if prev1 in (")", "]", "}"):
        return False
    if prev1 in ("+", "-") and prev2 == prev1:
        return False
    return True


def elided_view(text: str) -> str:
    """Return `text` with (i) `//` line comments, (ii) `/* */` block comments, (iii) the contents
    of `'...'`/`"..."`/`` `...` `` literals, and (iv) the contents of regex literals, each replaced
    character-for-character by the filler character (U+0020 SPACE) — `\\n` preserved verbatim
    throughout, so offsets are unchanged. A balanced-brace scan or an `export`-token collection over
    this view therefore never sees a construct that lived only inside a comment or a literal."""
    n = len(text)
    out = list(text)
    i = 0
    prev1 = None  # last significant (non-whitespace, non-comment) char seen so far
    prev2 = None  # the one before that — needed for the ++/-- disambiguation
    while i < n:
        c = text[i]

        # -- line comment: `//` to end of line.
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = i
            while j < n and text[j] != "\n":
                out[j] = FILLER
                j += 1
            i = j
            continue

        # -- block comment: `/* ... */`.
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = i
            while j < n and not (text[j] == "*" and j + 1 < n and text[j + 1] == "/"):
                if text[j] != "\n":
                    out[j] = FILLER
                j += 1
            for _ in range(2):  # the closing `*/` itself
                if j < n:
                    if text[j] != "\n":
                        out[j] = FILLER
                    j += 1
            i = j
            continue

        # -- single/double-quoted string literal.
        if c in ("'", '"'):
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == "\\" and j + 1 < n:
                    for _ in range(2):
                        if text[j] != "\n":
                            out[j] = FILLER
                        j += 1
                    continue
                if text[j] != "\n":
                    out[j] = FILLER
                j += 1
            if j < n:
                j += 1  # consume the closing quote (left as-is, not filled)
            i = j
            prev1, prev2 = quote, prev1
            continue

        # -- template literal (its `${...}` interpolations are elided along with everything else —
        #    a minimal elider, not a lexer; see the spec's R2 residual).
        if c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                if text[j] == "\\" and j + 1 < n:
                    for _ in range(2):
                        if text[j] != "\n":
                            out[j] = FILLER
                        j += 1
                    continue
                if text[j] != "\n":
                    out[j] = FILLER
                j += 1
            if j < n:
                j += 1
            i = j
            prev1, prev2 = "`", prev1
            continue

        # -- `/`: either a regex literal or division.
        if c == "/":
            if _is_regex_context(prev1, prev2):
                # Dry-run scan (no mutation yet) for the matching UNESCAPED `/`, character-class
                # aware (a `/` inside `[...]` never terminates the regex) — a newline before a
                # match means this was never a regex literal at all (invalid JS otherwise), so we
                # fall through to the division branch below without having touched `out`.
                k = i + 1
                in_class = False
                closed_at = None
                while k < n:
                    ch = text[k]
                    if ch == "\n":
                        break
                    if ch == "\\" and k + 1 < n:
                        k += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        closed_at = k
                        break
                    k += 1
                if closed_at is not None:
                    for p in range(i + 1, closed_at):
                        if text[p] != "\n":
                            out[p] = FILLER
                    k = closed_at + 1
                    while k < n and text[k].isalpha():  # flags, e.g. /g/i — kept as-is
                        k += 1
                    i = k
                    prev1, prev2 = "/", prev1
                    continue
            # division (or an unterminated `/` we declined to treat as a regex)
            prev1, prev2 = c, prev1
            i += 1
            continue

        # -- ordinary character.
        if not c.isspace():
            prev1, prev2 = c, prev1
        i += 1

    return "".join(out)


_EXPORT_TOKEN_RE = re.compile(r"\bexport\b")
_WORD_RE = re.compile(r"\S+")


def find_export_tokens(elided: str):
    """Collect every whole-word `export` occurrence in an elided view, by POSITION (offset, line,
    column) together with the next two whitespace-delimited words at that position — never a
    substring presence/index check over raw text (Terminology: "an export token")."""
    tokens = []
    for m in _EXPORT_TOKEN_RE.finditer(elided):
        offset = m.start()
        line = elided.count("\n", 0, offset) + 1
        last_nl = elided.rfind("\n", 0, offset)
        col = offset - last_nl
        rest = elided[m.end():]
        words = _WORD_RE.findall(rest)
        tokens.append({
            "offset": offset,
            "line": line,
            "col": col,
            "word1": words[0] if words else "",
            "word2": words[1] if len(words) > 1 else "",
        })
    return tokens


def is_meta_export(token) -> bool:
    """Terminology: "the meta export" — the export token whose next two words are exactly `const`
    and `meta`."""
    return token["word1"] == "const" and token["word2"] == "meta"


def check_export_shape(text: str) -> dict:
    """AC-RWS-3's assertion, structurally: an elided view's collected export-token list has length
    exactly 1 and its sole member is the meta export."""
    elided = elided_view(text)
    tokens = find_export_tokens(elided)
    ok = len(tokens) == 1 and is_meta_export(tokens[0])
    return {"ok": ok, "tokens": tokens}


def find_meta_declaration_span(text: str, elided: str):
    """Terminology: "the meta declaration" — the span from the meta export's offset to the offset
    of the brace that BALANCES the first `{` after it (a brace scan over the elided view, so braces
    inside comments/literals never count), extended over an immediately following optional `;`."""
    tokens = find_export_tokens(elided)
    meta_tokens = [t for t in tokens if is_meta_export(t)]
    assert len(meta_tokens) == 1, (
        f"expected exactly one meta export token to locate the meta declaration, found "
        f"{len(meta_tokens)}: {meta_tokens}"
    )
    meta = meta_tokens[0]
    brace_open = elided.index("{", meta["offset"])
    depth = 0
    i = brace_open
    n = len(elided)
    close = None
    while i < n:
        if elided[i] == "{":
            depth += 1
        elif elided[i] == "}":
            depth -= 1
            if depth == 0:
                close = i
                break
        i += 1
    assert close is not None, "unbalanced braces scanning the meta declaration"
    end = close + 1
    if end < len(text) and text[end] == ";":
        end += 1
    return meta["offset"], end


def wrapper_simulation_source(path: str) -> str:
    """Terminology: "the wrapper simulation" — delete the meta declaration from the file and embed
    the remainder as `(async () => {` + newline + remainder + newline + `})`."""
    text = open(path, encoding="utf-8").read()
    elided = elided_view(text)
    start, end = find_meta_declaration_span(text, elided)
    remainder = text[:start] + text[end:]
    return "(async () => {\n" + remainder + "\n})"


def node_check_exit_status(source: str) -> int:
    """Run `node --check --input-type=module` over `source` via stdin and return its exit status —
    the wrapper simulation is checked by node's exit status ONLY, never diagnostic wording."""
    assert NODE, "node not on PATH"
    proc = subprocess.run(
        [NODE, "--check", "--input-type=module"],
        input=source, capture_output=True, text=True, timeout=30,
    )
    return proc.returncode


# =================================================== AC-RWS-1 =================================== #

def test_the_four_rfh_pure_functions_are_plain_declarations():
    """The four RFH-PURE functions each begin with the bare token `function`, never `export
    function`; the file's export set is `export const meta` alone."""
    text = open(RELEASE_WAVE_JS, encoding="utf-8").read()
    assert text.count(BEGIN_SENTINEL) == 1
    assert text.count(END_SENTINEL) == 1
    begin = text.index(BEGIN_SENTINEL)
    end = text.index(END_SENTINEL) + len(END_SENTINEL)
    assert begin < end
    block = text[begin:end]

    for name in FOUR_FUNCTIONS:
        pattern = re.compile(r"(export\s+)?function\s+" + re.escape(name) + r"\s*\(")
        matches = list(pattern.finditer(block))
        assert len(matches) == 1, (
            f"{name}: expected exactly one declaration inside the RFH-PURE block, found "
            f"{len(matches)}"
        )
        assert matches[0].group(1) is None, (
            f"{name}: declaration still begins with 'export function' — must be a plain "
            f"'function' declaration"
        )

    result = check_export_shape(text)
    assert result["ok"], (
        f"workflows/release-wave.js must carry exactly one export (const meta) after the fix, "
        f"got: {result['tokens']}"
    )


def _resolve_base_text():
    """Resolve the merge-base blob text for `workflows/release-wave.js`. The LOCATOR-supplied
    `RWS_BASE` env var (a path to the blob `git show`'d by the checkpoint locator) is always
    preferred and used as-is when present — this is the AC-RWS-1 checkpoint's own exact mechanism,
    unchanged. When `RWS_BASE` is absent (a bare `pytest tests/ -q` run, e.g. AC-RWS-4's
    full-suite checkpoint / this repo's own pre-push floor), this falls back to resolving the same
    `git merge-base HEAD origin/main` blob directly, so the general suite stays green without
    requiring every caller to know about `RWS_BASE`. Either path FAILS (never skips) the moment a
    baseline cannot be produced — a missing/unreadable/unresolvable baseline is RED, never a silent
    pass, exactly as AC-RWS-1 requires.

    The fallback needs a full-history checkout, which it now has everywhere it runs: the dispatched
    worktree always had one, and `ci.yml`'s selftests job takes `fetch-depth: 0` in this same change
    (it previously used the default depth-1 clone, where `origin/main` does not exist as a
    remote-tracking ref and this case therefore went red for want of history rather than for a real
    delta). Under a shallow checkout the fail-closed path still applies — red, never a silent
    pass — so the assertion degrades safely rather than vacuously if that ever regresses."""
    base_path = os.environ.get("RWS_BASE")
    if base_path:
        if not os.path.isfile(base_path):
            pytest.fail(f"RWS_BASE={base_path!r} does not exist or is not a readable file")
        return open(base_path, encoding="utf-8").read()

    try:
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            pytest.fail(
                "RWS_BASE env var not set, and 'git merge-base HEAD origin/main' could not "
                f"resolve one either (requires a full-history checkout): {merge_base.stderr.strip()}"
            )
        sha = merge_base.stdout.strip()
        blob = subprocess.run(
            ["git", "show", f"{sha}:workflows/release-wave.js"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        if blob.returncode != 0:
            pytest.fail(
                f"RWS_BASE env var not set, and 'git show {sha}:workflows/release-wave.js' failed: "
                f"{blob.stderr.strip()}"
            )
        return blob.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"RWS_BASE env var not set, and resolving the merge base via git failed: {exc}")


def test_bodies_are_byte_identical_to_the_merge_base():
    """AC-RWS-1's minimal-diff guarantee: `release-wave.js` is byte-identical to its merge-base
    form once the leading `export ` is stripped from exactly the four RFH-PURE declarations — a
    single whole-file comparison, which proves the four bodies AND every in-file call site are
    unchanged in the same assertion. See `_resolve_base_text` for how the baseline is obtained;
    this case FAILS (never skips) when no baseline can be produced — a missing baseline is RED,
    never a silent pass."""
    base_text = _resolve_base_text()
    current_text = open(RELEASE_WAVE_JS, encoding="utf-8").read()

    expected = base_text
    for name in FOUR_FUNCTIONS:
        old = f"export function {name}("
        new = f"function {name}("
        occurrences = base_text.count(old)
        assert occurrences == 1, (
            f"merge-base form expected exactly one 'export function {name}(' occurrence, found "
            f"{occurrences}"
        )
        expected = expected.replace(old, new, 1)

    assert expected == current_text, (
        "workflows/release-wave.js must be byte-identical to its merge-base form except for the "
        "four 'export ' keywords stripped from normalizeEvidence/dedupKey/consolidateFindings/"
        "assembleReviewResult — any other delta (including a changed call site) is out of scope"
    )


# =================================================== AC-RWS-2 =================================== #

@pytest.mark.skipif(NODE is None, reason="node not on PATH — the wrapper-simulation oracle needs a real parser")
def test_release_wave_passes_the_workflow_runtime_wrapper_simulation():
    """The wrapper simulation of the subject file must PASS: pre-change the four stray `export
    function` declarations land inside the wrapper's function body (a SyntaxError); post-change
    they are plain declarations, which are legal there."""
    source = wrapper_simulation_source(RELEASE_WAVE_JS)
    rc = node_check_exit_status(source)
    assert rc == 0, f"node --check --input-type=module exited {rc} over the wrapper simulation"


@pytest.mark.skipif(NODE is None, reason="node not on PATH — the wrapper-simulation oracle needs a real parser")
def test_spec_audit_control_passes_the_wrapper_simulation_unchanged():
    """THE POSITIVE CONTROL: the same simulation over `workflows/spec-audit.js` must ALSO pass,
    unchanged by this atom, proving the oracle does not merely reject every workflow file — and it
    does so while that file retains its own top-level `return` (:1151), which is what pins the
    oracle to the EXPORT rule rather than accidentally to the RETURN rule."""
    source = wrapper_simulation_source(SPEC_AUDIT_JS)
    rc = node_check_exit_status(source)
    assert rc == 0, f"node --check --input-type=module exited {rc} over the control's wrapper simulation"


# =================================================== AC-RWS-3 =================================== #

@pytest.mark.parametrize(
    "path", [RELEASE_WAVE_JS, SPEC_AUDIT_JS], ids=["release-wave.js", "spec-audit.js"],
)
def test_every_workflow_file_exports_only_the_meta_const(path):
    """Node-FREE: for each `workflows/*.js` file, its elided view carries exactly one export
    token, and it is the meta export. Never skip-gated — this is the lock that actually catches a
    re-introduced `export function` on a machine with no Node at all."""
    text = open(path, encoding="utf-8").read()
    result = check_export_shape(text)
    assert result["ok"], f"{path}: expected exactly one export token (the meta const), got {result['tokens']}"


def _control_a_appended_export_function(text: str) -> str:
    return text + "\nexport function foo() {}\n"


def _control_b_export_default_added(text: str) -> str:
    return text + "\nexport default {}\n"


def _control_c_meta_export_stripped(text: str) -> str:
    assert "export const meta" in text
    return text.replace("export const meta", "const meta", 1)


def _control_d_exactly_one_non_meta_export(text: str) -> str:
    # The meta declaration's own `export` removed (so the ONLY export token left is the injected
    # one below) + a NEW, single export that is NOT the meta const — review B2's control: catches
    # an implementation that checks the export COUNT but drops the IDENTITY test.
    stripped = _control_c_meta_export_stripped(text)
    return stripped + "\nexport const other = {}\n"


def _control_e_export_function_only_in_comment_and_template(text: str) -> str:
    # "export function" appears twice more, but ONLY inside a `//` comment and inside a template
    # literal — both elided, so the real count must stay at exactly 1 (the meta export).
    return (
        text
        + "\n// a decoy comment: export function decoy() {}\n"
        + "const _decoyTemplate = `export function alsoDecoy() {}`\n"
    )


def _control_f_quote_bearing_regex_before_real_export(text: str) -> str:
    # A quote-bearing regex literal (a character class containing both a single and a double
    # quote) placed BEFORE the real export token — the shape a naive quote-TRACKING elider (one
    # that starts "string mode" on the first quote character it sees, ignoring regex context)
    # would swallow: it would treat everything from that stray `'` onward as string content and
    # miss the real `export const meta` entirely, a FALSE GREEN (spec R2's corrected bound). A
    # structural elider that recognizes the regex-literal context first must still find exactly
    # the one real export token.
    return "const RE = /['\"]/\n" + text


NEGATIVE_CONTROLS = [
    ("a_appended_export_function", _control_a_appended_export_function, False),
    ("b_export_default_added", _control_b_export_default_added, False),
    ("c_meta_export_stripped", _control_c_meta_export_stripped, False),
    ("d_exactly_one_non_meta_export", _control_d_exactly_one_non_meta_export, False),
    ("e_export_function_only_in_comment_and_template", _control_e_export_function_only_in_comment_and_template, True),
    ("f_quote_bearing_regex_before_real_export", _control_f_quote_bearing_regex_before_real_export, True),
]


@pytest.mark.parametrize(
    "name,mutate,expected_ok", NEGATIVE_CONTROLS, ids=[c[0] for c in NEGATIVE_CONTROLS],
)
def test_export_shape_negative_controls_all_fire(name, mutate, expected_ok):
    """Six controls over MUTATED IN-MEMORY copies (the real files on disk are never touched).
    (a)-(d) must report FAILURE (not sole-meta-export); (e)-(f) must report SUCCESS — the elision
    itself is under test in (e)/(f), never the raw presence of the substring 'export function'."""
    base = open(RELEASE_WAVE_JS, encoding="utf-8").read()
    mutated = mutate(base)
    result = check_export_shape(mutated)
    assert result["ok"] is expected_ok, (
        f"control {name}: expected ok={expected_ok}, got ok={result['ok']} tokens={result['tokens']}"
    )
