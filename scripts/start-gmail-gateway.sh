#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${GMAIL_GATEWAY_HOST:-127.0.0.1}"
PORT="${GMAIL_GATEWAY_PORT:-8791}"
DB_PATH="${GMAIL_GATEWAY_DB_PATH:-memory/gmail_gateway.db}"
LOG="${GMAIL_GATEWAY_LOG_PATH:-${REPO_DIR}/memory/gmail_gateway.log}"
PIDFILE="${GMAIL_GATEWAY_PIDFILE_PATH:-${REPO_DIR}/memory/gmail_gateway.pid}"
PYTHON_BIN="${GMAIL_GATEWAY_PYTHON_BIN:-${REPO_DIR}/venv/bin/python}"

cd "$REPO_DIR"

mkdir -p "$(dirname "$LOG")" "$(dirname "$PIDFILE")"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  exit 0
fi

nohup "$PYTHON_BIN" -m src.gmail_gateway.http \
  --host "$HOST" --port "$PORT" --db-path "$DB_PATH" \
  >>"$LOG" 2>&1 &

echo $! > "$PIDFILE"
