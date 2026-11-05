#!/usr/bin/env python3
"""foundry_operating_model.py -- the one reader of `.foundry/operating-model.yaml` (gv-03).

The operating-model policy file holds the tunables and the floors of the ported operating rules.
Every other primitive reads policy ONLY through `load`.

  * Absent file            -> every default, each key `source: default`.
  * Present but broken     -> refuse, naming the key. A present-but-broken file is NEVER silently
                              replaced by defaults.
  * A FLOOR key (a `const` in schema/operating-model.schema.json) written with any other value
                              -> `floor-rule-disabled:<key>`.

Importable: `load(project_dir) -> Policy`, `validate_text(text) -> [error]`,
            `resolve_workflow_size(policy, session_size, prompt_size) -> int`,
            `render_tick_section(policy, name, project_dir) -> str`,
            `render_tick_template(policy, template_text, workspace, operator, narrowing, ...) -> str`.

CLI:  foundry_operating_model.py [--project-dir DIR] status [--json]
      foundry_operating_model.py [--project-dir DIR] validate
      foundry_operating_model.py [--project-dir DIR] explain <key>
      foundry_operating_model.py [--project-dir DIR] render-tick-section <A1|A5|A6|B1|B7|B8|B9|C1|D2|E1|E2>
                                 [--session-size N] [--prompt-size N]
      foundry_operating_model.py [--project-dir DIR] render-tick-section TEMPLATE --workspace W --operator O
                                 [--narrowing N] [--template FILE] [--session-size N] [--prompt-size N]
                                 (the whole command-deck tick prompt: every `{{SECTION:<name>}}` marker of the
                                 template is filled from the policy, then the three placeholders)
Exit 0 ok / 2 refused (named error on stdout) / 1 internal error. Read-only: this script writes nothing.
Stdlib + PyYAML only. No network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "schema" / "operating-model.schema.json"
POLICY_REL = Path(".foundry") / "operating-model.yaml"
SECTIONS = ("A1", "A5", "A6", "B1", "B7", "B8", "B9", "C1", "D2", "E1", "E2")

E2_TEXT = """\
Use ASD-STE100-inspired Simplified Technical English for agent responses, tick reports, briefs and the documentation that agents write:
- Use short, direct sentences and active voice.
- Give one instruction per sentence.
- Use consistent terms for the same concepts.
- State conditions and required actions explicitly.
- Avoid idioms, vague wording and unnecessary jargon.
- Preserve necessary technical terms. Explain unfamiliar terms.
- Keep articles and other words needed for natural, complete sentences.
- Preserve technical accuracy and necessary detail.
- Follow a different style when the operator explicitly requests it.
Apply these principles without claiming strict ASD-STE100 compliance."""

POLICY_MAX_BYTES = 64 * 1024
STYLE_MAX_BYTES = 4 * 1024
STYLE_BEGIN = "--- BEGIN OPERATOR STYLE GUIDANCE (operator-defined instruction text) ---"
STYLE_END = "--- END OPERATOR STYLE GUIDANCE ---"


class PolicyError(Exception):
    """The policy file is unusable. `errors` is the list of named errors; `str()` is the first."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(self.errors[0] if self.errors else "policy-error")


# ---------------------------------------------------------------------------------------------
# schema walk (a deliberately small JSON-Schema subset: exactly what the policy schema uses)
# ---------------------------------------------------------------------------------------------
def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _is_leaf(node):
    return "properties" not in node


def _leaves(node, prefix=""):
    """Yield (dotted_key, leaf_schema) for every leaf under `node`, in declared order."""
    for name, sub in node.get("properties", {}).items():
        key = f"{prefix}{name}"
        if _is_leaf(sub):
            yield key, sub
        else:
            yield from _leaves(sub, key + ".")


def _default_of(leaf):
    if "const" in leaf:
        return leaf["const"]
    return json.loads(json.dumps(leaf.get("default")))


def _same(a, b):
    """Strict equality: True is not 1."""
    return type(a) is type(b) and a == b


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
}


_REMOVAL_KEYS = {"remove", "removed", "delete", "except", "exclude", "unset", "replace", "disable", "disabled"}


def _is_removal(item):
    if isinstance(item, dict):
        return bool(set(item) & _REMOVAL_KEYS)
    return isinstance(item, str) and item.lstrip().startswith(("!", "-", "~!", "not "))


