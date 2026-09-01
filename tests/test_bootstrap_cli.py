"""tests/test_bootstrap_cli.py — the pytest shim for `create-agentic-workspace`
(feat-foundry-bootstrap-cli).

Drives the package's own `node --test cli/test/**/*.test.mjs` suite as a subprocess (so the
shipped `python3 -m pytest tests/ -q` CI step runs it with no workflow edit — AC-BCL-10), plus a
battery of subprocess/integration-level tests exercising the CLI binary directly and structural
checks over cli/package.json, cli/src/**, cli/templates/** and the docs it touches. Every test
name below is the exact `-k` filter the acceptance contract's checkpoints use.

Fail-closed throughout: no skip/xfail marker, no warn-only branch. Every fixture is a throwaway
tmp_path (or an explicit tmp HOME), never the real tree or the real git config.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_DIR = REPO_ROOT / "cli"
NODE = shutil.which("node") or "node"
REQUIRED_NODE_MAJOR = 22


# ── shared helpers ──────────────────────────────────────────────────────────────────────────────


def _node_major():
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    m = re.match(r"v(\d+)", out.stdout.strip())
    return int(m.group(1)) if m else None


def run_cli(args, home, cwd=None, input_text="", timeout=30, env_extra=None, path_prepend=None):
    """Run the CLI binary as a subprocess against a throwaway HOME. Never touches the real HOME
    or the real git config."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    if path_prepend:
        env["PATH"] = f"{path_prepend}{os.pathsep}{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [NODE, str(CLI_DIR / "bin" / "create-agentic-workspace.mjs"), *args],
        cwd=str(cwd) if cwd else None,
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return proc


def run_node_tests(pattern=None, timeout=180, cwd_override=None):
    cmd = [NODE, "--test"]
    if pattern:
        cmd += ["--test-name-pattern", pattern]
    cmd += ["test/**/*.test.mjs"]
    proc = subprocess.run(
        cmd, cwd=str(cwd_override or CLI_DIR), capture_output=True, text=True, timeout=timeout
    )
    return proc


