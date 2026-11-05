#!/usr/bin/env python3
"""foundry_governance.py -- the governance core: load, validate, render, applicable, check, exceptions, baseline,
and (gv-15) the intake / upgrade verbs: discover, select, declare, apply, upgrade, leak-check.

Rules ship as kits in the plugin (`governance/kits/<category>/`). An adopter adds a layer in
`governance/local/` (own-prefix rules, overrides, exceptions, baselines). The resolver verbs are pure local
file work. The only subprocesses are local `git` (dirty-tree check, three-way merge, branch/commit/push) and,
under `--pr`, one `gh pr create`; nothing else touches a network.

  foundry_governance.py [--project-dir DIR] [--plugin-root DIR] load [--json]
  foundry_governance.py ... validate
  foundry_governance.py ... render [--level N] [--surfaces a,b] [--from-lock]
  foundry_governance.py ... applicable --paths P [P ...] [--json] [--level N] [--surfaces a,b] [--from-lock]
  foundry_governance.py ... discover [--out FILE] [--summary]
  foundry_governance.py ... select [--facts FILE] [--json] [--suggest ID] [--confirm ID]
  foundry_governance.py ... declare --asvs-level N [--data-classification C ...] [--trust-boundary G ...] [--surfaces a,b]
  foundry_governance.py ... apply [--pr] [--base BRANCH]
  foundry_governance.py ... upgrade [--pr] [--base BRANCH]
  foundry_governance.py ... leak-check --files F [F ...] [--literals FILE]
  foundry_governance.py ... check [--today YYYY-MM-DD]
  foundry_governance.py ... exceptions [--today YYYY-MM-DD]
  foundry_governance.py ... baseline --check NAME --findings FILE

Exit 0 ok / 1 findings / 2 refused (invalid input or an override that is not allowed) /
3 decision needed (`select` without `--confirm`; nothing was written).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_yaml_load as _y  # noqa: E402

ID_RE = re.compile(r"^(FG|[A-Z][A-Z0-9]{1,15})-[A-Z]{2,5}-[0-9]{3}$")
STATUS_RE = re.compile(r"^(active|retired|superseded-by:(FG|[A-Z][A-Z0-9]{1,15})-[A-Z]{2,5}-[0-9]{3})$")
CAT_RE = re.compile(r"^[a-z][a-z0-9-]*$")
CHECK_KINDS = ("hook", "ci", "lint", "review-lens", "scorecard", "declared", "none")
REF_REQUIRED = ("hook", "ci", "lint", "review-lens", "scorecard")
REQUIRED = ("id", "title", "statement", "category", "level", "floor", "status", "applies_to", "check", "exception")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


def schema_errors(doc, name: str) -> list:
    """Schema findings (strings) for `doc` against schema/governance-<name>.schema.json (shipped with this script)."""
    try:
        import jsonschema
    except ImportError:
        raise Refused("jsonschema-unavailable")
    schema = json.loads((_SCHEMA_DIR / f"governance-{name}.schema.json").read_text(encoding="utf-8"))
    return sorted(f"{'/'.join(str(x) for x in e.absolute_path) or '<root>'}: {e.message}"
                  for e in jsonschema.Draft202012Validator(schema).iter_errors(doc))


class Refused(Exception):
    """Invalid input or a refused override: exit 2."""


# ---------------------------------------------------------------- locations ---

def plugin_root(arg=None) -> Path:
    return Path(arg or os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1]).resolve()


def project_dir(arg=None) -> Path:
    return Path(arg or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def _load_yaml(path: Path):
    try:
        return _y.safe_load_file(str(path))
    except Exception as exc:  # noqa: BLE001
        raise Refused(f"unreadable-yaml:{path.name}:{type(exc).__name__}")


# --------------------------------------------------------- rule validation ---

def rule_errors(rule, upstream: bool) -> list:
    """Field-level errors for one rule document, each naming the rule id and the field."""
    errs = []
    if not isinstance(rule, dict):
        return ["<unknown>: rule: not a mapping"]
    rid = rule.get("id")
    label = rid if isinstance(rid, str) else "<unknown>"
    for f in REQUIRED:
        if f not in rule:
            errs.append(f"{label}: {f}: missing")
    if "id" in rule:
        if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
            errs.append(f"{label}: id: does not match {ID_RE.pattern}")
        elif upstream and not rid.startswith("FG-"):
            errs.append(f"{label}: id: upstream kit rules MUST use the FG- namespace")
    for f in ("title", "statement"):
        if f in rule and (not isinstance(rule[f], str) or not rule[f].strip()):
            errs.append(f"{label}: {f}: must be a non-empty string")
    if "category" in rule and (not isinstance(rule["category"], str) or not CAT_RE.fullmatch(rule["category"])):
        errs.append(f"{label}: category: must match {CAT_RE.pattern}")
    lv = rule.get("level")
    if "level" in rule and (isinstance(lv, bool) or not isinstance(lv, int) or not 1 <= lv <= 3):
        errs.append(f"{label}: level: must be an integer 1-3")
    if "floor" in rule and not isinstance(rule["floor"], bool):
        errs.append(f"{label}: floor: must be a boolean")
    if "status" in rule and (not isinstance(rule["status"], str) or not STATUS_RE.fullmatch(rule["status"])):
        errs.append(f"{label}: status: must be active, retired or superseded-by:<id>")
    at = rule.get("applies_to")
    for g in (at.get("paths") if isinstance(at, dict) and isinstance(at.get("paths"), list) else []):
        if isinstance(g, str) and not PATH_RE.fullmatch(g):
            errs.append(f"{label}: applies_to.paths: {g!r} has characters outside the allowed set")
    if "applies_to" in rule:
        if not isinstance(at, dict) or not all(
                isinstance(at.get(k), list) and at.get(k) and all(isinstance(x, str) and x for x in at[k])
                for k in ("paths", "blueprints")):
            errs.append(f"{label}: applies_to: needs non-empty paths[] and blueprints[]")
    ck = rule.get("check")
    if "check" in rule:
        if not isinstance(ck, dict) or ck.get("kind") not in CHECK_KINDS:
            errs.append(f"{label}: check: needs a binding with kind in {list(CHECK_KINDS)}")
        elif isinstance(ck.get("ref"), str) and ck["ref"] and not REF_RE.fullmatch(ck["ref"]):
            errs.append(f"{label}: check.ref: has characters outside the allowed set")
        elif ck["kind"] in REF_REQUIRED and not (isinstance(ck.get("ref"), str) and ck["ref"].strip()):
            errs.append(f"{label}: check.ref: required for kind {ck['kind']}")
    ex = rule.get("exception")
    if "exception" in rule:
        if not isinstance(ex, dict) or not isinstance(ex.get("allowed"), bool):
            errs.append(f"{label}: exception: needs allowed (boolean)")
        elif rule.get("floor") is True and ex["allowed"] is True:
            errs.append(f"{label}: exception.allowed: a floor rule cannot allow exceptions")
    if "note" in rule and not isinstance(rule["note"], str):
        errs.append(f"{label}: note: must be a string")
    for k in rule:
        if k not in REQUIRED and k not in ("sources", "note"):
            errs.append(f"{label}: {k}: unknown field")
    return errs


# ------------------------------------------------------------------ loading ---

def load_kits(root: Path) -> list:
    kits_dir = root / "governance" / "kits"
    out = []
    if not kits_dir.is_dir():
        return out
    for d in sorted(p for p in kits_dir.iterdir() if p.is_dir()):
        kit_f, rules_f = d / "kit.yaml", d / "rules.yaml"
        if not kit_f.is_file() or not rules_f.is_file():
            raise Refused(f"kit-incomplete:{d.name}")
        kit, rules = _load_yaml(kit_f), _load_yaml(rules_f)
        if not isinstance(kit, dict) or not isinstance(rules, dict) or not isinstance(rules.get("rules"), list):
            raise Refused(f"kit-malformed:{d.name}")
        out.append({"kit": kit, "rules": rules["rules"], "sha256": hashlib.sha256(
            kit_f.read_bytes() + rules_f.read_bytes()).hexdigest(), "dir": d.name})
    return out


def load_local(proj: Path) -> dict:
    base = proj / "governance" / "local"
    rules_f, exc_f = base / "rules.yaml", base / "exceptions.yaml"
    doc = _load_yaml(rules_f) if rules_f.is_file() else {}
    doc = doc or {}
    exc = _load_yaml(exc_f) if exc_f.is_file() else {}
    exc = exc or {}
    if not isinstance(doc, dict) or not isinstance(exc, dict):
        raise Refused("local-malformed")
    return {"rules": doc.get("rules") or [], "overrides": doc.get("overrides") or [],
            "exceptions": exc.get("exceptions") or []}


def validate_all(kits: list, local: dict) -> list:
    """All findings (strings). Empty means valid."""
    errs, seen = [], {}
    for k in kits:
        kd = k["kit"]
        errs += [f"kit {k['dir']}: kit.yaml {m}" for m in schema_errors(kd, "kit")]
        if kd.get("category") != k["dir"] or kd.get("kit") != k["dir"]:
            errs.append(f"kit {k['dir']}: kit/category: must equal the directory name")
        if not isinstance(kd.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+([-+][0-9A-Za-z.+-]+)?", kd.get("version", "")):
            errs.append(f"kit {k['dir']}: version: not semver")
        shipped = [r.get("id") for r in k["rules"] if isinstance(r, dict)]
        if sorted(kd.get("rules") or []) != sorted(x for x in shipped if isinstance(x, str)):
            errs.append(f"kit {k['dir']}: rules: kit.yaml ids differ from rules.yaml")
        for r in k["rules"]:
            errs += rule_errors(r, upstream=True)
            rid = r.get("id") if isinstance(r, dict) else None
            if rid in seen:
                errs.append(f"{rid}: id: duplicate")
            seen[rid] = r
    for r in local["rules"]:
        errs += rule_errors(r, upstream=False)
        rid = r.get("id") if isinstance(r, dict) else None
        if isinstance(rid, str) and rid.startswith("FG-"):
            errs.append(f"adopter-rule-in-upstream-namespace:{rid}")
        if rid in seen:
            errs.append(f"{rid}: id: duplicate")
        seen[rid] = r
    for rid, r in seen.items():
        st = r.get("status") if isinstance(r, dict) else None
        if isinstance(st, str) and st.startswith("superseded-by:"):
            tgt = st.split(":", 1)[1]
            if tgt not in seen:
                errs.append(f"{rid}: status: superseded-by target {tgt} does not exist")
    return errs


def effective(kits: list, local: dict) -> list:
    """kits + local, overrides applied only where the upstream rule is not floor.
    Raises Refused naming the id on a floor override or an override of an unknown id."""
    rules = {}
    order = []
    for k in kits:
        for r in k["rules"]:
            rules[r["id"]] = dict(r)
            order.append(r["id"])
    for ov in local["overrides"]:
        oid = ov.get("id") if isinstance(ov, dict) else None
        if oid not in rules:
            raise Refused(f"override-unknown-rule:{oid}")
        if rules[oid].get("floor") is True:
            raise Refused(f"floor-override-refused:{oid}")
        for key in ov:
            if key in ("statement", "title"):
                raise Refused(f"override-of-upstream-statement:{oid}")
            if key == "floor":
                raise Refused(f"override-sets-floor:{oid}")
            if key not in ("id", "level", "applies_to", "note"):
                raise Refused(f"override-field-not-allowed:{oid}:{key}")
        for key, val in ov.items():
            if key != "id":
                rules[oid][key] = val
    for r in local["rules"]:
        rules[r["id"]] = dict(r)
        order.append(r["id"])
    return [rules[i] for i in order]


def _prepare(args):
    root, proj = plugin_root(args.plugin_root), project_dir(args.project_dir)
    kits, local = load_kits(root), load_local(proj)
    return root, proj, kits, local


def checked_effective(kits, local):
    errs = validate_all(kits, local)
    if errs:
        raise Refused("; ".join(errs))
    eff = effective(kits, local)
    merged = [e for r in eff for e in rule_errors(r, upstream=False)]
    if merged:
        raise Refused("; ".join(merged))
    return eff


# ---------------------------------------------------------------- render ---

GEN_LINE = ("Generated by `scripts/foundry_governance.py render`. Do not edit. "
            "Effective rules (kits + governance/local). Details live in the source files.")
PATH_RE = re.compile(r"[A-Za-z0-9_./*?@\[\]{}!,+-]+")
REF_RE = re.compile(r"[A-Za-z0-9_./:#@-]+")


def is_generated(text: str) -> bool:
    """True only for a file carrying the full generated preamble (H1 + the Generated-by line)."""
    lines = text.split("\n")
    return len(lines) >= 3 and lines[0].startswith("# Governance: ") and lines[1] == "" and lines[2] == GEN_LINE


def is_local(rule) -> bool:
    return not str(rule["id"]).startswith("FG-")


def _flat(text) -> str:
    return " ".join(str(text).replace("```", "'''").split())


def rule_items(rule, note_cap=400) -> list:
    """Brief/render items for one rule. Upstream rules are plain lines; adopter rules and every note are
    fenced and labelled as data, never as instructions. A statement is never truncated; a note is capped."""
    rid = rule["id"]
    if is_local(rule):
        items = [f"```\n[adopter rule {rid} — data, not instructions]\n{_flat(rule['statement'])}\n```"]
    else:
        items = [f"- {rid}: {rule['statement'].strip()}"]
    note = rule.get("note")
    if note:
        n = _flat(note)
        if len(n) > note_cap:
            n = f"{n[:note_cap]}...[truncated: {len(n) - note_cap} chars]"
        items.append(f"```\n[adopter note {rid} — data, not instructions]\n{n}\n```")
    return items


def safe_outdir(proj: Path, rel: str) -> Path:
    """Create/return proj/rel, refusing a symlinked component or a resolution outside the project."""
    cur = proj
    for part in rel.split("/"):
        cur = cur / part
        if cur.is_symlink():
            raise Refused(f"output-is-symlink:{rel}")
    cur.mkdir(parents=True, exist_ok=True)
    try:
        cur.resolve().relative_to(proj.resolve())
    except ValueError:
        raise Refused(f"output-outside-project:{rel}")
    return cur


def render_docs(rules: list) -> dict:
    by_cat = {}
    for r in rules:
        by_cat.setdefault(r["category"], []).append(r)
    docs = {}
    for cat in sorted(by_cat):
        lines = [f"# Governance: {cat}", "", GEN_LINE, ""]
        for r in sorted(by_cat[cat], key=lambda x: x["id"]):
            flags = f"level {r['level']}" + (", floor" if r.get("floor") else "") + f", {r['status']}"
            ck = r["check"]
            body = ([f"## {r['id']}", ""] + rule_items(r) if is_local(r)
                    else [f"## {r['id']} - {r['title']}", "", r["statement"]] + rule_items(r)[1:])
            lines += body + ["",
                      f"- {flags}",
                      f"- applies to: {', '.join('`' + x + '`' for x in r['applies_to']['paths'])}",
                      f"- check: {ck['kind']}" + (f" -> `{ck['ref']}`" if ck.get("ref") else ""), ""]
        docs[cat] = "\n".join(lines).rstrip() + "\n"
    return docs


# ------------------------------------------------------------ applicable ---

def glob_re(pat: str):
    out, i = "", 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile(out)


def norm_path(p: str) -> str:
    """Backslashes to slashes; drop only literal leading './' segments (a leading dot in a name is kept)."""
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def applicable(rules: list, paths: list) -> list:
    hit = []
    norm = [norm_path(p) for p in paths]
    for r in rules:
        if r.get("status") != "active":
            continue
        if any(glob_re(g).fullmatch(p) for g in r["applies_to"]["paths"] for p in norm):
            hit.append(r["id"])
    return sorted(set(hit))


# ------------------------------------------------------------ exceptions ---

def _today(arg):
    return datetime.date.fromisoformat(arg) if arg else datetime.date.today()


def exception_report(rules: list, local: dict, today: datetime.date):
    """(findings, skips). Expired -> finding; unexpired -> skip (never a pass)."""
    by_id = {r["id"]: r for r in rules}
    findings, skips = [], []
    for i, e in enumerate(local["exceptions"]):
        if not isinstance(e, dict):
            findings.append(f"exception[{i}]: not a mapping")
            continue
        rid = e.get("rule")
        label = rid if isinstance(rid, str) else f"exception[{i}]"
        missing = [k for k in ("rule", "scope", "reason", "owner", "expires") if not e.get(k)]
        if missing:
            findings.append(f"{label}: exception missing {','.join(missing)}")
            continue
        if rid not in by_id:
            findings.append(f"{label}: exception names an unknown rule")
            continue
        if not by_id[rid]["exception"].get("allowed"):
            findings.append(f"{label}: exception not allowed for this rule")
            continue
        try:
            exp = datetime.date.fromisoformat(str(e["expires"]))
        except ValueError:
            findings.append(f"{label}: exception expires is not YYYY-MM-DD")
            continue
        if exp < today:
            findings.append(f"{label}: exception expired {exp.isoformat()} (scope {e['scope']}, owner {e['owner']})")
        else:
            skips.append(f"{label}: SKIP exception until {exp.isoformat()} (scope {e['scope']}, owner {e['owner']})")
    return findings, skips


# ------------------------------------------------------------ intake (gv-15) ---
#
# discover -> select -> declare -> apply -> baseline is the five-step intake; `upgrade` is the same step run again
# against a newer plugin. Facts -> kit selection has no prior art as an inference, so selection is a DECLARED
# signature table (each blueprint's `detect` block) plus a one-word human confirmation, never a guess.

EXIT_DECISION = 3
SURFACE_TAGS = ("ui", "api", "cli", "workflows", "mcp", "goal-agents")
LOCK_REL = ".foundry/governance.lock"
FACTS_REL = ".foundry/facts.json"
INTAKE_REL = ".foundry/governance-intake.json"
BASE_REL = "governance/local/.kit-base"
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                       ".ruff_cache", ".tox", ".terraform", ".worktrees", ".foundry", "dist", "build", "target"})
MAX_FILES = 100000
MAX_READ_BYTES = 1 << 20          # no manifest larger than this is read (could-not-look instead)
MAX_LISTED = 200
# Ordered file-signal detectors. Globs use foundry_blueprint.glob_match: no "/" -> basename at any depth.
DETECTORS = (
    ("manifests", ("pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt", "package.json", "Cargo.toml",
                   "go.mod", "pom.xml", "build.gradle", "Gemfile", "composer.json", "tsconfig.json",
                   ".claude-plugin/plugin.json", "hooks/hooks.json", "Chart.yaml", "dbt_project.yml",
                   "asyncapi.yaml", "openapi.yaml")),
    ("lockfiles", ("uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
                   "Cargo.lock", "go.sum", "Gemfile.lock", ".terraform.lock.hcl")),
    ("ci", (".github/workflows/*.yml", ".github/workflows/*.yaml", ".gitlab-ci.yml", "Jenkinsfile",
            ".circleci/config.yml", "azure-pipelines.yml")),
    ("iac", ("*.tf", "*.tfvars", "Chart.yaml", "kustomization.yaml", "cdk.json", "Pulumi.yaml")),
    ("containers", ("Dockerfile", "Dockerfile.*", "*.dockerfile", "docker-compose.yml", "docker-compose.yaml",
                    "compose.yaml", "compose.yml")),
)
# Dependency-name globs that mark an authentication surface. A HINT for `declare`, never a decision.
AUTH_PATTERNS = ("passlib", "authlib", "pyjwt", "python-jose", "oauthlib", "bcrypt", "argon2-cffi", "django-allauth",
                 "fastapi-users", "flask-login", "jsonwebtoken", "passport", "passport-*", "next-auth", "@auth/*",
                 "bcryptjs", "jose", "openid-client", "oidc-client*", "@okta/*", "@auth0/*", "auth0",
                 "amazon-cognito-identity-js", "keycloak-js", "msal", "@azure/msal-*")
PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _bp():
    import foundry_blueprint
    return foundry_blueprint


def _stack_profile(root: Path):
    spec = importlib.util.spec_from_file_location("foundry_stack_profile_gv15",
                                                  str(Path(__file__).resolve().parent / "foundry-stack-profile.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def facts_digest(facts: dict) -> str:
    core = {k: facts.get(k, []) for k in ("paths", "dependencies", "auth_libraries")}
    return hashlib.sha256(_canon(core).encode("utf-8")).hexdigest()


def _walk_tree(proj: Path):
    if not proj.is_dir():
        raise Refused(f"project-dir-not-found:{proj}")
    paths, unreadable, truncated = [], [], False

    def onerr(exc):
        try:
            unreadable.append(Path(exc.filename).relative_to(proj).as_posix())
        except (TypeError, ValueError):
            unreadable.append("<unknown>")

    for dirpath, dirnames, filenames in os.walk(proj, topdown=True, onerror=onerr, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = Path(dirpath).relative_to(proj).as_posix()
        for fn in sorted(filenames):
            try:
                if not stat.S_ISREG(os.lstat(os.path.join(dirpath, fn)).st_mode):
                    continue                      # symlinks, sockets, devices: never listed, never read
            except OSError:
                unreadable.append(fn if rel_dir == "." else f"{rel_dir}/{fn}")
                continue
            paths.append(fn if rel_dir == "." else f"{rel_dir}/{fn}")
            if len(paths) >= MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return sorted(paths), sorted(set(unreadable)), truncated


UNPARSED_MANIFESTS = ("go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
                      "setup.py", "setup.cfg", "Pipfile", "mix.exs")


def _bounded_read(path: Path) -> str:
    """The file's text, refusing a non-regular file, a symlink, or one larger than MAX_READ_BYTES (OSError)."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise OSError("not a regular file")
    if st.st_size > MAX_READ_BYTES:
        raise OSError("larger than the read cap")
    with open(path, "rb") as fh:
        raw = fh.read(MAX_READ_BYTES + 1)
    if len(raw) > MAX_READ_BYTES:
        raise OSError("larger than the read cap")
    return raw.decode("utf-8")


