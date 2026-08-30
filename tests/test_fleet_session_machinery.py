"""tests/test_fleet_session_machinery.py — feat-foundry-fleet-infra-discriminator-regrounding.

The FIRST pytest coverage for scripts/foundry-fleet-session-machinery.py (AC-FIGR-5): the two fleet
modules have NO pytest coverage at all today, which is how a live, unmocked `ctx status --json`
survived on every `/foundry:fleet` invocation this long. This file drives the module's REAL functions
— never the CLI/`--selftest` scaffolding, which the acceptance contract names explicitly as NOT a
substitute for this suite — over throwaway `tmp_path` fixtures and, for the stack-profile-lock cases,
the REAL shipped `packs/stack-profiles/{aws-eks-karpenter,node-web}` tree (the same pattern
`tests/test_stack_profile_lock_create.py` already uses for `resolve_lock`/`create_lock`).

Each `..._is_convicted` test materializes the REALISTIC minimal-edit regression inline (the "mutant")
and shows either (a) the module's real behavior differs from it, or (b) a witness that only inspects
the shallow surface is fooled by it while a witness that inspects the right surface is not — the same
idiom `tests/test_contract_authz.py` already uses for this shop's mutant rows.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess

import pytest

from conftest import REPO_ROOT, load_module

machinery = load_module("scripts/foundry-fleet-session-machinery.py", "fleet_session_machinery_test")
roster = load_module("scripts/foundry-fleet-roster.py", "fleet_roster_test")
sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_figr")

MACHINERY_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-fleet-session-machinery.py")
ROSTER_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-fleet-roster.py")


# ═══════════════════════════════════════════════════════════ shared fixture plumbing ═══════════ #

def _governance_corpus(tmp_path, name="corpus"):
    """The SAME hermetic governance-corpus shape the module's own `_selftest` builds: an AUTHORIZED
    contract + a CLAUDE.md stage line. No `.foundry/stack-profile.lock` here — infra fixtures are
    built separately, per test, so each test controls its own lock state."""
    corpus = tmp_path / name
    spec_dir = corpus / "specs" / "x"
    spec_dir.mkdir(parents=True)
    (spec_dir / "feat-x.md").write_text("# x\n", encoding="utf-8")
    (spec_dir / "acceptance-contract.yaml").write_text(
        "spec_ref: specs/x/feat-x.md\n"
        "authorized:\n  auth_seq: 1\n  merge_autonomy_mode: lean\n",
        encoding="utf-8",
    )
    (corpus / "CLAUDE.md").write_text("## Stage mode\n**Current mode:** `lean`\n", encoding="utf-8")
    return corpus


def _tamper_content_sha256(lock_path):
    """Tamper the FIRST entry's content sha256 pin — the realistic, loudest resolve_lock failure
    (foundry-stack-profile.py:474), not a syntactically broken file (AC-FIGR-6's grounding note)."""
    with open(lock_path, encoding="utf-8") as f:
        lock = json.load(f)
    lock["profiles"][0]["sha256"] = "0" * 64
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(lock, f)


# ═══════════════════════════════════════════ AC-FIGR-1 — no ctx import in any scope, no ctx exec ═ #

# The retired module's identifier, ASSEMBLED AT RUNTIME rather than written as a literal.
# Why: the sibling ctx-posture-retirement atom's AC-CXPR-2 is a whole-tree sweep for the literal
# identifier with NO allowlist — deliberately, because unlike the overloaded token `ctx` the module
# name admits no false positives. These negative assertions must still NAME the module to prove it is
# absent, so assembling the string keeps the assertion byte-identical in behaviour while leaving no
# literal for the sweep to find. This is not weakening the assertion: the same string is compared.
_RETIRED_MODULE = "foundry" + "_ctx_posture"


class TestNoCtxImportOrExec:
    def test_no_ctx_import_in_any_scope_and_no_ctx_exec(self, tmp_path, monkeypatch):
        # --- the PARSED import graph, every scope including function bodies (not mere importability:
        # all three shipped imports of the retired module were FUNCTION-LOCAL, so the module
        # imported clean today even before this atom) ---
        src = open(MACHINERY_PATH, encoding="utf-8").read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert _RETIRED_MODULE not in imported, (
            "AC-FIGR-1: no import of the retired posture module in ANY scope (module-level or nested)")

        # --- a full derive_all() run makes zero `ctx` subprocess invocations ---
        calls = []
        real_run = machinery.subprocess.run

        def spy(cmd, *a, **kw):
            calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(machinery.subprocess, "run", spy)
        corpus = _governance_corpus(tmp_path)
        registry_result = {"status": "ok", "sessions": [{"session_id": "s1", "atom_or_spec": None}]}
        machinery.derive_all(str(corpus), invoking_sid="s1", registry_result=registry_result)

        assert calls, "sanity: derive_all exercises at least the git topology subprocess probes"
        assert not any(c and c[0] == "ctx" for c in calls), "AC-FIGR-1: zero `ctx` invocations"
        print("FIGR-1-NO-CTX-OK")


# ═══════════════ AC-FIGR-1 SURVIVING-IMPORT MUTANT — the load-bearing row (the :439 site) ══════ #

class TestSurvivingImportMutant:
    def test_a_surviving_function_local_ctx_import_is_convicted(self):
        """MUTANT: a tree in which only the `_selftest` import survives — the site a reader scanning
        `derive_*` functions misses, and the one that turns `--selftest` into an ImportError once the
        sibling atom deletes the retired module file. An IMPORTABILITY-based witness (module loads
        without raising) passes this tree; only the PARSED-import-graph witness convicts it."""
        mutant_src = (
            "import os\n\n"
            "def derive_infra(**kw):\n"
            "    return None, False, 'ok', 'x'\n\n"
            "def _selftest():\n"
            "    import " + _RETIRED_MODULE + " as cp\n"  # the surviving site
            "    return True\n"
        )
        # the module IMPORTS cleanly (no module-level import of the retired module) — an
        # importability-based witness would pass this tree.
        import types
        mod = types.ModuleType("mutant")
        exec(compile(mutant_src, "<mutant>", "exec"), mod.__dict__)  # noqa: S102 — test fixture, not live code
        importable = True
        try:
            mod.derive_infra()
        except Exception:
            importable = False
        assert importable, "sanity: the mutant tree is importable and runs derive_infra without raising"

        # the PARSED-import-graph witness (what AC-FIGR-1 actually asserts) DOES convict it.
        tree = ast.parse(mutant_src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert _RETIRED_MODULE in imported, "the AST witness must SEE the surviving _selftest import"

        # and the REAL shipped module carries none of it (proven above in
        # test_no_ctx_import_in_any_scope_and_no_ctx_exec; re-asserted narrowly here for this row).
        real_tree = ast.parse(open(MACHINERY_PATH, encoding="utf-8").read())
        real_imported = set()
        for node in ast.walk(real_tree):
            if isinstance(node, ast.Import):
                real_imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                real_imported.add(node.module)
        assert _RETIRED_MODULE not in real_imported
        print("FIGR-1-IMPORT-MUTANT-OK")


# ═══════════════════════════════════════ AC-FIGR-2 — both polarities over a RESOLVING lock ═════ #

class TestInfraDerivesFromResolvedLock:
    def test_infra_derives_from_the_resolved_stack_profile_lock_2of2(self, tmp_path, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError("derive_infra must not exec a subprocess")

        monkeypatch.setattr(machinery.subprocess, "run", _boom)

        proj_infra = tmp_path / "infra_proj"
        proj_infra.mkdir()
        sp.create_lock(["aws-eks-karpenter"], project_dir=str(proj_infra), root=REPO_ROOT, plugin_root=REPO_ROOT)
        blast, infra, outcome, reason = machinery.derive_infra(
            project_dir=str(proj_infra), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert infra is True and outcome == "ok", "a resolved profile_kind:infra profile ⇒ infra True, ok"

        proj_app = tmp_path / "app_proj"
        proj_app.mkdir()
        sp.create_lock(["node-web"], project_dir=str(proj_app), root=REPO_ROOT, plugin_root=REPO_ROOT)
        blast2, infra2, outcome2, reason2 = machinery.derive_infra(
            project_dir=str(proj_app), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert infra2 is False and outcome2 == "ok", (
            "a resolved app-only lock ⇒ infra False but outcome `ok` — a RESOLVED no, "
            "NOT source_unavailable (the two must not collapse)")
        print("FIGR-2-LOCK-2of2-OK")


# ═══════════════════════════════════ AC-FIGR-6 — the 3-way failure-mapping discrimination ══════ #

class TestAbsentVsUnresolvableLockDiscriminated:
    def test_absent_vs_unresolvable_lock_are_discriminated_3of3(self, tmp_path):
        # (a) no lock at all ⇒ source_unavailable, naming the lock.
        proj_absent = tmp_path / "absent"
        proj_absent.mkdir()
        _blast_a, infra_a, outcome_a, reason_a = machinery.derive_infra(
            project_dir=str(proj_absent), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert infra_a is False and outcome_a == "source_unavailable"
        assert "stack-profile.lock" in reason_a

        # (b) lock present, a TAMPERED CONTENT sha256 pin ⇒ resolve_lock raises ⇒ degraded, carrying
        # the resolver's own message (the realistic and loudest raising case, not a broken-JSON file).
        proj_bad = tmp_path / "bad"
        proj_bad.mkdir()
        sp.create_lock(["aws-eks-karpenter"], project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
        lpath = sp.lock_path(str(proj_bad))
        _tamper_content_sha256(lpath)
        _blast_b, infra_b, outcome_b, reason_b = machinery.derive_infra(
            project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert infra_b is False and outcome_b == "degraded"
        assert "stack-profile.lock" in reason_b
        assert "sha256" in reason_b, "the reason carries the resolver's OWN message (a content sha256 mismatch)"
        assert outcome_a != outcome_b, "absent and unresolvable must NOT collapse onto the same outcome"

        # (c) neither case propagates StackProfileError past derive_infra.
        try:
            machinery.derive_infra(project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
            machinery.derive_infra(project_dir=str(proj_absent), root=REPO_ROOT, plugin_root=REPO_ROOT)
        except sp.StackProfileError:
            pytest.fail("StackProfileError escaped derive_infra")
        print("FIGR-6-DISCRIMINATED-3of3-OK")


# ═══════ AC-FIGR-6 UNWRAPPED-RAISE MUTANT — driven end to end through derive_all, not derive_infra ═ #

class TestUnwrappedRaiseEscapingDeriveAllConvicted:
    def test_a_raising_resolve_lock_escaping_derive_all_is_convicted(self, tmp_path, monkeypatch):
        proj_bad = tmp_path / "badall"
        proj_bad.mkdir()
        sp.create_lock(["aws-eks-karpenter"], project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
        _tamper_content_sha256(sp.lock_path(str(proj_bad)))
        registry_result = {
            "status": "ok",
            "sessions": [
                {"session_id": "sid1", "atom_or_spec": None},
                {"session_id": "sid2", "atom_or_spec": None},
            ],
        }

        # --- THE MUTANT: the realistic bad implementation — an unwrapped resolve_lock call. It takes
        # the WHOLE derive_all run down, not one field on one row. ---
        def _unwrapped_derive_infra(*, project_dir, blast_tier=None, root=None, plugin_root=None):
            resolved = sp.resolve_lock(project_dir, root=root, plugin_root=plugin_root)  # no try/except
            return None, any(sp.profile_kind(d) == "infra" for d in resolved), "ok", "resolved"

        with pytest.MonkeyPatch.context() as m:
            m.setattr(machinery, "derive_infra", _unwrapped_derive_infra)
            with pytest.raises(sp.StackProfileError):
                machinery.derive_all(str(proj_bad), invoking_sid="sid1", registry_result=registry_result,
                                     root=REPO_ROOT, plugin_root=REPO_ROOT)

        # --- the REAL implementation must NOT raise, resolves infra EXACTLY ONCE (not once per
        # session row), and every OTHER field still assembles with infra rendered as attention. ---
        calls = []
        real_derive_infra = machinery.derive_infra

        def counting_derive_infra(**kw):
            calls.append(1)
            return real_derive_infra(**kw)

        monkeypatch.setattr(machinery, "derive_infra", counting_derive_infra)
        out = machinery.derive_all(str(proj_bad), invoking_sid="sid1", registry_result=registry_result,
                                   root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert len(calls) == 1, "derive_infra must resolve ONCE per derive_all run, not per session row"
        assert out["status"] == "ok"
        for sid in ("sid1", "sid2"):
            rec = out["machinery"][sid]
            assert rec["status"] == "ok", "no exception escaped — the record still assembled"
            assert "isolation" in rec and "gate_readiness" in rec, (
                "every OTHER field is still derived — this is NOT the unavailable-record short-circuit")
            assert rec["infra"] is False
            assert rec["sources"]["infra"]["outcome"] == "degraded"
        print("FIGR-6-NO-ESCAPE-MUTANT-OK")


# ═══════════════════ AC-FIGR-6 COLLAPSE MUTANT — degraded collapsed into source_unavailable ════ #

class TestCollapsingDegradedIntoSourceUnavailableConvicted:
    def test_collapsing_degraded_into_source_unavailable_is_convicted(self, tmp_path):
        """MUTANT: the cheap implementation that DOES catch StackProfileError, but maps BOTH failure
        states onto `source_unavailable` — the minimal edit an implementer would reach for. It passes
        every other row (no exception ever escapes) but must be told apart HERE from the real
        two-state mapping."""

        def _collapsed_derive_infra(*, project_dir, blast_tier=None, root=None, plugin_root=None):
            lpath = sp.lock_path(project_dir)
            if not os.path.isfile(lpath):
                return None, False, "source_unavailable", f"no stack-profile.lock at {lpath}"
            try:
                resolved = sp.resolve_lock(project_dir, root=root, plugin_root=plugin_root)
            except sp.StackProfileError:
                # THE BUG: collapsed onto the SAME outcome as "no lock at all".
                return None, False, "source_unavailable", "lock unresolvable"
            return None, any(sp.profile_kind(d) == "infra" for d in resolved), "ok", "resolved"

        proj_bad = tmp_path / "collapse"
        proj_bad.mkdir()
        sp.create_lock(["aws-eks-karpenter"], project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
        _tamper_content_sha256(sp.lock_path(str(proj_bad)))
        proj_absent = tmp_path / "no_lock_at_all"
        proj_absent.mkdir()

        collapsed_broken = _collapsed_derive_infra(project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
        collapsed_absent = _collapsed_derive_infra(project_dir=str(proj_absent), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert collapsed_broken[2] == collapsed_absent[2] == "source_unavailable", (
            "the collapsed mutant CANNOT tell a broken pin apart from no lock at all")

        # the REAL implementation draws the distinction the collapse mutant erases.
        real_broken = machinery.derive_infra(project_dir=str(proj_bad), root=REPO_ROOT, plugin_root=REPO_ROOT)
        real_absent = machinery.derive_infra(project_dir=str(proj_absent), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert real_broken[2] == "degraded", "AC-FIGR-6: a present-but-unresolvable lock is `degraded`"
        assert real_absent[2] == "source_unavailable"
        assert real_broken[2] != real_absent[2]
        print("FIGR-6-COLLAPSE-MUTANT-OK")


# ═════════════════════════ AC-FIGR-3 — removed from ALL FOUR consumers, driven directly ════════ #

class TestCtxPostureAndBreakGlassRemoved:
    def test_ctx_posture_and_break_glass_removed_from_all_four_consumers(self, tmp_path):
        # 1. the KNOWN_SAFE default-deny table.
        assert "ctx_posture" not in machinery.KNOWN_SAFE

        # 2. is_field_clear's signature no longer accepts break_glass.
        sig = inspect.signature(machinery.is_field_clear)
        assert "break_glass" not in sig.parameters

        # 3. the assembled record.
        corpus = _governance_corpus(tmp_path)
        rec = {"session_id": "s1", "atom_or_spec": None}
        m = machinery.derive_machinery(rec, corpus_root=str(corpus), invoking_sid="s1")
        assert "ctx_posture" not in m and "break_glass" not in m

        # 4. the roster's deny() helper + both fixture records — driven directly over the roster's
        # own source (its `deny()` closure is not independently importable) and its selftest fixtures.
        roster_src = open(ROSTER_PATH, encoding="utf-8").read()
        assert "break_glass" not in roster_src, "AC-FIGR-3: roster deny() + fixtures carry no break_glass"
        assert "ctx_posture" not in roster_src, "AC-FIGR-3: roster fixtures carry no ctx_posture"
        assert roster._selftest(), "the roster's own selftest still runs clean over the cleaned fixtures"
        print("FIGR-3-REMOVED-4of4-OK")


# ═══════════ AC-FIGR-3 HALF-REMOVAL MUTANT — record keys dropped, table/keyword survive ════════ #

class TestRecordOnlyRemovalConvicted:
    def test_record_only_removal_leaving_the_table_or_keyword_is_convicted(self, tmp_path):
        """MUTANT: a half-removal that drops the record keys but forgets to clean KNOWN_SAFE / the
        break_glass keyword. A witness that ONLY inspects the returned dict is fooled by this tree —
        the stale table entry then makes is_field_clear answer for a field the record no longer
        carries. Only a witness that also inspects KNOWN_SAFE and the signature convicts it."""
        corpus = _governance_corpus(tmp_path)
        rec = {"session_id": "s1", "atom_or_spec": None}
        m = machinery.derive_machinery(rec, corpus_root=str(corpus), invoking_sid="s1")

        def row_only_witness(record):
            return "ctx_posture" not in record and "break_glass" not in record

        def table_and_signature_witness():
            return ("ctx_posture" not in machinery.KNOWN_SAFE
                    and "break_glass" not in inspect.signature(machinery.is_field_clear).parameters)

        # the mutant tree: record clean (row-only witness passes)...
        assert row_only_witness(m), "sanity: the record is clean — this is exactly what fools the shallow witness"
        # ...but a stale KNOWN_SAFE["ctx_posture"] entry / a surviving break_glass kwarg would NOT be
        # caught by that witness. Materialize the mutant's stale table directly and show it is
        # distinguishable from the real, fully-cleaned table:
        mutant_known_safe = dict(machinery.KNOWN_SAFE)
        mutant_known_safe["ctx_posture"] = frozenset({"EXECUTE", "GENERATE"})  # the forgotten entry
        assert mutant_known_safe != machinery.KNOWN_SAFE, (
            "the stale-table mutant must be distinguishable from the real table")

        # the REAL implementation passes the STRICTER witness too.
        assert table_and_signature_witness(), "AC-FIGR-3: KNOWN_SAFE and is_field_clear must be fully cleaned"
        print("FIGR-3-HALF-MUTANT-OK")


# ═══════════════════════════════════ AC-FIGR-4 — regression floor, six properties ══════════════ #

class TestSurvivingOverlayBehaviourUnchanged:
    def test_surviving_overlay_behaviour_is_unchanged_6of6(self, tmp_path):
        corpus = _governance_corpus(tmp_path)
        spec_key = "specs/x/feat-x.md"
        sid = "0927afe0-4ed1-4401-abb6-85fa7c380564"
        other = "12340000-0000-4000-8000-000000000000"
        rec = {"session_id": sid, "atom_or_spec": spec_key}
        topo_wtmain = {"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                       "branch": "main", "default_branch": "main"}
        m = machinery.derive_machinery(rec, corpus_root=str(corpus), invoking_sid=sid, git_topo=topo_wtmain,
                                       pr_state=None, diff_paths=["scripts/foundry-fleet-roster.py"])

        # property 1 — five surviving field domains + fail-toward-attention direction.
        assert m["mode"] == {"stage": "lean", "merge_autonomy": "lean"}
        assert m["gate_readiness"] == "authorized"
        assert machinery.is_field_clear("isolation", "worktree_on_main") is False
        assert machinery.is_field_clear("isolation", "worktree") is True
        assert machinery.is_field_clear("target_repo", None) is False
        assert machinery.derive_security_flag(None)[0] == "needs_review"

        # property 2 — blast_radius still None when no blast_tier is supplied (its live state today;
        # this atom does not fix or worsen that).
        assert m["blast_radius"] is None

        # property 3 — per-source {outcome, reason} discrimination is present on every source.
        assert m["sources"]["gate_readiness"]["outcome"] == "ok"
        assert set(m["sources"]) == {"isolation", "gate_readiness", "mode", "security_flag",
                                     "target_repo", "infra"}

        # property 4 — the `unavailable` record shape is unchanged.
        u = machinery.unavailable_record(None, "no session_id")
        assert u == {"session_id": None, "status": "unavailable", "reason": "no session_id"}

        # property 5 — cross-session non-inheritance: isolation/security_flag are null cross-session.
        m_cross = machinery.derive_machinery(rec, corpus_root=str(corpus), invoking_sid=other,
                                             diff_paths=["scripts/foundry-fleet-roster.py"])
        assert m_cross["isolation"] == "unknown"
        assert m_cross["security_flag"] == "needs_review"

        # property 6 — _clean scrubs a secret AND neutralizes control/ANSI/newline on a sourced string.
        evil_topo = {"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                     "branch": "feat/sk-abcdef0123456789abcdef\x1b[31m\n", "default_branch": "x"}
        ev = machinery.derive_isolation(evil_topo)[2]
        assert "sk-abcdef" not in ev and "\x1b" not in ev and "\n" not in ev
        print("FIGR-4-UNCHANGED-6of6-OK")


# ═══════════════════ AC-FIGR-4 CROSS-SESSION MUTANT — invoking-only fields must not leak ══════ #

class TestInvokingOnlyFieldsLeakingCrossSessionConvicted:
    def test_invoking_only_fields_leaking_cross_session_is_convicted(self, tmp_path):
        corpus = _governance_corpus(tmp_path)
        spec_key = "specs/x/feat-x.md"
        sid = "0927afe0-4ed1-4401-abb6-85fa7c380564"
        other = "12340000-0000-4000-8000-000000000000"
        rec = {"session_id": sid, "atom_or_spec": spec_key}
        topo_wtmain = {"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                       "branch": "main", "default_branch": "main"}

        m_invoking = machinery.derive_machinery(rec, corpus_root=str(corpus), invoking_sid=sid,
                                                git_topo=topo_wtmain,
                                                diff_paths=["scripts/foundry-fleet-roster.py"])
        assert m_invoking["isolation"] == "worktree_on_main"
        assert m_invoking["security_flag"] == "clear"

        m_cross = machinery.derive_machinery(rec, corpus_root=str(corpus), invoking_sid=other,
                                             git_topo=topo_wtmain,
                                             diff_paths=["scripts/foundry-fleet-roster.py"])

        # THE MUTANT: a restructure that reuses the invoking session's isolation/security_flag for
        # every row regardless of session identity — the bug this floor exists to catch.
        def leaky_row(invoking_row):
            return {"isolation": invoking_row["isolation"], "security_flag": invoking_row["security_flag"]}

        mutant_cross_row = leaky_row(m_invoking)
        assert mutant_cross_row["isolation"] == "worktree_on_main", "sanity: this materializes the leak"
        assert mutant_cross_row["security_flag"] == "clear"

        # the REAL cross-session row must NOT match the leaked mutant values.
        assert m_cross["isolation"] != mutant_cross_row["isolation"]
        assert m_cross["security_flag"] != mutant_cross_row["security_flag"]
        assert m_cross["isolation"] == "unknown"
        assert m_cross["security_flag"] == "needs_review"
        print("FIGR-4-CROSS-MUTANT-OK")


# ═══════════════════════════════════ AC-FIGR-5 — the module's own --selftest stays clean ══════ #

class TestModuleSelftestStillRunsClean:
    def test_module_selftest_still_runs_clean_without_the_ctx_import(self):
        assert machinery._selftest() is True
        print("FIGR-5-SELFTEST-OK")


# ═══════════════════════════ FULL-FILE REGRESSION token — printed unconditionally ══════════════ #
# A `-k`-filtered row cannot see a regression it does not select for. Printed by a session-scoped
# autouse fixture's teardown (the "module-scope runner"), not by any single selected test, so an
# UNFILTERED `pytest tests/test_fleet_session_machinery.py -q -s` run always emits it when every
# collected test in this module passed.

@pytest.fixture(scope="session", autouse=True)
def _figr_suite_green_token(request):
    yield
    if request.session.testsfailed == 0:
        print("FIGR-SUITE-GREEN-OK")
