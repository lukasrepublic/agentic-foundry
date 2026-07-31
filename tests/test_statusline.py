"""tests/test_statusline.py — converted from scripts/foundry_checks/{isolation-statusline,
statusline-wiring-live, init-statusline-wrapper, session-per-mandate}.py.

Ports the real behavioral assertions those four drop-in selftests drove — over the REAL shipped
`scripts/foundry-statusline.sh` renderer, `scripts/foundry-statusline-wrapper.sh` (the version-
agnostic self-resolving wrapper `/foundry:init` installs), and the pure `evaluate()`/`check()`
functions the statusline-wiring-live check exposed. CLI/doctor scaffolding is dropped.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from conftest import REPO_ROOT
import support_statusline_wiring as statusline_wiring

STATUSLINE = os.path.join(REPO_ROOT, "scripts", "foundry-statusline.sh")
WRAPPER = os.path.join(REPO_ROOT, "scripts", "foundry-statusline-wrapper.sh")


def _run(script, payload, *, env_extra=None, timeout=15):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(["bash", script], input=payload, capture_output=True, text=True,
                           env=env, timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return -1, ""


def _payload(cwd, remaining_percentage=None):
    obj = {"workspace": {"current_dir": cwd}}
    if remaining_percentage is not None:
        obj["context_window"] = {"remaining_percentage": remaining_percentage}
    return json.dumps(obj)


# ==================================================== isolation-statusline.py ==== #

class TestStatuslineRenderer:
    def test_renders_over_real_repo_without_crashing(self):
        rc, out = _run(STATUSLINE, _payload(REPO_ROOT, 90))
        assert rc == 0
        assert "ctx" in out  # the pressure-bar segment renders.

    def test_fails_open_on_malformed_json(self):
        rc, out = _run(STATUSLINE, "not json {{")
        assert rc == 0  # fail-open is the floor — never breaks a prompt.

    def test_fails_open_on_empty_stdin(self):
        rc, out = _run(STATUSLINE, "")
        assert rc == 0


# ==================================================== session-per-mandate.py ==== #

NUDGE_TEXT = "context snapshot"  # substring of the /foundry:context snapshot handoff nudge.


class TestSessionPerMandateEscalation:
    def _write_threshold(self, proj_dir, content):
        fdir = os.path.join(proj_dir, ".foundry")
        os.makedirs(fdir, exist_ok=True)
        path = os.path.join(fdir, "context-threshold")
        if content is None:
            if os.path.exists(path):
                os.remove(path)
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_over_budget_fires_nudge(self, tmp_path):
        proj = str(tmp_path)
        rc, out = _run(STATUSLINE, _payload("/tmp", 30), env_extra={"CLAUDE_PROJECT_DIR": proj})
        assert rc == 0
        assert NUDGE_TEXT in out.lower() or "snapshot" in out.lower()

    def test_under_budget_no_nudge(self, tmp_path):
        proj = str(tmp_path)
        rc, out = _run(STATUSLINE, _payload("/tmp", 90), env_extra={"CLAUDE_PROJECT_DIR": proj})
        assert rc == 0
        assert "snapshot" not in out.lower()

    def test_invalid_threshold_file_falls_back_to_default(self, tmp_path):
        proj = str(tmp_path)
        self._write_threshold(proj, "not-a-number")
        # remaining=30 -> used=70 >= the default 65 fallback -> still escalates.
        rc, out = _run(STATUSLINE, _payload("/tmp", 30), env_extra={"CLAUDE_PROJECT_DIR": proj})
        assert rc == 0
        assert "snapshot" in out.lower()

    def test_absent_remaining_percentage_degrades_cleanly(self, tmp_path):
        proj = str(tmp_path)
        rc, out = _run(STATUSLINE, _payload("/tmp", None), env_extra={"CLAUDE_PROJECT_DIR": proj})
        assert rc == 0
        assert "ctx" not in out and "snapshot" not in out.lower()


# ==================================================== init-statusline-wrapper.py ==== #

class TestStatuslineWrapperVersionResolution:
    def _seed_cache(self, home, versions):
        for v in versions:
            d = os.path.join(home, ".claude", "plugins", "cache", "mkt", "foundry", v, "scripts")
            os.makedirs(d, exist_ok=True)
            script = os.path.join(d, "foundry-statusline.sh")
            with open(script, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\necho RENDERED-%s\n" % v)
            os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)

    def test_selects_highest_version_by_path_segment_not_lexical_sort(self, tmp_path, monkeypatch):
        home = str(tmp_path)
        self._seed_cache(home, ["0.9.0", "0.11.1", "0.2.0"])
        monkeypatch.setenv("HOME", home)
        proc = subprocess.run(["bash", WRAPPER], input="{}", capture_output=True, text=True,
                              env=dict(os.environ, HOME=home))
        assert proc.returncode == 0
        assert "RENDERED-0.11.1" in proc.stdout

    def test_fails_open_with_no_cache_match(self, tmp_path, monkeypatch):
        home = str(tmp_path)
        monkeypatch.setenv("HOME", home)
        proc = subprocess.run(["bash", WRAPPER], input="{}", capture_output=True, text=True,
                              env=dict(os.environ, HOME=home))
        assert proc.returncode == 0
        assert proc.stdout == ""


# ==================================================== statusline-wiring-live.py ==== #

class TestStatuslineWiringLive:
    def _set_settings(self, ws, cmd):
        data = {"statusLine": {"type": "command", "command": cmd}} if cmd else {}
        with open(os.path.join(ws, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _seed(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(os.path.join(ws, ".claude", "hooks"))
        cache = str(tmp_path / "cache")
        for v in ("0.9.0", "0.11.1"):
            os.makedirs(os.path.join(cache, "mkt", "foundry", v, "scripts"))
        wrapper = os.path.join(ws, ".claude", "hooks", "foundry-statusline.sh")
        with open(wrapper, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IXUSR)
        return ws, cache

    def test_no_statusline_configured_is_not_applicable(self, tmp_path):
        ws, cache = self._seed(tmp_path)
        self._set_settings(ws, None)
        status, detail = statusline_wiring.evaluate(ws, cache)
        assert status is None

    def test_resolving_wrapper_is_green(self, tmp_path):
        ws, cache = self._seed(tmp_path)
        self._set_settings(ws, '"$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-statusline.sh"')
        status, detail = statusline_wiring.evaluate(ws, cache)
        assert status is True

    def test_nonexistent_script_is_red(self, tmp_path):
        ws, cache = self._seed(tmp_path)
        self._set_settings(ws, '"$CLAUDE_PROJECT_DIR/.claude/hooks/missing.sh"')
        status, detail = statusline_wiring.evaluate(ws, cache)
        assert status is False and "does not resolve" in detail

    def test_stale_version_keyed_path_is_flagged_red(self, tmp_path):
        ws, cache = self._seed(tmp_path)
        stale = os.path.join(cache, "mkt", "foundry", "0.9.0", "scripts", "r.sh")
        self._set_settings(ws, f'"{stale}"')
        status, detail = statusline_wiring.evaluate(ws, cache)
        assert status is False and "STALE WIRING" in detail and "0.11.1" in detail

    def test_highest_version_pin_resolves_green_with_note(self, tmp_path):
        ws, cache = self._seed(tmp_path)
        cur = os.path.join(cache, "mkt", "foundry", "0.11.1", "scripts", "r.sh")
        with open(cur, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(cur, os.stat(cur).st_mode | stat.S_IXUSR)
        self._set_settings(ws, f'"{cur}"')
        status, detail = statusline_wiring.evaluate(ws, cache)
        assert status is True and "will stale" in detail