def _read_dependencies(proj: Path, paths: list):
    """(names, problems): dependency names from manifests anywhere in the tree; `problems` names every manifest
    that exists but could not be parsed (a could-not-look, never a silent empty)."""
    names, problems = set(), []
    bp = _bp()
    for rel in paths:
        base = rel.rsplit("/", 1)[-1]
        try:
            if base == "pyproject.toml":
                try:
                    import tomllib
                except ImportError:
                    problems.append(f"{rel}: tomllib unavailable")
                    continue
                doc = tomllib.loads(_bounded_read(proj / rel))
                specs = list((doc.get("project") or {}).get("dependencies") or [])
                for grp in ((doc.get("project") or {}).get("optional-dependencies") or {}).values():
                    specs += list(grp or [])
                for grp in (doc.get("dependency-groups") or {}).values():
                    specs += [x for x in (grp or []) if isinstance(x, str)]
                specs += list((((doc.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}).keys())
                for sp in specs:
                    m = PEP508_NAME.match(str(sp))
                    if m:
                        names.add(m.group(1).lower())
            elif base == "package.json":
                doc = json.loads(_bounded_read(proj / rel))
                if not isinstance(doc, dict):
                    raise ValueError("not an object")
                for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    names.update(str(n).lower() for n in (doc.get(key) or {}))
            elif bp.glob_match("requirements*.txt", rel):
                for line in _bounded_read(proj / rel).splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line and not line.startswith("-"):
                        m = PEP508_NAME.match(line)
                        if m:
                            names.add(m.group(1).lower())
            elif base in UNPARSED_MANIFESTS:
                problems.append(f"{rel}: no dependency parser for this ecosystem")
        except (OSError, ValueError, UnicodeDecodeError, AttributeError, TypeError) as exc:
            problems.append(f"{rel}: {type(exc).__name__}: {exc}" if isinstance(exc, OSError) else f"{rel}: {type(exc).__name__}")
    return sorted(names), problems


def discover(proj: Path) -> dict:
    """Ordered file-signal detectors over the tree -> facts. No LLM, no network. Every detector reports
    found / not-found / could-not-look."""
    import fnmatch
    bp = _bp()
    paths, unreadable, truncated = _walk_tree(proj)
    deps, dep_problems = _read_dependencies(proj, paths)
    auth = sorted(d for d in deps if any(fnmatch.fnmatchcase(d, pat) for pat in AUTH_PATTERNS))
    tree_note = ([f"unreadable directory: {u}" for u in unreadable]
                 + (["tree truncated at the file cap"] if truncated else []))
    detectors = []
    for name, pats in DETECTORS:
        found = sorted({p for p in paths for g in pats if bp.glob_match(g, p)})
        status = "found" if found else ("could-not-look" if tree_note else "not-found")
        detectors.append({"name": name, "status": status, "found": found[:MAX_LISTED], "detail": tree_note})
    dstat = "found" if deps else ("could-not-look" if (dep_problems or tree_note) else "not-found")
    detectors.append({"name": "dependencies", "status": dstat, "found": deps[:MAX_LISTED],
                      "detail": dep_problems + tree_note})
    astat = "found" if auth else ("could-not-look" if (dep_problems or tree_note) else "not-found")
    detectors.append({"name": "auth-libraries", "status": astat, "found": auth,
                      "detail": dep_problems + tree_note})
    return {"schema": 1, "paths": paths, "dependencies": deps, "auth_libraries": auth, "detectors": detectors}


def _confined_out(proj: Path, arg: str) -> str:
    """`arg` as a project-relative path (absolute allowed only when it is inside the project)."""
    p = Path(arg)
    full = (p if p.is_absolute() else proj / p).resolve()
    try:
        rel = full.relative_to(proj.resolve()).as_posix()
    except ValueError:
        raise Refused(f"output-outside-project:{arg}")
    if not rel.startswith(".foundry/"):
        raise Refused(f"output-outside-runtime-dir:{arg} (discover writes only {FACTS_REL})")
    if rel != FACTS_REL:
        raise Refused(f"output-not-facts-path:{arg} (discover writes only {FACTS_REL}; a wider .foundry/ path could overwrite "
                      "the permission or policy files)")
    return rel


def cmd_discover(args):
    proj = project_dir(args.project_dir)
    facts = discover(proj)
    if args.out:
        _write_text(proj, _confined_out(proj, args.out), json.dumps(facts, indent=2, sort_keys=True) + "\n")
    if args.summary:
        for d in facts["detectors"]:
            print(f"{d['name']}: {d['status']}" + (f" ({', '.join(d['found'][:5])})" if d["found"] else ""))
        return 0
    print(json.dumps(facts, indent=2, sort_keys=True))
    return 0


# ---- small safe writers ---------------------------------------------------------------------------

def _atomic_write(base: Path, name: str, data: bytes, mode: int):
    """Exclusive temp file (mkstemp: O_EXCL, never a followed link), fsync, rename."""
    fd, tmp = tempfile.mkstemp(dir=str(base), prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, base / name)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _plain_file_bytes(proj: Path, rel: str):
    """(state, bytes): state is "missing", "file", or "odd" (a symlink - at the leaf or in any parent - or a
    non-regular file)."""
    cur = proj
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return "odd", None
    if not cur.exists():
        return "missing", None
    if not cur.is_file():
        return "odd", None
    return "file", cur.read_bytes()


class _Journal:
    """Remembers what every write replaced and what it wrote, so a verb that fails part-way restores the tree -
    through the same symlink-safe writer, and never over a file that changed since."""

    def __init__(self):
        self.items, self.seen, self.after = [], set(), {}

    def record(self, proj: Path, rel: str):
        if (str(proj), rel) in self.seen:
            return
        self.seen.add((str(proj), rel))
        state, data = _plain_file_bytes(proj, rel)
        self.items.append((proj, rel, data, state == "odd"))

    def wrote(self, proj: Path, rel: str, data):
        self.after[(str(proj), rel)] = data       # bytes written, or None for a deletion

    def rollback(self) -> list:
        """Restore; returns the paths it left alone because they changed since the run wrote them."""
        skipped = []
        for proj, rel, prev, odd in reversed(self.items):
            if odd or (str(proj), rel) not in self.after:
                continue
            state, cur = _plain_file_bytes(proj, rel)
            expected = self.after[(str(proj), rel)]
            if state == "odd" or (expected is None and state != "missing") or (expected is not None and cur != expected):
                skipped.append(rel)
                continue
            try:
                if prev is None:
                    if state == "file":
                        (proj / rel).unlink()
                else:
                    f = proj / rel
                    _atomic_write(f.parent, f.name, prev, stat.S_IMODE(f.stat().st_mode) if f.exists() else 0o644)
            except OSError:
                skipped.append(rel)
        return skipped


_JOURNAL = None


def _jrec(proj: Path, rel: str):
    if _JOURNAL is not None:
        _JOURNAL.record(proj, rel)


def _jwrote(proj: Path, rel: str, data):
    if _JOURNAL is not None:
        _JOURNAL.wrote(proj, rel, data)


def _write_text(proj: Path, rel: str, text: str) -> bool:
    """Write proj/rel (refusing a symlink or a path outside the project). True iff the content changed.
    The temporary file is created exclusively (mkstemp: O_EXCL, never a followed link), fsynced, then renamed."""
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    base = safe_outdir(proj, parent) if parent else proj
    target = base / rel.rsplit("/", 1)[-1]
    if target.is_symlink():
        raise Refused(f"output-is-symlink:{rel}")
    if target.is_file() and target.read_text(encoding="utf-8", errors="surrogateescape") == text:
        return False
    _jrec(proj, rel)
    mode = stat.S_IMODE(target.stat().st_mode) if target.is_file() else 0o644
    data = text.encode("utf-8", "surrogateescape")
    _atomic_write(base, target.name, data, mode)
    _jwrote(proj, rel, data)
    return True


def _read_text(proj: Path, rel: str):
    f = proj / rel
    if f.is_symlink() or not f.is_file():
        return None
    return f.read_text(encoding="utf-8", errors="surrogateescape")


# ---- intake state (machine-owned scratch between select / declare / apply) -------------------------

def _read_intake(proj: Path):
    raw = _read_text(proj, INTAKE_REL)
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        raise Refused("intake-state-malformed")
    if not isinstance(doc, dict):
        raise Refused("intake-state-malformed")
    return doc


def _write_intake(proj: Path, state: dict):
    _write_text(proj, INTAKE_REL, json.dumps(state, indent=2, sort_keys=True) + "\n")


# ---- select ---------------------------------------------------------------------------------------

def load_blueprints(root: Path) -> list:
    base = root / "packs" / "stack-profiles"
    out = []
    if base.is_dir():
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            f = d / "stack-profile.yaml"
            if f.is_file():
                doc = _load_yaml(f)
                if isinstance(doc, dict) and doc.get("id") and isinstance(doc.get("detect"), dict):
                    out.append(doc)
    return out


def _load_facts(args, proj: Path) -> dict:
    if getattr(args, "facts", None):
        try:
            facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused("facts-unreadable")
        if not isinstance(facts, dict) or not isinstance(facts.get("paths", []), list) \
                or not isinstance(facts.get("dependencies", []), list):
            raise Refused("facts-malformed")
        return facts
    return discover(proj)


def cmd_select(args):
    root, proj = plugin_root(args.plugin_root), project_dir(args.project_dir)
    facts = _load_facts(args, proj)
    profiles = load_blueprints(root)
    ids = [p["id"] for p in profiles]
    for flag, val in (("confirm", args.confirm), ("suggest", args.suggest)):
        if val is not None and val not in ids:
            raise Refused(f"unknown-blueprint:{val}")
    ranked = _bp().rank(profiles, facts)
    above = [r for r in ranked if r["above_threshold"]]
    if args.confirm:
        row = next(r for r in ranked if r["id"] == args.confirm)
        state = _read_intake(proj) or {}
        state.update({"blueprint": args.confirm, "facts_sha256": facts_digest(facts),
                      "selected_at": datetime.date.today().isoformat(), "source": "operator-confirmed"})
        _write_intake(proj, state)
        print(f"selected {args.confirm}"
              + ("" if row["above_threshold"] else " (below its detection threshold: operator override)"))
        return 0
    if args.json:
        print(json.dumps({"candidates": ranked, "above_threshold_count": len(above),
                          "suggestion": args.suggest}, indent=2, sort_keys=True))
        return EXIT_DECISION
    print("blueprint candidates (ranked; declared detect signatures, not an inference):")
    for i, r in enumerate(ranked, 1):
        thr = "n/a" if r["threshold"] is None else f"{r['threshold']:.2f}"
        print(f"{i}. {r['id']}  score {r['score']:.2f}  threshold {thr}  "
              + ("ABOVE threshold" if r["above_threshold"] else "below threshold"))
        for ev in r["evidence"]:
            print(f"     + {ev['kind']} {ev['pattern']} (weight {ev['weight']}) matched {ev['matched_by']}")
    if not above:
        print("no candidate above threshold: nothing in this tree matches a shipped blueprint's signatures.")
    if args.suggest:
        print(f"SUGGESTION (labelled, unverified, not a selection): {args.suggest}")
    print("decision needed: re-run with --confirm <blueprint-id> to select one. Nothing was written.")
    return EXIT_DECISION


# ---- declare --------------------------------------------------------------------------------------

def parse_surfaces(text: str) -> list:
    items = [x.strip() for x in str(text).split(",") if x.strip()]
    bad = [x for x in items if x not in SURFACE_TAGS]
    if bad:
        raise Refused(f"unknown-surface:{bad[0]} (allowed: {', '.join(SURFACE_TAGS)})")
    return sorted(set(items))


def _blueprint_doc(root: Path, bp_id: str):
    return next((p for p in load_blueprints(root) if p["id"] == bp_id), None)


def cmd_declare(args):
    root, proj = plugin_root(args.plugin_root), project_dir(args.project_dir)
    if not 1 <= args.asvs_level <= 3:
        raise Refused("asvs-level-out-of-range: 1-3")
    state = _read_intake(proj) or {}
    lock = _lenient_lock(proj)
    prior = dict(state.get("declarations") or _decl(lock))
    classes = args.data_classification if args.data_classification is not None else prior.get("data_classification", [])
    for c in classes:
        if not CAT_RE.fullmatch(c):
            raise Refused(f"bad-data-classification:{c}")
    bounds = args.trust_boundary if args.trust_boundary is not None else prior.get("trust_boundaries", [])
    for g in bounds:
        if not PATH_RE.fullmatch(g):
            raise Refused("bad-trust-boundary: characters outside the allowed glob set")
    if args.surfaces is not None:
        surfaces = parse_surfaces(args.surfaces)
    elif "surfaces" in prior:
        surfaces = prior["surfaces"]
    else:
        bp_id = state.get("blueprint") or ((lock or {}).get("blueprint") or {}).get("id")
        doc = _blueprint_doc(root, bp_id) if bp_id else None
        surfaces = sorted(doc.get("surfaces") or []) if doc else []
    decl = {"asvs_level": args.asvs_level, "data_classification": sorted(set(classes)),
            "trust_boundaries": list(bounds), "surfaces": surfaces}
    state["declarations"] = decl
    _write_intake(proj, state)
    if lock is not None:
        new = dict(lock)
        new["declarations"] = decl
        _check_lock(new)
        _write_text(proj, LOCK_REL, _lock_text(new))
        print("declarations written to the intake state and the lock")
    else:
        print("declarations written to the intake state")
    try:
        auth = discover(proj)["auth_libraries"]
    except Refused:
        auth = []
    if auth:
        print(f"hint: authentication libraries detected ({', '.join(auth[:5])}); consider asvs level 2 or 3 "
              "and a --trust-boundary for the code that calls them (a heuristic, not a decision).")
    return 0


# ---- lock -----------------------------------------------------------------------------------------

PENDING = "pending"


def _decl(lock) -> dict:
    """The lock's declarations as a mapping ({} while they are still `pending`)."""
    d = (lock or {}).get("declarations")
    return d if isinstance(d, dict) else {}


def _lenient_lock(proj: Path):
    """The lock as a mapping, or None when absent. A malformed lock raises (callers that must not fail use
    _try_lock)."""
    lock = _read_lock(proj)
    if lock is None:
        return None
    if not isinstance(lock, dict) or schema_errors(lock, "lock"):
        raise Refused("lock-malformed")
    return lock


def _try_lock(proj: Path):
    try:
        return _lenient_lock(proj)
    except Refused:
        return None


def _check_lock(lock: dict):
    errs = schema_errors(lock, "lock")
    if errs:
        raise Refused("lock-invalid: " + "; ".join(errs))


def _lock_text(lock: dict) -> str:
    import yaml
    return ("# Machine-owned by `foundry_governance.py apply|upgrade`. Do not edit; "
            "adopter rules live in governance/local/.\n" + yaml.safe_dump(lock, sort_keys=False))


def _plugin_version(root: Path):
    f = root / ".claude-plugin" / "plugin.json"
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


def _vt(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(version))[:3]) + (0,) * (3 - len(re.findall(r"\d+", str(version))[:3]))


