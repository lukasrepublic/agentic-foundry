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
    out = _run(env).communicate(input="{}", timeout=10)[0]   # clears the stale lock AND renders
    assert out == "LINE"
    assert counter.read_text().count("x") == 1
    assert not lock.exists(), "the render must release its lock"


def test_an_empty_stale_lock_does_not_wedge_the_line_forever(tmp_path):
    """A holder killed between `rm ts` and `rmdir` leaves an EMPTY lock. Read as "a render just
    started", it was fresh forever: every refresh waited, then printed one day-old cached line (another
    session's, at 86%) — /compact never changed the bar. No ts → the lock's own mtime decides."""
    env, counter = _setup(tmp_path)
    env["FOUNDRY_STATUSLINE_TTL"] = "0"
    key = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in env["CLAUDE_PROJECT_DIR"])
    lock = Path(env["XDG_CACHE_HOME"]) / "foundry-statusline" / f"{key}.lock"
    lock.mkdir(parents=True)
    old = time.time() - 3600
    os.utime(lock, (old, old))
    out = _run(env).communicate(input="{}", timeout=10)[0]
    assert out == "LINE"
    assert counter.read_text().count("x") == 1
    assert not lock.exists(), "the render must release its lock"


def test_a_young_empty_lock_is_still_respected(tmp_path):
    """The other half: an empty lock made a moment ago IS a render that just started — the refresh
    must not steal it (no second render)."""
    env, counter = _setup(tmp_path)
    key = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in env["CLAUDE_PROJECT_DIR"])
    lock = Path(env["XDG_CACHE_HOME"]) / "foundry-statusline" / f"{key}.lock"
    lock.mkdir(parents=True)
    _run(env).communicate(input="{}", timeout=10)
    assert not counter.exists(), "a fresh empty lock must not be treated as stale"


def _payload(sid="s1", cwd="/w", rem=None):
    cw = {} if rem is None else {"remaining_percentage": rem}
    return json.dumps({"session_id": sid, "workspace": {"current_dir": cwd}, "context_window": cw})


def test_a_cached_line_is_never_served_for_a_different_context(tmp_path):
    """v1.18.3: Claude Code re-runs the status line only on events. The first render of a session has
    no context figure; serving that cached line after the first reply (inside the TTL) left the `tok`
    bar absent until the NEXT event — every new session showed no bar. A new context figure, session
    or directory must re-render even inside the TTL; the same one must still hit the cache."""
    env, counter = _setup(tmp_path)
    assert _run(env).communicate(input=_payload(), timeout=10)[0] == "LINE"
    assert _run(env).communicate(input=_payload(), timeout=10)[0] == "LINE"
    assert counter.read_text().count("x") == 1, "the same context inside the TTL must hit the cache"
    _run(env).communicate(input=_payload(rem=95), timeout=10)
    assert counter.read_text().count("x") == 2, "a new context figure must re-render"
    _run(env).communicate(input=_payload(rem=95, sid="s2"), timeout=10)
    assert counter.read_text().count("x") == 3, "another session must re-render"
    _run(env).communicate(input=_payload(rem=95, sid="s2", cwd="/w/wt"), timeout=10)
    assert counter.read_text().count("x") == 4, "another directory must re-render"


def test_a_refresh_during_a_render_for_an_older_context_waits_for_a_current_line(tmp_path):
    """v1.18.3: a refresh that finds a render in flight for a DIFFERENT context waits for it and renders
    its own, instead of printing the line it knows is stale and exiting."""
    env, counter = _setup(tmp_path)
    first = _run(env)
    first.stdin.write(_payload())
    first.stdin.close()
    time.sleep(0.3)  # the first render now holds the lock (the test renderer sleeps 1 s)
    out = _run(env).communicate(input=_payload(rem=95), timeout=15)[0]
    first.wait(timeout=15)
    assert out == "LINE"
    assert counter.read_text().count("x") == 2, "the newer context must get its own render"


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


def test_a_stale_lock_that_cannot_be_removed_never_spins(tmp_path):
    """v1.18.3 security review R1: a stale lock holding a stray file (Finder's .DS_Store) cannot be
    rmdir'ed; the refresh must still exit within the bounded wait, never loop without sleeping."""
    env, counter = _setup(tmp_path)
    key = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in env["CLAUDE_PROJECT_DIR"])
    lock = Path(env["XDG_CACHE_HOME"]) / "foundry-statusline" / f"{key}.lock"
    lock.mkdir(parents=True)
    (lock / "ts").write_text("0\n")
    (lock / ".DS_Store").write_text("x")
    start = time.monotonic()
    _run(env).communicate(input=_payload(rem=50), timeout=30)
    assert time.monotonic() - start < 10, "a wedged lock must not hold the refresh"
