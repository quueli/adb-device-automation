import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.device_worker import DeviceWorker, TaskFn, WorkerProgress
from src.logger import get_logger
from src.thread_safe_resources import SharedStatistics

log = get_logger('manager')


@dataclass
class DeviceInfo:
    device_id: str
    status: str
    model: str = ''
    is_available: bool = True


class DeviceManager:
    def __init__(self, task: TaskFn, resource_pool=None):
        self.task = task
        self.resource_pool = resource_pool
        self.shared_stats = SharedStatistics()
        self.workers: Dict[str, DeviceWorker] = {}
        self._lock = threading.Lock()
        self.is_running = False

    @staticmethod
    def get_connected_devices() -> List[DeviceInfo]:
        devices = []
        result = subprocess.run(['adb', 'devices', '-l'], capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            model = ''
            for part in parts[2:]:
                if part.startswith('model:'):
                    model = part.split(':')[1]
                    break
            devices.append(DeviceInfo(device_id=parts[0], status=parts[1], model=model,
                                      is_available=parts[1] == 'device'))
        return devices

    @staticmethod
    def distribute_tasks(total: int, device_count: int) -> List[int]:
        """7 tasks on 3 devices -> [3, 2, 2]."""
        if device_count <= 0:
            return []
        base = total // device_count
        remainder = total % device_count
        return [base + (1 if i < remainder else 0) for i in range(device_count)]

    def start_parallel(self, selected_devices: List[str], total_tasks: int) -> bool:
        if self.is_running:
            log.warning("a batch is already running")
            return False
        if not selected_devices:
            log.warning("no devices selected")
            return False

        distribution = self.distribute_tasks(total_tasks, len(selected_devices))
        log.info(f"starting on {len(selected_devices)} devices, split {distribution}")

        self.is_running = True
        self.workers.clear()
        for device_id, task_count in zip(selected_devices, distribution):
            if task_count == 0:
                continue
            self.workers[device_id] = DeviceWorker(
                device_id=device_id,
                task_count=task_count,
                task=self.task,
                shared_stats=self.shared_stats,
                resource_pool=self.resource_pool,
                on_complete=self._on_worker_complete,
            )
        for worker in self.workers.values():
            worker.start()
        return True

    def _on_worker_complete(self, device_id: str, success: bool, completed: int, failed: int):
        log.info(f"[{device_id}] worker finished: {completed} ok, {failed} failed")
        if all(not w.is_running for w in self.workers.values()):
            self.is_running = False

    def stop_all(self):
        with self._lock:
            for worker in self.workers.values():
                worker.stop()

    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        start_time = time.time()
        while self.is_running:
            if timeout is not None and time.time() - start_time >= timeout:
                return False
            time.sleep(0.5)
        return True

    def get_status(self) -> Dict[str, WorkerProgress]:
        with self._lock:
            return {d: w.get_progress() for d, w in self.workers.items()}

    def get_overall_stats(self) -> dict:
        return self.shared_stats.get_overall_stats()
