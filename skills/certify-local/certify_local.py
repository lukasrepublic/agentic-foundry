#!/usr/bin/env python3
"""skills/certify-local/certify_local.py — the `/foundry:certify-local <release>` driver (introduced
in the v0.25.0 certification realignment, CONSTITUTION.md §V 'factory' mode tail: spec -> plan ->
build -> integrate -> certify locally -> operator acceptance -> staging).

A THIN procedure over native primitives — no verdict engine, no custom evidence format:

  1. Resolve the release manifest (`scripts/foundry_release.py`, imported read-only).
  2. Resolve the boot recipe — the PROJECT's own `repos.<key>.boot_command`
     (`.claude/foundry-project.json`) wins when declared (feat-foundry-boot-recipe-precedence,
     AC-BRP-1); the active stack profile's `app_exercise_binding.boot` — via the SAME resolution
     `scripts/foundry-verify.py` uses (`scripts/foundry-stack-profile.py`'s `read_lock`/
     `resolve_lock`, imported read-only, never redefined here) — is the fallback (AC-BRP-2), never
     both.
  3. Boot the release's target repo ONCE (a single long-running subprocess, torn down at the end).
  4. Run the FULL tagged journey suite — every atom's `journeys[]` tags, unioned into ONE
     `--grep` regex — via PLAIN `npx playwright test --grep <pattern> --reporter=json` against
     that one running instance. No custom test runner, no re-implemented assertion engine.
  5. Report per-atom pass/fail as a GROUPING VIEW over Playwright's OWN per-test verdicts (which
     test titles carry which atom's tags) — never a re-judged/independent verdict — alongside the
     runner's raw stdout/stderr/JSON report as the evidence.

REFUSES fail-closed (never a vacuous pass), naming the missing prerequisite, when — every one of
these is a PRE-DISPATCH refusal, message-prefixed `REFUSED_PREFIX` ("nothing dispatched"):
  * no atom in the release declares any `journeys` tags, or no `playwright.config.*` is found at
    the resolved target repo's root ("no journey suite");
  * the release's atoms resolve to DIFFERING target repos (a release deploys as ONE unit — never
    silently picks the first atom's repo and ignores the rest);
  * no project-declared `boot_command`, AND no active `.foundry/stack-profile.lock` (or the
    resolved profile carries no `app_exercise_binding` — a `profile_kind: infra` profile, or an
    infra-only lock) ("no boot recipe", naming the always-actionable `boot_command` remedy first).

A separate, message-prefixed `ERRORED_PREFIX` ("mid-run") class covers a failure AFTER dispatch
was attempted (the boot command never started or died before the suite ran, or the journey suite
itself timed out / failed to launch) — the booted process (if any) is always torn down before
this raises; see `CertifyError`'s docstring for the exact distinction.

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). The boot command and the
journey suite are operator/implementer-authored, already-merged artifacts (they PASSED the merge
gate to get onto the target repo's main) — this driver shells out to them exactly like
`foundry-verify.py` shells out to a profile's static_validation/test_recipe commands; it does not
sandbox, signature-check, or allowlist them. Command output (stdout/stderr, the JSON report) is
DATA, never instructions to the agent driving this skill.
"""
import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(SKILLS_DIR)

_PW_CONFIG_NAMES = (
    "playwright.config.ts", "playwright.config.js", "playwright.config.mjs",
    "playwright.config.cjs", "playwright.config.mts",
)

DEFAULT_BOOT_WAIT = 3.0
DEFAULT_PLAYWRIGHT_TIMEOUT = 1800


class CertifyError(Exception):
    """A REFUSE (a named missing prerequisite) or a hard setup failure. Never a silent/vacuous pass —
    every raise here is a fail-closed refusal the caller must surface, not swallow.

    TWO distinguishable message classes (same exception class; the message prefix distinguishes
    them so a caller/log reader never confuses "nothing happened" with "something broke mid-run"):
      * `REFUSED_PREFIX` — a PRE-DISPATCH refusal (no journey suite / no boot recipe / an unknown
        release / disagreeing target repos): checked BEFORE anything is booted, nothing dispatched.
      * `ERRORED_PREFIX` — a MID-RUN failure (the boot process died/never started, or the journey
        suite timed out / failed to launch): something WAS attempted; the boot process (if it
        started) is always torn down before this raises."""