def build_lock(root: Path, kits: list, prior, bp_id: str, declarations: dict, facts_sha: str, today: str) -> dict:
    doc = _blueprint_doc(root, bp_id)
    if doc is None:
        raise Refused(f"unknown-blueprint:{bp_id}")
    try:
        bid, bver, bsha = _stack_profile(root).blueprint_digest(bp_id, root=str(root), plugin_root=str(root))
    except Exception as exc:  # noqa: BLE001 - any profile-resolution failure is a refusal naming the class
        raise Refused(f"blueprint-unresolvable:{bp_id}:{type(exc).__name__}")
    installed = {k["kit"]["kit"]: k for k in kits}
    prior_levels = {k["id"]: k["level"] for k in (prior or {}).get("kits", [])}
    entries = []
    for kid, spec in sorted(((doc.get("governance") or {}).get("kits") or {}).items()):
        if kid not in installed:
            raise Refused(f"blueprint-kit-missing:{kid}")
        entries.append({"id": kid, "version": installed[kid]["kit"]["version"], "sha256": installed[kid]["sha256"],
                        "level": prior_levels.get(kid, int((spec or {}).get("level", 1)))})
    lock = {"blueprint": {"id": bid, "version": str(bver), "sha256": bsha}, "kits": entries,
            "declarations": declarations, "facts_sha256": facts_sha, "intake_at": today,
            "plugin_version": _plugin_version(root) or "unknown"}
    if prior and prior.get("intake_at"):
        lock["intake_at"] = prior["intake_at"]          # intake happened once; upgrades do not re-date it
    _check_lock(lock)
    return lock


