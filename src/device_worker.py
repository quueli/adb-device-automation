import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from src.adb_manager import ADBManager
from src.config_loader import config
from src.logger import get_logger
from src.state_manager import StateManager

log = get_logger('worker')

TaskFn = Callable[['DeviceWorker'], Tuple[bool, Optional[str]]]


@dataclass
class WorkerProgress:
    device_id: str
    total_tasks: int
    completed: int
    successful: int
    failed: int
    current_status: str
    is_running: bool
    error: Optional[str] = None


class DeviceWorker(threading.Thread):
    def __init__(
        self,
        device_id: str,
        task_count: int,
        task: TaskFn,
        shared_stats,
        resource_pool=None,
        state_file: Optional[str] = None,
        on_complete: Optional[Callable[[str, bool, int, int], None]] = None,
    ):
        super().__init__(daemon=True)

        self.device_id = device_id
        self.task_count = task_count
        self.task = task
        self.shared_stats = shared_stats
        self.resource_pool = resource_pool
        self.state_file = state_file or f"data/state_{device_id}.json"
        self.state_manager: Optional[StateManager] = None
        self.on_complete = on_complete

        self._stop_event = threading.Event()

        self.completed = 0
        self.successful = 0
        self.failed = 0
        self.current_status = 'initializing'
        self.is_running = False
        self.error: Optional[str] = None
        self.adb: Optional[ADBManager] = None

        self.shared_stats.register_device(device_id, task_count)

    def _log(self, message: str):
        log.info(f"[{self.device_id}] {message}")

    def _update_status(self, status: str):
        self.current_status = status
        self.shared_stats.update_status(self.device_id, status)

    def stop(self):
        self._log("stop requested")
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def get_progress(self) -> WorkerProgress:
        return WorkerProgress(
            device_id=self.device_id,
            total_tasks=self.task_count,
            completed=self.completed,
            successful=self.successful,
            failed=self.failed,
            current_status=self.current_status,
            is_running=self.is_running,
            error=self.error,
        )

    def _initialize_device(self) -> bool:
        self._update_status('connecting')
        self.adb = ADBManager(self.device_id)
        if self.device_id not in self.adb.get_connected_devices():
            self.error = f"device {self.device_id} not found"
            self._log(f"ERROR: {self.error}")
            return False
        self._log("connected")
        self.state_manager = StateManager(state_file=self.state_file)
        self.state_manager.start_batch(self.task_count)
        return True

    def _run_single_task(self) -> tuple:
        try:
            self._update_status('running')
            return self.task(self)
        except Exception as e:
            self._log(f"task error: {e}")
            log.debug(traceback.format_exc())
            return False, 'OTHER_ERROR'

    def run(self):
        self.is_running = True
        self._log(f"starting: {self.task_count} tasks")

        try:
            if not self._initialize_device():
                self._update_status('init_failed')
                return

            while self.completed < self.task_count and not self.is_stopped():
                task_num = self.completed + 1
                self._update_status(f"task_{task_num}")
                self._log(f"task {task_num}/{self.task_count}")

                success, error_code = self._run_single_task()

                self.completed += 1
                if success:
                    self.successful += 1
                    self.shared_stats.record_success(self.device_id)
                else:
                    self.failed += 1
                    self.shared_stats.record_failure(self.device_id)
                    self._log(f"task {task_num} failed: {error_code}")

                if self.state_manager:
                    self.state_manager.task_completed(success)

                if self.completed < self.task_count:
                    self._update_status('cooldown')
                    time.sleep(config.task_cooldown)

            self._update_status('completed')
            self._log(f"done: {self.successful} ok, {self.failed} failed")
            if self.state_manager:
                self.state_manager.clear_state()

        except Exception as e:
            self.error = str(e)
            self._update_status('error')
            self._log(f"worker error: {e}")

        finally:
            self.is_running = False
            self.ended_at = datetime.now()
            if self.on_complete:
                self.on_complete(self.device_id, self.failed == 0, self.successful, self.failed)
