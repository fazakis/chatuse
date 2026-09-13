#!/bin/sh
set -eu
chatuse_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
chatuse_codex=${CHATUSE_CODEX_BIN:-/Applications/ChatGPT.app/Contents/Resources/codex}
if ! [ -x "$chatuse_codex" ]; then chatuse_codex=$(command -v codex); fi
if "$chatuse_codex" mcp get chatuse >/dev/null 2>&1; then
  printf 'A chatuse MCP registration already exists. Inspect it with: codex mcp get chatuse\n'
  exit 0
fi
"$chatuse_codex" mcp add chatuse -- "$chatuse_root/chatuse" mcp
