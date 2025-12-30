#!/usr/bin/env python
"""Local socks5 relay: 127.0.0.1:LOCAL_PORT -> upstream, port read live from the port file."""
import io
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socks as pysocks
from dotenv import load_dotenv

import src.proxy_broadcast as pb
from src.proxy_checker import verify_proxy

# the parent keeps this as a log file, a non-ascii device name would kill the print
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(PROJECT_ROOT, '.env')
PORT_FILE = os.path.join(PROJECT_ROOT, 'data', 'broadcast_port.txt')

load_dotenv(ENV_FILE)

LOCAL_HOST = '127.0.0.1'
LOCAL_PORT = int(os.getenv('LOCAL_PORT', '1080'))

UP_HOST = (os.getenv('PROXY_HOST') or '').strip()
UP_USER = (os.getenv('PROXY_USER') or '').strip()
UP_PASS = (os.getenv('PROXY_PASS') or '').strip()

_lock = threading.Lock()
_current_port = None


def _read_port_file():
    try:
        with open(PORT_FILE, encoding='utf-8') as f:
            v = f.read().strip()
            return int(v) if v.isdigit() else None
    except (OSError, ValueError):
        return None


def _write_port_file(port):
    os.makedirs(os.path.dirname(PORT_FILE), exist_ok=True)
    with open(PORT_FILE, 'w', encoding='utf-8') as f:
        f.write(str(port))


def get_current_port():
    with _lock:
        return _current_port


def set_current_port(port, reason=''):
    global _current_port
    with _lock:
        if port != _current_port:
            print(f"[relay] upstream port -> {port} {reason}", flush=True)
            _current_port = port
            _write_port_file(port)


def _sync_port_from_file():
    # rotate() writes the port from another process, new connections must pick it up
    file_port = _read_port_file()
    if file_port and file_port != get_current_port():
        set_current_port(file_port, reason='(picked up from port file)')


def _refresh_upstream_creds():
    # cached at import, so an edited .env gave "handshake failed" until restart
    global UP_HOST, UP_USER, UP_PASS
    try:
        load_dotenv(ENV_FILE, override=True)
        UP_HOST = (os.getenv('PROXY_HOST') or '').strip()
        UP_USER = (os.getenv('PROXY_USER') or '').strip()
        UP_PASS = (os.getenv('PROXY_PASS') or '').strip()
    except Exception:
        pass


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _open_upstream(addr, port):
    _sync_port_from_file()
    up_port = get_current_port()
    s = pysocks.socksocket()
    s.set_proxy(pysocks.SOCKS5, UP_HOST, up_port,
                username=UP_USER or None, password=UP_PASS or None, rdns=True)
    s.settimeout(20)
    s.connect((addr, port))
    s.settimeout(None)
    return s


def _handle(client):
    try:
        # greeting: version 5, answer "no auth"
        data = client.recv(2)
        if len(data) < 2 or data[0] != 0x05:
            client.close()
            return
        nmethods = data[1]
        client.recv(nmethods)
        client.sendall(b'\x05\x00')

        # only CONNECT (0x01) is served
        hdr = client.recv(4)
        if len(hdr) < 4 or hdr[1] != 0x01:
            client.sendall(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
            client.close()
            return
        atyp = hdr[3]
        if atyp == 0x01:
            addr = socket.inet_ntoa(client.recv(4))
        elif atyp == 0x03:
            ln = client.recv(1)[0]
            addr = client.recv(ln).decode('ascii', errors='replace')
        elif atyp == 0x04:
            addr = socket.inet_ntop(socket.AF_INET6, client.recv(16))
        else:
            client.sendall(b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
            client.close()
            return
        port = struct.unpack('>H', client.recv(2))[0]

        try:
            upstream = _open_upstream(addr, port)
        except Exception as e:
            print(f"[relay] upstream failed for {addr}:{port} via port {get_current_port()}: {e}", flush=True)
            client.sendall(b'\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00')
            client.close()
            return

        client.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
        _pipe(upstream, client)
    except Exception as e:
        print(f"[relay] handler error: {type(e).__name__}: {e}", flush=True)
        try:
            client.close()
        except OSError:
            pass


def _rotation_watcher(interval=20.0):
    while True:
        time.sleep(interval)
        _refresh_upstream_creds()
        cur = get_current_port()
        try:
            res = verify_proxy({'host': UP_HOST, 'port': cur, 'username': UP_USER, 'password': UP_PASS},
                               level='L1', timeout=8.0)
            if not res.ok:
                nxt = pb.next_available_port(cur, UP_HOST, UP_USER, UP_PASS, level='L1', timeout=8.0)
                if nxt and nxt != cur:
                    set_current_port(nxt, reason='(auto rotation, current port is down)')
        except Exception as e:
            print(f"[relay] watcher error: {e}", flush=True)


def main():
    if not UP_HOST:
        print("[relay] PROXY_HOST is not set in .env", flush=True)
        return 1
    port = _read_port_file() or pb.pick_fastest_port(UP_HOST, UP_USER, UP_PASS, level='L1', timeout=8.0)
    if not port:
        print("[relay] no working upstream port", flush=True)
        return 1
    set_current_port(port, reason='(start)')

    threading.Thread(target=_rotation_watcher, daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LOCAL_HOST, LOCAL_PORT))
    srv.listen(128)
    print(f"[relay] socks5 on {LOCAL_HOST}:{LOCAL_PORT} -> {UP_HOST}:{get_current_port()}", flush=True)
    try:
        while True:
            client, _ = srv.accept()
            threading.Thread(target=_handle, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        print("[relay] stopped", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
