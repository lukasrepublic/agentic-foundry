#!/usr/bin/env bash
# foundry-work-isolation — thin seam over NATIVE worktree isolation (§5.4 Cluster-2 WRAP).
# Native `Agent isolation: worktree` + the WorktreeCreate/Remove hooks already CREATE +
# auto-clean (if unchanged) worker worktrees. What native does NOT do is the post-MERGE
# cleanup of a worktree whose branch has landed — that glue lives here.
#
#   foundry-work-isolation.sh cleanup <repo> <branch>   # remove the merged worktree + local branch
#   foundry-work-isolation.sh list                       # list linked worktrees
#
# MULTI-REPO (UL-0022, AC-MRDISPATCH-6): a worker worktree may be a LINKED worktree of a
# PRODUCT repo (resolved from .claude/foundry-project.json), not the workspace. Teardown is
# therefore REPO-AWARE — it delegates to `foundry-wt rm`, which runs `git -C <product-path>`
# and NEVER force-removes a dirty / unmerged worktree (vs. the old workspace-rooted
# `git -C $ROOT … --force`, which both targeted the wrong repo and could nuke uncommitted
# work). The single shared worktree layout `<workspace>/.worktrees/<key>/<agent>/<task>` is
# used by both the create side (foundry-wt claim) and here — no create/cleanup divergence.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WTBIN="$HERE/../scripts/foundry-wt"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

_cleanup() {  # <repo-key> <branch=<agent>/<task>>
  local repo="$1" branch="$2"
  local agent task
  if [ "${branch%/*}" != "$branch" ]; then agent="${branch%%/*}"; task="${branch#*/}"; else agent=""; task="$branch"; fi
  local wt_path="$ROOT/.worktrees/$repo/$branch"
  # Harvest the worker's learnings sidecar BEFORE removal (fail-open).
  [ -d "$wt_path" ] && "$HERE/foundry-harvest-learnings.sh" harvest "$wt_path" 2>/dev/null || true
  # Repo-aware path: delegate to foundry-wt (resolves the product repo, never force).
  if [ -n "$agent" ] && [ -x "$WTBIN" ] && CLAUDE_PROJECT_DIR="$ROOT" "$WTBIN" resolve "$repo" >/dev/null 2>&1; then
    if CLAUDE_PROJECT_DIR="$ROOT" "$WTBIN" rm "$repo" "$task" --as="$agent"; then
      echo "removed worktree (repo-aware) $repo/$branch"
    else
      echo "foundry-work-isolation: repo-aware teardown declined (dirty/unmerged, not forced) $repo/$branch" >&2
    fi
    return 0
  fi
  # Legacy / workspace fallback (single-repo): non-force, AC-6-honoring.
  if [ -d "$wt_path" ]; then
    git -C "$ROOT" worktree remove "$wt_path" 2>/dev/null \
      && echo "removed worktree $wt_path" \
      || echo "foundry-work-isolation: worktree remove declined (dirty/unmerged, not forced) $wt_path" >&2
  fi
  git -C "$ROOT" branch -d "$branch" 2>/dev/null && echo "deleted local branch $branch" || true
  git -C "$ROOT" worktree prune 2>/dev/null || true
}

_selftest() {
  local tmp ok=1; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  local ws="$tmp/ws"; mkdir -p "$ws/.claude"
  mkdir -p "$ws/app"; ( cd "$ws/app" && git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init )
  printf '{ "schema_version":1, "repos": { "app": { "path": "app" } } }\n' > "$ws/.claude/foundry-project.json"

  # claim a clean worktree of product repo "app", then cleanup -> removed AGAINST the product repo
  CLAUDE_PROJECT_DIR="$ws" "$WTBIN" claim app t-clean --as=platform-engineer >/dev/null 2>&1
  local wt="$ws/.worktrees/app/platform-engineer/t-clean"
  local r1=1
  [ -d "$wt" ] || { echo "  AC-6: claim did not create worktree" >&2; r1=0; }
  CLAUDE_PROJECT_DIR="$ws" bash "$0" cleanup app platform-engineer/t-clean >/dev/null 2>&1
  [ ! -d "$wt" ] || { echo "  AC-6: clean+merged worktree not removed" >&2; r1=0; }
  # the branch must be gone from the PRODUCT repo (proves git -C <product>, not workspace)
  if git -C "$ws/app" show-ref --verify --quiet "refs/heads/platform-engineer/t-clean"; then echo "  AC-6: product branch not deleted (wrong -C repo?)" >&2; r1=0; fi

  # never-force: a DIRTY worktree must NOT be removed
  CLAUDE_PROJECT_DIR="$ws" "$WTBIN" claim app t-dirty --as=platform-engineer >/dev/null 2>&1
  local wd="$ws/.worktrees/app/platform-engineer/t-dirty"
  echo "uncommitted" > "$wd/dirty.txt"
  CLAUDE_PROJECT_DIR="$ws" bash "$0" cleanup app platform-engineer/t-dirty >/dev/null 2>&1
  [ -d "$wd" ] || { echo "  AC-6: dirty worktree was force-removed (must never force)" >&2; r1=0; }

  if [ "$r1" -eq 1 ]; then echo "AC-MRDISPATCH-6 repo-aware-teardown-shared-layout: PASS"; else echo "AC-MRDISPATCH-6 repo-aware-teardown-shared-layout: FAIL"; ok=0; fi
  [ "$ok" -eq 1 ] && echo "FOUNDRY-WORK-ISOLATION-SELFTEST-GREEN" || { echo "FOUNDRY-WORK-ISOLATION-SELFTEST-RED"; return 1; }
}

ACTION="${1:-}"
case "$ACTION" in
  --selftest) _selftest ;;
  cleanup) _cleanup "${2:?usage: cleanup <repo> <branch>}" "${3:?usage: cleanup <repo> <branch>}" ;;
  list) git -C "$ROOT" worktree list ;;
  *) echo "usage: $0 cleanup <repo> <branch> | list | --selftest" >&2; exit 2 ;;
esac