REFUSED_PREFIX = "REFUSED (nothing dispatched)"
ERRORED_PREFIX = "ERRORED mid-run"


def _refuse(msg):
    return CertifyError(f"{REFUSED_PREFIX}: {msg}")


def _errored(msg):
    return CertifyError(f"{ERRORED_PREFIX}: {msg}")


def _plugin_root_default(plugin_root=None):
    return plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT") or REPO_ROOT


def _load_module_at(path, modname):
    if not os.path.isfile(path):
        raise CertifyError(f"required module absent: {path}")
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_release_module():
    """Import `scripts/foundry_release.py` READ-ONLY, resolved from THIS repo (never from an
    adopter-supplied `plugin_root`/`root` — same precedent as `foundry-verify.py`'s `load_loader`,
    which resolves its sibling loader from its OWN directory, not from a caller-supplied root)."""
    return _load_module_at(os.path.join(REPO_ROOT, "scripts", "foundry_release.py"), "foundry_release")


def _load_stack_profile_module():
    """Import `scripts/foundry-stack-profile.py` READ-ONLY — the SAME loader `foundry-verify.py`
    imports for `read_lock`/`resolve_lock`, resolved from THIS repo. This driver is a dispatcher,
    not a definer: it never edits the loader and never re-implements profile resolution. `plugin_root`/
    `root` (see `resolve_boot_recipe`/`certify` below) are passed THROUGH to the loader's own
    `resolve_lock(project_dir, root=, plugin_root=)` — they select the adopter's packs/tree +
    `.claude-plugin/plugin.json`, never where the loader SCRIPT itself lives."""
    return _load_module_at(os.path.join(REPO_ROOT, "scripts", "foundry-stack-profile.py"),
                           "foundry_stack_profile")


def collect_journey_tags(release):
    """Per-atom `journeys[]` tags (declaration order, empty list when an atom declares none) + the
    SORTED union across every atom in the release (the regex source for the ONE `--grep` dispatch)."""
    per_atom = {a.id: list(a.journeys) for a in release.atoms}
    all_tags = sorted({t for tags in per_atom.values() for t in tags})
    return per_atom, all_tags


def _tag_source(tag):
    """The boundary-aware regex SOURCE for exactly one journey tag: the tag literal, anchored so
    it can never substring-match INSIDE a longer sibling tag that happens to share its prefix
    (e.g. tag `AC-X-1` must NOT match a title that only carries `AC-X-10` — the classic
    numeric-suffix collision). A tag is refused a match if immediately preceded OR followed by
    another `[A-Za-z0-9-]` character — `-` is treated as an identifier char here (unlike a plain
    `\\b` word boundary) because journey tags themselves routinely contain it.

    USED IDENTICALLY by BOTH: (a) `certify()`'s `--grep` pattern (the union of every tag's source,
    alternated) dispatched to Playwright, and (b) `bucket_per_atom`'s own title matching — built
    ONCE, from this one function, so grep-selection and result-bucketing can never disagree on
    what a tag matches."""
    return r"(?<![A-Za-z0-9-])" + re.escape(tag) + r"(?![A-Za-z0-9-])"


def _tag_matches(tag, title):
    return re.search(_tag_source(tag), title or "") is not None


def find_playwright_config(repo_root):
    """The first `playwright.config.*` found at the repo root, else None. Presence (not content) is
    the "a journey suite exists to run" precondition — the suite itself is the implementer's own,
    already-merged artifact; this driver never inspects/validates its content."""
    for name in _PW_CONFIG_NAMES:
        p = os.path.join(repo_root, name)
        if os.path.isfile(p):
            return p
    return None


