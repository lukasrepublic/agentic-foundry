"""tests/test_git_identity.py — converted from scripts/foundry_checks/commit-identity-isolation.py.

Ports the real behavioral assertions over the native per-project commit-identity wiring check:
git `includeIf` -> a per-account include file -> the project's effective `user.email`. Hermetic:
a temp XDG_CONFIG_HOME (never the operator's real ~/.config/git) + temp git repos.
"""
from __future__ import annotations

import os
import subprocess

import support_git_identity as cid


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def test_dormant_noop_when_isolation_not_adopted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ok, detail = cid.check(root=str(project))
    assert ok is True and "not adopted" in detail


def test_malformed_slug_is_red(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "gh-identity").write_text("../escape", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ok, detail = cid.check(root=str(project))
    assert ok is False and "malformed" in detail.lower()


def test_missing_include_file_is_red(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "gh-identity").write_text("myaccount\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ok, detail = cid.check(root=str(project))
    assert ok is False and "ABSENT" in detail


def test_matching_identity_resolves_green(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    git_cfg_dir = xdg / "git"
    git_cfg_dir.mkdir(parents=True)
    inc = git_cfg_dir / "identity-myaccount"
    inc.write_text("[user]\n\temail = myaccount@example.com\n", encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir()
    _git("init", "-q", str(project))
    _git("-C", str(project), "config", "includeIf.gitdir:%s.path" % (str(project) + "/"), str(inc))
    _git("-C", str(project), "config", "user.email", "myaccount@example.com")
    (project / ".claude").mkdir()
    (project / ".claude" / "gh-identity").write_text("myaccount\n", encoding="utf-8")

    ok, detail = cid.check(root=str(project))
    assert ok is True, detail


def test_diverging_effective_email_is_red(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    git_cfg_dir = xdg / "git"
    git_cfg_dir.mkdir(parents=True)
    inc = git_cfg_dir / "identity-myaccount"
    inc.write_text("[user]\n\temail = myaccount@example.com\n", encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir()
    _git("init", "-q", str(project))
    # a stray --local override shadows the declared identity.
    _git("-C", str(project), "config", "user.email", "stray-override@example.com")
    (project / ".claude").mkdir()
    (project / ".claude" / "gh-identity").write_text("myaccount\n", encoding="utf-8")

    ok, detail = cid.check(root=str(project))
    assert ok is False and "!=" in detail
