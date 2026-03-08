#!/usr/bin/env bash
set -euo pipefail

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/android-sdk}"
ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
ANDROID_AVD_HOME="${ANDROID_AVD_HOME:-$HOME/.android/avd}"
ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-35}"
ANDROID_SYSTEM_IMAGE_FLAVOR="${ANDROID_SYSTEM_IMAGE_FLAVOR:-google_apis}"
ANDROID_ARCH="${ANDROID_ARCH:-x86_64}"
ANDROID_CMDLINE_TOOLS_URL="${ANDROID_CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip}"

mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools" "${ANDROID_AVD_HOME}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

ZIP_PATH="${TMP_DIR}/cmdline-tools.zip"

echo "==> Downloading Android command-line tools"
curl -fL "${ANDROID_CMDLINE_TOOLS_URL}" -o "${ZIP_PATH}"

rm -rf "${ANDROID_SDK_ROOT}/cmdline-tools/latest"
mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools/latest"
unzip -q "${ZIP_PATH}" -d "${TMP_DIR}/unzipped"

# Google zip unpacks to cmdline-tools/bin; we need .../latest/bin
if [[ -d "${TMP_DIR}/unzipped/cmdline-tools" ]]; then
  mv "${TMP_DIR}/unzipped/cmdline-tools"/* "${ANDROID_SDK_ROOT}/cmdline-tools/latest/"
else
  mv "${TMP_DIR}/unzipped"/* "${ANDROID_SDK_ROOT}/cmdline-tools/latest/"
fi

export ANDROID_HOME
export ANDROID_SDK_ROOT
export ANDROID_AVD_HOME
export PATH="${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${ANDROID_SDK_ROOT}/emulator:${PATH}"

mkdir -p mobile-automation/env
cat > mobile-automation/env/android-sdk.env <<ENV
export ANDROID_HOME="${ANDROID_HOME}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT}"
export ANDROID_AVD_HOME="${ANDROID_AVD_HOME}"
export PATH="${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${ANDROID_SDK_ROOT}/emulator:\$PATH"
ENV

echo "==> Accepting SDK licenses"
yes | sdkmanager --licenses >/dev/null

echo "==> Installing SDK packages"
sdkmanager --install \
  "platform-tools" \
  "emulator" \
  "platforms;android-${ANDROID_API_LEVEL}" \
  "system-images;android-${ANDROID_API_LEVEL};${ANDROID_SYSTEM_IMAGE_FLAVOR};${ANDROID_ARCH}"

echo "Android SDK install complete."
echo "Run: source mobile-automation/env/android-sdk.env"
