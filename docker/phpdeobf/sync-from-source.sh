#!/usr/bin/env bash
# Refresh the vendored PHPDeobfuscator source from ~/WORK/PHPDeobfuscator/.
#
# Run from repo root:  bash docker/phpdeobf/sync-from-source.sh
# Run from anywhere:   <repo>/docker/phpdeobf/sync-from-source.sh
#
# After running, review the diff and commit.

set -euo pipefail

SRC="${PHPDEOBF_SRC:-$HOME/WORK/PHPDeobfuscator}"
DST="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -d "$SRC" ]]; then
  echo "source not found: $SRC" >&2
  echo "set PHPDEOBF_SRC=/path/to/PHPDeobfuscator to override" >&2
  exit 1
fi

# Preserve sidecar-only files (server.php, Dockerfile.server, this script,
# tests/, .gitignore) by listing them as excludes — rsync --delete would
# otherwise wipe them.
#
# Also exclude upstream's CLAUDE.md / .htaccess / Dockerfile — these were
# removed in code review (see plan Task 1) because they conflict with or
# confuse the fork's setup. Re-syncing without these excludes would bring
# them back.
rsync -a --delete \
  --exclude='.git' --exclude='vendor' --exclude='samples' --exclude='docs' \
  --exclude='server.php' --exclude='Dockerfile.server' \
  --exclude='sync-from-source.sh' --exclude='tests/server_test.php' \
  --exclude='.gitignore' \
  --exclude='CLAUDE.md' --exclude='.htaccess' --exclude='Dockerfile' \
  "$SRC/" "$DST/"

echo "synced from $SRC to $DST"
echo "review with: git -C $(git -C "$DST" rev-parse --show-toplevel) diff --stat"
