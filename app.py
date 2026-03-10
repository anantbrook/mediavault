import os, sys, json, threading, webbrowser, time, re, uuid, random
from pathlib import Path
from urllib.parse import urlparse, quote
from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
import requests as req

# ── Custom unblock engine (7-strategy waterfall) ─────────────────────────────
try:
    import unblock_engine as _ub
    UNBLOCK_AVAILABLE = True
    print('[UNBLOCK] Custom unblock engine loaded ✅')
except ImportError:
    UNBLOCK_AVAILABLE = False
    print('[UNBLOCK] unblock_engine.py not found — using basic requests')

# ── URL resolver (hotlink.php / redirect wrappers / expired tokens) ──────────
try:
    import url_resolver as _ur
    RESOLVER_AVAILABLE = True
    print('[RESOLVER] URL resolver loaded ✅')
except ImportError:
    RESOLVER_AVAILABLE = False
    print('[RESOLVER] url_resolver.py not found')

# ── GOD MODE — 10-strategy universal download engine ─────────────────────────
try:
    import god_mode as _gm
    GOD_MODE = True
    print('[GOD MODE] Universal download engine loaded ✅')
except ImportError:
    _gm = None
    GOD_MODE = False
    print('[GOD MODE] god_mode.py not found')
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    yt_dlp = None
    YT_DLP_AVAILABLE = False
    print("[WARN] yt_dlp not installed — video downloads disabled")

# Always use the folder where app.py lives, regardless of cwd
BASE_DIR = Path(__file__).parent.resolve()
os.chdir(BASE_DIR)

app = Flask(__name__, template_folder=str(BASE_DIR))
# On Railway/Heroku, use /tmp for downloads (writable, survives the session)
import tempfile
_is_cloud = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("DYNO"))
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "mediavault_downloads" if _is_cloud else BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
active_downloads = {}
download_history = []

# ── TELEGRAM — defined here so ALL routes below can reference them ────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
TG_MAX_FILE_MB     = 50
_tg_queue          = []
_tg_lock           = threading.Lock()
_tg_enabled        = False
_tg_thread         = None
_TG_DB_FILE        = Path("/tmp/mediavault_tg_db.json")
_tg_db: list       = []
_tg_channels       = {}
_tg_active_channel = "default"

def _get_active_tg():
    """Return (token, chat_id) for the active channel."""
    if _tg_active_channel in _tg_channels:
        ch = _tg_channels[_tg_active_channel]
        return ch.get("token",""), ch.get("chat_id","")
    return TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def tg_enabled():
    token, chat_id = _get_active_tg()
    return bool(token and chat_id)

def _ensure_tg_worker():
    global _tg_thread
    if _tg_thread is None and tg_enabled():
        _tg_thread = threading.Thread(target=_tg_worker, daemon=True)
        _tg_thread.start()

print(f"[TG] Token: {(TELEGRAM_BOT_TOKEN[:10]+'...') if TELEGRAM_BOT_TOKEN else 'NOT SET'} | Chat ID: {TELEGRAM_CHAT_ID or 'NOT SET'}")

SUPPORTED_SITES = [
    {"name": "DeviantArt",  "url": "deviantart.com",  "icon": "🎨", "types": "Art, Wallpapers, Photos, Videos"},
    {"name": "YouTube",     "url": "youtube.com",     "icon": "▶️", "types": "Videos, Shorts, Playlists, Audio"},
    {"name": "Twitter/X",   "url": "twitter.com",     "icon": "🐦", "types": "Images, GIFs, Videos"},
    {"name": "Reddit",      "url": "reddit.com",      "icon": "🔴", "types": "Images, Galleries, Videos"},
    {"name": "Instagram",   "url": "instagram.com",   "icon": "📸", "types": "Posts, Reels, Stories"},
    {"name": "Pinterest",   "url": "pinterest.com",   "icon": "📌", "types": "High-res Images"},
    {"name": "Flickr",      "url": "flickr.com",      "icon": "📷", "types": "Original Photos"},
    {"name": "Vimeo",       "url": "vimeo.com",       "icon": "🎬", "types": "Videos up to 4K"},
    {"name": "TikTok",      "url": "tiktok.com",      "icon": "🎵", "types": "Videos, No Watermark"},
    {"name": "Tumblr",      "url": "tumblr.com",      "icon": "🌀", "types": "Images, GIFs, Videos"},
    {"name": "ArtStation",  "url": "artstation.com",  "icon": "🖼",  "types": "Artwork, Concept Art"},
    {"name": "SoundCloud",  "url": "soundcloud.com",  "icon": "🎶", "types": "Audio, Music"},
    {"name": "Facebook",    "url": "facebook.com",    "icon": "📘", "types": "Videos, Photos"},
    {"name": "Twitch",      "url": "twitch.tv",       "icon": "💜", "types": "Clips, VODs"},
]

QUALITY_MAP = {
    "max":      "bestvideo+bestaudio/best",
    "4k":       "bestvideo[height<=2160]+bestaudio/best[height<=2160]",
    "1080":     "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720":      "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "audio":    "bestaudio/best",
    "original": "bestvideo+bestaudio/best",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.deviantart.com/",
    "sec-ch-ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "Cache-Control": "max-age=0",
    "Cookie": "agegate_state=1; userinfo=mature_content_filter%3D0",
}

DA_VIDEO_TAGS   = ["animation","3d_animation","motion","mmd","animated","loop","video","cgi"]
DA_ART_TAGS     = ["anime","fantasy","landscape","character_design","concept_art","illustration","surreal","portrait","space","nature","architecture","digital_art"]
DA_WALLPAPER_TAGS = ["wallpaper","hd_wallpaper","desktop_wallpaper","4k_wallpaper","minimalist","dark_wallpaper","space","abstract","nature","landscape"]
# Mature / sexy wallpaper tags — 10 categories (DA requires age-gate cookie)
DA_MATURE_TAGS  = ["pinup","bikini","lingerie","ecchi","boudoir","glamour","nude","sexy","figure_study","artistic_nude"]

# ── RSS CACHE — avoids hammering DA and re-fetching identical tokens ──────────
_rss_cache = {}          # tag -> (timestamp, results)
_RSS_TTL   = 120         # seconds; fresh enough that tokens don't expire mid-use


def safe_name(s, maxlen=80):
    return re.sub(r'[^\w\-_ ]', '_', s or 'media').strip()[:maxlen]

def is_direct_image(url):
    return bool(re.search(r'\.(jpg|jpeg|png|gif|webp|bmp|tiff)(\?.*)?$', url.lower()))

def is_direct_video(url):
    return bool(re.search(r'\.(mp4|webm|mkv|avi|mov|flv)(\?.*)?$', url.lower()))




# ══════════════════════════════════════════════════════════════════════════════
#  TAG BLACKLIST + DUPLICATE DETECTION
# ══════════════════════════════════════════════════════════════════════════════
_BL_FILE   = Path("/tmp/mediavault_blacklist.json")
_SEEN_FILE = Path("/tmp/mediavault_seen.json")

_tag_blacklist: set = set()   # tags to never download
_seen_urls:     set = set()   # URLs already downloaded (dedup)

def _bl_load():
    global _tag_blacklist, _seen_urls
    try:
        if _BL_FILE.exists():
            _tag_blacklist = set(json.loads(_BL_FILE.read_text()))
        if _SEEN_FILE.exists():
            _seen_urls = set(json.loads(_SEEN_FILE.read_text()))
            # Keep only last 10000 to avoid memory bloat
            if len(_seen_urls) > 10000:
                _seen_urls = set(list(_seen_urls)[-10000:])
        print(f"[BL] {len(_tag_blacklist)} blacklisted tags, {len(_seen_urls)} seen URLs")
    except Exception as e:
        print(f"[BL] Load error: {e}")

def _bl_save():
    try:
        _BL_FILE.write_text(json.dumps(list(_tag_blacklist)))
        _SEEN_FILE.write_text(json.dumps(list(_seen_urls)[-10000:]))
    except Exception as e:
        print(f"[BL] Save error: {e}")

def _is_blacklisted(tag: str) -> bool:
    tag_l = tag.lower()
    return any(b.lower() in tag_l for b in _tag_blacklist)

def _is_seen(url: str) -> bool:
    return url in _seen_urls

def _mark_seen(url: str):
    _seen_urls.add(url)
    _bl_save()

_bl_load()

# ══════════════════════════════════════════════════════════════════════════════
#  STATS ENGINE
# ══════════════════════════════════════════════════════════════════════════════
_STATS_FILE = Path("/tmp/mediavault_stats.json")
_stats = {
    "total_files":    0,
    "total_bytes":    0,
    "total_videos":   0,
    "total_images":   0,
    "by_genre":       {},   # genre_key -> count
    "by_source":      {},   # "Rule34" -> count
    "by_day":         {},   # "2026-03-09" -> {"files":N,"bytes":N}
    "speed_log":      [],   # last 60 entries: {ts, bytes_per_sec}
    "errors":         0,
    "session_start":  time.time(),
}

def _stats_load():
    global _stats
    try:
        if _STATS_FILE.exists():
            saved = json.loads(_STATS_FILE.read_text())
            _stats.update(saved)
            _stats["session_start"] = time.time()
    except Exception as e:
        print(f"[STATS] Load error: {e}")

def _stats_save():
    try:
        _STATS_FILE.write_text(json.dumps(_stats))
    except Exception as e:
        print(f"[STATS] Save error: {e}")

def _stats_record(filepath, genre_key="manual", source="unknown"):
    """Call after every successful download."""
    try:
        size = os.path.getsize(filepath) if filepath and os.path.exists(filepath) else 0
        ext  = Path(filepath).suffix.lower() if filepath else ""
        is_v = ext in (".mp4",".webm",".mov",".gif",".mkv")
        today = time.strftime("%Y-%m-%d")

        _stats["total_files"]  += 1
        _stats["total_bytes"]  += size
        if is_v: _stats["total_videos"] += 1
        else:    _stats["total_images"] += 1

        _stats["by_genre"][genre_key] = _stats["by_genre"].get(genre_key, 0) + 1
        _stats["by_source"][source]   = _stats["by_source"].get(source, 0) + 1

        day = _stats["by_day"].setdefault(today, {"files":0,"bytes":0})
        day["files"]  += 1
        day["bytes"]  += size

        # Speed log — bytes per second this minute
        now = time.time()
        _stats["speed_log"].append({"ts": now, "bytes": size})
        _stats["speed_log"] = [e for e in _stats["speed_log"] if now - e["ts"] < 3600]  # keep 1hr

        _stats_save()
    except Exception as e:
        print(f"[STATS] Record error: {e}")

_stats_load()

# ══════════════════════════════════════════════════════════════════════════════
#  MULTI-SOURCE API FETCHER
#  All sources work from servers (no IP blocking, free, public APIs)
#  Sources:
#    Images/Art : Gelbooru, Danbooru, Konachan, Yande.re, Safebooru, Lolibooru
#    Wallpapers  : Wallhaven
#    Mature/18+  : Rule34.xxx, Rule34.paheal, xbooru
#    Videos      : Rule34.xxx (mp4/webm), Gelbooru video posts
# ══════════════════════════════════════════════════════════════════════════════

