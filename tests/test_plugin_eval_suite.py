"""tests/test_plugin_eval_suite.py — plugin-eval-suite (autonomy-continuation R4, AC-PES-3).

Validates the shape of every case under `evals/` WITHOUT running the eval — the eval itself is
the operator's own `claude plugin eval` run, and it bills real model calls (AC-PES-3's own
constraint). Every field name and grader `type` checked here is quoted verbatim from the
"Test plugins with evals" primary doc (re-read 2026-09-19; see
`.foundry/releases/ac-r4-certify-and-shed/charters/plugin-eval-suite.md`'s Primary-doc facts):

- prompt.md frontmatter fields ("prompt.md frontmatter" reference table): `schema_version`,
  `name`, `description`, `tags`, `plugins`, `runs`, `expected_outcome`, `model`, `max_turns`,
  `timeout_seconds`, `allowed_tools`, `append_system_prompt`, `env`. "An unknown key is an
  error" — so this test convicts one too, rather than only checking the keys we happened to use.
- case.yaml fields: the same `prompt.md`-shared fields at top level (minus `max_turns`,
  `timeout_seconds`, `allowed_tools`, `append_system_prompt`, `env`, which the doc places under
  `execution:` in case.yaml), plus `context.scaffold_script` / `context.history_file` /
  `context.add_dirs`, `execution.prompt`, and `graders` (a list of grader definitions).
- Grader frontmatter ("Grader frontmatter" reference table): `type` (required), `weight`, `arm`.
  Grader `type` ("Grader types" reference table): one of exactly `regex`, `tool_used`,
  `tool_order`, `file_exists`, `llm`, `baseline` — no custom-code graders exist.

AC-PES-1 also requires the six named cases to exist, each with at least one grader.
"""
from __future__ import annotations

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS_DIR = os.path.join(REPO_ROOT, "evals")

# The exact `prompt.md` frontmatter vocabulary (primary doc's "prompt.md frontmatter" table).
PROMPT_MD_FIELDS = frozenset({
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
    "model", "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env",
})

# case.yaml top-level fields (primary doc's "case.yaml fields" section): `schema_version` and
# `name` are required; the prompt.md-shared fields that stay top-level per the doc's own
# sentence ("The prompt.md fields description, tags, plugins, runs, and expected_outcome go at
# the top level"); `execution` and `context` are nested blocks; `graders` is case.yaml-only.
CASE_YAML_TOP_FIELDS = frozenset({
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
    "execution", "context", "graders",
})
# "model, max_turns, timeout_seconds, allowed_tools, append_system_prompt, and env go under
# execution:" -- plus execution.prompt ("the prompt, when you keep the whole case in case.yaml").
CASE_YAML_EXECUTION_FIELDS = frozenset({
    "model", "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env",
    "prompt",
})
CASE_YAML_CONTEXT_FIELDS = frozenset({"scaffold_script", "history_file", "add_dirs"})

# Grader frontmatter's own keys (primary doc's "Grader frontmatter" table) plus every type's own
# option keys (primary doc's "Grader types" table) -- checked together per grader file, since a
# grader file's frontmatter is one flat mapping of both.
GRADER_FRONTMATTER_FIELDS = frozenset({"type", "weight", "arm"})
GRADER_TYPE_OPTION_FIELDS = {
    "regex": frozenset({"pattern", "flags", "match", "target"}),
    "tool_used": frozenset({"tool", "input_match", "min", "max"}),
    "tool_order": frozenset({"before", "after"}),
    "file_exists": frozenset({"path", "exists"}),
    "llm": frozenset({"criteria", "focus"}),
    "baseline": frozenset({"baseline_file", "criteria"}),
}
# "Of the six types" -- the closed set, verbatim.
GRADER_TYPES = frozenset(GRADER_TYPE_OPTION_FIELDS)

REQUIRED_CASE_NAMES = (
    "keep-going",
    "blocker-with-evidence",
    "merge-waits-with-primitive",
    "unauthorized-claim-refused",
    "message-kind-lint",
    "state-read-first",
)


class EvalSuiteShapeError(Exception):
    """A case/grader file's shape does not match the primary doc's documented vocabulary."""


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _parse_frontmatter(text, path):
    """(frontmatter_dict, body) for a `---`-fenced YAML frontmatter file. Raises
    EvalSuiteShapeError on anything that would be an eval-time error: a missing fence, an
    unterminated fence, or frontmatter that isn't a YAML mapping."""
    if not text.startswith("---"):
        raise EvalSuiteShapeError(f"{path}: no `---` frontmatter fence at line 1")
    end = text.find("\n---", 3)
    if end == -1:
        raise EvalSuiteShapeError(f"{path}: unterminated frontmatter fence")
    fm_text = text[3:end]
    body = text[end + 4:]
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise EvalSuiteShapeError(f"{path}: frontmatter YAML parse error: {e}") from e
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        raise EvalSuiteShapeError(f"{path}: frontmatter is not a YAML mapping")
    return fm, body


