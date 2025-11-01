import os

from dotenv import load_dotenv


class Config:
    _instance = None
    _loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not Config._loaded:
            load_dotenv('.env')
            Config._loaded = True

    @staticmethod
    def get_int(key: str, default: int) -> int:
        try:
            return int(os.getenv(key, default))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def get_float(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, default))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def get_str(key: str, default: str = '') -> str:
        return os.getenv(key, default)

    @staticmethod
    def get_bool(key: str, default: bool) -> bool:
        raw = os.getenv(key)
        if raw is None:
            return default
        return raw.strip().lower() in ('1', 'true', 'yes', 'on')

    @property
    def adb_command_timeout(self) -> float:
        return self.get_float('ADB_COMMAND_TIMEOUT', 30.0)

    @property
    def ui_element_wait_timeout(self) -> int:
        return self.get_int('UI_ELEMENT_WAIT_TIMEOUT', 10)

    @property
    def app_launch_delay(self) -> float:
        return self.get_float('APP_LAUNCH_DELAY', 3.0)

    @property
    def button_tap_delay(self) -> float:
        return self.get_float('BUTTON_TAP_DELAY', 1.0)

    @property
    def screen_transition_delay(self) -> float:
        return self.get_float('SCREEN_TRANSITION_DELAY', 2.0)

    @property
    def task_cooldown(self) -> float:
        return self.get_float('TASK_COOLDOWN', 5.0)

    @property
    def run_mode(self) -> str:
        return self.get_str('RUN_MODE', 'fixed').strip().lower()


config = Config()
