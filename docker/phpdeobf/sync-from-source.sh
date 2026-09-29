#!/usr/bin/env bash
# Refresh the vendored PHPDeobfuscator engine from ~/WORK/PHPDeobfuscator/.
#
#   bash docker/phpdeobf/sync-from-source.sh [--allow-dirty]
#   PHPDEOBF_SRC=/path/to/PHPDeobfuscator bash docker/phpdeobf/sync-from-source.sh
#
# Sidecar-owned files are preserved (see the exclude list). The source repo's
# untracked research artefacts (out/, report/, samples/, docs/, stray
# tarballs) and its composer.lock are never copied: the fork keeps its own
# composer.lock so the image build is reproducible from the fork alone.
# bin/ is mostly ad-hoc research scripts and is excluded, EXCEPT bin/lib/,
# which the corpus runner (test.php) requires at runtime and so must be
# vendored into the image.
# Writes VERSION with the source commit so the sidecar can report it.
set -euo pipefail

SRC="${PHPDEOBF_SRC:-$HOME/WORK/PHPDeobfuscator}"
DST="$(cd "$(dirname "$0")" && pwd)"
ALLOW_DIRTY=0
[[ "${1:-}" == "--allow-dirty" ]] && ALLOW_DIRTY=1

if [[ ! -d "$SRC/.git" ]]; then
  echo "source not found or not a git checkout: $SRC" >&2
  echo "set PHPDEOBF_SRC=/path/to/PHPDeobfuscator to override" >&2
  exit 1
fi

DIRTY_COUNT="$(git -C "$SRC" status --porcelain --untracked-files=no | wc -l | tr -d ' ')"
if [[ "$DIRTY_COUNT" != "0" && "$ALLOW_DIRTY" != "1" ]]; then
  echo "source tree has $DIRTY_COUNT modified tracked file(s); commit them or pass --allow-dirty" >&2
  exit 1
fi
[[ -f "$SRC/src/Pipeline.php" ]] || { echo "source lacks src/Pipeline.php (PHPDeobfuscator PR #17 not merged/checked out?)" >&2; exit 1; }
COMMIT="$(git -C "$SRC" rev-parse --short=12 HEAD)"

rsync -a --delete \
  --include='bin/' --include='bin/lib/' --include='bin/lib/**' \
  --exclude='.git' --exclude='.worktrees' --exclude='.DS_Store' \
  --exclude='vendor' --exclude='composer.lock' \
  --exclude='samples' --exclude='docs' --exclude='bin/*' --exclude='out' --exclude='report' \
  --exclude='mago-*.tar.gz' \
  --exclude='server.php' --exclude='Dockerfile.server' \
  --exclude='sync-from-source.sh' --exclude='sync-evalhook-from-source.sh' \
  --exclude='evalhook' --exclude='VERSION' \
  --exclude='tests/server_test.php' --exclude='.gitignore' --exclude='.dockerignore' \
  --exclude='CLAUDE.md' --exclude='.htaccess' --exclude='Dockerfile' \
  "$SRC/" "$DST/"

{
  echo "$COMMIT"
  echo "source=$SRC"
  echo "dirty=$([[ "$DIRTY_COUNT" == "0" ]] && echo no || echo yes)"
  echo "synced=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$DST/VERSION"

echo "synced PHPDeobfuscator $COMMIT from $SRC to $DST"
echo "review with: git diff --stat -- $DST"
