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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.deviantart.com/",
}

DA_VIDEO_TAGS   = ["animation","3d-animation","motion-graphic","mmd","live-wallpaper","cinemagraph","gif-animation","stop-motion","animatic","vr-360"]
DA_ART_TAGS     = ["digital-art","fantasy","sci-fi","wallpaper","landscape","character-design","concept-art","illustration","surreal","dark-art","anime","portrait","space","nature","architecture"]
DA_WALLPAPER_TAGS = ["wallpaper","hd-wallpaper","live-wallpaper","desktop-wallpaper","dual-monitor","4k-wallpaper","minimalist-wallpaper","dark-wallpaper","space-wallpaper","abstract-wallpaper"]
# Mature / sexy wallpaper tags — 10 categories (DA requires age-gate cookie)
DA_MATURE_TAGS  = [
    "pinup",            # classic pinup art
    "figure-study",     # artistic figure / body study
    "artistic-nude",    # tasteful nudity
    "sensual",          # sensual digital art
    "sexy",             # sexy illustrations
    "glamour",          # glamour photography / art
    "lingerie",         # lingerie art
    "bikini",           # bikini wallpapers
    "ecchi",            # anime-style ecchi art
    "boudoir",          # boudoir style
]

# ── RSS CACHE — avoids hammering DA and re-fetching identical tokens ──────────
_rss_cache = {}          # tag -> (timestamp, results)
_RSS_TTL   = 120         # seconds; fresh enough that tokens don't expire mid-use


def safe_name(s, maxlen=80):
    return re.sub(r'[^\w\-_ ]', '_', s or 'media').strip()[:maxlen]

def is_direct_image(url):
    return bool(re.search(r'\.(jpg|jpeg|png|gif|webp|bmp|tiff)(\?.*)?$', url.lower()))

def is_direct_video(url):
    return bool(re.search(r'\.(mp4|webm|mkv|avi|mov|flv)(\?.*)?$', url.lower()))


def scrape_da_tag(tag, mature=False):
    """Fetch DeviantArt tag items via RSS with short-lived cache to keep tokens fresh."""
    now = time.time()
    cache_key = f"{tag}:{mature}"
    if cache_key in _rss_cache:
        ts, cached = _rss_cache[cache_key]
        if now - ts < _RSS_TTL:
            return cached

    results = []

    # 1. Try DA RSS feed — public, no auth, no scraping
    try:
        rss_url = f"https://backend.deviantart.com/rss.xml?type=deviation&q=tag%3A{quote(tag)}&offset={random.randint(0,100)}"
        rss_headers = dict(HEADERS)
        if mature:
            rss_headers["Cookie"] = "agegate_state=1; userinfo=mature_content_filter%3D0; Secure"
        r = req.get(rss_url, headers=rss_headers, timeout=15)
        if r.status_code == 200:
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item in items[:20]:
                page_url = re.search(r'<link>(https://www\.deviantart\.com/[^<]+)</link>', item)
                title    = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', item)
                author   = re.search(r'<media:credit[^>]*><!\[CDATA\[(.*?)\]\]></media:credit>', item)
                thumb    = re.search(r'<media:thumbnail[^>]+url="([^"]+)"', item)
                fullimg  = re.search(r'<media:content[^>]+url="([^"]+)"', item)
                if not page_url: continue
                results.append({
                    "title":   title.group(1) if title else tag,
                    "author":  author.group(1) if author else "",
                    "url":     page_url.group(1),
                    "thumbnail": fullimg.group(1) if fullimg else (thumb.group(1) if thumb else None),
                    "is_video": False,
                    "type":    "image"
                })
    except Exception as e:
        print("DA RSS error:", e)

    # 2. Fallback: scrape tag page for links only
    if not results:
        try:
            r = req.get(f"https://www.deviantart.com/tag/{quote(tag)}", headers=HEADERS, timeout=15)
            links = re.findall(r'href="(https://www\.deviantart\.com/[^/"]+/art/[^"]+)"', r.text)
            for link in list(dict.fromkeys(links))[:15]:
                results.append({"title": f"DeviantArt · {tag}", "author": "", "url": link,
                                 "thumbnail": None, "is_video": False, "type": "image"})
        except Exception as e:
            print("DA fallback scrape error:", e)

    _rss_cache[cache_key] = (time.time(), results)
    return results

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
    folder = str(DOWNLOAD_DIR.resolve())
    if sys.platform=="win32": os.startfile(folder)
    elif sys.platform=="darwin": os.system(f'open "{folder}"')
    else: os.system(f'xdg-open "{folder}"')
    return jsonify({"ok":True})


