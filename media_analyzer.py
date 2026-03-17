import os
import time
import json
import threading
from pathlib import Path
from PIL import Image

try:
    import imagehash
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False

_DEDUP_FILE = Path("/tmp/mediavault_dedup.json")
# mapping of filepath to its hash
_dedup_db = {}
_dedup_lock = threading.Lock()

def _load_dedup_db():
    global _dedup_db
    try:
        if _DEDUP_FILE.exists():
            _dedup_db = json.loads(_DEDUP_FILE.read_text())
    except Exception as e:
        print(f"[DEDUP] Load error: {e}")

def _save_dedup_db():
    try:
        with _dedup_lock:
            _DEDUP_FILE.write_text(json.dumps(_dedup_db))
    except Exception as e:
        print(f"[DEDUP] Save error: {e}")

_load_dedup_db()

def compute_hash(filepath):
    """Computes a perceptual hash for an image or a dummy hash for video."""
    if not IMAGEHASH_AVAILABLE:
        return None
    
    filepath = str(filepath)
    ext = filepath.lower().split('.')[-1]
    
    if ext in ("jpg", "jpeg", "png", "bmp", "webp"):
        try:
            with Image.open(filepath) as img:
                return str(imagehash.phash(img))
        except Exception as e:
            print(f"[DEDUP] Hash error for {filepath}: {e}")
            return None
    elif ext in ("mp4", "webm", "mkv", "mov", "flv", "gif"):
        # For video/gif, use file size as a basic hash for now
        # Perceptual hashing of video requires frame extraction
        try:
            return f"video_{os.path.getsize(filepath)}"
        except Exception:
            return None
    return None

def is_duplicate(filepath):
    """Checks if the file is a duplicate based on its hash."""
    h = compute_hash(filepath)
    if not h:
        return False, None
    
    with _dedup_lock:
        for existing_path, existing_hash in _dedup_db.items():
            if existing_path != str(filepath) and existing_hash == h:
                # verify existing file still exists
                if os.path.exists(existing_path):
                    return True, existing_path
                else:
                    # clean up dangling reference
                    del _dedup_db[existing_path]
                    _save_dedup_db()
                    return False, None
    return False, None

def add_to_db(filepath):
    h = compute_hash(filepath)
    if h:
        with _dedup_lock:
            _dedup_db[str(filepath)] = h
        _save_dedup_db()
        return True
    return False

def remove_from_db(filepath):
    with _dedup_lock:
        if str(filepath) in _dedup_db:
            del _dedup_db[str(filepath)]
            _save_dedup_db()

def scan_folder(folder_path):
    """Scans a folder and builds the deduplication database."""
    folder = Path(folder_path)
    if not folder.exists(): return
    count = 0
    for f in folder.iterdir():
        if f.is_file():
            if str(f) not in _dedup_db:
                add_to_db(f)
                count += 1
    print(f"[DEDUP] Scanned {count} new files in {folder_path}")

def find_all_duplicates():
    """Returns a list of duplicate groups (list of filepaths that are identical)."""
    hash_to_paths = {}
    with _dedup_lock:
        for p, h in _dedup_db.items():
            if os.path.exists(p):
                hash_to_paths.setdefault(h, []).append(p)
    
    duplicates = [paths for h, paths in hash_to_paths.items() if len(paths) > 1]
    return duplicates

def compare_hashes(h1, h2, threshold=5):
    """Compares two hashes and returns True if they are similar within a threshold."""
    if not IMAGEHASH_AVAILABLE or not h1 or not h2:
        return False
    try:
        # Convert hex strings back to imagehash objects to compute difference
        ih1 = imagehash.hex_to_hash(h1)
        ih2 = imagehash.hex_to_hash(h2)
        diff = ih1 - ih2
        return diff <= threshold
    except Exception as e:
        print(f"[DEDUP] Hash compare error: {e}")
        return False
