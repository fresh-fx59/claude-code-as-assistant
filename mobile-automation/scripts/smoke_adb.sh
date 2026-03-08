#!/usr/bin/env bash
set -euo pipefail

APP_PACKAGE="${APP_PACKAGE:-}"
ANDROID_EMULATOR_SERIAL="${ANDROID_EMULATOR_SERIAL:-emulator-5554}"
OUT_DIR="${OUT_DIR:-mobile-automation/artifacts}"

if [[ -z "${APP_PACKAGE}" ]]; then
  echo "Error: APP_PACKAGE is required." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
STAMP="$(date +%Y%m%d-%H%M%S)"

adb -s "${ANDROID_EMULATOR_SERIAL}" wait-for-device
adb -s "${ANDROID_EMULATOR_SERIAL}" shell input keyevent KEYCODE_HOME
adb -s "${ANDROID_EMULATOR_SERIAL}" shell monkey -p "${APP_PACKAGE}" 1
sleep 3
adb -s "${ANDROID_EMULATOR_SERIAL}" shell screencap -p "/sdcard/${APP_PACKAGE}-${STAMP}.png"
adb -s "${ANDROID_EMULATOR_SERIAL}" pull "/sdcard/${APP_PACKAGE}-${STAMP}.png" "${OUT_DIR}/"

echo "ADB smoke test complete: ${OUT_DIR}/${APP_PACKAGE}-${STAMP}.png"
