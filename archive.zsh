#!/usr/bin/env zsh
# Ustaz Archive Manager CLI entrypoint.
# Usage: ./archive.zsh <command> [args]
set -euo pipefail

ROOT="${0:A:h}"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  print -u2 "error: python3 not found on PATH"
  exit 1
fi

exec python3 -m manager "$@"
