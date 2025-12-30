#!/usr/bin/env python
"""Local socks5 relay: 127.0.0.1:LOCAL_PORT -> upstream socks5 from .env."""
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socks as pysocks
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

LOCAL_HOST = '127.0.0.1'
LOCAL_PORT = int(os.getenv('LOCAL_PORT', '1080'))

UP_HOST = (os.getenv('PROXY_HOST') or '').strip()
UP_PORT = int(os.getenv('PROXY_PORT', '10000'))
UP_USER = (os.getenv('PROXY_USER') or '').strip()
UP_PASS = (os.getenv('PROXY_PASS') or '').strip()


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
    s = pysocks.socksocket()
    s.set_proxy(pysocks.SOCKS5, UP_HOST, UP_PORT,
                username=UP_USER or None, password=UP_PASS or None, rdns=True)
    s.settimeout(20)
    s.connect((addr, port))
    s.settimeout(None)
    return s


def _handle(client):
    try:
        data = client.recv(2)
        if len(data) < 2 or data[0] != 0x05:
            client.close()
            return
        client.recv(data[1])
        client.sendall(b'\x05\x00')

        hdr = client.recv(4)
        if len(hdr) < 4 or hdr[1] != 0x01:
            client.sendall(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
            client.close()
            return
        atyp = hdr[3]
        if atyp == 0x01:
            addr = socket.inet_ntoa(client.recv(4))
        elif atyp == 0x03:
            addr = client.recv(client.recv(1)[0]).decode('ascii', errors='replace')
        else:
            client.sendall(b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
            client.close()
            return
        port = struct.unpack('>H', client.recv(2))[0]

        try:
            upstream = _open_upstream(addr, port)
        except Exception as e:
            print(f"[relay] upstream failed for {addr}:{port}: {e}", flush=True)
            client.sendall(b'\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00')
            client.close()
            return

        client.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
        _pipe(upstream, client)
    except Exception as e:
        print(f"[relay] handler error: {e}", flush=True)
        client.close()


def main():
    if not UP_HOST:
        print("[relay] PROXY_HOST is not set in .env", flush=True)
        return 1
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LOCAL_HOST, LOCAL_PORT))
    srv.listen(128)
    print(f"[relay] socks5 on {LOCAL_HOST}:{LOCAL_PORT} -> {UP_HOST}:{UP_PORT}", flush=True)
    try:
        while True:
            client, _ = srv.accept()
            threading.Thread(target=_handle, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        print("[relay] stopped", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
