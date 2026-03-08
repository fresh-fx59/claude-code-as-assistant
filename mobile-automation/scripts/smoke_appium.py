#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from appium import webdriver
from appium.options.android import UiAutomator2Options


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return value or ""


appium_url = env("APPIUM_URL", "http://127.0.0.1:4723")
platform_name = env("ANDROID_PLATFORM_NAME", "Android")
device_name = env("ANDROID_DEVICE_NAME", "emulator-5554")
platform_version = env("ANDROID_PLATFORM_VERSION", "")
app_package = env("APP_PACKAGE", required=True)
app_activity = env("APP_ACTIVITY", "")
no_reset = env("NO_RESET", "true").lower() == "true"
new_command_timeout = int(env("NEW_COMMAND_TIMEOUT", "300"))

caps = {
    "platformName": platform_name,
    "appium:automationName": "UiAutomator2",
    "appium:deviceName": device_name,
    "appium:noReset": no_reset,
    "appium:newCommandTimeout": new_command_timeout,
    "appium:appPackage": app_package,
}

if platform_version:
    caps["appium:platformVersion"] = platform_version
if app_activity:
    caps["appium:appActivity"] = app_activity

options = UiAutomator2Options().load_capabilities(caps)
driver = webdriver.Remote(command_executor=appium_url, options=options)

try:
    current_package = driver.current_package
    print(f"Connected. Current package: {current_package}")
finally:
    driver.quit()
    print("Appium smoke test complete.")
