import pytest

import src.device_worker as dw_module
from src.device_worker import DEVICE_LOST, STOPPED, DeviceWorker
from src.state_manager import StateManager


class FakeSharedStats:
    def __init__(self):
        self.successes = 0
        self.failures = 0

    def register_device(self, *a, **k):
        pass

    def update_status(self, *a, **k):
        pass

    def record_success(self, *a, **k):
        self.successes += 1

    def record_failure(self, *a, **k):
        self.failures += 1


class FakePool:
    def __init__(self, count=10):
        self._count = count

    def get_available_count(self):
        return self._count


def ok_task(worker):
    return True, None


def make_worker(**overrides):
    defaults = dict(
        device_id="DEV1",
        task_count=5,
        task=ok_task,
        shared_stats=FakeSharedStats(),
    )
    defaults.update(overrides)
    return DeviceWorker(**defaults)


@pytest.fixture
def fake_device(monkeypatch, tmp_path):
    monkeypatch.setattr(dw_module.ADBManager, 'get_connected_devices', staticmethod(lambda: ['DEV1']))
    monkeypatch.setenv('TASK_COOLDOWN', '0')
    return str(tmp_path / "state.json")


def test_pool_with_items():
    w = make_worker(resource_pool=FakePool(3))
    assert w._resource_exhausted_reason() is None


def test_resource_exhausted_empty_pool():
    w = make_worker(resource_pool=FakePool(0))
    assert w._resource_exhausted_reason() == "empty_pool"


def test_empty_pool_check_disabled():
    w = make_worker(resource_pool=FakePool(0), stop_on_empty_pool=False)
    assert w._resource_exhausted_reason() is None


def test_no_pool_configured():
    w = make_worker()
    assert w._resource_exhausted_reason() is None


def test_stop_sets_event_and_reason():
    w = make_worker()
    assert w.is_stopped() is False
    w.stop(reason="manual")
    assert w.is_stopped() is True
    assert w.stop_reason == "manual"


def test_stop_first_reason_wins():
    w = make_worker()
    w.stop(reason="empty_pool")
    w.stop(reason="manual")
    assert w.stop_reason == "empty_pool"


def test_progress_fields_fixed_mode():
    w = make_worker(task_count=3)
    progress = w.get_progress()
    assert progress.total_tasks == 3
    assert progress.run_until_stopped is False
    assert progress.stop_reason is None
    assert progress.current_stage == ""


def test_set_stage_in_progress():
    w = make_worker()
    w.set_stage("login")
    progress = w.get_progress()
    assert progress.current_stage == "login"
    assert progress.stage_started_at is not None


def test_state_file_default_is_per_device():
    w = make_worker(device_id="emulator-5554")
    assert "emulator-5554" in w.state_file


def test_state_file_can_be_overridden(tmp_path):
    custom = str(tmp_path / "custom_state.json")
    w = make_worker(state_file=custom)
    assert w.state_file == custom


def test_run_counts_results(fake_device):
    results = iter([(True, None), (False, 'SOME_ERROR'), (True, None)])
    stats = FakeSharedStats()
    w = make_worker(task_count=3, task=lambda worker: next(results), shared_stats=stats, state_file=fake_device)

    w.run()

    assert (w.completed, w.successful, w.failed) == (3, 2, 1)
    assert (stats.successes, stats.failures) == (2, 1)
    assert w.current_status == "completed"
    assert StateManager(fake_device).can_resume() is False


def test_stopped_result_is_not_counted(fake_device):
    attempts = []

    def task(worker):
        attempts.append(1)
        worker.stop()
        return False, STOPPED

    w = make_worker(task_count=5, task=task, state_file=fake_device)
    w.run()

    assert len(attempts) == 1
    assert w.completed == 0
    assert w.current_status == "stopped"
    assert StateManager(fake_device).can_resume() is True


def test_resume_from_saved_counters(fake_device):
    previous = StateManager(fake_device)
    previous.start_batch(4)
    previous.task_completed(True)
    previous.task_completed(False)

    calls = []
    w = make_worker(task_count=4, task=lambda worker: calls.append(1) or (True, None), state_file=fake_device)
    w.run()

    assert len(calls) == 2
    assert (w.completed, w.successful, w.failed) == (4, 3, 1)


def test_device_lost_ends_run(fake_device):
    calls = []

    def task(worker):
        calls.append(1)
        return False, DEVICE_LOST

    w = make_worker(task_count=5, task=task, state_file=fake_device)
    w.run()

    assert len(calls) == 1
    assert w.stop_reason == "device_lost"
    assert w.failed == 1


def test_init_failure_reports_completion(monkeypatch):
    monkeypatch.setattr(dw_module.ADBManager, 'get_connected_devices', staticmethod(lambda: []))
    reports = []
    w = make_worker(task_count=5, on_complete=lambda *args: reports.append(args))

    w.run()

    assert w.current_status == "init_failed"
    assert "not found" in w.error
    assert reports[0] == ("DEV1", False, 0, 5)


def test_task_exception_counts_as_failure(fake_device):
    def task(worker):
        raise RuntimeError("boom")

    w = make_worker(task_count=1, task=task, state_file=fake_device)
    w.run()

    assert (w.completed, w.failed) == (1, 1)
    assert w.current_status == "completed"
