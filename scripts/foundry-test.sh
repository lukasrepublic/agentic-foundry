#!/usr/bin/env bash
# foundry-test — run the repository's own CI command LOCALLY and record local-green for HEAD.
#
# Why: measured on an adopter app repo, 890 pull requests in 56 days (median 5 files) each ran a
# 32-minute CI pipeline and every merge deployed to staging — CI was doing the discovery that a
# local run should. The git-discipline hook refuses `git push` (and a non-draft `gh pr create`)
# unless `<git-dir>/foundry-local-green` names the current HEAD; this script is what writes it.
#
# The command is resolved in this order (first hit wins):
#   1. $FOUNDRY_TEST_CMD
#   2. Taskfile with a `ci:` task  → task ci       (else a `test:` task → task test)
#   3. Makefile with a `test:` target → make test
#   4. package.json with scripts.test → npm test --silent
#   5. pytest.ini / pyproject.toml / tests/ → python3 -m pytest -q
#   6. nothing found → the marker is written with "notests" and a warning (an untested repo is
#      not gated; that is honest, not safe — add a test command).
#
# usage: foundry-test.sh [--print-cmd] [--dir PATH]
set -uo pipefail
dir="$PWD"; print_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) dir="$2"; shift 2 ;;
    --print-cmd) print_only=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "foundry-test: unknown argument $1" >&2; exit 2 ;;
  esac
done
cd "$dir" || exit 2
gitdir="$(git rev-parse --git-dir 2>/dev/null)" || { echo "foundry-test: not a git repository" >&2; exit 2; }
case "$gitdir" in /*) : ;; *) gitdir="$PWD/$gitdir" ;; esac
head="$(git rev-parse HEAD 2>/dev/null || echo none)"

cmd=""
if [ -n "${FOUNDRY_TEST_CMD:-}" ]; then
  cmd="$FOUNDRY_TEST_CMD"
elif tf="$(ls Taskfile.yml Taskfile.yaml 2>/dev/null | head -1)" && [ -n "$tf" ]; then
  if grep -qE '^\s{2}ci:' "$tf"; then cmd="task ci"; elif grep -qE '^\s{2}test:' "$tf"; then cmd="task test"; fi
fi
if [ -z "$cmd" ] && [ -f Makefile ] && grep -qE '^test:' Makefile; then cmd="make test"; fi
if [ -z "$cmd" ] && [ -f package.json ] && python3 -c 'import json,sys; sys.exit(0 if (json.load(open("package.json")).get("scripts") or {}).get("test") else 1)' 2>/dev/null; then cmd="npm test --silent"; fi
if [ -z "$cmd" ] && { [ -f pytest.ini ] || [ -f pyproject.toml ] || [ -d tests ]; }; then cmd="python3 -m pytest -q"; fi

if [ "$print_only" = 1 ]; then echo "${cmd:-notests}"; exit 0; fi

if [ -z "$cmd" ]; then
  printf '%s notests %s\n' "$head" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$gitdir/foundry-local-green"
  echo "foundry-test: no test command found (Taskfile ci/test, Makefile test, npm test, pytest) — recorded local-green for $head as UNTESTED. Add a test command." >&2
  exit 0
fi

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  echo "foundry-test: working tree has uncommitted changes — the marker covers HEAD ($head) only; commit first if you intend to push these." >&2
fi
echo "foundry-test: running: $cmd" >&2
start=$(date +%s)
bash -lc "$cmd"; rc=$?
secs=$(( $(date +%s) - start ))
if [ $rc -eq 0 ]; then
  printf '%s %s %s %ss\n' "$head" "$(printf '%s' "$cmd" | tr ' ' '_')" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$secs" > "$gitdir/foundry-local-green"
  echo "foundry-test: GREEN for $head in ${secs}s — push and PR are now admitted." >&2
else
  rm -f "$gitdir/foundry-local-green"
  echo "foundry-test: FAILED (rc=$rc) in ${secs}s — fix locally; push stays refused." >&2
fi
exit $rc