def _check_leaf(node, value, key):
    """Return a list of named errors for `value` against a leaf schema (empty = valid)."""
    if "anyOf" in node:
        for branch in node["anyOf"]:
            if not _check_leaf(branch, value, key):
                return []
        return [f"invalid-value:{key}: matches none of the allowed forms"]
    if "const" in node:
        return [] if _same(value, node["const"]) else [f"floor-rule-disabled:{key}"]
    errs = []
    if node.get("x-add-only"):
        # ADD-ONLY list (gv-11): the policy may extend the built-in defaults, never shrink them. Every way a file
        # could TRY to remove a default is the one named error, so removal is not expressible at all.
        if isinstance(value, dict) and set(value) & _REMOVAL_KEYS:
            return [f"floor-rule-disabled:{key}.removal"]
        if isinstance(value, list) and any(_is_removal(v) for v in value):
            return [f"floor-rule-disabled:{key}.removal"]
    t = node.get("type")
    if t and not _TYPES[t](value):
        return [f"invalid-value:{key}: expected {t}"]
    if "enum" in node and not any(_same(value, e) for e in node["enum"]):
        return [f"invalid-value:{key}: must be one of {node['enum']}"]
    if t == "integer":
        if "minimum" in node and value < node["minimum"]:
            errs.append(f"invalid-value:{key}: below minimum {node['minimum']}")
        if "maximum" in node and value > node["maximum"]:
            errs.append(f"invalid-value:{key}: above maximum {node['maximum']}")
    if t == "string":
        if "minLength" in node and len(value) < node["minLength"]:
            errs.append(f"invalid-value:{key}: empty")
        if "maxLength" in node and len(value) > node["maxLength"]:
            errs.append(f"invalid-value:{key}: longer than {node['maxLength']} characters")
        elif "pattern" in node and not re.fullmatch(node["pattern"], value):
            errs.append(f"invalid-value:{key}: not an allowed form")
    if t == "array":
        items = node.get("items", {})
        for i, item in enumerate(value):
            errs.extend(_check_leaf(items, item, f"{key}[{i}]"))
        if "maxItems" in node and len(value) > node["maxItems"]:
            errs.append(f"invalid-value:{key}: more than {node['maxItems']} items")
        # item types are checked FIRST; the uniqueness pass only runs over already-typed items
        if not errs and node.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errs.append(f"invalid-value:{key}: duplicate items")
    return errs


def _check_object(node, value, prefix, errors):
    if not isinstance(value, dict):
        errors.append(f"invalid-value:{prefix.rstrip('.') or '<root>'}: expected a mapping")
        return
    props = node.get("properties", {})
    for name, v in value.items():
        key = f"{prefix}{name}"
        if not isinstance(name, str) or name not in props:
            errors.append(f"unknown-key:{key!r}"[:64 + len("unknown-key:")])
            continue
        sub = props[name]
        if _is_leaf(sub):
            errors.extend(_check_leaf(sub, v, key))
        else:
            _check_object(sub, v, key + ".", errors)


def _normalize(node, data):
    """YAML 1.1 reads an unquoted `off`/`on` as a boolean. Where a leaf's enum names "off"/"on", map the
    boolean back to that word so `style: off` means what the adopter wrote. Floors are `const` and are
    never touched. Returns a normalized copy."""
    if not isinstance(data, dict):
        return data
    out = {}
    props = node.get("properties", {})
    for k, v in data.items():
        sub = props.get(k) if isinstance(k, str) else None
        if sub is None:
            out[k] = v
        elif _is_leaf(sub):
            enum = sub.get("enum", [])
            if isinstance(v, bool) and "const" not in sub and ("off" in enum or "on" in enum):
                word = "on" if v else "off"
                out[k] = word if word in enum else v
            else:
                out[k] = v
        else:
            out[k] = _normalize(sub, v)
    return out


def validate_data(data):
    """Return the list of named errors for a parsed policy document (empty = valid)."""
    errors = []
    if data is None:
        return errors
    _check_object(_schema(), _normalize(_schema(), data), "", errors)
    return errors


