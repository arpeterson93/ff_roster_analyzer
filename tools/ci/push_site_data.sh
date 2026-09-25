#!/usr/bin/env bash
# Commits this run's own docs/data output onto the orphan site-data branch,
# overlaying ONLY the files this run actually produced onto whatever is
# already there - see update-scripts-implementation-plan.md Part 2.2. A
# file no one in this run produced is left as whatever the newest committed
# version already is; that's the whole merge strategy (no real merge logic
# needed since runs never touch overlapping files across stages... except
# a full/refresh run, which touches everything, by design - see the plan).
#
# Usage: push_site_data.sh <stage: full|refresh|live>
#
# Preconditions (both set up by the calling workflow, not this script):
#   - docs/data/  this run's full merged tree: the site-data baseline this
#     run started from, with its own stage's outputs already written on top
#     (engine.pipeline/engine.live only ever write the files their stage
#     produces, so nothing outside `patterns` below has changed).
#   - site-data/  an already-credentialed checkout of the site-data branch,
#     HEAD pointing at that branch (see .github/workflows/update.yml for how
#     a not-yet-existing branch gets bootstrapped as a fresh orphan before
#     this script ever runs).
set -euo pipefail

stage="${1:?usage: push_site_data.sh <full|refresh|live>}"

case "$stage" in
  full|refresh)
    patterns=("docs/data/leagues.json" "docs/data/sources.json" "docs/data/*/*.json")
    ;;
  live)
    patterns=("docs/data/*/schedule.json" "docs/data/*/standings.json" "docs/data/*/live.json")
    ;;
  *)
    echo "push_site_data.sh: unknown stage '$stage' (want full|refresh|live)" >&2
    exit 1
    ;;
esac

copy_own_output() {
  local pattern src dest
  for pattern in "${patterns[@]}"; do
    for src in $pattern; do
      [ -e "$src" ] || continue
      dest="site-data/${src#docs/data/}"
      mkdir -p "$(dirname "$dest")"
      cp "$src" "$dest"
    done
  done
}

commit_if_changed() {
  git -C site-data add -A
  if git -C site-data diff --cached --quiet; then
    return 1  # nothing to commit
  fi
  git -C site-data commit -q -m "update site data ($stage)"
  return 0
}

copy_own_output
if ! commit_if_changed; then
  echo "push_site_data.sh: no changes to commit"
  exit 0
fi

max_attempts=3
attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
  if git -C site-data push origin HEAD:site-data; then
    echo "push_site_data.sh: pushed on attempt $attempt"
    exit 0
  fi
  echo "push_site_data.sh: push rejected (attempt $attempt/$max_attempts) - rebasing onto the latest site-data"
  git -C site-data fetch origin site-data
  git -C site-data reset --hard origin/site-data
  copy_own_output
  if ! commit_if_changed; then
    echo "push_site_data.sh: no changes remain once rebased onto the latest site-data"
    exit 0
  fi
  attempt=$((attempt + 1))
done

echo "push_site_data.sh: failed to push site-data after $max_attempts attempts" >&2
exit 1