# Which sources are enabled (can be toggled via API)
SOURCES_CONFIG = {
    "rule34":     {"label": "Rule34.xxx",   "mature": True,  "video": True,  "image": True,  "enabled": True},
    "gelbooru":   {"label": "Gelbooru",     "mature": True,  "video": True,  "image": True,  "enabled": True},
    "danbooru":   {"label": "Danbooru",     "mature": False, "video": False, "image": True,  "enabled": True},
    "konachan":   {"label": "Konachan",     "mature": False, "video": False, "image": True,  "enabled": True},
    "yandere":    {"label": "Yande.re",     "mature": True,  "video": False, "image": True,  "enabled": True},
    "safebooru":  {"label": "Safebooru",    "mature": False, "video": False, "image": True,  "enabled": True},
    "wallhaven":  {"label": "Wallhaven",    "mature": True,  "video": False, "image": True,  "enabled": True},
    "xbooru":     {"label": "Xbooru",       "mature": True,  "video": True,  "image": True,  "enabled": True},
    "paheal":     {"label": "Rule34.paheal","mature": True,  "video": True,  "image": True,  "enabled": True},
    "lolibooru":  {"label": "Lolibooru",    "mature": True,  "video": False, "image": True,  "enabled": False},
}

def _fetch_booru(base_url, tag, pid=0, json_key=None, label="Booru"):
    """Generic booru API fetcher. Uses direct requests for speed (not unblock engine)."""
    results = []
    try:
        api_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = req.get(base_url, headers=api_headers, timeout=15)
        if r is None or r.status_code != 200: return results
        ct   = r.headers.get("content-type","")
        text = r.text.strip()
        if not text or text[0] not in ("[","{") or "html" in ct:
            return results
        data = r.json()
        posts = []
        if isinstance(data, list): posts = data
        elif isinstance(data, dict):
            for k in (json_key, "post", "posts", "data", "results"):
                if k and k in data:
                    posts = data[k]; break

        for p in posts[:30]:
            if not isinstance(p, dict): continue

            file_url = p.get("file_url") or p.get("large_file_url") or ""
            if not file_url or not file_url.startswith("http"): continue

            # Resolve hotlink wrappers (xbooru hotlink.php etc)
            if RESOLVER_AVAILABLE and _ur.is_wrapper_url(file_url):
                resolved = _ur.resolve(file_url)
                if resolved and resolved != file_url:
                    file_url = resolved

            # Detect video — check URL extension AND API file_type/image fields
            url_low = file_url.lower()
            api_type = str(p.get("file_ext","") or p.get("file_type","") or "").lower()
            is_video = (
                any(url_low.endswith(x) for x in (".mp4",".webm",".mov",".flv",".avi")) or
                api_type in ("mp4","webm","mov","flv","video") or
                "video" in api_type
            )

            # For GIFs — treat as video (animated)
            is_gif = url_low.endswith(".gif") or api_type == "gif"

            preview = (
                p.get("preview_url") or p.get("preview_file_url") or
                p.get("sample_url") or p.get("sample_file_url") or
                file_url
            )

            results.append({
                "title":    f"{label} · {tag}",
                "author":   str(p.get("owner","") or p.get("uploader_id","") or p.get("creator_id","") or ""),
                "url":      file_url,
                "thumbnail": preview,
                "is_video": is_video or is_gif,
                "is_gif":   is_gif,
                "type":     "video" if (is_video or is_gif) else "image",
                "direct":   True,
                "score":    int(p.get("score",0) or p.get("up_score",0) or 0),
                "width":    int(p.get("width",0) or 0),
                "height":   int(p.get("height",0) or 0),
            })
    except Exception as e:
        print(f"{label} fetch error ({tag}): {e}")
    return results


def scrape_da_tag(tag, mature=False, sources=None, video_only=False, image_only=False):
    """
    Multi-source fetcher. sources=None means use all enabled sources.
    video_only=True  → only return video posts
    image_only=True  → only return image posts
    """
    now = time.time()
    cache_key = f"{tag}:{mature}:{video_only}:{image_only}:{str(sources)}"
    if cache_key in _rss_cache:
        ts, cached = _rss_cache[cache_key]
        if now - ts < _RSS_TTL:
            return cached

    tag_u = tag.replace("-","_").replace(" ","_")
    pid   = random.randint(0, 8)
    results = []

    enabled = sources or [k for k,v in SOURCES_CONFIG.items() if v["enabled"]]

    # ── Rule34.xxx ────────────────────────────────────────────────────────────
    if "rule34" in enabled and SOURCES_CONFIG["rule34"]["enabled"]:
        extra = "+video" if video_only else ""
        url = (f"https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1"
               f"&limit=30&tags={quote(tag_u+extra)}&pid={pid}")
        results += _fetch_booru(url, tag, label="Rule34")

    # ── Gelbooru ──────────────────────────────────────────────────────────────
    if "gelbooru" in enabled and SOURCES_CONFIG["gelbooru"]["enabled"]:
        extra = "+video" if video_only else ""
        url = (f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1"
               f"&limit=30&tags={quote(tag_u+extra)}&pid={pid}")
        results += _fetch_booru(url, tag, json_key="post", label="Gelbooru")

    # ── Danbooru ──────────────────────────────────────────────────────────────
    if "danbooru" in enabled and SOURCES_CONFIG["danbooru"]["enabled"] and not (mature and video_only):
        url = (f"https://danbooru.donmai.us/posts.json?limit=30"
               f"&tags={quote(tag_u)}&page={pid+1}")
        results += _fetch_booru(url, tag, label="Danbooru")

    # ── Konachan ──────────────────────────────────────────────────────────────
    if "konachan" in enabled and SOURCES_CONFIG["konachan"]["enabled"]:
        url = (f"https://konachan.com/post.json?limit=30"
               f"&tags={quote(tag_u)}&page={pid+1}")
        results += _fetch_booru(url, tag, label="Konachan")

    # ── Yande.re ──────────────────────────────────────────────────────────────
    if "yandere" in enabled and SOURCES_CONFIG["yandere"]["enabled"]:
        url = (f"https://yande.re/post.json?limit=30"
               f"&tags={quote(tag_u)}&page={pid+1}")
        results += _fetch_booru(url, tag, label="Yande.re")

    # ── Safebooru ─────────────────────────────────────────────────────────────
    if "safebooru" in enabled and SOURCES_CONFIG["safebooru"]["enabled"] and not mature:
        url = (f"https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1"
               f"&limit=30&tags={quote(tag_u)}&pid={pid}")
        results += _fetch_booru(url, tag, label="Safebooru")

    # ── Wallhaven ─────────────────────────────────────────────────────────────
    if "wallhaven" in enabled and SOURCES_CONFIG["wallhaven"]["enabled"] and not video_only:
        purity = "110" if mature else "100"
        url = (f"https://wallhaven.cc/api/v1/search?q={quote(tag)}"
               f"&categories=111&purity={purity}&sorting=random&page={pid+1}")
        try:
            r = req.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
            if r.status_code == 200:
                for p in r.json().get("data", [])[:20]:
                    file_url = p.get("path","")
                    if not file_url: continue
                    preview  = p.get("thumbs",{}).get("large") or file_url
                    results.append({
                        "title":  f"Wallhaven · {tag}",
                        "author": p.get("uploader",{}).get("username",""),
                        "url": file_url, "thumbnail": preview,
                        "is_video": False, "type": "image", "direct": True,
                        "score": p.get("views", 0),
                    })
        except Exception as e:
            print(f"Wallhaven error ({tag}): {e}")

    # ── Xbooru ────────────────────────────────────────────────────────────────
    if "xbooru" in enabled and SOURCES_CONFIG["xbooru"]["enabled"] and mature:
        url = (f"https://xbooru.com/index.php?page=dapi&s=post&q=index&json=1"
               f"&limit=30&tags={quote(tag_u)}&pid={pid}")
        results += _fetch_booru(url, tag, label="Xbooru")

    # ── Lolibooru ─────────────────────────────────────────────────────────────
    # DISABLED: lolibooru.moe always times out from Railway servers
    # if "lolibooru" in enabled and SOURCES_CONFIG["lolibooru"]["enabled"] and mature:
    #     pass

    # ── Filter by type ────────────────────────────────────────────────────────
    if video_only:
        results = [r for r in results if r["is_video"]]
    elif image_only:
        results = [r for r in results if not r["is_video"]]

    # Sort by score, dedupe by URL
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: x.get("score",0), reverse=True):
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    _rss_cache[cache_key] = (time.time(), unique)
    return unique


def _scrape_da_tag_UNUSED(tag, mature=False):
    """Original scrape method - kept for reference."""
    offset = random.randint(0, 150)
    url = f"https://www.deviantart.com/tag/{tag}?order=popular&offset={offset}"
    headers = dict(HEADERS)
    if mature:
        headers["Cookie"] = "agegate_state=1; userinfo=mature_content_filter%3D0"
    try:
        r = req.get(url, headers=headers, timeout=15)
        html = r.text
        results = []

        # Try __NEXT_DATA__ JSON
        nd = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if nd:
            try:
                data = json.loads(nd.group(1))
                devs = []
                def hunt(obj, key):
                    if isinstance(obj, dict):
                        if key in obj: return obj[key]
                        for v in obj.values():
                            r2 = hunt(v, key)
                            if r2 is not None: return r2
                    elif isinstance(obj, list):
                        for item in obj:
                            r2 = hunt(item, key)
                            if r2 is not None: return r2
                    return None
                devs = hunt(data, "deviations") or hunt(data, "results") or []
                for item in devs:
                    if not isinstance(item, dict): continue
                    page_url = item.get("url","")
                    if not page_url: continue
                    title  = item.get("title","DeviantArt")
                    author = item.get("author",{})
                    author = author.get("username","") if isinstance(author,dict) else str(author)
                    is_vid = item.get("isVideo", False) or "video" in str(item.get("type","")).lower()
                    thumb  = None
                    media  = item.get("media",{})
                    if isinstance(media, dict):
                        base  = media.get("baseUri","")
                        token = (media.get("token") or [""])[0]
                        for t in (media.get("types") or []):
                            if t.get("t") in ("preview","fullview","gif","gif_400"):
                                c = t.get("c","")
                                thumb = f"{base}/{c}?token={token}" if (base and c) else (f"{base}?token={token}" if base else None)
                                break
                        if not thumb and base:
                            thumb = f"{base}?token={token}" if token else base
                    results.append({"title":title,"author":author,"url":page_url,"thumbnail":thumb,"is_video":is_vid,"type":"video" if is_vid else "image"})
            except Exception as e:
                print("NEXT_DATA parse error:", e)

        # HTML fallback
        if not results:
            links  = re.findall(r'href="(https://www\.deviantart\.com/[^/"]+/art/[^"]+)"', html)
            thumbs = re.findall(r'src="(https://(?:images-wixmp|wixmp)[^"]+\.(jpg|png|gif|webp)[^"]*)"', html)
            for i, link in enumerate(links[:20]):
                thumb = thumbs[i][0] if i < len(thumbs) else None
                results.append({"title":f"DeviantArt · {tag}","author":"Artist","url":link,"thumbnail":thumb,"is_video":False,"type":"image"})

        return results
    except Exception as e:
        print("scrape_da_tag error:", e)
        return []


