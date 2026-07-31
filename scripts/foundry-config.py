#!/usr/bin/env python3
"""foundry-config — adopter-config drift check + baseline record (feat-foundry-config-drift-upgrade v2.1).

Answers the two questions nothing in Foundry could answer before:
  * has an adopter's config changed since it was set up?   -> `check` (per-key, against a baseline)
  * is it even well-formed?                                -> `check` (against shipped JSON Schemas)

TWO VERBS, ONE WRITTEN FILE. `check` writes nothing at all; `adopt` writes exactly ONE file, the baseline
(.claude/foundry-config-baseline.json). NEITHER VERB EVER WRITES A MANAGED CONFIG FILE — that is the shape of
the atom, not a policy toggle. It is why the identity file (.claude/foundry-operators.json, which the merge
floor resolves operator_id against via pure key membership) carries no risk here: there is no write path into
it to abuse. Drift and schema violations are REPORTED; the operator edits by hand. (AC-CFG-6 case viii pins
this with a static source assertion as well as exercised CLI combinations.)

TWO-WAY, NOT THREE-WAY. An earlier design compared baseline x local x shipped-default and could carry adopter
edits forward. Its third leg does not exist: /foundry:init is an agent-driven skill (not a script),
foundry-bootstrap.sh --operator requires the registry to pre-exist, context/ ships no default document for
either file, and such default VALUES as exist are private constants scattered across ~10 consumer scripts.
So this is detect-and-report: the half of the standard baseline-and-compare pattern (dpkg conffiles, ucf,
kubectl apply, Helm 3) that the available inputs actually support.

Exit codes follow `terraform plan -detailed-exitcode` (0 clean / 1 error / 2 findings). `kubectl diff` and
`git diff --exit-code` use 0/1/>1; Terraform's is chosen because this verb reports THREE outcomes and the
git/kubectl mapping cannot express the third without overloading 1.
"""
import argparse
import json
import os
import sys
import tempfile

MANAGED = (".claude/foundry-operators.json", ".claude/foundry-project.json")
BASELINE_REL = ".claude/foundry-config-baseline.json"
BASELINE_SCHEMA_VERSION = 1
SCHEMA_FOR = {
    ".claude/foundry-operators.json": "foundry-operators.schema.json",
    ".claude/foundry-project.json": "foundry-project.schema.json",
}

# Exit codes (AC-CFG-3 precedence order: error > findings > clean).
EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS = 0, 1, 2


