#!/usr/bin/env bash
set -euo pipefail

AGENT="${1:?agent workspace name required}"
PROMPT="$(cat)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

case "$AGENT" in
  claude)
    exec claude \
      --model claude-opus-4-6 \
      --effort max \
      --permission-mode bypassPermissions \
      -p "$PROMPT"
    ;;
  codex)
    exec codex exec \
      --model gpt-5.4 \
      -c 'model_reasoning_effort="xhigh"' \
      --dangerously-bypass-approvals-and-sandbox \
      --cd "$REPO_DIR" \
      "$PROMPT"
    ;;
  *)
    echo "Unknown agent: $AGENT" >&2
    exit 1
    ;;
esac
