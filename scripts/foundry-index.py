#!/usr/bin/env python3
"""foundry-index.py — the thin, read-only machinery INDEX (feat-foundry-index).

DERIVES a deterministic `name · machinery · trigger` catalog from the `skills/*/SKILL.md` +
`agents/*.md` frontmatter (the source of truth — so the index can never drift), computed ON DEMAND
(no committed catalog, no staleness gate, no driver). It is the operationalization of the Terminology
Standard's trigger grammar: verb → machinery → example prompt. Read-only: it writes nothing.

Industry-standard pattern: a generated, read-only capability catalog (kubectl api-resources / gh /
git help -a / aws help).
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)

# A skill self-declares as a playbook (per docs/TERMINOLOGY.md §6) when its SKILL.md says "playbook"
# AND names an exit gate / exit criteria. Deterministic, frontmatter+body derived.
_PLAYBOOK_BODY = re.compile(r"playbook", re.IGNORECASE)
_EXIT_MARKER = re.compile(r"exit[\s-]?(?:gate|criteria|criterion)", re.IGNORECASE)


def _frontmatter(text):
    """{name, description} from the leading `--- ... ---` YAML block (tolerant line parse — no YAML dep,
    matching how the rest of foundry reads SKILL frontmatter)."""
    out = {"name": None, "description": None}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end == -1:
        return out
    block = text[3:end]
    for line in block.splitlines():
        m = re.match(r"\s*(name|description)\s*:\s*(.*)$", line)
        if m and out.get(m.group(1)) is None:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _trigger_excerpt(description, limit=140):
    """The first sentence/segment of the description — where the verb-object + NL trigger phrases live."""
    if not description:
        return ""
    seg = re.split(r"(?<=[.])\s|\s[—–-]\s", description, maxsplit=1)[0].strip()
    return (seg[: limit - 1] + "…") if len(seg) > limit else seg


def classify(*, is_agent, body):
    """agent (under agents/) · playbook (a skill self-declaring the playbook shape + an exit gate) · skill."""
    if is_agent:
        return "agent"
    if _PLAYBOOK_BODY.search(body or "") and _EXIT_MARKER.search(body or ""):
        return "playbook"
    return "skill"


def build_index(plugin_root=None):
    """Deterministic, name-sorted list of {name, machinery, trigger}. Read-only enumeration of
    skills/*/SKILL.md + agents/*.md. Falls back to the dir/file stem when a `name` is absent."""
    root = plugin_root or PLUGIN_ROOT
    rows = []
    skills_dir = os.path.join(root, "skills")
    if os.path.isdir(skills_dir):
        for entry in os.listdir(skills_dir):
            sp = os.path.join(skills_dir, entry, "SKILL.md")
            if not os.path.isfile(sp):
                continue
            body = open(sp, encoding="utf-8").read()
            fm = _frontmatter(body)
            rows.append({"name": fm["name"] or entry,
                         "machinery": classify(is_agent=False, body=body),
                         "trigger": _trigger_excerpt(fm["description"])})
    agents_dir = os.path.join(root, "agents")
    if os.path.isdir(agents_dir):
        for fn in os.listdir(agents_dir):
            if not fn.endswith(".md"):
                continue
            body = open(os.path.join(agents_dir, fn), encoding="utf-8").read()
            fm = _frontmatter(body)
            rows.append({"name": fm["name"] or os.path.splitext(fn)[0],
                         "machinery": classify(is_agent=True, body=body),
                         "trigger": _trigger_excerpt(fm["description"])})
    rows.sort(key=lambda r: (r["name"] or "").lower())
    return rows


def render(rows):
    """A markdown table: name | machinery | trigger."""
    out = ["| name | machinery | trigger |", "|---|---|---|"]
    for r in rows:
        trig = (r["trigger"] or "").replace("|", "\\|")
        out.append(f"| `{r['name']}` | {r['machinery']} | {trig} |")
    counts = {}
    for r in rows:
        counts[r["machinery"]] = counts.get(r["machinery"], 0) + 1
    summary = ", ".join(f"{counts[k]} {k}(s)" for k in sorted(counts))
    out.append("")
    out.append(f"_{len(rows)} entries — {summary}_")
    return "\n".join(out)


def _selftest():
    """Hermetic proof over a throwaway temp mini-plugin (sample skills + an agent). Asserts deterministic
    enumeration, machinery classification, and read-only behavior. Emits the FROZEN tokens."""
    import tempfile
    lines, ok = [], True

    def emit(token, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        lines.append(f"{token}: {'PASS' if passed else 'FAIL'}{(' — ' + detail) if detail else ''}")

    def write_skill(root, name, desc, body_extra=""):
        d = os.path.join(root, "skills", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\n{body_extra}\n")

    def write_agent(root, name, desc):
        d = os.path.join(root, "agents")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.md"), "w", encoding="utf-8") as f:
            f.write(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n")

    def snapshot(d):
        out = {}
        for r_, _, files in os.walk(d):
            for fn in files:
                p = os.path.join(r_, fn)
                out[os.path.relpath(p, d)] = open(p, "rb").read()
        return out

    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, ".claude-plugin"), exist_ok=True)
        write_skill(root, "zeta-skill", "do zeta things — \"do zeta\"")
        write_skill(root, "alpha-play", "Cut a thing as a guarded playbook — \"cut the thing\"",
                    body_extra="This is a **playbook** with an **EXIT GATE**.")
        write_agent(root, "beta-reviewer", "review a diff for risk")

        # AC-IDX-1: enumerate all three, deterministic (name-sorted), read-only (tree byte-unchanged).
        before = snapshot(root)
        rows = build_index(root)
        rows2 = build_index(root)
        after = snapshot(root)
        names = [r["name"] for r in rows]
        deterministic = rows == rows2 and names == sorted(names, key=str.lower)
        enumerates_all = set(names) == {"alpha-play", "beta-reviewer", "zeta-skill"}
        read_only = before == after
        has_trigger = all(r["trigger"] for r in rows)
        emit("index-enumerates-deterministic",
             deterministic and enumerates_all and read_only and has_trigger,
             f"deterministic={deterministic} all_present={enumerates_all} read_only={read_only} triggers={has_trigger}")

        # AC-IDX-2: machinery classification — agent / playbook / skill.
        by = {r["name"]: r["machinery"] for r in rows}
        classes_ok = (by.get("beta-reviewer") == "agent"
                      and by.get("alpha-play") == "playbook"
                      and by.get("zeta-skill") == "skill")
        emit("index-classifies-machinery", classes_ok,
             f"agent={by.get('beta-reviewer')} playbook={by.get('alpha-play')} skill={by.get('zeta-skill')}")

    print("foundry-index self-test:")
    for ln in lines:
        print("  " + ln)
    print("INDEX-SELFTEST-GREEN" if ok else "INDEX-SELFTEST-RED")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Foundry machinery index — a read-only verb→machinery→trigger "
                                             "catalog derived from skills/agents frontmatter.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    rows = build_index()
    if args.json:
        import json
        print(json.dumps(rows, indent=2, sort_keys=False))
    else:
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
