import time
from dataclasses import dataclass
from typing import Sequence, Tuple

from src.logger import get_logger

log = get_logger('screen')

UNKNOWN = 'UNKNOWN'
TIMEOUT = 'TIMEOUT'


@dataclass(frozen=True)
class ScreenRule:
    name: str
    any_of: Tuple[str, ...]

    def matches(self, screen_lower: str) -> bool:
        return any(m.lower() in screen_lower for m in self.any_of)


def detect_screen(screen_text: str, rules: Sequence[ScreenRule], default: str = UNKNOWN) -> str:
    low = (screen_text or '').lower()
    for rule in rules:
        if rule.matches(low):
            return rule.name
    return default


def wait_for_screen(adb, rules: Sequence[ScreenRule], timeout: float, poll_interval: float = 1.0) -> str:
    start = time.time()
    while time.time() - start < timeout:
        state = detect_screen(adb.get_screen_text(), rules)
        if state != UNKNOWN:
            return state
        time.sleep(poll_interval)
    return TIMEOUT