def get_da_media_url(page_url):
    """
    Get the FULL RESOLUTION, UNRESTRICTED media URL from any DeviantArt page.
    Handles: images, videos (any length), GIFs, mature content.
    8-strategy waterfall — never returns expired tokens.
    """
    da_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.deviantart.com/",
        "Cookie": "agegate_state=1; userinfo=mature_content_filter%3D0; da_is_logged_in=0",
        "DNT": "1",
    }

    def _is_alive(url, timeout=8):
        """Quick check if URL returns 200."""
        try:
            h = req.head(url, headers=da_headers, timeout=timeout, allow_redirects=True)
            return h.status_code in (200, 206)
        except Exception:
            return False

    def _clean_wixmp(url):
        """Strip resize params from wixmp URL to get original resolution."""
        if not url: return url
        url = re.sub(r"/v1/fill/w_\d+,h_\d+[^/]*/", "/", url)
        url = re.sub(r"/v1/crop/[^/]+/", "/", url)
        return url

    def _decode_wixmp(raw):
        return raw.replace("\u002F","/").replace("\\/","/").replace("&amp;","&")

    # ── Strategy 0: extended_fetch API — best for videos, returns src directly ──
    slug_match = re.search(r"-(\d+)$", page_url.rstrip("/"))
    dev_id = slug_match.group(1) if slug_match else None

    if dev_id:
        # Extract username from URL for better API results
        _user_match = re.search(r"deviantart\.com/([^/]+)/art/", page_url)
        _username = _user_match.group(1) if _user_match else ""

        extended_endpoints = [
            # extended_fetch with type=video — best for video deviations
            f"https://www.deviantart.com/_napi/shared_api/deviation/extended_fetch?deviationid={dev_id}&username={_username}&type=video&include_session=false",
            f"https://www.deviantart.com/_napi/shared_api/deviation/extended_fetch?deviationid={dev_id}&username={_username}&type=art&include_session=false",
            f"https://www.deviantart.com/_napi/da-browse/api/networkbar/deviation/{dev_id}",
            f"https://www.deviantart.com/_napi/shared_api/deviation/fetch?deviationid={dev_id}",
        ]
        for ep in extended_endpoints:
            try:
                api_r = req.get(ep, headers=da_headers, timeout=15)
                if api_r.status_code == 200:
                    raw = api_r.text
                    # Find any wixmp video URL in the response
                    vid_matches = re.findall(
                        r"https://[^\s\"'<>]+wixmp[^\s\"'<>]+\.mp4[^\s\"'<>]*",
                        raw.replace("\\u002F","/").replace("\\/","/")
                    )
                    if vid_matches:
                        best_vid = max(vid_matches, key=len)
                        print(f"[DA] extended_fetch video: {best_vid[:80]}")
                        return best_vid, "video"

                    # Parse JSON for token + baseUri
                    try:
                        d = api_r.json()
                        dev = d.get("deviation", d)  # some endpoints wrap in deviation{}

                        # Path 1: videoPreviews (DA's standard video structure)
                        vp = dev.get("videoPreviews") or d.get("videoPreviews") or []
                        if isinstance(vp, list) and vp:
                            for preview in vp:
                                srcs = preview.get("sources",[]) if isinstance(preview,dict) else []
                                if srcs:
                                    best = max(srcs, key=lambda s: s.get("width",0) if isinstance(s,dict) else 0)
                                    src = best.get("src","") if isinstance(best,dict) else ""
                                    if src:
                                        print(f"[DA] videoPreviews src: {src[:80]}")
                                        return _decode_wixmp(src), "video"

                        # Path 2: media.baseUri + token
                        media = dev.get("media",{})
                        base  = media.get("baseUri","")
                        tokens= media.get("token",[""])
                        tok   = tokens[0] if isinstance(tokens,list) and tokens else ""
                        if base and any(x in base for x in (".mp4",".webm",".flv")):
                            full = f"{_decode_wixmp(base)}?token={tok}" if tok else _decode_wixmp(base)
                            return full, "video"

                        # Path 3: videos array
                        vids = dev.get("videos",[]) or d.get("videos",[])
                        if vids:
                            src = vids[0].get("src","") if isinstance(vids[0],dict) else str(vids[0])
                            if src: return _decode_wixmp(src), "video"

                        # Path 4: deep scan entire response for any wixmp mp4
                        all_text = api_r.text.replace("\u002F", "/").replace("\\/", "/")
                        mp4s = re.findall(r"https://[^\s\"'<>]+wixmp[^\s\"'<>]+\.mp4[^\s\"'<>]*", all_text)
                        if mp4s:
                            return max(mp4s, key=len), "video"
                    except Exception as ep:
                        print(f"[DA] JSON parse: {ep}")
            except Exception as e:
                print(f"[DA] extended_fetch {ep[:50]}: {e}")

    # ── Strategy 1: Eclipse networkbar API ───────────────────────────────────
    try:
        if dev_id:
            api_url = f"https://www.deviantart.com/_napi/da-browse/api/networkbar/deviation/{dev_id}"
            api_r = req.get(api_url, headers=da_headers, timeout=12)
            if api_r.status_code == 200:
                d = api_r.json()
                for path in [
                    ["deviation","media","baseUri"],
                    ["deviation","media","prettyName"],
                    ["deviation","flash","src"],
                    ["deviation","videos",0,"src"],
                ]:
                    try:
                        v = d
                        for k in path: v = v[k]
                        if v and v.startswith("http"):
                            token_list = d.get("deviation",{}).get("media",{}).get("token",[""])
                            tok = token_list[0] if token_list else ""
                            full = f"{v}?token={tok}" if tok else v
                            # Don't _is_alive check — token expires during HEAD request
                            mtype = "video" if any(x in full for x in [".mp4",".webm",".flv"]) else "image"
                            return full, mtype
                    except (KeyError, IndexError, TypeError):
                        pass
    except Exception as e:
        print(f"[DA] Eclipse API: {e}")

    # ── Strategy 2: oEmbed — reliable for images, sometimes works for video ──
    try:
        oe = req.get(
            f"https://backend.deviantart.com/oembed?url={quote(page_url)}&format=json",
            headers=da_headers, timeout=12
        )
        if oe.status_code == 200:
            d = oe.json()
            # Try multiple fields — oEmbed returns different things for video vs image
            candidates = [
                d.get("url"),
                d.get("thumbnail_url"),
                d.get("html",""),  # video embed HTML sometimes has src
            ]
            # Extract src from embed HTML
            embed_html = d.get("html","")
            src_match = re.search(r"src=['\"]([^'\"]+\.(?:mp4|webm|jpg|png)[^'\"]*)['\"]", embed_html)
            if src_match:
                candidates.insert(0, src_match.group(1))
            for url in candidates:
                if url and url.startswith("http") and not url.endswith(".html"):
                    url = _clean_wixmp(url)
                    if _is_alive(url):
                        mtype = "video" if any(x in url for x in [".mp4",".webm"]) else "image"
                        return url, mtype
    except Exception as e:
        print(f"[DA] oEmbed: {e}")

    # ── Strategy 3: Scrape __NEXT_DATA__ — full page JSON contains all media ──
    html = ""
    try:
        r = req.get(page_url, headers=da_headers, timeout=20)
        html = r.text

        # 3a: Direct video src pattern in HTML — no _is_alive (token expires during HEAD)
        for vpat in [
            r'"src"\s*:\s*"(https://wixmp[^"]+\.mp4[^"]*)"',
            r'"src"\s*:\s*"(https://wixmp[^"]+\.webm[^"]*)"',
            r'<source[^>]+src="([^"]+\.(?:mp4|webm)[^"]*)"',
            r'https://[^\s"\'<>]+wixmp[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        ]:
            vm = re.search(vpat, html)
            if vm:
                raw = _decode_wixmp(vm.group(1) if vm.lastindex else vm.group(0))
                print(f"[DA] 3a video found: {raw[:80]}")
                return raw, "video"

        # 3b: Parse __NEXT_DATA__ JSON for all media fields
        nd = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if nd:
            try:
                data = json.loads(nd.group(1))

                # Video: look in all media/video keys
                def find_video(obj, depth=0):
                    if depth > 30: return None
                    if isinstance(obj, dict):
                        # ── Priority paths for DA video structure ──────────
                        # 1. videoPreviews[].sources[].src  (highest quality)
                        vp = obj.get("videoPreviews", [])
                        if isinstance(vp, list) and vp:
                            for preview in vp:
                                srcs = preview.get("sources",[]) if isinstance(preview,dict) else []
                                if isinstance(srcs, list) and srcs:
                                    best = max(srcs, key=lambda s: s.get("width",0) if isinstance(s,dict) else 0)
                                    url_v = best.get("src","") if isinstance(best,dict) else ""
                                    if url_v: return _decode_wixmp(url_v)
                        # 2. videos[].src
                        vids = obj.get("videos", [])
                        if isinstance(vids, list) and vids:
                            v0 = vids[0]
                            src = (v0.get("src","") or v0.get("url","")) if isinstance(v0,dict) else str(v0)
                            if src and src.startswith("http"): return _decode_wixmp(src)
                        # 3. media.baseUri with token
                        media = obj.get("media", {})
                        if isinstance(media, dict):
                            base = media.get("baseUri","")
                            if base and any(x in base for x in (".mp4",".webm",".flv")):
                                toks = media.get("token",[""])
                                tok = toks[0] if isinstance(toks,list) and toks else ""
                                return f"{_decode_wixmp(base)}?token={tok}" if tok else _decode_wixmp(base)
                        # 4. Any key with video URL
                        for k in ("src","videoSrc","video_src","mp4","webm","src_mp4","downloadUrl","download_url","fileUrl","file_url"):
                            v = obj.get(k,"")
                            if isinstance(v, str) and v.startswith("http") and any(x in v.lower() for x in (".mp4",".webm",".flv","video")):
                                return _decode_wixmp(v)
                        for v in obj.values():
                            r2 = find_video(v, depth+1)
                            if r2: return r2
                    elif isinstance(obj, list):
                        for item in obj:
                            r2 = find_video(item, depth+1)
                            if r2: return r2
                    return None

                vid = find_video(data)
                if vid:
                    return vid, "video"

                # Image: prefer "src" > "downloadUrl" > "fullview" > "original"
                def find_image(obj, depth=0):
                    if depth > 20: return None
                    best = {"score": 0, "url": None}
                    if isinstance(obj, dict):
                        priority = {"src":10,"downloadUrl":9,"download_url":9,"original":8,"fullview":7,"prettyName":6}
                        for k, score in priority.items():
                            v = obj.get(k,"")
                            if isinstance(v,str) and v.startswith("http") and any(x in v.lower() for x in [".jpg",".jpeg",".png",".webp",".gif",".avif"]):
                                if score > best["score"]:
                                    best = {"score":score,"url":_clean_wixmp(_decode_wixmp(v))}
                        if best["url"] and _is_alive(best["url"]):
                            return best["url"]
                        for v in obj.values():
                            r2 = find_image(v, depth+1)
                            if r2: return r2
                    elif isinstance(obj, list):
                        for item in obj:
                            r2 = find_image(item, depth+1)
                            if r2: return r2
                    return None

                img = find_image(data)
                if img:
                    return img, "image"

            except Exception as e:
                print(f"[DA] NEXT_DATA parse: {e}")

    except Exception as e:
        print(f"[DA] Page scrape: {e}")

    # ── Strategy 4: og:image — always fresh, strip resize for full-res ──
    if html:
        og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html)
        if og:
            img = _clean_wixmp(og.group(1).replace("&amp;","&"))
            if _is_alive(img):
                return img, "image"

        # Strategy 5: Any wixmp image URL in page HTML
        wix = re.findall(r"(https://[^\s\"'<>]*wixmp[^\s\"'<>]*\.(?:jpg|png|webp|gif)[^\s\"'<>]*)", html)
        for w in wix[:5]:
            w2 = _clean_wixmp(_decode_wixmp(w))
            if _is_alive(w2):
                return w2, "image"

    return None, None


