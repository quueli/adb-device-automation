# L1 is a plain tcp connect, L2 a real socks5 handshake with auth
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import socks as pysocks


@dataclass
class ProxyCheckResult:
    ok: bool
    tcp_ok: bool = False
    socks_ok: bool = False
    latency_ms: float = 0.0
    error: str = ''
    checked_at: datetime = field(default_factory=datetime.now)


# a fresh connection sometimes gets "host unreachable" from the proxy itself
# while the second attempt goes through
_CONNECT_RETRY_ATTEMPTS = 2
_CONNECT_RETRY_DELAY_SEC = 1.5


def check_target() -> tuple:
    return os.getenv('PROXY_CHECK_HOST', 'example.com'), int(os.getenv('PROXY_CHECK_PORT', '443'))


def parse_proxy_line(line: str) -> Optional[dict]:
    """host:port:user:pass, 'Name host:port:user:pass', [scheme://]user:pass@host:port or host:port."""
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

    core = stripped
    for scheme in ('socks5h://', 'socks5://', 'socks4://', 'https://', 'http://'):
        if core.lower().startswith(scheme):
            core = core[len(scheme):]
            break
    if '@' in core:
        cred, _, hostport = core.rpartition('@')
        hp = hostport.split(':')
        if len(hp) == 2 and hp[0] and hp[1]:
            user, _, pwd = cred.partition(':')
            return {'name': None, 'host': hp[0], 'port': hp[1], 'username': user, 'password': pwd}

    fields = stripped.split(':')
    if len(fields) == 4:
        return {'name': None, 'host': fields[0], 'port': fields[1],
                'username': fields[2], 'password': fields[3]}
    if len(fields) == 2 and fields[0] and fields[1]:
        return {'name': None, 'host': fields[0], 'port': fields[1], 'username': '', 'password': ''}
    return None


def _check_tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_socks5(proxy: dict, timeout: float) -> bool:
    target = check_target()
    for attempt in range(_CONNECT_RETRY_ATTEMPTS + 1):
        try:
            s = pysocks.socksocket()
            s.set_proxy(
                pysocks.SOCKS5,
                proxy['host'],
                int(proxy['port']),
                username=proxy.get('username') or None,
                password=proxy.get('password') or None,
                rdns=True,
            )
            s.settimeout(timeout)
            s.connect(target)
            s.close()
            return True
        except Exception:
            if attempt < _CONNECT_RETRY_ATTEMPTS:
                time.sleep(_CONNECT_RETRY_DELAY_SEC)
                continue
            return False
    return False


def verify_proxy(proxy: dict, level: str = 'L2', timeout: float = 15.0) -> ProxyCheckResult:
    start = time.time()
    host = (proxy or {}).get('host', '')
    try:
        port = int(proxy.get('port', 0))
    except (TypeError, ValueError):
        return ProxyCheckResult(ok=False, error=f"bad port: {proxy.get('port')!r}")

    if not host or not port:
        return ProxyCheckResult(ok=False, error="proxy host/port missing")

    if not _check_tcp(host, port, timeout=min(timeout, 5.0)):
        return ProxyCheckResult(ok=False, tcp_ok=False, error=f"tcp connect failed: {host}:{port}",
                                latency_ms=(time.time() - start) * 1000)
    if level == 'L1':
        return ProxyCheckResult(ok=True, tcp_ok=True, latency_ms=(time.time() - start) * 1000)

    if not _check_socks5(proxy, timeout=min(timeout, 8.0)):
        return ProxyCheckResult(ok=False, tcp_ok=True, socks_ok=False,
                                error="socks5 handshake or auth failed",
                                latency_ms=(time.time() - start) * 1000)
    return ProxyCheckResult(ok=True, tcp_ok=True, socks_ok=True, latency_ms=(time.time() - start) * 1000)
