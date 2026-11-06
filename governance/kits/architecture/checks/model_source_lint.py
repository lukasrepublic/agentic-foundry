#!/usr/bin/env python3
"""Model source lint for a Structurizr DSL workspace (docs/architecture/workspace.dsl).

  model_source_lint.py [FILE]      (default docs/architecture/workspace.dsl)

Fails (exit 1, one line per finding naming the element and its line) on:
  - an element (person, softwareSystem, container, component, deploymentNode, infrastructureNode,
    containerInstance, softwareSystemInstance, element; keywords match case-insensitively) with no non-empty `source` property
  - an element still tagged `draft` once the workspace is marked confirmed
    (workspace-level `properties { "model-status" "confirmed" }`)
An element is a model element the extraction produced; a name or description may be written by a person or a model,
but an element with no evidence locator is refused. Exit 2 when FILE is missing. Exit 0 and "model-lint: clean" otherwise.
"""
import re
import sys
from pathlib import Path

# deploymentEnvironment is not an element here: Structurizr DSL allows no tags or properties inside it.
KINDS = ("person", "softwareSystem", "container", "component", "deploymentNode", "infrastructureNode",
         "containerInstance", "softwareSystemInstance", "element")
ELEMENT_RE = re.compile(r"^\s*(?:([\w.-]+)\s*=\s*)?(" + "|".join(KINDS) + r")\b\s*(\"(?:[^\"\\]|\\.)*\")?", re.I)
TOKEN = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|([^\s\",]+)")


def tokens(line):
    """Quoted and unquoted tokens alike (`tags draft` is the same as `tags "draft"`)."""
    return [a if b == "" else b for a, b in TOKEN.findall(line)]


def strip_comment(line):
    out, in_q, i = [], False, 0
    while i < len(line):
        c = line[i]
        if c == "\\" and in_q and i + 1 < len(line):
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == '"':
            in_q = not in_q
        elif not in_q and (c == "#" or line[i:i + 2] == "//"):
            break
        out.append(c)
        i += 1
    return "".join(out)


def lint_text(text):
    findings = []
    stack = []          # frames: dict(kind, name, line, source, draft)
    confirmed = False
    elements = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        opens, closes = line.count("{"), line.count("}")
        m = None if any(fr.get("kind", "").lower() in ("views", "configuration") for fr in stack) else ELEMENT_RE.match(line)
        if m:
            fr = {"kind": m.group(2), "name": (m.group(3) or '""').strip('"') or m.group(1) or m.group(2),
                  "line": no, "source": False, "draft": False, "element": True}
            elements.append(fr)
            if opens > closes:
                stack.append(fr)
            continue
        if opens and not closes:
            head = line.split("{")[0].strip().split()
            stack.append({"kind": head[0].lower() if head else "", "element": False})
            continue
        if closes and not opens:
            for _ in range(closes):
                if stack:
                    stack.pop()
            continue
        # a plain statement inside the current frame
        top = stack[-1] if stack else None
        if top is None:
            continue
        toks = tokens(line)
        word = toks[0].lower() if toks else ""
        if word == "tags" and top.get("element"):
            if "draft" in {y.strip().lower() for x in toks[1:] for y in x.split(",")}:
                top["draft"] = True
        elif top.get("kind") == "properties" and len(stack) >= 2 and len(toks) >= 2:
            parent = stack[-2]
            if parent.get("element") and toks[0] == "source" and toks[1].strip():
                parent["source"] = True
            elif not parent.get("element") and len(stack) == 2 and toks[0].lower() == "model-status" \
                    and toks[1].strip().lower() == "confirmed":
                confirmed = True
    for e in elements:
        if not e["source"]:
            findings.append(f"line {e['line']}: {e['kind']} {e['name']!r} has no source property")
        if confirmed and e["draft"]:
            findings.append(f"line {e['line']}: {e['kind']} {e['name']!r} is still tagged draft in a confirmed model")
    return findings


def main(argv):
    target = Path(argv[0] if argv else "docs/architecture/workspace.dsl")
    if not target.is_file():
        print(f"model-lint: not a file: {target}", file=sys.stderr)
        return 2
    findings = lint_text(target.read_text(encoding="utf-8").replace("\r\n", "\n"))
    for f in findings:
        print(f)
    if not findings:
        print("model-lint: clean")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
