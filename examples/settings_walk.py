"""Walks the settings app on every connected device: python examples/settings_walk.py --count 4"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_loader import config
from src.device_manager import DeviceManager
from src.logger import setup_logging

SETTINGS_PACKAGE = 'com.android.settings'


def settings_walk(worker):
    adb = worker.adb
    adb.force_stop(SETTINGS_PACKAGE)
    adb.launch_app(SETTINGS_PACKAGE, '.Settings')

    if not adb.wait_for_element(text='Settings', timeout=config.ui_element_wait_timeout):
        return False, 'NO_SETTINGS_SCREEN'

    if not adb.safe_tap(text='About'):
        return False, 'ABOUT_NOT_FOUND'

    path = os.path.join('screenshots', f"{adb.device_id}_{int(time.time())}.png")
    if not adb.take_screenshot(path):
        return False, 'SCREENSHOT_FAILED'
    adb.back_button()
    time.sleep(config.screen_transition_delay)
    return True, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=3)
    args = ap.parse_args()

    log_file = setup_logging()
    print(f"log: {log_file}")

    selected = [d.device_id for d in DeviceManager.get_connected_devices() if d.is_available]
    if not selected:
        print("no devices")
        return 1

    manager = DeviceManager(task=settings_walk)
    if not manager.start_parallel(selected, total_tasks=args.count):
        return 1
    manager.wait_for_completion()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
