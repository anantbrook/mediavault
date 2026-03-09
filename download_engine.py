"""
download_engine.py
──────────────────
Core download engine:
 - Multi-threaded parallel downloads
 - Resume interrupted downloads (Range header)
 - Speed measurement & throttling
 - Proxy support (HTTP/SOCKS5)
 - yt-dlp integration for video sites
 - Progress callbacks
"""

import os
import time
import threading
import subprocess
from pathlib import Path
from typing import Callable, Optional
from bypass_cloudflare import download_with_bypass, get_session
from cookie_manager import get_cookies

DOWNLOAD_DIR = Path("/tmp/mediavault_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Active downloads tracker
_active: dict[str, dict] = {}   # dl_id → {progress, speed, status, ...}
_lock = threading.Lock()

# ── Proxy config ─────────────────────────────────────────────────────────────
_proxy: Optional[str] = None   # e.g. "socks5://127.0.0.1:9050"

def set_proxy(proxy_url: str):
    global _proxy
    _proxy = proxy_url.strip() if proxy_url.strip() else None

def get_proxy() -> Optional[str]:
    return _proxy


def _make_proxies():
    if not _proxy:
        return None
    return {"http": _proxy, "https": _proxy}


# ── yt-dlp Download (YouTube, Twitter, TikTok, Reddit video, etc.) ───────────
def ytdlp_download(
    url: str,
    dl_id: str,
    cookies_domain: str = "",
    progress_cb: Optional[Callable] = None,
) -> tuple[bool, str]:
    """Download via yt-dlp with full bypass options."""
    out_template = str(DOWNLOAD_DIR / f"{dl_id}_%(title)s.%(ext)s")
    cookies = get_cookies(cookies_domain) if cookies_domain else {}

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--merge-output-format", "mp4",
        "--output", out_template,
        "--retries", "5",
        "--fragment-retries", "10",
        "--add-header", f"User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    ]

    # Add cookies if available
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        cmd += ["--add-header", f"Cookie:{cookie_str}"]

    # Add proxy
    if _proxy:
        cmd += ["--proxy", _proxy]

    cmd.append(url)

    with _lock:
        _active[dl_id] = {"status": "running", "progress": 0, "speed": 0, "filename": ""}

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        filename = ""
        for line in proc.stdout:
            line = line.strip()
            # Parse progress line: [download]  45.2% of ~123.45MiB at 1.23MiB/s
            if "[download]" in line and "%" in line:
                try:
                    pct = float(line.split("%")[0].split()[-1])
                    speed_part = line.split("at ")[-1].split(" ")[0] if "at " in line else ""
                    with _lock:
                        _active[dl_id]["progress"] = pct
                        _active[dl_id]["speed_str"] = speed_part
                    if progress_cb:
                        progress_cb(dl_id, pct, speed_part)
                except Exception:
                    pass
            if "Destination:" in line or "Merging" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    filename = Path(parts[1].strip()).name
                    with _lock:
                        _active[dl_id]["filename"] = filename

        proc.wait()
        if proc.returncode == 0:
            # Find actual file
            if not filename:
                candidates = list(DOWNLOAD_DIR.glob(f"{dl_id}_*"))
                if candidates:
                    filename = candidates[0].name
            with _lock:
                _active[dl_id].update({"status": "done", "progress": 100, "filename": filename})
            return True, filename
        else:
            with _lock:
                _active[dl_id]["status"] = "error"
            return False, "yt-dlp failed"
    except Exception as e:
        with _lock:
            _active[dl_id].update({"status": "error", "error": str(e)})
        return False, str(e)


# ── Direct Download (images, files) ──────────────────────────────────────────
def direct_download(
    url: str,
    dl_id: str,
    filename: Optional[str] = None,
    cookies_domain: str = "",
    progress_cb: Optional[Callable] = None,
) -> tuple[bool, str]:
    """Download a direct file URL with resume support."""
    if not filename:
        filename = f"{dl_id}_{url.split('/')[-1].split('?')[0]}"
    dest = DOWNLOAD_DIR / filename

    cookies = get_cookies(cookies_domain) if cookies_domain else {}

    # Resume support — check existing partial file
    existing_size = dest.stat().st_size if dest.exists() else 0

    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    session = get_session(domain)
    if cookies:
        for k, v in cookies.items():
            session.cookies.set(k, v, domain=domain)
    if _proxy:
        session.proxies.update({"http": _proxy, "https": _proxy})

    headers = {}
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"

    with _lock:
        _active[dl_id] = {"status": "running", "progress": 0, "speed": 0, "filename": filename}

    try:
        resp = session.get(url, headers=headers, stream=True, timeout=60)
        if resp.status_code not in (200, 206):
            with _lock:
                _active[dl_id].update({"status": "error", "error": f"HTTP {resp.status_code}"})
            return False, f"HTTP {resp.status_code}"

        total = int(resp.headers.get("Content-Length", 0)) + existing_size
        downloaded = existing_size
        start_time = time.time()
        mode = "ab" if existing_size > 0 else "wb"

        with open(dest, mode) as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time + 0.001
                    speed = (downloaded - existing_size) / elapsed
                    pct = (downloaded / total * 100) if total > 0 else 0
                    with _lock:
                        _active[dl_id].update({"progress": pct, "speed": speed})
                    if progress_cb:
                        progress_cb(dl_id, pct, speed)

        with _lock:
            _active[dl_id].update({"status": "done", "progress": 100, "filename": filename})
        return True, filename

    except Exception as e:
        with _lock:
            _active[dl_id].update({"status": "error", "error": str(e)})
        return False, str(e)


def smart_download(
    url: str,
    dl_id: str,
    progress_cb: Optional[Callable] = None,
) -> tuple[bool, str]:
    """
    Auto-detect best download method:
    - yt-dlp for video platforms
    - direct for images/files
    """
    VIDEO_DOMAINS = [
        "youtube.com", "youtu.be", "twitter.com", "x.com",
        "tiktok.com", "reddit.com", "vimeo.com", "twitch.tv",
        "instagram.com", "facebook.com", "dailymotion.com",
    ]
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    use_ytdlp = any(vd in domain for vd in VIDEO_DOMAINS)

    # Also use yt-dlp for non-image extensions
    path = urlparse(url).path.lower()
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".avif")
    direct_file = any(path.endswith(ext) for ext in image_exts + (".mp4", ".webm", ".mov", ".mkv"))

    if use_ytdlp or (not direct_file):
        return ytdlp_download(url, dl_id, progress_cb=progress_cb)
    else:
        return direct_download(url, dl_id, progress_cb=progress_cb)


def get_status(dl_id: str) -> dict:
    with _lock:
        return dict(_active.get(dl_id, {"status": "unknown"}))


def get_all_active() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _active.items()}


def cancel_download(dl_id: str):
    with _lock:
        if dl_id in _active:
            _active[dl_id]["status"] = "cancelled"
