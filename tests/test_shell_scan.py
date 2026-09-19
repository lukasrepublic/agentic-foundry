"""feat-foundry-guards-guard-structured-observations — the tokenizer module itself
(AC-GSO-1, AC-GSO-2, AC-GSO-5, AC-GSO-6), driven directly against
`scripts/foundry_shell_scan.py`. The two guard hooks' own end-to-end behavior (the merge-floor
half — verdict carrier, tripwire, clause logic unchanged) is covered by
`tests/test_hooks_guards.py` and `tests/test_cloud_guard_verb_path.py`; this file covers the
tokenizer as an importable, independently testable unit, per the spec's own "Design / notes".
"""
import os
import stat
import sys

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import foundry_shell_scan as fss

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


# ============================================================================== AC-GSO-1 =======

def test_tokenize_returns_word_with_clause_index_data_and_kind():
    words = fss.tokenize("git status && echo hi")
    assert words, "tokenize() produced nothing for a trivial command"
    for w in words:
        assert hasattr(w, "word") and hasattr(w, "clause_index") and hasattr(w, "data") \
            and hasattr(w, "kind")
    assert [w.word for w in words] == ["git", "status", "echo", "hi"]
    # `&&` bounds a clause: "git status" and "echo hi" are different clause_index values.
    assert words[0].clause_index == words[1].clause_index
    assert words[2].clause_index == words[3].clause_index
    assert words[0].clause_index != words[2].clause_index


def test_tokenize_marks_command_substitution_and_process_substitution_segments():
    words = fss.tokenize("echo $(date) >(cat) <(cat)")
    kinds = {w.word: w.kind for w in words}
    assert kinds.get("date") == "cmd_sub"
    assert kinds.get("cat") in ("proc_sub",)  # first >( cat ) word


def test_tokenize_backslash_continuation_removed_first():
    # "gi\<newline>t status" must tokenize as a single "git" word (AC-GSO-1(a)).
    words = fss.tokenize("gi\\\nt status")
    assert [w.word for w in words] == ["git", "status"]


def test_tokenize_heredoc_opener_never_inside_quotes():
    # A literal "<<EOF" inside a quoted argument is NOT a heredoc opener.
    words = fss.tokenize('echo "not <<EOF a heredoc"')
    assert not any(w.kind == "heredoc" for w in words)


# ============================================================================== AC-GSO-2 =======

def test_inert_sink_heredoc_is_data_and_any_other_consumer_is_code():
    admit = "cat > docs/x.md <<'EOF'\nplain prose\nEOF"
    words = fss.tokenize(admit)
    body_words = [w for w in words if w.kind == "heredoc"]
    assert body_words and all(w.data for w in body_words), \
        "a closed inert-sink heredoc (cat > <path>) with no other consumer must classify data=True"

    # A pipe consumer on the SAME clause disqualifies it (AC-GSO-2(ii)) — code.
    piped = "cat <<'EOF' | bash\nplain prose\nEOF"
    words2 = fss.tokenize(piped)
    body_words2 = [w for w in words2 if w.kind == "heredoc"]
    assert body_words2 and not any(w.data for w in body_words2)


@pytest.mark.parametrize("cmd,expect_data", [
    ("cat > docs/x.md <<'EOF'\nhello\nEOF", True),
    ("cat >> docs/x.md <<'EOF'\nhello\nEOF", True),
    ("tee docs/x.md <<'EOF'\nhello\nEOF", True),
    ("git commit -F - <<'EOF'\nhello\nEOF", True),
    ("gh pr create --body-file - <<'EOF'\nhello\nEOF", True),
    ("gh issue create --body-file - <<'EOF'\nhello\nEOF", True),
    ("bash <<'EOF'\nhello\nEOF", False),               # not a closed sink command at all
    ("cat > /tmp/x <<'EOF'\nhello\nEOF", False),         # scratch/tmp path — AC-GSO-3 row 1
    ("cat > /dev/null <<'EOF'\nhello\nEOF", False),      # /dev/ excluded
    ("cat > - <<'EOF'\nhello\nEOF", False),              # literal '-' excluded
])
def test_closed_sink_set_membership(cmd, expect_data):
    words = fss.tokenize(cmd)
    body_words = [w for w in words if w.kind == "heredoc"]
    assert body_words
    assert all(w.data for w in body_words) == expect_data, cmd


def test_sink_path_mentioned_in_another_clause_convicts():
    # AC-GSO-2(iv) / the round-1 remediation: a same-call write-then-run.
    cmd = "cat > f.sh <<'EOF'\necho hi\nEOF\n; bash f.sh"
    words = fss.tokenize(cmd)
    body_words = [w for w in words if w.kind == "heredoc"]
    assert body_words and not any(w.data for w in body_words)


def test_sink_path_existing_fifo_convicts(tmp_path):
    fifo = tmp_path / "sink_fifo"
    os.mkfifo(fifo)
    try:
        cmd = f"cat > {fifo} <<'EOF'\nhello\nEOF"
        words = fss.tokenize(cmd)
        body_words = [w for w in words if w.kind == "heredoc"]
        assert body_words and not any(w.data for w in body_words)
    finally:
        os.remove(fifo)


# ============================================================================== AC-GSO-6 =======

