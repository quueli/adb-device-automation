# adb-device-automation

![ci](https://github.com/quueli/adb-device-automation/actions/workflows/ci.yml/badge.svg)

small library for driving android phones over adb from a pc: dump the screen, find an element, tap it, type, wait for the next screen. same routine on N devices at once, a thread per device and a state file so a batch survives a restart.

i wrote it for a project that had to click through an app on a rack of usb phones. the app-specific stuff is removed, what stays is the engine.

uiautomator dump is slow (2-3s on cheap phones), so the last dump is cached for a few hundred ms and a step that asks "is the error dialog up? is the spinner still there?" doesnt redump every time. with uiautomator2 installed the dump comes from the on-device server and its about 10x faster.

## usage

    pip install -r requirements.txt
    python examples/settings_walk.py     # needs a device in `adb devices`

tests run without a device, they parse a static xml dump:

    pytest

## notes

- proxy sharing: scripts/socks_relay.py + examples/share_proxy.py push the host's proxy to the phones over adb reverse, handy when they have no wifi of their own
- tested on android 9-13 on physical devices. emulators mostly work but input timing is different
