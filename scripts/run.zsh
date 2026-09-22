#!/usr/bin/env zsh
# Alias for the full pipeline stage: ./scripts/run.zsh <ustaz_id>
set -euo pipefail

ROOT="${0:A:h:h}"
exec "$ROOT/archive.zsh" run "$@"
