"""Synthetic fixture workspaces for tests/test_migrate.py (no real project names anywhere)."""
import os
import subprocess

SCOPE = {"owner": "example-org", "planning_repo": "planning", "project_number": 1, "operators": {}}
WS_SLUG = "example-org/workspace"
TARGET = "example-org/planning"

AUTH = """# === FOUNDRY-AUTHORIZED-TRAILER ===
authorized:
  operator_id: op_test
  auth_seq: 1
  spec_sha256: aaaa
  contract_sha256: bbbb
"""


def contract(spec_ref, extra=""):
    return (f"spec_ref: {spec_ref}\nspec_sha256: \"0000\"\ncheckpoints:\n"
            f"  - {{ac_id: AC-A-1, locator: \"sh -c 'rm -rf /tmp/x && echo SECRET-LOCATOR'\"}}\n{extra}" + AUTH)


def spec(title, acs):
    body = "\n".join(f"- **{i}** (Requirement): {t}" for i, t in acs)
    return (f"# {title}\n\n> intro text that is not an AC.\n\n<!-- normative -->\n## Acceptance criteria\n\n"
            f"{body}\n<!-- /normative -->\n\n## Notes\n\nEvidence text that must never be inlined.\n")


def common():
    return {
        ".claude/foundry-project.json": __import__("json").dumps(
            {"schema_version": 1, "github_scope": SCOPE}),
        ".claude/foundry-operators.json": '{"schema_version": 1, "operators": {"op_test": {"name": "T"}}}',
        ".claude/settings.json": "{}\n",
        ".claude/hooks/h.sh": "#!/bin/sh\n",
        ".github/workflows/ci.yml": "name: ci\n",
        "CLAUDE.md": (
            "# Instructions\n\n"
            "1. **Front-authorization** — every change needs a spec first; an un-authorized spec never "
            "reaches a product repo's `main` (no skip).\n"
            "2. **Merge floor** — a machine-derived PASS before merge.\n\n"
            "The release manifest is the work context and the acceptance\n"
            "record for every planned change. Another sentence follows here.\n\n"
            "The release manifest is the source of truth, kept for sign-off.\n"),
        "docs/note.md": "The release manifest is the work context and the acceptance record.\n",
        ".foundry/decisions/d1.md": "decision\n",
        ".foundry/evidence/e1.txt": "evidence\n",
        ".foundry/admission-ledger.jsonl": "{}\n",
        ".foundry/wiring-hash.pin": "pin\n",
        "status-reports/s1.md": "status\n",
        "specs/charters/c1.md": "# Charter\nCHARTER-TEXT-NEVER-INLINED\n",
    }


REL_A = """id: rel-a
# a comment that must survive
state: active   # trailing comment
description: >
  fixture release
atoms:
  - id: a1
    spec_ref: specs/features/foundry/demo/feat-a.md
    contract_ref: specs/features/foundry/demo/acceptance-contract.yaml
    owner: item-owner
    priority: P1
    depends_on: []
  - id: a2
    spec_ref: specs/features/foundry/demo/b/feat-b.md
    depends_on: [a1]
  - {id: a3, depends_on_release: rel-done}
gates:
  merge: pending
"""


def basic_files():
    f = common()
    f.update({
        "specs/features/foundry/demo/feat-a.md": spec("Feature A", [("AC-A-1", "Does the first thing."),
                                                                      ("AC-A-2", "Does the second thing.")]),
        "specs/features/foundry/demo/acceptance-contract.yaml": contract("specs/features/foundry/demo/feat-a.md"),
        "specs/features/foundry/demo/b/feat-b.md": spec("Feature B", [("AC-B-1", "B does a thing.")]),
        "specs/features/foundry/demo/b/acceptance-contract.yaml": "spec_ref: x\ncheckpoints: []\n",
        "specs/features/foundry/demo/other.md": "# Other\n",
        ".foundry/releases/rel-a/release.yaml": REL_A,
        ".foundry/releases/rel-a/RESUME.md": "resume\n",
        ".foundry/releases/rel-a/state.yaml": "x: 1\n",
        ".foundry/releases/rel-done/release.yaml": "id: rel-done\nstate: completed\natoms:\n  - id: d1\n",
        ".foundry/releases/rel-done/NOTES.md": "notes\n",
    })
    return f


def golden_files():
    f = common()
    f.update({
        ".foundry/releases/prog-closed/program.yaml": "id: prog-closed\nstate: closed-superseded   # closed\n",
        ".foundry/releases/prog-closed/AUDIT.md": "audit\n",
        ".foundry/releases/rel-live/release.yaml": (
            "id: rel-live\nprogram: prog-closed\nstate: active       # shipped but still marked active\n"
            "atoms:\n"
            "  - id: shipped-one\n    spec_ref: specs/features/foundry/g/one/feat-one.md\n"
            "    contract_ref: specs/features/foundry/g/one/acceptance-contract.yaml\n    depends_on: []\n"
            "  - id: shipped-two\n    spec_ref: specs/features/foundry/g/two/feat-two.md\n    depends_on: [shipped-one]\n"
            "coupled_reauthorization:\n  - id: reauth-one\n    prior_auth_seq: 1\n"
            "gates:\n  merge: done\n  release_cut: done\n"),
        ".foundry/releases/rel-live/RESUME.md": "resume\n",
        ".foundry/releases/rel-live/HANDOFF.md": "handoff\n",
        ".foundry/releases/rel-live/A2-PARKED.md": "parked\n",
        ".foundry/releases/rel-live/state.yaml": "x: 1\n",
        ".foundry/releases/rel-planned/release.yaml": (
            "id: rel-planned\nprogram: prog-closed\nstate: planned\n"
            "atoms:\n  - {id: flow-one, kind: HC}\n  - {id: flow-two, kind: HC, depends_on: [flow-one]}\n"),
        ".foundry/releases/rel-orphan/release.yaml": "id: rel-orphan\nprogram: missing-prog\nstate: planned\n",
        ".foundry/releases/rel-gone/release.yaml": "id: rel-gone\nstate: released\n",
        "specs/features/foundry/g/one/feat-one.md": spec("Feature One", [("AC-ONE-1", "one.")]),
        "specs/features/foundry/g/one/acceptance-contract.yaml": contract("specs/features/foundry/g/one/feat-one.md"),
        "specs/features/foundry/g/two/feat-two.md": spec("Feature Two", [("AC-TWO-1", "two.")]),
        "specs/features/foundry/g/two/acceptance-contract.yaml": contract("specs/features/foundry/g/two/feat-two.md"),
    })
    return f


def build(root, files, mode_exec=()):
    """Create a git workspace at `root` with an origin remote and a resolvable origin/HEAD."""
    root = str(root)
    os.makedirs(root, exist_ok=True)
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def g(*a):
        subprocess.run(["git", "-C", root, *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.invalid")
    g("config", "user.name", "T")
    g("config", "commit.gpgsign", "false")
    g("remote", "add", "origin", f"https://github.com/{WS_SLUG}.git")
    g("add", "-A")
    g("commit", "-q", "-m", "fixture")
    g("update-ref", "refs/remotes/origin/main", "HEAD")
    g("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return root
