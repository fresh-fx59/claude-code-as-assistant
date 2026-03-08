#!/usr/bin/env bash
set -euo pipefail

AVD_NAME="${AVD_NAME:-mobile_api35}"
ANDROID_EMULATOR_MEMORY_MB="${ANDROID_EMULATOR_MEMORY_MB:-4096}"
ANDROID_EMULATOR_SERIAL="${ANDROID_EMULATOR_SERIAL:-emulator-5554}"
ANDROID_EMULATOR_HEADLESS="${ANDROID_EMULATOR_HEADLESS:-1}"

ARGS=("@${AVD_NAME}" -no-boot-anim -gpu swiftshader_indirect -memory "${ANDROID_EMULATOR_MEMORY_MB}")
if [[ "${ANDROID_EMULATOR_HEADLESS}" == "1" ]]; then
  ARGS+=(-no-window -no-audio)
fi

echo "==> Starting emulator ${AVD_NAME}"
nohup emulator "${ARGS[@]}" > /tmp/emulator-${AVD_NAME}.log 2>&1 &

echo "==> Waiting for device"
adb wait-for-device

echo "==> Waiting for Android boot completion"
for _ in $(seq 1 120); do
  BOOTED="$(adb -s "${ANDROID_EMULATOR_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
  if [[ "${BOOTED}" == "1" ]]; then
    echo "Emulator is booted (${ANDROID_EMULATOR_SERIAL})."
    exit 0
  fi
  sleep 2
done

echo "Error: emulator did not finish booting in time." >&2
exit 1
