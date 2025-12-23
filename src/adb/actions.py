import time
from typing import Any, Dict, List, Optional

from src.adb.shell import log
from src.adb.ui import UiTree

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


class ADBManager(UiTree):
    def __init__(self, device_id: str = None, xml_cache_ttl: float = 0.3):
        super().__init__(device_id, xml_cache_ttl)
        # how often safe_tap found its target in the tree vs fell back to coordinates
        self.tap_metrics: Dict[str, int] = {'xml_hit': 0, 'fallback': 0, 'miss': 0}

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