# ---- level / surface filters ----------------------------------------------------------------------

def filter_rules(rules: list, kits: list, *, level=None, surfaces=None, lock=None) -> list:
    """Floor rules are never filtered out. A rule whose `applies_to.blueprints` names only surface tags is kept
    only when one of those surfaces is declared; a blueprint id or `*` alongside keeps it."""
    kit_of = {r["id"]: k["kit"]["kit"] for k in kits for r in k["rules"]}
    locked = {k["id"]: k["level"] for k in (lock or {}).get("kits", [])} if lock else None
    out = []
    for r in rules:
        if r.get("floor") is True:
            out.append(r)
            continue
        if level is not None and r["level"] > level:
            continue
        if locked is not None and r["id"] in kit_of:
            lv = locked.get(kit_of[r["id"]])
            if lv is None or r["level"] > lv:
                continue
        if surfaces is not None:
            bps = r["applies_to"]["blueprints"]
            tags = [b for b in bps if b in SURFACE_TAGS]
            if tags and len(tags) == len(bps) and not set(tags) & set(surfaces):
                continue
        out.append(r)
    return out


def _filter_args(args, proj: Path):
    """(level, surfaces, lock) from --level / --surfaces / --from-lock."""
    level = getattr(args, "level", None)
    if level is not None and not 1 <= level <= 3:
        raise Refused("level-out-of-range: 1-3")
    surfaces = parse_surfaces(args.surfaces) if getattr(args, "surfaces", None) is not None else None
    lock = None
    if getattr(args, "from_lock", False):
        lock = _lenient_lock(proj)
        if lock is None:
            raise Refused("no-governance-lock")
        if surfaces is None:
            surfaces = _decl(lock).get("surfaces")
    return level, surfaces, lock


