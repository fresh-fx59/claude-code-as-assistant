# Server-side Mobile App Automation: Generic MVP

This implementation translates the Ozon MVP plan into a generic setup that can automate other Android apps with minimal changes.

## What is included

- `mobile-automation/scripts/install_host.sh`: Linux dependencies + KVM checks
- `mobile-automation/scripts/install_android_sdk.sh`: Android command-line SDK + packages
- `mobile-automation/scripts/create_avd.sh`: AVD provisioning
- `mobile-automation/scripts/start_emulator.sh`: headless emulator startup + boot wait
- `mobile-automation/scripts/install_appium.sh`: Node.js + Appium 2 + UiAutomator2
- `mobile-automation/scripts/install_app.sh`: APK install or package verification
- `mobile-automation/scripts/smoke_adb.sh`: launch app + screenshot artifact
- `mobile-automation/scripts/start_appium.sh`: Appium daemon launcher
- `mobile-automation/scripts/smoke_appium.py`: generic Appium session validation
- `mobile-automation/scripts/run_mvp.sh`: end-to-end MVP smoke flow
- `mobile-automation/profiles/template.env`: reusable app profile template
- `mobile-automation/profiles/ozon.env`: Ozon profile example

## Quickstart

```bash
cd /home/claude-developer/iron-lady-assistant
./mobile-automation/scripts/install_host.sh
./mobile-automation/scripts/install_android_sdk.sh
source mobile-automation/env/android-sdk.env
./mobile-automation/scripts/install_appium.sh
./mobile-automation/scripts/run_mvp.sh mobile-automation/profiles/ozon.env
```

## Reuse for any app

1. Copy profile:
```bash
cp mobile-automation/profiles/template.env mobile-automation/profiles/<app>.env
```
2. Set:
- `APP_PACKAGE`
- `APP_ACTIVITY` (if known)
- optional `APK_PATH`
3. Run:
```bash
./mobile-automation/scripts/run_mvp.sh mobile-automation/profiles/<app>.env
```

## Recommended production pattern

1. Keep emulator as long-lived service.
2. Keep Appium as separate service.
3. Keep orchestrator/business logic separate from infra scripts.
4. Require explicit approval before final checkout/payment actions.
