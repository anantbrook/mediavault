"""
da_video_grab.py
================
Run this script to download ANY DeviantArt video directly.

Usage:
  python da_video_grab.py "https://www.deviantart.com/user/art/Title-12345"

It tries 6 methods in order until one works.
"""

import sys, re, json, os, time, requests, ast, html as html_lib
from pathlib import Path
from urllib.parse import quote
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
COOKIES = "agegate_state=1; userinfo=mature_content_filter%3D0"

HEADERS = {
    "User-Agent": UA,
    "Cookie": COOKIES,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.deviantart.com/",
}

def log(msg): print(f"  → {msg}")

def get_deviation_id(url):
    m = re.search(r"-(\d+)(?:/|$|\?)", url)
    return m.group(1) if m else None

def method1_eclipse_api(dev_id):
    """DeviantArt internal Eclipse API — returns video src directly"""
    log("Method 1: Eclipse internal API")
    endpoints = [
        f"https://www.deviantart.com/_napi/shared_api/deviation/extended_fetch?deviationid={dev_id}&username=&type=art&include_session=false",
        f"https://www.deviantart.com/_napi/da-browse/api/networkbar/deviation/{dev_id}",
        f"https://www.deviantart.com/_napi/shared_api/deviation/fetch?deviationid={dev_id}",
    ]
    for ep in endpoints:
        try:
            r = requests.get(ep, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                text = r.text
                # Find wixmp video URLs
                matches = re.findall(r'https://[^"\'\\s]+wixmp[^"\'\\s]+\.mp4[^"\'\\s]*', text)
                if matches:
                    log(f"  Found via Eclipse API: {matches[0][:80]}")
                    return max(matches, key=len)  # longest = highest quality
                # Find any wixmp URL
                wix = re.findall(r'https://[^"\'\\s]+wixmp\.com[^"\'\\s]+', text)
                if wix:
                    return max(wix, key=len)
        except Exception as e:
            log(f"  Endpoint failed: {e}")
    return None

def method2_oembed(url):
    """oEmbed endpoint"""
    log("Method 2: oEmbed API")
    try:
        r = requests.get(
            f"https://backend.deviantart.com/oembed?url={quote(url)}&format=json",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            d = r.json()
            for k in ("url","media_url","video_url"):
                v = d.get(k,"")
                is_media = bool(re.search(r'\.(mp4|webm|mov|mkv|jpg|jpeg|png|webp)(\?|$)', v, re.I)) or "wixmp" in v
                if v and v.startswith("http") and is_media:
                    log(f"  Found via oEmbed: {v[:80]}")
                    return v
    except Exception as e:
        log(f"  oEmbed failed: {e}")
    return None

def method3_page_scrape(url):
    """Scrape the page HTML directly"""
    log("Method 3: Page HTML scrape")
    try:
        page_html = ""
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            page_html = r.text
        elif cffi_requests:
            log(f"  Page returned {r.status_code} via requests - trying browser impersonation")
            r2 = cffi_requests.get(url, headers=HEADERS, impersonate="chrome124", timeout=20)
            if r2.status_code == 200:
                page_html = r2.text
            else:
                log(f"  Browser impersonation returned {r2.status_code}")
                return None
        else:
            log(f"  Page returned {r.status_code}")
            return None

        dev_id = get_deviation_id(url)
        if cffi_requests and dev_id:
            try:
                state_match = re.search(
                    r'window\.__INITIAL_STATE__\s*=\s*JSON\.parse\("((?:\\.|[^"\\])*)"\)',
                    page_html,
                )
                if state_match:
                    state = json.loads(ast.literal_eval('"' + state_match.group(1) + '"'))
                    entities = state.get("@@entities", {})
                    ext = (entities.get("deviationExtended", {}) or {}).get(str(dev_id), {})
                    embed = ext.get("embedCode", "")
                    iframe = re.search(r"src=['\"]([^'\"]+/embed/film/[^'\"]+)['\"]", embed or "")
                    embed_url = iframe.group(1).replace("&amp;", "&") if iframe else ""
                    if not embed_url:
                        deviation = (entities.get("deviation", {}) or {}).get(str(dev_id), {})
                        if deviation.get("type") == "film" or deviation.get("isVideo"):
                            embed_url = f"https://backend.deviantart.com/embed/film/{dev_id}/1/"
                    if embed_url:
                        er = cffi_requests.get(embed_url, headers=HEADERS, impersonate="chrome124", timeout=20)
                        if er.status_code == 200:
                            sm = re.search(r'gmon-sources="([^"]+)"', er.text)
                            if sm:
                                sources = json.loads(html_lib.unescape(sm.group(1)))
                                best = None
                                best_score = -1
                                for meta in sources.values():
                                    if not isinstance(meta, dict):
                                        continue
                                    src = meta.get("src", "")
                                    if not src.startswith("http"):
                                        continue
                                    score = int(meta.get("width", 0) or 0) * int(meta.get("height", 0) or 0)
                                    if score >= best_score:
                                        best = src
                                        best_score = score
                                if best:
                                    log(f"  Found via embed player: {best[:80]}")
                                    return best
            except Exception as e:
                log(f"  Embed scrape failed: {e}")

        # Priority: find wixmp video URLs
        patterns = [
            r'"src"\s*:\s*"(https://[^"]+wixmp[^"]+\.mp4[^"]*)"',
            r'"src"\s*:\s*"(https://[^"]+wixmp[^"]+\.webm[^"]*)"',
            r'<source[^>]+src="(https://[^"]+\.mp4[^"]*)"',
            r'"videoUrl"\s*:\s*"([^"]+)"',
            r'"video"\s*:\s*\{[^}]*"src"\s*:\s*"([^"]+)"',
            r'https://[^\s"\'<>]+wixmp[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        ]
        for pat in patterns:
            m = re.search(pat, page_html, re.I)
            if m:
                v = m.group(1) if '(' in pat else m.group(0)
                v = v.replace("\\u002F","/").replace("\\/","/")
                log(f"  Found via page scrape: {v[:80]}")
                return v

        # __NEXT_DATA__ deep scan
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.DOTALL)
        if nd:
            try:
                data = json.loads(nd.group(1))
                found = hunt_json(data)
                if found:
                    log(f"  Found in __NEXT_DATA__: {found[:80]}")
                    return found
            except:
                pass

    except Exception as e:
        log(f"  Page scrape failed: {e}")
    return None

def hunt_json(obj, depth=0):
    if depth > 30: return None
    if isinstance(obj, dict):
        # Check high-priority keys first
        for k in ("src","downloadUrl","download_url","videoSrc","video_src","mp4","original","prettyName","fullview"):
            v = obj.get(k,"")
            if isinstance(v,str) and v.startswith("http") and any(x in v.lower() for x in (".mp4",".webm","wixmp")):
                return v.replace("\\u002F","/").replace("\\/","/")
        for val in obj.values():
            r = hunt_json(val, depth+1)
            if r: return r
    elif isinstance(obj, list):
        for item in obj:
            r = hunt_json(item, depth+1)
            if r: return r
    return None

def method4_ytdlp(url):
    """yt-dlp — most reliable, handles auth"""
    log("Method 4: yt-dlp")
    try:
        import yt_dlp
        result = {}
        def hook(d):
            if d["status"] == "finished":
                result["path"] = d.get("filename","")

        with yt_dlp.YoutubeDL({
            "format": "bestvideo[ext=mp4]+bestaudio/best",
            "outtmpl": "%(title)s.%(ext)s",
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "age_limit": 99,
            "cookiesfrombrowser": ("chrome",),
            "http_headers": {"User-Agent": UA, "Referer": "https://www.deviantart.com/"},
        }) as ydl:
            info = ydl.extract_info(url)
            fname = ydl.prepare_filename(info)
            for ext in (".mp4",".webm",".mkv"):
                c = Path(fname).with_suffix(ext)
                if c.exists(): return str(c)
            return fname if os.path.exists(fname) else None
    except Exception as e:
        log(f"  yt-dlp failed: {e}")
        return None

def download_video(video_url, out_name="deviantart_video.mp4"):
    """Download the actual video file"""
    log(f"Downloading: {video_url[:80]}")
    h = {
        "User-Agent": UA,
        "Referer": "https://www.deviantart.com/",
        "Cookie": COOKIES,
        "Accept": "video/mp4,video/webm,video/*;q=0.9,*/*;q=0.5",
    }
    r = requests.get(video_url, headers=h, stream=True, timeout=(30, None))
    if r.status_code not in (200, 206):
        print(f"  Download failed: HTTP {r.status_code}")
        return None

    ct = r.headers.get("content-type","")
    if "html" in ct:
        print(f"  Server returned HTML instead of video — blocked")
        return None

    total = int(r.headers.get("content-length",0))
    done = 0
    with open(out_name, "wb") as f:
        for chunk in r.iter_content(chunk_size=1048576):
            if not chunk: continue
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = int(done/total*100)
                print(f"\r  Progress: {pct}% ({done//1024//1024}MB / {total//1024//1024}MB)", end="", flush=True)
    print()
    size = os.path.getsize(out_name)
    print(f"  ✅ Saved: {out_name} ({size//1024//1024}MB)")
    return out_name


# ─── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.deviantart.com/therrrealist/art/AI-Streamer-Sluts-Big-Tits-Flash-Compilation-1297831250"

    print(f"\n🎬 DeviantArt Video Downloader")
    print(f"   URL: {url}\n")

    dev_id = get_deviation_id(url)
    print(f"   Deviation ID: {dev_id}\n")

    video_url = None

    # Try all methods
    video_url = method1_eclipse_api(dev_id)
    if not video_url:
        video_url = method2_oembed(url)
    if not video_url:
        video_url = method3_page_scrape(url)
    if not video_url:
        # yt-dlp handles everything natively
        result = method4_ytdlp(url)
        if result:
            print(f"\n✅ Downloaded via yt-dlp: {result}")
            sys.exit(0)

    if video_url:
        # Clean up escaped slashes
        video_url = video_url.replace("\\u002F","/").replace("\\/","/")
        out = f"da_{dev_id}.mp4"
        result = download_video(video_url, out)
        if result:
            print(f"\n✅ Done! File saved as: {result}")
        else:
            print("\n❌ Download failed")
    else:
        print("\n❌ Could not find video URL. Try:")
        print("   pip install yt-dlp")
        print(f"   yt-dlp --cookies-from-browser chrome \"{url}\"")





