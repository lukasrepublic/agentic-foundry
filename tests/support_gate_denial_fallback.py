"""tests/support_gate_denial_fallback.py — pure helpers for tests/test_gate_denial_fallback.py
(feat-foundry-gate-denial-fallback).

Everything here is a pure function/predicate over bytes/strings passed in, or over an explicit
`root=` directory (never the real repo tree implicitly) — the same convention
`tests/support_agent_hardening.py` already uses.
"""
from __future__ import annotations

import os
import re


# ============================================================ canonical constants ==== #

CLAUSE_REL_PATH = "docs/harness-denial-fallback.md"

CLAUSE_START = b"<!-- foundry:harness-denial-fallback v1 -->"
CLAUSE_END = b"<!-- /foundry:harness-denial-fallback -->"

LIMB_A_LABEL = "**(a) Emit the exact ready-to-run command.**"
LIMB_B_LABEL = "**(b) Stop.**"
LIMB_C_LABEL = "**(c) Name the durable fix.**"
LIMB_LABELS_IN_ORDER = (LIMB_A_LABEL, LIMB_B_LABEL, LIMB_C_LABEL)

LIMB_A_LITERALS = (
    "`!`",
    "in-session",
    "byte-identical",
    "never freeform",
    "spec or PR body",
    "name the override flag",
)
LIMB_B_LITERALS = (
    "never retry",
    "never route around",
    "another tool or credential",
    "documented degraded path",
    "UPSTREAM-SUBMIT-LABEL-DEGRADED",
    "GATED",
)
LIMB_C_LITERALS = (
    ".claude/settings.json",
    "trust dialog",
)
LIMB_LITERALS = {
    LIMB_A_LABEL: LIMB_A_LITERALS,
    LIMB_B_LABEL: LIMB_B_LITERALS,
    LIMB_C_LABEL: LIMB_C_LITERALS,
}

SKILLS_SECTION_HEADING = "## Skills that carry this clause"

SEVEN_CEREMONY_SKILLS = (
    "skills/authorize/SKILL.md",
    "skills/authorize-release/SKILL.md",
    "skills/cut-release/SKILL.md",
    "skills/decommission-gate/SKILL.md",
    "skills/release/SKILL.md",
    "skills/upstream-submit/SKILL.md",
    "skills/id-apply/SKILL.md",
)

POINTER_PATH_LITERAL = "docs/harness-denial-fallback.md"
POINTER_STOP_LITERAL = "STOP"
TRIGGER_LITERALS = ("harness denial", "permission denial")

RESUMPTION_LABEL = "**Resuming after a real grant.**"

RETRY_TOKENS = (
    "retry", "retries", "retried", "retrying",
    "re-run", "re-runs", "re-ran", "re-running",
    "rerun", "reran", "rerunning",
    "run it again", "try again", "attempt again",
    "route around", "work around", "workaround",
    "circumvent", "sidestep",
    "bypass", "bypasses", "bypassed", "bypassing",
)
NEGATORS = ("never", "do not", "must not")

_RETRY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in sorted(RETRY_TOKENS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_NEGATOR_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in NEGATORS) + r")\b",
    re.IGNORECASE,
)
_TRIGGER_RE = re.compile(
    "|".join(re.escape(t) for t in TRIGGER_LITERALS),
    re.IGNORECASE,
)

_WS_RUN_RE = re.compile(r"\s+")


def normalize_ws(s: str) -> str:
    """Replace every maximal run of whitespace (including the hard line-wraps ordinary
    markdown prose uses) with a single space, so a required literal that happens to straddle
    a soft line-wrap in the source file is still matched. Multi-word literals below are
    single-space-joined, matching this normalization."""
    return _WS_RUN_RE.sub(" ", s).strip()


# ============================================================ delimited-region helpers ==== #

def _literal_line_positions(data: bytes, marker: bytes):
    out = []
    pos = 0
    for line in data.split(b"\n"):
        if line == marker:
            out.append(pos)
        pos += len(line) + 1
    return out


