"""feat-foundry-tag-pin-coherence — the tag ref must serve an install pin that resolves to the release.

Adopters install by ref (`marketplace add <repo>#vX.Y.Z`), which resolves marketplace.json AT THE
TAG and installs the commit its source.sha names. The emitted publish plan tagged at the release
commit and re-pinned source.sha in a LATER commit, so the tag carried the PREVIOUS release's sha
and an install by ref delivered the previous version's code. That shipped twice — v1.0.0 and
v1.0.1 — and was hand-corrected both times.

These tests build real git fixture repos and drive the shipped `tag_pin_coherence` check over
them: a coherent cut, the defective ordering, a tag-object pin, an out-of-history pin, and the
first-cut (no tag yet) case.
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUT = os.path.join(REPO_ROOT, "scripts", "foundry-cut-release.py")

_spec = importlib.util.spec_from_file_location("_cutrelease", CUT)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def _write_manifests(repo, version, sha="0" * 40, source_repo=None):
    """`source_repo`, when given, populates `source.repo` -- the field the ssh-alias-resolution
    cross-check exercises. Omitted by default, matching every pre-existing fixture."""
    os.makedirs(os.path.join(repo, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(repo, ".claude-plugin", "plugin.json"), "w") as f:
        json.dump({"name": "foundry", "version": version}, f)
    source = {"ref": f"v{version}", "sha": sha}
    if source_repo is not None:
        source["repo"] = source_repo
    with open(os.path.join(repo, ".claude-plugin", "marketplace.json"), "w") as f:
        json.dump({"plugins": [{"name": "foundry", "source": source}]}, f)


@pytest.fixture
def repo(tmp_path):
    """A git repo with a v1.0.0 release already cut coherently."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    # --- v1.0.0: content commit, then re-pin, then tag on the re-pin commit (the CORRECT order)
    _write_manifests(str(r), "1.0.0")
    git(r, "add", "-A"); git(r, "commit", "-qm", "v1.0.0 content")
    v100_content = git(r, "rev-parse", "HEAD")
    _write_manifests(str(r), "1.0.0", sha=v100_content)
    git(r, "add", "-A"); git(r, "commit", "-qm", "re-pin v1.0.0")
    git(r, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    return r


def cut_v101(repo, order):
    """Cut v1.0.1 in the given order. `correct` = re-pin then tag; `defective` = tag then re-pin."""
    _write_manifests(str(repo), "1.0.1", sha=git(repo, "rev-parse", "v1.0.0^{commit}"))
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 content")
    content = git(repo, "rev-parse", "HEAD")
    if order == "defective":
        git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")           # tag FIRST — the bug
        _write_manifests(str(repo), "1.0.1", sha=content)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin v1.0.1")
    else:
        _write_manifests(str(repo), "1.0.1", sha=content)          # re-pin FIRST — the fix
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin v1.0.1")
        git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    return content


def cut_v101_with_source_repo(repo, source_repo):
    """A CORRECTLY-ordered v1.0.1 cut whose marketplace.json carries `source.repo` -- the shape the
    ssh-alias-resolution cross-check exercises. The pre-existing `repo` fixture's manifests never set
    `source.repo` (grep-verified 2026-08-02: the cross-check has zero coverage on the base tree)."""
    _write_manifests(str(repo), "1.0.1", sha=git(repo, "rev-parse", "v1.0.0^{commit}"),
                     source_repo=source_repo)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 content")
    content = git(repo, "rev-parse", "HEAD")
    _write_manifests(str(repo), "1.0.1", sha=content, source_repo=source_repo)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin v1.0.1")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    return content


# --------------------------------------------------------------------------- AC-TPC-2 / AC-TPC-7
def test_stale_pin_is_gated(repo):
    """THE defect. Tagging before the re-pin leaves the tag serving the PREVIOUS release's sha, so
    an install by ref delivers the previous version's code."""
    cut_v101(repo, "defective")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok, f"an incoherent install pin passed the gate: {detail}"


def test_refusal_names_both_values(repo):
    """AC-TPC-7 — the message must name the sha the tag serves AND what it actually resolves to,
    so the operator sees the defect rather than a bare failure."""
    cut_v101(repo, "defective")
    _ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert "1.0.0" in detail and "1.0.1" in detail, f"refusal names neither version: {detail}"
    assert "INCOHERENT" in detail.upper()


# --------------------------------------------------------------------------- AC-TPC-1 / AC-TPC-3
def test_coherent_pin_is_ready(repo):
    """No over-block: a correctly ordered cut passes."""
    cut_v101(repo, "correct")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert ok, f"a coherent cut was refused: {detail}"


def _step(plan, pred):
    return next((n for n, s in enumerate(plan) if pred(s)), -1)


def test_emitted_plan_order_yields_coherence(repo):
    """AC-TPC-3 — following the EMITTED plan verbatim must produce a coherent tag.

    Asserted on the index of the EXECUTABLE STEPS, never on substring positions in the joined text.
    The first version of this test did `plan.index("source.sha") < plan.index("tag -a")`, which
    matched "source.sha" inside the human-readable `# edit …` COMMENT that sits above the commit.
    Swapping the real `git commit` and `git tag -a` steps — the literal v1.0.0/v1.0.1 defect — left
    that comment untouched and the assertion still passed. It proved nothing.
    """
    plan = cr.publish_plan(str(repo), "1.0.1")
    repin_at = _step(plan, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    tag_at = _step(plan, lambda s: s.startswith("git ") and " tag -a v1.0.1" in s)
    assert repin_at >= 0 and tag_at >= 0, f"plan is missing a re-pin or tag step:\n{plan}"
    assert repin_at < tag_at, (
        "the emitted plan still tags BEFORE re-pinning — following it verbatim reproduces the "
        f"stale-pin defect:\n{plan}")


def test_emitted_plan_order_assertion_is_not_vacuous(repo):
    """The meta-test: the order assertion above must FAIL on a plan with the steps swapped.

    Without this, a future refactor could reintroduce a substring-position check and nothing would
    notice that the guard stopped guarding."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    repin_at = _step(plan, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    tag_at = _step(plan, lambda s: s.startswith("git ") and " tag -a v1.0.1" in s)
    swapped = list(plan)
    swapped[repin_at], swapped[tag_at] = swapped[tag_at], swapped[repin_at]
    r2 = _step(swapped, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    t2 = _step(swapped, lambda s: s.startswith("git ") and " tag -a v1.0.1" in s)
    assert not (r2 < t2), "the order assertion does not actually detect a swapped plan"


def test_repin_step_is_path_scoped(repo):
    """The reorder puts the tag on R2 — a commit created AFTER the acceptance gate ran. A bare
    `commit -am` would sweep every modified tracked file into it and publish that under the release
    tag, ungated. The re-pin must commit ONLY the manifest."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    repin_at = _step(plan, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    step = plan[repin_at]
    assert "commit -am" not in step, f"the re-pin step sweeps the whole working tree: {step}"
    assert step.split("#")[0].rstrip().endswith(".claude-plugin/marketplace.json"), (
        f"the re-pin step is not path-scoped to the manifest: {step}")


def test_plan_verifies_the_tag_before_pushing(repo):
    """AC-TPC-2 has no caller on the cut that CREATES the tag unless the plan makes one. The machine
    re-check must sit after the tag step and before either push."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    tag_at = _step(plan, lambda s: " tag -a v1.0.1" in s)
    verify_at = _step(plan, lambda s: "--verify-tag" in s)
    push_at = _step(plan, lambda s: "push origin" in s)
    assert tag_at < verify_at < push_at, (
        f"the plan does not machine-verify the tag between tagging and pushing:\n{plan}")


def test_first_cut_with_no_tag_is_not_applicable(repo):
    """Before the tag exists the tree necessarily carries the previous sha; that is not a defect."""
    ok, detail = cr.tag_pin_coherence(str(repo), "9.9.9")
    assert ok and "not applicable" in detail


# ------------------------------------------------------------------- FAIL CLOSED WHEN UNCHECKABLE
def test_non_git_tree_refuses_rather_than_reporting_not_applicable(tmp_path):
    """THE FAIL-OPEN. `git rev-parse --verify refs/tags/X` exits non-zero for 'not a git
    repository', 'dubious ownership' (safe.directory — routine in CI containers and any checkout
    owned by another uid), a corrupt object store and permission errors — all indistinguishable
    from 'the tag is absent'. Reading those as not-applicable let a tree the gate could not inspect
    sail through to READY."""
    plain = tmp_path / "notarepo"
    plain.mkdir()
    _write_manifests(str(plain), "1.0.1")
    ok, detail = cr.tag_pin_coherence(str(plain), "1.0.1")
    assert not ok, f"a non-git tree reported OK: {detail}"
    assert "not a readable git repository" in detail


def test_dirty_tree_refuses(repo):
    """The tag now lands on R2, created AFTER the acceptance verdict. Anything left uncommitted
    would be swept into it by the re-pin step and published under the release tag ungated."""
    assert cr.worktree_clean(str(repo))[0], "fixture should start clean"
    with open(os.path.join(repo, "stray.txt"), "w") as f:
        f.write("uncommitted")
    git(repo, "add", "-A")
    ok, detail = cr.worktree_clean(str(repo))
    assert not ok and "stray.txt" in detail, detail


def test_worktree_clean_fails_closed_off_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    ok, detail = cr.worktree_clean(str(plain))
    assert not ok and "cannot check the working tree" in detail


# ------------------------------------------------------------------- THE PIN MUST BE AN IMMUTABLE ID
@pytest.mark.parametrize("bad", ["main", "HEAD", "v1.0.0", "abc1234", "0" * 39, "A" * 40])
def test_sha_must_be_full_lowercase_hex(repo, bad):
    """git resolves arbitrary revision expressions, so an unvalidated `source.sha` of "main" types
    as a commit and passes every downstream check — while being a MUTABLE pin whose resolution
    changes as the branch moves. That destroys the immutable-artifact property AC-TPC-1 asserts."""
    cut_v101(repo, "correct")
    git(repo, "tag", "-d", "v1.0.1")
    _write_manifests(str(repo), "1.0.1", sha=bad)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "bad pin: not a full hex id")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok, f"a non-immutable pin {bad!r} was accepted: {detail}"
    assert "40-character lowercase hex" in detail


def test_distant_ancestor_pin_is_refused(repo):
    """AC-TPC-5 tightened. `merge-base --is-ancestor` admits EVERY commit reachable from the tag.
    The version bump lands at the START of release work, so several ancestors carry the target
    version — an ancestor-plus-version check would admit a pin naming the version-bump commit and
    silently omit everything committed after it. Same wrong-code-delivered outcome, displaced by a
    few commits. The pin must be the tag commit or its first parent."""
    _write_manifests(str(repo), "1.0.1", sha=git(repo, "rev-parse", "v1.0.0^{commit}"))
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 bump (carries the target version)")
    early_bump = git(repo, "rev-parse", "HEAD")
    with open(os.path.join(repo, "feature.txt"), "w") as f:
        f.write("the code that would be omitted")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 content")
    _write_manifests(str(repo), "1.0.1", sha=early_bump)      # pin the BUMP, not the content
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin (to the wrong, earlier commit)")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok, f"a distant ancestor pin passed — feature.txt would be omitted: {detail}"
    assert "tag commit" in detail and "first parent" in detail


def test_named_plugin_entry_is_used_not_index_zero(repo):
    """The gate must select the 'foundry' entry BY NAME. Indexing [0] silently grades a different
    plugin's pin the moment the marketplace lists a second plugin first."""
    cut_v101(repo, "correct")
    git(repo, "tag", "-d", "v1.0.1")
    # the two-plugin manifest lands as a NEW commit, which becomes the tag commit — so the pin must
    # name the commit that will be its first parent, i.e. today's HEAD.
    content = git(repo, "rev-parse", "HEAD")
    with open(os.path.join(repo, ".claude-plugin", "marketplace.json"), "w") as f:
        json.dump({"plugins": [
            {"name": "other", "source": {"ref": "v9.9.9", "sha": "1" * 40}},   # FIRST, and wrong
            {"name": "foundry", "source": {"ref": "v1.0.1", "sha": content}},
        ]}, f)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "two-plugin manifest")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert ok, f"the gate graded the wrong plugins[] entry: {detail}"