AUTO_GENRES = {
    "anime_wallpaper": {
        "label": "🌸 Anime Wallpaper",
        "tags": ["anime-wallpaper","anime-background","anime-scenery","anime-landscape",
                 "anime-art","sword-art-online","attack-on-titan","demon-slayer",
                 "my-hero-academia","naruto","one-piece","studio-ghibli","hd-anime"],
        "mode": "image", "mature": False,
    },
    "live_wallpaper": {
        "label": "✨ Live Wallpaper",
        "tags": ["live-wallpaper","animated-wallpaper","cinemagraph","loop-animation",
                 "motion-wallpaper","4k-wallpaper","desktop-wallpaper","gif-animation",
                 "particle-animation","neon-wallpaper","cyberpunk-wallpaper"],
        "mode": "image", "mature": False,
    },
    "mature_art": {
        "label": "🔞 Mature Art",
        "tags": ["pinup","sensual","sexy","glamour","lingerie","bikini",
                 "ecchi","boudoir","artistic-nude","figure-study"],
        "mode": "image", "mature": True,
    },
    "mature_video": {
        "label": "🔞 18+ Video",
        "tags": ["mature-animation","adult-animation","ecchi-anime","hentai-art",
                 "erotic-art","sexy-animation","adult-mmd","18-plus"],
        "mode": "video", "mature": True,
    },
    "anime_art": {
        "label": "🎨 Anime Art",
        "tags": ["anime","manga-art","chibi","waifu","anime-character",
                 "fan-art","anime-girl","anime-boy","anime-fantasy","moe"],
        "mode": "image", "mature": False,
    },
    "fantasy": {
        "label": "🏰 Fantasy",
        "tags": ["fantasy","fantasy-art","dragon","elf","wizard","dark-fantasy",
                 "fantasy-landscape","medieval","magic","sword-and-sorcery"],
        "mode": "image", "mature": False,
    },
    "scifi": {
        "label": "🚀 Sci-Fi",
        "tags": ["sci-fi","cyberpunk","space","futuristic","robot","mech",
                 "space-art","galaxy","alien","dystopia"],
        "mode": "image", "mature": False,
    },
    "video_animation": {
        "label": "🎬 Animation Video",
        "tags": ["animation","mmd","3d-animation","motion-graphic","vr-360",
                 "anime-amv","fan-animation","loop-video","cgi-animation"],
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
    """Download one item at max quality with full fresh-token retry chain."""
    dl_id = "auto_" + str(uuid.uuid4())[:8]
    active_downloads[dl_id] = {"progress":0,"status":"starting","filename":None,"error":None}
    url   = item["url"]
    fname = safe_name(f"{item['genre_key']}_{item['title'] or item['tag']}_{dl_id}")

    # Always call _do_download — it has the full 3-attempt fresh-token chain
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


# ══════════════════════════════════════════════════════════════════════════════
#  PERMANENT STORAGE — TELEGRAM AUTO-UPLOAD
#  Every downloaded file is instantly sent to your Telegram channel.
#  Files survive Railway restarts, wipes, redeploys — forever.
#  Setup: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway env vars.
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TG_MAX_FILE_MB     = 50   # Telegram bot API limit per file

_tg_queue   = []          # files waiting to be uploaded
_tg_lock    = threading.Lock()
_tg_enabled = False


def tg_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _tg_send_file(filepath, caption=""):
    """Upload one file to Telegram. Returns True on success."""
    if not tg_enabled():
        return False
    try:
        fsize_mb = os.path.getsize(filepath) / 1048576
        ext      = Path(filepath).suffix.lower()
        base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

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
                     data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                     timeout=15)
            return True

        with open(filepath, "rb") as f:
            r = req.post(
                f"{base_url}/{method}",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "Markdown"},
                files={field: f},
                timeout=120
            )
        if r.status_code == 200:
            print(f"📤 Telegram: sent {Path(filepath).name}")
            return True
        else:
            print(f"Telegram error {r.status_code}: {r.text[:200]}")
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
    return jsonify({
        "enabled":    tg_enabled(),
        "bot_set":    bool(TELEGRAM_BOT_TOKEN),
        "chat_set":   bool(TELEGRAM_CHAT_ID),
        "queue_len":  len(_tg_queue),
    })

@app.route("/api/telegram/test", methods=["POST"])
def tg_test():
    if not tg_enabled():
        return jsonify({"ok": False, "error": "Bot token or chat ID not set"}), 400
    try:
        r = req.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID,
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

