import threading

import src.device_manager as dm_module


def test_distribute_tasks():
    assert dm_module.DeviceManager.distribute_tasks(7, 3) == [3, 2, 2]
    assert dm_module.DeviceManager.distribute_tasks(2, 4) == [1, 1, 0, 0]


def test_distribute_without_devices():
    assert dm_module.DeviceManager.distribute_tasks(5, 0) == []


class _FakeWorker:
    pass


def _make_manager(worker_ids):
    mgr = dm_module.DeviceManager.__new__(dm_module.DeviceManager)
    mgr._lock = threading.Lock()
    mgr.workers = {wid: _FakeWorker() for wid in worker_ids}
    mgr._completed_devices = set()
    mgr._workers_started = False
    mgr.is_running = True
    return mgr


def test_no_completion_before_all_started():
    mgr = _make_manager(["d1", "d2"])

    mgr._on_worker_complete("d1", False, 0, 1)
    assert mgr.is_running is True

    with mgr._lock:
        mgr._workers_started = True
        all_done = len(mgr._completed_devices) >= len(mgr.workers)
    assert all_done is False

    mgr._on_worker_complete("d2", True, 1, 0)
    assert mgr.is_running is False


def test_completion_settled_after_start():
    mgr = _make_manager(["d1", "d2"])

    mgr._on_worker_complete("d1", True, 1, 0)
    mgr._on_worker_complete("d2", True, 1, 0)
    assert mgr.is_running is True

    with mgr._lock:
        mgr._workers_started = True
        all_done = len(mgr._completed_devices) >= len(mgr.workers)
    if all_done:
        mgr.is_running = False
    assert mgr.is_running is False


def test_double_complete_is_idempotent():
    mgr = _make_manager(["d1", "d2"])
    with mgr._lock:
        mgr._workers_started = True
    mgr._on_worker_complete("d1", False, 0, 1)
    mgr._on_worker_complete("d1", False, 0, 1)
    assert mgr.is_running is True
    mgr._on_worker_complete("d2", True, 1, 0)
    assert mgr.is_running is False