def _node_eval(js_source, cwd=None, env_extra=None):
    """Run an ESM one-liner against the repo's own node, returning parsed JSON stdout."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", js_source],
        cwd=str(cwd or CLI_DIR),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node eval failed (rc={proc.returncode}):\n{proc.stderr}")
    return proc.stdout


IMPORT_RE = re.compile(r'''^\s*(?:import|export)\s+(?:[^'";]*?from\s+)?['"]([^'"]+)['"]''', re.M)
DYNAMIC_IMPORT_RE = re.compile(r"""import\(\s*([^)]*?)\s*\)""")
BANNED_NETWORK_MODULES = {
    "node:http",
    "node:https",
    "node:net",
    "node:tls",
    "node:dgram",
    "node:dns",
    "node:dns/promises",
    "node:worker_threads",
}


def _iter_mjs_files(root=CLI_DIR):
    return sorted(root.rglob("*.mjs"))


def _function_region(text, name):
    """Return the source from `function <name>(` to the next top-level declaration, or None.

    `_extract_function_body` below cannot be used on a function whose parameters are DESTRUCTURED:
    it takes the first `{` after the name, which for `f(args, { a, b })` is the parameter pattern
    rather than the body, so it returns `{ a, b }` and any assertion over it silently inspects a
    parameter list while appearing to inspect a function. That is the precise failure mode this
    file's own comments warn about ("a check that looks strict and inspects less than it claims"),
    so destructured-parameter functions get a region slice instead of a brace scan. Coarser than a
    balanced-brace body — it can run past the closing brace — which is safe for ABSENCE-of-guard
    assertions (a false green needs the guard to be present, and if it is present the property
    holds) but NOT for "makes no X call" claims; use it only for the former."""
    start = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", text)
    if start is None:
        return None
    rest = text[start.end():]
    nxt = re.search(r"\n(?:export\s+)?(?:function|const|class)\s", rest)
    return rest[: nxt.start()] if nxt else rest


def _extract_function_body(text, name):
    """Return the body of `function <name>(...) { ... }` by BALANCED-BRACE scan, or None if the
    function is not present. A brace scan rather than a regex because a regex over raw text cannot
    find the matching close brace, and a body that stops early would silently shrink what an
    assertion covers — the failure mode being guarded against here is exactly a check that looks
    strict and inspects less than it claims."""
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", text)
    if match is None:
        return None
    open_idx = text.index("{", match.end())
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx : i + 1]
    raise AssertionError(f"unbalanced braces scanning {name}")


def _collect_import_violations(root=CLI_DIR, banned=None):
    """Returns (violations, all_specs) for every static/dynamic import under `root`."""
    violations = []
    all_specs = []
    for f in _iter_mjs_files(root):
        text = f.read_text(encoding="utf-8")
        for m in IMPORT_RE.finditer(text):
            spec = m.group(1)
            all_specs.append((f, spec))
            ok = spec.startswith("node:") or spec.startswith("./") or spec.startswith("../")
            if not ok:
                violations.append(f"{f}: static import/export {spec!r} is not a node: builtin or a relative path")
            if banned and spec in banned:
                violations.append(f"{f}: banned network module import {spec!r}")
        for m in DYNAMIC_IMPORT_RE.finditer(text):
            arg = m.group(1).strip()
            if not arg:
                continue
            is_string_literal = (arg.startswith("'") and arg.endswith("'")) or (
                arg.startswith('"') and arg.endswith('"')
            )
            if not is_string_literal:
                violations.append(f"{f}: dynamic import() with a non-string-literal specifier: {arg!r}")
                continue
            spec = arg[1:-1]
            all_specs.append((f, spec))
            ok = spec.startswith("node:") or spec.startswith("./") or spec.startswith("../")
            if not ok:
                violations.append(f"{f}: dynamic import {spec!r} is not a node: builtin or a relative path")
            if banned and spec in banned:
                violations.append(f"{f}: banned network module dynamic import {spec!r}")
    return violations, all_specs


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_tree(root: Path):
    out = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(root))] = (sha256_of(p), p.stat().st_ino, p.stat().st_mtime_ns)
    return out


def load_pkg():
    return json.loads((CLI_DIR / "package.json").read_text(encoding="utf-8"))


def load_map():
    return json.loads((CLI_DIR / "permission-floor.json").read_text(encoding="utf-8"))


DECLARED_PATH_SET = {
    "CLAUDE.md",
    ".gitignore",
    ".claude/settings.json",
    ".claude/foundry-project.json",
    "specs/features/README.md",
    "specs/lifecycle/README.md",
    ".foundry/README.md",
}

FORBIDDEN_SETTINGS_KEYS = {
    "statusLine",
    "hooks",
    "sandbox",
    "apiKeyHelper",
    "env",
    "mcpServers",
    "additionalDirectories",
}


# ── reusable assertion helpers (shared between the primary checks and the negative controls, so a
#    control exercises the SAME logic the primary check runs — never a second, tautological copy) ─


def _assert_settings_bijection(settings, map_data):
    for tier in ("allow", "ask", "deny"):
        expected = {e["rule"] for e in map_data["entries"] if e["tier"] == tier}
        actual = set(settings["permissions"][tier])
        assert actual == expected, (tier, actual ^ expected)


def _assert_marketplace_pinned_literal(entry, marketplace_repo):
    """SUPERSEDED shape (feat-foundry-installer-unpinning, AC-IUP-3). Was AC-BCL-4(b), contract
    v1.2 (PR #61 security review Block 1): `source.ref` used to be asserted as the pin. It is
    REMOVED now — `source`'s key set is closed to exactly {source, repo}, restoring the shape
    feat-foundry-bootstrap-cli.md:184 (AC-BCL-4(b)) itself already declared; the `ref` the PR #61
    Block added was a post-authorization addition. See the supersession comment carried verbatim
    at cli/src/permissionFloor.mjs (the block above `buildSettings`'s composed literal) for the
    full grounds. Asserted as a whole-object equality so a REINTRODUCED ref (or any other key) is
    caught, not just a wrong one."""
    assert entry == {
        "source": {
            "source": "github",
            "repo": marketplace_repo,
        },
        "autoUpdate": False,
    }


def _assert_settings_key_set_closed(settings):
    top_keys = {k for k in settings if not k.startswith("//")}
    assert top_keys == {"permissions", "extraKnownMarketplaces", "enabledPlugins"}
    assert set(settings["permissions"].keys()) == {"allow", "ask", "deny"}
    found = _walk_forbidden_keys(settings, FORBIDDEN_SETTINGS_KEYS)
    assert not found, found


def _assert_covers_row(a, b, expected):
    js = """
import { covers } from './src/permissionFloor.mjs';
console.log(JSON.stringify(covers(%s, %s)));
""" % (json.dumps(a), json.dumps(b))
    result = json.loads(_node_eval(js))
    assert result == expected, (a, b, result, expected)


def _assert_flag_bijection(table_flags, accepted_flags, help_flags):
    assert set(table_flags) == set(accepted_flags), (set(table_flags) ^ set(accepted_flags))
    assert set(table_flags) == set(help_flags), (set(table_flags) ^ set(help_flags))


def _assert_preview_covers_declared_set(preview_text, declared_set):
    missing = [rel for rel in declared_set if f"] {rel}" not in preview_text]
    assert not missing, f"preview is missing rows for: {missing}"


# ── AC-BCL-1 ─────────────────────────────────────────────────────────────────────────────────────


def test_package_manifest_has_no_lifecycle_scripts_and_no_deps():
    pkg = load_pkg()
    assert pkg["name"] == "create-agentic-workspace"
    assert pkg["type"] == "module"
    assert pkg["bin"] == {"create-agentic-workspace": "bin/create-agentic-workspace.mjs"}
    assert pkg["engines"]["node"] == ">=22.0.0"
    assert isinstance(pkg.get("files"), list) and len(pkg["files"]) > 0
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        assert key not in pkg or pkg[key] == {}, f"{key} must be absent or empty"
    scripts = pkg.get("scripts", {})
    assert set(scripts.keys()) <= {"test"}, f"scripts keys must be a subset of {{'test'}}: {scripts.keys()}"
    for forbidden in ("preinstall", "install", "postinstall", "prepare", "prepack", "prepublish"):
        assert forbidden not in scripts


def test_every_import_is_a_node_builtin_or_relative():
    violations, _ = _collect_import_violations()
    assert not violations, "\n".join(violations)


def test_the_plugin_pin_block_matches_the_marketplace_manifest():
    pkg = load_pkg()
    marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    pins = pkg["foundry"]
    assert pins["marketplace_name"] == marketplace["name"]
    assert pins["marketplace_repo"] == marketplace["plugins"][0]["source"]["repo"]
    assert pins["plugin_name"] == marketplace["plugins"][0]["name"]
    assert pins["plugin_version"] == marketplace["plugins"][0]["version"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", pins["pins_researched"]), pins["pins_researched"]

    # The TARBALL's own version must move whenever the pin it embeds does (PR #68 security review,
    # Risk 4). npm versions are IMMUTABLE: if `create-agentic-workspace@X` is ever published
    # carrying plugin_version 1.1.0, that same X can never be republished with 1.2.0 — and since the
    # documented install is an UNPINNED `npx create-agentic-workspace` (the `latest` dist-tag), every
    # adopter would keep scaffolding workspaces whose emitted marketplace ref points at the previous
    # release. That is precisely the compensating control the bootstrap-cli spec leans on to justify
    # the unpinned npx line ("the tarball's own version is pinned by foundry.plugin_version"), and it
    # only holds if a NEW tarball actually ships when the pin moves.
    #
    # Asserted as a MAPPING keyed by plugin_version, so a bump to one without the other is caught in
    # the release diff rather than after publish, when it is unfixable. Add a row per release cut.
    TARBALL_VERSION_BY_PLUGIN_PIN = {
        "1.4.2": "0.4.2",
        "1.1.0": "0.1.0",
        "1.2.0": "0.2.0",
        "1.2.1": "0.2.1",
        # v1.2.2 republishes the CLI on purpose: 0.2.1 shipped from v1.2.1 on 2026-08-05 and
        # PR #75 (step 0 left the reader in a non-git directory) merged the day after, so the
        # fix existed on main and reached nobody. npm-publish skips a version already on the
        # registry, which is why the tarball bump is load-bearing rather than cosmetic.
        "1.2.2": "0.2.2",
        # v1.3.0 republishes the CLI because the CLI itself is what changed: the wizard's prompts
        # now carry descriptions, per-choice lines, and a default rendered alongside the choices
        # (ER #88). A pin bump without a tarball bump would leave `npx create-agentic-workspace`
        # scaffolding with the old, unexplained prompts.
        "1.3.0": "0.3.0",
        # v1.3.1 carries no CLI change — it releases two plugin-side fixes (contract_sha256 freeze
        # asymmetry, and a publish plan that could not run on a protected main). The tarball still
        # bumps, because the pin it EMBEDS moved: an unpinned `npx create-agentic-workspace` would
        # otherwise keep scaffolding workspaces whose marketplace ref names 1.3.0.
        "1.3.1": "0.3.1",
        # v1.4.0 DOES carry a CLI change — the --reconcile-floor flag and the floorReconcile
        # module — so the tarball bumps for its own sake as well as for the pin it embeds.
        "1.4.0": "0.4.0",
        # v1.4.1 carries CLI changes of its own — the consent-ordering fix (ER #95) and the
        # reconcile-path trust hand-off — so the tarball bumps for its own sake as well as for
        # the pin it embeds.
        "1.4.1": "0.4.1",
        # v1.5.0 DOES carry a CLI change of its own, on top of the pin it embeds: the bundled
        # `cli/permission-floor.json` gains one `allow` rule (the read-only command-deck module), so a
        # newly scaffolded or reconciled workspace receives a different floor than 0.4.2 wrote.
        "1.5.0": "0.5.0",
        # v1.6.0 DOES carry a CLI change of its own, on top of the pin it embeds: the bundled
        # `cli/permission-floor.json` loses the retired session-context posture module's
        # `not_invoked` entry when that dependency is severed and the module is deleted, so a newly
        # scaffolded or reconciled workspace receives a different floor than 0.5.0 wrote.
        # (Named by description, not by identifier: this file is inside the zero-reference gate's
        # swept surface, and spelling the retired module's name here would take that gate red.)
        "1.6.0": "0.6.0",
        # v1.7.0 carries a REAL CLI change, not merely the pin it embeds: the composed
        # extraKnownMarketplaces entry drops `source.ref` entirely (feat-foundry-installer-unpinning,
        # restoring the shape AC-BCL-4(b) declares), and floorReconcile's pinned-state predicate now
        # accepts a tagless entry naming this marketplace's own github source. A workspace scaffolded
        # or reconciled by 0.7.0 therefore receives a different settings entry than 0.6.0 wrote, so
        # the tarball takes a minor bump rather than echoing the pin.
        "1.7.0": "0.7.0",
        # v1.8.0 carries NO functional CLI change — the release removes the retired
        # session-context framework's name from the repo, which does not touch cli/src/. The tarball
        # still takes a minor bump because cli/package.json EMBEDS plugin_version: the pin moved to
        # 1.8.0, so the published tarball's bytes differ and a new one must ship, or npm can never
        # deliver this pin. Echoing the previous tarball version would strand 1.8.0 unreachable.
        "1.8.0": "0.8.0",
    }
    expected_tarball = TARBALL_VERSION_BY_PLUGIN_PIN.get(pins["plugin_version"])
    assert expected_tarball is not None, (
        f"plugin_version {pins['plugin_version']!r} has no recorded cli/package.json version. A "
        "release cut that moves the pin must also bump the tarball version and add its row here."
    )
    assert pkg["version"] == expected_tarball, (
        f"cli/package.json version is {pkg['version']!r} but plugin_version {pins['plugin_version']!r} "
        f"expects {expected_tarball!r} — bump the tarball, or npm can never ship this pin"
    )


# ── AC-BCL-2 ─────────────────────────────────────────────────────────────────────────────────────


def test_prompts_and_flags_are_a_bijection():
    js = """
import { QUESTION_TABLE, tableFlags } from './src/questions.mjs';
import { renderHelp, parseArgv } from './src/argv.mjs';
const flags = tableFlags(QUESTION_TABLE);
const help = renderHelp(QUESTION_TABLE);
const helpFlags = [...help.matchAll(/^\\s+--([a-z-]+)/gm)].map(m => m[1]);
let unknownRefused = false;
try { parseArgv(['--totally-bogus-flag'], QUESTION_TABLE); } catch (e) { unknownRefused = e.name === 'RefusalError'; }
console.log(JSON.stringify({flags, helpFlags, unknownRefused}));
"""
    data = json.loads(_node_eval(js))
    _assert_flag_bijection(data["flags"], data["flags"], data["helpFlags"])
    assert data["unknownRefused"] is True


def test_yes_mode_completes_without_a_prompt(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    proc = run_cli(["--dir", str(target), "--yes"], home=home, input_text="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "CLAUDE.md").exists()
    # no interactive prompt text should have been emitted
    assert "?" not in proc.stdout or "trust dialog" in proc.stdout  # sanity: not a hang artifact


def test_yes_mode_refuses_naming_the_missing_flag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proc = run_cli(["--yes"], home=home)
    assert proc.returncode == 1
    assert "--dir" in proc.stdout

    target = tmp_path / "ws2"
    proc2 = run_cli(["--dir", str(target), "--yes", "--stage-mode", "bogus"], home=home)
    assert proc2.returncode == 1
    assert "--stage-mode" in proc2.stdout
    assert "lean" in proc2.stdout and "scale" in proc2.stdout
    assert not target.exists()


# ── AC-BCL-3 ─────────────────────────────────────────────────────────────────────────────────────


def test_preview_lists_every_file_and_every_capability(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    proc = run_cli(["--dir", str(target), "--yes", "--dry-run"], home=home)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_preview_covers_declared_set(proc.stdout, DECLARED_PATH_SET)
    assert "[allow]" in proc.stdout and "[ask]" in proc.stdout and "[deny]" in proc.stdout
    map_data = load_map()
    sample = map_data["entries"][0]
    assert sample["rule"] in proc.stdout
    assert sample["rationale"] in proc.stdout


def test_dry_run_writes_nothing_and_spawns_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    # populate a REAL scaffold first, then drift one file, so the dry-run preview below is proved
    # over a POPULATED target carrying both a `create` and a `drifted` row.
    proc1 = run_cli(["--dir", str(target), "--yes"], home=home)
    assert proc1.returncode == 0
    (target / "CLAUDE.md").write_text((target / "CLAUDE.md").read_text() + "\nEDIT\n")
    (target / ".foundry" / "README.md").unlink()  # make it absent -> a `create` row on the re-run

    before = snapshot_tree(target)
    home_before = snapshot_tree(home)

    proc2 = run_cli(["--dir", str(target), "--yes", "--dry-run", "--existing"], home=home)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert "[drifted] CLAUDE.md" in proc2.stdout
    assert "[create] .foundry/README.md" in proc2.stdout

    after = snapshot_tree(target)
    assert before == after, "dry-run must not change any byte/inode/mtime under the target root"
    home_after = snapshot_tree(home)
    assert home_before == home_after, "dry-run must make no machine-scope write"


# ── AC-BCL-4 ─────────────────────────────────────────────────────────────────────────────────────


def _scaffold(tmp_path, extra_args=None, home_name="home", target_name="ws"):
    home = tmp_path / home_name
    home.mkdir(exist_ok=True)
    target = tmp_path / target_name
    args = ["--dir", str(target), "--yes"]
    if extra_args:
        args += extra_args
    proc = run_cli(args, home=home)
    return proc, home, target


def test_settings_permissions_are_a_bijection_onto_the_map(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    settings = json.loads((target / ".claude" / "settings.json").read_text())
    map_data = load_map()
    _assert_settings_bijection(settings, map_data)


def test_marketplace_and_plugin_are_single_sourced(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    settings = json.loads((target / ".claude" / "settings.json").read_text())
    pkg = load_pkg()
    pins = pkg["foundry"]
    assert set(settings["extraKnownMarketplaces"].keys()) == {pins["marketplace_name"]}
    entry = settings["extraKnownMarketplaces"][pins["marketplace_name"]]
    assert entry["source"]["repo"] == pins["marketplace_repo"]
    expected_key = f"{pins['plugin_name']}@{pins['marketplace_name']}"
    assert settings["enabledPlugins"] == {expected_key: True}

    # derivation, not a hardcoded literal: a copy of the package with DIFFERENT pins produces a
    # correspondingly different settings.json.
    alt_dir = tmp_path / "alt-cli"
    shutil.copytree(CLI_DIR, alt_dir)
    alt_pkg_path = alt_dir / "package.json"
    alt_pkg = json.loads(alt_pkg_path.read_text())
    alt_pkg["foundry"]["marketplace_name"] = "some-other-marketplace"
    alt_pkg["foundry"]["plugin_name"] = "some-other-plugin"
    alt_pkg_path.write_text(json.dumps(alt_pkg, indent=2))
    home2 = tmp_path / "home-alt"
    home2.mkdir()
    target2 = tmp_path / "ws-alt"
    proc2 = subprocess.run(
        [NODE, str(alt_dir / "bin" / "create-agentic-workspace.mjs"), "--dir", str(target2), "--yes"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home2)},
        timeout=30,
    )
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    settings2 = json.loads((target2 / ".claude" / "settings.json").read_text())
    assert set(settings2["extraKnownMarketplaces"].keys()) == {"some-other-marketplace"}
    assert "some-other-plugin@some-other-marketplace" in settings2["enabledPlugins"]


def test_the_marketplace_entry_is_the_pinned_literal(tmp_path):
    """INVERTED (feat-foundry-installer-unpinning, AC-IUP-3). WAS: asserted `source.ref` present.
    Name unchanged (checkpoints/AC-IUP-7 reference it by node id); the composed entry now carries
    NO ref key at all — see _assert_marketplace_pinned_literal's docstring for the supersession."""
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    settings = json.loads((target / ".claude" / "settings.json").read_text())
    pkg = load_pkg()
    pins = pkg["foundry"]
    entry = settings["extraKnownMarketplaces"][pins["marketplace_name"]]
    _assert_marketplace_pinned_literal(entry, pins["marketplace_repo"])
    assert set(entry.keys()) == {"source", "autoUpdate"}
    assert set(entry["source"].keys()) == {"source", "repo"}
    assert entry["source"]["source"] == "github"
    assert "ref" not in entry["source"]
    assert entry["autoUpdate"] is False


# ── AC-IUP-3 (feat-foundry-installer-unpinning) ─────────────────────────────────────────────────


def test_composed_marketplace_source_carries_no_ref_key():
    """NEW (AC-IUP-3): exercises buildSettings directly (never a scaffold subprocess) and asserts
    the composed entry's key set is exactly {source, autoUpdate}, source's key set exactly
    {source, repo} — the shape feat-foundry-bootstrap-cli.md:184 (AC-BCL-4(b)) already declares."""
    pkg = load_pkg()
    pins = pkg["foundry"]
    js = """
import { loadMap, buildSettings } from './src/permissionFloor.mjs';
const map = loadMap('./permission-floor.json');
const pins = %s;
const settings = buildSettings(map, pins);
console.log(JSON.stringify(settings.extraKnownMarketplaces[pins.marketplace_name]));
""" % json.dumps(pins)
    entry = json.loads(_node_eval(js))
    assert set(entry.keys()) == {"source", "autoUpdate"}, entry
    assert set(entry["source"].keys()) == {"source", "repo"}, entry
    assert entry["source"]["source"] == "github"
    assert entry["source"]["repo"] == pins["marketplace_repo"]
    assert entry["autoUpdate"] is False


def test_cli_registers_with_auto_update_false(tmp_path):
    """NEW (AC-IUP-4, the npx CLI half): the CLI already writes an explicit `autoUpdate: false` on
    every scaffold — this checkpoint pins that continuing to hold as the shell installer's own
    AC-IUP-4 node is satisfied a different way (see test_bootstrap_registers_with_auto_update_false
    in tests/test_bootstrap_install_pin.py)."""
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    settings = json.loads((target / ".claude" / "settings.json").read_text())
    pkg = load_pkg()
    pins = pkg["foundry"]
    entry = settings["extraKnownMarketplaces"][pins["marketplace_name"]]
    assert entry["autoUpdate"] is False


def test_no_ref_autoupdate_false_entry_classifies_pinned_and_grants_allow():
    """NEW (AC-IUP-5): the tagless, autoUpdate:false shape AC-IUP-3 now composes must classify
    PINNED under floorReconcile.mjs's predicate and must NOT withhold the allow tier — proven
    against the SAME classifyPin/planAdditions the reconcile path runs, not a re-implementation."""
    pkg = load_pkg()
    pins = pkg["foundry"]
    js = """
import { loadMap, classifyDrift } from './src/permissionFloor.mjs';
import { classifyPin, planAdditions, readTrackedRules } from './src/floorReconcile.mjs';
const map = loadMap('./permission-floor.json');
const pins = %s;
const entry = { source: { source: 'github', repo: pins.marketplace_repo }, autoUpdate: false };
const settingsObj = {
  permissions: { allow: [], ask: [], deny: [] },
  extraKnownMarketplaces: { [pins.marketplace_name]: entry },
};
const pin = classifyPin(settingsObj, pins);
const findings = classifyDrift(map, readTrackedRules(settingsObj), {
  pluginRootExpansion: ['x'], unreadableOrigins: [], home: '/home/testuser',
});
const plan = planAdditions({ findings, map, settingsObj, pins });
console.log(JSON.stringify({
  pinState: pin.state, pinRef: pin.ref, pinSkew: pin.skew,
  withheldAllow: plan.withheldAllow, allowCount: plan.additions.allow.length,
}));
""" % json.dumps(pins)
    data = json.loads(_node_eval(js))
    assert data["pinState"] == "pinned", data
    assert data["pinRef"] is None, data
    assert data["pinSkew"] is False, data
    assert data["withheldAllow"] is False, data
    assert data["allowCount"] > 0, data


def _walk_forbidden_keys(obj, forbidden, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in forbidden:
                found.append(f"{path}.{k}")
            found += _walk_forbidden_keys(v, forbidden, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _walk_forbidden_keys(v, forbidden, f"{path}[{i}]")
    return found


def test_settings_json_key_set_is_closed(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    settings = json.loads((target / ".claude" / "settings.json").read_text())
    _assert_settings_key_set_closed(settings)


def test_the_cli_never_spawns_claude_or_writes_home_claude(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    marker = tmp_path / "claude-was-invoked"
    claude_stub = fakebin / "claude"
    claude_stub.write_text(f"#!/usr/bin/env bash\ntouch {marker}\nexit 0\n")
    claude_stub.chmod(claude_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    proc, home, target = _scaffold(tmp_path)
    # re-run with the fake claude on PATH just in case any code path considers spawning it
    proc2 = run_cli(
        ["--dir", str(target), "--yes", "--existing", "--gh-account", "watchacct", "--git-author", "W <w@example.com>"],
        home=home,
        path_prepend=str(fakebin),
    )
    assert proc2.returncode in (0, 2)
    assert not marker.exists(), "the CLI must never spawn `claude`"
    assert not (home / ".claude").exists(), "the CLI must never write under $HOME/.claude/"
    assert not (target / ".claude" / "settings.local.json").exists()

    # Static witness. REWRITTEN 2026-09-01 — the previous version matched only a STRING-LITERAL
    # first argument (`execFileSync('git', ...)`), so the moment the update atom introduced
    # `execFileSync(claudeBin || 'claude', ...)` that site became invisible and this assertion
    # passed while guarding nothing. A guard that silently stops covering the one new spawn in the
    # package is worse than no guard, because its green is read as coverage.
    #
    # So: enumerate spawn sites STRUCTURALLY (every execFile/execFileSync, whatever the first
    # argument looks like), then split by module. The create path keeps the closed {git, gh} set.
    # The update path may spawn `claude` — that is what it is for — but only from one file, one
    # function, and behind the allowlist.
    # Spawn sites are found via the module's own child_process IMPORT BINDINGS, not by grepping for
    # the literal name `execFileSync`. `identity.mjs` already imports `execFile as execFileCb`, so
    # aliasing is in legitimate use here — and a name-based scan silently stops covering any call
    # made through an alias. Resolving the local binding names first means a rename cannot walk a
    # spawn out of this guard's view; it also pins the imported SURFACE, so `spawn`/`exec`/`execSync`
    # (shell-bearing, or stream-shaped) cannot be introduced without this assertion being revisited.
    UPDATE_PATH = {"update.mjs", "pluginRefresh.mjs", "cleanup.mjs"}
    ALLOWED_CP_IMPORTS = {"execFileSync", "execFile"}
    create_spawns, update_spawns = [], []
    for f in _iter_mjs_files(CLI_DIR / "src"):
        text = f.read_text()
        bindings = []
        for imp in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*['\"]node:child_process['\"]", text):
            for spec in imp.group(1).split(","):
                spec = spec.strip()
                if not spec:
                    continue
                parts = re.split(r"\s+as\s+", spec)
                original, local = parts[0].strip(), parts[-1].strip()
                assert original in ALLOWED_CP_IMPORTS, (
                    f"{f.name} imports {original!r} from child_process, outside the closed "
                    f"{sorted(ALLOWED_CP_IMPORTS)} surface"
                )
                bindings.append(local)
        assert "require(" not in text or "child_process" not in text, (
            f"{f.name} reaches child_process through require(), which this guard does not resolve"
        )
        for local in bindings:
            for m in re.finditer(re.escape(local) + r"\s*\(\s*([^,]+?)\s*,", text):
                (update_spawns if f.name in UPDATE_PATH else create_spawns).append((f, m.group(1).strip()))

    assert create_spawns, "expected at least one create-path spawn call site to inspect"
    for f, arg in create_spawns:
        literal = re.fullmatch(r"['\"]([a-zA-Z0-9_.-]+)['\"]", arg)
        assert literal, f"{f.name} spawns a non-literal executable {arg!r} on the create path"
        assert literal.group(1) in ("git", "gh"), (
            f"{f.name} spawns {literal.group(1)!r}, outside the closed {{git, gh}} set"
        )

    # The update path's single claude spawn. Pinned to one site so a second one cannot appear
    # without this test being revisited — the property AC-UAW-14 buys is "one allowlisted spawn",
    # and that is only true while there is exactly one.
    assert [f.name for f, _ in update_spawns] == ["pluginRefresh.mjs"], (
        f"the claude spawn must live in exactly one update-path module, found: "
        f"{[f.name for f, _ in update_spawns]}"
    )
    # NOT _extract_function_body here: that helper takes the first `{` after the parameter-list
    # `(`, which for `runClaude(args, { env, cwd, claudeBin })` is the DESTRUCTURING pattern, not
    # the body — it would return `{ env, cwd, claudeBin }` and every assertion below would be
    # inspecting a parameter list while appearing to inspect a function. Slice the source region
    # from `function runClaude` to the next top-level declaration instead.
    refresh_src = (CLI_DIR / "src" / "pluginRefresh.mjs").read_text()
    start = re.search(r"\bfunction\s+runClaude\s*\(", refresh_src)
    assert start is not None, "runClaude not found in pluginRefresh.mjs"
    nxt = re.search(r"\n(?:export\s+)?(?:function|const|class)\s", refresh_src[start.end():])
    run_claude = refresh_src[start.end(): start.end() + (nxt.start() if nxt else len(refresh_src))]
    assert re.search(r"execFile(?:Sync)?\s*\(", run_claude), (
        "the claude spawn is no longer inside runClaude — the allowlist check is bypassable"
    )
    assert "isAllowedInvocation" in run_claude, (
        "runClaude spawns without consulting isAllowedInvocation — the argv allowlist is the only "
        "thing standing between adopter-writable JSON and the claude CLI's option parser"
    )
    # ...and the create-path modules must not import it, or the closed set above is decorative.
    for f in _iter_mjs_files(CLI_DIR / "src"):
        if f.name in UPDATE_PATH:
            continue
        assert "runClaude" not in f.read_text(), f"create-path module {f.name} reaches for runClaude"


def test_the_exit_line_explains_the_trust_gate(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    out = proc.stdout
    assert "trust dialog" in out
    assert "cd " in out
    assert "claude" in out
    assert "/foundry:init" in out
    assert "/foundry:doctor" in out
    assert "only after" in out and "allow" in out
    assert "installed plugin" in out


# ── AC-BCL-5 ─────────────────────────────────────────────────────────────────────────────────────


def test_the_bundled_map_mirrors_the_shipped_map_byte_for_byte():
    bundled = (CLI_DIR / "permission-floor.json").read_bytes()
    shipped = (REPO_ROOT / "docs" / "permission-floor.json").read_bytes()
    assert bundled == shipped


def _extract_runtime_block(gitignore_text):
    m = re.search(
        r"# FOUNDRY-RUNTIME-GITIGNORE-BEGIN\n(.*?)# FOUNDRY-RUNTIME-GITIGNORE-END\n",
        gitignore_text,
        re.DOTALL,
    )
    assert m, "FOUNDRY-RUNTIME-GITIGNORE-BEGIN/END markers not found"
    return m.group(1)


def test_the_gitignore_template_carries_the_shipped_runtime_block():
    template_text = (CLI_DIR / "templates" / "gitignore.tmpl").read_text()
    interior = _extract_runtime_block(template_text)
    shipped = (REPO_ROOT / "scripts" / "foundry-runtime.gitignore").read_text()
    assert interior == shipped


def test_every_runtime_asset_is_packaged():
    pkg = load_pkg()
    files_field = pkg["files"]

    def matched(rel: str) -> bool:
        for entry in files_field:
            if rel == entry:
                return True
            if rel.startswith(entry.rstrip("/") + "/"):
                return True
        return False

    runtime_paths = [
        Path("bin/create-agentic-workspace.mjs"),
        *[p.relative_to(CLI_DIR) for p in _iter_mjs_files(CLI_DIR / "src")],
        *[p.relative_to(CLI_DIR) for p in (CLI_DIR / "templates").rglob("*") if p.is_file()],
        Path("permission-floor.json"),
        Path("package.json"),
    ]
    for rel in runtime_paths:
        assert matched(str(rel)), f"{rel} is not matched by package.json's files array"

    # The plugin cache is READ, and only read (PR #61 security review Risk 4). The prior assertion
    # here — `"plugins/cache" not in text or "plugin_root_glob" in text or "map." in text` — could
    # not fail for the one file it guarded: the third arm matches the substring "map." which any
    # module doing a dict/Map access carries, so the disjunction was unfalsifiable and the claim
    # "reads no plugin-cache path" was neither true (run.mjs stats the cache to derive the advisory
    # `stale-plugin-path` finding) nor tested. Assert the property that is actually true and
    # actually load-bearing: every filesystem call inside the cache-walking function is READ-ONLY.
    # Structural — the function body is extracted by balanced-brace scan and each `fs.<method>`
    # call inside it checked against a read-only allowlist, so a write introduced there fails even
    # if it is spelled in a way no substring search anticipated.
    # SCOPE, rewritten for the workspace-update atoms (2026-09-01). Until those atoms the claim
    # "the CLI only ever READS the plugin cache" was true of every module, so one read-only rule
    # covered the whole of cli/src. `cleanup.mjs` deliberately breaks it: pruning superseded cache
    # versions is that atom's entire job, and its spec's R1 says so in as many words ("this atom
    # puts the first recursive delete of an adopter path into cli/src/").
    #
    # The wrong response is to exempt it — that deletes the guard for the one file that most needs
    # one. So the file set is split: CREATE-path modules keep the unchanged read-only rule, and the
    # single cache MUTATOR is held to a stricter, structural containment rule of its own.
    read_only_fs = {"existsSync", "statSync", "lstatSync", "readdirSync", "readFileSync", "realpathSync"}
    CACHE_MUTATORS = {"cleanup.mjs"}
    cache_readers = []
    for f in _iter_mjs_files(CLI_DIR / "src") + _iter_mjs_files(CLI_DIR / "bin"):
        text = f.read_text()
        assert "fetch(" not in text, f"{f.name} carries a fetch() call"
        if "plugins/cache" not in text:
            continue
        if f.name in CACHE_MUTATORS:
            continue
        cache_readers.append(f.name)
        body = _extract_function_body(text, "expandPluginRootGlob")
        assert body is not None, f"{f.name} references plugins/cache outside expandPluginRootGlob"
        calls = set(re.findall(r"\bfs\.(\w+)\s*\(", body))
        assert calls, f"expandPluginRootGlob in {f.name} makes no fs call — did it move?"
        assert calls <= read_only_fs, (
            f"expandPluginRootGlob in {f.name} makes non-read-only fs calls: {sorted(calls - read_only_fs)}"
        )
    # Pin the reader set: a NEW file touching the plugin cache must come here and be justified,
    # rather than inheriting a guard written for run.mjs.
    assert cache_readers == ["run.mjs"], f"unexpected plugin-cache readers: {cache_readers}"

    # ── the cache MUTATOR: containment, asserted structurally ────────────────────────────────────
    # Three properties, each of which a plausible regression would break:
    #   1. `rmSync` appears in exactly one function, `applyCachePrune`. A delete introduced anywhere
    #      else in cli/src fails here even if it is spelled in a way no substring search anticipated.
    #   2. `applyCachePrune` does not ENUMERATE. It removes only the candidates handed to it; a
    #      `readdirSync` inside it would let it discover and delete paths that were never validated.
    #   3. `planCachePrune` — which produces those candidates — still carries all three of its
    #      guards. Deleting any one of them is how this becomes a directory-escape.
    mutator_src = (CLI_DIR / "src" / "cleanup.mjs").read_text()
    # RECURSIVE deletes specifically. `floorReconcile.mjs` legitimately does `fs.rmSync(tmp,
    # {force:true})` to clean up its own temp file in the atomic write — a single named file it
    # just created, not a tree walk. Pinning on `fs.rm*` alone would sweep that in and force the
    # guard to carry an exemption list; pinning on `recursive: true` states the property that
    # actually matters, so the temp-file cleanup is out of scope by construction rather than by
    # exception.
    rm_sites = [
        f.name
        for f in _iter_mjs_files(CLI_DIR / "src") + _iter_mjs_files(CLI_DIR / "bin")
        if re.search(r"\bfs\.rm(?:Sync)?\s*\([^;]*recursive\s*:\s*true", f.read_text())
    ]
    assert rm_sites == ["cleanup.mjs"], f"unexpected recursive-delete sites in cli/src: {rm_sites}"

    apply_body = _extract_function_body(mutator_src, "applyCachePrune")
    assert apply_body is not None, "applyCachePrune not found in cleanup.mjs"
    assert "rmSync" in apply_body, "applyCachePrune makes no rmSync call — did the delete move?"
    assert "readdirSync" not in apply_body, (
        "applyCachePrune enumerates the cache directory; it must remove ONLY the pre-validated "
        "candidates planCachePrune handed it"
    )

    # The plan/apply separation, asserted as a COUNT rather than as an absence over a region: the
    # file must carry exactly one recursive delete, and it must be the one inside applyCachePrune.
    # That is strictly stronger than "planCachePrune contains no rmSync" and does not depend on
    # where a region slice happens to end.
    recursive_rms = re.findall(r"\bfs\.rm(?:Sync)?\s*\([^;]*recursive\s*:\s*true", mutator_src)
    assert len(recursive_rms) == 1, (
        f"cleanup.mjs carries {len(recursive_rms)} recursive deletes; exactly one is permitted, "
        f"inside applyCachePrune"
    )
    assert re.search(r"\bfs\.rm(?:Sync)?\s*\([^;]*recursive\s*:\s*true", apply_body), (
        "the recursive delete is not inside applyCachePrune — planning and applying must stay split"
    )

    plan_body = _function_region(mutator_src, "planCachePrune")
    assert plan_body is not None, "planCachePrune not found in cleanup.mjs"
    for guard in ("isSymbolicLink", "isDirectory", "realpathSync"):
        assert guard in plan_body, (
            f"planCachePrune lost its {guard} guard — this is the check that keeps a recursive "
            f"delete inside the pinned cache root"
        )


# ── AC-BCL-6 ─────────────────────────────────────────────────────────────────────────────────────


def test_the_created_path_set_is_exactly_the_declared_set(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    actual = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
    assert actual == DECLARED_PATH_SET, actual ^ DECLARED_PATH_SET


def test_the_seeded_manifest_validates_against_the_shipped_schema(tmp_path):
    import jsonschema

    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    manifest = json.loads((target / ".claude" / "foundry-project.json").read_text())
    schema = json.loads((REPO_ROOT / "schema" / "foundry-project.schema.json").read_text())
    jsonschema.validate(manifest, schema)
    assert manifest["schema_version"] == 1
    assert manifest["repos"]["workspace"]["path"] == "."
    assert manifest["repos"]["workspace"]["role"] == "workspace"


def test_claude_md_carries_one_stage_mode_line(tmp_path):
    proc, home, target = _scaffold(tmp_path, extra_args=["--stage-mode", "scale"])
    assert proc.returncode == 0
    text = (target / "CLAUDE.md").read_text()
    matches = re.findall(r"^Stage mode: (lean|scale)$", text, re.M)
    assert matches == ["scale"]


def test_claude_md_carries_the_atomic_spec_convention_line(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    text = (target / "CLAUDE.md").read_text()
    hit = [
        line
        for line in text.splitlines()
        if "specs/features/" in line and "<!-- normative -->" in line and "acceptance-contract.yaml" in line
    ]
    assert len(hit) == 1, text


def test_gitignore_lines_are_root_anchored(tmp_path):
    proc, home, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    text = (target / ".gitignore").read_text()
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        assert "/" in line[:-1], f"non-root-anchored gitignore line: {line!r}"


# ── AC-BCL-7 ─────────────────────────────────────────────────────────────────────────────────────


def _run_shell_bootstrap(home, target, args, fakebin):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "foundry-bootstrap.sh"), "project-scaffold", str(target), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc


def _make_fake_claude(tmp_path, marketplace="lukasrepublic/agentic-foundry"):
    fakebin = tmp_path / "fakebin-claude"
    fakebin.mkdir(exist_ok=True)
    short = marketplace.split("/")[-1]
    stub = fakebin / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then\n'
        f'  echo \'[{{"id":"foundry@{short}"}}]\'\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fakebin


def test_identity_wiring_matches_the_shipped_bootstrap_byte_for_byte(tmp_path):
    fakebin = _make_fake_claude(tmp_path)
    slug = "diffacct"
    author = "Diff Person <diff@example.com>"
    home_dir = tmp_path / "diff-home"
    target_dir = tmp_path / "diff-target"

    # (A) the CLI's own run
    home_dir.mkdir()
    proc_cli = run_cli(
        ["--dir", str(target_dir), "--yes", "--gh-account", slug, "--git-author", author],
        home=home_dir,
    )
    assert proc_cli.returncode == 0, proc_cli.stdout + proc_cli.stderr
    a_gitconfig = (home_dir / ".gitconfig").read_bytes()
    a_inc_path = home_dir / ".config" / "git" / f"identity-{slug}"
    a_inc_bytes = a_inc_path.read_bytes()
    a_gitconfig_local = (target_dir / ".git" / "config").read_text()
    a_use_config_only = [l for l in a_gitconfig_local.splitlines() if "useConfigOnly" in l]
    a_marker = (target_dir / ".claude" / "gh-identity").read_bytes()

    # reset to pristine, REUSING the exact same paths, so the composed includeIf subsections and
    # include-file path are byte-identical between the two runs.
    shutil.rmtree(home_dir)
    shutil.rmtree(target_dir)
    home_dir.mkdir()
    target_dir.mkdir()
    subprocess.run(["git", "init", "--quiet", str(target_dir)], check=True, timeout=10)

    # (B) the shipped shell script's run
    proc_sh = _run_shell_bootstrap(
        home_dir, target_dir, ["--existing", "--gh-account", slug, "--git-author", author], fakebin
    )
    assert proc_sh.returncode == 0, proc_sh.stdout + proc_sh.stderr
    b_gitconfig = (home_dir / ".gitconfig").read_bytes()
    b_inc_path = home_dir / ".config" / "git" / f"identity-{slug}"
    b_inc_bytes = b_inc_path.read_bytes()
    b_gitconfig_local = (target_dir / ".git" / "config").read_text()
    b_use_config_only = [l for l in b_gitconfig_local.splitlines() if "useConfigOnly" in l]
    b_marker = (target_dir / ".claude" / "gh-identity").read_bytes()

    assert a_gitconfig == b_gitconfig, (a_gitconfig, b_gitconfig)
    assert a_inc_path == b_inc_path
    assert a_inc_bytes == b_inc_bytes
    assert a_use_config_only == b_use_config_only
    assert a_marker == b_marker


def test_the_gh_probe_is_bounded_and_degrades(tmp_path):
    fakebin = tmp_path / "fake-gh-bin"
    fakebin.mkdir()
    calls_marker = tmp_path / "gh-calls.log"
    env_marker = tmp_path / "gh-env.log"
    gh_stub = fakebin / "gh"
    gh_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {calls_marker}\n'
        f'env | grep -E "^(GH_CONFIG_DIR|GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN|GITHUB_ENTERPRISE_TOKEN|GH_HOST)=" >> {env_marker} || true\n'
        'echo \'{"login":"matchacct","name":"Match Person","email":"match@example.com"}\'\n'
    )
    gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    home = tmp_path / "gh-home"
    home.mkdir()
    target = tmp_path / "gh-target"
    proc = run_cli(
        ["--dir", str(target), "--yes", "--gh-account", "matchacct"],
        home=home,
        path_prepend=str(fakebin),
        env_extra={"GH_TOKEN": "should-be-stripped", "GH_HOST": "should-be-stripped.example"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls_marker.exists()
    calls = calls_marker.read_text().splitlines()
    assert calls == ["api user"], f"expected exactly one `gh api user` call, got {calls}"
    env_lines = env_marker.read_text()
    assert "GH_CONFIG_DIR=" in env_lines
    assert re.search(r"GH_CONFIG_DIR=.*gh-matchacct", env_lines)
    for stripped in ("GH_TOKEN=", "GITHUB_TOKEN=", "GH_ENTERPRISE_TOKEN=", "GITHUB_ENTERPRISE_TOKEN=", "GH_HOST="):
        assert stripped not in env_lines, f"{stripped} leaked into the probe's child environment"
    inc = home / ".config" / "git" / "identity-matchacct"
    assert "Match Person" in inc.read_text()
    assert "match@example.com" in inc.read_text()

    # a LOGIN MISMATCH discards the probe whole (never a partial adopt) and, under --yes with no
    # --git-author, degrades to a refusal (no TTY to prompt).
    gh_stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo \'{"login":"someone-else","name":"Someone Else","email":"else@example.com"}\'\n'
    )
    home2 = tmp_path / "gh-home2"
    home2.mkdir()
    target2 = tmp_path / "gh-target2"
    proc2 = run_cli(["--dir", str(target2), "--yes", "--gh-account", "matchacct"], home=home2, path_prepend=str(fakebin))
    assert proc2.returncode == 1
    assert "Someone Else" not in proc2.stdout
    assert not (home2 / ".config" / "git" / "identity-matchacct").exists()


def test_no_account_means_no_machine_scope_write(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    before = snapshot_tree(home)
    proc, _, target = _scaffold(tmp_path)
    assert proc.returncode == 0
    after = snapshot_tree(home)
    assert before == after
    assert not (home / ".config" / "git").exists()
    assert not (home / ".gitconfig").exists()


# ── AC-BCL-8 ─────────────────────────────────────────────────────────────────────────────────────


def test_a_second_run_writes_nothing(tmp_path):
    proc1, home, target = _scaffold(tmp_path)
    assert proc1.returncode == 0
    before = snapshot_tree(target)
    proc2 = run_cli(["--dir", str(target), "--yes", "--existing"], home=home)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    after = snapshot_tree(target)
    assert before == after
    for rel in DECLARED_PATH_SET:
        assert f"[unchanged] {rel}" in proc2.stdout


def test_an_edited_managed_file_is_reported_not_overwritten(tmp_path):
    proc1, home, target = _scaffold(tmp_path)
    assert proc1.returncode == 0
    claude_md = target / "CLAUDE.md"
    edited = claude_md.read_text() + "\nOPERATOR EDIT\n"
    claude_md.write_text(edited)
    proc2 = run_cli(["--dir", str(target), "--yes", "--existing"], home=home)
    assert proc2.returncode == 2
    assert "[drifted] CLAUDE.md" in proc2.stdout
    assert claude_md.read_text() == edited


def test_drift_classes_are_exactly_the_dpf_vocabulary():
    rc = run_node_tests(pattern="AC-BCL-8: classifyDrift")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    # Assert the NAMED test ran and passed — not a global pass COUNT.
    #
    # The count form (`"pass 1" in rc.stdout`) was counting the wrong thing. Under
    # `--test-name-pattern`, node still visits every file, and a file containing NO matching test
    # is itself reported as a passing test (`✔ test/handoff.test.mjs`). So the total was
    # (matching tests) + (files with no match), and adding ANY new test file anywhere under
    # cli/test/ turned this red — which is exactly how it broke when cli/test/handoff.test.mjs was
    # added, a file with no connection to drift classes at all.
    #
    # What the assertion meant was "the pattern selected this one test and it passed". That is what
    # it now says, and it is strictly stronger: the count form would have been satisfied by any
    # single passing test, including a file-level pass with the real test silently not matching.
    name = "AC-BCL-8: classifyDrift emits exactly the AC-DPF-8 vocabulary, never another class name"
    passed = re.search(rf"^(?:✔|ok \d+ -) {re.escape(name)}", rc.stdout, re.M)
    assert passed, f"the named drift-vocabulary test did not run/pass:\n{rc.stdout}"
    assert re.search(r"(?:^|\s)(?:#|ℹ) fail 0\b", rc.stdout, re.M), rc.stdout


def test_covers_agrees_with_the_map_suite_on_the_shared_table():
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import test_permission_floor_map as sibling

    rows = [
        ("Bash(a/b:*)", "Bash(a/b/c:*)", True),
        ("Bash(a/b/c:*)", "Bash(a/b:*)", False),
        ("Bash(a/b:*)", "Bash(a/b)", True),
        ("Bash(a/bc:*)", "Bash(a/b:*)", False),
        ("Bash(a/b:*)", "Bash(a/bc:*)", True),
        ("Bash(a/b:*)", "Bash(a/b:*)", True),
        (
            "Bash(~/.claude/plugins/cache/*/foundry/*/scripts/:*)",
            "Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-authorize.py:*)",
            True,
        ),
        ("Bash(gh pr merge --admin:*)", "Bash(gh pr merge:*)", False),
    ]
    for a, b, expected in rows:
        py_result = sibling._subsumes(a, b)
        assert py_result == expected, (a, b, py_result)
        _assert_covers_row(a, b, expected)


# ── AC-BCL-9 ─────────────────────────────────────────────────────────────────────────────────────


def test_writes_are_confined_to_the_target_root(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    home_before = set(p for p in home.rglob("*") if p.is_file())
    proc = run_cli(
        ["--dir", str(target), "--yes", "--gh-account", "confineacct", "--git-author", "C <c@example.com>"],
        home=home,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    home_after = set(p for p in home.rglob("*") if p.is_file())
    new_home_files = home_after - home_before
    expected_machine_scope = {
        home / ".gitconfig",
        home / ".config" / "git" / "identity-confineacct",
    }
    for p in new_home_files:
        assert p in expected_machine_scope, f"unexpected machine-scope write: {p}"
    # target-root writes are all physically inside target
    real_target = target.resolve()
    for p in target.rglob("*"):
        if p.is_file():
            assert str(p.resolve()).startswith(str(real_target) + os.sep)


def test_a_traversing_template_path_is_refused(tmp_path):
    js = """
import { buildManagedFiles, TEMPLATE_ENTRIES } from './src/scaffold.mjs';
import path from 'node:path';
const dir = %s;
const bad = [...TEMPLATE_ENTRIES, { template: 'CLAUDE.md.tmpl', target: '../escape.md' }];
let refused = false, message = '';
try {
  buildManagedFiles({ templatesDir: path.join(process.cwd(), 'templates'), physicalRoot: dir, projectName: 'x', stageMode: 'lean', settingsBytes: Buffer.from('{}'), entries: bad });
} catch (e) {
  refused = e.name === 'RefusalError';
  message = e.message;
}
console.log(JSON.stringify({refused, message}));
""" % json.dumps(str(tmp_path))
    data = json.loads(_node_eval(js))
    assert data["refused"] is True
    assert "escape.md" in data["message"]
    assert list(tmp_path.rglob("*")) == []


def test_a_non_empty_target_is_refused_without_existing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    target.mkdir()
    (target / "stray.txt").write_text("pre-existing")
    proc = run_cli(["--dir", str(target), "--yes"], home=home)
    assert proc.returncode == 1
    assert str(target) in proc.stdout
    assert "--existing" in proc.stdout
    remaining = {p.name for p in target.iterdir()}
    assert remaining == {"stray.txt"}


def test_an_existing_tree_is_never_clobbered_or_merged(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "ws"
    (target / ".claude").mkdir(parents=True)
    foreign_settings = json.dumps({"permissions": {"allow": ["Bash(echo:*)"], "ask": [], "deny": []}}, indent=2) + "\n"
    (target / ".claude" / "settings.json").write_text(foreign_settings)
    proc = run_cli(["--dir", str(target), "--yes", "--existing"], home=home)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert (target / ".claude" / "settings.json").read_text() == foreign_settings
    assert "[drifted] .claude/settings.json" in proc.stdout
    for rel in DECLARED_PATH_SET - {".claude/settings.json"}:
        assert (target / rel).exists(), f"{rel} should have been created (it was absent)"


def test_the_import_closure_carries_no_network_module():
    violations, _ = _collect_import_violations(banned=BANNED_NETWORK_MODULES)
    assert not violations, "\n".join(violations)
    for f in _iter_mjs_files():
        assert "fetch(" not in f.read_text(), f"{f} calls fetch()"


def test_the_no_telemetry_statement_is_present(tmp_path):
    readme = (CLI_DIR / "README.md").read_text()
    assert "no telemetry" in readme
    js = "import { renderHelp } from './src/argv.mjs'; import { QUESTION_TABLE } from './src/questions.mjs'; console.log(renderHelp(QUESTION_TABLE));"
    help_text = _node_eval(js)
    assert "no telemetry" in help_text


# ── AC-BCL-10 ────────────────────────────────────────────────────────────────────────────────────


def test_the_node_suite_runs_under_pytest_and_never_skips():
    major = _node_major()
    if major is None:
        pytest.fail("node is not on PATH; AC-BCL-10 requires this to FAIL, never skip")
    if major < REQUIRED_NODE_MAJOR:
        pytest.fail(f"node major {major} is below the declared engines.node floor {REQUIRED_NODE_MAJOR}")
    rc = run_node_tests()
    assert rc.returncode == 0, rc.stdout + rc.stderr
    tests_line = re.search(r"# tests (\d+)", rc.stdout) or re.search(r"ℹ tests (\d+)", rc.stdout)
    skip_line = re.search(r"# skipped (\d+)", rc.stdout) or re.search(r"ℹ skipped (\d+)", rc.stdout)
    assert tests_line and int(tests_line.group(1)) >= 1, rc.stdout
    assert skip_line and int(skip_line.group(1)) == 0, rc.stdout


def test_negative_controls_all_fire(tmp_path):
    results = {}

    # (a) one rule deleted from the bundled map
    alt = tmp_path / "ctrl-a"
    shutil.copytree(CLI_DIR, alt)
    m = json.loads((alt / "permission-floor.json").read_text())
    m["entries"].pop()
    (alt / "permission-floor.json").write_text(json.dumps(m, indent=2))
    bundled = (alt / "permission-floor.json").read_bytes()
    shipped = (REPO_ROOT / "docs" / "permission-floor.json").read_bytes()
    results["a_map_rule_deleted_breaks_mirror"] = bundled != shipped

    # (b) a postinstall key added to package.json
    alt_pkg = json.loads((alt / "package.json").read_text())
    alt_pkg["scripts"]["postinstall"] = "echo pwned"
    results["b_postinstall_breaks_closed_scripts"] = not (set(alt_pkg["scripts"].keys()) <= {"test"})

    # (c) a bare import added to a cli/src module
    alt_c = tmp_path / "ctrl-c"
    shutil.copytree(CLI_DIR, alt_c)
    target_file = alt_c / "src" / "util.mjs"
    target_file.write_text("import leftpad from 'leftpad';\n" + target_file.read_text())
    violations, _ = _collect_import_violations(root=alt_c)
    results["c_bare_import_fires"] = any("leftpad" in v for v in violations)

    # (d) a template entry whose path contains a `..` segment
    data_d = json.loads(
        _node_eval(
            """
import { buildManagedFiles, TEMPLATE_ENTRIES } from './src/scaffold.mjs';
import path from 'node:path';
const bad = [...TEMPLATE_ENTRIES, { template: 'CLAUDE.md.tmpl', target: 'a/../../escape.md' }];
let refused = false;
try {
  buildManagedFiles({ templatesDir: path.join(process.cwd(), 'templates'), physicalRoot: process.cwd(), projectName: 'x', stageMode: 'lean', settingsBytes: Buffer.from('{}'), entries: bad });
} catch (e) { refused = e.name === 'RefusalError'; }
console.log(JSON.stringify({refused}));
"""
        )
    )
    results["d_dotdot_template_path_refused"] = data_d["refused"] is True

    # (e) a managed file edited between two runs -> drifted, byte-identical, never overwritten
    proc1, home_e, target_e = _scaffold(tmp_path, home_name="home-e", target_name="ws-e")
    assert proc1.returncode == 0
    (target_e / "CLAUDE.md").write_text("EDITED\n")
    proc2 = run_cli(["--dir", str(target_e), "--yes", "--existing"], home=home_e)
    results["e_edited_file_reported_drifted"] = (
        proc2.returncode == 2 and (target_e / "CLAUDE.md").read_text() == "EDITED\n"
    )

    # (f) one written rule re-tiered relative to the bundled map -> the REAL bijection helper
    # (the one test_settings_permissions_are_a_bijection_onto_the_map itself calls) fires.
    m2 = load_map()
    real_settings = {
        "permissions": {tier: [e["rule"] for e in m2["entries"] if e["tier"] == tier] for tier in ("allow", "ask", "deny")}
    }
    # re-tier: move one `ask` rule into the written `allow` list, as a bad plugin build might.
    retiered = json.loads(json.dumps(real_settings))
    moved_rule = next(e["rule"] for e in m2["entries"] if e["tier"] == "ask")
    retiered["permissions"]["allow"].append(moved_rule)
    try:
        _assert_settings_bijection(retiered, m2)
        results["f_retiered_rule_breaks_bijection"] = False
    except AssertionError:
        results["f_retiered_rule_breaks_bijection"] = True
    # the un-mutated settings must still pass (sanity: the helper itself is not just always-failing)
    _assert_settings_bijection(real_settings, m2)

    # (g) one row of the AC-BCL-8 subsumption table flipped -> the REAL row-assertion helper fires
    # when fed the table's expected verdict flipped (row 2: A=`Bash(a/b/c:*)`, B=`Bash(a/b:*)`,
    # expected False; the mutated fixture asserts True instead).
    try:
        _assert_covers_row("Bash(a/b/c:*)", "Bash(a/b:*)", True)
        results["g_row_flip_control_fires"] = False
    except AssertionError:
        results["g_row_flip_control_fires"] = True
    _assert_covers_row("Bash(a/b/c:*)", "Bash(a/b:*)", False)  # the correct verdict still passes

    # (h) hooks + statusLine keys added to written settings -> the REAL closed-key-set helper fires
    real_map_settings = json.loads(json.dumps(real_settings))
    real_map_settings["extraKnownMarketplaces"] = {}
    real_map_settings["enabledPlugins"] = {}
    _assert_settings_key_set_closed(real_map_settings)  # sanity: passes unmutated
    mutated_h = json.loads(json.dumps(real_map_settings))
    mutated_h["hooks"] = {}
    mutated_h["statusLine"] = {"type": "command", "command": "echo hi"}
    try:
        _assert_settings_key_set_closed(mutated_h)
        results["h_hooks_and_statusline_detected"] = False
    except AssertionError:
        results["h_hooks_and_statusline_detected"] = True

    # (i) autoUpdate flipped true -> the REAL pinned-literal helper fires
    pkg_i = load_pkg()
    repo_i = pkg_i["foundry"]["marketplace_repo"]
    good_entry = {
        "source": {"source": "github", "repo": repo_i},
        "autoUpdate": False,
    }
    _assert_marketplace_pinned_literal(good_entry, repo_i)  # sanity
    bad_entry = {**good_entry, "autoUpdate": True}
    try:
        _assert_marketplace_pinned_literal(bad_entry, repo_i)
        results["i_autoupdate_true_detected"] = False
    except AssertionError:
        results["i_autoupdate_true_detected"] = True

    # (o) THE REINTRODUCED REF, and (o2) A NON-GITHUB SOURCE. Both must redden. R3 (spec Risks):
    # after feat-foundry-installer-unpinning a DROPPED ref is the CORRECT shape (the checkpoint
    # this control used to run — "a dropped ref is detected" — is exactly what this atom deliberately
    # reverses), so control (o) must convict something else real instead of riding on that no-longer-
    # defective case: a `ref` key an ordinary refactor might reintroduce, and a `source.source` that
    # silently stopped being "github".
    reintroduced_ref_entry = {
        "source": {"source": "github", "repo": repo_i, "ref": "v9.9.9"},
        "autoUpdate": False,
    }
    try:
        _assert_marketplace_pinned_literal(reintroduced_ref_entry, repo_i)
        results["o_reintroduced_ref_detected"] = False
    except AssertionError:
        results["o_reintroduced_ref_detected"] = True

    non_github_source_entry = {
        "source": {"source": "npm", "repo": repo_i},
        "autoUpdate": False,
    }
    try:
        _assert_marketplace_pinned_literal(non_github_source_entry, repo_i)
        results["o2_non_github_source_detected"] = False
    except AssertionError:
        results["o2_non_github_source_detected"] = True

    # (p) THE DANGLING-SYMLINK ESCAPE (PR #61 security review Block 2). A managed path that is a
    #     symlink to a NONEXISTENT target outside the root: existsSync FOLLOWS symlinks and so
    #     reported `false`, the row was classified `create`, and writeFileSync followed the link
    #     and wrote OUTSIDE the physically-resolved target root — reachable at
    #     $HOME/.claude/settings.json, which no trust dialog gates. Asserted at the OUTCOME level,
    #     the only level that matters: nothing may appear at the link target, and the run must not
    #     report success. Note the pre-existing confinement test could not catch this — it
    #     enumerates `p.is_file()`, and a symlink to a nonexistent path is not a file.
    home_p = tmp_path / "home-p"
    home_p.mkdir()
    target_p = tmp_path / "ws-p"
    (target_p / ".claude").mkdir(parents=True)
    outside_p = tmp_path / "outside-p" / "settings.json"
    outside_p.parent.mkdir(parents=True)
    (target_p / ".claude" / "settings.json").symlink_to(outside_p)
    proc_p = run_cli(["--dir", str(target_p), "--yes", "--existing"], home=home_p)
    results["p_dangling_symlink_never_written_through"] = (
        not outside_p.exists() and proc_p.returncode != 0
    )

    # (j) a foreign hand-authored settings.json under --existing --yes -> drifted, never merged
    home_j = tmp_path / "home-j"
    home_j.mkdir()
    target_j = tmp_path / "ws-j"
    (target_j / ".claude").mkdir(parents=True)
    foreign = '{"permissions": {"allow": ["Bash(echo:*)"], "ask": [], "deny": []}}\n'
    (target_j / ".claude" / "settings.json").write_text(foreign)
    proc_j = run_cli(["--dir", str(target_j), "--yes", "--existing"], home=home_j)
    results["j_foreign_settings_never_merged"] = (
        proc_j.returncode == 2 and (target_j / ".claude" / "settings.json").read_text() == foreign
    )

    # (k) one byte flipped in the CLI's identity wiring -> the differential must redden
    alt_k = tmp_path / "ctrl-k"
    shutil.copytree(CLI_DIR, alt_k)
    identity_file = alt_k / "src" / "identity.mjs"
    text_k = identity_file.read_text()
    mutated_k = text_k.replace("user.useConfigOnly", "user.useConfigOnlyX")
    assert mutated_k != text_k
    identity_file.write_text(mutated_k)
    home_k = tmp_path / "home-k"
    home_k.mkdir()
    target_k = tmp_path / "ws-k"
    proc_k = subprocess.run(
        [NODE, str(alt_k / "bin" / "create-agentic-workspace.mjs"), "--dir", str(target_k), "--yes",
         "--gh-account", "kacct", "--git-author", "K <k@example.com>"],
        capture_output=True, text=True, env={**os.environ, "HOME": str(home_k)}, timeout=30,
    )
    if proc_k.returncode == 0 and (target_k / ".git" / "config").exists():
        cfg_text = (target_k / ".git" / "config").read_text()
        results["k_flipped_byte_reddens_differential"] = "useConfigOnly = true" not in cfg_text
    else:
        results["k_flipped_byte_reddens_differential"] = True  # refused/broken outright also counts as reddening

    # (l) an eighth file written under the target -> closed path-set check fires
    proc_l, home_l, target_l = _scaffold(tmp_path, home_name="home-l", target_name="ws-l")
    assert proc_l.returncode == 0
    (target_l / "stray-eighth-file.txt").write_text("intrusion")
    actual_l = {str(p.relative_to(target_l)) for p in target_l.rglob("*") if p.is_file()}
    results["l_eighth_file_breaks_closed_set"] = actual_l != DECLARED_PATH_SET

    # (m) a flag accepted by argv (a mutated parser hard-codes one extra flag) but absent from the
    # question table -> the REAL bijection helper fires. `--help`'s own rendering is unaffected by
    # the mutation (it still only renders the real table), so the mismatch is exactly the parity
    # gap AC-BCL-2 exists to catch.
    js_m = """
import { QUESTION_TABLE, tableFlags } from './src/questions.mjs';
import { renderHelp } from './src/argv.mjs';
const help = renderHelp(QUESTION_TABLE);
const helpFlags = [...help.matchAll(/^\\s+--([a-z-]+)/gm)].map(m => m[1]);
const tableFlagsList = tableFlags(QUESTION_TABLE);
// a hard-coded second list a mutated argv parser might accept, NOT derived from the table:
const mutatedAcceptedFlags = [...tableFlagsList, 'ghost-flag'];
console.log(JSON.stringify({ tableFlagsList, helpFlags, mutatedAcceptedFlags }));
"""
    data_m = json.loads(_node_eval(js_m))
    _assert_flag_bijection(data_m["tableFlagsList"], data_m["tableFlagsList"], data_m["helpFlags"])  # sanity
    try:
        _assert_flag_bijection(data_m["tableFlagsList"], data_m["mutatedAcceptedFlags"], data_m["helpFlags"])
        results["m_ghost_flag_breaks_parity"] = False
    except AssertionError:
        results["m_ghost_flag_breaks_parity"] = True

    # (n) a managed path omitted from the preview -> the REAL preview-coverage helper fires
    proc_n, home_n, target_n = _scaffold(tmp_path, extra_args=["--dry-run"], home_name="home-n", target_name="ws-n")
    _assert_preview_covers_declared_set(proc_n.stdout, DECLARED_PATH_SET)  # sanity: passes unmutated
    mutated_preview = proc_n.stdout.replace("] .foundry/README.md", "] REDACTED")
    try:
        _assert_preview_covers_declared_set(mutated_preview, DECLARED_PATH_SET)
        results["n_omitted_path_detected"] = False
    except AssertionError:
        results["n_omitted_path_detected"] = True

    failed = [name for name, ok in results.items() if not ok]
    assert not failed, f"negative controls that did NOT fire: {failed}\nall results: {results}"
    assert len(results) >= 14, f"expected >=14 negative controls, got {len(results)}"


# ── AC-BCL-11 ────────────────────────────────────────────────────────────────────────────────────


def test_quickstart_leads_with_the_npx_line():
    text = (REPO_ROOT / "docs" / "QUICKSTART.md").read_text()
    npx_idx = text.find("npx create-agentic-workspace")
    marketplace_idx = text.find("claude plugin marketplace add")
    assert npx_idx != -1, "docs/QUICKSTART.md is missing the npx create-agentic-workspace line"
    assert marketplace_idx != -1
    assert npx_idx < marketplace_idx
    npx_line = next(l for l in text.splitlines() if "npx create-agentic-workspace" in l)
    assert not re.search(r"npx create-agentic-workspace@\S+", npx_line), "no new version literal"


def test_readme_and_changelog_name_the_cli():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "create-agentic-workspace" in readme

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
    headings = list(re.finditer(r"^## .+$", changelog, re.M))
    assert len(headings) >= 2, "CHANGELOG.md needs at least two top-level release headings"
    topmost_start = headings[0].end()
    topmost_end = headings[1].start()
    topmost_section = changelog[topmost_start:topmost_end]
    assert "create-agentic-workspace" in topmost_section, "the topmost CHANGELOG.md section must name create-agentic-workspace"
