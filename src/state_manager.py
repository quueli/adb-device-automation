import json
import os
from datetime import datetime
from typing import Optional

from src.logger import get_logger

log = get_logger('state')


class StateManager:
    def __init__(self, state_file: str = 'data/state.json'):
        self.state_file = state_file
        self.state = self.load_state()

    def _default_state(self) -> dict:
        return {
            'mode': 'fixed',
            'total_tasks': 0,
            'completed': 0,
            'failed': 0,
            'current_task': 0,
            'last_update': None,
        }

    def load_state(self) -> dict:
        # a kill mid-write leaves a truncated file, that must not stop the worker
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError, ValueError):
                log.warning(f"corrupt state file {self.state_file}, starting fresh")
        return self._default_state()

    def save_state(self):
        # temp + replace, the resume file is never half written
        self.state['last_update'] = datetime.now().isoformat()
        os.makedirs(os.path.dirname(self.state_file) or '.', exist_ok=True)
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_file)

    def start_batch(self, total: int, mode: str = 'fixed'):
        self.state['mode'] = mode
        self.state['total_tasks'] = total if mode != 'continuous' else 0
        self.state['completed'] = 0
        self.state['failed'] = 0
        self.state['current_task'] = 0
        self.save_state()

    def task_completed(self, success: bool):
        if success:
            self.state['completed'] += 1
        else:
            self.state['failed'] += 1
        self.state['current_task'] += 1
        self.save_state()

    def can_resume(self) -> bool:
        mode = self.state.get('mode', 'fixed')
        current = self.state.get('current_task', 0)
        if mode == 'continuous':
            return current > 0
        return current < self.state.get('total_tasks', 0)

    def resume_counters(self, cap: Optional[int] = None) -> tuple:
        done = self.state.get('current_task', 0)
        if cap is not None:
            done = min(done, cap)
        return done, self.state.get('completed', 0), self.state.get('failed', 0)

    def clear_state(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
