#!/bin/bash
# Local-only content restoration; not an MCP tool.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="$PWD/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Run ./Setup.command first." >&2
  exit 1
fi
export MACOS_LOCAL_MCP_STATE="$PWD/.local"
# Python source is a module, leaving stdin available for overwrite confirmation.
exec "$PYTHON" -m macos_local_mcp.restore "$@"
