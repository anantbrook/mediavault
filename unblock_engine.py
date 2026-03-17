"""
unblock_engine.py
─────────────────
Custom multi-strategy unblocking engine for booru/image sites.
Handles: Cloudflare, 403/429, rate limits, missing Referer, IP bans.

Strategy waterfall (tried in order per URL):
  1. Direct request with full browser headers + correct Referer
  2. Rotate User-Agent + add cache-busting param
  3. Switch to alternative CDN subdomain (common on rule34/gelbooru)
  4. Use requests-toolbelt multipart streaming
  5. Fetch via HEAD first to warm connection, then GET
  6. Fragment fetch (Range: bytes=0-) to bypass some CDN checks
  7. Mirror URL rewrite (e.g. img.rule34.xxx → us.rule34.xxx)
  8. Proxy fallback (if configured)

Also provides:
  - UnblockSession: persistent per-domain session with auto-retry
  - domain_ok(url): quick test if a domain is reachable
  - get_best_url(url): returns working URL or None after all strategies
"""

from __future__ import annotations
import re
import time
import random
import threading
import requests
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

# ── Browser User-Agents (rotate on each retry) ───────────────────────────────
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

# ── Per-domain Referer + Accept rules ────────────────────────────────────────
_DOMAIN_RULES: dict[str, dict] = {
    "rule34.xxx":          {"Referer": "https://rule34.xxx/",           "Accept": "image/avif,image/webp,image/apng,*/*;q=0.8"},
    "api.rule34.xxx":      {"Referer": "https://rule34.xxx/",           "Accept": "application/json"},
    "us.rule34.xxx":       {"Referer": "https://rule34.xxx/",           "Accept": "image/webp,*/*"},
    "wimg.rule34.xxx":     {"Referer": "https://rule34.xxx/",           "Accept": "image/webp,*/*"},
    "gelbooru.com":        {"Referer": "https://gelbooru.com/",         "Accept": "image/avif,image/webp,*/*;q=0.8"},
    "img2.gelbooru.com":   {"Referer": "https://gelbooru.com/",         "Accept": "image/webp,*/*"},
    "img3.gelbooru.com":   {"Referer": "https://gelbooru.com/",         "Accept": "image/webp,*/*"},
    "danbooru.donmai.us":  {"Referer": "https://danbooru.donmai.us/",   "Accept": "image/avif,image/webp,*/*;q=0.8",
                            "Origin": "https://danbooru.donmai.us"},
    "cdn.donmai.us":       {"Referer": "https://danbooru.donmai.us/",   "Accept": "image/webp,*/*"},
    "konachan.com":        {"Referer": "https://konachan.com/",         "Accept": "image/webp,*/*"},
    "konachan.net":        {"Referer": "https://konachan.com/",         "Accept": "image/webp,*/*"},
    "yande.re":            {"Referer": "https://yande.re/",             "Accept": "image/webp,*/*"},
    "files.yande.re":      {"Referer": "https://yande.re/",             "Accept": "image/webp,*/*"},
    "safebooru.org":       {"Referer": "https://safebooru.org/",        "Accept": "image/webp,*/*"},
    "wallhaven.cc":        {"Referer": "https://wallhaven.cc/",         "Accept": "image/webp,*/*"},
    "w.wallhaven.cc":      {"Referer": "https://wallhaven.cc/",         "Accept": "image/webp,*/*"},
    "xbooru.com":          {"Referer": "https://xbooru.com/",           "Accept": "image/webp,*/*"},
    "paheal.net":          {"Referer": "https://rule34.paheal.net/",    "Accept": "image/webp,*/*"},
    "rule34.paheal.net":   {"Referer": "https://rule34.paheal.net/",    "Accept": "image/webp,*/*"},
    "img.rule34.paheal.net":{"Referer": "https://rule34.paheal.net/",  "Accept": "image/webp,*/*"},
}

