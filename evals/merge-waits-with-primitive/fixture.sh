#!/usr/bin/env bash
# fixture.sh — runs OUTSIDE the agent's sandbox, as you, only with `--scaffold` (case.yaml's
# context.scaffold_script). Writes a stubbed `gh` into ./bin, on the empty workspace's own path,
# whose checks report green and whose view/merge calls succeed on the first query — a stand-in
# for a real PR that has already gone green, so the case measures whether Claude reaches for
# `foundry-merge-when-green.py` rather than a hand-rolled sleep-then-poll loop.
set -euo pipefail

mkdir -p bin

cat > bin/gh <<'GH_STUB'
#!/usr/bin/env bash
set -u
case "${1:-}" in
  pr)
    case "${2:-}" in
      checks)
        printf 'build\tpass\t12s\thttps://example.invalid/checks/build\n'
        exit 0
        ;;
      view)
        if printf '%s\n' "$*" | grep -q -- '--json mergeStateStatus'; then
          printf '{"mergeStateStatus":"CLEAN"}\n'
        elif printf '%s\n' "$*" | grep -q -- '--json mergeCommit'; then
          printf '{"mergeCommit":{"oid":"deadbeefcafe"}}\n'
        else
          printf '{}\n'
        fi
        exit 0
        ;;
      merge)
        printf 'Merged pull request via squash\n'
        exit 0
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac
GH_STUB
chmod +x bin/gh
