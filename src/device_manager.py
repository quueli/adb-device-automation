import threading
import time
from typing import Dict, List, Optional

from src.device_worker import DeviceWorker, TaskFn
from src.logger import get_logger
from src.thread_safe_resources import SharedStatistics
from src.worker_status import WorkerProgress

log = get_logger('manager')


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