def _case_dirs():
    """Every case directory directly under evals/ that carries a prompt.md or case.yaml --
    mirrors the primary doc's own case-discovery rule ("only prompt.md or case.yaml is required
    for a case to exist"). Does not recurse into results/ or mocks/, which are never cases."""
    if not os.path.isdir(EVALS_DIR):
        raise EvalSuiteShapeError(f"missing evals/ directory at {EVALS_DIR}")
    dirs = []
    for name in sorted(os.listdir(EVALS_DIR)):
        if name in ("results", "mocks"):
            continue
        full = os.path.join(EVALS_DIR, name)
        if not os.path.isdir(full):
            continue
        if os.path.isfile(os.path.join(full, "prompt.md")) or os.path.isfile(
            os.path.join(full, "case.yaml")
        ):
            dirs.append(full)
    return dirs


def _grader_files(case_dir):
    graders_dir = os.path.join(case_dir, "graders")
    if not os.path.isdir(graders_dir):
        return []
    return sorted(
        os.path.join(graders_dir, n) for n in os.listdir(graders_dir) if n.endswith(".md")
    )


def _validate_prompt_md(path):
    fm, _body = _parse_frontmatter(_read(path), path)
    unknown = set(fm) - PROMPT_MD_FIELDS
    if unknown:
        raise EvalSuiteShapeError(
            f"{path}: unknown prompt.md frontmatter key(s) {sorted(unknown)} -- an unknown key "
            f"is an eval-time error per the primary doc"
        )


def _validate_case_yaml(path):
    doc = yaml.safe_load(_read(path)) or {}
    if not isinstance(doc, dict):
        raise EvalSuiteShapeError(f"{path}: case.yaml is not a YAML mapping")
    unknown = set(doc) - CASE_YAML_TOP_FIELDS
    if unknown:
        raise EvalSuiteShapeError(f"{path}: unknown case.yaml top-level key(s) {sorted(unknown)}")
    if "schema_version" in doc and doc["schema_version"] != "1.1":
        raise EvalSuiteShapeError(f"{path}: schema_version must be \"1.1\", got {doc['schema_version']!r}")
    execution = doc.get("execution")
    if execution is not None:
        if not isinstance(execution, dict):
            raise EvalSuiteShapeError(f"{path}: execution: must be a mapping")
        unknown_exec = set(execution) - CASE_YAML_EXECUTION_FIELDS
        if unknown_exec:
            raise EvalSuiteShapeError(f"{path}: unknown case.yaml execution key(s) {sorted(unknown_exec)}")
    context = doc.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise EvalSuiteShapeError(f"{path}: context: must be a mapping")
        unknown_ctx = set(context) - CASE_YAML_CONTEXT_FIELDS
        if unknown_ctx:
            raise EvalSuiteShapeError(f"{path}: unknown case.yaml context key(s) {sorted(unknown_ctx)}")


def _validate_grader(path):
    fm, body = _parse_frontmatter(_read(path), path)
    if "type" not in fm:
        raise EvalSuiteShapeError(f"{path}: grader frontmatter missing required `type`")
    gtype = fm["type"]
    if gtype not in GRADER_TYPES:
        raise EvalSuiteShapeError(
            f"{path}: grader type {gtype!r} is not one of the six documented types "
            f"{sorted(GRADER_TYPES)}"
        )
    allowed = GRADER_FRONTMATTER_FIELDS | GRADER_TYPE_OPTION_FIELDS[gtype]
    unknown = set(fm) - allowed
    if unknown:
        raise EvalSuiteShapeError(
            f"{path}: unknown key(s) {sorted(unknown)} for grader type {gtype!r} "
            f"(allowed: {sorted(allowed)})"
        )
    if gtype == "llm" and "criteria" not in fm and not body.strip():
        raise EvalSuiteShapeError(f"{path}: llm grader has neither `criteria` nor a body rubric")


def test_evals_dir_exists():
    assert os.path.isdir(EVALS_DIR), f"evals/ does not exist at {EVALS_DIR}"


def test_required_six_cases_exist():
    """AC-PES-1: at least six cases, one per measured failure, with these exact names."""
    present = {os.path.basename(d) for d in _case_dirs()}
    missing = [n for n in REQUIRED_CASE_NAMES if n not in present]
    assert not missing, f"required case(s) missing under evals/: {missing} (found: {sorted(present)})"


