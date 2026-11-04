#!/usr/bin/env python3
"""foundry_governance.py -- the governance core: load, validate, render, applicable, check, exceptions, baseline.

Rules ship as kits in the plugin (`governance/kits/<category>/`). An adopter adds a layer in
`governance/local/` (own-prefix rules, overrides, exceptions, baselines). This script is pure local
file work: no network, no subprocess.

  foundry_governance.py [--project-dir DIR] [--plugin-root DIR] load [--json]
  foundry_governance.py ... validate
  foundry_governance.py ... render
  foundry_governance.py ... applicable --paths P [P ...] [--json]
  foundry_governance.py ... check [--today YYYY-MM-DD]
  foundry_governance.py ... exceptions [--today YYYY-MM-DD]
  foundry_governance.py ... baseline --check NAME --findings FILE

Exit 0 ok / 1 findings / 2 refused (invalid input or an override that is not allowed).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
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
    import jsonschema
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
    if "applies_to" in rule:
        if not isinstance(at, dict) or not all(
                isinstance(at.get(k), list) and at.get(k) and all(isinstance(x, str) and x for x in at[k])
                for k in ("paths", "blueprints")):
            errs.append(f"{label}: applies_to: needs non-empty paths[] and blueprints[]")
    ck = rule.get("check")
    if "check" in rule:
        if not isinstance(ck, dict) or ck.get("kind") not in CHECK_KINDS:
            errs.append(f"{label}: check: needs a binding with kind in {list(CHECK_KINDS)}")
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

GEN_HEADER = "# Governance: "


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
        lines = [f"# Governance: {cat}", "",
                 "Generated by `scripts/foundry_governance.py render`. Do not edit. "
                 "Effective rules (kits + governance/local). Details live in the source files.", ""]
        for r in sorted(by_cat[cat], key=lambda x: x["id"]):
            flags = f"level {r['level']}" + (", floor" if r.get("floor") else "") + f", {r['status']}"
            ck = r["check"]
            body = ([f"## {r['id']}", ""] + rule_items(r) if is_local(r)
                    else [f"## {r['id']} - {r['title']}", "", r["statement"]] + rule_items(r)[1:])
            lines += body + ["",
                      f"- {flags}",
                      f"- applies to: {', '.join(r['applies_to']['paths'])}",
                      f"- check: {ck['kind']}" + (f" -> {ck['ref']}" if ck.get("ref") else ""), ""]
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
    docs = render_docs(checked_effective(kits, local))
    out = safe_outdir(proj, "governance/rendered")
    for cat, text in docs.items():
        (out / f"{cat}.md").write_text(text, encoding="utf-8")
    for stale in out.glob("*.md"):
        if stale.stem not in docs and not stale.is_symlink() and \
                stale.read_text(encoding="utf-8", errors="replace").startswith(GEN_HEADER):
            stale.unlink()
    print(f"rendered {len(docs)} categories to governance/rendered/")
    return 0


def cmd_applicable(args):
    _, _, kits, local = _prepare(args)
    ids = applicable(checked_effective(kits, local), args.paths)
    print(json.dumps(ids) if args.json else "\n".join(ids))
    return 0


def _read_lock(proj: Path):
    f = proj / ".foundry" / "governance.lock"
    return _load_yaml(f) if f.is_file() else None


def cmd_check(args):
    _, proj, kits, local = _prepare(args)
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
    rdir = proj / "governance" / "rendered"
    if rdir.is_dir() or lock is not None:
        want = render_docs(rules)
        have_files = {p.stem: p for p in rdir.glob("*.md")} if rdir.is_dir() else {}
        for cat, text in want.items():
            p = have_files.get(cat)
            if p is None or p.read_text(encoding="utf-8") != text:
                findings.append(f"rendered-stale:{cat}")
        for cat in have_files:
            if cat not in want:
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
    (out / f"{args.check}.json").write_text(json.dumps({"check": args.check, "findings": items}, indent=2, sort_keys=True) + "\n",
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
    sub.add_parser("render")
    p = sub.add_parser("applicable")
    p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--json", action="store_true")
    for name in ("check", "exceptions"):
        p = sub.add_parser(name)
        p.add_argument("--today")
    p = sub.add_parser("baseline")
    p.add_argument("--check", required=True)
    p.add_argument("--findings", required=True)
    args = ap.parse_args(argv)
    try:
        return {"load": cmd_load, "validate": cmd_validate, "render": cmd_render, "applicable": cmd_applicable,
                "check": cmd_check, "exceptions": cmd_exceptions, "baseline": cmd_baseline}[args.verb](args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:  # bad --today
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