# ── THUMBNAIL PROXY — fetches wixmp image server-side so tokens never expire ──
@app.route("/api/thumb")
def thumb_proxy():
    url = request.args.get("url","").strip()
    if not url:
        return "", 400
    # Validate it's an image/media URL (basic safety check)
    try:
        from urllib.parse import urlparse as _up
        parsed = _up(url)
        if parsed.scheme not in ("http","https"):
            return "", 400
    except Exception:
        return "", 400

    # Build referer-spoofed headers per domain
    domain = url.split("/")[2] if url.count("/") >= 2 else ""
    thumb_headers = dict(HEADERS)
    if "konachan" in domain:
        thumb_headers["Referer"] = "https://konachan.com/"
    elif "yande.re" in domain:
        thumb_headers["Referer"] = "https://yande.re/"
    elif "gelbooru" in domain:
        thumb_headers["Referer"] = "https://gelbooru.com/"
    elif "rule34" in domain:
        thumb_headers["Referer"] = "https://rule34.xxx/"
    elif "danbooru" in domain:
        thumb_headers["Referer"] = "https://danbooru.donmai.us/"
    elif "wallhaven" in domain:
        thumb_headers["Referer"] = "https://wallhaven.cc/"
    elif "wixmp" in domain or "deviantart" in domain:
        thumb_headers["Referer"] = "https://www.deviantart.com/"
    else:
        thumb_headers["Referer"] = f"https://{domain}/"

    try:
        r = req.get(url, headers=thumb_headers, timeout=15, stream=True)
        r.raise_for_status()
        ct = r.headers.get("content-type","image/jpeg")
        # Only proxy images/video, not HTML pages
        if "html" in ct:
            return "", 404
        return Response(stream_with_context(r.iter_content(8192)),
                        content_type=ct,
                        headers={"Cache-Control":"public, max-age=3600"})
    except Exception as e:
        return str(e), 502




@app.route("/api/tgfiles")
def tg_files():
    """Return all files stored in Telegram (permanent library)."""
    token, _ = _get_active_tg()
    page  = int(request.args.get("page", 0))
    limit = int(request.args.get("limit", 20))
    items = list(reversed(_tg_db))  # newest first
    total = len(items)
    page_items = items[page*limit:(page+1)*limit]
    result = []
    for rec in page_items:
        result.append({
            "file_id":   rec["file_id"],
            "type":      rec["type"],
            "name":      rec["name"],
            "size":      rec["size"],
            "caption":   rec["caption"],
            "timestamp": rec["timestamp"],
            "msg_id":    rec.get("msg_id",""),
            "chat_id":   rec.get("chat_id",""),
        })
    return jsonify({"files": result, "total": total, "page": page})

@app.route("/api/tgfile/<file_id>")
def tg_serve_file(file_id):
    """Proxy a Telegram file to the browser for download."""
    # Find token for this file
    token = TELEGRAM_BOT_TOKEN
    for rec in _tg_db:
        if rec.get("file_id") == file_id:
            token = rec.get("token", TELEGRAM_BOT_TOKEN)
            break
    dl_url = _tg_get_file_url(file_id, token)
    if not dl_url:
        return "File not found", 404
    try:
        r = req.get(dl_url, stream=True, timeout=30)
        r.raise_for_status()
        ct = r.headers.get("content-type","application/octet-stream")
        return Response(stream_with_context(r.iter_content(8192)), content_type=ct,
                        headers={"Content-Disposition": f"attachment"})
    except Exception as e:
        return str(e), 502

@app.route("/api/tgthumb/<file_id>")
def tg_thumb(file_id):
    """Get a thumbnail for a Telegram file (photo types only)."""
    token = TELEGRAM_BOT_TOKEN
    for rec in _tg_db:
        if rec.get("file_id") == file_id:
            token = rec.get("token", TELEGRAM_BOT_TOKEN)
            break
    dl_url = _tg_get_file_url(file_id, token)
    if not dl_url:
        return "", 404
    try:
        r = req.get(dl_url, stream=True, timeout=15)
        ct = r.headers.get("content-type","image/jpeg")
        return Response(stream_with_context(r.iter_content(8192)), content_type=ct,
                        headers={"Cache-Control":"public,max-age=86400"})
    except Exception as e:
        return str(e), 502


@app.route("/api/tgrebuild", methods=["POST"])
def tg_rebuild():
    before = len(_tg_db)
    _tg_rebuild_db_from_history()
    return jsonify({"ok": True, "count": len(_tg_db), "new": len(_tg_db) - before})


# ── BLACKLIST API ─────────────────────────────────────────────────────────────

@app.route("/api/blacklist", methods=["GET"])
def get_blacklist():
    return jsonify({"tags": sorted(_tag_blacklist), "seen_count": len(_seen_urls)})

@app.route("/api/blacklist/add", methods=["POST"])
def add_blacklist():
    tags = request.json.get("tags", [])
    if isinstance(tags, str): tags = [tags]
    for t in tags:
        t = t.strip().lower()
        if t: _tag_blacklist.add(t)
    _bl_save()
    return jsonify({"ok": True, "tags": sorted(_tag_blacklist)})

@app.route("/api/blacklist/remove", methods=["POST"])
def remove_blacklist():
    tag = request.json.get("tag","").strip().lower()
    _tag_blacklist.discard(tag)
    _bl_save()
    return jsonify({"ok": True, "tags": sorted(_tag_blacklist)})

@app.route("/api/blacklist/clear_seen", methods=["POST"])
def clear_seen():
    _seen_urls.clear()
    _bl_save()
    return jsonify({"ok": True})

# ── STATS API ─────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def get_stats():
    now = time.time()
    # Compute bytes per minute from speed_log
    recent = [e for e in _stats["speed_log"] if now - e["ts"] < 60]
    bpm    = sum(e["bytes"] for e in recent)
    # Last 24h per-hour download counts for graph
    hourly = {}
    for day, data in _stats["by_day"].items():
        hourly[day] = data
    # Last 7 days
    days7 = {}
    for i in range(7):
        d = time.strftime("%Y-%m-%d", time.gmtime(now - i*86400))
        days7[d] = _stats["by_day"].get(d, {"files":0,"bytes":0})

    uptime_s = int(now - _stats["session_start"])
    return jsonify({
        "total_files":  _stats["total_files"],
        "total_bytes":  _stats["total_bytes"],
        "total_videos": _stats["total_videos"],
        "total_images": _stats["total_images"],
        "errors":       _stats["errors"],
        "by_genre":     _stats["by_genre"],
        "by_source":    _stats["by_source"],
        "days7":        days7,
        "bytes_per_min":bpm,
        "uptime_s":     uptime_s,
        "seen_count":   len(_seen_urls),
        "blacklist_count": len(_tag_blacklist),
    })

@app.route("/api/stats/reset", methods=["POST"])
def reset_stats():
    global _stats
    _stats = {
        "total_files":0,"total_bytes":0,"total_videos":0,"total_images":0,
        "by_genre":{},"by_source":{},"by_day":{},"speed_log":[],"errors":0,
        "session_start": time.time(),
    }
    _stats_save()
    return jsonify({"ok": True})


# ── ROOT ROUTE ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", sites=SUPPORTED_SITES)


# ── RANDOM ENDPOINT ───────────────────────────────────────────────────────────
@app.route("/api/random", methods=["POST"])
def get_random():
    data   = request.json or {}
    mode   = data.get("mode","art")      # art | video | wallpaper | mature
    mature = data.get("mature", False)

    if mode == "video":
        tags = DA_VIDEO_TAGS
    elif mode == "mature" and mature:
        tags = DA_MATURE_TAGS
    elif mode == "wallpaper":
        tags = DA_WALLPAPER_TAGS
    else:
        tags = DA_ART_TAGS

    # Try up to 5 tags until we get results
    for _ in range(5):
        tag   = random.choice(tags)
        items = scrape_da_tag(tag, mature=(mode=="mature" and mature))
        if items:
            item = random.choice(items)
            return jsonify({
                "title":    item.get("title","DeviantArt"),
                "author":   item.get("author","Unknown"),
                "url":      item.get("url",""),
                "thumbnail":item.get("thumbnail",""),
                "type":     item.get("type","image"),
                "is_video": item.get("is_video", False),
                "tag":      tag,
                "source":   "DeviantArt",
            })

    return jsonify({"error": "Could not fetch from DeviantArt. Check your internet and try again."}), 500


# ── INFO ──────────────────────────────────────────────────────────────────────
@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json or {}
    url  = data.get("url","").strip()
    if not url: return jsonify({"error":"No URL provided"})

    if "deviantart.com" in url:
        try:
            oe = req.get(f"https://backend.deviantart.com/oembed?url={quote(url)}&format=json", headers=HEADERS, timeout=10)
            if oe.status_code == 200:
                d = oe.json()
                return jsonify({"type":"image","title":d.get("title",""),"thumbnail":d.get("url") or d.get("thumbnail_url"),
                                "uploader":d.get("author_name",""),"extractor":"DeviantArt",
                                "image_url":d.get("url") or d.get("thumbnail_url"),"width":d.get("width"),"height":d.get("height")})
        except Exception: pass

    if "artstation.com" in url or is_direct_image(url) or is_direct_video(url):
        t = "video" if is_direct_video(url) else "image"
        try:
            r2 = req.get(url, headers=HEADERS, timeout=15)
            og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', r2.text)
            og_t = re.search(r'<meta property="og:title"\s+content="([^"]+)"', r2.text)
            img = og.group(1).replace("&amp;","&") if og else url
            return jsonify({"type":t,"title":og_t.group(1) if og_t else url,"thumbnail":img,"image_url":img,
                            "uploader":urlparse(url).netloc,"extractor":urlparse(url).netloc})
        except Exception:
            return jsonify({"type":t,"title":url.split("/")[-1].split("?")[0],"thumbnail":url if t=="image" else None,
                            "uploader":urlparse(url).netloc,"extractor":"Direct URL","image_url":url})

    if not YT_DLP_AVAILABLE:
        return jsonify({"error": "yt_dlp not installed on server"}), 500
    try:
        with yt_dlp.YoutubeDL({"quiet":True,"no_warnings":True,"skip_download":True}) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                e = info.get("entries",[])
                return jsonify({"type":"playlist","title":info.get("title","Playlist"),"count":len(e),
                                "thumbnail":e[0].get("thumbnail") if e else None,"uploader":info.get("uploader",""),"extractor":info.get("extractor_key","")})
            fmts = [{"quality":f"{f.get('height')}p","ext":f.get("ext")} for f in reversed(info.get("formats",[])) if f.get("height")][:8]
            thumb = info.get("thumbnail") or (info.get("thumbnails") or [{}])[-1].get("url")
            return jsonify({"type":"video" if info.get("duration") else "image","title":info.get("title","Unknown"),
                            "thumbnail":thumb,"uploader":info.get("uploader",""),"duration":info.get("duration"),
                            "width":info.get("width"),"height":info.get("height"),"extractor":info.get("extractor_key",""),
                            "formats":fmts,"view_count":info.get("view_count")})
    except Exception as e:
        # og:image fallback
        try:
            r2 = req.get(url, headers=HEADERS, timeout=15)
            og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', r2.text)
            og_t = re.search(r'<meta property="og:title"\s+content="([^"]+)"', r2.text)
            if og:
                img = og.group(1).replace("&amp;","&")
                return jsonify({"type":"image","title":og_t.group(1) if og_t else url,"thumbnail":img,"image_url":img,
                                "uploader":urlparse(url).netloc,"extractor":urlparse(url).netloc})
        except Exception: pass
        return jsonify({"error": str(e)}), 400