def validate_text(text):
    """Parse + validate policy text. A YAML syntax error is `malformed:<reason>`."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1} column {mark.column + 1}" if mark is not None else "unknown position"
        return [f"malformed:{where}"]
    if data is not None and not isinstance(data, dict):
        return ["malformed:top level must be a mapping"]
    return validate_data(data)


# ---------------------------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------------------------
class Policy:
    """Effective policy. A plain class (not a dataclass) so the module also loads when another script
    imports it by file location without registering it in sys.modules."""

    def __init__(self, values=None, sources=None, floors=frozenset(), path="", file_present=False):
        self.values = values if values is not None else {}    # dotted key -> effective value
        self.sources = sources if sources is not None else {}  # dotted key -> "default" | "file"
        self.floors = floors                                   # dotted keys that are FLOOR
        self.path = path                                       # the file path ("" when absent)
        self.file_present = file_present

    def get(self, key):
        return self.values[key]

    def source(self, key):
        return self.sources[key]

    def kind(self, key):
        return "floor" if key in self.floors else "tunable"


def _flat_leaves(data, leaf_keys, prefix=""):
    for name, v in data.items():
        key = f"{prefix}{name}"
        if key in leaf_keys:
            yield key, v
        elif isinstance(v, dict):
            yield from _flat_leaves(v, leaf_keys, key + ".")


def _safe_resolve(path, typed):
    """Path.resolve() raises RuntimeError on a symlink loop (OSError on some other faults): both are
    the typed refusal `typed`, never a traceback."""
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        raise PolicyError([typed])


def _read_regular(path, cap, refuse_hard_link=False):
    """Read a regular file without following a symlink or blocking on a FIFO. Returns bytes
    (at most cap+1 so the caller can detect an overrun), or None when the file is absent.
    Raises PolicyError('not-regular') for a symlink / FIFO / device / directory."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        raise PolicyError(["not-regular"])
    if not stat.S_ISREG(st.st_mode):
        raise PolicyError(["not-regular"])
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise PolicyError(["not-regular"])
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise PolicyError(["not-regular"])
        if refuse_hard_link and fst.st_nlink > 1:
            raise PolicyError(["hard-link"])
        return os.read(fd, cap + 1)
    finally:
        os.close(fd)


def _read_policy_text(pdir):
    """The policy text, or None when no file. Refuses a symlink/non-regular file, a path that leaves
    the project, and a file over POLICY_MAX_BYTES. The ONE presence test for load AND validate."""
    root = _safe_resolve(pdir, "policy-path-unresolvable")
    path = Path(pdir) / POLICY_REL
    parent = _safe_resolve(path.parent, "policy-path-unresolvable")
    if parent != root and root not in parent.parents:
        raise PolicyError(["policy-path-outside-project"])
    if parent != _safe_resolve(root / ".foundry", "policy-path-unresolvable"):
        raise PolicyError(["policy-path-outside-project"])
    try:
        raw = _read_regular(str(path), POLICY_MAX_BYTES)
    except PolicyError:
        raise PolicyError(["policy-path-not-regular-file"])
    if raw is None:
        return None
    if len(raw) > POLICY_MAX_BYTES:
        raise PolicyError([f"policy-file-too-large:limit {POLICY_MAX_BYTES} bytes"])
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PolicyError(["malformed:not valid UTF-8"])