def resolve_target_repo(release, project_dir, release_mod):
    """The release's single target repo, resolved via `foundry_release._resolve_repo` (the SAME
    multi-repo pin every other release primitive uses) for EVERY atom, then asserted to AGREE. A
    release is deploy-as-a-unit (one boot, one running instance): certify-local cannot boot more
    than one repo, so a release whose atoms resolve to DIFFERING target repos REFUSES, naming the
    split, rather than silently certifying against whichever atom happened to be first. Compares
    the RESOLVED (realpath'd) directory, not the raw `target_repo` config string, so one atom
    omitting `target_repo` (self-host default) and a sibling spelling it out explicitly as
    `workspace` — both of which resolve to the same directory — are correctly treated as
    agreeing.

    Returns `(repo_root, target_repo)` — the resolved directory AND the first atom's raw
    `target_repo` config value (None/falsy, "workspace", or an explicit `repos{}` key), used by
    `resolve_effective_boot_recipe` (AC-BRP-7) to name which `repos{}` key the project-declared
    boot recipe is looked up under. Safe to take from the first atom: every atom above was already
    asserted to resolve to the SAME directory, so any one atom's `target_repo` names an equivalent
    venue for the boot-recipe lookup too."""
    resolved = []
    for atom in release.atoms:
        target_repo, _csha = release_mod._contract_info(atom, project_dir)
        resolved.append((atom.id, target_repo, release_mod._resolve_repo(target_repo, project_dir)))
    distinct_paths = {os.path.realpath(p) for _aid, _tr, p in resolved}
    if len(distinct_paths) > 1:
        detail = ", ".join(f"{aid} -> {p!r}" for aid, _tr, p in resolved)
        raise _refuse(f"release atoms resolve to DIFFERING target repos — a release deploys as "
                     f"ONE unit, certify-local cannot boot more than one: {detail}")
    return resolved[0][2], resolved[0][1]


def resolve_boot_recipe(project_dir, plugin_root, root=None):
    """Resolve the ACTIVE stack profile's `app_exercise_binding.boot` — fail-closed, naming "no boot
    recipe" for every one of: no lock, an unresolvable lock, zero/ambiguous resolved profiles, or a
    resolved profile with no `app_exercise_binding` (e.g. `profile_kind: infra`)."""
    sp = _load_stack_profile_module()
    try:
        lock = sp.read_lock(project_dir)
    except sp.StackProfileError as e:
        raise _refuse(f"no boot recipe: stack-profile.lock unreadable/malformed: {e}")
    if lock is None:
        raise _refuse("no boot recipe: no active .foundry/stack-profile.lock (no active stack profile)")
    try:
        resolved = sp.resolve_lock(project_dir, root=root or plugin_root, plugin_root=plugin_root)
    except sp.StackProfileError as e:
        raise _refuse(f"no boot recipe: active stack-profile.lock does not resolve: {e}")
    if len(resolved) != 1:
        raise _refuse(f"no boot recipe: stack-profile.lock resolves to {len(resolved)} profile(s) "
                     f"(exactly 1 required, fail-closed on 0 or >1 — see foundry-verify.py's "
                     f"identical rule)")
    profile = resolved[0]
    binding = profile.get("app_exercise_binding")
    if not binding or not binding.get("boot"):
        raise _refuse(f"no boot recipe: resolved profile {profile.get('id')!r} declares no "
                     f"app_exercise_binding.boot (likely profile_kind: infra — nothing to boot)")
    return profile, binding


# ── boot-recipe precedence (feat-foundry-boot-recipe-precedence, AC-BRP-1..8) ──────────────────────
#
# The project's OWN `repos.<key>.boot_command` (`.claude/foundry-project.json`) wins over the
# active stack profile's `app_exercise_binding.boot` — the Playwright `webServer.command` shape
# (a project-level setting; a shared preset composes IN, it never replaces it). `resolve_boot_recipe`
# above is UNTOUCHED — it stays the stack-profile-only fallback primitive, exactly as
# `tests/test_python_stack_profile.py` exercises it today (AC-BRP-2's regression floor). Everything
# below is ADDITIVE: a project-declaration reader + a repos{} key-selection rule mirroring
# `foundry_release._resolve_repo`'s own three venue-resolution paths (AC-BRP-7), composed into one
# precedence-aware resolver that only `certify()` calls.

