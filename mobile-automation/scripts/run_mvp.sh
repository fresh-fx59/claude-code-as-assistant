#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <profile-env-file>" >&2
  echo "Example: $0 mobile-automation/profiles/ozon.env" >&2
  exit 1
fi

PROFILE="$1"
if [[ ! -f "${PROFILE}" ]]; then
  echo "Error: profile '${PROFILE}' not found." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${PROFILE}"
# shellcheck disable=SC1091
source mobile-automation/env/android-sdk.env 2>/dev/null || true

./mobile-automation/scripts/create_avd.sh
./mobile-automation/scripts/start_emulator.sh
./mobile-automation/scripts/install_app.sh
./mobile-automation/scripts/smoke_adb.sh
./mobile-automation/scripts/start_appium.sh

python3 -m venv .venv-mobile-automation
source .venv-mobile-automation/bin/activate
pip install -q --upgrade pip
pip install -q -r mobile-automation/requirements.txt

./mobile-automation/scripts/smoke_appium.py

echo "MVP run complete."