def test_verify_tag_cli_convicts_and_exits_nonzero(repo):
    """AC-TPC-2's caller. During a first cut the tag does not exist, so the check is a no-op — the
    post-tag CLI is the point at which it stops being one."""
    cut_v101(repo, "defective")
    r = subprocess.run(["python3", CUT, "--tree", str(repo), "--version", "1.0.1", "--verify-tag"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "TAG-PIN-INCOHERENT" in r.stdout
    git(repo, "tag", "-d", "v1.0.1")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")           # move the tag onto the re-pin commit
    r = subprocess.run(["python3", CUT, "--tree", str(repo), "--version", "1.0.1", "--verify-tag"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "TAG-PIN-COHERENT" in r.stdout, r.stdout + r.stderr


# --------------------------------------------------------------------------- AC-TPC-4 / AC-TPC-5
def test_tag_object_pin_is_refused(repo):
    """source.sha must name a COMMIT. An annotated-tag object is the documented trap."""
    cut_v101(repo, "correct")
    tag_obj = git(repo, "rev-parse", "v1.0.0")            # the annotated-tag object, not ^{commit}
    if tag_obj == git(repo, "rev-parse", "v1.0.0^{commit}"):
        pytest.skip("v1.0.0 is not an annotated tag in this fixture")
    git(repo, "tag", "-d", "v1.0.1")
    _write_manifests(str(repo), "1.0.1", sha=tag_obj)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "bad pin: tag object")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok and "not a commit" in detail, detail


def test_pin_outside_history_is_refused(repo):
    """A sha that is not an ancestor of the tag commit is outside the released history."""
    cut_v101(repo, "correct")
    git(repo, "checkout", "-q", "-b", "sidebranch", "v1.0.0^{commit}")
    with open(os.path.join(repo, "stray.txt"), "w") as f:
        f.write("x")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "off-history commit")
    stray = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    git(repo, "tag", "-d", "v1.0.1")
    _write_manifests(str(repo), "1.0.1", sha=stray)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "bad pin: off-history")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok and "released history" in detail, detail


# --------------------------------------------------------------------------------- AC-TPC-6
def test_cut_release_executes_nothing(repo):
    """The publish boundary is unchanged: the plan is DATA. Building it must create no tag, no
    commit, and no ref in the tree."""
    cut_v101(repo, "correct")
    before_refs = git(repo, "show-ref")
    before_head = git(repo, "rev-parse", "HEAD")
    plan = cr.publish_plan(str(repo), "1.0.1")
    assert isinstance(plan, (list, tuple)) and plan, "the plan is not emitted as data"
    assert git(repo, "show-ref") == before_refs, "building the plan mutated refs"
    assert git(repo, "rev-parse", "HEAD") == before_head, "building the plan moved HEAD"
    assert git(repo, "status", "--porcelain") == "", "building the plan dirtied the tree"


# =========================================================== feat-foundry-verify-tag-ssh-alias-resolution
#
# `_write_manifests`/`repo` never set an `origin` remote or a `source.repo` field, so the
# source.repo cross-check has ZERO coverage above this line (grep-verified 2026-08-02). Every case
# below sets BOTH, through `cut_v101_with_source_repo`, and is therefore the FIRST coverage of that
# branch in either direction -- not merely of the ssh-alias fix.

def _fake_resolver(mapping, calls=None):
    """A resolver(host) -> (rc, stdout) stub, mirroring `ssh -G`'s own output shape: one
    'key value' line per option, the target line 'hostname <value>'. `host` not in `mapping` =>
    (1, '') -- a clean non-zero exit, one of the resolver-failure shapes. `calls`, when given, is
    appended with every `host` the stub is invoked for -- the CALL COUNTER AC-VTA-1/-5 require,
    read off the fake rather than scraped from output text."""
    def resolver(host):
        if calls is not None:
            calls.append(host)
        if host not in mapping:
            return 1, ""
        return 0, f"hostname {mapping[host]}\n"
    return resolver


def _raising_resolver(host):
    raise RuntimeError("ssh unreachable (simulated)")


def _no_hostname_line_resolver(host):
    return 0, "user git\nport 22\n"                            # a real ssh -G shape, no hostname key


def _empty_hostname_resolver(host):
    return 0, "hostname \n"                                    # the key is present, the value is not


# --------------------------------------------------------------------------------- AC-VTA-1 / AC-VTA-2
def test_ssh_alias_origin_resolves_to_github_and_is_coherent(repo):
    """THE DEFECT, inverted. `git@personal-github:...` matches none of the three literal prefixes on
    main, so `norm` stays the whole URL and the coherent release is refused. Resolving the alias
    through `ssh -G` (here, its injected stand-in) recovers the correct verdict."""
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    fake = _fake_resolver({"personal-github": "github.com"})
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
    assert ok, f"an aliased ssh origin that resolves to github.com was refused: {detail}"


def test_ssh_alias_resolving_to_the_port_443_host_is_coherent(repo):
    """`ssh.github.com` is GitHub's OFFICIAL SSH endpoint on port 443 — its own documentation
    ("Using SSH over the HTTPS port") recommends exactly `Host github.com / Hostname
    ssh.github.com / Port 443`, which is what an operator behind a port-22-blocking firewall will
    have. Resolving the alias and then comparing to the single literal "github.com" got the
    resolution right and the verdict wrong: a coherent release was convicted because its operator
    followed GitHub's documented setup. Found by dogfooding this check on the v1.2.0 cut, one
    release after the alias-resolution atom shipped."""
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    fake = _fake_resolver({"personal-github": "ssh.github.com"})
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
    assert ok, f"an alias resolving to GitHub's port-443 SSH host was refused: {detail}"


def test_a_lookalike_github_host_is_still_refused(repo):
    """The negative control that keeps the widening honest. The accepted set is CLOSED and
    two-element, never a `.github.com` suffix match — a suffix rule would admit any
    attacker-shaped `evil.github.com` if DNS or ssh config were hostile, and the entire purpose of
    this check is to confirm the pin was verified against the repo an install would really fetch
    from."""
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    for hostile in ("evil.github.com", "github.com.evil.test", "notgithub.com"):
        fake = _fake_resolver({"personal-github": hostile})
        ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
        assert not ok, f"a lookalike host {hostile!r} was ACCEPTED — the set must stay closed"
        assert hostile in detail, f"the refusal should name the resolved host; got: {detail}"


def test_https_origin_never_invokes_the_ssh_resolver(repo):
    """AC-VTA-1 / R7: a non-ssh transport is adjudicated by the strict comparison ALONE -- the
    resolver is never consulted. Asserted by a call counter, never by scraping output text."""
    git(repo, "remote", "add", "origin", "https://github.com/lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    calls = []
    fake = _fake_resolver({"github.com": "github.com"}, calls=calls)
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
    assert ok, f"a coherent https origin was refused: {detail}"
    assert calls == [], f"the ssh resolver must never be invoked for an https origin: {calls}"


def test_charset_violating_host_never_reaches_ssh_and_is_refused(repo):
    """AC-VTA-1's charset gate (review B1): a leading-`-` argv-injection shape must (i) never reach
    `ssh` in ANY position -- the same call counter -- and (ii) still REFUSE via the strict-comparison
    fallback the charset-gate member of "a resolver failure" triggers."""
    git(repo, "remote", "add", "origin", "git@-oProxyCommand=x:owner/repo")
    cut_v101_with_source_repo(repo, "owner/repo")
    calls = []
    fake = _fake_resolver({}, calls=calls)
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
    assert not ok, f"a charset-violating argv-injection host was accepted: {detail}"
    assert calls == [], f"ssh must never be invoked for a charset-violating host: {calls}"


def test_unmocked_resolver_times_out_and_falls_back(repo, tmp_path, monkeypatch):
    """AC-VTA-1's real timeout (review B2). NO injection seam -- drives the real, UNMOCKED
    `subprocess.run` path against a deliberately slow fake `ssh` placed first on PATH, with the
    module constant `SSH_RESOLVE_TIMEOUT_SECONDS` monkeypatched small. Proves `timeout=` is actually
    wired to the constant BY REFERENCE: expiry raises TimeoutExpired, is caught as a resolver
    failure, and yields refusal rather than hanging every --verify-tag on a Match-exec config.
    Reports NOT-RUN (never a pass) when there is no /bin/sh to drive the fake `ssh` script."""
    if not os.path.exists("/bin/sh") or not os.access("/bin/sh", os.X_OK):
        pytest.skip("NOT-RUN: no executable /bin/sh to drive the slow fake ssh fixture")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake_ssh = fakebin / "ssh"
    fake_ssh.write_text("#!/bin/sh\nsleep 5\n")
    fake_ssh.chmod(0o755)
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    monkeypatch.setenv("PATH", str(fakebin) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(cr, "SSH_RESOLVE_TIMEOUT_SECONDS", 0.2)
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")           # NO resolver= -- the real path
    assert not ok, f"a resolver timeout must fall back to the strict comparison and refuse: {detail}"


def test_real_ssh_resolves_home_scoped_alias_config(repo, tmp_path, monkeypatch):
    """AC-VTA-5's end-to-end proof: the real, UNMOCKED `ssh -G` reading a HOME-scoped
    `~/.ssh/config` Host-alias block, with a UNIQUE alias name (never `personal-github`, which
    collides with THIS OPERATOR'S OWN real config — grep-verified present on this very machine, the
    identity-isolation practice `CLAUDE.md`/`skills/init/SKILL.md` document) and a self-probe that
    proves the HOME override actually took effect before trusting it. Reports NOT-RUN (never a
    pass), claiming no coverage, when there is no real `ssh` binary on PATH OR when this OpenSSH
    build resolves the default per-user config file via the passwd-database home directory rather
    than honouring a `HOME` env-var override (empirically the case on some OpenSSH builds/platforms)
    — the injected-seam cases above already carry every branch assertion, so no guarantee is lost,
    only the end-to-end proof, and this case says so rather than asserting against live operator
    config it does not own."""
    if shutil.which("ssh") is None:
        pytest.skip("NOT-RUN: no ssh binary on PATH to drive the real HOME-scoped config fixture")
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    alias = "foundry-vta-fixture-alias-2f9c1e"
    (ssh_dir / "config").write_text(f"Host {alias}\n    HostName github.com\n    User git\n")
    os.chmod(ssh_dir, 0o700)
    os.chmod(ssh_dir / "config", 0o600)
    monkeypatch.setenv("HOME", str(home))
    probe = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=10)
    probe_host = None
    for line in probe.stdout.splitlines():
        parts = line.split(None, 1)
        if parts and parts[0] == "hostname" and len(parts) > 1:
            probe_host = parts[1].strip().lower()
            break
    if probe.returncode != 0 or probe_host != "github.com":
        pytest.skip("NOT-RUN: this OpenSSH build does not honor a HOME env-var override for "
                    "~/.ssh/config -- claiming no end-to-end coverage; the injected-seam cases "
                    "above already cover every resolution branch")
    git(repo, "remote", "add", "origin", f"git@{alias}:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")           # NO resolver= -- the real ssh -G
    assert ok, f"the real ssh -G resolver did not resolve the HOME-scoped alias: {detail}"


# --------------------------------------------------------------------------------- AC-VTA-2
def test_aliased_origin_naming_a_different_repo_is_refused(repo):
    """THE NEGATIVE CONTROL. Same alias, same resolved host, a DIFFERENT owner/repo path -- still
    refused. Without this, the coherent-alias case above would prove only that the check got
    looser, not that it stayed correct."""
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "someoneelse/other-repo")
    fake = _fake_resolver({"personal-github": "github.com"})
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
    assert not ok, f"an aliased origin naming a DIFFERENT repo must still refuse: {detail}"


def test_repo_mismatch_detail_names_both_values(repo):
    """The refusal detail must name BOTH source.repo and the resolved path -- the inherited
    "name both values" convention this suite's test_refusal_names_both_values establishes."""
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "someoneelse/other-repo")
    fake = _fake_resolver({"personal-github": "github.com"})
    _ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake)
    assert "someoneelse/other-repo" in detail, f"refusal does not name source.repo: {detail}"
    assert "lukasrepublic/agentic-foundry" in detail, f"refusal does not name the compared path: {detail}"