def extract_delimited_region(data: bytes, start_marker: bytes, end_marker: bytes):
    """Mandatory, exactly-one region. Returns (region_bytes_or_None, n_starts, n_ends, ok)."""
    starts = _literal_line_positions(data, start_marker)
    ends = _literal_line_positions(data, end_marker)
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        return None, len(starts), len(ends), False
    s = starts[0]
    e = ends[0] + len(end_marker)
    return data[s:e], len(starts), len(ends), True


def _line_start_offsets(text: str):
    """[(char_offset, line_index)] for every line in text (0-indexed line_index)."""
    out = []
    pos = 0
    for i, line in enumerate(text.split("\n")):
        out.append((pos, i, line))
        pos += len(line) + 1
    return out


def find_line_leading_label(text: str, label: str):
    """Positions (char offsets) of every line whose lstripped content starts with `label`.
    Returns a list of char offsets (start of the label itself, i.e. offset of the line start
    plus any leading whitespace)."""
    out = []
    for pos, _i, line in _line_start_offsets(text):
        stripped = line.lstrip()
        if stripped.startswith(label):
            leading_ws = len(line) - len(stripped)
            out.append(pos + leading_ws)
    return out


def parse_limbs(region_text: str):
    """Locate the three labelled limbs, in order, inside `region_text` (the FULL region,
    including its delimiter lines). Returns a dict:
        {
          "ok": bool,
          "errors": [str, ...],
          "limbs": {label: limb_text_or_None, ...},
        }
    A limb's text runs from its own label's offset to the next label's offset (or end of
    region_text for the last limb). `ok` requires each label to appear EXACTLY ONCE,
    line-leading, and in the declared order.
    """
    errors = []
    offsets = {}
    for label in LIMB_LABELS_IN_ORDER:
        positions = find_line_leading_label(region_text, label)
        if len(positions) != 1:
            errors.append(f"label {label!r} found {len(positions)} time(s) (expected exactly 1)")
            offsets[label] = None
        else:
            offsets[label] = positions[0]

    ok = not errors
    if ok:
        vals = [offsets[l] for l in LIMB_LABELS_IN_ORDER]
        if vals != sorted(vals):
            ok = False
            errors.append(f"limb labels out of order: offsets={vals} for {LIMB_LABELS_IN_ORDER}")

    limbs = {}
    if ok:
        for idx, label in enumerate(LIMB_LABELS_IN_ORDER):
            start = offsets[label]
            end = offsets[LIMB_LABELS_IN_ORDER[idx + 1]] if idx + 1 < len(LIMB_LABELS_IN_ORDER) else len(region_text)
            limbs[label] = region_text[start:end]
    else:
        for label in LIMB_LABELS_IN_ORDER:
            limbs[label] = None

    return {"ok": ok, "errors": errors, "limbs": limbs}


def missing_limb_literals(limb_text: str, literals):
    normalized = normalize_ws(limb_text)
    return [lit for lit in literals if normalize_ws(lit) not in normalized]


# ============================================================ skills-section helpers ==== #

_SKILL_PATH_RE = re.compile(r"`(skills/[A-Za-z0-9._-]+/SKILL\.md)`")


def parse_skills_section(doc_text: str):
    """Extract the `## Skills that carry this clause` section's list of skill paths.
    Returns (paths_list_or_None, ok). `ok` is False if the heading is absent or no list
    items are found beneath it before the next heading/EOF."""
    lines = doc_text.split("\n")
    heading_idx = None
    for i, line in enumerate(lines):
        if line.strip() == SKILLS_SECTION_HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        return None, False

    paths = []
    for line in lines[heading_idx + 1:]:
        if line.startswith("#"):
            break
        m = _SKILL_PATH_RE.search(line)
        if m:
            paths.append(m.group(1))
    if not paths:
        return None, False
    return paths, True


# ============================================================ pointer-line helpers ==== #

def find_pointer_lines(skill_text: str):
    """All lines in `skill_text` that carry the pointer: the literal path, the literal
    'STOP', and a case-insensitive trigger literal — all on the SAME line. Returns a list
    of (1-indexed line number, line text)."""
    out = []
    for i, line in enumerate(skill_text.split("\n")):
        if POINTER_PATH_LITERAL not in line:
            continue
        if POINTER_STOP_LITERAL not in line:
            continue
        if not _TRIGGER_RE.search(line):
            continue
        out.append((i + 1, line))
    return out