# ── DOWNLOAD ─────────────────────────────────────────────────────────────────
@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json or {}
    url  = data.get("url","").strip()
    if not url: return jsonify({"error":"No URL provided"})
    dl_id = str(uuid.uuid4())[:8]
    active_downloads[dl_id] = {"progress":0,"status":"starting","filename":None,"error":None}
    threading.Thread(target=_do_download,
                     args=(url, data.get("quality","max"), data.get("format","mp4"),
                           data.get("filename","").strip(), data.get("audio_only",False),
                           data.get("no_watermark",True), dl_id), daemon=True).start()
    return jsonify({"dl_id": dl_id})


def _do_download(url, quality, fmt, filename, audio_only, no_wm, dl_id):
    """
    Master download dispatcher — routes through GOD MODE engine.
    Handles every site, every format, every restriction.
    """
    active_downloads[dl_id]["status"] = "fetching"

    def progress(done, total):
        if total:
            active_downloads[dl_id]["progress"] = max(2, min(95, int(done/total*100)))
        active_downloads[dl_id]["status"] = "downloading"

    fname = filename or url.split("/")[-1].split("?")[0].rsplit(".",1)[0] or "media"

    if GOD_MODE:
        # ── USE GOD MODE ENGINE ────────────────────────────────────────────
        ok, fpath, err = _gm.download(
            url       = url,
            dest_dir  = DOWNLOAD_DIR,
            dl_id     = dl_id,
            progress_cb = progress,
            filename  = safe_name(fname),
            quality   = quality or "best",
        )
        if ok and fpath and os.path.exists(fpath):
            fsize = os.path.getsize(fpath)
            active_downloads[dl_id].update({
                "progress": 100, "status": "done",
                "filename": os.path.basename(fpath),
                "filepath": fpath, "filesize": fsize,
            })
            download_history.insert(0, {
                "id": dl_id, "title": os.path.splitext(os.path.basename(fpath))[0],
                "url": url, "filename": os.path.basename(fpath),
                "extractor": "GodMode", "filesize": fsize,
                "thumbnail": url if re.search(r"\.(jpg|png|gif|webp)$", fpath.lower()) else "",
            })
            return
        # God mode failed — store error
        active_downloads[dl_id].update({"status": "error", "error": err or "God mode failed"})
        return

    # ── FALLBACK: legacy path (if god_mode.py missing) ────────────────────
    url_path_only = url.lower().split("?")[0]
    _is_direct_img = bool(re.search(r"\.(jpg|jpeg|png|gif|webp|bmp|avif)$", url_path_only))
    _is_direct_vid = bool(re.search(r"\.(mp4|webm|mkv|avi|mov|flv)$", url_path_only))
    _is_booru_cdn  = any(d in url for d in [
        "rule34.xxx/images","us.rule34.xxx","wimg.rule34.xxx",
        "img2.gelbooru.com","img3.gelbooru.com","cdn.donmai.us",
        "files.yande.re","konachan.com/data","img.xbooru.com",
        "xbooru.com/images","safebooru.org/images","wallhaven.cc/full",
        "img.rule34.paheal.net","wixmp.com",
    ])
    if _is_direct_img or _is_direct_vid or _is_booru_cdn:
        _direct(url, safe_name(fname), dl_id); return
    if "deviantart.com" in url:
        try:
            _ydlp(url, quality, fmt, fname, audio_only, no_wm, dl_id)
            if active_downloads[dl_id].get("status") == "done": return
        except: pass
        media_url, media_type = get_da_media_url(url)
        if media_url:
            if media_type == "video": _ydlp(media_url, quality, fmt, fname, audio_only, no_wm, dl_id)
            else: _direct(media_url, fname, dl_id)
            if active_downloads[dl_id].get("status") == "done": return
        active_downloads[dl_id].update({"status":"error","error":"DeviantArt extraction failed"})
        return
    _ydlp(url, quality, fmt, filename, audio_only, no_wm, dl_id)