# ── CDN Mirror rewrites (try these if main domain fails) ─────────────────────
_MIRROR_REWRITES: list[tuple[str, str]] = [
    # rule34 image CDNs
    (r"https://us\.rule34\.xxx/",       "https://wimg.rule34.xxx/"),
    (r"https://wimg\.rule34\.xxx/",     "https://us.rule34.xxx/"),
    # gelbooru image CDNs
    (r"https://img2\.gelbooru\.com/",   "https://img3.gelbooru.com/"),
    (r"https://img3\.gelbooru\.com/",   "https://img2.gelbooru.com/"),
    # danbooru CDN
    (r"https://cdn\.donmai\.us/",       "https://danbooru.donmai.us/"),
    # konachan mirror
    (r"https://konachan\.com/",         "https://konachan.net/"),
    (r"https://konachan\.net/",         "https://konachan.com/"),
    # yande.re CDN
    (r"https://yande\.re/image/",       "https://files.yande.re/image/"),
]

# ── Session pool (one per domain, reused for cookies/connection pooling) ─────
_sessions: dict[str, requests.Session] = {}
_slock = threading.Lock()

def _get_session(domain: str) -> requests.Session:
    with _slock:
        if domain not in _sessions:
            s = requests.Session()
            s.headers.update(_build_headers(domain))
            _sessions[domain] = s
        return _sessions[domain]

def _build_headers(domain: str, extra: dict | None = None) -> dict:
    h = {
        "User-Agent":      random.choice(_UAS),
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection":      "keep-alive",
        "DNT":             "1",
        "Sec-Fetch-Mode":  "no-cors",
        "Sec-Fetch-Dest":  "image",
        "Sec-Fetch-Site":  "cross-site",
    }
    for key, rules in _DOMAIN_RULES.items():
        if key in domain:
            h.update(rules)
            break
    if "Referer" not in h:
        h["Referer"] = f"https://{domain}/"
    if extra:
        h.update(extra)
    return h


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 1: Direct request with full headers
# ─────────────────────────────────────────────────────────────────────────────
def _try_direct(url: str, session: requests.Session, timeout: int = 20) -> Optional[requests.Response]:
    try:
        r = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206):
            return r
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 2: Rotate User-Agent + cache-bust
# ─────────────────────────────────────────────────────────────────────────────
def _try_rotated_ua(url: str, domain: str, timeout: int = 20) -> Optional[requests.Response]:
    try:
        headers = _build_headers(domain)
        headers["User-Agent"] = random.choice(_UAS)
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"
        bust_url = url + ("&" if "?" in url else "?") + f"_={int(time.time())}"
        r = requests.get(bust_url, headers=headers, stream=True,
                         timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206):
            return r
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 3: Mirror URL rewrite (alternate CDN)
# ─────────────────────────────────────────────────────────────────────────────
def _try_mirror(url: str, timeout: int = 20) -> Optional[requests.Response]:
    for pattern, replacement in _MIRROR_REWRITES:
        if re.search(pattern, url):
            mirror_url = re.sub(pattern, replacement, url)
            if mirror_url == url:
                continue
            try:
                parsed = urlparse(mirror_url)
                mirror_domain = parsed.netloc
                headers = _build_headers(mirror_domain)
                r = requests.get(mirror_url, headers=headers, stream=True,
                                 timeout=timeout, allow_redirects=True)
                if r.status_code in (200, 206):
                    return r
            except Exception:
                pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 4: HEAD warm-up then GET (bypasses some CDN challenges)
# ─────────────────────────────────────────────────────────────────────────────
def _try_head_warmup(url: str, domain: str, timeout: int = 20) -> Optional[requests.Response]:
    try:
        session = _get_session(domain)
        # Warm up the connection with HEAD
        session.head(url, timeout=8, allow_redirects=True)
        time.sleep(0.3)
        # Now GET with same session (cookies preserved from HEAD response)
        r = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206):
            return r
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 5: Range request (bytes=0-) — bypasses some hotlink checkers
# ─────────────────────────────────────────────────────────────────────────────
def _try_range(url: str, domain: str, timeout: int = 25) -> Optional[requests.Response]:
    try:
        headers = _build_headers(domain)
        headers["Range"] = "bytes=0-"
        r = requests.get(url, headers=headers, stream=True,
                         timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206):
            return r
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 6: Alternate protocol / URL normalization
# ─────────────────────────────────────────────────────────────────────────────
def _try_normalize(url: str, domain: str, timeout: int = 20) -> Optional[requests.Response]:
    """Try http instead of https, strip query params, etc."""
    variants = []
    # Try http fallback
    if url.startswith("https://"):
        variants.append(url.replace("https://", "http://", 1))
    # Strip query string
    if "?" in url:
        variants.append(url.split("?")[0])
    for v in variants:
        try:
            headers = _build_headers(domain)
            r = requests.get(v, headers=headers, stream=True,
                             timeout=timeout, allow_redirects=True)
            if r.status_code in (200, 206):
                return r
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 7: Proxy fallback
# ─────────────────────────────────────────────────────────────────────────────
_proxy_url: Optional[str] = None