def _read_foundry_project_manifest(project_dir):
    """Read `.claude/foundry-project.json`, project_dir-anchored (the SAME path
    `foundry_release._resolve_repo` / `foundry_project_tracking.read_config` use). Returns
    `(doc, malformed)`:
      * the file is absent -> `({}, False)` — "no project declaration", not a defect.
      * the file parses to a JSON object -> `(doc, False)`.
      * the file exists but is unreadable, not valid JSON, or its root is not an object ->
        `({}, True)` — AC-BRP-4's "malformed manifest" case, reported on its own line by callers
        (never silently folded into the ordinary "declared nothing" case)."""
    path = os.path.join(project_dir, ".claude", "foundry-project.json")
    if not os.path.isfile(path):
        return {}, False
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}, True
    if not isinstance(doc, dict):
        return {}, True
    return doc, False


def _project_repos_key(target_repo, project_dir):
    """AC-BRP-7: which `repos{}` key the project-declared boot recipe is looked up under, mirroring
    `foundry_release._resolve_repo`'s THREE venue-resolution paths (never its path-resolution —
    only which key each path implies):

      1. `target_repo == "workspace"` (the merge-gate sentinel) -> the literal key `"workspace"`.
         `_resolve_repo` returns the project dir here WITHOUT any `repos{}` lookup; the boot-recipe
         lookup still consults `repos.workspace.boot_command`, and its absence is the ordinary
         AC-BRP-2 fall-through, not an error.
      2. `target_repo` falsy (the self-host default) -> `self_host_code_repo` when the manifest
         declares one (itself read from the SAME manifest), else `"workspace"`.
      3. `target_repo` names a key -> that key (AC-BRP-1).

    Never raises: a malformed manifest here just means `self_host_code_repo` can't be read, so
    this degrades to `"workspace"` — the CALLER's own manifest read (for the boot_command lookup
    itself, `resolve_project_boot_command` below) is what actually reports the malformed-manifest
    case on its own line (AC-BRP-4/5)."""
    if target_repo == "workspace":
        return "workspace"
    if not target_repo:
        doc, malformed = _read_foundry_project_manifest(project_dir)
        if not malformed:
            shcr = doc.get("self_host_code_repo")
            if isinstance(shcr, str) and shcr.strip():
                return shcr
        return "workspace"
    return target_repo


def resolve_project_boot_command(project_dir, repos_key):
    """AC-BRP-1/2/4: the project-declared `boot_command` for `repos_key` in
    `.claude/foundry-project.json`, executed VERBATIM — no interpolation/templating/env-injection
    is ever applied to it here (AC-BRP-6). Returns `(boot_command_or_None, provenance)` where
    `provenance` is one of:

      `"project"`             — a non-empty (non-whitespace-only) `boot_command` was declared.
      `"manifest-empty"`      — the manifest read fine; `repos_key` is absent, has no
                                `boot_command`, or its value is empty/whitespace-only (AC-BRP-2).
      `"manifest-malformed"`  — the manifest exists but is unreadable/unparsable/not-an-object
                                (AC-BRP-4) — degrades to "no project-declared recipe" but is
                                reported distinctly by the caller (AC-BRP-5).

    Never raises."""
    doc, malformed = _read_foundry_project_manifest(project_dir)
    if malformed:
        return None, "manifest-malformed"
    repos = doc.get("repos")
    entry = repos.get(repos_key) if isinstance(repos, dict) else None
    if isinstance(entry, dict):
        bc = entry.get("boot_command")
        if isinstance(bc, str) and bc.strip():
            return bc, "project"
    return None, "manifest-empty"


