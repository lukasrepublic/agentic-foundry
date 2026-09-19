"""foundry_shell_scan — a stdlib-only, purpose-built Bash command tokenizer
(feat-foundry-guards-guard-structured-observations, AC-GSO-1..6).

WHY A NEW TOKENIZER (researched 2026-09-19, see the spec's "Prior art" section): `bashlex` is
GPLv3+ and cannot ship inside this MIT plugin; `tree-sitter-bash` needs a compiled binary the
stdlib-only plugin cannot ship. This module is the consensus-compatible route: a small,
purpose-built, stdlib-only scanner bounded to exactly what the guard hooks need — finding
heredoc boundaries the way bash does, and classifying a heredoc body as `data` (inert, never
scanned for a guarded verb) ONLY when it is provably a closed-set inert sink.

CONVICT ON DOUBT (AC-GSO-6) is the organizing principle throughout: every branch that cannot
prove a construction is safe leaves it (or forces it) `code` — never admits by exception. This
is NOT a general-purpose shell parser; it declines to be one. Constructs it cannot classify
(unterminated heredocs, unbalanced quotes, indirection through `bash`/`sh`/`eval`/`source`/
`python3 -`, nested/multi heredocs on one clause, a `<<` occurring inside a `$( )` command
substitution, a quoted terminator containing whitespace, CRLF line endings, a redirect target
that is a variable or expansion) all fall through to `code`.

Public surface:
    tokenize(command: str) -> List[Word]
    neutralize(command: str) -> str
        Returns `command` with every `data`-classified heredoc body's characters (never its
        newlines, so line-based reasoning downstream is unaffected) replaced by spaces. This is
        the ONLY thing the two guard hooks consume: they run their EXISTING, UNCHANGED clause
        logic (git/gh/rm detection, the cloud-CLI command-position scan) over
        `neutralize(cmd)` instead of `cmd` directly, while still using the raw `cmd` for
        messages/redaction. A `data` heredoc body simply stops containing any words for that
        pass to find; a `code` heredoc body is untouched and scans exactly as it always has
        (newlines already bound clauses in both hooks).

Both heredoc-discovery helpers below (`_parse_heredoc_opener`, `_find_heredoc_end`) are the
SINGLE implementation shared by `tokenize()`'s word-emission walk and the classification pass —
there is exactly one place that decides where a heredoc begins and ends.
"""
from __future__ import annotations

import os
import re
import shlex
import stat
import tempfile
from typing import Dict, List, NamedTuple, Optional, Tuple


class Word(NamedTuple):
    """One tokenizer output unit.

    word          — the literal text (verbatim; this is a detection scanner, not an executor).
    clause_index  — an increasing integer; words sharing a clause_index belong to the same
                     command clause (top-level `;`, `&&`, `||`, `|`, `&`, or newline bounds a
                     clause) OR, for heredoc-body words, the same heredoc body.
    data          — True iff this word is inside a heredoc body that AC-GSO-2 classifies as an
                     inert-sink `data` payload. False for every other word, INCLUDING a heredoc
                     body that fails AC-GSO-2 (that body is `code`, scanned like any other text).
    kind          — "word" (ordinary top-level token), "heredoc" (a heredoc body word, data or
                     code), "cmd_sub" (inside `$( )` / backtick / `$(( ))`), "proc_sub" (inside
                     `>( )` / `<( )`), or "comment" (a `#`-introduced trailing comment word).
    """
    word: str
    clause_index: int
    data: bool
    kind: str


_TEMP_PREFIXES_CACHE: Optional[set] = None


