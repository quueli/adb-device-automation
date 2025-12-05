import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from src.device_worker import DeviceWorker, TaskFn, WorkerProgress
from src.logger import get_logger
from src.thread_safe_resources import SharedStatistics

log = get_logger('manager')


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


class DeviceManager:
    def __init__(self, task: TaskFn, resource_pool=None):
        self.task = task
        self.resource_pool = resource_pool
        self.shared_stats = SharedStatistics()
        self.workers: Dict[str, DeviceWorker] = {}
        self._lock = threading.Lock()

        self.is_running = False
        self._stop_requested = False
        self._completed_devices = set()
        self._workers_started = False

    @staticmethod
    def distribute_tasks(total: int, device_count: int) -> List[int]:
        """7 tasks on 3 devices -> [3, 2, 2]."""
        if device_count <= 0:
            return []
        base = total // device_count
        remainder = total % device_count
        return [base + (1 if i < remainder else 0) for i in range(device_count)]

    def start_parallel(
        self,
        selected_devices: List[str],
        total_tasks: int,
        run_until_stopped: bool = False,
    ) -> bool:
        if self.is_running:
            log.warning("a batch is already running")
            return False
        if not selected_devices:
            log.warning("no devices selected")
            return False

        # in continuous mode the split is only shown for information, the workers
        # ignore task_count and run until stop()
        distribution = self.distribute_tasks(total_tasks, len(selected_devices))
        log.info(f"starting on {len(selected_devices)} devices, "
                 f"{'continuous' if run_until_stopped else total_tasks} tasks, split {distribution}")

        if self.resource_pool is not None and not run_until_stopped:
            available = self.resource_pool.get_available_count()
            if available < total_tasks:
                log.warning(f"only {available} items in the pool for {total_tasks} tasks")

        self.is_running = True
        self._stop_requested = False
        self.workers.clear()
        # the end of the batch is an explicit set of devices that reported, not a
        # poll of worker.is_running: a worker that has not started yet looks exactly
        # like a finished one, and on 10-20 devices one fast init failure was enough
        # to declare the whole batch done. a set, not a counter, because the
        # init_failed path reports twice (explicit + finally)
        self._completed_devices = set()
        self._workers_started = False

        for device_id, task_count in zip(selected_devices, distribution):
            if task_count == 0 and not run_until_stopped:
                continue
            worker = DeviceWorker(
                device_id=device_id,
                task_count=task_count,
                task=self.task,
                shared_stats=self.shared_stats,
                resource_pool=self.resource_pool,
                run_until_stopped=run_until_stopped,
                on_progress=self._on_worker_progress,
                on_complete=self._on_worker_complete,
            )
            with self._lock:
                self.workers[device_id] = worker

        for worker in self.workers.values():
            worker.start()

        # only now does "everyone reported" mean anything; if all workers finished
        # before this point the final state has to be settled here
        with self._lock:
            self._workers_started = True
            all_done = len(self._completed_devices) >= len(self.workers)
        if all_done:
            self.is_running = False
            log.info("all workers finished")

        return True

    def _on_worker_progress(self, device_id: str, status: str):
        pass

    def _on_worker_complete(self, device_id: str, success: bool, completed: int, failed: int):
        log.info(f"[{device_id}] worker finished: {completed} ok, {failed} failed")

        with self._lock:
            self._completed_devices.add(device_id)
            all_done = self._workers_started and len(self._completed_devices) >= len(self.workers)

        if all_done:
            self.is_running = False
            log.info("all workers finished")

    def stop_all(self, reason: str = 'manual'):
        log.info(f"stopping all workers (reason={reason})")
        self._stop_requested = True
        with self._lock:
            for worker in self.workers.values():
                worker.stop(reason=reason)

    def stop_device(self, device_id: str, reason: str = 'manual') -> bool:
        worker = self._worker(device_id)
        if not worker:
            return False
        worker.stop(reason=reason)
        return True

    def pause_device(self, device_id: str) -> bool:
        worker = self._worker(device_id)
        if not worker:
            return False
        worker.pause()
        return True

    def resume_device(self, device_id: str) -> bool:
        worker = self._worker(device_id)
        if not worker:
            return False
        worker.resume()
        return True

    def get_device_logs(self, device_id: str, max_lines: int = 200) -> list:
        worker = self._worker(device_id)
        return worker.drain_logs(max_lines=max_lines) if worker else []

    def _worker(self, device_id: str) -> Optional[DeviceWorker]:
        with self._lock:
            return self.workers.get(device_id)

    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        start_time = time.time()
        while self.is_running:
            with self._lock:
                workers_list = list(self.workers.values())

            if all(not w.is_alive() for w in workers_list):
                self.is_running = False
                return True

            if timeout is not None and time.time() - start_time >= timeout:
                return False
            time.sleep(0.5)
        return True

    def get_status(self) -> Dict[str, WorkerProgress]:
        with self._lock:
            return {
                device_id: worker.get_progress()
                for device_id, worker in self.workers.items()
            }

    def get_overall_stats(self) -> dict:
        return self.shared_stats.get_overall_stats()


