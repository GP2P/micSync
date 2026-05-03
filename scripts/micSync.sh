#!/bin/zsh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export MICSYNC_HOME="${MICSYNC_HOME:-$HOME/Downloads/micSync}"
export PYTHONPATH="$SERVICE_ROOT/src"

for arg in "$@"; do
  case "$arg" in
    --stop|-h|--help)
      exec python3 -m micsync.cli "$@"
      ;;
  esac
done

exec python3 -m micsync.cli --detach "$@"