def _direct(url, fname, dl_id):
    """
    Download any direct media URL (image or video) with full bypass.
    Handles: booru CDNs, wixmp, rule34, gelbooru, xbooru, large video files.
    """
    try:
        active_downloads[dl_id]["status"] = "downloading"

        # Step 0: Resolve hotlink/redirect wrappers
        if RESOLVER_AVAILABLE:
            resolved = _ur.resolve(url)
            if resolved and resolved != url and resolved.startswith("http"):
                print(f"[DIRECT] Unwrapped {url[:50]} → {resolved[:50]}")
                url = resolved

        # Step 1: Detect type from URL extension
        url_path = url.lower().split("?")[0]
        is_video = any(url_path.endswith(x) for x in (".mp4",".webm",".mkv",".mov",".flv"))
        is_gif   = url_path.endswith(".gif")

        parsed_domain = urlparse(url).netloc.lstrip("www.")

        # Step 2: Build exact headers for this domain — NO Range header
        # (Range causes 206 partial content which corrupts files)
        domain_referers = {
            "rule34.xxx": "https://rule34.xxx/",
            "xbooru.com": "https://xbooru.com/",
            "gelbooru.com": "https://gelbooru.com/",
            "danbooru.donmai.us": "https://danbooru.donmai.us/",
            "donmai.us": "https://danbooru.donmai.us/",
            "konachan.com": "https://konachan.com/",
            "konachan.net": "https://konachan.com/",
            "yande.re": "https://yande.re/",
            "safebooru.org": "https://safebooru.org/",
            "wallhaven.cc": "https://wallhaven.cc/",
            "xbooru.com": "https://xbooru.com/",
            "paheal.net": "https://rule34.paheal.net/",
            "wixmp.com": "https://www.deviantart.com/",
            "deviantart.com": "https://www.deviantart.com/",
            "img.xbooru.com": "https://xbooru.com/",
        }
        referer = "https://www.google.com/"
        for key, ref in domain_referers.items():
            if key in parsed_domain:
                referer = ref
                break

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": referer,
            "Accept": "video/mp4,video/webm,video/*;q=0.9,*/*;q=0.5" if is_video else "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
            "Sec-Fetch-Dest": "video" if is_video else "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        # Step 3: Download with retry — NO Range header to avoid partial/corrupt files
        # (connect_timeout, read_timeout) — never cut off video mid-stream
        timeout = (30, None) if is_video else (20, 90)
        r = None
        last_err = ""
        ua_pool = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        ]

        for attempt in range(5):
            try:
                if attempt > 0:
                    time.sleep(min(2 ** attempt, 10))
                    headers["User-Agent"] = ua_pool[attempt % len(ua_pool)]
                    headers["Cache-Control"] = "no-cache"
                    # On retry 3+, try without Referer (some CDNs block cross-origin)
                    if attempt >= 3:
                        headers.pop("Referer", None)
                        headers.pop("Sec-Fetch-Site", None)

                r = req.get(url, headers=headers, timeout=timeout,
                            stream=True, allow_redirects=True)

                if r.status_code in (200, 206):
                    # If 206 partial, check Content-Range to see if it's from byte 0
                    if r.status_code == 206:
                        cr = r.headers.get("Content-Range","")
                        if cr and not cr.startswith("bytes 0-"):
                            # Not from start — restart without Range
                            r = req.get(url, headers={**headers, "Range": ""}, timeout=timeout,
                                        stream=True, allow_redirects=True)
                    break
                last_err = f"HTTP {r.status_code}"
                r = None

            except req.exceptions.Timeout:
                last_err = f"Timeout after {timeout}s"
                r = None
            except Exception as ex:
                last_err = str(ex)
                r = None

        # Step 4: Fallback to unblock engine if all retries failed
        if r is None and UNBLOCK_AVAILABLE:
            print(f"[DIRECT] Trying unblock engine for {url[:60]}")
            r = _ub.get_response(url)

        if r is None:
            active_downloads[dl_id].update({"status":"error","error":f"Download failed: {last_err}"})
            return

        # Step 5: Determine file extension from content-type + URL
        ct = r.headers.get("content-type","").split(";")[0].strip().lower()

        # Immediate reject if server returns HTML — blocked/redirect page
        if ct in ("text/html", "text/plain", "application/json"):
            # Could be a block page, captcha, or redirect — try unblock engine
            if UNBLOCK_AVAILABLE:
                print(f"[DIRECT] Content-type={ct} — retrying with unblock engine")
                r2 = _ub.get_response(url)
                if r2 and r2.status_code in (200, 206):
                    ct = r2.headers.get("content-type","").split(";")[0].strip().lower()
                    if ct not in ("text/html", "text/plain"):
                        r = r2  # use unblocked response
                    else:
                        active_downloads[dl_id].update({"status":"error","error":f"Site blocked — returned {ct} instead of media"})
                        return
                else:
                    active_downloads[dl_id].update({"status":"error","error":f"Site blocked — returned {ct} even after bypass"})
                    return
            else:
                active_downloads[dl_id].update({"status":"error","error":f"Site blocked — returned {ct} instead of media (no unblock engine)"})
                return

        # Re-check is_video using ACTUAL content-type from server response
        # (URL may have no extension — CDN video URLs often look like image URLs)
        if "video/" in ct or ct in ("application/octet-stream",):
            is_video = True   # server says it's video — trust that
        elif "image/" in ct:
            is_video = False  # server says it's image — trust that

        ext_map = {
            "image/jpeg": ".jpg", "image/jpg": ".jpg",
            "image/png": ".png", "image/gif": ".gif",
            "image/webp": ".webp", "image/avif": ".avif",
            "image/bmp": ".bmp", "image/tiff": ".tiff",
            "video/mp4": ".mp4", "video/webm": ".webm",
            "video/x-matroska": ".mkv", "video/quicktime": ".mov",
            "application/octet-stream": ".mp4",  # always mp4 for binary — safest
        }
        ext = ext_map.get(ct, "")

        # If no ext from CT, try URL path
        if not ext:
            m = re.search(r"\.(jpg|jpeg|png|gif|webp|avif|mp4|webm|mkv|mov|flv)(\?|$)", url_path)
            ext = "." + m.group(1) if m else ""

        # If STILL no ext — check magic bytes from Content-Range or just use type
        if not ext:
            ext = ".mp4" if is_video else ".jpg"

        if ext == ".jpeg": ext = ".jpg"

        # Step 6: Write to file
        # CRITICAL: if is_video is True, NEVER save as image extension
        # This catches CDN URLs that return video/mp4 but URL had no extension
        if is_video and ext in (".jpg",".jpeg",".png",".webp",".avif",".bmp",".gif"):
            ext = ".mp4"

        out = DOWNLOAD_DIR / f"{safe_name(fname)}{ext}"
        c = 1
        while out.exists():
            out = DOWNLOAD_DIR / f"{safe_name(fname)}_{c}{ext}"; c += 1

        total = int(r.headers.get("content-length", 0))
        done = 0

        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1048576):  # 1MB chunks
                if not chunk: continue

                # First chunk: check magic bytes BEFORE writing anything
                if done == 0:
                    sniff = chunk[:32].lstrip()
                    # Reject HTML error pages
                    if sniff[:5].lower() in (b"<!doc", b"<html") or sniff[:6].lower() in (b"<head>", b"<body>"):
                        out.unlink(missing_ok=True)
                        active_downloads[dl_id].update({"status":"error","error":"Site returned HTML error page — likely blocked or login required"})
                        return
                    # Validate magic bytes match expected type
                    if is_video:
                        # MP4 magic: ftyp at offset 4, or starts with 0000
                        # WebM magic: 0x1A 0x45 0xDF 0xA3
                        # GIF magic: GIF8
                        # If first bytes look like text/HTML reject
                        if sniff[:1] in (b"<", b"{"): 
                            out.unlink(missing_ok=True)
                            active_downloads[dl_id].update({"status":"error","error":"CDN returned non-video data (text/JSON) for video URL"})
                            return

                f.write(chunk)
                done += len(chunk)
                if total:
                    active_downloads[dl_id]["progress"] = max(2, min(95, int(done / total * 100)))
                elif done > 0:
                    active_downloads[dl_id].update({"progress": min(90, done // 50000), "speed": f"{done//1024}KB"})

        # Step 7: Validate final file
        fsize = out.stat().st_size
        min_size = 4_096 if is_video else 512   # 4KB min for video, 512B for image
        if fsize < min_size:
            # Read first bytes to check if it's an error page
            with open(out,"rb") as f: head_bytes = f.read(64)
            out.unlink(missing_ok=True)
            if b"<html" in head_bytes.lower() or b"error" in head_bytes.lower():
                active_downloads[dl_id].update({"status":"error","error":"Site blocked download — returned error page"})
            else:
                active_downloads[dl_id].update({"status":"error","error":f"File too small ({fsize} bytes) — download incomplete"})
            return

        thumb = url if ext in (".jpg",".png",".webp",".gif",".avif") else ""
        active_downloads[dl_id].update({
            "progress": 100, "status": "done",
            "filename": out.name, "filepath": str(out), "filesize": fsize,
        })
        download_history.insert(0, {
            "id": dl_id, "title": out.stem, "url": url,
            "filename": out.name, "extractor": "Direct",
            "filesize": fsize, "thumbnail": thumb,
        })
        print(f"[DIRECT] ✅ {out.name} ({fsize//1024}KB)")

    except Exception as e:
        import traceback
        print(f"[DIRECT] ERROR: {e}\n{traceback.format_exc()}")
        active_downloads[dl_id].update({"status":"error","error":str(e)})


def _ydlp(url, quality, fmt, filename, audio_only, no_wm, dl_id):
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate",0); dl = d.get("downloaded_bytes",0)
            speed = d.get("speed",0)
            active_downloads[dl_id].update({"progress":int(dl/total*100) if total else 0,"status":"downloading",
                                             "speed":f"{speed/1024/1024:.1f} MB/s" if speed else "—","eta":d.get("eta",0)})
        elif d["status"] == "finished": active_downloads[dl_id]["status"] = "processing"

    out_tmpl = str(DOWNLOAD_DIR / (f"{safe_name(filename)}.%(ext)s" if filename else "%(title)s.%(ext)s"))
    fmt_str  = "bestaudio/best" if audio_only else QUALITY_MAP.get(quality, QUALITY_MAP["max"])
    if no_wm and "tiktok" in url.lower(): fmt_str = "download_addr-0"
    opts = {"format":fmt_str,"outtmpl":out_tmpl,"progress_hooks":[hook],"quiet":True,"no_warnings":True,
            "merge_output_format": fmt if fmt in ("mp4","mkv","webm") else "mp4",
            "postprocessors":[],"writethumbnail":False,"noplaylist":True}
    if audio_only: opts["postprocessors"].append({"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"320"})
    if not YT_DLP_AVAILABLE:
        active_downloads[dl_id].update({"status":"error","error":"yt_dlp not installed on server"}); return
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url); fname = ydl.prepare_filename(info)
            for ext in [".mp4",".mkv",".webm",".mp3",".jpg",".png",".webp",".gif"]:
                c = Path(fname).with_suffix(ext)
                if c.exists(): fname = str(c); break
            fsize = os.path.getsize(fname) if os.path.exists(fname) else 0
            active_downloads[dl_id].update({"progress":100,"status":"done","filename":os.path.basename(fname),
                                             "filepath":fname,"filesize":fsize,"title":info.get("title",""),"extractor":info.get("extractor_key","")})
            download_history.insert(0,{"id":dl_id,"title":info.get("title",""),"url":url,"filename":os.path.basename(fname),
                                        "extractor":info.get("extractor_key",""),"filesize":fsize,"thumbnail":info.get("thumbnail","")})
    except Exception as e:
        # og:image fallback
        try:
            r2 = req.get(url, headers=HEADERS, timeout=15)
            og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', r2.text)
            if og: _direct(og.group(1).replace("&amp;","&"), filename or "media", dl_id)
            else:  active_downloads[dl_id].update({"status":"error","error":str(e)})
        except Exception as e2:
            active_downloads[dl_id].update({"status":"error","error":str(e2)})


@app.route("/api/progress/<dl_id>")
def get_progress(dl_id): return jsonify(active_downloads.get(dl_id,{"status":"not_found"}))

@app.route("/api/file/<dl_id>")
def serve_file(dl_id):
    info = active_downloads.get(dl_id,{}); fp = info.get("filepath")
    if not fp or not os.path.exists(fp): return jsonify({"error":"File not found"}), 404
    resp = send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
    # Only auto-delete on cloud (saves /tmp space); keep files locally
    if _is_cloud:
        @resp.call_on_close
        def cleanup():
            try:
                if os.path.exists(fp): os.remove(fp)
            except Exception: pass
    return resp

@app.route("/api/history")
def get_history(): return jsonify(download_history)

@app.route("/api/files")
def list_files():
    files = [{"name":f.name,"size":f.stat().st_size,"ext":f.suffix} for f in DOWNLOAD_DIR.iterdir() if f.is_file()]
    return jsonify(sorted(files, key=lambda x: x["name"]))

@app.route("/api/delete/<filename>", methods=["DELETE"])
def delete_file(filename):
    fp = DOWNLOAD_DIR / filename
    if fp.exists(): fp.unlink(); return jsonify({"ok":True})
    return jsonify({"error":"Not found"}), 404

@app.route("/api/open_folder")
def open_folder():
    if _is_cloud:
        return jsonify({"ok": False, "msg": "Cannot open folder on cloud server"})
    folder = str(DOWNLOAD_DIR.resolve())
    if sys.platform=="win32": os.startfile(folder)
    elif sys.platform=="darwin": os.system(f'open "{folder}"')
    else: os.system(f'xdg-open "{folder}"')
    return jsonify({"ok":True})


AUTO_GENRES = {
    "anime_wallpaper": {
        "label": "🌸 Anime Wallpaper",
        "tags": ["wallpaper","anime_wallpaper","scenery","landscape","1920x1080",
                 "no_humans","sky","nature","city","beautiful_detailed_sky",
                 "cherry_blossoms","sunset","night_sky","stars","ocean"],
        "mode": "image", "mature": False,
    },
    "live_wallpaper": {
        # Tags that actually produce animated .gif/.mp4 on rule34/gelbooru
        "label": "✨ Live Wallpaper",
        "tags": ["animated","animated_gif","loop","cinemagraph",
                 "rain","fire","water","lightning","magic","neon","cyberpunk"],
        "mode": "video", "mature": False,
        "sources": ["rule34","gelbooru"],   # only sources that have animated content
    },
    "mature_art": {
        "label": "🔞 Mature Art",
        "tags": ["pinup","bikini","lingerie","ecchi","boudoir","large_breasts",
                 "cleavage","thighhighs","swimsuit","underwear","topless","nude"],
        "mode": "image", "mature": True,
    },
    "mature_video": {
        "label": "🔞 18+ Video",
        # These tags on rule34/gelbooru/xbooru reliably return mp4 results
        "tags": ["animated","3d","mmd","sex","hentai","blowjob","cum",
                 "penetration","vaginal","ahegao","moaning","orgasm"],
        "mode": "video", "mature": True,
        "sources": ["rule34","gelbooru","xbooru"],
    },
    "live_wallpaper_18": {
        "label": "🔞 Live Wallpaper 18+",
        "tags": ["animated","loop","mmd","3d","bouncing_breasts",
                 "jiggle","strip","undressing","nude","ecchi"],
        "mode": "video", "mature": True,
        "sources": ["rule34","gelbooru","xbooru"],
    },
    "anime_art": {
        "label": "🎨 Anime Art",
        "tags": ["anime","1girl","solo","highres","looking_at_viewer","smile",
                 "school_uniform","maid","fox_girl","cat_girl","elf","demon_girl"],
        "mode": "image", "mature": False,
    },
    "fantasy": {
        "label": "🏰 Fantasy",
        "tags": ["fantasy","dragon","elf","wizard","magic","armor","sword",
                 "dark_fantasy","monster_girl","knight","witch","fairy"],
        "mode": "image", "mature": False,
    },
    "scifi": {
        "label": "🚀 Sci-Fi",
        "tags": ["cyberpunk","mecha","robot","space","futuristic","android",
                 "science_fiction","power_armor","spaceship","alien","dystopia"],
        "mode": "image", "mature": False,
    },
    "video_animation": {
        "label": "🎬 Animation Video",
        # Specific tags that produce animated content on boorus
        "tags": ["animated","mmd","3d_animation","animated_gif","loop",
                 "dance","fight","running","motion_blur"],
        "mode": "video", "mature": False,
        "sources": ["rule34","gelbooru"],
    },
}

# Auto-downloader state
auto_state = {
    "running":    False,
    "paused":     False,
    "enabled_genres": list(AUTO_GENRES.keys()),  # all on by default
    "current":    None,
    "queue":      [],
    "done":       [],
    "errors":     [],
    "total":      0,
    "speed":      "—",
    "delay":      8,   # seconds between downloads
}

_auto_lock = threading.Lock()


def _auto_fetch_item(genre_key):
    """Fetch one random item for a genre — strictly filtered by mode."""
    genre      = AUTO_GENRES[genre_key]
    mode       = genre["mode"]          # "video" | "image" | "any"
    video_only = (mode == "video")
    image_only = (mode == "image")

    # Try up to 5 tags before giving up
    for _ in range(5):
        tag   = random.choice(genre["tags"])

        # Skip blacklisted tags
        if _is_blacklisted(tag):
            continue

        # Use genre-specific source list if defined (video genres need specific sources)
        genre_sources = genre.get("sources", None)
        items = scrape_da_tag(
            tag,
            mature     = genre["mature"],
            video_only = video_only,
            image_only = image_only,
            sources    = genre_sources,
        )
        if not items:
            continue

        # Extra hard filter — make sure mode matches
        if video_only:
            items = [i for i in items if i.get("is_video") or
                     any(i["url"].lower().endswith(x) for x in [".mp4",".webm",".mov",".gif"])]
        elif image_only:
            items = [i for i in items if not i.get("is_video") and
                     not any(i["url"].lower().endswith(x) for x in [".mp4",".webm",".mov"])]

        # Remove already-seen URLs
        items = [i for i in items if not _is_seen(i["url"])]

        if not items:
            continue

        item = random.choice(items)
        _mark_seen(item["url"])
        return {
            "genre_key":   genre_key,
            "genre_label": genre["label"],
            "tag":         tag,
            "url":         item["url"],
            "thumbnail":   item.get("thumbnail", item["url"]),
            "title":       item.get("title",""),
            "author":      item.get("author",""),
            "mode":        mode,
            "mature":      genre["mature"],
            "is_video":    item.get("is_video", False),
            "direct":      item.get("direct", True),
            "score":       item.get("score", 0),
        }
    return None


def _auto_download_item(item):
    """Download one item at max quality."""
    dl_id = "auto_" + str(uuid.uuid4())[:8]
    active_downloads[dl_id] = {"progress":0,"status":"starting","filename":None,"error":None}
    url   = item["url"]
    fname = safe_name(f"{item['genre_key']}_{item['title'] or item['tag']}_{dl_id}")

    # Step 0: Resolve any wrapper URL first so size check hits real CDN
    if RESOLVER_AVAILABLE:
        resolved = _ur.resolve(url)
        if resolved and resolved != url and resolved.startswith("http"):
            url = resolved
            item = dict(item); item["url"] = url  # update item too

    # Only reject video URLs in image-only genres (no size gate — kills too many valid videos)
    if item.get("mode") == "image":
        if any(url.lower().split("?")[0].endswith(x) for x in [".mp4",".webm",".mov",".flv"]):
            active_downloads[dl_id].update({"status":"error","error":"Wrong type (video in image genre), skipped"})
            return False, None, "wrong type"

    _do_download(url, "max", "mp4", fname, False, True, dl_id)

    status    = active_downloads[dl_id].get("status")
    fname_out = active_downloads[dl_id].get("filename", "")
    if status == "done" and fname_out:
        # Detect source from item title
        src = "unknown"
        for s in ["Rule34","Gelbooru","Danbooru","Konachan","Yande.re","Wallhaven","Safebooru","Xbooru"]:
            if s.lower() in item.get("title","").lower():
                src = s; break
        fpath = DOWNLOAD_DIR / fname_out if fname_out and not Path(fname_out).is_absolute() else Path(fname_out or "")
        _stats_record(str(fpath), genre_key=item.get("genre_key","auto"), source=src)
    else:
        _stats["errors"] += 1
    return status == "done", fname_out, active_downloads[dl_id].get("error","")


def _auto_engine():
    """Main 24/7 download loop."""
    print("🤖 Auto-downloader started")
    while auto_state["running"]:
        if auto_state["paused"] or not auto_state["enabled_genres"]:
            time.sleep(2)
            continue

        genre_key = random.choice(auto_state["enabled_genres"])
        try:
            item = _auto_fetch_item(genre_key)
            if not item:
                time.sleep(5)
                continue

            auto_state["current"] = {
                "genre": AUTO_GENRES[genre_key]["label"],
                "title": item["title"],
                "author": item["author"],
                "tag": item["tag"],
                "status": "downloading",
            }

            ok, fname, err = _auto_download_item(item)

            if ok:
                auto_state["total"] += 1
                entry = {
                    "genre":     AUTO_GENRES[genre_key]["label"],
                    "title":     item["title"],
                    "author":    item["author"],
                    "tag":       item["tag"],
                    "file":      fname,
                    "url":       item["url"],
                    "thumbnail": item.get("thumbnail", ""),
                    "is_video":  item.get("is_video", False),
                    "time":      time.strftime("%H:%M:%S"),
                }
                with _auto_lock:
                    auto_state["done"].insert(0, entry)
                    if len(auto_state["done"]) > 200:
                        auto_state["done"] = auto_state["done"][:200]
                print(f"✅ Auto [{entry['genre']}] {fname}")
                # ── Send to Telegram for permanent storage ──
                filepath = None
                for did, dinfo in active_downloads.items():
                    if dinfo.get("filename") == fname and dinfo.get("filepath"):
                        filepath = dinfo["filepath"]; break
                if filepath:
                    tg_queue_file(filepath,
                        genre  = AUTO_GENRES[genre_key]["label"],
                        title  = item["title"],
                        author = item["author"],
                        tag    = item["tag"])
            else:
                with _auto_lock:
                    auto_state["errors"].insert(0, {
                        "genre": AUTO_GENRES[genre_key]["label"],
                        "url":   item["url"],
                        "error": err or "Unknown error",
                        "time":  time.strftime("%H:%M:%S"),
                    })
                    if len(auto_state["errors"]) > 50:
                        auto_state["errors"] = auto_state["errors"][:50]
                print(f"❌ Auto [{AUTO_GENRES[genre_key]['label']}] failed: {err}")

        except Exception as e:
            import traceback
            print(f"Auto engine error: {e}\n{traceback.format_exc()}")

        auto_state["current"] = None
        # Short wait on error, normal wait on success
        wait = auto_state["delay"] if ok else 3
        for _ in range(wait * 2):
            if not auto_state["running"]: break
            time.sleep(0.5)

    print("🛑 Auto-downloader stopped")


# ── AUTO API ROUTES ────────────────────────────────────────────────────────────

@app.route("/api/auto/start", methods=["POST"])
def auto_start():
    if auto_state["running"]:
        return jsonify({"ok": True, "msg": "Already running"})
    auto_state["running"] = True
    auto_state["paused"]  = False
    threading.Thread(target=_auto_engine, daemon=True).start()
    return jsonify({"ok": True, "msg": "Auto-downloader started"})

@app.route("/api/auto/stop", methods=["POST"])
def auto_stop():
    auto_state["running"] = False
    auto_state["current"] = None
    return jsonify({"ok": True, "msg": "Stopped"})

@app.route("/api/auto/pause", methods=["POST"])
def auto_pause():
    auto_state["paused"] = not auto_state["paused"]
    return jsonify({"ok": True, "paused": auto_state["paused"]})

@app.route("/api/auto/status")
def auto_status():
    return jsonify({
        "running":  auto_state["running"],
        "paused":   auto_state["paused"],
        "current":  auto_state["current"],
        "total":    auto_state["total"],
        "done":     auto_state["done"][:20],
        "errors":   auto_state["errors"][:10],
        "enabled_genres": auto_state["enabled_genres"],
        "genres":   {k: AUTO_GENRES[k]["label"] for k in AUTO_GENRES},
        "delay":    auto_state["delay"],
        "disk_mb":  round(sum(f.stat().st_size for f in DOWNLOAD_DIR.iterdir() if f.is_file()) / 1048576, 1) if DOWNLOAD_DIR.exists() else 0,
    })

@app.route("/api/auto/genres", methods=["POST"])
def auto_set_genres():
    data = request.json or {}
    genres = [g for g in data.get("genres", []) if g in AUTO_GENRES]
    auto_state["enabled_genres"] = genres or list(AUTO_GENRES.keys())
    return jsonify({"ok": True, "enabled": auto_state["enabled_genres"]})

@app.route("/api/auto/delay", methods=["POST"])
def auto_set_delay():
    data  = request.json or {}
    delay = max(3, min(60, int(data.get("delay", 8))))
    auto_state["delay"] = delay
    return jsonify({"ok": True, "delay": delay})


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM PERMANENT DATABASE
#  Stores file_id of every uploaded file — survives Railway restarts
#  On restart, files are gone from /tmp but Telegram keeps them forever
#  The db is rebuilt by fetching history from the TG channel on startup
# ══════════════════════════════════════════════════════════════════════════════


def _tg_db_load():
    global _tg_db
    try:
        if _TG_DB_FILE.exists():
            _tg_db = json.loads(_TG_DB_FILE.read_text())
            print(f"[TG-DB] Loaded {len(_tg_db)} records")
    except Exception as e:
        print(f"[TG-DB] Load error: {e}")
        _tg_db = []

def _tg_db_save(record: dict):
    """Append one record and persist."""
    _tg_db.append(record)
    try:
        _TG_DB_FILE.write_text(json.dumps(_tg_db, indent=2))
    except Exception as e:
        print(f"[TG-DB] Save error: {e}")

def _tg_get_file_url(file_id: str, token: str) -> str:
    """Get a direct download URL for a Telegram file_id."""
    try:
        r = req.get(f"https://api.telegram.org/bot{token}/getFile",
                    params={"file_id": file_id}, timeout=10)
        if r.status_code == 200:
            path = r.json()["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{token}/{path}"
    except Exception as e:
        print(f"[TG-DB] getFile error: {e}")
    return ""

def _tg_rebuild_db_from_history():
    """
    On startup, fetch recent messages from the TG channel and 
    rebuild the db from file_ids found there.
    """
    global _tg_db
    token, chat_id = _get_active_tg()
    if not token or not chat_id:
        return
    try:
        r = req.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"limit": 100, "allowed_updates": ["channel_post","message"]},
            timeout=15
        )
        if r.status_code != 200:
            return
        updates = r.json().get("result", [])
        rebuilt = 0
        for upd in updates:
            msg = upd.get("message") or upd.get("channel_post") or {}
            for ftype in ("video","photo","document","animation","audio"):
                obj = msg.get(ftype)
                if obj:
                    if isinstance(obj, list): obj = obj[-1]
                    file_id = obj.get("file_id","")
                    if file_id and not any(r["file_id"]==file_id for r in _tg_db):
                        _tg_db.append({
                            "file_id":   file_id,
                            "type":      ftype,
                            "name":      obj.get("file_name", f"tg_{ftype}_{rebuilt}"),
                            "size":      obj.get("file_size", 0),
                            "caption":   (msg.get("caption") or "")[:200],
                            "timestamp": msg.get("date", time.time()),
                            "chat_id":   chat_id,
                            "token":     token,
                            "msg_id":    msg.get("message_id",""),
                        })
                        rebuilt += 1
        if rebuilt:
            _TG_DB_FILE.write_text(json.dumps(_tg_db, indent=2))
            print(f"[TG-DB] Rebuilt {rebuilt} records from TG history")
    except Exception as e:
        print(f"[TG-DB] Rebuild error: {e}")

# Load db on startup
_tg_db_load()


# ── SOURCE SELECTION API ───────────────────────────────────────────────────────

@app.route("/api/sources", methods=["GET"])
def get_sources():
    return jsonify({k: {
        "label":   v["label"],
        "enabled": v["enabled"],
        "mature":  v["mature"],
        "video":   v["video"],
        "image":   v["image"],
    } for k, v in SOURCES_CONFIG.items()})

@app.route("/api/sources", methods=["POST"])
def set_sources():
    data = request.json or {}
    for key, enabled in data.items():
        if key in SOURCES_CONFIG:
            SOURCES_CONFIG[key]["enabled"] = bool(enabled)
    return jsonify({k: v["enabled"] for k, v in SOURCES_CONFIG.items()})


# ── TELEGRAM CHANNEL SELECTOR API ─────────────────────────────────────────────
# Allows switching which Telegram channel to send files to at runtime


@app.route("/api/telegram/channels", methods=["GET"])
def tg_get_channels():
    safe = {k: {"name": k, "chat_id": v.get("chat_id",""), "active": k == _tg_active_channel}
            for k, v in _tg_channels.items()}
    safe["default"] = {
        "name": "default",
        "chat_id": TELEGRAM_CHAT_ID,
        "active": _tg_active_channel == "default"
    }
    return jsonify({"channels": safe, "active": _tg_active_channel})

@app.route("/api/telegram/channels", methods=["POST"])
def tg_add_channel():
    global _tg_active_channel
    data    = request.json or {}
    name    = data.get("name","").strip()
    token   = data.get("token","").strip()
    chat_id = data.get("chat_id","").strip()
    active  = data.get("active", False)
    if not name or not chat_id:
        return jsonify({"ok": False, "error": "name and chat_id required"}), 400
    _tg_channels[name] = {"token": token or TELEGRAM_BOT_TOKEN, "chat_id": chat_id}
    if active:
        _tg_active_channel = name
    return jsonify({"ok": True, "channels": list(_tg_channels.keys()) + ["default"]})

@app.route("/api/telegram/channels/select", methods=["POST"])
def tg_select_channel():
    global _tg_active_channel
    data = request.json or {}
    name = data.get("name","").strip()
    if name != "default" and name not in _tg_channels:
        return jsonify({"ok": False, "error": "Channel not found"}), 404
    _tg_active_channel = name
    return jsonify({"ok": True, "active": _tg_active_channel})

@app.route("/api/telegram/channels/delete", methods=["POST"])
def tg_delete_channel():
    global _tg_active_channel
    data = request.json or {}
    name = data.get("name","").strip()
    if name == "default":
        return jsonify({"ok": False, "error": "Cannot delete default channel"}), 400
    _tg_channels.pop(name, None)
    if _tg_active_channel == name:
        _tg_active_channel = "default"
    return jsonify({"ok": True})



# ══════════════════════════════════════════════════════════════════════════════
#  PERMANENT STORAGE — TELEGRAM AUTO-UPLOAD
#  Every downloaded file is instantly sent to your Telegram channel.
#  Files survive Railway restarts, wipes, redeploys — forever.
#  Setup: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway env vars.
# ══════════════════════════════════════════════════════════════════════════════

print(f"[TG] Token: {(TELEGRAM_BOT_TOKEN[:10]+'...') if TELEGRAM_BOT_TOKEN else 'NOT SET'} | Chat ID: {TELEGRAM_CHAT_ID or 'NOT SET'}")



def _tg_send_file(filepath, caption=""):
    """Upload one file to Telegram. Returns True on success."""
    token, chat_id = _get_active_tg()
    if not token or not chat_id:
        return False
    try:
        fsize_mb = os.path.getsize(filepath) / 1048576
        ext      = Path(filepath).suffix.lower()
        base_url = f"https://api.telegram.org/bot{token}"

        # Pick the right Telegram method based on file type
        if ext in (".mp4", ".webm", ".mkv", ".mov"):
            method   = "sendVideo"
            field    = "video"
        elif ext in (".jpg", ".jpeg", ".png", ".webp"):
            method   = "sendPhoto"
            field    = "photo"
        elif ext in (".gif",):
            method   = "sendAnimation"
            field    = "animation"
        elif ext in (".mp3", ".ogg", ".m4a", ".flac"):
            method   = "sendAudio"
            field    = "audio"
        else:
            method   = "sendDocument"
            field    = "document"

        if fsize_mb > TG_MAX_FILE_MB:
            # File too large — send a text notification instead
            msg = (f"⚠️ File too large to send ({fsize_mb:.1f} MB):\n"
                   f"`{Path(filepath).name}`\n{caption}")
            req.post(f"{base_url}/sendMessage",
                     data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                     timeout=15)
            return True

        with open(filepath, "rb") as f:
            r = req.post(
                f"{base_url}/{method}",
                data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "Markdown"},
                files={field: f},
                timeout=120
            )
        if r.status_code == 200:
            print(f"📤 Telegram: sent {Path(filepath).name}")
            # Store file_id in permanent db for later retrieval
            try:
                msg      = r.json()
                result   = msg.get("result", {})
                # Extract file_id from whichever type was sent
                for ftype in ("video","photo","document","animation","audio"):
                    obj = result.get(ftype)
                    if obj:
                        if isinstance(obj, list): obj = obj[-1]  # photo = list, take largest
                        file_id   = obj.get("file_id","")
                        file_size = obj.get("file_size", 0)
                        tg_record = {
                            "file_id":   file_id,
                            "type":      ftype,
                            "name":      Path(filepath).name,
                            "size":      file_size,
                            "caption":   caption[:200],
                            "timestamp": time.time(),
                            "chat_id":   chat_id,
                            "token":     token,
                            "msg_id":    result.get("message_id",""),
                        }
                        _tg_db_save(tg_record)
                        break
            except Exception as db_e:
                print(f"TG db save error: {db_e}")
            return True
        else:
            resp_json = {}
            try: resp_json = r.json()
            except: pass
            err_code = resp_json.get("error_code", r.status_code)
            err_desc = resp_json.get("description", r.text[:100])
            if err_code == 404:
                # 404 = wrong bot token or the sendVideo/sendPhoto endpoint doesn't exist
                token_preview = token[:10] + "..." if token else "EMPTY"
                print(f"Telegram 404: bad token ({token_preview}) or chat_id ({chat_id}). Check Railway env vars.")
            else:
                print(f"Telegram error {err_code}: {err_desc}")
            return False
    except Exception as e:
        print(f"Telegram upload error: {e}")
        return False


def _tg_worker():
    """Background thread — drains the upload queue."""
    while True:
        item = None
        with _tg_lock:
            if _tg_queue:
                item = _tg_queue.pop(0)
        if item:
            fp, caption = item
            if os.path.exists(fp):
                ok = _tg_send_file(fp, caption)
                if ok:
                    # Delete from /tmp after successful upload
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
                else:
                    # Re-queue once on failure
                    with _tg_lock:
                        _tg_queue.append(item)
            time.sleep(2)
        else:
            time.sleep(3)


def tg_queue_file(filepath, genre="", title="", author="", tag=""):
    """Add a file to the Telegram upload queue."""
    if not tg_enabled() or not filepath or not os.path.exists(filepath):
        return
    _ensure_tg_worker()
    caption = (
        f"🎴 *{title or Path(filepath).stem}*\n"
        f"👤 {author or 'Unknown'}\n"
        f"🏷 `{tag}`   📂 {genre}\n"
        f"📁 `{Path(filepath).name}`"
    )
    with _tg_lock:
        _tg_queue.append((filepath, caption))


# Start Telegram worker thread only if token is configured
def _tg_hook_direct(url, fname, dl_id):
    """Wrapper around _direct that queues file for Telegram after download."""
    _direct(url, fname, dl_id)
    info = active_downloads.get(dl_id, {})
    if info.get("status") == "done" and info.get("filepath"):
        tg_queue_file(info["filepath"])


# ── Telegram status + config API ──────────────────────────────────────────────

@app.route("/api/telegram/status")
def tg_status():
    token, chat_id = _get_active_tg()
    return jsonify({
        "enabled":        bool(token and chat_id),
        "bot_set":        bool(token),
        "chat_set":       bool(chat_id),
        "queue_len":      len(_tg_queue),
        "active_channel": _tg_active_channel,
    })

@app.route("/api/telegram/test", methods=["POST"])
def tg_test():
    if not tg_enabled():
        return jsonify({"ok": False, "error": "Bot token or chat ID not set"}), 400
    token, chat_id = _get_active_tg()
    if not token or not chat_id:
        return jsonify({"ok": False, "error": "No token or chat ID configured"}), 400
    try:
        r = req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id,
                  "text": "✅ *Media Vault* connected!\nDownloads will be sent here automatically.",
                  "parse_mode": "Markdown"},
            timeout=15
        )
        if r.status_code == 200:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": r.text}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



