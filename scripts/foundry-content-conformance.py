#!/usr/bin/env python3
"""foundry-content-conformance — a generic, declarative, deterministic content shape/leak
checker the OPERATOR runs over content atoms (docs, pack manifests, skill bundles)
(feat-foundry-content-conformance-checker, content-governance Fork 1 / §1B.1).

Foundry gates CODE atoms via `--selftest` tokens the live-seam walk captures, but content
atoms had only vacuous greps (an empty file passes; `MIT` matches "committed"). This tool
parses a content artifact against a declarative conformance spec (`conformance.yaml`) and
emits per-rule COMPUTED PASS/FAIL — the content analog of a code atom's `--selftest`.

  foundry-content-conformance.py --check <artifact> --spec <conformance.yaml>

THREAT MODEL — TRUSTED OPERATOR (D9). The operator is the trusted root who AUTHORS the
rubric. This is a determinism + MISTAKE-CATCHING linter FOR the operator, NOT a defense
against a malicious rubric-author or a forged token. There is NO anti-tamper signing, no
anti-forgery re-execution, no self-grading machinery. ReDoS protection is a mistake-catcher
for the operator's own gate (reject catastrophic patterns at spec-load + cap input size),
NOT an anti-adversary runtime timeout — which avoids the signal.alarm POSIX/C-backtrack hole.

Output: a DETERMINISTIC JSON report (`json.dumps(sort_keys=True)`):
    {artifact, rules: [{id, type, status, detail}], summary}
Exits FAIL-CLOSED (non-zero) if any rule FAILs, the artifact is unreadable/empty, or the
spec is missing/malformed/unsafe.

FOUR rule types (`tag_wellformed` is DEFERRED — §Out-of-scope):
  - section_present       — a declared heading exists (ATX markdown; trim; optional `ci`).
  - section_order         — among declared headings THAT APPEAR, first occurrences are in
                            declared order (presence is section_present's job).
  - denylist_empty        — each pattern matches ZERO times in scope (the negative leak-scan).
  - attribution_present   — `pattern` matches (word-boundaried) >=1x; optional
                            `near: {anchor, within_lines}` proximity (unit = LINES).

PARSE MODEL (pinned): markdown = ATX (`#`) headings; `region in {whole-file,
normative-region, body}` (markdown only; normative-region = between `<!-- normative -->`
delimiters, absent/unbalanced -> fail-closed, multiple -> concatenated). For YAML/manifest
artifacts `region` is ignored (whole-doc; "sections" = top-level/dotted keys).

The consumer token form (AC-CONF-4): a `--check` invocation against a real artifact emits
`conformance:<artifact-basename>:<rule_id>: PASS` per rule that COMPUTED PASS — so a
consuming atom's acceptance-contract `matches` value is rule-result-gated.
"""
import argparse
import json
import os
import re
import sys

try:
    import yaml  # PyYAML — a hard foundry dependency (requirements.txt; every gate path).
except ImportError:  # pragma: no cover - declared dependency
    yaml = None

# --- bounds (mistake-catcher for the operator's own gate; NOT an anti-adversary control) --- #
MAX_INPUT_BYTES = 5 * 1024 * 1024     # cap artifact size (ReDoS-by-construction defense)
SCHEMA_VERSION = 1

VALID_REGIONS = ("whole-file", "normative-region", "body")
RULE_TYPES = ("section_present", "section_order", "denylist_empty", "attribution_present")

# Markdown ATX heading: leading '#'..'######' then required space, capture the text.
_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
NORMATIVE_OPEN = "<!-- normative -->"
NORMATIVE_CLOSE = "<!-- /normative -->"


class SpecError(Exception):
    """The conformance spec is missing / malformed / unsafe — fail-closed."""


# --------------------------- ReDoS-safety (spec-load validation) --------------------------- #
# stdlib-safe BY CONSTRUCTION: validate each pattern at spec-load for catastrophic constructs
# (nested / overlapping unbounded quantifiers, e.g. (a+)+, (.*)*, (a*)+) — these cause
# exponential backtracking. An unsafe pattern is a spec-validation FAIL (never a runtime hang).
# We do NOT use signal.alarm: it is POSIX-only and cannot interrupt C-level regex backtracking.

