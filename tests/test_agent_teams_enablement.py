"""tests/test_agent_teams_enablement.py — feat-agent-teams-enablement (AC-ATE-1..-5).

Two live seams, per AC-ATE-5:

1. `workflows/release-wave.js` never passes `name:` inside any `agent(...)` call's option object
   (AC-ATE-2) — verified with a real (not string-fooled) parse: `agent(...)` call sites are found by
   a paren-depth scan over the SAME elided view `tests/test_workflow_export_shape.py` already uses
   to keep string/template/comment content from ever being mistaken for code, then each call's span
   is searched for a `name` property key.
2. `scripts/foundry-doctor.py`'s `agent-teams` advisory line (AC-ATE-4) — on/off fixtures over the
   effective settings files' `env` block, at every precedence layer (`~/.claude/settings.json`, the
   project's `.claude/settings.json`, then `.claude/settings.local.json`), plus the "never RED"
   guarantee and the doc-sync exclusion (AC-ATE-4's line stays outside the doctor-probe-claims
   count, the same way `permissions-policy` already does).
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

from conftest import REPO_ROOT, load_module, _functional_plugin_root
from test_workflow_export_shape import elided_view

RELEASE_WAVE_JS = os.path.join(REPO_ROOT, "workflows", "release-wave.js")
COMMAND_DECK_SKILL = os.path.join(REPO_ROOT, "skills", "command-deck", "SKILL.md")
AGENT_TEAMS_HOWTO = os.path.join(REPO_ROOT, "docs", "how-to", "agent-teams.md")

doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ================================================================================================ #
# AC-ATE-2 — workflows/release-wave.js: no agent(...) call ever passes `name:`
# ================================================================================================ #

_AGENT_CALL_RE = re.compile(r"\bagent\s*\(")
_NAME_KEY_RE = re.compile(r"(?<![\w$])name\s*:")


def _agent_call_spans(elided):
    """Every `agent(...)` call's (start, end) span — `end` is the index AFTER the call's closing
    `)` — found by a paren-depth scan starting at each call's own open paren, over the ELIDED view,
    so a paren living only inside a string/template/comment can never mis-close a span."""
    spans = []
    n = len(elided)
    for m in _AGENT_CALL_RE.finditer(elided):
        open_paren = m.end() - 1
        depth = 0
        i = open_paren
        close = None
        while i < n:
            c = elided[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
            i += 1
        assert close is not None, (
            f"unbalanced parens scanning an agent(...) call at offset {m.start()} in "
            f"{RELEASE_WAVE_JS}"
        )
        spans.append((m.start(), close + 1))
    return spans


def test_release_wave_has_several_agent_calls():
    """A floor for the negative assertion below — if this ever collects zero call sites, the naming
    assertion would pass vacuously instead of actually checking anything."""
    elided = elided_view(_read(RELEASE_WAVE_JS))
    spans = _agent_call_spans(elided)
    assert len(spans) >= 5, f"expected several agent(...) call sites, found {len(spans)}: {spans}"


def test_release_wave_agent_calls_never_pass_name():
    """AC-ATE-2: no `agent(...)` call carries a `name` key inside its option object — every worker
    this fan-out dispatches stays an ordinary subagent even when the operator's own settings turn
    the native team surface on."""
    elided = elided_view(_read(RELEASE_WAVE_JS))
    spans = _agent_call_spans(elided)
    offenders = [(start, end) for start, end in spans if _NAME_KEY_RE.search(elided[start:end])]
    assert not offenders, (
        f"agent(...) call(s) in {RELEASE_WAVE_JS} carrying a name: key at offsets {offenders}"
    )


def test_release_wave_carries_the_ac_ate_2_rationale_comment():
    """The invariant is explained in place, not just enforced silently (reviewability)."""
    text = _read(RELEASE_WAVE_JS)
    assert "AC-ATE-2" in text
    assert "name" in text.lower()


# ================================================================================================ #
# AC-ATE-4 — scripts/foundry-doctor.py: the `agent-teams` advisory line
# ================================================================================================ #

_ENV_KEY = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def _fixture(tmp_path, monkeypatch, *, home_env=None, project_env=None, local_env=None):
    """Builds a throwaway `(home, project_dir)` pair with each settings file present only when its
    corresponding `*_env` arg is not None (a dict, possibly `{}`, becomes that file's top-level
    `env` block)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(parents=True, exist_ok=True)
    project.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    if home_env is not None:
        _write_json(str(home / ".claude" / "settings.json"), {"env": home_env})
    if project_env is not None:
        _write_json(str(project / ".claude" / "settings.json"), {"env": project_env})
    if local_env is not None:
        _write_json(str(project / ".claude" / "settings.local.json"), {"env": local_env})
    return str(home), str(project)


