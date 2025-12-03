import os

from src.state_manager import StateManager


def test_fresh_state_cannot_resume(tmp_path):
    sm = StateManager(state_file=str(tmp_path / "state.json"))
    assert sm.can_resume() is False


def test_start_batch_and_progress(tmp_path):
    sm = StateManager(state_file=str(tmp_path / "state.json"))
    sm.start_batch(5)
    sm.task_completed(True)
    sm.task_completed(False)

    assert sm.state["current_task"] == 2
    assert sm.state["completed"] == 1
    assert sm.state["failed"] == 1
    assert sm.can_resume() is True


def test_state_persists_across_instances(tmp_path):
    path = str(tmp_path / "state.json")
    sm = StateManager(state_file=path)
    sm.start_batch(5)
    sm.task_completed(True)

    sm2 = StateManager(state_file=path)
    assert sm2.can_resume() is True
    assert sm2.state["current_task"] == 1
    assert sm2.state["completed"] == 1


def test_completed_batch_cannot_resume(tmp_path):
    sm = StateManager(state_file=str(tmp_path / "state.json"))
    sm.start_batch(2)
    sm.task_completed(True)
    sm.task_completed(True)
    assert sm.can_resume() is False


def test_clear_state_removes_file(tmp_path):
    path = str(tmp_path / "state.json")
    sm = StateManager(state_file=path)
    sm.start_batch(3)
    sm.task_completed(True)
    assert os.path.exists(path)
    sm.clear_state()
    assert not os.path.exists(path)


def test_corrupt_state_falls_back(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"mode": "fixed", "total_tasks": 5, "curr', encoding="utf-8")
    sm = StateManager(state_file=str(path))
    assert sm.can_resume() is False
    assert sm.state["current_task"] == 0


def test_per_device_files_isolated(tmp_path):
    sm_a = StateManager(state_file=str(tmp_path / "state_DEV_A.json"))
    sm_b = StateManager(state_file=str(tmp_path / "state_DEV_B.json"))

    sm_a.start_batch(10)
    sm_a.task_completed(True)
    sm_a.task_completed(True)

    sm_b.start_batch(3)
    sm_b.task_completed(False)

    assert sm_a.state["current_task"] == 2
    assert sm_b.state["current_task"] == 1
    assert sm_a.state["completed"] == 2
    assert sm_b.state["failed"] == 1


def test_continuous_resume(tmp_path):
    path = str(tmp_path / "state.json")
    sm = StateManager(state_file=path)
    sm.start_batch(0, mode='continuous')
    assert sm.can_resume() is False

    sm.task_completed(True)
    sm.task_completed(False)
    assert sm.can_resume() is True

    sm2 = StateManager(state_file=path)
    assert sm2.can_resume() is True
    assert sm2.state.get("mode") == "continuous"
