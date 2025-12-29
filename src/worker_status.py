import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from src.logger import get_logger

log = get_logger('worker')


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
    current_stage: str = ''
    stage_started_at: Optional[datetime] = None
    stop_reason: Optional[str] = None
    run_until_stopped: bool = False
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    is_paused: bool = False


class WorkerStatus(threading.Thread):
    def __init__(
        self,
        device_id: str,
        task_count: int,
        shared_stats,
        run_until_stopped: bool = False,
        on_progress: Optional[Callable[[str, str], None]] = None,
    ):
        super().__init__(daemon=True)

        self.device_id = device_id
        self.task_count = task_count
        self.shared_stats = shared_stats
        self.run_until_stopped = run_until_stopped
        self.on_progress = on_progress

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        self.completed = 0
        self.successful = 0
        self.failed = 0
        self.current_status = 'initializing'
        self.current_stage = ''
        self.stage_started_at: Optional[datetime] = None
        self.is_running = False
        self.error: Optional[str] = None
        self.stop_reason: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.ended_at: Optional[datetime] = None

        # per-device tail for a gui; bounded so it does not grow when nobody reads it
        self.log_queue: "queue.Queue[str]" = queue.Queue(maxsize=1000)

        self.shared_stats.register_device(device_id, task_count)

    def _log(self, message: str):
        log.info(f"[{self.device_id}] {message}")
        try:
            self.log_queue.put_nowait(f"{datetime.now().strftime('%H:%M:%S')} {message}")
        except queue.Full:
            pass

    def _update_status(self, status: str):
        self.current_status = status
        self.shared_stats.update_status(self.device_id, status)
        if self.on_progress:
            try:
                self.on_progress(self.device_id, status)
            except Exception:
                pass

    def set_stage(self, stage: str) -> str:
        self.current_stage = stage
        self.stage_started_at = datetime.now()
        return stage

    def stop(self, reason: str = 'manual'):
        self._log(f"stop requested (reason={reason})")
        if self.stop_reason is None:
            self.stop_reason = reason
        self._stop_event.set()
        self._pause_event.set()

    def pause(self):
        self._log("pausing")
        self._pause_event.clear()

    def resume(self):
        self._log("resuming")
        self._pause_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def wait_if_paused(self):
        while not self._pause_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.1)

    def drain_logs(self, max_lines: int = 200) -> list:
        lines = []
        for _ in range(max_lines):
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return lines

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
            current_stage=self.current_stage,
            stage_started_at=self.stage_started_at,
            stop_reason=self.stop_reason,
            run_until_stopped=self.run_until_stopped,
            started_at=self.started_at,
            ended_at=self.ended_at,
            is_paused=((not self._pause_event.is_set()) and self.is_running),
        )
