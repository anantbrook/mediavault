import os, sys, json, threading, webbrowser, time, re, uuid, random
from pathlib import Path
from urllib.parse import urlparse, quote
from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
import requests as req
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
    """Generic booru API fetcher (Gelbooru/Danbooru/Konachan/Yande.re style)."""
    results = []
    try:
        r = req.get(base_url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=10)
        if r.status_code != 200: return results
        # Check it's actually JSON before parsing
        ct   = r.headers.get("content-type","")
        text = r.text.strip()
        if not text or text[0] not in ("[","{") or "html" in ct or "xml" in ct:
            return results
        data = r.json()
        posts = []
        if isinstance(data, list): posts = data
        elif isinstance(data, dict):
            for k in (json_key, "post", "posts", "data", "results"):
                if k and k in data:
                    posts = data[k]; break
        for p in posts[:25]:
            if not isinstance(p, dict): continue
            file_url = p.get("file_url") or p.get("large_file_url") or p.get("source","")
            if not file_url or not file_url.startswith("http"): continue
            preview = p.get("preview_url") or p.get("preview_file_url") or p.get("sample_url") or file_url
            is_video = any(file_url.lower().endswith(x) for x in [".mp4",".webm",".mov"])
            results.append({
                "title":  f"{label} · {tag}",
                "author": str(p.get("owner","") or p.get("uploader_id","") or ""),
                "url":    file_url,
                "thumbnail": preview,
                "is_video": is_video,
                "type": "video" if is_video else "image",
                "direct": True,
                "score": int(p.get("score",0) or p.get("up_score",0) or 0),
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
    Permanent fix for wixmp 404s:
    Always fetch a FRESH download URL at call-time via multiple strategies.
    Never return a stored/cached wixmp token URL — they expire in ~60s.
    """
    # ── Strategy 1: oEmbed API (most reliable, returns fresh URL every call) ──
    try:
        oe = req.get(
            f"https://backend.deviantart.com/oembed?url={quote(page_url)}&format=json",
            headers=HEADERS, timeout=12
        )
        if oe.status_code == 200:
            d = oe.json()
            url = d.get("url") or d.get("thumbnail_url") or (d.get("media",{}).get("gif","") if isinstance(d.get("media"),dict) else None)
            if url and url.startswith("http"):
                media_type = "video" if any(x in url for x in [".mp4",".webm"]) else "image"
                return url, media_type
    except Exception as e:
        print("oEmbed error:", e)

    # ── Strategy 2: Scrape page for __NEXT_DATA__ and extract fresh download URL ──
    try:
        r = req.get(page_url, headers=HEADERS, timeout=15)
        html = r.text

        # Video first
        v = re.search(r'"src"\s*:\s*"(https://wixmp[^"]+\.mp4[^"]*)"', html)
        if v:
            raw = v.group(1).replace("\\u002F","/").replace("\\/","/")
            # Re-validate immediately — if token expired, skip
            try:
                hd = req.head(raw, headers=HEADERS, timeout=8, allow_redirects=True)
                if hd.status_code == 200:
                    return raw, "video"
            except Exception: pass

        # __NEXT_DATA__ — look for download URL specifically (not preview)
        nd = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if nd:
            def find_download_url(obj, depth=0):
                if depth > 15: return None
                if isinstance(obj, dict):
                    # Prefer keys that indicate full/original resolution
                    for k in ("src","downloadUrl","download_url","fullview","original"):
                        v = obj.get(k)
                        if isinstance(v, str) and v.startswith("http") and any(x in v.lower() for x in [".jpg",".jpeg",".png",".webp",".gif",".mp4"]):
                            return v
                    for v in obj.values():
                        r2 = find_download_url(v, depth+1)
                        if r2: return r2
                elif isinstance(obj, list):
                    for item in obj:
                        r2 = find_download_url(item, depth+1)
                        if r2: return r2
                return None
            try:
                found = find_download_url(json.loads(nd.group(1)))
                if found:
                    # Validate the URL is still alive before returning
                    try:
                        hd = req.head(found, headers=HEADERS, timeout=8, allow_redirects=True)
                        if hd.status_code == 200:
                            mtype = "video" if any(x in found for x in [".mp4",".webm"]) else "image"
                            return found, mtype
                    except Exception:
                        pass  # token expired — fall through to og:image
            except Exception: pass

        # ── Strategy 3: og:image (always a fresh CDN URL, lower res but reliable) ──
        og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html)
        if og:
            img = og.group(1).replace("&amp;","&")
            # Strip resize params to get higher res
            img = re.sub(r'/v1/fill/w_\d+,h_\d+[^/]*/', '/', img)
            return img, "image"

    except Exception as e:
        print("get_da_media_url error:", e)

    return None, None



# ── THUMBNAIL PROXY — fetches wixmp image server-side so tokens never expire ──
@app.route("/api/thumb")
def thumb_proxy():
    url = request.args.get("url","").strip()
    if not url or "wixmp" not in url and "deviantart" not in url:
        return "", 400
    try:
        r = req.get(url, headers=HEADERS, timeout=15, stream=True)
        r.raise_for_status()
        ct = r.headers.get("content-type","image/jpeg")
        return Response(stream_with_context(r.iter_content(8192)),
                        content_type=ct,
                        headers={"Cache-Control":"no-store"})
    except Exception as e:
        return str(e), 502


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
    if not url: return jsonify({"error":"No URL provided"}), 400

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
    if not url: return jsonify({"error":"No URL provided"}), 400
    dl_id = str(uuid.uuid4())[:8]
    active_downloads[dl_id] = {"progress":0,"status":"starting","filename":None,"error":None}
    threading.Thread(target=_do_download,
                     args=(url, data.get("quality","max"), data.get("format","mp4"),
                           data.get("filename","").strip(), data.get("audio_only",False),
                           data.get("no_watermark",True), dl_id), daemon=True).start()
    return jsonify({"dl_id": dl_id})


def _do_download(url, quality, fmt, filename, audio_only, no_wm, dl_id):
    # Direct media URLs (from Rule34, Gelbooru, Wallhaven) — download directly
    if is_direct_image(url) or is_direct_video(url):
        _direct(url, filename or url.split("/")[-1].split("?")[0].rsplit(".",1)[0], dl_id); return

    if "deviantart.com" in url:
        active_downloads[dl_id]["status"] = "fetching"
        fname = filename or safe_name(url.split("/")[-1]) or "da-media"

        # ── Attempt 1: yt-dlp (handles auth natively) ──
        try:
            _ydlp(url, quality, fmt, fname, audio_only, no_wm, dl_id)
            if active_downloads[dl_id].get("status") == "done":
                return
        except Exception: pass

        # ── Attempt 2: get_da_media_url (fetches fresh token every time) ──
        active_downloads[dl_id].update({"status":"fetching","error":None})
        media_url, media_type = get_da_media_url(url)  # always fresh
        if media_url:
            if media_type == "video":
                _ydlp(media_url, quality, fmt, fname, audio_only, no_wm, dl_id)
            else:
                _direct(media_url, fname, dl_id)
            if active_downloads[dl_id].get("status") == "done":
                return

        # ── Attempt 3: yt-dlp on media_url directly ──
        if media_url:
            active_downloads[dl_id].update({"status":"fetching","error":None})
            try:
                _ydlp(media_url, quality, fmt, fname, audio_only, no_wm, dl_id)
                if active_downloads[dl_id].get("status") == "done":
                    return
            except Exception: pass

        # ── All attempts failed ──
        err = active_downloads[dl_id].get("error") or "Could not extract media from DeviantArt"
        active_downloads[dl_id].update({"status":"error","error":err})
        return

    if "artstation.com" in url:
        try:
            r2 = req.get(url, headers=HEADERS, timeout=15)
            og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', r2.text)
            if og: _direct(og.group(1).replace("&amp;","&"), filename or "artstation", dl_id); return
        except Exception: pass
        active_downloads[dl_id].update({"status":"error","error":"Could not find image"}); return

    if is_direct_image(url) or is_direct_video(url):
        _direct(url, filename or url.split("/")[-1].split("?")[0].rsplit(".",1)[0], dl_id); return

    _ydlp(url, quality, fmt, filename, audio_only, no_wm, dl_id)


def _direct(url, fname, dl_id):
    try:
        active_downloads[dl_id]["status"] = "downloading"
        r = req.get(url, headers=HEADERS, timeout=30, stream=True); r.raise_for_status()
        ct = r.headers.get("content-type","")
        ext_map = {"image/jpeg":".jpg","image/png":".png","image/gif":".gif","image/webp":".webp","video/mp4":".mp4","video/webm":".webm"}
        ext = ext_map.get(ct.split(";")[0].strip(),"")
        if not ext:
            m = re.search(r'\.(jpg|jpeg|png|gif|webp|mp4|webm)(\?|$)', url.lower())
            ext = "."+m.group(1) if m else ".jpg"
        if ext == ".jpeg": ext = ".jpg"
        out = DOWNLOAD_DIR / f"{safe_name(fname)}{ext}"; c = 1
        while out.exists(): out = DOWNLOAD_DIR / f"{safe_name(fname)}_{c}{ext}"; c += 1
        total = int(r.headers.get("content-length",0)); done = 0
        with open(out,"wb") as f:
            for chunk in r.iter_content(65536):
                if chunk: f.write(chunk); done += len(chunk)
                if total: active_downloads[dl_id]["progress"] = max(5, min(95, int(done/total*100)))
        fsize = out.stat().st_size
        active_downloads[dl_id].update({"progress":100,"status":"done","filename":out.name,"filepath":str(out),"filesize":fsize})
        download_history.insert(0,{"id":dl_id,"title":out.stem,"url":url,"filename":out.name,"extractor":"Direct","filesize":fsize,"thumbnail":url if ext in [".jpg",".png",".webp",".gif"] else ""})
    except Exception as e:
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
        "tags": ["anime_wallpaper","anime_background","anime_scenery","sword_art_online",
                 "attack_on_titan","demon_slayer","my_hero_academia","naruto","one_piece",
                 "studio_ghibli","hd_anime","anime_landscape","solo_leveling"],
        "mode": "image", "mature": False,
    },
    "live_wallpaper": {
        "label": "✨ Live Wallpaper",
        "tags": ["animated_wallpaper","loop","cinemagraph","animated","gif",
                 "4k_wallpaper","desktop_wallpaper","particle","neon","cyberpunk"],
        "mode": "image", "mature": False,
    },
    "mature_art": {
        "label": "🔞 Mature Art",
        "tags": ["pinup","bikini","lingerie","ecchi","boudoir","glamour","nude","sexy"],
        "mode": "image", "mature": True,
    },
    "mature_video": {
        "label": "🔞 18+ Video",
        "tags": ["animated","3d","mmd","video","hentai","ecchi_video","loop_video"],
        "mode": "video", "mature": True,
    },
    "anime_art": {
        "label": "🎨 Anime Art",
        "tags": ["anime","manga","chibi","waifu","anime_girl","anime_boy","fan_art","moe","kawaii"],
        "mode": "image", "mature": False,
    },
    "fantasy": {
        "label": "🏰 Fantasy",
        "tags": ["fantasy","dragon","elf","wizard","dark_fantasy","fantasy_landscape","magic","medieval"],
        "mode": "image", "mature": False,
    },
    "scifi": {
        "label": "🚀 Sci-Fi",
        "tags": ["sci-fi","cyberpunk","space","futuristic","robot","mech","galaxy","alien","dystopia"],
        "mode": "image", "mature": False,
    },
    "video_animation": {
        "label": "🎬 Animation Video",
        "tags": ["animated","mmd","3d_animation","motion","video","loop","cgi"],
        "mode": "video", "mature": False,
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
    """Fetch one random item for a genre and queue it for download."""
    genre = AUTO_GENRES[genre_key]
    tag   = random.choice(genre["tags"])
    items = scrape_da_tag(tag, mature=genre["mature"])
    if not items:
        return None
    item = random.choice(items)
    return {
        "genre_key": genre_key,
        "genre_label": genre["label"],
        "tag":   tag,
        "url":   item["url"],
        "title": item.get("title",""),
        "author":item.get("author",""),
        "mode":  genre["mode"],
        "mature":genre["mature"],
    }


def _auto_download_item(item):
    """Download one item at max quality. For videos, verify 10+ seconds duration."""
    dl_id = "auto_" + str(uuid.uuid4())[:8]
    active_downloads[dl_id] = {"progress":0,"status":"starting","filename":None,"error":None}
    url   = item["url"]
    fname = safe_name(f"{item['genre_key']}_{item['title'] or item['tag']}_{dl_id}")

    # For video genres: check file size first — skip tiny files (< 1MB = likely < 10s)
    if item.get("mode") == "video" and item.get("direct"):
        try:
            head = req.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
            size = int(head.headers.get("content-length", 0))
            if size > 0 and size < 1_000_000:  # < 1MB = too short
                active_downloads[dl_id].update({"status":"error","error":"Video too short (<10s), skipped"})
                return False, None, "too short"
        except Exception:
            pass  # proceed anyway if HEAD fails

    _do_download(url, "max", "mp4", fname, False, True, dl_id)

    status    = active_downloads[dl_id].get("status")
    fname_out = active_downloads[dl_id].get("filename", "")
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
                    "genre":  AUTO_GENRES[genre_key]["label"],
                    "title":  item["title"],
                    "author": item["author"],
                    "tag":    item["tag"],
                    "file":   fname,
                    "time":   time.strftime("%H:%M:%S"),
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
                        "url": item["url"], "error": err,
                        "time": time.strftime("%H:%M:%S"),
                    })
                    if len(auto_state["errors"]) > 50:
                        auto_state["errors"] = auto_state["errors"][:50]

        except Exception as e:
            print(f"Auto engine error: {e}")

        auto_state["current"] = None
        # Wait between downloads
        for _ in range(auto_state["delay"] * 2):
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

_tg_channels = {}  # name -> {token, chat_id}
_tg_active_channel = "default"

def _get_active_tg():
    """Return (token, chat_id) for the active channel."""
    if _tg_active_channel in _tg_channels:
        ch = _tg_channels[_tg_active_channel]
        return ch.get("token",""), ch.get("chat_id","")
    return TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
print(f"[TG] Token: {(TELEGRAM_BOT_TOKEN[:10]+'...') if TELEGRAM_BOT_TOKEN else 'NOT SET'} | Chat ID: {TELEGRAM_CHAT_ID or 'NOT SET'}")
TG_MAX_FILE_MB     = 50   # Telegram bot API limit per file

_tg_queue   = []          # files waiting to be uploaded
_tg_lock    = threading.Lock()
_tg_enabled = False


def tg_enabled():
    token, chat_id = _get_active_tg()
    return bool(token and chat_id)


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
_tg_thread = None
def _ensure_tg_worker():
    global _tg_thread
    if _tg_thread is None and tg_enabled():
        _tg_thread = threading.Thread(target=_tg_worker, daemon=True)
        _tg_thread.start()


# ── Hook into auto-downloader: queue every completed file ─────────────────────
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

