import os
import re
import subprocess
from typing import List, Optional

from src.config_loader import config
from src.logger import get_logger

log = get_logger('adb')


class AdbShell:
    def __init__(self, device_id: str = None):
        self.device_id = device_id
        self.config = config

    @staticmethod
    def check_adb_installed() -> bool:
        try:
            result = subprocess.run(['adb', 'version'], capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def get_connected_devices() -> List[str]:
        try:
            result = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
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

        if self.device_id:
            if self.device_id in devices:
                log.info(f"[{self.device_id}] connected")
                return True
            log.error(f"[{self.device_id}] device not found")
            return False

        self.device_id = devices[0]
        log.info(f"[{self.device_id}] connected")
        return True

    def _adb(self, *args: str, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        if not self.device_id:
            raise RuntimeError("device not connected")
        return subprocess.run(['adb', '-s', self.device_id, *args],
                              capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout or self.config.adb_command_timeout)

    def execute_command(self, command: str, timeout: Optional[float] = None) -> str:
        try:
            return self._adb('shell', command, timeout=timeout).stdout.strip()
        except subprocess.TimeoutExpired:
            log.warning(f"[{self.device_id}] shell command timed out: {command[:60]}")
            return ''

    def get_screen_size(self) -> tuple:
        output = self.execute_command('wm size')
        match = re.search(r'(\d+)x(\d+)', output)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1080, 1920

    def install_apk(self, apk_path: str) -> bool:
        log.info(f"[{self.device_id}] installing {apk_path}")
        try:
            result = self._adb('install', '-r', apk_path, timeout=300)
        except subprocess.TimeoutExpired:
            log.error(f"[{self.device_id}] install timed out")
            return False
        if 'Success' in result.stdout:
            return True
        log.error(f"[{self.device_id}] install failed: {(result.stdout + result.stderr).strip()}")
        return False

    def uninstall_app(self, package: str) -> bool:
        result = self.execute_command(f'pm uninstall {package}')
        if 'Success' in result or 'Unknown package' in result:
            return True
        log.warning(f"[{self.device_id}] uninstall {package}: {result}")
        return False

    def clear_app_data(self, package: str) -> bool:
        return 'Success' in self.execute_command(f'pm clear {package}')

    def is_app_installed(self, package: str) -> bool:
        # pm filters by substring, so com.example.app would also match com.example.app.beta
        output = self.execute_command(f'pm list packages {package}')
        return f'package:{package}' in output.split()

    def current_focus(self) -> str:
        return self.execute_command('dumpsys window | grep mCurrentFocus')

    def is_in_foreground(self, package: str) -> bool:
        return package in self.current_focus()

    def grant_permissions(self, package: str, permissions: List[str]) -> List[str]:
        refused = []
        for permission in permissions:
            result = self.execute_command(f'pm grant {package} {permission} 2>&1')
            # empty answer is success, the rest depends on the android version
            if result.strip() and 'Unknown permission' not in result and 'not a changeable' not in result:
                refused.append(permission)
        return refused

    def take_screenshot(self, local_path: str, remote_path: str = '/sdcard/screenshot.png') -> bool:
        self.execute_command(f'screencap -p {remote_path}')
        os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
        try:
            result = self._adb('pull', remote_path, local_path, timeout=60)
        except subprocess.TimeoutExpired:
            return False
        self.execute_command(f'rm -f {remote_path}')
        return result.returncode == 0 and os.path.exists(local_path)