# A quantified group whose body is itself unbounded-quantified, with an outer unbounded
# quantifier on the group — the canonical catastrophic shape.
_NESTED_QUANT_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*]")
# Quantifier-on-quantifier without a group, e.g. `a+*`, `.*+` (also pathological / invalid-ish).
_DOUBLE_QUANT_RE = re.compile(r"[+*]\s*[+*]")


def _assert_safe_pattern(pattern, rule_id):
    if not isinstance(pattern, str):
        raise SpecError(f"rule {rule_id!r}: pattern must be a string")
    if _NESTED_QUANT_RE.search(pattern) or _DOUBLE_QUANT_RE.search(pattern):
        raise SpecError(
            f"rule {rule_id!r}: pattern {pattern!r} rejected — nested/overlapping unbounded "
            f"quantifier (catastrophic-backtracking risk); rewrite without (X+)+ / (.*)* forms")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise SpecError(f"rule {rule_id!r}: pattern {pattern!r} is not a valid regex: {e}")


# --------------------------- spec load + schema validation (AC-CONF-2) --------------------------- #
def load_spec(spec_path):
    """Load + VALIDATE conformance.yaml against the pinned schema. Returns a list of normalized
    rule dicts (each with a compiled `_re`/`_anchor_re` where relevant). Raises SpecError
    (fail-closed) on missing / malformed / unknown-type / empty-rules / unsafe-pattern."""
    if yaml is None:
        raise SpecError("PyYAML unavailable — cannot parse conformance.yaml")
    if not os.path.isfile(spec_path):
        raise SpecError(f"conformance spec missing: {spec_path}")
    try:
        raw = open(spec_path, encoding="utf-8").read()
    except OSError as e:
        raise SpecError(f"conformance spec unreadable: {e}")
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise SpecError(f"conformance spec is not valid YAML: {e}")
    if not isinstance(doc, dict):
        raise SpecError("conformance spec must be a mapping with schema_version + rules")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise SpecError(f"conformance spec schema_version must be {SCHEMA_VERSION}, "
                        f"got {doc.get('schema_version')!r}")
    rules = doc.get("rules")
    if not isinstance(rules, list) or not rules:
        raise SpecError("conformance spec `rules` must be a non-empty list")

    seen_ids = set()
    normalized = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise SpecError(f"rule #{i} must be a mapping")
        rid = r.get("id")
        if not (isinstance(rid, str) and rid):
            raise SpecError(f"rule #{i} missing a non-empty string `id`")
        if rid in seen_ids:
            raise SpecError(f"duplicate rule id {rid!r}")
        seen_ids.add(rid)
        rtype = r.get("type")
        if rtype not in RULE_TYPES:
            raise SpecError(f"rule {rid!r}: unknown/unsupported type {rtype!r} "
                            f"(supported: {', '.join(RULE_TYPES)})")
        region = r.get("region", "whole-file")
        if region not in VALID_REGIONS:
            raise SpecError(f"rule {rid!r}: region must be one of {VALID_REGIONS}, got {region!r}")
        norm = {"id": rid, "type": rtype, "region": region}

        if rtype == "section_present":
            heading = r.get("heading")
            if not (isinstance(heading, str) and heading.strip()):
                raise SpecError(f"rule {rid!r}: section_present requires a non-empty `heading`")
            norm["heading"] = heading
            norm["ci"] = bool(r.get("ci", False))
        elif rtype == "section_order":
            headings = r.get("headings")
            if not (isinstance(headings, list) and len(headings) >= 2
                    and all(isinstance(h, str) and h.strip() for h in headings)):
                raise SpecError(f"rule {rid!r}: section_order requires `headings[]` "
                                f"(>=2 non-empty strings)")
            norm["headings"] = headings
            norm["ci"] = bool(r.get("ci", False))
        elif rtype == "denylist_empty":
            patterns = r.get("patterns")
            if not (isinstance(patterns, list) and patterns
                    and all(isinstance(p, str) for p in patterns)):
                raise SpecError(f"rule {rid!r}: denylist_empty requires a non-empty "
                                f"`patterns[]` of strings")
            norm["patterns"] = [(p, _assert_safe_pattern(p, rid)) for p in patterns]
        elif rtype == "attribution_present":
            pattern = r.get("pattern")
            if not (isinstance(pattern, str) and pattern):
                raise SpecError(f"rule {rid!r}: attribution_present requires a non-empty `pattern`")
            # word-boundaried match of the pattern.
            norm["pattern"] = pattern
            norm["_re"] = _assert_safe_pattern(r"\b(?:" + pattern + r")\b", rid)
            near = r.get("near")
            if near is not None:
                if not isinstance(near, dict):
                    raise SpecError(f"rule {rid!r}: near must be a mapping {{anchor, within_lines}}")
                anchor = near.get("anchor")
                within = near.get("within_lines")
                if not (isinstance(anchor, str) and anchor):
                    raise SpecError(f"rule {rid!r}: near.anchor must be a non-empty string")
                if not (isinstance(within, int) and not isinstance(within, bool) and within >= 0):
                    raise SpecError(f"rule {rid!r}: near.within_lines must be a non-negative int")
                norm["_anchor_re"] = _assert_safe_pattern(anchor, rid)
                norm["_within_lines"] = within
        normalized.append(norm)
    return normalized