def _temp_prefixes() -> set:
    """Scratch/tmp directory prefixes a sink path may not resolve under.

    Not named in AC-GSO-2's literal text, but required by AC-GSO-3: the tripwire's row 1
    (`cat > /tmp/x <<EOF … EOF`) is an otherwise-exact structural match for the closed sink set
    (single clause, no other consumer, a plain non-`-`, non-`/dev`/`/proc` path unmentioned
    elsewhere) and MUST still convict — the BLOCK set does not shrink. A scratch/tmp target is
    not a committed, inspectable artifact; convict-on-doubt (AC-GSO-6) extends "plain regular-
    file path" to exclude it. This is a considered interpretation, flagged for operator review
    in the PR description.
    """
    global _TEMP_PREFIXES_CACHE
    if _TEMP_PREFIXES_CACHE is None:
        prefixes = {"/tmp/", "/var/tmp/", "/private/tmp/", "/private/var/tmp/"}
        try:
            td = os.path.realpath(tempfile.gettempdir())
            if td and td != "/":
                prefixes.add(td.rstrip("/") + "/")
        except Exception:
            pass
        _TEMP_PREFIXES_CACHE = prefixes
    return _TEMP_PREFIXES_CACHE


# ---------------------------------------------------------------------------------------------
# Shared heredoc-boundary discovery (used by BOTH the word-emission walk and classification).
# ---------------------------------------------------------------------------------------------
def _parse_heredoc_opener(cmd: str, i: int) -> Optional[Tuple[bool, str, int]]:
    """`cmd[i:i+2] == '<<'` already confirmed by the caller, at a true top-level, word-boundary
    position (not `<<<`, not preceded by another `<`). Returns (strip_tabs, delim, j) where j is
    the index just past the parsed opener, or None if the opener cannot be resolved at all
    (AC-GSO-6: malformed opener)."""
    j = i + 2
    strip_tabs = False
    if j < len(cmd) and cmd[j] == "-":
        strip_tabs = True
        j += 1
    while j < len(cmd) and cmd[j] in " \t":
        j += 1
    if j < len(cmd) and cmd[j] in ("'", '"'):
        q = cmd[j]
        k = j + 1
        dtext: List[str] = []
        while k < len(cmd):
            if cmd[k] == q:
                return strip_tabs, "".join(dtext), k + 1
            dtext.append(cmd[k])
            k += 1
        return None  # unterminated quoted delimiter
    if j < len(cmd) and (cmd[j].isalnum() or cmd[j] == "_"):
        k = j
        dtext = []
        while k < len(cmd) and (cmd[k].isalnum() or cmd[k] == "_"):
            dtext.append(cmd[k])
            k += 1
        return strip_tabs, "".join(dtext), k
    return None  # no delimiter at all


def _find_heredoc_end(cmd: str, body_start: int, delim: str, strip_tabs: bool) -> Tuple[int, int, bool]:
    """Scan forward from `body_start` (just after the opener line's newline) for the first
    UNINDENTED (tab-stripped for `<<-`) line exactly equal to `delim`. Returns
    (body_end, term_end, found)."""
    n = len(cmd)
    k = body_start
    while k <= n:
        nl = cmd.find("\n", k)
        line = cmd[k: nl if nl != -1 else n]
        check_line = line.lstrip("\t") if strip_tabs else line
        if check_line == delim:
            term_end = (nl + 1) if nl != -1 else n
            return k, term_end, True
        if nl == -1:
            return n, n, False
        k = nl + 1
    return n, n, False


_FD_DUP_RE = re.compile(r"^\d*[<>]&\d*$")


