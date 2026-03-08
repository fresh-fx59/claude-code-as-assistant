#!/usr/bin/env bash
set -euo pipefail

APP_PACKAGE="${APP_PACKAGE:-}"
APK_PATH="${APK_PATH:-}"
ANDROID_EMULATOR_SERIAL="${ANDROID_EMULATOR_SERIAL:-emulator-5554}"

if [[ -n "${APK_PATH}" ]]; then
  if [[ ! -f "${APK_PATH}" ]]; then
    echo "Error: APK not found at '${APK_PATH}'." >&2
    exit 1
  fi
  echo "==> Installing APK ${APK_PATH}"
  adb -s "${ANDROID_EMULATOR_SERIAL}" install -r "${APK_PATH}"
fi

if [[ -n "${APP_PACKAGE}" ]]; then
  echo "==> Verifying package ${APP_PACKAGE}"
  if ! adb -s "${ANDROID_EMULATOR_SERIAL}" shell pm list packages | tr -d '\r' | grep -q "package:${APP_PACKAGE}"; then
    echo "Error: package '${APP_PACKAGE}' is not installed." >&2
    exit 1
  fi
fi

echo "App install/verify complete."