def _lock_already_exists(project_dir):
    """AC-BRP-3: whether `.foundry/stack-profile.lock` is present on disk (existence only, not
    validity) — the precondition for "activating a different profile" being a reachable remedy at
    all. Never raises; an unreadable stack-profile module is treated as "no lock" (the refusal then
    names only the always-actionable primary remedy)."""
    try:
        sp = _load_stack_profile_module()
        return os.path.isfile(sp.lock_path(project_dir))
    except CertifyError:
        return False


def _no_recipe_message(repos_key, project_dir):
    """AC-BRP-3's refusal text: the PRIMARY remedy — declare `repos.<repos_key>.boot_command` — is
    always named, since it is the one an adopter can always act on. The profile remedy is named
    ONLY when a `.foundry/stack-profile.lock` already exists (i.e. when "activate a different
    profile" is actually reachable — until the sibling atom `feat-foundry-stack-profile-lock-create`
    ships, no code path CREATES that lock, so naming that remedy unconditionally would be a
    dead-end pointer)."""
    msg = (f"no boot recipe: declare `repos.{repos_key}.boot_command` in "
          f".claude/foundry-project.json (primary remedy — {repos_key!r} is the venue this "
          f"release resolves to)")
    if _lock_already_exists(project_dir):
        msg += ("; alternatively, activate a different stack profile — an active "
               ".foundry/stack-profile.lock already exists")
    return msg


def resolve_effective_boot_recipe(project_dir, plugin_root, target_repo, root=None):
    """AC-BRP-1..5: the boot-recipe precedence resolver. The project's own declaration wins
    (AC-BRP-1); the active stack profile is consulted ONLY when the project declares nothing
    usable (AC-BRP-2, `resolve_boot_recipe` above is called UNCHANGED — the regression floor);
    neither source yielding a recipe REFUSES (AC-BRP-3), never a vacuous pass.

    Returns `(boot_command, provenance, profile_or_None, repos_key)` where `provenance` names
    which of AC-BRP-5's first three outcomes won:

      `"project"`                      (a) the project manifest supplied it.
      `"profile"`                      (b) the manifest declared nothing; the profile supplied it.
      `"profile (manifest malformed)"` (c) the manifest was unreadable/malformed; the profile
                                            supplied it — DISTINCT from (b) so a typo'd manifest is
                                            never indistinguishable from an absent declaration.

    Raises `CertifyError` (AC-BRP-3, outcome (d)) when neither source yields a recipe."""
    repos_key = _project_repos_key(target_repo, project_dir)
    boot_command, prov = resolve_project_boot_command(project_dir, repos_key)
    if boot_command is not None:
        return boot_command, "project", None, repos_key

    try:
        profile, binding = resolve_boot_recipe(project_dir, plugin_root, root=root)
    except CertifyError:
        raise _refuse(_no_recipe_message(repos_key, project_dir))

    source = "profile (manifest malformed)" if prov == "manifest-malformed" else "profile"
    return binding["boot"], source, profile, repos_key


def _iter_specs(node):
    for spec in node.get("specs", []) or []:
        yield spec
    for sub in node.get("suites", []) or []:
        yield from _iter_specs(sub)


def _all_specs(report):
    specs = []
    for suite in report.get("suites", []) or []:
        specs.extend(_iter_specs(suite))
    return specs


def bucket_per_atom(per_atom_journeys, specs):
    """A GROUPING VIEW over Playwright's own per-spec `ok` verdict — never a re-judged verdict. For
    each atom's declared tags, find every spec whose TITLE matches that tag via the SAME
    boundary-aware `_tag_source`/`_tag_matches` the `--grep` dispatch itself uses (never a bare
    substring test — `AC-X-1` must never misattribute a title that only carries `AC-X-10`) and
    adopt Playwright's own `ok` boolean; a tag with zero matching titles is a named miss (declared
    coverage that was never written), not silently ignored. An atom with no declared journeys is
    `not-applicable` (not a failure — a non-UI/pure-backend atom, per
    `context/feat-spec-template.md`'s Journeys section)."""
    results = {}
    for atom_id, tags in per_atom_journeys.items():
        if not tags:
            results[atom_id] = {"verdict": "not-applicable", "tags": {}}
            continue
        tag_results = {}
        atom_pass = True
        for tag in tags:
            matched = [s for s in specs if _tag_matches(tag, s.get("title"))]
            if not matched:
                tag_results[tag] = {"matched": 0, "passed": False,
                                    "reason": "no Playwright test title contains this tag"}
                atom_pass = False
            else:
                ok = all(bool(s.get("ok")) for s in matched)
                tag_results[tag] = {"matched": len(matched), "passed": ok,
                                    "titles": [s.get("title") for s in matched]}
                atom_pass = atom_pass and ok
        results[atom_id] = {"verdict": "pass" if atom_pass else "fail", "tags": tag_results}
    return results