def _scan(cmd: str, emit_words: bool):
    """The single shared scan loop. If `emit_words` is False, returns only the heredoc region
    list (used for classification/neutralize). If True, also returns the full top-level Word
    list (heredoc-body words are appended separately by the caller, once classified)."""
    n = len(cmd)
    i = 0
    words: List[Word] = []
    clause_index = 0
    buf: List[str] = []

    in_squote = False
    in_dquote = False
    sub_stack: List[str] = []          # 'cmdsub' | 'backtick' | 'arith' | 'procsub'

    pending: List[Dict] = []
    heredoc_regions: List[Dict] = []

    hard_clause_start = 0
    cur_other_consumer = False

    def cur_kind() -> str:
        if sub_stack:
            return "proc_sub" if sub_stack[-1] == "procsub" else "cmd_sub"
        return "word"

    def flush_word():
        nonlocal buf, cur_other_consumer
        if buf:
            text = "".join(buf)
            if not sub_stack and _FD_DUP_RE.match(text):
                cur_other_consumer = True
            if emit_words:
                words.append(Word(text, clause_index, False, cur_kind()))
        buf = []

    while i < n:
        ch = cmd[i]

        if in_squote:
            if ch == "'":
                in_squote = False
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        if in_dquote:
            if ch == "\\" and i + 1 < n and cmd[i + 1] in ('"', "\\", "$", "`"):
                buf.append(ch)
                buf.append(cmd[i + 1])
                i += 2
                continue
            if ch == '"':
                in_dquote = False
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        if ch == "'":
            in_squote = True
            i += 1
            continue
        if ch == '"':
            in_dquote = True
            i += 1
            continue

        if ch == "$" and i + 1 < n and cmd[i + 1] == "(":
            flush_word()
            if i + 2 < n and cmd[i + 2] == "(":
                sub_stack.append("arith")
                i += 3
            else:
                sub_stack.append("cmdsub")
                i += 2
            continue

        if ch == "`":
            flush_word()
            if sub_stack and sub_stack[-1] == "backtick":
                sub_stack.pop()
            else:
                sub_stack.append("backtick")
            i += 1
            continue

        if ch in (">", "<") and i + 1 < n and cmd[i + 1] == "(":
            flush_word()
            # AC-GSO-2(ii): a process-substitution consumer (`>(` / `<(`) anywhere in the
            # clause disqualifies it from inert-sink classification, regardless of where it
            # sits relative to the heredoc opener (`tee >(bash) <<EOF` names its own consumer
            # BEFORE the redirect). Tracked here rather than by text-matching the prefix so it
            # cannot be fooled by an unrelated `>(`/`<(`-shaped literal elsewhere.
            cur_other_consumer = True
            sub_stack.append("procsub")
            i += 2
            continue

        if ch == ")":
            if sub_stack and sub_stack[-1] in ("cmdsub", "procsub"):
                flush_word()
                sub_stack.pop()
                i += 1
                continue
            if sub_stack and sub_stack[-1] == "arith":
                flush_word()
                sub_stack.pop()
                i += 2 if (i + 1 < n and cmd[i + 1] == ")") else 1
                continue
            buf.append(ch)
            i += 1
            continue

        if not sub_stack and ch in "<>" and i + 1 < n and cmd[i + 1] == "&":
            cur_other_consumer = True

        # Heredoc opener — only at true top level (AC-GSO-1(c)); never inside a substitution
        # span, which is exactly what makes a `<<` inside `$( )` fall through as an ordinary,
        # unclassified token (AC-GSO-6).
        if not sub_stack and ch == "<" and i + 1 < n and cmd[i + 1] == "<" and not (
                i + 2 < n and cmd[i + 2] == "<") and not (i > 0 and cmd[i - 1] == "<"):
            open_pos = i
            parsed = _parse_heredoc_opener(cmd, i)
            if parsed is None:
                flush_word()
                if emit_words:
                    words.append(Word(cmd[open_pos:], clause_index, False, "code"))
                i = n
                break
            strip_tabs, delim, j = parsed
            if " " in delim or "\t" in delim:
                # AC-GSO-6: a quoted terminator containing spaces is not treated as an opener.
                buf.append(cmd[open_pos:j])
                i = j
                continue
            flush_word()
            pending.append({
                "delim": delim, "strip_tabs": strip_tabs,
                "clause_prefix": cmd[hard_clause_start:open_pos],
            })
            i = j
            continue

        if ch == "\n" and not sub_stack:
            flush_word()
            i += 1
            line_end_pos = i - 1
            if pending:
                for hd in pending:
                    body_start = i
                    body_end, term_end, found = _find_heredoc_end(
                        cmd, body_start, hd["delim"], hd["strip_tabs"])
                    heredoc_regions.append({
                        "delim": hd["delim"], "strip_tabs": hd["strip_tabs"],
                        "body_start": body_start, "body_end": body_end, "term_end": term_end,
                        "clause_prefix": hd["clause_prefix"],
                        "hard_clause_start": hard_clause_start, "line_end": line_end_pos,
                        "other_consumer": cur_other_consumer,
                        "unresolved": not found,
                        "same_line_count": len(pending),
                    })
                    i = term_end
                pending = []
            clause_index += 1
            hard_clause_start = i
            cur_other_consumer = False
            continue

        if not sub_stack and ch in ";&|":
            two = cmd[i:i + 2]
            flush_word()
            if two in ("&&", "||"):
                clause_index += 1
                hard_clause_start = i + 2
                cur_other_consumer = False
                i += 2
            elif ch == ";":
                clause_index += 1
                hard_clause_start = i + 1
                cur_other_consumer = False
                i += 1
            else:
                cur_other_consumer = True
                clause_index += 1
                i += 1
            continue

        if ch in " \t":
            flush_word()
            i += 1
            continue

        if ch == "#" and not sub_stack and not buf:
            nl = cmd.find("\n", i)
            end = nl if nl != -1 else n
            if emit_words:
                words.append(Word(cmd[i:end], clause_index, False, "comment"))
            i = end
            continue

        buf.append(ch)
        i += 1

    flush_word()
    if pending:
        for hd in pending:
            heredoc_regions.append({
                "delim": hd["delim"], "strip_tabs": hd["strip_tabs"],
                "body_start": n, "body_end": n, "term_end": n,
                "clause_prefix": hd["clause_prefix"],
                "hard_clause_start": hard_clause_start, "line_end": n,
                "other_consumer": cur_other_consumer, "unresolved": True,
                "same_line_count": len(pending),
            })

    return words, heredoc_regions, clause_index


