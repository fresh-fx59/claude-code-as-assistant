#!/usr/bin/env bash
set -euo pipefail

if ! command -v node >/dev/null 2>&1; then
  echo "==> Installing Node.js LTS"
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "==> Node version"
node --version
npm --version

echo "==> Installing Appium 2 + UiAutomator2"
npm install -g appium
appium driver install uiautomator2
appium driver doctor uiautomator2 || true

echo "Appium install complete."
echo "Start with: appium --address 127.0.0.1 --port 4723"
