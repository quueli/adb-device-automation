"""Shares the host proxy with every usb device: python examples/share_proxy.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adb import ADBManager
from src.broadcast_manager import RELAY_LOG, BroadcastManager
from src.config_loader import config
from src.devices import get_connected_devices
from src.logger import setup_logging


def main() -> int:
    setup_logging()
    upstream = config.get_str('PROXY_HOST')
    if not upstream:
        print("PROXY_HOST is not set, see .env.example")
        return 1

    mgr = BroadcastManager()
    if not mgr.start():
        return 1
    print(f"relay 127.0.0.1:{mgr.local_port} -> {upstream}:{mgr.current_port()}")

    attached = []
    for dev in get_connected_devices():
        if dev.is_available and mgr.attach_device(dev.device_id):
            attached.append(dev.device_id)
            mgr.start_connecting_watcher(dev.device_id, ADBManager(dev.device_id))
    print(f"{len(attached)} device(s) attached, point the app on the phone at socks5://127.0.0.1:{mgr.local_port}")

    try:
        while True:
            time.sleep(5)
            if not mgr.relay_alive():
                print(f"relay died, see {RELAY_LOG}")
                return 1
    except KeyboardInterrupt:
        pass
    finally:
        for device_id in attached:
            mgr.detach_device(device_id)
        mgr.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
