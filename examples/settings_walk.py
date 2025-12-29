"""Walks the settings app on every selected device: python examples/settings_walk.py --count 4"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_loader import config
from src.dashboard import display_live_progress
from src.device_manager import DeviceManager
from src.device_worker import DEVICE_LOST, STOPPED
from src.devices import get_connected_devices, interactive_device_selection
from src.logger import setup_logging
from src.screen_state import TIMEOUT, ScreenRule, wait_for_screen

SETTINGS_PACKAGE = 'com.android.settings'

# specific first: the about page also has the word "settings" somewhere in the tree
SETTINGS_SCREENS = [
    ScreenRule('ABOUT', any_of=('build number', 'android version', 'model number')),
    ScreenRule('SETTINGS', any_of=('search settings', 'network', 'connections', 'display')),
]


def settings_walk(worker):
    adb = worker.adb
    if worker.is_stopped():
        return False, STOPPED

    worker.set_stage('launch')
    adb.force_stop(SETTINGS_PACKAGE)
    adb.launch_app(SETTINGS_PACKAGE, '.Settings')
    state = wait_for_screen(adb, SETTINGS_SCREENS, timeout=config.ui_element_wait_timeout)
    if state == TIMEOUT:
        if adb.device_id not in adb.get_connected_devices():
            return False, DEVICE_LOST
        return False, 'NO_SETTINGS_SCREEN'

    worker.set_stage('about')
    if not adb.scroll_to_find_text('About'):
        return False, 'ABOUT_NOT_FOUND'
    adb.safe_tap(text='About', action_label='About phone')
    if wait_for_screen(adb, SETTINGS_SCREENS, timeout=config.ui_element_wait_timeout) != 'ABOUT':
        return False, 'ABOUT_NOT_OPENED'

    worker.set_stage('screenshot')
    path = os.path.join('screenshots', f"{adb.device_id}_{int(time.time())}.png")
    if not adb.take_screenshot(path):
        return False, 'SCREENSHOT_FAILED'
    adb.back_button()
    time.sleep(config.screen_transition_delay)
    return True, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=3, help='tasks in the batch, split across devices')
    ap.add_argument('--all', action='store_true', help='use every device without asking')
    ap.add_argument('--continuous', action='store_true', help='ignore --count, run until ctrl-c')
    args = ap.parse_args()

    # the dashboard redraws the console, details go to the file
    log_file = setup_logging(console=False)
    print(f"log: {log_file}")

    devices = get_connected_devices()
    if args.all:
        selected = [d.device_id for d in devices if d.is_available]
    else:
        selected = interactive_device_selection(devices)
    if not selected:
        return 1

    manager = DeviceManager(task=settings_walk)
    continuous = args.continuous or config.run_mode == 'continuous'
    if not manager.start_parallel(selected, total_tasks=args.count, run_until_stopped=continuous):
        return 1

    display_live_progress(manager, refresh_interval=2.0)
    manager.wait_for_completion()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