_UNCLASSIFIABLE_CASES = [
    ("unterminated-heredoc", "cat > docs/x.md <<'EOF'\nhello, no terminator"),
    ("unbalanced-quote", "cat > docs/x.md <<'EOF\nhello\nEOF"),
    ("bash-consumer", "bash <<'EOF'\nhello\nEOF"),
    ("sh-consumer", "sh <<'EOF'\nhello\nEOF"),
    ("python3-dash-consumer", "python3 - <<'EOF'\nhello\nEOF"),
    ("eval-consumer", "eval \"$(cat <<'EOF'\nhello\nEOF\n)\""),
    ("source-consumer", "source <<'EOF'\nhello\nEOF"),
    ("dot-source-consumer", ". <<'EOF'\nhello\nEOF"),
    ("nested-multi-heredoc-same-clause", "cat > docs/x.md <<A <<B\nhello\nA\nB"),
    ("quoted-terminator-with-spaces", "cat > docs/x.md <<'MY EOF'\nhello\nMY EOF"),
    ("crlf-line-endings", "cat > docs/x.md <<'EOF'\r\nhello\r\nEOF\r\n"),
    ("redirect-target-is-variable", 'T=docs/x.md; cat > "$T" <<\'EOF\'\nhello\nEOF'),
    ("redirect-target-is-cmd-sub", "cat > $(echo docs/x.md) <<'EOF'\nhello\nEOF"),
    ("heredoc-inside-cmd-sub", "x=$(cat <<'EOF'\nhello\nEOF\n)"),
]


@pytest.mark.parametrize("name,cmd", _UNCLASSIFIABLE_CASES, ids=[c[0] for c in _UNCLASSIFIABLE_CASES])
def test_unclassifiable_constructions_are_code(name, cmd):
    """AC-GSO-6: every named construction the tokenizer cannot classify convicts (data=False for
    any heredoc body it can find at all; never admitted by exception)."""
    words = fss.tokenize(cmd)
    body_words = [w for w in words if w.kind == "heredoc"]
    assert not any(w.data for w in body_words), f"{name}: {cmd!r} was wrongly classified data"


# =========================================================================== neutralize() ======

def test_neutralize_blanks_only_data_heredoc_bodies_preserving_line_count():
    cmd = "cat > docs/x.md <<'EOF'\nline one\nline two\nEOF\ngit status"
    neut = fss.neutralize(cmd)
    assert neut.count("\n") == cmd.count("\n")
    assert "line one" not in neut and "line two" not in neut
    assert "git status" in neut


def test_neutralize_leaves_code_heredoc_untouched():
    cmd = "bash <<'EOF'\ngit push --force origin main\nEOF"
    neut = fss.neutralize(cmd)
    assert "git push --force origin main" in neut


def test_neutralize_non_string_input_is_inert():
    assert fss.neutralize(None) == ""
    assert fss.neutralize("") == ""
    assert fss.tokenize(None) == []
    assert fss.tokenize("") == []


# ============================================================================== AC-GSO-5 =======

def _parse_corpus(path):
    text = open(path, encoding="utf-8").read()
    cases = []
    admit, convict, mode, buf = None, None, None, []

    def _flush():
        nonlocal admit, convict, buf, mode
        if mode == "admit":
            admit = "\n".join(buf)
        elif mode == "convict":
            convict = "\n".join(buf)
        buf = []

    for line in text.splitlines():
        if line.startswith("@@@ CASE"):
            _flush()
            if admit is not None or convict is not None:
                cases.append((admit, convict))
            admit, convict, mode, buf = None, None, None, []
            continue
        if line.startswith("#") and mode is None:
            continue
        if line == "--- ADMIT ---":
            _flush()
            mode = "admit"
            continue
        if line == "--- CONVICT ---":
            _flush()
            mode = "convict"
            continue
        if mode is not None:
            buf.append(line)
    _flush()
    if admit is not None or convict is not None:
        cases.append((admit, convict))
    return cases


CORPUS = _parse_corpus(os.path.join(FIXTURES_DIR, "guard-false-positives.txt"))


def test_corpus_has_at_least_forty_paired_cases():
    assert len(CORPUS) >= 40, f"only {len(CORPUS)} corpus cases — AC-GSO-5 requires >= 40"
    for admit, convict in CORPUS:
        assert admit and convict, "every corpus row must be paired with a convict sibling"


@pytest.mark.parametrize("idx,case", list(enumerate(CORPUS)), ids=[f"case-{i}" for i in range(len(CORPUS))])
def test_false_positive_corpus_admits_and_each_row_has_a_paired_convict(idx, case):
    admit, convict = case
    admit_neut = fss.neutralize(admit)
    convict_neut = fss.neutralize(convict)
    # The ADMIT row's heredoc body must have been blanked (no `data` heredoc words survive as
    # scannable text); the CONVICT sibling of the identical content must NOT have been blanked.
    assert admit_neut != admit or "EOF" not in admit, f"admit case {idx} was not neutralized at all"
    # A stronger, content-based check: whatever body text differs between admit/convict (they
    # share the same heredoc body content by construction) must survive in the convict scan.
    admit_body_words = [w for w in fss.tokenize(admit) if w.kind == "heredoc"]
    convict_body_words = [w for w in fss.tokenize(convict) if w.kind == "heredoc"]
    assert admit_body_words and all(w.data for w in admit_body_words), (
        f"corpus admit case {idx} was not classified data: {admit!r}")
    assert convict_body_words and not any(w.data for w in convict_body_words), (
        f"corpus convict sibling {idx} was wrongly classified data: {convict!r}")
