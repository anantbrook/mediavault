"""
god_mode.py — Universal Download Engine
════════════════════════════════════════
No website can block this. 10-strategy waterfall that handles:

  • Cloudflare / IUAM challenges
  • 403 hotlink protection
  • Token-gated CDNs (wixmp, akamai)  
  • Redirect wrappers (hotlink.php)
  • Age-gated / login-required pages
  • DRM-free video sites (any platform yt-dlp knows)
  • Direct CDN links (rule34, gelbooru, xbooru, danbooru, etc.)
  • Long videos (1hr+) via chunked streaming
  • Sites that detect bots via TLS fingerprint

Strategies (tried in order):
  1. Smart direct — correct headers + referer per domain
  2. Redirect follow — resolve all hops to real URL
  3. yt-dlp extraction — handles 1000+ platforms natively
  4. Embedded media hunt — scrape og:video, og:image, source tags
  5. __NEXT_DATA__ JSON hunt — React/Next.js SPAs
  6. API endpoint probe — try /api/post/ID, /oembed, etc.
  7. User-Agent spoof cycle — 8 different browsers
  8. curl-cffi TLS impersonation — bypasses Cloudflare JS challenge
  9. Mirror/CDN rewrite — alternate subdomains
 10. Proxy fallback — SOCKS5/HTTP proxy if configured

Usage:
  from god_mode import download, can_download, extract_url
  ok, path, err = download(url, dest_dir, dl_id, progress_cb)
"""

from __future__ import annotations
import os, re, json, time, random, threading, uuid
from pathlib import Path
from urllib.parse import urlparse, quote, urljoin, parse_qs
from typing import Optional, Callable
import requests

# ── Optional: curl_cffi for TLS fingerprint bypass ───────────────────────────
try:
    from curl_cffi import requests as cffi_req
    CFFI_AVAILABLE = True
except ImportError:
    CFFI_AVAILABLE = False

# ── Optional: yt-dlp ─────────────────────────────────────────────────────────
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    yt_dlp = None
    YTDLP_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
_PROXY: Optional[str] = None
_LOCK  = threading.Lock()

def set_proxy(p: str): 
    global _PROXY; _PROXY = p.strip() or None

# ── Browser fingerprint pool ──────────────────────────────────────────────────
_UA_POOL = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Firefox Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Chrome Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    # iPhone Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