# --------------------------------------------------------------------------------- AC-VTA-3
@pytest.mark.parametrize("fake_resolver,label", [
    (_raising_resolver, "raises"),
    (_no_hostname_line_resolver, "no hostname line"),
    (_empty_hostname_resolver, "empty-valued hostname"),
])
def test_resolver_failure_falls_back_to_the_strict_comparison(repo, fake_resolver, label):
    """FAIL-CLOSED, never fail-open. Every shape of resolver failure lands on the SHIPPED strict
    comparison, whose verdict for the aliased URL is refusal -- there is no path on which a failed
    resolve is treated as coherent."""
    git(repo, "remote", "add", "origin", "git@personal-github:lukasrepublic/agentic-foundry")
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1", resolver=fake_resolver)
    assert not ok, f"a resolver failure ({label}) must fall back to the strict comparison and refuse: {detail}"


def test_absent_origin_remote_still_reports_not_cross_checked(repo):
    """The pre-existing 'no resolvable origin remote' branch (today's `note`) survives unchanged:
    when `git remote get-url origin` itself fails, the function still reports (True, ...) and says
    source.repo was NOT cross-checked."""
    cut_v101_with_source_repo(repo, "lukasrepublic/agentic-foundry")     # no `git remote add` at all
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert ok, f"an absent origin remote must not itself cause a refusal: {detail}"
    assert "NOT cross-checked" in detail, detail