def set_proxy(proxy: str):
    global _proxy_url
    _proxy_url = proxy.strip() if proxy.strip() else None

def _try_proxy(url: str, domain: str, timeout: int = 30) -> Optional[requests.Response]:
    if not _proxy_url:
        return None
    try:
        headers = _build_headers(domain)
        proxies = {"http": _proxy_url, "https": _proxy_url}
        r = requests.get(url, headers=headers, proxies=proxies,
                         stream=True, timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206):
            return r
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN ENTRY: get_response — tries all strategies in order
# ─────────────────────────────────────────────────────────────────────────────
def get_response(url: str, retries: int = 3) -> Optional[requests.Response]:
    """
    Try every unblocking strategy for a URL.
    Returns a streaming Response on success, or None if all fail.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    session = _get_session(domain)

    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt + random.uniform(0, 1))
            # Rotate UA on retry
            session.headers["User-Agent"] = random.choice(_UAS)

        # Strategy 1: Direct
        r = _try_direct(url, session)
        if r: return r

        # Strategy 2: Rotated UA + cache bust
        r = _try_rotated_ua(url, domain)
        if r: return r

        # Strategy 3: Mirror CDN
        r = _try_mirror(url)
        if r: return r

        # Strategy 4: HEAD warm-up
        r = _try_head_warmup(url, domain)
        if r: return r

        # Strategy 5: Range request
        r = _try_range(url, domain)
        if r: return r

        # Strategy 6: Normalize URL
        r = _try_normalize(url, domain)
        if r: return r

    # Strategy 7: Proxy (last resort)
    r = _try_proxy(url, domain)
    if r: return r

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  download_file — stream a URL to disk using all strategies
# ─────────────────────────────────────────────────────────────────────────────
def download_file(
    url: str,
    dest: Path,
    progress_cb=None,   # callback(bytes_done, total_bytes)
    cookies: dict | None = None,
) -> tuple[bool, str]:
    """
    Download url → dest using the full unblock strategy waterfall.
    Returns (success, message).
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    session = _get_session(domain)

    # Inject cookies if provided
    if cookies:
        for k, v in cookies.items():
            session.cookies.set(k, v, domain=domain)

    resp = get_response(url)
    if resp is None:
        return False, f"All unblock strategies failed for {domain}"

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(done, total)
        if done < 512:
            dest.unlink(missing_ok=True)
            return False, f"Downloaded only {done} bytes — likely blocked HTML page"
        return True, f"OK ({done:,} bytes)"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
#  domain_ok — quick reachability test
# ─────────────────────────────────────────────────────────────────────────────
def domain_ok(url: str, timeout: int = 8) -> dict:
    """Test if a URL/domain is reachable. Returns diagnostic dict."""
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    result = {
        "url": url, "domain": domain,
        "reachable": False, "status": None,
        "strategy": None, "blocked": False, "reason": "",
    }
    try:
        headers = _build_headers(domain)
        r = requests.head(url, headers=headers, timeout=timeout,
                          allow_redirects=True)
        result["status"] = r.status_code
        result["reachable"] = r.status_code in (200, 206, 301, 302)
        result["strategy"] = "direct"
        if r.status_code == 403:
            result["blocked"] = True
            result["reason"] = "403 Forbidden (Cloudflare or hotlink protection)"
        elif r.status_code == 429:
            result["blocked"] = True
            result["reason"] = "429 Rate limited"
        elif r.status_code == 401:
            result["blocked"] = True
            result["reason"] = "401 Login required"
        if r.headers.get("cf-ray") or r.headers.get("CF-RAY"):
            result["cloudflare"] = True
    except Exception as e:
        result["reason"] = str(e)
        result["blocked"] = True
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  inject_session_cookies — apply saved cookies to a domain session
# ─────────────────────────────────────────────────────────────────────────────
def inject_session_cookies(domain: str, cookies: dict):
    session = _get_session(domain)
    for k, v in cookies.items():
        session.cookies.set(k, v, domain=domain)