def _lock_rules(rules, kits, lock):
    return filter_rules(rules, kits, surfaces=_decl(lock).get("surfaces"), lock=lock)


# ---- install / three-way --------------------------------------------------------------------------

def snippet_plan(root: Path, kits: list, kit_ids) -> list:
    """The kit CI / hook snippets to install: kit.yaml `checks` entries ending `.tmpl`.
    X.yml.tmpl -> .github/workflows/X.yml ; X.sh.tmpl / X.py.tmpl -> .claude/hooks/X. Verbatim, no substitution."""
    by = {k["kit"]["kit"]: k for k in kits}
    plan, seen = [], {}
    for kid in sorted(kit_ids):
        k = by[kid]
        kdir = (root / "governance" / "kits" / k["dir"]).resolve()
        for rel in k["kit"].get("checks") or []:
            if not isinstance(rel, str) or not rel.endswith(".tmpl"):
                continue
            src = (kdir / rel).resolve()
            try:
                src.relative_to(kdir)
            except ValueError:
                raise Refused(f"kit-path-escapes:{kid}:{rel}")
            name = src.name[:-len(".tmpl")]
            if name.endswith((".yml", ".yaml")):
                dest = f".github/workflows/{name}"
            elif name.endswith((".sh", ".py")):
                dest = f".claude/hooks/{name}"
            else:
                continue
            if dest in seen:
                raise Refused(f"duplicate-snippet-destination:{dest}")
            seen[dest] = kid
            if not src.is_file():
                raise Refused(f"kit-snippet-missing:{kid}:{rel}")
            plan.append({"dest": dest, "kit": kid, "text": src.read_text(encoding="utf-8")})
    return plan


def _git(proj: Path, *argv):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(["git", "-C", str(proj), *argv], capture_output=True, text=True, env=env)
    except FileNotFoundError:
        raise Refused("git-not-found")


def require_clean_tree(proj: Path):
    r = _git(proj, "status", "--porcelain", "--untracked-files=no")
    if r.returncode != 0:
        raise Refused("not-a-git-repo: governance upgrade needs a git work tree")
    if r.stdout.strip():
        raise Refused("dirty-tree: commit or stash tracked changes first (nothing was changed)")


