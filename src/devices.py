import subprocess
from dataclasses import dataclass
from typing import List

from src.logger import get_logger

log = get_logger('devices')


@dataclass
class DeviceInfo:
    device_id: str
    status: str  # device / offline / unauthorized
    model: str = ''
    android_version: str = ''
    is_available: bool = True


def get_connected_devices() -> List[DeviceInfo]:
    devices = []
    try:
        result = subprocess.run(['adb', 'devices', '-l'], capture_output=True, text=True, timeout=10)

        for line in result.stdout.strip().split('\n')[1:]:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue

            device_id, status = parts[0], parts[1]
            model = ''
            for part in parts[2:]:
                if part.startswith('model:'):
                    model = part.split(':')[1]
                    break

            devices.append(DeviceInfo(
                device_id=device_id,
                status=status,
                model=model,
                is_available=status == 'device',
            ))

        for device in devices:
            if device.is_available:
                try:
                    result = subprocess.run(
                        ['adb', '-s', device.device_id, 'shell', 'getprop', 'ro.build.version.release'],
                        capture_output=True, text=True, timeout=5,
                    )
                    device.android_version = result.stdout.strip()
                except (subprocess.TimeoutExpired, OSError):
                    pass

    except Exception as e:
        log.error(f"device scan failed: {e}")

    return devices


def interactive_device_selection(devices: List[DeviceInfo]) -> List[str]:
    if not devices:
        print("No devices found")
        return []

    available = [d for d in devices if d.is_available]
    if not available:
        print("No available devices. Check usb debugging and that the device is authorized.")
        return []

    print("\nAvailable devices:\n")
    for i, device in enumerate(available, 1):
        model_str = f" ({device.model})" if device.model else ""
        android_str = f" Android {device.android_version}" if device.android_version else ""
        print(f"  {i}. {device.device_id}{model_str}{android_str}")

    print("\n  [a] all devices   [1,2,3] pick some   [q] cancel")

    while True:
        try:
            choice = input("\nSelect devices: ").strip().lower()

            if choice == 'q':
                return []

            if choice == 'a':
                selected = [d.device_id for d in available]
                print(f"\nSelected all {len(selected)} devices")
                return selected

            try:
                indices = [int(x.strip()) for x in choice.split(',')]
            except ValueError:
                print("Enter device numbers separated by commas.")
                continue

            selected = []
            for idx in indices:
                if 1 <= idx <= len(available):
                    selected.append(available[idx - 1].device_id)
                else:
                    print(f"Invalid selection: {idx}")

            if selected:
                print(f"\nSelected {len(selected)} device(s):")
                for device_id in selected:
                    print(f"  - {device_id}")
                return selected

        except KeyboardInterrupt:
            print("\n\nSelection cancelled")
            return []