# ── Per-domain rules: Referer + cookies that bypass age gates ────────────────
_DOMAIN_RULES: dict[str, dict] = {
    "rule34.xxx":           {"Referer":"https://rule34.xxx/",           "Cookie":""},
    "api.rule34.xxx":       {"Referer":"https://rule34.xxx/",           "Cookie":""},
    "us.rule34.xxx":        {"Referer":"https://rule34.xxx/",           "Cookie":""},
    "wimg.rule34.xxx":      {"Referer":"https://rule34.xxx/",           "Cookie":""},
    "gelbooru.com":         {"Referer":"https://gelbooru.com/",         "Cookie":"fringeBenefits=yup"},
    "img2.gelbooru.com":    {"Referer":"https://gelbooru.com/",         "Cookie":"fringeBenefits=yup"},
    "img3.gelbooru.com":    {"Referer":"https://gelbooru.com/",         "Cookie":"fringeBenefits=yup"},
    "danbooru.donmai.us":   {"Referer":"https://danbooru.donmai.us/",   "Cookie":""},
    "cdn.donmai.us":        {"Referer":"https://danbooru.donmai.us/",   "Cookie":""},
    "konachan.com":         {"Referer":"https://konachan.com/",         "Cookie":""},
    "konachan.net":         {"Referer":"https://konachan.com/",         "Cookie":""},
    "yande.re":             {"Referer":"https://yande.re/",             "Cookie":""},
    "files.yande.re":       {"Referer":"https://yande.re/",             "Cookie":""},
    "safebooru.org":        {"Referer":"https://safebooru.org/",        "Cookie":""},
    "wallhaven.cc":         {"Referer":"https://wallhaven.cc/",         "Cookie":""},
    "w.wallhaven.cc":       {"Referer":"https://wallhaven.cc/",         "Cookie":""},
    "xbooru.com":           {"Referer":"https://xbooru.com/",           "Cookie":""},
    "img.xbooru.com":       {"Referer":"https://xbooru.com/",           "Cookie":""},
    "paheal.net":           {"Referer":"https://rule34.paheal.net/",    "Cookie":""},
    "rule34.paheal.net":    {"Referer":"https://rule34.paheal.net/",    "Cookie":""},
    "deviantart.com":       {"Referer":"https://www.deviantart.com/",   "Cookie":"agegate_state=1; userinfo=mature_content_filter%3D0"},
    "wixmp.com":            {"Referer":"https://www.deviantart.com/",   "Cookie":"agegate_state=1"},
    "artstation.com":       {"Referer":"https://www.artstation.com/",   "Cookie":""},
    "pixiv.net":            {"Referer":"https://www.pixiv.net/",        "Cookie":""},
    "i.pximg.net":          {"Referer":"https://www.pixiv.net/",        "Cookie":""},
    "twitter.com":          {"Referer":"https://twitter.com/",          "Cookie":""},
    "x.com":                {"Referer":"https://x.com/",                "Cookie":""},
    "pbs.twimg.com":        {"Referer":"https://x.com/",                "Cookie":""},
    "reddit.com":           {"Referer":"https://www.reddit.com/",       "Cookie":"over18=1"},
    "i.redd.it":            {"Referer":"https://www.reddit.com/",       "Cookie":"over18=1"},
    "preview.redd.it":      {"Referer":"https://www.reddit.com/",       "Cookie":"over18=1"},
    "nhentai.net":          {"Referer":"https://nhentai.net/",          "Cookie":""},
    "i5.nhentai.net":       {"Referer":"https://nhentai.net/",          "Cookie":""},
    "e621.net":             {"Referer":"https://e621.net/",             "Cookie":""},
    "static1.e621.net":     {"Referer":"https://e621.net/",             "Cookie":""},
    "e-hentai.org":         {"Referer":"https://e-hentai.org/",         "Cookie":"nw=1"},
    "i.e-hentai.org":       {"Referer":"https://e-hentai.org/",         "Cookie":"nw=1"},
    "pururin.to":           {"Referer":"https://pururin.to/",           "Cookie":""},
    "hentaifox.com":        {"Referer":"https://hentaifox.com/",        "Cookie":""},
}

# ── CDN mirror rewrites ───────────────────────────────────────────────────────
_CDN_MIRRORS = [
    (r"https://us\.rule34\.xxx/",    "https://wimg.rule34.xxx/"),
    (r"https://wimg\.rule34\.xxx/",  "https://us.rule34.xxx/"),
    (r"https://img2\.gelbooru\.com/","https://img3.gelbooru.com/"),
    (r"https://img3\.gelbooru\.com/","https://img2.gelbooru.com/"),
    (r"https://cdn\.donmai\.us/",    "https://danbooru.donmai.us/"),
    (r"https://konachan\.com/data/", "https://konachan.net/data/"),
]

# ── Session pool ─────────────────────────────────────────────────────────────
_sessions: dict[str, requests.Session] = {}
_sess_lock = threading.Lock()

def _session(domain: str) -> requests.Session:
    with _sess_lock:
        if domain not in _sessions:
            s = requests.Session()
            _sessions[domain] = s
        return _sessions[domain]

