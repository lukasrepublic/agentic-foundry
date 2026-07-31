#!/usr/bin/env python3
"""foundry-project-sync — the GraphQL PROJECTOR CLI (PTC, ER #112 atom 2 of 3;
feat-foundry-project-tracking-projector-cli). The NETWORK layer.

ER #112 wants a native ONE-WAY projection of file-based atoms into a GitHub org Project — the spec
files stay the single source of truth, the Project is a *generated view*. This module IMPORTS the
PTM model (`scripts/foundry_project_tracking.py`, atom 1 of 3 — config parse, the stable atom
marker, the derived Status, and the role-keyed field values) READ-ONLY (it never modifies that
file) and turns the derived desired-state into GitHub mutations:

  * **upsert** exactly one issue per atom, keyed by PTM's body marker
    `<!-- foundry-atom: <atom-id> -->` (AC-PTC-1), and write/update the local convenience cache
    `.foundry/project-map.json` (`<atom-id> -> issue-number`, AC-PTC-8);
  * **attach** the issue to the configured org Project (`addProjectV2ItemById`) and **set** the
    mapped Project fields (Status / Area / Priority / Control) from the PTM-derived role-keyed item
    (`updateProjectV2ItemFieldValue`), resolving each `field_map` role to a concrete Project field
    node-ID (AC-PTC-2);
  * **build the hierarchy** — an OPTIONAL synthesized per-`<domain>` epic-parent issue with atoms
    linked as `addSubIssue` sub-issues, and a `release.yaml` -> repository-milestone mapping,
    gracefully no-op when neither is present (AC-PTC-3);
  * **self-heal** a hand-edited Project field back to the derived value on the next `sync`
    (AC-PTC-4), while remaining strictly ONE-WAY — the only local write is the project-map cache,
    never a spec or acceptance-contract file (AC-PTC-9, the blast-radius floor).

Config-gated, default OFF (AC-PTC-5): `github_projects.enabled` absent/false => inactive no-op,
zero GitHub calls, zero local writes. Enabled-but-no-credential fails CLOSED, naming the missing
env var, with no partial mutation (AC-PTC-10). The credential is sourced ONLY from the environment
variable named by `github_projects.auth_env` (default `FOUNDRY_PROJECTS_TOKEN`) — there is no
`--token` CLI flag and no config field carries a token; any such decoy is structurally ignored
(AC-PTC-6, the credential-handling Invariant). `--dry-run` emits the full projection plan with ZERO
GitHub mutation (AC-PTC-7, plan-then-apply separation of duties).

Threat model — TOUCHES AUTH / SECRETS / SUPPLY-CHAIN (this atom holds a `project`-scoped credential
and issues authenticated GraphQL mutations). It ships with NO live target: all real GitHub I/O sits
behind a small transport seam (`GitHubGraphQLTransport`, real; `FakeGraphQLTransport`, in-memory) so
the entire runtime surface below is verified OFFLINE — no socket opened, no token vendored in-repo.

  python scripts/foundry-project-sync.py                 # sync against the resolved adopter root
  python scripts/foundry-project-sync.py --root DIR       # override the resolved adopter root
  python scripts/foundry-project-sync.py --dry-run        # plan-only; zero GitHub mutation
  python scripts/foundry-project-sync.py --selftest       # AC-PTC-1..10 (isolated temp fixtures + fake transport)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import traceback

import yaml

# PTM is imported READ-ONLY (config parse, the atom marker, Status, and role-keyed field
# derivation). PTC owns only the network/mutation layer below; it never modifies foundry_project_tracking.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_project_tracking as ptm  # noqa: E402

DEFAULT_AUTH_ENV = "FOUNDRY_PROJECTS_TOKEN"
EPIC_MARKER_TMPL = "<!-- foundry-domain-epic: {domain} -->"

# Which projected-item roles map to a Project *single-select* field (needs option-ID resolution)
# vs a free-text field (the raw string is written directly). `control` is the traceability AC-ID
# set (a list) rendered as a comma-joined text value; the others are single-select enums.
ROLE_ORDER = ("status", "area", "priority", "control")
FIELD_KIND = {"status": "select", "area": "select", "priority": "select", "control": "text"}

# The GraphQL WRITE mutations — anything NOT in this set is a read/lookup call and is safe to issue
# under --dry-run (it only informs the plan, it never mutates the Project/repo). AC-PTC-7 requires
# ZERO calls from this set while `--dry-run` is given.
WRITE_METHODS = {
    "create_issue", "update_issue_body", "add_project_item",
    "set_field_value", "create_milestone", "add_sub_issue",
}


# ==================================================================================================== #
# Transport seam. The REAL transport imports urllib.request LAZILY inside its own methods (never at
# module import/top-level, so the module has NO network dependency at rest) and is constructed only
# on the live apply path, only AFTER the credential-present check (AC-PTC-10) has already passed.
# --selftest and --dry-run drive FakeGraphQLTransport exclusively — an in-memory, no-socket double.
# ==================================================================================================== #

class GitHubGraphQLTransport:
    """The REAL transport: authenticated HTTPS POST to the documented GitHub Projects v2 / Issues /
    sub-issues GraphQL mutations. Never instantiated by --selftest or the --dry-run plan path; no
    live target ships with this atom (residual: first live run is the operator's out-of-band
    validation). The token is held only in memory and used only as the Authorization header value —
    it is never interpolated into a query string, a log line, or an exception message (AC-PTC-6)."""

    API_URL = "https://api.github.com/graphql"

    def __init__(self, token):
        self._token = token
        # Cached by resolve_project_node_id (always the reconcile's first transport call, per
        # build_and_apply) so set_field_value can bind `projectId` correctly (§8 audit fix #1)
        # WITHOUT widening this method's signature beyond FakeGraphQLTransport's — keeps both
        # transports duck-type identical at every call site in the shared reconcile pass.
        self._project_node_id = None

    def assert_live_apply_ready(self):
        """§8 audit fix #2 — a fail-EARLY, fail-CLEAN guard. `get_field_value` and
        `create_milestone` below are unimplemented residuals (no live target ships with this
        atom to validate them against); if a real, non-dry-run apply were allowed to proceed, it
        could commit `create_issue` / `add_project_item` writes and only THEN hit one of those
        stubs mid-sequence, leaving partial, non-convergent Project state that re-raises on every
        subsequent run instead of ever settling. `sync()` calls this BEFORE constructing/calling
        ANY transport method on a real, non-dry-run apply — so the refusal is atomic: zero GitHub
        calls, zero mutation, zero partial state, every time, until this transport's live-field-
        read/milestone paths are implemented and validated. `--dry-run` is unaffected (it never
        reaches this guard) and `FakeGraphQLTransport` never defines this method, so --selftest is
        untouched."""
        raise RuntimeError(
            "live projection transport is not yet validated against a live target "
            "(GitHubGraphQLTransport.get_field_value / create_milestone are unimplemented "
            "residuals) — run --dry-run only; refusing before any GitHub call, so no partial "
            "Project state is written"
        )

    def _post(self, query, variables=None, extra_headers=None):
        import urllib.error  # lazy import: no network dependency at module import/top-level
        import urllib.request
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        }
        if extra_headers:
            headers.update(extra_headers)
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(self.API_URL, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # pragma: no cover (live network)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:  # pragma: no cover (live network)
            raise RuntimeError(f"GitHub GraphQL request failed: {e.reason}") from None

    def resolve_project_node_id(self, org, project_number):  # pragma: no cover (live network)
        q = "query($org:String!,$num:Int!){organization(login:$org){projectV2(number:$num){id}}}"
        data = self._post(q, {"org": org, "num": project_number})
        node_id = data["data"]["organization"]["projectV2"]["id"]
        self._project_node_id = node_id  # cached for set_field_value's projectId binding
        return node_id

    def find_issue_by_marker(self, repo, marker):  # pragma: no cover (live network)
        # Ordering note (live-path, low-severity residual): GitHub's `search` index is
        # EVENTUALLY consistent, so a marker-by-search lookup immediately after a `create_issue`
        # in the SAME run (or a rapid back-to-back run) can race and miss the just-created issue,
        # risking a duplicate on retry. The authoritative dedup fallback on the live path is the
        # local `.foundry/project-map.json` cache (`<atom-id> -> issue-number`, AC-PTC-8) — a
        # live-path caller SHOULD consult that cache (resolve atom_id -> issue number -> a direct
        # `repository.issue(number:)` lookup, not `search`) BEFORE falling back to this
        # search-based marker lookup, which exists to (re)discover/self-heal an atom whose cache
        # entry is stale, missing, or was hand-deleted. This method itself stays a pure
        # marker-by-search primitive; the cache-first ordering is the live orchestration layer's
        # responsibility (not yet wired — no live target ships with this atom; see the module
        # docstring's live-transport residual).
        owner, name = repo.split("/", 1)
        q = ("query($q:String!){search(query:$q,type:ISSUE,first:5){nodes{"
             "... on Issue{number id body}}}}")
        data = self._post(q, {"q": f"repo:{repo} \"{marker}\" in:body"})
        for node in data["data"]["search"]["nodes"]:
            if marker in (node.get("body") or ""):
                return {"number": node["number"], "node_id": node["id"], "body": node["body"]}
        return None

    def create_issue(self, repo, title, body):  # pragma: no cover (live network)
        owner, name = repo.split("/", 1)
        rq = "query($o:String!,$n:String!){repository(owner:$o,name:$n){id}}"
        rid = self._post(rq, {"o": owner, "n": name})["data"]["repository"]["id"]
        mq = ("mutation($rid:ID!,$t:String!,$b:String!){createIssue(input:{repositoryId:$rid,"
              "title:$t,body:$b}){issue{number id}}}")
        data = self._post(mq, {"rid": rid, "t": title, "b": body})["data"]["createIssue"]["issue"]
        return {"number": data["number"], "node_id": data["id"]}

    def update_issue_body(self, repo, number, node_id, body):  # pragma: no cover (live network)
        mq = "mutation($id:ID!,$b:String!){updateIssue(input:{id:$id,body:$b}){issue{number id}}}"
        data = self._post(mq, {"id": node_id, "b": body})["data"]["updateIssue"]["issue"]
        return {"number": data["number"], "node_id": data["id"]}

    def find_project_item(self, project_node_id, issue_node_id):  # pragma: no cover (live network)
        q = ("query($p:ID!){node(id:$p){... on ProjectV2{items(first:100){nodes{id content{"
             "... on Issue{id}}}}}}}")
        for node in self._post(q, {"p": project_node_id})["data"]["node"]["items"]["nodes"]:
            content = node.get("content") or {}
            if content.get("id") == issue_node_id:
                return node["id"]
        return None

    def add_project_item(self, project_node_id, issue_node_id):  # pragma: no cover (live network)
        mq = ("mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c})"
              "{item{id}}}")
        data = self._post(mq, {"p": project_node_id, "c": issue_node_id})
        return data["data"]["addProjectV2ItemById"]["item"]["id"]

    def resolve_field(self, project_node_id, field_name):  # pragma: no cover (live network)
        q = ("query($p:ID!,$n:String!){node(id:$p){... on ProjectV2{field(name:$n){"
             "... on ProjectV2FieldCommon{id} ... on ProjectV2SingleSelectField{id "
             "options{id name}}}}}}")
        field = self._post(q, {"p": project_node_id, "n": field_name})["data"]["node"]["field"]
        options = {o["name"]: o["id"] for o in field.get("options", [])} if field.get("options") else {}
        return {"id": field["id"], "options": options}

    def resolve_option_id(self, project_node_id, field_name, option_name):  # pragma: no cover
        field = self.resolve_field(project_node_id, field_name)
        if option_name not in field["options"]:
            raise RuntimeError(f"Project field {field_name!r} has no option named {option_name!r}")
        return field["options"][option_name]

    def get_field_value(self, item_id, field_id):  # pragma: no cover (live network)
        q = ("query($i:ID!){node(id:$i){... on ProjectV2Item{fieldValueByName(name:\"\"){"
             "... on ProjectV2ItemFieldSingleSelectValue{name} "
             "... on ProjectV2ItemFieldTextValue{text}}}}}")
        # Residual: full-fidelity live field-value reading (paginating every field on the item) is
        # out of scope for this offline-verified atom; the self-heal reconcile is exercised end to
        # end against FakeGraphQLTransport (AC-PTC-4). A live-parity extension is a follow-up. This
        # (and `create_milestone` below) is why `sync()` refuses a real, non-dry-run apply BEFORE
        # any write mutation is attempted (`assert_live_apply_ready`, §8 audit fix #2) rather than
        # letting a live run reach this stub mid-sequence with partial Project state already
        # written.
        raise NotImplementedError("live field-value read is a residual; verified via the fake transport")

    def set_field_value(self, item_id, field_id, value):  # pragma: no cover (live network)
        # Fixed (§8 audit fix #1): the mutation now correctly binds all four
        # updateProjectV2ItemFieldValue inputs — `projectId` <- self._project_node_id (cached by
        # resolve_project_node_id, always this reconcile's first transport call), `itemId` <-
        # item_id, `fieldId` <- field_id — instead of the prior defect, which declared `$p`
        # unused and bound BOTH `projectId` and `itemId` to the FIELD id (never passing item_id
        # at all, so every live field-set/self-heal write would have silently mutated the wrong
        # object or errored). NOTE: `value:{text:$v}` is correct for a text-kind field; a
        # single-select field (status/area/priority — see FIELD_KIND) actually requires
        # `value:{singleSelectOptionId:$v}` on live GitHub — a further live-parity residual not
        # reachable today because `assert_live_apply_ready` refuses before this method is ever
        # called on the live path.
        if not self._project_node_id:
            raise RuntimeError(
                "set_field_value called before resolve_project_node_id — no projectId to bind "
                "(programming error in the reconcile ordering, not a live-target issue)"
            )
        mq = ("mutation($p:ID!,$i:ID!,$f:ID!,$v:String!){updateProjectV2ItemFieldValue(input:{"
              "projectId:$p,itemId:$i,fieldId:$f,value:{text:$v}}){projectV2Item{id}}}")
        self._post(mq, {"p": self._project_node_id, "i": item_id, "f": field_id, "v": str(value)})

    def find_milestone(self, repo, title):  # pragma: no cover (live network)
        owner, name = repo.split("/", 1)
        q = ("query($o:String!,$n:String!){repository(owner:$o,name:$n){milestones(first:50,"
             "query:$t){nodes{number title}}}}")
        for node in self._post(q, {"o": owner, "n": name, "t": title})["data"]["repository"]["milestones"]["nodes"]:
            if node["title"] == title:
                return node["number"]
        return None

    def create_milestone(self, repo, title):  # pragma: no cover (live network)
        # Unimplemented residual (see `assert_live_apply_ready` — §8 audit fix #2): milestone
        # creation uses the REST milestones endpoint (no GraphQL mutation exists for it), which
        # this atom does not implement; unreachable on the live path because `sync()` refuses a
        # real, non-dry-run apply before any transport method is ever called.
        raise NotImplementedError(
            "milestone creation uses the REST milestones endpoint (no GraphQL mutation exists); "
            "residual — no live target ships with this atom"
        )

    def add_sub_issue(self, repo, parent_node_id, child_node_id):  # pragma: no cover (live network)
        mq = "mutation($p:ID!,$c:ID!){addSubIssue(input:{issueId:$p,subIssueId:$c}){issue{id}}}"
        self._post(mq, {"p": parent_node_id, "c": child_node_id},
                    extra_headers={"GraphQL-Features": "sub_issues"})


class FakeGraphQLTransport:
    """In-memory GraphQL transport double for --selftest (and any future offline test harness). No
    socket, no `requests`/`urllib`/`http`/`socket` import, ever. Records every call (`self.calls`,
    a plain list of method names, for the selftest's assertions) and returns deterministic canned
    node-IDs. `raise_on` names a method that raises a RuntimeError when invoked — used to exercise
    the error/exception path for AC-PTC-6's hardened no-token-leak assertion."""

    def __init__(self, raise_on=None):
        self._issues = {}            # repo -> [ {number, node_id, title, body} ]
        self._next_issue_no = {}     # repo -> int
        self._project_items = {}     # project_node_id -> { issue_node_id: item_id }
        self._item_fields = {}       # item_id -> { field_id: value }
        self._fields = {}            # project_node_id -> { field_name: {"id", "options": {name: id}} }
        self._sub_issues = []        # [ (parent_node_id, child_node_id) ]
        self._milestones = {}        # repo -> { title: number }
        self._next_milestone_no = {}
        self._project_node_ids = {}  # (org, number) -> node_id
        self.calls = []
        self._raise_on = raise_on

    def _log(self, name):
        self.calls.append(name)
        if self._raise_on == name:
            raise RuntimeError(f"fake transport: simulated GraphQL error in {name}()")

    def mutation_count(self):
        """Count of WRITE-method calls only (AC-PTC-5/AC-PTC-7 assert this is zero)."""
        return sum(1 for c in self.calls if c in WRITE_METHODS)

    def resolve_project_node_id(self, org, project_number):
        self._log("resolve_project_node_id")
        key = (org, project_number)
        return self._project_node_ids.setdefault(key, f"PROJECT_{org}_{project_number}")

    def find_issue_by_marker(self, repo, marker):
        self._log("find_issue_by_marker")
        for iss in self._issues.get(repo, []):
            if marker in iss["body"]:
                return dict(iss)
        return None

    def create_issue(self, repo, title, body):
        self._log("create_issue")
        n = self._next_issue_no.get(repo, 0) + 1
        self._next_issue_no[repo] = n
        node_id = f"ISSUE_{repo}_{n}"
        rec = {"number": n, "node_id": node_id, "title": title, "body": body}
        self._issues.setdefault(repo, []).append(rec)
        return dict(rec)

    def update_issue_body(self, repo, number, node_id, body):
        self._log("update_issue_body")
        for iss in self._issues.get(repo, []):
            if iss["number"] == number:
                iss["body"] = body
                return dict(iss)
        raise KeyError(f"no such issue {repo}#{number}")

    def find_project_item(self, project_node_id, issue_node_id):
        self._log("find_project_item")
        return self._project_items.get(project_node_id, {}).get(issue_node_id)

    def add_project_item(self, project_node_id, issue_node_id):
        self._log("add_project_item")
        items = self._project_items.setdefault(project_node_id, {})
        if issue_node_id in items:
            return items[issue_node_id]
        item_id = f"ITEM_{len(items) + 1}_{issue_node_id}"
        items[issue_node_id] = item_id
        self._item_fields[item_id] = {}
        return item_id

    def resolve_field(self, project_node_id, field_name):
        self._log("resolve_field")
        fields = self._fields.setdefault(project_node_id, {})
        if field_name not in fields:
            fields[field_name] = {"id": f"FIELD_{project_node_id}_{field_name}", "options": {}}
        return {"id": fields[field_name]["id"], "options": dict(fields[field_name]["options"])}

    def resolve_option_id(self, project_node_id, field_name, option_name):
        self._log("resolve_option_id")
        field = self.resolve_field(project_node_id, field_name)
        opts = self._fields[project_node_id][field_name]["options"]
        if option_name not in opts:
            opts[option_name] = f"OPT_{field['id']}_{option_name}"
        return opts[option_name]

    def get_field_value(self, item_id, field_id):
        self._log("get_field_value")
        return self._item_fields.get(item_id, {}).get(field_id)

    def set_field_value(self, item_id, field_id, value):
        self._log("set_field_value")
        self._item_fields.setdefault(item_id, {})[field_id] = value

    def find_milestone(self, repo, title):
        self._log("find_milestone")
        return self._milestones.get(repo, {}).get(title)

    def create_milestone(self, repo, title):
        self._log("create_milestone")
        n = self._next_milestone_no.get(repo, 0) + 1
        self._next_milestone_no[repo] = n
        self._milestones.setdefault(repo, {})[title] = n
        return n

    def add_sub_issue(self, repo, parent_node_id, child_node_id):
        self._log("add_sub_issue")
        pair = (parent_node_id, child_node_id)
        if pair not in self._sub_issues:
            self._sub_issues.append(pair)


# ==================================================================================================== #
# Reconcile: the single pass shared by --dry-run (plan-only) and the real apply. Read/lookup calls
# (find_issue_by_marker, find_project_item, resolve_field, resolve_option_id, get_field_value,
# find_milestone, resolve_project_node_id) are issued regardless of dry_run — they only inform the
# plan and mutate nothing. WRITE calls (WRITE_METHODS) are issued ONLY when dry_run is False.
# ==================================================================================================== #

def _issue_body(marker, item):
    control = ", ".join(item["fields"]["control"]) if item["fields"]["control"] else "(none)"
    return (
        f"{marker}\n\n"
        "Projected by `foundry-project-sync` — one-way; hand-edits are overwritten on the next sync.\n\n"
        f"Status: {item['status']}\n"
        f"Area: {item['fields']['area']}\n"
        f"Control: {control}\n"
    )


def _read_extra_config(root):
    """PTC's own additive, OPTIONAL config knob (`github_projects.epic_parents`) — PTM's
    `read_config` exposes only the fields it owns (org/project_number/field_map/auth_env/
    issues_repo/enabled, AC-PTM-1); this reads the SAME file again for PTC's own extra key. Never
    writes to the file. Absent/unreadable -> epic_parents False (the AC-PTC-3 no-op branch)."""
    path = os.path.join(root, ".claude", "foundry-project.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return {"epic_parents": False}
    gp = doc.get("github_projects") if isinstance(doc, dict) else None
    if not isinstance(gp, dict):
        return {"epic_parents": False}
    return {"epic_parents": bool(gp.get("epic_parents"))}


def _resolve_issues_repo(cfg, root):
    """`issues_repo` when configured; else best-effort derive the adopter's primary repo from the
    git remote (Clarifications: "defaults to the adopter's primary repo when absent"). Falls back to
    a stable placeholder (never raises) so an unresolvable repo simply leaves the hierarchy edges to
    no-op rather than crashing the whole projection — a bounded, logged limitation (residual)."""
    if cfg.get("issues_repo"):
        return cfg["issues_repo"]
    try:
        import subprocess
        url = subprocess.run(
            ["git", "-C", root, "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False, timeout=5,
        ).stdout.strip()
        if url:
            name = url.rstrip("/")
            if name.endswith(".git"):
                name = name[:-4]
            for sep in ("github.com:", "github.com/"):
                if sep in name:
                    name = name.split(sep, 1)[1]
                    break
            parts = [p for p in name.split("/") if p]
            if len(parts) >= 2:
                return f"{parts[-2]}/{parts[-1]}"
    except Exception:
        pass
    return "unresolved/unresolved"


def build_and_apply(root, cfg, transport, dry_run):
    """The reconcile pass. Returns (plan, cache_map) where `plan` is the JSON-serializable
    projection plan (issues / fields / hierarchy / milestones) and `cache_map` is the
    `<atom-id> -> issue-number` mapping resolved this run (empty under dry_run — nothing was
    actually upserted so there is nothing new to cache)."""
    corpus = ptm.project_corpus(root)
    items = corpus.get("items", [])
    issues_repo = _resolve_issues_repo(cfg, root)
    field_map = cfg.get("field_map") or {}
    extra = _read_extra_config(root)

    plan = {"issues": [], "fields": [], "hierarchy": [], "milestones": []}
    cache_map = {}

    project_node_id = transport.resolve_project_node_id(cfg.get("org"), cfg.get("project_number"))

    resolved_issue = {}  # atom_id -> {"number", "node_id", ...} | None (unresolved under dry_run for a NEW atom)
    for item in items:
        atom_id = item["atom_id"]
        marker = item["marker"]
        body = _issue_body(marker, item)
        existing = transport.find_issue_by_marker(issues_repo, marker)

        if existing is None:
            plan["issues"].append({"atom_id": atom_id, "action": "create", "repo": issues_repo})
            resolved_issue[atom_id] = transport.create_issue(issues_repo, atom_id, body) if not dry_run else None
        else:
            needs_update = existing["body"] != body
            plan["issues"].append({
                "atom_id": atom_id, "action": "update" if needs_update else "noop",
                "repo": issues_repo, "number": existing["number"],
            })
            if needs_update and not dry_run:
                resolved_issue[atom_id] = transport.update_issue_body(
                    issues_repo, existing["number"], existing["node_id"], body)
            else:
                resolved_issue[atom_id] = existing

        issue = resolved_issue.get(atom_id)
        if issue:
            cache_map[atom_id] = issue["number"]

        # -- project-item attach (idempotent) --------------------------------------------------- #
        item_id = None
        issue_node_id = issue["node_id"] if issue else None
        if issue_node_id:
            item_id = transport.find_project_item(project_node_id, issue_node_id)
            if item_id is None:
                plan["fields"].append({"atom_id": atom_id, "action": "add-to-project"})
                if not dry_run:
                    item_id = transport.add_project_item(project_node_id, issue_node_id)

        # -- field set, with self-heal diff (AC-PTC-2 / AC-PTC-4) --------------------------------- #
        role_values = {
            "status": item["status"],
            "area": item["fields"]["area"],
            "priority": item["fields"]["priority"],
            "control": ", ".join(item["fields"]["control"]) if item["fields"]["control"] else None,
        }
        for role in ROLE_ORDER:
            field_name = field_map.get(role)
            raw_value = role_values.get(role)
            if not field_name or raw_value in (None, ""):
                continue
            resolved_field = transport.resolve_field(project_node_id, field_name)
            field_id = resolved_field["id"]
            if FIELD_KIND.get(role) == "select":
                target_value = transport.resolve_option_id(project_node_id, field_name, raw_value)
            else:
                target_value = raw_value
            current_value = transport.get_field_value(item_id, field_id) if item_id else None
            if current_value != target_value:
                plan["fields"].append({
                    "atom_id": atom_id, "role": role, "field": field_name, "value": raw_value,
                })
                if not dry_run and item_id:
                    transport.set_field_value(item_id, field_id, target_value)

    # -- hierarchy: OPTIONAL per-domain epic parent + addSubIssue (AC-PTC-3) --------------------- #
    if extra["epic_parents"]:
        domains = sorted({it["fields"]["area"] for it in items if it["fields"]["area"]})
        domain_parent = {}
        for domain in domains:
            marker = EPIC_MARKER_TMPL.format(domain=domain)
            body = f"{marker}\n\nSynthesized domain-epic parent for `{domain}` atoms."
            existing = transport.find_issue_by_marker(issues_repo, marker)
            if existing is None:
                plan["hierarchy"].append({"type": "epic-parent", "domain": domain, "action": "create"})
                if not dry_run:
                    created = transport.create_issue(issues_repo, f"[epic] {domain}", body)
                    domain_parent[domain] = created["node_id"]
            else:
                domain_parent[domain] = existing["node_id"]
                plan["hierarchy"].append({"type": "epic-parent", "domain": domain, "action": "noop"})

        for item in items:
            domain = item["fields"]["area"]
            parent_node_id = domain_parent.get(domain)
            child = resolved_issue.get(item["atom_id"])
            if not (parent_node_id and child and child.get("node_id")):
                continue
            pair_exists = (parent_node_id, child["node_id"]) in getattr(transport, "_sub_issues", [])
            plan["hierarchy"].append({
                "type": "sub-issue", "atom_id": item["atom_id"], "domain": domain,
                "action": "noop" if pair_exists else "add",
            })
            if not dry_run and not pair_exists:
                transport.add_sub_issue(issues_repo, parent_node_id, child["node_id"])

    # -- release.yaml -> milestone (graceful no-op absent, AC-PTC-3) ----------------------------- #
    release_path = os.path.join(root, "release.yaml")
    if os.path.isfile(release_path):
        try:
            with open(release_path, encoding="utf-8") as f:
                rel_doc = yaml.safe_load(f.read()) or {}
        except Exception:
            rel_doc = {}
        title = str(rel_doc.get("version") or rel_doc.get("name") or "release")
        existing_ms = transport.find_milestone(issues_repo, title)
        plan["milestones"].append({"title": title, "action": "noop" if existing_ms else "create"})
        if existing_ms is None and not dry_run:
            transport.create_milestone(issues_repo, title)

    return plan, cache_map


# ==================================================================================================== #
# Cache write (AC-PTC-8) + the top-level `sync()` gate (AC-PTC-5 inactive / AC-PTC-10 fail-closed /
# AC-PTC-6 env-only credential / AC-PTC-7 dry-run / AC-PTC-9 one-way — the ONLY local write below the
# gate is the cache file).
# ==================================================================================================== #

def _write_cache(path, cache_map):
    existing = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    existing.update(cache_map)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, sort_keys=True)
        f.write("\n")
    return existing


def sync(root, transport_factory, dry_run=False, cache_path=None):
    """The top-level reconcile entrypoint.

    `transport_factory(token) -> transport` is called ONLY after the config-active check (AC-PTC-5)
    and the credential-present check (AC-PTC-10) both pass — so an inactive or credential-absent run
    never constructs a transport and therefore issues literally zero GitHub calls.

    Returns a plain, JSON-serializable dict:
      {"status": "inactive", "reason": ...}
      {"status": "fail-closed", "reason": ...}          # names the missing env var
      {"status": "refused", "reason": ...}               # real transport, non-dry-run apply refused (§8 audit fix #2)
      {"status": "dry-run", "plan": {...}}               # zero mutation
      {"status": "applied", "plan": {...}, "cache": {...}, "cache_path": ...}

    None of these fields ever carries the credential value (AC-PTC-6) — the token is read into a
    local variable and passed only to `transport_factory`; it is never placed into `plan`, `cache`,
    or any returned string.
    """
    cfg = ptm.read_config(root)
    if not cfg.get("active"):
        return {"status": "inactive", "reason": cfg.get("reason")}

    auth_env = cfg.get("auth_env") or DEFAULT_AUTH_ENV
    token = os.environ.get(auth_env)
    if not token:
        return {
            "status": "fail-closed",
            "reason": f"github_projects.enabled is true but the credential env var {auth_env!r} is not set",
        }

    transport = transport_factory(token)

    # §8 audit fix #2: the REAL transport's get_field_value/create_milestone are unimplemented
    # residuals (no live target ships with this atom). Rather than let a real, non-dry-run apply
    # commit create_issue/add_project_item writes and only then hit one of those stubs mid-
    # sequence (partial, non-convergent Project state), refuse ATOMICALLY here — before ANY
    # transport method has been called on this run — whenever the constructed transport is the
    # live one and this is not a --dry-run. FakeGraphQLTransport has no
    # `assert_live_apply_ready` attribute, so --selftest (which only ever constructs
    # FakeGraphQLTransport) never reaches this branch.
    live_guard = getattr(transport, "assert_live_apply_ready", None)
    if not dry_run and callable(live_guard):
        try:
            live_guard()
        except RuntimeError as e:
            return {"status": "refused", "reason": str(e)}

    plan, cache_map = build_and_apply(root, cfg, transport, dry_run)

    if dry_run:
        return {"status": "dry-run", "plan": plan}

    resolved_cache_path = cache_path or os.path.join(root, ".foundry", "project-map.json")
    written = _write_cache(resolved_cache_path, cache_map)
    return {"status": "applied", "plan": plan, "cache": written, "cache_path": resolved_cache_path}


# ==================================================================================================== #
# --selftest — AC-PTC-1..10 over throwaway temp fixtures + FakeGraphQLTransport. Emits ONE
# `AC-PTC-<id> <label>: PASS` token per AC ONLY when the behavior actually computed PASS. Reuses
# PTM's own fixture builders (`_mk_atom` / `_write`) — a plain read-only import, not a modification.
# ==================================================================================================== #

def _emit(token, label, ok):
    print(f"AC-PTC-{token} {label}: {'PASS' if ok else 'FAIL'}")
    return ok


def _mk_root(prefix, org="acme", enabled=True, extra_gp=None, field_map=None, auth_env=DEFAULT_AUTH_ENV,
             issues_repo="acme/widgets"):
    tmp = tempfile.mkdtemp(prefix=prefix)
    gp = {"enabled": enabled, "org": org, "project_number": 7, "auth_env": auth_env}
    if issues_repo is not None:
        gp["issues_repo"] = issues_repo
    if field_map is not None:
        gp["field_map"] = field_map
    if extra_gp:
        gp.update(extra_gp)
    ptm._write(os.path.join(tmp, ".claude", "foundry-project.json"),
               json.dumps({"github_projects": gp}))
    return tmp


_DEFAULT_FIELD_MAP = {"status": "Status", "area": "Area", "control": "Control"}


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _selftest_ptc1():
    """AC-PTC-1: a second `sync` with unchanged inputs creates NO additional issue / project item
    for an atom already reconciled (idempotent upsert-by-marker)."""
    ok = True
    tmp = _mk_root("ptc-1-", field_map=_DEFAULT_FIELD_MAP)
    try:
        ptm._mk_atom(tmp, "foundry", "d1", "cap1", "atom-one", ["AC-X-1"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc1-token"
        try:
            transport = FakeGraphQLTransport()
            r1 = sync(tmp, lambda tok: transport)
            ok = ok and r1["status"] == "applied"
            creates_1 = transport.calls.count("create_issue")
            adds_1 = transport.calls.count("add_project_item")
            ok = ok and creates_1 == 1 and adds_1 == 1
            ok = ok and r1["plan"]["issues"][0]["action"] == "create"

            r2 = sync(tmp, lambda tok: transport)
            ok = ok and r2["status"] == "applied"
            creates_2 = transport.calls.count("create_issue")
            adds_2 = transport.calls.count("add_project_item")
            ok = ok and creates_2 == creates_1 and adds_2 == adds_1  # no duplicate
            ok = ok and r2["plan"]["issues"][0]["action"] == "noop"
            ok = ok and r2["plan"]["fields"] == []  # nothing diverged -> no new field-set entries
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc8():
    """AC-PTC-8: `sync` writes/updates `.foundry/project-map.json` (`<atom-id> -> issue-number`)."""
    ok = True
    tmp = _mk_root("ptc-8-", field_map=_DEFAULT_FIELD_MAP)
    try:
        ptm._mk_atom(tmp, "foundry", "d1", "cap1", "cache-atom", ["AC-X-1"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc8-token"
        try:
            transport = FakeGraphQLTransport()
            r = sync(tmp, lambda tok: transport)
            cache_path = os.path.join(tmp, ".foundry", "project-map.json")
            ok = ok and r["status"] == "applied" and os.path.isfile(cache_path)
            with open(cache_path, encoding="utf-8") as f:
                on_disk = json.load(f)
            ok = ok and on_disk.get("feat-cache-atom") == 1
            ok = ok and r["cache"].get("feat-cache-atom") == 1
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc2():
    """AC-PTC-2: `sync` attaches the atom's issue to the Project (`addProjectV2ItemById`) then sets
    the mapped fields (`updateProjectV2ItemFieldValue`), resolving each field_map role to a concrete
    field node-ID."""
    ok = True
    tmp = _mk_root("ptc-2-", field_map=_DEFAULT_FIELD_MAP)
    try:
        ptm._mk_atom(tmp, "foundry", "billing", "cap2", "field-atom", ["AC-Y-1", "AC-Y-2"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc2-token"
        try:
            transport = FakeGraphQLTransport()
            r = sync(tmp, lambda tok: transport)
            ok = ok and r["status"] == "applied"
            ok = ok and "add_project_item" in transport.calls
            ok = ok and "set_field_value" in transport.calls

            project_node_id = list(transport._project_node_ids.values())[0]
            item_id = list(transport._project_items[project_node_id].values())[0]
            fields = transport._item_fields[item_id]
            status_field_id = transport._fields[project_node_id]["Status"]["id"]
            area_field_id = transport._fields[project_node_id]["Area"]["id"]
            control_field_id = transport._fields[project_node_id]["Control"]["id"]
            # status/area are single-select -> resolved to an OPTION node-id, not the raw label.
            ok = ok and fields[status_field_id] == transport._fields[project_node_id]["Status"]["options"]["Authorized"]
            ok = ok and fields[area_field_id] == transport._fields[project_node_id]["Area"]["options"]["billing"]
            # control is text -> the raw comma-joined AC-ID string, unresolved.
            ok = ok and fields[control_field_id] == "AC-Y-1, AC-Y-2"
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc3():
    """AC-PTC-3: (a) neither a domain-parent nor a release.yaml -> hierarchy edges gracefully no-op
    (flat, idempotent, no error); (b) epic_parents enabled + a release.yaml present -> addSubIssue
    hierarchy + a release->milestone edge are built (and idempotent on a second run)."""
    ok = True

    # (a) graceful no-op branch.
    tmp_a = _mk_root("ptc-3a-", field_map=_DEFAULT_FIELD_MAP)
    try:
        ptm._mk_atom(tmp_a, "foundry", "flat-domain", "cap", "flat-atom", ["AC-Z-1"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc3a-token"
        try:
            transport = FakeGraphQLTransport()
            r = sync(tmp_a, lambda tok: transport)
            ok = ok and r["status"] == "applied"
            ok = ok and r["plan"]["hierarchy"] == [] and r["plan"]["milestones"] == []
            ok = ok and transport._sub_issues == [] and transport._milestones == {}
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp_a)

    # (b) hierarchy-built branch.
    tmp_b = _mk_root("ptc-3b-", field_map=_DEFAULT_FIELD_MAP, extra_gp={"epic_parents": True})
    try:
        ptm._mk_atom(tmp_b, "foundry", "hier-domain", "cap1", "hier-atom-1", ["AC-Z-2"], authorized=True)
        ptm._mk_atom(tmp_b, "foundry", "hier-domain", "cap2", "hier-atom-2", ["AC-Z-3"], authorized=True)
        ptm._write(os.path.join(tmp_b, "release.yaml"), "version: v9.9.9\n")
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc3b-token"
        try:
            transport = FakeGraphQLTransport()
            r1 = sync(tmp_b, lambda tok: transport)
            ok = ok and r1["status"] == "applied"
            ok = ok and len(transport._sub_issues) == 2
            ok = ok and transport._milestones.get("acme/widgets", {}).get("v9.9.9") == 1
            hierarchy_actions_1 = [h["action"] for h in r1["plan"]["hierarchy"]]
            ok = ok and "create" in hierarchy_actions_1 and "add" in hierarchy_actions_1

            r2 = sync(tmp_b, lambda tok: transport)  # idempotent: no duplicate edges
            ok = ok and len(transport._sub_issues) == 2
            ok = ok and len(transport._milestones.get("acme/widgets", {})) == 1
            hierarchy_actions_2 = [h["action"] for h in r2["plan"]["hierarchy"]]
            ok = ok and all(a == "noop" for a in hierarchy_actions_2)
            ok = ok and r2["plan"]["milestones"][0]["action"] == "noop"
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp_b)

    return ok


def _selftest_ptc4():
    """AC-PTC-4: a hand-edited (diverged) Project field is reset to the PTM-derived value on the
    next `sync` (self-heal)."""
    ok = True
    tmp = _mk_root("ptc-4-", field_map=_DEFAULT_FIELD_MAP)
    try:
        ptm._mk_atom(tmp, "foundry", "heal-domain", "cap", "heal-atom", ["AC-H-1"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc4-token"
        try:
            transport = FakeGraphQLTransport()
            r1 = sync(tmp, lambda tok: transport)
            ok = ok and r1["status"] == "applied"

            project_node_id = list(transport._project_node_ids.values())[0]
            item_id = list(transport._project_items[project_node_id].values())[0]
            status_field_id = transport._fields[project_node_id]["Status"]["id"]
            derived_value = transport._item_fields[item_id][status_field_id]

            # hand-edit: diverge the Status field from derived truth.
            transport._item_fields[item_id][status_field_id] = "HAND-EDITED-BOGUS-VALUE"
            set_calls_before = transport.calls.count("set_field_value")

            r2 = sync(tmp, lambda tok: transport)
            ok = ok and r2["status"] == "applied"
            set_calls_after = transport.calls.count("set_field_value")
            ok = ok and set_calls_after > set_calls_before  # a corrective set_field_value fired
            ok = ok and transport._item_fields[item_id][status_field_id] == derived_value  # healed back
            field_roles = [f["role"] for f in r2["plan"]["fields"] if f.get("atom_id") == "feat-heal-atom"]
            ok = ok and "status" in field_roles
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc9():
    """AC-PTC-9 (Invariant): a full `sync` run makes no write to any spec or acceptance-contract
    file — the ONLY local write is `.foundry/project-map.json`."""
    ok = True
    tmp = _mk_root("ptc-9-", field_map=_DEFAULT_FIELD_MAP)
    try:
        spec_path, contract_path = ptm._mk_atom(
            tmp, "foundry", "oneway-domain", "cap", "oneway-atom", ["AC-O-1"], authorized=True)

        def _snapshot(root, skip):
            snap = {}
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    p = os.path.join(dirpath, fn)
                    if p == skip:
                        continue
                    with open(p, "rb") as f:
                        snap[p] = f.read()
            return snap

        cache_path = os.path.join(tmp, ".foundry", "project-map.json")
        before = _snapshot(tmp, skip=cache_path)

        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc9-token"
        try:
            transport = FakeGraphQLTransport()
            r = sync(tmp, lambda tok: transport)
            ok = ok and r["status"] == "applied"
        finally:
            del os.environ[DEFAULT_AUTH_ENV]

        after = _snapshot(tmp, skip=cache_path)
        ok = ok and before == after  # every OTHER file byte-unchanged (no new/removed file either)
        with open(spec_path, "rb") as f:
            ok = ok and f.read() == before[spec_path]
        with open(contract_path, "rb") as f:
            ok = ok and f.read() == before[contract_path]
        ok = ok and os.path.isfile(cache_path)  # the one permitted local write did happen
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc5():
    """AC-PTC-5: `github_projects` absent or `enabled: false` -> `sync` reports "inactive", issues
    ZERO GitHub calls (the transport is never even constructed) and ZERO local writes."""
    ok = True

    def _forbidden_factory(token):
        raise AssertionError("transport MUST NOT be constructed while inactive")

    # (a) config file absent entirely.
    tmp_a = tempfile.mkdtemp(prefix="ptc-5a-")
    try:
        cache_path = os.path.join(tmp_a, ".foundry", "project-map.json")
        r = sync(tmp_a, _forbidden_factory, cache_path=cache_path)
        ok = ok and r["status"] == "inactive"
        ok = ok and not os.path.exists(cache_path)
    finally:
        _rmtree(tmp_a)

    # (b) config present but enabled: false.
    tmp_b = _mk_root("ptc-5b-", enabled=False)
    try:
        cache_path = os.path.join(tmp_b, ".foundry", "project-map.json")
        r = sync(tmp_b, _forbidden_factory, cache_path=cache_path)
        ok = ok and r["status"] == "inactive"
        ok = ok and not os.path.exists(cache_path)
    finally:
        _rmtree(tmp_b)

    return ok


def _selftest_ptc10():
    """AC-PTC-10: `github_projects.enabled: true` but the `auth_env`-named credential is absent ->
    `sync` fails CLOSED, naming the missing env var, with ZERO GitHub calls / mutations."""
    ok = True

    def _forbidden_factory(token):
        raise AssertionError("transport MUST NOT be constructed with no credential present")

    tmp = _mk_root("ptc-10-", auth_env="FOUNDRY_PROJECTS_TOKEN_CUSTOM")
    try:
        os.environ.pop("FOUNDRY_PROJECTS_TOKEN_CUSTOM", None)  # ensure truly absent
        cache_path = os.path.join(tmp, ".foundry", "project-map.json")
        r = sync(tmp, _forbidden_factory, cache_path=cache_path)
        ok = ok and r["status"] == "fail-closed"
        ok = ok and "FOUNDRY_PROJECTS_TOKEN_CUSTOM" in r.get("reason", "")
        ok = ok and not os.path.exists(cache_path)
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc6():
    """AC-PTC-6 (Invariant, hardened): a sentinel token is injected into `auth_env`; `sync` runs on
    BOTH the success path and a fake-transport-RAISES-error path; ALL captured stdout/stderr/plan/
    exception text is asserted free of the sentinel literal. A decoy value on a config `token` field
    is separately asserted never to be honored (never used as the transport token, never leaked)."""
    ok = True
    SENTINEL = "sentinel-DO-NOT-LEAK-9f3c7a1e"
    DECOY = "DECOY-CONFIG-TOKEN-never-used"
    tmp = _mk_root("ptc-6-", field_map=_DEFAULT_FIELD_MAP, extra_gp={"token": DECOY})
    try:
        ptm._mk_atom(tmp, "foundry", "leak-domain", "cap", "leak-atom", ["AC-L-1"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = SENTINEL
        try:
            received_tokens = []

            def _factory(tok):
                received_tokens.append(tok)
                return FakeGraphQLTransport()

            captured = []

            # -- success path -------------------------------------------------------------------- #
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                r_success = sync(tmp, _factory, dry_run=True)
            captured.append(buf_out.getvalue())
            captured.append(buf_err.getvalue())
            captured.append(json.dumps(r_success, default=str))

            # -- error path (fake transport RAISES) ----------------------------------------------- #
            def _factory_raise(tok):
                received_tokens.append(tok)
                return FakeGraphQLTransport(raise_on="create_issue")

            buf_out2, buf_err2 = io.StringIO(), io.StringIO()
            exc_text = ""
            with contextlib.redirect_stdout(buf_out2), contextlib.redirect_stderr(buf_err2):
                try:
                    sync(tmp, _factory_raise, dry_run=False)
                except Exception as e:
                    exc_text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            captured.append(buf_out2.getvalue())
            captured.append(buf_err2.getvalue())
            captured.append(exc_text)
            ok = ok and exc_text != ""  # the error path actually raised (not a vacuous pass)

            all_text = "\n".join(captured)
            ok = ok and SENTINEL not in all_text
            ok = ok and DECOY not in all_text

            # env var is the ONLY credential source: both factory invocations received the sentinel,
            # never the decoy.
            ok = ok and received_tokens and all(t == SENTINEL for t in received_tokens)

            # a decoy on a --token-style CLI flag is not honored either: the CLI defines NO --token
            # flag at all, so argparse structurally REJECTS it (SystemExit, no parsed namespace) —
            # there is no code path through which such a flag's value could ever reach `sync()` /
            # `transport_factory` as a credential (argparse's own usage/error text is expected to
            # echo the rejected arg back — that's ordinary CLI UX, not a credential leak; the decoy
            # here was never a secret to begin with, only the SENTINEL env-var value is).
            parser = _build_arg_parser()
            buf_err3 = io.StringIO()
            with contextlib.redirect_stderr(buf_err3):
                try:
                    parser.parse_args(["--dry-run", "--token", "DECOY-FLAG-VALUE"])
                    flag_rejected = False
                except SystemExit:
                    flag_rejected = True
            ok = ok and flag_rejected
            ok = ok and SENTINEL not in buf_err3.getvalue()  # the REAL credential never appears
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp)
    return ok


def _selftest_ptc7():
    """AC-PTC-7: `--dry-run` emits the full projection plan (issues / fields / hierarchy it WOULD
    write) and performs ZERO GitHub mutation."""
    ok = True
    tmp = _mk_root("ptc-7-", field_map=_DEFAULT_FIELD_MAP)
    try:
        ptm._mk_atom(tmp, "foundry", "dry-domain", "cap", "dry-atom", ["AC-D-1"], authorized=True)
        os.environ[DEFAULT_AUTH_ENV] = "sentinel-ptc7-token"
        try:
            transport = FakeGraphQLTransport()
            cache_path = os.path.join(tmp, ".foundry", "project-map.json")
            r = sync(tmp, lambda tok: transport, dry_run=True, cache_path=cache_path)
            ok = ok and r["status"] == "dry-run"
            ok = ok and len(r["plan"]["issues"]) == 1 and r["plan"]["issues"][0]["action"] == "create"
            ok = ok and len(r["plan"]["fields"]) >= 1  # would-set fields are visible in the plan
            ok = ok and transport.mutation_count() == 0  # zero WRITE calls
            ok = ok and not os.path.exists(cache_path)  # no local write either
        finally:
            del os.environ[DEFAULT_AUTH_ENV]
    finally:
        _rmtree(tmp)
    return ok


def selftest():
    results = [
        _emit("1", "idempotent-upsert-by-marker", _selftest_ptc1()),
        _emit("2", "add-item-and-set-fields", _selftest_ptc2()),
        _emit("3", "hierarchy-subissues-milestones", _selftest_ptc3()),
        _emit("4", "selfheal-diverged-field", _selftest_ptc4()),
        _emit("5", "inactive-no-op", _selftest_ptc5()),
        _emit("6", "no-token-leak", _selftest_ptc6()),
        _emit("7", "dry-run-plan-no-mutation", _selftest_ptc7()),
        _emit("8", "project-map-cache-write", _selftest_ptc8()),
        _emit("9", "one-way-no-file-write", _selftest_ptc9()),
        _emit("10", "fail-closed-no-credential", _selftest_ptc10()),
    ]
    return 0 if all(results) else 1


# ==================================================================================================== #
# CLI. Deliberately NO --token flag: the credential's only channel is the `auth_env`-named
# environment variable (AC-PTC-6) — there is nothing here for a decoy to be honored by.
# ==================================================================================================== #

def _build_arg_parser():
    import argparse
    ap = argparse.ArgumentParser(
        description="foundry-project-sync — the GraphQL projector CLI (PTC, ER #112 atom 2 of 3)"
    )
    ap.add_argument("--root", help="override the resolved adopter root (default: CLAUDE_PROJECT_DIR)")
    ap.add_argument("--dry-run", action="store_true",
                     help="emit the projection plan only; perform no GitHub mutation")
    ap.add_argument("--selftest", action="store_true", help="run AC-PTC-1..10 over temp fixtures")
    return ap


def main(argv=None):
    args = _build_arg_parser().parse_args(argv)
    if args.selftest:
        return selftest()

    root = ptm._resolve_root(args.root)
    result = sync(root, lambda token: GitHubGraphQLTransport(token), dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 1 if result["status"] in ("fail-closed", "refused") else 0


if __name__ == "__main__":
    sys.exit(main())