def _terminate(proc):
    """Tear down the ONE booted instance: SIGTERM the process group, SIGKILL after a grace period.
    Never leaves a booted dev server running past this call."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def certify(release_id, *, project_dir=None, plugin_root=None, root=None,
           boot_wait=DEFAULT_BOOT_WAIT, playwright_timeout=DEFAULT_PLAYWRIGHT_TIMEOUT,
           npx_bin="npx"):
    """Deploy the release ONCE, run the full tagged journey suite against it, return the report dict:

        {"verdict": "pass"|"fail", "release_id", "profile_id", "repo_root", "grep_pattern",
         "boot_recipe": {"command", "provenance", "repos_key"},
         "playwright": {"returncode", "stdout", "stderr", "report": <raw JSON reporter doc or None>},
         "atoms": {<atom_id>: {"verdict": "pass"|"fail"|"not-applicable", "tags": {...}}}}

    `boot_recipe.provenance` (AC-BRP-5) is one of `"project"` (the project's own
    `repos.<key>.boot_command` won), `"profile"` (the manifest declared nothing, the active stack
    profile supplied it), or `"profile (manifest malformed)"` (the manifest was unreadable/
    malformed, the profile supplied it) — kept distinguishable so a typo'd manifest is never
    reported the same as an absent declaration.

    Raises `CertifyError` (a fail-closed REFUSE, never a vacuous pass) naming the missing
    prerequisite when there is no journey suite or no boot recipe — checked BEFORE anything boots."""
    pd = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    pr = _plugin_root_default(plugin_root)
    release_mod = _load_release_module()

    try:
        release = release_mod.load_release(release_id, project_dir=pd)
    except release_mod.ReleaseError as e:
        raise _refuse(f"unknown/invalid release {release_id!r}: {e}")

    per_atom_journeys, all_tags = collect_journey_tags(release)
    if not all_tags:
        raise _refuse(f"no journey suite: release {release_id!r} declares no atom `journeys` "
                     f"tags — nothing to certify (see context/feat-spec-template.md's "
                     f"Journeys section)")

    repo_root, target_repo = resolve_target_repo(release, pd, release_mod)  # raises _refuse on a repo split
    pw_config = find_playwright_config(repo_root)
    if pw_config is None:
        raise _refuse(f"no journey suite: no playwright.config.* found under {repo_root!r} — "
                     f"journeys are declared but no Playwright suite exists to run them")

    # AC-BRP-1..5: the project's own `boot_command` wins; the active stack profile is the fallback.
    # Raises _refuse: "no boot recipe: …" when neither source yields a recipe (AC-BRP-3).
    boot_command, boot_provenance, profile, repos_key = resolve_effective_boot_recipe(
        pd, pr, target_repo, root=root)

    # ONE boundary-aware regex per tag (`_tag_source`), alternated — the SAME source
    # `bucket_per_atom` matches titles against below, so grep-selection and result-bucketing can
    # never disagree on what a tag matches.
    grep_pattern = "|".join(_tag_source(t) for t in all_tags)

    try:
        boot_proc = subprocess.Popen(boot_command, shell=True, cwd=repo_root,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, start_new_session=True)
    except OSError as e:
        # the boot command never started at all — still a mid-run-class failure (dispatch WAS
        # attempted), just not the "booted then torn down" sub-case the shared prefix otherwise
        # describes; say so precisely rather than reusing that phrase inaccurately.
        raise CertifyError(f"{ERRORED_PREFIX} (boot command never started): "
                           f"boot command failed to launch: {e}")

    report_fd, report_path = tempfile.mkstemp(prefix="certify-local-", suffix=".json")
    os.close(report_fd)
    proc = None
    raw_report = None
    try:
        try:
            time.sleep(boot_wait)
            if boot_proc.poll() is not None:
                # died between launch and the journey suite dispatch — a clearer diagnosis than
                # letting every single journey fail with a generic connection-refused error.
                raise _errored(f"boot command exited before the journey suite ran "
                               f"(exit code {boot_proc.returncode})")
            env = dict(os.environ, PLAYWRIGHT_JSON_OUTPUT_NAME=report_path)
            try:
                proc = subprocess.run(
                    [npx_bin, "playwright", "test", "--grep", grep_pattern, "--reporter=json"],
                    cwd=repo_root, capture_output=True, text=True, timeout=playwright_timeout, env=env)
            except subprocess.TimeoutExpired as e:
                raise _errored(f"journey suite timed out after {playwright_timeout}s: {e}")
            except OSError as e:
                raise _errored(f"could not launch the journey suite ({npx_bin} playwright): {e}")
        finally:
            _terminate(boot_proc)

        if os.path.isfile(report_path):
            try:
                with open(report_path, encoding="utf-8") as f:
                    raw_report = json.load(f)
            except (OSError, ValueError):
                raw_report = None
    finally:
        # ALWAYS unlink the report tempfile — including the timeout/launch-failure/dead-boot
        # paths above, which raise BEFORE the (now-removed) inline cleanup used to run.
        try:
            os.unlink(report_path)
        except OSError:
            pass

    specs = _all_specs(raw_report) if isinstance(raw_report, dict) else []
    atoms = bucket_per_atom(per_atom_journeys, specs)
    overall = "pass" if all(a["verdict"] != "fail" for a in atoms.values()) and proc.returncode == 0 \
        else "fail"

    return {
        "verdict": overall,
        "release_id": release.id,
        "profile_id": profile.get("id") if profile is not None else None,
        "repo_root": repo_root,
        "grep_pattern": grep_pattern,
        "boot_recipe": {"command": boot_command, "provenance": boot_provenance, "repos_key": repos_key},
        "playwright": {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
                      "report": raw_report},
        "atoms": atoms,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="/foundry:certify-local — deploy a release ONCE, run its full tagged journey "
                    "suite, report per-atom pass/fail with the runner's own output as evidence.")
    ap.add_argument("release_id")
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--plugin-root", default=None)
    ap.add_argument("--boot-wait", type=float, default=DEFAULT_BOOT_WAIT)
    ap.add_argument("--timeout", type=int, default=DEFAULT_PLAYWRIGHT_TIMEOUT)
    ap.add_argument("--npx", default="npx")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON")
    args = ap.parse_args(argv)
    try:
        result = certify(args.release_id, project_dir=args.project_dir, plugin_root=args.plugin_root,
                         boot_wait=args.boot_wait, playwright_timeout=args.timeout, npx_bin=args.npx)
    except CertifyError as e:
        # the message itself already carries the REFUSED/ERRORED distinction (see `CertifyError`'s
        # docstring) — no additional wrapper prefix needed here.
        print(f"certify-local: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"certify-local: {result['verdict'].upper()} — release {result['release_id']!r} "
              f"(profile {result['profile_id']!r}, repo {result['repo_root']!r})")
        br = result["boot_recipe"]
        print(f"  boot recipe: {br['command']!r} (source: {br['provenance']}, "
              f"repos key: {br['repos_key']!r})")
        for atom_id, r in result["atoms"].items():
            print(f"  [{r['verdict']}] {atom_id}")
        print("\n--- playwright output (evidence) ---")
        print(result["playwright"]["stdout"])
        if result["playwright"]["stderr"]:
            print(result["playwright"]["stderr"], file=sys.stderr)
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