# ══════════════════════════════════════════════════════════════════════════════
#  UNBLOCK ENGINE API ROUTES
#  Custom 7-strategy waterfall for bypassing 403/429/Cloudflare
# ══════════════════════════════════════════════════════════════════════════════


@app.route("/api/resolve", methods=["POST"])
def resolve_url():
    """Resolve a hotlink/redirect/wrapper URL to the real direct URL."""
    data = request.json or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not RESOLVER_AVAILABLE:
        return jsonify({"original": url, "resolved": url, "changed": False, "error": "resolver not loaded"})
    is_wrapper = _ur.is_wrapper_url(url)
    resolved   = _ur.resolve(url) if is_wrapper else url
    return jsonify({
        "original":   url,
        "resolved":   resolved,
        "changed":    resolved != url,
        "is_wrapper": is_wrapper,
    })

@app.route("/api/unblock/test", methods=["POST"])
def unblock_test():
    """Test if a URL is reachable using the unblock engine."""
    data = request.json or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not UNBLOCK_AVAILABLE:
        return jsonify({"error": "Unblock engine not loaded"}), 500
    result = _ub.domain_ok(url)
    return jsonify(result)

@app.route("/api/unblock/status")
def unblock_status():
    """Return unblock engine status and active proxy."""
    proxy = _ub._proxy_url if UNBLOCK_AVAILABLE else None
    return jsonify({
        "available": UNBLOCK_AVAILABLE,
        "strategies": 7,
        "proxy": proxy,
        "session_count": len(_ub._sessions) if UNBLOCK_AVAILABLE else 0,
    })

