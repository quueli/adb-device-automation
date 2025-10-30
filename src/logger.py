import logging
import os
from datetime import datetime

ROOT_NAME = 'automation'
FILE_FORMAT = '%(asctime)s | %(levelname)-8s | %(message)s'
CONSOLE_FORMAT = '%(asctime)s %(message)s'


def get_logger(name: str = '') -> logging.Logger:
    return logging.getLogger(f'{ROOT_NAME}.{name}' if name else ROOT_NAME)


def setup_logging(log_dir: str = 'logs', console: bool = True, level: int = logging.DEBUG) -> str:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    root = logging.getLogger(ROOT_NAME)
    root.setLevel(level)
    # a second run in the same process must not print every line twice
    if root.handlers:
        root.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt='%Y-%m-%d %H:%M:%S'))
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt='%H:%M:%S'))
        root.addHandler(stream)

    return log_file