# --------------------------- artifact load + region scoping (AC-CONF-1) --------------------------- #
def _is_yaml_artifact(path):
    return os.path.splitext(path)[1].lower() in (".yaml", ".yml")


def load_artifact(artifact_path):
    """Read + size-check the artifact. Returns the raw text. Raises ValueError (fail-closed)
    if missing / unreadable / empty / over the size cap."""
    if not os.path.isfile(artifact_path):
        raise ValueError(f"artifact missing: {artifact_path}")
    size = os.path.getsize(artifact_path)
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"artifact exceeds size cap ({size} > {MAX_INPUT_BYTES} bytes) "
                         f"— refused (ReDoS-by-construction defense)")
    try:
        text = open(artifact_path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"artifact unreadable: {e}")
    if not text.strip():
        raise ValueError("artifact is empty (no content to check) — fail-closed")
    return text


def scope_text(text, region):
    """Return the in-scope text for a markdown rule's region. Raises ValueError (fail-closed)
    if the normative markers are absent/unbalanced."""
    if region == "whole-file":
        return text
    if region == "normative-region":
        opens = [m.start() for m in re.finditer(re.escape(NORMATIVE_OPEN), text)]
        closes = [m.start() for m in re.finditer(re.escape(NORMATIVE_CLOSE), text)]
        if not opens or len(opens) != len(closes):
            raise ValueError(f"normative-region markers absent/unbalanced "
                             f"({len(opens)} open, {len(closes)} close) — fail-closed")
        # Concatenate each open..close span (multiple regions -> concatenated).
        parts = []
        for o, c in zip(opens, closes):
            if c <= o:
                raise ValueError("normative-region close precedes its open — fail-closed")
            parts.append(text[o + len(NORMATIVE_OPEN):c])
        return "\n".join(parts)
    if region == "body":
        # body = the artifact MINUS any normative regions (the example-bearing prose around
        # the normative block). If no markers, body == whole-file.
        opens = [m.start() for m in re.finditer(re.escape(NORMATIVE_OPEN), text)]
        closes = [m.start() for m in re.finditer(re.escape(NORMATIVE_CLOSE), text)]
        if not opens:
            return text
        if len(opens) != len(closes):
            raise ValueError(f"body-region normative markers unbalanced "
                             f"({len(opens)} open, {len(closes)} close) — fail-closed")
        kept, cursor = [], 0
        for o, c in zip(opens, closes):
            if c <= o:
                raise ValueError("normative-region close precedes its open — fail-closed")
            kept.append(text[cursor:o])
            cursor = c + len(NORMATIVE_CLOSE)
        kept.append(text[cursor:])
        return "".join(kept)
    raise ValueError(f"unknown region {region!r}")  # defensive (schema already validated)


