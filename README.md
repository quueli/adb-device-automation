# adb-device-automation

![ci](https://github.com/quueli/adb-device-automation/actions/workflows/ci.yml/badge.svg)

small library for driving android phones over adb from a pc: dump the screen, find an element, tap it, type, wait for the next screen. same routine on N devices at once, a thread per device and a state file so a batch survives a restart.

    pip install -r requirements.txt
    python examples/settings_walk.py     # needs a device in `adb devices`
