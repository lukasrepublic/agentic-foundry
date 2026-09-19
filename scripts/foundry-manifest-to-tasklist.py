#!/usr/bin/env python3
"""foundry-manifest-to-tasklist — project a release manifest's atoms into a native shared-task-list
plan (feat manifest-to-tasklist, AC-MTL-1..3).

**Why this exists.** The command deck re-measures the ready set from disk every tick
(`foundry_command_deck.ready_set` / `derive_run_state`) — a hand-rolled version of what the native
agent-team substrate already gives for free: a shared task list at `~/.claude/tasks/<team>/`
(`blockedBy`, file-locked self-claim, auto-unblock). In a TEAM session, the manifest is projected
ONCE into that task list, and the harness itself enforces the dependency DAG through `blockedBy` —
the deck's own ready-set derivation becomes advisory (see `skills/command-deck/SKILL.md`).

**What this script does, and does not.** It prints a JSON PLAN of the `TaskCreate` calls the caller
(a team-session lead) would need to issue — one per manifest atom, in manifest declaration order —
and executes NOTHING: this module never calls `TaskCreate` (an agent-only native tool unavailable
to a plain Python subprocess), never writes into `~/.claude/tasks/`, and never claims/assigns a task
(self-claim is native, out of scope by charter). It is read-only over the release manifest, the
atom's charter/contract, and (for the AC-MTL-2 idempotence read) the tasks directory.

**The real per-task JSON shape this module reads (AC-MTL-2), observed read-only on this machine at
`~/.claude/tasks/<team>/<n>.json` and relied on here — nothing beyond these two fields is read:**

    {"id": "<per-team-unique string>", "subject": "<free text — this atom's projection\n"
     " emits 'atom:<release>/<id>'>", "description": "...", "activeForm": "...",
     "status": "pending|in_progress|completed|...", "blocks": [...], "blockedBy": [...]}

Only `subject` (the idempotence match key) and `id` (the value reported back in a `skip` entry) are
read; every other field belongs to the native harness and is never interpreted here. A task's
`status` can lag reality (primary-doc fact, README) — this module never reads or reports `status`;
`done_when` evidence, not task status, stays the truth (charter Goal).

CLI:
    foundry-manifest-to-tasklist.py <release-id> [--tasks-dir DIR] [--root DIR]

Exit codes:
    0   a JSON plan was printed (possibly with `authorized: false` rows — AC-MTL-3; that is a
        successful plan, not a failure: the floor-hooks atom is what refuses the CLAIM later)
    2   the release manifest is malformed or otherwise unresolvable (`foundry_release.ReleaseError`
        — missing manifest, bad YAML, a schema violation, an unknown depends_on edge, ...)
    3   the release id, or `--tasks-dir`, was refused as non-slug / outside expectations (a
        fail-closed containment refusal — never a crash, never a silent fallback to a wrong dir)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_release as fr  # noqa: E402
import foundry_command_deck_watch as cdw  # noqa: E402  — done_when_escalate_when (AC-DWE-3 reader)


class PlanError(Exception):
    """Fail-closed: a `--tasks-dir` that does not resolve inside the expected tasks root. Never a
    silent fallback to a different directory."""


def _wave_plan_module():
    """Import the hyphenated shipped wave planner by file path (mirrors
    `foundry_command_deck._import_wave_plan`) — reused here ONLY for its two charter/contract
    `allowed_paths` readers (`_load_charter_allowed_paths` / `_load_contract_allowed_paths`), never
    re-derived."""
    path = os.path.join(HERE, "foundry-wave-plan.py")
    spec = importlib.util.spec_from_file_location("_foundry_wave_plan_mtl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── tasks-dir resolution (AC-MTL-2 read surface; fail-closed containment) ──────────────────────── #

def default_tasks_root(home=None):
    """`~/.claude/tasks` (or `<home>/.claude/tasks` when `home` is injected, for tests)."""
    h = home or os.path.expanduser("~")
    return os.path.join(h, ".claude", "tasks")


def resolve_tasks_dir(tasks_dir, *, home=None):
    """Resolve the directory this plan's idempotence read (AC-MTL-2) scans. `None` (no
    `--tasks-dir`) -> the tasks ROOT itself (every team's directory is walked — this script does
    not know which team session it will be issued into). An explicit value is resolved relative to
    the tasks root when not absolute, then containment-checked by realpath/commonpath (the SAME
    fail-closed pattern `foundry_release.load_release` uses for the releases dir): it must resolve
    to the tasks root itself or a path strictly inside it. Anything else — an absolute path outside
    the root, a `..` escape — raises `PlanError` (mapped to exit 3), never silently substituted."""
    root = default_tasks_root(home)
    if tasks_dir is None:
        return root
    candidate = os.path.expanduser(tasks_dir)
    if not os.path.isabs(candidate):
        candidate = os.path.join(root, candidate)
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    if os.path.commonpath([real_root, real_candidate]) != real_root:
        raise PlanError(
            f"--tasks-dir {tasks_dir!r} resolves outside the expected tasks root ({root}) — "
            f"refused (fail-closed containment)")
    return candidate


def _existing_subjects(tasks_dir):
    """AC-MTL-2: `{subject: task_id}` read from every `*.json` file under `tasks_dir` (recursive —
    `tasks_dir` may be the tasks ROOT holding one directory per team, or a single team's directory
    directly). A missing directory reads as `{}` (nothing exists yet, never an error — every atom
    is a fresh `create`). A file that is absent/unreadable/not-JSON/not-a-mapping/has no string
    `subject` is skipped, never a crash; the FIRST file seen for a given subject wins (the native
    task list itself guarantees subject uniqueness within one team — this is defense in depth, not
    a dedup policy of its own)."""
    subjects = {}
    if not tasks_dir or not os.path.isdir(tasks_dir):
        return subjects
    for dirpath, _dirnames, filenames in os.walk(tasks_dir):
        for fn in sorted(filenames):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            subject = doc.get("subject")
            if isinstance(subject, str) and subject and subject not in subjects:
                subjects[subject] = doc.get("id")
    return subjects


# ── description assembly (AC-MTL-1: charter/spec ref + done_when + escalate_when + scope) ──────── #

def _ref_lines(atom):
    if atom.charter_ref:
        return [f"charter_ref: {atom.charter_ref}"]
    return [f"spec_ref: {atom.spec_ref}", f"contract_ref: {atom.contract_ref}"]


def _scope_for_atom(atom, project_dir):
    """This atom's declared write-boundary `allowed_paths` — the charter's `## Scope (write
    boundary)` section for a charter-lane atom, or the frozen contract's `scope.allowed_paths` for
    a factory-lane atom (reusing `foundry-wave-plan.py`'s own readers — never re-derived). `[]` when
    unresolvable (absent file, no parseable section/field) — a definite "not declared", never a
    crash."""
    wp = _wave_plan_module()
    if atom.charter_ref:
        allowed = wp._load_charter_allowed_paths(atom.charter_ref, project_dir)
    elif atom.contract_ref:
        allowed = wp._load_contract_allowed_paths(atom.contract_ref, project_dir)
    else:
        allowed = None
    return allowed or []


def _description_for_atom(atom, project_dir):
    lines = _ref_lines(atom)
    done, escalate = cdw.done_when_escalate_when(atom, project_dir)
    scope = _scope_for_atom(atom, project_dir)
    lines.append("done_when: " + ("; ".join(done) if done else "(not declared)"))
    lines.append("escalate_when: " + ("; ".join(escalate) if escalate else "(not declared)"))
    lines.append("scope: " + ("; ".join(scope) if scope else "(not declared)"))
    return "\n".join(lines)


# ── the plan (AC-MTL-1/-2/-3) ────────────────────────────────────────────────────────────────────── #

def subject_for(release_id, atom_id):
    """`atom:<release>/<id>` — a task subject naming exactly one manifest atom (both slugs, per the
    charter README)."""
    return f"atom:{release_id}/{atom_id}"


def build_plan(release, *, project_dir=None, tasks_dir=None, authorized_fn=None):
    """AC-MTL-1/-2/-3: the pure projection — never mutates anything, never calls `TaskCreate`.

    One entry per atom, in `release.atoms` MANIFEST DECLARATION ORDER (not the topological wave
    order `release.order` carries — AC-MTL-1 says manifest order explicitly, and a team-session
    lead issuing `TaskCreate` calls in that order still gets a correct DAG because `blockedBy` names
    the dependency, not position).

    `authorized_fn` is injectable FOR TESTS ONLY (mirrors `foundry_release.derive_closure`'s own
    `authorized_fn`/`merged_fn` injection points); the production default is
    `foundry_release._default_authorized`, which already re-derives BOTH shapes correctly: a
    factory-lane atom via `foundry_authz.is_authorized` (contract `AUTHORIZED` state), a
    charter-lane atom via `git log -1 -- <charter_ref>` (the charter is committed) — AC-MTL-3's own
    "contract not AUTHORIZED / charter not committed" vocabulary, verbatim.
    """
    pd = fr._project_dir(project_dir)
    authorized_fn = authorized_fn or (lambda a: fr._default_authorized(a, pd))
    existing = _existing_subjects(tasks_dir)
    tasks = []
    for atom in release.atoms:
        subject = subject_for(release.id, atom.id)
        entry = {
            "subject": subject,
            "description": _description_for_atom(atom, pd),
            "blockedBy": [subject_for(release.id, dep) for dep in atom.depends_on],
            "authorized": bool(authorized_fn(atom)),
        }
        if subject in existing:
            entry["action"] = "skip"
            entry["task_id"] = existing[subject]
        else:
            entry["action"] = "create"
        tasks.append(entry)
    return {"release": release.id, "tasks_dir": tasks_dir, "tasks": tasks}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────── #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Project a release manifest into a TaskCreate plan for the native shared task "
                    "list. Prints JSON; executes nothing.")
    ap.add_argument("release_id")
    ap.add_argument("--tasks-dir", default=None,
                    help="a team's task-list directory, or a dir containing several (default: "
                         "~/.claude/tasks, every team walked)")
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    args = ap.parse_args(argv)

    if not isinstance(args.release_id, str) or not fr._SLUG.match(args.release_id):
        print(f"foundry-manifest-to-tasklist: REFUSED — release id {args.release_id!r} is not a "
              f"[a-z0-9-]+ slug (no path separators/traversal)", file=sys.stderr)
        return 3

    try:
        release = fr.load_release(args.release_id, project_dir=args.root)
    except fr.ReleaseError as e:
        print(f"foundry-manifest-to-tasklist: REFUSED — {e}", file=sys.stderr)
        return 2

    try:
        tasks_dir = resolve_tasks_dir(args.tasks_dir)
    except PlanError as e:
        print(f"foundry-manifest-to-tasklist: REFUSED — {e}", file=sys.stderr)
        return 3

    plan = build_plan(release, project_dir=args.root, tasks_dir=tasks_dir)
    print(json.dumps(plan, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