def load(project_dir=None):
    """Load the effective policy. Absent file -> defaults. Present-but-broken -> PolicyError."""
    pdir = Path(project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    leaves = list(_leaves(_schema()))
    values = {k: _default_of(leaf) for k, leaf in leaves}
    sources = {k: "default" for k, _ in leaves}
    floors = frozenset(k for k, leaf in leaves if "const" in leaf)
    path = pdir / POLICY_REL
    text = _read_policy_text(pdir)
    if text is None:
        return Policy(values, sources, floors, "", False)
    errors = validate_text(text)
    if errors:
        raise PolicyError(errors)
    data = _normalize(_schema(), yaml.safe_load(text) or {})
    for key, v in _flat_leaves(data, {k for k, _ in leaves}):
        values[key] = v
        sources[key] = "file"
    return Policy(values, sources, floors, str(path), True)


def communication_style(project_dir=None):
    """The effective `communication.style` word: simplified-technical-english | off | operator-defined.
    The shared brief renderer calls this by file location. A broken policy raises PolicyError."""
    return load(project_dir).get("communication.style")


def resolve_workflow_size(policy, session_size=None, prompt_size=None):
    """B8: the smallest of the policy's max_builders, the session setting and the tick prompt."""
    sizes = [policy.get("deck.max_builders")]
    for extra in (session_size, prompt_size):
        if extra is not None:
            sizes.append(int(extra))
    if min(sizes) < 1:
        raise PolicyError(["workflow-size-not-positive"])
    return min(sizes)


# ---------------------------------------------------------------------------------------------
# explain: rule, why, enforcing primitive (port plan section 2)
# ---------------------------------------------------------------------------------------------
_EXPLAIN = {
    "pr_strategy.granularity": (
        "A1: map every ready item to the files it changes and merge overlapping items into one cluster.",
        "Many small PRs on shared files conflict in the queue; each conflict costs a re-test and a re-queue.",
        "merge-plan script, called by the command-deck tick template (team mode) and by dispatch."),
    "pr_strategy.one_pr_per_hot_file_per_round": (
        "A1: a shared file or catalogue changes in one PR per round.",
        "Two PRs on one hot file conflict in the queue.",
        "merge-plan script."),
    "merge.queue": (
        "A2: read the live ruleset before relying on it; a queue waits only for the checks the ruleset names.",
        "A queue with no required check once merged red PRs.",
        "queue-land script and the foundry-git-discipline hook."),
    "merge.requeue_budget_outside_diff": (
        "A2 (floor): re-queue once for a failure outside the PR's diff, after posting the evidence. A second failure goes to the test's owner. There is never a third roll.",
        "Unchanged re-queues wasted runner capacity.",
        "queue-land script (rejection classifier)."),
    "merge.full_suite_on_branch": (
        "A3: run the full suite on a branch only for these triggers; otherwise the queue's own run is the gate.",
        "Duplicate full suites exhaust shared runners and cost money.",
        "queue-land script and the merge policy keys."),
    "merge.record_line_before_enqueue": (
        "A2 (floor): post one record line before each enqueue (PR, head, base merged at, catalogue tests, branch full suite, queued). A column that cannot be filled honestly means do not queue.",
        "A change that merged over a red check had no record of what was verified.",
        "queue-land script."),
    "merge.human_review_required": (
        "A4: the floor is green CI plus a separate-context agent review. Human code-owner review is optional.",
        "A mandatory human review gate stalled a two-person team.",
        "the merge policy key and the dispatch review stage (security lens for auth, secrets, payments, external APIs, supply chain)."),
    "deck.max_builders": (
        "B1: each tick dispatches every ready cluster, up to this number, through one Workflow with one worktree per cluster.",
        "Fewer running builders than ready clusters is a stall.",
        "command-deck tick template (B1, B8) and dispatch."),
    "deck.heavy_slots": (
        "B2: heavy local commands run through a slot lock sized to the host; auto sizes it from measured CPU and memory.",
        "Unbounded parallel heavy commands exhaust the machine.",
        "deck-resources script and deck-heavy lock wrapper."),
    "deck.resource_check": (
        "B2 (floor): each tick first measures CPU, memory, swap and the root disk and sizes the fan-out from the GREEN, AMBER or RED verdict.",
        "Parallelism is bounded by the machine, never by the agent count.",
        "deck-resources script."),
    "deck.workflow_size_source": (
        "B8 (floor): when the session setting and the tick prompt disagree on fan-out size, the smaller one wins, and every agent counts toward it.",
        "A larger prompt size overrode a smaller session limit once.",
        "command-deck tick template and `render-tick-section B8`."),
    "decisions.research_first": (
        "B3 (floor): run a prior-art sweep before asking the operator. Adopt the converged recommendation and record a dated ruling. Escalate only on no consensus.",
        "Questions that research could answer cost the operator's attention.",
        "research-first skill and the blocker lint."),
    "decisions.escalation_set": (
        "B3/B4: the operator is asked only for these reasons, and only after an attempt that could have failed.",
        "Four items reported as operator-gated in one review were the agent's own work.",
        "blocker schema (attempted[], control) and its lint."),
    "claims.enabled": (
        "B5: machines check in on a shared lease record before, during and after a chunk of work.",
        "Two machines took the same work.",
        "claims script and the claim-required push/PR guard (team mode)."),
    "claims.expires_after": (
        "B5: a lease expires without a heartbeat; only an expired lease can be taken over, and the takeover is recorded.",
        "A crashed machine held work forever.",
        "claims script."),
    "cost.weigh_in_every_decision": (
        "C1 (floor): every deck decision weighs cost. Use the smallest fan-out that keeps the time to market.",
        "Cost is an input to every decision.",
        "cost section of the tick template."),
    "cost.priced_ask_for_always_on": (
        "C1 (floor): a new always-on resource needs a priced ask (purpose plus a cost breakdown). A capacity raise is a priced option for the operator, never a PR.",
        "An always-on resource was added without a cost review.",
        "priced-ask blocker kind."),
    "report.format": (
        "E1: the report has a first line (local date and time, progress count) and four blocks: Your decisions, Building now, Needs you, Ledger.",
        "ID-heavy status text could not be followed.",
        "deck-report renderer."),
    "report.timezone": ("E1: the first line uses this zone; auto means the operator's local zone.", "The operator reads local time.", "deck-report renderer."),
    "report.plain_language": ("E1: name fixes by what they do, not by IDs.", "ID-heavy status text could not be followed.", "deck-report renderer."),
    "report.max_lines": ("E1: the renderer refuses output longer than this; every line counts, headings included.", "The operator reads short, plain reports.", "deck-report renderer."),
    "report.include_ids": ("E1: when false, the report names work by what it does, not by ID.", "ID-heavy status text could not be followed.", "deck-report renderer."),
    "report.handoff_dir": (
        "E3: step-by-step operator documents go to this directory; generated PDFs go next to them.",
        "Hand-off files in the repo are noise.",
        "this policy key."),
    "communication.style": (
        "E2: use Simplified Technical English in agent responses, tick reports, briefs and documentation; off or operator-defined are allowed.",
        "Operators could not follow dense, ID-heavy status text.",
        "brief preamble, tick template and author personas via `render-tick-section E2`."),
    "communication.operator_style_file": (
        "E2: the .foundry/ .md or .txt file used when style is operator-defined.", "An operator may need a different style.", "`render-tick-section E2`."),
    "models.builder": (
        "B7: builders use this model; the default is the smaller model.", "Token cost: the strongest model is not needed to build.",
        "command-deck tick template via `render-tick-section B7`."),
    "models.reviewer": (
        "B7: separate-context reviewers use this model and receive a diff file, never shell access.", "Token cost, and a reviewer with a shell can change what it reviews.",
        "command-deck tick template via `render-tick-section B7`."),
    "models.security_reviewer": (
        "B7: security reviews of auth, secrets and crypto use this model; the default is the strongest model.", "Only these reviews need the strongest model.",
        "command-deck tick template via `render-tick-section B7`."),
    "hygiene.disk_sweep_each_tick": (
        "F2: measure the mount where the work writes, not the default one.", "The wrong disk filled while the default looked fine.", "deck-resources script."),
    "hygiene.teardown_required": (
        "F1 (floor): pair every start with a teardown of containers, clusters, port-forwards, worktrees and scratch output, scoped by your own project label.",
        "Orphaned resources accumulate and cost money.",
        "brief preamble hygiene section and the env-hygiene reaper."),
    "secrets.value_reads_forbidden": (
        "D1 (floor): verify by metadata only; never read or print a secret value.",
        "A command-text hook is not a boundary; only an IAM-level boundary prevents the read.",
        "secret-read hook (configurable verbs and no-print roots) and the docs page."),
    "secrets.forbidden_verbs": (
        "D1: extra command phrases the secret-read guard refuses, ADDED to the built-in defaults (a default can never be removed).",
        "A team's own secret-printing verbs need the same refusal as the defaults.",
        "the secret-read guard hook and docs/how-to/secrets-never-read.md."),
    "secrets.no_print_roots": (
        "D1: extra paths no reader command may be pointed at, ADDED to ~/.ssh, ~/.aws, ~/.config/gcloud, ~/.kube and .env*.",
        "Private key material never enters an agent's context.",
        "the secret-read guard hook and docs/how-to/secrets-never-read.md."),
    "messaging.machine_channel": (
        "Cross-session machine messages are an append-only JSONL log on a protected git branch.", "A git log is the established shape for a machine ledger.", "message-log script."),
    "messaging.people_channel": (
        "One issue per production change, request or decision.", "People read issues; machines read the log.", "the inbox and the message-log refs."),
    "messaging.chat_mirror": (
        "Opt-in mirror of PROD-BEFORE and PROD-AFTER lines to chat; off by default.", "Chat is a convenience, not a record.", "message-log script."),
    "messaging.open_change_minutes": (
        "A PROD-BEFORE with no terminal PROD-AFTER after this many minutes is reported open.", "A missed after-line is a process miss.", "message-log audit."),
    "messaging.cleanup_timeout_s": (
        "The total wait for the --on-failure cleanup commands of a wrapped production step before STOP says the cleanup timed out.",
        "A hung cleanup must not hold the STOP line back forever.", "foundry_prod_step.py."),
    "governance.design_required_paths": (
        "Changes touching these path globs need a design before build.", "Large changes without a design rework late.", "the governance brief renderer."),
    "governance.size_tag_required": (
        "A design is required at or above this size tag.", "Large changes without a design rework late.", "the governance brief renderer."),
    "schema_version": ("The policy file format version; only 1 exists.", "A future format change needs a named migration.", "this validator."),
}


def explain(key):
    """Return (rule, why, primitive) for a dotted key, or None."""
    return _EXPLAIN.get(key)


# ---------------------------------------------------------------------------------------------
# render-tick-section: plain strings a template includes
# ---------------------------------------------------------------------------------------------
def render_tick_section(policy, name, project_dir=None, session_size=None, prompt_size=None):
    """Return the policy-driven text for section `name`. E2 is empty when style is off."""
    g = policy.get
    if name == "A1":
        if g("pr_strategy.granularity") == "coarse":
            text = ("Before you dispatch, map each ready item to the files and components it changes. "
                    "Merge overlapping items into one cluster, across specs. "
                    "Add new work for an area to that area's open PR that is not yet queued. Do not open a sibling PR. ")
        else:
            text = ("Before you dispatch, map each ready item to the files and components it changes. "
                    "Fine-grained PRs are allowed by policy: one PR for each item. ")
        if g("pr_strategy.one_pr_per_hot_file_per_round"):
            text += "Change a shared file or catalogue in one PR for each round. "
        return text + "State the plan in one report line."
    if name == "A5":
        return ("The output of the deck is merged fixes. Paperwork follows the code. "
                "Dispatch the largest and most complex cluster first. "
                "Give every open item an owner or a dated recheck hold. "
                "When the board is empty, rebuild it from the whole backlog before you say that the deck is idle. "
                "Report progress over the whole backlog, never over a subset.")
    if name == "A6":
        return ("A fix is done only when you re-measure it. After a merge, run again the check that the fix targets, on the merged code. "
                "Batch all re-checks of a tick into one record PR. "
                "Re-audit items that are marked fixed before any formal run.")
    if name == "B1":
        size = resolve_workflow_size(policy, session_size, prompt_size)
        noun = "builder" if size == 1 else "builders"
        return (f"Each tick, dispatch every ready cluster, up to {size} {noun}, through one Workflow: "
                "build, then a separate-context review, then at most 2 fix rounds, with one worktree for each cluster. "
                "Never wait idle on remote CI or queues. "
                "If fewer builders run than there are ready clusters, that is a stall. Say so in the report.")
    if name == "B7":
        def who(v):
            return f"the {v} model" if v in ("smaller", "strongest") else f"the model {v}"
        return (f"Token discipline: use {who(g('models.builder'))} for builders, {who(g('models.reviewer'))} for reviewers "
                f"and {who(g('models.security_reviewer'))} for security reviews of auth, secrets and crypto. "
                "Give each reviewer the path of a diff FILE. Never give a reviewer shell access. Keep tick turns short.")
    if name == "B8":
        size = resolve_workflow_size(policy, session_size, prompt_size)
        return ("If the session setting and the tick prompt give different fan-out sizes, the smaller size wins. "
                "Every agent counts toward the size. "
                f"The policy limit is {g('deck.max_builders')}. The size for this tick is {size}.")
    if name == "B9":
        return ("Check the pause flag first. If a pause is armed (\"pause once X lands\"), start no new fan-out, "
                "even when you cannot observe X. Finish or park what is running.")
    if name == "C1":
        return ("Weigh cost in every decision. Avoid unchanged re-queues and surplus full suites. "
                "Use the smallest fan-out that keeps the time to market. "
                "A new always-on resource needs a priced ask: the purpose and a cost breakdown. "
                "A capacity raise is a priced option for the operator, never a PR.")
    if name == "D2":
        return ("Before each irreversible or production write, post one line that says what will happen. "
                "After the write, post the outcome. If you miss the before line, report a process miss. "
                "Subagents follow the same rule. On a failure, stop, clean up what you created and report.")
    if name == "E1":
        if g("report.format") == "detailed":
            shape = "Use the detailed report format."
        else:
            shape = ("The first line is the local date and time plus the progress count (\"X of N done, +moved\"). "
                     "Then write four blocks: Your decisions, Building now, Needs you, Ledger.")
        plain = " Use plain words." if g("report.plain_language") else ""
        ids = " IDs are allowed." if g("report.include_ids") else " Do not use IDs; name fixes by what they do."
        return (f"{shape}{plain}{ids} Use at most {g('report.max_lines')} lines. "
                f"Use time zone: {g('report.timezone')}. "
                "Name every stuck item in full with its reason, every time. Never write \"nothing new\". "
                "If a tick merged nothing and verified nothing, write STALL and say why.")
    if name == "E2":
        style = g("communication.style")
        if style == "off":
            return ""
        if style == "simplified-technical-english":
            return E2_TEXT
        return _operator_style_block(g("communication.operator_style_file"), project_dir)
    raise ValueError(f"unknown section {name!r}")


TEMPLATE_REL = Path("skills") / "command-deck" / "tick-prompt.template.md"
_MARKER_RE = re.compile(r"\{\{SECTION:([A-Z][0-9]+)\}\}")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_PLACEHOLDER_MAX = {"WORKSPACE": 512, "OPERATOR": 128, "NARROWING": 512}


def _one_line(value, limit):
    """A value that goes into a prompt: one line, no control or format characters, bounded."""
    text = "".join(" " if c in "\r\n\t\u2028\u2029" else c for c in str(value)
                   if c in "\r\n\t\u2028\u2029" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    return " ".join(text.split())[:limit]


def render_tick_template(policy, template_text, workspace, operator, narrowing="none", project_dir=None,
                         session_size=None, prompt_size=None):
    """Fill the command-deck tick template: every `{{SECTION:<name>}}` marker from the policy (an empty section,
    E2 with style off, removes its whole line and one blank line), then WORKSPACE, OPERATOR and NARROWING."""
    def fill(m):
        name = m.group(1)
        if name not in SECTIONS:
            raise PolicyError([f"unknown-template-section:{name}"])
        return render_tick_section(policy, name, project_dir, session_size, prompt_size)

    out = []
    blank_pending = False
    for line in template_text.split("\n"):
        if _MARKER_RE.search(line):
            filled = _MARKER_RE.sub(fill, line)
            if not filled.strip():
                blank_pending = True     # drop this line and the blank line that follows it
                continue
            line = filled
        elif blank_pending and not line.strip():
            blank_pending = False
            continue
        blank_pending = False
        out.append(line)
    text = "\n".join(out)
    values = {"WORKSPACE": _one_line(workspace, _PLACEHOLDER_MAX["WORKSPACE"]),
              "OPERATOR": _one_line(operator, _PLACEHOLDER_MAX["OPERATOR"]),
              "NARROWING": _one_line(narrowing, _PLACEHOLDER_MAX["NARROWING"]) or "none"}
    text = _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)
    left = _PLACEHOLDER_RE.findall(text) + [f"SECTION:{n}" for n in _MARKER_RE.findall(text)]
    if left:
        raise PolicyError([f"unfilled-template-placeholder:{left[0]}"])
    return text


def _operator_style_block(rel, project_dir):
    """The operator's own style text, as a delimited block. It IS instruction text by design (the
    operator wrote it); it is read only from a regular .md/.txt file under .foundry/, at most 4 KiB,
    control and format characters stripped. Anything else is refused."""
    key = "communication.operator_style_file"
    if not rel:
        raise PolicyError([f"invalid-value:{key}: required when style is operator-defined"])
    if re.fullmatch(r"\.foundry/([A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(md|txt)", rel) is None:
        raise PolicyError([f"invalid-value:{key}: must be a .md or .txt file under .foundry/"])
    root = _safe_resolve(project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), "policy-path-unresolvable")
    target = root / rel
    parent = _safe_resolve(target.parent, "policy-path-unresolvable")
    fdir = _safe_resolve(root / ".foundry", "policy-path-unresolvable")
    # the resolved .foundry must itself sit under the resolved root, independently of `load`
    if root not in fdir.parents or (parent != fdir and fdir not in parent.parents):
        raise PolicyError([f"invalid-value:{key}: path leaves .foundry/"])
    try:
        raw = _read_regular(str(target), STYLE_MAX_BYTES, refuse_hard_link=True)
    except PolicyError as e:
        if e.errors == ["hard-link"]:
            raise PolicyError([f"style-file-hard-link:{key}"])
        raw = False
    if raw is False:
        raise PolicyError([f"invalid-value:{key}: not a regular file"])
    if raw is None:
        raise PolicyError([f"invalid-value:{key}: cannot read the file"])
    if len(raw) > STYLE_MAX_BYTES:
        raise PolicyError([f"style-file-too-large:{key}: limit {STYLE_MAX_BYTES} bytes"])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PolicyError([f"invalid-value:{key}: not valid UTF-8"])
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    text = text.strip()
    if not text:
        raise PolicyError([f"style-file-empty:{key}"])
    folded = " ".join(unicodedata.normalize("NFKC", text.replace("\u2028", "\n").replace("\u2029", "\n")).casefold().split())
    if "style guidance" in folded:
        raise PolicyError([f"invalid-value:{key}: contains the block delimiter"])
    return f"{STYLE_BEGIN}\n{text}\n{STYLE_END}"


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _cmd_status(pdir, as_json):
    p = load(pdir)
    rows = [{"key": k, "value": v, "kind": p.kind(k), "source": p.sources[k]} for k, v in p.values.items()]
    if as_json:
        print(json.dumps({"file": p.path or None, "rows": rows}, indent=2, sort_keys=True))
        return 0
    print(f"operating-model: {'file ' + p.path if p.file_present else 'defaults (no file)'}")
    for r in rows:
        print(f"{r['key']} = {json.dumps(r['value'])}  [{r['kind']}, source: {r['source']}]")
    if p.get("communication.style") == "off":
        print("communication.style is off: the E2 block is omitted from rendered briefs and prompts.")
    return 0