def test_agent_teams_off_when_no_settings_files_exist(tmp_path, monkeypatch):
    _, project = _fixture(tmp_path, monkeypatch)
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_on_from_project_settings(tmp_path, monkeypatch):
    _, project = _fixture(tmp_path, monkeypatch, project_env={_ENV_KEY: "1"})
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "on (settings env)"


def test_agent_teams_on_from_user_global_settings(tmp_path, monkeypatch):
    """`~/.claude/settings.json` alone is enough — the project carries no settings files at all."""
    _, project = _fixture(tmp_path, monkeypatch, home_env={_ENV_KEY: "1"})
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "on (settings env)"


def test_agent_teams_local_settings_override_project_settings_off_wins(tmp_path, monkeypatch):
    """Ascending precedence, last-one-present wins: local's explicit "0" overrides project's "1"."""
    _, project = _fixture(
        tmp_path, monkeypatch, project_env={_ENV_KEY: "1"}, local_env={_ENV_KEY: "0"},
    )
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_local_settings_override_project_settings_on_wins(tmp_path, monkeypatch):
    """The other direction: local's explicit "1" overrides project's "0"."""
    _, project = _fixture(
        tmp_path, monkeypatch, project_env={_ENV_KEY: "0"}, local_env={_ENV_KEY: "1"},
    )
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "on (settings env)"