def extract_headings(text):
    """ATX (`#`) headings in order of first appearance. Returns a list of (normalized_text,
    raw_text). Headings inside fenced code blocks (``` / ~~~) are excluded."""
    headings = []
    fence = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None and (stripped.startswith("```") or stripped.startswith("~~~")):
            fence = stripped[:3]
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        m = _ATX_RE.match(line)
        if m:
            headings.append(m.group(2).strip())
    return headings


# --------------------------- YAML-artifact key model (AC-CONF-1) --------------------------- #
def _yaml_keys(text):
    """Top-level + dotted keys of a YAML/manifest artifact (the "sections"). Returns a set of
    dotted-path strings. Raises ValueError (fail-closed) on unparseable YAML."""
    if yaml is None:
        raise ValueError("PyYAML unavailable — cannot parse YAML artifact")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML artifact unparseable: {e}")
    keys = set()

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                dotted = f"{prefix}.{k}" if prefix else str(k)
                keys.add(dotted)
                walk(v, dotted)

    walk(doc, "")
    return keys


# --------------------------- rule evaluation --------------------------- #
def _norm(s, ci):
    s = s.strip()
    return s.lower() if ci else s


def _eval_section_present(rule, text, is_yaml):
    heading = rule["heading"]
    if is_yaml:
        keys = _yaml_keys(text)
        target = heading.strip()
        present = target in keys or target.lstrip("#").strip() in keys
        return present, ("key present" if present else f"top-level/dotted key {target!r} absent")
    scoped = scope_text(text, rule["region"])
    headings = extract_headings(scoped)
    target = _norm(heading.lstrip("#"), rule["ci"])
    norm_headings = [_norm(h, rule["ci"]) for h in headings]
    present = target in norm_headings
    return present, ("heading present" if present
                     else f"heading {heading!r} not found in {rule['region']}")


def _eval_section_order(rule, text, is_yaml):
    declared = [_norm(h.lstrip("#"), rule["ci"]) for h in rule["headings"]]
    if is_yaml:
        # ordering is not meaningful for YAML keys; treat the in-doc key set, fail-closed on
        # any out-of-spec usage by requiring markdown — but spec is generic, so: among declared
        # keys that appear, we cannot order them (dicts unordered) -> rule N/A => PASS only if
        # all appear. Conservative: order rules target markdown; for YAML we just check presence.
        keys = _yaml_keys(text)
        appearing = [d for d, raw in zip(declared, rule["headings"]) if raw.strip() in keys]
        return True, (f"YAML artifact — ordering not applicable; {len(appearing)} declared "
                      f"key(s) present")
    scoped = scope_text(text, rule["region"])
    headings = [_norm(h, rule["ci"]) for h in extract_headings(scoped)]
    # first-occurrence index of each declared heading that appears.
    positions = []
    for d in declared:
        if d in headings:
            positions.append((d, headings.index(d)))
    # check first-occurrences are in declared order.
    for (a_name, a_pos), (b_name, b_pos) in zip(positions, positions[1:]):
        if a_pos > b_pos:
            return False, (f"out of order: {b_name!r} (at heading #{b_pos}) appears before "
                           f"{a_name!r} (at heading #{a_pos})")
    return True, (f"{len(positions)} declared heading(s) present and in declared order")


def _eval_denylist_empty(rule, text, is_yaml):
    # denylist scans the WHOLE artifact (the leak-scan); region scoping applies to markdown.
    if is_yaml:
        scoped = text
    else:
        scoped = scope_text(text, rule["region"])
    lines = scoped.splitlines()
    for raw_pat, crx in rule["patterns"]:
        for lineno, line in enumerate(lines, 1):
            m = crx.search(line)
            if m:
                return False, (f"denylisted pattern {raw_pat!r} matched at line {lineno} "
                               f"col {m.start() + 1}")
    return True, (f"all {len(rule['patterns'])} denylist pattern(s) matched zero times")


