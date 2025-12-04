import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

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


class ThreadSafeResourcePool:
    def __init__(self, pool_file: str = 'data/pool.txt'):
        self.pool_file = pool_file
        self._lock = threading.Lock()
        self._assigned: Dict[str, Resource] = {}
        self._items: List[Resource] = self._load_from_file()

    def _load_from_file(self) -> List[Resource]:
        if not os.path.exists(self.pool_file):
            return []
        with open(self.pool_file, 'r', encoding='utf-8') as f:
            return [Resource(line.strip()) for line in f if line.strip() and not line.startswith('#')]

    def _save(self):
        with open(self.pool_file, 'w', encoding='utf-8') as f:
            for item in self._items:
                f.write(item.value + '\n')

    def acquire(self, device_id: str) -> Optional[Resource]:
        with self._lock:
            if device_id in self._assigned:
                return self._assigned[device_id]
            if not self._items:
                return None
            item = self._items.pop(0)
            item.assigned_to = device_id
            item.assigned_at = datetime.now()
            self._assigned[device_id] = item
            self._save()
            return item

    def release(self, device_id: str, remove_permanently: bool = False):
        with self._lock:
            if device_id not in self._assigned:
                return
            item = self._assigned.pop(device_id)
            if not remove_permanently:
                item.assigned_to = None
                self._items.append(item)
            self._save()

    def get_available_count(self) -> int:
        with self._lock:
            return len(self._items)


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
                self._device_stats[device_id].current_status = status

    def record_success(self, device_id: str):
        with self._lock:
            if device_id in self._device_stats:
                stats = self._device_stats[device_id]
                stats.completed += 1
                stats.successful += 1

    def record_failure(self, device_id: str):
        with self._lock:
            if device_id in self._device_stats:
                stats = self._device_stats[device_id]
                stats.completed += 1
                stats.failed += 1

    def get_overall_stats(self) -> dict:
        with self._lock:
            total_tasks = sum(s.total_tasks for s in self._device_stats.values())
            completed = sum(s.completed for s in self._device_stats.values())
            return {
                'total_tasks': total_tasks,
                'completed': completed,
                'successful': sum(s.successful for s in self._device_stats.values()),
                'failed': sum(s.failed for s in self._device_stats.values()),
                'progress_percent': (completed / total_tasks * 100) if total_tasks > 0 else 0,
                'start_time': self._start_time,
                'device_count': len(self._device_stats),
            }
