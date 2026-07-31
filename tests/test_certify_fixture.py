"""tests/test_certify_fixture.py — the certify-local fixture proof
(feat-*-certification, `2026-07-27-phase5-certification.md` charter).

Exercises `skills/certify-local/certify_local.py` over the checked-in static-site fixture
(`tests/fixtures/certify/`: an `index.html`/`page2.html` app, a 2-journey Playwright spec tagged
`@FIX-1`/`@FIX-2`, and a `release.yaml` manifest declaring two atoms — one per journey):

  * the two REFUSE paths (no boot recipe; no journey suite) — always run, no network/Playwright
    needed, since both are refused BEFORE anything is dispatched;
  * the acceptance-note (`accept`) verb end-to-end, over the same release manifest;
  * the full happy path — deploy once (a plain `python3 -m http.server`), run the tagged journey
    suite via a REAL `npx playwright test`, certify green — gated on `@playwright/test` actually
    being installable + runnable in THIS environment. If it is not, the happy-path test SKIPS
    with a named reason (never a faked browser run); the REFUSE/accept tests above still prove
    the certification machinery regardless.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

import pytest
import yaml

from conftest import REPO_ROOT, load_module

FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "certify")

release = load_module("scripts/foundry_release.py", "foundry_release_certify_fixture")
sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_certify_fixture")
cl = load_module("skills/certify-local/certify_local.py", "certify_local_fixture")


# ────────────────────────────────────────────────────────────────────── shared fixture plumbing ── #

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_repo(tmp_path, *, journeys=True):
    """Copy the checked-in static-site fixture into a throwaway dir that doubles as BOTH the
    `CLAUDE_PROJECT_DIR` project dir AND the release's target repo (both atoms omit `target_repo`
    -> the self-host default -> the project dir itself; the "workspace" multi-repo indirection
    collapses to one dir for this fixture, same convention `tests/test_release.py` uses). Places
    the manifest at the real `.foundry/releases/<id>/release.yaml` path. `journeys=False` strips
    both atoms' `journeys[]` (the "no journey suite" REFUSE fixture)."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_DIR, repo)
    manifest_path = repo / "release.yaml"
    with open(manifest_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not journeys:
        for atom in doc["atoms"]:
            atom.pop("journeys", None)
    rel_dir = repo / ".foundry" / "releases" / doc["id"]
    os.makedirs(rel_dir, exist_ok=True)
    with open(rel_dir / "release.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)
    os.remove(manifest_path)
    return repo, doc["id"]


def _seed_stack_profile(tmp_path, port):
    """A throwaway packs/tree + `.claude-plugin/plugin.json` (the SAME shape
    `tests/test_release.py`'s `_seed_verify_plugin_root` builds) carrying ONE synthetic `app`
    profile whose `app_exercise_binding.boot` serves the fixture repo over `port`. Returns the
    throwaway root (passed as BOTH `root=` and `plugin_root=` to `certify()`, exactly like
    `foundry-verify.py`'s own tests do — `certify_local.py` itself always imports the REAL
    `scripts/foundry_release.py`/`foundry-stack-profile.py` from this repo, never from this
    throwaway tree; only the packs/schema/plugin.json vary per test)."""
    root = tmp_path / "plugin"
    root.mkdir()
    shutil.copytree(os.path.join(REPO_ROOT, "schema"), root / "schema")
    (root / ".claude-plugin").mkdir()
    with open(root / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"name": "foundry", "version": "0.99.0"}, f)

    pack_dir = root / "packs" / "stack-profiles" / "certify-demo"
    pack_dir.mkdir(parents=True)
    (pack_dir / "conventions.md").write_text("conventions", encoding="utf-8")
    doc = {
        "id": "certify-demo", "version": "1.0.0", "requires_core": ">=0.1",
        "matches": {"languages": ["x"], "frameworks": ["x"], "package_managers": ["x"]},
        "architecture": {"layers": ["a"], "allowed_dependencies": [], "conventions_doc": "conventions.md"},
        "implementation_skills": ["conventions.md"],
        "static_validation": {"format": "true", "lint": "true", "typecheck": "true", "build": "true"},
        "test_recipe": {"unit": "true", "integration": "true", "e2e": "true", "coverage_gate": 0},
        "security_checklist": "n/a", "performance_checklist": "n/a",
        "observability": "n/a", "documentation": "n/a",
        "app_exercise_binding": {
            "boot": f"{sys.executable} -m http.server {port}",
            "surfaces": [{"kind": "ui", "exercise": "load index.html, follow the link to page2.html."}],
        },
    }
    with open(pack_dir / "stack-profile.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)
    return str(root)


def _write_lock(repo, plugin_root):
    csha = sp.content_sha256(os.path.join(plugin_root, "packs", "stack-profiles",
                                          "certify-demo", "stack-profile.yaml"))
    lock = {"profiles": [{"id": "certify-demo", "version": "1.0.0", "sha256": csha}]}
    os.makedirs(repo / ".foundry", exist_ok=True)
    with open(repo / ".foundry" / "stack-profile.lock", "w", encoding="utf-8") as f:
        json.dump(lock, f)


# ══════════════════════ bucket_per_atom — pure, no browser/network needed (always run) ════════ #
# PR #272 review BLOCK: a bare `tag in title` substring test misattributes a numeric-suffix
# collision (`AC-X-1` matching a title that only carries `AC-X-10`) — the exact silent-pass
# failure mode the SKILL promises never happens. These pin the boundary-aware fix directly
# against synthetic Playwright JSON-reporter spec dicts, with no boot/browser/network involved.

class TestBucketPerAtom:
    def test_numeric_suffix_collision_does_not_misattribute(self):
        """The Block: a title carrying ONLY the longer sibling tag `AC-X-10` must NOT satisfy the
        shorter tag `AC-X-1` — a bare substring test would have wrongly reported this a match."""
        specs = [{"title": "exercises AC-X-10 setup", "ok": True}]
        per_atom = {"atom1": ["AC-X-1"]}
        result = cl.bucket_per_atom(per_atom, specs)
        assert result["atom1"]["verdict"] == "fail"
        assert result["atom1"]["tags"]["AC-X-1"]["matched"] == 0

    def test_exact_tag_matches_correctly_alongside_its_longer_sibling(self):
        """Both tags present, side by side in one title: each must attribute to ONLY its own exact
        tag, never cross-matching the other."""
        specs = [{"title": "exercises AC-X-1 and AC-X-10", "ok": True}]
        per_atom = {"a1": ["AC-X-1"], "a10": ["AC-X-10"]}
        result = cl.bucket_per_atom(per_atom, specs)
        assert result["a1"]["verdict"] == "pass"
        assert result["a1"]["tags"]["AC-X-1"]["matched"] == 1
        assert result["a10"]["verdict"] == "pass"
        assert result["a10"]["tags"]["AC-X-10"]["matched"] == 1

    def test_zero_matching_titles_is_a_named_miss_never_a_pass(self):
        """A declared tag with ZERO matching titles must produce the named miss (fail), never a
        silent/vacuous pass."""
        specs = [{"title": "unrelated journey @OTHER-9", "ok": True}]
        per_atom = {"atom-a": ["NOPE-1"]}
        result = cl.bucket_per_atom(per_atom, specs)
        assert result["atom-a"]["verdict"] == "fail"
        assert result["atom-a"]["tags"]["NOPE-1"]["matched"] == 0
        assert "reason" in result["atom-a"]["tags"]["NOPE-1"]

    def test_a_failing_matched_test_fails_the_atom(self):
        specs = [{"title": "exercises AC-Y-1", "ok": False}]
        result = cl.bucket_per_atom({"atomy": ["AC-Y-1"]}, specs)
        assert result["atomy"]["verdict"] == "fail"
        assert result["atomy"]["tags"]["AC-Y-1"]["matched"] == 1
        assert result["atomy"]["tags"]["AC-Y-1"]["passed"] is False

    def test_no_declared_journeys_is_not_applicable_not_a_failure(self):
        result = cl.bucket_per_atom({"backend-atom": []}, [{"title": "anything", "ok": True}])
        assert result["backend-atom"]["verdict"] == "not-applicable"

    def test_grep_pattern_and_bucketing_share_the_same_tag_source(self):
        """`certify()`'s `--grep` pattern and `bucket_per_atom`'s title matching are BUILT from the
        SAME `_tag_source` — pin that a title only the union regex would select is exactly the
        title `_tag_matches` also accepts (no drift between grep-selection and bucketing)."""
        tags = ["AC-X-1", "AC-X-10"]
        grep_pattern = "|".join(cl._tag_source(t) for t in tags)
        import re as _re
        title = "exercises AC-X-1 and AC-X-10"
        assert _re.search(grep_pattern, title)
        assert cl._tag_matches("AC-X-1", title)
        assert cl._tag_matches("AC-X-10", title)
        assert not cl._tag_matches("AC-X-1", "exercises AC-X-10 only")


# ═══════════════════════════════════════════════════════════ REFUSE paths (always run) ════════ #

class TestCertifyLocalRefuse:
    def test_no_boot_recipe_no_lock_at_all(self, tmp_path):
        repo, rel_id = _seed_repo(tmp_path)                    # journeys present, no lock written
        plugin_root = _seed_stack_profile(tmp_path, _free_port())   # packs tree exists but unused
        with pytest.raises(cl.CertifyError, match="no boot recipe"):
            cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root)

    def test_no_boot_recipe_infra_profile_no_binding(self, tmp_path):
        """A resolved profile with NO `app_exercise_binding` (schema requires it for `app`-kind, so
        this simulates the residual: a profile dict lacking the key entirely, e.g. a future/odd
        loader state) still refuses named 'no boot recipe', never crashing on a missing key."""
        repo, rel_id = _seed_repo(tmp_path)
        assert cl.resolve_target_repo   # sanity: module loaded

        class _FakeProfile(dict):
            pass

        # exercise resolve_boot_recipe directly against a stubbed loader (no real lock needed) to
        # pin the "resolved-but-no-binding" branch independent of the full resolve_lock() path.
        fake_sp = type("FakeSP", (), {})()
        fake_sp.StackProfileError = sp.StackProfileError
        fake_sp.read_lock = lambda project_dir: {"profiles": [{"id": "x", "version": "1.0.0", "sha256": "0"}]}
        fake_sp.resolve_lock = lambda project_dir, root=None, plugin_root=None: [{"id": "x"}]  # no binding key
        orig = cl._load_stack_profile_module
        cl._load_stack_profile_module = lambda: fake_sp
        try:
            with pytest.raises(cl.CertifyError, match="no boot recipe"):
                cl.resolve_boot_recipe(str(repo), "unused-plugin-root")
        finally:
            cl._load_stack_profile_module = orig

    def test_no_journey_suite_no_atom_declares_journeys(self, tmp_path):
        repo, rel_id = _seed_repo(tmp_path, journeys=False)
        plugin_root = _seed_stack_profile(tmp_path, _free_port())
        _write_lock(repo, plugin_root)
        with pytest.raises(cl.CertifyError, match="no journey suite"):
            cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root)

    def test_no_journey_suite_no_playwright_config(self, tmp_path):
        repo, rel_id = _seed_repo(tmp_path)                    # journeys present
        os.remove(repo / "playwright.config.js")               # ...but no Playwright config
        plugin_root = _seed_stack_profile(tmp_path, _free_port())
        _write_lock(repo, plugin_root)
        with pytest.raises(cl.CertifyError, match="no journey suite"):
            cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root)

    def test_differing_target_repos_refused(self, tmp_path):
        """A release deploys as ONE unit — two atoms resolving to DIFFERING target repos REFUSES,
        naming the split, rather than silently booting whichever atom happened to be first."""
        repo, rel_id = _seed_repo(tmp_path)
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        os.makedirs(repo / ".claude", exist_ok=True)
        with open(repo / ".claude" / "foundry-project.json", "w", encoding="utf-8") as f:
            json.dump({"repos": {"other": {"path": str(other_repo)}}}, f)
        contract_path = repo / "specs" / "fix-b.yaml"
        contract_path.write_text("target_repo: other\n" + contract_path.read_text(encoding="utf-8"),
                                 encoding="utf-8")
        plugin_root = _seed_stack_profile(tmp_path, _free_port())
        _write_lock(repo, plugin_root)
        with pytest.raises(cl.CertifyError, match="DIFFERING target repos"):
            cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root)

    def test_journey_suite_check_precedes_boot_recipe_check(self, tmp_path):
        """No journeys AND no lock: the journey-suite precondition is checked first (cheaper,
        manifest-only) — the refusal names 'no journey suite', not 'no boot recipe'."""
        repo, rel_id = _seed_repo(tmp_path, journeys=False)     # no lock written either
        plugin_root = _seed_stack_profile(tmp_path, _free_port())
        with pytest.raises(cl.CertifyError, match="no journey suite"):
            cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root)

    def test_unknown_release_refused(self, tmp_path):
        repo, _rel_id = _seed_repo(tmp_path)
        with pytest.raises(cl.CertifyError, match="unknown/invalid release"):
            cl.certify("does-not-exist-xyz", project_dir=str(repo))


