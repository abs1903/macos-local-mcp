#!/bin/bash
# User LaunchAgent entry point, not a root LaunchDaemon.
# Point the LaunchAgent plist's ProgramArguments at this script's absolute path.
# Use KeepAlive.SuccessfulExit=false; do not unconditionally restart an already-running instance.
set -euo pipefail
umask 077
PROJECT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT"
PYTHON="$PWD/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Run ./Setup.command first." >&2
  exit 1
fi
if [ ! -f .local/connection.json ]; then
  echo "Run ./Configure.command first." >&2
  exit 1
fi

# Skip an existing verified instance. This is an identity check, not a startup mutex.
if [ -f .local/running.json ] && "$PYTHON" -c '
import json, os, psutil
try:
    r = json.load(open(".local/running.json", encoding="utf-8"))
    p = psutil.Process(int(r["pid"]))
    ok = (abs(float(p.create_time()) - float(r["create_time"])) < 0.01
          and os.path.realpath(p.exe()) == os.path.realpath(r["executable"]))
except (OSError, ValueError, KeyError, psutil.Error):
    ok = False
raise SystemExit(0 if ok else 1)
'; then
  exit 0
fi
rm -f .local/running.json

TUNNEL="$(find .local/tools -type f -name tunnel-client -perm -111 2>/dev/null | sort | tail -n 1)"
if [ -z "$TUNNEL" ]; then
  echo "tunnel-client missing; run Setup.command" >&2
  exit 1
fi
TUNNEL_ID="$("$PYTHON" -c 'import json; print(json.load(open(".local/connection.json"))["tunnel_id"])')"
RUNTIME_KEY="$(security find-generic-password -s macos-local-mcp-runtime -a "$USER" -w)"

find .local -maxdepth 1 -name 'health-*.url' -type f -delete 2>/dev/null || true
for log in .local/tunnel.stdout.log .local/tunnel.stderr.log; do
  if [ -f "$log" ] && [ "$(stat -f %z "$log" 2>/dev/null || echo 0)" -gt 10485760 ]; then
    mv "$log" "$log.1"
  fi
done
HEALTH_FILE="$PWD/.local/health-$(date +%s)-$$.url"
export CONTROL_PLANE_API_KEY="$RUNTIME_KEY"
export CONTROL_PLANE_TUNNEL_ID="$TUNNEL_ID"
export MCP_COMMAND="\"$PYTHON\" -m macos_local_mcp"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export MACOS_LOCAL_MCP_STATE="$PWD/.local"
unset RUNTIME_KEY

# Record the intended executable atomically before exec, without a timed background writer.
exec "$PYTHON" -m macos_local_mcp.launchd "$TUNNEL" "$HEALTH_FILE" \
  >>.local/tunnel.stdout.log 2>>.local/tunnel.stderr.log
