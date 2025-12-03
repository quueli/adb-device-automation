import os
import time


# msvcrt on windows, fcntl elsewhere
class FileLock:
    def __init__(self, lock_path: str, timeout: float = 10.0, poll_interval: float = 0.1):
        self.lock_path = lock_path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fh = None

    def __enter__(self):
        lock_dir = os.path.dirname(self.lock_path)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)

        self._fh = open(self.lock_path, "a+")
        start_time = time.time()

        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except Exception:
                if time.time() - start_time >= self.timeout:
                    # __exit__ does not run when __enter__ raises, so close it here
                    try:
                        self._fh.close()
                    finally:
                        self._fh = None
                    raise TimeoutError(f"Timeout acquiring lock: {self.lock_path}")
                time.sleep(self.poll_interval)

        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            if self._fh:
                self._fh.close()
                self._fh = None
