#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "Error: run as root or install sudo." >&2
    exit 1
  fi
else
  SUDO=""
fi

if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  OS_ID="${ID:-}"
else
  echo "Error: /etc/os-release not found." >&2
  exit 1
fi

case "${OS_ID}" in
  ubuntu|debian) ;;
  *)
    echo "Error: unsupported OS '${OS_ID}'. This script supports Ubuntu/Debian." >&2
    exit 1
    ;;
esac

PACKAGES=(
  openjdk-17-jdk
  unzip
  wget
  curl
  git
  jq
  cpu-checker
  qemu-kvm
  libvirt-daemon-system
  libvirt-clients
  bridge-utils
  libnss3
  libx11-6
  libx11-xcb1
  libxcb1
  libxcomposite1
  libxcursor1
  libxi6
  libxdamage1
  libxrandr2
  libgbm1
  libasound2
  libatk1.0-0
  libc6
  libcairo2
  libcups2
  libdbus-1-3
  libexpat1
  libfontconfig1
  libgcc-s1
  libglib2.0-0
  libgtk-3-0
  libnspr4
  libpango-1.0-0
  libstdc++6
  libxext6
  zlib1g
  adb
)

echo "==> Installing host dependencies"
${SUDO} apt-get update
${SUDO} apt-get install -y "${PACKAGES[@]}"

echo "==> CPU virtualization flags"
egrep -c '(vmx|svm)' /proc/cpuinfo || true

echo "==> /dev/kvm check"
if [[ -e /dev/kvm ]]; then
  ls -l /dev/kvm
else
  echo "Warning: /dev/kvm not found. Emulator will be much slower." >&2
fi

echo "==> kvm-ok"
if command -v kvm-ok >/dev/null 2>&1; then
  kvm-ok || true
fi

echo "Host install complete."
