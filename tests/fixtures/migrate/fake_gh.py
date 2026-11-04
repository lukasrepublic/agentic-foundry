#!/usr/bin/env python3
"""A fake `gh` for tests/test_migrate.py. No network. Records every argv (and stdin) to $FAKE_GH_LOG and
serves state from the JSON file $FAKE_GH_STATE.

It MODELS GitHub's real defaults so an implementation that under-reads fails:
  * `gh issue list` without flags returns only OPEN issues, capped at 30;
  * the REST issue listing returns 100 per page and returns later pages only under --paginate
    (concatenated JSON arrays, as gh prints them).

State shape:
  {"user": "me",
   "repos": {"o/r": {"visibility": "PRIVATE", "error": false,
                      "issues": [ {number, state, state_reason, user:{login}, author_association, body,
                                   title, html_url} ... ], "next": 1}},
   "projects": [{"title": "T", "public": false}],
   "fail_create_after": null,        # fail every `issue create` once this many have been created
   "fail": []}                       # list of argv prefixes (lists) that exit 1
"""
import json
import os
import sys

STATE = os.environ["FAKE_GH_STATE"]
LOG = os.environ["FAKE_GH_LOG"]
argv = sys.argv[1:]
stdin = None


def load():
    with open(STATE) as fh:
        return json.load(fh)


def save(st):
    with open(STATE, "w") as fh:
        json.dump(st, fh)


def die(msg, rc=1):
    print(msg, file=sys.stderr)
    sys.exit(rc)


def flag_values(args, name):
    out, i = [], 0
    while i < len(args):
        if args[i] == name and i + 1 < len(args):
            out.append(args[i + 1])
            i += 2
        else:
            i += 1
    return out


if argv[:2] == ["issue", "create"]:
    stdin = sys.stdin.read()
with open(LOG, "a") as fh:
    fh.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")

st = load()
for pref in st.get("fail", []):
    if argv[:len(pref)] == pref:
        die("injected failure")

if argv == ["api", "user"]:
    print(json.dumps({"login": st["user"]}))
elif argv[:2] == ["repo", "view"]:
    repo = st["repos"].get(argv[2])
    if repo is None or repo.get("error"):
        die("GraphQL: Could not resolve to a Repository (HTTP 404)")
    print(json.dumps({"visibility": repo["visibility"]}))
elif argv[:1] == ["api"] and "--paginate" in argv or argv[:1] == ["api"] and argv[1].startswith("repos/"):
    path = [a for a in argv[1:] if a.startswith("repos/")][0]
    repo_name = path.split("/issues")[0][len("repos/"):]
    repo = st["repos"].get(repo_name)
    if repo is None or repo.get("error"):
        die("HTTP 404")
    issues = sorted(repo["issues"], key=lambda i: -i["number"])
    pages = [issues[i:i + 100] for i in range(0, len(issues), 100)] or [[]]
    if "--paginate" in argv:
        print("".join(json.dumps(p) for p in pages))
    else:
        print(json.dumps(pages[0]))
elif argv[:2] == ["issue", "list"]:
    repo = st["repos"][flag_values(argv, "--repo")[0]]
    openi = [i for i in sorted(repo["issues"], key=lambda i: -i["number"]) if i["state"] == "open"][:30]
    print(json.dumps(openi))
elif argv[:2] == ["issue", "create"]:
    r = flag_values(argv, "--repo")[0]
    repo = st["repos"][r]
    limit = st.get("fail_create_after")
    if limit is not None and st.get("created", 0) >= limit:
        die("injected create failure")
    n = repo.get("next") or (max([i["number"] for i in repo["issues"]] or [0]) + 1)
    repo["next"] = n + 1
    issue = {"number": n, "state": "open", "state_reason": None, "user": {"login": st["user"]},
             "author_association": "OWNER", "body": stdin, "title": flag_values(argv, "--title")[0],
             "html_url": f"https://github.com/{r}/issues/{n}",
             "blocked_by": flag_values(argv, "--blocked-by"), "assignees": flag_values(argv, "--assignee"),
             "project": flag_values(argv, "--project")}
    repo["issues"].append(issue)
    st["created"] = st.get("created", 0) + 1
    save(st)
    print(issue["html_url"])
elif argv[:2] == ["issue", "reopen"]:
    repo = st["repos"][flag_values(argv, "--repo")[0]]
    for i in repo["issues"]:
        if str(i["number"]) == argv[2]:
            i["state"], i["state_reason"] = "open", "reopened"
    save(st)
elif argv[:2] == ["project", "list"]:
    print(json.dumps({"projects": st.get("projects", []), "totalCount": len(st.get("projects", []))}))
else:
    die(f"fake gh: unsupported argv {argv}", 2)
