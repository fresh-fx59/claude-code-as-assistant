#!/usr/bin/env bash
set -euo pipefail

AVD_NAME="${AVD_NAME:-mobile_api35}"
ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-35}"
ANDROID_SYSTEM_IMAGE_FLAVOR="${ANDROID_SYSTEM_IMAGE_FLAVOR:-google_apis}"
ANDROID_ARCH="${ANDROID_ARCH:-x86_64}"
ANDROID_DEVICE_PROFILE="${ANDROID_DEVICE_PROFILE:-pixel_7}"

IMAGE="system-images;android-${ANDROID_API_LEVEL};${ANDROID_SYSTEM_IMAGE_FLAVOR};${ANDROID_ARCH}"

echo "==> Creating AVD ${AVD_NAME} (${IMAGE})"
if emulator -list-avds | grep -Fxq "${AVD_NAME}"; then
  echo "AVD '${AVD_NAME}' already exists; skipping create."
  exit 0
fi

echo "no" | avdmanager create avd -n "${AVD_NAME}" -k "${IMAGE}" -d "${ANDROID_DEVICE_PROFILE}"

echo "AVD created."
emulator -list-avds