# ============================================================ AC-GDF-3: retry-token scan ==== #

def _paragraphs_with_lineno(lines_with_no, ):
    """`lines_with_no`: list of (lineno, text). Returns list of paragraphs, each a list of
    (lineno, text) for a maximal run of consecutive non-blank lines."""
    paragraphs = []
    current = []
    for lineno, text in lines_with_no:
        if text.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append((lineno, text))
    if current:
        paragraphs.append(current)
    return paragraphs


def _join_paragraph(paragraph):
    """paragraph: [(lineno, text), ...]. Returns (joined_text, spans) where spans is a
    sorted list of (start_offset_in_joined, lineno) covering `joined_text`."""
    parts = []
    spans = []
    pos = 0
    for idx, (lineno, text) in enumerate(paragraph):
        if idx > 0:
            parts.append(" ")
            pos += 1
        spans.append((pos, lineno))
        parts.append(text)
        pos += len(text)
    return "".join(parts), spans


def _lineno_at(spans, offset):
    lineno = spans[0][1]
    for start, ln in spans:
        if start <= offset:
            lineno = ln
        else:
            break
    return lineno


def _split_sentences(paragraph_text: str):
    """Returns [(start_offset, sentence_text), ...] — split after each '.', '!' or '?' that
    is followed by whitespace or ends the paragraph."""
    sentences = []
    start = 0
    n = len(paragraph_text)
    i = 0
    while i < n:
        ch = paragraph_text[i]
        if ch in ".!?" and (i + 1 >= n or paragraph_text[i + 1].isspace()):
            sentences.append((start, paragraph_text[start:i + 1]))
            j = i + 1
            while j < n and paragraph_text[j].isspace():
                j += 1
            start = j
            i = j
            continue
        i += 1
    if start < n:
        sentences.append((start, paragraph_text[start:]))
    return sentences


def scan_retry_violations(source_label: str, lines_with_no):
    """`lines_with_no`: list of (1-indexed lineno, text) for ONE source (the clause region,
    or one skill's pointer line). Returns a list of violation dicts:
        {"file": source_label, "line": lineno, "token": matched_text}
    for every un-exempt occurrence of an enumerated retry token.
    """
    violations = []
    for paragraph in _paragraphs_with_lineno(lines_with_no):
        first_line_text = paragraph[0][1]
        if first_line_text.lstrip().startswith(RESUMPTION_LABEL):
            # exemption (ii): the whole resumption-block paragraph is exempt.
            continue
        joined, spans = _join_paragraph(paragraph)
        for sent_start, sent_text in _split_sentences(joined):
            negator_positions = [m.start() for m in _NEGATOR_RE.finditer(sent_text)]
            for m in _RETRY_RE.finditer(sent_text):
                tok_pos = m.start()
                exempt = any(neg_pos < tok_pos for neg_pos in negator_positions)
                if not exempt:
                    abs_offset = sent_start + tok_pos
                    lineno = _lineno_at(spans, abs_offset)
                    violations.append({"file": source_label, "line": lineno, "token": m.group(0)})
    return violations


def region_lines_with_lineno(full_doc_text: str, region_text: str):
    """Maps `region_text` (as returned by extract_delimited_region, decoded) back onto its
    1-indexed line numbers within `full_doc_text`."""
    offset = full_doc_text.index(region_text)
    start_lineno = full_doc_text.count("\n", 0, offset) + 1
    out = []
    for i, line in enumerate(region_text.split("\n")):
        out.append((start_lineno + i, line))
    return out


# ============================================================ the evaluator ==== #

