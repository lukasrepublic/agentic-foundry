"""v1.18.2: the status-line wrapper is single-flight and cached. Claude Code re-runs the status line on
every update; a slow renderer (git + python, seconds on WSL) overlapped itself until an adopter's
machine ran out of memory and Claude Code crashed (44,616 spawns in 21 minutes). A burst of refreshes
must run the renderer ONCE, and a refresh inside the TTL must not run it at all."""
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "cli" / "templates" / "foundry-statusline.sh"


def _setup(tmp_path):
    plugin = tmp_path / "cache" / "agentic-foundry" / "foundry" / "9.9.9"
    (plugin / "scripts").mkdir(parents=True)
    counter = tmp_path / "renders"
    renderer = plugin / "scripts" / "foundry-statusline.sh"
    renderer.write_text(
        "#!/usr/bin/env bash\n# foundry-statusline.sh — test renderer\n"
        f"echo x >> '{counter}'\nsleep 1\nprintf 'LINE'\n"
    )
    cfg = tmp_path / "cfg"
    (cfg / "plugins").mkdir(parents=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"foundry@agentic-foundry": [{"scope": "user", "installPath": str(plugin), "version": "9.9.9"}]}}))
    proj = tmp_path / "proj"
    proj.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_PROJECT_DIR=str(proj), TMPDIR=str(tmpdir),
               XDG_CACHE_HOME=str(tmp_path / "xdg"), FOUNDRY_STATUSLINE_TTL="30")
    return env, counter


def _run(env):
    return subprocess.Popen(["/bin/bash", str(WRAPPER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, text=True)


def test_a_burst_of_refreshes_renders_once_and_the_ttl_serves_the_cache(tmp_path):
    env, counter = _setup(tmp_path)
    procs = [_run(env) for _ in range(20)]
    outs = [p.communicate(input="{}", timeout=30)[0] for p in procs]
    assert counter.read_text().count("x") == 1, "the renderer ran more than once for one burst"
    assert "LINE" in outs  # the one that rendered printed the line; the rest exited at once
    start = time.monotonic()
    p = _run(env)
    out = p.communicate(input="{}", timeout=10)[0]
    assert out == "LINE", "a refresh inside the TTL must print the cached line"
    assert time.monotonic() - start < 0.9, "a cache hit must not wait for the renderer"
    assert counter.read_text().count("x") == 1


def test_a_stale_lock_does_not_wedge_the_line_forever(tmp_path):
    env, counter = _setup(tmp_path)
    env["FOUNDRY_STATUSLINE_TTL"] = "0"
    key = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in env["CLAUDE_PROJECT_DIR"])
    lock = Path(env["XDG_CACHE_HOME"]) / "foundry-statusline" / f"{key}.lock"
    lock.mkdir(parents=True)
    (lock / "ts").write_text("0\n")  # a crashed render from long ago
    _run(env).communicate(input="{}", timeout=10)   # clears the stale lock
    out = _run(env).communicate(input="{}", timeout=10)[0]
    assert out == "LINE"
    assert counter.read_text().count("x") == 1


def test_a_planted_cache_dir_is_never_read_or_written(tmp_path):
    """Security review (Block): a cache dir that is a symlink (or not ours) is not used at all — the
    wrapper renders directly; a file behind a planted link is neither shown nor clobbered."""
    env, counter = _setup(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    target = tmp_path / "attacker-dir"
    target.mkdir()
    key = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in env["CLAUDE_PROJECT_DIR"])
    (target / f"{key}.out").symlink_to(secret)
    (target / f"{key}.out.ts").write_text("99999999999\n")
    xdg = Path(env["XDG_CACHE_HOME"])
    xdg.mkdir(parents=True)
    (xdg / "foundry-statusline").symlink_to(target)
    out = _run(env).communicate(input="{}", timeout=10)[0]
    assert "TOP-SECRET" not in out and out == "LINE"
    assert secret.read_text() == "TOP-SECRET"
