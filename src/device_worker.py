import time
import traceback
from datetime import datetime
from typing import Callable, Optional, Tuple

from src.adb import ADBManager
from src.config_loader import config
from src.state_manager import StateManager
from src.worker_status import WorkerStatus, log

STOPPED = 'STOPPED'          # task backed out at a safe point after stop(), not counted
DEVICE_LOST = 'DEVICE_LOST'  # device went offline, the worker ends after this attempt

TaskFn = Callable[['DeviceWorker'], Tuple[bool, Optional[str]]]


class DeviceWorker(WorkerStatus):
    def __init__(
        self,
        device_id: str,
        task_count: int,
        task: TaskFn,
        shared_stats,
        resource_pool=None,
        run_until_stopped: bool = False,
        stop_on_empty_pool: bool = True,
        state_file: Optional[str] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        on_complete: Optional[Callable[[str, bool, int, int], None]] = None,
    ):
        super().__init__(device_id, task_count, shared_stats, run_until_stopped, on_progress)

        self.task = task
        self.resource_pool = resource_pool
        self.stop_on_empty_pool = stop_on_empty_pool
        self.state_file = state_file or f"data/state_{device_id}.json"
        self.state_manager: Optional[StateManager] = None
        self.on_complete = on_complete
        self.adb: Optional[ADBManager] = None

    def _initialize_device(self) -> bool:
        try:
            self._update_status('connecting')
            self.adb = ADBManager(self.device_id)
            if self.device_id not in self.adb.get_connected_devices():
                self.error = f"device {self.device_id} not found"
                self._log(f"ERROR: {self.error}")
                return False
            self._log("connected")

            self.state_manager = StateManager(state_file=self.state_file)
            if self.state_manager.can_resume():
                cap = None if self.run_until_stopped else self.task_count
                self.completed, self.successful, self.failed = self.state_manager.resume_counters(cap)
                self._log(f"unfinished batch: {self.completed} done "
                          f"({self.successful} ok, {self.failed} failed), resuming")
            elif self.run_until_stopped:
                self.state_manager.start_batch(0, mode='continuous')
            else:
                self.state_manager.start_batch(self.task_count)
            return True

        except Exception as e:
            self.error = f"device init failed: {e}"
            self._log(f"ERROR: {self.error}")
            log.debug(traceback.format_exc())
            return False

    def _run_single_task(self) -> tuple:
        try:
            self._update_status('running')
            return self.task(self)
        except Exception as e:
            self._log(f"task error: {e}")
            log.debug(traceback.format_exc())
            return False, 'OTHER_ERROR'

    def _resource_exhausted_reason(self) -> Optional[str]:
        if self.stop_on_empty_pool and self.resource_pool is not None:
            try:
                if self.resource_pool.get_available_count() <= 0:
                    return 'empty_pool'
            except Exception:
                pass
        return None

    def _more_to_do(self) -> bool:
        return self.run_until_stopped or self.completed < self.task_count

    def _save_state(self, success: bool):
        if not self.state_manager:
            return
        try:
            self.state_manager.task_completed(success)
        except Exception as e:
            self._log(f"could not save state: {e}")

    def run(self):
        self.is_running = True
        self.started_at = datetime.now()
        self.ended_at = None
        if self.run_until_stopped:
            self._log("starting: continuous mode (until stop or empty pool)")
        else:
            self._log(f"starting: {self.task_count} tasks")

        try:
            if not self._initialize_device():
                self._update_status('init_failed')
                self.is_running = False
                if self.on_complete:
                    self.on_complete(self.device_id, False, 0, self.task_count)
                return

            while self._more_to_do() and not self.is_stopped():
                self.wait_if_paused()
                if self.is_stopped():
                    break

                if self.run_until_stopped:
                    reason = self._resource_exhausted_reason()
                    if reason:
                        self._log(f"nothing left in the pool ({reason}), stopping")
                        self.stop(reason=reason)
                        break

                task_num = self.completed + 1
                total_label = 'inf' if self.run_until_stopped else str(self.task_count)
                self._update_status(f"task_{task_num}")
                self._log(f"task {task_num}/{total_label}")

                success, error_code = self._run_single_task()

                if error_code == STOPPED:
                    self._log("task interrupted by stop request, not counted")
                    break

                self.completed += 1
                if success:
                    self.successful += 1
                    self.shared_stats.record_success(self.device_id)
                    self._log(f"task {task_num} ok")
                else:
                    self.failed += 1
                    self.shared_stats.record_failure(self.device_id)
                    self._log(f"task {task_num} failed: {error_code}")

                self._save_state(success)

                if error_code == DEVICE_LOST:
                    self.stop(reason='device_lost')
                    break

                if self._more_to_do() and not self.is_stopped():
                    self._update_status('cooldown')
                    time.sleep(config.task_cooldown)

            if self.is_stopped():
                self._update_status('stopped')
                self._log(f"stopped (reason={self.stop_reason})")
                # the state file stays, the next start resumes from it
            else:
                self._update_status('completed')
                self._log(f"done: {self.successful} ok, {self.failed} failed")
                if self.state_manager and not self.run_until_stopped:
                    self.state_manager.clear_state()

        except Exception as e:
            self.error = str(e)
            self._update_status('error')
            self._log(f"worker error: {e}")
            log.debug(traceback.format_exc())

        finally:
            self.is_running = False
            self.ended_at = datetime.now()
            if self.on_complete:
                try:
                    overall_success = self.failed == 0 and self.successful > 0
                    self.on_complete(self.device_id, overall_success, self.successful, self.failed)
                except Exception:
                    pass
