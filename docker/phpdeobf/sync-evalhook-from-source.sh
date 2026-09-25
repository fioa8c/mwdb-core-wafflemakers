#!/usr/bin/env bash
# Vendor the php-eval-hook extension sources (tracked files only) from
# ~/WORK/php-eval-hook/ into docker/phpdeobf/evalhook/ and record the commit.
#
#   bash docker/phpdeobf/sync-evalhook-from-source.sh
#   EVALHOOK_SRC=/path/to/php-eval-hook bash docker/phpdeobf/sync-evalhook-from-source.sh
set -euo pipefail

SRC="${EVALHOOK_SRC:-$HOME/WORK/php-eval-hook}"
DST="$(cd "$(dirname "$0")" && pwd)/evalhook"

if [[ ! -d "$SRC/.git" ]]; then
  echo "source not found or not a git checkout: $SRC" >&2
  exit 1
fi
for f in evalhook.c config.m4 LICENSE; do
  if ! git -C "$SRC" ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    echo "$f is not a tracked file in $SRC" >&2
    exit 1
  fi
done

mkdir -p "$DST"
for f in evalhook.c config.m4 LICENSE; do
  cp "$SRC/$f" "$DST/$f"
done
{
  echo "upstream=$(git -C "$SRC" remote get-url origin)"
  echo "commit=$(git -C "$SRC" rev-parse HEAD)"
  echo "synced=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$DST/SOURCE"

echo "vendored php-eval-hook $(git -C "$SRC" rev-parse --short HEAD) into $DST"
