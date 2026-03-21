#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

OPENCLAW_AGENTS="${OPENCLAW_AGENTS:-$HOME/.openclaw/workspace/agents}"

declare -A SKILLS=(
  [athena-ledger]="athena"
  [hestia-ledger]="hestia"
)

usage() {
  echo "Usage: $0 <push|pull>"
  echo ""
  echo "  push  Copy skill definitions from this repo into the openclaw agent directories"
  echo "  pull  Copy skill definitions from the openclaw agent directories back into this repo"
  exit 1
}

cmd_push() {
  for skill in "${!SKILLS[@]}"; do
    agent="${SKILLS[$skill]}"
    src="$REPO_ROOT/skills/$skill/"
    dst="$OPENCLAW_AGENTS/$agent/skills/$skill/"
    mkdir -p "$dst"
    rsync -a --delete "$src" "$dst"
    echo "push: $skill -> $dst"
  done
}

cmd_pull() {
  for skill in "${!SKILLS[@]}"; do
    agent="${SKILLS[$skill]}"
    src="$OPENCLAW_AGENTS/$agent/skills/$skill/"
    dst="$REPO_ROOT/skills/$skill/"
    if [[ ! -d "$src" ]]; then
      echo "pull: $src not found, skipping $skill" >&2
      continue
    fi
    rsync -a --delete "$src" "$dst"
    echo "pull: $skill <- $src"
  done
}

[[ $# -eq 1 ]] || usage

case "$1" in
  push) cmd_push ;;
  pull) cmd_pull ;;
  *) usage ;;
esac