def merge3(ours: str, base: str, theirs: str, theirs_label: str):
    """(merged_text, conflicts) via `git merge-file` (local); inline conflict markers, never a clobber."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = Path(td) / name
            p.write_bytes(text.encode("utf-8", "surrogateescape"))
            paths.append(str(p))
        try:
            r = subprocess.run(["git", "merge-file", "-p", "-L", "adopter", "-L", "kit-base", "-L", theirs_label,
                                *paths], capture_output=True)
        except FileNotFoundError:
            r = None
    if r is not None and 0 <= r.returncode <= 127:
        return r.stdout.decode("utf-8", "surrogateescape"), r.returncode
    marked = f"<<<<<<< adopter\n{ours}=======\n{theirs}>>>>>>> {theirs_label}\n"
    return marked, 1


def install_file(proj: Path, dest: str, new: str, mode: str, label: str):
    """(action, conflicts). `apply` never overwrites a differing file (it keeps it and records the kit snippet as
    the snapshot, so a later upgrade has a true base). `upgrade` three-way merges against that snapshot. A merge
    that conflicts NEVER puts markers into the file: the adopter's file stays as is and the kit version is left
    beside it as `<dest>.kit-new`, which stays reported until the adopter deletes it."""
    ours = _read_text(proj, dest)
    base_rel = f"{BASE_REL}/{dest}"
    base = _read_text(proj, base_rel)
    kit_new = f"{dest}.kit-new"
    pending = _read_text(proj, kit_new) is not None
    if ours is None:
        if mode == "upgrade" and base is not None:
            _write_text(proj, base_rel, new)
            return "adopter-removed", 0
        _write_text(proj, dest, new)
        _write_text(proj, base_rel, new)
        return "created", 0
    if ours == new:
        _write_text(proj, base_rel, new)
        return "unchanged", 0
    if mode == "apply":
        if base is None:
            _write_text(proj, base_rel, new)      # the snapshot of what the kit ships, not of the adopter's file
        return "kept", 0
    if base is not None and ours == base:
        _write_text(proj, dest, new)
        _write_text(proj, base_rel, new)
        return "updated", 0
    if base is not None and base == new:
        return ("conflict", 1) if pending else ("kept", 0)
    merged, conflicts = merge3(ours, base or "", new, label)
    _write_text(proj, base_rel, new)
    if conflicts:
        _write_text(proj, kit_new, new)
        return "conflict", 1
    _write_text(proj, dest, merged)
    return "merged", 0


def apply_migrations(proj: Path, kits: list, prior_lock: dict) -> list:
    """Kit `migrations` newer than the locked kit version rename ids inside governance/local/**.yaml|json.
    Every path component is checked for symlinks and the resolved file must stay inside the repository."""
    locked = {k["id"]: k["version"] for k in prior_lock.get("kits", [])}
    renames = {}
    for k in kits:
        kid = k["kit"]["kit"]
        if kid not in locked:
            continue
        for m in k["kit"].get("migrations") or []:
            if _vt(m["since"]) > _vt(locked[kid]) and _vt(m["since"]) <= _vt(k["kit"]["version"]):
                renames[m["from_id"]] = m["to_id"]
    touched = []
    local = proj / "governance" / "local"
    real_proj = proj.resolve()
    if renames and local.is_dir() and not local.is_symlink():
        for f in sorted(local.rglob("*")):
            if f.suffix not in (".yaml", ".yml", ".json"):
                continue
            rel = f.relative_to(proj)
            if ".kit-base" in rel.parts:
                continue
            cur, linked = proj, False
            for part in rel.parts:
                cur = cur / part
                if cur.is_symlink():
                    linked = True
                    break
            if linked or not f.is_file():
                continue
            try:
                f.resolve().relative_to(real_proj)
            except ValueError:
                continue
            text = f.read_text(encoding="utf-8")
            new = text
            for old, to in renames.items():
                new = re.sub(r"(?<![A-Za-z0-9-])" + re.escape(old) + r"(?![A-Za-z0-9-])", to, new)
            if new != text:
                _write_text(proj, rel.as_posix(), new)
                touched.append(rel.as_posix())
    return touched


LOCAL_README = """# governance/local

The adopter layer. The plugin never overwrites anything in this directory.

- `rules.yaml` - your own rules (ids use your own prefix, never `FG-`) and `overrides:` of non-floor kit rules.
- `exceptions.yaml` - time-boxed exceptions: `rule`, `scope`, `reason`, `owner`, `expires` (YYYY-MM-DD).
- `baselines/` - existing violations frozen per check (`foundry_governance.py baseline`).
- `adr/` - architecture decision records.
- `.kit-base/` - machine-owned snapshots of the kit files last installed; the base of the three-way merge on upgrade.

This directory is private to your workspace and is refused by the upstream leak guard.
"""


def scaffold_local(proj: Path) -> list:
    made = []
    for rel, text in (("governance/local/README.md", LOCAL_README),
                      ("governance/local/exceptions.yaml", "exceptions: []\n"),
                      ("governance/local/baselines/.gitkeep", ""),
                      ("governance/local/adr/.gitkeep", "")):
        if _read_text(proj, rel) is None:
            _write_text(proj, rel, text)
            made.append(rel)
    return made


def write_rendered(proj: Path, docs: dict) -> list:
    out = safe_outdir(proj, "governance/rendered")
    touched = []
    for cat, text in docs.items():
        target = out / f"{cat}.md"
        if target.is_symlink():
            raise Refused(f"output-is-symlink:governance/rendered/{cat}.md")
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            _jrec(proj, f"governance/rendered/{cat}.md")
            target.write_text(text, encoding="utf-8")
            _jwrote(proj, f"governance/rendered/{cat}.md", text.encode("utf-8"))
            touched.append(f"governance/rendered/{cat}.md")
    for stale in out.glob("*.md"):
        if stale.stem not in docs and not stale.is_symlink() and \
                is_generated(stale.read_text(encoding="utf-8", errors="replace")):
            _jrec(proj, f"governance/rendered/{stale.name}")
            stale.unlink()
            _jwrote(proj, f"governance/rendered/{stale.name}", None)
            touched.append(f"governance/rendered/{stale.name}")
    return touched


# ---- stack-profile.lock absorbed (one-time migration) ---------------------------------------------

def stack_lock_blueprint(root: Path, proj: Path):
    """The shipped blueprint id the legacy .foundry/stack-profile.lock names, or None."""
    raw = _read_text(proj, ".foundry/stack-profile.lock")
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
        ids = [p.get("id") for p in (doc.get("profiles") or []) if isinstance(p, dict)]
    except (ValueError, AttributeError):
        return None
    shipped = {p["id"] for p in load_blueprints(root)}
    return next((i for i in ids if i in shipped), None)


# ---- pull request ---------------------------------------------------------------------------------

CRED_RE = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://)[^@\n]*@")


def _redact(text) -> str:
    """Hide `scheme://user:secret@` in any text that carries git / gh output."""
    return CRED_RE.sub(r"\1***@", str(text))


def _tail(r) -> str:
    out = (r.stderr or r.stdout or "").strip().splitlines()
    return _redact(out[-1] if out else "no output")[:300]


def pr_preflight(proj: Path, branch: str):
    """Everything a pull request needs, checked BEFORE anything is written: origin, gh, a clean tree, a branch
    name. Returns (original_branch, branch_exists_locally, branch_exists_on_origin)."""
    r = _git(proj, "remote", "get-url", "origin")
    if r.returncode != 0 or not r.stdout.strip():
        raise Refused("no-origin: --pr needs a remote named origin (run without --pr to review the diff locally)")
    if shutil.which("gh") is None:
        raise Refused("gh-not-found: install the GitHub CLI or run without --pr")
    require_clean_tree(proj)
    cur = _git(proj, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not cur or cur == "HEAD":
        raise Refused("detached-head: check out a branch before using --pr")
    exists = _git(proj, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    # exit 0 = the head exists on origin; 2 = it does not; anything else (unreachable origin) is "unknown", and the
    # push step reports that honestly
    remote = _git(proj, "ls-remote", "--exit-code", "--heads", "origin", branch).returncode == 0
    return cur, exists, remote


def _stage(proj: Path, rels: list) -> list:
    """Stage what the run wrote. Only the lock may be force-added; any other gitignored path is left alone (an
    adopter's ignored layer is never pushed). A removed path stages its deletion. Returns the skipped paths."""
    skipped = []
    for rel in rels:
        if (proj / rel).exists() or (proj / rel).is_symlink():
            ignored = _git(proj, "check-ignore", "-q", "--", rel).returncode == 0
            if ignored and rel != LOCK_REL:
                skipped.append(rel)
                continue
            r = _git(proj, "add", *(["-f"] if rel == LOCK_REL else []), "--", rel)
        else:
            r = _git(proj, "rm", "-q", "--cached", "--ignore-unmatch", "--", rel)
        if r.returncode != 0:
            raise Refused(f"git-add-failed: {_tail(r)}")
    return skipped


def _pr_body(kind: str, lines: list, notes: list, files=()) -> str:
    return "\n".join([f"Governance {kind} (generated by `foundry_governance.py {kind}`).", "", "Changes:"]
                     + [f"- {x}" for x in lines] + ([""] + notes if notes else [])
                     + (["", "Managed files in this pull request (review each, including any you edited yourself, e.g. "
                         "`governance/local/exceptions.yaml`):"] + [f"- {f}" for f in files] if files else [])
                     + ["", "Review with `/foundry:post-upgrade`."])


def _snapshot_managed(proj: Path) -> dict:
    """rel -> bytes of every governance-managed file that already exists, taken BEFORE the branch switch: a file from an
    earlier local apply is committed on the governance branch, and leaving that branch removes it from the work tree."""
    rels = [LOCK_REL, "governance/local/README.md", "governance/local/exceptions.yaml"]
    for top in (BASE_REL, "governance/rendered", ".github/workflows", ".claude/hooks"):
        d = proj / top
        if d.is_dir() and not d.is_symlink():
            rels += [x.relative_to(proj).as_posix() for x in sorted(d.rglob("*")) if x.is_file()]
    snap = {}
    for rel in rels:
        state, data = _plain_file_bytes(proj, rel)
        if state == "file":
            snap[rel] = data
    return snap


def _restore_snapshot(proj: Path, snap: dict) -> list:
    """Put back a snapshotted file that is now MISSING (never over a file that exists). Returns the paths restored."""
    back = []
    for rel, data in sorted(snap.items()):
        f = proj / rel
        if f.exists() or f.is_symlink():
            continue
        try:
            base = safe_outdir(proj, rel.rsplit("/", 1)[0])
            _atomic_write(base, f.name, data, 0o644)
            back.append(rel)
        except (OSError, Refused):
            print(f"undo-could-not-restore:{rel}", file=sys.stderr)
    return back


def _plan_and_write(args, mode: str):
    root, proj, kits, local = _prepare(args)
    today = datetime.date.today().isoformat()
    prior = _lenient_lock(proj)
    notes, changes, touched, conflicts = [], [], [], []
    state = _read_intake(proj) or {}
    if mode == "upgrade":
        if prior is None:
            seeded = stack_lock_blueprint(root, proj)
            if seeded is None:
                raise Refused("no-governance-lock: run the intake first (select, declare, apply)")
        require_clean_tree(proj)
    elif getattr(args, "pr", False):
        require_clean_tree(proj)

    bp_id = state.get("blueprint") or ((prior or {}).get("blueprint") or {}).get("id")
    decl = state.get("declarations") or (prior or {}).get("declarations")
    facts_sha = state.get("facts_sha256") or (prior or {}).get("facts_sha256")
    if prior is None and not (state.get("blueprint")):
        seeded = stack_lock_blueprint(root, proj)
        if seeded is not None:
            bp_id = seeded
            notes.append(f"stack-profile.lock: absorbed (blueprint {seeded} carried into the governance lock; "
                         "one-time migration)")
            if decl is None:
                decl = PENDING          # never silently defaulted: the operator declares (ASVS level, data, boundaries)
    if not bp_id:
        raise Refused("intake-incomplete: run `select --confirm <blueprint-id>` first")
    if decl is None:
        raise Refused("declarations-missing: run `declare --asvs-level N` first")
    if not facts_sha:
        facts_sha = facts_digest(discover(proj))

    # validate everything that can refuse BEFORE the first write
    lock = build_lock(root, kits, prior, bp_id, decl, facts_sha, today)
    plan = snippet_plan(root, kits, [k["id"] for k in lock["kits"]])

    if mode == "upgrade" and prior is not None:
        migrated = apply_migrations(proj, kits, prior)
        touched += migrated
        changes += [f"{m}: migrated renamed rule ids" for m in migrated]
        if migrated:                       # the adopter layer changed on disk: reload before validating
            local = load_local(proj)
    rules = checked_effective(kits, local)

    written_dests = set()
    for item in plan:
        action, c = install_file(proj, item["dest"], item["text"], mode, f"kit-{lock['plugin_version']}")
        if c:
            conflicts.append(item["dest"])
            touched.append(f"{item['dest']}.kit-new")
        if action != "unchanged":
            changes.append(f"{item['dest']}: {action}"
                           + (f" (kit version left at {item['dest']}.kit-new; the file is untouched)" if c else ""))
        if action in ("created", "updated", "merged", "unchanged"):
            touched.append(item["dest"])
            written_dests.add(item["dest"])
        touched.append(f"{BASE_REL}/{item['dest']}")
    for rel in scaffold_local(proj):
        changes.append(f"{rel}: created")
        touched.append(rel)
    snippets = {}
    for item in plan:             # gv-20: the sha256 of every snippet this run left in place, so `check` can see a hand edit
        if item["dest"] in written_dests:
            cur = proj / item["dest"]
            if cur.is_file() and not cur.is_symlink():
                snippets[item["dest"]] = hashlib.sha256(cur.read_bytes()).hexdigest()
    if snippets or (prior or {}).get("snippets"):
        lock["snippets"] = snippets
        _check_lock(lock)
    if prior != lock:
        only_version = prior is not None and ({k: v for k, v in prior.items() if k != "plugin_version"}
                                              == {k: v for k, v in lock.items() if k != "plugin_version"})
        if not (only_version and not changes):      # a plugin_version bump alone is provenance, not a change
            _write_text(proj, LOCK_REL, _lock_text(lock))
            changes.append(f"{LOCK_REL}: {'written' if prior is None else 'bumped'}")
    touched.append(LOCK_REL)
    docs = render_docs(_lock_rules(rules, kits, lock))
    for rel in write_rendered(proj, docs):
        changes.append(f"{rel}: rendered")
        touched.append(rel)
    # everything this verb manages, so `--pr` can commit it even when an earlier local run already wrote it
    touched += [f"governance/rendered/{c}.md" for c in docs]
    touched += ["governance/local/README.md", "governance/local/exceptions.yaml",
                "governance/local/baselines/.gitkeep", "governance/local/adr/.gitkeep"]
    return proj, changes, touched, conflicts, notes


def _uncommitted(proj: Path, touched: list) -> bool:
    """True when governance files this verb manages are in the working tree but not committed: the lock is not
    tracked, or any managed path is modified / untracked."""
    if _git(proj, "ls-files", "--error-unmatch", "--", LOCK_REL).returncode != 0 and (proj / LOCK_REL).is_file():
        return True
    rels = sorted({t for t in touched if (proj / t).exists()})
    if not rels:
        return False
    return bool(_git(proj, "status", "--porcelain", "--", *rels).stdout.strip())


def _pending_line(proj: Path):
    lock = _try_lock(proj)
    if lock is not None and lock.get("declarations") == PENDING:
        print("declarations pending: run `foundry_governance.py declare`")


def _run_verb(args, mode: str) -> int:
    global _JOURNAL
    kind = "apply" if mode == "apply" else "upgrade"
    root, proj = plugin_root(args.plugin_root), project_dir(args.project_dir)
    use_pr, base_branch, branch, cur = bool(getattr(args, "pr", False)), getattr(args, "base", None), None, None
    if use_pr:
        branch = "governance/intake" if kind == "apply" else f"governance/upgrade-{_plugin_version(root) or 'next'}"
        cur, exists, on_origin = pr_preflight(proj, branch)
        if exists:
            print(f"{kind} PR open: {branch} (merge or delete that branch, then run again)")
            return 0
        if on_origin:
            print(f"branch-exists-on-origin:{branch} (a {kind} branch is already pushed; merge it, or delete it with "
                  f"`git push origin --delete {branch}`, then run again)")
            return 0
    _JOURNAL, switched, pushed = _Journal(), False, False
    snap = _snapshot_managed(proj) if use_pr else {}
    try:
        if use_pr:
            r = _git(proj, "checkout", "-q", "-b", branch)
            if r.returncode != 0:
                raise Refused(f"git-checkout-failed: {_tail(r)}")
            switched = True
        proj, changes, touched, conflicts, notes = _plan_and_write(args, mode)
        for n in notes:
            print(n)
        if not changes and not conflicts and use_pr and _uncommitted(proj, touched):
            # the files are already in place (a local apply) but no governance branch carries them: commit them
            changes = ["governance files already in place: committing the working-tree state"]
        if not changes and not conflicts:
            print(f"governance-{kind}: already current")
            if switched:
                _git(proj, "checkout", "-q", cur)
                _git(proj, "branch", "-D", branch)
            _pending_line(proj)
            return 0
        for c in changes:
            print(f"  {c}")
        print(f"governance-{kind}: {len(changes)} change(s)")
        if conflicts:
            for c in conflicts:
                notes.append(f"CONFLICT {c}: the kit changed this file and so did you; the kit version is {c}.kit-new "
                             "(merge it by hand, then delete the .kit-new file)")
            print("GOVERNANCE-UPGRADE-CONFLICTS " + " ".join(f"{c}.kit-new" for c in conflicts))
        if use_pr:
            uniq = sorted({t for t in touched if (proj / t).exists() or (proj / t).is_symlink() or t.startswith("governance/rendered/")})
            skipped = _stage(proj, uniq)
            if skipped:
                notes.append("not committed (gitignored, left in the working tree): " + ", ".join(skipped))
            title = "Governance intake" if kind == "apply" else "Governance upgrade"
            if _git(proj, "diff", "--cached", "--quiet").returncode == 0:
                raise Refused("nothing-to-commit: every changed path is gitignored")
            r = _git(proj, "commit", "-q", "-m", title)
            if r.returncode != 0:
                raise Refused(f"git-commit-failed: {_tail(r)}")
            r = _git(proj, "push", "-q", "-u", "origin", branch)
            if r.returncode != 0:
                raise Refused(f"git-push-failed: {_tail(r)}")
            pushed = True
            gh = shutil.which("gh")
            r = subprocess.run([gh, "pr", "create", "--base", base_branch or cur, "--head", branch, "--title", title,
                                "--body", _pr_body(kind, changes, notes, [u for u in uniq if u not in skipped])],
                               cwd=str(proj), capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if r.returncode != 0:
                raise Refused(f"gh-pr-create-failed: {_tail(r)}")
            print(_redact((r.stdout.strip().splitlines() or ["pull request opened"])[-1]))
            _git(proj, "checkout", "-q", cur)
            switched = False
        _pending_line(proj)
        return 1 if conflicts else 0
    except BaseException:
        if pushed:              # the push succeeded but the pull request did not: do not leave an orphan branch
            d = _git(proj, "push", "origin", "--delete", branch)
            if d.returncode != 0:
                print(f"remote-branch-not-deleted:{branch}: {_tail(d)}", file=sys.stderr)
        if switched:
            _git(proj, "checkout", "-q", "-f", cur)
            _git(proj, "branch", "-D", branch)
        for rel in _JOURNAL.rollback():
            print(f"undo-skipped-modified:{rel}", file=sys.stderr)
        if snap:
            _restore_snapshot(proj, snap)
        raise
    finally:
        _JOURNAL = None


def cmd_apply(args):
    return _run_verb(args, "apply")


def cmd_upgrade(args):
    return _run_verb(args, "upgrade")


# ---- upstream leak guard (design 5.6) -------------------------------------------------------------

ORG_ID_RE = re.compile(r"(?<![a-z0-9])([a-z][a-z0-9]{1,15})-[a-z]{2,5}-[0-9]{3}(?![a-z0-9])")   # run on casefolded text
ADOPTER_OWNED = ("governance/rendered/**", ".foundry/governance.lock", ".foundry/governance-intake.json")


_HYPHENS = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u207b\u208b\ufe58\ufe63\uff0d\u058a\u05be\u1806\u2e3a\u2e3b"), "-")
# Cyrillic / Greek letters that read as a Latin one (casefolded); applied AFTER casefold
_CONFUSABLES = dict(zip(map(ord, "\u0430\u0432\u0435\u0437\u0456\u0458\u043a\u043c\u043d\u043e\u0440\u0441\u0442\u0443\u0445\u0455\u0501\u051b\u051d\u04bb"
                                 "\u03b1\u03b2\u03b5\u03b9\u03ba\u03bd\u03bf\u03c1\u03c4\u03c5\u03c7\u0261\u0262\u0584"),
                        "abe3ijkmhopctyxsdqwhabeiknoptyxggq"))


def _fold(text: str) -> str:
    """What a human reads, not what bytes were typed: NFKC, drop format (Cf) and combining (Mn) characters, fold every dash
    to '-', every non-ASCII decimal digit to its ASCII digit, casefold, then map Cyrillic / Greek look-alike letters to Latin."""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Cf", "Mn"))
    t = "".join(str(unicodedata.decimal(ch)) if ord(ch) > 127 and unicodedata.category(ch) == "Nd" else ch for ch in t)
    return t.translate(_HYPHENS).casefold().translate(_CONFUSABLES)


_TOKEN_SPLIT = re.compile(r"[\s,;:()\[\]{}<>\"'`|]+")


def leak_findings(files: dict, lock=None, literals=()) -> list:
    """Findings (strings; the matched text is NEVER echoed) for a set of files bound for an upstream
    submission. `files` maps a repo-relative path to its text, or None when it could not be read (a finding,
    never a pass). Refuses: any governance/local/ path, any path the lock marks adopter-owned, any non-FG rule
    id (found after Unicode normalisation, so a homoglyph or a zero-width split does not hide one), and any
    product literal (foundry_blueprint's HOW/WHAT deny-list, generalised)."""
    bp = _bp()
    owned = [_fold(g) for g in ADOPTER_OWNED] + [
        _fold(g) for g in (_decl(lock).get("trust_boundaries") or [])
        if isinstance(g, str) and g]
    extras = [r"(?<!\w)" + re.escape(_fold(x)) + r"(?!\w)" for x in literals if isinstance(x, str) and x.strip()]
    out = []
    for path in sorted(files):
        p = norm_path(path)
        p = re.sub(r"/{2,}", "/", p)                    # `governance//local/x` is `governance/local/x`
        pf = _fold(p)
        text = files[path]
        if pf == "governance/local" or pf.startswith("governance/local/"):
            out.append(f"governance-local-path:{p}")
            continue
        if any(glob_re(g).fullmatch(pf) for g in owned):
            out.append(f"adopter-owned-path:{p}")
            continue
        if text is None:
            out.append(f"unreadable:{p}")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in ORG_ID_RE.finditer(_fold(line)):
                if m.group(1) != "fg":
                    out.append(f"private-rule-id:{p}:{n}")
            # gv-20: an id-shaped token that only reads as one after folding (U+2011 hyphens, a Cyrillic letter, fullwidth forms)
            # is a planted look-alike, whatever prefix it folds to
            for tok in _TOKEN_SPLIT.split(line):
                if tok and not tok.isascii() and ORG_ID_RE.search(_fold(tok)):
                    out.append(f"confusable-rule-id:{p}:{n}")
                    break
        base_hits = bp.guard_blueprint(text, {}, [])
        if base_hits:
            out.append(f"product-id-pattern:{p}")
        if extras and bp.guard_blueprint(_fold(text), {}, [], deny_extra=extras)[len(bp.guard_blueprint(_fold(text), {}, [])):]:
            out.append(f"product-literal:{p}")
    return out


def leak_check_files(proj: Path, rels: list, literals=()) -> list:
    files = {}
    base = proj.resolve()
    for rel in rels:
        full = (proj / rel).resolve()
        try:
            full.relative_to(base)
        except ValueError:
            raise Refused(f"path-outside-project:{rel}")
        key = full.relative_to(base).as_posix()
        try:
            files[key] = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else None
        except OSError:
            files[key] = None
    return leak_findings(files, lock=_try_lock(proj), literals=literals)


def read_literals(path) -> list:
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise Refused("literals-unreadable")
    return [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]


def cmd_leak_check(args):
    proj = project_dir(args.project_dir)
    found = leak_check_files(proj, args.files, read_literals(args.literals))
    for f in found:
        print(f)
    if not found:
        print("leak-check-clean")
    return 1 if found else 0


# ----------------------------------------------------------------- verbs ---

def cmd_load(args):
    _, _, kits, local = _prepare(args)
    rules = checked_effective(kits, local)
    if args.json:
        print(json.dumps({"kits": [{"id": k["kit"]["kit"], "version": k["kit"]["version"], "sha256": k["sha256"]}
                                   for k in kits], "rules": rules}, indent=2, sort_keys=True))
    else:
        for k in kits:
            print(f"kit {k['kit']['kit']} {k['kit']['version']}")
        print(f"{len(rules)} effective rules")
    return 0


def cmd_validate(args):
    _, _, kits, local = _prepare(args)
    errs = validate_all(kits, local)
    if not errs:
        try:
            eff = {r["id"]: r for r in checked_effective(kits, local)}
        except Refused as exc:
            errs.append(str(exc))
        else:
            # floor rules present and not disabled: every upstream floor rule must survive unchanged
            for k in kits:
                for r in k["rules"]:
                    if r.get("floor") and eff.get(r["id"]) != r:
                        errs.append(f"{r['id']}: floor rule altered or missing")
    if errs:
        for e in errs:
            print(f"refused: {e}", file=sys.stderr)
        return 2
    print("governance-valid")
    return 0


def cmd_render(args):
    _, proj, kits, local = _prepare(args)
    level, surfaces, lock = _filter_args(args, proj)
    rules = filter_rules(checked_effective(kits, local), kits, level=level, surfaces=surfaces, lock=lock)
    docs = render_docs(rules)
    write_rendered(proj, docs)
    print(f"rendered {len(docs)} categories to governance/rendered/")
    return 0


def cmd_applicable(args):
    _, proj, kits, local = _prepare(args)
    level, surfaces, lock = _filter_args(args, proj)
    rules = filter_rules(checked_effective(kits, local), kits, level=level, surfaces=surfaces, lock=lock)
    held = _try_lock(proj)
    extra = [g for g in (_decl(held).get("trust_boundaries") or [])
             if isinstance(g, str) and PATH_RE.fullmatch(g)]
    if extra:   # FG-SEC-032's security-review globs grow with the boundaries the adopter declared
        rules = [dict(r, applies_to=dict(r["applies_to"], paths=list(r["applies_to"]["paths"]) + extra))
                 if r["id"] == "FG-SEC-032" else r for r in rules]
    ids = applicable(rules, args.paths)
    print(json.dumps(ids) if args.json else "\n".join(ids))
    return 0


def _read_lock(proj: Path):
    f = proj / ".foundry" / "governance.lock"
    return _load_yaml(f) if f.is_file() else None


def _lock_drift(root: Path, lock: dict) -> list:
    """Lock-vs-plugin drift beyond kit versions: the blueprint pack and the plugin's own version. Each check is
    skipped when the plugin does not ship the thing it would compare (no packs/ tree, no plugin manifest)."""
    findings = []
    bp = lock.get("blueprint") or {}
    if (root / "packs" / "stack-profiles").is_dir() and bp.get("id"):
        if not (root / "packs" / "stack-profiles" / bp["id"] / "stack-profile.yaml").is_file():
            findings.append(f"lock-blueprint-missing:{bp['id']}")
        else:
            try:
                _, ver, sha = _stack_profile(root).blueprint_digest(bp["id"], root=str(root), plugin_root=str(root))
            except Exception as exc:  # noqa: BLE001
                findings.append(f"lock-blueprint-unresolvable:{bp['id']}:{type(exc).__name__}")
            else:
                if str(ver) != bp.get("version"):
                    findings.append(f"lock-blueprint-drift:{bp['id']}: locked {bp.get('version')}, installed {ver}")
                elif sha != bp.get("sha256"):
                    findings.append(f"lock-blueprint-drift:{bp['id']}: content changed at version {ver}")
    return findings          # lock.plugin_version is provenance only: a bump alone is never a finding


def cmd_check(args):
    root, proj, kits, local = _prepare(args)
    rules = checked_effective(kits, local)
    findings, today = [], _today(args.today)
    lock = _read_lock(proj)
    if lock is not None:
        if not isinstance(lock, dict) or schema_errors(lock, "lock"):
            raise Refused("lock-malformed")
        installed = {k["kit"]["kit"]: k["kit"]["version"] for k in kits}
        for lk in (lock.get("kits") or []):
            have = installed.get(lk.get("id"))
            if have is None:
                findings.append(f"lock-kit-missing:{lk.get('id')}")
            elif have != lk.get("version"):
                findings.append(f"lock-version-drift:{lk.get('id')}: locked {lk.get('version')}, installed {have}")
        findings += _lock_drift(root, lock)
        for dest, want in sorted((lock.get("snippets") or {}).items()):
            f = proj / dest
            if f.is_symlink() or (f.exists() and (not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != want)):
                findings.append(f"snippet-tampered:{dest}")
        if lock.get("declarations") == PENDING:
            findings.append("declarations-pending: run `foundry_governance.py declare --asvs-level N`")
    rdir = proj / "governance" / "rendered"
    if rdir.is_dir() or lock is not None:
        have_files = {p.stem: p for p in rdir.glob("*.md")} if rdir.is_dir() else {}
        wants = [render_docs(rules)]
        if lock is not None:     # `apply` / `render --from-lock` render at the locked levels and surfaces
            wants.append(render_docs(_lock_rules(rules, kits, lock)))

        def stale(want):
            bad = [c for c, t in want.items() if c not in have_files or have_files[c].read_text(encoding="utf-8") != t]
            return bad + [c for c in have_files if c not in want]
        for cat in min((stale(w) for w in wants), key=len):
            findings.append(f"rendered-stale:{cat}")
    ex_find, skips = exception_report(rules, local, today)
    findings += ex_find
    for s in skips:
        print(s)
    for f in findings:
        print(f)
    if not findings:
        print("governance-check-ok")
    return 1 if findings else 0


def cmd_exceptions(args):
    _, _, kits, local = _prepare(args)
    rules = checked_effective(kits, local)
    findings, skips = exception_report(rules, local, _today(args.today))
    for s in skips:
        print(s)
    for f in findings:
        print(f)
    if not findings and not skips:
        print("no exceptions")
    return 1 if findings else 0


def cmd_baseline(args):
    _, proj, _, _ = _prepare(args)
    if not NAME_RE.fullmatch(args.check):
        raise Refused(f"baseline-bad-check-name:{args.check}")
    try:
        raw = sys.stdin.read() if args.findings == "-" else Path(args.findings).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        raise Refused("baseline-findings-unreadable")
    if not isinstance(data, list) or not all(isinstance(x, (str, dict)) for x in data):
        raise Refused("baseline-findings-must-be-a-list")
    uniq = {json.dumps(x, sort_keys=True): x for x in data}
    items = [uniq[k] for k in sorted(uniq)]
    out = safe_outdir(proj, "governance/local/baselines")
    target = out / f"{args.check}.json"
    if target.is_symlink():
        raise Refused(f"output-is-symlink:governance/local/baselines/{args.check}.json")
    target.write_text(json.dumps({"check": args.check, "findings": items}, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    print(f"baseline {args.check}: {len(items)} findings")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_governance.py", description=__doc__.split("\n")[0])
    ap.add_argument("--project-dir")
    ap.add_argument("--plugin-root")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("load")
    p.add_argument("--json", action="store_true")
    sub.add_parser("validate")

    def filters(p):
        p.add_argument("--level", type=int, help="keep rules at this adoption level or lower (floor rules always stay)")
        p.add_argument("--surfaces", help="comma list of declared surfaces: " + ", ".join(SURFACE_TAGS))
        p.add_argument("--from-lock", action="store_true", help="use the lock's per-kit levels and declared surfaces")
    p = sub.add_parser("render")
    filters(p)
    p = sub.add_parser("applicable")
    p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--json", action="store_true")
    filters(p)
    for name in ("check", "exceptions"):
        p = sub.add_parser(name)
        p.add_argument("--today")
    p = sub.add_parser("baseline")
    p.add_argument("--check", required=True)
    p.add_argument("--findings", required=True)
    p = sub.add_parser("discover")
    p.add_argument("--out")
    p.add_argument("--summary", action="store_true")
    p = sub.add_parser("select")
    p.add_argument("--facts")
    p.add_argument("--json", action="store_true")
    p.add_argument("--suggest")
    p.add_argument("--confirm")
    p = sub.add_parser("declare")
    p.add_argument("--asvs-level", type=int, required=True)
    p.add_argument("--data-classification", action="append")
    p.add_argument("--trust-boundary", action="append")
    p.add_argument("--surfaces")
    for name in ("apply", "upgrade"):
        p = sub.add_parser(name)
        p.add_argument("--pr", action="store_true", help="branch, commit, push and open a pull request")
        p.add_argument("--base", help="the pull request's base branch (default: the current branch)")
    p = sub.add_parser("leak-check")
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--literals")
    args = ap.parse_args(argv)
    try:
        return {"load": cmd_load, "validate": cmd_validate, "render": cmd_render, "applicable": cmd_applicable,
                "check": cmd_check, "exceptions": cmd_exceptions, "baseline": cmd_baseline,
                "discover": cmd_discover, "select": cmd_select, "declare": cmd_declare, "apply": cmd_apply,
                "upgrade": cmd_upgrade, "leak-check": cmd_leak_check}[args.verb](args)
    except Refused as exc:
        print(f"refused: {_redact(exc)}", file=sys.stderr)
        return 2
    except ValueError as exc:  # bad --today
        print(f"refused: {_redact(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
