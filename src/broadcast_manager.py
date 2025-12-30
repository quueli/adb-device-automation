# phone -> usb -> local relay -> upstream socks5. switching the upstream is a
# write to the port file, the phone is never touched.
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import src.proxy_broadcast as pb
from src.logger import get_logger

log = get_logger('broadcast')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELAY_SCRIPT = PROJECT_ROOT / 'scripts' / 'socks_relay.py'
PORT_FILE = PROJECT_ROOT / 'data' / 'broadcast_port.txt'
RELAY_LOG = PROJECT_ROOT / 'data' / 'broadcast_relay.log'

DEFAULT_LOCAL_PORT = int(os.getenv('BROADCAST_LOCAL_PORT', '1080'))


class BroadcastManager:
    # one instance per worker but a single port file, so the lock is on the class
    _ROTATE_LOCK = threading.Lock()

    def __init__(self, local_port: int = DEFAULT_LOCAL_PORT):
        self.local_port = local_port
        self._relay_proc: Optional[subprocess.Popen] = None
        self._relay_log = None
        self._watchers: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start_relay(self, wait_bind: float = 12.0) -> bool:
        if self._relay_proc and self._relay_proc.poll() is None:
            return True
        env = dict(os.environ, LOCAL_PORT=str(self.local_port))
        RELAY_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._relay_log = open(RELAY_LOG, 'w', encoding='utf-8')
        self._relay_proc = subprocess.Popen(
            [sys.executable, str(RELAY_SCRIPT)],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=self._relay_log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + wait_bind
        while time.time() < deadline:
            if self._port_listening(self.local_port):
                log.info(f"relay listening on 127.0.0.1:{self.local_port}")
                return True
            if self._relay_proc.poll() is not None:
                log.error(f"relay exited on start, see {RELAY_LOG}")
                return False
            time.sleep(0.5)
        log.warning(f"relay did not bind in {wait_bind:.0f}s, see {RELAY_LOG}")
        return False

    @staticmethod
    def _port_listening(port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            return s.connect_ex(('127.0.0.1', port)) == 0
        finally:
            s.close()

    def relay_alive(self) -> bool:
        return self._port_listening(self.local_port)

    def stop_relay(self):
        if self._relay_proc and self._relay_proc.poll() is None:
            self._relay_proc.terminate()
            try:
                self._relay_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._relay_proc.kill()
        self._relay_proc = None
        if self._relay_log:
            try:
                self._relay_log.close()
            except Exception:
                pass
            self._relay_log = None

    def current_port(self) -> Optional[int]:
        try:
            v = PORT_FILE.read_text(encoding='utf-8').strip()
            return int(v) if v.isdigit() else None
        except (OSError, ValueError):
            return None

    def rotate(self) -> Optional[int]:
        host = (os.getenv('PROXY_HOST') or '').strip()
        user = (os.getenv('PROXY_USER') or '').strip()
        pwd = (os.getenv('PROXY_PASS') or '').strip()
        with BroadcastManager._ROTATE_LOCK:
            cur = self.current_port()
            # L2, not L1: a port can accept tcp and still refuse the socks5 auth
            nxt = pb.next_available_port(cur, host, user, pwd, level='L2', timeout=10.0)
            if nxt is None:
                log.error("rotation: no working upstream port")
                return None
            if nxt != cur:
                PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = PORT_FILE.with_suffix('.txt.tmp')
                tmp.write_text(str(nxt), encoding='utf-8')
                os.replace(tmp, PORT_FILE)
                log.info(f"upstream port {cur} -> {nxt}")
            return nxt

    def attach_device(self, device_id: str) -> bool:
        try:
            subprocess.run(
                ['adb', '-s', device_id, 'reverse', f'tcp:{self.local_port}', f'tcp:{self.local_port}'],
                capture_output=True, text=True, timeout=15, check=False,
            )
            out = subprocess.run(['adb', '-s', device_id, 'reverse', '--list'],
                                 capture_output=True, text=True, timeout=10).stdout
            ok = f'tcp:{self.local_port}' in out
            log.info(f"[{device_id}] adb reverse: {'ok' if ok else 'failed'}")
            return ok
        except Exception as e:
            log.error(f"[{device_id}] adb reverse error: {e}")
            return False

    def detach_device(self, device_id: str):
        try:
            subprocess.run(['adb', '-s', device_id, 'reverse', '--remove', f'tcp:{self.local_port}'],
                           capture_output=True, text=True, timeout=10, check=False)
        except Exception:
            pass

    def start_connecting_watcher(self, device_id: str, adb, interval: float = 8.0):
        # two "Connecting..." in a row mean the upstream is dead for this device
        stop = threading.Event()
        with self._lock:
            old = self._watchers.get(device_id)
            if old:
                old.set()
            self._watchers[device_id] = stop

        def loop():
            misses = 0
            while not stop.is_set():
                time.sleep(interval)
                if stop.is_set():
                    break
                try:
                    if pb.is_device_connecting(adb):
                        misses += 1
                        if misses >= 2:
                            log.info(f"[{device_id}] still connecting, rotating upstream port")
                            self.rotate()
                            misses = 0
                    else:
                        misses = 0
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True).start()

    def stop_watcher(self, device_id: str):
        with self._lock:
            ev = self._watchers.pop(device_id, None)
        if ev:
            ev.set()

    def start(self) -> bool:
        return self.start_relay()

    def stop(self):
        for dev in list(self._watchers):
            self.stop_watcher(dev)
        self.stop_relay()
