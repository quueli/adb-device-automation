import json
import os
from datetime import datetime

from src.logger import get_logger

log = get_logger('state')


class StateManager:
    def __init__(self, state_file: str = 'data/state.json'):
        self.state_file = state_file
        self.state = self.load_state()

    def _default_state(self) -> dict:
        return {
            'total_tasks': 0,
            'completed': 0,
            'failed': 0,
            'current_task': 0,
            'last_update': None,
        }

    def load_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return self._default_state()

    def save_state(self):
        self.state['last_update'] = datetime.now().isoformat()
        os.makedirs(os.path.dirname(self.state_file) or '.', exist_ok=True)
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2)

    def start_batch(self, total: int):
        self.state['total_tasks'] = total
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
        return self.state.get('current_task', 0) < self.state.get('total_tasks', 0)

    def clear_state(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