BAR_WIDTH = 20


def _bar(completed: int, total: int) -> str:
    if total <= 0:
        return ' ' * BAR_WIDTH
    filled = int(BAR_WIDTH * completed / total)
    bar = '=' * filled
    if filled < BAR_WIDTH:
        bar += '>'
    return bar.ljust(BAR_WIDTH)


def _device_line(device_id, progress, status_str) -> str:
    short_id = device_id[:12].ljust(12)
    return (f"[{short_id}] [{_bar(progress.completed, progress.total_tasks)}] "
            f"{progress.completed:2d}/{progress.total_tasks:2d} | "
            f"OK: {progress.successful} | FAIL: {progress.failed} | {status_str}")


def _overall_line(overall) -> str:
    return (f"Overall: {overall['completed']}/{overall['total_tasks']} "
            f"({overall['progress_percent']:.1f}%) | "
            f"Success: {overall['successful']} | Failed: {overall['failed']}")


def _status_label(progress) -> str:
    if not progress.is_running and progress.completed == progress.total_tasks:
        return "DONE"
    if progress.error:
        return f"ERROR: {progress.error[:15]}"
    label = progress.current_status[:15]
    if progress.current_stage:
        label = f"{label} ({progress.current_stage})"
    return label


def print_progress(manager):
    status = manager.get_status()
    overall = manager.get_overall_stats()

    print("\n" + "=" * 70)
    print("  PROGRESS")
    print("=" * 70)
    for device_id, progress in status.items():
        print(_device_line(device_id, progress, progress.current_status))
    print("-" * 70)
    print(_overall_line(overall))
    print("=" * 70)


def display_live_progress(manager, refresh_interval: float = 2.0):
    try:
        while manager.is_running:
            os.system('cls' if os.name == 'nt' else 'clear')

            status = manager.get_status()
            overall = manager.get_overall_stats()

            print("=" * 70)
            print("  MULTI-DEVICE RUN - LIVE PROGRESS")
            print("=" * 70)
            print(f"Total: {overall['total_tasks']} tasks | Devices: {overall['device_count']}")
            print()

            for device_id, progress in status.items():
                print(_device_line(device_id, progress, _status_label(progress)))

            print()
            print("-" * 70)
            print(_overall_line(overall))

            if overall['completed'] > 0 and overall['start_time']:
                elapsed = (datetime.now() - overall['start_time']).total_seconds()
                rate = overall['completed'] / elapsed
                remaining = overall['total_tasks'] - overall['completed']
                if rate > 0:
                    print(f"ETA: ~{int(remaining / rate / 60)} minutes")

            print("=" * 70)
            print("\nPress Ctrl+C to stop all workers after their current task")

            time.sleep(refresh_interval)

    except KeyboardInterrupt:
        print("\n\nStopping workers...")
        manager.stop_all()

    finally:
        print("\n" + "=" * 70)
        print("  FINAL RESULTS")
        print("=" * 70)
        print_progress(manager)