def _path_word(tok: str) -> str:
    parts = [p for p in tok.split("/") if p not in ("", ".")]
    return parts[-1] if parts else tok


def _valid_sink_path(path: str) -> bool:
    """AC-GSO-2(iii): a plain relative or absolute regular-file path."""
    if not path or path == "-":
        return False
    if "$" in path or "`" in path:
        # AC-GSO-6: a redirect target that is a variable or expansion is never a recognized sink.
        return False
    if path.startswith("/dev/") or path.startswith("/proc/"):
        return False
    for pre in _temp_prefixes():
        if path == pre.rstrip("/") or path.startswith(pre):
            return False
    expanded = os.path.expanduser(path)
    try:
        if os.path.exists(expanded):
            st = os.lstat(expanded)
            if (stat.S_ISFIFO(st.st_mode) or stat.S_ISCHR(st.st_mode)
                    or stat.S_ISBLK(st.st_mode) or stat.S_ISSOCK(st.st_mode)):
                return False
    except Exception:
        return False
    return True


def _cat_redirect_target(rest: List[str], ops: List[str]) -> Optional[str]:
    """`rest` (the tokens after `cat`, before the delimiter) must be EXACTLY the redirect —
    `[op, target]` or one glued `op+target` token — and nothing else, keeping the sink shape
    tight (`cat > <path>` / `cat >> <path>`, no extra flags)."""
    if len(rest) == 2 and rest[0] in ops:
        return rest[1]
    if len(rest) == 1:
        for op in ops:
            if rest[0].startswith(op) and len(rest[0]) > len(op):
                return rest[0][len(op):]
    return None


def _match_inert_sink(clause_prefix: str) -> Optional[str]:
    """AC-GSO-2(i): does the clause text BEFORE the heredoc opener match one of the six closed
    inert-sink forms? Returns the sink path ('-' for the stdin-flag forms), or None (not a
    recognized sink -> code, convict on doubt)."""
    prefix = clause_prefix.strip()
    if not prefix:
        return None
    try:
        toks = shlex.split(prefix, posix=True)
    except Exception:
        return None
    if not toks:
        return None
    head = _path_word(toks[0])
    low = [t.lower() for t in toks]

    if head == "cat":
        path = _cat_redirect_target(toks[1:], [">>", ">"])
        # '-' is reserved for the git-commit/gh-create STDIN sentinel families below; as a
        # cat/tee redirect TARGET it is not a plain regular-file path (AC-GSO-2(iii)).
        return None if path == "-" else path

    if head == "tee":
        rest = toks[1:]
        bare = [t for t in rest if not t.startswith("-")]
        flags = [t for t in rest if t.startswith("-")]
        if flags or len(bare) != 1:
            return None
        return None if bare[0] == "-" else bare[0]

    if head == "git" and len(toks) >= 2 and low[1] == "commit":
        rest = toks[2:]
        for idx, t in enumerate(rest):
            tl = t.lower()
            if tl in ("-f", "--file") and idx + 1 < len(rest) and rest[idx + 1] == "-":
                return "-"
            if tl == "--file=-":
                return "-"
        return None

    if head == "gh" and len(toks) >= 3 and low[1] in ("pr", "issue") and low[2] == "create":
        rest = toks[3:]
        for idx, t in enumerate(rest):
            tl = t.lower()
            if tl == "--body-file" and idx + 1 < len(rest) and rest[idx + 1] == "-":
                return "-"
            if tl == "--body-file=-":
                return "-"
        return None

    return None


