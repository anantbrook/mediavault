from __future__ import annotations
"""
media_analyzer.py
─────────────────
Analyze downloaded media files:
 - Detect format, resolution, file size
 - Generate thumbnails for video files
 - Compute perceptual hash (pHash) for duplicate detection
 - EXIF data extraction
 - Quality scoring (resolution + file size combined)
"""

import os
import io
import json
import hashlib
import struct
from pathlib import Path
from typing import Optional

# Try importing PIL — used for image analysis
try:
    from PIL import Image, ExifTags
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
#  FILE INFO
# ─────────────────────────────────────────────────────────────────────────────
def get_file_info(filepath: Path | str) -> dict:
    """Return metadata dict for any media file."""
    filepath = Path(filepath)
    if not filepath.exists():
        return {"error": "File not found"}

    ext = filepath.suffix.lower()
    size = filepath.stat().st_size
    info = {
        "name":     filepath.name,
        "path":     str(filepath),
        "size":     size,
        "size_str": _fmt_size(size),
        "ext":      ext,
        "is_video": ext in (".mp4", ".webm", ".mov", ".gif", ".mkv", ".avi"),
        "is_image": ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"),
        "width":    None,
        "height":   None,
        "quality_score": 0,
    }

    if info["is_image"] and PIL_AVAILABLE:
        try:
            with Image.open(filepath) as img:
                info["width"]  = img.width
                info["height"] = img.height
                info["mode"]   = img.mode
                info["format"] = img.format
                info["quality_score"] = _quality_score(img.width, img.height, size)
                exif = _get_exif(img)
                if exif:
                    info["exif"] = exif
        except Exception as e:
            info["image_error"] = str(e)

    elif info["is_image"] and not PIL_AVAILABLE:
        # Fallback: parse image dimensions from header bytes
        dims = _read_image_dims(filepath)
        if dims:
            info["width"], info["height"] = dims
            info["quality_score"] = _quality_score(dims[0], dims[1], size)

    return info


def _fmt_size(b: int) -> str:
    if b >= 1_073_741_824: return f"{b/1_073_741_824:.1f} GB"
    if b >= 1_048_576:     return f"{b/1_048_576:.1f} MB"
    if b >= 1024:          return f"{b/1024:.0f} KB"
    return f"{b} B"


def _quality_score(w: int, h: int, size: int) -> int:
    """Higher resolution + larger file = higher score (0–100)."""
    mp = (w * h) / 1_000_000  # megapixels
    mb = size / 1_048_576      # file size MB
    score = min(100, int(mp * 10 + mb * 5))
    return score


def _get_exif(img) -> dict:
    """Extract readable EXIF tags from a PIL Image."""
    try:
        raw = img._getexif()
        if not raw:
            return {}
        return {
            ExifTags.TAGS.get(k, k): str(v)
            for k, v in raw.items()
            if k in ExifTags.TAGS
        }
    except Exception:
        return {}


def _read_image_dims(path: Path) -> Optional[tuple[int, int]]:
    """Read image dimensions without PIL by parsing file headers."""
    try:
        with open(path, "rb") as f:
            header = f.read(26)
        ext = path.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            # JPEG — find SOF marker
            with open(path, "rb") as f:
                data = f.read(4096)
            i = 0
            while i < len(data) - 8:
                if data[i] == 0xFF and data[i+1] in (0xC0, 0xC2):
                    h = struct.unpack(">H", data[i+5:i+7])[0]
                    w = struct.unpack(">H", data[i+7:i+9])[0]
                    return w, h
                i += 1
        elif ext == ".png":
            if header[:8] == b'\x89PNG\r\n\x1a\n':
                w = struct.unpack(">I", header[16:20])[0]
                h = struct.unpack(">I", header[20:24])[0]
                return w, h
        elif ext == ".webp":
            if header[8:12] == b"WEBP":
                if header[12:16] == b"VP8 ":
                    w = struct.unpack("<H", header[26:28])[0] & 0x3FFF
                    h = struct.unpack("<H", header[28:30])[0] & 0x3FFF
                    return w, h
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  PERCEPTUAL HASH — duplicate detection without PIL
# ─────────────────────────────────────────────────────────────────────────────
def phash_file(filepath: Path | str) -> Optional[str]:
    """
    Compute a simple perceptual hash of an image for duplicate detection.
    Falls back to SHA256 if PIL not available.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return None

    if PIL_AVAILABLE:
        try:
            with Image.open(filepath) as img:
                # Resize to 8x8 grayscale
                small = img.convert("L").resize((8, 8), Image.LANCZOS)
                pixels = list(small.getdata())
                avg = sum(pixels) / len(pixels)
                bits = "".join("1" if p > avg else "0" for p in pixels)
                return hex(int(bits, 2))[2:].zfill(16)
        except Exception:
            pass

    # Fallback: SHA256 of first 64KB
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read(65536)).hexdigest()[:16]
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  THUMBNAIL GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def generate_thumbnail(
    filepath: Path | str,
    size: tuple[int, int] = (256, 256),
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Generate a thumbnail for an image file."""
    if not PIL_AVAILABLE:
        return None
    filepath = Path(filepath)
    if not filepath.exists():
        return None
    out_dir = output_dir or filepath.parent / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = out_dir / f"thumb_{filepath.stem}.jpg"

    try:
        with Image.open(filepath) as img:
            img.thumbnail(size, Image.LANCZOS)
            img.convert("RGB").save(thumb_path, "JPEG", quality=80)
        return thumb_path
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  SCAN DOWNLOAD FOLDER
# ─────────────────────────────────────────────────────────────────────────────
def scan_folder(folder: Path | str) -> list[dict]:
    """Scan a folder and return info for all media files."""
    folder = Path(folder)
    results = []
    media_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
                  ".mp4", ".webm", ".mov", ".mkv", ".avi"}
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in media_exts:
            results.append(get_file_info(f))
    return results


def find_duplicates(folder: Path | str) -> list[list[str]]:
    """Find duplicate files in a folder using pHash."""
    folder = Path(folder)
    hashes: dict[str, list[str]] = {}
    for f in folder.iterdir():
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            h = phash_file(f)
            if h:
                hashes.setdefault(h, []).append(str(f))
    return [paths for paths in hashes.values() if len(paths) > 1]
