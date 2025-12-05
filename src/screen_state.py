import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, Tuple

from src.logger import get_logger

log = get_logger('screen')

UNKNOWN = 'UNKNOWN'
TIMEOUT = 'TIMEOUT'


# rules are checked in order and the first hit wins, so the specific ones (an
# error dialog) go before the generic ones (the form behind it)
@dataclass(frozen=True)
class ScreenRule:
    name: str
    any_of: Tuple[str, ...]
    none_of: Tuple[str, ...] = ()

    def matches(self, screen_lower: str) -> bool:
        if not any(m.lower() in screen_lower for m in self.any_of):
            return False
        return not any(m.lower() in screen_lower for m in self.none_of)


def detect_screen(screen_text: str, rules: Sequence[ScreenRule], default: str = UNKNOWN) -> str:
    low = (screen_text or '').lower()
    for rule in rules:
        if rule.matches(low):
            return rule.name
    return default


def wait_for_screen(
    adb,
    rules: Sequence[ScreenRule],
    timeout: float,
    poll_interval: float = 1.0,
    transient: Iterable[str] = (),
    report: Optional[Callable[[str], None]] = None,
) -> str:
    report = report or log.debug
    transient = set(transient)
    start = time.time()
    last_report = start
    while time.time() - start < timeout:
        state = detect_screen(adb.get_screen_text(), rules)
        if state != UNKNOWN and state not in transient:
            return state
        if time.time() - last_report >= 5:
            report(f"still waiting ({state.lower()}, {int(time.time() - start)}s)")
            last_report = time.time()
        time.sleep(poll_interval)
    return TIMEOUT


def screen_has_any(adb, markers: Iterable[str], screen_text: Optional[str] = None) -> bool:
    try:
        text = screen_text if screen_text is not None else adb.get_screen_text()
    except Exception:
        return False
    low = (text or '').lower()
    return any(m.lower() in low for m in markers)