def _mentions_elsewhere(cmd: str, all_regions: List[Dict], region: Dict, path: str) -> bool:
    """AC-GSO-2(iv): does `path` (or its basename) appear in any OTHER clause of the same
    command string? Blanks every heredoc body (all regions) and this region's own clause span,
    then token-scans what remains."""
    chars = list(cmd)

    def blank(a, b):
        for k in range(a, min(b, len(chars))):
            if chars[k] != "\n":
                chars[k] = " "

    for r in all_regions:
        blank(r["body_start"], r["term_end"])
    blank(region["hard_clause_start"], region["line_end"])
    text = "".join(chars)
    norm = re.sub(r"[\r\n]+", " ; ", text)
    norm = re.sub(r"&&", " && ", norm)
    norm = re.sub(r"\|\|", " || ", norm)
    norm = re.sub(r";", " ; ", norm)
    norm = re.sub(r"\|", " | ", norm)
    norm = re.sub(r"&", " & ", norm)
    try:
        toks = shlex.split(norm, posix=True)
    except Exception:
        toks = norm.split()
    base = os.path.basename(path)
    for t in toks:
        if t == path or os.path.basename(t) == base:
            return True
    return False


def _classify_region(cmd: str, region: Dict, all_regions: List[Dict], has_crlf: bool) -> bool:
    if has_crlf or region["unresolved"]:
        return False
    if region.get("same_line_count", 1) > 1:
        # AC-GSO-6 "nested heredocs" — more than one heredoc opener on the same clause/line is
        # convict-on-doubt, regardless of which one might otherwise look like a sink.
        return False
    if region["other_consumer"]:
        return False
    sink_path = _match_inert_sink(region["clause_prefix"])
    if sink_path is None:
        return False
    if sink_path != "-":
        if not _valid_sink_path(sink_path):
            return False
        if _mentions_elsewhere(cmd, all_regions, region, sink_path):
            return False
    return True


def tokenize(command) -> List[Word]:
    """Tokenize a Bash command string per AC-GSO-1..2 and AC-GSO-6. See the module docstring."""
    if not isinstance(command, str) or command == "":
        return []

    # (a) backslash line continuations removed FIRST, before any other step.
    cmd = re.sub(r"\\\r?\n", "", command)
    has_crlf = "\r" in cmd

    words, regions, last_clause = _scan(cmd, emit_words=True)

    heredoc_clause_counter = last_clause + 1
    for region in regions:
        classified_data = _classify_region(cmd, region, regions, has_crlf)
        body_text = cmd[region["body_start"]:region["body_end"]]
        this_clause = heredoc_clause_counter
        heredoc_clause_counter += 1
        for tok in body_text.split():
            words.append(Word(tok, this_clause, classified_data, "heredoc"))

    return words


def neutralize(command) -> str:
    """Return `command` with every `data`-classified heredoc body's non-newline characters
    replaced by a space. Both guard hooks scan THIS text with their existing, unchanged clause
    logic instead of the raw command — see the module docstring."""
    if not isinstance(command, str) or command == "":
        return command if isinstance(command, str) else ""

    cmd = re.sub(r"\\\r?\n", "", command)
    has_crlf = "\r" in cmd
    _, regions, _ = _scan(cmd, emit_words=False)

    chars = list(cmd)
    for region in regions:
        if _classify_region(cmd, region, regions, has_crlf):
            for k in range(region["body_start"], min(region["body_end"], len(chars))):
                if chars[k] != "\n":
                    chars[k] = " "
    return "".join(chars)