def test_agent_teams_project_settings_override_user_global(tmp_path, monkeypatch):
    _, project = _fixture(
        tmp_path, monkeypatch, home_env={_ENV_KEY: "1"}, project_env={_ENV_KEY: "0"},
    )
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_boolean_true_is_not_the_documented_on_value(tmp_path, monkeypatch):
    _, project = _fixture(tmp_path, monkeypatch, project_env={_ENV_KEY: True})
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_malformed_settings_file_is_tolerated_not_crashed(tmp_path, monkeypatch):
    home, project = _fixture(tmp_path, monkeypatch)
    settings_path = os.path.join(project, ".claude", "settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as f:
        f.write("{ not valid json at all")
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_oversized_settings_file_is_skipped(tmp_path, monkeypatch):
    home, project = _fixture(tmp_path, monkeypatch)
    settings_path = os.path.join(project, ".claude", "settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    padding = "x" * (doctor._SETTINGS_MAX_BYTES + 1024)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"env": {_ENV_KEY: "1"}, "_pad": padding}, f)
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_absent_env_key_with_other_env_vars_present_is_off(tmp_path, monkeypatch):
    _, project = _fixture(tmp_path, monkeypatch, project_env={"SOME_OTHER_VAR": "1"})
    ok, detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
    assert ok is True
    assert detail == "off"


def test_agent_teams_never_red_on_a_populated_workspace(tmp_path, monkeypatch):
    """AC-ATE-4: never RED (never `False`), regardless of on/off — on a functioning plugin_root
    `ok` is literally `True`; see the crash-fixture tests below for the ADVISORY path, also never
    RED."""
    for env in (None, {_ENV_KEY: "1"}, {_ENV_KEY: "0"}, {_ENV_KEY: "garbage"}):
        _, project = _fixture(tmp_path / str(env), monkeypatch,
                               project_env=env) if env is not None else _fixture(
            tmp_path / "none", monkeypatch)
        ok, _detail = doctor.check_agent_teams_flag(plugin_root=REPO_ROOT, project_dir=project)
        assert ok is True


# ------------------------------------------------------------------------------------------------ #
# PR #185 review finding 1 (Block): `check_agent_teams_flag` must never RAISE — it is called
# directly from `main()`, not through the crash-proof `_run("<name>", ...)` wrapper the `checks`
# list uses, and it runs BEFORE the `--session-start` fail-open branch. A broken
# `foundry_permission_floor.py` (reached via `_settings_candidate_paths` ->
# `_load_permission_floor_module`) must degrade to an ADVISORY line, never wedge a session.
# ------------------------------------------------------------------------------------------------ #

def _broken_plugin_root(base):
    """A functional plugin tree (`conftest._functional_plugin_root` — every other doctor
    probe's dependency present) whose `scripts/foundry_permission_floor.py` is syntactically
    broken."""
    root = _functional_plugin_root(base)
    broken_path = os.path.join(root, "scripts", "foundry_permission_floor.py")
    with open(broken_path, "w", encoding="utf-8") as f:
        f.write("def _broken(:\n    pass\n")
    return root


def test_agent_teams_survives_a_broken_permission_floor_import(tmp_path, monkeypatch):
    """In-process variant of the crash fixture. `_load_permission_floor_module` does a bare
    `import foundry_permission_floor as pf`, which is subject to ordinary `sys.modules` name
    caching — since this test suite's own module-level `import foundry_permission_floor` (e.g.
    tests/test_floor_drift_classification.py) already populates
    `sys.modules["foundry_permission_floor"]` with the REAL (working) module, a bare
    re-import would silently return that cached module instead of ever touching the broken copy at
    `broken_root`. `monkeypatch.delitem` evicts the cache entry for just this test (auto-restoring
    the real cached module afterward), forcing a genuine re-import from `broken_root`'s `scripts/`."""
    monkeypatch.delitem(sys.modules, "foundry_permission_floor", raising=False)
    # `_load_permission_floor_module` also does `sys.path.insert(0, scripts_dir)` on the way to the
    # failed import; swap in a throwaway COPY of `sys.path` so that in-place mutation is undone by
    # monkeypatch's own teardown (reassigning the attribute back) rather than leaking `broken_root`
    # onto the real `sys.path` for the rest of the test session.
    monkeypatch.setattr(sys, "path", list(sys.path))
    home, project = _fixture(tmp_path, monkeypatch, project_env={_ENV_KEY: "1"})
    broken_root = _broken_plugin_root(tmp_path / "broken-plugin")
    ok, detail = doctor.check_agent_teams_flag(plugin_root=broken_root, project_dir=project)
    assert ok is doctor.ADVISORY
    assert detail.startswith("unknown (probe error"), detail


def test_doctor_cli_session_start_survives_a_broken_permission_floor_import(tmp_path, monkeypatch):
    """End-to-end: `--session-start` still exits 0 and the `agent-teams` line still renders (as
    ADVISORY, not RED, not absent) even when the module this probe's settings-path resolution
    depends on is broken."""
    import subprocess
    import sys

    home, project = _fixture(tmp_path, monkeypatch, project_env={_ENV_KEY: "1"})
    broken_root = _broken_plugin_root(tmp_path / "broken-plugin-cli")
    env = dict(os.environ)
    env["HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = project
    env["CLAUDE_PLUGIN_ROOT"] = broken_root
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py"), "--session-start"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode == 0, f"--session-start must fail open:\n{r.stdout}\n{r.stderr}"
    assert "Traceback" not in r.stderr, f"doctor crashed instead of degrading:\n{r.stderr}"
    combined = r.stdout + r.stderr
    lines = [ln for ln in combined.splitlines() if "] agent-teams:" in ln]
    assert len(lines) == 1, f"expected exactly one agent-teams line:\n{combined}"
    assert "[adv " in lines[0]
    assert "unknown (probe error" in lines[0]


def test_doctor_cli_operator_invoked_survives_a_broken_permission_floor_import(tmp_path, monkeypatch):
    """The operator-invoked (fail-CLOSED) cadence must COMPLETE without a traceback. Other probes
    may legitimately turn the overall run RED for reasons unrelated to `agent-teams` on this
    deliberately-broken fixture tree (e.g. `permission-floor` itself, which has its own documented
    unimportable-module RED path) — only the absence of a crash and the `agent-teams` line's own
    ADVISORY rendering are asserted here."""
    import subprocess
    import sys

    home, project = _fixture(tmp_path, monkeypatch, project_env={_ENV_KEY: "1"})
    broken_root = _broken_plugin_root(tmp_path / "broken-plugin-op")
    env = dict(os.environ)
    env["HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = project
    env["CLAUDE_PLUGIN_ROOT"] = broken_root
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py")],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode in (0, 1), f"doctor did not complete cleanly:\n{r.stdout}\n{r.stderr}"
    assert "Traceback" not in r.stderr, f"doctor crashed instead of degrading:\n{r.stderr}"
    lines = [ln for ln in r.stdout.splitlines() if "] agent-teams:" in ln]
    assert len(lines) == 1, f"expected exactly one agent-teams line:\n{r.stdout}"
    assert "[adv " in lines[0]
    assert "unknown (probe error" in lines[0]


def test_agent_teams_line_is_not_a_run_call_site_literal():
    """The `agent-teams` line is rendered the SAME way `permissions-policy` already is — outside
    the `checks` list's `_run("<name>", ...)` call sites — so it stays outside
    `tests/test_doc_claims.py`'s doctor-probe-claims count (`_derive_doctor_probe_ids`, which
    regex-matches ONLY `_run("...")` literals) and never forces a `docs/QUICKSTART.md` edit, a file
    outside this atom's allowed_paths."""
    with open(os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py"), encoding="utf-8") as f:
        text = f.read()
    assert '_run("agent-teams"' not in text
    assert re.search(r'_render_row\(\s*"agent-teams"', text), (
        "expected an explicit _render_row(\"agent-teams\", ...) call site"
    )


def test_doctor_cli_prints_the_agent_teams_line_and_stays_green(tmp_path, monkeypatch):
    """End-to-end: the real doctor CLI, run against a fixture project dir with the flag on, prints
    the advisory line and does not turn the run RED because of it (other probes may still be
    skip/advisory on a bare fixture tree; only this one line's contribution is asserted)."""
    import subprocess
    import sys

    home, project = _fixture(tmp_path, monkeypatch, project_env={_ENV_KEY: "1"})
    env = dict(os.environ)
    env["HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = project
    env["CLAUDE_PLUGIN_ROOT"] = REPO_ROOT
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py")],
        capture_output=True, text=True, timeout=30, env=env,
    )
    lines = [ln for ln in r.stdout.splitlines() if "] agent-teams:" in ln]
    assert len(lines) == 1, f"expected exactly one agent-teams line:\n{r.stdout}"
    assert "[ok " in lines[0]
    assert "agent-teams: on (settings env)" in lines[0]


# ================================================================================================ #
# AC-ATE-1 — docs/how-to/agent-teams.md quotes the primary-doc facts
# ================================================================================================ #

def test_how_to_doc_exists_and_quotes_the_enable_block():
    text = _read(AGENT_TEAMS_HOWTO)
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" in text
    assert '"1"' in text


def test_how_to_doc_states_the_documented_limitations():
    text = _read(AGENT_TEAMS_HOWTO)
    for pattern in (
        r"`?-p`?\s+sessions never spawn",
        r"One team per session",
        r"do not survive `?/resume`?",
        r"Task status can lag",
        r"Permission prompts bubble up",
    ):
        assert re.search(pattern, text), f"missing documented limitation matching: {pattern!r}"


def test_how_to_doc_states_the_token_cost_stance():
    """The multiplier is stated in WORDS, not digits — a bare digit token in docs/ is a
    count-bearing claim `tests/test_doc_claims.py::test_no_unclassified_count_bearing_claim` would
    demand a derivable source for, and Anthropic's own doc is external, not derivable here."""
    text = _read(AGENT_TEAMS_HOWTO)
    assert "fifteen times" in text and "significantly more" in text
    assert not re.search(r"(?<![A-Za-z0-9_])15(?![A-Za-z0-9_])", text), (
        "token-cost stance must be spelled out in words, not the digit '15'"
    )


def test_how_to_doc_states_when_not_to_use_a_team():
    text = _read(AGENT_TEAMS_HOWTO)
    assert "Sequential work" in text
    assert "Same-file edits" in text


# ================================================================================================ #
# AC-ATE-3 — skills/command-deck/SKILL.md carries the spawn convention
# ================================================================================================ #

def test_command_deck_skill_has_a_teammates_section():
    text = _read(COMMAND_DECK_SKILL)
    assert re.search(r"^## Teammates\s*$", text, re.MULTILINE), (
        "expected a top-level '## Teammates' section in skills/command-deck/SKILL.md"
    )


def test_command_deck_teammates_section_states_the_spawn_convention():
    text = _read(COMMAND_DECK_SKILL)
    m = re.search(r"^## Teammates\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, "no '## Teammates' section body found"
    body = m.group(1)
    assert "builder-<atom>" in body
    assert "reviewer-<atom>" in body
    assert "agents/" in body
    assert "teams-allowed" in body
    assert re.search(r"two or more|>=\s*2|≥2", body), (
        "expected the >=2 disjoint-scope-atoms threshold stated in the section body"
    )
    assert "subagent" in body.lower()
