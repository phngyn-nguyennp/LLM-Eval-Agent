#!/usr/bin/env bash
# Cron entry point for `eval-agent auto`: bring a dedicated checkout to the head of a product branch, then run auto.
# Usage: cron-auto.sh <product-repo> <branch> <checkout-dir> <registry-repo> <feature> [auto options...]
# Needs EVAL_AGENT_HOME; set EVAL_AGENT_USER to a runner identity (e.g. cron-auto) so the audit log shows who ran it.
set -euo pipefail
umask 077  # reports, logs and the checkout stay owner-only (security check)
src=$1 branch=$2 checkout=$3 repo=$4 feature=$5
shift 5
here=$(cd "$(dirname "$0")/.." && pwd)

exec 9>"${EVAL_AGENT_HOME:?}/.auto-${repo}-${feature}.lock"
flock -n 9 || { echo "$(date -u +%FT%TZ) previous auto run still going, skipping"; exit 0; }

echo "=== $(date -u +%FT%TZ) auto $repo/$feature ($branch)"
git -C "$src" fetch -q origin "$branch"
if [ ! -e "$checkout/.git" ]; then
  mkdir -p "$(dirname "$checkout")"
  git -C "$src" worktree add -q --detach "$checkout" "origin/$branch"
else
  git -C "$checkout" checkout -q --detach "origin/$branch"
fi
cd "$EVAL_AGENT_HOME"
exec "${PYTHON:-python3}" "$here/cli.py" auto --repo "$repo" --feature "$feature" --repo-path "$checkout" "$@"
