import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from src.config_loader import config
from src.logger import get_logger

log = get_logger('adb')

# the device shell (mksh) sees the argument of `input text`, so these need a backslash
_SHELL_SPECIAL = set('&<>()|;\'"`$\\')


def escape_input_text(text: str) -> str:
    out = []
    for ch in text:
        if ch == ' ':
            out.append('%s')
        elif ch in _SHELL_SPECIAL:
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


class ADBManager:
    def __init__(self, device_id: str = None, xml_cache_ttl: float = 0.3):
        self.device_id = device_id
        self.config = config
        self._xml_cache: Optional[str] = None
        self._xml_cache_time = 0.0
        self._xml_cache_ttl = xml_cache_ttl
        self._u2 = None
        self._u2_failed = False
        self.tap_metrics: Dict[str, int] = {'xml_hit': 0, 'fallback': 0, 'miss': 0}

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

    def _get_u2(self):
        # uiautomator2 keeps a server on the device: dump_hierarchy ~0.2s against
        # ~2.7s for `uiautomator dump`. any failure falls back to the plain dump
        if self._u2 is not None:
            return self._u2
        if self._u2_failed or os.getenv('U2_ENABLED', '1').lower() not in ('1', 'true', 'yes'):
            return None
        try:
            import uiautomator2 as u2
            self._u2 = u2.connect(self.device_id)
            return self._u2
        except Exception as e:
            log.info(f"[{self.device_id}] uiautomator2 not available ({type(e).__name__}: {e}), "
                     f"using uiautomator dump")
            self._u2_failed = True
            return None

    def invalidate_xml_cache(self):
        self._xml_cache = None

    def _store_xml(self, xml: str) -> str:
        self._xml_cache = xml
        self._xml_cache_time = time.time()
        return xml

    def get_screen_text(self, use_cache: bool = True) -> str:
        """Raw uiautomator xml of the current screen, '' if every dump path failed."""
        if use_cache and self._xml_cache is not None and (time.time() - self._xml_cache_time) < self._xml_cache_ttl:
            return self._xml_cache

        d = self._get_u2()
        if d is not None:
            try:
                xml = d.dump_hierarchy()
                if xml and '<hierarchy' in xml:
                    return self._store_xml(xml)
            except Exception:
                pass

        # one adb round trip: dump straight to the tty instead of file + cat
        try:
            r = self._adb('exec-out', 'uiautomator', 'dump', '/dev/tty', timeout=15)
            xml = r.stdout or ''
            if '<hierarchy' in xml:
                start = xml.find('<?xml')
                if start < 0:
                    start = xml.find('<hierarchy')
                end = xml.rfind('</hierarchy>')
                xml = xml[start:end + len('</hierarchy>')] if end >= 0 else xml[start:]
                return self._store_xml(xml)
        except Exception:
            pass

        # slowest path, for devices where exec-out mangles the output
        try:
            self.execute_command('uiautomator dump /sdcard/window_dump.xml')
            xml = self.execute_command('cat /sdcard/window_dump.xml')
            if xml and '<hierarchy' in xml:
                return self._store_xml(xml)
        except Exception:
            pass
        return ''

    def get_screen_xml(self):
        xml_str = self.get_screen_text()
        if not xml_str:
            return None
        try:
            return ET.fromstring(xml_str)
        except ET.ParseError:
            return None

    @staticmethod
    def _bounds_center(bounds: str) -> Optional[tuple]:
        # bounds look like [x1,y1][x2,y2]
        try:
            x1, y1, x2, y2 = [int(n) for n in re.findall(r'\d+', bounds)]
            return (x1 + x2) // 2, (y1 + y2) // 2
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _match_text(node: Any, text: str, partial: bool) -> bool:
        t = (node.attrib.get('text') or '').lower()
        d = (node.attrib.get('content-desc') or '').lower()
        text_lower = text.lower()
        match_text = (text_lower in t) if partial else (t == text_lower)
        match_desc = (text_lower in d) if partial else (d == text_lower)
        return match_text or match_desc

    @staticmethod
    def _match_resource(node: Any, resource_id: str) -> bool:
        rid = node.attrib.get('resource-id') or ''
        return rid.endswith(resource_id) or rid == resource_id

    @staticmethod
    def _match_class(node: Any, class_name: str) -> bool:
        cls = node.attrib.get('class') or ''
        return cls.endswith(class_name) or cls == class_name

    @staticmethod
    def _match_content_desc(node: Any, content_desc: str, partial: bool) -> bool:
        d = (node.attrib.get('content-desc') or '').lower()
        cd_lower = content_desc.lower()
        return (cd_lower in d) if partial else (d == cd_lower)

    def _matches(self, node, text, partial_text, resource_id, content_desc, partial_desc, class_name) -> bool:
        if text and not self._match_text(node, text, partial_text):
            return False
        if resource_id and not self._match_resource(node, resource_id):
            return False
        if content_desc and not self._match_content_desc(node, content_desc, partial_desc):
            return False
        if class_name and not self._match_class(node, class_name):
            return False
        return True

    def find_element(
        self,
        text: str = None,
        partial_text: bool = True,
        resource_id: str = None,
        content_desc: str = None,
        partial_desc: bool = True,
        class_name: str = None,
    ) -> Optional[Dict[str, Any]]:
        for hit in self._iter_matches(text, partial_text, resource_id, content_desc, partial_desc, class_name):
            return hit
        return None

    def find_all_elements(
        self,
        text: str = None,
        partial_text: bool = True,
        resource_id: str = None,
        content_desc: str = None,
        partial_desc: bool = True,
        class_name: str = None,
    ) -> List[Dict[str, Any]]:
        # document order; sort by center x or y yourself when the layout matters
        return list(self._iter_matches(text, partial_text, resource_id, content_desc, partial_desc, class_name))

    def _iter_matches(self, text, partial_text, resource_id, content_desc, partial_desc, class_name):
        root = self.get_screen_xml()
        if root is None:
            return
        for node in root.iter():
            if not self._matches(node, text, partial_text, resource_id, content_desc, partial_desc, class_name):
                continue
            bounds = node.attrib.get('bounds')
            center = self._bounds_center(bounds) if bounds else None
            if center:
                yield {'center': center, 'node': node}

    def tap(self, x: int, y: int):
        self.execute_command(f'input tap {x} {y}')
        self.invalidate_xml_cache()
        time.sleep(0.5)

    def input_text(self, text: str):
        # `input text` only takes ascii, anything else needs an ime on the device
        self.execute_command(f'input text {escape_input_text(text)}')
        self.invalidate_xml_cache()
        time.sleep(0.3)

    def press_key(self, keycode: int):
        self.execute_command(f'input keyevent {keycode}')
        self.invalidate_xml_cache()
        time.sleep(0.3)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        self.execute_command(f'input swipe {x1} {y1} {x2} {y2} {duration}')
        self.invalidate_xml_cache()
        time.sleep(0.5)

    def back_button(self):
        self.press_key(4)

    def home_button(self):
        self.press_key(3)

    def recent_apps(self):
        self.press_key(187)

    def launch_app(self, package: str, activity: str = None):
        if activity:
            self.execute_command(f'am start -n {package}/{activity}')
        else:
            self.execute_command(f'monkey -p {package} -c android.intent.category.LAUNCHER 1')
        self.invalidate_xml_cache()
        time.sleep(self.config.app_launch_delay)

    def force_stop(self, package: str):
        self.execute_command(f'am force-stop {package}')
        self.invalidate_xml_cache()

    def wait_for_element(
        self,
        text: str = None,
        partial_text: bool = True,
        resource_id: str = None,
        content_desc: str = None,
        partial_desc: bool = True,
        class_name: str = None,
        timeout: Optional[int] = None,
        poll_interval: float = 0.5,
    ) -> Optional[Dict[str, Any]]:
        timeout = timeout or self.config.ui_element_wait_timeout
        start_time = time.time()
        while time.time() - start_time < timeout:
            found = self.find_element(
                text=text,
                partial_text=partial_text,
                resource_id=resource_id,
                content_desc=content_desc,
                partial_desc=partial_desc,
                class_name=class_name,
            )
            if found:
                return found
            time.sleep(poll_interval)
        return None

    def wait_for_any_text(
        self,
        texts: List[str],
        partial: bool = True,
        timeout: Optional[int] = None,
        poll_interval: float = 0.5,
    ) -> Optional[Dict[str, Any]]:
        timeout = timeout or self.config.ui_element_wait_timeout
        start_time = time.time()
        while time.time() - start_time < timeout:
            # one dump per round for the whole list, thanks to the cache
            for text in texts:
                found = self.find_element(text=text, partial_text=partial)
                if found:
                    found['matched_text'] = text
                    return found
            time.sleep(poll_interval)
        return None

    def safe_tap(
        self,
        text: str = None,
        partial_text: bool = True,
        resource_id: str = None,
        content_desc: str = None,
        partial_desc: bool = True,
        class_name: str = None,
        timeout: Optional[int] = None,
        fallback_coords: Optional[tuple] = None,
        action_label: str = '',
    ) -> bool:
        """Wait for the element and tap its center; tap fallback_coords if it never shows up."""
        label = action_label or text or resource_id or content_desc or class_name or 'element'
        found = self.wait_for_element(
            text=text,
            partial_text=partial_text,
            resource_id=resource_id,
            content_desc=content_desc,
            partial_desc=partial_desc,
            class_name=class_name,
            timeout=timeout,
        )
        if found and found.get('center'):
            x, y = found['center']
            log.debug(f"[{self.device_id}] tap {label} at ({x}, {y})")
            self.tap_metrics['xml_hit'] += 1
            self.tap(x, y)
            time.sleep(self.config.button_tap_delay)
            return True

        if fallback_coords:
            x, y = fallback_coords
            log.warning(f"[{self.device_id}] {label} not in the tree, tapping fallback ({x}, {y})")
            self.tap_metrics['fallback'] += 1
            self.tap(x, y)
            time.sleep(self.config.button_tap_delay)
            return True

        self.tap_metrics['miss'] += 1
        log.warning(f"[{self.device_id}] {label} not found")
        return False

    def get_tap_metrics(self) -> Dict[str, int]:
        return dict(self.tap_metrics)

    def scroll_to_find_text(self, text: str, max_scrolls: int = 10) -> bool:
        if self.find_element(text=text, partial_text=True):
            return True
        width, height = self.get_screen_size()
        sx = width // 2
        y1 = int(height * 0.8)
        y2 = int(height * 0.3)
        for _ in range(max_scrolls):
            self.swipe(sx, y1, sx, y2, duration=400)
            time.sleep(0.6)
            if self.find_element(text=text, partial_text=True):
                return True
        return False
