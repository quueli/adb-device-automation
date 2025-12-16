import contextlib
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from src.file_lock import FileLock
from src.logger import get_logger

log = get_logger('resources')


@dataclass
class Resource:
    value: str
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None


@dataclass
class DeviceStats:
    device_id: str
    total_tasks: int = 0
    completed: int = 0
    successful: int = 0
    failed: int = 0
    current_status: str = 'idle'
    start_time: Optional[datetime] = None
    last_update: Optional[datetime] = None


# one line of the pool file is one resource, handed to one device at a time
class ThreadSafeResourcePool:
    def __init__(self, pool_file: str = 'data/pool.txt', use_file_lock: bool = True,
                 lock_timeout: float = 10.0):
        self.pool_file = pool_file
        self.use_file_lock = use_file_lock
        self.lock_timeout = lock_timeout
        self.lock_file = f"{pool_file}.lock"
        self._lock = threading.Lock()
        self._assigned: Dict[str, Resource] = {}
        self._items: List[Resource] = self._load_from_file()

    def _load_from_file(self) -> List[Resource]:
        if not os.path.exists(self.pool_file):
            return []
        try:
            with open(self.pool_file, 'r', encoding='utf-8') as f:
                return [Resource(line.strip()) for line in f if line.strip() and not line.startswith('#')]
        except OSError as e:
            log.error(f"cannot read {self.pool_file}: {e}")
            return []

    def _save(self, items: List[Resource]):
        try:
            with open(self.pool_file, 'w', encoding='utf-8') as f:
                for item in items:
                    f.write(item.value + '\n')
        except OSError as e:
            log.error(f"cannot write {self.pool_file}: {e}")

    def _file_lock(self):
        if self.use_file_lock:
            return FileLock(self.lock_file, timeout=self.lock_timeout)
        return contextlib.nullcontext()

    def _fresh_items(self) -> List[Resource]:
        # another process may have taken lines since this one loaded the file
        return self._load_from_file() if self.use_file_lock else self._items

    def acquire(self, device_id: str) -> Optional[Resource]:
        with self._lock:
            if device_id in self._assigned:
                return self._assigned[device_id]
            try:
                with self._file_lock():
                    self._items = self._fresh_items()
                    if not self._items:
                        return None
                    item = self._items.pop(0)
                    item.assigned_to = device_id
                    item.assigned_at = datetime.now()
                    self._assigned[device_id] = item
                    self._save(self._items)
                    return item
            except TimeoutError:
                log.warning(f"timeout waiting for the lock on {self.pool_file}")
                return None

    def release(self, device_id: str, remove_permanently: bool = False):
        with self._lock:
            if device_id not in self._assigned:
                return
            item = self._assigned.pop(device_id)
            try:
                with self._file_lock():
                    items = self._fresh_items()
                    if not remove_permanently:
                        item.assigned_to = None
                        item.assigned_at = None
                        items.append(item)
                    self._items = items
                    self._save(self._items)
            except TimeoutError:
                log.warning(f"timeout waiting for the lock on {self.pool_file}")

    def remove(self, value: str):
        with self._lock:
            try:
                with self._file_lock():
                    self._items = [i for i in self._fresh_items() if i.value != value]
                    for device_id, item in list(self._assigned.items()):
                        if item.value == value:
                            del self._assigned[device_id]
                    self._save(self._items)
            except TimeoutError:
                log.warning(f"timeout waiting for the lock on {self.pool_file}")

    def get_available_count(self) -> int:
        with self._lock:
            try:
                with self._file_lock():
                    return len(self._fresh_items())
            except TimeoutError:
                return 0

    def get_total_count(self) -> int:
        with self._lock:
            return len(self._items) + len(self._assigned)


class SharedStatistics:
    def __init__(self):
        self._lock = threading.Lock()
        self._device_stats: Dict[str, DeviceStats] = {}
        self._start_time: Optional[datetime] = None

    def register_device(self, device_id: str, task_count: int):
        with self._lock:
            self._device_stats[device_id] = DeviceStats(
                device_id=device_id,
                total_tasks=task_count,
                start_time=datetime.now(),
            )
            if self._start_time is None:
                self._start_time = datetime.now()

    def update_status(self, device_id: str, status: str):
        with self._lock:
            if device_id in self._device_stats:
                stats = self._device_stats[device_id]
                stats.current_status = status
                stats.last_update = datetime.now()

    def record_success(self, device_id: str):
        with self._lock:
            if device_id in self._device_stats:
                stats = self._device_stats[device_id]
                stats.completed += 1
                stats.successful += 1
                stats.last_update = datetime.now()

    def record_failure(self, device_id: str):
        with self._lock:
            if device_id in self._device_stats:
                stats = self._device_stats[device_id]
                stats.completed += 1
                stats.failed += 1
                stats.last_update = datetime.now()

    def get_device_stats(self, device_id: str) -> Optional[DeviceStats]:
        with self._lock:
            if device_id in self._device_stats:
                return DeviceStats(**vars(self._device_stats[device_id]))
            return None

    def get_all_stats(self) -> Dict[str, DeviceStats]:
        with self._lock:
            return {
                device_id: DeviceStats(**vars(stats))
                for device_id, stats in self._device_stats.items()
            }

    def get_overall_stats(self) -> dict:
        with self._lock:
            total_tasks = sum(s.total_tasks for s in self._device_stats.values())
            completed = sum(s.completed for s in self._device_stats.values())
            successful = sum(s.successful for s in self._device_stats.values())
            failed = sum(s.failed for s in self._device_stats.values())
            return {
                'total_tasks': total_tasks,
                'completed': completed,
                'successful': successful,
                'failed': failed,
                'progress_percent': (completed / total_tasks * 100) if total_tasks > 0 else 0,
                'start_time': self._start_time,
                'device_count': len(self._device_stats),
            }

    def is_all_completed(self) -> bool:
        with self._lock:
            for stats in self._device_stats.values():
                if stats.completed < stats.total_tasks:
                    return False
            return len(self._device_stats) > 0