# --------------------------------------------------------------------------------- AC-VTA-4
def test_cut_release_module_adds_no_network_call():
    """STRUCTURAL, over the module's parsed AST -- never over substrings of its source text.

    (i) every literal argv-head string passed to `subprocess.run` is a member of {"git", "gh",
    "ssh"} -- "git" and "gh" pre-exist this atom ("gh" is `_gh_er_state`'s own documented "ONLY
    network touchpoint", untouched here); "ssh" is the one new process THIS atom adds, and no
    OTHER literal head (curl, wget, nc, ...) is introduced. (ii) no call passes a truthy `shell=`
    keyword. (iii) no imported/referenced name belongs to the network-capable set
    {socket, urllib, http, httplib, requests, ssl} nor is `asyncio.open_connection` referenced.
    """
    src = open(CUT, encoding="utf-8").read()
    tree = ast.parse(src, filename=CUT)

    argv_heads = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_run = (isinstance(func, ast.Attribute) and func.attr == "run") or \
                 (isinstance(func, ast.Name) and func.id == "run")
        if not is_run:
            continue
        if node.args:
            first = node.args[0]
            if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
                head = first.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    argv_heads.add(head.value)
        for kw in node.keywords:
            if kw.arg == "shell":
                truthy = isinstance(kw.value, ast.Constant) and bool(kw.value.value)
                assert not truthy, "subprocess.run must never be called with a truthy shell="

    assert argv_heads, "no subprocess.run argv-head literals were found -- the AST walk is broken"
    assert argv_heads <= {"git", "gh", "ssh"}, (
        f"an unexpected external process was introduced: {argv_heads - {'git', 'gh', 'ssh'}}")
    assert "ssh" in argv_heads, "this atom must add ssh -G as a literal argv head"

    banned = {"socket", "urllib", "http", "httplib", "requests", "ssl"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"a network-capable module is imported: {imported & banned}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "open_connection":
            raise AssertionError("asyncio.open_connection is referenced")


def test_cli_path_never_injects_a_host_resolver():
    """The CLI never injects the resolver: main()'s --verify-tag branch calls tag_pin_coherence
    with NO resolver keyword -- mirrors the shipped test_cli_path_never_injects_a_suite_runner
    guard on the cut_release path. Asserted structurally, over the AST call node, not by substring."""
    src = open(CUT, encoding="utf-8").read()
    tree = ast.parse(src, filename=CUT)
    main_def = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main_def)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tag_pin_coherence"]
    assert calls, "main() no longer calls tag_pin_coherence at all"
    for call in calls:
        kw_names = {kw.arg for kw in call.keywords}
        assert "resolver" not in kw_names, "main()'s CLI path must never inject a resolver"