def test_every_case_has_prompt_or_case_yaml():
    dirs = _case_dirs()
    assert dirs, f"no case directories found under {EVALS_DIR}"
    for case_dir in dirs:
        has_prompt = os.path.isfile(os.path.join(case_dir, "prompt.md"))
        has_case_yaml = os.path.isfile(os.path.join(case_dir, "case.yaml"))
        assert has_prompt or has_case_yaml, f"{case_dir}: neither prompt.md nor case.yaml"


def test_every_case_has_at_least_one_grader():
    for case_dir in _case_dirs():
        graders = _grader_files(case_dir)
        assert graders, f"{case_dir}: no graders/*.md -- every case needs at least one grader"


def test_prompt_md_frontmatter_keys_are_documented():
    for case_dir in _case_dirs():
        prompt_path = os.path.join(case_dir, "prompt.md")
        if os.path.isfile(prompt_path):
            _validate_prompt_md(prompt_path)


def test_case_yaml_keys_are_documented():
    for case_dir in _case_dirs():
        case_yaml_path = os.path.join(case_dir, "case.yaml")
        if os.path.isfile(case_yaml_path):
            _validate_case_yaml(case_yaml_path)


def test_grader_types_are_documented():
    for case_dir in _case_dirs():
        for grader_path in _grader_files(case_dir):
            _validate_grader(grader_path)


def test_required_cases_max_turns_and_timeout_floor():
    """AC-PES-1: every case's max_turns >= 30, timeout_seconds >= 600."""
    for name in REQUIRED_CASE_NAMES:
        case_dir = os.path.join(EVALS_DIR, name)
        prompt_path = os.path.join(case_dir, "prompt.md")
        assert os.path.isfile(prompt_path), f"{name}: no prompt.md"
        fm, _ = _parse_frontmatter(_read(prompt_path), prompt_path)
        max_turns = fm.get("max_turns")
        timeout_seconds = fm.get("timeout_seconds")
        assert isinstance(max_turns, int) and max_turns >= 30, (
            f"{name}: prompt.md max_turns must be an int >= 30, got {max_turns!r}"
        )
        assert isinstance(timeout_seconds, int) and timeout_seconds >= 600, (
            f"{name}: prompt.md timeout_seconds must be an int >= 600, got {timeout_seconds!r}"
        )


def test_required_cases_tag_the_release_that_built_the_behaviour():
    """AC-PES-1: tags naming the release that built the behaviour -- a version tag of the shape
    vX.Y.Z, sourced from either case.yaml's top-level tags or prompt.md's own tags (prompt.md
    frontmatter overrides case.yaml on a shared field, so either source is a legitimate carrier)."""
    version_re_source = r"^v\d+\.\d+\.\d+$"
    import re

    version_re = re.compile(version_re_source)
    for name in REQUIRED_CASE_NAMES:
        case_dir = os.path.join(EVALS_DIR, name)
        tags = []
        case_yaml_path = os.path.join(case_dir, "case.yaml")
        if os.path.isfile(case_yaml_path):
            doc = yaml.safe_load(_read(case_yaml_path)) or {}
            tags.extend(doc.get("tags") or [])
        prompt_path = os.path.join(case_dir, "prompt.md")
        if os.path.isfile(prompt_path):
            fm, _ = _parse_frontmatter(_read(prompt_path), prompt_path)
            tags.extend(fm.get("tags") or [])
        version_tags = [t for t in tags if isinstance(t, str) and version_re.match(t)]
        assert version_tags, f"{name}: no vX.Y.Z release tag among {tags!r}"


def test_tool_used_skill_graders_are_with_only_or_both():
    """Charter instruction: `arm: with-only` on any `tool_used: Skill` indicator (mirrors the
    primary doc's own worked example: a check that can never pass without the plugin must be
    excluded from the two-arm score, or explicitly forced into both with `arm: both`)."""
    for case_dir in _case_dirs():
        for grader_path in _grader_files(case_dir):
            fm, _ = _parse_frontmatter(_read(grader_path), grader_path)
            if fm.get("type") == "tool_used" and fm.get("tool") == "Skill":
                assert fm.get("arm") in ("with-only", "both"), (
                    f"{grader_path}: a tool_used: Skill grader must set arm: with-only or "
                    f"arm: both"
                )


def test_scaffold_scripts_referenced_from_context_exist_and_are_executable():
    for case_dir in _case_dirs():
        case_yaml_path = os.path.join(case_dir, "case.yaml")
        if not os.path.isfile(case_yaml_path):
            continue
        doc = yaml.safe_load(_read(case_yaml_path)) or {}
        script = (doc.get("context") or {}).get("scaffold_script")
        if not script:
            continue
        script_path = os.path.join(case_dir, script)
        assert os.path.isfile(script_path), f"{case_yaml_path}: scaffold_script {script!r} not found"
        assert os.access(script_path, os.X_OK), f"{script_path}: scaffold_script is not executable"