# ------------------------------------------------------------------ helpers --
def _plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plugin_version():
    try:
        with open(os.path.join(_plugin_root(), ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "unknown")
    except (OSError, ValueError, AttributeError):
        return "unknown"


def _utc_now():
    # datetime is imported lazily so the module stays importable in minimal envs.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _esc(token):
    """RFC 6901 reference-token escaping: '~' -> '~0', '/' -> '~1' (order matters)."""
    return str(token).replace("~", "~0").replace("/", "~1")


def flatten(value, prefix=""):
    """Map a parsed JSON document to {json_pointer: value}.

    A dict is recursed. EVERYTHING ELSE IS A LEAF -- including arrays (AC-CFG-2). kubectl/Helm merge lists by
    patchMergeKey, which needs a schema this atom does not have; index-wise comparison manufactures spurious
    drift on insertion/reorder, so an array is compared whole at its own pointer.
    """
    if isinstance(value, dict):
        out = {}
        if not value and prefix:
            out[prefix] = {}           # an empty object is itself a leaf-ish observable value
        for k, v in value.items():
            out.update(flatten(v, prefix + "/" + _esc(k)))
        return out
    return {prefix or "/": value}


def classify(base_doc, local_doc):
    """Two-way per-key classification (AC-CFG-2). Comparison is deep structural equality over PARSED values,
    so key order / indentation / whitespace are never differences."""
    base, local = flatten(base_doc), flatten(local_doc)
    rows = []
    for ptr in sorted(set(base) | set(local)):
        in_b, in_l = ptr in base, ptr in local
        if in_b and in_l:
            rows.append((ptr, "clean" if base[ptr] == local[ptr] else "local-edit"))
        elif in_b:
            rows.append((ptr, "locally-removed"))
        else:
            rows.append((ptr, "local-addition"))
    return rows


def atomic_write(path, text):
    """Write-temp-then-rename in the SAME directory (AC-CFG-1). An interrupted run leaves any prior file
    intact and no stray temp behind."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".foundry-config.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    """-> (doc, error_or_None). Absent file is (None, None); unparseable is (None, '<reason>').

    SYMLINK POSTURE (deliberate, and divergent from foundry-bootstrap.sh, which refuses to read the registry
    through a symlink): this uses a plain open() and FOLLOWS symlinks, because `foundry_authz.load_operators`
    -- the actual authorization reader -- does too. Reading the same bytes the floor resolves against is the
    point; refusing here would make the checker blind to a registry authz still honours.
    RecursionError is caught so a pathologically nested document yields the documented error outcome rather
    than a traceback.
    """
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except ValueError as e:
        return None, "unparseable JSON: %s" % e
    except RecursionError:
        return None, "unparseable JSON: nesting too deep"
    except OSError as e:
        # e carries an ABSOLUTE path in strerror; report the relative one we were given instead.
        return None, "unreadable: %s" % (e.strerror or e.__class__.__name__)


# --------------------------------------------------------------- validation --
def _fallback_validate(rel, doc):
    """Dependency-free structural checks that run even with NO json-schema validator importable (AC-CFG-5).
    Covers schema_version presence on BOTH files plus the operator-registry required fields, so the identity
    file's shape check can never silently evaporate."""
    out = []
    if not isinstance(doc, dict):
        return ["%s: top level is not an object" % rel]
    if not isinstance(doc.get("schema_version"), int):
        out.append("%s: missing or non-integer `schema_version`" % rel)
    if rel.endswith("foundry-operators.json"):
        ops = doc.get("operators")
        if not isinstance(ops, dict):
            out.append("%s: `operators` missing or not an object" % rel)
        else:
            for oid, entry in sorted(ops.items()):
                if not isinstance(entry, dict):
                    out.append("%s: operators.%s is not an object" % (rel, oid))
                    continue
                for field in ("name", "github", "added_at"):
                    if not entry.get(field):
                        out.append("%s: operators.%s missing `%s`" % (rel, oid, field))
    return out


def validate(rel, doc, plugin_root):
    """-> (violations, skipped_reason_or_None). The fallback ALWAYS runs; the richer schema check runs only
    when a validator is importable, and its absence is an explicit SKIP -- never a crash, never a false PASS."""
    violations = _fallback_validate(rel, doc)
    schema_path = os.path.join(plugin_root, "schema", SCHEMA_FOR[rel])
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return violations, "no jsonschema module: full schema checks skipped (structural fallback ran)"
    try:
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, ValueError) as e:
        return violations, "schema unreadable (%s): full schema checks skipped" % e
    import jsonschema as _js
    validator = _js.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/" + "/".join(_esc(p) for p in err.path) if err.path else "(root)"
        # Report the failing RULE + location, not jsonschema's message, which interpolates the offending
        # instance -- that would push registry values (operator names, GitHub handles) or a whole document
        # into terminal transcripts and CI logs, whose audience and retention differ from the repo's.
        violations.append("%s: %s fails `%s`" % (rel, loc, err.validator))
    return violations, None


# ------------------------------------------------------------------- verbs ---
def _load_baseline(root):
    """-> (baseline_or_None, error_or_None). A PRESENT-but-unparseable baseline is an ERROR, never silently
    treated as absent -- that would discard the drift a valid baseline would have surfaced (AC-CFG-3)."""
    doc, err = read_json(os.path.join(root, BASELINE_REL))
    if err:
        return None, "%s %s" % (BASELINE_REL, err)
    if doc is None:
        return None, None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        return None, "%s malformed: missing `files` object" % BASELINE_REL
    return doc, None


def _survey(root):
    """Read every managed file + the baseline; classify what is baselined. Pure read."""
    plugin_root = _plugin_root()
    baseline, berr = _load_baseline(root)
    errors = [berr] if berr else []
    drift, schema_findings, skips, unbaselined = [], [], [], []
    for rel in MANAGED:
        doc, err = read_json(os.path.join(root, rel))
        if err:
            errors.append("%s %s" % (rel, err))
            continue
        if doc is None:
            errors.append("%s not found (run /foundry:init)" % rel)
            continue
        v, skip = validate(rel, doc, plugin_root)
        schema_findings.extend(v)
        if skip:
            skips.append("%s: %s" % (rel, skip))
        entry = (baseline or {}).get("files", {}).get(rel) if baseline else None
        if not isinstance(entry, dict) or "document" not in entry:
            unbaselined.append(rel)
            continue
        for ptr, cls in classify(entry["document"], doc):
            if cls != "clean":
                drift.append({"file": rel, "pointer": ptr, "class": cls})
    return {"drift": drift, "schema": schema_findings, "skips": skips,
            "unbaselined": unbaselined, "errors": errors,
            "baseline_present": baseline is not None}