# ── the main-push step must survive a protected branch ──────────────────────────────────────
# Regression from the v1.3.0 cut: the plan emitted a bare `push origin main`, which a repo whose
# main carries branch protection with enforce_admins REFUSES for everyone ("protected branch hook
# declined"). The tag pushed fine, so the release existed upstream while its commits were not on
# main, and the operator had to improvise the PR landing mid-cut. The direct push is kept (it is
# correct wherever main is unprotected), but the plan now carries the fallback.

def test_plan_names_the_protected_branch_fallback(repo):
    plan = cr.publish_plan(str(repo), "1.0.1")
    joined = "\n".join(plan)
    assert "push origin main" in joined, "the direct push is still the first thing to try"
    assert "protected branch hook declined" in joined, (
        "the plan must name the EXACT refusal text, so an operator hitting it can match it"
    )
    assert "refs/heads/release/v1.0.1" in joined, "the fallback must name the branch to push"
    low = joined.lower()
    assert "pr" in low and "main" in low, "the fallback must route through a PR into main"


def test_plan_warns_that_a_squash_landing_rewrites_the_tagged_commits(repo):
    """The non-obvious consequence: after a squash/rebase landing the tag's commit is NOT an
    ancestor of main. That is harmless — installs resolve by ref/sha, never via the branch — but an
    operator who does not know it will think the release is broken and try to 'fix' it."""
    joined = "\n".join(cr.publish_plan(str(repo), "1.0.1"))
    assert "ancestor" in joined
    assert "source.sha" in joined and "never via the" in joined
    assert "diff v1.0.1^{commit} origin/main" in joined, "give the operator the parity check"


def test_the_fallback_is_emitted_as_inert_comment_lines(repo):
    """cut-release emits DATA the operator runs; the fallback is guidance, not a step to execute
    blindly. Every fallback line must be commented so a copy-paste of the whole plan cannot run it
    while the direct push is still the right move."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    fallback = [s for s in plan if s.lstrip().startswith("# FALLBACK")]
    assert fallback, "the fallback step is present and is its own block"
    for step in fallback:
        for line in step.splitlines():
            if not line.strip():
                continue
            assert line.lstrip().startswith("#"), f"fallback line is executable, not commented: {line}"