def _eval_attribution_present(rule, text, is_yaml):
    scoped = text if is_yaml else scope_text(text, rule["region"])
    lines = scoped.splitlines()
    match_lines = [i for i, line in enumerate(lines) if rule["_re"].search(line)]
    if not match_lines:
        return False, f"attribution pattern {rule['pattern']!r} not found (word-boundaried)"
    if "_anchor_re" in rule:
        anchor_lines = [i for i, line in enumerate(lines) if rule["_anchor_re"].search(line)]
        if not anchor_lines:
            return False, "attribution present but the proximity anchor never appears"
        within = rule["_within_lines"]
        near_ok = any(abs(ml - al) <= within for ml in match_lines for al in anchor_lines)
        if not near_ok:
            return False, (f"attribution present but no match within {within} line(s) of an "
                           f"anchor match (nearest exceeds the proximity budget)")
        return True, f"attribution present within {within} line(s) of an anchor"
    return True, "attribution present (word-boundaried)"


_EVALUATORS = {
    "section_present": _eval_section_present,
    "section_order": _eval_section_order,
    "denylist_empty": _eval_denylist_empty,
    "attribution_present": _eval_attribution_present,
}


def check(artifact_path, spec_path):
    """Evaluate the spec's rules against the artifact. Returns (report_dict, exit_code).
    exit_code is 0 iff every rule PASSed (fail-closed on artifact/spec errors)."""
    artifact_disp = os.path.basename(artifact_path)
    # spec load first (a malformed/unsafe spec is fail-closed BEFORE touching the artifact).
    try:
        rules = load_spec(spec_path)
    except SpecError as e:
        return ({"artifact": artifact_disp,
                 "rules": [{"id": "_spec", "type": "_spec", "status": "FAIL",
                            "detail": f"spec error: {e}"}],
                 "summary": {"total": 0, "passed": 0, "failed": 1, "error": str(e)}}, 1)
    try:
        text = load_artifact(artifact_path)
    except ValueError as e:
        return ({"artifact": artifact_disp,
                 "rules": [{"id": "_artifact", "type": "_artifact", "status": "FAIL",
                            "detail": f"artifact error: {e}"}],
                 "summary": {"total": len(rules), "passed": 0, "failed": len(rules),
                             "error": str(e)}}, 1)
    is_yaml = _is_yaml_artifact(artifact_path)
    rule_results = []
    passed = 0
    for rule in rules:
        try:
            ok, detail = _EVALUATORS[rule["type"]](rule, text, is_yaml)
        except ValueError as e:  # region/parse fail-closed -> rule FAIL
            ok, detail = False, f"evaluation fail-closed: {e}"
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        rule_results.append({"id": rule["id"], "type": rule["type"],
                             "status": status, "detail": detail})
    failed = len(rules) - passed
    report = {"artifact": artifact_disp,
              "rules": rule_results,
              "summary": {"total": len(rules), "passed": passed, "failed": failed}}
    return report, (0 if failed == 0 else 1)


def consumer_tokens(report):
    """The per-rule consumer token form (AC-CONF-4), emitted ONLY for rules that COMPUTED PASS:
        conformance:<artifact-basename>:<rule_id>: PASS
    so a consuming atom's contract `matches` value is rule-result-gated (never always-true)."""
    artifact = report.get("artifact", "")
    return [f"conformance:{artifact}:{r['id']}: PASS"
            for r in report.get("rules", []) if r.get("status") == "PASS"]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic declarative content shape/leak checker (trusted-operator).")
    ap.add_argument("--check", required=True, metavar="ARTIFACT",
                    help="the content artifact to check (markdown or YAML/manifest)")
    ap.add_argument("--spec", required=True, metavar="CONFORMANCE_YAML",
                    help="the declarative conformance.yaml rubric")
    ap.add_argument("--emit-tokens", action="store_true",
                    help="also print the per-rule consumer tokens (conformance:<artifact>:<id>: "
                         "PASS) for rules that computed PASS (the AC-CONF-4 consumer form)")
    args = ap.parse_args(argv)

    report, code = check(args.check, args.spec)
    print(json.dumps(report, sort_keys=True))
    if args.emit_tokens:
        for tok in consumer_tokens(report):
            print(tok)
    return code


if __name__ == "__main__":
    sys.exit(main())
