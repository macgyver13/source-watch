#!/usr/bin/env bash
# Merge source-watch/main into each downstream watch repo and deploy it.
#
# Usage:
#   SOURCE_WATCH_REPOS="frost-watch silent-payments-watch post-quantum-watch" \
#     scripts/propagate_and_deploy.sh              # all repos
#   scripts/propagate_and_deploy.sh frost-watch     # just one
#   scripts/propagate_and_deploy.sh --dry-run       # print commands, don't run them
#   scripts/propagate_and_deploy.sh --force-deploy  # deploy even if merge was a no-op
#
# Set SOURCE_WATCH_REPOS (space-separated repo names, relative to REPOS_DIR)
# in your shell profile so the list doesn't need to live in this script.

set -euo pipefail

REPOS_DIR="$HOME/src"

if [[ -z "${SOURCE_WATCH_REPOS:-}" ]]; then
  echo "abort: set SOURCE_WATCH_REPOS to a space-separated list of repo names" >&2
  exit 1
fi
ALL_REPOS=($SOURCE_WATCH_REPOS)

dry_run=false
force_deploy=false
selected=()

for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    --force-deploy) force_deploy=true ;;
    *) selected+=("$arg") ;;
  esac
done

repos=("${selected[@]:-${ALL_REPOS[@]}}")

run() {
  echo "+ $*"
  if ! $dry_run; then
    "$@"
  fi
}

for repo in "${repos[@]}"; do
  path="$REPOS_DIR/$repo"
  echo "=== $repo ==="

  if [[ ! -d "$path" ]]; then
    echo "skip: $path does not exist" >&2
    exit 1
  fi

  cd "$path"

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "abort: $repo has uncommitted changes -- commit or stash them and rerun" >&2
    exit 1
  fi

  branch=$(git branch --show-current)
  if [[ "$branch" != "main" ]]; then
    run git checkout main
  fi

  before_sha=$(git rev-parse HEAD)
  run git fetch source-watch
  run git merge --no-edit source-watch/main
  run git push origin

  if $dry_run; then
    echo "+ npx wrangler deploy (if changes were merged, or --force-deploy)"
  elif $force_deploy || [[ "$before_sha" != "$(git rev-parse HEAD)" ]]; then
    run npx wrangler deploy
  else
    echo "no new commits merged -- skipping deploy (use --force-deploy to deploy anyway)"
  fi

  echo
done