def cmd_check(args):
    r = _survey(args.root)
    if args.json:
        print(json.dumps({"drift": r["drift"], "schema": r["schema"], "skips": r["skips"],
                          "unbaselined": r["unbaselined"], "errors": r["errors"],
                          "baseline_present": r["baseline_present"]}, indent=2, sort_keys=True))
    else:
        print("foundry config check")
        for e in r["errors"]:
            print("  [ERR ] %s" % e)
        for rel in r["unbaselined"]:
            print("  [    ] %s: UNBASELINED — no recorded baseline; run `foundry-config.py adopt --yes`" % rel)
        if r["drift"]:
            print("  drift:")
            for d in r["drift"]:
                print("    [%-15s] %s %s" % (d["class"], d["file"], d["pointer"]))
        elif not r["unbaselined"] and not r["errors"]:
            print("  drift: none — every baselined key is clean")
        if r["schema"]:
            print("  schema violations:")          # a DISTINCT section from drift (AC-CFG-3)
            for s in r["schema"]:
                print("    [invalid] %s" % s)
        else:
            print("  schema: no violations")
        for s in r["skips"]:
            print("  [skip] %s" % s)
    if r["errors"]:
        return EXIT_ERROR
    if r["drift"] or r["schema"] or r["unbaselined"]:
        return EXIT_FINDINGS
    return EXIT_CLEAN


def cmd_adopt(args):
    root = args.root
    baseline, berr = _load_baseline(root)
    if berr:
        print("refusing: %s" % berr, file=sys.stderr)
        return EXIT_ERROR
    if baseline is not None and not args.force:
        print("refusing: a baseline already exists at %s — pass --force to replace it" % BASELINE_REL,
              file=sys.stderr)
        return EXIT_ERROR

    plugin_root, docs, problems, skips = _plugin_root(), {}, [], []
    for rel in MANAGED:
        doc, err = read_json(os.path.join(root, rel))
        if err or doc is None:
            problems.append("%s %s" % (rel, err or "not found (run /foundry:init)"))
            continue
        v, skip = validate(rel, doc, plugin_root)
        problems.extend(v)           # PRE-WRITE assertion: validation that only advises is not a control
        if skip:
            skips.append("%s: %s" % (rel, skip))
        docs[rel] = doc
    # Say so when the pre-write control ran degraded. Silently blessing a baseline that only cleared the
    # dependency-free fallback would misrepresent how strong this gate actually was.
    for s in skips:
        print("  [skip] %s" % s, file=sys.stderr)
    if problems:
        print("refusing to record a baseline from an invalid config:", file=sys.stderr)
        for p in problems:
            print("  %s" % p, file=sys.stderr)
        return EXIT_ERROR

    # PLAN BEFORE BLESSING (AC-CFG-4): replacing a baseline is otherwise a laundering step that makes a
    # pre-existing bad edit indistinguishable from a legitimate one, forever.
    if baseline is not None:
        print("about to REPLACE the existing baseline; what changes relative to it:")
        any_row = False
        for rel in MANAGED:
            entry = baseline.get("files", {}).get(rel)
            if not isinstance(entry, dict) or "document" not in entry:
                print("  [newly-blessed ] %s (was not in the baseline)" % rel)
                any_row = True
                continue
            for ptr, cls in classify(entry["document"], docs[rel]):
                if cls != "clean":
                    print("  [%-15s] %s %s" % (cls, rel, ptr))
                    any_row = True
        if not any_row:
            print("  (no differences — the baseline is already current)")

    if not args.yes:
        print("refusing: --yes is required to write the baseline", file=sys.stderr)
        return EXIT_ERROR

    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "foundry_version": _plugin_version(),
        "recorded_at": _utc_now(),
        "files": {rel: {"schema_version": docs[rel].get("schema_version")
                        if isinstance(docs[rel].get("schema_version"), int) else None,
                        "document": docs[rel]} for rel in MANAGED},
    }
    atomic_write(os.path.join(root, BASELINE_REL), json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print("recorded baseline: %s (%d files)" % (BASELINE_REL, len(docs)))
    return EXIT_CLEAN


def main(argv=None):
    p = argparse.ArgumentParser(prog="foundry-config", description=__doc__.splitlines()[0])
    p.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
                   help="adopter repo root (default: $CLAUDE_PROJECT_DIR or cwd)")
    sub = p.add_subparsers(dest="verb", required=True)
    c = sub.add_parser("check", help="report per-key drift vs the baseline + schema validity (read-only)")
    c.add_argument("--json", action="store_true", help="emit machine-readable findings")
    c.set_defaults(func=cmd_check)
    a = sub.add_parser("adopt", help="record the current config as the baseline")
    a.add_argument("--yes", action="store_true", help="required to write")
    a.add_argument("--force", action="store_true", help="required to replace an existing baseline")
    a.set_defaults(func=cmd_adopt)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
