import time
import json
import threading
from pathlib import Path

_STALKER_FILE = Path("/tmp/mediavault_stalker.json")
# format: {"source:artist": {"source": "rule34", "artist": "someartist", "last_checked": 0, "last_id": 0}}
_stalker_db = {}
_stalker_lock = threading.Lock()

def _load_stalker():
    global _stalker_db
    try:
        if _STALKER_FILE.exists():
            _stalker_db = json.loads(_STALKER_FILE.read_text())
    except Exception as e:
        print(f"[STALKER] Load error: {e}")

def _save_stalker():
    try:
        with _stalker_lock:
            _STALKER_FILE.write_text(json.dumps(_stalker_db))
    except Exception as e:
        print(f"[STALKER] Save error: {e}")

_load_stalker()

def add_artist(source, artist):
    key = f"{source}:{artist}"
    with _stalker_lock:
        if key not in _stalker_db:
            _stalker_db[key] = {
                "source": source,
                "artist": artist,
                "last_checked": 0,
                "last_id": 0
            }
    _save_stalker()
    return True

def remove_artist(source, artist):
    key = f"{source}:{artist}"
    with _stalker_lock:
        if key in _stalker_db:
            del _stalker_db[key]
    _save_stalker()
    return True

def get_artists():
    with _stalker_lock:
        return list(_stalker_db.values())

def update_artist_state(source, artist, last_checked, last_id):
    key = f"{source}:{artist}"
    with _stalker_lock:
        if key in _stalker_db:
            _stalker_db[key]["last_checked"] = last_checked
            if last_id is not None:
                _stalker_db[key]["last_id"] = last_id
    _save_stalker()

