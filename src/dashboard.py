import os
import time
from datetime import datetime

BAR_WIDTH = 20


def _device_line(device_id, progress, status_str) -> str:
    if progress.total_tasks > 0:
        filled = int(BAR_WIDTH * progress.completed / progress.total_tasks)
        bar = ('=' * filled + ('>' if filled < BAR_WIDTH else '')).ljust(BAR_WIDTH)
    else:
        bar = ' ' * BAR_WIDTH
    return (f"[{device_id[:12].ljust(12)}] [{bar}] "
            f"{progress.completed:2d}/{progress.total_tasks:2d} | "
            f"OK: {progress.successful} | FAIL: {progress.failed} | {status_str}")


def _overall_line(overall) -> str:
    return (f"Overall: {overall['completed']}/{overall['total_tasks']} "
            f"({overall['progress_percent']:.1f}%) | "
            f"Success: {overall['successful']} | Failed: {overall['failed']}")


def _status_label(progress) -> str:
    if not progress.is_running and progress.completed == progress.total_tasks:
        return "DONE"
    if progress.error:
        return f"ERROR: {progress.error[:15]}"
    label = progress.current_status[:15]
    if progress.current_stage:
        label = f"{label} ({progress.current_stage})"
    return label


def print_progress(manager):
    status = manager.get_status()
    overall = manager.get_overall_stats()

    print("\n" + "=" * 70)
    print("  PROGRESS")
    print("=" * 70)
    for device_id, progress in status.items():
        print(_device_line(device_id, progress, progress.current_status))
    print("-" * 70)
    print(_overall_line(overall))
    print("=" * 70)


def display_live_progress(manager, refresh_interval: float = 2.0):
    try:
        while manager.is_running:
            os.system('cls' if os.name == 'nt' else 'clear')

            status = manager.get_status()
            overall = manager.get_overall_stats()

            print("=" * 70)
            print("  MULTI-DEVICE RUN - LIVE PROGRESS")
            print("=" * 70)
            print(f"Total: {overall['total_tasks']} tasks | Devices: {overall['device_count']}")
            print()

            for device_id, progress in status.items():
                print(_device_line(device_id, progress, _status_label(progress)))

            print()
            print("-" * 70)
            print(_overall_line(overall))

            if overall['completed'] > 0 and overall['start_time']:
                elapsed = (datetime.now() - overall['start_time']).total_seconds()
                rate = overall['completed'] / elapsed
                remaining = overall['total_tasks'] - overall['completed']
                if rate > 0:
                    print(f"ETA: ~{int(remaining / rate / 60)} minutes")

            print("=" * 70)
            print("\nPress Ctrl+C to stop all workers after their current task")

            time.sleep(refresh_interval)

    except KeyboardInterrupt:
        print("\n\nStopping workers...")
        manager.stop_all()

    finally:
        print("\n" + "=" * 70)
        print("  FINAL RESULTS")
        print("=" * 70)
        print_progress(manager)