# ═══════════════════════════════════════════════════════════ acceptance-note verb, end-to-end ══ #

class TestAcceptanceNoteEndToEnd:
    def test_accept_after_certify_refusal_is_still_a_practice_record_not_a_gate(self, tmp_path):
        """The acceptance note is NEVER gated on certify-local having run/passed — it is a practice
        record the operator appends after their OWN judgement (CLAUDE.md 'Delivery sign-off').
        Recording acceptance works even though certify-local above would refuse this same release
        (no lock seeded here) — proving `accept` reads/writes the manifest independently."""
        repo, rel_id = _seed_repo(tmp_path)
        rel = release.append_acceptance(rel_id, "op_fixture", "accepted",
                                        "certified locally, journeys green",
                                        project_dir=str(repo), date="2026-07-27")
        assert rel.acceptance == [{"date": "2026-07-27", "operator": "op_fixture",
                                   "verdict": "accepted", "note": "certified locally, journeys green"}]

    def test_accept_cli_verb_end_to_end(self, tmp_path):
        repo, rel_id = _seed_repo(tmp_path)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(repo))
        release_cli = os.path.join(REPO_ROOT, "scripts", "foundry_release.py")
        p = subprocess.run([sys.executable, release_cli, "accept", rel_id, "--operator", "op_fixture",
                           "--verdict", "accepted", "--note", "fixture end-to-end"],
                          capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stdout + p.stderr
        reloaded = release.load_release(rel_id, project_dir=str(repo))
        assert len(reloaded.acceptance) == 1
        assert reloaded.acceptance[0] == {"date": reloaded.acceptance[0]["date"], "operator": "op_fixture",
                                          "verdict": "accepted", "note": "fixture end-to-end"}


# ═══════════════════════════════════════════════════════════ happy path (real Playwright run) ══ #

def _playwright_available():
    """Best-effort, TIME-BOUNDED check that `@playwright/test` + a browser are actually runnable in
    THIS environment — never assumed. Installs `@playwright/test@1.62.0` (pinned) into a throwaway
    npm project (network-dependent) and confirms THREE things, any failure -> unavailable, named
    in the skip reason:

      1. `npm install` succeeds (the package resolves at all).
      2. `npx playwright --version` succeeds (the CLI itself runs).
      3. a chromium browser BINARY actually exists on disk (CI finding, PR #272: `npx playwright
         --version` can succeed while NO browser is installed — `browserType.launch` then fails
         mid-test with "Executable doesn't exist" instead of this probe skipping cleanly). Checked
         via a bounded `node -e` probe against `playwright-core`'s OWN `chromium.executablePath()`
         (which already honors a `PLAYWRIGHT_BROWSERS_PATH` override) + `fs.existsSync` — never a
         real browser launch.

    The REFUSE/accept tests above already prove the certification machinery regardless of this
    probe's outcome, so a skip here never leaves this file's charter obligation unmet. Per the
    coordinator's addendum: this probe intentionally does NOT install a browser itself (keeps CI
    lean / unpinned-fetch-free) — the live browser proof ran at BUILD TIME locally and remains
    runnable wherever a chromium binary already exists; where it does not, this SKIPS, it never
    fakes a run."""
    if shutil.which("npm") is None or shutil.which("npx") is None or shutil.which("node") is None:
        return False, "npm/npx/node not on PATH"
    work = tempfile.mkdtemp(prefix="certify-local-pw-check-")
    try:
        subprocess.run(["npm", "init", "-y"], cwd=work, capture_output=True, text=True, timeout=30)
        install = subprocess.run(["npm", "install", "@playwright/test@1.62.0"],
                                 cwd=work, capture_output=True, text=True, timeout=120)
        if install.returncode != 0:
            return False, f"npm install @playwright/test@1.62.0 failed: {install.stderr[-400:]}"
        ver = subprocess.run(["npx", "--prefix", work, "playwright", "--version"],
                            cwd=work, capture_output=True, text=True, timeout=60)
        if ver.returncode != 0:
            return False, f"npx playwright --version failed: {ver.stderr[-400:]}"
        probe = subprocess.run(
            ["node", "-e",
             "const {chromium}=require('playwright-core');const fs=require('fs');"
             "const p=chromium.executablePath();process.stdout.write(p);"
             "process.exit(fs.existsSync(p)?0:1);"],
            cwd=work, capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            return False, (
                f"@playwright/test@1.62.0 installs and its CLI runs, but no chromium browser "
                f"binary is present ({probe.stdout.strip() or 'executablePath() unresolvable'}) — "
                f"run `npx playwright install chromium` in an environment with browsers; the live "
                f"browser proof for this fixture ran at build time locally and remains runnable "
                f"wherever a chromium binary already exists")
        return True, f"{ver.stdout.strip()}, chromium at {probe.stdout.strip()}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"playwright availability probe errored: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


_PW_OK, _PW_REASON = _playwright_available()


@pytest.mark.skipif(not _PW_OK, reason=f"@playwright/test not installable/runnable in this "
                                       f"environment ({_PW_REASON}) — REFUSE/accept tests above "
                                       f"still prove the certification machinery")
class TestCertifyLocalHappyPath:
    def test_certifies_green_end_to_end(self, tmp_path, monkeypatch):
        port = _free_port()
        monkeypatch.setenv("FIXTURE_PORT", str(port))   # playwright.config.js's baseURL <-> boot port
        repo, rel_id = _seed_repo(tmp_path)
        plugin_root = _seed_stack_profile(tmp_path, port)
        _write_lock(repo, plugin_root)

        # this repo's OWN throwaway node project (mirrors the availability probe above) so
        # `npx playwright` resolves `@playwright/test` from a REAL install — never faked.
        node_home = tmp_path / "node-home"
        node_home.mkdir()
        subprocess.run(["npm", "init", "-y"], cwd=str(node_home), capture_output=True, text=True, timeout=30)
        subprocess.run(["npm", "install", "@playwright/test@1.62.0"], cwd=str(node_home),
                       capture_output=True, text=True, timeout=120)
        shutil.copytree(node_home / "node_modules", repo / "node_modules")
        shutil.copy(node_home / "package.json", repo / "package.json")

        result = cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root,
                            boot_wait=1.5, playwright_timeout=120, npx_bin="npx",)
        assert result["verdict"] == "pass", result["playwright"]["stdout"] + result["playwright"]["stderr"]
        assert result["atoms"]["fix-a"]["verdict"] == "pass"
        assert result["atoms"]["fix-b"]["verdict"] == "pass"
        # Nit 8: a REAL, specific assertion on the runner's own JSON report — a spec titled with
        # `@FIX-1` actually ran and Playwright's own `ok` verdict for it is True (never a vacuous
        # "some evidence exists somewhere" check).
        assert result["playwright"]["report"] is not None
        specs = cl._all_specs(result["playwright"]["report"])
        fix1_spec = next((s for s in specs if "@FIX-1" in (s.get("title") or "")), None)
        assert fix1_spec is not None, specs
        assert fix1_spec.get("ok") is True, fix1_spec

    def test_certify_reports_fail_on_a_broken_journey(self, tmp_path, monkeypatch):
        """Break the SECOND page's element id so its journey's own assertion fails — proves the
        per-atom bucketing reports the runner's OWN failure, not a rubber-stamp pass."""
        port = _free_port()
        monkeypatch.setenv("FIXTURE_PORT", str(port))
        repo, rel_id = _seed_repo(tmp_path)
        (repo / "page2.html").write_text(
            "<!doctype html><html><body><h1 id='different'>broken</h1></body></html>",
            encoding="utf-8")
        plugin_root = _seed_stack_profile(tmp_path, port)
        _write_lock(repo, plugin_root)

        node_home = tmp_path / "node-home2"
        node_home.mkdir()
        subprocess.run(["npm", "init", "-y"], cwd=str(node_home), capture_output=True, text=True, timeout=30)
        subprocess.run(["npm", "install", "@playwright/test@1.62.0"], cwd=str(node_home),
                       capture_output=True, text=True, timeout=120)
        shutil.copytree(node_home / "node_modules", repo / "node_modules")
        shutil.copy(node_home / "package.json", repo / "package.json")

        result = cl.certify(rel_id, project_dir=str(repo), plugin_root=plugin_root, root=plugin_root,
                            boot_wait=1.5, playwright_timeout=120, npx_bin="npx")
        assert result["verdict"] == "fail"
        assert result["atoms"]["fix-a"]["verdict"] == "pass"
        assert result["atoms"]["fix-b"]["verdict"] == "fail"
