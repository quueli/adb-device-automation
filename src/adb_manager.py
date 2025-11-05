import re
import subprocess
import time
from typing import List, Optional

from src.config_loader import config
from src.logger import get_logger

log = get_logger('adb')


class ADBManager:
    def __init__(self, device_id: str = None):
        self.device_id = device_id
        self.config = config

    @staticmethod
    def get_connected_devices() -> List[str]:
        result = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=10)
        devices = []
        for line in result.stdout.strip().split('\n')[1:]:
            if '\tdevice' in line:
                devices.append(line.split('\t')[0])
        return devices

    def connect_device(self) -> bool:
        devices = self.get_connected_devices()
        if not devices:
            log.error("no devices found, plug one in with usb debugging enabled")
            return False
        if self.device_id and self.device_id not in devices:
            log.error(f"[{self.device_id}] device not found")
            return False
        self.device_id = self.device_id or devices[0]
        log.info(f"[{self.device_id}] connected")
        return True

    def _adb(self, *args: str, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        return subprocess.run(['adb', '-s', self.device_id, *args],
                              capture_output=True, text=True,
                              timeout=timeout or self.config.adb_command_timeout)

    def execute_command(self, command: str, timeout: Optional[float] = None) -> str:
        return self._adb('shell', command, timeout=timeout).stdout.strip()

    def tap(self, x: int, y: int):
        self.execute_command(f'input tap {x} {y}')
        time.sleep(0.5)

    def input_text(self, text: str):
        self.execute_command(f"input text {text.replace(' ', '%s')}")
        time.sleep(0.3)

    def press_key(self, keycode: int):
        self.execute_command(f'input keyevent {keycode}')
        time.sleep(0.3)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        self.execute_command(f'input swipe {x1} {y1} {x2} {y2} {duration}')
        time.sleep(0.5)

    def back_button(self):
        self.press_key(4)

    def home_button(self):
        self.press_key(3)

    def get_screen_size(self) -> tuple:
        match = re.search(r'(\d+)x(\d+)', self.execute_command('wm size'))
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1080, 1920

    def is_app_installed(self, package: str) -> bool:
        return package in self.execute_command(f'pm list packages {package}')

    def launch_app(self, package: str, activity: str = None):
        if activity:
            self.execute_command(f'am start -n {package}/{activity}')
        else:
            self.execute_command(f'monkey -p {package} -c android.intent.category.LAUNCHER 1')
        time.sleep(self.config.app_launch_delay)

    def force_stop(self, package: str):
        self.execute_command(f'am force-stop {package}')

    def current_focus(self) -> str:
        return self.execute_command('dumpsys window | grep mCurrentFocus')