def test_add_dirs_referenced_from_context_exist():
    for case_dir in _case_dirs():
        case_yaml_path = os.path.join(case_dir, "case.yaml")
        if not os.path.isfile(case_yaml_path):
            continue
        doc = yaml.safe_load(_read(case_yaml_path)) or {}
        for rel in (doc.get("context") or {}).get("add_dirs") or []:
            full = os.path.join(case_dir, rel)
            assert os.path.isdir(full), f"{case_yaml_path}: add_dirs entry {rel!r} not found"


def test_regex_graders_never_use_python_inline_flag_groups():
    """Base-RED evidence (this atom's own `claude plugin eval . --case keep-going` try): the
    engine's regex is JavaScript, which has no `(?i)`/`(?s)`/etc inline-flag-group syntax --
    `no-stop-and-ask` shipped with `pattern: (?i)\\b...` and the run reported `grader threw:
    Invalid regular expression: unrecognized character after (?`. Case-insensitivity and
    dotall belong in the `flags:` field (the primary doc, verbatim: "Put case-insensitivity in
    flags: i; inline (?i) isn't supported"). Fixed in the same commit that adds this check."""
    import re

    inline_flag_group_re = re.compile(r"\(\?[a-zA-Z]")
    for case_dir in _case_dirs():
        for grader_path in _grader_files(case_dir):
            fm, _ = _parse_frontmatter(_read(grader_path), grader_path)
            if fm.get("type") != "regex":
                continue
            pattern = fm.get("pattern")
            if not isinstance(pattern, str):
                continue
            m = inline_flag_group_re.search(pattern)
            assert not m, (
                f"{grader_path}: pattern uses a Python-only inline flag group {m.group()!r} -- "
                f"JavaScript regex has no such syntax; use the flags: field instead"
            )


def test_regex_grader_flags_are_documented_js_letters():
    """`flags` (primary doc: "Put case-insensitivity in flags: i") is passed straight through
    to a JavaScript RegExp's own flag string -- restrict it to that engine's own letters so a
    typo fails here rather than as an opaque `grader threw` at billed-run time."""
    valid_js_flags = frozenset("dgimsuvy")
    for case_dir in _case_dirs():
        for grader_path in _grader_files(case_dir):
            fm, _ = _parse_frontmatter(_read(grader_path), grader_path)
            if fm.get("type") != "regex":
                continue
            flags = fm.get("flags")
            if flags is None:
                continue
            assert isinstance(flags, str) and flags and set(flags) <= valid_js_flags, (
                f"{grader_path}: flags {flags!r} is not a plain string of JS RegExp flag "
                f"letters ({sorted(valid_js_flags)})"
            )


def test_no_generated_results_committed():
    """The primary doc: "add results/ to .gitignore" -- a run's aggregate-result.json/report.html
    are per-run local evidence, never suite source. evals/.gitignore covers it; this asserts
    nothing under evals/results/ is actually tracked."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "evals/results"], cwd=REPO_ROOT, capture_output=True, text=True,
        check=True,
    ).stdout.strip()
    assert not tracked, f"evals/results/ has tracked file(s):\n{tracked}"


def test_negative_control_unknown_prompt_key_convicts(tmp_path):
    """Convicts the checker itself: an unknown prompt.md key must be caught, mirroring the
    primary doc's own 'an unknown key is an error'."""
    bad = tmp_path / "prompt.md"
    bad.write_text("---\nmax_turns: 10\nnot_a_real_field: true\n---\n\nhello\n", encoding="utf-8")
    try:
        _validate_prompt_md(str(bad))
    except EvalSuiteShapeError:
        pass
    else:
        raise AssertionError("expected EvalSuiteShapeError for an unknown prompt.md key")


def test_negative_control_unknown_grader_type_convicts(tmp_path):
    bad = tmp_path / "grader.md"
    bad.write_text("---\ntype: does_not_exist\n---\n", encoding="utf-8")
    try:
        _validate_grader(str(bad))
    except EvalSuiteShapeError:
        pass
    else:
        raise AssertionError("expected EvalSuiteShapeError for an undocumented grader type")


def test_negative_control_unknown_grader_option_convicts(tmp_path):
    """A `regex` grader carrying a `tool`-only option key (tool_used's, not regex's) must be
    caught -- proves the per-type option check is real, not just the type-membership check."""
    bad = tmp_path / "grader.md"
    bad.write_text("---\ntype: regex\ntool: Bash\n---\n", encoding="utf-8")
    try:
        _validate_grader(str(bad))
    except EvalSuiteShapeError:
        pass
    else:
        raise AssertionError("expected EvalSuiteShapeError for a regex grader with a tool key")