@app.route("/api/unblock/proxy", methods=["POST"])
def unblock_set_proxy():
    """Set a SOCKS5/HTTP proxy for the unblock engine."""
    data  = request.json or {}
    proxy = data.get("proxy", "").strip()
    if not UNBLOCK_AVAILABLE:
        return jsonify({"error": "Unblock engine not loaded"}), 500
    _ub.set_proxy(proxy)
    return jsonify({"ok": True, "proxy": proxy or None})

@app.route("/api/unblock/clear_sessions", methods=["POST"])
def unblock_clear_sessions():
    """Clear all cached sessions (forces fresh connections)."""
    if UNBLOCK_AVAILABLE:
        _ub._sessions.clear()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    # Local: bind to 127.0.0.1 to avoid Windows Firewall popups
    # Cloud: bind to 0.0.0.0
    host = "0.0.0.0" if _is_cloud else "127.0.0.1"
    print(f"\n{'='*52}")
    print(f"  MEDIA VAULT  —  http://localhost:{port}")
    print(f"  Downloads: {DOWNLOAD_DIR.resolve()}")
    print(f"{'='*52}\n")
    # Open browser 2 seconds after server starts
    threading.Thread(
        target=lambda: (time.sleep(2), webbrowser.open(f"http://localhost:{port}")),
        daemon=True
    ).start()
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"\n[ERROR] Could not start server: {e}")
        print("Try changing port 5050 to another number in app.py")
        input("Press Enter to exit...")


# ══════════════════════════════════════════════════════════════════════════════
#  24/7 AUTO-DOWNLOADER ENGINE
#  Continuously downloads anime wallpapers, live wallpapers, mature art,
#  18+ videos, and any genre at max quality — runs forever in background
# ══════════════════════════════════════════════════════════════════════════════

