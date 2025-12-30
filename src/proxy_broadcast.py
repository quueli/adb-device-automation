import os
from typing import List, Optional

from src.proxy_checker import verify_proxy
from src.screen_state import screen_has_any

# what apps put in the title bar while the connection is down
CONNECTING_MARKERS = (
    'connecting...',
    'connecting…',
    'waiting for network',
    'no connection',
    'reconnecting',
    'updating...',
)


# PROXY_PORTS="10000,10001", else PROXY_PORT_MIN..PROXY_PORT_MAX, else 10000-10009
def candidate_ports() -> List[int]:
    raw = os.getenv('PROXY_PORTS', '').strip()
    if raw:
        ports = []
        for tok in raw.replace(';', ',').split(','):
            tok = tok.strip()
            if tok.isdigit():
                ports.append(int(tok))
        if ports:
            return ports
    pmin = os.getenv('PROXY_PORT_MIN', '').strip()
    pmax = os.getenv('PROXY_PORT_MAX', '').strip()
    if pmin.isdigit() and pmax.isdigit() and int(pmin) <= int(pmax):
        return list(range(int(pmin), int(pmax) + 1))
    return list(range(10000, 10010))


def _proxy_dict(host: str, port: int, username: str, password: str) -> dict:
    return {'host': host, 'port': str(port), 'username': username, 'password': password}


def check_ports(
    host: str,
    username: str,
    password: str,
    ports: Optional[List[int]] = None,
    level: str = 'L1',
    timeout: float = 12.0,
) -> List[dict]:
    ports = ports if ports is not None else candidate_ports()
    results = []
    for p in ports:
        try:
            res = verify_proxy(_proxy_dict(host, p, username, password), level=level, timeout=timeout)
            results.append({'port': p, 'ok': bool(res.ok), 'latency_ms': float(res.latency_ms)})
        except Exception as e:
            results.append({'port': p, 'ok': False, 'latency_ms': float('inf'), 'error': str(e)})
    results.sort(key=lambda r: (not r['ok'], r['latency_ms']))
    return results


def pick_fastest_port(
    host: str, username: str, password: str,
    ports: Optional[List[int]] = None, level: str = 'L1', timeout: float = 12.0,
) -> Optional[int]:
    for r in check_ports(host, username, password, ports, level=level, timeout=timeout):
        if r['ok']:
            return r['port']
    return None


def next_available_port(
    current_port: Optional[int],
    host: str, username: str, password: str,
    ports: Optional[List[int]] = None, level: str = 'L1', timeout: float = 12.0,
) -> Optional[int]:
    # fastest working port that is not the current one, or the current one if it
    # is the last one standing
    checked = check_ports(host, username, password, ports, level=level, timeout=timeout)
    ok_ports = [r['port'] for r in checked if r['ok']]
    if not ok_ports:
        return None
    for p in ok_ports:
        if p != current_port:
            return p
    return ok_ports[0]


def is_device_connecting(adb, screen_text: Optional[str] = None) -> bool:
    return screen_has_any(adb, CONNECTING_MARKERS, screen_text)
