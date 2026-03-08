# Mobile App Automation MVP (Generic)

This folder provides a reusable server-side stack for Android app automation with:

- Android Emulator (AVD)
- Appium 2 + UiAutomator2
- app profile files (`profiles/*.env`)
- ADB and Appium smoke tests

The setup is app-agnostic. Ozon is included as an example profile only.

## Hard requirement

This setup requires a **KVM-capable VPS/host** for stable Android emulator automation.
Without KVM (nested virtualization), emulator boot is slow/unstable and not suitable for reliable Appium flows.
If KVM is unavailable, use an external browser/device farm instead of local emulator execution.

## 1) Install host prerequisites (Ubuntu/Debian)

```bash
./mobile-automation/scripts/install_host.sh
```

## 2) Install Android SDK + emulator toolchain

```bash
./mobile-automation/scripts/install_android_sdk.sh
source mobile-automation/env/android-sdk.env
```

## 3) Install Appium

```bash
./mobile-automation/scripts/install_appium.sh
```

## 4) Pick an app profile

- Generic template: `mobile-automation/profiles/template.env`
- Ozon example: `mobile-automation/profiles/ozon.env`

To create your own app profile:

```bash
cp mobile-automation/profiles/template.env mobile-automation/profiles/myapp.env
# edit APP_PACKAGE, APP_ACTIVITY, AVD_NAME, Android/API fields
```

## 5) Run the MVP flow

```bash
source mobile-automation/profiles/ozon.env
source mobile-automation/env/android-sdk.env
./mobile-automation/scripts/run_mvp.sh mobile-automation/profiles/ozon.env
```

This will:

1. Create AVD if missing
2. Start emulator and wait for full boot
3. Install/verify target app
4. Run ADB smoke test and pull screenshot artifact
5. Start Appium
6. Run Appium smoke test (session + package check)

## 6) Manual app prep (recommended)

For reliable MVP behavior:

1. Open app manually in emulator once
2. Complete onboarding/login/permissions
3. Validate profile/cart/checkout entry points
4. Keep `NO_RESET=true` to preserve session

## 7) Generic knobs for other apps

Set these in profile:

- `APP_PACKAGE` (required)
- `APP_ACTIVITY` (optional, but recommended)
- `APK_PATH` (optional sideload)
- `ANDROID_API_LEVEL`, `AVD_NAME`, `ANDROID_DEVICE_PROFILE`
- `ANDROID_EMULATOR_HEADLESS` for server mode

## Notes

- If `/dev/kvm` is unavailable, emulator may be unstable/slow.
- Anti-bot and OTP/payment flows can still require manual takeover.
- Keep final purchase/commit actions behind explicit user confirmation.
