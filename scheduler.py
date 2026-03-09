"""
scheduler.py
────────────
Schedule auto-downloads to run at specific times or intervals.
 - Cron-style scheduling
 - Run once / repeat daily / repeat hourly
 - Persist schedules across restarts
 - Thread-safe, non-blocking background runner
"""

import json
import time
import threading
from pathlib import Path
from typing import Callable, Optional

SCHED_FILE = Path("/tmp/mediavault_schedules.json")

_schedules: list[dict] = []
_lock = threading.Lock()
_runner: Optional[threading.Thread] = None
_running = False
_callbacks: dict[str, Callable] = {}   # event_id -> callback


def _load():
    global _schedules
    try:
        if SCHED_FILE.exists():
            _schedules = json.loads(SCHED_FILE.read_text())
    except Exception:
        _schedules = []


def _save():
    try:
        SCHED_FILE.write_text(json.dumps(_schedules, indent=2))
    except Exception as e:
        print(f"[SCHED] Save error: {e}")


def add_schedule(
    name: str,
    genre_key: str,
    hour: int,
    minute: int = 0,
    repeat: str = "daily",  # "once", "daily", "hourly", "every_N_min"
    interval_min: int = 60,
    count: int = 5,
    enabled: bool = True,
) -> str:
    """Add a scheduled auto-download job. Returns schedule ID."""
    import uuid
    sched_id = str(uuid.uuid4())[:8]
    entry = {
        "id":           sched_id,
        "name":         name,
        "genre_key":    genre_key,
        "hour":         hour,
        "minute":       minute,
        "repeat":       repeat,
        "interval_min": interval_min,
        "count":        count,
        "enabled":      enabled,
        "last_run":     0,
        "run_count":    0,
        "next_run_str": _calc_next_str(hour, minute, repeat, interval_min),
    }
    with _lock:
        _schedules.append(entry)
    _save()
    return sched_id


def remove_schedule(sched_id: str) -> bool:
    with _lock:
        before = len(_schedules)
        _schedules[:] = [s for s in _schedules if s["id"] != sched_id]
    _save()
    return len(_schedules) < before


def toggle_schedule(sched_id: str, enabled: bool):
    with _lock:
        for s in _schedules:
            if s["id"] == sched_id:
                s["enabled"] = enabled
    _save()


def list_schedules() -> list[dict]:
    _load()
    return [dict(s) for s in _schedules]


def _calc_next_str(hour: int, minute: int, repeat: str, interval_min: int) -> str:
    import datetime
    now = datetime.datetime.now()
    if repeat == "hourly":
        next_t = now.replace(minute=minute, second=0, microsecond=0)
        if next_t <= now:
            next_t += datetime.timedelta(hours=1)
    elif repeat == "every_N_min":
        next_t = now + datetime.timedelta(minutes=interval_min)
    else:  # daily / once
        next_t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_t <= now:
            next_t += datetime.timedelta(days=1)
    return next_t.strftime("%Y-%m-%d %H:%M")


def _should_run(sched: dict) -> bool:
    """Check if a schedule should fire right now."""
    if not sched["enabled"]:
        return False
    now = time.time()
    last = sched["last_run"]
    repeat = sched["repeat"]

    import datetime
    dt_now = datetime.datetime.now()
    h, m = sched["hour"], sched["minute"]

    if repeat == "once":
        if last > 0:
            return False
        return dt_now.hour == h and dt_now.minute == m

    elif repeat == "daily":
        if now - last < 86000:  # not within 86000s of last run
            return False
        return dt_now.hour == h and dt_now.minute == m

    elif repeat == "hourly":
        if now - last < 3500:
            return False
        return dt_now.minute == m

    elif repeat == "every_N_min":
        interval_s = sched["interval_min"] * 60
        return now - last >= interval_s

    return False


def register_callback(event: str, cb: Callable):
    """Register a callback for schedule events. event='fire'."""
    _callbacks[event] = cb


def _runner_loop():
    global _running
    _running = True
    _load()
    print("[SCHED] Scheduler started")
    while _running:
        try:
            with _lock:
                for sched in _schedules:
                    if _should_run(sched):
                        sched["last_run"] = time.time()
                        sched["run_count"] += 1
                        sched["next_run_str"] = _calc_next_str(
                            sched["hour"], sched["minute"],
                            sched["repeat"], sched["interval_min"]
                        )
                        _save()
                        # Fire callback
                        cb = _callbacks.get("fire")
                        if cb:
                            threading.Thread(
                                target=cb,
                                args=(sched["genre_key"], sched["count"], sched["id"]),
                                daemon=True
                            ).start()
                        print(f"[SCHED] Fired: {sched['name']}")
        except Exception as e:
            print(f"[SCHED] Error: {e}")
        time.sleep(30)  # Check every 30 seconds


def start():
    global _runner, _running
    if _runner and _runner.is_alive():
        return
    _running = True
    _runner = threading.Thread(target=_runner_loop, daemon=True, name="Scheduler")
    _runner.start()


def stop():
    global _running
    _running = False


_load()
