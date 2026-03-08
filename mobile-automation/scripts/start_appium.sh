#!/usr/bin/env bash
set -euo pipefail

APPIUM_HOST="${APPIUM_HOST:-127.0.0.1}"
APPIUM_PORT="${APPIUM_PORT:-4723}"
APPIUM_LOG_FILE="${APPIUM_LOG_FILE:-/tmp/appium.log}"

nohup appium --address "${APPIUM_HOST}" --port "${APPIUM_PORT}" > "${APPIUM_LOG_FILE}" 2>&1 &

echo "Appium started at http://${APPIUM_HOST}:${APPIUM_PORT} (log: ${APPIUM_LOG_FILE})"