def _headers(url: str, ua: str = None) -> dict:
    domain = urlparse(url).netloc.lstrip("www.")
    rules  = {}
    for key, val in _DOMAIN_RULES.items():
        if key in domain:
            rules = val
            break
    h = {
        "User-Agent":      ua or random.choice(_UA_POOL),
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "DNT":             "1",
    }
    if rules.get("Referer"): h["Referer"] = rules["Referer"]
    else:                    h["Referer"] = f"https://{domain}/"
    if rules.get("Cookie"):  h["Cookie"]  = rules["Cookie"]
    return h


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 1 — Smart direct download
# ═════════════════════════════════════════════════════════════════════════════
def _try_direct(url: str, dest: Path, progress_cb, timeout: int = 300):
    domain = urlparse(url).netloc.lstrip("www.")
    sess   = _session(domain)
    h = _headers(url)

    url_lc = url.lower().split("?")[0]
    is_vid = any(url_lc.endswith(x) for x in (".mp4",".webm",".mkv",".mov",".flv",".avi"))
    if is_vid:
        h["Accept"] = "video/mp4,video/webm,video/*;q=0.9,*/*;q=0.5"

    # (connect_timeout, read_timeout) — connect fast, read forever for large files
    req_timeout = (30, None) if is_vid else (20, 120)

    last_err = "no attempts made"
    for attempt in range(4):
        try:
            if attempt > 0:
                time.sleep(min(2 ** attempt, 10))
                h["User-Agent"] = random.choice(_UA_POOL)
                if attempt >= 3:
                    h.pop("Referer", None)
                    h.pop("Cookie", None)
            r = sess.get(url, headers=h, timeout=req_timeout,
                         stream=True, allow_redirects=True)
            if r.status_code in (200, 206):
                return _stream_to_file(r, dest, progress_cb)
            last_err = f"HTTP {r.status_code}"
        except requests.exceptions.ConnectionError as e:
            last_err = f"connection error: {e}"
        except Exception as e:
            last_err = str(e)

    return None, last_err


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 2 — Follow all redirects, find real URL
# ═════════════════════════════════════════════════════════════════════════════
def _follow_redirects(url: str, max_hops: int = 10) -> Optional[str]:
    current = url
    visited = set()
    for _ in range(max_hops):
        if current in visited: break
        visited.add(current)
        try:
            r = requests.get(current, headers=_headers(current), timeout=15,
                             allow_redirects=False, stream=True)
            if r.status_code in (301,302,303,307,308):
                loc = r.headers.get("Location","")
                if not loc: break
                if loc.startswith("/"): 
                    p = urlparse(current)
                    loc = f"{p.scheme}://{p.netloc}{loc}"
                current = loc; continue
            if r.status_code in (200,206):
                ct = r.headers.get("content-type","")
                if any(t in ct for t in ("image/","video/","application/octet")):
                    return current
                url_lc = urlparse(current).path.lower()
                if any(url_lc.endswith(e) for e in (".jpg",".jpeg",".png",".gif",".webp",".mp4",".webm",".mkv")):
                    return current
            break
        except: break
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 3 — yt-dlp (handles 1000+ sites, long videos, playlists)
# ═════════════════════════════════════════════════════════════════════════════
def _try_ytdlp(url: str, dest_dir: Path, filename: str, progress_cb, quality: str = "best") -> tuple[bool, str, str]:
    if not YTDLP_AVAILABLE:
        return False, "", "yt-dlp not installed"

    results = {"done": False, "path": "", "err": ""}

    def hook(d):
        if d["status"] == "downloading" and progress_cb:
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            dl    = d.get("downloaded_bytes", 0)
            progress_cb(dl, total)
        elif d["status"] == "finished":
            results["path"] = d.get("filename","")

    safe_fname = re.sub(r'[^\w\-_ ]', '_', filename or "media")[:80]
    out_tmpl = str(dest_dir / f"{safe_fname}.%(ext)s")

    quality_map = {
        "max": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "1080": "bestvideo[height<=1080][ext=mp4]+bestaudio/best[height<=1080]/best",
        "720":  "bestvideo[height<=720][ext=mp4]+bestaudio/best[height<=720]/best",
        "480":  "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
        "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    }

    opts = {
        "format":              quality_map.get(quality, quality_map["best"]),
        "outtmpl":             out_tmpl,
        "progress_hooks":      [hook],
        "quiet":               True,
        "no_warnings":         True,
        "merge_output_format": "mp4",
        "writethumbnail":      False,
        "noplaylist":          True,
        "ignoreerrors":        False,
        # ── Bypass ALL restrictions ──────────────────────────────────────
        "age_limit":           99,             # 99 = allow all ages
        "geo_bypass":          True,
        "geo_bypass_country":  "US",
        "nocheckcertificate":  True,
        "socket_timeout":      120,
        "retries":             5,
        "fragment_retries":    10,
        "http_chunk_size":     10485760,       # 10MB chunks for large videos
        "buffersize":          1048576,        # 1MB buffer
        "concurrent_fragment_downloads": 4,   # download 4 fragments at once
        # ── Browser impersonation ────────────────────────────────────────
        "http_headers": {
            "User-Agent": random.choice(_UA_POOL),
            "Referer":    f"https://{urlparse(url).netloc}/",
            "Accept":     "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if _PROXY:
        opts["proxy"] = _PROXY

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url)
            fname = ydl.prepare_filename(info)
            # Find the actual output file (extension may differ)
            # Primary: check with_suffix variants
            for ext in (".mp4",".mkv",".webm",".mp3",".m4a",".jpg",".png",".gif",".flv",".ts"):
                candidate = Path(fname).with_suffix(ext)
                if candidate.exists():
                    results["path"] = str(candidate); break

            # Fallback: glob the dest_dir for any file created in last 30s
            if not results["path"]:
                import glob, time as _time
                now = _time.time()
                pattern = str(dest_dir / "*")
                candidates = [
                    f for f in glob.glob(pattern)
                    if os.path.isfile(f)
                    and not f.endswith(".part")
                    and not f.endswith(".ytdl")
                    and (_time.time() - os.path.getmtime(f)) < 60
                ]
                if candidates:
                    results["path"] = max(candidates, key=os.path.getmtime)

            # Last resort: the raw fname itself
            if not results["path"] and os.path.exists(fname):
                results["path"] = fname

            if results["path"]:
                return True, results["path"], ""
            return False, "", "yt-dlp finished but output file not found"
    except yt_dlp.utils.DownloadError as e:
        return False, "", str(e)
    except Exception as e:
        return False, "", str(e)


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 4 — Scrape page for embedded media
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_page_media(url: str) -> Optional[str]:
    """Find best direct media URL by scraping the page HTML."""
    try:
        h = _headers(url)
        h["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.9"
        r = requests.get(url, headers=h, timeout=30)
        if r.status_code not in (200, 206): return None
        html = r.text

        domain = urlparse(url).netloc

        # Priority 1: og:video:secure_url / og:video:url (direct MP4 links)
        # Note: og:video itself often points to Flash/embed player, NOT the direct file
        # og:video:url and og:video:secure_url are the actual direct MP4/WebM links
        for prop in ("og:video:secure_url", "og:video:url", "og:video"):
            for pat in [
                rf'<meta[^>]+property=["\'\']{re.escape(prop)}["\'\'][^>]+content=["\'\']([^"\'\' ]+)["\'\']>',
                rf'<meta[^>]+content=["\'\']([^"\'\' ]+)["\'\'][^>]+property=["\'\']{re.escape(prop)}["\'\']>',
            ]:
                m = re.search(pat, html, re.I)
                if m:
                    v = m.group(1).replace("&amp;","&")
                    # Skip embed/iframe player URLs — we want direct file URLs
                    if not any(skip in v for skip in ("/embed/","/player/","/watch?",".swf","iframe")):
                        return v

        # Priority 2: video source tags
        for pat in [
            r'<source[^>]+src=["\']([^"\']+\.(?:mp4|webm|m3u8)[^"\']*)["\']',
            r'"(?:src|videoUrl|video_url|mp4|url)"\s*:\s*"([^"]+\.(?:mp4|webm)[^"]*)"',
            r"'(?:src|videoUrl|video_url|mp4|url)'\s*:\s*'([^']+\.(?:mp4|webm)[^']*)'",
        ]:
            m = re.search(pat, html, re.I)
            if m:
                src = m.group(1).replace("\\u002F","/").replace("\\/","/").replace("&amp;","&")
                if src.startswith("//"): src = "https:" + src
                elif src.startswith("/"): src = f"https://{domain}{src}"
                return src

        # Priority 3: og:image (images)
        for pat in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                img = m.group(1).replace("&amp;","&")
                # Strip resize params to get full res
                img = re.sub(r"/v1/fill/w_\d+,h_\d+[^/]*/", "/", img)
                return img

        # Priority 4: largest img src on page
        imgs = re.findall(r'<img[^>]+src=["\']([^"\']+\.(?:jpg|jpeg|png|webp|gif)[^"\']*)["\']', html, re.I)
        if imgs:
            best = max(imgs, key=lambda x: len(x))
            if best.startswith("//"): best = "https:" + best
            elif best.startswith("/"): best = f"https://{domain}{best}"
            return best

    except Exception as e:
        print(f"[GOD] scrape_page_media error: {e}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 5 — __NEXT_DATA__ deep scan (React/Next.js SPAs)
# ═════════════════════════════════════════════════════════════════════════════
def _scrape_next_data(url: str) -> Optional[str]:
    try:
        h = _headers(url)
        r = requests.get(url, headers=h, timeout=20)
        if r.status_code != 200: return None
        nd = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.DOTALL)
        if not nd: return None
        data = json.loads(nd.group(1))

        best = {"score": 0, "url": None}

        def hunt(obj, depth=0):
            if depth > 25: return
            if isinstance(obj, dict):
                priority = {
                    "src": 10, "downloadUrl": 9, "download_url": 9,
                    "original": 8, "fullview": 7, "prettyName": 6,
                    "videoSrc": 10, "video_src": 10, "mp4": 9,
                    "url": 5, "thumbnail_url": 3,
                }
                for k, score in priority.items():
                    v = obj.get(k, "")
                    if isinstance(v, str) and v.startswith("http"):
                        lv = v.lower()
                        if any(x in lv for x in (".mp4",".webm",".jpg",".jpeg",".png",".gif",".webp")):
                            decoded = v.replace("\\u002F","/").replace("\\/","/")
                            if score > best["score"]:
                                best["score"] = score
                                best["url"] = decoded
                for val in obj.values():
                    hunt(val, depth+1)
            elif isinstance(obj, list):
                for item in obj:
                    hunt(item, depth+1)

        hunt(data)
        return best["url"]
    except Exception as e:
        print(f"[GOD] next_data error: {e}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 6 — API probing (oEmbed, site-specific APIs)
# ═════════════════════════════════════════════════════════════════════════════
def _try_api_probe(url: str) -> Optional[str]:
    domain = urlparse(url).netloc

    # oEmbed — works on DeviantArt, Twitter, Reddit, many others
    oembed_url = f"https://noembed.com/embed?url={quote(url)}"
    try:
        r = requests.get(oembed_url, headers={"User-Agent": _UA_POOL[0]}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            for k in ("url","thumbnail_url","media_url"):
                v = d.get(k,"")
                if v and v.startswith("http") and not v.endswith(".html"):
                    return v
    except: pass

    # DeviantArt specific
    if "deviantart.com" in domain:
        try:
            oe = requests.get(
                f"https://backend.deviantart.com/oembed?url={quote(url)}&format=json",
                headers=_headers(url), timeout=12
            )
            if oe.status_code == 200:
                d = oe.json()
                v = d.get("url") or d.get("thumbnail_url")
                if v: return v
        except: pass

    return None


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 7 — UA cycle (try each browser identity)
# ═════════════════════════════════════════════════════════════════════════════
def _try_ua_cycle(url: str, dest: Path, progress_cb):
    url_lc3 = url.lower().split("?")[0]
    is_vid3  = any(url_lc3.endswith(x) for x in (".mp4",".webm",".mkv",".mov",".flv"))
    ua_timeout = (30, None) if is_vid3 else (20, 120)
    for ua in _UA_POOL:
        try:
            h = _headers(url, ua=ua)
            r = requests.get(url, headers=h, timeout=ua_timeout, stream=True, allow_redirects=True)
            if r.status_code in (200, 206):
                result, err = _stream_to_file(r, dest, progress_cb)
                if result: return result, err
        except: pass
    return None, "all UA attempts failed"


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 8 — curl-cffi TLS impersonation (bypasses Cloudflare JS challenge)
# ═════════════════════════════════════════════════════════════════════════════
def _try_cffi(url: str, dest: Path, progress_cb):
    if not CFFI_AVAILABLE:
        return None, "curl_cffi not installed"
    try:
        url_lc = url.lower().split("?")[0]
        is_vid = any(url_lc.endswith(x) for x in (".mp4",".webm",".mkv",".mov",".flv"))
        cffi_timeout = (30, None) if is_vid else (20, 120)
        r = cffi_req.get(
            url,
            headers=_headers(url),
            impersonate="chrome124",
            timeout=cffi_timeout,
            stream=True,
            allow_redirects=True,
        )
        if r.status_code in (200, 206):
            return _stream_to_file(r, dest, progress_cb)
    except Exception as e:
        return None, str(e)
    return None, f"cffi returned {r.status_code}"


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 9 — CDN mirror rewrite
# ═════════════════════════════════════════════════════════════════════════════
def _try_mirror(url: str, dest: Path, progress_cb):
    for pattern, replacement in _CDN_MIRRORS:
        if re.search(pattern, url):
            mirror = re.sub(pattern, replacement, url)
            if mirror != url:
                result, err = _try_direct(mirror, dest, progress_cb)
                if result: return result, err
    return None, "no mirror matched"


# ═════════════════════════════════════════════════════════════════════════════
#  STRATEGY 10 — Proxy fallback
# ═════════════════════════════════════════════════════════════════════════════
def _try_proxy_direct(url: str, dest: Path, progress_cb):
    if not _PROXY:
        return None, "no proxy configured"
    try:
        proxies = {"http": _PROXY, "https": _PROXY}
        url_lc2 = url.lower().split("?")[0]
        is_vid2 = any(url_lc2.endswith(x) for x in (".mp4",".webm",".mkv",".mov",".flv"))
        proxy_timeout = (30, None) if is_vid2 else (20, 120)
        r = requests.get(url, headers=_headers(url), proxies=proxies,
                         timeout=proxy_timeout, stream=True, allow_redirects=True)
        if r.status_code in (200, 206):
            return _stream_to_file(r, dest, progress_cb)
    except Exception as e:
        return None, str(e)
    return None, "proxy request failed"


# ─────────────────────────────────────────────────────────────────────────────
#  Shared: stream response to file
# ─────────────────────────────────────────────────────────────────────────────
def _stream_to_file(r, dest: Path, progress_cb) -> tuple[Optional[Path], str]:
    """Stream response to file. Handles videos of any size."""
    total = int(r.headers.get("content-length", 0))
    done  = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    first_chunk = True

    try:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1048576):  # 1MB chunks
                if not chunk:
                    continue
                # Check first chunk for HTML error pages BEFORE writing
                if first_chunk:
                    first_chunk = False
                    sniff = chunk[:32].lstrip()
                    if sniff[:5].lower() in (b"<!doc", b"<html") or sniff[:6].lower() in (b"<head>", b"<body>"):
                        dest.unlink(missing_ok=True)
                        return None, "server returned HTML error page instead of media"
                    # Also reject JSON error responses
                    if sniff[:1] in (b"{", b"[") and b"error" in chunk[:256].lower():
                        try:
                            err_data = json.loads(chunk[:2048].decode("utf-8","ignore"))
                            if isinstance(err_data, dict) and ("error" in err_data or "message" in err_data):
                                dest.unlink(missing_ok=True)
                                return None, f"API error: {err_data.get('error', err_data.get('message',''))}"
                        except:
                            pass
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total or done)

        fsize = dest.stat().st_size
        if fsize < 512:
            dest.unlink(missing_ok=True)
            return None, f"file too small ({fsize}B)"

        print(f"[GOD] ✅ Saved {dest.name} ({fsize//1024}KB)")
        return dest, ""
    except Exception as e:
        dest.unlink(missing_ok=True)
        return None, f"write error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
#  Detect file extension from content-type + URL
# ─────────────────────────────────────────────────────────────────────────────
def _ext(url: str, ct: str = "") -> str:
    ct = ct.split(";")[0].strip().lower()
    ct_map = {
        "image/jpeg":".jpg","image/jpg":".jpg","image/png":".png",
        "image/gif":".gif","image/webp":".webp","image/avif":".avif",
        "video/mp4":".mp4","video/webm":".webm","video/quicktime":".mov",
        "video/x-matroska":".mkv","application/octet-stream":".bin",
    }
    if ct in ct_map: return ct_map[ct]
    m = re.search(r"\.(jpg|jpeg|png|gif|webp|avif|mp4|webm|mkv|mov|flv|avi)(\?|$)", url.lower())
    return ("." + m.group(1)) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
#  Resolve hotlink/redirect wrappers before anything
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_wrapper(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    # ?img=, ?url=, ?src= style wrappers
    for key in ("img","url","image","src","file","to","link"):
        val = qs.get(key, [None])[0]
        if val:
            decoded = val.replace("%2F","/")
            if decoded.startswith("http"): return decoded

    # xbooru hotlink.php
    if "xbooru.com/public/hotlink.php" in url:
        img = qs.get("img",[None])[0]
        if img:
            prefix = img[:4]
            cdn = f"https://img.xbooru.com/images/{prefix}/{img}"
            return cdn

    # Any redirect.php, hotlink.php, proxy.php
    if re.search(r"/(redirect|hotlink|proxy|go|out)\.php", url, re.I):
        resolved = _follow_redirects(url)
        if resolved and resolved != url: return resolved

    return url


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def download(
    url:         str,
    dest_dir:    Path,
    dl_id:       str = "",
    progress_cb: Callable = None,
    filename:    str = "",
    quality:     str = "best",
) -> tuple[bool, str, str]:
    """
    Download ANYTHING from ANY URL. No restrictions.
    Returns: (success, filepath, error_message)
    """
    if not url or not url.startswith("http"):
        return False, "", "Invalid URL"

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 0: Resolve wrappers ──────────────────────────────────────────────
    real_url = _resolve_wrapper(url)
    if real_url != url:
        print(f"[GOD] Unwrapped: {url[:50]} → {real_url[:50]}")
        url = real_url

    # ── Step 1: Determine if this is a direct media URL ───────────────────────
    url_path = urlparse(url).path.lower()
    is_direct = bool(re.search(r"\.(jpg|jpeg|png|gif|webp|avif|mp4|webm|mkv|mov|flv|avi|bmp|tiff)$", url_path))
    is_booru_cdn = any(d in url for d in [
        "rule34.xxx/images","us.rule34.xxx","wimg.rule34.xxx",
        "img2.gelbooru.com","img3.gelbooru.com","cdn.donmai.us",
        "files.yande.re","konachan.com/data","konachan.net/data",
        "img.xbooru.com","xbooru.com/images","safebooru.org/images",
        "wallhaven.cc/full","img.rule34.paheal.net","wixmp.com",
        "i.pximg.net","static1.e621.net","i5.nhentai.net",
        "i.redd.it","pbs.twimg.com","preview.redd.it",
    ])

    # Build destination path
    fname_base = filename or url.split("/")[-1].split("?")[0].rsplit(".",1)[0] or f"media_{dl_id}"
    fname_base = re.sub(r'[^\w\-_ ]', '_', fname_base)[:80]

    errors = []

    # ─────────────────────────────────────────────────────────────────────────
    # PATH A: Direct media URL (booru CDN, wixmp, direct image/video)
    # ─────────────────────────────────────────────────────────────────────────
    if is_direct or is_booru_cdn:
        # Determine extension — check URL and content-type
        ext = _ext(url)
        url_lc = url.lower().split("?")[0]
        if not ext:
            # Try HEAD request to get content-type before allocating path
            try:
                hd = requests.head(url, headers=_headers(url), timeout=10, allow_redirects=True)
                ext = _ext(url, hd.headers.get("content-type",""))
            except: pass
        if not ext:
            # Guess from URL context
            if any(x in url_lc for x in ("/video","/mp4","/webm","video=","type=video")):
                ext = ".mp4"
            elif any(url_lc.endswith(x) for x in (".mp4",".webm",".gif",".mkv")):
                ext = "." + url_lc.rsplit(".",1)[-1]
            else:
                ext = ".jpg"   # image fallback
        dest = dest_dir / f"{fname_base}{ext}"
        c = 1
        while dest.exists(): dest = dest_dir / f"{fname_base}_{c}{ext}"; c += 1

        # Try Strategy 1 first (fastest)
        result, err = _try_direct(url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"direct: {err}")

        # Strategy 9: CDN mirror
        result, err = _try_mirror(url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"mirror: {err}")

        # Strategy 7: UA cycle
        result, err = _try_ua_cycle(url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"ua_cycle: {err}")

        # Strategy 8: TLS impersonation
        result, err = _try_cffi(url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"cffi: {err}")

        # Strategy 10: Proxy
        result, err = _try_proxy_direct(url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"proxy: {err}")

        return False, "", " | ".join(errors)

    # ─────────────────────────────────────────────────────────────────────────
    # PATH B: Page URL — need to extract media first, then download
    # ─────────────────────────────────────────────────────────────────────────

    # Strategy 3: yt-dlp (handles pages natively — YouTube, Twitter, Reddit, DA, etc.)
    ok, fpath, err = _try_ytdlp(url, dest_dir, fname_base, progress_cb, quality)
    if ok: return True, fpath, ""
    errors.append(f"ytdlp: {err}")

    # Strategy 6: API probe
    media_url = _try_api_probe(url)
    if media_url:
        ext = _ext(media_url)
        if not ext:
            url_lc_b = media_url.lower().split("?")[0]
            ext = ".mp4" if any(url_lc_b.endswith(x) for x in (".mp4",".webm",".mkv",".flv")) else ".jpg"
        dest = dest_dir / f"{fname_base}{ext}"
        c = 1
        while dest.exists(): dest = dest_dir / f"{fname_base}_{c}{ext}"; c += 1
        result, err2 = _try_direct(media_url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"api_probe_dl: {err2}")

    # Strategy 4: Scrape page HTML for embedded media
    media_url = _scrape_page_media(url)
    if media_url:
        ext = _ext(media_url)
        if not ext:
            url_lc_b2 = media_url.lower().split("?")[0]
            ext = ".mp4" if any(url_lc_b2.endswith(x) for x in (".mp4",".webm",".mkv",".flv")) else ".jpg"
        dest = dest_dir / f"{fname_base}{ext}"
        c = 1
        while dest.exists(): dest = dest_dir / f"{fname_base}_{c}{ext}"; c += 1
        result, err2 = _try_direct(media_url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"page_scrape_dl: {err2}")

    # Strategy 5: __NEXT_DATA__ deep scan
    media_url = _scrape_next_data(url)
    if media_url:
        ext = _ext(media_url)
        if not ext:
            url_lc_b3 = media_url.lower().split("?")[0]
            ext = ".mp4" if any(url_lc_b3.endswith(x) for x in (".mp4",".webm",".mkv",".flv")) else ".jpg"
        dest = dest_dir / f"{fname_base}{ext}"
        c = 1
        while dest.exists(): dest = dest_dir / f"{fname_base}_{c}{ext}"; c += 1
        result, err2 = _try_direct(media_url, dest, progress_cb)
        if result: return True, str(result), ""
        errors.append(f"next_data_dl: {err2}")

    # Strategy 8: TLS impersonation on original page (Cloudflare bypass)
    if CFFI_AVAILABLE:
        # Re-extract after bypassing Cloudflare
        try:
            r = cffi_req.get(url, headers=_headers(url), impersonate="chrome124", timeout=20)
            if r.status_code == 200:
                html = r.text
                og = re.search(r'<meta[^>]+property=["\']og:(?:image|video)["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                if og:
                    media_url = og.group(1).replace("&amp;","&")
                    ext = _ext(media_url) or ".jpg"
                    dest = dest_dir / f"{fname_base}{ext}"
                    c = 1
                    while dest.exists(): dest = dest_dir / f"{fname_base}_{c}{ext}"; c += 1
                    result, err2 = _try_direct(media_url, dest, progress_cb)
                    if result: return True, str(result), ""
        except: pass

    return False, "", "All 10 strategies failed: " + " | ".join(errors[-3:])


def extract_url(url: str) -> Optional[str]:
    """Just extract the direct media URL without downloading."""
    real = _resolve_wrapper(url)
    if real != url: return real
    
    url_path = urlparse(url).path.lower()
    if re.search(r"\.(jpg|jpeg|png|gif|webp|mp4|webm|mkv)$", url_path):
        return url

    # Try page scraping
    found = _scrape_page_media(url) or _scrape_next_data(url) or _try_api_probe(url)
    return found


def inject_cookies(domain: str, cookies: dict):
    """Inject cookies into the session for a specific domain."""
    sess = _session(domain)
    for k, v in cookies.items():
        sess.cookies.set(k, v, domain=domain)
