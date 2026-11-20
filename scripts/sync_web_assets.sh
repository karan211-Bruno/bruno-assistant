#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/apps/desktop/web" "$ROOT/apps/mobile/web"

rsync -a --delete "$ROOT/static/" "$ROOT/apps/desktop/web/"
rsync -a --delete "$ROOT/static/" "$ROOT/apps/mobile/web/"

echo "Synced static/ -> apps/desktop/web/ and apps/mobile/web/"