def evaluate_gate_denial_fallback(root: str):
    """Runs every AC-GDF invariant over the tree rooted at `root`. Returns a dict of
    per-AC booleans plus enough detail to build assertion messages and drive the AC-GDF-4
    negative control."""
    res = {
        "ac1_ok": True, "ac1_detail": [],
        "ac2_ok": True, "ac2_detail": [],
        "ac3_ok": True, "ac3_detail": [],
    }

    clause_path = os.path.join(root, CLAUSE_REL_PATH)
    if not os.path.isfile(clause_path):
        res["ac1_ok"] = False
        res["ac1_detail"].append(f"{CLAUSE_REL_PATH}: file not found")
        res["ac2_ok"] = False
        res["ac2_detail"].append(f"{CLAUSE_REL_PATH}: file not found — cannot check skills-section/three-way equality")
        res["ac3_ok"] = False
        res["ac3_detail"].append(f"{CLAUSE_REL_PATH}: file not found — cannot scan for retry tokens")
        return res

    with open(clause_path, "rb") as f:
        doc_bytes = f.read()
    doc_text = doc_bytes.decode("utf-8")

    region_bytes, n_starts, n_ends, region_ok = extract_delimited_region(doc_bytes, CLAUSE_START, CLAUSE_END)
    region_text = None
    limb_parse = None
    if not region_ok:
        res["ac1_ok"] = False
        res["ac1_detail"].append(
            f"{CLAUSE_REL_PATH}: delimited region malformed (starts={n_starts}, ends={n_ends})"
        )
    else:
        region_text = region_bytes.decode("utf-8")
        limb_parse = parse_limbs(region_text)
        if not limb_parse["ok"]:
            res["ac1_ok"] = False
            for err in limb_parse["errors"]:
                res["ac1_detail"].append(f"{CLAUSE_REL_PATH}: {err}")
        else:
            for label in LIMB_LABELS_IN_ORDER:
                missing = missing_limb_literals(limb_parse["limbs"][label], LIMB_LITERALS[label])
                if missing:
                    res["ac1_ok"] = False
                    res["ac1_detail"].append(
                        f"{CLAUSE_REL_PATH}: limb {label!r} missing required literal(s) {missing}"
                    )

    # ---------------------------------------------------------------- AC-GDF-2 -------- #
    listed_paths, listed_ok = parse_skills_section(doc_text)
    if not listed_ok:
        res["ac2_ok"] = False
        res["ac2_detail"].append(f"{CLAUSE_REL_PATH}: {SKILLS_SECTION_HEADING!r} section missing or unparseable")
        listed_set = set()
    else:
        listed_set = set(listed_paths)

    enumerated_set = set(SEVEN_CEREMONY_SKILLS)

    on_disk_set = set()
    skill_pointer_lines = {}  # relpath -> [(lineno, text), ...]
    skills_dir = os.path.join(root, "skills")
    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            skill_md = os.path.join("skills", name, "SKILL.md")
            abs_path = os.path.join(root, skill_md)
            if not os.path.isfile(abs_path):
                continue
            with open(abs_path, "rb") as f:
                skill_bytes = f.read()
            skill_text = skill_bytes.decode("utf-8")
            pointers = find_pointer_lines(skill_text)
            if pointers:
                on_disk_set.add(skill_md.replace(os.sep, "/"))
                skill_pointer_lines[skill_md.replace(os.sep, "/")] = pointers

    if enumerated_set != listed_set or enumerated_set != on_disk_set or listed_set != on_disk_set:
        res["ac2_ok"] = False
        res["ac2_detail"].append(
            "three-way set mismatch: "
            f"enumerated={sorted(enumerated_set)} "
            f"listed={sorted(listed_set)} "
            f"on_disk={sorted(on_disk_set)} "
            f"(enumerated^listed={sorted(enumerated_set ^ listed_set)}, "
            f"enumerated^on_disk={sorted(enumerated_set ^ on_disk_set)}, "
            f"listed^on_disk={sorted(listed_set ^ on_disk_set)})"
        )

    # ---------------------------------------------------------------- AC-GDF-3 -------- #
    violations = []
    if region_text is not None:
        violations.extend(scan_retry_violations(CLAUSE_REL_PATH, region_lines_with_lineno(doc_text, region_text)))
    for relpath, pointers in sorted(skill_pointer_lines.items()):
        for lineno, text in pointers:
            violations.extend(scan_retry_violations(relpath, [(lineno, text)]))

    if violations:
        res["ac3_ok"] = False
        for v in violations:
            res["ac3_detail"].append(f"{v['file']}:{v['line']}: un-negated retry token {v['token']!r}")

    return res