def _cmd_validate(pdir):
    p = load(pdir)
    print("ok" if p.file_present else "ok: defaults (no file)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="foundry_operating_model.py", description=__doc__.split("\n")[0])
    ap.add_argument("--project-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    sub.add_parser("validate")
    ex = sub.add_parser("explain")
    ex.add_argument("key")
    rt = sub.add_parser("render-tick-section")
    rt.add_argument("name", choices=SECTIONS + ("TEMPLATE",))
    rt.add_argument("--template", default=None)
    rt.add_argument("--workspace", default=None)
    rt.add_argument("--operator", default=None)
    rt.add_argument("--narrowing", default="none")
    rt.add_argument("--session-size", type=int, default=None)
    rt.add_argument("--prompt-size", type=int, default=None)
    args = ap.parse_args(argv)
    pdir = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        if args.cmd == "status":
            return _cmd_status(pdir, args.json)
        if args.cmd == "validate":
            return _cmd_validate(pdir)
        if args.cmd == "explain":
            hit = explain(args.key)
            if hit is None:
                print(f"unknown-key:{args.key!r:.64}")
                return 2
            p = load(pdir)
            print(f"key: {args.key}\nkind: {p.kind(args.key) if args.key in p.values else 'meta'}\n"
                  f"value: {json.dumps(p.values.get(args.key))}\n"
                  f"rule: {hit[0]}\nwhy: {hit[1]}\nenforced by: {hit[2]}")
            return 0
        if args.cmd == "render-tick-section":
            p = load(pdir)
            if args.name == "TEMPLATE":
                if not args.workspace or not args.operator:
                    print("template-needs: --workspace and --operator")
                    return 2
                tpl = Path(args.template) if args.template else HERE.parent / TEMPLATE_REL
                try:
                    tpl = tpl.resolve(strict=True)
                    tpl.relative_to(HERE.parent.resolve())      # confined to the plugin root (symlinks resolved)
                except (OSError, ValueError):
                    print(f"template-outside-plugin-root:{str(args.template)[:128]}")
                    return 2
                try:
                    text = tpl.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    print(f"template-unreadable:{str(tpl)[:128]}")
                    return 2
                sys.stdout.write(render_tick_template(p, text, args.workspace, args.operator, args.narrowing, pdir,
                                                      args.session_size, args.prompt_size))
                return 0
            print(render_tick_section(p, args.name, pdir, args.session_size, args.prompt_size))
            return 0
    except PolicyError as e:
        for line in e.errors:
            print(line)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
