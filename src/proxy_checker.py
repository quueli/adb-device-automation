import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ProxyCheckResult:
    ok: bool
    tcp_ok: bool = False
    latency_ms: float = 0.0
    error: str = ''
    checked_at: datetime = field(default_factory=datetime.now)


def parse_proxy_line(line: str) -> Optional[dict]:
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        return None

    parts = re.split(r'\s+', stripped, maxsplit=1)
    if len(parts) == 2:
        name, rest = parts
        fields = rest.split(':')
        if len(fields) == 4:
            return {'name': name, 'host': fields[0], 'port': fields[1],
                    'username': fields[2], 'password': fields[3]}

    fields = stripped.split(':')
    if len(fields) == 4:
        return {'name': None, 'host': fields[0], 'port': fields[1],
                'username': fields[2], 'password': fields[3]}
    return None


def _check_tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def verify_proxy(proxy: dict, timeout: float = 15.0) -> ProxyCheckResult:
    start = time.time()
    host = (proxy or {}).get('host', '')
    try:
        port = int(proxy.get('port', 0))
    except (TypeError, ValueError):
        return ProxyCheckResult(ok=False, error=f"bad port: {proxy.get('port')!r}")

    if not host or not port:
        return ProxyCheckResult(ok=False, error="proxy host/port missing")

    if not _check_tcp(host, port, timeout=min(timeout, 5.0)):
        return ProxyCheckResult(ok=False, error=f"tcp connect failed: {host}:{port}",
                                latency_ms=(time.time() - start) * 1000)
    return ProxyCheckResult(ok=True, tcp_ok=True, latency_ms=(time.time() - start) * 1000)
