#!/bin/zsh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${NEXUS_DATA_ROOT:-}" ] && [ -f "$HOME/.config/nexus/env.sh" ]; then
  . "$HOME/.config/nexus/env.sh"
fi

unset NEXUS_DEPLOY_ROOT
export NEXUS_DATA_ROOT="${NEXUS_DATA_ROOT:-$SERVICE_ROOT/data}"
export PYTHONPATH="$SERVICE_ROOT/src"

for arg in "$@"; do
  case "$arg" in
    --stop|-h|--help)
      exec python3 -m micsync.cli "$@"
      ;;
  esac
done

exec python3 -m micsync.cli --detach "$@"
